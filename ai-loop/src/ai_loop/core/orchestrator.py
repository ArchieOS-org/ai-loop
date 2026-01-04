"""Core orchestrator for the AI Loop pipeline."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ai_loop.core.artifacts import ArtifactManager
from ai_loop.core.logging import log as term_log
from ai_loop.core.models import (
    ApprovalMode,
    CritiqueResult,
    GateResult,
    LinearIssue,
    PlanVersion,
    RunContext,
    RunStatus,
)
from ai_loop.integrations.claude_runner import ClaudeRunner
from ai_loop.integrations.git_tools import GitTools
from ai_loop.integrations.openai_critique_runner import OpenAICritiqueRunner
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
        self.critique = OpenAICritiqueRunner()

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

    def _should_block_at_gate(
        self,
        ctx: RunContext,
        critique: CritiqueResult,
        gate_result: GateResult,
    ) -> bool:
        """Determine if we should block for human approval at this gate."""
        mode = ctx.approval_mode

        if mode == ApprovalMode.AUTO:
            # Never block
            return False
        elif mode == ApprovalMode.ALWAYS_GATE:
            # Always block
            return True
        elif mode == ApprovalMode.GATE_ON_FAIL:
            # Block only if gate failed
            return gate_result == GateResult.FAIL

        return False

    async def _wait_for_gate_resolution(
        self,
        ctx: RunContext,
        gate_type: str,
        critique: CritiqueResult,
        log: Callable[[str, dict | None], None],
    ) -> str:
        """
        Wait for human resolution at a gate.

        Writes gate_pending.json, polls for gate_resolution.json.
        Returns the action: 'approve', 'reject', or 'request_changes'.
        """
        run_dir = self.artifacts_root / ctx.run_id
        pending_path = run_dir / "gate_pending.json"
        resolution_path = run_dir / "gate_resolution.json"

        # Write gate_pending.json
        pending_data = {
            "gate_type": gate_type,
            "created_at": datetime.now().isoformat(),
            "critique": {
                "confidence": critique.confidence,
                "approved": critique.approved,
                "blockers": critique.blockers,
                "warnings": critique.warnings,
                "feedback": critique.feedback,
            },
        }
        pending_path.write_text(json.dumps(pending_data, indent=2))

        # Log the gate pending event (SSE will pick this up)
        log("gate_pending", {
            "gate_type": gate_type,
            "critique": pending_data["critique"],
        })

        # Poll for resolution (2s initially, 5s after 60s)
        start_time = datetime.now()
        timeout = 30 * 60  # 30 minutes
        poll_interval = 2  # Start with 2s

        while True:
            elapsed = (datetime.now() - start_time).total_seconds()

            # Check timeout
            if elapsed > timeout:
                # Auto-reject on timeout
                resolution_path.write_text(json.dumps({
                    "action": "reject",
                    "feedback": "Timed out (30m)",
                    "resolved_at": datetime.now().isoformat(),
                }))

            # Check for resolution file
            if resolution_path.exists():
                try:
                    resolution = json.loads(resolution_path.read_text())
                    action = resolution.get("action", "reject")
                    feedback = resolution.get("feedback", "")

                    # Store feedback for next iteration
                    if feedback:
                        ctx.human_feedback = feedback

                    # Clean up files
                    pending_path.unlink(missing_ok=True)
                    resolution_path.unlink(missing_ok=True)

                    # Log resolution
                    log("gate_resolved", {"action": action, "feedback": feedback})

                    return action
                except (json.JSONDecodeError, IOError):
                    pass

            # Backoff after 60s
            if elapsed > 60:
                poll_interval = 5

            await asyncio.sleep(poll_interval)

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
        2. PLAN_GATE loop (OpenAI critique → Claude refine) until stable
        3. Implement (Claude)
        4. CODE_GATE loop (OpenAI critique → Claude fix) until pass or max attempts
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

            # Terminal logging for visibility
            term_log("PIPELINE", f"Starting run for {ctx.issue.identifier}")
            term_log("PIPELINE", f"Run ID: {ctx.run_id}")
            term_log("PIPELINE", f"Mode: {'dry-run' if ctx.dry_run else 'write'}")

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
            term_log("PLANNING", "Generating plan with Claude...")

            # Generate initial plan
            plan_content, elapsed = await self.claude.generate_plan(safe_issue, ctx.repo_root)
            ctx.current_iteration = 1
            plan = PlanVersion(version=1, content=plan_content)
            ctx.plan_versions.append(plan)
            self.artifacts.write_plan(ctx, 1, plan_content)
            log("claude_completed", {
                "phase": "planning",
                "step": "generate_plan",
                "output": plan_content,
                "duration_s": elapsed,
                "char_count": len(plan_content),
            })
            log("plan_generated", {"version": 1})

            # === PLAN_GATE LOOP ===
            while ctx.current_iteration <= ctx.max_iterations:
                update_status(RunStatus.PLAN_GATE)
                log("plan_gate_started", {"iteration": ctx.current_iteration})
                term_log("PLAN_GATE", f"Running critique (iteration {ctx.current_iteration})...")

                # Run OpenAI critique
                prev_critique = ctx.plan_gates[-1] if ctx.plan_gates else None
                critique = await self.critique.plan_gate(
                    issue_pack,
                    ctx.plan_versions[-1].content,
                    ctx.current_iteration,
                    ctx,
                    prev_critique=prev_critique,
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
                term_log("PLAN_GATE", f"Result: confidence={critique.confidence}, approved={critique.approved}")

                # Check gate
                gate_result = self._check_gate(critique, ctx.confidence_threshold)

                # Check if we should block for human approval
                if self._should_block_at_gate(ctx, critique, gate_result):
                    action = await self._wait_for_gate_resolution(
                        ctx, "plan_gate", critique, log
                    )

                    if action == "reject":
                        ctx.status = RunStatus.FAILED
                        ctx.error_message = f"Rejected by user: {ctx.human_feedback or 'No feedback'}"
                        log("pipeline_rejected", {"feedback": ctx.human_feedback})
                        break
                    elif action == "approve":
                        # Override gate result to PASS
                        gate_result = GateResult.PASS
                    elif action == "request_changes":
                        # Force another iteration with feedback
                        gate_result = GateResult.FAIL
                        # Feedback already stored in ctx.human_feedback

                if gate_result == GateResult.PASS:
                    ctx.stable_pass_count += 1
                    log("plan_gate_passed", {"stable_count": ctx.stable_pass_count})
                    term_log("PLAN_GATE", f"Passed (stable count: {ctx.stable_pass_count})")

                    if ctx.stable_pass_count >= ctx.stable_passes:
                        # Plan approved!
                        ctx.final_plan = ctx.plan_versions[-1].content
                        self.artifacts.write_final_plan(ctx, ctx.final_plan)
                        log("plan_approved", {"iterations": ctx.current_iteration})
                        term_log("PLAN_GATE", f"Stable count: {ctx.stable_pass_count} -> Plan approved!")
                        break
                else:
                    ctx.stable_pass_count = 0
                    log("plan_gate_failed", {"blockers": critique.blockers})
                    term_log("PLAN_GATE", f"Failed with {len(critique.blockers)} blockers")

                # Check for stuck state
                if self._detect_stuck(ctx):
                    ctx.status = RunStatus.STUCK
                    ctx.error_message = "Pipeline stuck: plan hash repeated 3 times"
                    log("pipeline_stuck")
                    break

                # Refine plan
                update_status(RunStatus.REFINING)
                ctx.current_iteration += 1
                term_log("REFINING", f"Refining plan (iteration {ctx.current_iteration})...")

                # Include human feedback if available
                human_feedback = ctx.human_feedback
                ctx.human_feedback = ""  # Clear after use

                refined, elapsed = await self.claude.refine_plan(
                    safe_issue,
                    ctx.plan_versions[-1].content,
                    critique,
                    ctx.current_iteration - 1,
                    ctx.repo_root,
                    human_feedback=human_feedback,
                )
                plan = PlanVersion(version=ctx.current_iteration, content=refined)
                ctx.plan_versions.append(plan)
                self.artifacts.write_plan(ctx, ctx.current_iteration, refined)
                log("claude_completed", {
                    "phase": "planning",
                    "step": "refine_plan",
                    "output": refined,
                    "duration_s": elapsed,
                    "char_count": len(refined),
                })
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
                term_log("IMPLEMENTING", "Claude implementing final plan...")

                implement_log, elapsed = await self.claude.implement(ctx.final_plan, ctx)
                self.artifacts.write_implement_log(ctx, implement_log)
                log("claude_completed", {
                    "phase": "implementation",
                    "step": "implement",
                    "output": implement_log,
                    "duration_s": elapsed,
                    "char_count": len(implement_log),
                })
                log("implementation_completed")

                # === CODE_GATE LOOP ===
                fix_iteration = 0
                while fix_iteration < MAX_FIX_ITERATIONS:
                    update_status(RunStatus.CODE_GATE)
                    log("code_gate_started", {"fix_iteration": fix_iteration})
                    term_log("CODE_GATE", "Running code critique...")

                    # Get diff and run tests
                    git_diff = await self.git.get_diff(ctx.working_dir())
                    # TODO: Actually run tests and capture output
                    test_results = None

                    critique = await self.critique.code_gate(
                        ctx.final_plan,
                        git_diff,
                        test_results,
                        fix_iteration,
                        ctx,
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
                    term_log("CODE_GATE", f"Result: confidence={critique.confidence}, approved={critique.approved}")

                    gate_result = self._check_gate(critique, ctx.confidence_threshold)

                    # Check if we should block for human approval
                    if self._should_block_at_gate(ctx, critique, gate_result):
                        action = await self._wait_for_gate_resolution(
                            ctx, "code_gate", critique, log
                        )

                        if action == "reject":
                            ctx.status = RunStatus.FAILED
                            ctx.error_message = f"Rejected by user: {ctx.human_feedback or 'No feedback'}"
                            log("pipeline_rejected", {"feedback": ctx.human_feedback})
                            break
                        elif action == "approve":
                            gate_result = GateResult.PASS
                        elif action == "request_changes":
                            gate_result = GateResult.FAIL

                    if gate_result == GateResult.PASS:
                        ctx.status = RunStatus.SUCCESS
                        log("code_gate_passed")
                        term_log("CODE_GATE", "Passed -> Implementation complete!")
                        break
                    else:
                        log("code_gate_failed", {"blockers": critique.blockers})
                        term_log("CODE_GATE", f"Failed with {len(critique.blockers)} blockers")

                        # Try to fix
                        fix_iteration += 1
                        if fix_iteration < MAX_FIX_ITERATIONS:
                            update_status(RunStatus.FIXING)
                            log("fixing_started", {"iteration": fix_iteration})

                            # Include human feedback if available
                            human_feedback = ctx.human_feedback
                            ctx.human_feedback = ""

                            fix_log, elapsed = await self.claude.fix_code(
                                ctx.final_plan,
                                critique,
                                ctx,
                                human_feedback=human_feedback,
                            )
                            self.artifacts.write_fix_log(ctx, fix_iteration, fix_log)
                            log("claude_completed", {
                                "phase": "fixing",
                                "step": "fix",
                                "output": fix_log,
                                "duration_s": elapsed,
                                "char_count": len(fix_log),
                            })
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
            term_log("PIPELINE", f"Completed with status: {ctx.status.value}")

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
