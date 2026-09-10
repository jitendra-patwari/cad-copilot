"""Tests for output snapshot data models, streaming capture, and equality verification."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

import pytest

from batch.filesystem import PathIdentity, get_path_identity
from batch.output_snapshot import (
    OutputSnapshot,
    capture_output_snapshot,
    verify_output_snapshot_equality,
)


class TestOutputSnapshot:
    """Tests for OutputSnapshot data class invariants and validation."""

    def test_output_snapshot_is_frozen(self) -> None:
        ident = PathIdentity(device=1, inode=2, mode=0o100644)
        snap = OutputSnapshot(
            identity=ident,
            size_bytes=100,
            mtime_ns=1_000_000,
            sha256="a" * 64,
        )
        with pytest.raises(FrozenInstanceError):
            snap.size_bytes = 200  # type: ignore[misc]

    def test_output_snapshot_rejects_non_positive_size(self) -> None:
        ident = PathIdentity(device=1, inode=2, mode=0o100644)
        with pytest.raises(ValueError, match="positive int"):
            OutputSnapshot(identity=ident, size_bytes=0, mtime_ns=1000, sha256="a" * 64)
        with pytest.raises(ValueError, match="positive int"):
            OutputSnapshot(identity=ident, size_bytes=-10, mtime_ns=1000, sha256="a" * 64)

    def test_output_snapshot_rejects_invalid_sha256(self) -> None:
        ident = PathIdentity(device=1, inode=2, mode=0o100644)
        # Uppercase hex rejected
        with pytest.raises(ValueError, match="64-character lowercase hex"):
            OutputSnapshot(identity=ident, size_bytes=100, mtime_ns=1000, sha256="A" * 64)
        # Short length rejected
        with pytest.raises(ValueError, match="64-character lowercase hex"):
            OutputSnapshot(identity=ident, size_bytes=100, mtime_ns=1000, sha256="a" * 63)
        # Non-hex characters rejected
        with pytest.raises(ValueError, match="64-character lowercase hex"):
            OutputSnapshot(identity=ident, size_bytes=100, mtime_ns=1000, sha256="z" * 64)

    def test_output_snapshot_rejects_invalid_identity(self) -> None:
        with pytest.raises(TypeError, match="identity must be PathIdentity"):
            OutputSnapshot(
                identity="not_an_identity",  # type: ignore[arg-type]
                size_bytes=100,
                mtime_ns=1000,
                sha256="a" * 64,
            )

    def test_output_snapshot_rejects_zero_inode(self) -> None:
        ident = PathIdentity(device=1, inode=0, mode=0o100644)
        with pytest.raises(ValueError, match="non-zero"):
            OutputSnapshot(identity=ident, size_bytes=100, mtime_ns=1000, sha256="a" * 64)


class TestCaptureOutputSnapshot:
    """Tests for capture_output_snapshot capture helper."""

    def test_capture_output_snapshot_success(self, tmp_path: Path) -> None:
        p = tmp_path / "out.step"
        p.write_bytes(b"SOLIDWORKS OR SOLID EDGE EXPORT BYTES")
        snap = capture_output_snapshot(p)

        assert snap.size_bytes == len(b"SOLIDWORKS OR SOLID EDGE EXPORT BYTES")
        assert snap.identity == get_path_identity(p)
        assert len(snap.sha256) == 64

    def test_capture_output_snapshot_rejects_zero_byte_file(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.step"
        p.write_bytes(b"")
        with pytest.raises(ValueError, match="cannot be zero bytes"):
            capture_output_snapshot(p)

    def test_capture_output_snapshot_rejects_zero_inode(self, tmp_path: Path) -> None:
        p = tmp_path / "zero_inode.step"
        p.write_bytes(b"content bytes")
        real_snap = capture_output_snapshot(p)
        zero_id = PathIdentity(device=real_snap.identity.device, inode=0, mode=real_snap.identity.mode)
        from batch.source_integrity import SourceSnapshot

        fake_snap = SourceSnapshot(
            identity=zero_id,
            size_bytes=real_snap.size_bytes,
            mtime_ns=real_snap.mtime_ns,
            sha256=real_snap.sha256,
        )
        with (
            patch("batch.output_snapshot.capture_source_snapshot", return_value=fake_snap),
            pytest.raises(ValueError, match="zero inode"),
        ):
            capture_output_snapshot(p)


class TestVerifyOutputSnapshotEquality:
    """Tests for verify_output_snapshot_equality comparison helper."""

    def test_equality_identical_snapshots(self) -> None:
        ident = PathIdentity(device=1, inode=2, mode=0o100644)
        s1 = OutputSnapshot(identity=ident, size_bytes=100, mtime_ns=1000, sha256="a" * 64)
        s2 = OutputSnapshot(identity=ident, size_bytes=100, mtime_ns=1000, sha256="a" * 64)
        assert verify_output_snapshot_equality(s1, s2) is True

    def test_equality_different_fields_return_false(self) -> None:
        ident1 = PathIdentity(device=1, inode=2, mode=0o100644)
        ident2 = PathIdentity(device=1, inode=3, mode=0o100644)
        base = OutputSnapshot(identity=ident1, size_bytes=100, mtime_ns=1000, sha256="a" * 64)

        diff_id = OutputSnapshot(identity=ident2, size_bytes=100, mtime_ns=1000, sha256="a" * 64)
        diff_size = OutputSnapshot(identity=ident1, size_bytes=200, mtime_ns=1000, sha256="a" * 64)
        diff_mtime = OutputSnapshot(identity=ident1, size_bytes=100, mtime_ns=2000, sha256="a" * 64)
        diff_hash = OutputSnapshot(identity=ident1, size_bytes=100, mtime_ns=1000, sha256="b" * 64)

        assert verify_output_snapshot_equality(base, diff_id) is False
        assert verify_output_snapshot_equality(base, diff_size) is False
        assert verify_output_snapshot_equality(base, diff_mtime) is False
        assert verify_output_snapshot_equality(base, diff_hash) is False
        assert verify_output_snapshot_equality(base, "not_a_snapshot") is False  # type: ignore[arg-type]
