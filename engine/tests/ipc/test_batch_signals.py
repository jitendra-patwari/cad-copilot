"""Focused unit and lifecycle tests for batch cooperative signal handling.

Proves:
1. Deterministic invocation of the installed signal handler updates cancellation state.
2. End-to-end execution where handler invocation sets cancellation_check to True.
3. Accurate restoration of original handlers upon clean exit.
4. Transactional rollback of installed handlers when installation partially fails.
5. Restoration failure raises RuntimeError and fails closed with fatal diagnostic.
"""

from __future__ import annotations

import json
import signal
from collections.abc import Callable, Mapping
from typing import Any
from unittest.mock import patch

import pytest

from batch.models import (
    BatchManifestReference,
    BatchRequest,
    BatchResponse,
    BatchSummary,
)
from batch.projection import project_batch_response
from ipc.batch_progress import BATCH_FATAL_DIAGNOSTIC_BYTES
from ipc.batch_stdio import (
    _cooperative_cancellation_scope,
)
from tests.ipc.support import run_batch_stdio_in_pipes


def test_installed_signal_handler_sets_event_and_cancellation_check() -> None:
    """Proves invoking the installed signal handler directly sets the cancellation Event."""
    with _cooperative_cancellation_scope() as cancel_event:
        assert not cancel_event.is_set()

        # Retrieve the installed handler for SIGINT
        sigint_handler = signal.getsignal(signal.SIGINT)
        assert callable(sigint_handler)

        # Invoke handler deterministically
        sigint_handler(signal.SIGINT, None)
        assert cancel_event.is_set()

        # If SIGBREAK is available on Windows, verify it also sets the event
        if hasattr(signal, "SIGBREAK"):
            cancel_event.clear()
            assert not cancel_event.is_set()
            sigbreak_handler = signal.getsignal(signal.SIGBREAK)
            assert callable(sigbreak_handler)
            sigbreak_handler(signal.SIGBREAK, None)
            assert cancel_event.is_set()


def test_batch_main_end_to_end_signal_invocation() -> None:
    """Proves signal invocation during batch run triggers cancellation_check and yields cancelled response."""
    request_data = {
        "contract_version": "1.0",
        "request_id": "req-cancel-001",
        "kind": "batch_operation",
        "operation": {
            "type": "export_3d",
            "formats": ["step"],
        },
        "input": {
            "root": "E:/input",
            "files": ["part1.par", "part2.par"],
        },
        "output_root": "E:/output",
    }

    observed_cancellation_before: list[bool] = []
    observed_cancellation_after: list[bool] = []

    def mock_composition_with_signal(
        req: BatchRequest,
        *,
        cancellation_check: Callable[[], bool],
        observer: Callable[..., None],
    ) -> Mapping[str, Any]:
        # 1. Assert cancellation probe is initially False
        observed_cancellation_before.append(cancellation_check())

        # 2. Retrieve the active installed signal handler and invoke it
        handler = signal.getsignal(signal.SIGINT)
        assert callable(handler)
        handler(signal.SIGINT, None)

        # 3. Assert cancellation probe is now True
        observed_cancellation_after.append(cancellation_check())

        # 4. Return canonical cancelled response
        resp = BatchResponse(
            contract_version="1.0",
            request_id=req.request_id,
            status="cancelled",
            summary=BatchSummary(total=2, accepted=0, partial=0, failed=0, unprocessed=0, cancelled=2),
            results=(),
            manifest=BatchManifestReference(path="E:/output/req-cancel-001.batch_manifest.json"),
            unprocessed_files=(),
            cancelled_files=("part1.par", "part2.par"),
            errors=(),
            warnings=(),
        )
        return project_batch_response(resp)

    result = run_batch_stdio_in_pipes(
        stdin_data=json.dumps(request_data).encode("utf-8"),
        composition_handler=mock_composition_with_signal,
    )

    assert result.returncode == 0
    assert observed_cancellation_before == [False]
    assert observed_cancellation_after == [True]

    resp = result.stdout_json
    assert resp["status"] == "cancelled"
    assert resp["cancelled_files"] == ["part1.par", "part2.par"]
    assert resp["summary"]["cancelled"] == 2


def test_signal_scope_restores_original_handlers() -> None:
    """Proves original signal handlers are accurately restored upon clean scope exit."""
    orig_int = signal.getsignal(signal.SIGINT)

    with _cooperative_cancellation_scope():
        current_int = signal.getsignal(signal.SIGINT)
        assert current_int != orig_int

    restored_int = signal.getsignal(signal.SIGINT)
    assert restored_int == orig_int


def test_signal_partial_installation_rollback() -> None:
    """Proves that a failure on a subsequent signal rolls back previously installed handlers."""
    orig_int = signal.getsignal(signal.SIGINT)
    real_signal = signal.signal

    call_count = 0

    def fail_second_signal(sig: int, handler: Any) -> Any:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise OSError("Simulated second signal install failure")
        return real_signal(sig, handler)

    with (
        patch("signal.signal", side_effect=fail_second_signal),
        pytest.raises(RuntimeError, match="Failed to install cooperative batch signal handlers"),
        _cooperative_cancellation_scope(),
    ):
        pass

    # Verify SIGINT was restored to orig_int
    assert signal.getsignal(signal.SIGINT) == orig_int


def test_signal_restoration_failure_raises_runtime_error() -> None:
    """Proves failure during finally restoration raises RuntimeError."""
    real_signal = signal.signal

    # Allow installation to succeed, but fail during cleanup
    is_restoring = False

    def fail_on_restore(sig: int, handler: Any) -> Any:
        if is_restoring:
            raise ValueError("Simulated restoration error")
        return real_signal(sig, handler)

    with (
        patch("signal.signal", side_effect=fail_on_restore),
        pytest.raises(RuntimeError, match="Failed to restore batch signal handlers"),
        _cooperative_cancellation_scope(),
    ):
        is_restoring = True


def test_signal_installation_failure_fails_closed_in_main() -> None:
    """Proves installation failure causes main to fail closed with exit 1 and fatal diagnostic."""
    with patch("signal.signal", side_effect=ValueError("Signal only works in main thread")):
        result = run_batch_stdio_in_pipes(
            stdin_data=json.dumps(
                {
                    "contract_version": "1.0",
                    "request_id": "req-sig-fail",
                    "kind": "batch_operation",
                    "operation": {"type": "export_3d", "formats": ["step"]},
                    "input": {"root": "E:/input", "files": ["part1.par"]},
                    "output_root": "E:/output",
                }
            ).encode("utf-8"),
        )
        assert result.returncode == 1
        assert result.stdout_bytes == b""
        assert BATCH_FATAL_DIAGNOSTIC_BYTES in result.stderr_bytes
