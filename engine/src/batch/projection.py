"""Projection of validated typed models to schema-conforming JSON dictionaries."""

from __future__ import annotations

from typing import Any

from batch.models import (
    BatchManifest,
    BatchRequest,
    BatchResponse,
    BatchValidationError,
)
from batch.paths import validate_batch_request_semantics
from batch.schemas import (
    get_batch_manifest_validator,
    get_batch_request_validator,
    get_batch_response_validator,
)
from batch.terminal import (
    validate_batch_manifest_semantics,
    validate_batch_response_semantics,
)


def project_batch_request(request: BatchRequest) -> dict[str, Any]:
    """Project a typed BatchRequest to a canonical schema-validated dictionary."""
    if not isinstance(request, BatchRequest):
        raise BatchValidationError(
            f"Expected BatchRequest instance, got {type(request).__name__}",
            code="INVALID_SCHEMA",
            request_id="unknown",
        )

    # Pre-projection semantic validation
    validate_batch_request_semantics(request)

    data: dict[str, Any] = {
        "contract_version": request.contract_version,
        "request_id": request.request_id,
        "kind": request.kind,
        "input": {
            "root": request.input.root,
            "files": list(request.input.files),
        },
        "output_root": request.output_root,
        "operation": {
            "type": request.operation.type,
            "formats": list(request.operation.formats),
        },
    }
    options_dict: dict[str, Any] = {}
    if request.options.continue_on_error is not None:
        options_dict["continue_on_error"] = request.options.continue_on_error
    if request.options.max_files is not None:
        options_dict["max_files"] = request.options.max_files
    if options_dict:
        data["options"] = options_dict

    if request.metadata is not None:
        meta_dict: dict[str, Any] = {}
        if request.metadata.source is not None:
            meta_dict["source"] = request.metadata.source
        if request.metadata.label is not None:
            meta_dict["label"] = request.metadata.label
        if request.metadata.job_id is not None:
            meta_dict["job_id"] = request.metadata.job_id
        if meta_dict:
            data["metadata"] = meta_dict

    validator = get_batch_request_validator()
    errors = sorted(validator.iter_errors(data), key=lambda e: str(e.path))
    if errors:
        raise BatchValidationError(
            "Projected batch request failed schema validation.",
            code="INVALID_SCHEMA",
            request_id=request.request_id,
        )
    return data


def project_batch_response(response: BatchResponse) -> dict[str, Any]:
    """Project a typed BatchResponse to a canonical schema-validated dictionary."""
    if not isinstance(response, BatchResponse):
        raise BatchValidationError(
            f"Expected BatchResponse instance, got {type(response).__name__}",
            code="INVALID_SCHEMA",
            request_id="unknown",
        )

    # Pre-projection semantic validation
    validate_batch_response_semantics(response)

    data: dict[str, Any] = {
        "contract_version": response.contract_version,
        "request_id": response.request_id,
        "status": response.status,
    }

    if response.status == "rejected":
        data["errors"] = [
            {
                "code": e.code,
                "message": e.message,
                **({"format": e.format} if e.format else {}),
            }
            for e in response.errors
        ]
        validator = get_batch_response_validator()
        errors = sorted(validator.iter_errors(data), key=lambda e: str(e.path))
        if errors:
            raise BatchValidationError(
                "Projected batch response failed schema validation.",
                code="INVALID_SCHEMA",
                request_id=response.request_id,
            )
        return data

    if response.status == "failed" and response.summary is None:
        data["errors"] = [
            {
                "code": e.code,
                "message": e.message,
                **({"format": e.format} if e.format else {}),
            }
            for e in response.errors
        ]
        if response.warnings:
            data["warnings"] = [
                {
                    "code": w.code,
                    "message": w.message,
                    **({"format": w.format} if w.format else {}),
                }
                for w in response.warnings
            ]
        validator = get_batch_response_validator()
        errors = sorted(validator.iter_errors(data), key=lambda e: str(e.path))
        if errors:
            raise BatchValidationError(
                "Projected batch response failed schema validation.",
                code="INVALID_SCHEMA",
                request_id=response.request_id,
            )
        return data

    # Progressed failure, completed, or cancelled
    assert response.summary is not None
    data["summary"] = {
        "total": response.summary.total,
        "accepted": response.summary.accepted,
        "partial": response.summary.partial,
        "failed": response.summary.failed,
        "unprocessed": response.summary.unprocessed,
        "cancelled": response.summary.cancelled,
    }

    results_out: list[dict[str, Any]] = []
    for r in response.results:
        r_dict: dict[str, Any] = {
            "input": r.input,
            "status": r.status,
        }
        if r.status in ("accepted", "partial"):
            r_dict["artifacts"] = [{"format": a.format, "path": a.path} for a in r.artifacts]
        if r.status in ("partial", "failed") and r.errors:
            r_dict["errors"] = [
                {
                    "code": e.code,
                    "message": e.message,
                    **({"format": e.format} if e.format else {}),
                }
                for e in r.errors
            ]
        if r.warnings:
            r_dict["warnings"] = [
                {
                    "code": w.code,
                    "message": w.message,
                    **({"format": w.format} if w.format else {}),
                }
                for w in r.warnings
            ]
        results_out.append(r_dict)
    data["results"] = results_out

    if response.manifest is not None:
        data["manifest"] = {"path": response.manifest.path}

    if response.unprocessed_files:
        data["unprocessed_files"] = list(response.unprocessed_files)

    if response.status == "cancelled" or response.cancelled_files:
        data["cancelled_files"] = list(response.cancelled_files)

    if response.errors:
        data["errors"] = [
            {
                "code": e.code,
                "message": e.message,
                **({"format": e.format} if e.format else {}),
            }
            for e in response.errors
        ]

    if response.warnings:
        data["warnings"] = [
            {
                "code": w.code,
                "message": w.message,
                **({"format": w.format} if w.format else {}),
            }
            for w in response.warnings
        ]

    validator = get_batch_response_validator()
    errors = sorted(validator.iter_errors(data), key=lambda e: str(e.path))
    if errors:
        raise BatchValidationError(
            "Projected batch response failed schema validation.",
            code="INVALID_SCHEMA",
            request_id=response.request_id,
        )
    return data


def project_batch_manifest(manifest: BatchManifest) -> dict[str, Any]:
    """Project a typed BatchManifest to a canonical schema-validated dictionary."""
    if not isinstance(manifest, BatchManifest):
        raise BatchValidationError(
            f"Expected BatchManifest instance, got {type(manifest).__name__}",
            code="INVALID_SCHEMA",
            request_id="unknown",
        )

    # Pre-projection semantic validation
    validate_batch_manifest_semantics(manifest)

    data: dict[str, Any] = {
        "manifest_version": manifest.manifest_version,
        "contract_version": manifest.contract_version,
        "request_id": manifest.request_id,
        "status": manifest.status,
        "operation": {
            "type": manifest.operation.type,
            "formats": list(manifest.operation.formats),
        },
        "summary": {
            "total": manifest.summary.total,
            "accepted": manifest.summary.accepted,
            "partial": manifest.summary.partial,
            "failed": manifest.summary.failed,
            "unprocessed": manifest.summary.unprocessed,
            "cancelled": manifest.summary.cancelled,
        },
    }

    results_out: list[dict[str, Any]] = []
    for r in manifest.results:
        r_dict: dict[str, Any] = {
            "input": r.input,
            "status": r.status,
        }
        if r.status in ("accepted", "partial"):
            r_dict["artifacts"] = [
                {
                    "format": a.format,
                    "relative_path": a.relative_path,
                    "size_bytes": a.size_bytes,
                    "sha256": a.sha256,
                }
                for a in r.artifacts
            ]
        if r.status in ("partial", "failed") and r.errors:
            r_dict["errors"] = [
                {
                    "code": e.code,
                    "message": e.message,
                    **({"format": e.format} if e.format else {}),
                }
                for e in r.errors
            ]
        if r.warnings:
            r_dict["warnings"] = [
                {
                    "code": w.code,
                    "message": w.message,
                    **({"format": w.format} if w.format else {}),
                }
                for w in r.warnings
            ]
        results_out.append(r_dict)
    data["results"] = results_out

    if manifest.status == "cancelled" or manifest.cancelled_files:
        data["cancelled_files"] = list(manifest.cancelled_files)

    if manifest.unprocessed_files:
        data["unprocessed_files"] = list(manifest.unprocessed_files)

    if manifest.errors:
        data["errors"] = [
            {
                "code": e.code,
                "message": e.message,
                **({"format": e.format} if e.format else {}),
            }
            for e in manifest.errors
        ]

    if manifest.warnings:
        data["warnings"] = [
            {
                "code": w.code,
                "message": w.message,
                **({"format": w.format} if w.format else {}),
            }
            for w in manifest.warnings
        ]

    data["engine_version"] = manifest.engine_version

    if manifest.cad_runtime_version_build is not None:
        data["cad_runtime_version_build"] = manifest.cad_runtime_version_build

    validator = get_batch_manifest_validator()
    errors = sorted(validator.iter_errors(data), key=lambda e: str(e.path))
    if errors:
        raise BatchValidationError(
            "Projected batch manifest failed schema validation.",
            code="INVALID_SCHEMA",
            request_id=manifest.request_id,
        )
    return data


__all__ = [
    "project_batch_manifest",
    "project_batch_request",
    "project_batch_response",
]
