"""Unit tests for pure work allocation, collision detection, and safety boundary seam."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from batch.allocation import (
    APPROVED_FORMAT_SUFFIXES,
    AllocatedBatchFile,
    AllocatedBatchFormat,
    AllocatedBatchWork,
    BatchAllocationCollisionError,
    BatchSafetyBoundary,
    BatchSafetyRejectionError,
    PreparedBatchFile,
    PreparedBatchFormat,
    PreparedBatchWork,
    allocate_batch_work,
    build_collision_rejection,
    validate_prepared_work_consistency,
)
from batch.execution import BatchExecutionSpec
from batch.models import BatchDiagnostic


def _make_spec(
    inputs: tuple[str, ...],
    formats: tuple[str, ...] = ("step", "stl"),
    operation_id: str = "export_3d",
) -> BatchExecutionSpec:
    return BatchExecutionSpec(
        contract_version="1.0",
        request_id="req-alloc-001",
        input_root="models",
        output_root="output",
        inputs=inputs,
        operation_id=operation_id,
        formats=formats,
        continue_on_error=True,
    )


class TestPureWorkAllocation:
    def test_approved_suffixes_mapping(self) -> None:
        assert APPROVED_FORMAT_SUFFIXES == {
            "step": ".step",
            "stl": ".stl",
            "pdf": ".pdf",
            "dxf": ".dxf",
        }

    def test_mirrors_directory_nesting_and_replaces_only_final_suffix(self) -> None:
        spec = _make_spec(
            inputs=(
                "parts/bracket.par",
                "sub/deep/gear.part.par",
                "root.par",
            ),
            formats=("step", "stl"),
        )

        work = allocate_batch_work(spec)
        assert len(work.files) == 3
        assert isinstance(work.files[0], AllocatedBatchFile)

        # File 1
        assert work.files[0].input == "parts/bracket.par"
        assert work.files[0].formats == (
            AllocatedBatchFormat(format="step", target_relative_path="parts/bracket.step"),
            AllocatedBatchFormat(format="stl", target_relative_path="parts/bracket.stl"),
        )

        # File 2: only replaces the final suffix (.par), preserving .part
        assert work.files[1].input == "sub/deep/gear.part.par"
        assert work.files[1].formats == (
            AllocatedBatchFormat(format="step", target_relative_path="sub/deep/gear.part.step"),
            AllocatedBatchFormat(format="stl", target_relative_path="sub/deep/gear.part.stl"),
        )

        # File 3
        assert work.files[2].input == "root.par"
        assert work.files[2].formats == (
            AllocatedBatchFormat(format="step", target_relative_path="root.step"),
            AllocatedBatchFormat(format="stl", target_relative_path="root.stl"),
        )

    def test_drawing_formats_allocation(self) -> None:
        spec = _make_spec(
            inputs=("drawings/assembly.dft",),
            formats=("pdf", "dxf"),
            operation_id="publish_drawing",
        )
        work = allocate_batch_work(spec)
        assert len(work.files) == 1
        assert work.files[0].formats == (
            AllocatedBatchFormat(format="pdf", target_relative_path="drawings/assembly.pdf"),
            AllocatedBatchFormat(format="dxf", target_relative_path="drawings/assembly.dxf"),
        )

    def test_preserves_caller_file_and_format_order(self) -> None:
        spec = _make_spec(
            inputs=("z_file.par", "a_file.par", "m_file.par"),
            formats=("stl", "step"),
        )
        work = allocate_batch_work(spec)
        assert [f.input for f in work.files] == ["z_file.par", "a_file.par", "m_file.par"]
        assert [fmt.format for fmt in work.files[0].formats] == ["stl", "step"]

    def test_detects_case_insensitive_collision_across_different_sources(self) -> None:
        # e.g. .par and .psm both mapping to .step in the same directory
        spec = _make_spec(
            inputs=("parts/bracket.par", "parts/bracket.psm"),
            formats=("step",),
        )
        with pytest.raises(BatchAllocationCollisionError) as exc_info:
            allocate_batch_work(spec)

        err = exc_info.value
        assert err.code == "OUTPUT_TARGET_COLLISION"
        assert err.target_relative_path == "parts/bracket.step"
        assert "parts/bracket.step" in err.message

    def test_detects_case_folded_target_collision(self) -> None:
        spec = _make_spec(
            inputs=("parts/Bracket.par", "parts/bracket.psm"),
            formats=("step",),
        )
        with pytest.raises(BatchAllocationCollisionError) as exc_info:
            allocate_batch_work(spec)

        assert exc_info.value.target_relative_path.casefold() == "parts/bracket.step"

    def test_build_collision_rejection(self) -> None:
        spec = _make_spec(inputs=("parts/a.par", "parts/a.psm"), formats=("step",))
        err = BatchAllocationCollisionError("parts/a.step", request_id=spec.request_id)
        outcome = build_collision_rejection(spec, err)

        assert outcome.status == "rejected"
        assert outcome.request_id == spec.request_id
        assert len(outcome.errors) == 1
        assert outcome.errors[0].code == "OUTPUT_TARGET_COLLISION"
        assert "parts/a.step" in outcome.errors[0].message
        assert outcome.summary is None
        assert outcome.file_results == ()

    def test_zero_filesystem_access(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def forbid_fs(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("Filesystem access strictly forbidden during allocation")

        monkeypatch.setattr(Path, "resolve", forbid_fs)
        monkeypatch.setattr(Path, "exists", forbid_fs)
        monkeypatch.setattr(os, "stat", forbid_fs)

        spec = _make_spec(inputs=("a.par", "b/c.par"), formats=("step", "stl"))
        # Must execute cleanly with zero filesystem calls
        work = allocate_batch_work(spec)
        assert len(work.files) == 2


class TestPreparedWorkModels:
    def test_prepared_format_requires_absolute_paths(self, tmp_path: Path) -> None:
        work_p = tmp_path / "work" / "a.step"
        target_p = tmp_path / "out" / "a.step"

        fmt = PreparedBatchFormat(
            format="step",
            target_relative_path="a.step",
            work_path=work_p,
            target_path=target_p,
        )
        assert fmt.work_path == work_p
        assert fmt.target_path == target_p
        assert fmt.preflight_error is None

        with pytest.raises(ValueError, match="work_path must be an absolute Path"):
            PreparedBatchFormat(
                format="step",
                target_relative_path="a.step",
                work_path=Path("rel/work.step"),
                target_path=target_p,
            )

    def test_prepared_file_with_preflight_error(self, tmp_path: Path) -> None:
        err = BatchDiagnostic(code="INPUT_FILE_NOT_FOUND", message="File does not exist")
        work_p = tmp_path / "work" / "a.step"
        target_p = tmp_path / "out" / "a.step"

        prep_fmt = PreparedBatchFormat(
            format="step",
            target_relative_path="a.step",
            work_path=work_p,
            target_path=target_p,
        )

        prep_file = PreparedBatchFile(
            input="a.par",
            source_path=tmp_path / "src" / "a.par",
            formats=(prep_fmt,),
            preflight_error=err,
        )
        assert prep_file.preflight_error == err

    def test_prepared_format_rejects_warning_preflight_error(self, tmp_path: Path) -> None:
        warn = BatchDiagnostic(code="VERSION_METADATA_UNAVAILABLE", message="Warning")
        with pytest.raises(ValueError, match="preflight_error must not be a warning diagnostic"):
            PreparedBatchFormat(
                format="step",
                target_relative_path="a.step",
                work_path=tmp_path / "work" / "a.step",
                target_path=tmp_path / "out" / "a.step",
                preflight_error=warn,
            )

    def test_prepared_file_rejects_warning_preflight_error(self, tmp_path: Path) -> None:
        warn = BatchDiagnostic(code="VERSION_METADATA_UNAVAILABLE", message="Warning")
        prep_fmt = PreparedBatchFormat(
            format="step",
            target_relative_path="a.step",
            work_path=tmp_path / "work" / "a.step",
            target_path=tmp_path / "out" / "a.step",
        )
        with pytest.raises(ValueError, match="preflight_error must not be a warning diagnostic"):
            PreparedBatchFile(
                input="a.par",
                source_path=tmp_path / "src" / "a.par",
                formats=(prep_fmt,),
                preflight_error=warn,
            )

    def test_prepared_work_requires_absolute_output_root(self, tmp_path: Path) -> None:
        work_p = tmp_path / "work" / "a.step"
        target_p = tmp_path / "out" / "a.step"
        prep_fmt = PreparedBatchFormat(
            format="step",
            target_relative_path="a.step",
            work_path=work_p,
            target_path=target_p,
        )
        prep_file = PreparedBatchFile(
            input="a.par",
            source_path=tmp_path / "src" / "a.par",
            formats=(prep_fmt,),
        )

        prep_work = PreparedBatchWork(
            files=(prep_file,),
            output_root=tmp_path / "out",
        )
        assert prep_work.output_root == tmp_path / "out"

        with pytest.raises(ValueError, match="output_root must be an absolute Path"):
            PreparedBatchWork(files=(prep_file,), output_root=Path("rel/out"))

    def test_prepared_format_rejects_path_traversal(self, tmp_path: Path) -> None:
        work_p = tmp_path / "work" / "a.step"
        target_p = tmp_path / "out" / "a.step"

        with pytest.raises(ValueError, match="canonical posix relative path"):
            PreparedBatchFormat(
                format="step",
                target_relative_path="../elsewhere.step",
                work_path=work_p,
                target_path=target_p,
            )

        with pytest.raises(ValueError, match="canonical posix relative path"):
            PreparedBatchFormat(
                format="step",
                target_relative_path="a/../../elsewhere.step",
                work_path=work_p,
                target_path=target_p,
            )

    def test_allocated_format_rejects_path_traversal(self) -> None:
        with pytest.raises(ValueError, match="canonical posix relative path"):
            AllocatedBatchFormat(
                format="step",
                target_relative_path="../elsewhere.step",
            )


class TestSafetyBoundaryProtocol:
    def test_fake_safety_boundary_compliance(self, tmp_path: Path) -> None:
        class FakeSafetyBoundary:
            def prepare(self, spec: BatchExecutionSpec, work: AllocatedBatchWork) -> PreparedBatchWork:
                out_root = tmp_path / "out"
                files: list[PreparedBatchFile] = []
                for f in work.files:
                    fmts: list[PreparedBatchFormat] = []
                    for fmt in f.formats:
                        fmts.append(
                            PreparedBatchFormat(
                                format=fmt.format,
                                target_relative_path=fmt.target_relative_path,
                                work_path=tmp_path / "work" / fmt.target_relative_path,
                                target_path=out_root / fmt.target_relative_path,
                            )
                        )
                    files.append(
                        PreparedBatchFile(
                            input=f.input,
                            source_path=tmp_path / "in" / f.input,
                            formats=tuple(fmts),
                        )
                    )
                return PreparedBatchWork(files=tuple(files), output_root=out_root)

            def verify_before_open(self, prepared_file: PreparedBatchFile) -> BatchDiagnostic | None:
                return None

            def verify_after_close(self, prepared_file: PreparedBatchFile) -> BatchDiagnostic | None:
                return None

        # Verify static protocol assignment
        boundary: BatchSafetyBoundary = FakeSafetyBoundary()
        spec = _make_spec(inputs=("bracket.par",), formats=("step",))
        allocated = allocate_batch_work(spec)
        prep = boundary.prepare(spec, allocated)

        assert len(prep.files) == 1
        assert boundary.verify_before_open(prep.files[0]) is None
        assert boundary.verify_after_close(prep.files[0]) is None

    def test_safety_rejection_error(self) -> None:
        diag = BatchDiagnostic(code="INPUT_ROOT_NOT_FOUND", message="Input root does not exist")
        err = BatchSafetyRejectionError(diag, request_id="req-123")
        assert err.code == "INPUT_ROOT_NOT_FOUND"
        assert err.message == "Input root does not exist"
        assert err.diagnostic == diag


class TestPreparedWorkConsistency:
    @pytest.fixture
    def sample_work(self, tmp_path: Path) -> tuple[AllocatedBatchWork, PreparedBatchWork]:
        spec = _make_spec(inputs=("part1.par", "sub/part2.par"), formats=("step", "stl"))
        allocated = allocate_batch_work(spec)
        out_root = tmp_path / "out"

        prep_files = []
        for f in allocated.files:
            prep_fmts = []
            for fmt in f.formats:
                prep_fmts.append(
                    PreparedBatchFormat(
                        format=fmt.format,
                        target_relative_path=fmt.target_relative_path,
                        work_path=tmp_path / "work" / fmt.target_relative_path,
                        target_path=out_root / fmt.target_relative_path,
                    )
                )
            prep_files.append(
                PreparedBatchFile(
                    input=f.input,
                    source_path=tmp_path / "in" / f.input,
                    formats=tuple(prep_fmts),
                )
            )

        prepared = PreparedBatchWork(files=tuple(prep_files), output_root=out_root)
        return allocated, prepared

    def test_matching_work_passes(self, sample_work: tuple[AllocatedBatchWork, PreparedBatchWork]) -> None:
        allocated, prepared = sample_work
        validate_prepared_work_consistency(allocated, prepared)

    def test_rejects_type_mismatch(self, sample_work: tuple[AllocatedBatchWork, PreparedBatchWork]) -> None:
        allocated, prepared = sample_work
        with pytest.raises(TypeError, match="allocated must be AllocatedBatchWork"):
            validate_prepared_work_consistency("not-allocated", prepared)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="prepared must be PreparedBatchWork"):
            validate_prepared_work_consistency(allocated, "not-prepared")  # type: ignore[arg-type]

    def test_detects_dropped_file(self, sample_work: tuple[AllocatedBatchWork, PreparedBatchWork]) -> None:
        allocated, prepared = sample_work
        truncated_prep = PreparedBatchWork(files=(prepared.files[0],), output_root=prepared.output_root)
        with pytest.raises(ValueError, match="file count \\(1\\) does not match allocated file count \\(2\\)"):
            validate_prepared_work_consistency(allocated, truncated_prep)

    def test_detects_reordered_file(self, sample_work: tuple[AllocatedBatchWork, PreparedBatchWork]) -> None:
        allocated, prepared = sample_work
        reordered_prep = PreparedBatchWork(
            files=(prepared.files[1], prepared.files[0]),
            output_root=prepared.output_root,
        )
        with pytest.raises(ValueError, match=r"input 'sub/part2\.par' does not match allocated input 'part1\.par'"):
            validate_prepared_work_consistency(allocated, reordered_prep)

    def test_detects_dropped_format(self, sample_work: tuple[AllocatedBatchWork, PreparedBatchWork]) -> None:
        allocated, prepared = sample_work
        bad_file0 = PreparedBatchFile(
            input=prepared.files[0].input,
            source_path=prepared.files[0].source_path,
            formats=(prepared.files[0].formats[0],),  # Dropped second format
        )
        bad_prep = PreparedBatchWork(
            files=(bad_file0, prepared.files[1]),
            output_root=prepared.output_root,
        )
        with pytest.raises(ValueError, match=r"format count \(1\) does not match allocated format count \(2\)"):
            validate_prepared_work_consistency(allocated, bad_prep)

    def test_detects_reordered_format(self, sample_work: tuple[AllocatedBatchWork, PreparedBatchWork]) -> None:
        allocated, prepared = sample_work
        bad_file0 = PreparedBatchFile(
            input=prepared.files[0].input,
            source_path=prepared.files[0].source_path,
            formats=(prepared.files[0].formats[1], prepared.files[0].formats[0]),  # Swapped format order
        )
        bad_prep = PreparedBatchWork(
            files=(bad_file0, prepared.files[1]),
            output_root=prepared.output_root,
        )
        with pytest.raises(ValueError, match=r"format\[0\] 'stl' does not match allocated format 'step'"):
            validate_prepared_work_consistency(allocated, bad_prep)

    def test_detects_rewritten_target_relative_path(
        self, sample_work: tuple[AllocatedBatchWork, PreparedBatchWork]
    ) -> None:
        allocated, prepared = sample_work
        tampered_fmt0 = PreparedBatchFormat(
            format=prepared.files[0].formats[0].format,
            target_relative_path="different/path.step",  # Rewritten!
            work_path=prepared.files[0].formats[0].work_path,
            target_path=prepared.files[0].formats[0].target_path,
        )
        bad_file0 = PreparedBatchFile(
            input=prepared.files[0].input,
            source_path=prepared.files[0].source_path,
            formats=(tampered_fmt0, prepared.files[0].formats[1]),
        )
        bad_prep = PreparedBatchWork(
            files=(bad_file0, prepared.files[1]),
            output_root=prepared.output_root,
        )
        with pytest.raises(
            ValueError,
            match=r"target_relative_path 'different/path\.step' does not match allocated 'part1\.step'",
        ):
            validate_prepared_work_consistency(allocated, bad_prep)

    def test_consistency_rejects_warning_preflight_error(
        self, sample_work: tuple[AllocatedBatchWork, PreparedBatchWork]
    ) -> None:
        allocated, prepared = sample_work
        tampered_file = PreparedBatchFile(
            input=prepared.files[0].input,
            source_path=prepared.files[0].source_path,
            formats=prepared.files[0].formats,
        )
        warn = BatchDiagnostic(code="VERSION_METADATA_UNAVAILABLE", message="Warning")
        object.__setattr__(tampered_file, "preflight_error", warn)

        tampered_prep = PreparedBatchWork(
            files=(tampered_file, prepared.files[1]),
            output_root=prepared.output_root,
        )
        with pytest.raises(ValueError, match="contains invalid preflight error"):
            validate_prepared_work_consistency(allocated, tampered_prep)
