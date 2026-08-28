"""Unit tests for ownership safety, borrowed non-interference, and bounded teardown state machine."""

from __future__ import annotations

import os
import time
from unittest.mock import MagicMock, patch

from drivers.solidedge.errors import MK_E_UNAVAILABLE
from drivers.solidedge.runtime import SolidEdgeRuntime
from drivers.solidedge.types import (
    OwnershipMode,
    ProcessIdentity,
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

    def Quit(self) -> None:
        self.quit_called = True


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

        runtime.teardown(force_kill_on_failure=True)

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

        runtime.teardown(force_kill_on_failure=True)

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

        runtime.teardown(force_kill_on_failure=True)

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

        runtime.teardown(force_kill_on_failure=True)

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

        runtime.teardown(force_kill_on_failure=True)

        # Verified that force kill was called strictly for owned identity
        assert mock_kill.call_count == 1
        assert mock_kill.call_args[0][0] == [owned_identity]


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

        runtime.teardown(force_kill_on_failure=True)

        # Worker was marked poisoned, skipped Quit, and proceeded to identity-checked kill
        assert mock_kill.call_count == 1
        assert mock_kill.call_args[0][0] == [owned_identity]


def test_teardown_idempotency() -> None:
    """Proves that consecutive teardown() calls are safe, non-raising, and idempotent."""
    runtime = SolidEdgeRuntime()
    runtime.teardown()
    runtime.teardown(force_kill_on_failure=True)
    assert runtime.is_healthy() is False
