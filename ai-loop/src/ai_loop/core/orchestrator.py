"""Core orchestrator for the AI Loop pipeline."""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ai_loop.core.artifacts import ArtifactManager
from ai_loop.core.models import (
    CritiqueResult,
    GateResult,
    LinearIssue,
    PlanVersion,
    RunContext,
    RunStatus,
)
from ai_loop.integrations.claude_runner import ClaudeRunner
from ai_loop.integrations.codex_runner import CodexRunner
from ai_loop.integrations.git_tools import GitTools
from ai_loop.integrations.linear import LinearClient
from ai_loop.safety.sanitizer import sanitize_issue_content, sanitize_issue_title

if TYPE_CHECKING:
    pass

# Maximum fix iterations for CODE_GATE
MAX_FIX_ITERATIONS = 3


class PipelineOrchestrator:
    """Orchestrates the full AI Loop pipeline."""

    def __init__(
        self,
        repo_root: Path | None = None,
        artifacts_root: Path | None = None,
    ):
        self.git = GitTools(repo_root)
        self.repo_root = self.git.get_repo_root()
        self.artifacts_root = artifacts_root or (self.repo_root / "artifacts")
        self.artifacts = ArtifactManager(self.artifacts_root)
        self.linear = LinearClient()
        self.claude = ClaudeRunner()
        self.codex = CodexRunner()

    def _generate_run_id(self, issue_identifier: str) -> str:
        """Generate a unique run ID."""
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        short_uuid = uuid.uuid4().hex[:6]
        safe_id = issue_identifier.replace("/", "-").lower()
        return f"{safe_id}-{timestamp}-{short_uuid}"

    async def create_context(
        self,
        issue: LinearIssue,
        *,
        dry_run: bool = True,
        max_iterations: int = 5,
        confidence_threshold: int = 97,
        stable_passes: int = 2,
        use_worktree: bool = True,
        no_linear_writeback: bool = False,
        verbose: bool = False,
    ) -> RunContext:
        """Create a run context for an issue."""
        run_id = self._generate_run_id(issue.identifier)
        artifacts_dir = self.artifacts.get_run_dir(run_id)
        branch_name = self.git.generate_branch_name(issue.identifier)

        worktree_dir = None
        if use_worktree and not dry_run:
            worktree_dir = artifacts_dir / "worktree"

        return RunContext(
            run_id=run_id,
            issue=issue,
            repo_root=self.repo_root,
            artifacts_dir=artifacts_dir,
            worktree_dir=worktree_dir,
            branch_name=branch_name,
            dry_run=dry_run,
            max_iterations=max_iterations,
            confidence_threshold=confidence_threshold,
            stable_passes=stable_passes,
            use_worktree=use_worktree,
            no_linear_writeback=no_linear_writeback,
            verbose=verbose,
        )

    def _sanitize_issue(self, issue: LinearIssue) -> LinearIssue:
        """Sanitize issue content for safe prompt construction."""
        return LinearIssue(
            id=issue.id,
            identifier=issue.identifier,
            title=sanitize_issue_title(issue.title),
            description=sanitize_issue_content(issue.description),
            state=issue.state,
            priority=issue.priority,
            team_id=issue.team_id,
            team_name=issue.team_name,
            project_id=issue.project_id,
            project_name=issue.project_name,
            labels=issue.labels[:10],  # Limit labels
            url=issue.url,
        )

    def _check_gate(
        self,
        critique: CritiqueResult,
        threshold: int,
    ) -> GateResult:
        """Check if a gate passes."""
        if critique.approved and critique.confidence >= threshold and not critique.blockers:
            return GateResult.PASS
        return GateResult.FAIL

    def _detect_stuck(self, ctx: RunContext) -> bool:
        """Detect if the pipeline is stuck (repeating plans)."""
        if len(ctx.plan_versions) < 3:
            return False

        # Check if last 3 plans have same hash
        recent_hashes = [p.hash for p in ctx.plan_versions[-3:]]
        return len(set(recent_hashes)) == 1

    async def run_pipeline(
        self,
        ctx: RunContext,
        on_status_change: Callable[[RunContext], None] | None = None,
        on_event: Callable[[str, dict], None] | None = None,
    ) -> RunContext:
        """
        Run the full pipeline for an issue.

        Stages:
        1. Generate initial plan (Claude)
        2. PLAN_GATE loop (Codex critique → Claude refine) until stable
        3. Implement (Claude)
        4. CODE_GATE loop (Codex critique → Claude fix) until pass or max attempts
        """
        ctx.started_at = datetime.now()

        def update_status(status: RunStatus) -> None:
            ctx.status = status
            if on_status_change:
                on_status_change(ctx)

        def log(event_type: str, data: dict | None = None) -> None:
            self.artifacts.log_event(ctx, event_type, data)
            if on_event:
                on_event(event_type, data or {})

        try:
            # Sanitize issue
            safe_issue = self._sanitize_issue(ctx.issue)
            issue_pack = safe_issue.to_issue_pack()
            self.artifacts.write_issue_pack(ctx, issue_pack)
            log("pipeline_started", {"issue": ctx.issue.identifier})

            # Setup git isolation (unless dry run)
            if not ctx.dry_run:
                if ctx.use_worktree and ctx.worktree_dir:
                    await self.git.create_worktree(ctx.branch_name, ctx.worktree_dir)
                    log("worktree_created", {"path": str(ctx.worktree_dir)})
                else:
                    await self.git.create_branch(ctx.branch_name)
                    log("branch_created", {"branch": ctx.branch_name})

            # === PLANNING PHASE ===
            update_status(RunStatus.PLANNING)
            log("planning_started")

            # Generate initial plan
            plan_content = await self.claude.generate_plan(safe_issue, ctx.repo_root)
            ctx.current_iteration = 1
            plan = PlanVersion(version=1, content=plan_content)
            ctx.plan_versions.append(plan)
            self.artifacts.write_plan(ctx, 1, plan_content)
            log("plan_generated", {"version": 1})

            # === PLAN_GATE LOOP ===
            while ctx.current_iteration <= ctx.max_iterations:
                update_status(RunStatus.PLAN_GATE)
                log("plan_gate_started", {"iteration": ctx.current_iteration})

                # Run Codex critique
                critique = await self.codex.plan_gate(
                    issue_pack,
                    ctx.plan_versions[-1].content,
                    ctx.current_iteration,
                    ctx,
                    event_callback=lambda e: log("codex_event", e),
                )
                ctx.plan_gates.append(critique)
                log(
                    "plan_gate_result",
                    {
                        "confidence": critique.confidence,
                        "approved": critique.approved,
                        "blockers": len(critique.blockers),
                    },
                )

                # Check gate
                gate_result = self._check_gate(critique, ctx.confidence_threshold)

                if gate_result == GateResult.PASS:
                    ctx.stable_pass_count += 1
                    log("plan_gate_passed", {"stable_count": ctx.stable_pass_count})

                    if ctx.stable_pass_count >= ctx.stable_passes:
                        # Plan approved!
                        ctx.final_plan = ctx.plan_versions[-1].content
                        self.artifacts.write_final_plan(ctx, ctx.final_plan)
                        log("plan_approved", {"iterations": ctx.current_iteration})
                        break
                else:
                    ctx.stable_pass_count = 0
                    log("plan_gate_failed", {"blockers": critique.blockers})

                # Check for stuck state
                if self._detect_stuck(ctx):
                    ctx.status = RunStatus.STUCK
                    ctx.error_message = "Pipeline stuck: plan hash repeated 3 times"
                    log("pipeline_stuck")
                    break

                # Refine plan
                update_status(RunStatus.REFINING)
                ctx.current_iteration += 1

                refined = await self.claude.refine_plan(
                    safe_issue,
                    ctx.plan_versions[-1].content,
                    critique,
                    ctx.current_iteration - 1,
                    ctx.repo_root,
                )
                plan = PlanVersion(version=ctx.current_iteration, content=refined)
                ctx.plan_versions.append(plan)
                self.artifacts.write_plan(ctx, ctx.current_iteration, refined)
                log("plan_refined", {"version": ctx.current_iteration})

            # Check if we exhausted iterations
            if ctx.current_iteration > ctx.max_iterations and not ctx.final_plan:
                ctx.status = RunStatus.FAILED
                ctx.error_message = f"Max iterations ({ctx.max_iterations}) reached without approval"
                log("max_iterations_reached")

            # === IMPLEMENTATION PHASE ===
            if ctx.final_plan and not ctx.dry_run:
                update_status(RunStatus.IMPLEMENTING)
                log("implementation_started")

                implement_log = await self.claude.implement(ctx.final_plan, ctx)
                self.artifacts.write_implement_log(ctx, implement_log)
                log("implementation_completed")

                # === CODE_GATE LOOP ===
                fix_iteration = 0
                while fix_iteration < MAX_FIX_ITERATIONS:
                    update_status(RunStatus.CODE_GATE)
                    log("code_gate_started", {"fix_iteration": fix_iteration})

                    # Get diff and run tests
                    git_diff = await self.git.get_diff(ctx.working_dir())
                    # TODO: Actually run tests and capture output
                    test_results = None

                    critique = await self.codex.code_gate(
                        ctx.final_plan,
                        git_diff,
                        test_results,
                        fix_iteration,
                        ctx,
                        event_callback=lambda e: log("codex_event", e),
                    )
                    ctx.code_gates.append(critique)
                    log(
                        "code_gate_result",
                        {
                            "confidence": critique.confidence,
                            "approved": critique.approved,
                            "blockers": len(critique.blockers),
                        },
                    )

                    gate_result = self._check_gate(critique, ctx.confidence_threshold)

                    if gate_result == GateResult.PASS:
                        ctx.status = RunStatus.SUCCESS
                        log("code_gate_passed")
                        break
                    else:
                        log("code_gate_failed", {"blockers": critique.blockers})

                        # Try to fix
                        fix_iteration += 1
                        if fix_iteration < MAX_FIX_ITERATIONS:
                            update_status(RunStatus.FIXING)
                            log("fixing_started", {"iteration": fix_iteration})

                            fix_log = await self.claude.fix_code(
                                ctx.final_plan,
                                critique,
                                ctx,
                            )
                            self.artifacts.write_fix_log(ctx, fix_iteration, fix_log)
                            log("fix_applied", {"iteration": fix_iteration})
                        else:
                            ctx.status = RunStatus.FAILED
                            ctx.error_message = f"Code fixes exhausted after {MAX_FIX_ITERATIONS} attempts"
                            log("code_fixes_exhausted")

            elif ctx.final_plan and ctx.dry_run:
                ctx.status = RunStatus.SUCCESS
                log("dry_run_completed")

        except Exception as e:
            ctx.status = RunStatus.FAILED
            ctx.error_message = str(e)
            log("pipeline_error", {"error": str(e)})

        finally:
            ctx.completed_at = datetime.now()
            self.artifacts.write_summary(ctx)
            log("pipeline_completed", {"status": ctx.status.value})

            # Cleanup worktree on failure (optional)
            # if ctx.worktree_dir and ctx.status == RunStatus.FAILED:
            #     await self.git.remove_worktree(ctx.worktree_dir)

        # Optional: Write back to Linear
        if not ctx.no_linear_writeback and not ctx.dry_run:
            await self._writeback_to_linear(ctx)

        return ctx

    async def _writeback_to_linear(self, ctx: RunContext) -> None:
        """Write a summary comment back to Linear."""
        status_emoji = "✅" if ctx.status == RunStatus.SUCCESS else "❌"
        confidence = (
            ctx.plan_gates[-1].confidence if ctx.plan_gates else "N/A"
        )

        comment = f"""## AI Loop Run {status_emoji}

**Run ID:** `{ctx.run_id}`
**Status:** {ctx.status.value}
**Iterations:** {ctx.current_iteration}
**Final Confidence:** {confidence}
**Branch:** `{ctx.branch_name}`

---

Artifacts: `artifacts/{ctx.run_id}/`
"""

        if ctx.error_message:
            comment += f"\n**Error:** {ctx.error_message}"

        try:
            await self.linear.add_comment(ctx.issue.id, comment)
        except Exception:
            pass  # Don't fail the run for writeback errors
