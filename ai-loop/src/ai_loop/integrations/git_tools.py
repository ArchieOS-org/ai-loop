"""Git operations for branch isolation and worktrees."""

from __future__ import annotations

import asyncio
import subprocess
from datetime import datetime
from pathlib import Path


class GitTools:
    """Git operations helper."""

    def __init__(self, repo_root: Path | None = None):
        self.repo_root = repo_root or self._detect_repo_root()

    @staticmethod
    def _detect_repo_root() -> Path:
        """Detect git repository root."""
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        return Path(result.stdout.strip())

    def _run_git(self, *args: str, cwd: Path | None = None) -> str:
        """Run a git command and return stdout."""
        result = subprocess.run(
            ["git", *args],
            cwd=cwd or self.repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    async def _run_git_async(self, *args: str, cwd: Path | None = None) -> str:
        """Run a git command asynchronously."""
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=cwd or self.repo_root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"Git error: {stderr.decode()}")
        return stdout.decode().strip()

    def generate_branch_name(self, issue_identifier: str) -> str:
        """Generate a branch name for an issue."""
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        # Sanitize identifier
        safe_id = issue_identifier.replace("/", "-").lower()
        return f"agent/{safe_id}-jobs-grade-{timestamp}"

    async def create_branch(self, branch_name: str) -> None:
        """Create and checkout a new branch."""
        await self._run_git_async("checkout", "-b", branch_name)

    async def create_worktree(
        self,
        branch_name: str,
        worktree_dir: Path,
    ) -> None:
        """Create a git worktree with a new branch."""
        worktree_dir.parent.mkdir(parents=True, exist_ok=True)
        await self._run_git_async(
            "worktree",
            "add",
            "-b",
            branch_name,
            str(worktree_dir),
        )

    async def remove_worktree(self, worktree_dir: Path) -> None:
        """Remove a git worktree."""
        await self._run_git_async("worktree", "remove", str(worktree_dir), "--force")

    async def get_diff(self, cwd: Path | None = None) -> str:
        """Get the diff of all changes (staged and unstaged)."""
        # Get diff against main/master
        try:
            base = await self._run_git_async("merge-base", "HEAD", "main", cwd=cwd)
        except RuntimeError:
            try:
                base = await self._run_git_async("merge-base", "HEAD", "master", cwd=cwd)
            except RuntimeError:
                base = "HEAD~1"

        return await self._run_git_async("diff", base, "HEAD", cwd=cwd)

    async def get_current_branch(self, cwd: Path | None = None) -> str:
        """Get the current branch name."""
        return await self._run_git_async("branch", "--show-current", cwd=cwd)

    async def has_changes(self, cwd: Path | None = None) -> bool:
        """Check if there are uncommitted changes."""
        status = await self._run_git_async("status", "--porcelain", cwd=cwd)
        return bool(status.strip())

    async def commit_all(self, message: str, cwd: Path | None = None) -> None:
        """Stage and commit all changes."""
        await self._run_git_async("add", "-A", cwd=cwd)
        await self._run_git_async("commit", "-m", message, cwd=cwd)

    def get_repo_root(self) -> Path:
        """Return the repository root path."""
        return self.repo_root
