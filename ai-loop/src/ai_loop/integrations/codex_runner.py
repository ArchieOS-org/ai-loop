"""Codex CLI runner for critique gating."""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, AsyncIterator

from ai_loop.config import get_settings, get_prompts_dir, get_schemas_dir
from ai_loop.core.models import CritiqueResult

if TYPE_CHECKING:
    from ai_loop.core.models import RunContext

logger = logging.getLogger(__name__)

# Module-level cache keyed by codex command path (survives batch/concurrency)
_CODEX_CAPS_CACHE: dict[str, dict[str, bool]] = {}
_DETECTION_TIMEOUT = 10  # seconds - fail closed if exceeded


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

    async def _detect_codex_capabilities(self) -> dict[str, bool]:
        """Run `codex exec --help` once, cache supported flags.

        Caches at module level (survives multiple runner instances in batch).
        Parses both stdout AND stderr (some CLIs print help to stderr).
        Times out after 10s and fails closed (conservative defaults).
        """
        # Check module-level cache first
        if self.cmd in _CODEX_CAPS_CACHE:
            return _CODEX_CAPS_CACHE[self.cmd]

        try:
            proc = await asyncio.create_subprocess_exec(
                self.cmd, "exec", "--help",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_DETECTION_TIMEOUT
            )
            # Parse BOTH stdout and stderr (CLIs vary on where help goes)
            help_text = (stdout + stderr).decode(errors="replace")

            caps = {
                "approval_mode": "--approval-mode" in help_text,
                "full_auto": "--full-auto" in help_text,
                "json": "--json" in help_text,
                "output_schema": "--output-schema" in help_text,
                "quiet": "-q" in help_text or "--quiet" in help_text,
            }
        except asyncio.TimeoutError:
            logger.warning(
                f"Codex capability detection timed out after {_DETECTION_TIMEOUT}s; "
                "using conservative defaults"
            )
            caps = {
                "approval_mode": False,
                "full_auto": False,
                "json": False,
                "output_schema": True,
                "quiet": False,
            }
        except Exception as e:
            logger.warning(
                f"Codex capability detection failed: {e}; using conservative defaults"
            )
            caps = {
                "approval_mode": False,
                "full_auto": False,
                "json": False,
                "output_schema": True,
                "quiet": False,
            }

        _CODEX_CAPS_CACHE[self.cmd] = caps
        return caps

    async def _build_codex_args(
        self, schema_path: Path, output_path: Path, prompt: str
    ) -> list[str]:
        """Build args based on detected capabilities.

        Note: --json is intentionally NOT used in V1. It streams JSONL to stdout
        which would need routing to trace.jsonl while still relying on -o for
        structured output. Orchestrator stage events provide sufficient visibility.
        """
        caps = await self._detect_codex_capabilities()

        args = [self.cmd, "exec"]

        # Approval mode: use what's available, log if missing
        if caps["approval_mode"]:
            args.extend(["--approval-mode", "full-auto"])
        elif caps["full_auto"]:
            args.append("--full-auto")
        else:
            # Log once, continue without - Codex will use defaults
            if not hasattr(self, "_logged_no_approval"):
                logger.warning("Codex: approval mode unsupported; using defaults")
                self._logged_no_approval = True

        # Quiet mode: only add if supported
        if caps["quiet"]:
            args.append("-q")

        args.extend(["--output-schema", str(schema_path), "-o", str(output_path)])

        # V1: Don't use --json. Rely on orchestrator stage events for visibility.
        # Future: If --json enabled, capture JSONL stream to trace.jsonl

        args.append(prompt)
        return args

    async def _run_codex_exec(
        self,
        prompt: str,
        cwd: Path,
        output_path: Path,
        timeout: int = 300,
    ) -> None:
        """Run codex exec with structured output.

        Uses capability detection to build correct command args.
        V1: Does not use --json streaming; relies on orchestrator stage events.
        """
        schema_path = self._get_schema_path()

        # Build command using detected capabilities
        cmd_parts = await self._build_codex_args(schema_path, output_path, prompt)

        proc = await asyncio.create_subprocess_exec(
            *cmd_parts,
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            # V1: Just wait for completion, no JSONL streaming
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise TimeoutError(f"Codex timed out after {timeout}s")

        # Codex may exit non-zero for MCP client errors, warnings, etc.
        # but still produce valid output. Check output file before failing.
        if proc.returncode != 0:
            # Check if output was still produced (Codex can succeed with warnings)
            if output_path.exists():
                try:
                    with open(output_path) as f:
                        data = json.load(f)
                    # Valid JSON output exists - log warning but continue
                    if data:  # Non-empty output
                        logger.warning(
                            f"Codex exited with code {proc.returncode} but produced valid output"
                        )
                        return  # Success - output is usable
                except (json.JSONDecodeError, Exception):
                    pass  # Invalid output - fall through to raise error

            raise RuntimeError(
                f"Codex exited with code {proc.returncode}: {stderr.decode()}"
            )

    async def plan_gate(
        self,
        issue_pack: str,
        plan: str,
        version: int,
        ctx: RunContext,
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

        await self._run_codex_exec(
            prompt,
            cwd=ctx.repo_root,
            output_path=output_path,
            timeout=300,
        )

        # Parse output
        return self._parse_critique_output(output_path)

    async def code_gate(
        self,
        final_plan: str,
        git_diff: str,
        test_results: str | None,
        version: int,
        ctx: RunContext,
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

        await self._run_codex_exec(
            prompt,
            cwd=ctx.working_dir(),
            output_path=output_path,
            timeout=300,
        )

        return self._parse_critique_output(output_path)

    def _parse_critique_output(self, output_path: Path) -> CritiqueResult:
        """Parse critique JSON output into CritiqueResult."""
        if not output_path.exists():
            raise FileNotFoundError(f"Codex output not found: {output_path}")

        with open(output_path) as f:
            data = json.load(f)

        return CritiqueResult.model_validate(data)
