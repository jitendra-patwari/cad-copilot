"""Single-Threaded Apartment (STA) Windows COM runtime for Siemens Solid Edge."""

from __future__ import annotations

import concurrent.futures
import contextlib
import dataclasses
import gc
import queue
import sys
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, TypeVar

from interfaces.exceptions import (
    CADDocumentError,
    CADExecutionError,
    CADRuntimeError,
    CADRuntimeUnavailableError,
)
from interfaces.models import RuntimeDiagnostics
from interfaces.runtime_abc import CADRuntimeABC

from .errors import (
    MK_E_UNAVAILABLE,
    describe_exception,
    normalize_com_error,
)
from .message_filter import (
    SEMessageFilter,
    register_message_filter,
    revoke_message_filter,
)
from .process import (
    get_process_identity,
    is_process_alive,
    kill_orphan_processes,
    try_get_process_id,
)
from .types import (
    AttachmentMode,
    OwnershipMode,
    ProcessIdentity,
    SolidEdgeApplicationHandle,
    SolidEdgeDocumentHandle,
    SolidEdgePartDocumentHandle,
)

DEFAULT_ATTACH_TIMEOUT: float = 30.0

DEFAULT_DOC_CREATE_TIMEOUT: float = 10.0
DEFAULT_DOC_CLOSE_TIMEOUT: float = 10.0
DEFAULT_DEFERRED_CREATE_TEARDOWN_TIMEOUT: float = 5.0
DEFAULT_QUIT_TIMEOUT: float = 10.0
DEFAULT_EXIT_POLL_TIMEOUT: float = 10.0

T = TypeVar("T")


def _load_pywin32_modules() -> tuple[Any, Any]:
    """Safely import win32com and pythoncom for Windows execution."""
    if sys.platform != "win32":
        return None, None
    try:
        import pythoncom
        import win32com.client

        return pythoncom, win32com.client
    except ImportError:
        return None, None


def _validate_pure_task_result(
    result: Any,
    raw_doc: Any,
    worker: Any,
    _visited: set[int] | None = None,
) -> None:
    """Validate that document task results do not leak raw COM objects or STA worker references."""
    if _visited is None:
        _visited = set()
    obj_id = id(result)
    if obj_id in _visited:
        return
    _visited.add(obj_id)

    if result is raw_doc or result is worker:
        raise CADExecutionError("Document task returned raw COM document or worker reference")
    if hasattr(result, "_oleobj_") or hasattr(result, "_dispobj_"):
        raise CADExecutionError("Document task returned raw COM object reference")

    if dataclasses.is_dataclass(result) and not isinstance(result, type):
        for f in dataclasses.fields(result):
            _validate_pure_task_result(getattr(result, f.name), raw_doc, worker, _visited)
    elif isinstance(result, (list, tuple, set)):
        for elem in result:
            _validate_pure_task_result(elem, raw_doc, worker, _visited)
    elif isinstance(result, dict):
        for k, v in result.items():
            _validate_pure_task_result(k, raw_doc, worker, _visited)
            _validate_pure_task_result(v, raw_doc, worker, _visited)


@dataclasses.dataclass
class _PendingDocumentCreation:
    """Thread-safe state tracking an in-flight or abandoned Part document creation.

    Contains strictly thread-safe metadata and synchronization primitives.
    Zero raw COM references (raw_doc, raw_app) are ever stored in this structure.
    """

    abandoned: threading.Event = dataclasses.field(default_factory=threading.Event)
    completed: threading.Event = dataclasses.field(default_factory=threading.Event)
    doc_handle: SolidEdgePartDocumentHandle | None = None
    cleanup_outcome: Literal["pending", "succeeded", "failed"] = "pending"
    lock: threading.Lock = dataclasses.field(default_factory=threading.Lock)


class STAThreadWorker(threading.Thread):
    """Dedicated background thread running a Single-Threaded Apartment (STA) message loop.

    Owns COM initialization, message filtering, and all native COM object storage.
    """

    def __init__(self) -> None:
        super().__init__(name="SolidEdgeSTAWorker", daemon=True)
        self._queue: queue.Queue[tuple[Callable[..., Any], concurrent.futures.Future[Any]] | None] = queue.Queue()
        self._stop_event = threading.Event()
        self._pythoncom: Any = None
        self._win32com: Any = None
        self._message_filter: SEMessageFilter | None = None
        self._raw_app: Any | None = None
        self._document_registry: dict[str, Any] = {}
        self.thread_id: int | None = None

    def run(self) -> None:
        """Worker main loop running active Windows message pump and task execution."""
        self.thread_id = threading.get_ident()
        self._pythoncom, self._win32com = _load_pywin32_modules()

        # Ensure STA thread is attached to interactive desktop so it can see user's GUI session
        if sys.platform == "win32":
            try:
                import ctypes

                user32 = ctypes.windll.user32
                h_default = user32.OpenDesktopW("Default", 0, False, 0x01FF)
                if h_default:
                    user32.SetThreadDesktop(h_default)
            except Exception:
                pass

        if self._pythoncom is not None:
            try:
                self._pythoncom.CoInitialize()
                self._message_filter = register_message_filter(self._pythoncom)
            except Exception as exc:
                describe_exception(exc, "Failed to initialize COM or register message filter on STA worker")

        while not self._stop_event.is_set():
            if self._pythoncom is not None:
                with contextlib.suppress(Exception):
                    self._pythoncom.PumpWaitingMessages()

            try:
                task = self._queue.get(timeout=0.010)
            except queue.Empty:
                continue

            if task is None:
                # Shutdown signal received
                break

            func, future = task
            if future.cancelled():
                continue

            try:
                result = func()
                if not future.cancelled():
                    future.set_result(result)
            except Exception as exc:
                if not future.cancelled():
                    try:
                        normalized = normalize_com_error(exc)
                        # Detach raw exception chains, traceback, and worker frames across the STA boundary (SEC-07)
                        normalized.__cause__ = None
                        normalized.__context__ = None
                        normalized.__suppress_context__ = True
                        normalized.__traceback__ = None
                        future.set_exception(normalized)
                    except Exception:
                        pass

        # Teardown apartment - Clear all COM references on the STA thread before CoUninitialize
        self._document_registry.clear()
        self._raw_app = None

        gc.collect()

        if self._pythoncom is not None:
            try:
                if self._message_filter is not None:
                    revoke_message_filter(self._pythoncom, self._message_filter)
                self._pythoncom.CoUninitialize()
            except Exception as exc:
                describe_exception(exc, "Error during STA worker COM uninitialization")

    def _invoke_com(self, func: Callable[[], T]) -> T:
        """Wrap an individual COM invocation with call-boundary attempt reset."""
        if self._message_filter is not None:
            self._message_filter.begin_call()
        return func()

    def submit(self, func: Callable[..., T]) -> concurrent.futures.Future[T]:
        """Asynchronously submit a callable to execute on the STA worker thread."""
        if not self.is_alive():
            raise CADRuntimeError("STA worker thread is not running")

        future: concurrent.futures.Future[T] = concurrent.futures.Future()
        self._queue.put((func, future))
        return future

    def call(self, func: Callable[..., T], timeout: float | None = None) -> T:
        """Synchronously execute a callable on the STA worker thread with optional timeout."""
        future = self.submit(func)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise TimeoutError(f"Operation on STA worker timed out after {timeout}s") from exc

    def shutdown(self, timeout: float = 3.0) -> None:
        """Signal the STA worker thread to exit and wait for termination."""
        self._stop_event.set()
        self._queue.put(None)
        if self.is_alive():
            self.join(timeout=timeout)


class SolidEdgeRuntime(CADRuntimeABC):
    """Facade controller managing Solid Edge lifecycle, process safety, and STA dispatching."""

    def __init__(self) -> None:
        self._worker: STAThreadWorker | None = None
        self._application: SolidEdgeApplicationHandle | None = None
        self._open_document_handles: dict[str, SolidEdgeDocumentHandle | SolidEdgePartDocumentHandle] = {}
        self._closed_pending_idle_handles: set[str] = set()
        self._owned_process_identity: ProcessIdentity | None = None
        self._pending_creation: _PendingDocumentCreation | None = None
        self._is_poisoned: bool = False

    def _ensure_worker(self) -> STAThreadWorker:
        """Ensure the background STA worker thread is started and responsive."""
        if self._worker is None or not self._worker.is_alive():
            self._worker = STAThreadWorker()
            self._worker.start()
            # Bounded wait for thread startup
            start_time = time.time()
            while self._worker.thread_id is None and (time.time() - start_time) < 2.0:
                time.sleep(0.01)
        return self._worker

    def connect_application(self) -> SolidEdgeApplicationHandle:
        """Connect to an active Solid Edge instance or spawn a new visible instance."""
        worker = self._ensure_worker()

        def _connect_task() -> SolidEdgeApplicationHandle:
            pythoncom, win32_client = _load_pywin32_modules()
            raw_app: Any = None
            ownership = OwnershipMode.UNKNOWN
            attachment_mode = AttachmentMode.UNSPECIFIED
            process_id: int | None = None
            identity: ProcessIdentity | None = None

            # Step 1: Try attaching to existing running instance
            if win32_client is not None:
                try:
                    if pythoncom is not None and hasattr(pythoncom, "GetActiveObject"):
                        disp = worker._invoke_com(lambda: pythoncom.GetActiveObject("SolidEdge.Application"))
                        if hasattr(disp, "QueryInterface") and hasattr(pythoncom, "IID_IDispatch"):
                            disp = disp.QueryInterface(pythoncom.IID_IDispatch)
                        if hasattr(win32_client, "dynamic"):
                            raw_app = worker._invoke_com(lambda: win32_client.dynamic.Dispatch(disp))
                        else:
                            raw_app = worker._invoke_com(lambda: win32_client.Dispatch(disp))
                    else:
                        raw_app = worker._invoke_com(lambda: win32_client.GetActiveObject("SolidEdge.Application"))

                    # Immediate health probe on existing instance
                    try:
                        _ = worker._invoke_com(lambda: getattr(raw_app, "Visible", None))
                    except Exception as health_exc:
                        raise CADRuntimeUnavailableError(
                            describe_exception(health_exc, "Existing Solid Edge instance is unresponsive")
                        ) from health_exc

                    ownership = OwnershipMode.BORROWED
                    attachment_mode = AttachmentMode.ATTACHED_EXISTING
                except Exception as exc:
                    if isinstance(exc, CADRuntimeUnavailableError):
                        raise

                    hresult: Any = getattr(exc, "hresult", None)
                    if hresult is None and hasattr(exc, "args") and len(exc.args) > 0 and isinstance(exc.args[0], int):
                        hresult = exc.args[0]
                    hresult_u32 = (hresult & 0xFFFFFFFF) if isinstance(hresult, int) else None

                    # Step 2: Spawn new instance ONLY if MK_E_UNAVAILABLE
                    if hresult_u32 == MK_E_UNAVAILABLE:
                        spawn_start_time = time.time()
                        try:
                            if hasattr(win32_client, "gencache"):
                                raw_app = worker._invoke_com(
                                    lambda: win32_client.gencache.EnsureDispatch("SolidEdge.Application")
                                )
                            elif hasattr(win32_client, "dynamic"):
                                raw_app = worker._invoke_com(
                                    lambda: win32_client.dynamic.Dispatch("SolidEdge.Application")
                                )
                            else:
                                raw_app = worker._invoke_com(lambda: win32_client.Dispatch("SolidEdge.Application"))
                        except Exception:
                            try:
                                if hasattr(win32_client, "dynamic"):
                                    raw_app = worker._invoke_com(
                                        lambda: win32_client.dynamic.Dispatch("SolidEdge.Application")
                                    )
                                else:
                                    raw_app = worker._invoke_com(lambda: win32_client.Dispatch("SolidEdge.Application"))
                            except Exception:
                                raw_app = worker._invoke_com(lambda: win32_client.Dispatch("SolidEdge.Application"))
                        spawn_end_time = time.time()

                        process_id = try_get_process_id(raw_app)
                        if process_id:
                            identity = get_process_identity(process_id)

                        # Active time-windowed ownership proof
                        if identity is not None and identity.pid > 0 and identity.creation_time_ft > 0:
                            # Convert Windows FILETIME (100ns intervals since Jan 1, 1601 UTC) to UNIX epoch seconds
                            creation_epoch_sec = (identity.creation_time_ft - 116444736000000000) / 10000000.0
                            if (spawn_start_time - 3.0) <= creation_epoch_sec <= (spawn_end_time + 3.0):
                                ownership = OwnershipMode.OWNED
                                attachment_mode = AttachmentMode.SPAWNED_NEW
                                self._owned_process_identity = identity

                                # Owned-only property mutations executed strictly after proof
                                try:
                                    worker._invoke_com(lambda: setattr(raw_app, "Visible", True))
                                    worker._invoke_com(lambda: setattr(raw_app, "DisplayAlerts", False))
                                except Exception as prop_exc:
                                    describe_exception(prop_exc, "Failed to configure owned Solid Edge properties")
                            else:
                                ownership = OwnershipMode.UNKNOWN
                                attachment_mode = AttachmentMode.SPAWNED_NEW
                        else:
                            ownership = OwnershipMode.UNKNOWN
                            attachment_mode = AttachmentMode.SPAWNED_NEW
                    else:
                        raise normalize_com_error(exc) from exc

            if raw_app is None:
                raise CADRuntimeUnavailableError("Unable to acquire or spawn Solid Edge application session")

            # On borrowed/unknown: extract PID if possible; never mutate DisplayAlerts
            if ownership != OwnershipMode.OWNED:
                process_id = try_get_process_id(raw_app)
                if process_id:
                    identity = get_process_identity(process_id)

            version_build: str | None = None
            connection_warnings: list[dict[str, str]] = []
            try:
                version_raw = worker._invoke_com(lambda: getattr(raw_app, "Version", None))
                if version_raw is not None and str(version_raw).strip():
                    version_build = str(version_raw).strip()
                else:
                    connection_warnings.append(
                        {
                            "code": "VERSION_METADATA_UNAVAILABLE",
                            "message": "Solid Edge application did not report a valid Version property.",
                        }
                    )
            except Exception as ver_exc:
                connection_warnings.append(
                    {
                        "code": "VERSION_METADATA_UNAVAILABLE",
                        "message": describe_exception(ver_exc, "Failed to read Solid Edge version property"),
                    }
                )

            worker._raw_app = raw_app
            app_handle = SolidEdgeApplicationHandle(
                handle_id=str(uuid.uuid4()),
                ownership=ownership,
                attachment_mode=attachment_mode,
                process_identity=identity,
                version_build=version_build,
                warnings=tuple(connection_warnings),
            )
            return app_handle

        self._application = worker.call(_connect_task, timeout=DEFAULT_ATTACH_TIMEOUT)
        return self._application

    def get_diagnostics(self) -> RuntimeDiagnostics:
        """Retrieve local runtime diagnostic metadata and health status."""
        if self._application is None or self._worker is None:
            return RuntimeDiagnostics(
                ownership="unknown",
                attachment_mode="unspecified",
                visibility="unknown",
                is_healthy=False,
            )

        ownership_str = self._application.ownership.value
        attachment_str = self._application.attachment_mode.value
        pid = self._application.process_identity.pid if self._application.process_identity else None
        version_str = self._application.version_build

        visibility_str = "unknown"
        is_healthy = False
        worker = self._worker

        if not self._is_poisoned and worker is not None and worker.is_alive():
            try:

                def _probe() -> tuple[str, bool]:
                    assert worker is not None
                    if worker._raw_app is None:
                        return "unknown", False
                    vis_bool = bool(worker._invoke_com(lambda: getattr(worker._raw_app, "Visible", False)))
                    vis_str = "visible" if vis_bool else "hidden"
                    return vis_str, True

                visibility_str, is_healthy = worker.call(_probe, timeout=2.0)
            except Exception:
                is_healthy = False

        return RuntimeDiagnostics(
            ownership=ownership_str,
            attachment_mode=attachment_str,
            visibility=visibility_str,
            process_id=pid,
            version_build=version_str,
            is_healthy=is_healthy,
            warnings=list(self._application.warnings),
        )

    def create_part_document(self, application: Any) -> SolidEdgePartDocumentHandle:
        """Create a fresh request-owned part document."""
        worker = self._ensure_worker()

        if self._is_poisoned:
            raise CADRuntimeError("Cannot create part document: Solid Edge runtime is poisoned from a prior timeout")

        if self._pending_creation is not None and not self._pending_creation.completed.is_set():
            raise CADRuntimeError("A Part document creation is already in progress")

        pending = _PendingDocumentCreation()
        self._pending_creation = pending

        def _create_task() -> SolidEdgePartDocumentHandle | None:
            try:
                if worker._raw_app is None:
                    raise CADRuntimeError("No active Solid Edge application instance")

                app = worker._raw_app
                raw_doc = worker._invoke_com(lambda: app.Documents.Add("SolidEdge.PartDocument"))
                handle_id = str(uuid.uuid4())
                doc_handle = SolidEdgePartDocumentHandle(handle_id=handle_id)

                # Register in worker registry on STA thread immediately
                worker._document_registry[handle_id] = raw_doc

                with pending.lock:
                    was_abandoned_before_mode = pending.abandoned.is_set()

                if was_abandoned_before_mode:
                    try:
                        self._worker_close_document(worker, handle_id)
                        self._closed_pending_idle_handles.discard(handle_id)
                        with pending.lock:
                            pending.cleanup_outcome = "succeeded"
                    except Exception as close_exc:
                        describe_exception(
                            close_exc, "Failed to close abandoned Part document created after caller timeout"
                        )
                        self._open_document_handles[handle_id] = doc_handle
                        with pending.lock:
                            pending.cleanup_outcome = "failed"
                    return None

                try:
                    worker._invoke_com(lambda: setattr(raw_doc, "ModelingMode", 2))
                    current_mode = worker._invoke_com(lambda: getattr(raw_doc, "ModelingMode", None))
                    if current_mode != 2:
                        raise CADDocumentError(
                            f"Failed to establish Ordered modeling mode on new Part document (expected 2, got {current_mode})"
                        )
                except Exception as mode_exc:
                    try:
                        self._worker_close_document(worker, handle_id)
                        self._closed_pending_idle_handles.discard(handle_id)
                        with pending.lock:
                            pending.cleanup_outcome = "succeeded"
                    except Exception as close_exc:
                        describe_exception(close_exc, "Failed to close Part document after mode configuration failure")
                        self._open_document_handles[handle_id] = doc_handle
                        with pending.lock:
                            pending.cleanup_outcome = "failed"

                    if isinstance(mode_exc, CADDocumentError):
                        raise
                    raise CADDocumentError(
                        describe_exception(mode_exc, "Failed to configure Ordered modeling mode on new Part document")
                    ) from mode_exc

                with pending.lock:
                    if pending.abandoned.is_set():
                        should_clean = True
                    else:
                        pending.doc_handle = doc_handle
                        should_clean = False

                if should_clean:
                    try:
                        self._worker_close_document(worker, handle_id)
                        self._closed_pending_idle_handles.discard(handle_id)
                        with pending.lock:
                            pending.cleanup_outcome = "succeeded"
                    except Exception as close_exc:
                        describe_exception(close_exc, "Deferred close failed for abandoned Part document")
                        self._open_document_handles[handle_id] = doc_handle
                        with pending.lock:
                            pending.cleanup_outcome = "failed"
                    return None
                else:
                    with pending.lock:
                        pending.cleanup_outcome = "succeeded"
                    return doc_handle
            finally:
                pending.completed.set()

        fut = worker.submit(_create_task)
        try:
            res = fut.result(timeout=DEFAULT_DOC_CREATE_TIMEOUT)
            if res is not None:
                self._open_document_handles[res.handle_id] = res
                self._pending_creation = None
                return res
            raise CADDocumentError(
                "Part document creation was abandoned",
                error_code="DOCUMENT_ABANDONED",
            )
        except concurrent.futures.TimeoutError as exc:
            with pending.lock:
                if pending.doc_handle is not None:
                    res = pending.doc_handle
                    self._open_document_handles[res.handle_id] = res
                    self._pending_creation = None
                    return res
                pending.abandoned.set()
            raise CADDocumentError(
                f"Part document creation timed out fail-closed after {DEFAULT_DOC_CREATE_TIMEOUT}s",
                error_code="DOCUMENT_CREATE_TIMEOUT",
            ) from exc

    def open_document(self, application: Any, path: Path) -> SolidEdgeDocumentHandle:
        """Open an existing CAD document by path with explicit ownership."""
        resolved_path = path.resolve()
        if not resolved_path.is_file():
            raise CADDocumentError(f"CAD document file not found: {resolved_path}")

        worker = self._ensure_worker()

        def _open_task() -> SolidEdgeDocumentHandle:
            if worker._raw_app is None:
                raise CADRuntimeError("No active Solid Edge application instance")

            app = worker._raw_app
            raw_doc = worker._invoke_com(lambda: app.Documents.Open(str(resolved_path)))
            handle_id = str(uuid.uuid4())
            worker._document_registry[handle_id] = raw_doc
            return SolidEdgeDocumentHandle(handle_id=handle_id, path=resolved_path)

        handle = worker.call(_open_task, timeout=15.0)
        self._open_document_handles[handle.handle_id] = handle
        return handle

    def _worker_close_document(self, worker: Any, handle_id: str) -> None:
        """Worker-side document close, reference release, pending-idle tracking, and DoIdle sequence.

        Must execute entirely on the STA worker thread.
        Invariants:
        1. If handle_id is not in _closed_pending_idle_handles:
           - Fetch raw_doc from worker._document_registry; if None, raise CADDocumentError DOCUMENT_LOST.
           - Call raw_doc.Close(False).
           - Remove from worker._document_registry and release local reference.
           - Record handle_id in _closed_pending_idle_handles.
        2. Invoke Application.DoIdle() if active raw_app is available; if raw_app is None, raise CADRuntimeError.
        3. Do NOT discard pending-idle tracking or public handles here; finalization occurs only after
           worker.call returns successfully to the caller, or upon confirmed create-mode cleanup.
        """
        if handle_id not in self._closed_pending_idle_handles:
            raw_doc = worker._document_registry.get(handle_id)
            if raw_doc is None:
                raise CADDocumentError(
                    "Document object not found in STA worker registry",
                    error_code="DOCUMENT_LOST",
                )
            worker._invoke_com(lambda: raw_doc.Close(False))
            worker._document_registry.pop(handle_id, None)
            del raw_doc
            self._closed_pending_idle_handles.add(handle_id)

        raw_app = getattr(worker, "_raw_app", None)
        if raw_app is None:
            raise CADRuntimeError("No active Solid Edge application instance")
        worker._invoke_com(lambda: raw_app.DoIdle())

    def _execute_document_close(self, handle_id: str, timeout: float = DEFAULT_DOC_CLOSE_TIMEOUT) -> None:
        """Execute centralized worker-side document close sequence with timeout poisoning."""
        worker = self._ensure_worker()

        try:
            worker.call(lambda: self._worker_close_document(worker, handle_id), timeout=timeout)
            self._closed_pending_idle_handles.discard(handle_id)
            self._open_document_handles.pop(handle_id, None)
        except TimeoutError:
            self._is_poisoned = True
            raise

    def close_document(self, doc_handle: Any) -> None:
        """Release or close an open CAD document handle unconditionally without saving."""
        if self._is_poisoned:
            raise CADRuntimeError("Cannot close document: Solid Edge runtime is poisoned from a prior timeout")

        if not isinstance(doc_handle, (SolidEdgePartDocumentHandle, SolidEdgeDocumentHandle)):
            handle_type = type(doc_handle).__name__
            raise CADDocumentError(
                f"Invalid document handle type: expected SolidEdgePartDocumentHandle or SolidEdgeDocumentHandle, got {handle_type}"
            )

        if not doc_handle.handle_id:
            raise CADDocumentError("Invalid document handle: missing handle_id")

        tracked_handle = self._open_document_handles.get(doc_handle.handle_id)
        if tracked_handle is None:
            raise CADDocumentError("Untracked or closed document handle", error_code="DOCUMENT_LOST")

        if type(doc_handle) is not type(tracked_handle):
            doc_type = type(doc_handle).__name__
            tracked_type = type(tracked_handle).__name__
            raise CADDocumentError(f"Tracked document handle type mismatch: expected {tracked_type}, got {doc_type}")

        if doc_handle != tracked_handle:
            raise CADDocumentError(
                "Document handle metadata does not match tracked handle",
                error_code="DOCUMENT_LOST",
            )

        try:
            self._execute_document_close(doc_handle.handle_id, timeout=DEFAULT_DOC_CLOSE_TIMEOUT)
        except Exception as exc:
            describe_exception(exc, "Error closing document")
            raise

    def run_document_task(
        self,
        doc_handle: SolidEdgePartDocumentHandle | SolidEdgeDocumentHandle,
        task: Callable[[Any, STAThreadWorker], T],
        timeout: float = 120.0,
    ) -> T:
        """Driver-internal seam to execute a task against an explicit tracked CAD document on the STA thread.

        Args:
            doc_handle: The explicit request-owned SolidEdgePartDocumentHandle or SolidEdgeDocumentHandle.
            task: Callable receiving `(raw_doc, worker)` and executing entirely on the STA worker thread.
            timeout: Bounded timeout in seconds for the entire document task (default 120s).

        Returns:
            The pure Python return value from `task`.

        Raises:
            CADRuntimeError: If runtime is poisoned, uninitialized, or worker is not running.
            CADDocumentError: If doc_handle is invalid, untracked, mismatched, forged, or closed.
            TimeoutError: If task execution exceeds timeout, poisoning the runtime.
        """
        if self._is_poisoned:
            raise CADRuntimeError("Cannot execute document task: Solid Edge runtime is poisoned from a prior timeout")

        worker = self._worker
        if worker is None or not worker.is_alive():
            raise CADRuntimeError("Cannot execute document task: STA worker thread is not running")

        if not isinstance(doc_handle, (SolidEdgePartDocumentHandle, SolidEdgeDocumentHandle)):
            handle_type = type(doc_handle).__name__
            raise CADDocumentError(
                f"Invalid document handle type: expected SolidEdgePartDocumentHandle or SolidEdgeDocumentHandle, got {handle_type}"
            )

        if not doc_handle.handle_id:
            raise CADDocumentError("Invalid document handle: missing handle_id")

        tracked_handle = self._open_document_handles.get(doc_handle.handle_id)
        if tracked_handle is None:
            raise CADDocumentError("Untracked or closed document handle", error_code="DOCUMENT_LOST")

        if type(doc_handle) is not type(tracked_handle):
            doc_type = type(doc_handle).__name__
            tracked_type = type(tracked_handle).__name__
            raise CADDocumentError(f"Tracked document handle type mismatch: expected {tracked_type}, got {doc_type}")

        if doc_handle != tracked_handle:
            raise CADDocumentError(
                "Document handle metadata does not match tracked handle",
                error_code="DOCUMENT_LOST",
            )

        handle_id = doc_handle.handle_id

        def _wrapped_task() -> T:
            raw_doc = worker._document_registry.get(handle_id)
            if raw_doc is None:
                raise CADDocumentError("Document object not found in STA worker registry", error_code="DOCUMENT_LOST")
            result = task(raw_doc, worker)
            _validate_pure_task_result(result, raw_doc, worker)
            return result

        try:
            return worker.call(_wrapped_task, timeout=timeout)
        except TimeoutError:
            self._is_poisoned = True
            describe_exception(
                TimeoutError(f"Document task timed out after {timeout}s"),
                "Document task timed out fail-closed",
            )
            raise TimeoutError(f"Document task timed out after {timeout}s") from None

    def is_healthy(self) -> bool:
        """Check if the CAD runtime is alive and responsive."""
        worker = self._worker
        if self._application is None or self._is_poisoned or worker is None:
            return False

        if not worker.is_alive():
            return False

        try:

            def _probe() -> bool:
                assert worker is not None
                if worker._raw_app is None:
                    return False
                app = worker._raw_app
                _ = worker._invoke_com(lambda: getattr(app, "Visible", None))
                return True

            return bool(worker.call(_probe, timeout=2.0))
        except Exception:
            return False

    def teardown(self, force_kill_on_failure: bool = False) -> bool:
        """Cleanly close request documents, restore state, and safely terminate owned processes.

        Returns:
            True if all cleanup operations completed cleanly; False if any tracked document,
            application shutdown, worker termination, or owned-process cleanup was incomplete.
        """
        worker = self._worker

        # Check if already completely torn down (idempotent no-op)
        if (
            worker is None
            and self._application is None
            and not self._open_document_handles
            and not self._closed_pending_idle_handles
            and self._owned_process_identity is None
            and self._pending_creation is None
        ):
            return True

        # Stage 0: Bounded resolution for pending/abandoned document creation
        pending_clean = True
        pending = self._pending_creation
        if pending is not None:
            if pending.abandoned.is_set() or not pending.completed.is_set():
                completed = pending.completed.wait(timeout=DEFAULT_DEFERRED_CREATE_TEARDOWN_TIMEOUT)
                if not completed:
                    describe_exception(
                        CADDocumentError("Abandoned document creation did not complete within teardown bounds"),
                        "Teardown pending creation timeout",
                    )
                    self._is_poisoned = True
                    pending_clean = False
                else:
                    if pending.cleanup_outcome != "succeeded":
                        self._is_poisoned = True
                        pending_clean = False
            self._pending_creation = None

        # Stage 1: Close remaining tracked open documents
        docs_clean = True
        if self._open_document_handles:
            if self._is_poisoned or worker is None or not worker.is_alive():
                docs_clean = False
            else:
                for handle_id in list(self._open_document_handles.keys()):
                    if handle_id in self._closed_pending_idle_handles:
                        docs_clean = False
                        continue
                    try:
                        self._execute_document_close(handle_id, timeout=DEFAULT_DOC_CLOSE_TIMEOUT)
                    except Exception as close_exc:
                        describe_exception(close_exc, "Teardown document close timeout/error")
                        self._is_poisoned = True
                        docs_clean = False
                        break

        # Stage 2: Quiesce still-running connection worker if application handle was never published
        if self._application is None and worker is not None and worker.is_alive():
            try:
                worker.shutdown(timeout=2.0)
            except Exception as quiesce_exc:
                describe_exception(quiesce_exc, "Teardown connection worker quiesce error")

            # If worker remains alive and ownership remains unknown, connection is still in flight:
            # preserve unresolved state and return False cleanly.
            if worker.is_alive() and self._owned_process_identity is None:
                self._is_poisoned = True
                return False

        if self._application is None and self._owned_process_identity is not None:
            self._is_poisoned = True

        # Stage 2: Borrowed/Unknown Protection - Zero termination
        # Re-check identity after quiescing any in-flight worker: _owned_process_identity is authoritative owned state.
        is_owned = self._owned_process_identity is not None or (
            self._application is not None and self._application.ownership == OwnershipMode.OWNED
        )

        if not is_owned:
            worker_clean = True
            if worker is not None and worker.is_alive():
                try:
                    worker.shutdown(timeout=2.0)
                    if worker.is_alive():
                        worker_clean = False
                except Exception as worker_exc:
                    describe_exception(worker_exc, "Teardown worker shutdown error")
                    worker_clean = False

            # If identity appeared during shutdown, switch to owned cleanup rather than wiping
            if self._owned_process_identity is not None:
                is_owned = True
            else:
                clean_teardown = docs_clean and worker_clean and pending_clean
                if clean_teardown:
                    self._worker = None
                    self._application = None
                    self._open_document_handles.clear()
                    self._closed_pending_idle_handles.clear()
                    self._owned_process_identity = None
                    self._pending_creation = None
                    self._is_poisoned = False
                else:
                    if worker is not None and not worker.is_alive():
                        self._worker = None
                return clean_teardown

        # Stage 3: Graceful Quit for Owned Instances
        graceful_quit_attempted = False
        if not self._is_poisoned and self._application is not None and worker is not None and worker.is_alive():
            graceful_quit_attempted = True
            try:

                def _quit() -> None:
                    assert worker is not None
                    if worker._raw_app is not None:
                        app = worker._raw_app
                        worker._invoke_com(lambda: app.Quit())
                    worker._document_registry.clear()
                    worker._raw_app = None
                    import gc

                    gc.collect()

                worker.call(_quit, timeout=DEFAULT_QUIT_TIMEOUT)

            except Exception as quit_exc:
                describe_exception(quit_exc, "Teardown Quit() timeout or error on owned Solid Edge")
                self._is_poisoned = True

        # Stage 4: Process Exit Polling
        if graceful_quit_attempted and not self._is_poisoned and self._owned_process_identity is not None:
            poll_start = time.time()
            while (time.time() - poll_start) < DEFAULT_EXIT_POLL_TIMEOUT:
                if not is_process_alive(self._owned_process_identity.pid):
                    break
                time.sleep(0.25)

        # Stage 5: Identity-Checked Force Cleanup
        if self._owned_process_identity is not None:
            if not is_process_alive(self._owned_process_identity.pid):
                self._owned_process_identity = None
            elif force_kill_on_failure:
                try:
                    terminated_pids = kill_orphan_processes([self._owned_process_identity])
                    if self._owned_process_identity.pid in terminated_pids or not is_process_alive(
                        self._owned_process_identity.pid
                    ):
                        self._owned_process_identity = None
                except Exception as kill_exc:
                    describe_exception(kill_exc, "Teardown force kill error")

        # Stage 6: Worker Shutdown & State Reset
        worker_clean = True
        if worker is not None and worker.is_alive():
            try:
                worker.shutdown(timeout=2.0)
                if worker.is_alive():
                    worker_clean = False
            except Exception as worker_exc:
                describe_exception(worker_exc, "Teardown worker shutdown error")
                worker_clean = False

        process_clean = self._owned_process_identity is None or not is_process_alive(self._owned_process_identity.pid)
        if process_clean:
            self._owned_process_identity = None

        clean_teardown = process_clean and worker_clean and pending_clean
        if clean_teardown:
            self._worker = None
            self._application = None
            self._open_document_handles.clear()
            self._closed_pending_idle_handles.clear()
            self._owned_process_identity = None
            self._pending_creation = None
            self._is_poisoned = False
        else:
            if worker is not None and not worker.is_alive():
                self._worker = None

        return clean_teardown


__all__ = [
    "STAThreadWorker",
    "SolidEdgeRuntime",
]
