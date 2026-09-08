"""Terminal response and manifest cross-field semantic validation tests."""

from __future__ import annotations

import pytest

from batch.models import (
    BatchArtifactRecord,
    BatchDiagnostic,
    BatchFileResult,
    BatchManifest,
    BatchManifestReference,
    BatchOperation,
    BatchResponse,
    BatchSummary,
    ManifestArtifactRecord,
    ManifestFileResult,
)


class TestTerminalSemanticInvariants:
    """Proves that constructing BatchResponse or BatchManifest enforces cross-field semantic invariants."""

    def test_completed_response_missing_manifest_rejected(self) -> None:
        with pytest.raises(ValueError, match="requires a manifest reference"):
            BatchResponse(
                contract_version="1.0",
                request_id="req-inv-1",
                status="completed",
                summary=BatchSummary(total=1, accepted=1, partial=0, failed=0, unprocessed=0, cancelled=0),
                results=(
                    BatchFileResult(
                        input="a.par",
                        status="accepted",
                        artifacts=(BatchArtifactRecord(format="step", path="a.step"),),
                    ),
                ),
                manifest=None,
            )

    def test_completed_response_rejects_cancelled_files(self) -> None:
        """Completed response must have zero cancelled files in summary and list."""
        with pytest.raises(ValueError, match="Completed response must have zero cancelled files"):
            BatchResponse(
                contract_version="1.0",
                request_id="req-inv-2",
                status="completed",
                summary=BatchSummary(total=2, accepted=1, partial=0, failed=0, unprocessed=0, cancelled=1),
                results=(
                    BatchFileResult(
                        input="a.par",
                        status="accepted",
                        artifacts=(BatchArtifactRecord(format="step", path="a.step"),),
                    ),
                ),
                manifest=BatchManifestReference(path="out/man.json"),
                cancelled_files=(),
            )

    def test_response_internal_error_in_warnings_rejected(self) -> None:
        """Reject error-only code INTERNAL_ERROR inside warnings."""
        with pytest.raises(ValueError, match="not permitted as a warning"):
            BatchResponse(
                contract_version="1.0",
                request_id="req-inv-3",
                status="completed",
                summary=BatchSummary(total=1, accepted=1, partial=0, failed=0, unprocessed=0, cancelled=0),
                results=(
                    BatchFileResult(
                        input="a.par",
                        status="accepted",
                        artifacts=(BatchArtifactRecord(format="step", path="a.step"),),
                    ),
                ),
                manifest=BatchManifestReference(path="out/man.json"),
                warnings=(BatchDiagnostic(code="INTERNAL_ERROR", message="illegal warning code"),),
            )

    def test_completed_response_with_errors_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not have top-level errors"):
            BatchResponse(
                contract_version="1.0",
                request_id="req-inv-4",
                status="completed",
                summary=BatchSummary(total=1, accepted=1, partial=0, failed=0, unprocessed=0, cancelled=0),
                results=(
                    BatchFileResult(
                        input="a.par",
                        status="accepted",
                        artifacts=(BatchArtifactRecord(format="step", path="a.step"),),
                    ),
                ),
                manifest=BatchManifestReference(path="out/man.json"),
                errors=(BatchDiagnostic(code="INTERNAL_ERROR", message="bad error in completed"),),
            )

    def test_rejected_response_without_errors_rejected(self) -> None:
        with pytest.raises(ValueError, match="requires at least one top-level error"):
            BatchResponse(
                contract_version="1.0",
                request_id="req-inv-5",
                status="rejected",
                errors=(),
            )

    def test_manifest_version_mismatch_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"Manifest version must be '1\.0'"):
            BatchManifest(
                manifest_version="2.0",
                contract_version="1.0",
                request_id="req-inv-6",
                status="completed",
                operation=BatchOperation(type="export_3d", formats=("step",)),
                summary=BatchSummary(total=1, accepted=1, partial=0, failed=0, unprocessed=0, cancelled=0),
                results=(
                    ManifestFileResult(
                        input="a.par",
                        status="accepted",
                        artifacts=(
                            ManifestArtifactRecord(
                                format="step", relative_path="a.step", size_bytes=10, sha256="0" * 64
                            ),
                        ),
                    ),
                ),
                engine_version="0.1.0",
            )

    def test_failed_file_result_rejects_artifacts(self) -> None:
        """Failed BatchFileResult must not allow artifacts."""
        with pytest.raises(ValueError, match="Failed FileResult must not have artifacts"):
            BatchFileResult(
                input="a.par",
                status="failed",
                artifacts=(BatchArtifactRecord(format="step", path="a.step"),),
                errors=(BatchDiagnostic(code="INTERNAL_ERROR", message="fail"),),
            )

    def test_failed_manifest_file_result_rejects_artifacts(self) -> None:
        """Failed ManifestFileResult must not allow artifacts."""
        with pytest.raises(ValueError, match="Failed ManifestFileResult must not have artifacts"):
            ManifestFileResult(
                input="a.par",
                status="failed",
                artifacts=(
                    ManifestArtifactRecord(format="step", relative_path="a.step", size_bytes=10, sha256="0" * 64),
                ),
                errors=(BatchDiagnostic(code="INTERNAL_ERROR", message="fail"),),
            )

    def test_response_rejects_unknown_status(self) -> None:
        """BatchResponse rejects non-standard status like 'bogus'."""
        with pytest.raises(ValueError, match="Invalid response status"):
            BatchResponse(
                contract_version="1.0",
                request_id="req-bogus",
                status="bogus",  # type: ignore[arg-type]
                summary=BatchSummary(total=1, accepted=1, partial=0, failed=0, unprocessed=0, cancelled=0),
                results=(
                    BatchFileResult(
                        input="a.par",
                        status="accepted",
                        artifacts=(BatchArtifactRecord(format="step", path="a.step"),),
                    ),
                ),
                manifest=BatchManifestReference(path="out/man.json"),
            )

    def test_cancelled_response_rejects_zero_cancelled_files(self) -> None:
        """Cancelled response must have at least one cancelled file or BATCH_CANCELLED."""
        with pytest.raises(ValueError, match="requires at least one cancelled file or BATCH_CANCELLED"):
            BatchResponse(
                contract_version="1.0",
                request_id="req-canc-zero",
                status="cancelled",
                summary=BatchSummary(total=1, accepted=1, partial=0, failed=0, unprocessed=0, cancelled=0),
                results=(
                    BatchFileResult(
                        input="a.par",
                        status="accepted",
                        artifacts=(BatchArtifactRecord(format="step", path="a.step"),),
                    ),
                ),
                manifest=BatchManifestReference(path="out/man.json"),
                cancelled_files=(),
            )

    def test_manifest_rejects_unknown_status(self) -> None:
        """BatchManifest rejects non-standard status like 'bogus'."""
        with pytest.raises(ValueError, match="Invalid manifest status"):
            BatchManifest(
                manifest_version="1.0",
                contract_version="1.0",
                request_id="req-m-bogus",
                status="bogus",  # type: ignore[arg-type]
                operation=BatchOperation(type="export_3d", formats=("step",)),
                summary=BatchSummary(total=1, accepted=1, partial=0, failed=0, unprocessed=0, cancelled=0),
                results=(
                    ManifestFileResult(
                        input="a.par",
                        status="accepted",
                        artifacts=(
                            ManifestArtifactRecord(
                                format="step", relative_path="a.step", size_bytes=10, sha256="0" * 64
                            ),
                        ),
                    ),
                ),
                engine_version="0.1.0",
            )

    def test_completed_manifest_rejects_cancelled_files(self) -> None:
        """Completed manifest must have zero cancelled files."""
        with pytest.raises(ValueError, match="Completed manifest must have zero cancelled files"):
            BatchManifest(
                manifest_version="1.0",
                contract_version="1.0",
                request_id="req-m-c1",
                status="completed",
                operation=BatchOperation(type="export_3d", formats=("step",)),
                summary=BatchSummary(total=1, accepted=1, partial=0, failed=0, unprocessed=0, cancelled=0),
                results=(
                    ManifestFileResult(
                        input="a.par",
                        status="accepted",
                        artifacts=(
                            ManifestArtifactRecord(
                                format="step", relative_path="a.step", size_bytes=10, sha256="0" * 64
                            ),
                        ),
                    ),
                ),
                cancelled_files=("extra.par",),
                engine_version="0.1.0",
            )

    def test_manifest_rejects_artifact_format_mismatch(self) -> None:
        """BatchManifest rejects artifact formats that disagree with requested operation formats."""
        with pytest.raises(ValueError, match="Artifact format 'step' not in manifest operation formats"):
            BatchManifest(
                manifest_version="1.0",
                contract_version="1.0",
                request_id="req-m-fmt-err",
                status="completed",
                operation=BatchOperation(type="publish_drawing", formats=("pdf",)),
                summary=BatchSummary(total=1, accepted=1, partial=0, failed=0, unprocessed=0, cancelled=0),
                results=(
                    ManifestFileResult(
                        input="drawing.dft",
                        status="accepted",
                        artifacts=(
                            ManifestArtifactRecord(
                                format="step",
                                relative_path="drawing.step",
                                size_bytes=10,
                                sha256="0" * 64,
                            ),
                        ),
                    ),
                ),
                engine_version="0.1.0",
            )
