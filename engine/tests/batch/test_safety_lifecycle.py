"""Unit tests for FilesystemBatchSafetyBoundary source integrity lifecycle."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from batch.allocation import (
    PreparedBatchFile,
    PreparedBatchFormat,
    allocate_batch_work,
)
from batch.execution import BatchExecutionSpec
from batch.filesystem import PathIdentity, is_symlink_or_reparse_point
from batch.safety import FilesystemBatchSafetyBoundary
from batch.source_integrity import SourceIntegrityError, SourceSnapshot


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


class TestSafetyBoundarySourceIntegrity:
    """Tests for verify_before_open() and verify_after_close() source integrity enforcement."""

    def test_before_open_unprepared_error(self, tmp_path: Path) -> None:
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        boundary = FilesystemBatchSafetyBoundary()

        fake_file = PreparedBatchFile(
            input="part.par",
            source_path=in_dir / "part.par",
            formats=(
                PreparedBatchFormat(
                    format="step",
                    target_relative_path="part.step",
                    work_path=out_dir / "work" / "part.step",
                    target_path=out_dir / "part.step",
                ),
            ),
        )
        diag = boundary.verify_before_open(fake_file)
        assert diag is not None
        assert diag.code == "INTERNAL_ERROR"

    def test_before_open_preflight_error_passthrough(self, tmp_path: Path) -> None:
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        spec = _make_spec(in_dir, out_dir, inputs=("missing.par",))
        work = allocate_batch_work(spec)
        boundary = FilesystemBatchSafetyBoundary()
        prepared = boundary.prepare(spec, work)

        diag = boundary.verify_before_open(prepared.files[0])
        assert diag is not None
        assert diag.code == "INPUT_FILE_NOT_FOUND"

        # Calling again on preflight-failed file is rejected as invariant failure
        second_diag = boundary.verify_before_open(prepared.files[0])
        assert second_diag is not None
        assert second_diag.code == "INTERNAL_ERROR"

    def test_before_open_happy_path(self, tmp_path: Path) -> None:
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        (in_dir / "part.par").write_bytes(b"cad part content")

        spec = _make_spec(in_dir, out_dir, inputs=("part.par",))
        work = allocate_batch_work(spec)
        boundary = FilesystemBatchSafetyBoundary()
        prepared = boundary.prepare(spec, work)

        diag = boundary.verify_before_open(prepared.files[0])
        assert diag is None

    def test_before_open_duplicate_rejected(self, tmp_path: Path) -> None:
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        (in_dir / "part.par").write_bytes(b"cad part content")

        spec = _make_spec(in_dir, out_dir, inputs=("part.par",))
        work = allocate_batch_work(spec)
        boundary = FilesystemBatchSafetyBoundary()
        prepared = boundary.prepare(spec, work)

        assert boundary.verify_before_open(prepared.files[0]) is None

        second_diag = boundary.verify_before_open(prepared.files[0])
        assert second_diag is not None
        assert second_diag.code == "INTERNAL_ERROR"
        assert "Duplicate or out-of-order pre-open" in second_diag.message

    def test_before_open_detects_input_root_deletion(self, tmp_path: Path) -> None:
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        (in_dir / "part.par").write_bytes(b"cad part content")

        spec = _make_spec(in_dir, out_dir, inputs=("part.par",))
        work = allocate_batch_work(spec)
        boundary = FilesystemBatchSafetyBoundary()
        prepared = boundary.prepare(spec, work)

        with patch("pathlib.Path.exists", return_value=False):
            diag = boundary.verify_before_open(prepared.files[0])
            assert diag is not None
            assert diag.code == "INPUT_PATH_NOT_ALLOWED"

    def test_before_open_detects_input_root_substitution(self, tmp_path: Path) -> None:
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        (in_dir / "part.par").write_bytes(b"cad part content")

        spec = _make_spec(in_dir, out_dir, inputs=("part.par",))
        work = allocate_batch_work(spec)
        boundary = FilesystemBatchSafetyBoundary()
        prepared = boundary.prepare(spec, work)

        substitute_id = PathIdentity(device=9999, inode=8888, mode=0o40755)
        with patch("batch.safety.get_path_identity", return_value=substitute_id):
            diag = boundary.verify_before_open(prepared.files[0])
            assert diag is not None
            assert diag.code == "INPUT_PATH_NOT_ALLOWED"
            assert "identity changed unexpectedly" in diag.message

    def test_before_open_detects_reparse_source(self, tmp_path: Path) -> None:
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        (in_dir / "part.par").write_bytes(b"cad part content")

        spec = _make_spec(in_dir, out_dir, inputs=("part.par",))
        work = allocate_batch_work(spec)
        boundary = FilesystemBatchSafetyBoundary()
        prepared = boundary.prepare(spec, work)

        orig_reparse = is_symlink_or_reparse_point

        def _mock_reparse(p: Path) -> bool:
            if p.name == "part.par":
                return True
            return orig_reparse(p)

        with patch("batch.safety.is_symlink_or_reparse_point", side_effect=_mock_reparse):
            diag = boundary.verify_before_open(prepared.files[0])
            assert diag is not None
            assert diag.code == "INPUT_PATH_NOT_ALLOWED"
            assert "symbolic link" in diag.message

    def test_before_open_containment_violation(self, tmp_path: Path) -> None:
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        (in_dir / "part.par").write_bytes(b"cad part content")

        spec = _make_spec(in_dir, out_dir, inputs=("part.par",))
        work = allocate_batch_work(spec)
        boundary = FilesystemBatchSafetyBoundary()
        prepared = boundary.prepare(spec, work)

        other_file = tmp_path / "outside.par"
        other_file.write_bytes(b"outside")
        tampered_prep_file = PreparedBatchFile(
            input="part.par",
            source_path=other_file,
            formats=prepared.files[0].formats,
        )

        diag = boundary.verify_before_open(tampered_prep_file)
        assert diag is not None
        assert diag.code == "INTERNAL_ERROR"

    def test_before_open_detects_source_disappearance_mapped_to_not_found(self, tmp_path: Path) -> None:
        """Source disappearing between prepare() and verify_before_open() returns INPUT_FILE_NOT_FOUND."""
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        source_path = in_dir / "part.par"
        source_path.write_bytes(b"initial cad content")

        spec = _make_spec(in_dir, out_dir, inputs=("part.par",))
        work = allocate_batch_work(spec)
        boundary = FilesystemBatchSafetyBoundary()
        prepared = boundary.prepare(spec, work)

        # Remove source file after preparation
        source_path.unlink()

        diag = boundary.verify_before_open(prepared.files[0])
        assert diag is not None
        assert diag.code == "INPUT_FILE_NOT_FOUND"
        assert "not found" in diag.message.lower()

    def test_before_open_detects_source_replacement(self, tmp_path: Path) -> None:
        """Replacing the source file between prepare() and verify_before_open() is rejected."""
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        source_path = in_dir / "part.par"
        source_path.write_bytes(b"initial cad content")

        spec = _make_spec(in_dir, out_dir, inputs=("part.par",))
        work = allocate_batch_work(spec)
        boundary = FilesystemBatchSafetyBoundary()
        prepared = boundary.prepare(spec, work)

        prep_info = boundary._files[prepared.files[0].input]
        assert prep_info.source_id is not None
        replaced_id = PathIdentity(
            device=prep_info.source_id.device,
            inode=prep_info.source_id.inode + 100,
            mode=prep_info.source_id.mode,
        )

        with patch("batch.safety.get_path_identity") as mock_id:

            def _mock_id(path: Path) -> PathIdentity:
                if path.resolve() == in_dir.resolve():
                    return boundary._input_root_id  # type: ignore[return-value]
                return replaced_id

            mock_id.side_effect = _mock_id
            diag = boundary.verify_before_open(prepared.files[0])
            assert diag is not None
            assert diag.code == "INPUT_PATH_NOT_ALLOWED"
            assert "replaced after preparation" in diag.message

    def test_before_open_detects_nested_reparse_component(self, tmp_path: Path) -> None:
        """A directory component becoming a reparse point before pre-open is rejected."""
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        sub_dir = in_dir / "sub"
        sub_dir.mkdir(parents=True)
        out_dir.mkdir()

        source_path = sub_dir / "part.par"
        source_path.write_bytes(b"nested cad content")

        spec = _make_spec(in_dir, out_dir, inputs=("sub/part.par",))
        work = allocate_batch_work(spec)
        boundary = FilesystemBatchSafetyBoundary()
        prepared = boundary.prepare(spec, work)

        orig_reparse = is_symlink_or_reparse_point

        def _mock_reparse(p: Path) -> bool:
            if p.name == "sub":
                return True
            return orig_reparse(p)

        with patch("batch.filesystem.is_symlink_or_reparse_point", side_effect=_mock_reparse):
            diag = boundary.verify_before_open(prepared.files[0])
            assert diag is not None
            assert diag.code == "INPUT_PATH_NOT_ALLOWED"
            assert "sub" in diag.message
            assert "reparse point" in diag.message or "symbolic link" in diag.message

    def test_before_open_detects_newly_zero_byte_source(self, tmp_path: Path) -> None:
        """Source file truncated to zero bytes before pre-open is rejected with INPUT_PATH_NOT_ALLOWED."""
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        source_path = in_dir / "part.par"
        source_path.write_bytes(b"initial cad content")

        spec = _make_spec(in_dir, out_dir, inputs=("part.par",))
        work = allocate_batch_work(spec)
        boundary = FilesystemBatchSafetyBoundary()
        prepared = boundary.prepare(spec, work)

        # Truncate source file
        source_path.write_bytes(b"")

        diag = boundary.verify_before_open(prepared.files[0])
        assert diag is not None
        assert diag.code == "INPUT_PATH_NOT_ALLOWED"
        assert "cannot be zero bytes" in diag.message

    def test_before_open_detects_non_regular_and_zero_inode(self, tmp_path: Path) -> None:
        """Source becoming non-regular or having zero inode is rejected with INPUT_PATH_NOT_ALLOWED."""
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        source_path = in_dir / "part.par"
        source_path.write_bytes(b"initial cad content")

        spec = _make_spec(in_dir, out_dir, inputs=("part.par",))
        work = allocate_batch_work(spec)
        boundary = FilesystemBatchSafetyBoundary()
        prepared = boundary.prepare(spec, work)

        # 1. Non-regular
        non_regular_id = PathIdentity(device=10, inode=20, mode=0o040755)
        with patch("batch.safety.get_path_identity") as mock_id:
            mock_id.side_effect = lambda p: (
                boundary._input_root_id if p.resolve() == in_dir.resolve() else non_regular_id
            )
            diag = boundary.verify_before_open(prepared.files[0])
            assert diag is not None
            assert diag.code == "INPUT_PATH_NOT_ALLOWED"
            assert "not a regular file" in diag.message

        # Re-prepare for zero inode check
        boundary2 = FilesystemBatchSafetyBoundary()
        prepared2 = boundary2.prepare(spec, work)

        # 2. Zero inode
        zero_id = PathIdentity(device=10, inode=0, mode=0o100644)
        with patch("batch.safety.get_path_identity") as mock_id:
            mock_id.side_effect = lambda p: boundary2._input_root_id if p.resolve() == in_dir.resolve() else zero_id
            diag = boundary2.verify_before_open(prepared2.files[0])
            assert diag is not None
            assert diag.code == "INPUT_PATH_NOT_ALLOWED"
            assert "zero inode" in diag.message

    def test_before_open_capture_diagnostic_mapping(self, tmp_path: Path) -> None:
        """Diagnostic mapping for SourceIntegrityError during pre-open capture."""
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        source_path = in_dir / "part.par"
        source_path.write_bytes(b"initial cad content")

        spec = _make_spec(in_dir, out_dir, inputs=("part.par",))
        work = allocate_batch_work(spec)

        # 1. "not_found" -> INPUT_FILE_NOT_FOUND
        b1 = FilesystemBatchSafetyBoundary()
        p1 = b1.prepare(spec, work)
        with patch("batch.safety.capture_source_snapshot", side_effect=SourceIntegrityError("not_found", "file gone")):
            diag = b1.verify_before_open(p1.files[0])
            assert diag is not None
            assert diag.code == "INPUT_FILE_NOT_FOUND"

        # 2. "unstable" -> INPUT_PATH_NOT_ALLOWED
        b2 = FilesystemBatchSafetyBoundary()
        p2 = b2.prepare(spec, work)
        with patch("batch.safety.capture_source_snapshot", side_effect=SourceIntegrityError("unstable", "modified")):
            diag = b2.verify_before_open(p2.files[0])
            assert diag is not None
            assert diag.code == "INPUT_PATH_NOT_ALLOWED"

        # 3. "unreadable" -> INPUT_PATH_NOT_ALLOWED
        b3 = FilesystemBatchSafetyBoundary()
        p3 = b3.prepare(spec, work)
        with patch("batch.safety.capture_source_snapshot", side_effect=SourceIntegrityError("unreadable", "locked")):
            diag = b3.verify_before_open(p3.files[0])
            assert diag is not None
            assert diag.code == "INPUT_PATH_NOT_ALLOWED"

        # 4. generic exception -> INPUT_PATH_NOT_ALLOWED
        b4 = FilesystemBatchSafetyBoundary()
        p4 = b4.prepare(spec, work)
        with patch("batch.safety.capture_source_snapshot", side_effect=RuntimeError("unexpected")):
            diag = b4.verify_before_open(p4.files[0])
            assert diag is not None
            assert diag.code == "INPUT_PATH_NOT_ALLOWED"

    def test_before_open_detects_snapshot_identity_mismatch_with_preparation(self, tmp_path: Path) -> None:
        """A stable replacement occurring during snapshot capture is rejected before opening."""
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        source_path = in_dir / "part.par"
        source_path.write_bytes(b"initial cad content")

        spec = _make_spec(in_dir, out_dir, inputs=("part.par",))
        work = allocate_batch_work(spec)
        boundary = FilesystemBatchSafetyBoundary()
        prepared = boundary.prepare(spec, work)

        prep_info = boundary._files[prepared.files[0].input]
        assert prep_info.source_id is not None

        # Simulate capture_source_snapshot returning a snapshot with a stable different identity
        different_id = PathIdentity(
            device=prep_info.source_id.device,
            inode=prep_info.source_id.inode + 999,
            mode=prep_info.source_id.mode,
        )
        fake_snapshot = SourceSnapshot(
            identity=different_id,
            size_bytes=len(b"replaced cad content"),
            mtime_ns=1_000_000,
            sha256="b" * 64,
        )

        with patch("batch.safety.capture_source_snapshot", return_value=fake_snapshot):
            diag = boundary.verify_before_open(prepared.files[0])
            assert diag is not None
            assert diag.code == "INPUT_PATH_NOT_ALLOWED"
            assert "replaced after preparation" in diag.message

        # Verify state transitioned to closed and before_open_snapshot was not retained
        assert prep_info.state == "closed"
        assert prep_info.before_open_snapshot is None

    def test_before_open_rejects_snapshot_with_zero_inode_or_non_regular_or_empty(self, tmp_path: Path) -> None:
        """Snapshots with unavailable identity, non-regular mode, or zero bytes are rejected."""
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        source_path = in_dir / "part.par"
        source_path.write_bytes(b"initial cad content")

        spec = _make_spec(in_dir, out_dir, inputs=("part.par",))
        work = allocate_batch_work(spec)

        # 1. Zero inode snapshot
        b1 = FilesystemBatchSafetyBoundary()
        p1 = b1.prepare(spec, work)
        snap_zero_ino = SourceSnapshot(
            identity=PathIdentity(device=1, inode=0, mode=0o100644),
            size_bytes=100,
            mtime_ns=1000,
            sha256="c" * 64,
        )
        with patch("batch.safety.capture_source_snapshot", return_value=snap_zero_ino):
            d1 = b1.verify_before_open(p1.files[0])
            assert d1 is not None
            assert d1.code == "INPUT_PATH_NOT_ALLOWED"
            assert "zero inode" in d1.message

        # 2. Non-regular snapshot
        b2 = FilesystemBatchSafetyBoundary()
        p2 = b2.prepare(spec, work)
        p2_info = b2._files[p2.files[0].input]
        assert p2_info.source_id is not None
        snap_non_reg = SourceSnapshot(
            identity=PathIdentity(device=p2_info.source_id.device, inode=p2_info.source_id.inode, mode=0o040755),
            size_bytes=100,
            mtime_ns=1000,
            sha256="d" * 64,
        )
        with patch("batch.safety.capture_source_snapshot", return_value=snap_non_reg):
            d2 = b2.verify_before_open(p2.files[0])
            assert d2 is not None
            assert d2.code == "INPUT_PATH_NOT_ALLOWED"
            assert "not a regular file" in d2.message

        # 3. Empty (zero bytes) snapshot
        b3 = FilesystemBatchSafetyBoundary()
        p3 = b3.prepare(spec, work)
        p3_info = b3._files[p3.files[0].input]
        assert p3_info.source_id is not None
        snap_empty = SourceSnapshot(
            identity=PathIdentity(device=p3_info.source_id.device, inode=p3_info.source_id.inode, mode=0o100644),
            size_bytes=0,
            mtime_ns=1000,
            sha256="e" * 64,
        )
        with patch("batch.safety.capture_source_snapshot", return_value=snap_empty):
            d3 = b3.verify_before_open(p3.files[0])
            assert d3 is not None
            assert d3.code == "INPUT_PATH_NOT_ALLOWED"
            assert "cannot be zero bytes" in d3.message

    def test_after_close_unprepared_error(self, tmp_path: Path) -> None:
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        boundary = FilesystemBatchSafetyBoundary()

        fake_file = PreparedBatchFile(
            input="part.par",
            source_path=in_dir / "part.par",
            formats=(
                PreparedBatchFormat(
                    format="step",
                    target_relative_path="part.step",
                    work_path=out_dir / "work" / "part.step",
                    target_path=out_dir / "part.step",
                ),
            ),
        )
        diag = boundary.verify_after_close(fake_file)
        assert diag is not None
        assert diag.code == "INTERNAL_ERROR"

    def test_after_close_without_before_open_rejected(self, tmp_path: Path) -> None:
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        (in_dir / "part.par").write_bytes(b"cad part content")

        spec = _make_spec(in_dir, out_dir, inputs=("part.par",))
        work = allocate_batch_work(spec)
        boundary = FilesystemBatchSafetyBoundary()
        prepared = boundary.prepare(spec, work)

        diag = boundary.verify_after_close(prepared.files[0])
        assert diag is not None
        assert diag.code == "INTERNAL_ERROR"
        assert "without valid pre-open snapshot" in diag.message

    def test_after_close_duplicate_rejected(self, tmp_path: Path) -> None:
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        (in_dir / "part.par").write_bytes(b"cad part content")

        spec = _make_spec(in_dir, out_dir, inputs=("part.par",))
        work = allocate_batch_work(spec)
        boundary = FilesystemBatchSafetyBoundary()
        prepared = boundary.prepare(spec, work)

        assert boundary.verify_before_open(prepared.files[0]) is None
        assert boundary.verify_after_close(prepared.files[0]) is None

        # Second call must fail because snapshot was cleared and state is closed
        second_diag = boundary.verify_after_close(prepared.files[0])
        assert second_diag is not None
        assert second_diag.code == "INTERNAL_ERROR"
        assert "without valid pre-open snapshot" in second_diag.message

    def test_after_close_happy_path_clears_snapshot(self, tmp_path: Path) -> None:
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        (in_dir / "part.par").write_bytes(b"cad part content")

        spec = _make_spec(in_dir, out_dir, inputs=("part.par",))
        work = allocate_batch_work(spec)
        boundary = FilesystemBatchSafetyBoundary()
        prepared = boundary.prepare(spec, work)

        assert boundary.verify_before_open(prepared.files[0]) is None
        assert boundary.verify_after_close(prepared.files[0]) is None

    def test_after_close_detects_source_modification(self, tmp_path: Path) -> None:
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        source_path = in_dir / "part.par"
        source_path.write_bytes(b"initial content before open")

        spec = _make_spec(in_dir, out_dir, inputs=("part.par",))
        work = allocate_batch_work(spec)
        boundary = FilesystemBatchSafetyBoundary()
        prepared = boundary.prepare(spec, work)

        assert boundary.verify_before_open(prepared.files[0]) is None

        # Simulate mutation during execution
        source_path.write_bytes(b"MUTATED CONTENT AFTER CLOSE")

        diag = boundary.verify_after_close(prepared.files[0])
        assert diag is not None
        assert diag.code == "SOURCE_INTEGRITY_FAILED"

    def test_after_close_detects_source_timestamp_touch(self, tmp_path: Path) -> None:
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        source_path = in_dir / "part.par"
        source_path.write_bytes(b"initial content before open")

        spec = _make_spec(in_dir, out_dir, inputs=("part.par",))
        work = allocate_batch_work(spec)
        boundary = FilesystemBatchSafetyBoundary()
        prepared = boundary.prepare(spec, work)

        assert boundary.verify_before_open(prepared.files[0]) is None

        # Touch timestamp without changing bytes
        stat_orig = source_path.stat()
        os.utime(source_path, ns=(stat_orig.st_atime_ns, stat_orig.st_mtime_ns + 10_000_000_000))

        diag = boundary.verify_after_close(prepared.files[0])
        assert diag is not None
        assert diag.code == "SOURCE_INTEGRITY_FAILED"

    def test_after_close_detects_source_deletion(self, tmp_path: Path) -> None:
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        source_path = in_dir / "part.par"
        source_path.write_bytes(b"initial content before open")

        spec = _make_spec(in_dir, out_dir, inputs=("part.par",))
        work = allocate_batch_work(spec)
        boundary = FilesystemBatchSafetyBoundary()
        prepared = boundary.prepare(spec, work)

        assert boundary.verify_before_open(prepared.files[0]) is None

        source_path.unlink()

        diag = boundary.verify_after_close(prepared.files[0])
        assert diag is not None
        assert diag.code == "SOURCE_INTEGRITY_FAILED"

    def test_after_close_detects_root_substitution(self, tmp_path: Path) -> None:
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        source_path = in_dir / "part.par"
        source_path.write_bytes(b"initial content before open")

        spec = _make_spec(in_dir, out_dir, inputs=("part.par",))
        work = allocate_batch_work(spec)
        boundary = FilesystemBatchSafetyBoundary()
        prepared = boundary.prepare(spec, work)

        assert boundary.verify_before_open(prepared.files[0]) is None

        substitute_id = PathIdentity(device=9999, inode=8888, mode=0o40755)
        with patch("batch.safety.get_path_identity", return_value=substitute_id):
            diag = boundary.verify_after_close(prepared.files[0])
            assert diag is not None
            assert diag.code == "SOURCE_INTEGRITY_FAILED"
