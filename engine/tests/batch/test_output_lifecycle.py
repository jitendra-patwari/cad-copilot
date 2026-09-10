"""Tests for output lifecycle staging primitives: candidate allocation, activation, and cleanup."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from batch.filesystem import PathIdentity, get_path_identity
from batch.output_lifecycle import (
    WORK_DIR_PREFIX,
    activate_private_work_directory,
    allocate_private_work_path,
    cleanup_private_work_directory,
)
from batch.output_publication import BatchWorkspaceError


class TestAllocatePrivateWorkPath:
    """Tests for allocate_private_work_path candidate allocator."""

    def test_allocates_absent_candidate_path(self, tmp_path: Path) -> None:
        target = tmp_path / "model.step"
        work_path = allocate_private_work_path(tmp_path, target)

        assert not work_path.exists()
        assert not work_path.parent.exists()
        assert work_path.parent.name.startswith(WORK_DIR_PREFIX)
        assert work_path.name == "model.step"

    def test_allocation_exhaustion_raises_workspace_error(self, tmp_path: Path) -> None:
        target = tmp_path / "model.step"
        with (
            patch("secrets.token_hex", return_value="collision"),
            pytest.raises(BatchWorkspaceError) as exc_info,
        ):
            (tmp_path / f"{WORK_DIR_PREFIX}collision").mkdir()
            allocate_private_work_path(tmp_path, target, max_candidates=2)

        assert exc_info.value.diagnostic.code == "OUTPUT_ROOT_UNAVAILABLE"


class TestActivatePrivateWorkDirectory:
    """Tests for activate_private_work_directory exclusive activation and proven rollback."""

    def test_activate_creates_empty_directory_and_returns_identity(self, tmp_path: Path) -> None:
        root = tmp_path / "out_root"
        root.mkdir()
        work_dir = root / f"{WORK_DIR_PREFIX}abcdef1234567890"
        work_path = work_dir / "output.step"

        dir_id = activate_private_work_directory(
            work_dir,
            work_path,
            root,
            request_id="req-act-1",
            format_name="step",
        )

        assert isinstance(dir_id, PathIdentity)
        assert work_dir.is_dir()
        assert list(work_dir.iterdir()) == []
        assert not work_path.exists()

    def test_activate_rejects_work_dir_not_direct_child(self, tmp_path: Path) -> None:
        root = tmp_path / "out_root"
        root.mkdir()
        sub = root / "nested"
        sub.mkdir()
        work_dir = sub / f"{WORK_DIR_PREFIX}abcdef1234567890"
        work_path = work_dir / "output.step"

        with pytest.raises(BatchWorkspaceError) as exc_info:
            activate_private_work_directory(
                work_dir,
                work_path,
                root,
                request_id="req-act-2",
                format_name="step",
            )

        assert exc_info.value.diagnostic.code == "INTERNAL_ERROR"
        assert "direct child of the output root" in exc_info.value.diagnostic.message
        assert not work_dir.exists()

    def test_activate_rejects_work_dir_without_prefix(self, tmp_path: Path) -> None:
        root = tmp_path / "out_root"
        root.mkdir()
        work_dir = root / "unprefixed_staging_dir"
        work_path = work_dir / "output.step"

        with pytest.raises(BatchWorkspaceError) as exc_info:
            activate_private_work_directory(
                work_dir,
                work_path,
                root,
                request_id="req-act-3",
                format_name="step",
            )

        assert exc_info.value.diagnostic.code == "INTERNAL_ERROR"
        assert "private prefix" in exc_info.value.diagnostic.message
        assert not work_dir.exists()

    def test_activate_collision_raises_output_root_unavailable(self, tmp_path: Path) -> None:
        root = tmp_path / "out_root"
        root.mkdir()
        work_dir = root / f"{WORK_DIR_PREFIX}abcdef1234567890"
        work_dir.mkdir()
        work_path = work_dir / "output.step"

        with pytest.raises(BatchWorkspaceError) as exc_info:
            activate_private_work_directory(
                work_dir,
                work_path,
                root,
                request_id="req-act-4",
                format_name="step",
            )

        assert exc_info.value.diagnostic.code == "OUTPUT_ROOT_UNAVAILABLE"
        assert "Work directory collision" in exc_info.value.diagnostic.message

    def test_activate_identity_capture_failure_refuses_deletion(self, tmp_path: Path) -> None:
        root = tmp_path / "out_root"
        root.mkdir()
        work_dir = root / f"{WORK_DIR_PREFIX}abcdef1234567890"
        work_path = work_dir / "output.step"

        with (
            patch("batch.output_lifecycle.get_path_identity", side_effect=OSError("Identity error")),
            pytest.raises(BatchWorkspaceError) as exc_info,
        ):
            activate_private_work_directory(
                work_dir,
                work_path,
                root,
                request_id="req-act-id-fail",
                format_name="step",
            )

        assert exc_info.value.diagnostic.code == "ARTIFACT_EXPORT_FAILED"
        assert "refusing unverified rollback" in exc_info.value.diagnostic.message
        assert work_dir.exists()

    def test_activate_reparse_creation_refuses_deletion(self, tmp_path: Path) -> None:
        root = tmp_path / "out_root"
        root.mkdir()
        work_dir = root / f"{WORK_DIR_PREFIX}abcdef1234567890"
        work_path = work_dir / "output.step"

        with (
            patch("batch.output_lifecycle.is_symlink_or_reparse_point", return_value=True),
            pytest.raises(BatchWorkspaceError) as exc_info,
        ):
            activate_private_work_directory(
                work_dir,
                work_path,
                root,
                request_id="req-act-reparse",
                format_name="step",
            )

        assert exc_info.value.diagnostic.code == "ARTIFACT_EXPORT_FAILED"
        assert "refusing unverified rollback" in exc_info.value.diagnostic.message
        assert work_dir.exists()

    def test_activate_identity_mismatch_before_rollback_refuses_deletion(self, tmp_path: Path) -> None:
        root = tmp_path / "out_root"
        root.mkdir()
        work_dir = root / f"{WORK_DIR_PREFIX}abcdef1234567890"
        work_path = work_dir / "output.step"

        real_get_id = get_path_identity
        calls = 0

        def mismatched_id(p: Path) -> PathIdentity:
            nonlocal calls
            calls += 1
            real = real_get_id(p)
            if calls > 1:
                return PathIdentity(device=real.device + 1, inode=real.inode, mode=real.mode)
            return real

        # Intercept mkdir to create a file, prompting rollback
        real_mkdir = os.mkdir

        def fake_mkdir(path: str | os.PathLike[str], mode: int = 0o777, *, dir_fd: int | None = None) -> None:
            real_mkdir(path, mode, dir_fd=dir_fd)
            (work_dir / "rogue.txt").write_bytes(b"DATA")

        with (
            patch("os.mkdir", side_effect=fake_mkdir),
            patch("batch.output_lifecycle.get_path_identity", side_effect=mismatched_id),
            pytest.raises(BatchWorkspaceError) as exc_info,
        ):
            activate_private_work_directory(
                work_dir,
                work_path,
                root,
                request_id="req-act-mismatch",
                format_name="step",
            )

        assert exc_info.value.diagnostic.code == "ARTIFACT_EXPORT_FAILED"
        assert "Activation rollback failed" in exc_info.value.diagnostic.message
        assert work_dir.exists()

    def test_activate_rolls_back_successfully_when_empty_dir_unclean(self, tmp_path: Path) -> None:
        root = tmp_path / "out_root"
        root.mkdir()
        work_dir = root / f"{WORK_DIR_PREFIX}abcdef1234567890"
        work_path = work_dir / "output.step"

        # Simulating work_path exists check failing while work_dir is empty
        with (
            patch.object(Path, "exists", autospec=True) as mock_exists,
            pytest.raises(BatchWorkspaceError) as exc_info,
        ):

            def fake_exists(p: Path) -> bool:
                if p == work_path:
                    return True
                return p.exists()

            mock_exists.side_effect = fake_exists
            activate_private_work_directory(
                work_dir,
                work_path,
                root,
                request_id="req-act-empty-rb",
                format_name="step",
            )

        assert exc_info.value.diagnostic.code == "OUTPUT_ROOT_UNAVAILABLE"
        assert "Work file unexpectedly exists" in exc_info.value.diagnostic.message
        assert not work_dir.exists()


class TestCleanupPrivateWorkDirectory:
    """Tests for cleanup_private_work_directory guarded teardown."""

    def test_cleanup_unactivated_absent_directory_is_noop(self, tmp_path: Path) -> None:
        root = tmp_path / "out_root"
        root.mkdir()
        work_dir = root / f"{WORK_DIR_PREFIX}abcdef1234567890"
        work_path = work_dir / "output.step"

        cleanup_private_work_directory(
            work_dir,
            work_path,
            root,
            expected_dir_id=None,
            request_id="req-clean-1",
            format_name="step",
        )
        assert not work_dir.exists()

    def test_cleanup_unactivated_existing_directory_refuses_deletion(self, tmp_path: Path) -> None:
        """Unactivated record with an existing directory has no proven ownership and refuses deletion."""
        root = tmp_path / "out_root"
        root.mkdir()
        work_dir = root / f"{WORK_DIR_PREFIX}abcdef1234567890"
        work_dir.mkdir()
        work_path = work_dir / "output.step"

        with pytest.raises(BatchWorkspaceError) as exc_info:
            cleanup_private_work_directory(
                work_dir,
                work_path,
                root,
                expected_dir_id=None,
                request_id="req-clean-unactivated-existing",
                format_name="step",
            )

        assert exc_info.value.diagnostic.code == "ARTIFACT_EXPORT_FAILED"
        assert "Cannot clean up unowned directory" in exc_info.value.diagnostic.message
        assert work_dir.exists()

    def test_cleanup_active_missing_directory_raises_artifact_export_failed(self, tmp_path: Path) -> None:
        """Missing active work directory during cleanup raises ARTIFACT_EXPORT_FAILED."""
        root = tmp_path / "out_root"
        root.mkdir()
        work_dir = root / f"{WORK_DIR_PREFIX}abcdef1234567890"
        work_path = work_dir / "output.step"
        expected_id = PathIdentity(device=1, inode=2, mode=0o040755)

        with pytest.raises(BatchWorkspaceError) as exc_info:
            cleanup_private_work_directory(
                work_dir,
                work_path,
                root,
                expected_dir_id=expected_id,
                request_id="req-clean-active-missing",
                format_name="step",
            )

        assert exc_info.value.diagnostic.code == "ARTIFACT_EXPORT_FAILED"
        assert "Active work directory unexpectedly missing" in exc_info.value.diagnostic.message

    def test_cleanup_active_dangling_reparse_point_raises_artifact_export_failed(self, tmp_path: Path) -> None:
        """Dangling reparse point is not treated as clean absence and raises ARTIFACT_EXPORT_FAILED."""
        root = tmp_path / "out_root"
        root.mkdir()
        work_dir = root / f"{WORK_DIR_PREFIX}abcdef1234567890"
        work_path = work_dir / "output.step"
        expected_id = PathIdentity(device=1, inode=2, mode=0o040755)

        with (
            patch("batch.output_lifecycle.is_symlink_or_reparse_point", return_value=True),
            pytest.raises(BatchWorkspaceError) as exc_info,
        ):
            cleanup_private_work_directory(
                work_dir,
                work_path,
                root,
                expected_dir_id=expected_id,
                request_id="req-clean-dangling-reparse",
                format_name="step",
            )

        assert exc_info.value.diagnostic.code == "ARTIFACT_EXPORT_FAILED"

    def test_cleanup_rejects_work_dir_not_direct_child(self, tmp_path: Path) -> None:
        root = tmp_path / "out_root"
        root.mkdir()
        sub = root / "nested"
        sub.mkdir()
        work_dir = sub / f"{WORK_DIR_PREFIX}abcdef1234567890"
        work_path = work_dir / "output.step"

        with pytest.raises(BatchWorkspaceError) as exc_info:
            cleanup_private_work_directory(
                work_dir,
                work_path,
                root,
                request_id="req-clean-2",
                format_name="step",
            )

        assert exc_info.value.diagnostic.code == "INTERNAL_ERROR"
        assert "direct child of the output root" in exc_info.value.diagnostic.message

    def test_cleanup_rejects_work_dir_without_prefix(self, tmp_path: Path) -> None:
        root = tmp_path / "out_root"
        root.mkdir()
        work_dir = root / "unprefixed_dir"
        work_path = work_dir / "output.step"

        with pytest.raises(BatchWorkspaceError) as exc_info:
            cleanup_private_work_directory(
                work_dir,
                work_path,
                root,
                request_id="req-clean-3",
                format_name="step",
            )

        assert exc_info.value.diagnostic.code == "INTERNAL_ERROR"
        assert "private prefix" in exc_info.value.diagnostic.message

    def test_cleanup_identity_mismatch_raises_artifact_export_failed(self, tmp_path: Path) -> None:
        root = tmp_path / "out_root"
        root.mkdir()
        work_dir = root / f"{WORK_DIR_PREFIX}abcdef1234567890"
        work_dir.mkdir()
        work_path = work_dir / "output.step"

        expected_id = PathIdentity(device=999, inode=888, mode=0o040755)

        with pytest.raises(BatchWorkspaceError) as exc_info:
            cleanup_private_work_directory(
                work_dir,
                work_path,
                root,
                expected_dir_id=expected_id,
                request_id="req-clean-4",
                format_name="step",
            )

        assert exc_info.value.diagnostic.code == "ARTIFACT_EXPORT_FAILED"
        assert "Work directory identity changed before cleanup" in exc_info.value.diagnostic.message
        assert work_dir.exists()

    def test_cleanup_refuses_deletion_if_inventory_has_unexpected_file(self, tmp_path: Path) -> None:
        root = tmp_path / "out_root"
        root.mkdir()
        work_dir = root / f"{WORK_DIR_PREFIX}abcdef1234567890"
        work_dir.mkdir()
        dir_id = get_path_identity(work_dir)
        work_path = work_dir / "output.step"
        work_path.write_bytes(b"VALID")
        rogue_path = work_dir / "extra.tmp"
        rogue_path.write_bytes(b"ROGUE")

        with pytest.raises(BatchWorkspaceError) as exc_info:
            cleanup_private_work_directory(
                work_dir,
                work_path,
                root,
                expected_dir_id=dir_id,
                request_id="req-clean-5",
                format_name="step",
            )

        assert exc_info.value.diagnostic.code == "ARTIFACT_EXPORT_FAILED"
        assert "Unexpected files found in work directory during cleanup" in exc_info.value.diagnostic.message
        assert work_path.exists()
        assert rogue_path.exists()
        assert work_dir.exists()

    def test_cleanup_refuses_deletion_if_work_file_is_directory(self, tmp_path: Path) -> None:
        root = tmp_path / "out_root"
        root.mkdir()
        work_dir = root / f"{WORK_DIR_PREFIX}abcdef1234567890"
        work_dir.mkdir()
        dir_id = get_path_identity(work_dir)
        work_path = work_dir / "output.step"
        work_path.mkdir()

        with pytest.raises(BatchWorkspaceError) as exc_info:
            cleanup_private_work_directory(
                work_dir,
                work_path,
                root,
                expected_dir_id=dir_id,
                request_id="req-clean-6",
                format_name="step",
            )

        assert exc_info.value.diagnostic.code == "ARTIFACT_EXPORT_FAILED"
        assert "Work file is not an ordinary regular file" in exc_info.value.diagnostic.message
        assert work_path.exists()
        assert work_dir.exists()

    def test_cleanup_unlink_failure_raises_artifact_export_failed(self, tmp_path: Path) -> None:
        root = tmp_path / "out_root"
        root.mkdir()
        work_dir = root / f"{WORK_DIR_PREFIX}abcdef1234567890"
        work_dir.mkdir()
        dir_id = get_path_identity(work_dir)
        work_path = work_dir / "output.step"
        work_path.write_bytes(b"DATA")

        with (
            patch("pathlib.Path.unlink", side_effect=PermissionError("File locked")),
            pytest.raises(BatchWorkspaceError) as exc_info,
        ):
            cleanup_private_work_directory(
                work_dir,
                work_path,
                root,
                expected_dir_id=dir_id,
                request_id="req-clean-7",
                format_name="step",
            )

        assert exc_info.value.diagnostic.code == "ARTIFACT_EXPORT_FAILED"
        assert "Failed to unlink work file during cleanup" in exc_info.value.diagnostic.message
        assert work_path.exists()
        assert work_dir.exists()

    def test_cleanup_rmdir_failure_raises_artifact_export_failed(self, tmp_path: Path) -> None:
        root = tmp_path / "out_root"
        root.mkdir()
        work_dir = root / f"{WORK_DIR_PREFIX}abcdef1234567890"
        work_dir.mkdir()
        dir_id = get_path_identity(work_dir)
        work_path = work_dir / "output.step"
        work_path.write_bytes(b"DATA")

        with (
            patch("pathlib.Path.rmdir", side_effect=PermissionError("Dir locked")),
            pytest.raises(BatchWorkspaceError) as exc_info,
        ):
            cleanup_private_work_directory(
                work_dir,
                work_path,
                root,
                expected_dir_id=dir_id,
                request_id="req-clean-8",
                format_name="step",
            )

        assert exc_info.value.diagnostic.code == "ARTIFACT_EXPORT_FAILED"
        assert "Failed to remove empty work directory during cleanup" in exc_info.value.diagnostic.message
        assert not work_path.exists()
        assert work_dir.exists()

    def test_cleanup_success_unlinks_work_file_and_removes_directory(self, tmp_path: Path) -> None:
        root = tmp_path / "out_root"
        root.mkdir()
        work_dir = root / f"{WORK_DIR_PREFIX}abcdef1234567890"
        work_dir.mkdir()
        dir_id = get_path_identity(work_dir)
        work_path = work_dir / "output.step"
        work_path.write_bytes(b"DATA")

        cleanup_private_work_directory(
            work_dir,
            work_path,
            root,
            expected_dir_id=dir_id,
            request_id="req-clean-9",
            format_name="step",
        )

        assert not work_path.exists()
        assert not work_dir.exists()
