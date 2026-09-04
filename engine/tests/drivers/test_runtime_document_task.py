"""Unit tests for SolidEdgeRuntime.run_document_task STA document task seam."""

from __future__ import annotations

import threading
import time
import traceback
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from drivers.solidedge.runtime import SolidEdgeRuntime, STAThreadWorker
from drivers.solidedge.types import (
    AttachmentMode,
    OwnershipMode,
    SolidEdgeApplicationHandle,
    SolidEdgeDocumentHandle,
    SolidEdgePartDocumentHandle,
)
from interfaces.exceptions import (
    CADDocumentError,
    CADExecutionError,
    CADExportError,
    CADRuntimeError,
)


@pytest.fixture
def runtime_with_mock_doc() -> Iterator[tuple[SolidEdgeRuntime, SolidEdgePartDocumentHandle, MagicMock]]:
    """Provide a SolidEdgeRuntime with a running STA worker and a mock registered Part document."""
    runtime = SolidEdgeRuntime()
    worker = runtime._ensure_worker()
    mock_app = MagicMock()
    worker._raw_app = mock_app

    runtime._application = SolidEdgeApplicationHandle(
        handle_id="test-app-1",
        ownership=OwnershipMode.BORROWED,
        attachment_mode=AttachmentMode.ATTACHED_EXISTING,
    )

    handle_id = "test-part-doc-1"
    doc_handle = SolidEdgePartDocumentHandle(handle_id=handle_id)
    mock_raw_doc = MagicMock()
    mock_raw_doc.ModelingMode = 2

    worker._document_registry[handle_id] = mock_raw_doc
    runtime._open_document_handles[handle_id] = doc_handle

    try:
        yield runtime, doc_handle, mock_raw_doc
    finally:
        runtime.teardown(force_kill_on_failure=False)


def test_run_document_task_success_executes_on_worker_thread(
    runtime_with_mock_doc: tuple[SolidEdgeRuntime, SolidEdgePartDocumentHandle, MagicMock],
) -> None:
    """Proves that a valid document task executes on the dedicated STA worker thread."""
    runtime, doc_handle, mock_raw_doc = runtime_with_mock_doc
    worker = runtime._worker
    assert worker is not None

    caller_thread_id = threading.get_ident()
    task_thread_id: list[int | None] = []

    def _sample_task(raw_doc: Any, w: STAThreadWorker) -> dict[str, Any]:
        task_thread_id.append(threading.get_ident())
        assert raw_doc is mock_raw_doc
        assert w is worker
        return {"status": "ok", "mode": getattr(raw_doc, "ModelingMode", None)}

    result = runtime.run_document_task(doc_handle, _sample_task, timeout=5.0)

    assert result == {"status": "ok", "mode": 2}
    assert len(task_thread_id) == 1
    assert task_thread_id[0] == worker.thread_id
    assert task_thread_id[0] != caller_thread_id


def test_run_document_task_rejects_non_part_handle(
    runtime_with_mock_doc: tuple[SolidEdgeRuntime, SolidEdgePartDocumentHandle, MagicMock],
) -> None:
    """Proves that non-part document handles are rejected before worker execution."""
    runtime, _, _ = runtime_with_mock_doc

    non_part_handle = SolidEdgeDocumentHandle(handle_id="doc-2", path=Path("part.par"))

    with pytest.raises(CADDocumentError, match="Invalid document handle type: expected SolidEdgePartDocumentHandle"):
        runtime.run_document_task(non_part_handle, lambda doc, w: None)  # type: ignore[arg-type]

    with pytest.raises(CADDocumentError, match="Invalid document handle type"):
        runtime.run_document_task("invalid-handle-string", lambda doc, w: None)  # type: ignore[arg-type]

    with pytest.raises(CADDocumentError, match="Invalid document handle type"):
        runtime.run_document_task(None, lambda doc, w: None)  # type: ignore[arg-type]


def test_run_document_task_rejects_empty_handle_id(
    runtime_with_mock_doc: tuple[SolidEdgeRuntime, SolidEdgePartDocumentHandle, MagicMock],
) -> None:
    """Proves that a handle with empty handle_id is rejected."""
    runtime, _, _ = runtime_with_mock_doc
    empty_handle = SolidEdgePartDocumentHandle(handle_id="")

    with pytest.raises(CADDocumentError, match="Invalid document handle: missing handle_id"):
        runtime.run_document_task(empty_handle, lambda doc, w: None)


def test_run_document_task_rejects_untracked_handle(
    runtime_with_mock_doc: tuple[SolidEdgeRuntime, SolidEdgePartDocumentHandle, MagicMock],
) -> None:
    """Proves that an unknown/untracked handle is rejected before worker execution."""
    runtime, _, _ = runtime_with_mock_doc
    untracked_handle = SolidEdgePartDocumentHandle(handle_id="unknown-handle-999")

    with pytest.raises(CADDocumentError, match="Untracked or closed document handle"):
        runtime.run_document_task(untracked_handle, lambda doc, w: None)


def test_run_document_task_rejects_closed_handle(
    runtime_with_mock_doc: tuple[SolidEdgeRuntime, SolidEdgePartDocumentHandle, MagicMock],
) -> None:
    """Proves that a previously closed document handle is rejected."""
    runtime, doc_handle, mock_raw_doc = runtime_with_mock_doc

    runtime.close_document(doc_handle)
    mock_raw_doc.Close.assert_called_once_with(False)

    with pytest.raises(CADDocumentError, match="Untracked or closed document handle"):
        runtime.run_document_task(doc_handle, lambda doc, w: None)


def test_run_document_task_missing_raw_doc_in_worker_registry(
    runtime_with_mock_doc: tuple[SolidEdgeRuntime, SolidEdgePartDocumentHandle, MagicMock],
) -> None:
    """Proves that a handle missing in worker registry raises CADDocumentError on worker."""
    runtime, doc_handle, _ = runtime_with_mock_doc
    assert runtime._worker is not None
    runtime._worker._document_registry.pop(doc_handle.handle_id, None)

    with pytest.raises(CADDocumentError, match="Document object not found in STA worker registry"):
        runtime.run_document_task(doc_handle, lambda doc, w: None)


def test_run_document_task_timeout_poisons_runtime(
    runtime_with_mock_doc: tuple[SolidEdgeRuntime, SolidEdgePartDocumentHandle, MagicMock],
) -> None:
    """Proves that a document task exceeding timeout poisons the runtime fail-closed."""
    runtime, doc_handle, _ = runtime_with_mock_doc

    def _hanging_task(raw_doc: Any, w: STAThreadWorker) -> None:
        time.sleep(0.5)

    with pytest.raises(TimeoutError, match="timed out"):
        runtime.run_document_task(doc_handle, _hanging_task, timeout=0.05)

    assert runtime._is_poisoned is True
    assert runtime.is_healthy() is False

    # Subsequent tasks are rejected immediately
    with pytest.raises(CADRuntimeError, match="runtime is poisoned"):
        runtime.run_document_task(doc_handle, lambda doc, w: None)


def test_run_document_task_propagates_application_exceptions_without_poisoning(
    runtime_with_mock_doc: tuple[SolidEdgeRuntime, SolidEdgePartDocumentHandle, MagicMock],
) -> None:
    """Proves that modeling/execution exceptions are propagated without poisoning the runtime."""
    runtime, doc_handle, _ = runtime_with_mock_doc

    def _failing_task(raw_doc: Any, w: STAThreadWorker) -> None:
        raise CADExecutionError("Geometry invalid: cutout misses solid body", error_code="FEATURE_CREATION_FAILED")

    with pytest.raises(CADExecutionError) as exc_info:
        runtime.run_document_task(doc_handle, _failing_task)

    assert exc_info.value.error_code == "FEATURE_CREATION_FAILED"
    assert runtime._is_poisoned is False
    assert runtime.is_healthy() is True

    # Subsequent task succeeds
    success_result = runtime.run_document_task(doc_handle, lambda doc, w: "recovered")
    assert success_result == "recovered"


def test_run_document_task_rejects_when_worker_not_alive(
    runtime_with_mock_doc: tuple[SolidEdgeRuntime, SolidEdgePartDocumentHandle, MagicMock],
) -> None:
    """Proves that run_document_task rejects execution when the worker is not running."""
    runtime, doc_handle, _ = runtime_with_mock_doc
    assert runtime._worker is not None
    runtime._worker.shutdown(timeout=1.0)

    with pytest.raises(CADRuntimeError, match="STA worker thread is not running"):
        runtime.run_document_task(doc_handle, lambda doc, w: None)


def test_run_document_task_rejects_forged_part_handle_for_tracked_generic_document(
    runtime_with_mock_doc: tuple[SolidEdgeRuntime, SolidEdgePartDocumentHandle, MagicMock],
) -> None:
    """Proves that a forged Part handle matching a tracked generic document ID is rejected."""
    runtime, _, _ = runtime_with_mock_doc
    generic_id = "generic-opened-doc-1"
    generic_handle = SolidEdgeDocumentHandle(handle_id=generic_id, path=Path("opened.par"))

    # Register as generic opened document
    runtime._open_document_handles[generic_id] = generic_handle

    # Attacker / caller creates a forged Part handle with the same ID
    forged_part_handle = SolidEdgePartDocumentHandle(handle_id=generic_id)

    with pytest.raises(
        CADDocumentError,
        match="Tracked document handle is not a Part document: expected SolidEdgePartDocumentHandle, got SolidEdgeDocumentHandle",
    ):
        runtime.run_document_task(forged_part_handle, lambda doc, w: None)


def test_run_document_task_rejects_leaked_raw_doc_result(
    runtime_with_mock_doc: tuple[SolidEdgeRuntime, SolidEdgePartDocumentHandle, MagicMock],
) -> None:
    """Proves that returning raw_doc from a task raises CADExecutionError at the worker boundary."""
    runtime, doc_handle, _ = runtime_with_mock_doc

    with pytest.raises(CADExecutionError, match="Document task returned raw COM document or worker reference"):
        runtime.run_document_task(doc_handle, lambda doc, w: doc)


def test_run_document_task_rejects_leaked_worker_result(
    runtime_with_mock_doc: tuple[SolidEdgeRuntime, SolidEdgePartDocumentHandle, MagicMock],
) -> None:
    """Proves that returning the STAThreadWorker from a task raises CADExecutionError."""
    runtime, doc_handle, _ = runtime_with_mock_doc

    with pytest.raises(CADExecutionError, match="Document task returned raw COM document or worker reference"):
        runtime.run_document_task(doc_handle, lambda doc, w: w)


def test_run_document_task_rejects_leaked_com_object_result(
    runtime_with_mock_doc: tuple[SolidEdgeRuntime, SolidEdgePartDocumentHandle, MagicMock],
) -> None:
    """Proves that returning a raw COM object (or nested dict containing one) raises CADExecutionError."""
    runtime, doc_handle, _ = runtime_with_mock_doc

    class MockCOMObject:
        def __init__(self) -> None:
            self._oleobj_ = MagicMock()

    leaked_com = MockCOMObject()

    # Direct return
    with pytest.raises(CADExecutionError, match="Document task returned raw COM object reference"):
        runtime.run_document_task(doc_handle, lambda doc, w: leaked_com)

    # Nested dictionary return
    with pytest.raises(CADExecutionError, match="Document task returned raw COM object reference"):
        runtime.run_document_task(doc_handle, lambda doc, w: {"result": "ok", "leaked": [leaked_com]})


def test_run_document_task_rejects_leaked_com_object_in_dataclass(
    runtime_with_mock_doc: tuple[SolidEdgeRuntime, SolidEdgePartDocumentHandle, MagicMock],
) -> None:
    """Proves that a dataclass containing a raw COM object (e.g. ExecutionSuccess with COM payload) is rejected."""
    runtime, doc_handle, _ = runtime_with_mock_doc

    class MockCOMObject:
        def __init__(self) -> None:
            self._oleobj_ = MagicMock()

    leaked_com = MockCOMObject()

    from dataclasses import dataclass

    @dataclass
    class CustomTaskResult:
        status: str
        proxy: Any

    with pytest.raises(CADExecutionError, match="Document task returned raw COM object reference"):
        runtime.run_document_task(doc_handle, lambda doc, w: CustomTaskResult(status="ok", proxy=leaked_com))


def test_run_document_task_detaches_exception_chains_at_sta_boundary(
    runtime_with_mock_doc: tuple[SolidEdgeRuntime, SolidEdgePartDocumentHandle, MagicMock],
) -> None:
    """Proves that raw vendor exceptions, secret path text, and COM objects are detached at the STA worker boundary."""
    runtime, doc_handle, mock_raw_doc = runtime_with_mock_doc

    secret_dir = r"E:\Client, LLC\SECRET_PROJECT\model.par"
    raw_vendor_error = OSError(f"Access denied at {secret_dir}")

    def faulty_export_task(doc: Any, worker: Any) -> None:
        # Simulate an exporter raising a CADExportError with cause/context attached
        err = CADExportError(
            "Failed to export PAR artifact 'model.par': <path>",
            error_code="ARTIFACT_EXPORT_FAILED",
        )
        err.__cause__ = raw_vendor_error
        err.__context__ = raw_vendor_error
        raise err

    with pytest.raises(CADExportError) as exc_info:
        runtime.run_document_task(doc_handle, faulty_export_task)

    propagated_exc = exc_info.value

    # 1. Verify cause and context are stripped
    assert propagated_exc.__cause__ is None
    assert propagated_exc.__context__ is None
    assert propagated_exc.__suppress_context__ is True

    # 2. Verify formatted traceback does not leak secret directory, raw OSError, or worker internals
    tb_str = "".join(traceback.format_exception(propagated_exc))
    assert "SECRET_PROJECT" not in tb_str
    assert "Client, LLC" not in tb_str
    assert "Access denied" not in tb_str
    assert "raw_vendor_error" not in tb_str

    # 3. Verify no raw document is referenced in exception attributes
    for attr_name in dir(propagated_exc):
        if not attr_name.startswith("__"):
            val = getattr(propagated_exc, attr_name)
            assert val is not mock_raw_doc


def test_run_document_task_normalizes_and_detaches_raw_vendor_exception(
    runtime_with_mock_doc: tuple[SolidEdgeRuntime, SolidEdgePartDocumentHandle, MagicMock],
) -> None:
    """Proves that raw unhandled vendor exceptions are normalized and have tracebacks/chains detached."""
    runtime, doc_handle, _ = runtime_with_mock_doc

    secret_dir = r"E:\Client, LLC\SECRET_PROJECT\model.par"

    def faulty_raw_task(doc: Any, worker: Any) -> None:
        raise OSError(f"Disk failure at {secret_dir}")

    with pytest.raises(CADRuntimeError) as exc_info:
        runtime.run_document_task(doc_handle, faulty_raw_task)

    propagated_exc = exc_info.value
    assert propagated_exc.__cause__ is None
    assert propagated_exc.__context__ is None

    tb_str = "".join(traceback.format_exception(propagated_exc))
    assert "SECRET_PROJECT" not in tb_str
    assert "Client, LLC" not in tb_str
