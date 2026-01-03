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
    """Simple console output for single runs or non-interactive mode."""

    def __init__(self):
        self.console = Console()

    def log(self, message: str, style: str = "") -> None:
        """Log a message."""
        if style:
            self.console.print(message, style=style)
        else:
            self.console.print(message)

    def status_update(self, ctx: "RunContext") -> None:
        """Print a status update."""
        confidence = None
        if ctx.plan_gates:
            confidence = ctx.plan_gates[-1].confidence
        elif ctx.code_gates:
            confidence = ctx.code_gates[-1].confidence

        self.console.print(
            f"[{ctx.status.value}] "
            f"Iteration {ctx.current_iteration} | "
            f"Confidence: {confidence or 'N/A'} | "
            f"Stable passes: {ctx.stable_pass_count}"
        )

    def event(self, event_type: str, data: dict) -> None:
        """Print an event."""
        self.console.print(f"  → {event_type}: {data}", style="dim")
