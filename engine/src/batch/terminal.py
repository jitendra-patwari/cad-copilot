"""Batch terminal response and manifest semantic validation."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from batch.accounting import (
    validate_diagnostics,
    validate_summary_accounting,
)
from batch.models import BatchValidationError

if TYPE_CHECKING:
    from batch.models import (
        BatchDiagnostic,
        BatchFileResult,
        BatchManifest,
        BatchResponse,
        BatchSummary,
        ManifestFileResult,
    )

VALID_RESPONSE_STATUSES: frozenset[str] = frozenset({"completed", "cancelled", "rejected", "failed"})
VALID_MANIFEST_STATUSES: frozenset[str] = frozenset({"completed", "cancelled", "failed"})
_FATAL_RESPONSE_TOP_LEVEL_CODES: frozenset[str] = frozenset(
    {
        "SOLID_EDGE_UNHEALTHY",
        "DOCUMENT_CLOSE_FAILED",
        "SOURCE_INTEGRITY_FAILED",
        "INTERNAL_ERROR",
        "MANIFEST_PUBLICATION_FAILED",
    }
)
_FATAL_MANIFEST_TOP_LEVEL_CODES: frozenset[str] = frozenset(
    {
        "SOLID_EDGE_UNHEALTHY",
        "DOCUMENT_CLOSE_FAILED",
        "SOURCE_INTEGRITY_FAILED",
        "INTERNAL_ERROR",
    }
)


def validate_terminal_semantics_core(
    *,
    status: str,
    summary: BatchSummary | None,
    results: Sequence[BatchFileResult],
    unprocessed_files: Sequence[str],
    cancelled_files: Sequence[str],
    errors: Sequence[BatchDiagnostic],
    warnings: Sequence[BatchDiagnostic],
    request_id: str = "unknown",
) -> None:
    """Validate terminal semantic invariants common to BatchResponse and BatchExecutionOutcome."""
    req_id = request_id

    if status not in VALID_RESPONSE_STATUSES:
        raise BatchValidationError(
            f"Invalid response status '{status}'",
            code="INVALID_SCHEMA",
            request_id=req_id,
        )

    # Validate diagnostics
    validate_diagnostics(warnings, is_warning=True, request_id=req_id)
    validate_diagnostics(errors, is_warning=False, request_id=req_id)

    # BATCH_CANCELLED is strictly forbidden as a top-level error
    for e in errors:
        if e.code == "BATCH_CANCELLED":
            raise BatchValidationError(
                "BATCH_CANCELLED is not permitted as a top-level error",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )

    # Validate per-file result diagnostics
    for r in results:
        validate_diagnostics(r.warnings, is_warning=True, request_id=req_id)
        validate_diagnostics(r.errors, is_warning=False, request_id=req_id)

    # Collect all BATCH_CANCELLED diagnostics across all per-file results
    cancel_errors: list[tuple[BatchFileResult, BatchDiagnostic]] = [
        (r, e) for r in results for e in r.errors if e.code == "BATCH_CANCELLED"
    ]
    if len(cancel_errors) > 1:
        raise BatchValidationError(
            "Only one BATCH_CANCELLED error marker is permitted across all file results",
            code="INVALID_SCHEMA",
            request_id=req_id,
        )
    if cancel_errors:
        canc_file, canc_diag = cancel_errors[0]
        if canc_diag.format is None:
            raise BatchValidationError(
                "BATCH_CANCELLED error must specify a format",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        if results and results[-1] is not canc_file:
            raise BatchValidationError(
                "BATCH_CANCELLED must be on the final attempted file result",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        artifact_formats = {a.format for a in canc_file.artifacts}
        if canc_diag.format in artifact_formats:
            raise BatchValidationError(
                f"Contradictory result: format '{canc_diag.format}' has both an artifact and BATCH_CANCELLED",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        other_error_formats = {oe.format for oe in canc_file.errors if oe is not canc_diag and oe.format is not None}
        if canc_diag.format in other_error_formats:
            raise BatchValidationError(
                f"Contradictory result: format '{canc_diag.format}' has both an execution error and BATCH_CANCELLED",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )

    if status == "completed":
        if summary is None:
            raise BatchValidationError(
                "Completed response requires a summary",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        if errors:
            raise BatchValidationError(
                "Completed response must not have top-level errors",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        if cancel_errors:
            raise BatchValidationError(
                "Completed response must not contain BATCH_CANCELLED errors",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        if summary.cancelled != 0 or len(cancelled_files) != 0:
            raise BatchValidationError(
                "Completed response must have zero cancelled files",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        validate_summary_accounting(
            summary,
            results,
            unprocessed_files,
            cancelled_files,
            request_id=req_id,
        )

    elif status == "cancelled":
        if summary is None:
            raise BatchValidationError(
                "Cancelled response requires a summary",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        if errors:
            raise BatchValidationError(
                "Cancelled response must not have top-level errors",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        has_cancelled_files = len(cancelled_files) > 0
        has_batch_cancelled = len(cancel_errors) > 0
        if not has_cancelled_files and not has_batch_cancelled:
            raise BatchValidationError(
                "Cancelled response requires at least one cancelled file or BATCH_CANCELLED per-file diagnostic",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        validate_summary_accounting(
            summary,
            results,
            unprocessed_files,
            cancelled_files,
            request_id=req_id,
        )

    elif status == "rejected":
        if summary is not None:
            raise BatchValidationError(
                "Rejected response must not include summary",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        if results:
            raise BatchValidationError(
                "Rejected response must not include results",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        if unprocessed_files or cancelled_files:
            raise BatchValidationError(
                "Rejected response must not include unprocessed or cancelled files",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        if warnings:
            raise BatchValidationError(
                "Rejected response must not include warnings",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        if not errors:
            raise BatchValidationError(
                "Rejected response requires at least one top-level error",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )

    elif status == "failed":
        if not errors:
            raise BatchValidationError(
                "Failed response requires at least one top-level error",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        if summary is None:
            # Early failure before processing began
            if results or unprocessed_files or cancelled_files:
                raise BatchValidationError(
                    "Early failed response must not include results or skipped files",
                    code="INVALID_SCHEMA",
                    request_id=req_id,
                )
        else:
            # Progressed failure after processing began
            if summary.cancelled != 0 or len(cancelled_files) != 0:
                raise BatchValidationError(
                    "Failed response must have zero cancelled files",
                    code="INVALID_SCHEMA",
                    request_id=req_id,
                )
            if cancel_errors:
                has_fatal_top_level = any(e.code in _FATAL_RESPONSE_TOP_LEVEL_CODES for e in errors)
                if not has_fatal_top_level:
                    raise BatchValidationError(
                        "BATCH_CANCELLED in failed response requires a fatal top-level error",
                        code="INVALID_SCHEMA",
                        request_id=req_id,
                    )
            validate_summary_accounting(
                summary,
                results,
                unprocessed_files,
                cancelled_files,
                request_id=req_id,
            )


def validate_batch_response_semantics(response: BatchResponse, *, request_id: str = "unknown") -> None:
    """Validate terminal response semantic relationships, accounting, and warning rules."""
    req_id = response.request_id or request_id

    validate_terminal_semantics_core(
        status=response.status,
        summary=response.summary,
        results=response.results,
        unprocessed_files=response.unprocessed_files,
        cancelled_files=response.cancelled_files,
        errors=response.errors,
        warnings=response.warnings,
        request_id=req_id,
    )

    if response.status == "completed":
        if response.manifest is None:
            raise BatchValidationError(
                "Completed response requires a manifest reference",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )

    elif response.status == "cancelled":
        if response.manifest is None:
            raise BatchValidationError(
                "Cancelled response requires a manifest reference",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )

    elif response.status == "rejected":
        if response.manifest is not None:
            raise BatchValidationError(
                "Rejected response must not include manifest",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )

    elif response.status == "failed" and response.summary is None and response.manifest is not None:
        raise BatchValidationError(
            "Early failed response must not include manifest reference",
            code="INVALID_SCHEMA",
            request_id=req_id,
        )


def validate_batch_manifest_semantics(manifest: BatchManifest, *, request_id: str = "unknown") -> None:
    """Validate manifest semantic relationships, accounting, format matching, and warning rules."""
    req_id = manifest.request_id or request_id

    if manifest.manifest_version != "1.0":
        raise BatchValidationError(
            f"Manifest version must be '1.0', got '{manifest.manifest_version}'",
            code="INVALID_SCHEMA",
            request_id=req_id,
        )
    if manifest.contract_version != "1.0":
        raise BatchValidationError(
            f"Contract version must be '1.0', got '{manifest.contract_version}'",
            code="INVALID_SCHEMA",
            request_id=req_id,
        )

    if manifest.status not in VALID_MANIFEST_STATUSES:
        raise BatchValidationError(
            f"Invalid manifest status '{manifest.status}'",
            code="INVALID_SCHEMA",
            request_id=req_id,
        )

    # Validate diagnostics
    validate_diagnostics(manifest.warnings, is_warning=True, request_id=req_id)
    validate_diagnostics(manifest.errors, is_warning=False, request_id=req_id)

    # BATCH_CANCELLED is strictly forbidden as a top-level error
    for e in manifest.errors:
        if e.code == "BATCH_CANCELLED":
            raise BatchValidationError(
                "BATCH_CANCELLED is not permitted as a top-level error",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )

    # Validate per-file result diagnostics
    for r in manifest.results:
        validate_diagnostics(r.warnings, is_warning=True, request_id=req_id)
        validate_diagnostics(r.errors, is_warning=False, request_id=req_id)

    # Cross-field format validation
    allowed_formats = set(manifest.operation.formats)
    for r in manifest.results:
        r_formats: set[str] = set()
        for a in r.artifacts:
            if a.format not in allowed_formats:
                raise BatchValidationError(
                    f"Artifact format '{a.format}' not in manifest operation formats",
                    code="INVALID_SCHEMA",
                    request_id=req_id,
                )
            r_formats.add(a.format)
        for e in r.errors:
            if e.format is not None and e.format not in allowed_formats:
                raise BatchValidationError(
                    f"Error format '{e.format}' not in manifest operation formats",
                    code="INVALID_SCHEMA",
                    request_id=req_id,
                )
        if r.status == "accepted":
            if r_formats != allowed_formats:
                raise BatchValidationError(
                    "Accepted result artifacts must cover all requested operation formats",
                    code="INVALID_SCHEMA",
                    request_id=req_id,
                )
        elif r.status == "partial":
            if not (0 < len(r_formats) < len(allowed_formats)):
                raise BatchValidationError(
                    "Partial result must produce a strict subset of requested operation formats",
                    code="INVALID_SCHEMA",
                    request_id=req_id,
                )
        elif r.status == "failed" and r.artifacts:
            raise BatchValidationError(
                "Failed file result must not contain artifacts",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )

    # Collect all BATCH_CANCELLED diagnostics across all per-file results
    cancel_errors: list[tuple[ManifestFileResult, BatchDiagnostic]] = [
        (r, e) for r in manifest.results for e in r.errors if e.code == "BATCH_CANCELLED"
    ]
    if len(cancel_errors) > 1:
        raise BatchValidationError(
            "Only one BATCH_CANCELLED error marker is permitted across all file results",
            code="INVALID_SCHEMA",
            request_id=req_id,
        )
    if cancel_errors:
        canc_file, canc_diag = cancel_errors[0]
        if canc_diag.format is None:
            raise BatchValidationError(
                "BATCH_CANCELLED error must specify a format",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        if manifest.results and manifest.results[-1] is not canc_file:
            raise BatchValidationError(
                "BATCH_CANCELLED must be on the final attempted file result",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        if canc_diag.format not in allowed_formats:
            raise BatchValidationError(
                f"BATCH_CANCELLED format '{canc_diag.format}' not in manifest operation formats",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        artifact_formats = {a.format for a in canc_file.artifacts}
        if canc_diag.format in artifact_formats:
            raise BatchValidationError(
                f"Contradictory result: format '{canc_diag.format}' has both an artifact and BATCH_CANCELLED",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        other_error_formats = {oe.format for oe in canc_file.errors if oe is not canc_diag and oe.format is not None}
        if canc_diag.format in other_error_formats:
            raise BatchValidationError(
                f"Contradictory result: format '{canc_diag.format}' has both an execution error and BATCH_CANCELLED",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )

        # Enforce first-unattempted-format semantics using manifest operation format order
        attempted_formats = artifact_formats | other_error_formats
        cancel_idx = manifest.operation.formats.index(canc_diag.format)
        for prev_fmt in manifest.operation.formats[:cancel_idx]:
            if prev_fmt not in attempted_formats:
                raise BatchValidationError(
                    f"BATCH_CANCELLED format '{canc_diag.format}' is not the first unattempted format; "
                    f"prior format '{prev_fmt}' was not attempted",
                    code="INVALID_SCHEMA",
                    request_id=req_id,
                )
        for post_fmt in manifest.operation.formats[cancel_idx + 1 :]:
            if post_fmt in attempted_formats:
                raise BatchValidationError(
                    f"Contradictory result: format '{post_fmt}' was attempted after BATCH_CANCELLED on '{canc_diag.format}'",
                    code="INVALID_SCHEMA",
                    request_id=req_id,
                )

    if manifest.status == "completed":
        if manifest.errors:
            raise BatchValidationError(
                "Completed manifest must not have top-level errors",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        if cancel_errors:
            raise BatchValidationError(
                "Completed manifest must not contain BATCH_CANCELLED errors",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        if manifest.summary.cancelled != 0 or len(manifest.cancelled_files) != 0:
            raise BatchValidationError(
                "Completed manifest must have zero cancelled files",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        validate_summary_accounting(
            manifest.summary,
            manifest.results,
            manifest.unprocessed_files,
            manifest.cancelled_files,
            request_id=req_id,
        )
    elif manifest.status == "cancelled":
        if manifest.errors:
            raise BatchValidationError(
                "Cancelled manifest must not have top-level errors",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        has_cancelled_files = len(manifest.cancelled_files) > 0
        has_batch_cancelled = len(cancel_errors) > 0
        if not has_cancelled_files and not has_batch_cancelled:
            raise BatchValidationError(
                "Cancelled manifest requires at least one cancelled file or BATCH_CANCELLED per-file diagnostic",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        validate_summary_accounting(
            manifest.summary,
            manifest.results,
            manifest.unprocessed_files,
            manifest.cancelled_files,
            request_id=req_id,
        )
    elif manifest.status == "failed":
        if not manifest.errors:
            raise BatchValidationError(
                "Failed manifest requires at least one top-level error",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        if manifest.summary.cancelled != 0 or len(manifest.cancelled_files) != 0:
            raise BatchValidationError(
                "Failed manifest must have zero cancelled files",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        if cancel_errors:
            has_fatal_top_level = any(e.code in _FATAL_MANIFEST_TOP_LEVEL_CODES for e in manifest.errors)
            if not has_fatal_top_level:
                raise BatchValidationError(
                    "BATCH_CANCELLED in failed manifest requires a fatal top-level error",
                    code="INVALID_SCHEMA",
                    request_id=req_id,
                )
        validate_summary_accounting(
            manifest.summary,
            manifest.results,
            manifest.unprocessed_files,
            manifest.cancelled_files,
            request_id=req_id,
        )


__all__ = [
    "VALID_MANIFEST_STATUSES",
    "VALID_RESPONSE_STATUSES",
    "validate_batch_manifest_semantics",
    "validate_batch_response_semantics",
    "validate_terminal_semantics_core",
]
