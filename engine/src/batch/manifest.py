"""Canonical batch manifest assembly.

Translates an execution outcome and validated request into an immutable BatchManifest:
- Revalidates request/outcome identity, version, and output-root intent.
- Traverses per-file results and captures fresh streaming OutputSnapshots of every successful artifact.
- Re-verifies strict containment beneath the output root and rejects symlinks/reparse points.
- Derives portable forward-slash relative paths for manifest artifact records.
- Preserves request file order, format order, and request-relative unprocessed/cancelled paths.
- Assembles and semantically validates typed BatchManifest.
"""

from __future__ import annotations

from pathlib import Path

from batch.execution import BatchExecutionOutcome
from batch.filesystem import (
    assert_strictly_contained,
    is_symlink_or_reparse_point,
)
from batch.models import (
    BatchContractError,
    BatchManifest,
    BatchRequest,
    ManifestArtifactRecord,
    ManifestFileResult,
)
from batch.output_snapshot import capture_output_snapshot
from batch.paths import PORTABLE_PATH_PATTERN
from batch.terminal import validate_batch_manifest_semantics


class ManifestAssemblyError(BatchContractError):
    """Raised when manifest assembly fails due to invalid outcome, missing artifacts, or path violation."""

    def __init__(self, message: str, *, request_id: str = "unknown") -> None:
        super().__init__("MANIFEST_PUBLICATION_FAILED", message, request_id=request_id)


def assemble_batch_manifest(
    request: BatchRequest,
    outcome: BatchExecutionOutcome,
    *,
    engine_version: str,
) -> BatchManifest:
    """Assemble an immutable, semantically validated BatchManifest from request and outcome.

    Args:
        request: Original validated BatchRequest.
        outcome: Terminal BatchExecutionOutcome from batch execution.
        engine_version: Verified installed engine distribution version string.

    Returns:
        Immutable BatchManifest conforming to batch-manifest-v1 schema and semantic invariants.

    Raises:
        ManifestAssemblyError: If outcome is not eligible, identity mismatches, or artifact integrity fails.
    """
    req_id = request.request_id

    # 1. Eligibility: outcome must be progressed (must have a summary and be completed, cancelled, or failed)
    if outcome.status not in ("completed", "cancelled", "failed") or outcome.summary is None:
        raise ManifestAssemblyError(
            f"Outcome status '{outcome.status}' with summary={outcome.summary is not None} is not eligible for manifest assembly",
            request_id=req_id,
        )

    # 2. Identity and version agreement
    if outcome.request_id != request.request_id:
        raise ManifestAssemblyError(
            f"Request ID mismatch: request has '{request.request_id}', outcome has '{outcome.request_id}'",
            request_id=req_id,
        )
    if outcome.contract_version != request.contract_version:
        raise ManifestAssemblyError(
            f"Contract version mismatch: request has '{request.contract_version}', outcome has '{outcome.contract_version}'",
            request_id=req_id,
        )
    if outcome.output_root is None or not outcome.output_root.is_absolute():
        raise ManifestAssemblyError(
            "Outcome output_root must be an absolute Path for manifest assembly",
            request_id=req_id,
        )
    if outcome.output_root != Path(request.output_root):
        raise ManifestAssemblyError(
            "Outcome output_root does not match request output_root",
            request_id=req_id,
        )

    # 3. Engine version validation
    if not isinstance(engine_version, str) or not engine_version.strip() or engine_version != engine_version.strip():
        raise ManifestAssemblyError(
            "engine_version must be a non-empty string without leading/trailing whitespace",
            request_id=req_id,
        )

    output_root = outcome.output_root

    # 4. Request file partition and accounting proof
    if outcome.summary.total != len(request.input.files):
        raise ManifestAssemblyError(
            f"Outcome summary total ({outcome.summary.total}) does not match request file count ({len(request.input.files)})",
            request_id=req_id,
        )

    outcome_partition = (
        tuple(file_res.input for file_res in outcome.file_results)
        + tuple(outcome.unprocessed_files)
        + tuple(outcome.cancelled_files)
    )
    if outcome_partition != tuple(request.input.files):
        raise ManifestAssemblyError(
            "Outcome file partition does not match request input files in identity, count, or order",
            request_id=req_id,
        )

    allowed_formats = frozenset(request.operation.formats)

    # 5. Project file results to manifest file results
    manifest_results: list[ManifestFileResult] = []
    for file_res in outcome.file_results:
        manifest_artifacts: list[ManifestArtifactRecord] = []
        for art in file_res.artifacts:
            if art.format not in allowed_formats:
                raise ManifestAssemblyError(
                    f"Artifact format '{art.format}' is not in requested operation formats",
                    request_id=req_id,
                )
            art_path = Path(art.path)
            if not art_path.is_absolute():
                raise ManifestAssemblyError(
                    f"Artifact path '{art.path}' is not absolute",
                    request_id=req_id,
                )

            # Re-verify strict containment
            try:
                assert_strictly_contained(art_path, output_root, request_id=req_id)
            except Exception as exc:
                raise ManifestAssemblyError(
                    f"Artifact path '{art.path}' is not contained within output root",
                    request_id=req_id,
                ) from exc

            # Must exist, be a regular file, not a symlink or reparse point
            if is_symlink_or_reparse_point(art_path):
                raise ManifestAssemblyError(
                    f"Artifact path '{art.path}' is a symlink or reparse point",
                    request_id=req_id,
                )
            if not art_path.is_file():
                raise ManifestAssemblyError(
                    f"Artifact path '{art.path}' is not a regular file",
                    request_id=req_id,
                )

            # Derive relative path from output root
            try:
                rel_path = art_path.relative_to(output_root).as_posix()
            except ValueError as exc:
                raise ManifestAssemblyError(
                    f"Artifact path '{art.path}' cannot be relativized to output root",
                    request_id=req_id,
                ) from exc

            if not PORTABLE_PATH_PATTERN.fullmatch(rel_path):
                raise ManifestAssemblyError(
                    f"Derived artifact relative path '{rel_path}' does not conform to portable path contract",
                    request_id=req_id,
                )

            # Fresh streaming snapshot
            try:
                snap = capture_output_snapshot(art_path)
            except Exception as exc:
                raise ManifestAssemblyError(
                    f"Failed to capture snapshot for artifact '{art.path}': {exc}",
                    request_id=req_id,
                ) from exc

            manifest_artifacts.append(
                ManifestArtifactRecord(
                    format=art.format,
                    relative_path=rel_path,
                    size_bytes=snap.size_bytes,
                    sha256=snap.sha256,
                )
            )

        manifest_results.append(
            ManifestFileResult(
                input=file_res.input,
                status=file_res.status,
                artifacts=tuple(manifest_artifacts),
                errors=file_res.errors,
                warnings=file_res.warnings,
            )
        )

    # 6. Assemble typed BatchManifest
    try:
        manifest = BatchManifest(
            manifest_version="1.0",
            contract_version=outcome.contract_version,
            request_id=request.request_id,
            status=outcome.status,
            operation=request.operation,
            summary=outcome.summary,
            results=tuple(manifest_results),
            engine_version=engine_version,
            cancelled_files=outcome.cancelled_files,
            unprocessed_files=outcome.unprocessed_files,
            errors=outcome.errors,
            warnings=outcome.warnings,
            cad_runtime_version_build=outcome.cad_runtime_version_build,
        )
    except Exception as exc:
        raise ManifestAssemblyError(
            f"Failed to construct BatchManifest: {exc}",
            request_id=req_id,
        ) from exc

    # 6. Validate semantic contract
    try:
        validate_batch_manifest_semantics(manifest, request_id=req_id)
    except Exception as exc:
        raise ManifestAssemblyError(
            f"Assembled BatchManifest failed semantic validation: {exc}",
            request_id=req_id,
        ) from exc

    return manifest


__all__ = [
    "ManifestAssemblyError",
    "assemble_batch_manifest",
]
