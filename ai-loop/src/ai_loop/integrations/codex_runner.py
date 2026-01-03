"""Codex CLI runner for critique gating."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, AsyncIterator

from ai_loop.config import get_settings, get_prompts_dir, get_schemas_dir
from ai_loop.core.models import CritiqueResult

if TYPE_CHECKING:
    from ai_loop.core.models import RunContext


class CodexRunner:
    """Runner for Codex CLI (codex exec) subprocess."""

    def __init__(self, cmd: str | None = None):
        settings = get_settings()
        self.cmd = cmd or settings.codex_cmd
        self.prompts_dir = get_prompts_dir()
        self.schemas_dir = get_schemas_dir()

    def _load_prompt(self, name: str) -> str:
        """Load a prompt template."""
        path = self.prompts_dir / f"{name}.md"
        if not path.exists():
            raise FileNotFoundError(f"Prompt not found: {path}")
        return path.read_text()

    def _get_schema_path(self) -> Path:
        """Get path to critique schema."""
        return self.schemas_dir / "critique_schema.json"

    async def _run_codex_exec(
        self,
        prompt: str,
        cwd: Path,
        output_path: Path,
        timeout: int = 300,
        use_json: bool = True,
    ) -> AsyncIterator[dict]:
        """
        Run codex exec with structured output.
        Yields JSONL events if --json is supported.
        """
        schema_path = self._get_schema_path()

        # Build command
        cmd_parts = [
            self.cmd,
            "exec",
            "--approval-mode", "full-auto",
            "-q",  # quiet mode
            "--output-schema", str(schema_path),
            "-o", str(output_path),
        ]

        if use_json:
            cmd_parts.append("--json")

        cmd_parts.append(prompt)

        proc = await asyncio.create_subprocess_exec(
            *cmd_parts,
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Stream stdout for JSONL events
        events: list[dict] = []
        try:
            async def read_output():
                assert proc.stdout is not None
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    line_str = line.decode().strip()
                    if line_str and use_json:
                        try:
                            event = json.loads(line_str)
                            events.append(event)
                            yield event
                        except json.JSONDecodeError:
                            pass

            async for event in read_output():
                yield event

            await asyncio.wait_for(proc.wait(), timeout=timeout)

        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise TimeoutError(f"Codex timed out after {timeout}s")

        if proc.returncode != 0:
            stderr = await proc.stderr.read() if proc.stderr else b""
            # Try without --json flag if it failed
            if use_json and b"unknown flag" in stderr.lower():
                async for event in self._run_codex_exec(
                    prompt, cwd, output_path, timeout, use_json=False
                ):
                    yield event
                return
            raise RuntimeError(
                f"Codex exited with code {proc.returncode}: {stderr.decode()}"
            )

    async def plan_gate(
        self,
        issue_pack: str,
        plan: str,
        version: int,
        ctx: RunContext,
        event_callback: callable | None = None,
    ) -> CritiqueResult:
        """Run PLAN_GATE critique on a plan."""
        template = self._load_prompt("codex_plan_gate")

        prompt = f"""{template}

---

## Issue Pack
{issue_pack}

---

## Plan (v{version})
{plan}
"""

        output_path = ctx.artifacts_dir / f"plan_gate_v{version}.json"

        async for event in self._run_codex_exec(
            prompt,
            cwd=ctx.repo_root,
            output_path=output_path,
            timeout=300,
        ):
            if event_callback:
                event_callback(event)

        # Parse output
        return self._parse_critique_output(output_path)

    async def code_gate(
        self,
        final_plan: str,
        git_diff: str,
        test_results: str | None,
        version: int,
        ctx: RunContext,
        event_callback: callable | None = None,
    ) -> CritiqueResult:
        """Run CODE_GATE critique on implemented code."""
        template = self._load_prompt("codex_code_gate")

        prompt = f"""{template}

---

## Final Plan
{final_plan}

---

## Git Diff
```diff
{git_diff}
```

---

## Test Results
{test_results or '_No test results available_'}
"""

        output_path = ctx.artifacts_dir / f"code_gate_v{version}.json"

        async for event in self._run_codex_exec(
            prompt,
            cwd=ctx.working_dir(),
            output_path=output_path,
            timeout=300,
        ):
            if event_callback:
                event_callback(event)

        return self._parse_critique_output(output_path)

    def _parse_critique_output(self, output_path: Path) -> CritiqueResult:
        """Parse critique JSON output into CritiqueResult."""
        if not output_path.exists():
            raise FileNotFoundError(f"Codex output not found: {output_path}")

        with open(output_path) as f:
            data = json.load(f)

        return CritiqueResult.model_validate(data)
