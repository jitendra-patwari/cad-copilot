"""In-memory test doubles and helper factories for BatchService tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from batch.allocation import (
    AllocatedBatchWork,
    BatchSafetyBoundary,
    BatchSafetyRejectionError,
    PreparedBatchFile,
    PreparedBatchFormat,
    PreparedBatchWork,
)
from batch.bindings import (
    BatchHandlerFactory,
    BatchOperationHandler,
    OperationBinding,
    OperationBindings,
)
from batch.execution import BatchExecutionSpec, BatchItemContext, BatchItemOutcome
from batch.models import BatchArtifactRecord, BatchDiagnostic, BatchOutputFormat
from batch.registry import OperationRegistry, build_initial_registry
from interfaces.models import RuntimeDiagnostics
from interfaces.runtime_abc import CADRuntimeABC


@dataclass(frozen=True)
class FakeDocumentHandle:
    """Opaque in-memory document handle representation."""

    path: Path
    handle_id: str
    raw_doc: Any = None
    worker: Any = None


class FakeCADRuntime(CADRuntimeABC):
    """Spy runtime double tracking connection, open/close pairing, and teardown."""

    def __init__(
        self,
        *,
        version_build: str | None = "226.00.00.106",
        fail_connect: bool = False,
        connect_returns_none: bool = False,
        fail_open_inputs: set[str] | None = None,
        fail_close_inputs: set[str] | None = None,
        fail_teardown: bool = False,
        healthy: bool = True,
        fail_diagnostics: bool = False,
        diagnostics_warnings: list[dict[str, str]] | None = None,
        on_close: Callable[[Any], None] | None = None,
        open_callback: Callable[[Any, Path], Any] | None = None,
    ) -> None:
        self.version_build = version_build
        self.fail_connect = fail_connect
        self.connect_returns_none = connect_returns_none
        self.fail_open_inputs = fail_open_inputs or set()
        self.fail_close_inputs = fail_close_inputs or set()
        self.fail_teardown = fail_teardown
        self.healthy = healthy
        self.fail_diagnostics = fail_diagnostics
        self.diagnostics_warnings = diagnostics_warnings
        self.on_close = on_close
        self.open_callback = open_callback

        self.connect_calls = 0
        self.open_calls: list[Path] = []
        self.close_calls: list[Any] = []
        self.teardown_calls: list[bool] = []
        self.open_handles: set[Any] = set()
        self.max_simultaneous_open: int = 0

    def connect_application(self) -> Any:
        self.connect_calls += 1
        if self.fail_connect:
            raise RuntimeError("Fake connect failure")
        if self.connect_returns_none:
            return None
        return "fake_app_session"

    def get_diagnostics(self) -> RuntimeDiagnostics:
        if self.fail_diagnostics:
            raise RuntimeError("Fake diagnostics lookup failure")
        return RuntimeDiagnostics(
            ownership="owned",
            attachment_mode="spawned_new",
            is_healthy=self.healthy,
            version_build=self.version_build,
            warnings=self.diagnostics_warnings or [],
        )

    def create_part_document(self, application: Any) -> Any:
        raise NotImplementedError("create_part_document is not used in batch execution")

    def open_document(self, application: Any, path: Path) -> Any:
        if self.open_callback is not None:
            return self.open_callback(application, path)
        for pattern in self.fail_open_inputs:
            if pattern in str(path) or pattern == path.name:
                raise RuntimeError(f"Fake open failure for {path.name}")
        handle = FakeDocumentHandle(path=path, handle_id=f"fake-doc-{len(self.open_calls) + 1}")
        self.open_handles.add(handle)
        self.open_calls.append(path)
        if len(self.open_handles) > self.max_simultaneous_open:
            self.max_simultaneous_open = len(self.open_handles)
        return handle

    def close_document(self, doc_handle: Any) -> None:
        self.close_calls.append(doc_handle)
        if self.on_close is not None:
            self.on_close(doc_handle)
        for pattern in self.fail_close_inputs:
            if pattern in str(doc_handle.path) or pattern == doc_handle.path.name:
                raise RuntimeError(f"Fake close failure for {doc_handle.path.name}")
        self.open_handles.discard(doc_handle)

    def teardown(self, force_kill_on_failure: bool = False) -> bool:
        self.teardown_calls.append(force_kill_on_failure)
        self.open_handles.clear()
        return not self.fail_teardown

    def is_healthy(self) -> bool:
        return self.healthy


class FakeSafetyBoundary(BatchSafetyBoundary):
    """Spy safety boundary constructing boundary-prepared paths and injecting errors."""

    def __init__(
        self,
        base_dir: Path,
        *,
        rejection_diagnostic: BatchDiagnostic | None = None,
        preflight_file_errors: dict[str, BatchDiagnostic] | None = None,
        preflight_format_errors: dict[tuple[str, str], BatchDiagnostic] | None = None,
        fail_before_open: dict[str, Any] | None = None,
        fail_after_close: dict[str, Any] | None = None,
        throw_before_open: set[str] | None = None,
        raise_on_prepare: bool = False,
    ) -> None:
        self.base_dir = base_dir
        self.rejection_diagnostic = rejection_diagnostic
        self.preflight_file_errors = preflight_file_errors or {}
        self.preflight_format_errors = preflight_format_errors or {}
        self.fail_before_open = fail_before_open or {}
        self.fail_after_close = fail_after_close or {}
        self.throw_before_open = throw_before_open or set()
        self.raise_on_prepare = raise_on_prepare
        self.verify_before_open_calls: list[str] = []
        self.verify_after_close_calls: list[str] = []

    def prepare(self, spec: BatchExecutionSpec, work: AllocatedBatchWork) -> PreparedBatchWork:
        if self.raise_on_prepare:
            raise RuntimeError("Fake unexpected boundary error on prepare")
        if self.rejection_diagnostic is not None:
            raise BatchSafetyRejectionError(self.rejection_diagnostic, request_id=spec.request_id)

        prep_files: list[PreparedBatchFile] = []
        out_root = self.base_dir / "output"

        for f in work.files:
            prep_fmts: list[PreparedBatchFormat] = []
            for fmt in f.formats:
                fmt_err = self.preflight_format_errors.get((f.input, fmt.format))
                prep_fmts.append(
                    PreparedBatchFormat(
                        format=fmt.format,
                        target_relative_path=fmt.target_relative_path,
                        work_path=self.base_dir / "work" / fmt.target_relative_path,
                        target_path=out_root / fmt.target_relative_path,
                        preflight_error=fmt_err,
                    )
                )
            file_err = self.preflight_file_errors.get(f.input)
            prep_files.append(
                PreparedBatchFile(
                    input=f.input,
                    source_path=self.base_dir / "input" / f.input,
                    formats=tuple(prep_fmts),
                    preflight_error=file_err,
                )
            )

        return PreparedBatchWork(files=tuple(prep_files), output_root=out_root)

    def verify_before_open(self, prepared_file: PreparedBatchFile) -> BatchDiagnostic | None:
        self.verify_before_open_calls.append(prepared_file.input)
        if prepared_file.input in self.throw_before_open:
            raise RuntimeError(f"Fake unexpected boundary failure before opening '{prepared_file.input}'")
        return cast(BatchDiagnostic | None, self.fail_before_open.get(prepared_file.input))

    def verify_after_close(self, prepared_file: PreparedBatchFile) -> BatchDiagnostic | None:
        self.verify_after_close_calls.append(prepared_file.input)
        return cast(BatchDiagnostic | None, self.fail_after_close.get(prepared_file.input))


def make_test_bindings(
    *,
    fail_formats: dict[tuple[str, str], BatchDiagnostic] | None = None,
    throw_formats: set[tuple[str, str]] | None = None,
    on_format: Callable[[BatchItemContext], None] | None = None,
    registry: OperationRegistry | None = None,
) -> tuple[OperationBindings, list[BatchItemContext]]:
    """Build OperationBindings wired with a spy handler for all registered operations."""
    recorded_calls: list[BatchItemContext] = []
    fail_map = fail_formats or {}
    throw_set = throw_formats or set()

    def make_handler(op_id: str) -> BatchOperationHandler:
        def handler(doc_handle: object, context: BatchItemContext) -> BatchItemOutcome:
            recorded_calls.append(context)
            if on_format is not None:
                on_format(context)
            key = (context.input, context.format)
            if key in throw_set:
                raise RuntimeError(f"Fake handler crash on {key}")
            if key in fail_map:
                diag = fail_map[key]
                return BatchItemOutcome.failure(format=context.format, errors=(diag,))
            fmt_literal = cast(BatchOutputFormat, context.format)
            return BatchItemOutcome.success(
                format=context.format,
                artifact=BatchArtifactRecord(format=fmt_literal, path=str(context.target_path)),
            )

        return handler

    def make_factory(op_id: str) -> BatchHandlerFactory:
        def factory(runtime: CADRuntimeABC) -> BatchOperationHandler:
            return make_handler(op_id)

        return factory

    target_registry = registry or build_initial_registry()
    bindings_list: list[OperationBinding] = []
    for op_id in target_registry.operation_ids:
        bindings_list.append(OperationBinding(operation_id=op_id, factory=make_factory(op_id)))

    return OperationBindings(bindings_list, registry=target_registry), recorded_calls


def make_execution_spec(
    inputs: tuple[str, ...] = ("part1.par", "part2.par"),
    formats: tuple[str, ...] = ("step", "stl"),
    operation_id: str = "export_3d",
    continue_on_error: bool = True,
    request_id: str = "req-test-001",
) -> BatchExecutionSpec:
    """Convenience helper to build a valid BatchExecutionSpec for tests."""
    return BatchExecutionSpec(
        contract_version="1.0",
        request_id=request_id,
        input_root="models",
        output_root="output",
        inputs=inputs,
        operation_id=operation_id,
        formats=formats,
        continue_on_error=continue_on_error,
    )


class FakeTrackedDocumentRuntime(FakeCADRuntime):
    """Structural runtime double providing run_document_task for composition and handler tests."""

    def __init__(
        self,
        *,
        version_build: str | None = "226.00.00.106",
        healthy: bool = True,
        task_callback: Callable[[Any, Any], Any] | None = None,
        fail_task: bool = False,
        open_callback: Callable[[Any, Path], Any] | None = None,
    ) -> None:
        super().__init__(
            version_build=version_build,
            healthy=healthy,
            open_callback=open_callback,
        )
        self.task_callback = task_callback
        self.fail_task = fail_task
        self.task_calls: list[tuple[Any, Any]] = []

    def run_document_task(
        self,
        doc_handle: Any,
        task: Callable[[Any, Any], Any],
        timeout: float = 120.0,
    ) -> Any:
        if self.fail_task:
            raise RuntimeError("Fake task execution failure")
        self.task_calls.append((doc_handle, task))
        if self.task_callback is not None:
            return self.task_callback(doc_handle, task)
        fake_raw_doc = getattr(doc_handle, "raw_doc", None)
        if fake_raw_doc is None:
            fake_raw_doc = object()
        fake_worker = getattr(doc_handle, "worker", None)
        if fake_worker is None:
            fake_worker = object()
        return task(fake_raw_doc, fake_worker)
