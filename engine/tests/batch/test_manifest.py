"""Tests for canonical batch manifest assembly."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from batch.execution import BatchExecutionOutcome
from batch.manifest import (
    ManifestAssemblyError,
    assemble_batch_manifest,
)
from batch.models import (
    BatchArtifactRecord,
    BatchDiagnostic,
    BatchFileResult,
    BatchInputSelection,
    BatchManifest,
    BatchOperation,
    BatchOptions,
    BatchOutputFormat,
    BatchRequest,
    BatchSummary,
)


def _make_request(
    tmp_path: Path,
    *,
    request_id: str = "req-test-01",
    files: tuple[str, ...] = ("part_a.par",),
    formats: tuple[BatchOutputFormat, ...] = ("step", "stl"),
) -> BatchRequest:
    in_root = tmp_path / "in"
    out_root = tmp_path / "out"
    in_root.mkdir(parents=True, exist_ok=True)
    out_root.mkdir(parents=True, exist_ok=True)

    for f in files:
        src = in_root / f
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b"FAKE_SRC_CONTENT")

    return BatchRequest(
        contract_version="1.0",
        request_id=request_id,
        kind="batch_operation",
        input=BatchInputSelection(root=str(in_root), files=files),
        output_root=str(out_root),
        operation=BatchOperation(type="export_3d", formats=formats),
        options=BatchOptions(continue_on_error=True),
    )


# ---------------------------------------------------------------------------
# 1. Successful Manifest Assembly
# ---------------------------------------------------------------------------


def test_assemble_batch_manifest_completed_outcome(tmp_path: Path) -> None:
    """Proves successful assembly for a completed batch outcome with verified disk artifacts."""
    req = _make_request(tmp_path, files=("sub/part_a.par", "part_b.psm"), formats=("step", "stl"))
    out_root = Path(req.output_root)

    # Create expected artifacts under out_root
    art_a_step = out_root / "sub" / "part_a.step"
    art_a_stl = out_root / "sub" / "part_a.stl"
    art_b_step = out_root / "part_b.step"
    art_b_stl = out_root / "part_b.stl"

    for art, content in (
        (art_a_step, b"STEP_DATA_A"),
        (art_a_stl, b"STL_DATA_A"),
        (art_b_step, b"STEP_DATA_B"),
        (art_b_stl, b"STL_DATA_B"),
    ):
        art.parent.mkdir(parents=True, exist_ok=True)
        art.write_bytes(content)

    outcome = BatchExecutionOutcome(
        status="completed",
        request_id=req.request_id,
        contract_version="1.0",
        summary=BatchSummary(total=2, accepted=2, partial=0, failed=0, unprocessed=0, cancelled=0),
        file_results=(
            BatchFileResult(
                input="sub/part_a.par",
                status="accepted",
                artifacts=(
                    BatchArtifactRecord(format="step", path=str(art_a_step)),
                    BatchArtifactRecord(format="stl", path=str(art_a_stl)),
                ),
            ),
            BatchFileResult(
                input="part_b.psm",
                status="accepted",
                artifacts=(
                    BatchArtifactRecord(format="step", path=str(art_b_step)),
                    BatchArtifactRecord(format="stl", path=str(art_b_stl)),
                ),
            ),
        ),
        output_root=out_root,
        cad_runtime_version_build="226.00.00.106",
    )

    manifest = assemble_batch_manifest(req, outcome, engine_version="1.0.0")

    assert isinstance(manifest, BatchManifest)
    assert manifest.manifest_version == "1.0"
    assert manifest.contract_version == "1.0"
    assert manifest.request_id == req.request_id
    assert manifest.status == "completed"
    assert manifest.engine_version == "1.0.0"
    assert manifest.cad_runtime_version_build == "226.00.00.106"
    assert manifest.summary == outcome.summary
    assert len(manifest.results) == 2

    # Check first result artifacts
    res_a = manifest.results[0]
    assert res_a.input == "sub/part_a.par"
    assert res_a.status == "accepted"
    assert len(res_a.artifacts) == 2
    assert res_a.artifacts[0].format == "step"
    assert res_a.artifacts[0].relative_path == "sub/part_a.step"
    assert res_a.artifacts[0].size_bytes == len(b"STEP_DATA_A")
    assert res_a.artifacts[0].sha256 == hashlib.sha256(b"STEP_DATA_A").hexdigest()

    # Check second result artifacts
    res_b = manifest.results[1]
    assert res_b.input == "part_b.psm"
    assert res_b.artifacts[1].format == "stl"
    assert res_b.artifacts[1].relative_path == "part_b.stl"
    assert res_b.artifacts[1].size_bytes == len(b"STL_DATA_B")
    assert res_b.artifacts[1].sha256 == hashlib.sha256(b"STL_DATA_B").hexdigest()


def test_assemble_batch_manifest_cancelled_before_files(tmp_path: Path) -> None:
    """Proves manifest assembly for batch cancelled before file execution started."""
    req = _make_request(tmp_path, files=("f1.par", "f2.par"), formats=("step",))
    out_root = Path(req.output_root)

    outcome = BatchExecutionOutcome(
        status="cancelled",
        request_id=req.request_id,
        contract_version="1.0",
        summary=BatchSummary(total=2, accepted=0, partial=0, failed=0, unprocessed=0, cancelled=2),
        file_results=(),
        cancelled_files=("f1.par", "f2.par"),
        output_root=out_root,
    )

    manifest = assemble_batch_manifest(req, outcome, engine_version="1.0.0")

    assert manifest.status == "cancelled"
    assert manifest.cancelled_files == ("f1.par", "f2.par")
    assert manifest.results == ()
    assert manifest.summary.cancelled == 2


def test_assemble_batch_manifest_cancelled_mid_file(tmp_path: Path) -> None:
    """Proves manifest assembly for batch cancelled mid-file with partial attribution."""
    req = _make_request(tmp_path, files=("f1.par", "f2.par"), formats=("step", "stl"))
    out_root = Path(req.output_root)

    art_step = out_root / "f1.step"
    art_step.write_bytes(b"F1_STEP")

    outcome = BatchExecutionOutcome(
        status="cancelled",
        request_id=req.request_id,
        contract_version="1.0",
        summary=BatchSummary(total=2, accepted=0, partial=1, failed=0, unprocessed=0, cancelled=1),
        file_results=(
            BatchFileResult(
                input="f1.par",
                status="partial",
                artifacts=(BatchArtifactRecord(format="step", path=str(art_step)),),
                errors=(BatchDiagnostic(code="BATCH_CANCELLED", message="Cancelled before stl", format="stl"),),
            ),
        ),
        cancelled_files=("f2.par",),
        output_root=out_root,
    )

    manifest = assemble_batch_manifest(req, outcome, engine_version="1.0.0")

    assert manifest.status == "cancelled"
    assert len(manifest.results) == 1
    assert manifest.results[0].status == "partial"
    assert manifest.results[0].errors[0].code == "BATCH_CANCELLED"
    assert manifest.cancelled_files == ("f2.par",)


def test_assemble_batch_manifest_progressed_failed_outcome(tmp_path: Path) -> None:
    """Proves manifest assembly for progressed failed batch carrying file failure and top-level errors."""
    req = _make_request(tmp_path, files=("bad.par",), formats=("step",))
    out_root = Path(req.output_root)

    outcome = BatchExecutionOutcome(
        status="failed",
        request_id=req.request_id,
        contract_version="1.0",
        summary=BatchSummary(total=1, accepted=0, partial=0, failed=1, unprocessed=0, cancelled=0),
        file_results=(
            BatchFileResult(
                input="bad.par",
                status="failed",
                artifacts=(),
                errors=(BatchDiagnostic(code="ARTIFACT_EXPORT_FAILED", message="Failed"),),
            ),
        ),
        errors=(BatchDiagnostic(code="INTERNAL_ERROR", message="Batch failed"),),
        output_root=out_root,
    )

    manifest = assemble_batch_manifest(req, outcome, engine_version="1.0.0")

    assert manifest.status == "failed"
    assert len(manifest.results) == 1
    assert manifest.results[0].status == "failed"
    assert manifest.errors[0].code == "INTERNAL_ERROR"


# ---------------------------------------------------------------------------
# 2. Rejection of Ineligible Outcomes & Identity Mismatches
# ---------------------------------------------------------------------------


def test_assemble_batch_manifest_rejects_unstarted_or_early_failed(tmp_path: Path) -> None:
    """Proves rejected outcomes and early failed outcomes (without summary) are ineligible."""
    req = _make_request(tmp_path)

    # 1. Rejected outcome
    outcome_rejected = BatchExecutionOutcome(
        status="rejected",
        request_id=req.request_id,
        contract_version="1.0",
        errors=(BatchDiagnostic(code="INVALID_SCHEMA", message="Rejected"),),
    )
    with pytest.raises(ManifestAssemblyError, match="not eligible for manifest assembly"):
        assemble_batch_manifest(req, outcome_rejected, engine_version="1.0.0")

    # 2. Early failed outcome without summary
    outcome_early_failed = BatchExecutionOutcome(
        status="failed",
        request_id=req.request_id,
        contract_version="1.0",
        errors=(BatchDiagnostic(code="OUTPUT_ROOT_UNAVAILABLE", message="Unavailable"),),
    )
    with pytest.raises(ManifestAssemblyError, match="not eligible for manifest assembly"):
        assemble_batch_manifest(req, outcome_early_failed, engine_version="1.0.0")


def test_assemble_batch_manifest_rejects_identity_mismatches(tmp_path: Path) -> None:
    """Proves request_id, contract_version, and output_root mismatches are rejected."""
    req = _make_request(tmp_path)
    out_root = Path(req.output_root)

    # Request ID mismatch
    outcome_bad_id = BatchExecutionOutcome(
        status="completed",
        request_id="different-req-id",
        contract_version="1.0",
        summary=BatchSummary(total=1, accepted=0, partial=0, failed=0, unprocessed=1, cancelled=0),
        unprocessed_files=("part_a.par",),
        output_root=out_root,
    )
    with pytest.raises(ManifestAssemblyError, match="Request ID mismatch"):
        assemble_batch_manifest(req, outcome_bad_id, engine_version="1.0.0")

    # Contract version mismatch
    outcome_bad_version = BatchExecutionOutcome(
        status="completed",
        request_id=req.request_id,
        contract_version="2.0",
        summary=BatchSummary(total=1, accepted=0, partial=0, failed=0, unprocessed=1, cancelled=0),
        unprocessed_files=("part_a.par",),
        output_root=out_root,
    )
    with pytest.raises(ManifestAssemblyError, match="Contract version mismatch"):
        assemble_batch_manifest(req, outcome_bad_version, engine_version="1.0.0")

    # Output root mismatch
    other_root = tmp_path / "other_out"
    other_root.mkdir(parents=True, exist_ok=True)
    outcome_bad_root = BatchExecutionOutcome(
        status="completed",
        request_id=req.request_id,
        contract_version="1.0",
        summary=BatchSummary(total=1, accepted=0, partial=0, failed=0, unprocessed=1, cancelled=0),
        unprocessed_files=("part_a.par",),
        output_root=other_root,
    )
    with pytest.raises(ManifestAssemblyError, match="Outcome output_root does not match request"):
        assemble_batch_manifest(req, outcome_bad_root, engine_version="1.0.0")


def test_assemble_batch_manifest_rejects_invalid_engine_version(tmp_path: Path) -> None:
    """Proves invalid engine versions (empty, whitespace) are rejected."""
    req = _make_request(tmp_path)
    out_root = Path(req.output_root)

    outcome = BatchExecutionOutcome(
        status="completed",
        request_id=req.request_id,
        contract_version="1.0",
        summary=BatchSummary(total=1, accepted=0, partial=0, failed=0, unprocessed=1, cancelled=0),
        unprocessed_files=("part_a.par",),
        output_root=out_root,
    )

    for bad_ver in ("", "  ", "\t\n", " 1.0.0 ", "1.0.0 "):
        with pytest.raises(ManifestAssemblyError, match="engine_version must be a non-empty string"):
            assemble_batch_manifest(req, outcome, engine_version=bad_ver)


# ---------------------------------------------------------------------------
# 3. Artifact Safety & Disk Integrity Checks
# ---------------------------------------------------------------------------


def test_assemble_batch_manifest_missing_artifact_raises(tmp_path: Path) -> None:
    """Proves missing artifact file on disk raises ManifestAssemblyError."""
    req = _make_request(tmp_path)
    out_root = Path(req.output_root)
    non_existent = out_root / "missing.step"

    outcome = BatchExecutionOutcome(
        status="completed",
        request_id=req.request_id,
        contract_version="1.0",
        summary=BatchSummary(total=1, accepted=1, partial=0, failed=0, unprocessed=0, cancelled=0),
        file_results=(
            BatchFileResult(
                input="part_a.par",
                status="accepted",
                artifacts=(BatchArtifactRecord(format="step", path=str(non_existent)),),
            ),
        ),
        output_root=out_root,
    )

    with pytest.raises(ManifestAssemblyError, match="is not a regular file"):
        assemble_batch_manifest(req, outcome, engine_version="1.0.0")


def test_assemble_batch_manifest_empty_artifact_raises(tmp_path: Path) -> None:
    """Proves 0-byte artifact file raises ManifestAssemblyError."""
    req = _make_request(tmp_path)
    out_root = Path(req.output_root)
    empty_file = out_root / "empty.step"
    empty_file.write_bytes(b"")

    outcome = BatchExecutionOutcome(
        status="completed",
        request_id=req.request_id,
        contract_version="1.0",
        summary=BatchSummary(total=1, accepted=1, partial=0, failed=0, unprocessed=0, cancelled=0),
        file_results=(
            BatchFileResult(
                input="part_a.par",
                status="accepted",
                artifacts=(BatchArtifactRecord(format="step", path=str(empty_file)),),
            ),
        ),
        output_root=out_root,
    )

    with pytest.raises(ManifestAssemblyError, match="Failed to capture snapshot for artifact"):
        assemble_batch_manifest(req, outcome, engine_version="1.0.0")


def test_assemble_batch_manifest_escaping_artifact_raises(tmp_path: Path) -> None:
    """Proves artifact path escaping output root raises ManifestAssemblyError."""
    req = _make_request(tmp_path)
    out_root = Path(req.output_root)
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir(parents=True, exist_ok=True)
    outside_art = outside_dir / "escaped.step"
    outside_art.write_bytes(b"ESCAPED")

    outcome = BatchExecutionOutcome(
        status="completed",
        request_id=req.request_id,
        contract_version="1.0",
        summary=BatchSummary(total=1, accepted=1, partial=0, failed=0, unprocessed=0, cancelled=0),
        file_results=(
            BatchFileResult(
                input="part_a.par",
                status="accepted",
                artifacts=(BatchArtifactRecord(format="step", path=str(outside_art)),),
            ),
        ),
        output_root=out_root,
    )

    with pytest.raises(ManifestAssemblyError, match="not contained within output root"):
        assemble_batch_manifest(req, outcome, engine_version="1.0.0")


def test_assemble_batch_manifest_relative_artifact_path_rejected(tmp_path: Path) -> None:
    """Proves non-absolute artifact path in outcome raises ManifestAssemblyError."""
    req = _make_request(tmp_path)
    out_root = Path(req.output_root)

    outcome = BatchExecutionOutcome(
        status="completed",
        request_id=req.request_id,
        contract_version="1.0",
        summary=BatchSummary(total=1, accepted=1, partial=0, failed=0, unprocessed=0, cancelled=0),
        file_results=(
            BatchFileResult(
                input="part_a.par",
                status="accepted",
                artifacts=(BatchArtifactRecord(format="step", path="relative/path.step"),),
            ),
        ),
        output_root=out_root,
    )

    with pytest.raises(ManifestAssemblyError, match="is not absolute"):
        assemble_batch_manifest(req, outcome, engine_version="1.0.0")


def test_assemble_batch_manifest_directory_artifact_raises(tmp_path: Path) -> None:
    """Proves directory used as an artifact path raises ManifestAssemblyError."""
    req = _make_request(tmp_path)
    out_root = Path(req.output_root)
    dir_art = out_root / "dir_artifact.step"
    dir_art.mkdir(parents=True, exist_ok=True)

    outcome = BatchExecutionOutcome(
        status="completed",
        request_id=req.request_id,
        contract_version="1.0",
        summary=BatchSummary(total=1, accepted=1, partial=0, failed=0, unprocessed=0, cancelled=0),
        file_results=(
            BatchFileResult(
                input="part_a.par",
                status="accepted",
                artifacts=(BatchArtifactRecord(format="step", path=str(dir_art)),),
            ),
        ),
        output_root=out_root,
    )

    with pytest.raises(ManifestAssemblyError, match="is not a regular file"):
        assemble_batch_manifest(req, outcome, engine_version="1.0.0")


def test_assemble_batch_manifest_rejects_foreign_file_partition(tmp_path: Path) -> None:
    """Proves manifest assembly rejects an outcome whose file partition does not match the request."""
    req = _make_request(tmp_path, files=("expected.par",), formats=("step",))
    out_root = Path(req.output_root)

    art = out_root / "other.step"
    art.write_bytes(b"OTHER_DATA")

    outcome = BatchExecutionOutcome(
        status="completed",
        request_id=req.request_id,
        contract_version="1.0",
        summary=BatchSummary(total=1, accepted=1, partial=0, failed=0, unprocessed=0, cancelled=0),
        file_results=(
            BatchFileResult(
                input="other.par",
                status="accepted",
                artifacts=(BatchArtifactRecord(format="step", path=str(art)),),
            ),
        ),
        output_root=out_root,
    )

    with pytest.raises(ManifestAssemblyError, match="Outcome file partition does not match"):
        assemble_batch_manifest(req, outcome, engine_version="1.0.0")


def test_assemble_batch_manifest_rejects_reordered_file_partition(tmp_path: Path) -> None:
    """Proves manifest assembly rejects an outcome where file order is scrambled relative to request."""
    req = _make_request(tmp_path, files=("first.par", "second.par"), formats=("step",))
    out_root = Path(req.output_root)

    art1 = out_root / "first.step"
    art2 = out_root / "second.step"
    art1.write_bytes(b"1")
    art2.write_bytes(b"2")

    outcome = BatchExecutionOutcome(
        status="completed",
        request_id=req.request_id,
        contract_version="1.0",
        summary=BatchSummary(total=2, accepted=2, partial=0, failed=0, unprocessed=0, cancelled=0),
        file_results=(
            BatchFileResult(
                input="second.par",
                status="accepted",
                artifacts=(BatchArtifactRecord(format="step", path=str(art2)),),
            ),
            BatchFileResult(
                input="first.par",
                status="accepted",
                artifacts=(BatchArtifactRecord(format="step", path=str(art1)),),
            ),
        ),
        output_root=out_root,
    )

    with pytest.raises(ManifestAssemblyError, match="Outcome file partition does not match"):
        assemble_batch_manifest(req, outcome, engine_version="1.0.0")


def test_assemble_batch_manifest_rejects_unrequested_artifact_format(tmp_path: Path) -> None:
    """Proves manifest assembly rejects an artifact format not requested by the operation."""
    req = _make_request(tmp_path, files=("part.par",), formats=("step",))
    out_root = Path(req.output_root)

    art = out_root / "part.stl"
    art.write_bytes(b"STL_DATA")

    outcome = BatchExecutionOutcome(
        status="completed",
        request_id=req.request_id,
        contract_version="1.0",
        summary=BatchSummary(total=1, accepted=1, partial=0, failed=0, unprocessed=0, cancelled=0),
        file_results=(
            BatchFileResult(
                input="part.par",
                status="accepted",
                artifacts=(BatchArtifactRecord(format="stl", path=str(art)),),
            ),
        ),
        output_root=out_root,
    )

    with pytest.raises(ManifestAssemblyError, match="not in requested operation formats"):
        assemble_batch_manifest(req, outcome, engine_version="1.0.0")
