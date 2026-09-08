"""Parsing and mapping conversion from JSON payloads to validated typed models."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from batch.accounting import validate_summary_accounting
from batch.models import (
    APPROVED_DIAGNOSTIC_CODES,
    APPROVED_WARNING_CODES,
    BatchArtifactRecord,
    BatchDiagnostic,
    BatchFileResult,
    BatchInputSelection,
    BatchManifest,
    BatchManifestReference,
    BatchMetadata,
    BatchOperation,
    BatchOperationType,
    BatchOptions,
    BatchRequest,
    BatchResponse,
    BatchSummary,
    BatchTerminalStatus,
    BatchValidationError,
    ManifestArtifactRecord,
    ManifestFileResult,
)
from batch.paths import validate_and_canonicalize_files
from batch.schemas import (
    get_batch_manifest_validator,
    get_batch_request_validator,
    get_batch_response_validator,
    safe_request_id,
)


def _parse_diagnostic(data: Mapping[str, Any], *, request_id: str, is_warning: bool = False) -> BatchDiagnostic:
    """Parse a single diagnostic (error or warning) record."""
    code = data.get("code")
    message = data.get("message")
    fmt = data.get("format")

    if not isinstance(code, str) or code not in APPROVED_DIAGNOSTIC_CODES:
        raise BatchValidationError(
            f"Invalid diagnostic code: {code!r}",
            code="INVALID_SCHEMA",
            request_id=request_id,
        )
    if is_warning and code not in APPROVED_WARNING_CODES:
        raise BatchValidationError(
            f"Diagnostic code '{code}' is not an approved warning code",
            code="INVALID_SCHEMA",
            request_id=request_id,
        )
    if not is_warning and code in APPROVED_WARNING_CODES:
        raise BatchValidationError(
            f"Warning code '{code}' is not permitted as an error",
            code="INVALID_SCHEMA",
            request_id=request_id,
        )
    if not isinstance(message, str) or not (1 <= len(message) <= 512):
        raise BatchValidationError(
            "Diagnostic message must be 1..512 characters",
            code="INVALID_SCHEMA",
            request_id=request_id,
        )
    if fmt is not None and fmt not in ("step", "stl", "pdf", "dxf"):
        raise BatchValidationError(
            f"Invalid diagnostic format: {fmt!r}",
            code="INVALID_SCHEMA",
            request_id=request_id,
        )

    return BatchDiagnostic(code=code, message=message, format=fmt)


def _parse_summary(data: Mapping[str, Any], *, request_id: str) -> BatchSummary:
    """Parse a summary record with strict integer type checking."""
    fields = ("total", "accepted", "partial", "failed", "unprocessed", "cancelled")
    vals: dict[str, int] = {}
    for name in fields:
        val = data.get(name)
        if type(val) is not int:
            raise BatchValidationError(
                f"Summary field '{name}' must be an integer, got {type(val).__name__}",
                code="INVALID_SCHEMA",
                request_id=request_id,
            )
        if val < 0:
            raise BatchValidationError(
                f"Summary field '{name}' must be non-negative, got {val}",
                code="INVALID_SCHEMA",
                request_id=request_id,
            )
        vals[name] = val
    try:
        return BatchSummary(**vals)
    except Exception as exc:
        raise BatchValidationError(str(exc), code="INVALID_SCHEMA", request_id=request_id) from exc


def parse_batch_request(payload: Mapping[str, Any]) -> BatchRequest:
    """Parse and validate a batch request mapping into a typed BatchRequest."""
    req_id = safe_request_id(payload)

    validator = get_batch_request_validator()
    errors = sorted(validator.iter_errors(payload), key=lambda e: str(e.path))
    if errors:
        raise BatchValidationError(
            "Batch request failed schema validation.",
            code="INVALID_SCHEMA",
            request_id=req_id,
        )

    op_raw = payload["operation"]
    op_type: BatchOperationType = op_raw["type"]
    formats = tuple(op_raw["formats"])
    operation = BatchOperation(type=op_type, formats=formats)

    options_raw = payload.get("options")
    if isinstance(options_raw, Mapping):
        cont = options_raw.get("continue_on_error", True)
        max_f = options_raw.get("max_files", 100)
        if type(cont) is not bool:
            raise BatchValidationError(
                "options.continue_on_error must be a boolean",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        if type(max_f) is not int:
            raise BatchValidationError(
                "options.max_files must be an integer",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        options = BatchOptions(continue_on_error=cont, max_files=max_f)
    else:
        options = BatchOptions()

    inp_raw = payload["input"]
    canonical_files = validate_and_canonicalize_files(inp_raw["files"], op_type, request_id=req_id)
    if len(canonical_files) > options.max_files:
        raise BatchValidationError(
            f"Requested file count ({len(canonical_files)}) exceeds options.max_files ({options.max_files})",
            code="INVALID_SCHEMA",
            request_id=req_id,
        )

    metadata: BatchMetadata | None = None
    if "metadata" in payload:
        meta_raw = payload["metadata"]
        if isinstance(meta_raw, Mapping):
            metadata = BatchMetadata(
                source=meta_raw.get("source"),
                label=meta_raw.get("label"),
                job_id=meta_raw.get("job_id"),
            )

    return BatchRequest(
        contract_version=payload["contract_version"],
        request_id=payload["request_id"],
        kind=payload["kind"],
        input=BatchInputSelection(root=inp_raw["root"], files=canonical_files),
        output_root=payload["output_root"],
        operation=operation,
        options=options,
        metadata=metadata,
    )


def parse_batch_response(payload: Mapping[str, Any]) -> BatchResponse:
    """Parse and validate a batch response mapping into a typed BatchResponse."""
    req_id = safe_request_id(payload)

    validator = get_batch_response_validator()
    errors = sorted(validator.iter_errors(payload), key=lambda e: str(e.path))
    if errors:
        raise BatchValidationError(
            "Batch response failed schema validation.",
            code="INVALID_SCHEMA",
            request_id=req_id,
        )

    status: BatchTerminalStatus = payload["status"]
    contract_version = payload["contract_version"]
    request_id = payload["request_id"]

    # Top-level errors
    top_errors: list[BatchDiagnostic] = []
    if "errors" in payload:
        for item in payload["errors"]:
            top_errors.append(_parse_diagnostic(item, request_id=req_id, is_warning=False))

    # Top-level warnings
    top_warnings: list[BatchDiagnostic] = []
    if "warnings" in payload:
        for item in payload["warnings"]:
            top_warnings.append(_parse_diagnostic(item, request_id=req_id, is_warning=True))

    # Terminal state checks
    if status == "rejected":
        if not top_errors:
            raise BatchValidationError(
                "Rejected response must contain errors",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        return BatchResponse(
            contract_version=contract_version,
            request_id=request_id,
            status=status,
            errors=tuple(top_errors),
        )

    # Early failure check (failed without summary)
    if status == "failed" and "summary" not in payload:
        if not top_errors:
            raise BatchValidationError(
                "Early failed response must contain errors",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        return BatchResponse(
            contract_version=contract_version,
            request_id=request_id,
            status=status,
            errors=tuple(top_errors),
            warnings=tuple(top_warnings),
        )

    # Responses with execution results (completed, cancelled, or progressed failed)
    summary = _parse_summary(payload["summary"], request_id=req_id)

    results_list: list[BatchFileResult] = []
    for r_raw in payload["results"]:
        artifacts: list[BatchArtifactRecord] = []
        if "artifacts" in r_raw:
            for art in r_raw["artifacts"]:
                artifacts.append(BatchArtifactRecord(format=art["format"], path=art["path"]))
        errs: list[BatchDiagnostic] = []
        if "errors" in r_raw:
            for e in r_raw["errors"]:
                errs.append(_parse_diagnostic(e, request_id=req_id, is_warning=False))
        warns: list[BatchDiagnostic] = []
        if "warnings" in r_raw:
            for w in r_raw["warnings"]:
                warns.append(_parse_diagnostic(w, request_id=req_id, is_warning=True))
        results_list.append(
            BatchFileResult(
                input=r_raw["input"],
                status=r_raw["status"],
                artifacts=tuple(artifacts),
                errors=tuple(errs),
                warnings=tuple(warns),
            )
        )

    unprocessed_files = tuple(payload.get("unprocessed_files", ()))
    cancelled_files = tuple(payload.get("cancelled_files", ()))

    validate_summary_accounting(summary, results_list, unprocessed_files, cancelled_files, request_id=req_id)

    manifest_ref: BatchManifestReference | None = None
    if "manifest" in payload:
        manifest_ref = BatchManifestReference(path=payload["manifest"]["path"])

    if status == "completed":
        if manifest_ref is None:
            raise BatchValidationError(
                "Completed response requires manifest",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        if cancelled_files:
            raise BatchValidationError(
                "Completed response must not have cancelled_files",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        if top_errors:
            raise BatchValidationError(
                "Completed response must not have errors",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
    elif status == "cancelled":
        if manifest_ref is None:
            raise BatchValidationError(
                "Cancelled response requires manifest",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        if not cancelled_files:
            raise BatchValidationError(
                "Cancelled response requires cancelled_files",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        if top_errors:
            raise BatchValidationError(
                "Cancelled response must not have errors",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
    elif status == "failed":
        if not top_errors:
            raise BatchValidationError(
                "Progressed failed response requires errors",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
        if cancelled_files:
            raise BatchValidationError(
                "Progressed failed response must not have cancelled_files",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )

    return BatchResponse(
        contract_version=contract_version,
        request_id=request_id,
        status=status,
        summary=summary,
        results=tuple(results_list),
        manifest=manifest_ref,
        unprocessed_files=unprocessed_files,
        cancelled_files=cancelled_files,
        errors=tuple(top_errors),
        warnings=tuple(top_warnings),
    )


def parse_batch_manifest(payload: Mapping[str, Any]) -> BatchManifest:
    """Parse and validate a batch manifest mapping into a typed BatchManifest."""
    req_id = safe_request_id(payload)

    validator = get_batch_manifest_validator()
    errors = sorted(validator.iter_errors(payload), key=lambda e: str(e.path))
    if errors:
        raise BatchValidationError(
            "Batch manifest failed schema validation.",
            code="INVALID_SCHEMA",
            request_id=req_id,
        )

    op_raw = payload["operation"]
    operation = BatchOperation(type=op_raw["type"], formats=tuple(op_raw["formats"]))
    summary = _parse_summary(payload["summary"], request_id=req_id)

    results_list: list[ManifestFileResult] = []
    for r_raw in payload["results"]:
        artifacts: list[ManifestArtifactRecord] = []
        if "artifacts" in r_raw:
            for art in r_raw["artifacts"]:
                size = art["size_bytes"]
                if type(size) is not int or size <= 0:
                    raise BatchValidationError(
                        "size_bytes must be positive int",
                        code="INVALID_SCHEMA",
                        request_id=req_id,
                    )
                artifacts.append(
                    ManifestArtifactRecord(
                        format=art["format"],
                        relative_path=art["relative_path"],
                        size_bytes=size,
                        sha256=art["sha256"],
                    )
                )
        errs: list[BatchDiagnostic] = []
        if "errors" in r_raw:
            for e in r_raw["errors"]:
                errs.append(_parse_diagnostic(e, request_id=req_id, is_warning=False))
        warns: list[BatchDiagnostic] = []
        if "warnings" in r_raw:
            for w in r_raw["warnings"]:
                warns.append(_parse_diagnostic(w, request_id=req_id, is_warning=True))
        results_list.append(
            ManifestFileResult(
                input=r_raw["input"],
                status=r_raw["status"],
                artifacts=tuple(artifacts),
                errors=tuple(errs),
                warnings=tuple(warns),
            )
        )

    unprocessed_files = tuple(payload.get("unprocessed_files", ()))
    cancelled_files = tuple(payload.get("cancelled_files", ()))

    validate_summary_accounting(summary, results_list, unprocessed_files, cancelled_files, request_id=req_id)

    top_errors: list[BatchDiagnostic] = []
    if "errors" in payload:
        for e in payload["errors"]:
            top_errors.append(_parse_diagnostic(e, request_id=req_id, is_warning=False))

    top_warnings: list[BatchDiagnostic] = []
    if "warnings" in payload:
        for w in payload["warnings"]:
            top_warnings.append(_parse_diagnostic(w, request_id=req_id, is_warning=True))

    return BatchManifest(
        manifest_version=payload["manifest_version"],
        contract_version=payload["contract_version"],
        request_id=payload["request_id"],
        status=payload["status"],
        operation=operation,
        summary=summary,
        results=tuple(results_list),
        engine_version=payload["engine_version"],
        cancelled_files=cancelled_files,
        unprocessed_files=unprocessed_files,
        errors=tuple(top_errors),
        warnings=tuple(top_warnings),
        cad_runtime_version_build=payload.get("cad_runtime_version_build"),
    )


__all__ = [
    "parse_batch_manifest",
    "parse_batch_request",
    "parse_batch_response",
]
