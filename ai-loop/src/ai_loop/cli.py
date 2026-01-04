"""CLI for AI Loop."""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from ai_loop.config import get_settings
from ai_loop.core.logging import log
from ai_loop.core.artifacts import ArtifactManager
from ai_loop.core.dashboard import Dashboard, SimpleDashboard
from ai_loop.core.orchestrator import PipelineOrchestrator
from ai_loop.integrations.linear import LinearClient


app = typer.Typer(
    name="ai-loop",
    help="CLI orchestrator: Linear issues → Claude plans → Codex critique → Claude implementation",
    no_args_is_help=True,
)
console = Console()


def _get_default_bool(env_value: bool | None, default: bool) -> bool:
    """Get boolean with environment default."""
    return env_value if env_value is not None else default


@app.command()
def run(
    issue: Annotated[str, typer.Option("--issue", "-i", help="Linear issue identifier (e.g., LIN-123)")],
    dry_run: Annotated[Optional[bool], typer.Option("--dry-run/--no-dry-run", help="Don't create branches or implement")] = None,
    max_iterations: Annotated[Optional[int], typer.Option("--max-iterations", help="Max plan iterations")] = None,
    confidence_threshold: Annotated[Optional[int], typer.Option("--confidence-threshold", help="Confidence threshold (0-100)")] = None,
    stable_passes: Annotated[Optional[int], typer.Option("--stable-passes", help="Required stable gate passes")] = None,
    repo_root: Annotated[Optional[Path], typer.Option("--repo-root", help="Repository root (auto-detected)")] = None,
    use_worktree: Annotated[Optional[bool], typer.Option("--use-worktree/--no-worktree", help="Use git worktree for isolation")] = None,
    no_linear_writeback: Annotated[bool, typer.Option("--no-linear-writeback", help="Don't comment on Linear issue")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Verbose output")] = False,
) -> None:
    """Run pipeline for a single Linear issue."""
    settings = get_settings()

    # Apply defaults from settings
    dry_run = dry_run if dry_run is not None else settings.dry_run_default
    max_iterations = max_iterations or settings.max_iterations_default
    confidence_threshold = confidence_threshold or settings.confidence_threshold_default
    stable_passes = stable_passes or settings.stable_passes_default
    use_worktree = use_worktree if use_worktree is not None else settings.use_worktree_default
    no_linear_writeback = no_linear_writeback or settings.no_linear_writeback_default

    async def run_async():
        # Fetch issue
        linear = LinearClient()
        console.print(f"[bold]Fetching issue:[/bold] {issue}")
        linear_issue = await linear.get_issue(issue)
        console.print(f"[green]Found:[/green] {linear_issue.title}")

        # Create orchestrator and context
        orchestrator = PipelineOrchestrator(repo_root=repo_root)
        ctx = await orchestrator.create_context(
            linear_issue,
            dry_run=dry_run,
            max_iterations=max_iterations,
            confidence_threshold=confidence_threshold,
            stable_passes=stable_passes,
            use_worktree=use_worktree,
            no_linear_writeback=no_linear_writeback,
            verbose=verbose,
        )

        # Setup simple dashboard with issue ID
        dashboard = SimpleDashboard(issue_id=linear_issue.identifier)

        # Key events to always show (not just verbose)
        KEY_EVENTS = {
            "plan_gate_result", "code_gate_result", "plan_approved",
            "plan_gate_passed", "plan_gate_failed", "code_gate_passed",
            "code_gate_failed", "pipeline_error",
        }

        # Stage mapping for spinner
        STAGE_DESCRIPTIONS = {
            "planning": "Generating plan with Claude",
            "plan_gate": f"Critiquing plan (iter {ctx.current_iteration})",
            "refining": "Updating plan from feedback",
            "implementing": "Claude implementing plan",
            "code_gate": "Critiquing implementation",
            "fixing": "Fixing code from feedback",
        }

        current_stage = None

        def on_status_change(c):
            nonlocal current_stage
            new_stage = c.status.value
            if new_stage != current_stage:
                # Stop previous stage spinner
                dashboard.stop_stage()
                # Start new stage
                desc = STAGE_DESCRIPTIONS.get(new_stage, new_stage)
                if new_stage == "plan_gate":
                    desc = f"Critiquing plan (iter {c.current_iteration})"
                dashboard.start_stage(new_stage, desc)
                current_stage = new_stage

        def on_event(event_type, data):
            # Always show key events
            if event_type in KEY_EVENTS:
                dashboard.key_event(event_type, data)
            # Show all events in verbose mode
            elif verbose:
                dashboard.event(event_type, data)

        console.print(f"\n[bold cyan]Starting pipeline run:[/bold cyan] {ctx.run_id}")
        if dry_run:
            console.print("[yellow]DRY RUN MODE - no branches or implementation[/yellow]")
        console.print()

        # Get event loop for proper signal handling
        loop = asyncio.get_running_loop()
        interrupted = False
        pipeline_task = None

        def handle_sigint():
            """Asyncio-safe signal handler - cancels tasks instead of raising."""
            nonlocal interrupted
            interrupted = True
            if pipeline_task and not pipeline_task.done():
                pipeline_task.cancel()

        # Use asyncio's signal handler (runs in event loop context, not as interrupt)
        loop.add_signal_handler(signal.SIGINT, handle_sigint)

        # Background task to update elapsed time every second
        async def update_elapsed():
            while True:
                await asyncio.sleep(1)
                dashboard.update_stage()

        elapsed_task = asyncio.create_task(update_elapsed())

        # Run pipeline
        try:
            pipeline_task = asyncio.create_task(
                orchestrator.run_pipeline(
                    ctx,
                    on_status_change=on_status_change,
                    on_event=on_event,
                )
            )
            result = await pipeline_task
        except asyncio.CancelledError:
            if interrupted:
                # User pressed Ctrl-C - handle gracefully
                dashboard.stop_stage()
                dashboard.show_interrupt(
                    artifacts_path=str(ctx.artifacts_dir),
                    branch_name=ctx.branch_name or "unknown",
                )
                return
            raise  # Re-raise if not from our interrupt
        finally:
            # Cancel elapsed timer and wait for clean shutdown
            elapsed_task.cancel()
            try:
                await elapsed_task
            except asyncio.CancelledError:
                pass
            dashboard.stop_stage()
            loop.remove_signal_handler(signal.SIGINT)

        # Print result
        console.print()
        if result.status.value == "success":
            console.print("[bold green]✓ Pipeline completed successfully![/bold green]")
        else:
            dashboard.show_failure(
                stage=current_stage or "unknown",
                exit_code=None,
                error_msg=result.error_message or "Unknown error",
                artifacts_path=str(result.artifacts_dir),
            )

        console.print(f"\nArtifacts: {result.artifacts_dir}")
        if result.branch_name:
            console.print(f"Branch: {result.branch_name}")

    asyncio.run(run_async())


@app.command()
def batch(
    issues: Annotated[Optional[str], typer.Option("--issues", "-i", help="Comma-separated issue IDs (e.g., DIS-56,DIS-57,DIS-58)")] = None,
    team: Annotated[Optional[str], typer.Option("--team", "-t", help="Filter by team name")] = None,
    project: Annotated[Optional[str], typer.Option("--project", "-p", help="Filter by project name")] = None,
    state: Annotated[str, typer.Option("--state", "-s", help="Filter by state")] = "Todo",
    label: Annotated[Optional[str], typer.Option("--label", "-l", help="Filter by label")] = None,
    limit: Annotated[int, typer.Option("--limit", help="Max issues to process")] = 20,
    concurrency: Annotated[int, typer.Option("--concurrency", "-c", help="Concurrent runs (default 5)")] = 5,
    dry_run: Annotated[Optional[bool], typer.Option("--dry-run/--no-dry-run", help="Don't create branches or implement")] = None,
    max_iterations: Annotated[Optional[int], typer.Option("--max-iterations", help="Max plan iterations")] = None,
    confidence_threshold: Annotated[Optional[int], typer.Option("--confidence-threshold", help="Confidence threshold")] = None,
    stable_passes: Annotated[Optional[int], typer.Option("--stable-passes", help="Required stable passes")] = None,
    repo_root: Annotated[Optional[Path], typer.Option("--repo-root", help="Repository root")] = None,
    use_worktree: Annotated[Optional[bool], typer.Option("--use-worktree/--no-worktree", help="Use git worktree")] = None,
    no_linear_writeback: Annotated[bool, typer.Option("--no-linear-writeback", help="Don't comment on Linear")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Verbose output")] = False,
) -> None:
    """Run pipeline for multiple Linear issues with live dashboard."""
    settings = get_settings()

    # Apply defaults
    dry_run = dry_run if dry_run is not None else settings.dry_run_default
    max_iterations = max_iterations or settings.max_iterations_default
    confidence_threshold = confidence_threshold or settings.confidence_threshold_default
    stable_passes = stable_passes or settings.stable_passes_default
    use_worktree = use_worktree if use_worktree is not None else settings.use_worktree_default

    async def run_batch():
        # Fetch issues
        linear = LinearClient()

        if issues:
            # Explicit issue IDs provided
            issue_ids = [i.strip() for i in issues.split(",")]
            console.print(f"[bold]Fetching {len(issue_ids)} issues...[/bold]")
            issue_list = []
            for issue_id in issue_ids:
                try:
                    issue_list.append(await linear.get_issue(issue_id))
                except Exception as e:
                    console.print(f"[yellow]Warning: Could not fetch {issue_id}: {e}[/yellow]")
        else:
            # Query by filters
            console.print(f"[bold]Querying Linear issues...[/bold]")
            issue_list = await linear.list_issues(
                team=team,
                project=project,
                state=state,
                label=label,
                limit=limit,
            )

        if not issue_list:
            console.print("[yellow]No issues found matching criteria[/yellow]")
            return

        console.print(f"[green]Found {len(issue_list)} issues[/green]")
        log("BATCH", f"Processing {len(issue_list)} issues with concurrency {concurrency}")

        # Setup dashboard
        dashboard = Dashboard()
        dashboard.add_issues([(i.identifier, i.title) for i in issue_list])

        # Create orchestrator
        orchestrator = PipelineOrchestrator(repo_root=repo_root)

        # Semaphore for concurrency
        semaphore = asyncio.Semaphore(concurrency)

        async def process_issue(issue):
            async with semaphore:
                log("BATCH", f"Starting: {issue.identifier} - {issue.title[:50]}")
                dashboard.update(issue.identifier, status="planning", last_event="Starting...")

                ctx = await orchestrator.create_context(
                    issue,
                    dry_run=dry_run,
                    max_iterations=max_iterations,
                    confidence_threshold=confidence_threshold,
                    stable_passes=stable_passes,
                    use_worktree=use_worktree,
                    no_linear_writeback=no_linear_writeback,
                    verbose=verbose,
                )

                def on_status_change(c):
                    dashboard.update_from_context(c)

                def on_event(event_type, data):
                    dashboard.update(issue.identifier, last_event=event_type)

                try:
                    result = await orchestrator.run_pipeline(
                        ctx,
                        on_status_change=on_status_change,
                        on_event=on_event,
                    )
                    log("BATCH", f"Completed: {issue.identifier} -> {result.status.value}")
                except Exception as e:
                    log("BATCH", f"Failed: {issue.identifier} -> {str(e)[:50]}")
                    dashboard.update(
                        issue.identifier,
                        status="failed",
                        error=str(e),
                        last_event=f"Error: {str(e)[:20]}",
                    )
                finally:
                    # Clean up lock file for web UI idempotency
                    lock_path = ctx.artifacts_dir.parent / "locks" / f"{issue.identifier}.lock"
                    if lock_path.exists():
                        lock_path.unlink()

        # Run dashboard and processing concurrently
        async def run_all():
            tasks = [process_issue(issue) for issue in issue_list]
            await asyncio.gather(*tasks)

        # Start dashboard and processing
        dashboard_task = asyncio.create_task(dashboard.run())
        processing_task = asyncio.create_task(run_all())

        await processing_task
        dashboard.stop()
        await dashboard_task

        # Print summary
        console.print()
        console.print(f"[bold]Batch complete:[/bold] {dashboard.progress.completed} succeeded, {dashboard.progress.failed} failed")

    asyncio.run(run_batch())


@app.command()
def watch(
    run_id: Annotated[str, typer.Option("--run-id", "-r", help="Run ID to watch")],
) -> None:
    """Tail a run log and show latest status."""
    from ai_loop.integrations.git_tools import GitTools

    git = GitTools()
    artifacts = ArtifactManager(git.get_repo_root() / "artifacts")

    trace_events = artifacts.read_trace(run_id)

    if not trace_events:
        console.print(f"[yellow]No trace found for run: {run_id}[/yellow]")
        raise typer.Exit(1)

    console.print(f"[bold]Trace for run:[/bold] {run_id}")
    console.print()

    for event in trace_events:
        timestamp = event.timestamp.strftime("%H:%M:%S")
        console.print(f"[dim]{timestamp}[/dim] [{event.stage}] {event.event_type}")
        if event.data:
            for key, value in event.data.items():
                console.print(f"        {key}: {value}")


@app.command("list-runs")
def list_runs() -> None:
    """List recent runs."""
    from ai_loop.integrations.git_tools import GitTools

    try:
        git = GitTools()
        artifacts = ArtifactManager(git.get_repo_root() / "artifacts")
    except Exception:
        console.print("[yellow]Not in a git repository or no artifacts found[/yellow]")
        return

    runs = artifacts.list_runs()

    if not runs:
        console.print("[dim]No runs found[/dim]")
        return

    table = Table(title="Recent Runs")
    table.add_column("Run ID", style="cyan")
    table.add_column("Issue")
    table.add_column("Status")
    table.add_column("Iterations", justify="center")
    table.add_column("Confidence", justify="center")
    table.add_column("Branch")
    table.add_column("Completed")

    for run in runs[:20]:
        status_style = ""
        if run.status.value == "success":
            status_style = "green"
        elif run.status.value == "failed":
            status_style = "red"

        completed = run.completed_at.strftime("%Y-%m-%d %H:%M") if run.completed_at else "-"

        table.add_row(
            run.run_id[:30] + "..." if len(run.run_id) > 30 else run.run_id,
            run.issue_identifier,
            f"[{status_style}]{run.status.value}[/{status_style}]" if status_style else run.status.value,
            str(run.iterations),
            str(run.final_confidence) if run.final_confidence else "-",
            run.branch_name[:30] + "..." if len(run.branch_name) > 30 else run.branch_name,
            completed,
        )

    console.print(table)


@app.command()
def serve(
    port: Annotated[int, typer.Option("--port", "-p", help="Server port")] = 8080,
    open_browser: Annotated[bool, typer.Option("--open", help="Open browser")] = False,
    enable_writes: Annotated[bool, typer.Option("--enable-writes", help="Allow real implementations (not just dry-run)")] = False,
) -> None:
    """Start web dashboard server.

    By default, web-triggered runs are dry-run only (safe).
    Use --enable-writes to allow real branch creation and implementation.
    """
    from ai_loop.integrations.git_tools import GitTools
    from ai_loop.web.server import run_server

    git = GitTools()
    repo_root = git.get_repo_root()
    artifacts_dir = repo_root / "artifacts"
    artifacts_dir.mkdir(exist_ok=True)

    if open_browser:
        import webbrowser

        webbrowser.open(f"http://127.0.0.1:{port}")

    # Blocking call - runs until Ctrl-C
    run_server(
        port=port,
        artifacts_dir=artifacts_dir,
        repo_root=repo_root,
        enable_writes=enable_writes,
    )


if __name__ == "__main__":
    app()
