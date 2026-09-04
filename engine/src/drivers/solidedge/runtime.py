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
from typing import Any, TypeVar

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

DEFAULT_DOC_CLOSE_TIMEOUT: float = 10.0
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
        self._owned_process_identity: ProcessIdentity | None = None
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

        def _create_task() -> SolidEdgePartDocumentHandle:
            if worker._raw_app is None:
                raise CADRuntimeError("No active Solid Edge application instance")

            app = worker._raw_app
            raw_doc = worker._invoke_com(lambda: app.Documents.Add("SolidEdge.PartDocument"))
            handle_id = str(uuid.uuid4())
            doc_handle = SolidEdgePartDocumentHandle(handle_id=handle_id)

            # Track immediately so any subsequent teardown can find and close this document
            worker._document_registry[handle_id] = raw_doc
            self._open_document_handles[handle_id] = doc_handle

            try:
                worker._invoke_com(lambda: setattr(raw_doc, "ModelingMode", 2))
                current_mode = worker._invoke_com(lambda: getattr(raw_doc, "ModelingMode", None))
                if current_mode != 2:
                    raise CADDocumentError(
                        f"Failed to establish Ordered modeling mode on new Part document (expected 2, got {current_mode})"
                    )
            except Exception as mode_exc:
                try:
                    worker._invoke_com(lambda: raw_doc.Close(False))
                    # Only remove tracking if immediate closure succeeded
                    worker._document_registry.pop(handle_id, None)
                    self._open_document_handles.pop(handle_id, None)
                except Exception:
                    # If closure failed, retain tracking in registry & handles so teardown will retry
                    pass

                if isinstance(mode_exc, CADDocumentError):
                    raise
                raise CADDocumentError(
                    describe_exception(mode_exc, "Failed to configure Ordered modeling mode on new Part document")
                ) from mode_exc

            return doc_handle

        return worker.call(_create_task, timeout=10.0)

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

    def close_document(self, doc_handle: Any) -> None:
        """Release or close an open CAD document handle unconditionally without saving."""
        if not hasattr(doc_handle, "handle_id"):
            return

        if self._is_poisoned:
            raise CADRuntimeError("Cannot close document: Solid Edge runtime is poisoned from a prior timeout")

        handle_id: str = doc_handle.handle_id
        worker = self._ensure_worker()

        def _close_task() -> None:
            raw_doc = worker._document_registry.get(handle_id)
            if raw_doc is not None:
                worker._invoke_com(lambda: raw_doc.Close(False))
                worker._document_registry.pop(handle_id, None)

        try:
            worker.call(_close_task, timeout=DEFAULT_DOC_CLOSE_TIMEOUT)
            # Only remove tracking upon successful close
            self._open_document_handles.pop(handle_id, None)
        except Exception as exc:
            if isinstance(exc, TimeoutError):
                self._is_poisoned = True
            describe_exception(exc, "Error closing document")
            raise

    def run_document_task(
        self,
        doc_handle: SolidEdgePartDocumentHandle,
        task: Callable[[Any, STAThreadWorker], T],
        timeout: float = 120.0,
    ) -> T:
        """Driver-internal seam to execute a modeling task against an explicit tracked Part document on the STA thread.

        Args:
            doc_handle: The explicit request-owned SolidEdgePartDocumentHandle.
            task: Callable receiving `(raw_doc, worker)` and executing entirely on the STA worker thread.
            timeout: Bounded timeout in seconds for the entire document task (default 120s).

        Returns:
            The pure Python return value from `task`.

        Raises:
            CADRuntimeError: If runtime is poisoned, uninitialized, or worker is not running.
            CADDocumentError: If doc_handle is invalid, untracked, wrong type, or closed.
            TimeoutError: If task execution exceeds timeout, poisoning the runtime.
        """
        if self._is_poisoned:
            raise CADRuntimeError("Cannot execute document task: Solid Edge runtime is poisoned from a prior timeout")

        worker = self._worker
        if worker is None or not worker.is_alive():
            raise CADRuntimeError("Cannot execute document task: STA worker thread is not running")

        if not isinstance(doc_handle, SolidEdgePartDocumentHandle):
            handle_type = type(doc_handle).__name__
            raise CADDocumentError(
                f"Invalid document handle type: expected SolidEdgePartDocumentHandle, got {handle_type}"
            )

        if not doc_handle.handle_id:
            raise CADDocumentError("Invalid document handle: missing handle_id")

        tracked_handle = self._open_document_handles.get(doc_handle.handle_id)
        if tracked_handle is None:
            raise CADDocumentError("Untracked or closed document handle", error_code="DOCUMENT_LOST")

        if not isinstance(tracked_handle, SolidEdgePartDocumentHandle):
            tracked_type = type(tracked_handle).__name__
            raise CADDocumentError(
                f"Tracked document handle is not a Part document: expected SolidEdgePartDocumentHandle, got {tracked_type}"
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

    def teardown(self, force_kill_on_failure: bool = False) -> None:
        """Cleanly close request documents, restore state, and safely terminate owned processes."""
        worker = self._worker

        # Stage 1: Close remaining tracked open documents
        if not self._is_poisoned and worker is not None and worker.is_alive():
            for handle_id in list(self._open_document_handles.keys()):
                try:

                    def _close_doc(hid: str = handle_id) -> None:
                        raw_doc = worker._document_registry.get(hid)
                        if raw_doc is not None:
                            worker._invoke_com(lambda: raw_doc.Close(False))
                            worker._document_registry.pop(hid, None)

                    worker.call(_close_doc, timeout=DEFAULT_DOC_CLOSE_TIMEOUT)
                except Exception as close_exc:
                    describe_exception(close_exc, "Teardown document close timeout/error")
                    self._is_poisoned = True
                    break

        # Stage 2: Borrowed/Unknown Protection - Zero termination
        is_owned = self._application is not None and self._application.ownership == OwnershipMode.OWNED

        if not is_owned:
            if worker is not None:
                worker.shutdown(timeout=2.0)
            self._worker = None
            self._application = None
            self._open_document_handles.clear()
            self._owned_process_identity = None
            self._is_poisoned = False
            return

        # Stage 3: Graceful Quit for Owned Instances
        if not self._is_poisoned and worker is not None and worker.is_alive():
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
        if not self._is_poisoned and self._owned_process_identity is not None:
            poll_start = time.time()
            while (time.time() - poll_start) < DEFAULT_EXIT_POLL_TIMEOUT:
                if not is_process_alive(self._owned_process_identity.pid):
                    break
                time.sleep(0.25)

        # Stage 5: Identity-Checked Force Cleanup
        if self._owned_process_identity is not None:
            still_alive = is_process_alive(self._owned_process_identity.pid)
            if (self._is_poisoned or still_alive) and force_kill_on_failure:
                kill_orphan_processes([self._owned_process_identity])

        # Stage 6: Worker Shutdown & State Reset
        if worker is not None:
            worker.shutdown(timeout=2.0)

        self._worker = None
        self._application = None
        self._open_document_handles.clear()
        self._owned_process_identity = None
        self._is_poisoned = False


__all__ = [
    "STAThreadWorker",
    "SolidEdgeRuntime",
]
