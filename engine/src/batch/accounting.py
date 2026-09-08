"""Batch diagnostics and summary accounting invariants validation."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from batch.models import (
    APPROVED_DIAGNOSTIC_CODES,
    APPROVED_WARNING_CODES,
    BatchDiagnostic,
    BatchFileResult,
    BatchSummary,
    BatchValidationError,
    ManifestFileResult,
)


def validate_diagnostics(
    diagnostics: Sequence[Any],
    *,
    is_warning: bool,
    request_id: str = "unknown",
) -> None:
    """Validate diagnostic records against canonical codes and message length rules."""
    for d in diagnostics:
        if not isinstance(d, BatchDiagnostic):
            raise BatchValidationError(
                f"Diagnostic record must be a BatchDiagnostic, got {type(d).__name__}",
                code="INVALID_SCHEMA",
                request_id=request_id,
            )
        if d.code not in APPROVED_DIAGNOSTIC_CODES:
            raise BatchValidationError(
                f"Diagnostic code '{d.code}' is not an approved canonical code",
                code="INVALID_SCHEMA",
                request_id=request_id,
            )
        if is_warning and d.code not in APPROVED_WARNING_CODES:
            raise BatchValidationError(
                f"Diagnostic code '{d.code}' is not permitted as a warning",
                code="INVALID_SCHEMA",
                request_id=request_id,
            )
        if not is_warning and d.code in APPROVED_WARNING_CODES:
            raise BatchValidationError(
                f"Diagnostic code '{d.code}' is a warning code and not permitted in errors",
                code="INVALID_SCHEMA",
                request_id=request_id,
            )
        if not (1 <= len(d.message) <= 512):
            raise BatchValidationError(
                "Diagnostic message length must be between 1 and 512 characters",
                code="INVALID_SCHEMA",
                request_id=request_id,
            )
        if d.format is not None and not (1 <= len(d.format) <= 32):
            raise BatchValidationError(
                "Diagnostic format length must be between 1 and 32 characters",
                code="INVALID_SCHEMA",
                request_id=request_id,
            )


def validate_summary_accounting(
    summary: BatchSummary,
    results: Sequence[BatchFileResult | ManifestFileResult],
    unprocessed_files: Sequence[str],
    cancelled_files: Sequence[str],
    *,
    request_id: str = "unknown",
) -> None:
    """Validate summary arithmetic and result list partition invariants."""
    if not isinstance(summary, BatchSummary):
        raise BatchValidationError(
            f"Summary must be a BatchSummary instance, got {type(summary).__name__}",
            code="INVALID_SCHEMA",
            request_id=request_id,
        )

    expected_total = summary.accepted + summary.partial + summary.failed + summary.unprocessed + summary.cancelled
    if summary.total != expected_total:
        raise BatchValidationError(
            f"Summary total ({summary.total}) does not match sum of category counts ({expected_total})",
            code="INVALID_SCHEMA",
            request_id=request_id,
        )

    expected_results_len = summary.accepted + summary.partial + summary.failed
    if len(results) != expected_results_len:
        raise BatchValidationError(
            f"Results length ({len(results)}) does not match summary attempted count ({expected_results_len})",
            code="INVALID_SCHEMA",
            request_id=request_id,
        )

    if len(unprocessed_files) != summary.unprocessed:
        raise BatchValidationError(
            f"Unprocessed files length ({len(unprocessed_files)}) does not match summary.unprocessed ({summary.unprocessed})",
            code="INVALID_SCHEMA",
            request_id=request_id,
        )

    if len(cancelled_files) != summary.cancelled:
        raise BatchValidationError(
            f"Cancelled files length ({len(cancelled_files)}) does not match summary.cancelled ({summary.cancelled})",
            code="INVALID_SCHEMA",
            request_id=request_id,
        )

    acc_count = sum(1 for r in results if r.status == "accepted")
    part_count = sum(1 for r in results if r.status == "partial")
    fail_count = sum(1 for r in results if r.status == "failed")

    if acc_count != summary.accepted:
        raise BatchValidationError(
            f"Results accepted count ({acc_count}) does not match summary.accepted ({summary.accepted})",
            code="INVALID_SCHEMA",
            request_id=request_id,
        )
    if part_count != summary.partial:
        raise BatchValidationError(
            f"Results partial count ({part_count}) does not match summary.partial ({summary.partial})",
            code="INVALID_SCHEMA",
            request_id=request_id,
        )
    if fail_count != summary.failed:
        raise BatchValidationError(
            f"Results failed count ({fail_count}) does not match summary.failed ({summary.failed})",
            code="INVALID_SCHEMA",
            request_id=request_id,
        )

    seen_all: set[str] = set()
    for r in results:
        key = r.input.lower()
        if key in seen_all:
            raise BatchValidationError(
                "Duplicate input in results",
                code="INVALID_SCHEMA",
                request_id=request_id,
            )
        seen_all.add(key)

    for u in unprocessed_files:
        key = u.lower()
        if key in seen_all:
            raise BatchValidationError(
                "Overlapping or duplicate unprocessed file",
                code="INVALID_SCHEMA",
                request_id=request_id,
            )
        seen_all.add(key)

    for c in cancelled_files:
        key = c.lower()
        if key in seen_all:
            raise BatchValidationError(
                "Overlapping or duplicate cancelled file",
                code="INVALID_SCHEMA",
                request_id=request_id,
            )
        seen_all.add(key)

    if len(seen_all) != summary.total:
        raise BatchValidationError(
            f"Total unique files ({len(seen_all)}) does not equal summary.total ({summary.total})",
            code="INVALID_SCHEMA",
            request_id=request_id,
        )


__all__ = [
    "validate_diagnostics",
    "validate_summary_accounting",
]
