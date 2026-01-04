"""Tests for Codex runner capability detection."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from ai_loop.integrations.codex_runner import (
    CodexRunner,
    _CODEX_CAPS_CACHE,
    _DETECTION_TIMEOUT,
)


@pytest.fixture(autouse=True)
def clear_caps_cache():
    """Clear the module-level cache before each test."""
    _CODEX_CAPS_CACHE.clear()
    yield
    _CODEX_CAPS_CACHE.clear()


class TestCodexCapabilityDetection:
    """Tests for _detect_codex_capabilities."""

    @pytest.mark.asyncio
    async def test_detects_approval_mode_from_stdout(self):
        """Should detect --approval-mode from stdout."""
        runner = CodexRunner(cmd="codex")

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(
            b"Usage: codex exec [OPTIONS]\n  --approval-mode <MODE>\n  --json\n",
            b"",
        ))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with patch("asyncio.wait_for", return_value=mock_proc.communicate.return_value):
                caps = await runner._detect_codex_capabilities()

        assert caps["approval_mode"] is True
        assert caps["json"] is True

    @pytest.mark.asyncio
    async def test_detects_approval_mode_from_stderr(self):
        """Should detect --approval-mode from stderr (some CLIs output help there)."""
        runner = CodexRunner(cmd="codex")

        mock_proc = AsyncMock()
        # Help output on stderr, empty stdout
        mock_proc.communicate = AsyncMock(return_value=(
            b"",
            b"Usage: codex exec [OPTIONS]\n  --approval-mode <MODE>\n  --full-auto\n",
        ))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with patch("asyncio.wait_for", return_value=mock_proc.communicate.return_value):
                caps = await runner._detect_codex_capabilities()

        assert caps["approval_mode"] is True
        assert caps["full_auto"] is True

    @pytest.mark.asyncio
    async def test_detects_full_auto_when_approval_mode_absent(self):
        """Should detect --full-auto when --approval-mode is not available."""
        runner = CodexRunner(cmd="codex")

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(
            b"Usage: codex exec [OPTIONS]\n  --full-auto\n  --output-schema\n",
            b"",
        ))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with patch("asyncio.wait_for", return_value=mock_proc.communicate.return_value):
                caps = await runner._detect_codex_capabilities()

        assert caps["approval_mode"] is False
        assert caps["full_auto"] is True
        assert caps["output_schema"] is True

    @pytest.mark.asyncio
    async def test_timeout_uses_conservative_defaults(self):
        """Should use conservative defaults when detection times out."""
        runner = CodexRunner(cmd="codex")

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
                caps = await runner._detect_codex_capabilities()

        # Conservative defaults: no approval mode, no json, no quiet
        assert caps["approval_mode"] is False
        assert caps["full_auto"] is False
        assert caps["json"] is False
        assert caps["quiet"] is False
        assert caps["output_schema"] is True  # Assume this is available

    @pytest.mark.asyncio
    async def test_exception_uses_conservative_defaults(self):
        """Should use conservative defaults when detection fails."""
        runner = CodexRunner(cmd="codex")

        with patch("asyncio.create_subprocess_exec", side_effect=Exception("Command not found")):
            caps = await runner._detect_codex_capabilities()

        assert caps["approval_mode"] is False
        assert caps["full_auto"] is False
        assert caps["json"] is False
        assert caps["quiet"] is False
        assert caps["output_schema"] is True

    @pytest.mark.asyncio
    async def test_cache_shared_across_instances(self):
        """Module-level cache should prevent repeated --help calls."""
        # First runner detects capabilities
        runner1 = CodexRunner(cmd="codex")

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(
            b"  --approval-mode\n  --json\n",
            b"",
        ))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            with patch("asyncio.wait_for", return_value=mock_proc.communicate.return_value):
                caps1 = await runner1._detect_codex_capabilities()

        assert mock_exec.call_count == 1

        # Second runner should use cache
        runner2 = CodexRunner(cmd="codex")

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec2:
            caps2 = await runner2._detect_codex_capabilities()

        # Should NOT call subprocess again
        assert mock_exec2.call_count == 0
        assert caps2 == caps1


class TestCodexArgBuilder:
    """Tests for _build_codex_args."""

    @pytest.mark.asyncio
    async def test_uses_approval_mode_when_detected(self):
        """Should use --approval-mode when detected."""
        runner = CodexRunner(cmd="codex")

        # Pre-populate cache
        _CODEX_CAPS_CACHE["codex"] = {
            "approval_mode": True,
            "full_auto": True,
            "json": True,
            "output_schema": True,
            "quiet": True,
        }

        args = await runner._build_codex_args(
            schema_path=Path("/tmp/schema.json"),
            output_path=Path("/tmp/output.json"),
            prompt="Test prompt",
        )

        assert "--approval-mode" in args
        assert "full-auto" in args
        assert "--full-auto" not in args  # Should use --approval-mode style

    @pytest.mark.asyncio
    async def test_uses_full_auto_when_approval_mode_absent(self):
        """Should use --full-auto when --approval-mode is not available."""
        runner = CodexRunner(cmd="codex")

        _CODEX_CAPS_CACHE["codex"] = {
            "approval_mode": False,
            "full_auto": True,
            "json": True,
            "output_schema": True,
            "quiet": False,
        }

        args = await runner._build_codex_args(
            schema_path=Path("/tmp/schema.json"),
            output_path=Path("/tmp/output.json"),
            prompt="Test prompt",
        )

        assert "--approval-mode" not in args
        assert "--full-auto" in args

    @pytest.mark.asyncio
    async def test_omits_approval_flags_when_neither_detected(self):
        """Should omit approval flags when neither is detected, with warning."""
        runner = CodexRunner(cmd="codex")

        _CODEX_CAPS_CACHE["codex"] = {
            "approval_mode": False,
            "full_auto": False,
            "json": True,
            "output_schema": True,
            "quiet": False,
        }

        with patch("ai_loop.integrations.codex_runner.logger") as mock_logger:
            args = await runner._build_codex_args(
                schema_path=Path("/tmp/schema.json"),
                output_path=Path("/tmp/output.json"),
                prompt="Test prompt",
            )

        assert "--approval-mode" not in args
        assert "--full-auto" not in args
        mock_logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_no_json_in_v1(self):
        """Should NOT append --json even if detected (V1 decision)."""
        runner = CodexRunner(cmd="codex")

        _CODEX_CAPS_CACHE["codex"] = {
            "approval_mode": True,
            "full_auto": True,
            "json": True,  # Detected but should not be used
            "output_schema": True,
            "quiet": True,
        }

        args = await runner._build_codex_args(
            schema_path=Path("/tmp/schema.json"),
            output_path=Path("/tmp/output.json"),
            prompt="Test prompt",
        )

        assert "--json" not in args

    @pytest.mark.asyncio
    async def test_includes_required_args_with_quiet(self):
        """Should include -q when quiet is supported, plus --output-schema, -o, and prompt."""
        runner = CodexRunner(cmd="codex")

        _CODEX_CAPS_CACHE["codex"] = {
            "approval_mode": True,
            "full_auto": True,
            "json": False,
            "output_schema": True,
            "quiet": True,
        }

        schema_path = Path("/tmp/schema.json")
        output_path = Path("/tmp/output.json")
        prompt = "Test prompt"

        args = await runner._build_codex_args(
            schema_path=schema_path,
            output_path=output_path,
            prompt=prompt,
        )

        assert args[0] == "codex"
        assert args[1] == "exec"
        assert "-q" in args
        assert "--output-schema" in args
        assert str(schema_path) in args
        assert "-o" in args
        assert str(output_path) in args
        assert prompt == args[-1]

    @pytest.mark.asyncio
    async def test_omits_quiet_when_not_supported(self):
        """Should omit -q when quiet is not supported."""
        runner = CodexRunner(cmd="codex")

        _CODEX_CAPS_CACHE["codex"] = {
            "approval_mode": True,
            "full_auto": True,
            "json": False,
            "output_schema": True,
            "quiet": False,
        }

        args = await runner._build_codex_args(
            schema_path=Path("/tmp/schema.json"),
            output_path=Path("/tmp/output.json"),
            prompt="Test prompt",
        )

        assert "-q" not in args
        assert "--output-schema" in args
        assert "-o" in args
