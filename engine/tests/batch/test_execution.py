"""Unit tests for batch execution value objects, context, outcomes, and progress updates."""

from __future__ import annotations

from pathlib import Path

import pytest

from batch.execution import (
    APPROVED_PROGRESS_PHASES,
    BatchExecutionOutcome,
    BatchExecutionSpec,
    BatchItemContext,
    BatchItemOutcome,
    BatchProgressUpdate,
)
from batch.models import (
    BatchArtifactRecord,
    BatchDiagnostic,
    BatchFileResult,
    BatchInputSelection,
    BatchOperation,
    BatchOptions,
    BatchRequest,
    BatchSummary,
    BatchValidationError,
)


def _make_valid_request(
    operation_type: str = "export_3d",
    formats: tuple[str, ...] = ("step", "stl"),
    files: tuple[str, ...] = ("part1.par", "sub/part2.par"),
    continue_on_error: bool = True,
) -> BatchRequest:
    return BatchRequest(
        contract_version="1.0",
        request_id="req-exec-001",
        kind="batch_operation",
        input=BatchInputSelection(root="models", files=files),
        output_root="output",
        operation=BatchOperation(type=operation_type, formats=formats),  # type: ignore[arg-type]
        options=BatchOptions(continue_on_error=continue_on_error),
    )


class TestBatchExecutionSpec:
    def test_from_request_maps_correctly(self) -> None:
        req = _make_valid_request()
        spec = BatchExecutionSpec.from_request(req)

        assert spec.contract_version == "1.0"
        assert spec.request_id == "req-exec-001"
        assert spec.input_root == "models"
        assert spec.output_root == "output"
        assert spec.inputs == ("part1.par", "sub/part2.par")
        assert spec.operation_id == "export_3d"
        assert spec.formats == ("step", "stl")
        assert spec.continue_on_error is True

    def test_from_request_rejects_invalid_type(self) -> None:
        with pytest.raises(TypeError, match="request must be a BatchRequest"):
            BatchExecutionSpec.from_request("not-a-request")  # type: ignore[arg-type]

    def test_direct_construction_supports_test_operations(self) -> None:
        spec = BatchExecutionSpec(
            contract_version="1.0",
            request_id="test-op-req",
            input_root="in",
            output_root="out",
            inputs=("a.test",),
            operation_id="custom_test_op",
            formats=("step",),
            continue_on_error=False,
        )
        assert spec.operation_id == "custom_test_op"
        assert spec.continue_on_error is False

    def test_immutability_and_validation(self) -> None:
        spec = BatchExecutionSpec(
            contract_version="1.0",
            request_id="req-1",
            input_root="in",
            output_root="out",
            inputs=("a.par",),
            operation_id="export_3d",
            formats=("step",),
        )
        with pytest.raises(AttributeError):
            spec.inputs = ("b.par",)  # type: ignore[misc]

        with pytest.raises(ValueError, match="inputs must contain between 1 and 500 items"):
            BatchExecutionSpec(
                contract_version="1.0",
                request_id="req-1",
                input_root="in",
                output_root="out",
                inputs=(),
                operation_id="export_3d",
                formats=("step",),
            )

        with pytest.raises(ValueError, match="formats must contain between 1 and 10 items"):
            BatchExecutionSpec(
                contract_version="1.0",
                request_id="req-1",
                input_root="in",
                output_root="out",
                inputs=("a.par",),
                operation_id="export_3d",
                formats=(),
            )

        with pytest.raises(TypeError, match="formats must be a tuple or list"):
            BatchExecutionSpec(
                contract_version="1.0",
                request_id="req-1",
                input_root="in",
                output_root="out",
                inputs=("a.par",),
                operation_id="export_3d",
                formats="step",  # type: ignore[arg-type]
            )

        with pytest.raises(TypeError, match="continue_on_error must be a bool"):
            BatchExecutionSpec(
                contract_version="1.0",
                request_id="req-1",
                input_root="in",
                output_root="out",
                inputs=("a.par",),
                operation_id="export_3d",
                formats=("step",),
                continue_on_error=1,  # type: ignore[arg-type]
            )

    def test_rejects_path_traversal_inputs(self) -> None:
        with pytest.raises(BatchValidationError) as exc_info:
            BatchExecutionSpec(
                contract_version="1.0",
                request_id="req-1",
                input_root="in",
                output_root="out",
                inputs=("../secret.par",),
                operation_id="export_3d",
                formats=("step",),
            )
        assert exc_info.value.code == "INPUT_PATH_NOT_ALLOWED"

        with pytest.raises(BatchValidationError) as exc_info2:
            BatchExecutionSpec(
                contract_version="1.0",
                request_id="req-1",
                input_root="in",
                output_root="out",
                inputs=("parts/../../secret.par",),
                operation_id="export_3d",
                formats=("step",),
            )
        assert exc_info2.value.code == "INPUT_PATH_NOT_ALLOWED"

    def test_rejects_duplicate_or_case_colliding_inputs(self) -> None:
        with pytest.raises(BatchValidationError) as exc_info:
            BatchExecutionSpec(
                contract_version="1.0",
                request_id="req-1",
                input_root="in",
                output_root="out",
                inputs=("parts/bracket.par", "parts/BRACKET.par"),
                operation_id="export_3d",
                formats=("step",),
            )
        assert exc_info.value.code == "INPUT_PATH_NOT_ALLOWED"

    def test_canonicalizes_windows_separators(self) -> None:
        spec = BatchExecutionSpec(
            contract_version="1.0",
            request_id="req-1",
            input_root="in",
            output_root="out",
            inputs=("parts\\bracket.par",),
            operation_id="export_3d",
            formats=("step",),
        )
        assert spec.inputs == ("parts/bracket.par",)


class TestBatchItemContext:
    def test_valid_context(self, tmp_path: Path) -> None:
        src = tmp_path / "source" / "a.par"
        work = tmp_path / "work" / "a.step"
        target = tmp_path / "out" / "a.step"

        ctx = BatchItemContext(
            request_id="req-1",
            operation_id="export_3d",
            input="a.par",
            source_path=src,
            format="step",
            target_relative_path="a.step",
            work_path=work,
            target_path=target,
        )

        assert ctx.request_id == "req-1"
        assert ctx.source_path == src
        assert ctx.work_path == work
        assert ctx.target_path == target

    def test_rejects_relative_paths(self) -> None:
        with pytest.raises(ValueError, match="source_path must be an absolute Path"):
            BatchItemContext(
                request_id="req-1",
                operation_id="export_3d",
                input="a.par",
                source_path=Path("rel/source.par"),
                format="step",
                target_relative_path="a.step",
                work_path=Path("C:/abs/work.step"),
                target_path=Path("C:/abs/target.step"),
            )

    def test_rejects_empty_strings(self, tmp_path: Path) -> None:
        abs_p = tmp_path / "file.step"
        with pytest.raises(ValueError, match="input must be a non-empty string"):
            BatchItemContext(
                request_id="req-1",
                operation_id="export_3d",
                input="",
                source_path=abs_p,
                format="step",
                target_relative_path="a.step",
                work_path=abs_p,
                target_path=abs_p,
            )


class TestBatchItemOutcome:
    def test_success_construction(self, tmp_path: Path) -> None:
        target = tmp_path / "out" / "a.step"
        artifact = BatchArtifactRecord(format="step", path=str(target))
        warning = BatchDiagnostic(code="VERSION_METADATA_UNAVAILABLE", message="Build unavailable")

        outcome = BatchItemOutcome.success(format="step", artifact=artifact, warnings=(warning,))
        assert outcome.is_success is True
        assert outcome.artifact == artifact
        assert outcome.errors == ()
        assert outcome.warnings == (warning,)

    def test_failure_construction(self) -> None:
        err = BatchDiagnostic(code="ARTIFACT_EXPORT_FAILED", message="Export failed", format="step")
        outcome = BatchItemOutcome.failure(format="step", errors=(err,))
        assert outcome.is_success is False
        assert outcome.artifact is None
        assert outcome.errors == (err,)
        assert outcome.warnings == ()

    def test_rejects_success_with_error(self, tmp_path: Path) -> None:
        target = tmp_path / "out" / "a.step"
        artifact = BatchArtifactRecord(format="step", path=str(target))
        err = BatchDiagnostic(code="ARTIFACT_EXPORT_FAILED", message="Failed")
        with pytest.raises(ValueError, match="Successful BatchItemOutcome must not contain error"):
            BatchItemOutcome(format="step", artifact=artifact, diagnostics=(err,))

    def test_rejects_failure_without_error(self) -> None:
        with pytest.raises(ValueError, match="Failed BatchItemOutcome must contain at least one error"):
            BatchItemOutcome(format="step", artifact=None, diagnostics=())

    def test_rejects_artifact_format_mismatch(self, tmp_path: Path) -> None:
        target = tmp_path / "out" / "a.stl"
        artifact = BatchArtifactRecord(format="stl", path=str(target))
        with pytest.raises(ValueError, match="Artifact format 'stl' does not match outcome format 'step'"):
            BatchItemOutcome(format="step", artifact=artifact)

    def test_accepts_diagnostics_with_matching_or_none_format(self, tmp_path: Path) -> None:
        target = tmp_path / "out" / "a.step"
        artifact = BatchArtifactRecord(format="step", path=str(target))
        warn_matching = BatchDiagnostic(code="VERSION_METADATA_UNAVAILABLE", message="Warn matching", format="step")
        warn_none = BatchDiagnostic(code="VERSION_METADATA_UNAVAILABLE", message="Warn none", format=None)

        success_outcome = BatchItemOutcome.success(
            format="step",
            artifact=artifact,
            warnings=(warn_matching, warn_none),
        )
        assert len(success_outcome.warnings) == 2

        err_matching = BatchDiagnostic(code="ARTIFACT_EXPORT_FAILED", message="Err matching", format="step")
        err_none = BatchDiagnostic(code="ARTIFACT_EXPORT_FAILED", message="Err none", format=None)
        fail_outcome = BatchItemOutcome.failure(
            format="step",
            errors=(err_matching, err_none),
        )
        assert len(fail_outcome.errors) == 2

    def test_rejects_diagnostics_with_mismatched_format(self, tmp_path: Path) -> None:
        target = tmp_path / "out" / "a.step"
        artifact = BatchArtifactRecord(format="step", path=str(target))
        mismatched_warn = BatchDiagnostic(code="VERSION_METADATA_UNAVAILABLE", message="Warn stl", format="stl")

        with pytest.raises(ValueError, match="Diagnostic format 'stl' does not match outcome format 'step'"):
            BatchItemOutcome.success(
                format="step",
                artifact=artifact,
                warnings=(mismatched_warn,),
            )

        mismatched_err = BatchDiagnostic(code="ARTIFACT_EXPORT_FAILED", message="Err stl", format="stl")
        with pytest.raises(ValueError, match="Diagnostic format 'stl' does not match outcome format 'step'"):
            BatchItemOutcome.failure(
                format="step",
                errors=(mismatched_err,),
            )

    def test_validate_against_context(self, tmp_path: Path) -> None:
        target = tmp_path / "out" / "a.step"
        artifact = BatchArtifactRecord(format="step", path=str(target))
        outcome = BatchItemOutcome.success(format="step", artifact=artifact)

        ctx = BatchItemContext(
            request_id="req-1",
            operation_id="export_3d",
            input="a.par",
            source_path=tmp_path / "a.par",
            format="step",
            target_relative_path="a.step",
            work_path=tmp_path / "work" / "a.step",
            target_path=target,
        )
        # Should not raise
        outcome.validate_against_context(ctx)

        # Mismatched format
        ctx_mismatch_fmt = BatchItemContext(
            request_id="req-1",
            operation_id="export_3d",
            input="a.par",
            source_path=tmp_path / "a.par",
            format="stl",
            target_relative_path="a.stl",
            work_path=tmp_path / "work" / "a.stl",
            target_path=tmp_path / "out" / "a.stl",
        )
        with pytest.raises(ValueError, match="Outcome format 'step' does not match context format 'stl'"):
            outcome.validate_against_context(ctx_mismatch_fmt)

        # Mismatched target path
        ctx_mismatch_path = BatchItemContext(
            request_id="req-1",
            operation_id="export_3d",
            input="a.par",
            source_path=tmp_path / "a.par",
            format="step",
            target_relative_path="a.step",
            work_path=tmp_path / "work" / "a.step",
            target_path=tmp_path / "different" / "a.step",
        )
        with pytest.raises(ValueError, match="does not match context target path"):
            outcome.validate_against_context(ctx_mismatch_path)


class TestBatchExecutionOutcome:
    def test_valid_completed_outcome(self, tmp_path: Path) -> None:
        res = BatchFileResult(
            input="a.par",
            status="accepted",
            artifacts=(BatchArtifactRecord(format="step", path="C:/out/a.step"),),
        )
        summary = BatchSummary(total=1, accepted=1, partial=0, failed=0, unprocessed=0, cancelled=0)
        outcome = BatchExecutionOutcome(
            status="completed",
            request_id="req-1",
            contract_version="1.0",
            summary=summary,
            file_results=(res,),
            output_root=tmp_path / "out",
        )
        assert outcome.status == "completed"
        assert outcome.summary == summary
        assert outcome.file_results == (res,)

    def test_completed_rejects_top_level_errors(self) -> None:
        res = BatchFileResult(
            input="a.par",
            status="accepted",
            artifacts=(BatchArtifactRecord(format="step", path="C:/out/a.step"),),
        )
        summary = BatchSummary(total=1, accepted=1, partial=0, failed=0, unprocessed=0, cancelled=0)
        err = BatchDiagnostic(code="INTERNAL_ERROR", message="Something broke")
        with pytest.raises(ValueError, match="Completed response must not have top-level errors"):
            BatchExecutionOutcome(
                status="completed",
                request_id="req-1",
                contract_version="1.0",
                summary=summary,
                file_results=(res,),
                errors=(err,),
            )

    def test_valid_rejected_outcome(self) -> None:
        err = BatchDiagnostic(code="OUTPUT_TARGET_COLLISION", message="Collision detected")
        outcome = BatchExecutionOutcome(
            status="rejected",
            request_id="req-1",
            contract_version="1.0",
            errors=(err,),
        )
        assert outcome.status == "rejected"
        assert outcome.summary is None
        assert outcome.file_results == ()

    def test_rejected_outcome_rejects_summary(self) -> None:
        summary = BatchSummary(total=0, accepted=0, partial=0, failed=0, unprocessed=0, cancelled=0)
        err = BatchDiagnostic(code="OUTPUT_TARGET_COLLISION", message="Collision detected")
        with pytest.raises(ValueError, match="Rejected response must not include summary"):
            BatchExecutionOutcome(
                status="rejected",
                request_id="req-1",
                contract_version="1.0",
                summary=summary,
                errors=(err,),
            )

    def test_cancelled_outcome_with_cancelled_files(self) -> None:
        summary = BatchSummary(total=1, accepted=0, partial=0, failed=0, unprocessed=0, cancelled=1)
        outcome = BatchExecutionOutcome(
            status="cancelled",
            request_id="req-1",
            contract_version="1.0",
            summary=summary,
            cancelled_files=("a.par",),
        )
        assert outcome.status == "cancelled"
        assert outcome.cancelled_files == ("a.par",)

    def test_cancelled_outcome_with_mid_file_cancellation(self) -> None:
        cancel_diag = BatchDiagnostic(code="BATCH_CANCELLED", message="Cancelled mid-file", format="stl")
        res = BatchFileResult(
            input="a.par",
            status="partial",
            artifacts=(BatchArtifactRecord(format="step", path="C:/out/a.step"),),
            errors=(cancel_diag,),
        )
        summary = BatchSummary(total=1, accepted=0, partial=1, failed=0, unprocessed=0, cancelled=0)
        outcome = BatchExecutionOutcome(
            status="cancelled",
            request_id="req-1",
            contract_version="1.0",
            summary=summary,
            file_results=(res,),
            cancelled_files=(),
        )
        assert outcome.status == "cancelled"

    def test_cancelled_outcome_rejects_missing_cancellation_proof(self) -> None:
        res = BatchFileResult(
            input="a.par",
            status="accepted",
            artifacts=(BatchArtifactRecord(format="step", path="C:/out/a.step"),),
        )
        summary = BatchSummary(total=1, accepted=1, partial=0, failed=0, unprocessed=0, cancelled=0)
        with pytest.raises(
            ValueError, match="Cancelled response requires at least one cancelled file or BATCH_CANCELLED"
        ):
            BatchExecutionOutcome(
                status="cancelled",
                request_id="req-1",
                contract_version="1.0",
                summary=summary,
                file_results=(res,),
                cancelled_files=(),
            )

    def test_partition_disjointness_enforced(self) -> None:
        res = BatchFileResult(
            input="a.par",
            status="accepted",
            artifacts=(BatchArtifactRecord(format="step", path="C:/out/a.step"),),
        )
        summary = BatchSummary(total=2, accepted=1, partial=0, failed=0, unprocessed=1, cancelled=0)
        err = BatchDiagnostic(code="DOCUMENT_OPEN_FAILED", message="Open failed")
        with pytest.raises(ValueError, match="Overlapping or duplicate unprocessed file"):
            BatchExecutionOutcome(
                status="failed",
                request_id="req-1",
                contract_version="1.0",
                summary=summary,
                file_results=(res,),
                unprocessed_files=("a.par",),  # Duplicate of res.input!
                errors=(err,),
            )

    def test_rejected_outcome_rejects_warnings(self) -> None:
        err = BatchDiagnostic(code="OUTPUT_TARGET_COLLISION", message="Collision detected")
        warn = BatchDiagnostic(code="VERSION_METADATA_UNAVAILABLE", message="Build unavailable")
        with pytest.raises(ValueError, match="Rejected response must not include warnings"):
            BatchExecutionOutcome(
                status="rejected",
                request_id="req-1",
                contract_version="1.0",
                errors=(err,),
                warnings=(warn,),
            )

    def test_failed_outcome_requires_top_level_error(self) -> None:
        res = BatchFileResult(
            input="a.par",
            status="failed",
            errors=(BatchDiagnostic(code="DOCUMENT_OPEN_FAILED", message="Open failed"),),
        )
        summary = BatchSummary(total=1, accepted=0, partial=0, failed=1, unprocessed=0, cancelled=0)
        with pytest.raises(ValueError, match="Failed response requires at least one top-level error"):
            BatchExecutionOutcome(
                status="failed",
                request_id="req-1",
                contract_version="1.0",
                summary=summary,
                file_results=(res,),
                errors=(),  # Missing required top-level error!
            )

    def test_completed_outcome_rejects_cancellation_marker(self) -> None:
        cancel_diag = BatchDiagnostic(code="BATCH_CANCELLED", message="Cancelled", format="step")
        res = BatchFileResult(
            input="a.par",
            status="failed",
            errors=(cancel_diag,),
        )
        summary = BatchSummary(total=1, accepted=0, partial=0, failed=1, unprocessed=0, cancelled=0)
        with pytest.raises(ValueError, match="Completed response must not contain BATCH_CANCELLED errors"):
            BatchExecutionOutcome(
                status="completed",
                request_id="req-1",
                contract_version="1.0",
                summary=summary,
                file_results=(res,),
            )

    def test_duplicate_cancellation_markers_rejected(self) -> None:
        cancel1 = BatchDiagnostic(code="BATCH_CANCELLED", message="Cancelled 1", format="step")
        cancel2 = BatchDiagnostic(code="BATCH_CANCELLED", message="Cancelled 2", format="stl")
        res = BatchFileResult(
            input="a.par",
            status="failed",
            errors=(cancel1, cancel2),
        )
        summary = BatchSummary(total=1, accepted=0, partial=0, failed=1, unprocessed=0, cancelled=0)
        with pytest.raises(
            ValueError, match="Only one BATCH_CANCELLED error marker is permitted across all file results"
        ):
            BatchExecutionOutcome(
                status="cancelled",
                request_id="req-1",
                contract_version="1.0",
                summary=summary,
                file_results=(res,),
            )


class TestBatchProgressUpdate:
    def test_phases_set(self) -> None:
        expected = {
            "batch_started",
            "file_started",
            "format_started",
            "format_finished",
            "file_finished",
            "batch_finished",
        }
        assert expected == APPROVED_PROGRESS_PHASES

    def test_valid_progress_update_and_dict_serialization(self) -> None:
        update = BatchProgressUpdate(
            request_id="req-1",
            phase="file_started",
            total_files=5,
            completed_files=2,
            current_file="bracket.par",
        )
        d = update.to_dict()
        assert d == {
            "request_id": "req-1",
            "phase": "file_started",
            "total_files": 5,
            "completed_files": 2,
            "current_file": "bracket.par",
        }
        assert "current_format" not in d
        assert "file_status" not in d

    def test_valid_format_and_file_finished_updates(self) -> None:
        fmt_up = BatchProgressUpdate(
            request_id="req-1",
            phase="format_started",
            total_files=3,
            completed_files=1,
            current_file="a.par",
            current_format="step",
        )
        assert fmt_up.current_format == "step"

        fmt_done = BatchProgressUpdate(
            request_id="req-1",
            phase="format_finished",
            total_files=3,
            completed_files=1,
            current_file="a.par",
            current_format="step",
        )
        assert fmt_done.phase == "format_finished"

        file_done = BatchProgressUpdate(
            request_id="req-1",
            phase="file_finished",
            total_files=3,
            completed_files=2,
            current_file="a.par",
            file_status="accepted",
        )
        assert file_done.file_status == "accepted"

        batch_done = BatchProgressUpdate(
            request_id="req-1",
            phase="batch_finished",
            total_files=3,
            completed_files=3,
        )
        assert batch_done.phase == "batch_finished"

    def test_batch_started_constraints(self) -> None:
        # Valid
        BatchProgressUpdate(request_id="req-1", phase="batch_started", total_files=5, completed_files=0)

        # Forbidden fields
        with pytest.raises(ValueError, match="batch_started requires completed_files == 0"):
            BatchProgressUpdate(request_id="req-1", phase="batch_started", total_files=5, completed_files=1)

        with pytest.raises(ValueError, match="batch_started must not include current_file"):
            BatchProgressUpdate(
                request_id="req-1", phase="batch_started", total_files=5, completed_files=0, current_file="a.par"
            )

    def test_file_started_and_format_constraints(self) -> None:
        with pytest.raises(ValueError, match="file_started requires current_file"):
            BatchProgressUpdate(request_id="req-1", phase="file_started", total_files=5, completed_files=0)

        with pytest.raises(ValueError, match="file_started must not include current_format"):
            BatchProgressUpdate(
                request_id="req-1",
                phase="file_started",
                total_files=5,
                completed_files=0,
                current_file="a.par",
                current_format="step",
            )

        with pytest.raises(ValueError, match="format_started requires current_format"):
            BatchProgressUpdate(
                request_id="req-1",
                phase="format_started",
                total_files=5,
                completed_files=0,
                current_file="a.par",
            )

    def test_file_finished_constraints(self) -> None:
        with pytest.raises(ValueError, match="file_finished requires file_status"):
            BatchProgressUpdate(
                request_id="req-1",
                phase="file_finished",
                total_files=5,
                completed_files=1,
                current_file="a.par",
            )

        with pytest.raises(ValueError, match="file_finished requires completed_files >= 1"):
            BatchProgressUpdate(
                request_id="req-1",
                phase="file_finished",
                total_files=5,
                completed_files=0,
                current_file="a.par",
                file_status="accepted",
            )

    def test_batch_finished_constraints(self) -> None:
        with pytest.raises(ValueError, match="batch_finished must not include current_file"):
            BatchProgressUpdate(
                request_id="req-1",
                phase="batch_finished",
                total_files=5,
                completed_files=5,
                current_file="a.par",
            )

    def test_current_file_rejects_path_traversal(self) -> None:
        with pytest.raises((BatchValidationError, ValueError)):
            BatchProgressUpdate(
                request_id="req-1",
                phase="file_started",
                total_files=5,
                completed_files=0,
                current_file="../secret.par",
            )

        with pytest.raises((BatchValidationError, ValueError)):
            BatchProgressUpdate(
                request_id="req-1",
                phase="file_started",
                total_files=5,
                completed_files=0,
                current_file="sub\\part.par",  # Non-canonical backslash
            )

    def test_rejects_invalid_file_status(self) -> None:
        with pytest.raises(ValueError, match="Invalid file_status"):
            BatchProgressUpdate(
                request_id="req-1",
                phase="file_finished",
                total_files=5,
                completed_files=1,
                current_file="a.par",
                file_status="cancelled",  # type: ignore[arg-type] # Not a valid FileResultStatus
            )

    def test_rejects_invalid_phase(self) -> None:
        with pytest.raises(ValueError, match="Invalid progress phase"):
            BatchProgressUpdate(
                request_id="req-1",
                phase="unknown_phase",  # type: ignore[arg-type]
                total_files=5,
                completed_files=0,
            )

    def test_rejects_completed_greater_than_total(self) -> None:
        with pytest.raises(ValueError, match="cannot exceed total_files"):
            BatchProgressUpdate(
                request_id="req-1",
                phase="file_finished",
                total_files=3,
                completed_files=4,
                current_file="a.par",
                file_status="accepted",
            )
