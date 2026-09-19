"""Tests for generation stdio scoped signal cancellation (FR-21)."""

from __future__ import annotations

import signal
from typing import Any

import pytest

from application.models import GenerationRequest
from ipc.stdio import _scoped_generation_cancellation


def test_scoped_cancellation_installs_and_restores_on_normal_exit() -> None:
    """Verifies that _scoped_generation_cancellation restores previous signal handler."""
    if not hasattr(signal, "SIGBREAK"):
        pytest.skip("SIGBREAK is only available on Windows")

    orig_handler = signal.getsignal(signal.SIGBREAK)
    try:
        with _scoped_generation_cancellation():
            active_handler = signal.getsignal(signal.SIGBREAK)
            assert active_handler is not orig_handler
            assert callable(active_handler)

        restored_handler = signal.getsignal(signal.SIGBREAK)
        assert restored_handler is orig_handler
    finally:
        signal.signal(signal.SIGBREAK, orig_handler)


def test_scoped_cancellation_restores_on_exception() -> None:
    """Verifies that signal handler is restored even when an exception occurs inside scope."""
    if not hasattr(signal, "SIGBREAK"):
        pytest.skip("SIGBREAK is only available on Windows")

    orig_handler = signal.getsignal(signal.SIGBREAK)
    try:
        with pytest.raises(RuntimeError, match="test error"), _scoped_generation_cancellation():
            raise RuntimeError("test error")

        restored_handler = signal.getsignal(signal.SIGBREAK)
        assert restored_handler is orig_handler
    finally:
        signal.signal(signal.SIGBREAK, orig_handler)


def test_scoped_cancellation_translates_sigbreak_to_keyboard_interrupt() -> None:
    """Verifies that invoking the scoped handler raises KeyboardInterrupt and sets SIG_IGN."""
    if not hasattr(signal, "SIGBREAK"):
        pytest.skip("SIGBREAK is only available on Windows")

    orig_handler = signal.getsignal(signal.SIGBREAK)
    try:
        with _scoped_generation_cancellation():
            active_handler = signal.getsignal(signal.SIGBREAK)
            assert callable(active_handler)

            # Invoking the handler directly simulates the OS delivering the signal
            with pytest.raises(KeyboardInterrupt):
                active_handler(int(signal.SIGBREAK), None)

            # Subsequent signal must be set to SIG_IGN to protect teardown
            post_handler = signal.getsignal(signal.SIGBREAK)
            assert post_handler == signal.SIG_IGN

        # Leaving scope must still restore the original handler
        assert signal.getsignal(signal.SIGBREAK) is orig_handler
    finally:
        signal.signal(signal.SIGBREAK, orig_handler)


def test_run_stdio_exits_130_on_cancellation() -> None:
    """Verifies that a KeyboardInterrupt inside the execution scope exits 130 with no stdout."""
    from tests.ipc.support import run_stdio_in_pipes

    req_json = b'{"contract_version":"1.0","request_id":"req_sig_1","kind":"example_plan","unit":"mm","example_id":"spur_gear"}\n'

    def _cancelling_handler(req: GenerationRequest) -> dict[str, Any]:
        raise KeyboardInterrupt()

    result = run_stdio_in_pipes(
        argv=[],
        stdin_data=req_json,
        composition_handler=_cancelling_handler,
    )

    assert result.returncode == 130
    assert result.stdout_bytes == b""
