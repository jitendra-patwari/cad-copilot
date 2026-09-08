"""Batch terminal response and manifest semantic validation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from batch.accounting import (
    validate_diagnostics,
    validate_summary_accounting,
)
from batch.models import BatchValidationError

if TYPE_CHECKING:
    from batch.models import BatchManifest, BatchResponse

VALID_RESPONSE_STATUSES: frozenset[str] = frozenset({"completed", "cancelled", "rejected", "failed"})
VALID_MANIFEST_STATUSES: frozenset[str] = frozenset({"completed", "cancelled", "failed"})


def validate_batch_response_semantics(response: BatchResponse, *, request_id: str = "unknown") -> None:
    """Validate terminal response semantic relationships, accounting, and warning rules."""
    req_id = response.request_id or request_id

    if response.status not in VALID_RESPONSE_STATUSES:
        raise BatchValidationError(
            f"Invalid response status '{response.status}'",
            code="INVALID_SCHEMA",
            request_id=req_id,
        )

    # Validate diagnostics
    validate_diagnostics(response.warnings, is_warning=True, request_id=req_id)
    validate_diagnostics(response.errors, is_warning=False, request_id=req_id)

    # Validate per-file result diagnostics
    for r in response.results:
        validate_diagnostics(r.warnings, is_warning=True, request_id=req_id)
        validate_diagnostics(r.errors, is_warning=False, request_id=req_id)

    if response.status == "completed":
        if response.summary is None:
            raise BatchValidationError(
                "Completed response requires a summary",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        if response.manifest is None:
            raise BatchValidationError(
                "Completed response requires a manifest reference",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        if response.errors:
            raise BatchValidationError(
                "Completed response must not have top-level errors",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        if response.summary.cancelled != 0 or len(response.cancelled_files) != 0:
            raise BatchValidationError(
                "Completed response must have zero cancelled files",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        validate_summary_accounting(
            response.summary,
            response.results,
            response.unprocessed_files,
            response.cancelled_files,
            request_id=req_id,
        )

    elif response.status == "cancelled":
        if response.summary is None:
            raise BatchValidationError(
                "Cancelled response requires a summary",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        if response.manifest is None:
            raise BatchValidationError(
                "Cancelled response requires a manifest reference",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        if response.errors:
            raise BatchValidationError(
                "Cancelled response must not have top-level errors",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        if response.summary.cancelled == 0 or len(response.cancelled_files) == 0:
            raise BatchValidationError(
                "Cancelled response must have at least one cancelled file",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        validate_summary_accounting(
            response.summary,
            response.results,
            response.unprocessed_files,
            response.cancelled_files,
            request_id=req_id,
        )

    elif response.status == "rejected":
        if response.summary is not None:
            raise BatchValidationError(
                "Rejected response must not include summary",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        if response.manifest is not None:
            raise BatchValidationError(
                "Rejected response must not include manifest",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        if response.results:
            raise BatchValidationError(
                "Rejected response must not include results",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        if response.unprocessed_files or response.cancelled_files:
            raise BatchValidationError(
                "Rejected response must not include unprocessed or cancelled files",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        if response.warnings:
            raise BatchValidationError(
                "Rejected response must not include warnings",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        if not response.errors:
            raise BatchValidationError(
                "Rejected response requires at least one top-level error",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )

    elif response.status == "failed":
        if not response.errors:
            raise BatchValidationError(
                "Failed response requires at least one top-level error",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        if response.summary is None:
            # Early failure before processing began
            if response.manifest is not None:
                raise BatchValidationError(
                    "Early failed response must not include manifest reference",
                    code="INVALID_SCHEMA",
                    request_id=req_id,
                )
            if response.results or response.unprocessed_files or response.cancelled_files:
                raise BatchValidationError(
                    "Early failed response must not include results or skipped files",
                    code="INVALID_SCHEMA",
                    request_id=req_id,
                )
        else:
            # Progressed failure after processing began
            if response.summary.cancelled != 0 or len(response.cancelled_files) != 0:
                raise BatchValidationError(
                    "Failed response must have zero cancelled files",
                    code="INVALID_SCHEMA",
                    request_id=req_id,
                )
            validate_summary_accounting(
                response.summary,
                response.results,
                response.unprocessed_files,
                response.cancelled_files,
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

    if manifest.status == "completed":
        if manifest.errors:
            raise BatchValidationError(
                "Completed manifest must not have top-level errors",
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
        if manifest.summary.cancelled == 0 or len(manifest.cancelled_files) == 0:
            raise BatchValidationError(
                "Cancelled manifest must have at least one cancelled file",
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
]
