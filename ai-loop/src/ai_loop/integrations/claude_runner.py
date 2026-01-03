"""Claude CLI runner for plan generation and implementation."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from ai_loop.config import get_settings, get_prompts_dir

if TYPE_CHECKING:
    from ai_loop.core.models import CritiqueResult, LinearIssue, RunContext


class ClaudeRunner:
    """Runner for Claude CLI subprocess."""

    def __init__(self, cmd: str | None = None):
        settings = get_settings()
        self.cmd = cmd or settings.claude_cmd
        self.prompts_dir = get_prompts_dir()

    def _load_prompt(self, name: str) -> str:
        """Load a prompt template."""
        path = self.prompts_dir / f"{name}.md"
        if not path.exists():
            raise FileNotFoundError(f"Prompt not found: {path}")
        return path.read_text()

    async def _run_claude(
        self,
        prompt: str,
        cwd: Path | None = None,
        timeout: int = 300,
    ) -> tuple[str, str]:
        """Run Claude CLI with prompt via stdin, return (stdout, stderr)."""
        proc = await asyncio.create_subprocess_exec(
            self.cmd,
            "--print",
            "-p", prompt,
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise TimeoutError(f"Claude timed out after {timeout}s")

        if proc.returncode != 0:
            raise RuntimeError(
                f"Claude exited with code {proc.returncode}: {stderr.decode()}"
            )

        return stdout.decode(), stderr.decode()

    async def generate_plan(
        self,
        issue: LinearIssue,
        repo_root: Path,
    ) -> str:
        """Generate initial implementation plan."""
        template = self._load_prompt("claude_planner")
        issue_pack = issue.to_issue_pack()
        prompt = f"{template}\n\n---\n\n{issue_pack}"
        stdout, _ = await self._run_claude(prompt, cwd=repo_root)
        return stdout

    async def refine_plan(
        self,
        issue: LinearIssue,
        current_plan: str,
        critique: CritiqueResult,
        version: int,
        repo_root: Path,
    ) -> str:
        """Refine plan based on critique feedback."""
        template = self._load_prompt("claude_refiner")
        issue_pack = issue.to_issue_pack()

        critique_text = f"""
## Critique Result (v{version})

**Confidence:** {critique.confidence}/100
**Approved:** {critique.approved}
**Blockers:** {', '.join(critique.blockers) if critique.blockers else 'None'}
**Warnings:** {', '.join(critique.warnings) if critique.warnings else 'None'}

### Feedback
{critique.feedback}

### Rubric Breakdown
- Goal Clarity: {critique.rubric_breakdown.goal_clarity}/100
- Scope Minimality: {critique.rubric_breakdown.scope_minimality}/100
- UX Contract: {critique.rubric_breakdown.ux_contract}/100
- Data Contract: {critique.rubric_breakdown.data_contract}/100
- Architecture: {critique.rubric_breakdown.architecture}/100
- Test Coverage: {critique.rubric_breakdown.test_coverage}/100
- Rollout Safety: {critique.rubric_breakdown.rollout_safety}/100
- Done Checklist: {critique.rubric_breakdown.done_checklist}/100
"""

        prompt = f"""{template}

---

## Issue Pack
{issue_pack}

---

## Current Plan (v{version})
{current_plan}

---

{critique_text}
"""
        stdout, _ = await self._run_claude(prompt, cwd=repo_root)
        return stdout

    async def implement(
        self,
        final_plan: str,
        ctx: RunContext,
    ) -> str:
        """Implement the final plan in the working directory."""
        template = self._load_prompt("claude_implementer")
        working_dir = ctx.working_dir()

        prompt = f"""{template}

---

## Final Plan
{final_plan}

---

## Instructions
- You are working in: {working_dir}
- Branch: {ctx.branch_name}
- Issue: {ctx.issue.identifier} - {ctx.issue.title}
- Make small, focused commits with clear messages
- Run tests after implementation
- Do NOT expand scope beyond the plan
"""
        stdout, _ = await self._run_claude(prompt, cwd=working_dir, timeout=600)
        return stdout

    async def fix_code(
        self,
        final_plan: str,
        critique: CritiqueResult,
        ctx: RunContext,
    ) -> str:
        """Fix code based on CODE_GATE critique."""
        template = self._load_prompt("claude_implementer")
        working_dir = ctx.working_dir()

        blockers_text = "\n".join(f"- {b}" for b in critique.blockers)
        warnings_text = "\n".join(f"- {w}" for w in critique.warnings)

        prompt = f"""{template}

---

## Final Plan
{final_plan}

---

## CODE_GATE Critique - Blockers to Fix
{blockers_text or 'None'}

## Warnings (address if quick)
{warnings_text or 'None'}

## Detailed Feedback
{critique.feedback}

---

## Instructions
- You are working in: {working_dir}
- Branch: {ctx.branch_name}
- Fix ONLY the blockers listed above
- Keep changes minimal and focused
- Preserve existing intent
- Run tests after fixes
"""
        stdout, _ = await self._run_claude(prompt, cwd=working_dir, timeout=600)
        return stdout
