"""Unit tests for STA runtime lifecycle, connection modes, handles, and diagnostics."""

from __future__ import annotations

import concurrent.futures
import os
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from drivers.solidedge.errors import (
    MK_E_UNAVAILABLE,
    RPC_E_CALL_REJECTED,
)
from drivers.solidedge.runtime import (
    SolidEdgeRuntime,
    STAThreadWorker,
)
from drivers.solidedge.types import (
    AttachmentMode,
    OwnershipMode,
    ProcessIdentity,
    SolidEdgeDocumentHandle,
    SolidEdgePartDocumentHandle,
)
from interfaces.exceptions import (
    CADDocumentError,
    CADRuntimeBusyError,
    CADRuntimeError,
    CADRuntimeUnavailableError,
)


class MockDoc:
    """Mock Solid Edge Document object."""

    def __init__(self, doc_type: str = "Part") -> None:
        self.doc_type = doc_type
        self.ModelingMode = 0
        self.closed = False
        self.closed_save_arg: bool | None = None
        self.close_count: int = 0
        self.close_call_count: int = 0

    def Close(self, save_changes: bool = False) -> None:
        self.closed = True
        self.closed_save_arg = save_changes
        self.close_count += 1
        self.close_call_count += 1


class MockDocuments:
    """Mock Solid Edge Documents collection."""

    def __init__(self) -> None:
        self.added_docs: list[MockDoc] = []
        self.opened_paths: list[str] = []

    @property
    def docs(self) -> list[MockDoc]:
        return self.added_docs

    def Add(self, prog_id: str) -> MockDoc:
        doc = MockDoc(doc_type=prog_id)
        self.added_docs.append(doc)
        return doc

    def Open(self, path: str) -> MockDoc:
        doc = MockDoc(doc_type="Opened")
        self.opened_paths.append(path)
        return doc


class MockSolidEdgeApp:
    """Mock Solid Edge Application object."""

    def __init__(self, pid: int = 1234, version: str = "226.00.00.00") -> None:
        self.ProcessID = pid
        self.Version = version
        self.Visible = False
        self.DisplayAlerts = True
        self.display_alerts_written = False
        self.Documents = MockDocuments()
        self.quit_called = False
        self.idle_called = False
        self.idle_call_count = 0
        self.thread_ids_called: list[int] = []

    def Quit(self) -> None:
        self.quit_called = True
        self.thread_ids_called.append(threading.get_ident())

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


def test_sta_worker_executes_on_dedicated_thread() -> None:
    """Proves that tasks submitted to STAThreadWorker execute strictly on the worker thread."""
    worker = STAThreadWorker()
    worker.start()

    main_thread_id = threading.get_ident()

    def _task() -> int:
        return threading.get_ident()

    worker_thread_id = worker.call(_task, timeout=2.0)
    assert worker_thread_id != main_thread_id
    assert worker_thread_id == worker.thread_id

    worker.shutdown()


def test_connect_application_borrowed_mode_zero_display_alerts_mutation() -> None:
    """Proves that attaching to existing Solid Edge sets BORROWED mode and NEVER writes DisplayAlerts."""
    mock_app = MockSolidEdgeApp(pid=os.getpid(), version="226.01.00")
    runtime = SolidEdgeRuntime()

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
        assert handle.attachment_mode == AttachmentMode.ATTACHED_EXISTING
        assert handle.version_build == "226.01.00"
        # Strict Invariant: DisplayAlerts was NEVER modified on borrowed session!
        assert mock_app.DisplayAlerts is True
        assert mock_app.Visible is False

    runtime.teardown()


def test_connect_application_borrowed_unresponsive_raises_unavailable() -> None:
    """Proves that connect_application raises CADRuntimeUnavailableError when existing session is unresponsive."""
    mock_app = MagicMock()
    type(mock_app).Visible = property(fget=MagicMock(side_effect=MockCOMError(RPC_E_CALL_REJECTED, "Unresponsive")))
    runtime = SolidEdgeRuntime()

    with patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules:
        mock_win32_client = MagicMock()
        mock_win32_client.GetActiveObject.return_value = mock_app
        mock_modules.return_value = (None, mock_win32_client)

        with pytest.raises(CADRuntimeUnavailableError, match="unresponsive"):
            runtime.connect_application()

    runtime.teardown()


def test_connect_application_exact_mk_e_unavailable_spawns_owned() -> None:
    """Proves that only MK_E_UNAVAILABLE (0x800401E3) triggers Dispatch to spawn owned instance."""
    mock_app = MockSolidEdgeApp(pid=os.getpid(), version="226.02.00")
    runtime = SolidEdgeRuntime()

    with (
        patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules,
        patch("drivers.solidedge.runtime.get_process_identity") as mock_ident,
    ):
        mock_win32_client = MagicMock()
        mock_win32_client.GetActiveObject.side_effect = MockCOMError(MK_E_UNAVAILABLE, "Unavailable")
        mock_win32_client.gencache.EnsureDispatch.return_value = mock_app
        mock_modules.return_value = (None, mock_win32_client)
        mock_ident.return_value = ProcessIdentity(pid=os.getpid(), creation_time_ft=_make_valid_creation_ft())

        handle = runtime.connect_application()

        assert handle.ownership == OwnershipMode.OWNED
        assert handle.attachment_mode == AttachmentMode.SPAWNED_NEW
        # Owned instance: Visible is set True, DisplayAlerts set False
        assert mock_app.Visible is True
        assert mock_app.DisplayAlerts is False

    runtime.teardown()


def test_connect_application_spawn_out_of_window_falls_back_to_unknown() -> None:
    """Proves that a spawned process with creation timestamp outside spawn window falls back to UNKNOWN."""
    mock_app = MockSolidEdgeApp(pid=os.getpid(), version="226.02.00")
    runtime = SolidEdgeRuntime()

    # Creation time 1 hour in the past (outside spawn window)
    stale_creation_ft = int((time.time() - 3600) * 10000000 + 116444736000000000)

    with (
        patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules,
        patch("drivers.solidedge.runtime.get_process_identity") as mock_ident,
    ):
        mock_win32_client = MagicMock()
        mock_win32_client.GetActiveObject.side_effect = MockCOMError(MK_E_UNAVAILABLE, "Unavailable")
        mock_win32_client.gencache.EnsureDispatch.return_value = mock_app
        mock_modules.return_value = (None, mock_win32_client)
        mock_ident.return_value = ProcessIdentity(pid=os.getpid(), creation_time_ft=stale_creation_ft)

        handle = runtime.connect_application()

        assert handle.ownership == OwnershipMode.UNKNOWN
        assert handle.attachment_mode == AttachmentMode.SPAWNED_NEW
        # Not marked owned -> DisplayAlerts not mutated
        assert mock_app.DisplayAlerts is True

    runtime.teardown()


def test_connect_application_other_hresult_raises_without_dispatch() -> None:
    """Proves that non-MK_E_UNAVAILABLE COM failures (e.g. RPC_E_CALL_REJECTED) raise without calling Dispatch."""
    runtime = SolidEdgeRuntime()

    with patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules:
        mock_win32_client = MagicMock()
        mock_win32_client.GetActiveObject.side_effect = MockCOMError(RPC_E_CALL_REJECTED, "Rejected")
        mock_modules.return_value = (None, mock_win32_client)

        with pytest.raises(CADRuntimeBusyError):
            runtime.connect_application()

        assert mock_win32_client.gencache.EnsureDispatch.call_count == 0
        assert mock_win32_client.Dispatch.call_count == 0

    runtime.teardown()


def test_get_diagnostics_live_query() -> None:
    """Proves that get_diagnostics returns live session status and metadata."""
    runtime = SolidEdgeRuntime()

    # Before connection
    diag_pre = runtime.get_diagnostics()
    assert diag_pre.ownership == "unknown"
    assert diag_pre.is_healthy is False

    mock_app = MockSolidEdgeApp(pid=1234, version="226.03.00")
    mock_app.Visible = True

    with (
        patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules,
        patch("drivers.solidedge.runtime.get_process_identity") as mock_ident,
    ):
        mock_win32_client = MagicMock()
        mock_win32_client.GetActiveObject.return_value = mock_app
        mock_modules.return_value = (None, mock_win32_client)
        mock_ident.return_value = ProcessIdentity(pid=1234, creation_time_ft=_make_valid_creation_ft())

        runtime.connect_application()
        diag = runtime.get_diagnostics()

        assert diag.ownership == "borrowed"
        assert diag.attachment_mode == "attached_existing"
        assert diag.visibility == "visible"
        assert diag.process_id == 1234
        assert diag.version_build == "226.03.00"
        assert diag.is_healthy is True

    runtime.teardown()


def test_create_part_document_opaque_handle_and_ordered_mode() -> None:
    """Proves that create_part_document returns opaque handle, sets Ordered Mode 2, and stores in registry."""
    runtime = SolidEdgeRuntime()
    mock_app = MockSolidEdgeApp()

    with patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules:
        mock_win32_client = MagicMock()
        mock_win32_client.GetActiveObject.return_value = mock_app
        mock_modules.return_value = (None, mock_win32_client)

        app_handle = runtime.connect_application()
        doc_handle = runtime.create_part_document(app_handle)

        assert isinstance(doc_handle, SolidEdgePartDocumentHandle)
        assert isinstance(doc_handle.handle_id, str)
        assert len(doc_handle.handle_id) > 0
        # Check that Ordered Mode 2 was set on mock doc
        assert len(mock_app.Documents.added_docs) == 1
        assert mock_app.Documents.added_docs[0].ModelingMode == 2

    runtime.teardown()


def test_open_document_clean_semantics(tmp_path: Path) -> None:
    """Proves that open_document opens file without modifying ModelingMode and validates file existence."""
    runtime = SolidEdgeRuntime()
    mock_app = MockSolidEdgeApp()

    dummy_file = tmp_path / "test.par"
    dummy_file.write_text("cad content")

    with patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules:
        mock_win32_client = MagicMock()
        mock_win32_client.GetActiveObject.return_value = mock_app
        mock_modules.return_value = (None, mock_win32_client)

        app_handle = runtime.connect_application()

        # Non-existent file raises CADDocumentError
        with pytest.raises(CADDocumentError, match="not found"):
            runtime.open_document(app_handle, tmp_path / "non_existent.par")

        doc_handle = runtime.open_document(app_handle, dummy_file)
        assert isinstance(doc_handle, SolidEdgeDocumentHandle)
        assert doc_handle.path == dummy_file.resolve()
        assert str(dummy_file.resolve()) in mock_app.Documents.opened_paths

    runtime.teardown()


def test_close_document_unconditional_close_without_save() -> None:
    """Proves that close_document unconditionally passes False to Close()."""
    runtime = SolidEdgeRuntime()
    mock_app = MockSolidEdgeApp()

    with patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules:
        mock_win32_client = MagicMock()
        mock_win32_client.GetActiveObject.return_value = mock_app
        mock_modules.return_value = (None, mock_win32_client)

        app_handle = runtime.connect_application()
        doc_handle = runtime.create_part_document(app_handle)

        created_mock_doc = mock_app.Documents.added_docs[0]
        assert created_mock_doc.closed is False
        assert doc_handle.handle_id in runtime._open_document_handles

        runtime.close_document(doc_handle)
        assert created_mock_doc.closed is True
        assert created_mock_doc.closed_save_arg is False
        assert doc_handle.handle_id not in runtime._open_document_handles

    runtime.teardown()


def test_close_document_retains_tracking_on_failure() -> None:
    """Proves that close_document retains handle tracking when Close(False) raises or times out."""
    runtime = SolidEdgeRuntime()
    mock_app = MockSolidEdgeApp()

    with patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules:
        mock_win32_client = MagicMock()
        mock_win32_client.GetActiveObject.return_value = mock_app
        mock_modules.return_value = (None, mock_win32_client)

        app_handle = runtime.connect_application()
        doc_handle = runtime.create_part_document(app_handle)

        # Make doc.Close raise an error
        created_mock_doc = mock_app.Documents.added_docs[0]
        created_mock_doc.Close = MagicMock(side_effect=RuntimeError("COM close failure"))  # type: ignore[method-assign]

        with pytest.raises(CADRuntimeError):
            runtime.close_document(doc_handle)

        # Invariant: Failed close must NOT remove handle tracking!
        assert doc_handle.handle_id in runtime._open_document_handles

    runtime.teardown()


def test_is_healthy_responsive_and_error() -> None:
    """Proves that is_healthy reflects live COM responsiveness."""
    runtime = SolidEdgeRuntime()
    assert runtime.is_healthy() is False

    mock_app = MockSolidEdgeApp()

    with patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules:
        mock_win32_client = MagicMock()
        mock_win32_client.GetActiveObject.return_value = mock_app
        mock_modules.return_value = (None, mock_win32_client)

        runtime.connect_application()
        assert runtime.is_healthy() is True

    runtime.teardown()
    assert runtime.is_healthy() is False


def test_create_part_document_sets_ordered_mode_and_verifies_readback() -> None:
    """Proves that create_part_document explicitly sets and verifies Ordered modeling mode (2)."""
    runtime = SolidEdgeRuntime()
    mock_app = MockSolidEdgeApp()

    with patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules:
        mock_win32_client = MagicMock()
        mock_win32_client.GetActiveObject.return_value = mock_app
        mock_modules.return_value = (None, mock_win32_client)

        app_handle = runtime.connect_application()
        doc_handle = runtime.create_part_document(app_handle)

        created_mock_doc = mock_app.Documents.added_docs[0]
        assert created_mock_doc.ModelingMode == 2
        assert doc_handle.handle_id in runtime._open_document_handles

    runtime.teardown()


def test_create_part_document_closes_without_saving_when_ordered_mode_fails() -> None:
    """Proves that failing to establish Ordered mode immediately closes the document without saving and raises CADDocumentError."""
    runtime = SolidEdgeRuntime()

    class RejectingMockDoc(MockDoc):
        @property
        def ModelingMode(self) -> int:
            return 1  # Rejects setting to 2, stays in Synchronous (1)

        @ModelingMode.setter
        def ModelingMode(self, val: int) -> None:
            pass  # Ignored

    class RejectingMockDocuments:
        def __init__(self) -> None:
            self.docs: list[RejectingMockDoc] = []

        def Add(self, prog_id: str) -> RejectingMockDoc:
            doc = RejectingMockDoc()
            self.docs.append(doc)
            return doc

    rejecting_docs = RejectingMockDocuments()
    mock_app = MockSolidEdgeApp()
    mock_app.Documents = rejecting_docs  # type: ignore[assignment]

    with patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules:
        mock_win32_client = MagicMock()
        mock_win32_client.GetActiveObject.return_value = mock_app
        mock_modules.return_value = (None, mock_win32_client)

        app_handle = runtime.connect_application()

        with pytest.raises(CADDocumentError) as exc_info:
            runtime.create_part_document(app_handle)

        assert "Failed to establish Ordered modeling mode" in str(exc_info.value)
        assert len(rejecting_docs.docs) == 1
        created_doc = rejecting_docs.docs[0]
        # Invariant: Document must be closed immediately without saving!
        assert created_doc.closed is True
        assert created_doc.closed_save_arg is False
        assert mock_app.idle_called is True
        assert len(runtime._open_document_handles) == 0
        assert len(runtime._closed_pending_idle_handles) == 0

    runtime.teardown()


def test_create_part_document_retains_tracking_for_teardown_when_immediate_close_fails() -> None:
    """Proves that if Ordered mode fails AND immediate Close(False) raises, tracking is retained so teardown retries closure."""
    runtime = SolidEdgeRuntime()

    class RejectingUnclosableMockDoc(MockDoc):
        def __init__(self) -> None:
            super().__init__()
            self.close_attempt_count = 0

        @property
        def ModelingMode(self) -> int:
            return 1  # Rejects setting to 2

        @ModelingMode.setter
        def ModelingMode(self, val: int) -> None:
            pass

        def Close(self, save_changes: bool = False) -> None:
            self.close_attempt_count += 1
            if self.close_attempt_count == 1:
                # First attempt (immediate cleanup) fails
                raise RuntimeError("COM busy during immediate cleanup")
            # Subsequent attempts (during teardown) succeed
            self.closed = True
            self.closed_save_arg = save_changes

    class RejectingMockDocuments:
        def __init__(self) -> None:
            self.docs: list[RejectingUnclosableMockDoc] = []

        def Add(self, prog_id: str) -> RejectingUnclosableMockDoc:
            doc = RejectingUnclosableMockDoc()
            self.docs.append(doc)
            return doc

    rejecting_docs = RejectingMockDocuments()
    mock_app = MockSolidEdgeApp()
    mock_app.Documents = rejecting_docs  # type: ignore[assignment]

    with patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules:
        mock_win32_client = MagicMock()
        mock_win32_client.GetActiveObject.return_value = mock_app
        mock_modules.return_value = (None, mock_win32_client)

        app_handle = runtime.connect_application()

        with pytest.raises(CADDocumentError) as exc_info:
            runtime.create_part_document(app_handle)

        assert "Failed to establish Ordered modeling mode" in str(exc_info.value)
        assert len(rejecting_docs.docs) == 1
        created_doc = rejecting_docs.docs[0]
        # Invariant 1: Immediate close failed, so document is NOT yet closed
        assert created_doc.closed is False
        assert created_doc.close_attempt_count == 1
        # Invariant 2: Tracking MUST be retained in open handles and registry!
        assert len(runtime._open_document_handles) == 1
        tracked_handle_id = next(iter(runtime._open_document_handles.keys()))
        assert runtime._worker is not None
        assert tracked_handle_id in runtime._worker._document_registry
        assert tracked_handle_id not in runtime._closed_pending_idle_handles
        assert mock_app.idle_called is False

        # Invariant 3: Teardown executes Stage 1 document close, retrying and closing the tracked doc
        clean = runtime.teardown()
        assert clean is True
        assert created_doc.closed is True
        assert created_doc.closed_save_arg is False
        assert created_doc.close_attempt_count == 2
        assert mock_app.idle_called is True
        assert len(runtime._open_document_handles) == 0
        assert len(runtime._closed_pending_idle_handles) == 0


def test_create_part_document_retains_pending_idle_when_doidle_fails_during_mode_failure_cleanup() -> None:
    """Proves that if Ordered mode fails, Close succeeds, but DoIdle fails, tracking is retained in pending-idle."""
    runtime = SolidEdgeRuntime()

    class RejectingMockDoc(MockDoc):
        @property
        def ModelingMode(self) -> int:
            return 1

        @ModelingMode.setter
        def ModelingMode(self, val: int) -> None:
            pass

    class RejectingMockDocuments:
        def __init__(self) -> None:
            self.docs: list[RejectingMockDoc] = []

        def Add(self, prog_id: str) -> RejectingMockDoc:
            doc = RejectingMockDoc()
            self.docs.append(doc)
            return doc

    rejecting_docs = RejectingMockDocuments()
    mock_app = MockSolidEdgeApp()
    mock_app.Documents = rejecting_docs  # type: ignore[assignment]

    def _failing_idle() -> None:
        raise RuntimeError("COM busy during DoIdle")

    mock_app.DoIdle = _failing_idle  # type: ignore[method-assign]

    try:
        with patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules:
            mock_win32_client = MagicMock()
            mock_win32_client.GetActiveObject.return_value = mock_app
            mock_modules.return_value = (None, mock_win32_client)

            app_handle = runtime.connect_application()

            with pytest.raises(CADDocumentError) as exc_info:
                runtime.create_part_document(app_handle)

            assert "Failed to establish Ordered modeling mode" in str(exc_info.value)
            assert len(rejecting_docs.docs) == 1
            created_doc = rejecting_docs.docs[0]
            # Invariant 1: Close(False) succeeded
            assert created_doc.closed is True
            assert created_doc.closed_save_arg is False

            # Invariant 2: Failed DoIdle retains public handle and pending-idle state, raw registry is cleared
            worker = runtime._worker
            assert worker is not None
            assert len(runtime._open_document_handles) == 1
            handle_id = next(iter(runtime._open_document_handles.keys()))
            assert handle_id not in worker._document_registry
            assert handle_id in runtime._closed_pending_idle_handles

            # Invariant 3: Teardown skips duplicate Close(False) and reports incomplete teardown
            clean = runtime.teardown()
            assert clean is False
            assert handle_id in runtime._open_document_handles
            assert handle_id in runtime._closed_pending_idle_handles
    finally:
        # Clean up remaining handles and ensure worker is cleanly shut down even if assertions fail
        if runtime._worker is not None and runtime._worker.is_alive():
            runtime._worker.shutdown(timeout=2.0)
        runtime._closed_pending_idle_handles.clear()
        runtime._open_document_handles.clear()
        runtime.teardown()


def test_connect_application_captures_version_unavailable_warning() -> None:
    """Proves that missing Version property records a sanitized diagnostic warning without failing connection."""
    runtime = SolidEdgeRuntime()
    mock_app = MockSolidEdgeApp()
    mock_app.Version = None  # type: ignore[assignment]

    with patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules:
        mock_win32_client = MagicMock()
        mock_win32_client.GetActiveObject.return_value = mock_app
        mock_modules.return_value = (None, mock_win32_client)

        app_handle = runtime.connect_application()
        assert app_handle.version_build is None
        assert len(app_handle.warnings) == 1
        assert app_handle.warnings[0]["code"] == "VERSION_METADATA_UNAVAILABLE"

        diag = runtime.get_diagnostics()
        assert diag.version_build is None
        assert len(diag.warnings) == 1
        assert diag.warnings[0]["code"] == "VERSION_METADATA_UNAVAILABLE"
        assert diag.is_healthy is True

        runtime.teardown()


def test_connect_application_captures_version_exception_warning() -> None:
    """Proves that an exception while reading Version property records a sanitized warning and connects safely."""
    runtime = SolidEdgeRuntime()

    class ExceptionVersionApp(MockSolidEdgeApp):
        @property
        def Version(self) -> str:
            raise RuntimeError("C:\\Secret\\Path\\COM Version call failed (0x80004005)")

        @Version.setter
        def Version(self, val: Any) -> None:
            pass

    mock_app = ExceptionVersionApp()

    with patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules:
        mock_win32_client = MagicMock()
        mock_win32_client.GetActiveObject.return_value = mock_app
        mock_modules.return_value = (None, mock_win32_client)

        app_handle = runtime.connect_application()
        assert app_handle.version_build is None
        assert len(app_handle.warnings) == 1
        assert app_handle.warnings[0]["code"] == "VERSION_METADATA_UNAVAILABLE"
        # Invariant: Exception details are masked and sanitized
        assert "Secret" not in app_handle.warnings[0]["message"]
        assert "0x80004005" not in app_handle.warnings[0]["message"]

        diag = runtime.get_diagnostics()
        assert len(diag.warnings) == 1
        assert diag.warnings[0]["code"] == "VERSION_METADATA_UNAVAILABLE"
        assert diag.is_healthy is True

        runtime.teardown()


def test_close_document_raises_when_runtime_is_poisoned() -> None:
    """Proves that close_document immediately raises CADRuntimeError when runtime is poisoned without re-entering COM."""
    runtime = SolidEdgeRuntime()
    runtime._is_poisoned = True

    handle = SolidEdgePartDocumentHandle(handle_id="test-handle-id")
    with pytest.raises(CADRuntimeError, match="runtime is poisoned"):
        runtime.close_document(handle)


def test_close_document_exact_sequence_order() -> None:
    """Proves the exact close sequence: Close(False) -> raw reference removal -> DoIdle() -> public handle removal."""
    runtime = SolidEdgeRuntime()
    mock_app = MockSolidEdgeApp()

    with patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules:
        mock_win32_client = MagicMock()
        mock_win32_client.GetActiveObject.return_value = mock_app
        mock_modules.return_value = (None, mock_win32_client)

        app_handle = runtime.connect_application()
        doc_handle = runtime.create_part_document(app_handle)
        handle_id = doc_handle.handle_id

        created_mock_doc = mock_app.Documents.added_docs[0]
        worker = runtime._worker
        assert worker is not None

        events: list[str] = []

        orig_close = created_mock_doc.Close

        def _recording_close(save_changes: bool = False) -> None:
            events.append(f"close_{save_changes}")
            assert handle_id in worker._document_registry
            assert handle_id in runtime._open_document_handles
            assert handle_id not in runtime._closed_pending_idle_handles
            orig_close(save_changes)

        created_mock_doc.Close = _recording_close  # type: ignore[method-assign]

        def _recording_doidle() -> None:
            events.append("doidle")
            assert created_mock_doc.closed is True
            assert created_mock_doc.closed_save_arg is False
            assert handle_id not in worker._document_registry
            assert handle_id in runtime._closed_pending_idle_handles
            assert handle_id in runtime._open_document_handles
            mock_app.idle_called = True
            mock_app.idle_call_count += 1

        mock_app.DoIdle = _recording_doidle  # type: ignore[method-assign]

        runtime.close_document(doc_handle)

        assert events == ["close_False", "doidle"]
        assert handle_id not in runtime._closed_pending_idle_handles
        assert handle_id not in runtime._open_document_handles
        assert handle_id not in worker._document_registry

    runtime.teardown()


def test_close_document_doidle_failure_retains_handles_without_raw_doc() -> None:
    """Proves that DoIdle() failure retains public handle and pending-idle state while removing raw doc."""
    runtime = SolidEdgeRuntime()
    mock_app = MockSolidEdgeApp()

    with patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules:
        mock_win32_client = MagicMock()
        mock_win32_client.GetActiveObject.return_value = mock_app
        mock_modules.return_value = (None, mock_win32_client)

        app_handle = runtime.connect_application()
        doc_handle = runtime.create_part_document(app_handle)
        handle_id = doc_handle.handle_id

        created_mock_doc = mock_app.Documents.added_docs[0]
        worker = runtime._worker
        assert worker is not None

        def _failing_doidle() -> None:
            raise RuntimeError("Simulated DoIdle COM failure")

        mock_app.DoIdle = _failing_doidle  # type: ignore[method-assign]

        with pytest.raises(CADRuntimeError):
            runtime.close_document(doc_handle)

        assert created_mock_doc.closed is True
        assert created_mock_doc.closed_save_arg is False
        assert handle_id not in worker._document_registry
        assert handle_id in runtime._closed_pending_idle_handles
        assert handle_id in runtime._open_document_handles
        assert runtime._is_poisoned is False

    runtime.teardown()


def test_close_document_retry_after_doidle_failure_does_not_double_close() -> None:
    """Proves that retrying close_document after DoIdle() failure calls only DoIdle() without double-closing."""
    runtime = SolidEdgeRuntime()
    mock_app = MockSolidEdgeApp()

    with patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules:
        mock_win32_client = MagicMock()
        mock_win32_client.GetActiveObject.return_value = mock_app
        mock_modules.return_value = (None, mock_win32_client)

        app_handle = runtime.connect_application()
        doc_handle = runtime.create_part_document(app_handle)
        handle_id = doc_handle.handle_id

        created_mock_doc = mock_app.Documents.added_docs[0]
        close_call_count = 0
        orig_close = created_mock_doc.Close

        def _counting_close(save_changes: bool = False) -> None:
            nonlocal close_call_count
            close_call_count += 1
            orig_close(save_changes)

        created_mock_doc.Close = _counting_close  # type: ignore[method-assign]

        idle_call_count = 0

        def _flaky_doidle() -> None:
            nonlocal idle_call_count
            idle_call_count += 1
            if idle_call_count == 1:
                raise RuntimeError("Temporary DoIdle COM busy error")

        mock_app.DoIdle = _flaky_doidle  # type: ignore[method-assign]

        with pytest.raises(CADRuntimeError):
            runtime.close_document(doc_handle)

        assert close_call_count == 1
        assert idle_call_count == 1
        assert handle_id in runtime._closed_pending_idle_handles
        assert handle_id in runtime._open_document_handles

        runtime.close_document(doc_handle)

        assert close_call_count == 1
        assert idle_call_count == 2
        assert handle_id not in runtime._closed_pending_idle_handles
        assert handle_id not in runtime._open_document_handles

    runtime.teardown()


def test_close_document_forgery_and_untracked_rejection() -> None:
    """Proves that close_document rejects invalid, untracked, mismatched, and forged handles."""
    runtime = SolidEdgeRuntime()
    mock_app = MockSolidEdgeApp()

    with patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules:
        mock_win32_client = MagicMock()
        mock_win32_client.GetActiveObject.return_value = mock_app
        mock_modules.return_value = (None, mock_win32_client)

        app_handle = runtime.connect_application()
        doc_handle = runtime.create_part_document(app_handle)
        valid_id = doc_handle.handle_id

        with pytest.raises(CADDocumentError, match="Invalid document handle type"):
            runtime.close_document("not-a-handle")

        with pytest.raises(CADDocumentError, match="Invalid document handle type"):
            runtime.close_document(object())

        empty_handle = SolidEdgePartDocumentHandle(handle_id="")
        with pytest.raises(CADDocumentError, match="missing handle_id"):
            runtime.close_document(empty_handle)

        untracked = SolidEdgePartDocumentHandle(handle_id="00000000-0000-0000-0000-000000000000")
        with pytest.raises(CADDocumentError) as exc_info:
            runtime.close_document(untracked)
        assert exc_info.value.error_code == "DOCUMENT_LOST"

        mismatched_type = SolidEdgeDocumentHandle(handle_id=valid_id, path=Path("E:/test/dummy.par"))
        with pytest.raises(CADDocumentError, match="handle type mismatch"):
            runtime.close_document(mismatched_type)

        runtime.close_document(doc_handle)
        assert valid_id not in runtime._open_document_handles

        with pytest.raises(CADDocumentError) as exc_info_closed:
            runtime.close_document(doc_handle)
        assert exc_info_closed.value.error_code == "DOCUMENT_LOST"

    runtime.teardown()


def test_close_document_generic_document_metadata_forgery_rejection(tmp_path: Path) -> None:
    """Proves that close_document rejects generic handles whose path metadata was forged or altered."""
    runtime = SolidEdgeRuntime()
    mock_app = MockSolidEdgeApp()

    dummy_doc = tmp_path / "valid_model.par"
    dummy_doc.write_text("content", encoding="utf-8")

    with patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules:
        mock_win32_client = MagicMock()
        mock_win32_client.GetActiveObject.return_value = mock_app
        mock_modules.return_value = (None, mock_win32_client)

        app_handle = runtime.connect_application()
        genuine_handle = runtime.open_document(app_handle, dummy_doc)
        valid_id = genuine_handle.handle_id

        forged_handle = SolidEdgeDocumentHandle(handle_id=valid_id, path=tmp_path / "forged_model.par")
        with pytest.raises(CADDocumentError) as exc_info:
            runtime.close_document(forged_handle)
        assert exc_info.value.error_code == "DOCUMENT_LOST"
        assert "metadata does not match" in str(exc_info.value)

        runtime.close_document(genuine_handle)
        assert valid_id not in runtime._open_document_handles

    runtime.teardown()


def test_close_document_fails_and_retains_pending_idle_when_raw_app_is_none() -> None:
    """Proves close fails and retains pending-idle state when raw_app is unavailable, preventing false success and double-close."""
    runtime = SolidEdgeRuntime()
    mock_app = MockSolidEdgeApp()

    with patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules:
        mock_win32_client = MagicMock()
        mock_win32_client.GetActiveObject.return_value = mock_app
        mock_modules.return_value = (None, mock_win32_client)

        app_handle = runtime.connect_application()
        doc_handle = runtime.create_part_document(app_handle)
        handle_id = doc_handle.handle_id

        created_mock_doc = mock_app.Documents.added_docs[0]
        worker = runtime._worker
        assert worker is not None

        close_call_count = 0
        orig_close = created_mock_doc.Close

        def _counting_close(save_changes: bool = False) -> None:
            nonlocal close_call_count
            close_call_count += 1
            orig_close(save_changes)

        created_mock_doc.Close = _counting_close  # type: ignore[method-assign]

        # Simulate that after tracking the document, the raw application reference becomes unavailable
        worker._raw_app = None

        # 1. Close must NOT claim success: it must fail and raise CADRuntimeError
        with pytest.raises(CADRuntimeError, match="No active Solid Edge application instance"):
            runtime.close_document(doc_handle)

        # 2. Invariants: Close(False) was called once, raw doc was removed from registry
        assert close_call_count == 1
        assert created_mock_doc.closed is True
        assert handle_id not in worker._document_registry

        # 3. Invariants: State is NOT discarded; retained as pending-idle and open handle
        assert handle_id in runtime._closed_pending_idle_handles
        assert handle_id in runtime._open_document_handles

        # 4. Retrying close while raw_app is still None raises again and NEVER calls Close(False) again
        with pytest.raises(CADRuntimeError, match="No active Solid Edge application instance"):
            runtime.close_document(doc_handle)
        assert close_call_count == 1  # No duplicate Close(False)

        # 5. Restoring raw_app allows retry to complete DoIdle() and cleanly finalize close without double-close
        worker._raw_app = mock_app
        runtime.close_document(doc_handle)

        assert close_call_count == 1  # Still exactly 1: no duplicate Close(False)
        assert mock_app.idle_called is True
        assert handle_id not in runtime._closed_pending_idle_handles
        assert handle_id not in runtime._open_document_handles

    runtime.teardown()


def test_close_document_timeout_preserves_tracking_when_worker_completes_late() -> None:
    """Proves that a timeout during close_document preserves pending-idle and public tracking even after late completion."""
    runtime = SolidEdgeRuntime()
    mock_app = MockSolidEdgeApp()
    unblock_idle_event = threading.Event()
    idle_started_event = threading.Event()

    try:
        with patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules:
            mock_win32_client = MagicMock()
            mock_win32_client.GetActiveObject.return_value = mock_app
            mock_modules.return_value = (None, mock_win32_client)

            app_handle = runtime.connect_application()
            doc_handle = runtime.create_part_document(app_handle)
            handle_id = doc_handle.handle_id

            def _delayed_idle() -> None:
                idle_started_event.set()
                unblock_idle_event.wait(timeout=5.0)

            mock_app.DoIdle = _delayed_idle  # type: ignore[method-assign]

            # Call _execute_document_close with a very short timeout so worker.call times out deterministically
            with pytest.raises(TimeoutError):
                runtime._execute_document_close(handle_id, timeout=0.05)

            # Confirm the runtime is poisoned from the timeout
            assert runtime._is_poisoned is True
            assert idle_started_event.is_set() is True

            # Now unblock the worker thread so DoIdle completes late
            unblock_idle_event.set()

            # Wait for the worker thread to finish processing the task
            worker = runtime._worker
            assert worker is not None
            # Drain/sync with worker thread by submitting a no-op task
            sync_task = worker.submit(lambda: None)
            sync_task.result(timeout=5.0)

            # Invariant: Despite worker-side completion, fail-closed tracking state is NOT discarded
            assert handle_id in runtime._open_document_handles
            assert handle_id in runtime._closed_pending_idle_handles
            assert handle_id not in worker._document_registry
    finally:
        unblock_idle_event.set()
        if runtime._worker is not None and runtime._worker.is_alive():
            runtime._worker.shutdown(timeout=2.0)
        runtime._closed_pending_idle_handles.clear()
        runtime._open_document_handles.clear()
        runtime.teardown()


def test_create_part_document_timeout_modal_deferred_cleanup_sequence() -> None:
    """Proves:
    1. Timeout while Ordered-mode setter is blocked does NOT trigger premature close.
    2. Dialog release is followed by exactly one Close(False) and successful DoIdle().
    3. Borrowed application is never quit or terminated.
    4. Raw COM objects are never read or released from caller thread.
    """
    runtime = SolidEdgeRuntime()
    mock_app = MockSolidEdgeApp()
    dialog_unblock = threading.Event()
    modal_started = threading.Event()

    class ModalBlockingPartDoc(MockDoc):
        def __init__(self) -> None:
            self._mode = 0
            super().__init__()

        @property
        def ModelingMode(self) -> int:
            return self._mode

        @ModelingMode.setter
        def ModelingMode(self, val: int) -> None:
            if val == 2:
                modal_started.set()
                dialog_unblock.wait(timeout=10.0)
            self._mode = val

    class ModalDocuments:
        def __init__(self) -> None:
            self.docs: list[ModalBlockingPartDoc] = []

        def Add(self, prog_id: str) -> ModalBlockingPartDoc:
            doc = ModalBlockingPartDoc()
            self.docs.append(doc)
            return doc

    mock_app.Documents = ModalDocuments()  # type: ignore[assignment]

    try:
        with (
            patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules,
            patch("drivers.solidedge.runtime.DEFAULT_DOC_CREATE_TIMEOUT", 0.05),
        ):
            mock_win32_client = MagicMock()
            mock_win32_client.GetActiveObject.return_value = mock_app
            mock_modules.return_value = (None, mock_win32_client)

            app_handle = runtime.connect_application()

            # Caller timeout: 0.05s, while modal is held
            with pytest.raises(CADDocumentError) as exc_info:
                runtime.create_part_document(app_handle)

            assert "timed out fail-closed" in str(exc_info.value)
            assert modal_started.is_set() is True
            created_doc = mock_app.Documents.docs[0]

            # Invariant 1: No premature close while modal is still blocked
            assert created_doc.closed is False
            assert created_doc.close_call_count == 0
            assert len(runtime._open_document_handles) == 0

            # Invariant 2: No raw COM references leak across thread boundary
            pending = runtime._pending_creation
            assert pending is not None
            assert pending.abandoned.is_set() is True
            assert not hasattr(pending, "raw_doc")
            assert not hasattr(pending, "_raw_app")

            # Release the dialog
            dialog_unblock.set()

            # Invariant 3: Teardown waits on bounded deferred cleanup, closes doc exactly once, and runs DoIdle
            clean = runtime.teardown()
            assert clean is True
            assert created_doc.closed is True
            assert created_doc.closed_save_arg is False
            assert created_doc.close_call_count == 1
            assert mock_app.idle_called is True
            assert mock_app.quit_called is False
            assert len(runtime._open_document_handles) == 0
            assert len(runtime._closed_pending_idle_handles) == 0
    finally:
        dialog_unblock.set()
        runtime.teardown()


def test_create_part_document_modal_never_released_fails_teardown_truthfully() -> None:
    """Proves that if a vendor dialog remains blocked past the teardown recovery window:
    1. Teardown returns False (incomplete cleanup).
    2. Software truthfully does NOT claim the Part was closed.
    3. Borrowed Solid Edge is NOT terminated.
    """
    runtime = SolidEdgeRuntime()
    mock_app = MockSolidEdgeApp()
    dialog_unblock = threading.Event()

    class PermanentlyBlockedPartDoc(MockDoc):
        def __init__(self) -> None:
            self._mode = 0
            super().__init__()

        @property
        def ModelingMode(self) -> int:
            return self._mode

        @ModelingMode.setter
        def ModelingMode(self, val: int) -> None:
            if val == 2:
                dialog_unblock.wait(timeout=10.0)
            self._mode = val

    class BlockingDocs:
        def __init__(self) -> None:
            self.docs: list[PermanentlyBlockedPartDoc] = []

        def Add(self, prog_id: str) -> PermanentlyBlockedPartDoc:
            doc = PermanentlyBlockedPartDoc()
            self.docs.append(doc)
            return doc

    mock_app.Documents = BlockingDocs()  # type: ignore[assignment]

    try:
        with (
            patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules,
            patch("drivers.solidedge.runtime.DEFAULT_DOC_CREATE_TIMEOUT", 0.05),
            patch("drivers.solidedge.runtime.DEFAULT_DEFERRED_CREATE_TEARDOWN_TIMEOUT", 0.05),
        ):
            mock_win32_client = MagicMock()
            mock_win32_client.GetActiveObject.return_value = mock_app
            mock_modules.return_value = (None, mock_win32_client)

            app_handle = runtime.connect_application()

            with pytest.raises(CADDocumentError):
                runtime.create_part_document(app_handle)

            created_doc = mock_app.Documents.docs[0]
            assert created_doc.closed is False

            # Teardown without unblocking dialog
            clean = runtime.teardown()
            assert clean is False
            assert runtime._is_poisoned is True
            assert created_doc.closed is False
            assert mock_app.quit_called is False
    finally:
        dialog_unblock.set()
        if runtime._worker is not None and runtime._worker.is_alive():
            runtime._worker.shutdown(timeout=1.0)


def test_create_part_document_timeout_before_handle_metadata_published() -> None:
    """Proves that if caller times out before Add returns:
    1. Worker observes abandonment immediately upon Add completion.
    2. Mode configuration is skipped.
    3. Document is closed immediately without waiting for dialog.
    """
    runtime = SolidEdgeRuntime()
    mock_app = MockSolidEdgeApp()
    unblock_add = threading.Event()
    add_started = threading.Event()

    class SlowAddDocuments:
        def __init__(self) -> None:
            self.docs: list[MockDoc] = []

        def Add(self, prog_id: str) -> MockDoc:
            add_started.set()
            unblock_add.wait(timeout=10.0)
            doc = MockDoc()
            self.docs.append(doc)
            return doc

    mock_app.Documents = SlowAddDocuments()  # type: ignore[assignment]

    try:
        with (
            patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules,
            patch("drivers.solidedge.runtime.DEFAULT_DOC_CREATE_TIMEOUT", 0.05),
        ):
            mock_win32_client = MagicMock()
            mock_win32_client.GetActiveObject.return_value = mock_app
            mock_modules.return_value = (None, mock_win32_client)

            app_handle = runtime.connect_application()

            with pytest.raises(CADDocumentError):
                runtime.create_part_document(app_handle)

            assert add_started.is_set() is True

            # Unblock Add
            unblock_add.set()

            # Teardown resolves deferred cleanup
            clean = runtime.teardown()
            assert clean is True
            created_doc = mock_app.Documents.docs[0]
            assert created_doc.closed is True
            assert created_doc.closed_save_arg is False
    finally:
        unblock_add.set()
        runtime.teardown()


def test_create_part_document_deferred_close_failure_makes_teardown_fail() -> None:
    """Proves that if deferred Close(False) fails upon dialog release:
    1. Tracking is retained truthful in open handles.
    2. Teardown reports False.
    3. Runtime is poisoned.
    """
    runtime = SolidEdgeRuntime()
    mock_app = MockSolidEdgeApp()
    dialog_unblock = threading.Event()

    class FailingClosePartDoc(MockDoc):
        def __init__(self) -> None:
            self._mode = 0
            super().__init__()

        @property
        def ModelingMode(self) -> int:
            return self._mode

        @ModelingMode.setter
        def ModelingMode(self, val: int) -> None:
            if val == 2:
                dialog_unblock.wait(timeout=10.0)
            self._mode = val

        def Close(self, save_changes: bool = False) -> None:
            raise RuntimeError("Deferred Close(False) failed in COM")

    class FailingCloseDocuments:
        def __init__(self) -> None:
            self.docs: list[FailingClosePartDoc] = []

        def Add(self, prog_id: str) -> FailingClosePartDoc:
            doc = FailingClosePartDoc()
            self.docs.append(doc)
            return doc

    mock_app.Documents = FailingCloseDocuments()  # type: ignore[assignment]

    try:
        with (
            patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules,
            patch("drivers.solidedge.runtime.DEFAULT_DOC_CREATE_TIMEOUT", 0.05),
        ):
            mock_win32_client = MagicMock()
            mock_win32_client.GetActiveObject.return_value = mock_app
            mock_modules.return_value = (None, mock_win32_client)

            app_handle = runtime.connect_application()

            with pytest.raises(CADDocumentError):
                runtime.create_part_document(app_handle)

            dialog_unblock.set()

            clean = runtime.teardown()
            assert clean is False
            assert runtime._is_poisoned is True
            assert len(runtime._open_document_handles) == 1
    finally:
        dialog_unblock.set()
        if runtime._worker is not None and runtime._worker.is_alive():
            runtime._worker.shutdown(timeout=1.0)
        runtime._open_document_handles.clear()


def test_create_part_document_deferred_doidle_failure_makes_teardown_fail() -> None:
    """Proves that if deferred DoIdle() fails upon dialog release:
    1. Close(False) succeeds, but handle is retained in pending-idle.
    2. Teardown reports False.
    3. Runtime is poisoned.
    """
    runtime = SolidEdgeRuntime()
    mock_app = MockSolidEdgeApp()
    dialog_unblock = threading.Event()

    class DeferredDoc(MockDoc):
        def __init__(self) -> None:
            self._mode = 0
            super().__init__()

        @property
        def ModelingMode(self) -> int:
            return self._mode

        @ModelingMode.setter
        def ModelingMode(self, val: int) -> None:
            if val == 2:
                dialog_unblock.wait(timeout=10.0)
            self._mode = val

    class DeferredDocs:
        def __init__(self) -> None:
            self.docs: list[DeferredDoc] = []

        def Add(self, prog_id: str) -> DeferredDoc:
            doc = DeferredDoc()
            self.docs.append(doc)
            return doc

    mock_app.Documents = DeferredDocs()  # type: ignore[assignment]

    def _failing_idle() -> None:
        raise RuntimeError("Deferred DoIdle failed in COM")

    mock_app.DoIdle = _failing_idle  # type: ignore[method-assign]

    try:
        with (
            patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules,
            patch("drivers.solidedge.runtime.DEFAULT_DOC_CREATE_TIMEOUT", 0.05),
        ):
            mock_win32_client = MagicMock()
            mock_win32_client.GetActiveObject.return_value = mock_app
            mock_modules.return_value = (None, mock_win32_client)

            app_handle = runtime.connect_application()

            with pytest.raises(CADDocumentError):
                runtime.create_part_document(app_handle)

            dialog_unblock.set()

            clean = runtime.teardown()
            assert clean is False
            assert runtime._is_poisoned is True
            created_doc = mock_app.Documents.docs[0]
            assert created_doc.closed is True
            assert len(runtime._closed_pending_idle_handles) == 1
    finally:
        dialog_unblock.set()
        if runtime._worker is not None and runtime._worker.is_alive():
            runtime._worker.shutdown(timeout=1.0)
        runtime._closed_pending_idle_handles.clear()


def test_repeated_teardown_is_safe_and_idempotent_after_deferred_cleanup() -> None:
    """Proves that repeated teardown calls after deferred cleanup do not attempt double close."""
    runtime = SolidEdgeRuntime()
    mock_app = MockSolidEdgeApp()
    dialog_unblock = threading.Event()

    class CountingCloseDoc(MockDoc):
        def __init__(self) -> None:
            self._mode = 0
            super().__init__()

        @property
        def ModelingMode(self) -> int:
            return self._mode

        @ModelingMode.setter
        def ModelingMode(self, val: int) -> None:
            if val == 2:
                dialog_unblock.wait(timeout=10.0)
            self._mode = val

    class CountingDocs:
        def __init__(self) -> None:
            self.docs: list[CountingCloseDoc] = []

        def Add(self, prog_id: str) -> CountingCloseDoc:
            doc = CountingCloseDoc()
            self.docs.append(doc)
            return doc

    mock_app.Documents = CountingDocs()  # type: ignore[assignment]

    try:
        with (
            patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules,
            patch("drivers.solidedge.runtime.DEFAULT_DOC_CREATE_TIMEOUT", 0.05),
        ):
            mock_win32_client = MagicMock()
            mock_win32_client.GetActiveObject.return_value = mock_app
            mock_modules.return_value = (None, mock_win32_client)

            app_handle = runtime.connect_application()

            with pytest.raises(CADDocumentError):
                runtime.create_part_document(app_handle)

            dialog_unblock.set()

            # First teardown executes deferred cleanup
            clean1 = runtime.teardown()
            assert clean1 is True
            created_doc = mock_app.Documents.docs[0]
            assert created_doc.close_count == 1

            # Second teardown is an idempotent no-op
            clean2 = runtime.teardown()
            assert clean2 is True
            assert created_doc.close_count == 1  # No double close
    finally:
        dialog_unblock.set()
        runtime.teardown()


def test_create_part_document_future_publication_race_wins_ownership() -> None:
    """Proves the exact race condition where caller's fut.result(timeout=...) times out
    at the exact moment the worker task finished and published doc_handle under pending.lock:
    1. Caller takes ownership via pending.doc_handle without calling fut.result(timeout=0).
    2. Document is NOT abandoned.
    3. Caller registers the document in _open_document_handles.
    4. Teardown cleanly closes the document.
    """
    runtime = SolidEdgeRuntime()
    mock_app = MockSolidEdgeApp()

    with (
        patch("drivers.solidedge.runtime._load_pywin32_modules") as mock_modules,
        patch("drivers.solidedge.runtime.DEFAULT_DOC_CREATE_TIMEOUT", 0.001),
    ):
        mock_win32_client = MagicMock()
        mock_win32_client.GetActiveObject.return_value = mock_app
        mock_modules.return_value = (None, mock_win32_client)

        app_handle = runtime.connect_application()
        worker = runtime._worker
        assert worker is not None

        # Intercept worker.submit to simulate fut.result() raising TimeoutError
        # even though pending.doc_handle is populated under pending.lock
        orig_submit = worker.submit

        def _racing_submit(fn: Any) -> Any:
            fut = orig_submit(fn)

            class RacingFuture:
                def result(self, timeout: float | None = None) -> Any:
                    # Wait until task completes and publishes doc_handle
                    fut.result(timeout=5.0)
                    # Simulate caller timing out at the boundary
                    raise concurrent.futures.TimeoutError("Simulated race timeout")

            return RacingFuture()

        with patch.object(worker, "submit", side_effect=_racing_submit):
            doc = runtime.create_part_document(app_handle)

        # Proves caller claimed ownership through the pending.doc_handle handshake
        assert doc is not None
        assert doc.handle_id in runtime._open_document_handles
        assert runtime._pending_creation is None

        # Proves document was not abandoned or closed prematurely
        created_doc = mock_app.Documents.docs[0]
        assert created_doc.closed is False

        # Proves normal teardown closes the owned document cleanly
        clean = runtime.teardown()
        assert clean is True
        assert created_doc.closed is True
        assert created_doc.close_count == 1
