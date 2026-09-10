"""Unit tests for FilesystemBatchSafetyBoundary preparation and preflight validation."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from batch.allocation import (
    BatchSafetyRejectionError,
    allocate_batch_work,
)
from batch.execution import BatchExecutionSpec
from batch.filesystem import PathIdentity, get_path_identity, is_symlink_or_reparse_point
from batch.safety import FilesystemBatchSafetyBoundary


def _make_spec(
    input_root: Path,
    output_root: Path,
    inputs: tuple[str, ...],
    formats: tuple[str, ...] = ("step", "stl"),
    request_id: str = "req-safety-001",
) -> BatchExecutionSpec:
    return BatchExecutionSpec(
        contract_version="1.0",
        request_id=request_id,
        input_root=str(input_root),
        output_root=str(output_root),
        inputs=inputs,
        operation_id="export_3d",
        formats=formats,
        continue_on_error=True,
    )


class TestSafetyBoundaryPreparation:
    """Tests for FilesystemBatchSafetyBoundary.prepare() lifecycle and validation."""

    def test_init_and_properties(self) -> None:
        boundary = FilesystemBatchSafetyBoundary()
        assert boundary.is_prepared is False

    def test_roots_strictly_derived_from_spec(self, tmp_path: Path) -> None:
        in_dir = tmp_path / "spec_input"
        out_dir = tmp_path / "spec_output"
        in_dir.mkdir()
        out_dir.mkdir()
        (in_dir / "part.par").write_bytes(b"data")

        boundary = FilesystemBatchSafetyBoundary()
        spec = _make_spec(in_dir, out_dir, ("part.par",))
        work = allocate_batch_work(spec)

        prepared = boundary.prepare(spec, work)
        assert prepared.output_root == out_dir.resolve()
        assert prepared.files[0].source_path.resolve() == (in_dir / "part.par").resolve()

    def test_type_guards_on_prepare(self, tmp_path: Path) -> None:
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        boundary = FilesystemBatchSafetyBoundary()
        spec = _make_spec(in_dir, out_dir, ("bracket.par",))
        work = allocate_batch_work(spec)

        with pytest.raises(TypeError, match="spec must be BatchExecutionSpec"):
            boundary.prepare("not a spec", work)  # type: ignore[arg-type]

        with pytest.raises(TypeError, match="work must be AllocatedBatchWork"):
            boundary.prepare(spec, "not work")  # type: ignore[arg-type]

    def test_single_use_guard(self, tmp_path: Path) -> None:
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()
        (in_dir / "bracket.par").write_bytes(b"solid edge part data")

        boundary = FilesystemBatchSafetyBoundary()
        spec = _make_spec(in_dir, out_dir, ("bracket.par",))
        work = allocate_batch_work(spec)

        boundary.prepare(spec, work)
        assert boundary.is_prepared is True

        with pytest.raises(BatchSafetyRejectionError) as exc_info:
            boundary.prepare(spec, work)

        assert exc_info.value.code == "INTERNAL_ERROR"
        assert exc_info.value.request_id == spec.request_id

    def test_missing_input_root_rejected(self, tmp_path: Path) -> None:
        missing_in = tmp_path / "missing_input"
        out_dir = tmp_path / "output"
        out_dir.mkdir()

        boundary = FilesystemBatchSafetyBoundary()
        spec = _make_spec(missing_in, out_dir, ("bracket.par",))
        work = allocate_batch_work(spec)

        with pytest.raises(BatchSafetyRejectionError) as exc_info:
            boundary.prepare(spec, work)

        assert exc_info.value.code == "INPUT_ROOT_NOT_FOUND"

    def test_missing_output_root_rejected(self, tmp_path: Path) -> None:
        in_dir = tmp_path / "input"
        in_dir.mkdir()
        missing_out = tmp_path / "missing_output"

        boundary = FilesystemBatchSafetyBoundary()
        spec = _make_spec(in_dir, missing_out, ("bracket.par",))
        work = allocate_batch_work(spec)

        with pytest.raises(BatchSafetyRejectionError) as exc_info:
            boundary.prepare(spec, work)

        assert exc_info.value.code == "OUTPUT_ROOT_UNAVAILABLE"

    def test_input_root_identity_capture_failure_normalized(self, tmp_path: Path) -> None:
        """When input root identity capture raises, translate to typed INPUT_PATH_NOT_ALLOWED."""
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()
        (in_dir / "part.par").write_bytes(b"data")

        real_get_identity = get_path_identity

        def _fail_input_identity(p: Path, *, follow_symlinks: bool = False) -> PathIdentity:
            if p.resolve() == in_dir.resolve():
                raise OSError("Simulated inaccessible input root")
            return real_get_identity(p, follow_symlinks=follow_symlinks)

        boundary = FilesystemBatchSafetyBoundary()
        spec = _make_spec(in_dir, out_dir, ("part.par",))
        work = allocate_batch_work(spec)

        with (
            patch("batch.safety_preparation.get_path_identity", side_effect=_fail_input_identity),
            pytest.raises(BatchSafetyRejectionError) as exc_info,
        ):
            boundary.prepare(spec, work)

        assert exc_info.value.code == "INPUT_PATH_NOT_ALLOWED"
        assert "Failed to capture input root filesystem identity" in exc_info.value.message

    def test_input_root_non_directory_rejected(self, tmp_path: Path) -> None:
        """When input root identity does not represent a directory, fail closed with INPUT_PATH_NOT_ALLOWED."""
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()
        (in_dir / "part.par").write_bytes(b"data")

        real_get_identity = get_path_identity

        def _non_dir_input_identity(p: Path, *, follow_symlinks: bool = False) -> PathIdentity:
            real_id = real_get_identity(p, follow_symlinks=follow_symlinks)
            if p.resolve() == in_dir.resolve():
                return PathIdentity(device=real_id.device, inode=real_id.inode, mode=0o100644)
            return real_id

        boundary = FilesystemBatchSafetyBoundary()
        spec = _make_spec(in_dir, out_dir, ("part.par",))
        work = allocate_batch_work(spec)

        with (
            patch("batch.safety_preparation.get_path_identity", side_effect=_non_dir_input_identity),
            pytest.raises(BatchSafetyRejectionError) as exc_info,
        ):
            boundary.prepare(spec, work)

        assert exc_info.value.code == "INPUT_PATH_NOT_ALLOWED"
        assert "Input root is not a directory" in exc_info.value.message

    def test_output_root_identity_capture_failure_normalized(self, tmp_path: Path) -> None:
        """When output root identity capture raises, translate to typed OUTPUT_ROOT_UNAVAILABLE."""
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()
        (in_dir / "part.par").write_bytes(b"data")

        real_get_identity = get_path_identity

        def _fail_output_identity(p: Path, *, follow_symlinks: bool = False) -> PathIdentity:
            if p.resolve() == out_dir.resolve():
                raise OSError("Simulated inaccessible output root")
            return real_get_identity(p, follow_symlinks=follow_symlinks)

        boundary = FilesystemBatchSafetyBoundary()
        spec = _make_spec(in_dir, out_dir, ("part.par",))
        work = allocate_batch_work(spec)

        with (
            patch("batch.safety_preparation.get_path_identity", side_effect=_fail_output_identity),
            pytest.raises(BatchSafetyRejectionError) as exc_info,
        ):
            boundary.prepare(spec, work)

        assert exc_info.value.code == "OUTPUT_ROOT_UNAVAILABLE"
        assert "Failed to capture output root filesystem identity" in exc_info.value.message

    def test_output_root_non_directory_rejected(self, tmp_path: Path) -> None:
        """When output root identity does not represent a directory, fail closed with OUTPUT_ROOT_UNAVAILABLE."""
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()
        (in_dir / "part.par").write_bytes(b"data")

        real_get_identity = get_path_identity

        def _non_dir_output_identity(p: Path, *, follow_symlinks: bool = False) -> PathIdentity:
            real_id = real_get_identity(p, follow_symlinks=follow_symlinks)
            if p.resolve() == out_dir.resolve():
                return PathIdentity(device=real_id.device, inode=real_id.inode, mode=0o100644)
            return real_id

        boundary = FilesystemBatchSafetyBoundary()
        spec = _make_spec(in_dir, out_dir, ("part.par",))
        work = allocate_batch_work(spec)

        with (
            patch("batch.safety_preparation.get_path_identity", side_effect=_non_dir_output_identity),
            pytest.raises(BatchSafetyRejectionError) as exc_info,
        ):
            boundary.prepare(spec, work)

        assert exc_info.value.code == "OUTPUT_ROOT_UNAVAILABLE"
        assert "Output root is not a directory" in exc_info.value.message

    def test_happy_path_preparation(self, tmp_path: Path) -> None:
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        (in_dir / "part.par").write_bytes(b"model data par")
        (in_dir / "sheet.psm").write_bytes(b"model data psm")
        (in_dir / "asm.asm").write_bytes(b"model data asm")
        (in_dir / "draft.dft").write_bytes(b"model data dft")

        spec = _make_spec(
            in_dir,
            out_dir,
            inputs=("part.par", "sheet.psm", "asm.asm", "draft.dft"),
            formats=("step", "stl"),
        )
        work = allocate_batch_work(spec)

        boundary = FilesystemBatchSafetyBoundary()
        prepared_work = boundary.prepare(spec, work)

        assert boundary.is_prepared is True
        assert len(prepared_work.files) == 4
        assert prepared_work.output_root == out_dir.resolve()

        for prep_file in prepared_work.files:
            assert prep_file.preflight_error is None
            assert prep_file.source_path.exists()
            assert len(prep_file.formats) == 2

            for prep_fmt in prep_file.formats:
                assert prep_fmt.preflight_error is None
                assert prep_fmt.target_path.parent.resolve() == out_dir.resolve()
                assert prep_fmt.work_path.parent.name.startswith(".cad-copilot-work-")
                assert not prep_fmt.work_path.exists()
                assert not prep_fmt.work_path.parent.exists()
                expected_ext = ".step" if prep_fmt.format == "step" else ".stl"
                assert prep_fmt.work_path.suffix.lower() == expected_ext
                assert prep_fmt.target_path.suffix.lower() == expected_ext

    def test_missing_source_file_preflight_error(self, tmp_path: Path) -> None:
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        (in_dir / "present.par").write_bytes(b"present content")

        spec = _make_spec(in_dir, out_dir, inputs=("present.par", "missing.par"))
        work = allocate_batch_work(spec)

        boundary = FilesystemBatchSafetyBoundary()
        prepared = boundary.prepare(spec, work)

        assert prepared.files[0].preflight_error is None
        assert prepared.files[1].preflight_error is not None
        assert prepared.files[1].preflight_error.code == "INPUT_FILE_NOT_FOUND"
        assert "missing.par" in prepared.files[1].preflight_error.message
        assert str(tmp_path) not in prepared.files[1].preflight_error.message

    def test_zero_byte_source_preflight_error(self, tmp_path: Path) -> None:
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        (in_dir / "empty.par").write_bytes(b"")

        spec = _make_spec(in_dir, out_dir, inputs=("empty.par",))
        work = allocate_batch_work(spec)

        boundary = FilesystemBatchSafetyBoundary()
        prepared = boundary.prepare(spec, work)

        assert prepared.files[0].preflight_error is not None
        assert prepared.files[0].preflight_error.code == "INPUT_PATH_NOT_ALLOWED"
        assert "zero bytes" in prepared.files[0].preflight_error.message

    def test_non_regular_source_preflight_error(self, tmp_path: Path) -> None:
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        (in_dir / "directory.par").mkdir()

        spec = _make_spec(in_dir, out_dir, inputs=("directory.par",))
        work = allocate_batch_work(spec)

        boundary = FilesystemBatchSafetyBoundary()
        prepared = boundary.prepare(spec, work)

        assert prepared.files[0].preflight_error is not None
        assert prepared.files[0].preflight_error.code == "INPUT_PATH_NOT_ALLOWED"
        assert "not a regular file" in prepared.files[0].preflight_error.message

    def test_reparse_source_preflight_error(self, tmp_path: Path) -> None:
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        (in_dir / "link.par").write_bytes(b"data")

        spec = _make_spec(in_dir, out_dir, inputs=("link.par",))
        work = allocate_batch_work(spec)
        boundary = FilesystemBatchSafetyBoundary()

        orig_reparse = is_symlink_or_reparse_point

        def _mock_reparse(p: Path) -> bool:
            if p.name == "link.par":
                return True
            return orig_reparse(p)

        with (
            patch("batch.safety_preparation.is_symlink_or_reparse_point", side_effect=_mock_reparse),
            patch("batch.filesystem.is_symlink_or_reparse_point", side_effect=_mock_reparse),
        ):
            prepared = boundary.prepare(spec, work)

        assert prepared.files[0].preflight_error is not None
        assert prepared.files[0].preflight_error.code == "INPUT_PATH_NOT_ALLOWED"
        assert "reparse point" in prepared.files[0].preflight_error.message

    def test_alias_detection_rejects_request(self, tmp_path: Path) -> None:
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        file1 = in_dir / "part1.par"
        file2 = in_dir / "part2.par"
        file1.write_bytes(b"unique part bytes")

        try:
            os.link(file1, file2)
        except OSError:
            pytest.skip("Hard links not supported in this test environment")

        spec = _make_spec(in_dir, out_dir, inputs=("part1.par", "part2.par"))
        work = allocate_batch_work(spec)

        boundary = FilesystemBatchSafetyBoundary()
        with pytest.raises(BatchSafetyRejectionError) as exc_info:
            boundary.prepare(spec, work)

        assert exc_info.value.code == "INPUT_PATH_NOT_ALLOWED"
        assert "Duplicate source file identity detected in request" in exc_info.value.message

    def test_zero_inode_source_file_rejected_in_preflight(self, tmp_path: Path) -> None:
        """When source file identity is unavailable (zero inode), fail closed with INPUT_PATH_NOT_ALLOWED."""
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        (in_dir / "part1.par").write_bytes(b"content 1")

        orig_get_identity = get_path_identity

        def _zero_inode_for_source(p: Path, *, follow_symlinks: bool = False) -> PathIdentity:
            real_id = orig_get_identity(p, follow_symlinks=follow_symlinks)
            if p.name == "part1.par":
                return PathIdentity(device=real_id.device, inode=0, mode=real_id.mode)
            return real_id

        spec = _make_spec(in_dir, out_dir, inputs=("part1.par",))
        work = allocate_batch_work(spec)
        boundary = FilesystemBatchSafetyBoundary()

        with patch("batch.safety_preparation.get_path_identity", side_effect=_zero_inode_for_source):
            prepared = boundary.prepare(spec, work)

        assert prepared.files[0].preflight_error is not None
        assert prepared.files[0].preflight_error.code == "INPUT_PATH_NOT_ALLOWED"
        assert "zero inode" in prepared.files[0].preflight_error.message

    def test_zero_inode_roots_fail_closed(self, tmp_path: Path) -> None:
        """When input or output root identity has zero inode, boundary.prepare() must fail closed."""
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()
        (in_dir / "part1.par").write_bytes(b"content 1")

        orig_get_identity = get_path_identity

        def _zero_inode_input_root(p: Path, *, follow_symlinks: bool = False) -> PathIdentity:
            real_id = orig_get_identity(p, follow_symlinks=follow_symlinks)
            if p.resolve() == in_dir.resolve():
                return PathIdentity(device=real_id.device, inode=0, mode=real_id.mode)
            return real_id

        spec = _make_spec(in_dir, out_dir, inputs=("part1.par",))
        work = allocate_batch_work(spec)

        boundary_in = FilesystemBatchSafetyBoundary()
        with (
            patch("batch.safety_preparation.get_path_identity", side_effect=_zero_inode_input_root),
            pytest.raises(BatchSafetyRejectionError) as exc_info,
        ):
            boundary_in.prepare(spec, work)
        assert exc_info.value.code == "INPUT_PATH_NOT_ALLOWED"
        assert "zero inode" in exc_info.value.message

        def _zero_inode_output_root(p: Path, *, follow_symlinks: bool = False) -> PathIdentity:
            real_id = orig_get_identity(p, follow_symlinks=follow_symlinks)
            if p.resolve() == out_dir.resolve():
                return PathIdentity(device=real_id.device, inode=0, mode=real_id.mode)
            return real_id

        boundary_out = FilesystemBatchSafetyBoundary()
        with (
            patch("batch.safety_preparation.get_path_identity", side_effect=_zero_inode_output_root),
            pytest.raises(BatchSafetyRejectionError) as exc_info,
        ):
            boundary_out.prepare(spec, work)
        assert exc_info.value.code == "OUTPUT_ROOT_UNAVAILABLE"
        assert "zero inode" in exc_info.value.message

    def test_target_already_exists_per_format(self, tmp_path: Path) -> None:
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        (in_dir / "bracket.par").write_bytes(b"cad part")
        (out_dir / "bracket.step").write_bytes(b"pre-existing step")

        spec = _make_spec(in_dir, out_dir, inputs=("bracket.par",), formats=("step", "stl"))
        work = allocate_batch_work(spec)

        boundary = FilesystemBatchSafetyBoundary()
        prepared = boundary.prepare(spec, work)

        prep_file = prepared.files[0]
        assert prep_file.preflight_error is None
        # Format 0 (step) already exists
        assert prep_file.formats[0].preflight_error is not None
        assert prep_file.formats[0].preflight_error.code == "TARGET_ALREADY_EXISTS"
        assert prep_file.formats[0].preflight_error.format == "step"
        # Format 1 (stl) does not exist and is safe
        assert prep_file.formats[1].preflight_error is None

    def test_target_parent_component_conflict(self, tmp_path: Path) -> None:
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        (in_dir / "nested").mkdir()
        (in_dir / "nested" / "part.par").write_bytes(b"cad part")

        # Conflict: create a regular file where directory is expected
        (out_dir / "nested").write_bytes(b"conflict file")

        spec = _make_spec(in_dir, out_dir, inputs=("nested/part.par",), formats=("step",))
        work = allocate_batch_work(spec)

        boundary = FilesystemBatchSafetyBoundary()
        prepared = boundary.prepare(spec, work)

        assert prepared.files[0].formats[0].preflight_error is not None
        assert prepared.files[0].formats[0].preflight_error.code == "OUTPUT_ROOT_UNAVAILABLE"


class TestDiagnosticZeroRawPathSanitization:
    """SEC-07: Verify that diagnostics never contain raw workstation paths or raw exceptions."""

    def test_diagnostics_contain_no_raw_paths(self, tmp_path: Path) -> None:
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        spec = _make_spec(in_dir, out_dir, inputs=("nonexistent_part.par",))
        work = allocate_batch_work(spec)
        boundary = FilesystemBatchSafetyBoundary()
        prepared = boundary.prepare(spec, work)

        diag = prepared.files[0].preflight_error
        assert diag is not None
        assert str(in_dir) not in diag.message
        assert str(out_dir) not in diag.message
        assert "nonexistent_part.par" in diag.message
