"""Minimal web server for AI Loop dashboard."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import signal
import subprocess
import threading
import time
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn
from typing import Any
from urllib.parse import parse_qs, urlparse

from ai_loop.core.logging import is_high_signal, log


# ---------------------------------------------------------------------------
# ProjectManager: Manages recent projects and last-used persistence
# ---------------------------------------------------------------------------

def _get_app_dir() -> Path:
    """Get platform-appropriate app config directory."""
    # Prefer XDG on Linux, ~/Library/Application Support on macOS, etc.
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif os.name == "posix" and "darwin" in os.uname().sysname.lower():
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "ai-loop"


class ProjectManager:
    """Manages known projects and current selection."""

    def __init__(self):
        self.config_path = _get_app_dir() / "projects.json"
        self.config = self._load_config()

    def _load_config(self) -> dict:
        """Load or create config with recent projects."""
        if self.config_path.exists():
            try:
                return json.loads(self.config_path.read_text())
            except (json.JSONDecodeError, IOError):
                pass
        return {"recent_projects": [], "last_project": None}

    def _save_config(self) -> None:
        """Persist config to disk."""
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(self.config, indent=2))

    def get_recent_projects(self) -> list[dict]:
        """Return recent projects with metadata (filtered to existing paths)."""
        valid = []
        for p in self.config.get("recent_projects", []):
            path = Path(p.get("path", ""))
            if path.exists() and (path / ".git").exists():
                valid.append(p)
        return valid[:10]  # Max 10 recents

    def add_project(self, path: Path) -> dict:
        """Add/update a project in recents."""
        path_str = str(path.resolve())
        entry = {
            "path": path_str,
            "name": path.name,
            "last_used": datetime.now().isoformat(),
        }

        # Remove existing entry for this path
        self.config["recent_projects"] = [
            p for p in self.config.get("recent_projects", [])
            if p.get("path") != path_str
        ]

        # Add to front
        self.config["recent_projects"].insert(0, entry)
        self.config["recent_projects"] = self.config["recent_projects"][:10]
        self.config["last_project"] = path_str

        self._save_config()
        return entry

    def get_last_project(self) -> Path | None:
        """Get the last used project path."""
        last = self.config.get("last_project")
        if last:
            p = Path(last)
            if p.exists() and (p / ".git").exists():
                return p
        return None


# Singleton project manager instance
_project_manager: ProjectManager | None = None


def get_project_manager() -> ProjectManager:
    """Get or create the singleton ProjectManager."""
    global _project_manager
    if _project_manager is None:
        _project_manager = ProjectManager()
    return _project_manager


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """HTTP server that handles each request in a separate thread."""
    daemon_threads = True

# UI version flag (default v1, set via --ui-version or AI_LOOP_UI_VERSION env)
UI_VERSION = os.environ.get("AI_LOOP_UI_VERSION", "v1")


class DashboardHandler(SimpleHTTPRequestHandler):
    """Handler for dashboard API and static files."""

    # Class-level config (set by run_server)
    artifacts_dir: Path = Path("artifacts")
    repo_root: Path = Path(".")
    enable_writes: bool = False
    csrf_token: str = ""
    port: int = 8080

    # Static directory for serving files
    _static_dir: str = ""

    def __init__(self, *args, **kwargs):
        # Set static dir before super().__init__ can set self.directory to cwd
        if not DashboardHandler._static_dir:
            DashboardHandler._static_dir = str(Path(__file__).parent / "static")
        super().__init__(*args, directory=DashboardHandler._static_dir, **kwargs)

    def _check_host(self) -> bool:
        """Strict host check - exact match only."""
        host = self.headers.get("Host", "")
        host_only = host.split(":")[0]  # Strip port
        if host_only not in ("127.0.0.1", "localhost"):
            self._send_json({"error": "forbidden"}, 403)
            return False
        return True

    def _check_origin(self) -> bool:
        """Verify Origin header for CSRF protection."""
        origin = self.headers.get("Origin", "")
        if not origin:
            return True  # No origin = same-origin or non-browser
        allowed = (f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}")
        if origin not in allowed:
            self._send_json({"error": "invalid origin"}, 403)
            return False
        return True

    def _check_csrf(self) -> bool:
        """Verify CSRF token for POST requests."""
        token = self.headers.get("X-CSRF-Token", "")
        if token != self.csrf_token:
            self._send_json({"error": "invalid csrf token"}, 403)
            return False
        return True

    def _verify_pid(self, pid: int, expected_cmd: list[str]) -> bool:
        """Verify PID still belongs to our process (guards against PID reuse)."""
        try:
            # Check process exists
            os.kill(pid, 0)

            # Verify command matches (macOS/Linux)
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                return False

            actual_cmd = result.stdout.strip()
            # Check if expected command substring is in actual
            # Use first few args to match (e.g. "ai-loop batch --issues")
            expected_substr = " ".join(expected_cmd[:3]) if len(expected_cmd) >= 3 else " ".join(expected_cmd)
            return expected_substr in actual_cmd
        except (OSError, ProcessLookupError):
            return False

    def do_GET(self):
        if not self._check_host():
            return
        if self.path == "/" or self.path == "/index.html":
            self._send_index_with_token()
        elif self.path.startswith("/api/events"):
            self._handle_sse()
        elif self.path.startswith("/api/issues"):
            self._send_issues_list()
        elif self.path == "/api/jobs":
            self._send_jobs_list()
        elif self.path.startswith("/api/runs"):
            # Handle both /api/runs and /api/runs?show_hidden=true
            parsed = urlparse(self.path)
            if parsed.path == "/api/runs":
                self._send_runs_list()
            else:
                # /api/runs/{run_id}
                run_id = parsed.path.split("/")[-1]
                self._send_run_detail(run_id)
        elif self.path == "/api/projects":
            self._send_projects_list()
        elif self.path == "/api/projects/current":
            self._send_current_project()
        else:
            super().do_GET()

    def do_POST(self):
        if not self._check_host():
            return
        if not self._check_origin():
            return
        if not self._check_csrf():
            return
        if self.path == "/api/runs":
            self._start_runs()
        elif self.path.startswith("/api/jobs/") and self.path.endswith("/stop"):
            job_id = self.path.split("/")[-2]
            self._stop_job(job_id)
        elif self.path.startswith("/api/jobs/") and self.path.endswith("/kill"):
            job_id = self.path.split("/")[-2]
            self._kill_job(job_id)
        elif self.path.startswith("/api/runs/") and self.path.endswith("/hide"):
            run_id = self.path.split("/")[-2]
            self._hide_run(run_id)
        elif self.path.startswith("/api/runs/") and self.path.endswith("/unhide"):
            run_id = self.path.split("/")[-2]
            self._unhide_run(run_id)
        elif self.path.startswith("/api/runs/") and self.path.endswith("/feedback"):
            run_id = self.path.split("/")[-2]
            self._submit_feedback(run_id)
        elif self.path.startswith("/api/runs/") and self.path.endswith("/config"):
            run_id = self.path.split("/")[-2]
            self._update_run_config(run_id)
        elif self.path == "/api/projects/switch":
            self._switch_project()

    def _send_index_with_token(self) -> None:
        """Serve index.html with CSRF token injected."""
        # Route to v2 if UI_VERSION is set
        if UI_VERSION == "v2":
            index_path = Path(__file__).parent / "static" / "v2" / "index.html"
        else:
            index_path = Path(__file__).parent / "static" / "index.html"
        html = index_path.read_text()
        # Inject token and mode
        html = html.replace("{{CSRF_TOKEN}}", self.csrf_token)
        html = html.replace("{{MODE}}", "write_enabled" if self.enable_writes else "dry_run")
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, data: Any, status: int = 200) -> None:
        """Send JSON response."""
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _handle_sse(self) -> None:
        """GET /api/events - Server-Sent Events stream.

        Tails trace.jsonl files for active runs.
        Supports replay via ?since=run_id:line_number
        """
        params = parse_qs(urlparse(self.path).query)
        since = params.get("since", [None])[0]

        # Parse replay position
        replay_positions: dict[str, int] = {}
        if since:
            for pos in since.split(","):
                if ":" in pos:
                    run_id, line_str = pos.rsplit(":", 1)
                    try:
                        replay_positions[run_id] = int(line_str)
                    except ValueError:
                        pass

        # Send SSE headers
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        # Track file positions for tailing
        file_positions: dict[str, int] = {}
        last_heartbeat = time.time()
        last_scan = 0.0

        # Send init event with current state
        init_data = self._build_sse_init()
        self._send_sse_event("init", init_data)

        try:
            while True:
                now = time.time()

                # Periodic scan for new run directories (every 10s)
                if now - last_scan > 10:
                    self._scan_for_new_runs(file_positions)
                    last_scan = now

                # Tail all active trace files
                events_sent = 0
                for run_id, position in list(file_positions.items()):
                    trace_path = self.artifacts_dir / run_id / "trace.jsonl"
                    if not trace_path.exists():
                        continue

                    try:
                        with open(trace_path, "r") as f:
                            f.seek(position)
                            while True:
                                line = f.readline()
                                if not line:
                                    break
                                line = line.strip()
                                if not line:
                                    continue

                                # Skip if replaying and before replay position
                                current_line = file_positions.get(f"{run_id}_line", 0) + 1
                                file_positions[f"{run_id}_line"] = current_line
                                if run_id in replay_positions and current_line <= replay_positions[run_id]:
                                    continue

                                try:
                                    event = json.loads(line)
                                    sse_event = self._trace_event_to_sse(run_id, event)
                                    if sse_event:
                                        event_type, event_data = sse_event
                                        event_data["_line"] = current_line
                                        self._send_sse_event(event_type, event_data, f"{run_id}:{current_line}")
                                        events_sent += 1
                                except json.JSONDecodeError:
                                    continue

                            file_positions[run_id] = f.tell()
                    except IOError:
                        continue

                    # Throttle: max 100 events per flush cycle
                    if events_sent >= 100:
                        break

                # Heartbeat every 30s
                if now - last_heartbeat > 30:
                    self._send_sse_event("heartbeat", {})
                    last_heartbeat = now

                # Flush and sleep (100ms flush frequency)
                self.wfile.flush()
                time.sleep(0.1)

        except (BrokenPipeError, ConnectionResetError):
            # Client disconnected
            pass

    def _build_sse_init(self) -> dict:
        """Build initial state for SSE init event."""
        runs = []
        if self.artifacts_dir.exists():
            for run_dir in sorted(self.artifacts_dir.iterdir(), reverse=True):
                if not run_dir.is_dir():
                    continue
                summary_path = run_dir / "summary.json"
                if not summary_path.exists():
                    continue
                try:
                    data = json.loads(summary_path.read_text())
                    if data.get("hidden_at"):
                        continue
                    # Map to run shape expected by UI
                    runs.append({
                        "run_id": data.get("run_id", run_dir.name),
                        "issue_identifier": data.get("issue_identifier", ""),
                        "issue_title": data.get("issue_title", ""),
                        "status": data.get("status", "unknown"),
                        "approval_mode": data.get("approval_mode", "auto"),
                        "iteration": data.get("iteration", 0),
                        "confidence": data.get("confidence"),
                        "started_at": data.get("started_at"),
                        "completed_at": data.get("completed_at"),
                        "gate_pending": self._get_gate_pending(run_dir),
                    })
                except (json.JSONDecodeError, IOError):
                    continue

        return {
            "mode": "write_enabled" if self.enable_writes else "dry_run",
            "runs": runs[:100],  # Max 100 runs in init
        }

    def _get_gate_pending(self, run_dir: Path) -> dict | None:
        """Check if a gate is pending for a run."""
        gate_path = run_dir / "gate_pending.json"
        if gate_path.exists():
            try:
                return json.loads(gate_path.read_text())
            except (json.JSONDecodeError, IOError):
                pass
        return None

    def _scan_for_new_runs(self, file_positions: dict[str, int]) -> None:
        """Scan artifacts dir for new run directories to tail."""
        if not self.artifacts_dir.exists():
            return

        for run_dir in self.artifacts_dir.iterdir():
            if not run_dir.is_dir():
                continue
            run_id = run_dir.name
            if run_id in file_positions:
                continue
            trace_path = run_dir / "trace.jsonl"
            if trace_path.exists():
                # Start tailing from current end
                file_positions[run_id] = trace_path.stat().st_size
                file_positions[f"{run_id}_line"] = sum(1 for _ in open(trace_path))

    def _trace_event_to_sse(self, run_id: str, event: dict) -> tuple[str, dict] | None:
        """Convert a trace event to an SSE event type and data."""
        event_type = event.get("event_type", event.get("type", ""))

        if event_type == "run_started":
            return ("run:created", {
                "run_id": run_id,
                "issue_identifier": event.get("issue_identifier", ""),
                "issue_title": event.get("issue_title", ""),
            })
        elif event_type == "status_change":
            return ("run:status", {
                "run_id": run_id,
                "status": event.get("status", ""),
                "iteration": event.get("iteration"),
                "confidence": event.get("confidence"),
            })
        elif event_type in ("stdout", "stderr", "output"):
            return ("run:output", {
                "run_id": run_id,
                "content": event.get("content", event.get("data", "")),
                "stream": event_type if event_type in ("stdout", "stderr") else "stdout",
            })
        elif event_type == "run_completed":
            return ("run:completed", {
                "run_id": run_id,
                "status": event.get("status", "completed"),
                "final_confidence": event.get("confidence"),
            })
        elif event_type == "gate_pending":
            return ("gate:pending", {
                "run_id": run_id,
                "gate_type": event.get("gate_type", ""),
                "critique": event.get("critique", {}),
            })
        elif event_type == "gate_resolved":
            return ("gate:resolved", {
                "run_id": run_id,
                "action": event.get("action", ""),
                "feedback": event.get("feedback", ""),
            })
        elif event_type == "error":
            return ("run:error", {
                "run_id": run_id,
                "error": event.get("error", event.get("message", "")),
            })

        return None

    def _send_sse_event(self, event_type: str, data: dict, event_id: str | None = None) -> None:
        """Send a single SSE event."""
        try:
            if event_id:
                self.wfile.write(f"id: {event_id}\n".encode())
            self.wfile.write(f"event: {event_type}\n".encode())
            self.wfile.write(f"data: {json.dumps(data)}\n\n".encode())
        except (BrokenPipeError, ConnectionResetError):
            raise

    def _submit_feedback(self, run_id: str) -> None:
        """POST /api/runs/{id}/feedback - submit gate resolution."""
        run_dir = self.artifacts_dir / run_id
        if not run_dir.exists():
            self._send_json({"error": "Run not found"}, 404)
            return

        gate_pending = run_dir / "gate_pending.json"
        if not gate_pending.exists():
            self._send_json({"error": "No gate pending"}, 400)
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length))

        action = body.get("action")
        if action not in ("approve", "reject", "request_changes"):
            self._send_json({"error": "Invalid action"}, 400)
            return

        feedback = body.get("feedback", "")

        # Write resolution file
        resolution = {
            "action": action,
            "feedback": feedback,
            "resolved_at": datetime.now().isoformat(),
        }
        resolution_path = run_dir / "gate_resolution.json"
        resolution_path.write_text(json.dumps(resolution, indent=2))

        self._send_json({"resolved": True, "run_id": run_id, "action": action})

    def _update_run_config(self, run_id: str) -> None:
        """POST /api/runs/{id}/config - update run configuration."""
        run_dir = self.artifacts_dir / run_id
        summary_path = run_dir / "summary.json"

        if not summary_path.exists():
            self._send_json({"error": "Run not found"}, 404)
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length))

        approval_mode = body.get("approval_mode")
        if approval_mode and approval_mode not in ("auto", "gate_on_fail", "always_gate"):
            self._send_json({"error": "Invalid approval_mode"}, 400)
            return

        try:
            summary = json.loads(summary_path.read_text())
            if approval_mode:
                summary["approval_mode"] = approval_mode
            summary_path.write_text(json.dumps(summary, indent=2))
            self._send_json({"updated": True, "run_id": run_id, "approval_mode": approval_mode})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _send_issues_list(self) -> None:
        """GET /api/issues - list issues from Linear."""
        import asyncio
        import traceback

        params = parse_qs(urlparse(self.path).query)
        state = params.get("state", ["Todo"])[0]
        team = params.get("team", [None])[0]
        project = params.get("project", [None])[0]
        limit = int(params.get("limit", ["20"])[0])

        log("API", f"GET /api/issues state={state} team={team} project={project} limit={limit}")

        async def fetch():
            from ai_loop.integrations.linear import LinearClient

            client = LinearClient()
            return await client.list_issues(state=state, team=team, project=project, limit=limit)

        try:
            issues = asyncio.run(fetch())
            log("API", f"Found {len(issues)} issues")
            self._send_json([
                {
                    "identifier": i.identifier,
                    "title": i.title,
                    "state": i.state,
                    "priority": i.priority,
                    "team_name": i.team_name,
                    "labels": i.labels,
                }
                for i in issues
            ])
        except Exception as e:
            log("ERROR", f"Fetching issues: {e}")
            traceback.print_exc()
            self._send_json({"error": str(e)}, 500)

    def _send_jobs_list(self) -> None:
        """GET /api/jobs - list jobs with verified status."""
        jobs = []
        jobs_dir = self.artifacts_dir / "jobs"
        if not jobs_dir.exists():
            self._send_json(jobs)
            return

        for job_file in jobs_dir.glob("*.json"):
            try:
                data = json.loads(job_file.read_text())
                pid = data.get("pid")
                cmd = data.get("cmd", [])

                # Verify process is actually ours
                if self._verify_pid(pid, cmd):
                    if data.get("stop_requested_at"):
                        data["status"] = "stopping"
                    else:
                        data["status"] = "running"
                else:
                    # Process not running - determine final status
                    if data.get("status") == "stopping":
                        data["status"] = "stopped"
                    elif data.get("status") == "running":
                        data["status"] = "completed"
                    # Clean up locks for completed/stopped jobs
                    self._cleanup_job_locks(data.get("job_id", ""), data.get("issues", []))

                jobs.append(data)
            except (json.JSONDecodeError, IOError):
                continue

        self._send_json(jobs)

    def _cleanup_job_locks(self, job_id: str, issues: list[str]) -> None:
        """Remove only locks owned by this job."""
        locks_dir = self.artifacts_dir / "locks"
        for issue_id in issues:
            lock_path = locks_dir / f"{issue_id}.lock"
            if not lock_path.exists():
                continue
            try:
                lock_data = json.loads(lock_path.read_text())
                # Only delete if we own it
                if lock_data.get("job_id") == job_id:
                    lock_path.unlink()
            except (json.JSONDecodeError, IOError):
                continue  # Don't delete locks we can't verify

    def _stop_job(self, job_id: str) -> None:
        """POST /api/jobs/{id}/stop - request graceful stop (SIGTERM)."""
        job_file = self.artifacts_dir / "jobs" / f"{job_id}.json"
        if not job_file.exists():
            self._send_json({"error": "Job not found"}, 404)
            return

        try:
            data = json.loads(job_file.read_text())
            pid = data.get("pid")
            cmd = data.get("cmd", [])

            # Already stopping?
            if data.get("stop_requested_at"):
                self._send_json({"status": "stopping", "job_id": job_id})
                return

            # Verify PID belongs to us before sending signal
            if not self._verify_pid(pid, cmd):
                # Process already dead - clean up
                self._cleanup_job_locks(job_id, data.get("issues", []))
                data["status"] = "stopped"
                data["stopped_at"] = datetime.now().isoformat()
                job_file.write_text(json.dumps(data, indent=2))
                self._send_json({"status": "stopped", "job_id": job_id})
                return

            # Mark stop requested BEFORE sending signal
            data["stop_requested_at"] = datetime.now().isoformat()
            data["status"] = "stopping"
            job_file.write_text(json.dumps(data, indent=2))

            # Send SIGTERM to process group
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass  # Race condition - already dead

            # Return immediately - UI will poll until stopped
            self._send_json({"status": "stopping", "job_id": job_id})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _kill_job(self, job_id: str) -> None:
        """POST /api/jobs/{id}/kill - force kill (SIGKILL). Requires prior stop request."""
        job_file = self.artifacts_dir / "jobs" / f"{job_id}.json"
        if not job_file.exists():
            self._send_json({"error": "Job not found"}, 404)
            return

        try:
            data = json.loads(job_file.read_text())
            pid = data.get("pid")
            cmd = data.get("cmd", [])
            issues = data.get("issues", [])

            # Must have requested stop first (safety interlock)
            if not data.get("stop_requested_at"):
                self._send_json({"error": "Must request stop before kill"}, 400)
                return

            # Verify and kill
            if self._verify_pid(pid, cmd):
                try:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    pass

            # Clean up owned locks only
            self._cleanup_job_locks(job_id, issues)

            # Update status
            data["status"] = "stopped"
            data["stopped_at"] = datetime.now().isoformat()
            data["killed"] = True
            job_file.write_text(json.dumps(data, indent=2))

            self._send_json({"status": "stopped", "job_id": job_id, "killed": True})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _hide_run(self, run_id: str) -> None:
        """POST /api/runs/{id}/hide - hide run from default view."""
        summary_path = self.artifacts_dir / run_id / "summary.json"
        if not summary_path.exists():
            self._send_json({"error": "Run not found"}, 404)
            return

        try:
            summary = json.loads(summary_path.read_text())
            summary["hidden_at"] = datetime.now().isoformat()
            summary_path.write_text(json.dumps(summary, indent=2))
            self._send_json({"hidden": True, "run_id": run_id})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _unhide_run(self, run_id: str) -> None:
        """POST /api/runs/{id}/unhide - restore hidden run."""
        summary_path = self.artifacts_dir / run_id / "summary.json"
        if not summary_path.exists():
            self._send_json({"error": "Run not found"}, 404)
            return

        try:
            summary = json.loads(summary_path.read_text())
            summary.pop("hidden_at", None)
            summary_path.write_text(json.dumps(summary, indent=2))
            self._send_json({"hidden": False, "run_id": run_id})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _start_runs(self) -> None:
        """POST /api/runs - spawn CLI subprocess with lock-based idempotency."""
        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length))

        issue_ids = body.get("issue_identifiers", [])
        concurrency = body.get("concurrency", 3)
        job_id = secrets.token_hex(8)
        mode = "write_enabled" if self.enable_writes else "dry_run"

        log("API", f"POST /api/runs issues={issue_ids} concurrency={concurrency}")

        if not issue_ids:
            self._send_json({"error": "issue_identifiers required"}, 400)
            return

        # Try to acquire locks for each issue (atomic)
        locks_dir = self.artifacts_dir / "locks"
        locks_dir.mkdir(exist_ok=True)

        started = []
        rejected = []
        reason_by_issue = {}

        for issue_id in issue_ids:
            lock_path = locks_dir / f"{issue_id}.lock"
            try:
                # Atomic create - fails if exists
                fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                lock_data = json.dumps({
                    "job_id": job_id,
                    "pid": None,  # Will be updated after spawn
                    "created_at": datetime.now().isoformat(),
                })
                os.write(fd, lock_data.encode())
                os.close(fd)
                started.append(issue_id)
            except FileExistsError:
                # Read existing lock to report which job owns it
                try:
                    existing = json.loads(lock_path.read_text())
                    reason_by_issue[issue_id] = f"locked by job {existing.get('job_id', 'unknown')[:8]}"
                except (json.JSONDecodeError, IOError):
                    reason_by_issue[issue_id] = "already running"
                rejected.append(issue_id)

        log("API", f"Acquired locks: {started}")
        if rejected:
            log("API", f"Rejected: {rejected}")

        if not started:
            self._send_json({
                "job_id": job_id,
                "mode": mode,
                "started": [],
                "rejected": rejected,
                "reason_by_issue": reason_by_issue,
            })
            return

        # Find ai-loop executable (don't hardcode uv)
        ai_loop_path = shutil.which("ai-loop")
        if ai_loop_path:
            cmd = [ai_loop_path]
        else:
            # Fallback to uv run
            cmd = ["uv", "run", "--project", str(self.repo_root / "ai-loop"), "ai-loop"]

        cmd.extend([
            "batch",
            "--issues", ",".join(started),
            "--concurrency", str(concurrency),
        ])
        if not self.enable_writes:
            cmd.append("--dry-run")

        log("API", f"Spawning: {' '.join(cmd)}")

        # Create log file for this job
        jobs_dir = self.artifacts_dir / "jobs"
        jobs_dir.mkdir(exist_ok=True)
        log_path = jobs_dir / f"{job_id}.log"

        # Spawn subprocess with captured output
        proc = subprocess.Popen(
            cmd,
            cwd=str(self.repo_root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # Merge stderr into stdout
            start_new_session=True,
            text=True,
            bufsize=1,  # Line buffered
        )

        # Background thread: filtered tee - ALL output to log file, only HIGH-SIGNAL to terminal
        def tee_output():
            try:
                with open(log_path, "w") as log_file:
                    for line in proc.stdout:
                        log_file.write(line)  # ALWAYS to file
                        log_file.flush()
                        if is_high_signal(line):  # Only high-signal to terminal
                            print(line, end="", flush=True)
            except Exception as e:
                log("ERROR", f"Tee output failed: {e}")

        threading.Thread(target=tee_output, daemon=True).start()

        log("API", f"Job {job_id[:8]} started, PID={proc.pid}")

        # Update lock files with PID (for ownership verification)
        for issue_id in started:
            lock_path = locks_dir / f"{issue_id}.lock"
            try:
                lock_data = json.loads(lock_path.read_text())
                lock_data["pid"] = proc.pid
                lock_path.write_text(json.dumps(lock_data))
            except (json.JSONDecodeError, IOError):
                pass  # Best effort

        # Record job metadata with enhanced fields
        (jobs_dir / f"{job_id}.json").write_text(json.dumps({
            "job_id": job_id,
            "pid": proc.pid,
            "issues": started,
            "started_at": datetime.now().isoformat(),
            "mode": mode,
            "cmd": cmd,
            "cwd": str(self.repo_root),
            "status": "running",
            "stop_requested_at": None,
            "log_path": str(log_path),
        }))

        # Return stubs keyed by temp_id (issue_identifier)
        # UI will upgrade to real run_id when SSE sends run:created
        run_stubs = []
        for issue_id in started:
            run_stubs.append({
                "temp_id": issue_id,  # KEY: use issue_identifier, not fake run_id
                "issue_identifier": issue_id,
                "status": "pending",
            })

        self._send_json({
            "job_id": job_id,
            "mode": mode,
            "started": started,
            "rejected": rejected,
            "reason_by_issue": reason_by_issue,
            "stubs": run_stubs,  # Renamed from "runs" to be clear these are stubs
        })

    def _send_runs_list(self) -> None:
        """GET /api/runs - list runs, with optional show_hidden."""
        params = parse_qs(urlparse(self.path).query)
        show_hidden = params.get("show_hidden", ["false"])[0] == "true"

        runs = []
        if not self.artifacts_dir.exists():
            self._send_json(runs)
            return

        for run_dir in sorted(self.artifacts_dir.iterdir(), reverse=True):
            if not run_dir.is_dir():
                continue
            summary_path = run_dir / "summary.json"
            if not summary_path.exists():
                continue
            try:
                data = json.loads(summary_path.read_text())
                # Skip hidden unless show_hidden=true
                if data.get("hidden_at") and not show_hidden:
                    continue
                runs.append(data)
            except (json.JSONDecodeError, IOError):
                continue

        self._send_json(runs)

    def _send_run_detail(self, run_id: str) -> None:
        """GET /api/runs/{run_id} - run details + recent events."""
        run_dir = self.artifacts_dir / run_id
        summary_path = run_dir / "summary.json"

        if not summary_path.exists():
            self._send_json({"error": "Run not found"}, 404)
            return

        try:
            summary = json.loads(summary_path.read_text())
        except (json.JSONDecodeError, IOError):
            self._send_json({"error": "Failed to read summary"}, 500)
            return

        # Read last 50 trace events
        events = []
        trace_path = run_dir / "trace.jsonl"
        if trace_path.exists():
            lines = trace_path.read_text().splitlines()
            for line in lines[-50:]:
                if line.strip():
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        # Find latest plan and critique
        plan_path = None
        critique_path = None
        for f in sorted(run_dir.glob("plan_v*.md"), reverse=True):
            plan_path = str(f.relative_to(self.artifacts_dir))
            break
        for f in sorted(run_dir.glob("plan_gate_v*.json"), reverse=True):
            critique_path = str(f.relative_to(self.artifacts_dir))
            break

        self._send_json({
            "summary": summary,
            "recent_events": events,
            "current_plan_path": plan_path,
            "latest_critique_path": critique_path,
        })

    # ---------------------------------------------------------------------------
    # Project Management API
    # ---------------------------------------------------------------------------

    def _send_projects_list(self) -> None:
        """GET /api/projects - list recent projects."""
        pm = get_project_manager()
        projects = pm.get_recent_projects()
        self._send_json({"projects": projects})

    def _send_current_project(self) -> None:
        """GET /api/projects/current - get current project info."""
        self._send_json({
            "path": str(self.repo_root),
            "name": self.repo_root.name,
            "artifacts_dir": str(self.artifacts_dir),
        })

    def _get_active_jobs(self) -> list[dict]:
        """Get list of currently running jobs."""
        jobs = []
        jobs_dir = self.artifacts_dir / "jobs"
        if not jobs_dir.exists():
            return jobs

        for job_file in jobs_dir.glob("*.json"):
            try:
                data = json.loads(job_file.read_text())
                pid = data.get("pid")
                cmd = data.get("cmd", [])

                # Only include if process is actually running
                if self._verify_pid(pid, cmd):
                    jobs.append(data)
            except (json.JSONDecodeError, IOError):
                continue

        return jobs

    def _switch_project(self) -> None:
        """POST /api/projects/switch - switch current project context.

        Hard contract:
        - Returns 409 if any job is running
        - Returns 200 with project info + reconnect flag on success
        """
        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length)) if content_length else {}

        new_path_str = body.get("path", "")
        if not new_path_str:
            self._send_json({"error": "path required"}, 400)
            return

        new_path = Path(new_path_str)

        # Validate: exists, is directory
        if not new_path.exists() or not new_path.is_dir():
            self._send_json({"error": "Directory not found"}, 404)
            return

        # Validate: is git repo
        if not (new_path / ".git").is_dir():
            self._send_json({"error": "Not a git repository"}, 400)
            return

        # Hard contract: refuse if any job is running
        active_jobs = self._get_active_jobs()
        if active_jobs:
            job_ids = [j.get("job_id", "unknown")[:8] for j in active_jobs]
            self._send_json({
                "error": "Stop runs to switch projects",
                "active_jobs": job_ids,
            }, 409)
            return

        # Resolve and switch
        new_path = new_path.resolve()

        # Update class-level config (affects all handlers)
        DashboardHandler.repo_root = new_path
        DashboardHandler.artifacts_dir = new_path / "artifacts"
        DashboardHandler.artifacts_dir.mkdir(exist_ok=True)

        # Persist to config
        pm = get_project_manager()
        entry = pm.add_project(new_path)

        log("API", f"Switched project to: {new_path}")

        self._send_json({
            "project": entry,
            "artifacts_dir": str(DashboardHandler.artifacts_dir),
            "reconnect": True,
        })

    def log_message(self, format: str, *args) -> None:
        """Suppress request logging."""
        pass


def start_server(port: int, artifacts_dir: Path) -> threading.Thread:
    """Start dashboard server in background thread."""
    DashboardHandler.artifacts_dir = artifacts_dir

    server = ThreadingHTTPServer(("", port), DashboardHandler)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def _ensure_static_permissions(static_dir: Path) -> None:
    """Ensure static assets are world-readable. Self-heals bad permissions."""
    fixed = 0
    for path in static_dir.rglob("*"):
        if path.is_file():
            if not os.access(path, os.R_OK) or (path.stat().st_mode & 0o444) != 0o444:
                path.chmod(0o644)
                fixed += 1
        elif path.is_dir():
            if (path.stat().st_mode & 0o555) != 0o555:
                path.chmod(0o755)
                fixed += 1
    if fixed:
        print(f"  Fixed {fixed} static asset permissions")


def _kill_port_process(port: int) -> None:
    """Kill any existing process using the given port."""
    import platform

    try:
        if platform.system() == "Darwin" or platform.system() == "Linux":
            # Use lsof to find process on port, then kill it
            result = subprocess.run(
                ["lsof", "-ti", f":{port}"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                pids = result.stdout.strip().split("\n")
                for pid in pids:
                    if pid:
                        try:
                            os.kill(int(pid), signal.SIGTERM)
                            time.sleep(0.1)  # Brief wait for cleanup
                        except (ProcessLookupError, ValueError):
                            pass
        elif platform.system() == "Windows":
            # Windows: use netstat and taskkill
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True,
                text=True,
            )
            for line in result.stdout.split("\n"):
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.split()
                    if parts:
                        pid = parts[-1]
                        subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
    except Exception:
        pass  # Best effort - if it fails, socket bind will raise anyway


def run_server(
    port: int, artifacts_dir: Path, repo_root: Path, enable_writes: bool = False
) -> None:
    """Run the dashboard server (blocking).

    This is for the `serve` command - runs until Ctrl-C.
    """
    # Check required API keys
    print("\n=== AI Loop Dashboard ===")
    print(f"Repo root: {repo_root}")
    print(f"Artifacts: {artifacts_dir}")

    # Ensure static files are readable (self-healing for bad perms)
    static_dir = Path(__file__).parent / "static"
    _ensure_static_permissions(static_dir)

    linear_key = os.environ.get("LINEAR_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")

    print("\nAPI Keys:")
    if linear_key:
        print(f"  LINEAR_API_KEY: {linear_key[:8]}...{linear_key[-4:]}")
    else:
        print("  LINEAR_API_KEY: ❌ MISSING - issues will fail to load")

    if openai_key:
        print(f"  OPENAI_API_KEY: {openai_key[:8]}...{openai_key[-4:]}")
    else:
        print("  OPENAI_API_KEY: ⚠️  missing (optional, for critique gates)")

    print("  Claude Code: ✓ uses your authenticated session")

    print()

    DashboardHandler.artifacts_dir = artifacts_dir
    DashboardHandler.repo_root = repo_root
    DashboardHandler.enable_writes = enable_writes
    DashboardHandler.csrf_token = secrets.token_hex(16)
    DashboardHandler.port = port

    # Kill any existing process on this port
    _kill_port_process(port)

    # Bind to loopback only for security
    server = ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler)
    mode_str = "WRITE ENABLED" if enable_writes else "dry-run only"
    print(f"Dashboard: http://127.0.0.1:{port}")
    print(f"Mode: {mode_str}")
    print()
    server.serve_forever()
