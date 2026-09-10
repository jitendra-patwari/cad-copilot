"""Tests for Windows atomic publication, parent safety, error mapping, and race handling."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from batch.execution import BatchItemContext
from batch.output_publication import (
    BatchWorkspaceError,
    ensure_safe_parent_directory,
    publish_output_file,
)
from batch.output_snapshot import OutputSnapshot, capture_output_snapshot


def create_sample_context(
    output_root: Path,
    *,
    fmt: str = "step",
    relative_input: str = "part1.par",
    relative_target: str = "part1.step",
) -> BatchItemContext:
    work_dir = output_root / ".cad-copilot-work-0123456789abcdef"
    work_path = work_dir / Path(relative_target).name
    target_path = output_root / relative_target
    return BatchItemContext(
        request_id="req-test-pub",
        operation_id="op-test",
        input=relative_input,
        source_path=output_root / relative_input,
        format=fmt,
        target_relative_path=relative_target,
        work_path=work_path,
        target_path=target_path,
    )


class TestEnsureSafeParentDirectory:
    """Tests for ensure_safe_parent_directory parent directory validation."""

    def test_creates_clean_parent_hierarchy(self, tmp_path: Path) -> None:
        root = tmp_path / "output_root"
        root.mkdir()
        target_parent = root / "models" / "sub" / "v1"

        ensure_safe_parent_directory(target_parent, root)
        assert target_parent.is_dir()

    def test_rejects_invalid_characters_in_segment(self, tmp_path: Path) -> None:
        root = tmp_path / "output_root"
        root.mkdir()
        with pytest.raises(BatchWorkspaceError) as exc_info:
            ensure_safe_parent_directory(root / "bad:dir" / "child", root)
        assert exc_info.value.diagnostic.code == "OUTPUT_ROOT_UNAVAILABLE"
        assert "invalid characters" in exc_info.value.diagnostic.message

    def test_rejects_reparse_point_in_parent_hierarchy(self, tmp_path: Path) -> None:
        root = tmp_path / "output_root"
        root.mkdir()
        models_dir = root / "models"
        models_dir.mkdir()

        with (
            patch("batch.output_publication.is_symlink_or_reparse_point", return_value=True),
            pytest.raises(BatchWorkspaceError) as exc_info,
        ):
            ensure_safe_parent_directory(models_dir / "child", root)
        assert exc_info.value.diagnostic.code == "OUTPUT_ROOT_UNAVAILABLE"
        assert "Parent component 'models' is not a safe directory" in exc_info.value.diagnostic.message

    def test_rejects_parent_component_that_is_a_file(self, tmp_path: Path) -> None:
        root = tmp_path / "output_root"
        root.mkdir()
        file_component = root / "models"
        file_component.write_text("not a dir")

        with pytest.raises(BatchWorkspaceError) as exc_info:
            ensure_safe_parent_directory(file_component / "child", root)
        assert exc_info.value.diagnostic.code == "OUTPUT_ROOT_UNAVAILABLE"
        assert "Parent component 'models' is not a safe directory" in exc_info.value.diagnostic.message


class TestPublishOutputFile:
    """Tests for publish_output_file Windows atomic publication helper."""

    def test_atomic_rename_success_on_windows(self, tmp_path: Path) -> None:
        if sys.platform != "win32":
            pytest.skip("Production atomic rename requires Windows filesystem")

        root = tmp_path / "out"
        root.mkdir()
        ctx = create_sample_context(root)
        ctx.work_path.parent.mkdir()
        ctx.work_path.write_bytes(b"STEP_DATA_CONTENT")
        snap = capture_output_snapshot(ctx.work_path)

        published = publish_output_file(ctx, snap, root)
        assert published == ctx.target_path
        assert ctx.target_path.exists()
        assert ctx.target_path.read_bytes() == b"STEP_DATA_CONTENT"
        assert not ctx.work_path.exists()
        # Work directory was cleaned up after successful publication
        assert not ctx.work_path.parent.exists()

    def test_atomic_rename_race_winner_preserved_at_rename_boundary(self, tmp_path: Path) -> None:
        """Verify that a race winner created immediately at the rename boundary is preserved."""
        if sys.platform != "win32":
            pytest.skip("Production atomic rename race test requires Windows filesystem")

        root = tmp_path / "out"
        root.mkdir()
        ctx = create_sample_context(root)
        ctx.work_path.parent.mkdir()
        ctx.work_path.write_bytes(b"WORK_FILE_CONTENT")
        snap = capture_output_snapshot(ctx.work_path)

        # Inject competing target right at the rename boundary
        real_rename = os.rename

        def race_rename(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
            Path(dst).parent.mkdir(parents=True, exist_ok=True)
            Path(dst).write_bytes(b"PRE_EXISTING_WINNER_BYTES")
            real_rename(src, dst)

        with (
            patch("os.rename", side_effect=race_rename),
            pytest.raises(BatchWorkspaceError) as exc_info,
        ):
            publish_output_file(ctx, snap, root)

        assert exc_info.value.diagnostic.code == "TARGET_ALREADY_EXISTS"
        # Winner bytes are preserved
        assert ctx.target_path.read_bytes() == b"PRE_EXISTING_WINNER_BYTES"
        # Work file remains intact
        assert ctx.work_path.read_bytes() == b"WORK_FILE_CONTENT"

    def test_publication_on_non_windows_fails_closed(self, tmp_path: Path) -> None:
        root = tmp_path / "out"
        root.mkdir()
        ctx = create_sample_context(root)
        ctx.work_path.parent.mkdir()
        ctx.work_path.write_bytes(b"DATA")
        snap = capture_output_snapshot(ctx.work_path)

        with (
            patch("sys.platform", "linux"),
            pytest.raises(BatchWorkspaceError) as exc_info,
        ):
            publish_output_file(ctx, snap, root)

        assert exc_info.value.diagnostic.code == "OUTPUT_ROOT_UNAVAILABLE"
        assert "Atomic publication requires Windows filesystem semantics" in exc_info.value.diagnostic.message
        # Work file remains untouched
        assert ctx.work_path.exists()
        assert not ctx.target_path.exists()

    @pytest.mark.parametrize("winerror", [80, 183])
    def test_windows_error_codes_normalize_to_target_already_exists(self, tmp_path: Path, winerror: int) -> None:
        root = tmp_path / "out"
        root.mkdir()
        ctx = create_sample_context(root)
        ctx.work_path.parent.mkdir()
        ctx.work_path.write_bytes(b"DATA")
        snap = capture_output_snapshot(ctx.work_path)

        err = OSError("Destination exists")
        err.winerror = winerror
        with (
            patch("sys.platform", "win32"),
            patch("os.rename", side_effect=err),
            pytest.raises(BatchWorkspaceError) as exc_info,
        ):
            publish_output_file(ctx, snap, root)
        assert exc_info.value.diagnostic.code == "TARGET_ALREADY_EXISTS"

    def test_generic_rename_error_maps_to_artifact_export_failed(self, tmp_path: Path) -> None:
        root = tmp_path / "out"
        root.mkdir()
        ctx = create_sample_context(root)
        ctx.work_path.parent.mkdir()
        ctx.work_path.write_bytes(b"DATA")
        snap = capture_output_snapshot(ctx.work_path)

        with (
            patch("sys.platform", "win32"),
            patch("os.rename", side_effect=PermissionError("Locked")),
            pytest.raises(BatchWorkspaceError) as exc_info,
        ):
            publish_output_file(ctx, snap, root)
        assert exc_info.value.diagnostic.code == "ARTIFACT_EXPORT_FAILED"
        assert "Failed to publish artifact" in exc_info.value.diagnostic.message

    def test_post_publication_snapshot_mismatch_raises_artifact_export_failed(self, tmp_path: Path) -> None:
        """Target content or metadata changed between rename and post-check."""
        root = tmp_path / "out"
        root.mkdir()
        ctx = create_sample_context(root)
        ctx.work_path.parent.mkdir()
        ctx.work_path.write_bytes(b"DATA")
        snap = capture_output_snapshot(ctx.work_path)

        # Mismatched expected snapshot
        mismatched_snap = OutputSnapshot(
            identity=snap.identity,
            size_bytes=snap.size_bytes + 1,
            mtime_ns=snap.mtime_ns,
            sha256="b" * 64,
        )

        with (
            patch("sys.platform", "win32"),
            pytest.raises(BatchWorkspaceError) as exc_info,
        ):
            publish_output_file(ctx, mismatched_snap, root)
        assert exc_info.value.diagnostic.code == "ARTIFACT_EXPORT_FAILED"
        assert "mismatch" in exc_info.value.diagnostic.message

    def test_rmdir_failure_does_not_suppress_error(self, tmp_path: Path) -> None:
        """Failure to remove the empty work directory must raise ARTIFACT_EXPORT_FAILED."""
        if sys.platform != "win32":
            pytest.skip("Windows atomic publication test")

        root = tmp_path / "out"
        root.mkdir()
        ctx = create_sample_context(root)
        ctx.work_path.parent.mkdir()
        ctx.work_path.write_bytes(b"DATA")
        snap = capture_output_snapshot(ctx.work_path)

        with (
            patch("pathlib.Path.rmdir", side_effect=PermissionError("Directory locked")),
            pytest.raises(BatchWorkspaceError) as exc_info,
        ):
            publish_output_file(ctx, snap, root)

        assert exc_info.value.diagnostic.code == "ARTIFACT_EXPORT_FAILED"
        assert "Failed to remove private work directory" in exc_info.value.diagnostic.message
