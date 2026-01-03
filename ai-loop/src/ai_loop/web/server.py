"""Minimal web server for AI Loop dashboard."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import signal
import subprocess
import threading
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


class DashboardHandler(SimpleHTTPRequestHandler):
    """Handler for dashboard API and static files."""

    # Class-level config (set by run_server)
    artifacts_dir: Path = Path("artifacts")
    repo_root: Path = Path(".")
    enable_writes: bool = False
    csrf_token: str = ""
    port: int = 8080

    def __init__(self, *args, **kwargs):
        # Set static directory
        self.directory = str(Path(__file__).parent / "static")
        super().__init__(*args, **kwargs)

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

    def _send_index_with_token(self) -> None:
        """Serve index.html with CSRF token injected."""
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

    def _send_issues_list(self) -> None:
        """GET /api/issues - list issues from Linear."""
        import asyncio
        import traceback

        params = parse_qs(urlparse(self.path).query)
        state = params.get("state", ["Todo"])[0]
        team = params.get("team", [None])[0]
        project = params.get("project", [None])[0]
        limit = int(params.get("limit", ["20"])[0])

        print(f"[API] GET /api/issues state={state} team={team} project={project} limit={limit}")

        async def fetch():
            from ai_loop.integrations.linear import LinearClient

            client = LinearClient()
            return await client.list_issues(state=state, team=team, project=project, limit=limit)

        try:
            issues = asyncio.run(fetch())
            print(f"[API] Found {len(issues)} issues")
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
            print(f"[API] ERROR fetching issues: {e}")
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

        # Spawn detached subprocess
        proc = subprocess.Popen(
            cmd,
            cwd=str(self.repo_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

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
        jobs_dir = self.artifacts_dir / "jobs"
        jobs_dir.mkdir(exist_ok=True)
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
        }))

        self._send_json({
            "job_id": job_id,
            "mode": mode,
            "started": started,
            "rejected": rejected,
            "reason_by_issue": reason_by_issue,
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

    def log_message(self, format: str, *args) -> None:
        """Suppress request logging."""
        pass


def start_server(port: int, artifacts_dir: Path) -> threading.Thread:
    """Start dashboard server in background thread."""
    DashboardHandler.artifacts_dir = artifacts_dir

    server = HTTPServer(("", port), DashboardHandler)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


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

    # Bind to loopback only for security
    server = HTTPServer(("127.0.0.1", port), DashboardHandler)
    mode_str = "WRITE ENABLED" if enable_writes else "dry-run only"
    print(f"Dashboard: http://127.0.0.1:{port}")
    print(f"Mode: {mode_str}")
    print()
    server.serve_forever()
