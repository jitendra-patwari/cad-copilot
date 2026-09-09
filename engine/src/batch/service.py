"""Sequential batch execution coordinator for CAD Copilot.

Processes validated batch execution specifications through a single CAD runtime connection,
opening at most one document at a time, dispatching requested formats through bound operation
handlers, and managing cleanup, cancellation, and terminal outcomes.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from batch.allocation import (
    BatchAllocationCollisionError,
    BatchSafetyBoundary,
    BatchSafetyRejectionError,
    PreparedBatchWork,
    allocate_batch_work,
    build_collision_rejection,
    validate_prepared_work_consistency,
)
from batch.bindings import OperationBindings
from batch.execution import (
    BatchExecutionOutcome,
    BatchExecutionSpec,
    BatchProgressUpdate,
)
from batch.file_execution import execute_single_file
from batch.models import (
    BatchConfigurationError,
    BatchDiagnostic,
    BatchFileResult,
    BatchRequest,
    BatchSummary,
    BatchTerminalStatus,
)
from batch.registry import OperationRegistry, build_initial_registry
from interfaces.runtime_abc import CADRuntimeABC
from manifests.models import (
    FREE_TEXT_CREDENTIAL_PATTERN,
    LOCAL_PATH_PATTERN,
    SENSITIVE_KEY_PATTERN,
)

APPROVED_WARNING_MESSAGES: dict[str, str] = {
    "VERSION_METADATA_UNAVAILABLE": "CAD runtime did not report version metadata.",
}


class _CancellationProbeError(Exception):
    """Internal exception raised when an injected cancellation check raises unexpectedly."""


class BatchService:
    """Sequential batch execution coordinator.

    Executes validated batch requests through a single CAD runtime instance,
    ensuring at most one document is open at any time, isolating format errors,
    cooperatively checking cancellation, and safely managing process lifecycle.
    """

    def __init__(
        self,
        runtime_factory: Callable[[], CADRuntimeABC],
        safety_boundary: BatchSafetyBoundary,
        bindings: OperationBindings,
        cancellation_check: Callable[[], bool] | None = None,
        observer: Callable[[BatchProgressUpdate], None] | None = None,
        registry: OperationRegistry | None = None,
    ) -> None:
        if not callable(runtime_factory):
            raise TypeError("runtime_factory must be callable")
        for method_name in ("prepare", "verify_before_open", "verify_after_close"):
            if not hasattr(safety_boundary, method_name) or not callable(getattr(safety_boundary, method_name)):
                raise TypeError(
                    f"safety_boundary must implement callable '{method_name}' of BatchSafetyBoundary protocol"
                )
        if not isinstance(bindings, OperationBindings):
            raise TypeError(f"bindings must be OperationBindings, got {type(bindings).__name__}")

        if cancellation_check is not None and not callable(cancellation_check):
            raise TypeError("cancellation_check must be callable or None")
        if observer is not None and not callable(observer):
            raise TypeError("observer must be callable or None")

        if registry is None:
            registry = build_initial_registry()
        elif not isinstance(registry, OperationRegistry):
            raise TypeError(f"registry must be OperationRegistry or None, got {type(registry).__name__}")

        # Enforce exact 1-to-1 parity between metadata and bindings at configuration time
        bindings.validate_parity(registry)

        self._runtime_factory = runtime_factory
        self._safety_boundary = safety_boundary
        self._bindings = bindings
        self._cancellation_check = cancellation_check
        self._observer = observer
        self._registry = registry

    @property
    def registry(self) -> OperationRegistry:
        """Return the immutable operation metadata registry."""
        return self._registry

    @property
    def bindings(self) -> OperationBindings:
        """Return the immutable operation bindings."""
        return self._bindings

    def _notify(self, update: BatchProgressUpdate) -> None:
        """Safely notify the progress observer, swallowing any observer exceptions.

        Progress updates are strictly best-effort and must never disrupt execution,
        corrupt accounting, or prevent cleanup.
        """
        if self._observer is None:
            return
        with contextlib.suppress(Exception):
            self._observer(update)

    def _is_cancelled(self) -> bool:
        """Cooperatively check cancellation status.

        Raises _CancellationProbeError if the cancellation callback raises unexpectedly.
        """
        if self._cancellation_check is None:
            return False
        try:
            return bool(self._cancellation_check())
        except Exception as exc:
            raise _CancellationProbeError("Cancellation check failed unexpectedly") from exc

    def _is_runtime_healthy(self, runtime: CADRuntimeABC | None) -> bool:
        """Safely probe runtime responsiveness without raising."""
        if runtime is None:
            return False
        try:
            return bool(runtime.is_healthy())
        except Exception:
            return False

    def _prepare_execution(
        self, spec: BatchExecutionSpec
    ) -> tuple[PreparedBatchWork | None, BatchExecutionOutcome | None]:
        """Validate specification, allocate relative paths, and prepare safety boundary."""
        descriptor = self._registry.get(spec.operation_id)
        for req_fmt in spec.formats:
            if req_fmt not in descriptor.output_formats:
                raise BatchConfigurationError(
                    f"Format '{req_fmt}' is not supported by operation '{spec.operation_id}'. "
                    f"Supported formats: {descriptor.output_formats}"
                )
        for inp in spec.inputs:
            ext = Path(inp).suffix.lower()
            if ext not in descriptor.input_extensions:
                raise BatchConfigurationError(
                    f"Input extension '{ext}' in '{inp}' is not supported by operation '{spec.operation_id}'. "
                    f"Supported extensions: {descriptor.input_extensions}"
                )

        # Pure relative allocation
        try:
            allocated = allocate_batch_work(spec)
        except BatchAllocationCollisionError as err:
            return None, build_collision_rejection(spec, err)

        # Safety boundary preparation
        try:
            prepared = self._safety_boundary.prepare(spec, allocated)
        except BatchSafetyRejectionError as err:
            return None, BatchExecutionOutcome(
                status="rejected",
                request_id=spec.request_id,
                contract_version=spec.contract_version,
                errors=(err.diagnostic,),
            )
        except Exception:
            return None, BatchExecutionOutcome(
                status="failed",
                request_id=spec.request_id,
                contract_version=spec.contract_version,
                errors=(
                    BatchDiagnostic(
                        code="INTERNAL_ERROR",
                        message="Unexpected error during safety boundary preparation",
                    ),
                ),
            )

        # Enforce exact sequence parity
        try:
            validate_prepared_work_consistency(allocated, prepared)
        except Exception:
            return None, BatchExecutionOutcome(
                status="failed",
                request_id=spec.request_id,
                contract_version=spec.contract_version,
                errors=(
                    BatchDiagnostic(
                        code="INTERNAL_ERROR",
                        message="Safety boundary produced inconsistent prepared work",
                    ),
                ),
            )

        return prepared, None

    def _capture_runtime_diagnostics(
        self,
        runtime: CADRuntimeABC,
        batch_warnings: list[BatchDiagnostic],
    ) -> str | None:
        """Extract and sanitize runtime metadata and map approved diagnostic warnings."""
        cad_version_build: str | None = None
        try:
            diag = runtime.get_diagnostics()
            if diag is not None:
                if diag.warnings:
                    for w in diag.warnings:
                        code = w.get("code") if isinstance(w, dict) else str(w)
                        if code in APPROVED_WARNING_MESSAGES:
                            warn_obj = BatchDiagnostic(
                                code=code,
                                message=APPROVED_WARNING_MESSAGES[code],
                            )
                            if warn_obj not in batch_warnings:
                                batch_warnings.append(warn_obj)

                if isinstance(diag.version_build, str) and diag.version_build.strip():
                    cleaned_build = " ".join(diag.version_build.split())
                    if (
                        cleaned_build
                        and len(cleaned_build) <= 128
                        and not LOCAL_PATH_PATTERN.search(cleaned_build)
                        and not FREE_TEXT_CREDENTIAL_PATTERN.search(cleaned_build)
                        and not SENSITIVE_KEY_PATTERN.search(cleaned_build)
                    ):
                        cad_version_build = cleaned_build
        except Exception:
            pass

        if cad_version_build is None and not any(w.code == "VERSION_METADATA_UNAVAILABLE" for w in batch_warnings):
            batch_warnings.append(
                BatchDiagnostic(
                    code="VERSION_METADATA_UNAVAILABLE",
                    message=APPROVED_WARNING_MESSAGES["VERSION_METADATA_UNAVAILABLE"],
                )
            )
        return cad_version_build

    def _assemble_outcome(
        self,
        spec: BatchExecutionSpec,
        terminal_status: BatchTerminalStatus,
        execution_started: bool,
        file_results: list[BatchFileResult],
        unprocessed_files: list[str],
        cancelled_files: list[str],
        top_level_errors: list[BatchDiagnostic],
        batch_warnings: list[BatchDiagnostic],
        cad_version_build: str | None,
        output_root: Path,
    ) -> BatchExecutionOutcome:
        """Assemble the final immutable BatchExecutionOutcome."""
        if not execution_started:
            summary = None
            res_tuple: tuple[BatchFileResult, ...] = ()
            unproc_tuple: tuple[str, ...] = ()
            canc_tuple: tuple[str, ...] = ()
        else:
            summary = BatchSummary(
                total=len(spec.inputs),
                accepted=sum(1 for r in file_results if r.status == "accepted"),
                partial=sum(1 for r in file_results if r.status == "partial"),
                failed=sum(1 for r in file_results if r.status == "failed"),
                unprocessed=len(unprocessed_files),
                cancelled=len(cancelled_files),
            )
            res_tuple = tuple(file_results)
            unproc_tuple = tuple(unprocessed_files)
            canc_tuple = tuple(cancelled_files)

        return BatchExecutionOutcome(
            status=terminal_status,
            request_id=spec.request_id,
            contract_version=spec.contract_version,
            summary=summary,
            file_results=res_tuple,
            unprocessed_files=unproc_tuple,
            cancelled_files=canc_tuple,
            errors=tuple(top_level_errors),
            warnings=tuple(batch_warnings),
            cad_runtime_version_build=cad_version_build,
            output_root=output_root,
        )

    def execute(self, request_or_spec: BatchRequest | BatchExecutionSpec) -> BatchExecutionOutcome:
        """Execute a batch request or execution specification sequentially.

        Follows phases A through G:
        - Phase A: Pre-flight validation, pure work allocation, and safety preparation
        - Phase B: Single runtime connection and request-scoped handler binding
        - Phase C: Per-file iteration and preflight checks
        - Phase D: Document open and per-format handler dispatch
        - Phase E: Mandatory document close and file result aggregation
        - Phase F: Continue policy, error isolation, and cancellation precedence
        - Phase G: Runtime teardown and terminal outcome assembly
        """
        if isinstance(request_or_spec, BatchRequest):
            spec = BatchExecutionSpec.from_request(request_or_spec)
        elif isinstance(request_or_spec, BatchExecutionSpec):
            spec = request_or_spec
        else:
            raise TypeError(
                f"request_or_spec must be BatchRequest or BatchExecutionSpec, got {type(request_or_spec).__name__}"
            )

        prepared, early_outcome = self._prepare_execution(spec)
        if early_outcome is not None:
            return early_outcome
        assert prepared is not None

        # Pre-runtime checks passed: emit batch_started
        self._notify(
            BatchProgressUpdate(
                request_id=spec.request_id,
                phase="batch_started",
                total_files=len(spec.inputs),
                completed_files=0,
            )
        )

        # Check cancellation before acquiring runtime
        try:
            if self._is_cancelled():
                self._notify(
                    BatchProgressUpdate(
                        request_id=spec.request_id,
                        phase="batch_finished",
                        total_files=len(spec.inputs),
                        completed_files=0,
                    )
                )
                return BatchExecutionOutcome(
                    status="cancelled",
                    request_id=spec.request_id,
                    contract_version=spec.contract_version,
                    summary=BatchSummary(
                        total=len(spec.inputs),
                        accepted=0,
                        partial=0,
                        failed=0,
                        unprocessed=0,
                        cancelled=len(spec.inputs),
                    ),
                    file_results=(),
                    unprocessed_files=(),
                    cancelled_files=spec.inputs,
                    errors=(),
                    warnings=(),
                    output_root=prepared.output_root,
                )
        except _CancellationProbeError:
            self._notify(
                BatchProgressUpdate(
                    request_id=spec.request_id,
                    phase="batch_finished",
                    total_files=len(spec.inputs),
                    completed_files=0,
                )
            )
            return BatchExecutionOutcome(
                status="failed",
                request_id=spec.request_id,
                contract_version=spec.contract_version,
                errors=(
                    BatchDiagnostic(
                        code="INTERNAL_ERROR",
                        message="Internal error occurred during cancellation check",
                    ),
                ),
                output_root=prepared.output_root,
            )

        runtime: CADRuntimeABC | None = None
        file_results: list[BatchFileResult] = []
        unprocessed_files: list[str] = []
        cancelled_files: list[str] = []
        top_level_errors: list[BatchDiagnostic] = []
        batch_warnings: list[BatchDiagnostic] = []
        terminal_status: BatchTerminalStatus = "completed"
        cad_version_build: str | None = None
        fatal_batch_error: BatchDiagnostic | None = None
        batch_cancelled = False
        execution_started = False

        try:
            try:
                runtime = self._runtime_factory()
            except Exception:
                terminal_status = "failed"
                top_level_errors.append(
                    BatchDiagnostic(
                        code="SOLID_EDGE_UNAVAILABLE",
                        message="Failed to instantiate CAD runtime",
                    )
                )

            if runtime is not None:
                app_handle: Any = None
                try:
                    app_handle = runtime.connect_application()
                    if app_handle is None:
                        raise RuntimeError("connect_application returned None")
                except Exception:
                    terminal_status = "failed"
                    top_level_errors.append(
                        BatchDiagnostic(
                            code="SOLID_EDGE_UNAVAILABLE",
                            message="Solid Edge application is unavailable or failed to connect",
                        )
                    )

                if app_handle is not None:
                    cad_version_build = self._capture_runtime_diagnostics(runtime, batch_warnings)

                    if not self._is_runtime_healthy(runtime):
                        terminal_status = "failed"
                        top_level_errors.append(
                            BatchDiagnostic(
                                code="SOLID_EDGE_UNAVAILABLE",
                                message="Solid Edge runtime is unhealthy after connection",
                            )
                        )
                    else:
                        binding = self._bindings.get_binding(spec.operation_id)
                        handler: Any = None
                        try:
                            handler = binding.create_handler(runtime)
                        except Exception:
                            terminal_status = "failed"
                            top_level_errors.append(
                                BatchDiagnostic(
                                    code="INTERNAL_ERROR",
                                    message=f"Failed to create handler for operation '{spec.operation_id}'",
                                )
                            )

                        if handler is not None:
                            execution_started = True

                            for file_idx, prep_file in enumerate(prepared.files):
                                try:
                                    if self._is_cancelled():
                                        batch_cancelled = True
                                        cancelled_files.extend(f.input for f in prepared.files[file_idx:])
                                        terminal_status = "cancelled"
                                        break
                                except _CancellationProbeError:
                                    fatal_batch_error = BatchDiagnostic(
                                        code="INTERNAL_ERROR",
                                        message="Internal error occurred during cancellation check",
                                    )
                                    unprocessed_files.extend(f.input for f in prepared.files[file_idx:])
                                    terminal_status = "failed"
                                    top_level_errors.append(fatal_batch_error)
                                    break

                                file_result, fatal_batch_error = execute_single_file(
                                    spec=spec,
                                    prep_file=prep_file,
                                    runtime=runtime,
                                    app_handle=app_handle,
                                    handler=handler,
                                    completed_files_count=len(file_results),
                                    safety_boundary=self._safety_boundary,
                                    is_cancelled=self._is_cancelled,
                                    is_runtime_healthy=self._is_runtime_healthy,
                                    notify=self._notify,
                                )
                                file_results.append(file_result)

                                if any(e.code == "BATCH_CANCELLED" for e in file_result.errors):
                                    batch_cancelled = True

                                if fatal_batch_error is not None:
                                    unprocessed_files.extend(f.input for f in prepared.files[file_idx + 1 :])
                                    cancelled_files.clear()
                                    terminal_status = "failed"
                                    if fatal_batch_error not in top_level_errors:
                                        top_level_errors.append(fatal_batch_error)
                                    break

                                if batch_cancelled:
                                    cancelled_files.extend(f.input for f in prepared.files[file_idx + 1 :])
                                    terminal_status = "cancelled"
                                    break

                                if file_result.status != "accepted" and not spec.continue_on_error:
                                    unprocessed_files.extend(f.input for f in prepared.files[file_idx + 1 :])
                                    terminal_status = "completed"
                                    break
        finally:
            if runtime is not None:
                clean_teardown = False
                try:
                    clean_teardown = runtime.teardown(force_kill_on_failure=True)
                except Exception:
                    clean_teardown = False

                if not clean_teardown:
                    terminal_status = "failed"
                    if fatal_batch_error is not None and fatal_batch_error.code == "SOLID_EDGE_UNHEALTHY":
                        teardown_err = fatal_batch_error
                    else:
                        teardown_err = BatchDiagnostic(
                            code="INTERNAL_ERROR",
                            message="Runtime teardown was incomplete",
                        )
                    if teardown_err not in top_level_errors:
                        top_level_errors.append(teardown_err)
                    if cancelled_files:
                        unprocessed_files.extend(cancelled_files)
                        cancelled_files.clear()

            self._notify(
                BatchProgressUpdate(
                    request_id=spec.request_id,
                    phase="batch_finished",
                    total_files=len(spec.inputs),
                    completed_files=len(file_results),
                )
            )

        return self._assemble_outcome(
            spec=spec,
            terminal_status=terminal_status,
            execution_started=execution_started,
            file_results=file_results,
            unprocessed_files=unprocessed_files,
            cancelled_files=cancelled_files,
            top_level_errors=top_level_errors,
            batch_warnings=batch_warnings,
            cad_version_build=cad_version_build,
            output_root=prepared.output_root,
        )


__all__ = ["BatchService"]
