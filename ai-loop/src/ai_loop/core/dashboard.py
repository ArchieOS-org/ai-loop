"""Live terminal dashboard for batch runs using Rich."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from ai_loop.core.models import RunContext, RunStatus


@dataclass
class IssueProgress:
    """Progress tracker for a single issue."""

    issue_identifier: str
    issue_title: str
    status: str = "pending"
    iteration: int = 0
    confidence: int | None = None
    blockers: int = 0
    started_at: datetime | None = None
    last_event: str = ""
    error: str = ""

    def elapsed(self) -> str:
        """Get elapsed time string."""
        if not self.started_at:
            return "-"
        delta = datetime.now() - self.started_at
        total_seconds = int(delta.total_seconds())
        minutes, seconds = divmod(total_seconds, 60)
        return f"{minutes:02d}:{seconds:02d}"


@dataclass
class BatchProgress:
    """Progress tracker for a batch run."""

    issues: dict[str, IssueProgress] = field(default_factory=dict)
    started_at: datetime = field(default_factory=datetime.now)
    completed: int = 0
    failed: int = 0
    total: int = 0

    def add_issue(self, identifier: str, title: str) -> None:
        """Add an issue to track."""
        self.issues[identifier] = IssueProgress(
            issue_identifier=identifier,
            issue_title=title[:40] + "..." if len(title) > 40 else title,
        )
        self.total += 1

    def update(
        self,
        identifier: str,
        *,
        status: str | None = None,
        iteration: int | None = None,
        confidence: int | None = None,
        blockers: int | None = None,
        last_event: str | None = None,
        error: str | None = None,
    ) -> None:
        """Update progress for an issue."""
        if identifier not in self.issues:
            return

        progress = self.issues[identifier]

        if status is not None:
            progress.status = status
            if status == "success":
                self.completed += 1
            elif status == "failed":
                self.failed += 1

        if iteration is not None:
            progress.iteration = iteration

        if confidence is not None:
            progress.confidence = confidence

        if blockers is not None:
            progress.blockers = blockers

        if last_event is not None:
            progress.last_event = last_event

        if error is not None:
            progress.error = error

        if progress.started_at is None and status not in ("pending", None):
            progress.started_at = datetime.now()

    def from_context(self, ctx: "RunContext") -> None:
        """Update from a RunContext."""
        confidence = None
        blockers = 0
        if ctx.plan_gates:
            confidence = ctx.plan_gates[-1].confidence
            blockers = len(ctx.plan_gates[-1].blockers)
        elif ctx.code_gates:
            confidence = ctx.code_gates[-1].confidence
            blockers = len(ctx.code_gates[-1].blockers)

        self.update(
            ctx.issue.identifier,
            status=ctx.status.value,
            iteration=ctx.current_iteration,
            confidence=confidence,
            blockers=blockers,
            error=ctx.error_message,
        )


class Dashboard:
    """Live terminal dashboard for batch runs."""

    def __init__(self):
        self.console = Console()
        self.progress = BatchProgress()
        self._live: Live | None = None
        self._update_event = asyncio.Event()

    def add_issues(self, issues: list[tuple[str, str]]) -> None:
        """Add issues to track. Each tuple is (identifier, title)."""
        for identifier, title in issues:
            self.progress.add_issue(identifier, title)

    def update(self, identifier: str, **kwargs) -> None:
        """Update progress for an issue."""
        self.progress.update(identifier, **kwargs)
        self._update_event.set()

    def update_from_context(self, ctx: "RunContext") -> None:
        """Update from a RunContext."""
        self.progress.from_context(ctx)
        self._update_event.set()

    def _build_table(self) -> Table:
        """Build the progress table."""
        table = Table(
            title="AI Loop Batch Progress",
            title_style="bold cyan",
            show_header=True,
            header_style="bold",
        )

        table.add_column("Issue", style="dim", width=12)
        table.add_column("Title", width=40)
        table.add_column("Stage", width=14)
        table.add_column("Iter", justify="center", width=4)
        table.add_column("Conf", justify="center", width=5)
        table.add_column("Block", justify="center", width=5)
        table.add_column("Time", justify="center", width=6)
        table.add_column("Last Event", width=30)

        for progress in self.progress.issues.values():
            # Status styling
            status_text = Text(progress.status)
            if progress.status == "success":
                status_text.stylize("bold green")
            elif progress.status == "failed":
                status_text.stylize("bold red")
            elif progress.status == "stuck":
                status_text.stylize("bold yellow")
            elif progress.status == "pending":
                status_text.stylize("dim")
            else:
                status_text.stylize("cyan")

            # Confidence styling
            conf_text = "-"
            if progress.confidence is not None:
                conf_text = str(progress.confidence)
                if progress.confidence >= 97:
                    conf_text = f"[green]{conf_text}[/green]"
                elif progress.confidence >= 80:
                    conf_text = f"[yellow]{conf_text}[/yellow]"
                else:
                    conf_text = f"[red]{conf_text}[/red]"

            # Blockers styling
            blockers_text = str(progress.blockers) if progress.blockers else "-"
            if progress.blockers > 0:
                blockers_text = f"[red]{blockers_text}[/red]"

            table.add_row(
                progress.issue_identifier,
                progress.issue_title,
                status_text,
                str(progress.iteration) if progress.iteration else "-",
                conf_text,
                blockers_text,
                progress.elapsed(),
                progress.last_event[:30] if progress.last_event else "-",
            )

        # Summary row
        elapsed = datetime.now() - self.progress.started_at
        elapsed_str = f"{int(elapsed.total_seconds())}s"

        table.add_section()
        table.add_row(
            "",
            f"[bold]Total: {self.progress.total}[/bold]",
            f"[green]Done: {self.progress.completed}[/green] [red]Fail: {self.progress.failed}[/red]",
            "",
            "",
            "",
            elapsed_str,
            "",
        )

        return table

    async def run(self) -> None:
        """Run the dashboard with live updates."""
        with Live(
            self._build_table(),
            console=self.console,
            refresh_per_second=4,
        ) as live:
            self._live = live
            while True:
                # Wait for update or timeout
                try:
                    await asyncio.wait_for(self._update_event.wait(), timeout=0.25)
                    self._update_event.clear()
                except asyncio.TimeoutError:
                    pass

                live.update(self._build_table())

                # Check if all done
                if (
                    self.progress.completed + self.progress.failed
                    >= self.progress.total
                ):
                    await asyncio.sleep(1)  # Show final state briefly
                    break

    def stop(self) -> None:
        """Stop the dashboard."""
        if self._live:
            self._live.stop()


class SimpleDashboard:
    """Simple console output for single runs or non-interactive mode.

    Shows:
    - Spinner with stage label + elapsed time during operations
    - Key events (gate results, approvals, errors) always shown
    - Raw events only in verbose mode
    """

    # Stage labels for display
    STAGE_LABELS = {
        "planning": "PLAN",
        "plan_gate": "GATE",
        "refining": "REFINE",
        "implementing": "IMPL",
        "code_gate": "REVIEW",
        "fixing": "FIX",
    }

    def __init__(self, issue_id: str | None = None, batch_mode: bool = False):
        self.console = Console()
        self.issue_id = issue_id
        self.batch_mode = batch_mode
        self._status_context = None
        self._stage_start: datetime | None = None

    def _format_elapsed(self) -> str:
        """Format elapsed time since stage start."""
        if not self._stage_start:
            return "0:00"
        delta = datetime.now() - self._stage_start
        total_seconds = int(delta.total_seconds())
        minutes, seconds = divmod(total_seconds, 60)
        return f"{minutes}:{seconds:02d}"

    def _stage_prefix(self, stage: str) -> str:
        """Build stage prefix with optional issue ID for batch mode."""
        label = self.STAGE_LABELS.get(stage, stage.upper())
        if self.batch_mode and self.issue_id:
            return f"[{self.issue_id}][{label}]"
        return f"[{label}]"

    def start_stage(self, stage: str, description: str) -> None:
        """Start a stage with spinner display."""
        self._stage_start = datetime.now()
        prefix = self._stage_prefix(stage)
        self._status_context = self.console.status(
            f"{prefix} {description}... ({self._format_elapsed()})",
            spinner="dots",
        )
        self._status_context.start()
        self._current_stage = stage
        self._current_description = description

    def update_stage(self, extra: str = "") -> None:
        """Update the spinner with current elapsed time."""
        if self._status_context:
            prefix = self._stage_prefix(self._current_stage)
            msg = f"{prefix} {self._current_description}... ({self._format_elapsed()})"
            if extra:
                msg += f" {extra}"
            self._status_context.update(msg)

    def stop_stage(self) -> None:
        """Stop the current stage spinner."""
        if self._status_context:
            self._status_context.stop()
            self._status_context = None
            self._stage_start = None

    def log(self, message: str, style: str = "") -> None:
        """Log a message (stops spinner if running)."""
        if self._status_context:
            self._status_context.stop()
        if style:
            self.console.print(message, style=style)
        else:
            self.console.print(message)
        if self._status_context:
            self._status_context.start()

    def key_event(self, event_type: str, data: dict) -> None:
        """Print a key event (always shown, not just verbose)."""
        # Stop spinner briefly to print
        if self._status_context:
            self._status_context.stop()

        if event_type == "plan_gate_result":
            conf = data.get("confidence", "?")
            blockers = data.get("blockers", 0)
            approved = data.get("approved", False)
            result = "approved" if approved else "rejected"
            blocker_str = f"{blockers} blockers | " if blockers else ""
            self.console.print(f"  → Confidence: {conf} | {blocker_str}{result}")

        elif event_type == "code_gate_result":
            conf = data.get("confidence", "?")
            blockers = data.get("blockers", 0)
            approved = data.get("approved", False)
            result = "approved" if approved else "rejected"
            blocker_str = f"{blockers} blockers | " if blockers else ""
            self.console.print(f"  → Confidence: {conf} | {blocker_str}{result}")

        elif event_type == "plan_approved":
            iterations = data.get("iterations", "?")
            self.console.print(
                f"  → Plan approved ({iterations} iterations)",
                style="green",
            )

        elif event_type == "plan_gate_passed":
            stable = data.get("stable_count", "?")
            self.console.print(f"  → Gate passed (stable: {stable})")

        elif event_type == "plan_gate_failed":
            blockers = data.get("blockers", [])
            if blockers:
                self.console.print(f"  → Gate failed: {blockers[0][:60]}...", style="yellow")

        elif event_type == "code_gate_passed":
            self.console.print("  → Code approved", style="green")

        elif event_type == "code_gate_failed":
            blockers = data.get("blockers", [])
            if blockers:
                self.console.print(f"  → Review failed: {blockers[0][:60]}...", style="yellow")

        elif event_type == "pipeline_error":
            error = data.get("error", "Unknown error")
            self.console.print(f"  → Error: {error}", style="red")

        # Restart spinner if it was running
        if self._status_context:
            self._status_context.start()

    def status_update(self, ctx: "RunContext") -> None:
        """Print a status update line."""
        confidence = None
        if ctx.plan_gates:
            confidence = ctx.plan_gates[-1].confidence
        elif ctx.code_gates:
            confidence = ctx.code_gates[-1].confidence

        self.console.print(
            f" Iteration {ctx.current_iteration} | "
            f"Confidence: {confidence or 'N/A'} | "
            f"Stable passes: {ctx.stable_pass_count}"
        )

    def event(self, event_type: str, data: dict) -> None:
        """Print an event (verbose mode only, dim style)."""
        self.console.print(f"  [dim]{event_type}: {data}[/dim]")

    def show_failure(
        self,
        stage: str,
        exit_code: int | None,
        error_msg: str,
        artifacts_path: str,
    ) -> None:
        """Show formatted failure output per UX contract."""
        self.console.print()
        self.console.print(f"[bold red]✗ Pipeline failed at {stage.upper()}[/bold red]")
        self.console.print()
        if exit_code is not None:
            self.console.print(f"  Exit code: {exit_code}")
        self.console.print(f"  Error: {error_msg}")
        self.console.print()
        self.console.print(f"  Artifacts: {artifacts_path}")
        self.console.print(f"  Logs: {artifacts_path}/trace.jsonl")
        self.console.print()
        self.console.print("  Next: re-run with --verbose and inspect trace.jsonl")

    def show_interrupt(self, artifacts_path: str, branch_name: str) -> None:
        """Show formatted interrupt output per UX contract."""
        self.console.print()
        self.console.print("[bold yellow]⚠ Interrupted by user[/bold yellow]")
        self.console.print()
        self.console.print(f"  Partial artifacts saved to: {artifacts_path}")
        self.console.print(f"  Branch preserved: {branch_name}")
        self.console.print()
        self.console.print("  Resume: ai-loop run --issue ISSUE-ID --continue-from run-id")
