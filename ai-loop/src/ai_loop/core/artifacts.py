"""Artifact management for pipeline runs."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ai_loop.core.models import RunSummary, RunStatus, TraceEvent
from ai_loop.safety.secrets import redact_secrets

if TYPE_CHECKING:
    from ai_loop.core.models import RunContext


class ArtifactManager:
    """Manages artifacts for a pipeline run."""

    def __init__(self, artifacts_root: Path):
        self.artifacts_root = artifacts_root
        self.artifacts_root.mkdir(parents=True, exist_ok=True)

    def get_run_dir(self, run_id: str) -> Path:
        """Get the artifacts directory for a run."""
        run_dir = self.artifacts_root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    def write_issue_pack(self, ctx: RunContext, content: str) -> Path:
        """Write the issue pack markdown."""
        path = ctx.artifacts_dir / "issue_pack.md"
        safe_content, _ = redact_secrets(content)
        path.write_text(safe_content)
        return path

    def write_plan(self, ctx: RunContext, version: int, content: str) -> Path:
        """Write a plan version."""
        path = ctx.artifacts_dir / f"plan_v{version}.md"
        safe_content, _ = redact_secrets(content)
        path.write_text(safe_content)
        return path

    def write_final_plan(self, ctx: RunContext, content: str) -> Path:
        """Write the final approved plan."""
        path = ctx.artifacts_dir / "final_plan.md"
        safe_content, _ = redact_secrets(content)
        path.write_text(safe_content)
        return path

    def write_implement_log(self, ctx: RunContext, content: str) -> Path:
        """Write implementation log."""
        path = ctx.artifacts_dir / "implement_log.txt"
        safe_content, _ = redact_secrets(content)
        path.write_text(safe_content)
        return path

    def write_fix_log(self, ctx: RunContext, iteration: int, content: str) -> Path:
        """Write code fix log."""
        path = ctx.artifacts_dir / f"implement_fix_v{iteration}.txt"
        safe_content, _ = redact_secrets(content)
        path.write_text(safe_content)
        return path

    def append_trace(self, ctx: RunContext, event: TraceEvent) -> None:
        """Append an event to the trace log."""
        path = ctx.artifacts_dir / "trace.jsonl"
        with open(path, "a") as f:
            f.write(json.dumps(event.to_dict()) + "\n")

    def write_summary(self, ctx: RunContext) -> Path:
        """Write the run summary."""
        final_confidence = None
        if ctx.plan_gates:
            final_confidence = ctx.plan_gates[-1].confidence
        elif ctx.code_gates:
            final_confidence = ctx.code_gates[-1].confidence

        summary = RunSummary(
            run_id=ctx.run_id,
            issue_identifier=ctx.issue.identifier,
            issue_title=ctx.issue.title,
            status=ctx.status,
            iterations=ctx.current_iteration,
            final_confidence=final_confidence,
            branch_name=ctx.branch_name,
            started_at=ctx.started_at,
            completed_at=ctx.completed_at,
            error_message=ctx.error_message,
        )

        path = ctx.artifacts_dir / "summary.json"
        path.write_text(json.dumps(summary.to_dict(), indent=2))
        return path

    def log_event(
        self,
        ctx: RunContext,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Log an event to trace and optionally console."""
        event = TraceEvent(
            timestamp=datetime.now(),
            event_type=event_type,
            stage=ctx.status.value,
            data=data or {},
        )
        self.append_trace(ctx, event)

    def list_runs(self) -> list[RunSummary]:
        """List all runs with their summaries."""
        runs: list[RunSummary] = []

        for run_dir in sorted(self.artifacts_root.iterdir(), reverse=True):
            if not run_dir.is_dir():
                continue

            summary_path = run_dir / "summary.json"
            if not summary_path.exists():
                continue

            try:
                data = json.loads(summary_path.read_text())
                runs.append(
                    RunSummary(
                        run_id=data["run_id"],
                        issue_identifier=data["issue_identifier"],
                        issue_title=data["issue_title"],
                        status=RunStatus(data["status"]),
                        iterations=data["iterations"],
                        final_confidence=data.get("final_confidence"),
                        branch_name=data["branch_name"],
                        started_at=(
                            datetime.fromisoformat(data["started_at"])
                            if data.get("started_at")
                            else None
                        ),
                        completed_at=(
                            datetime.fromisoformat(data["completed_at"])
                            if data.get("completed_at")
                            else None
                        ),
                        error_message=data.get("error_message", ""),
                    )
                )
            except (json.JSONDecodeError, KeyError, ValueError):
                continue

        return runs

    def read_trace(self, run_id: str) -> list[TraceEvent]:
        """Read trace events for a run."""
        path = self.artifacts_root / run_id / "trace.jsonl"
        if not path.exists():
            return []

        events: list[TraceEvent] = []
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                events.append(
                    TraceEvent(
                        timestamp=datetime.fromisoformat(data["timestamp"]),
                        event_type=data["event_type"],
                        stage=data["stage"],
                        data=data.get("data", {}),
                    )
                )
            except (json.JSONDecodeError, KeyError, ValueError):
                continue

        return events
