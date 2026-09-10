"""Tests for immutable source snapshots, streaming SHA-256, and integrity verification."""

from __future__ import annotations

import hashlib
import os
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from batch.filesystem import PathIdentity
from batch.source_integrity import (
    SourceIntegrityError,
    SourceSnapshot,
    capture_source_snapshot,
    verify_snapshot_equality,
)


class TestSourceSnapshot:
    """Tests for SourceSnapshot immutable dataclass."""

    def test_snapshot_attributes_and_immutability(self) -> None:
        identity = PathIdentity(device=1, inode=2, mode=0o100644)
        digest = "a" * 64
        snapshot = SourceSnapshot(
            identity=identity,
            size_bytes=1024,
            mtime_ns=1700000000000000,
            sha256=digest,
        )
        assert snapshot.identity == identity
        assert snapshot.size_bytes == 1024
        assert snapshot.mtime_ns == 1700000000000000
        assert snapshot.sha256 == digest

        with pytest.raises(FrozenInstanceError):
            snapshot.size_bytes = 2048  # type: ignore[misc]

    def test_snapshot_type_validation(self) -> None:
        identity = PathIdentity(device=1, inode=2, mode=0o100644)
        digest = "a" * 64

        with pytest.raises(TypeError, match="identity must be PathIdentity"):
            SourceSnapshot(identity="bad", size_bytes=10, mtime_ns=1, sha256=digest)  # type: ignore[arg-type]

        with pytest.raises(ValueError, match="size_bytes must be a non-negative int"):
            SourceSnapshot(identity=identity, size_bytes=-1, mtime_ns=1, sha256=digest)

        with pytest.raises(TypeError, match="mtime_ns must be int"):
            SourceSnapshot(identity=identity, size_bytes=10, mtime_ns="bad", sha256=digest)  # type: ignore[arg-type]

        with pytest.raises(ValueError, match="sha256 must be a 64-character lowercase hex string"):
            SourceSnapshot(identity=identity, size_bytes=10, mtime_ns=1, sha256="bad_hash")

        with pytest.raises(ValueError, match="sha256 must be a 64-character lowercase hex string"):
            SourceSnapshot(identity=identity, size_bytes=10, mtime_ns=1, sha256="A" * 64)  # uppercase forbidden


class TestCaptureSourceSnapshot:
    """Tests for capture_source_snapshot."""

    def test_capture_known_content(self, tmp_path: Path) -> None:
        file_path = tmp_path / "model.par"
        content = b"SOLID EDGE TEST FILE CONTENTS FOR SNAPSHOT"
        file_path.write_bytes(content)

        expected_hash = hashlib.sha256(content).hexdigest().lower()
        expected_size = len(content)

        snapshot = capture_source_snapshot(file_path)
        assert snapshot.size_bytes == expected_size
        assert snapshot.sha256 == expected_hash
        assert snapshot.identity.is_regular_file is True

    def test_streaming_multi_chunk(self, tmp_path: Path) -> None:
        file_path = tmp_path / "large_model.par"
        content = b"0123456789abcdef" * 100  # 1600 bytes
        file_path.write_bytes(content)

        expected_hash = hashlib.sha256(content).hexdigest().lower()

        # Stream with 32-byte chunks to test multi-chunk reading
        snapshot = capture_source_snapshot(file_path, buffer_size=32)
        assert snapshot.size_bytes == len(content)
        assert snapshot.sha256 == expected_hash

    def test_capture_zero_byte_file(self, tmp_path: Path) -> None:
        file_path = tmp_path / "empty.par"
        file_path.write_bytes(b"")

        empty_sha256 = hashlib.sha256(b"").hexdigest().lower()
        snapshot = capture_source_snapshot(file_path)
        assert snapshot.size_bytes == 0
        assert snapshot.sha256 == empty_sha256

    def test_capture_invalid_arguments(self, tmp_path: Path) -> None:
        with pytest.raises(TypeError, match="path must be a Path"):
            capture_source_snapshot("not_a_path")  # type: ignore[arg-type]

        valid_file = tmp_path / "test.par"
        valid_file.write_bytes(b"data")

        with pytest.raises(ValueError, match="buffer_size must be positive"):
            capture_source_snapshot(valid_file, buffer_size=0)

        with pytest.raises(ValueError, match="buffer_size must be positive"):
            capture_source_snapshot(valid_file, buffer_size=-100)

    def test_capture_missing_file_raises_typed_not_found(self, tmp_path: Path) -> None:
        missing = tmp_path / "missing.par"
        with pytest.raises(SourceIntegrityError) as exc_info:
            capture_source_snapshot(missing)
        assert exc_info.value.reason == "not_found"
        assert "does not exist" in exc_info.value.message

    def test_capture_permission_denied_raises_typed_unreadable(self, tmp_path: Path) -> None:
        file_path = tmp_path / "unreadable.par"
        file_path.write_bytes(b"some data")
        with patch.object(Path, "open", side_effect=PermissionError("Permission denied")):
            with pytest.raises(SourceIntegrityError) as exc_info:
                capture_source_snapshot(file_path)
            assert exc_info.value.reason == "unreadable"
            assert "Permission denied" in exc_info.value.message

    def test_capture_directory_raises_typed_non_regular(self, tmp_path: Path) -> None:
        sub_dir = tmp_path / "sub"
        sub_dir.mkdir()
        with pytest.raises(SourceIntegrityError) as exc_info:
            capture_source_snapshot(sub_dir)
        assert exc_info.value.reason == "non_regular"
        assert "not a regular file" in exc_info.value.message

    def test_capture_reparse_point_raises_typed_reparse(self, tmp_path: Path) -> None:
        f = tmp_path / "part.par"
        f.write_bytes(b"content")
        with (
            patch("batch.source_integrity.is_stat_reparse_point", return_value=True),
            pytest.raises(SourceIntegrityError) as exc_info,
        ):
            capture_source_snapshot(f)
        assert exc_info.value.reason == "reparse_point"
        assert "symbolic link or reparse point" in exc_info.value.message

    def test_capture_lstat_permission_error_raises_unreadable_not_reparse_point(self, tmp_path: Path) -> None:
        """Verify that metadata permission errors are truthfully classified as unreadable, not reparse points."""
        file_path = tmp_path / "noperm.par"
        file_path.write_bytes(b"content")
        with patch.object(Path, "lstat", side_effect=PermissionError("Metadata access denied")):
            with pytest.raises(SourceIntegrityError) as exc_info:
                capture_source_snapshot(file_path)
            assert exc_info.value.reason == "unreadable"
            assert "Permission denied reading source file" in exc_info.value.message

    def test_capture_detects_mutation_during_streaming(self, tmp_path: Path) -> None:
        file_path = tmp_path / "mutating.par"
        file_path.write_bytes(b"initial data 1234567890")

        original_lstat = file_path.lstat()
        call_count = 0

        def mutating_lstat() -> os.stat_result:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Call 1: stat_before
                return original_lstat
            # Call 2: stat_after returns modified size and nanosecond timestamp
            return os.stat_result(
                (
                    original_lstat.st_mode,
                    original_lstat.st_ino,
                    original_lstat.st_dev,
                    original_lstat.st_nlink,
                    original_lstat.st_uid,
                    original_lstat.st_gid,
                    original_lstat.st_size + 10,
                    original_lstat.st_atime,
                    original_lstat.st_mtime + 5,
                    original_lstat.st_ctime,
                ),
                {"st_mtime_ns": original_lstat.st_mtime_ns + 5_000_000_000},
            )

        with (
            patch.object(Path, "lstat", side_effect=mutating_lstat),
            pytest.raises(SourceIntegrityError) as exc_info,
        ):
            capture_source_snapshot(file_path, buffer_size=4)
        assert exc_info.value.reason == "unstable"
        assert "modified during snapshot capture" in exc_info.value.message

    def test_capture_detects_reparse_point_transition_during_streaming(self, tmp_path: Path) -> None:
        file_path = tmp_path / "trans.par"
        file_path.write_bytes(b"content data 1234")

        original_lstat = file_path.lstat()
        call_count = 0

        def transitioning_lstat() -> os.stat_result:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return original_lstat
            import stat as stat_mod

            mock_stat = MagicMock()
            mock_stat.st_mode = stat_mod.S_IFREG | 0o644
            mock_stat.st_file_attributes = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT
            mock_stat.st_reparse_tag = 0xA000000C
            return mock_stat

        with (
            patch("sys.platform", "win32"),
            patch.object(Path, "lstat", side_effect=transitioning_lstat),
            pytest.raises(SourceIntegrityError) as exc_info,
        ):
            capture_source_snapshot(file_path, buffer_size=4)
        assert exc_info.value.reason == "reparse_point"
        assert "became a symbolic link or reparse point" in exc_info.value.message


class TestVerifySnapshotEquality:
    """Tests for verify_snapshot_equality."""

    @pytest.fixture
    def base_snapshot(self) -> SourceSnapshot:
        return SourceSnapshot(
            identity=PathIdentity(device=10, inode=20, mode=0o100644),
            size_bytes=512,
            mtime_ns=1700000000000,
            sha256="c" * 64,
        )

    def test_identical_snapshots_equal(self, base_snapshot: SourceSnapshot) -> None:
        identical = SourceSnapshot(
            identity=PathIdentity(device=10, inode=20, mode=0o100644),
            size_bytes=512,
            mtime_ns=1700000000000,
            sha256="c" * 64,
        )
        assert verify_snapshot_equality(base_snapshot, identical) is True

    def test_non_snapshot_argument_returns_false(self, base_snapshot: SourceSnapshot) -> None:
        assert verify_snapshot_equality(base_snapshot, "not_a_snapshot") is False  # type: ignore[arg-type]
        assert verify_snapshot_equality("not_a_snapshot", base_snapshot) is False  # type: ignore[arg-type]

    def test_size_mismatch_returns_false(self, base_snapshot: SourceSnapshot) -> None:
        different = SourceSnapshot(
            identity=base_snapshot.identity,
            size_bytes=base_snapshot.size_bytes + 1,
            mtime_ns=base_snapshot.mtime_ns,
            sha256=base_snapshot.sha256,
        )
        assert verify_snapshot_equality(base_snapshot, different) is False

    def test_mtime_mismatch_returns_false(self, base_snapshot: SourceSnapshot) -> None:
        different = SourceSnapshot(
            identity=base_snapshot.identity,
            size_bytes=base_snapshot.size_bytes,
            mtime_ns=base_snapshot.mtime_ns + 1000,
            sha256=base_snapshot.sha256,
        )
        assert verify_snapshot_equality(base_snapshot, different) is False

    def test_sha256_mismatch_returns_false(self, base_snapshot: SourceSnapshot) -> None:
        different = SourceSnapshot(
            identity=base_snapshot.identity,
            size_bytes=base_snapshot.size_bytes,
            mtime_ns=base_snapshot.mtime_ns,
            sha256="d" * 64,
        )
        assert verify_snapshot_equality(base_snapshot, different) is False

    def test_identity_mismatch_returns_false(self, base_snapshot: SourceSnapshot) -> None:
        different_identity = PathIdentity(device=10, inode=999, mode=0o100644)
        different = SourceSnapshot(
            identity=different_identity,
            size_bytes=base_snapshot.size_bytes,
            mtime_ns=base_snapshot.mtime_ns,
            sha256=base_snapshot.sha256,
        )
        assert verify_snapshot_equality(base_snapshot, different) is False

    def test_restored_timestamp_mutation_detected(self, tmp_path: Path) -> None:
        """Verify that modifying file content while preserving size and restoring mtime fails equality."""
        file_path = tmp_path / "part.par"
        file_path.write_bytes(b"ORIGINAL_BYTES_1234")

        snap1 = capture_source_snapshot(file_path)

        # Mutate content with same length
        st = file_path.stat()
        file_path.write_bytes(b"MUTATED__BYTES_1234")
        # Restore original mtime
        os.utime(file_path, (st.st_atime, st.st_mtime))

        snap2 = capture_source_snapshot(file_path)

        assert snap1.size_bytes == snap2.size_bytes
        assert snap1.sha256 != snap2.sha256
        assert verify_snapshot_equality(snap1, snap2) is False

    def test_hard_links_have_identical_snapshots(self, tmp_path: Path) -> None:
        src = tmp_path / "src.par"
        src.write_bytes(b"IDENTICAL_BYTES_FOR_HARD_LINK")
        dst = tmp_path / "dst.par"
        try:
            os.link(src, dst)
        except OSError, NotImplementedError, AttributeError:
            pytest.skip("Hard links not supported or permitted in this environment")

        snap1 = capture_source_snapshot(src)
        snap2 = capture_source_snapshot(dst)
        assert verify_snapshot_equality(snap1, snap2) is True
