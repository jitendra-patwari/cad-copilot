"""Tests for batch execution outcome finalization and canonical response assembly."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from batch.execution import BatchExecutionOutcome
from batch.finalization import (
    MANIFEST_PUBLICATION_FAILED_MESSAGE,
    finalize_batch_outcome,
)
from batch.models import (
    BatchArtifactRecord,
    BatchDiagnostic,
    BatchFileResult,
    BatchInputSelection,
    BatchOperation,
    BatchOptions,
    BatchOutputFormat,
    BatchRequest,
    BatchResponse,
    BatchSummary,
)
from batch.terminal import validate_batch_response_semantics


def _setup_request_and_artifacts(
    tmp_path: Path,
    *,
    request_id: str = "req-fin-01",
    files: tuple[str, ...] = ("part.par",),
    formats: tuple[BatchOutputFormat, ...] = ("step",),
) -> tuple[BatchRequest, dict[str, Path]]:
    in_root = tmp_path / "in"
    out_root = tmp_path / "out"
    in_root.mkdir(parents=True, exist_ok=True)
    out_root.mkdir(parents=True, exist_ok=True)

    artifacts: dict[str, Path] = {}
    for f in files:
        src = in_root / f
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_bytes(b"SRC")

        stem = Path(f).stem
        for fmt in formats:
            art = out_root / f"{stem}.{fmt}"
            art.parent.mkdir(parents=True, exist_ok=True)
            art.write_bytes(f"DATA_{stem}_{fmt}".encode("ascii"))
            artifacts[f"{f}:{fmt}"] = art

    req = BatchRequest(
        contract_version="1.0",
        request_id=request_id,
        kind="batch_operation",
        input=BatchInputSelection(root=str(in_root), files=files),
        output_root=str(out_root),
        operation=BatchOperation(type="export_3d", formats=formats),
        options=BatchOptions(continue_on_error=True),
    )
    return req, artifacts


# ---------------------------------------------------------------------------
# 1. Early Outcomes: Never Touch Disk or Manifest
# ---------------------------------------------------------------------------


def test_finalize_rejected_outcome_no_manifest_or_disk_touches(tmp_path: Path) -> None:
    """Proves early rejected outcome returns schema-valid BatchResponse without attempting manifest."""
    req, _ = _setup_request_and_artifacts(tmp_path)

    outcome = BatchExecutionOutcome(
        status="rejected",
        request_id=req.request_id,
        contract_version="1.0",
        errors=(BatchDiagnostic(code="INVALID_SCHEMA", message="Schema rejection"),),
    )

    with (
        patch("batch.finalization.assemble_batch_manifest") as mock_assemble,
        patch("batch.finalization.publish_batch_manifest") as mock_pub,
    ):
        resp = finalize_batch_outcome(req, outcome, engine_version="1.0.0")
        assert mock_assemble.call_count == 0
        assert mock_pub.call_count == 0

    assert isinstance(resp, BatchResponse)
    assert resp.status == "rejected"
    assert resp.manifest is None
    assert resp.summary is None
    assert resp.results == ()
    assert resp.unprocessed_files == ()
    assert resp.errors[0].code == "INVALID_SCHEMA"
    validate_batch_response_semantics(resp)


def test_finalize_early_failed_outcome_no_manifest(tmp_path: Path) -> None:
    """Proves early failed outcome without summary returns BatchResponse without attempting manifest."""
    req, _ = _setup_request_and_artifacts(tmp_path)

    outcome = BatchExecutionOutcome(
        status="failed",
        request_id=req.request_id,
        contract_version="1.0",
        errors=(BatchDiagnostic(code="OUTPUT_ROOT_UNAVAILABLE", message="Root unavailable"),),
    )

    with (
        patch("batch.finalization.assemble_batch_manifest") as mock_assemble,
        patch("batch.finalization.publish_batch_manifest") as mock_pub,
    ):
        resp = finalize_batch_outcome(req, outcome, engine_version="1.0.0")
        assert mock_assemble.call_count == 0
        assert mock_pub.call_count == 0

    assert resp.status == "failed"
    assert resp.manifest is None
    assert resp.summary is None
    assert resp.unprocessed_files == ()
    validate_batch_response_semantics(resp)


def test_finalize_rejects_identity_mismatches_on_early_outcomes(tmp_path: Path) -> None:
    """Proves finalize_batch_outcome rejects request_id and contract_version mismatches on early outcomes."""
    req, _ = _setup_request_and_artifacts(tmp_path)

    # 1. Rejected outcome with mismatched request_id
    rejected_bad_id = BatchExecutionOutcome(
        status="rejected",
        request_id="different-req",
        contract_version="1.0",
        errors=(BatchDiagnostic(code="INVALID_SCHEMA", message="Rejected"),),
    )
    with pytest.raises(ValueError, match="Request ID mismatch"):
        finalize_batch_outcome(req, rejected_bad_id, engine_version="1.0.0")

    # 2. Rejected outcome with mismatched contract_version
    rejected_bad_ver = BatchExecutionOutcome(
        status="rejected",
        request_id=req.request_id,
        contract_version="2.0",
        errors=(BatchDiagnostic(code="INVALID_SCHEMA", message="Rejected"),),
    )
    with pytest.raises(ValueError, match="Contract version mismatch"):
        finalize_batch_outcome(req, rejected_bad_ver, engine_version="1.0.0")

    # 3. Early failed outcome with mismatched request_id
    failed_bad_id = BatchExecutionOutcome(
        status="failed",
        request_id="different-req",
        contract_version="1.0",
        errors=(BatchDiagnostic(code="OUTPUT_ROOT_UNAVAILABLE", message="Unavailable"),),
    )
    with pytest.raises(ValueError, match="Request ID mismatch"):
        finalize_batch_outcome(req, failed_bad_id, engine_version="1.0.0")

    # 4. Early failed outcome with mismatched contract_version
    failed_bad_ver = BatchExecutionOutcome(
        status="failed",
        request_id=req.request_id,
        contract_version="2.0",
        errors=(BatchDiagnostic(code="OUTPUT_ROOT_UNAVAILABLE", message="Unavailable"),),
    )
    with pytest.raises(ValueError, match="Contract version mismatch"):
        finalize_batch_outcome(req, failed_bad_ver, engine_version="1.0.0")


# ---------------------------------------------------------------------------
# 2. Progressed Outcomes: Completed & Cancelled with Published Manifest
# ---------------------------------------------------------------------------


def test_finalize_completed_outcome_publishes_manifest(tmp_path: Path) -> None:
    """Proves progressed completed outcome publishes manifest and includes forward-slash reference."""
    req, arts = _setup_request_and_artifacts(tmp_path, files=("p1.par", "p2.par"), formats=("step",))
    art1 = arts["p1.par:step"]
    art2 = arts["p2.par:step"]

    outcome = BatchExecutionOutcome(
        status="completed",
        request_id=req.request_id,
        contract_version="1.0",
        summary=BatchSummary(total=2, accepted=2, partial=0, failed=0, unprocessed=0, cancelled=0),
        file_results=(
            BatchFileResult(
                input="p1.par",
                status="accepted",
                artifacts=(BatchArtifactRecord(format="step", path=str(art1)),),
            ),
            BatchFileResult(
                input="p2.par",
                status="accepted",
                artifacts=(BatchArtifactRecord(format="step", path=str(art2)),),
            ),
        ),
        output_root=Path(req.output_root),
    )

    resp = finalize_batch_outcome(req, outcome, engine_version="1.0.0")

    assert resp.status == "completed"
    assert resp.manifest is not None
    # Must be absolute, forward-slash normalized
    assert resp.manifest.path == (Path(req.output_root) / f"{req.request_id}.batch_manifest.json").as_posix()
    assert "/" in resp.manifest.path
    assert "\\" not in resp.manifest.path

    # Local response retains absolute artifact paths
    assert resp.results[0].artifacts[0].path == str(art1)
    assert resp.results[1].artifacts[0].path == str(art2)

    # Manifest file physically exists on disk
    manifest_disk_path = Path(resp.manifest.path)
    assert manifest_disk_path.is_file()

    validate_batch_response_semantics(resp)


def test_finalize_cancelled_outcome_publishes_manifest(tmp_path: Path) -> None:
    """Proves progressed cancelled outcome publishes cancelled manifest."""
    req, _ = _setup_request_and_artifacts(tmp_path, files=("p1.par", "p2.par"), formats=("step",))

    outcome = BatchExecutionOutcome(
        status="cancelled",
        request_id=req.request_id,
        contract_version="1.0",
        summary=BatchSummary(total=2, accepted=0, partial=0, failed=0, unprocessed=0, cancelled=2),
        file_results=(),
        cancelled_files=("p1.par", "p2.par"),
        output_root=Path(req.output_root),
    )

    resp = finalize_batch_outcome(req, outcome, engine_version="1.0.0")

    assert resp.status == "cancelled"
    assert resp.manifest is not None
    assert resp.manifest.path == (Path(req.output_root) / f"{req.request_id}.batch_manifest.json").as_posix()
    assert resp.cancelled_files == ("p1.par", "p2.par")
    validate_batch_response_semantics(resp)


# ---------------------------------------------------------------------------
# 3. Publication Failure Precedence
# ---------------------------------------------------------------------------


def test_finalize_publication_failure_converts_completed_to_failed(tmp_path: Path) -> None:
    """Proves completed outcome with manifest publication failure converts to failed without manifest."""
    req, arts = _setup_request_and_artifacts(tmp_path, files=("p1.par",), formats=("step",))
    art1 = arts["p1.par:step"]

    # Create collision sentinel to trigger publication failure
    sentinel = Path(req.output_root) / f"{req.request_id}.batch_manifest.json"
    sentinel.write_bytes(b'{"sentinel": true}\n')

    outcome = BatchExecutionOutcome(
        status="completed",
        request_id=req.request_id,
        contract_version="1.0",
        summary=BatchSummary(total=1, accepted=1, partial=0, failed=0, unprocessed=0, cancelled=0),
        file_results=(
            BatchFileResult(
                input="p1.par",
                status="accepted",
                artifacts=(BatchArtifactRecord(format="step", path=str(art1)),),
            ),
        ),
        output_root=Path(req.output_root),
    )

    resp = finalize_batch_outcome(req, outcome, engine_version="1.0.0")

    # Converted to failed
    assert resp.status == "failed"
    assert resp.manifest is None
    assert len(resp.errors) == 1
    assert resp.errors[0].code == "MANIFEST_PUBLICATION_FAILED"
    assert resp.errors[0].message == MANIFEST_PUBLICATION_FAILED_MESSAGE

    # Per-file results preserved
    assert len(resp.results) == 1
    assert resp.results[0].status == "accepted"

    # Sentinel untouched
    assert sentinel.read_bytes() == b'{"sentinel": true}\n'

    validate_batch_response_semantics(resp)


def test_finalize_publication_failure_converts_cancelled_to_failed_with_unprocessed_shift(
    tmp_path: Path,
) -> None:
    """Proves cancelled outcome with publication failure shifts cancelled files to unprocessed."""
    req, arts = _setup_request_and_artifacts(tmp_path, files=("p1.par", "p2.par", "p3.par"), formats=("step", "stl"))
    art1_step = arts["p1.par:step"]

    # Trigger publication failure by sentinel collision
    sentinel = Path(req.output_root) / f"{req.request_id}.batch_manifest.json"
    sentinel.write_bytes(b'{"sentinel": true}\n')

    outcome = BatchExecutionOutcome(
        status="cancelled",
        request_id=req.request_id,
        contract_version="1.0",
        summary=BatchSummary(total=3, accepted=0, partial=1, failed=0, unprocessed=0, cancelled=2),
        file_results=(
            BatchFileResult(
                input="p1.par",
                status="partial",
                artifacts=(BatchArtifactRecord(format="step", path=str(art1_step)),),
                errors=(BatchDiagnostic(code="BATCH_CANCELLED", message="Cancelled mid-file", format="stl"),),
            ),
        ),
        cancelled_files=("p2.par", "p3.par"),
        output_root=Path(req.output_root),
    )

    resp = finalize_batch_outcome(req, outcome, engine_version="1.0.0")

    # Status converted to failed
    assert resp.status == "failed"
    assert resp.manifest is None

    # Failed responses MUST have cancelled_files=() and summary.cancelled=0
    assert resp.cancelled_files == ()
    assert resp.summary is not None
    assert resp.summary.cancelled == 0

    # Cancelled files moved to unprocessed_files
    assert resp.unprocessed_files == ("p2.par", "p3.par")
    assert resp.summary.unprocessed == 2
    assert resp.summary.total == 3
    assert resp.summary.partial == 1

    # Errors contain MANIFEST_PUBLICATION_FAILED
    assert len(resp.errors) == 1
    assert resp.errors[0].code == "MANIFEST_PUBLICATION_FAILED"

    # Mid-file BATCH_CANCELLED remains on attempted file result
    assert resp.results[0].errors[0].code == "BATCH_CANCELLED"

    validate_batch_response_semantics(resp)


def test_finalize_publication_failure_on_already_failed_outcome_appends_error_once(
    tmp_path: Path,
) -> None:
    """Proves already progressed failed outcome retains fatal errors and appends publication error once."""
    req, _ = _setup_request_and_artifacts(tmp_path, files=("bad.par",), formats=("step",))

    # Collision sentinel
    sentinel = Path(req.output_root) / f"{req.request_id}.batch_manifest.json"
    sentinel.write_bytes(b'{"sentinel": true}\n')

    initial_error = BatchDiagnostic(code="INTERNAL_ERROR", message="Execution failed")
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
                errors=(BatchDiagnostic(code="ARTIFACT_EXPORT_FAILED", message="File failed"),),
            ),
        ),
        errors=(initial_error,),
        output_root=Path(req.output_root),
    )

    resp = finalize_batch_outcome(req, outcome, engine_version="1.0.0")

    assert resp.status == "failed"
    assert resp.manifest is None
    assert len(resp.errors) == 2
    assert resp.errors[0] == initial_error
    assert resp.errors[1].code == "MANIFEST_PUBLICATION_FAILED"

    validate_batch_response_semantics(resp)
