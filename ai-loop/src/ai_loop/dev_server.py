"""Dev server with auto-reload support.

This module provides:
1. run_with_autoreload() - Parent process with watchfiles watcher
2. Entry point for child process (python -m ai_loop.dev_server)

Architecture:
- Parent owns browser + tokens - Child process never opens browser, never generates tokens
- Tokens passed via env vars - Stable across restarts, browser session survives
- Subprocess command approach - Most reliable watchfiles pattern
"""

from __future__ import annotations

import atexit
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

from rich.console import Console

console = Console()

# Paths to ignore (generated files, venvs, caches)
IGNORE_PATTERNS = {
    "/.venv/",
    "/.git/",
    "/__pycache__/",
    "/dist/",
    "/build/",
    "/.mypy_cache/",
    "/.pytest_cache/",
    "/.ruff_cache/",
    "/node_modules/",
    "/artifacts/",
}


def should_watch(change, path: str) -> bool:
    """Watch only .py files, excluding generated/cache dirs."""
    if not path.endswith(".py"):
        return False
    for pattern in IGNORE_PATTERNS:
        if pattern in path:
            return False
    return True


def run_with_autoreload(
    port: int,
    pairing_token: str,
    no_open: bool,
    repo_root: Path,
    artifacts_dir: Path,
) -> None:
    """Run server with watchfiles auto-reload.

    Uses lower-level subprocess + watchfiles.watch() for maximum reliability:
    - We control the subprocess lifecycle directly (no API surprises)
    - Watch only .py files (not static assets)
    - Ignore __pycache__, .venv, .git, build dirs
    - Wait for server ready (HTTP probe, not just TCP) before opening browser
    - Clean SIGTERM on changes, SIGKILL if stuck
    - atexit + SIGINT ensure child is always reaped
    """
    import watchfiles

    src_path = Path(__file__).parent  # src/ai_loop/

    # Pass config to child via env vars
    # Note: repo_root/artifacts_dir are NOT passed - let server restore from last project
    env = os.environ.copy()
    env["AI_LOOP_DEV_MODE"] = "1"
    env["AI_LOOP_PORT"] = str(port)
    env["AI_LOOP_PAIRING_TOKEN"] = pairing_token
    env["AI_LOOP_UI_VERSION"] = "v2"  # Explicit: always use v2 in dev mode

    process = None
    browser_opened = False

    def start_server():
        """Start the child server process."""
        nonlocal process
        process = subprocess.Popen(
            [sys.executable, "-m", "ai_loop.dev_server"],
            env=env,
        )

    def stop_server():
        """Stop the child server process gracefully."""
        nonlocal process
        if process and process.poll() is None:
            process.terminate()  # SIGTERM
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()  # SIGKILL if stuck
                process.wait()
        process = None

    def wait_for_ready(timeout: float = 5.0) -> bool:
        """Wait until server is FULLY ready (not just TCP accept).

        Probes /api/status and requires 200 + {"ready": true}.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                # First check TCP (fast fail if nothing listening)
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    pass
                # Then check HTTP endpoint (app routes ready)
                req = urllib.request.Request(f"http://127.0.0.1:{port}/api/status")
                with urllib.request.urlopen(req, timeout=0.5) as resp:
                    if resp.status == 200:
                        data = json.loads(resp.read())
                        if data.get("ready"):
                            return True
            except Exception:
                pass
            time.sleep(0.05)
        return False

    # Ensure child is ALWAYS reaped, even if watcher throws or parent is killed
    atexit.register(stop_server)

    def sigint_handler(signum, frame):
        """Handle Ctrl+C: stop child, then exit."""
        console.print("\n[dim]Shutting down...[/dim]")
        stop_server()
        sys.exit(0)

    signal.signal(signal.SIGINT, sigint_handler)

    console.print(f"[green]✓[/green] Watching {src_path}/**/*.py for changes...")

    try:
        # Start initial server
        start_server()

        # Wait for ready (HTTP probe), then open browser
        if wait_for_ready():
            console.print("[green]✓[/green] Server ready")
            if not no_open and not browser_opened:
                url = f"http://127.0.0.1:{port}?token={pairing_token}"
                webbrowser.open(url)
                console.print("[green]✓[/green] Opening browser...")
                browser_opened = True
        else:
            console.print("[red]✗[/red] Server failed to start")

        # Watch for changes - .py only, excluding cache/venv dirs
        for changes in watchfiles.watch(src_path, watch_filter=should_watch):
            changed_files = [str(p) for _, p in changes]
            console.print(
                f"[yellow]⟳[/yellow] {len(changes)} file(s) changed, restarting..."
            )
            for f in changed_files[:3]:
                console.print(f"    [dim]{Path(f).name}[/dim]")
            if len(changed_files) > 3:
                console.print(f"    [dim]... and {len(changed_files) - 3} more[/dim]")

            stop_server()
            start_server()

            if wait_for_ready():
                console.print("[green]✓[/green] Server restarted")
            else:
                console.print("[red]✗[/red] Server failed to restart")

    finally:
        stop_server()


# ---------------------------------------------------------------------------
# Child process entry point (python -m ai_loop.dev_server)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    """Child process entry point - reads config from env vars."""
    from ai_loop.web.server import create_server

    port = int(os.environ["AI_LOOP_PORT"])
    pairing_token = os.environ["AI_LOOP_PAIRING_TOKEN"]

    # Allow repo_root to be restored from last project if not explicitly set
    repo_root_env = os.environ.get("AI_LOOP_REPO_ROOT", "")
    repo_root = Path(repo_root_env) if repo_root_env else None

    artifacts_dir_env = os.environ.get("AI_LOOP_ARTIFACTS_DIR", "")
    artifacts_dir = Path(artifacts_dir_env) if artifacts_dir_env else None

    # Clean shutdown on SIGTERM (sent by parent before restart)
    server = None

    def shutdown_handler(signum, frame):
        if server:
            server.shutdown()
            server.server_close()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown_handler)

    # Create server with pairing token from parent
    server = create_server(
        port=port,
        dev_mode=True,
        pairing_token=pairing_token,
        artifacts_dir=artifacts_dir,
        repo_root=repo_root,
    )

    try:
        server.serve_forever()
    finally:
        server.shutdown()
        server.server_close()
