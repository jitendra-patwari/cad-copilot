"""Artifact pipeline orchestration for safe multi-format generation finalization.

Invariants:
    - Vendor Neutrality: inter-operates with CAD kernels strictly via abstract CADExecutorABC.
    - Authoritative M3.2 QA: rejects invalid single-solid topology before any export.
    - Canonical Export Sequence: required model exports (.par -> .step -> .stl) precede best-effort preview (.jpg).
    - Fail-Fast Stat Checks: verifies file existence and non-zero size immediately after each export.
    - Terminal Document Release: closes request document in a try...finally boundary before validation/publication.
    - Closed-File Integrity: multi-format validation and streaming SHA-256 execute strictly on closed files.
    - Atomic Publication: publishes via single directory rename after confirmed validation and closure.
    - Guarded Cleanup: removes staging only when document release is confirmed; never deletes unconfirmed residue.
    - Fail-Closed Redacted Diagnostics: shareable warnings and errors never interpolate untrusted exception strings.
"""

from __future__ import annotations

import contextlib
import dataclasses
import math
import stat
from collections.abc import Mapping
from pathlib import Path

from interfaces.exceptions import (
    CADDocumentError,
    CADError,
    CADExecutionError,
    CADExportError,
)
from interfaces.executor_abc import CADExecutorABC
from interfaces.models import (
    ArtifactFormat,
    ArtifactRecord,
    ExecutionSuccess,
    StandardInspectionReport,
)
from manifests import (
    FREE_TEXT_CREDENTIAL_PATTERN,
    LOCAL_PATH_PATTERN,
    SENSITIVE_KEY_PATTERN,
    ManifestError,
    RunManifestContext,
    assemble_run_manifest,
    write_staged_run_manifest,
)

from .paths import (
    ArtifactPaths,
    assert_contained,
    is_symlink_or_reparse_point,
    prepare_artifact_paths,
)
from .validation import (
    ArtifactValidationError,
    FileSnapshot,
    validate_artifact_file,
)

_REDACTED_DETAILS: str = "<details redacted>"
REQUIRED_MODEL_FORMATS: tuple[ArtifactFormat, ...] = ("par", "step", "stl")

_GENERIC_WARNING_MESSAGE: str = "A non-fatal diagnostic warning was recorded."

_CANONICAL_WARNING_MESSAGES: Mapping[str, str] = {
    # Artifact & Preview warnings
    "PREVIEW_EXPORT_FAILED": "Preview image generation was skipped or unavailable; CAD geometry exported successfully.",
    "PREVIEW_CLEANUP_FAILED": "Temporary preview export file cleanup failed.",
    # Runtime warnings
    "VERSION_METADATA_UNAVAILABLE": "CAD runtime did not report version metadata.",
    # Validation & Gate Policy warnings
    "GATE_POLICY_RELAXED": "Feature validation evaluated under relaxed capability-first policy.",
    "GATE_POLICY_SHADOW": "Feature validation evaluated under shadow gate policy.",
    "CANONICAL_MULTI_HOLE_FAMILY": "Multiple circular through-holes accepted by canonical validation.",
    "UNKNOWN_BOOLEAN_OPERATION": "Unrecognized boolean operation fell back to default union.",
    "UNKNOWN_PLACEMENT_MODE": "Unrecognized placement mode fell back to default absolute placement.",
    "UNKNOWN_FACE_ALIAS": "Unrecognized face alias fell back to default sketch plane.",
    "UNKNOWN_SLOT_ORIENTATION": "Unrecognized slot orientation fell back to default horizontal orientation.",
    "UNKNOWN_SWEEP_PATH_TYPE": "Unrecognized sweep path type fell back to default.",
    "UNKNOWN_SWEEP_SECTION_TYPE": "Unrecognized sweep section type fell back to default.",
    "UNKNOWN_SWEEP_SECTION_POSITION": "Unrecognized sweep section position fell back to default.",
}
_KNOWN_CANONICAL_WARNING_STRINGS: frozenset[str] = frozenset(_CANONICAL_WARNING_MESSAGES.values()) | {
    _GENERIC_WARNING_MESSAGE
}


def _project_preview_warning(warning_obj: object) -> str:
    """Safely project a preview warning into an allowlisted canonical message (fail-closed).

    Unrecognized warning strings, arbitrary mapping message payloads, or any text
    containing local filesystem paths or credentials are strictly mapped to the
    fail-closed generic non-fatal warning message.
    """
    text: str = _GENERIC_WARNING_MESSAGE

    if isinstance(warning_obj, str):
        if warning_obj in _CANONICAL_WARNING_MESSAGES:
            text = _CANONICAL_WARNING_MESSAGES[warning_obj]
        elif warning_obj in _KNOWN_CANONICAL_WARNING_STRINGS:
            text = warning_obj
    elif isinstance(warning_obj, Mapping):
        code_val = warning_obj.get("code") or warning_obj.get("warning")
        if isinstance(code_val, str):
            if code_val in _CANONICAL_WARNING_MESSAGES:
                text = _CANONICAL_WARNING_MESSAGES[code_val]
            elif code_val in _KNOWN_CANONICAL_WARNING_STRINGS:
                text = code_val

    # Clean control characters and normalize whitespace
    cleaned = "".join(ch if (ch.isprintable() or ch in " \t") else " " for ch in text)
    normalized = " ".join(cleaned.split())
    if not normalized:
        return _GENERIC_WARNING_MESSAGE

    # Screening: check for credentials, local paths, or disallowed patterns
    if (
        LOCAL_PATH_PATTERN.search(normalized)
        or FREE_TEXT_CREDENTIAL_PATTERN.search(normalized)
        or SENSITIVE_KEY_PATTERN.search(normalized)
        or "<details redacted>" in normalized
    ):
        return _GENERIC_WARNING_MESSAGE

    return normalized


def _sanitize_diagnostic(error: BaseException, context_msg: str) -> str:
    """Format a fail-closed diagnostic message without evaluating untrusted exception text."""
    return f"{context_msg} -> {type(error).__name__}: {_REDACTED_DETAILS}"


def _verify_inspection_authority(
    inspection_report: StandardInspectionReport | None,
) -> StandardInspectionReport:
    """Validate that the M3.2 execution produced an authoritative, single-solid inspection report."""
    if inspection_report is None:
        raise CADExecutionError(
            "Native inspection report missing from execution result",
            error_code="NATIVE_QA_BLOCKED",
        )

    if inspection_report.body_count != 1:
        raise CADExecutionError(
            f"Native inspection report requires exactly 1 total body, got {inspection_report.body_count}",
            error_code="NATIVE_QA_BLOCKED",
        )

    if inspection_report.solid_body_count != 1:
        raise CADExecutionError(
            f"Native inspection report requires exactly 1 solid body, got {inspection_report.solid_body_count}",
            error_code="NATIVE_QA_BLOCKED",
        )

    if (inspection_report.sheet_body_count is not None and inspection_report.sheet_body_count != 0) or (
        inspection_report.wire_body_count is not None and inspection_report.wire_body_count != 0
    ):
        raise CADExecutionError(
            f"Native inspection report contains non-solid bodies "
            f"(sheet={inspection_report.sheet_body_count}, wire={inspection_report.wire_body_count})",
            error_code="NATIVE_QA_BLOCKED",
        )

    if not math.isfinite(inspection_report.volume_mm3) or inspection_report.volume_mm3 <= 0:
        raise CADExecutionError(
            f"Native inspection report has invalid volume ({inspection_report.volume_mm3} mm3)",
            error_code="NATIVE_QA_BLOCKED",
        )

    return inspection_report


def _ensure_staging_jpg_absent(paths: ArtifactPaths, cleanup_exc: Exception | None = None) -> None:
    """Safely verify that staging JPG is absent after capture or validation rejection.

    If deletion reports failure or exception, determine safely whether canonical staging
    JPG still exists. If absent, retain sanitized cleanup warning and allow required
    artifacts to proceed. If JPG still exists or its state cannot be verified, raise a
    fatal normalized CADExportError (ARTIFACT_EXPORT_FAILED) so publication is blocked.
    """
    try:
        exists = paths.staging_jpg.exists()
    except OSError as stat_exc:
        raise CADExportError(
            "Cannot verify absence of rejected preview in staging",
            error_code="ARTIFACT_EXPORT_FAILED",
            details={"reason": _sanitize_diagnostic(stat_exc, "Staging JPG status check failed")},
        ) from None

    if exists:
        details_map: dict[str, str] = {}
        if cleanup_exc is not None:
            details_map["reason"] = _sanitize_diagnostic(cleanup_exc, "Partial preview cleanup failed")
        else:
            details_map["reason"] = "Rejected preview file still exists in staging"
        raise CADExportError(
            "Rejected preview file could not be safely removed from staging",
            error_code="ARTIFACT_EXPORT_FAILED",
            details=details_map,
        )


def _verify_pre_publication_inventory(
    paths: ArtifactPaths,
    request_id: str,
    accepted_snapshots: dict[str, FileSnapshot],
    expect_jpg: bool,
    *,
    expect_manifest: bool = False,
) -> None:
    """Enforce exact accepted inventory, containment, and file stability immediately before directory publication."""
    expected_filenames = {f"{request_id}.par", f"{request_id}.step", f"{request_id}.stl"}
    if expect_jpg:
        expected_filenames.add(f"{request_id}.jpg")
    if expect_manifest:
        expected_filenames.add(paths.staging_manifest.name)

    try:
        entries = list(paths.staging_dir.iterdir())
    except OSError as scan_exc:
        raise CADExportError(
            "Failed to enumerate staging directory inventory prior to publication",
            error_code="ARTIFACT_EXPORT_FAILED",
            details={"reason": _sanitize_diagnostic(scan_exc, "Staging scan failed")},
        ) from None

    seen_names: set[str] = set()
    for entry in entries:
        seen_names.add(entry.name)

        if entry.name not in expected_filenames:
            raise CADExportError(
                f"Unexpected entry in staging directory prior to publication: '{entry.name}'",
                error_code="ARTIFACT_EXPORT_FAILED",
            )

        if is_symlink_or_reparse_point(entry):
            raise CADExportError(
                f"Staging inventory entry is a symbolic link or reparse point: '{entry.name}'",
                error_code="ARTIFACT_EXPORT_FAILED",
            )

        if not entry.is_file():
            raise CADExportError(
                f"Staging inventory entry is not a regular file: '{entry.name}'",
                error_code="ARTIFACT_EXPORT_FAILED",
            )

        assert_contained(entry, paths.staging_dir)

        # Re-verify lightweight file stability snapshot
        expected_snap = accepted_snapshots.get(entry.name)
        if expected_snap is None:
            raise CADExportError(
                f"Unregistered staging artifact in accepted inventory: '{entry.name}'",
                error_code="ARTIFACT_EXPORT_FAILED",
            )

        current_snap = FileSnapshot.capture(entry)
        if current_snap != expected_snap:
            raise CADExportError(
                f"Artifact file modified prior to publication: '{entry.name}'",
                error_code="ARTIFACT_EXPORT_FAILED",
            )

    if seen_names != expected_filenames:
        missing = expected_filenames - seen_names
        raise CADExportError(
            f"Staging inventory missing expected artifacts prior to publication: {sorted(missing)}",
            error_code="ARTIFACT_EXPORT_FAILED",
        )


def finalize_request_artifacts(
    executor: CADExecutorABC,
    success_result: ExecutionSuccess,
    output_root: Path | str,
    request_id: str,
    *,
    manifest_context: RunManifestContext | None = None,
) -> ExecutionSuccess:
    """Finalize, validate, hash, and publish generation artifacts for a completed request.

    Args:
        executor: The request-bound CAD executor possessing the active request document.
        success_result: Successful execution result payload from Milestone 3.2.
        output_root: Configured root directory beneath which artifacts will be published.
        request_id: Validated canonical request identifier.
        manifest_context: Optional request-local manifest context. When supplied, assembles,
            validates, and publishes run_manifest.json as a required transaction artifact.

    Returns:
        ExecutionSuccess: Populated result with typed ArtifactRecord entries, preserved
        inspection report, and combined execution and preview warnings.

    Raises:
        CADExecutionError: If inspection authority is missing or invalid (NATIVE_QA_BLOCKED).
        ArtifactPathError: If request ID or paths violate containment, security, or collision rules.
        CADExportError: If any required model export, manifest, or validation fails (ARTIFACT_EXPORT_FAILED).
        CADDocumentError: If document release or runtime health fails.
    """
    primary_exc: BaseException | None = None
    document_closed: bool = False
    staging_allocated: bool = False
    paths: ArtifactPaths | None = None

    preview_captured: bool = False
    preview_warnings: list[dict[str, str]] = []

    # Phase 1: Preflight & Staging Allocation + Model & Preview Exports
    # Managed inside a try block with terminal executor.close_request_document() in finally.
    try:
        # 1.1 Verify authoritative M3.2 inspection result
        inspection_report = _verify_inspection_authority(success_result.inspection_report)

        # 1.2 Validate paths, assert containment, check collisions, allocate exclusive staging
        paths = prepare_artifact_paths(output_root, request_id)
        staging_allocated = True

        # 1.3 Export required model formats in fixed canonical sequence (.par -> .step -> .stl)
        for fmt in REQUIRED_MODEL_FORMATS:
            target_staging = paths.get_staging_path(fmt)
            executor.export_model(fmt, target_staging)

            # Fail-fast check on exported regular file with guarded stat
            try:
                st = target_staging.stat()
                if not stat.S_ISREG(st.st_mode) or st.st_size <= 0:
                    raise CADExportError(
                        f"Export of {fmt.upper()} artifact produced missing or empty file",
                        error_code="ARTIFACT_EXPORT_FAILED",
                    )
            except OSError as stat_exc:
                raise CADExportError(
                    f"Export of {fmt.upper()} artifact produced inaccessible file",
                    error_code="ARTIFACT_EXPORT_FAILED",
                    details={"format": fmt, "reason": _sanitize_diagnostic(stat_exc, "Filesystem stat failure")},
                ) from None

        # 1.4 Export optional preview image (.jpg) attempted last among COM operations
        jpg_staging = paths.get_staging_path("jpg")
        try:
            executor.capture_preview(jpg_staging)
            preview_captured = True
        except CADExportError as preview_exc:
            if getattr(preview_exc, "error_code", None) == "PREVIEW_EXPORT_FAILED":
                # Localized preview capture failure is non-fatal when document/runtime remains healthy
                preview_captured = False
                preview_warnings.append(
                    {
                        "code": "PREVIEW_EXPORT_FAILED",
                        "warning": "PREVIEW_EXPORT_FAILED",
                        "message": _sanitize_diagnostic(preview_exc, "Preview capture failed"),
                    }
                )
            else:
                # Security, path containment, or other non-localized export errors must remain fatal
                raise

    except BaseException as exc:
        primary_exc = exc
    finally:
        # Phase 2: Guaranteed Terminal Document Release
        try:
            executor.close_request_document()
            document_closed = True
        except BaseException as close_exc:
            document_closed = False
            # Normalize terminal close exception if not already a CADError
            if isinstance(close_exc, CADError):
                normalized_close: BaseException = close_exc
            else:
                normalized_close = CADDocumentError(
                    _sanitize_diagnostic(close_exc, "Terminal document release failed"),
                    error_code="DOCUMENT_CLOSE_FAILED",
                )

            if primary_exc is None:
                primary_exc = normalized_close
            else:
                # Normalize primary exception if non-CADError before attaching details
                if not isinstance(primary_exc, CADError) and isinstance(primary_exc, Exception):
                    primary_exc = CADExecutionError(
                        _sanitize_diagnostic(primary_exc, "Pipeline failure during export"),
                        error_code="CAD_EXECUTION_FAILED",
                    )
                # Primary error occurred during preflight/export; attach secondary close failure
                if isinstance(primary_exc, CADError):
                    primary_exc.details["secondary_close_error"] = _sanitize_diagnostic(
                        close_exc, "Document release failed during error handling"
                    )

    # Phase 3: Failure Handling & Staging Cleanup Guard
    if primary_exc is not None:
        if isinstance(primary_exc, Exception) and not isinstance(primary_exc, CADError):
            primary_exc = CADExecutionError(
                _sanitize_diagnostic(primary_exc, "Pipeline failure during export"),
                error_code="CAD_EXECUTION_FAILED",
            )
        if staging_allocated and paths is not None:
            if document_closed:
                try:
                    paths.cleanup_staging(document_closed=True)
                except Exception as cleanup_exc:
                    if isinstance(primary_exc, CADError):
                        primary_exc.details["cleanup_error"] = _sanitize_diagnostic(
                            cleanup_exc, "Staging cleanup failed"
                        )
                        primary_exc.details["retained_staging"] = True
            else:
                if isinstance(primary_exc, CADError):
                    primary_exc.details["retained_staging"] = True
        raise primary_exc

    # Beyond this point, document closure is confirmed and primary_exc is None
    assert paths is not None
    assert inspection_report is not None

    # Phase 4: Post-Release Multi-Format Validation & File Stability Binding
    try:
        # Clean up known auxiliary exporter translation log (<request_id>.log) generated by
        # Solid Edge's translator so staging contains only canonical model artifacts.
        aux_log = paths.staging_dir / f"{request_id}.log"
        if aux_log.is_file():
            with contextlib.suppress(OSError):
                aux_log.unlink(missing_ok=True)

        accepted_snapshots: dict[str, FileSnapshot] = {}

        # 4.1 Validate required native Part (.par)
        par_res = validate_artifact_file("par", paths.get_staging_path("par"), inspection_report)
        par_sha, par_size = par_res[0], par_res[1]
        accepted_snapshots[paths.staging_par.name] = getattr(par_res, "snapshot", None) or FileSnapshot.capture(
            paths.staging_par
        )

        # 4.2 Validate required exchange STEP (.step)
        step_res = validate_artifact_file("step", paths.get_staging_path("step"))
        step_sha, step_size = step_res[0], step_res[1]
        accepted_snapshots[paths.staging_step.name] = getattr(step_res, "snapshot", None) or FileSnapshot.capture(
            paths.staging_step
        )

        # 4.3 Validate required mesh STL (.stl)
        stl_res = validate_artifact_file("stl", paths.get_staging_path("stl"))
        stl_sha, stl_size = stl_res[0], stl_res[1]
        accepted_snapshots[paths.staging_stl.name] = getattr(stl_res, "snapshot", None) or FileSnapshot.capture(
            paths.staging_stl
        )

        # 4.4 Process preview image (.jpg)
        jpg_record: ArtifactRecord | None = None
        if not preview_captured:
            # Ensure any 0-byte or partial staging preview file is safely unlinked after document close
            cleanup_err: Exception | None = None
            try:
                paths.cleanup_partial_preview(document_closed=True)
            except Exception as cleanup_exc:
                cleanup_err = cleanup_exc

            _ensure_staging_jpg_absent(paths, cleanup_err)
            if cleanup_err is not None:
                preview_warnings.append(
                    {
                        "code": "PREVIEW_CLEANUP_FAILED",
                        "warning": "PREVIEW_CLEANUP_FAILED",
                        "message": _sanitize_diagnostic(cleanup_err, "Partial preview cleanup failed"),
                    }
                )
        else:
            try:
                jpg_res = validate_artifact_file("jpg", paths.get_staging_path("jpg"))
                jpg_sha, jpg_size = jpg_res[0], jpg_res[1]
                jpg_record = ArtifactRecord(
                    type="preview_image",
                    format="jpg",
                    path=str(paths.final_jpg),
                    origin="cad_copilot",
                    size_bytes=jpg_size,
                    sha256=jpg_sha,
                )
                accepted_snapshots[paths.staging_jpg.name] = getattr(jpg_res, "snapshot", None) or FileSnapshot.capture(
                    paths.staging_jpg
                )
            except (ArtifactValidationError, CADExportError) as preview_val_exc:
                # Localized validation failure: cleanup partial staging jpg and convert to preview warning
                cleanup_err = None
                try:
                    paths.cleanup_partial_preview(document_closed=True)
                except Exception as cleanup_exc:
                    cleanup_err = cleanup_exc

                _ensure_staging_jpg_absent(paths, cleanup_err)
                if cleanup_err is not None:
                    preview_warnings.append(
                        {
                            "code": "PREVIEW_CLEANUP_FAILED",
                            "warning": "PREVIEW_CLEANUP_FAILED",
                            "message": _sanitize_diagnostic(
                                cleanup_err, "Partial preview cleanup failed after validation"
                            ),
                        }
                    )
                preview_warnings.append(
                    {
                        "code": "PREVIEW_EXPORT_FAILED",
                        "warning": "PREVIEW_EXPORT_FAILED",
                        "message": _sanitize_diagnostic(preview_val_exc, "Preview validation failed"),
                    }
                )
                jpg_record = None

        # Assemble records using pre-verified canonical final paths before atomic publication
        records: list[ArtifactRecord] = [
            ArtifactRecord(
                type="native_part",
                format="par",
                path=str(paths.final_par),
                origin="cad_copilot",
                size_bytes=par_size,
                sha256=par_sha,
            ),
            ArtifactRecord(
                type="geometry_step",
                format="step",
                path=str(paths.final_step),
                origin="cad_copilot",
                size_bytes=step_size,
                sha256=step_sha,
            ),
            ArtifactRecord(
                type="mesh_stl",
                format="stl",
                path=str(paths.final_stl),
                origin="cad_copilot",
                size_bytes=stl_size,
                sha256=stl_sha,
            ),
        ]
        if jpg_record is not None:
            records.append(jpg_record)

        # Phase 5: Run manifest assembly, validation, and staging snapshot (if context provided)
        if manifest_context is not None:
            if not isinstance(manifest_context, RunManifestContext):
                raise CADExportError(
                    "manifest_context must be an instance of RunManifestContext",
                    error_code="ARTIFACT_EXPORT_FAILED",
                )

            # 5.1 Project only finalization-created preview warnings
            projected_preview_warnings = [_project_preview_warning(w) for w in preview_warnings]
            clean_preview_warnings = [w for w in projected_preview_warnings if w is not None]

            # 5.2 Deduplicate combined pre-finalization warnings and finalization preview warnings
            combined_manifest_warnings = tuple(dict.fromkeys(list(manifest_context.warnings) + clean_preview_warnings))
            updated_context = dataclasses.replace(
                manifest_context,
                warnings=combined_manifest_warnings,
            )

            # 5.3 Assemble manifest and write exclusively in staging with read-back schema validation
            try:
                manifest_data = assemble_run_manifest(
                    context=updated_context,
                    execution=success_result,
                    artifacts=records,
                )
                staging_manifest_path = paths.get_manifest_staging_path()
                write_staged_run_manifest(staging_manifest_path, manifest_data)
            except ManifestError as manifest_exc:
                raise CADExportError(
                    "Failed to assemble or publish run manifest",
                    error_code="ARTIFACT_EXPORT_FAILED",
                    details={"reason": _sanitize_diagnostic(manifest_exc, "Manifest generation failed")},
                ) from None

            # 5.4 Capture snapshot of staged manifest for exact pre-publication inventory
            accepted_snapshots[paths.staging_manifest.name] = FileSnapshot.capture(staging_manifest_path)

        # Phase 6: Enforce exact accepted inventory and file stability before atomic publication
        _verify_pre_publication_inventory(
            paths=paths,
            request_id=request_id,
            accepted_snapshots=accepted_snapshots,
            expect_jpg=(jpg_record is not None),
            expect_manifest=(manifest_context is not None),
        )

        # Phase 7: Atomic Directory Publication — the final fallible boundary
        paths.publish()

    except BaseException as post_exc:
        # On post-close validation or publication failure, staging cleanup is safe since document_closed=True
        if isinstance(post_exc, Exception) and not isinstance(post_exc, CADError):
            post_exc = CADExecutionError(
                _sanitize_diagnostic(post_exc, "Post-release processing failed"),
                error_code="CAD_EXECUTION_FAILED",
            )
        if paths is not None:
            try:
                paths.cleanup_staging(document_closed=True)
            except Exception as cleanup_exc:
                if isinstance(post_exc, CADError):
                    post_exc.details["cleanup_error"] = _sanitize_diagnostic(
                        cleanup_exc, "Staging cleanup failed after post-release failure"
                    )
                    post_exc.details["retained_staging"] = True
        raise post_exc

    combined_warnings = list(success_result.warnings or []) + preview_warnings

    return ExecutionSuccess(
        operations_executed=success_result.operations_executed,
        exported_artifacts=records,
        warnings=combined_warnings,
        operation_results=list(success_result.operation_results or []),
        inspection_report=inspection_report,
    )


__all__ = [
    "REQUIRED_MODEL_FORMATS",
    "finalize_request_artifacts",
]
