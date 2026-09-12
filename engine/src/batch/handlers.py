"""Production batch operation handlers for CAD Copilot format export and publication.

Enforces the five-call guarded workspace lifecycle:
1. Preparation check: assembly references for .asm or view refresh for .dft (cached per document handle).
2. Workspace activation: workspace.begin_format(context)
3. Driver operation: export_model_to_path or publish_drawing_to_path
4. Closed-file structural validation: validate_batch_output(format, work_path) -> OutputSnapshot
5. Atomic publication: workspace.finalize_format(context, snapshot) -> target_path
6. Guarded failure cleanup: workspace.cleanup_format(context) on any failure after activation.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final, TypeAlias, cast

from batch.execution import BatchItemContext, BatchItemOutcome
from batch.format_validation import BatchFormatValidationError, validate_batch_output
from batch.models import (
    BatchArtifactRecord,
    BatchDiagnostic,
    BatchOutputFormat,
)
from batch.output_publication import BatchWorkspaceError, _as_batch_output_format
from batch.output_workspace import BatchOutputWorkspace
from drivers.solidedge.assembly_references import AssemblyReferenceCheckResult
from interfaces.exceptions import (
    CADDocumentError,
    CADExportError,
    CADRuntimeBusyError,
    CADRuntimeUnavailableError,
)

SUPPORTED_3D_FORMATS: Final[frozenset[str]] = frozenset({"step", "stl"})
SUPPORTED_3D_EXTENSIONS: Final[frozenset[str]] = frozenset({".par", ".psm", ".asm"})

SUPPORTED_DRAWING_FORMATS: Final[frozenset[str]] = frozenset({"pdf", "dxf"})
SUPPORTED_DRAWING_EXTENSIONS: Final[frozenset[str]] = frozenset({".dft"})

_PrepCacheKey: TypeAlias = tuple[str, str]
_DiagnosticPayload: TypeAlias = tuple[str, str]


def _extract_handle_id(doc_handle: object) -> str:
    """Extract and validate the non-empty handle_id from an opaque document handle."""
    handle_id = getattr(doc_handle, "handle_id", None)
    if not isinstance(handle_id, str) or not handle_id or handle_id != handle_id.strip():
        raise CADDocumentError("Document handle missing valid non-empty handle_id")
    return handle_id


def _handle_format_failure(
    workspace: BatchOutputWorkspace,
    context: BatchItemContext,
    exc: Exception,
    *,
    activated: bool,
) -> BatchItemOutcome:
    """Safely map an exception to a failure outcome, invoking cleanup if activated.

    Fatal COM and timeout errors are re-raised so the central coordinator can poison
    or terminate the runtime session appropriately.
    """
    if isinstance(exc, (TimeoutError, CADRuntimeBusyError, CADRuntimeUnavailableError)):
        if activated:
            with contextlib.suppress(Exception):
                workspace.cleanup_format(context)
        raise exc

    cleanup_diagnostic: BatchDiagnostic | None = None
    if activated:
        try:
            workspace.cleanup_format(context)
        except Exception:
            cleanup_diagnostic = BatchDiagnostic(
                code="ARTIFACT_EXPORT_FAILED",
                message=f"Failed to clean up temporary workspace output for format '{context.format}'",
                format=_as_batch_output_format(context.format),
            )

    format_literal = _as_batch_output_format(context.format)

    if isinstance(exc, BatchWorkspaceError):
        primary_diag = exc.diagnostic
    elif isinstance(exc, BatchFormatValidationError):
        primary_diag = BatchDiagnostic(
            code="ARTIFACT_EXPORT_FAILED",
            message=exc.message,
            format=format_literal,
        )
    elif isinstance(exc, CADExportError):
        primary_diag = BatchDiagnostic(
            code="ARTIFACT_EXPORT_FAILED",
            message=f"Failed to export {context.format.upper()} artifact '{context.target_path.name}'",
            format=format_literal,
        )
    elif isinstance(exc, CADDocumentError):
        primary_diag = BatchDiagnostic(
            code="INTERNAL_ERROR",
            message=f"Document error during {context.format.upper()} export of '{context.input}'",
            format=format_literal,
        )
    else:
        primary_diag = BatchDiagnostic(
            code="ARTIFACT_EXPORT_FAILED",
            message=f"Export failed for format '{context.format}'",
            format=format_literal,
        )

    errors = (primary_diag, cleanup_diagnostic) if cleanup_diagnostic is not None else (primary_diag,)

    return BatchItemOutcome.failure(format=context.format, errors=errors)


class Export3DHandler:
    """Request-scoped handler for 3D model exports (.par, .psm, .asm to STEP/STL)."""

    def __init__(
        self,
        workspace: BatchOutputWorkspace,
        model_exporter: Callable[[object, str, Path], None],
        assembly_checker: Callable[[object], AssemblyReferenceCheckResult],
    ) -> None:
        self._workspace: Final[BatchOutputWorkspace] = workspace
        self._model_exporter: Final[Callable[[object, str, Path], None]] = model_exporter
        self._assembly_checker: Final[Callable[[object], AssemblyReferenceCheckResult]] = assembly_checker
        # Bounded cache of pure assembly preparation results keyed by (context.input, handle_id)
        self._assembly_cache: dict[_PrepCacheKey, _DiagnosticPayload | None] = {}

    def __call__(
        self,
        doc_handle: object,
        context: BatchItemContext,
    ) -> BatchItemOutcome:
        """Execute guarded 3D export lifecycle for a single format."""
        format_literal = _as_batch_output_format(context.format)

        # 1. Preflight operation, format, and extension contracts
        if context.operation_id != "export_3d":
            return BatchItemOutcome.failure(
                format=context.format,
                errors=(
                    BatchDiagnostic(
                        code="INTERNAL_ERROR",
                        message=f"Unexpected operation_id '{context.operation_id}' for Export3DHandler",
                        format=format_literal,
                    ),
                ),
            )

        if context.format not in SUPPORTED_3D_FORMATS:
            return BatchItemOutcome.failure(
                format=context.format,
                errors=(
                    BatchDiagnostic(
                        code="ARTIFACT_EXPORT_FAILED",
                        message=f"Unsupported format '{context.format}' for 3D export",
                        format=format_literal,
                    ),
                ),
            )

        source_suffix = context.source_path.suffix.lower()
        if source_suffix not in SUPPORTED_3D_EXTENSIONS:
            return BatchItemOutcome.failure(
                format=context.format,
                errors=(
                    BatchDiagnostic(
                        code="ARTIFACT_EXPORT_FAILED",
                        message=f"Source extension '{source_suffix}' not supported for 3D export",
                        format=format_literal,
                    ),
                ),
            )

        try:
            handle_id = _extract_handle_id(doc_handle)
        except CADDocumentError as handle_exc:
            return BatchItemOutcome.failure(
                format=context.format,
                errors=(
                    BatchDiagnostic(
                        code="INTERNAL_ERROR",
                        message=str(handle_exc),
                        format=format_literal,
                    ),
                ),
            )

        # 2. Assembly reference check (performed once per .asm document handle across formats)
        if source_suffix == ".asm":
            cache_key = (context.input, handle_id)
            if cache_key not in self._assembly_cache:
                try:
                    check_result = self._assembly_checker(doc_handle)
                    if isinstance(check_result, AssemblyReferenceCheckResult):
                        if not check_result.is_resolved:
                            self._assembly_cache[cache_key] = (
                                "ARTIFACT_EXPORT_FAILED",
                                f"Assembly contains {check_result.unresolved_count} unresolved reference(s)",
                            )
                        else:
                            self._assembly_cache[cache_key] = None
                    else:
                        self._assembly_cache[cache_key] = (
                            "ARTIFACT_EXPORT_FAILED",
                            f"Invalid assembly reference check outcome for '{context.input}'",
                        )
                except Exception as check_exc:
                    if isinstance(check_exc, (TimeoutError, CADRuntimeBusyError, CADRuntimeUnavailableError)):
                        raise check_exc
                    self._assembly_cache[cache_key] = (
                        "ARTIFACT_EXPORT_FAILED",
                        f"Failed to inspect assembly references for '{context.input}'",
                    )

            cached_error = self._assembly_cache[cache_key]
            if cached_error is not None:
                err_code, err_msg = cached_error
                diag = BatchDiagnostic(
                    code=err_code,
                    message=err_msg,
                    format=format_literal,
                )
                # Do not activate workspace or call export if assembly references are unresolved
                return BatchItemOutcome.failure(format=context.format, errors=(diag,))

        # 3. Guarded workspace lifecycle execution
        activated = False
        try:
            self._workspace.begin_format(context)
            activated = True

            self._model_exporter(doc_handle, context.format, context.work_path)

            snapshot = validate_batch_output(
                cast(BatchOutputFormat, context.format),
                context.work_path,
            )

            published_path = self._workspace.finalize_format(context, snapshot)

            artifact_record = BatchArtifactRecord(
                format=cast(BatchOutputFormat, context.format),
                path=str(published_path),
            )
            return BatchItemOutcome.success(format=context.format, artifact=artifact_record)

        except Exception as exc:
            return _handle_format_failure(self._workspace, context, exc, activated=activated)


class PublishDrawingHandler:
    """Request-scoped handler for 2D drawing publication (.dft to PDF/DXF)."""

    def __init__(
        self,
        workspace: BatchOutputWorkspace,
        drawing_publisher: Callable[[object, str, Path], None],
        drawing_preparer: Callable[[object], Any],
    ) -> None:
        self._workspace: Final[BatchOutputWorkspace] = workspace
        self._drawing_publisher: Final[Callable[[object, str, Path], None]] = drawing_publisher
        self._drawing_preparer: Final[Callable[[object], Any]] = drawing_preparer
        # Bounded cache of pure view refresh results keyed by (context.input, handle_id)
        self._prep_cache: dict[_PrepCacheKey, _DiagnosticPayload | None] = {}

    def __call__(
        self,
        doc_handle: object,
        context: BatchItemContext,
    ) -> BatchItemOutcome:
        """Execute guarded drawing publication lifecycle for a single format."""
        format_literal = _as_batch_output_format(context.format)

        # 1. Preflight operation, format, and extension contracts
        if context.operation_id != "publish_drawing":
            return BatchItemOutcome.failure(
                format=context.format,
                errors=(
                    BatchDiagnostic(
                        code="INTERNAL_ERROR",
                        message=f"Unexpected operation_id '{context.operation_id}' for PublishDrawingHandler",
                        format=format_literal,
                    ),
                ),
            )

        if context.format not in SUPPORTED_DRAWING_FORMATS:
            return BatchItemOutcome.failure(
                format=context.format,
                errors=(
                    BatchDiagnostic(
                        code="ARTIFACT_EXPORT_FAILED",
                        message=f"Unsupported format '{context.format}' for drawing publication",
                        format=format_literal,
                    ),
                ),
            )

        source_suffix = context.source_path.suffix.lower()
        if source_suffix not in SUPPORTED_DRAWING_EXTENSIONS:
            return BatchItemOutcome.failure(
                format=context.format,
                errors=(
                    BatchDiagnostic(
                        code="ARTIFACT_EXPORT_FAILED",
                        message=f"Source extension '{source_suffix}' not supported for drawing publication",
                        format=format_literal,
                    ),
                ),
            )

        try:
            handle_id = _extract_handle_id(doc_handle)
        except CADDocumentError as handle_exc:
            return BatchItemOutcome.failure(
                format=context.format,
                errors=(
                    BatchDiagnostic(
                        code="INTERNAL_ERROR",
                        message=str(handle_exc),
                        format=format_literal,
                    ),
                ),
            )

        # 2. Drawing view refresh (performed once per .dft document handle across formats)
        cache_key = (context.input, handle_id)
        if cache_key not in self._prep_cache:
            try:
                self._drawing_preparer(doc_handle)
                self._prep_cache[cache_key] = None
            except Exception as prep_exc:
                if isinstance(prep_exc, (TimeoutError, CADRuntimeBusyError, CADRuntimeUnavailableError)):
                    raise prep_exc
                self._prep_cache[cache_key] = (
                    "ARTIFACT_EXPORT_FAILED",
                    f"Failed to refresh drawing views for '{context.input}'",
                )

        cached_error = self._prep_cache[cache_key]
        if cached_error is not None:
            err_code, err_msg = cached_error
            diag = BatchDiagnostic(
                code=err_code,
                message=err_msg,
                format=format_literal,
            )
            # Do not activate workspace or call publication if drawing view refresh failed
            return BatchItemOutcome.failure(format=context.format, errors=(diag,))

        # 3. Guarded workspace lifecycle execution
        activated = False
        try:
            self._workspace.begin_format(context)
            activated = True

            self._drawing_publisher(doc_handle, context.format, context.work_path)

            snapshot = validate_batch_output(
                cast(BatchOutputFormat, context.format),
                context.work_path,
            )

            published_path = self._workspace.finalize_format(context, snapshot)

            artifact_record = BatchArtifactRecord(
                format=cast(BatchOutputFormat, context.format),
                path=str(published_path),
            )
            return BatchItemOutcome.success(format=context.format, artifact=artifact_record)

        except Exception as exc:
            return _handle_format_failure(self._workspace, context, exc, activated=activated)


__all__ = [
    "Export3DHandler",
    "PublishDrawingHandler",
]
