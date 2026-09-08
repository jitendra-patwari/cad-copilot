"""Terminal cancellation semantic validation tests."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from batch.models import (
    BatchArtifactRecord,
    BatchDiagnostic,
    BatchFileResult,
    BatchManifest,
    BatchManifestReference,
    BatchOperation,
    BatchOutputFormat,
    BatchResponse,
    BatchSummary,
    BatchTerminalStatus,
    FileResultStatus,
    ManifestArtifactRecord,
    ManifestFileResult,
    ManifestTerminalStatus,
)


def _file_result(
    input_path: str = "a.par",
    status: FileResultStatus = "partial",
    artifacts: Sequence[BatchArtifactRecord] = (),
    errors: Sequence[BatchDiagnostic] = (),
) -> BatchFileResult:
    return BatchFileResult(
        input=input_path,
        status=status,
        artifacts=tuple(artifacts),
        errors=tuple(errors),
    )


def _manifest_result(
    input_path: str = "a.par",
    status: FileResultStatus = "partial",
    artifacts: Sequence[ManifestArtifactRecord] = (),
    errors: Sequence[BatchDiagnostic] = (),
) -> ManifestFileResult:
    return ManifestFileResult(
        input=input_path,
        status=status,
        artifacts=tuple(artifacts),
        errors=tuple(errors),
    )


_DEFAULT_MANIFEST_REF = BatchManifestReference(path="out/man.json")


def _cancelled_response(
    results: Sequence[BatchFileResult] = (),
    errors: Sequence[BatchDiagnostic] = (),
    cancelled_files: Sequence[str] = (),
    status: BatchTerminalStatus = "cancelled",
    summary: BatchSummary | None = None,
    manifest: BatchManifestReference | None = _DEFAULT_MANIFEST_REF,
) -> BatchResponse:
    if summary is None:
        total = len(results) + len(cancelled_files)
        accepted = sum(1 for r in results if r.status == "accepted")
        partial = sum(1 for r in results if r.status == "partial")
        failed = sum(1 for r in results if r.status == "failed")
        summary = BatchSummary(
            total=total,
            accepted=accepted,
            partial=partial,
            failed=failed,
            unprocessed=0,
            cancelled=len(cancelled_files),
        )
    return BatchResponse(
        contract_version="1.0",
        request_id="req-canc-test",
        status=status,
        summary=summary,
        results=tuple(results),
        manifest=manifest,
        errors=tuple(errors),
        cancelled_files=tuple(cancelled_files),
    )


def _cancelled_manifest(
    results: Sequence[ManifestFileResult] = (),
    errors: Sequence[BatchDiagnostic] = (),
    cancelled_files: Sequence[str] = (),
    status: ManifestTerminalStatus = "cancelled",
    formats: Sequence[BatchOutputFormat] = ("step", "stl"),
    summary: BatchSummary | None = None,
) -> BatchManifest:
    if summary is None:
        total = len(results) + len(cancelled_files)
        accepted = sum(1 for r in results if r.status == "accepted")
        partial = sum(1 for r in results if r.status == "partial")
        failed = sum(1 for r in results if r.status == "failed")
        summary = BatchSummary(
            total=total,
            accepted=accepted,
            partial=partial,
            failed=failed,
            unprocessed=0,
            cancelled=len(cancelled_files),
        )
    return BatchManifest(
        manifest_version="1.0",
        contract_version="1.0",
        request_id="req-m-canc-test",
        status=status,
        operation=BatchOperation(type="export_3d", formats=tuple(formats)),
        summary=summary,
        results=tuple(results),
        cancelled_files=tuple(cancelled_files),
        errors=tuple(errors),
        engine_version="0.1.0",
    )


class TestTerminalCancellationSemantics:
    """Proves cancellation-specific invariants and BATCH_CANCELLED validation."""

    def test_cancelled_manifest_rejects_zero_cancelled_files_without_batch_cancelled(self) -> None:
        """Cancelled manifest must have at least one cancelled file or BATCH_CANCELLED."""
        with pytest.raises(ValueError, match="requires at least one cancelled file or BATCH_CANCELLED"):
            _cancelled_manifest(
                results=(
                    _manifest_result(
                        status="accepted",
                        artifacts=(
                            ManifestArtifactRecord(
                                format="step", relative_path="a.step", size_bytes=10, sha256="0" * 64
                            ),
                        ),
                    ),
                ),
                cancelled_files=(),
                formats=("step",),
            )

    def test_mid_file_cancelled_response_accepted(self) -> None:
        """Mid-file cancelled response with empty cancelled_files and BATCH_CANCELLED error is valid."""
        resp = _cancelled_response(
            results=(
                _file_result(
                    status="partial",
                    artifacts=(BatchArtifactRecord(format="step", path="a.step"),),
                    errors=(BatchDiagnostic(code="BATCH_CANCELLED", message="Cancelled before STL", format="stl"),),
                ),
            ),
            cancelled_files=(),
        )
        assert resp.status == "cancelled"
        assert len(resp.cancelled_files) == 0

    def test_mid_file_cancelled_manifest_accepted(self) -> None:
        """Mid-file cancelled manifest with empty cancelled_files and BATCH_CANCELLED error is valid."""
        man = _cancelled_manifest(
            results=(
                _manifest_result(
                    status="partial",
                    artifacts=(
                        ManifestArtifactRecord(format="step", relative_path="a.step", size_bytes=10, sha256="0" * 64),
                    ),
                    errors=(BatchDiagnostic(code="BATCH_CANCELLED", message="Cancelled before STL", format="stl"),),
                ),
            ),
            cancelled_files=(),
            formats=("step", "stl"),
        )
        assert man.status == "cancelled"
        assert len(man.cancelled_files) == 0

    def test_batch_cancelled_missing_format_rejected_in_response(self) -> None:
        """BATCH_CANCELLED must specify format in response."""
        with pytest.raises(ValueError, match="BATCH_CANCELLED error must specify a format"):
            _cancelled_response(
                results=(
                    _file_result(
                        status="partial",
                        artifacts=(BatchArtifactRecord(format="step", path="a.step"),),
                        errors=(BatchDiagnostic(code="BATCH_CANCELLED", message="Missing format"),),
                    ),
                ),
                cancelled_files=(),
            )

    def test_batch_cancelled_missing_format_rejected_in_manifest(self) -> None:
        """BATCH_CANCELLED must specify format in manifest."""
        with pytest.raises(ValueError, match="BATCH_CANCELLED error must specify a format"):
            _cancelled_manifest(
                results=(
                    _manifest_result(
                        status="partial",
                        artifacts=(
                            ManifestArtifactRecord(
                                format="step", relative_path="a.step", size_bytes=10, sha256="0" * 64
                            ),
                        ),
                        errors=(BatchDiagnostic(code="BATCH_CANCELLED", message="Missing format"),),
                    ),
                ),
                cancelled_files=(),
                formats=("step", "stl"),
            )

    def test_batch_cancelled_in_completed_response_rejected(self) -> None:
        """Completed response must not contain BATCH_CANCELLED."""
        with pytest.raises(ValueError, match="Completed response must not contain BATCH_CANCELLED errors"):
            _cancelled_response(
                status="completed",
                results=(
                    _file_result(
                        status="partial",
                        artifacts=(BatchArtifactRecord(format="step", path="a.step"),),
                        errors=(BatchDiagnostic(code="BATCH_CANCELLED", message="Cancel in completed", format="stl"),),
                    ),
                ),
                cancelled_files=(),
            )

    def test_batch_cancelled_in_completed_manifest_rejected(self) -> None:
        """Completed manifest must not contain BATCH_CANCELLED."""
        with pytest.raises(ValueError, match="Completed manifest must not contain BATCH_CANCELLED errors"):
            _cancelled_manifest(
                status="completed",
                results=(
                    _manifest_result(
                        status="partial",
                        artifacts=(
                            ManifestArtifactRecord(
                                format="step", relative_path="a.step", size_bytes=10, sha256="0" * 64
                            ),
                        ),
                        errors=(BatchDiagnostic(code="BATCH_CANCELLED", message="Cancel in completed", format="stl"),),
                    ),
                ),
                cancelled_files=(),
                formats=("step", "stl"),
            )

    def test_batch_cancelled_top_level_error_rejected(self) -> None:
        """BATCH_CANCELLED is forbidden as top-level error in response."""
        with pytest.raises(ValueError, match="BATCH_CANCELLED is not permitted as a top-level error"):
            _cancelled_response(
                status="failed",
                errors=(BatchDiagnostic(code="BATCH_CANCELLED", message="Top cancel"),),
                manifest=None,
            )

    def test_manifest_batch_cancelled_top_level_error_rejected(self) -> None:
        """BATCH_CANCELLED is forbidden as top-level error in manifest."""
        with pytest.raises(ValueError, match="BATCH_CANCELLED is not permitted as a top-level error"):
            _cancelled_manifest(
                status="failed",
                results=(
                    _manifest_result(
                        status="accepted",
                        artifacts=(
                            ManifestArtifactRecord(
                                format="step", relative_path="a.step", size_bytes=10, sha256="0" * 64
                            ),
                        ),
                    ),
                ),
                errors=(BatchDiagnostic(code="BATCH_CANCELLED", message="Top cancel"),),
                cancelled_files=(),
                formats=("step",),
            )

    def test_batch_cancelled_in_progressed_failed_response_with_fatal_code_accepted(self) -> None:
        """BATCH_CANCELLED in failed response is permitted when accompanied by a fatal top-level error."""
        resp = _cancelled_response(
            status="failed",
            results=(
                _file_result(
                    status="partial",
                    artifacts=(BatchArtifactRecord(format="step", path="a.step"),),
                    errors=(BatchDiagnostic(code="BATCH_CANCELLED", message="Cancelled", format="stl"),),
                ),
            ),
            errors=(BatchDiagnostic(code="SOLID_EDGE_UNHEALTHY", message="Session crashed"),),
            manifest=None,
        )
        assert resp.status == "failed"

    def test_batch_cancelled_in_progressed_failed_response_with_non_fatal_code_rejected(self) -> None:
        """BATCH_CANCELLED in failed response is rejected without a fatal top-level error."""
        with pytest.raises(ValueError, match="requires a fatal top-level error"):
            _cancelled_response(
                status="failed",
                results=(
                    _file_result(
                        status="partial",
                        artifacts=(BatchArtifactRecord(format="step", path="a.step"),),
                        errors=(BatchDiagnostic(code="BATCH_CANCELLED", message="Cancelled", format="stl"),),
                    ),
                ),
                errors=(BatchDiagnostic(code="ARTIFACT_EXPORT_FAILED", message="Non fatal"),),
                manifest=None,
            )

    def test_batch_cancelled_in_progressed_failed_manifest_with_fatal_code_accepted(self) -> None:
        """BATCH_CANCELLED in failed manifest is permitted when accompanied by a fatal top-level error."""
        man = _cancelled_manifest(
            status="failed",
            results=(
                _manifest_result(
                    status="partial",
                    artifacts=(
                        ManifestArtifactRecord(format="step", relative_path="a.step", size_bytes=10, sha256="0" * 64),
                    ),
                    errors=(BatchDiagnostic(code="BATCH_CANCELLED", message="Cancelled", format="stl"),),
                ),
            ),
            errors=(BatchDiagnostic(code="DOCUMENT_CLOSE_FAILED", message="Close failed"),),
            cancelled_files=(),
            formats=("step", "stl"),
        )
        assert man.status == "failed"

    def test_batch_cancelled_in_progressed_failed_manifest_with_non_fatal_code_rejected(self) -> None:
        """BATCH_CANCELLED in failed manifest is rejected without a fatal top-level error."""
        with pytest.raises(ValueError, match="requires a fatal top-level error"):
            _cancelled_manifest(
                status="failed",
                results=(
                    _manifest_result(
                        status="partial",
                        artifacts=(
                            ManifestArtifactRecord(
                                format="step", relative_path="a.step", size_bytes=10, sha256="0" * 64
                            ),
                        ),
                        errors=(BatchDiagnostic(code="BATCH_CANCELLED", message="Cancelled", format="stl"),),
                    ),
                ),
                errors=(BatchDiagnostic(code="ARTIFACT_EXPORT_FAILED", message="Non fatal"),),
                cancelled_files=(),
                formats=("step", "stl"),
            )

    def test_manifest_batch_cancelled_unsupported_format_rejected(self) -> None:
        """BATCH_CANCELLED format must belong to requested operation formats in manifest."""
        with pytest.raises(ValueError, match="not in manifest operation formats"):
            _cancelled_manifest(
                results=(
                    _manifest_result(
                        status="partial",
                        artifacts=(
                            ManifestArtifactRecord(
                                format="step", relative_path="a.step", size_bytes=10, sha256="0" * 64
                            ),
                        ),
                        errors=(BatchDiagnostic(code="BATCH_CANCELLED", message="Bad format", format="pdf"),),
                    ),
                ),
                cancelled_files=(),
                formats=("step", "stl"),
            )

    def test_batch_cancelled_in_failed_response_with_manifest_publication_failed_accepted(self) -> None:
        """MANIFEST_PUBLICATION_FAILED is allowed to supersede BATCH_CANCELLED in BatchResponse."""
        resp = _cancelled_response(
            status="failed",
            results=(
                _file_result(
                    status="partial",
                    artifacts=(BatchArtifactRecord(format="step", path="a.step"),),
                    errors=(BatchDiagnostic(code="BATCH_CANCELLED", message="Cancelled", format="stl"),),
                ),
            ),
            errors=(BatchDiagnostic(code="MANIFEST_PUBLICATION_FAILED", message="Publication failed"),),
            manifest=None,
        )
        assert resp.status == "failed"

    def test_batch_cancelled_in_failed_manifest_with_manifest_publication_failed_rejected(self) -> None:
        """MANIFEST_PUBLICATION_FAILED cannot supersede cancellation inside a BatchManifest."""
        with pytest.raises(ValueError, match="requires a fatal top-level error"):
            _cancelled_manifest(
                status="failed",
                results=(
                    _manifest_result(
                        status="partial",
                        artifacts=(
                            ManifestArtifactRecord(
                                format="step", relative_path="a.step", size_bytes=10, sha256="0" * 64
                            ),
                        ),
                        errors=(BatchDiagnostic(code="BATCH_CANCELLED", message="Cancelled", format="stl"),),
                    ),
                ),
                errors=(BatchDiagnostic(code="MANIFEST_PUBLICATION_FAILED", message="Publication failed"),),
                cancelled_files=(),
                formats=("step", "stl"),
            )

    def test_multiple_batch_cancelled_markers_in_same_file_rejected(self) -> None:
        """A single file result cannot contain multiple BATCH_CANCELLED markers."""
        with pytest.raises(ValueError, match="Only one BATCH_CANCELLED error marker is permitted"):
            _cancelled_response(
                results=(
                    _file_result(
                        status="failed",
                        errors=(
                            BatchDiagnostic(code="BATCH_CANCELLED", message="Cancel 1", format="step"),
                            BatchDiagnostic(code="BATCH_CANCELLED", message="Cancel 2", format="stl"),
                        ),
                    ),
                ),
                cancelled_files=(),
            )

    def test_multiple_batch_cancelled_markers_across_files_rejected(self) -> None:
        """Multiple file results cannot claim BATCH_CANCELLED."""
        with pytest.raises(ValueError, match="Only one BATCH_CANCELLED error marker is permitted"):
            _cancelled_response(
                results=(
                    _file_result(
                        input_path="a.par",
                        status="partial",
                        artifacts=(BatchArtifactRecord(format="step", path="a.step"),),
                        errors=(BatchDiagnostic(code="BATCH_CANCELLED", message="Cancel a", format="stl"),),
                    ),
                    _file_result(
                        input_path="b.par",
                        status="partial",
                        artifacts=(BatchArtifactRecord(format="step", path="b.step"),),
                        errors=(BatchDiagnostic(code="BATCH_CANCELLED", message="Cancel b", format="stl"),),
                    ),
                ),
                cancelled_files=(),
            )

    def test_batch_cancelled_overlapping_with_artifact_format_rejected(self) -> None:
        """BATCH_CANCELLED cannot share format with an artifact in the same file result."""
        with pytest.raises(
            ValueError, match=r"Contradictory result: format 'step' has both an artifact and BATCH_CANCELLED"
        ):
            _cancelled_response(
                results=(
                    _file_result(
                        status="partial",
                        artifacts=(BatchArtifactRecord(format="step", path="a.step"),),
                        errors=(BatchDiagnostic(code="BATCH_CANCELLED", message="Cancel step", format="step"),),
                    ),
                ),
                cancelled_files=(),
            )

    def test_batch_cancelled_overlapping_with_execution_error_format_rejected(self) -> None:
        """BATCH_CANCELLED cannot share format with an execution error in the same file result."""
        with pytest.raises(
            ValueError, match=r"Contradictory result: format 'stl' has both an execution error and BATCH_CANCELLED"
        ):
            _cancelled_response(
                results=(
                    _file_result(
                        status="failed",
                        errors=(
                            BatchDiagnostic(code="ARTIFACT_EXPORT_FAILED", message="Fail stl", format="stl"),
                            BatchDiagnostic(code="BATCH_CANCELLED", message="Cancel stl", format="stl"),
                        ),
                    ),
                ),
                cancelled_files=(),
            )

    def test_batch_cancelled_not_on_final_attempted_file_rejected(self) -> None:
        """BATCH_CANCELLED must be on the final attempted file result."""
        with pytest.raises(ValueError, match="BATCH_CANCELLED must be on the final attempted file result"):
            _cancelled_response(
                results=(
                    _file_result(
                        input_path="a.par",
                        status="partial",
                        artifacts=(BatchArtifactRecord(format="step", path="a.step"),),
                        errors=(BatchDiagnostic(code="BATCH_CANCELLED", message="Cancel a", format="stl"),),
                    ),
                    _file_result(
                        input_path="b.par",
                        status="accepted",
                        artifacts=(
                            BatchArtifactRecord(format="step", path="b.step"),
                            BatchArtifactRecord(format="stl", path="b.stl"),
                        ),
                    ),
                ),
                cancelled_files=(),
            )

    def test_manifest_batch_cancelled_prior_format_unattempted_rejected(self) -> None:
        """Manifest validation rejects BATCH_CANCELLED when a prior format in operation order was unattempted."""
        with pytest.raises(ValueError, match="prior format 'step' was not attempted"):
            _cancelled_manifest(
                results=(
                    _manifest_result(
                        status="failed",
                        artifacts=(),
                        errors=(BatchDiagnostic(code="BATCH_CANCELLED", message="Cancel stl", format="stl"),),
                    ),
                ),
                cancelled_files=(),
                formats=("step", "stl"),
            )

    def test_manifest_batch_cancelled_subsequent_format_attempted_rejected(self) -> None:
        """Manifest validation rejects when a format after BATCH_CANCELLED was attempted."""
        with pytest.raises(ValueError, match=r"format 'stl' was attempted after BATCH_CANCELLED on 'step'"):
            _cancelled_manifest(
                results=(
                    _manifest_result(
                        status="partial",
                        artifacts=(
                            ManifestArtifactRecord(format="stl", relative_path="a.stl", size_bytes=10, sha256="0" * 64),
                        ),
                        errors=(BatchDiagnostic(code="BATCH_CANCELLED", message="Cancel step", format="step"),),
                    ),
                ),
                cancelled_files=(),
                formats=("step", "stl"),
            )
