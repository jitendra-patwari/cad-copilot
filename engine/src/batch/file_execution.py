"""Private per-file and per-format batch execution lifecycle.

Coordinates document open, cancellation probes, format dispatch, handler validation,
mandatory document close without saving, source integrity checks, and file result assembly.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from batch.allocation import (
    BatchSafetyBoundary,
    PreparedBatchFile,
)
from batch.execution import (
    BatchExecutionSpec,
    BatchItemContext,
    BatchItemOutcome,
    BatchProgressUpdate,
)
from batch.models import (
    APPROVED_WARNING_CODES,
    BatchArtifactRecord,
    BatchDiagnostic,
    BatchFileResult,
    BatchOutputFormat,
    FileResultStatus,
)
from interfaces.runtime_abc import CADRuntimeABC


def execute_single_file(
    spec: BatchExecutionSpec,
    prep_file: PreparedBatchFile,
    runtime: CADRuntimeABC,
    app_handle: Any,
    handler: Any,
    completed_files_count: int,
    safety_boundary: BatchSafetyBoundary,
    is_cancelled: Callable[[], bool],
    is_runtime_healthy: Callable[[CADRuntimeABC | None], bool],
    notify: Callable[[BatchProgressUpdate], None],
) -> tuple[BatchFileResult, BatchDiagnostic | None]:
    """Execute all requested formats for a single document under mandatory close."""
    notify(
        BatchProgressUpdate(
            request_id=spec.request_id,
            phase="file_started",
            total_files=len(spec.inputs),
            completed_files=completed_files_count,
            current_file=prep_file.input,
        )
    )

    # Source-specific preflight error check
    if prep_file.preflight_error is not None:
        if (
            not isinstance(prep_file.preflight_error, BatchDiagnostic)
            or prep_file.preflight_error.code in APPROVED_WARNING_CODES
        ):
            fatal_err = BatchDiagnostic(
                code="INTERNAL_ERROR",
                message=f"Prepared file contained invalid preflight error for '{prep_file.input}'",
            )
            file_result = BatchFileResult(
                input=prep_file.input,
                status="failed",
                artifacts=(),
                errors=(fatal_err,),
                warnings=(),
            )
            notify(
                BatchProgressUpdate(
                    request_id=spec.request_id,
                    phase="file_finished",
                    total_files=len(spec.inputs),
                    completed_files=completed_files_count + 1,
                    current_file=prep_file.input,
                    file_status="failed",
                )
            )
            return file_result, fatal_err

        file_result = BatchFileResult(
            input=prep_file.input,
            status="failed",
            artifacts=(),
            errors=(prep_file.preflight_error,),
            warnings=(),
        )
        notify(
            BatchProgressUpdate(
                request_id=spec.request_id,
                phase="file_finished",
                total_files=len(spec.inputs),
                completed_files=completed_files_count + 1,
                current_file=prep_file.input,
                file_status="failed",
            )
        )
        return file_result, None

    # Check runtime health before document open
    if not is_runtime_healthy(runtime):
        fatal_err = BatchDiagnostic(
            code="SOLID_EDGE_UNHEALTHY",
            message="Solid Edge runtime became unresponsive before document open",
        )
        file_result = BatchFileResult(
            input=prep_file.input,
            status="failed",
            artifacts=(),
            errors=(fatal_err,),
            warnings=(),
        )
        notify(
            BatchProgressUpdate(
                request_id=spec.request_id,
                phase="file_finished",
                total_files=len(spec.inputs),
                completed_files=completed_files_count + 1,
                current_file=prep_file.input,
                file_status="failed",
            )
        )
        return file_result, fatal_err

    # Pre-open safety check with runtime validation of return value
    pre_open_diag: BatchDiagnostic | None = None
    try:
        raw_pre_open = safety_boundary.verify_before_open(prep_file)
        if raw_pre_open is not None and (
            not isinstance(raw_pre_open, BatchDiagnostic) or raw_pre_open.code in APPROVED_WARNING_CODES
        ):
            fatal_err = BatchDiagnostic(
                code="INTERNAL_ERROR",
                message=f"Safety boundary returned invalid pre-open verification result for '{prep_file.input}'",
            )
            file_result = BatchFileResult(
                input=prep_file.input,
                status="failed",
                artifacts=(),
                errors=(fatal_err,),
                warnings=(),
            )
            notify(
                BatchProgressUpdate(
                    request_id=spec.request_id,
                    phase="file_finished",
                    total_files=len(spec.inputs),
                    completed_files=completed_files_count + 1,
                    current_file=prep_file.input,
                    file_status="failed",
                )
            )
            return file_result, fatal_err
        pre_open_diag = raw_pre_open
    except Exception:
        fatal_err = BatchDiagnostic(
            code="INTERNAL_ERROR",
            message=f"Safety boundary failed unexpectedly during pre-open verification for '{prep_file.input}'",
        )
        file_result = BatchFileResult(
            input=prep_file.input,
            status="failed",
            artifacts=(),
            errors=(fatal_err,),
            warnings=(),
        )
        notify(
            BatchProgressUpdate(
                request_id=spec.request_id,
                phase="file_finished",
                total_files=len(spec.inputs),
                completed_files=completed_files_count + 1,
                current_file=prep_file.input,
                file_status="failed",
            )
        )
        return file_result, fatal_err

    if pre_open_diag is not None:
        file_result = BatchFileResult(
            input=prep_file.input,
            status="failed",
            artifacts=(),
            errors=(pre_open_diag,),
            warnings=(),
        )
        notify(
            BatchProgressUpdate(
                request_id=spec.request_id,
                phase="file_finished",
                total_files=len(spec.inputs),
                completed_files=completed_files_count + 1,
                current_file=prep_file.input,
                file_status="failed",
            )
        )
        return file_result, None

    doc_handle: Any = None
    file_artifacts: list[BatchArtifactRecord] = []
    file_errors: list[BatchDiagnostic] = []
    file_warnings: list[BatchDiagnostic] = []
    fatal_batch_error: BatchDiagnostic | None = None
    doc_open_failed = False

    try:
        try:
            doc_handle = runtime.open_document(app_handle, prep_file.source_path)
            if doc_handle is None:
                raise RuntimeError("open_document returned None")
        except Exception:
            doc_open_failed = True
            open_diag = BatchDiagnostic(
                code="DOCUMENT_OPEN_FAILED",
                message=f"Failed to open CAD document '{prep_file.input}'",
            )
            file_errors.append(open_diag)
            if not is_runtime_healthy(runtime):
                fatal_batch_error = BatchDiagnostic(
                    code="SOLID_EDGE_UNHEALTHY",
                    message="Solid Edge runtime became unresponsive during document open",
                )
                file_errors.append(fatal_batch_error)

        if not doc_open_failed and fatal_batch_error is None:
            for prep_fmt in prep_file.formats:
                format_literal = cast(BatchOutputFormat, prep_fmt.format)

                # Check cancellation before format dispatch
                try:
                    if is_cancelled():
                        file_errors.append(
                            BatchDiagnostic(
                                code="BATCH_CANCELLED",
                                message="Batch execution cancelled",
                                format=format_literal,
                            )
                        )
                        break
                except Exception:
                    fatal_batch_error = BatchDiagnostic(
                        code="INTERNAL_ERROR",
                        message="Internal error occurred during cancellation check",
                    )
                    file_errors.append(fatal_batch_error)
                    break

                # Per-format preflight error check
                if prep_fmt.preflight_error is not None:
                    notify(
                        BatchProgressUpdate(
                            request_id=spec.request_id,
                            phase="format_started",
                            total_files=len(spec.inputs),
                            completed_files=completed_files_count,
                            current_file=prep_file.input,
                            current_format=prep_fmt.format,
                        )
                    )
                    if (
                        not isinstance(prep_fmt.preflight_error, BatchDiagnostic)
                        or prep_fmt.preflight_error.code in APPROVED_WARNING_CODES
                    ):
                        fatal_batch_error = BatchDiagnostic(
                            code="INTERNAL_ERROR",
                            message=f"Prepared format contained invalid preflight error for '{prep_file.input}' and '{prep_fmt.format}'",
                            format=format_literal,
                        )
                        file_errors.append(fatal_batch_error)
                        notify(
                            BatchProgressUpdate(
                                request_id=spec.request_id,
                                phase="format_finished",
                                total_files=len(spec.inputs),
                                completed_files=completed_files_count,
                                current_file=prep_file.input,
                                current_format=prep_fmt.format,
                            )
                        )
                        break

                    file_errors.append(prep_fmt.preflight_error)
                    notify(
                        BatchProgressUpdate(
                            request_id=spec.request_id,
                            phase="format_finished",
                            total_files=len(spec.inputs),
                            completed_files=completed_files_count,
                            current_file=prep_file.input,
                            current_format=prep_fmt.format,
                        )
                    )
                    continue

                context = BatchItemContext(
                    request_id=spec.request_id,
                    operation_id=spec.operation_id,
                    input=prep_file.input,
                    source_path=prep_file.source_path,
                    format=prep_fmt.format,
                    target_relative_path=prep_fmt.target_relative_path,
                    work_path=prep_fmt.work_path,
                    target_path=prep_fmt.target_path,
                )

                notify(
                    BatchProgressUpdate(
                        request_id=spec.request_id,
                        phase="format_started",
                        total_files=len(spec.inputs),
                        completed_files=completed_files_count,
                        current_file=prep_file.input,
                        current_format=prep_fmt.format,
                    )
                )

                # Dispatch handler
                outcome: Any = None
                try:
                    outcome = handler(doc_handle, context)
                except Exception:
                    file_errors.append(
                        BatchDiagnostic(
                            code="INTERNAL_ERROR",
                            message=f"Handler execution failed unexpectedly for format '{prep_fmt.format}'",
                            format=format_literal,
                        )
                    )
                    notify(
                        BatchProgressUpdate(
                            request_id=spec.request_id,
                            phase="format_finished",
                            total_files=len(spec.inputs),
                            completed_files=completed_files_count,
                            current_file=prep_file.input,
                            current_format=prep_fmt.format,
                        )
                    )
                    if not is_runtime_healthy(runtime):
                        fatal_batch_error = BatchDiagnostic(
                            code="SOLID_EDGE_UNHEALTHY",
                            message="Solid Edge runtime became unresponsive during format export",
                        )
                        file_errors.append(fatal_batch_error)
                        break
                    continue

                # Validate returned-state: invalid type or context mismatch is a fatal programming fault
                if not isinstance(outcome, BatchItemOutcome):
                    fatal_batch_error = BatchDiagnostic(
                        code="INTERNAL_ERROR",
                        message=f"Handler for format '{prep_fmt.format}' returned invalid type {type(outcome).__name__}",
                        format=format_literal,
                    )
                    file_errors.append(fatal_batch_error)
                    notify(
                        BatchProgressUpdate(
                            request_id=spec.request_id,
                            phase="format_finished",
                            total_files=len(spec.inputs),
                            completed_files=completed_files_count,
                            current_file=prep_file.input,
                            current_format=prep_fmt.format,
                        )
                    )
                    break

                try:
                    outcome.validate_against_context(context)
                except Exception:
                    # Sanitize message: do not interpolate exc to avoid exposing private filesystem paths
                    fatal_batch_error = BatchDiagnostic(
                        code="INTERNAL_ERROR",
                        message=f"Handler outcome for format '{prep_fmt.format}' violated dispatch context",
                        format=format_literal,
                    )
                    file_errors.append(fatal_batch_error)
                    notify(
                        BatchProgressUpdate(
                            request_id=spec.request_id,
                            phase="format_finished",
                            total_files=len(spec.inputs),
                            completed_files=completed_files_count,
                            current_file=prep_file.input,
                            current_format=prep_fmt.format,
                        )
                    )
                    break

                if outcome.is_success:
                    assert outcome.artifact is not None
                    file_artifacts.append(outcome.artifact)
                else:
                    file_errors.extend(outcome.errors)
                    # Immediate runtime health verification on typed failure
                    if not is_runtime_healthy(runtime):
                        fatal_batch_error = BatchDiagnostic(
                            code="SOLID_EDGE_UNHEALTHY",
                            message="Solid Edge runtime became unresponsive after format export failure",
                        )
                        file_errors.append(fatal_batch_error)
                        notify(
                            BatchProgressUpdate(
                                request_id=spec.request_id,
                                phase="format_finished",
                                total_files=len(spec.inputs),
                                completed_files=completed_files_count,
                                current_file=prep_file.input,
                                current_format=prep_fmt.format,
                            )
                        )
                        break

                file_warnings.extend(outcome.warnings)
                notify(
                    BatchProgressUpdate(
                        request_id=spec.request_id,
                        phase="format_finished",
                        total_files=len(spec.inputs),
                        completed_files=completed_files_count,
                        current_file=prep_file.input,
                        current_format=prep_fmt.format,
                    )
                )

    finally:
        if doc_handle is not None:
            close_failed = False
            try:
                runtime.close_document(doc_handle)
            except Exception:
                close_failed = True

            if close_failed:
                close_diag = BatchDiagnostic(
                    code="DOCUMENT_CLOSE_FAILED",
                    message=f"Failed to close CAD document '{prep_file.input}'",
                )
                file_errors.append(close_diag)
                fatal_batch_error = close_diag
            else:
                integrity_diag: BatchDiagnostic | None = None
                try:
                    raw_integrity = safety_boundary.verify_after_close(prep_file)
                    if raw_integrity is not None and (
                        not isinstance(raw_integrity, BatchDiagnostic)
                        or raw_integrity.code != "SOURCE_INTEGRITY_FAILED"
                    ):
                        fatal_batch_error = BatchDiagnostic(
                            code="INTERNAL_ERROR",
                            message=f"Safety boundary returned invalid post-close verification result for '{prep_file.input}'",
                        )
                        file_errors.append(fatal_batch_error)
                    else:
                        integrity_diag = raw_integrity
                except Exception:
                    integrity_diag = BatchDiagnostic(
                        code="SOURCE_INTEGRITY_FAILED",
                        message=f"Failed to verify source integrity after close for '{prep_file.input}'",
                    )

                if integrity_diag is not None:
                    file_errors.append(integrity_diag)
                    fatal_batch_error = integrity_diag
                elif fatal_batch_error is None and not is_runtime_healthy(runtime):
                    fatal_batch_error = BatchDiagnostic(
                        code="SOLID_EDGE_UNHEALTHY",
                        message="Solid Edge runtime became unresponsive after document close",
                    )
                    file_errors.append(fatal_batch_error)
            doc_handle = None

    file_status: FileResultStatus
    if fatal_batch_error is not None or doc_open_failed:
        file_status = "failed"
        file_artifacts.clear()
    elif len(file_artifacts) == len(prep_file.formats):
        file_status = "accepted"
    elif len(file_artifacts) > 0:
        file_status = "partial"
    else:
        file_status = "failed"

    # Invariant guard: failed status must have at least one error
    if file_status == "failed" and not file_errors:
        if fatal_batch_error is not None:
            file_errors.append(fatal_batch_error)
        else:
            file_errors.append(
                BatchDiagnostic(
                    code="INTERNAL_ERROR",
                    message=f"Execution failed for '{prep_file.input}'",
                )
            )

    file_result = BatchFileResult(
        input=prep_file.input,
        status=file_status,
        artifacts=tuple(file_artifacts),
        errors=tuple(file_errors),
        warnings=tuple(file_warnings),
    )

    notify(
        BatchProgressUpdate(
            request_id=spec.request_id,
            phase="file_finished",
            total_files=len(spec.inputs),
            completed_files=completed_files_count + 1,
            current_file=prep_file.input,
            file_status=file_status,
        )
    )
    return file_result, fatal_batch_error
