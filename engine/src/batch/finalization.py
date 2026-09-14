"""Terminal batch execution finalization and canonical response assembly.

Coordinates finalization of an immutable BatchExecutionOutcome into a canonical BatchResponse:
- Terminal eligibility enforcement: rejected and early failed outcomes never attempt a manifest.
- Progressed completed/cancelled/failed outcomes assemble and publish a durable BatchManifest.
- Manifest references are absolute local paths normalized with forward slashes (target_path.as_posix()).
- Enforces publication failure precedence: converts would-be completed/cancelled outcomes to progressed failed,
  moves cancelled inputs to unprocessed, resets cancelled counts, and records MANIFEST_PUBLICATION_FAILED once.
- Preserves attempted file results and existing fatal errors.
- Proves semantic validity of every returned response.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from batch.execution import BatchExecutionOutcome
from batch.manifest import assemble_batch_manifest
from batch.manifest_publication import publish_batch_manifest
from batch.models import (
    BatchDiagnostic,
    BatchManifestReference,
    BatchRequest,
    BatchResponse,
    BatchSummary,
)
from batch.terminal import validate_batch_response_semantics

MANIFEST_PUBLICATION_FAILED_MESSAGE: Final[str] = "Failed to publish batch summary manifest."


def finalize_batch_outcome(
    request: BatchRequest,
    outcome: BatchExecutionOutcome,
    *,
    engine_version: str,
) -> BatchResponse:
    """Finalize a batch execution outcome into a canonical, schema-valid BatchResponse.

    Args:
        request: Original validated BatchRequest.
        outcome: Terminal BatchExecutionOutcome produced by BatchService.
        engine_version: Verified installed engine distribution version string.

    Returns:
        Canonical BatchResponse with published manifest reference if eligible and successful.
    """
    req_id = request.request_id

    # 1. Identity and version agreement must hold for ALL outcomes (including rejected and early failed)
    if outcome.request_id != request.request_id:
        raise ValueError(f"Request ID mismatch: request '{request.request_id}' vs outcome '{outcome.request_id}'")

    if outcome.contract_version != request.contract_version:
        raise ValueError(
            f"Contract version mismatch: request '{request.contract_version}' vs outcome '{outcome.contract_version}'"
        )

    # 2. Early rejected requests: never hash artifacts or attempt manifest
    if outcome.status == "rejected":
        response = BatchResponse(
            contract_version=outcome.contract_version,
            request_id=req_id,
            status="rejected",
            summary=None,
            results=(),
            manifest=None,
            unprocessed_files=outcome.unprocessed_files,
            cancelled_files=outcome.cancelled_files,
            errors=outcome.errors,
            warnings=outcome.warnings,
        )
        validate_batch_response_semantics(response, request_id=req_id)
        return response

    # 3. Early failed requests (no summary): never hash artifacts or attempt manifest
    if outcome.status == "failed" and outcome.summary is None:
        response = BatchResponse(
            contract_version=outcome.contract_version,
            request_id=req_id,
            status="failed",
            summary=None,
            results=(),
            manifest=None,
            unprocessed_files=outcome.unprocessed_files,
            cancelled_files=outcome.cancelled_files,
            errors=outcome.errors,
            warnings=outcome.warnings,
        )
        validate_batch_response_semantics(response, request_id=req_id)
        return response

    # 4. Preflight agreement for progressed outcomes
    if outcome.summary is None:
        raise ValueError(f"Progressed outcome '{outcome.status}' must have a summary")

    if outcome.output_root is None or outcome.output_root != Path(request.output_root):
        raise ValueError("Outcome output_root must be present and match request output_root")

    # 4. Progressed execution: assemble, publish, and reference manifest
    try:
        manifest = assemble_batch_manifest(request, outcome, engine_version=engine_version)
        published_path = publish_batch_manifest(manifest, outcome.output_root)
        manifest_ref = BatchManifestReference(path=published_path.as_posix())

        response = BatchResponse(
            contract_version=outcome.contract_version,
            request_id=req_id,
            status=outcome.status,
            summary=outcome.summary,
            results=outcome.file_results,
            manifest=manifest_ref,
            unprocessed_files=outcome.unprocessed_files,
            cancelled_files=outcome.cancelled_files,
            errors=outcome.errors,
            warnings=outcome.warnings,
        )
        validate_batch_response_semantics(response, request_id=req_id)
        return response

    except Exception:
        # Manifest assembly, serialization, collision, rename, or post-check failed.
        # Enforce batch publication failure precedence rules.
        new_unprocessed = outcome.unprocessed_files + outcome.cancelled_files
        new_summary = BatchSummary(
            total=outcome.summary.total,
            accepted=outcome.summary.accepted,
            partial=outcome.summary.partial,
            failed=outcome.summary.failed,
            unprocessed=len(new_unprocessed),
            cancelled=0,
        )

        pub_error = BatchDiagnostic(
            code="MANIFEST_PUBLICATION_FAILED",
            message=MANIFEST_PUBLICATION_FAILED_MESSAGE,
        )

        errors: tuple[BatchDiagnostic, ...]
        if outcome.status in ("completed", "cancelled"):
            # Convert would-be completed/cancelled into progressed failed
            errors = (pub_error,)
        else:
            # Already progressed failed: retain existing fatal errors and append MANIFEST_PUBLICATION_FAILED once
            has_pub_error = any(e.code == "MANIFEST_PUBLICATION_FAILED" for e in outcome.errors)
            errors = outcome.errors if has_pub_error else (*outcome.errors, pub_error)

        fallback_response = BatchResponse(
            contract_version=outcome.contract_version,
            request_id=req_id,
            status="failed",
            summary=new_summary,
            results=outcome.file_results,
            manifest=None,
            unprocessed_files=new_unprocessed,
            cancelled_files=(),
            errors=errors,
            warnings=outcome.warnings,
        )
        validate_batch_response_semantics(fallback_response, request_id=req_id)
        return fallback_response


__all__ = [
    "MANIFEST_PUBLICATION_FAILED_MESSAGE",
    "finalize_batch_outcome",
]
