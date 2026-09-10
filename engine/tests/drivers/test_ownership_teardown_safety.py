"""Unit tests for ownership safety, borrowed non-interference, and bounded teardown state machine."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from drivers.solidedge.errors import MK_E_UNAVAILABLE
from drivers.solidedge.runtime import SolidEdgeRuntime
from drivers.solidedge.types import (
    OwnershipMode,
    ProcessIdentity,
    SolidEdgeApplicationHandle,
    SolidEdgePartDocumentHandle,
)


class MockDoc:
    def __init__(self) -> None:
        self.closed = False
        self.ModelingMode = 2

    def Close(self, save_changes: bool = False) -> None:
        self.closed = True


class MockDocuments:
    def __init__(self) -> None:
        self.docs: list[MockDoc] = []

    def Add(self, prog_id: str) -> MockDoc:
        doc = MockDoc()
        self.docs.append(doc)
        return doc


class MockSolidEdgeApp:
    def __init__(self, pid: int = 1234) -> None:
        self.ProcessID = pid
        self.Visible = True
        self.DisplayAlerts = True
        self.Documents = MockDocuments()
        self.quit_called = False
        self.idle_called = False
        self.idle_call_count = 0

    def Quit(self) -> None:
        self.quit_called = True

    def DoIdle(self) -> None:
        self.idle_called = True
        self.idle_call_count += 1


class MockCOMError(Exception):
    def __init__(self, hresult: int, message: str) -> None:
        super().__init__(message)
        self.hresult = hresult
        self.args = (hresult, message)


def _make_valid_creation_ft() -> int:
    """Create a Windows FILETIME corresponding to current system time."""
    return int(time.time() * 10000000 + 116444736000000000)


def test_borrowed_session_teardown_zero_quit_and_zero_kill() -> None:
    """Strict Invariant (SE-10): Borrowed user sessions are NEVER Quit() and NEVER terminated."""
    runtime = SolidEdgeRuntime()
    mock_app = MockSolidEdgeApp(pid=os.getpid())

    with (
        patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules,
        patch("drivers.solidedge.runtime.get_process_identity") as mock_ident,
        patch("drivers.solidedge.runtime.kill_orphan_processes") as mock_kill,
    ):
        mock_win32_client = MagicMock()
        mock_win32_client.GetActiveObject.return_value = mock_app
        mock_modules.return_value = (None, mock_win32_client)
        mock_ident.return_value = ProcessIdentity(pid=os.getpid(), creation_time_ft=_make_valid_creation_ft())

        handle = runtime.connect_application()
        assert handle.ownership == OwnershipMode.BORROWED

        assert runtime.teardown(force_kill_on_failure=True) is True

        assert mock_app.quit_called is False
        assert mock_kill.call_count == 0


def test_borrowed_session_poisoned_worker_teardown_shuts_down_worker_safely() -> None:
    """Proves that a poisoned worker in BORROWED mode still receives shutdown() without terminating Solid Edge."""
    runtime = SolidEdgeRuntime()
    mock_app = MockSolidEdgeApp(pid=os.getpid())

    with (
        patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules,
        patch("drivers.solidedge.runtime.get_process_identity") as mock_ident,
        patch("drivers.solidedge.runtime.kill_orphan_processes") as mock_kill,
        patch("drivers.solidedge.runtime.DEFAULT_DOC_CLOSE_TIMEOUT", 0.05),
    ):
        mock_win32_client = MagicMock()
        mock_win32_client.GetActiveObject.return_value = mock_app
        mock_modules.return_value = (None, mock_win32_client)
        mock_ident.return_value = ProcessIdentity(pid=os.getpid(), creation_time_ft=_make_valid_creation_ft())

        handle = runtime.connect_application()
        assert handle.ownership == OwnershipMode.BORROWED

        runtime.create_part_document(handle)
        created_doc = mock_app.Documents.docs[0]

        def _hung_close(save: bool = False) -> None:
            time.sleep(0.3)

        created_doc.Close = _hung_close  # type: ignore[method-assign,assignment]

        # Teardown: document close will time out -> _is_poisoned = True
        assert runtime.teardown(force_kill_on_failure=True) is False

        # Invariant: Borrowed application was NEVER Quit or killed
        assert mock_app.quit_called is False
        assert mock_kill.call_count == 0
        assert runtime._worker is None


def test_unknown_session_teardown_zero_quit_and_zero_kill() -> None:
    """Proves that UNKNOWN ownership sessions follow borrowed-safe rules (no Quit, no kill)."""
    runtime = SolidEdgeRuntime()
    mock_app = MockSolidEdgeApp(pid=os.getpid())

    with (
        patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules,
        patch("drivers.solidedge.runtime.get_process_identity") as mock_ident,
        patch("drivers.solidedge.runtime.kill_orphan_processes") as mock_kill,
    ):
        mock_win32_client = MagicMock()
        mock_win32_client.GetActiveObject.side_effect = MockCOMError(MK_E_UNAVAILABLE, "Unavailable")
        mock_win32_client.gencache.EnsureDispatch.return_value = mock_app
        mock_modules.return_value = (None, mock_win32_client)
        # Simulate unprovable process identity
        mock_ident.return_value = None

        handle = runtime.connect_application()
        assert handle.ownership == OwnershipMode.UNKNOWN

        assert runtime.teardown(force_kill_on_failure=True) is True

        assert mock_app.quit_called is False
        assert mock_kill.call_count == 0


def test_owned_session_graceful_quit_and_exit_polling() -> None:
    """Proves that owned sessions call Quit() and poll for process termination."""
    runtime = SolidEdgeRuntime()
    mock_app = MockSolidEdgeApp(pid=9999991)
    owned_identity = ProcessIdentity(pid=9999991, creation_time_ft=_make_valid_creation_ft())

    with (
        patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules,
        patch("drivers.solidedge.runtime.get_process_identity", return_value=owned_identity),
        patch("drivers.solidedge.runtime.is_process_alive", side_effect=[True, False, False]),
        patch("drivers.solidedge.runtime.kill_orphan_processes") as mock_kill,
    ):
        mock_win32_client = MagicMock()
        mock_win32_client.GetActiveObject.side_effect = MockCOMError(MK_E_UNAVAILABLE, "Unavailable")
        mock_win32_client.gencache.EnsureDispatch.return_value = mock_app
        mock_modules.return_value = (None, mock_win32_client)

        handle = runtime.connect_application()
        assert handle.ownership == OwnershipMode.OWNED

        assert runtime.teardown(force_kill_on_failure=True) is True

        assert mock_app.quit_called is True
        # Process exited gracefully during polling, so force kill was not needed
        assert mock_kill.call_count == 0


def test_owned_session_blocked_quit_triggers_poisoned_state_and_force_kill() -> None:
    """Proves that a deadlocked Quit() genuinely times out on worker future, poisons worker, and triggers force kill."""
    runtime = SolidEdgeRuntime()
    mock_app = MockSolidEdgeApp(pid=9999992)
    owned_identity = ProcessIdentity(pid=9999992, creation_time_ft=_make_valid_creation_ft())

    def _hung_quit() -> None:
        time.sleep(0.3)

    mock_app.Quit = _hung_quit  # type: ignore[method-assign]

    with (
        patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules,
        patch("drivers.solidedge.runtime.get_process_identity", return_value=owned_identity),
        patch("drivers.solidedge.runtime.is_process_alive", return_value=True),
        patch("drivers.solidedge.runtime.kill_orphan_processes") as mock_kill,
        patch("drivers.solidedge.runtime.DEFAULT_QUIT_TIMEOUT", 0.05),
    ):
        mock_win32_client = MagicMock()
        mock_win32_client.GetActiveObject.side_effect = MockCOMError(MK_E_UNAVAILABLE, "Unavailable")
        mock_win32_client.gencache.EnsureDispatch.return_value = mock_app
        mock_modules.return_value = (None, mock_win32_client)

        handle = runtime.connect_application()
        assert handle.ownership == OwnershipMode.OWNED

        mock_kill.return_value = [owned_identity.pid]

        assert runtime.teardown(force_kill_on_failure=True) is True

        # Verified that force kill was called strictly for owned identity
        assert mock_kill.call_count == 1
        assert mock_kill.call_args[0][0] == [owned_identity]
        assert runtime._owned_process_identity is None


def test_blocked_close_document_timeout_isolation() -> None:
    """Proves that a blocked document Close() genuinely times out on worker, poisons worker, and allows teardown to proceed."""
    runtime = SolidEdgeRuntime()
    mock_app = MockSolidEdgeApp(pid=9999993)
    owned_identity = ProcessIdentity(pid=9999993, creation_time_ft=_make_valid_creation_ft())

    with (
        patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules,
        patch("drivers.solidedge.runtime.get_process_identity", return_value=owned_identity),
        patch("drivers.solidedge.runtime.is_process_alive", return_value=True),
        patch("drivers.solidedge.runtime.kill_orphan_processes") as mock_kill,
        patch("drivers.solidedge.runtime.DEFAULT_DOC_CLOSE_TIMEOUT", 0.05),
    ):
        mock_win32_client = MagicMock()
        mock_win32_client.GetActiveObject.side_effect = MockCOMError(MK_E_UNAVAILABLE, "Unavailable")
        mock_win32_client.gencache.EnsureDispatch.return_value = mock_app
        mock_modules.return_value = (None, mock_win32_client)

        app_handle = runtime.connect_application()
        _ = runtime.create_part_document(app_handle)

        # Make doc.Close block for 0.3s

        created_doc = mock_app.Documents.docs[0]

        def _hung_close(save: bool = False) -> None:
            time.sleep(0.3)

        created_doc.Close = _hung_close  # type: ignore[method-assign,assignment]

        mock_kill.return_value = [owned_identity.pid]

        assert runtime.teardown(force_kill_on_failure=True) is True

        # Worker was marked poisoned, skipped Quit, and proceeded to identity-checked kill
        assert mock_kill.call_count == 1
        assert mock_kill.call_args[0][0] == [owned_identity]
        assert runtime._owned_process_identity is None


def test_owned_session_teardown_without_force_kill_when_exit_fails() -> None:
    """Proves that force_kill_on_failure=False leaves orphan kill uncalled, returns False, and preserves state when process stays alive."""
    runtime = SolidEdgeRuntime()
    mock_app = MockSolidEdgeApp(pid=9999994)
    owned_identity = ProcessIdentity(pid=9999994, creation_time_ft=_make_valid_creation_ft())

    with (
        patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules,
        patch("drivers.solidedge.runtime.get_process_identity", return_value=owned_identity),
        patch("drivers.solidedge.runtime.is_process_alive", return_value=True),
        patch("drivers.solidedge.runtime.kill_orphan_processes") as mock_kill,
        patch("drivers.solidedge.runtime.DEFAULT_EXIT_POLL_TIMEOUT", 0.05),
    ):
        mock_win32_client = MagicMock()
        mock_win32_client.GetActiveObject.side_effect = MockCOMError(MK_E_UNAVAILABLE, "Unavailable")
        mock_win32_client.gencache.EnsureDispatch.return_value = mock_app
        mock_modules.return_value = (None, mock_win32_client)

        handle = runtime.connect_application()
        assert handle.ownership == OwnershipMode.OWNED

        # Teardown without force kill: process remains alive, returns False
        clean = runtime.teardown(force_kill_on_failure=False)
        assert clean is False
        assert mock_kill.call_count == 0
        assert runtime._owned_process_identity == owned_identity
        assert runtime._application is not None


def test_owned_session_force_kill_failed_termination_returns_false() -> None:
    """Proves that when force kill fails to terminate the process, teardown returns False and preserves unresolved state."""
    runtime = SolidEdgeRuntime()
    mock_app = MockSolidEdgeApp(pid=9999995)
    owned_identity = ProcessIdentity(pid=9999995, creation_time_ft=_make_valid_creation_ft())

    def _hung_quit() -> None:
        time.sleep(0.3)

    mock_app.Quit = _hung_quit  # type: ignore[method-assign]

    with (
        patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules,
        patch("drivers.solidedge.runtime.get_process_identity", return_value=owned_identity),
        patch("drivers.solidedge.runtime.is_process_alive", return_value=True),
        patch("drivers.solidedge.runtime.kill_orphan_processes", return_value=[]) as mock_kill,
        patch("drivers.solidedge.runtime.DEFAULT_QUIT_TIMEOUT", 0.05),
    ):
        mock_win32_client = MagicMock()
        mock_win32_client.GetActiveObject.side_effect = MockCOMError(MK_E_UNAVAILABLE, "Unavailable")
        mock_win32_client.gencache.EnsureDispatch.return_value = mock_app
        mock_modules.return_value = (None, mock_win32_client)

        handle = runtime.connect_application()
        assert handle.ownership == OwnershipMode.OWNED

        # Force kill called, but fails to terminate PID
        clean = runtime.teardown(force_kill_on_failure=True)
        assert clean is False
        assert mock_kill.call_count == 1
        assert runtime._owned_process_identity == owned_identity
        assert runtime._application is not None


def test_teardown_missing_raw_document_in_registry_marks_incomplete_and_preserves_handle() -> None:
    """Proves that a tracked handle missing from STA worker registry fails cleanly, preserves handle, and marks teardown incomplete."""
    runtime = SolidEdgeRuntime()
    mock_app = MockSolidEdgeApp(pid=9999996)
    owned_identity = ProcessIdentity(pid=9999996, creation_time_ft=_make_valid_creation_ft())

    with (
        patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules,
        patch("drivers.solidedge.runtime.get_process_identity", return_value=owned_identity),
        patch("drivers.solidedge.runtime.is_process_alive", return_value=True),
        patch("drivers.solidedge.runtime.kill_orphan_processes") as mock_kill,
    ):
        mock_win32_client = MagicMock()
        mock_win32_client.GetActiveObject.side_effect = MockCOMError(MK_E_UNAVAILABLE, "Unavailable")
        mock_win32_client.gencache.EnsureDispatch.return_value = mock_app
        mock_modules.return_value = (None, mock_win32_client)

        handle = runtime.connect_application()
        assert handle.ownership == OwnershipMode.OWNED

        # Manually inject a tracked handle whose COM object is missing from the worker registry
        missing_handle = SolidEdgePartDocumentHandle(
            handle_id="unseated-doc-uuid",
            path=Path("C:/mock/unseated.par"),
        )
        runtime._open_document_handles[missing_handle.handle_id] = missing_handle
        assert runtime._worker is not None
        assert missing_handle.handle_id not in runtime._worker._document_registry

        # Graceful teardown (force_kill_on_failure=False)
        clean = runtime.teardown(force_kill_on_failure=False)
        assert clean is False
        # Handle remains tracked, runtime marked poisoned, process not killed
        assert "unseated-doc-uuid" in runtime._open_document_handles
        assert runtime._is_poisoned is True
        assert mock_kill.call_count == 0
        assert runtime._owned_process_identity == owned_identity


def test_teardown_failed_attempt_preserves_state_and_retries_cleanly() -> None:
    """Proves that a failed teardown preserves unresolved state so subsequent force kill can retry and succeed."""
    runtime = SolidEdgeRuntime()
    mock_app = MockSolidEdgeApp(pid=9999997)
    owned_identity = ProcessIdentity(pid=9999997, creation_time_ft=_make_valid_creation_ft())

    alive_state = [True]

    def mock_is_alive(pid: int) -> bool:
        return alive_state[0]

    with (
        patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules,
        patch("drivers.solidedge.runtime.get_process_identity", return_value=owned_identity),
        patch("drivers.solidedge.runtime.is_process_alive", side_effect=mock_is_alive),
        patch("drivers.solidedge.runtime.kill_orphan_processes") as mock_kill,
        patch("drivers.solidedge.runtime.DEFAULT_EXIT_POLL_TIMEOUT", 0.05),
    ):
        mock_win32_client = MagicMock()
        mock_win32_client.GetActiveObject.side_effect = MockCOMError(MK_E_UNAVAILABLE, "Unavailable")
        mock_win32_client.gencache.EnsureDispatch.return_value = mock_app
        mock_modules.return_value = (None, mock_win32_client)

        handle = runtime.connect_application()
        assert handle.ownership == OwnershipMode.OWNED

        # Attempt 1: Graceful teardown fails because process stays alive (e.g. exit timeout)
        clean1 = runtime.teardown(force_kill_on_failure=False)
        assert clean1 is False
        assert mock_kill.call_count == 0
        assert runtime._owned_process_identity == owned_identity
        assert runtime._application is not None

        # Attempt 2: Retry with force_kill_on_failure=True
        def fake_kill(identities: Any) -> list[int]:
            alive_state[0] = False
            return [owned_identity.pid]

        mock_kill.side_effect = fake_kill

        clean2 = runtime.teardown(force_kill_on_failure=True)
        assert clean2 is True
        assert mock_kill.call_count == 1
        assert runtime._owned_process_identity is None
        assert runtime._application is None
        assert not runtime._open_document_handles
        assert runtime._is_poisoned is False

        # Attempt 3: Idempotent no-op
        clean3 = runtime.teardown()
        assert clean3 is True


def test_teardown_idempotency() -> None:
    """Proves that consecutive teardown() calls are safe, non-raising, and idempotent."""
    runtime = SolidEdgeRuntime()
    assert runtime.teardown() is True
    assert runtime.teardown(force_kill_on_failure=True) is True
    assert runtime.is_healthy() is False


def test_partial_connect_in_memory_probe_treats_identity_as_owned() -> None:
    """Proves that an existing _owned_process_identity without _application is treated as owned and force-killed."""
    runtime = SolidEdgeRuntime()
    owned_identity = ProcessIdentity(pid=9999998, creation_time_ft=_make_valid_creation_ft())
    runtime._owned_process_identity = owned_identity
    runtime._application = None

    with (
        patch("drivers.solidedge.runtime.is_process_alive", return_value=True),
        patch("drivers.solidedge.runtime.kill_orphan_processes", return_value=[owned_identity.pid]) as mock_kill,
    ):
        # 1. Refused force kill retains process and returns False
        clean_refused = runtime.teardown(force_kill_on_failure=False)
        assert clean_refused is False
        assert mock_kill.call_count == 0
        assert runtime._owned_process_identity == owned_identity

        # 2. Allowed force kill invokes kill_orphan_processes and returns True
        clean_force = runtime.teardown(force_kill_on_failure=True)
        assert clean_force is True
        assert mock_kill.call_count == 1
        assert mock_kill.call_args[0][0] == [owned_identity]
        assert runtime._owned_process_identity is None


def test_partial_connect_timed_out_connect_quiesces_worker_and_force_kills_process() -> None:
    """Proves that a timed-out connection with an active worker quiesces the worker and terminates the owned process."""
    runtime = SolidEdgeRuntime()
    owned_identity = ProcessIdentity(pid=9999999, creation_time_ft=_make_valid_creation_ft())

    with (
        patch("drivers.solidedge.runtime.is_process_alive", return_value=True),
        patch("drivers.solidedge.runtime.kill_orphan_processes", return_value=[owned_identity.pid]) as mock_kill,
    ):
        worker = runtime._ensure_worker()
        assert worker.is_alive()

        # Simulate ownership proof established before connect failure
        runtime._owned_process_identity = owned_identity
        runtime._application = None

        clean = runtime.teardown(force_kill_on_failure=True)
        assert clean is True
        assert not worker.is_alive()
        assert mock_kill.call_count == 1
        assert mock_kill.call_args[0][0] == [owned_identity]
        assert runtime._owned_process_identity is None


def test_partial_connect_worker_populates_identity_during_quiesce() -> None:
    """Proves that re-checking identity after worker quiesce captures an identity recorded in flight."""
    runtime = SolidEdgeRuntime()
    owned_identity = ProcessIdentity(pid=9999910, creation_time_ft=_make_valid_creation_ft())

    with (
        patch("drivers.solidedge.runtime.is_process_alive", return_value=True),
        patch("drivers.solidedge.runtime.kill_orphan_processes", return_value=[owned_identity.pid]) as mock_kill,
    ):
        worker = runtime._ensure_worker()
        assert worker.is_alive()

        started = threading.Event()

        # Submit an in-flight worker task that simulates spawn proof completing
        def _in_flight_connect_proof() -> None:
            started.set()
            time.sleep(0.05)
            runtime._owned_process_identity = owned_identity

        worker.submit(_in_flight_connect_proof)
        assert started.wait(timeout=1.0) is True
        runtime._application = None

        # At call time, _owned_process_identity is still None
        assert runtime._owned_process_identity is None

        clean = runtime.teardown(force_kill_on_failure=True)
        assert clean is True
        assert not worker.is_alive()
        assert mock_kill.call_count == 1
        assert mock_kill.call_args[0][0] == [owned_identity]
        assert runtime._owned_process_identity is None


class DeterministicShutdownWorker:
    """Compact worker stub simulating in-flight identity arrival during shutdown."""

    def __init__(self, on_shutdown: Callable[[], None] | None = None) -> None:
        self._alive = True
        self._on_shutdown = on_shutdown

    def is_alive(self) -> bool:
        return self._alive

    def shutdown(self, timeout: float = 2.0) -> None:
        if self._on_shutdown is not None:
            self._on_shutdown()
        self._alive = False


def test_deterministic_identity_arrival_during_final_shutdown_triggers_owned_cleanup() -> None:
    """Proves that identity appearing during worker shutdown routes to owned cleanup rather than wiping."""
    runtime = SolidEdgeRuntime()
    owned_identity = ProcessIdentity(pid=9999911, creation_time_ft=_make_valid_creation_ft())

    def _late_identity_hook() -> None:
        runtime._owned_process_identity = owned_identity

    stub_worker = DeterministicShutdownWorker(on_shutdown=_late_identity_hook)
    runtime._worker = stub_worker  # type: ignore[assignment]
    runtime._application = None
    assert runtime._owned_process_identity is None

    with (
        patch("drivers.solidedge.runtime.is_process_alive", return_value=True),
        patch("drivers.solidedge.runtime.kill_orphan_processes", return_value=[owned_identity.pid]) as mock_kill,
    ):
        clean = runtime.teardown(force_kill_on_failure=True)
        assert clean is True
        assert not stub_worker.is_alive()
        assert mock_kill.call_count == 1
        assert mock_kill.call_args[0][0] == [owned_identity]
        assert runtime._owned_process_identity is None


def test_partial_connect_worker_alive_with_unknown_ownership_preserves_state() -> None:
    """Proves that if worker remains alive after Stage 0 and ownership is unknown, teardown returns False and preserves state."""
    runtime = SolidEdgeRuntime()

    class StillAliveWorker:
        def is_alive(self) -> bool:
            return True

        def shutdown(self, timeout: float = 2.0) -> None:
            pass

    stub_worker = StillAliveWorker()
    runtime._worker = stub_worker  # type: ignore[assignment]
    runtime._application = None
    assert runtime._owned_process_identity is None

    with (
        patch("drivers.solidedge.runtime.is_process_alive", return_value=True),
        patch("drivers.solidedge.runtime.kill_orphan_processes") as mock_kill,
    ):
        clean = runtime.teardown(force_kill_on_failure=True)
        assert clean is False
        assert mock_kill.call_count == 0
        assert runtime._is_poisoned is True
        assert runtime._worker is not None
        assert runtime._owned_process_identity is None


def test_deterministic_identity_arrival_during_stage2_shutdown_switches_to_owned_cleanup() -> None:
    """Proves that identity appearing during Stage 2 shutdown reclassifies to owned and invokes force cleanup."""
    runtime = SolidEdgeRuntime()
    owned_identity = ProcessIdentity(pid=9999912, creation_time_ft=_make_valid_creation_ft())

    def _late_identity_hook() -> None:
        runtime._owned_process_identity = owned_identity

    stub_worker = DeterministicShutdownWorker(on_shutdown=_late_identity_hook)
    runtime._worker = stub_worker  # type: ignore[assignment]
    runtime._application = SolidEdgeApplicationHandle(
        handle_id="unknown-app-handle",
        ownership=OwnershipMode.UNKNOWN,
    )
    assert runtime._owned_process_identity is None

    with (
        patch("drivers.solidedge.runtime.is_process_alive", return_value=True),
        patch("drivers.solidedge.runtime.kill_orphan_processes", return_value=[owned_identity.pid]) as mock_kill,
    ):
        clean = runtime.teardown(force_kill_on_failure=True)
        assert clean is True
        assert not stub_worker.is_alive()
        assert mock_kill.call_count == 1
        assert mock_kill.call_args[0][0] == [owned_identity]
        assert runtime._owned_process_identity is None


def test_teardown_with_pending_idle_handle_skips_duplicate_close_and_avoids_misreporting_lost() -> None:
    """Proves teardown on a pending-idle handle avoids duplicate Close, avoids DOCUMENT_LOST, and marks teardown incomplete."""
    runtime = SolidEdgeRuntime()
    mock_app = MockSolidEdgeApp(pid=os.getpid())

    with (
        patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules,
        patch("drivers.solidedge.runtime.get_process_identity") as mock_ident,
    ):
        mock_win32_client = MagicMock()
        mock_win32_client.GetActiveObject.return_value = mock_app
        mock_modules.return_value = (None, mock_win32_client)
        mock_ident.return_value = ProcessIdentity(pid=os.getpid(), creation_time_ft=_make_valid_creation_ft())

        handle = runtime.connect_application()
        assert handle.ownership == OwnershipMode.BORROWED

        doc_handle = runtime.create_part_document(handle)
        handle_id = doc_handle.handle_id

        worker = runtime._worker
        assert worker is not None

        # Simulate that Close(False) already succeeded and raw doc was released, but DoIdle failed
        worker._document_registry.pop(handle_id, None)
        runtime._closed_pending_idle_handles.add(handle_id)

        # Invariant: teardown must NOT attempt raw_doc lookup (which would raise DOCUMENT_LOST)
        # and must NOT attempt duplicate Close(False). It marks teardown incomplete and returns False cleanly.
        clean = runtime.teardown(force_kill_on_failure=True)
        assert clean is False
        assert mock_app.quit_called is False
        assert handle_id in runtime._open_document_handles
        assert handle_id in runtime._closed_pending_idle_handles
        assert runtime._is_poisoned is False


def test_teardown_retries_close_via_centralized_primitive_for_unclosed_document() -> None:
    """Proves teardown retries unclosed documents via the centralized close primitive including DoIdle."""
    runtime = SolidEdgeRuntime()
    mock_app = MockSolidEdgeApp(pid=os.getpid())

    with (
        patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules,
        patch("drivers.solidedge.runtime.get_process_identity") as mock_ident,
    ):
        mock_win32_client = MagicMock()
        mock_win32_client.GetActiveObject.return_value = mock_app
        mock_modules.return_value = (None, mock_win32_client)
        mock_ident.return_value = ProcessIdentity(pid=os.getpid(), creation_time_ft=_make_valid_creation_ft())

        handle = runtime.connect_application()
        assert handle.ownership == OwnershipMode.BORROWED

        doc_handle = runtime.create_part_document(handle)
        handle_id = doc_handle.handle_id
        mock_doc = mock_app.Documents.docs[0]

        assert mock_doc.closed is False
        assert mock_app.idle_called is False

        # Teardown should close document with Close(False) and DoIdle()
        clean = runtime.teardown()
        assert clean is True
        assert mock_doc.closed is True
        assert mock_app.idle_called is True
        assert mock_app.quit_called is False
        assert handle_id not in runtime._open_document_handles
        assert handle_id not in runtime._closed_pending_idle_handles


def test_teardown_owned_session_with_pending_idle_handle_cleans_up_process_and_resets_state() -> None:
    """Proves owned session with pending-idle handle terminates process and cleanly resets runtime state."""
    runtime = SolidEdgeRuntime()
    mock_app = MockSolidEdgeApp(pid=9999913)
    owned_identity = ProcessIdentity(pid=9999913, creation_time_ft=_make_valid_creation_ft())

    with (
        patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules,
        patch("drivers.solidedge.runtime.get_process_identity", return_value=owned_identity),
        patch("drivers.solidedge.runtime.is_process_alive", return_value=False),
    ):
        mock_win32_client = MagicMock()
        mock_win32_client.GetActiveObject.side_effect = MockCOMError(MK_E_UNAVAILABLE, "Unavailable")
        mock_win32_client.gencache.EnsureDispatch.return_value = mock_app
        mock_modules.return_value = (None, mock_win32_client)

        app_handle = runtime.connect_application()
        doc_handle = runtime.create_part_document(app_handle)
        handle_id = doc_handle.handle_id

        worker = runtime._worker
        assert worker is not None

        # Simulate pending-idle state
        worker._document_registry.pop(handle_id, None)
        runtime._closed_pending_idle_handles.add(handle_id)

        clean = runtime.teardown()
        assert clean is True
        assert mock_app.quit_called is True
        assert handle_id not in runtime._open_document_handles
        assert handle_id not in runtime._closed_pending_idle_handles
        assert runtime._worker is None
