"""Unit tests for pure immutable batch models, dataclass invariants, and validation rules."""

from __future__ import annotations

import dataclasses

import pytest

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
    BatchOptions,
    BatchRequest,
    BatchResponse,
    BatchSummary,
    ManifestArtifactRecord,
    ManifestFileResult,
)


class TestBatchSummary:
    """Invariants and arithmetic for BatchSummary."""

    def test_valid_summary_construction(self) -> None:
        summary = BatchSummary(total=10, accepted=5, partial=2, failed=1, unprocessed=1, cancelled=1)
        assert summary.total == 10
        assert summary.accepted == 5
        assert summary.partial == 2
        assert summary.failed == 1
        assert summary.unprocessed == 1
        assert summary.cancelled == 1

    def test_summary_arithmetic_mismatch_raises(self) -> None:
        with pytest.raises(ValueError, match="does not equal total"):
            BatchSummary(total=10, accepted=5, partial=2, failed=1, unprocessed=1, cancelled=0)

    def test_summary_negative_count_raises(self) -> None:
        with pytest.raises(ValueError, match="must be non-negative"):
            BatchSummary(total=4, accepted=5, partial=-1, failed=0, unprocessed=0, cancelled=0)

    def test_summary_bool_as_int_rejected(self) -> None:
        with pytest.raises(TypeError, match="must be an integer"):
            BatchSummary(total=1, accepted=True, partial=0, failed=0, unprocessed=0, cancelled=0)

    def test_summary_immutability(self) -> None:
        summary = BatchSummary(total=2, accepted=2, partial=0, failed=0, unprocessed=0, cancelled=0)
        with pytest.raises(dataclasses.FrozenInstanceError):
            summary.total = 5  # type: ignore[misc]


class TestBatchDiagnostic:
    """Invariants for BatchDiagnostic (errors and warnings)."""

    def test_approved_codes_count(self) -> None:
        assert len(APPROVED_DIAGNOSTIC_CODES) == 21
        assert "BATCH_CANCELLED" in APPROVED_DIAGNOSTIC_CODES
        assert "VERSION_METADATA_UNAVAILABLE" in APPROVED_WARNING_CODES
        assert len(APPROVED_WARNING_CODES) == 1
        assert "BATCH_CANCELLED" not in APPROVED_WARNING_CODES

    def test_valid_diagnostic_with_and_without_format(self) -> None:
        diag1 = BatchDiagnostic(code="INVALID_SCHEMA", message="Invalid syntax")
        assert diag1.code == "INVALID_SCHEMA"
        assert diag1.format is None

        diag2 = BatchDiagnostic(code="ARTIFACT_EXPORT_FAILED", message="Export failed", format="step")
        assert diag2.code == "ARTIFACT_EXPORT_FAILED"
        assert diag2.format == "step"

        diag3 = BatchDiagnostic(code="ARTIFACT_EXPORT_FAILED", message="Export failed", format="parasolid")
        assert diag3.code == "ARTIFACT_EXPORT_FAILED"
        assert diag3.format == "parasolid"

    def test_unknown_code_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown diagnostic code"):
            BatchDiagnostic(code="CUSTOM_UNKNOWN_CODE", message="Fail")

    def test_message_bounds(self) -> None:
        with pytest.raises(ValueError, match="length between 1 and 512"):
            BatchDiagnostic(code="INTERNAL_ERROR", message="")

        with pytest.raises(ValueError, match="length between 1 and 512"):
            BatchDiagnostic(code="INTERNAL_ERROR", message="x" * 513)

    def test_invalid_format_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid format"):
            BatchDiagnostic(code="INTERNAL_ERROR", message="Fail", format="exe")  # type: ignore[arg-type]


class TestArtifactRecords:
    """Validation of wire and manifest artifact records."""

    def test_batch_artifact_record_valid(self) -> None:
        rec = BatchArtifactRecord(format="step", path="C:/out/part.step")
        assert rec.format == "step"
        assert rec.path == "C:/out/part.step"

        rec_parasolid = BatchArtifactRecord(format="parasolid", path="C:/out/part.x_t")
        assert rec_parasolid.format == "parasolid"
        assert rec_parasolid.path == "C:/out/part.x_t"

    def test_batch_artifact_record_invalid(self) -> None:
        with pytest.raises(ValueError, match="Invalid artifact format"):
            BatchArtifactRecord(format="obj", path="part.obj")  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="Artifact path must be a non-empty string"):
            BatchArtifactRecord(format="step", path="")

    def test_manifest_artifact_record_valid(self) -> None:
        rec = ManifestArtifactRecord(
            format="stl",
            relative_path="part.stl",
            size_bytes=4096,
            sha256="a" * 64,
        )
        assert rec.size_bytes == 4096
        assert rec.sha256 == "a" * 64

        rec_parasolid = ManifestArtifactRecord(
            format="parasolid",
            relative_path="part.x_t",
            size_bytes=2048,
            sha256="b" * 64,
        )
        assert rec_parasolid.format == "parasolid"
        assert rec_parasolid.relative_path == "part.x_t"

    def test_manifest_artifact_record_bool_size_rejected(self) -> None:
        with pytest.raises(ValueError, match="positive integer"):
            ManifestArtifactRecord(
                format="stl",
                relative_path="part.stl",
                size_bytes=True,
                sha256="a" * 64,
            )

    def test_manifest_artifact_record_size_non_positive_rejected(self) -> None:
        with pytest.raises(ValueError, match="positive integer"):
            ManifestArtifactRecord(
                format="stl",
                relative_path="part.stl",
                size_bytes=0,
                sha256="a" * 64,
            )

    def test_manifest_artifact_record_sha256_rules(self) -> None:
        # Uppercase rejected
        with pytest.raises(ValueError, match="64-character lowercase hex"):
            ManifestArtifactRecord(
                format="stl",
                relative_path="part.stl",
                size_bytes=100,
                sha256="A" * 64,
            )
        # Short rejected
        with pytest.raises(ValueError, match="64-character lowercase hex"):
            ManifestArtifactRecord(
                format="stl",
                relative_path="part.stl",
                size_bytes=100,
                sha256="a" * 63,
            )


class TestFileResults:
    """File result invariants for accepted, partial, and failed states."""

    def test_accepted_file_result(self) -> None:
        art = BatchArtifactRecord(format="step", path="out/part.step")
        res = BatchFileResult(input="part.par", status="accepted", artifacts=(art,))
        assert res.status == "accepted"
        assert len(res.artifacts) == 1

        # Accepted cannot have errors
        err = BatchDiagnostic(code="INTERNAL_ERROR", message="fail")
        with pytest.raises(ValueError, match="Accepted FileResult must not have errors"):
            BatchFileResult(input="part.par", status="accepted", artifacts=(art,), errors=(err,))

        # Accepted must have artifacts
        with pytest.raises(ValueError, match="Accepted FileResult must have at least one artifact"):
            BatchFileResult(input="part.par", status="accepted", artifacts=())

    def test_partial_file_result(self) -> None:
        art = BatchArtifactRecord(format="step", path="out/part.step")
        err = BatchDiagnostic(code="ARTIFACT_EXPORT_FAILED", message="stl failed", format="stl")
        res = BatchFileResult(input="part.par", status="partial", artifacts=(art,), errors=(err,))
        assert res.status == "partial"

        # Partial requires artifacts
        with pytest.raises(ValueError, match="Partial FileResult must have at least one artifact"):
            BatchFileResult(input="part.par", status="partial", artifacts=(), errors=(err,))

        # Partial requires errors
        with pytest.raises(ValueError, match="Partial FileResult must have at least one error"):
            BatchFileResult(input="part.par", status="partial", artifacts=(art,), errors=())

    def test_failed_file_result(self) -> None:
        err = BatchDiagnostic(code="DOCUMENT_OPEN_FAILED", message="open fail")
        res = BatchFileResult(input="part.par", status="failed", errors=(err,))
        assert res.status == "failed"

        # Failed requires errors
        with pytest.raises(ValueError, match="Failed FileResult must have at least one error"):
            BatchFileResult(input="part.par", status="failed", errors=())

    def test_manifest_file_result_invariants(self) -> None:
        art = ManifestArtifactRecord(format="step", relative_path="p.step", size_bytes=10, sha256="f" * 64)
        res = ManifestFileResult(input="p.par", status="accepted", artifacts=(art,))
        assert res.status == "accepted"


class TestBatchOperation:
    """Matrix validation for export_3d and publish_drawing operations."""

    def test_valid_export_3d(self) -> None:
        op = BatchOperation(type="export_3d", formats=("step", "stl", "parasolid"))
        assert op.type == "export_3d"
        assert op.formats == ("step", "stl", "parasolid")

    def test_valid_publish_drawing(self) -> None:
        op = BatchOperation(type="publish_drawing", formats=("pdf", "dxf"))
        assert op.type == "publish_drawing"
        assert op.formats == ("pdf", "dxf")

    def test_export_3d_rejects_drawing_formats(self) -> None:
        with pytest.raises(ValueError, match="export_3d operation formats must only contain"):
            BatchOperation(type="export_3d", formats=("pdf",))

    def test_promoted_export_3d_accepts_parasolid(self) -> None:
        """Promoted invariant: BatchOperation export_3d accepts parasolid format alone and combined."""
        op_single = BatchOperation(type="export_3d", formats=("parasolid",))
        assert op_single.formats == ("parasolid",)
        op_all = BatchOperation(type="export_3d", formats=("step", "stl", "parasolid"))
        assert op_all.formats == ("step", "stl", "parasolid")

    def test_export_3d_rejects_unapproved_or_excess_formats(self) -> None:
        """export_3d rejects unapproved format tokens and >3 formats."""
        with pytest.raises(ValueError, match="export_3d operation formats must only contain"):
            BatchOperation(type="export_3d", formats=("jt",))  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="export_3d operation formats must have 1 to 3 items"):
            BatchOperation(type="export_3d", formats=("step", "stl", "parasolid", "step"))

    def test_publish_drawing_rejects_3d_formats(self) -> None:
        with pytest.raises(ValueError, match="publish_drawing operation formats must only contain"):
            BatchOperation(type="publish_drawing", formats=("step",))

    def test_duplicate_formats_rejected(self) -> None:
        with pytest.raises(ValueError, match="formats must contain unique items"):
            BatchOperation(type="export_3d", formats=("step", "step"))

    def test_empty_formats_rejected(self) -> None:
        with pytest.raises(ValueError, match="formats must have 1 to 3 items"):
            BatchOperation(type="export_3d", formats=())

    def test_invalid_operation_type_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unsupported operation type"):
            BatchOperation(type="convert", formats=("step",))  # type: ignore[arg-type]


class TestBatchOptions:
    """Default values and bounds for BatchOptions."""

    def test_default_options(self) -> None:
        opts = BatchOptions()
        assert opts.continue_on_error is True
        assert opts.max_files == 100

    def test_custom_options(self) -> None:
        opts = BatchOptions(continue_on_error=False, max_files=250)
        assert opts.continue_on_error is False
        assert opts.max_files == 250

    def test_max_files_bounds(self) -> None:
        with pytest.raises(ValueError, match="max_files must be an integer between 1 and 500"):
            BatchOptions(max_files=0)
        with pytest.raises(ValueError, match="max_files must be an integer between 1 and 500"):
            BatchOptions(max_files=501)

    def test_bool_rejected_for_max_files(self) -> None:
        with pytest.raises(ValueError, match="max_files must be an integer"):
            BatchOptions(max_files=True)


class TestBatchRequest:
    """Request construction, invariants, and immutability."""

    def test_valid_request(self) -> None:
        req = BatchRequest(
            contract_version="1.0",
            request_id="batch-001",
            kind="batch_operation",
            input=BatchInputSelection(root="C:/data", files=("part.par",)),
            output_root="C:/data/out",
            operation=BatchOperation(type="export_3d", formats=("step",)),
            options=BatchOptions(continue_on_error=True, max_files=10),
            metadata=BatchMetadata(source="desktop_app", label="test", job_id="job-1"),
        )
        assert req.request_id == "batch-001"
        assert req.options.max_files == 10
        assert req.metadata is not None
        assert req.metadata.source == "desktop_app"

    def test_request_contract_version_invalid(self) -> None:
        with pytest.raises(ValueError, match=r"contract_version must be '1\.0'"):
            BatchRequest(
                contract_version="2.0",
                request_id="req-1",
                kind="batch_operation",
                input=BatchInputSelection(root="C:/data", files=("part.par",)),
                output_root="C:/data/out",
                operation=BatchOperation(type="export_3d", formats=("step",)),
            )

    def test_request_files_exceeding_max_files_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"exceeds options\.max_files"):
            BatchRequest(
                contract_version="1.0",
                request_id="req-1",
                kind="batch_operation",
                input=BatchInputSelection(root="C:/data", files=("p1.par", "p2.par")),
                output_root="C:/data/out",
                operation=BatchOperation(type="export_3d", formats=("step",)),
                options=BatchOptions(max_files=1),
            )

    def test_request_immutability(self) -> None:
        req = BatchRequest(
            contract_version="1.0",
            request_id="req-1",
            kind="batch_operation",
            input=BatchInputSelection(root="C:/data", files=("part.par",)),
            output_root="C:/data/out",
            operation=BatchOperation(type="export_3d", formats=("step",)),
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            req.request_id = "new-id"  # type: ignore[misc]


class TestBatchResponseAndManifest:
    """Response and manifest models."""

    def test_manifest_reference_nul_rejected(self) -> None:
        with pytest.raises(ValueError, match="without NULs"):
            BatchManifestReference(path="out/\0manifest.json")

    def test_batch_response_immutability(self) -> None:
        resp = BatchResponse(
            contract_version="1.0",
            request_id="req-1",
            status="rejected",
            errors=(BatchDiagnostic(code="INVALID_SCHEMA", message="Rejected"),),
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            resp.status = "completed"  # type: ignore[misc]

    def test_batch_manifest_immutability(self) -> None:
        manifest = BatchManifest(
            manifest_version="1.0",
            contract_version="1.0",
            request_id="req-1",
            status="completed",
            operation=BatchOperation(type="export_3d", formats=("step",)),
            summary=BatchSummary(total=1, accepted=1, partial=0, failed=0, unprocessed=0, cancelled=0),
            results=(
                ManifestFileResult(
                    input="p.par",
                    status="accepted",
                    artifacts=(
                        ManifestArtifactRecord(format="step", relative_path="p.step", size_bytes=100, sha256="c" * 64),
                    ),
                ),
            ),
            engine_version="0.1.0",
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            manifest.status = "failed"  # type: ignore[misc]


class TestCallerListMutationDefense:
    """Proves that passing mutable lists to models converts them to immutable tuples unaffected by source mutations."""

    def test_batch_input_selection_mutation_defense(self) -> None:
        files = ["a.par", "b.par"]
        selection = BatchInputSelection(root="C:/data", files=files)  # type: ignore[arg-type]
        assert selection.files == ("a.par", "b.par")
        files.append("c.par")
        files[0] = "mutated.par"
        assert selection.files == ("a.par", "b.par")

    def test_batch_operation_mutation_defense(self) -> None:
        fmts = ["step", "stl"]
        op = BatchOperation(type="export_3d", formats=fmts)  # type: ignore[arg-type]
        assert op.formats == ("step", "stl")
        fmts.append("pdf")
        fmts[0] = "dxf"
        assert op.formats == ("step", "stl")

    def test_batch_file_result_mutation_defense(self) -> None:
        art_list = [BatchArtifactRecord(format="step", path="a.step")]
        err_list = [BatchDiagnostic(code="DOCUMENT_OPEN_FAILED", message="failed")]
        warn_list = [BatchDiagnostic(code="VERSION_METADATA_UNAVAILABLE", message="warn")]

        res = BatchFileResult(
            input="a.par",
            status="partial",
            artifacts=art_list,  # type: ignore[arg-type]
            errors=err_list,  # type: ignore[arg-type]
            warnings=warn_list,  # type: ignore[arg-type]
        )
        assert len(res.artifacts) == 1
        assert len(res.errors) == 1
        assert len(res.warnings) == 1

        art_list.append(BatchArtifactRecord(format="stl", path="a.stl"))
        err_list.clear()
        warn_list.clear()

        assert len(res.artifacts) == 1
        assert len(res.errors) == 1
        assert len(res.warnings) == 1

    def test_manifest_file_result_mutation_defense(self) -> None:
        art_list = [ManifestArtifactRecord(format="step", relative_path="a.step", size_bytes=10, sha256="0" * 64)]
        err_list = [BatchDiagnostic(code="DOCUMENT_OPEN_FAILED", message="failed")]
        warn_list = [BatchDiagnostic(code="VERSION_METADATA_UNAVAILABLE", message="warn")]

        res = ManifestFileResult(
            input="a.par",
            status="partial",
            artifacts=art_list,  # type: ignore[arg-type]
            errors=err_list,  # type: ignore[arg-type]
            warnings=warn_list,  # type: ignore[arg-type]
        )
        art_list.clear()
        err_list.clear()
        warn_list.clear()

        assert len(res.artifacts) == 1
        assert len(res.errors) == 1
        assert len(res.warnings) == 1

    def test_batch_response_mutation_defense(self) -> None:
        results_list = [
            BatchFileResult(
                input="a.par",
                status="accepted",
                artifacts=(BatchArtifactRecord(format="step", path="a.step"),),
            )
        ]
        unproc_list = ["b.par"]
        canc_list: list[str] = []
        err_list: list[BatchDiagnostic] = []
        warn_list = [BatchDiagnostic(code="VERSION_METADATA_UNAVAILABLE", message="warn")]

        resp = BatchResponse(
            contract_version="1.0",
            request_id="req-mut-1",
            status="completed",
            summary=BatchSummary(total=2, accepted=1, partial=0, failed=0, unprocessed=1, cancelled=0),
            results=results_list,  # type: ignore[arg-type]
            manifest=BatchManifestReference(path="out/man.json"),
            unprocessed_files=unproc_list,  # type: ignore[arg-type]
            cancelled_files=canc_list,  # type: ignore[arg-type]
            errors=err_list,  # type: ignore[arg-type]
            warnings=warn_list,  # type: ignore[arg-type]
        )

        results_list.clear()
        unproc_list.append("c.par")
        warn_list.clear()

        assert len(resp.results) == 1
        assert resp.unprocessed_files == ("b.par",)
        assert len(resp.warnings) == 1

    def test_batch_manifest_mutation_defense(self) -> None:
        results_list = [
            ManifestFileResult(
                input="a.par",
                status="accepted",
                artifacts=(
                    ManifestArtifactRecord(format="step", relative_path="a.step", size_bytes=10, sha256="0" * 64),
                ),
            )
        ]
        unproc_list = ["b.par"]
        canc_list: list[str] = []
        err_list: list[BatchDiagnostic] = []
        warn_list = [BatchDiagnostic(code="VERSION_METADATA_UNAVAILABLE", message="warn")]

        man = BatchManifest(
            manifest_version="1.0",
            contract_version="1.0",
            request_id="req-mut-2",
            status="completed",
            operation=BatchOperation(type="export_3d", formats=("step",)),
            summary=BatchSummary(total=2, accepted=1, partial=0, failed=0, unprocessed=1, cancelled=0),
            results=results_list,  # type: ignore[arg-type]
            engine_version="0.1.0",
            unprocessed_files=unproc_list,  # type: ignore[arg-type]
            cancelled_files=canc_list,  # type: ignore[arg-type]
            errors=err_list,  # type: ignore[arg-type]
            warnings=warn_list,  # type: ignore[arg-type]
        )

        results_list.clear()
        unproc_list.clear()
        warn_list.clear()

        assert len(man.results) == 1
        assert man.unprocessed_files == ("b.par",)
        assert len(man.warnings) == 1
