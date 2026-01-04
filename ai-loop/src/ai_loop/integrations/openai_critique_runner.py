"""OpenAI Responses API critique runner for plan and code gates."""

from __future__ import annotations

import asyncio
import json

from openai import APIConnectionError, APITimeoutError, AsyncOpenAI, RateLimitError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ai_loop.config import get_prompts_dir, get_schemas_dir, get_settings
from ai_loop.core.models import CritiqueResult, RunContext

# Module-level semaphore for batch concurrency control
_api_semaphore: asyncio.Semaphore | None = None


def _get_semaphore(max_concurrent: int) -> asyncio.Semaphore:
    """Get or create the API semaphore for concurrency control."""
    global _api_semaphore
    if _api_semaphore is None:
        _api_semaphore = asyncio.Semaphore(max_concurrent)
    return _api_semaphore


class OpenAICritiqueRunner:
    """Run critique gates via OpenAI Responses API with structured output."""

    def __init__(self) -> None:
        settings = get_settings()
        self.client = AsyncOpenAI(api_key=settings.openai_api_key)
        self.model = settings.critique_model
        self.prompts_dir = get_prompts_dir()
        self.schemas_dir = get_schemas_dir()
        self.max_concurrent = settings.critique_max_concurrent

    def _load_prompt(self, name: str) -> str:
        """Load a prompt template from disk."""
        path = self.prompts_dir / f"{name}.md"
        return path.read_text()

    def _load_json_schema(self) -> dict:
        """Load hand-written critique_schema.json from disk (single source of truth)."""
        path = self.schemas_dir / "critique_schema.json"
        return json.loads(path.read_text())

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=60),
        retry=retry_if_exception_type((RateLimitError, APITimeoutError, APIConnectionError)),
    )
    async def _call_api(
        self,
        system: str,
        user: str,
        ctx: RunContext,
        artifact_name: str,
        timeout: int = 300,
    ) -> CritiqueResult:
        """Single API call with retries, structured output, and fail-closed validation."""
        semaphore = _get_semaphore(self.max_concurrent)

        async with semaphore:
            response = await self.client.responses.create(
                model=self.model,
                input=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "critique_result",
                        "schema": self._load_json_schema(),
                        "strict": True,
                    }
                },
                reasoning={"effort": "high"},
                store=False,
                timeout=timeout,
            )

        # Fail-closed parsing: log raw response on any failure
        raw_output = response.output_text
        try:
            data = json.loads(raw_output)
            result = CritiqueResult.model_validate(data)
        except (json.JSONDecodeError, Exception) as e:
            # Log raw response to artifacts for debugging
            error_path = ctx.artifacts_dir / f"{artifact_name}_raw_error.txt"
            error_path.write_text(f"Parse error: {e}\n\nRaw output:\n{raw_output}")
            raise ValueError(f"Failed to parse critique response: {e}") from e

        return result

    async def plan_gate(
        self,
        issue_pack: str,
        plan: str,
        version: int,
        ctx: RunContext,
        prev_critique: CritiqueResult | None = None,
    ) -> CritiqueResult:
        """Run PLAN_GATE critique."""
        system = self._load_prompt("openai_plan_gate")

        # Build user message with bounded context
        user_parts = [
            f"## Issue Pack\n{issue_pack}",
            f"## Plan (v{version})\n{plan}",
        ]

        # Include previous critique if iterating (bounded context, not session)
        if prev_critique and version > 1:
            user_parts.append(f"## Previous Critique (v{version-1})\n{prev_critique.feedback}")

        user = "\n\n---\n\n".join(user_parts)

        artifact_name = f"plan_gate_v{version}"
        result = await self._call_api(system, user, ctx, artifact_name)

        # Save successful result to artifacts
        output_path = ctx.artifacts_dir / f"{artifact_name}.json"
        output_path.write_text(result.model_dump_json(indent=2))

        return result

    async def code_gate(
        self,
        final_plan: str,
        git_diff: str,
        test_results: str | None,
        version: int,
        ctx: RunContext,
    ) -> CritiqueResult:
        """Run CODE_GATE critique."""
        system = self._load_prompt("openai_code_gate")

        user = f"""## Final Plan
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

        artifact_name = f"code_gate_v{version}"
        result = await self._call_api(system, user, ctx, artifact_name)

        output_path = ctx.artifacts_dir / f"{artifact_name}.json"
        output_path.write_text(result.model_dump_json(indent=2))

        return result
