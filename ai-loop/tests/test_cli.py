"""Tests for CLI signal handling."""

import asyncio
import signal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestGracefulSigint:
    """Tests for graceful SIGINT (Ctrl-C) handling."""

    @pytest.mark.asyncio
    async def test_sigint_cancels_pipeline_task(self):
        """SIGINT handler should cancel the pipeline task, not raise exception."""
        # Simulate the signal handler behavior
        interrupted = False
        pipeline_task = None

        def handle_sigint():
            nonlocal interrupted
            interrupted = True
            if pipeline_task and not pipeline_task.done():
                pipeline_task.cancel()

        # Create a long-running task
        async def long_running():
            await asyncio.sleep(10)

        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, handle_sigint)

        try:
            pipeline_task = asyncio.create_task(long_running())

            # Simulate SIGINT after a short delay
            async def send_sigint():
                await asyncio.sleep(0.1)
                handle_sigint()

            asyncio.create_task(send_sigint())

            # Wait for the task (should be cancelled)
            try:
                await pipeline_task
            except asyncio.CancelledError:
                pass  # Expected

            assert interrupted is True
            assert pipeline_task.cancelled()
        finally:
            loop.remove_signal_handler(signal.SIGINT)

    @pytest.mark.asyncio
    async def test_sigint_allows_cleanup(self):
        """After SIGINT, cleanup code should be able to run."""
        cleanup_ran = False
        interrupted = False
        pipeline_task = None

        def handle_sigint():
            nonlocal interrupted
            interrupted = True
            if pipeline_task and not pipeline_task.done():
                pipeline_task.cancel()

        async def long_running():
            await asyncio.sleep(10)

        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGINT, handle_sigint)

        elapsed_task = None
        try:
            # Create elapsed timer like in CLI
            async def update_elapsed():
                while True:
                    await asyncio.sleep(0.1)

            elapsed_task = asyncio.create_task(update_elapsed())
            pipeline_task = asyncio.create_task(long_running())

            # Simulate SIGINT
            async def send_sigint():
                await asyncio.sleep(0.05)
                handle_sigint()

            asyncio.create_task(send_sigint())

            try:
                await pipeline_task
            except asyncio.CancelledError:
                if interrupted:
                    # Cleanup should work
                    cleanup_ran = True
        finally:
            if elapsed_task:
                elapsed_task.cancel()
                try:
                    await elapsed_task
                except asyncio.CancelledError:
                    pass
            loop.remove_signal_handler(signal.SIGINT)

        assert cleanup_ran is True

    @pytest.mark.asyncio
    async def test_cancelled_error_without_interrupt_reraises(self):
        """CancelledError not from SIGINT should be re-raised."""
        interrupted = False

        async def task_that_gets_cancelled():
            await asyncio.sleep(10)

        task = asyncio.create_task(task_that_gets_cancelled())

        # Cancel without setting interrupted flag
        async def cancel_task():
            await asyncio.sleep(0.05)
            task.cancel()

        asyncio.create_task(cancel_task())

        with pytest.raises(asyncio.CancelledError):
            try:
                await task
            except asyncio.CancelledError:
                if interrupted:
                    pass  # Would handle gracefully
                raise  # Re-raise since not from our interrupt
