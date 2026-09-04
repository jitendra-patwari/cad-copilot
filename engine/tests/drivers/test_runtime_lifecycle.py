"""Unit tests for STA runtime lifecycle, connection modes, handles, and diagnostics."""

from __future__ import annotations

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

    def Close(self, save_changes: bool = False) -> None:
        self.closed = True
        self.closed_save_arg = save_changes


class MockDocuments:
    """Mock Solid Edge Documents collection."""

    def __init__(self) -> None:
        self.added_docs: list[MockDoc] = []
        self.opened_paths: list[str] = []

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
        self.thread_ids_called: list[int] = []

    def Quit(self) -> None:
        self.quit_called = True
        self.thread_ids_called.append(threading.get_ident())


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
        assert len(runtime._open_document_handles) == 0

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

        # Invariant 3: Teardown executes Stage 1 document close, retrying and closing the tracked doc
        runtime.teardown()
        assert created_doc.closed is True
        assert created_doc.closed_save_arg is False
        assert created_doc.close_attempt_count == 2
        assert len(runtime._open_document_handles) == 0


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
