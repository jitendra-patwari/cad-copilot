"""Tests for output workspace coordination: registration, state transitions, and integration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from batch.allocation import BatchSafetyRejectionError
from batch.execution import BatchItemContext
from batch.filesystem import get_path_identity
from batch.models import BatchDiagnostic
from batch.output_publication import BatchWorkspaceError
from batch.output_snapshot import OutputSnapshot, capture_output_snapshot
from batch.output_workspace import (
    WORK_DIR_PREFIX,
    FilesystemOutputWorkspace,
    allocate_private_work_path,
)


@pytest.fixture
def workspace_setup(tmp_path: Path) -> tuple[Path, FilesystemOutputWorkspace]:
    output_root = tmp_path / "batch_out"
    output_root.mkdir()
    root_id = get_path_identity(output_root)
    ws = FilesystemOutputWorkspace(output_root, root_id)
    return output_root, ws


def create_sample_context(
    output_root: Path,
    *,
    fmt: str = "step",
    relative_input: str = "part1.par",
    relative_target: str = "part1.step",
) -> BatchItemContext:
    target_path = output_root / relative_target
    work_path = allocate_private_work_path(output_root, target_path)
    return BatchItemContext(
        request_id="req-test-123",
        operation_id="op-convert",
        input=relative_input,
        source_path=output_root / relative_input,
        format=fmt,
        target_relative_path=relative_target,
        work_path=work_path,
        target_path=target_path,
    )


class TestRegisterAndContextValidation:
    """Tests for context registration and forgery detection."""

    def test_register_context_duplicate_rejected(self, workspace_setup: tuple[Path, FilesystemOutputWorkspace]) -> None:
        output_root, ws = workspace_setup
        ctx = create_sample_context(output_root)
        ws.register_context(ctx)

        with pytest.raises(BatchWorkspaceError) as exc_info:
            ws.register_context(ctx)
        assert exc_info.value.diagnostic.code == "INTERNAL_ERROR"
        assert "already registered" in exc_info.value.diagnostic.message

    @pytest.mark.parametrize(
        "field,forged_value",
        [
            ("request_id", "forged-req"),
            ("operation_id", "forged-op"),
            ("input", "forged-input.par"),
            ("source_path", "forged-source.par"),
            ("format", "stl"),
            ("target_relative_path", "forged.step"),
            ("work_path", "forged-work.step"),
            ("target_path", "forged-target.step"),
        ],
    )
    def test_context_field_mismatch_rejected(
        self,
        workspace_setup: tuple[Path, FilesystemOutputWorkspace],
        field: str,
        forged_value: str,
    ) -> None:
        output_root, ws = workspace_setup
        ctx = create_sample_context(output_root)
        ws.register_context(ctx)

        forged_ctx: BatchItemContext
        if field == "request_id":
            forged_ctx = BatchItemContext(
                request_id=forged_value,
                operation_id=ctx.operation_id,
                input=ctx.input,
                source_path=ctx.source_path,
                format=ctx.format,
                target_relative_path=ctx.target_relative_path,
                work_path=ctx.work_path,
                target_path=ctx.target_path,
            )
        elif field == "operation_id":
            forged_ctx = BatchItemContext(
                request_id=ctx.request_id,
                operation_id=forged_value,
                input=ctx.input,
                source_path=ctx.source_path,
                format=ctx.format,
                target_relative_path=ctx.target_relative_path,
                work_path=ctx.work_path,
                target_path=ctx.target_path,
            )
        elif field == "input":
            forged_ctx = BatchItemContext(
                request_id=ctx.request_id,
                operation_id=ctx.operation_id,
                input=forged_value,
                source_path=ctx.source_path,
                format=ctx.format,
                target_relative_path=ctx.target_relative_path,
                work_path=ctx.work_path,
                target_path=ctx.target_path,
            )
        elif field == "source_path":
            forged_ctx = BatchItemContext(
                request_id=ctx.request_id,
                operation_id=ctx.operation_id,
                input=ctx.input,
                source_path=output_root / forged_value,
                format=ctx.format,
                target_relative_path=ctx.target_relative_path,
                work_path=ctx.work_path,
                target_path=ctx.target_path,
            )
        elif field == "format":
            forged_ctx = BatchItemContext(
                request_id=ctx.request_id,
                operation_id=ctx.operation_id,
                input=ctx.input,
                source_path=ctx.source_path,
                format=forged_value,
                target_relative_path=ctx.target_relative_path,
                work_path=ctx.work_path,
                target_path=ctx.target_path,
            )
        elif field == "target_relative_path":
            forged_ctx = BatchItemContext(
                request_id=ctx.request_id,
                operation_id=ctx.operation_id,
                input=ctx.input,
                source_path=ctx.source_path,
                format=ctx.format,
                target_relative_path=forged_value,
                work_path=ctx.work_path,
                target_path=ctx.target_path,
            )
        elif field == "work_path":
            forged_ctx = BatchItemContext(
                request_id=ctx.request_id,
                operation_id=ctx.operation_id,
                input=ctx.input,
                source_path=ctx.source_path,
                format=ctx.format,
                target_relative_path=ctx.target_relative_path,
                work_path=ctx.work_path.parent / forged_value,
                target_path=ctx.target_path,
            )
        else:
            forged_ctx = BatchItemContext(
                request_id=ctx.request_id,
                operation_id=ctx.operation_id,
                input=ctx.input,
                source_path=ctx.source_path,
                format=ctx.format,
                target_relative_path=ctx.target_relative_path,
                work_path=ctx.work_path,
                target_path=output_root / forged_value,
            )

        with pytest.raises(BatchWorkspaceError) as exc_info:
            ws.begin_format(forged_ctx)
        assert exc_info.value.diagnostic.code == "INTERNAL_ERROR"


class TestBeginFormat:
    """Tests for begin_format coordinator validation and activation transition."""

    def test_begin_format_transitions_to_active(self, workspace_setup: tuple[Path, FilesystemOutputWorkspace]) -> None:
        output_root, ws = workspace_setup
        ctx = create_sample_context(output_root)
        ws.register_context(ctx)

        ws.begin_format(ctx)
        assert ctx.work_path.parent.is_dir()
        assert not ctx.work_path.exists()

    def test_begin_format_validates_format_before_creating_directory(
        self, workspace_setup: tuple[Path, FilesystemOutputWorkspace]
    ) -> None:
        output_root, ws = workspace_setup
        target_path = output_root / "part.xyz"
        work_dir = output_root / f"{WORK_DIR_PREFIX}fake123"
        work_path = work_dir / "part.xyz"
        ctx = BatchItemContext(
            request_id="req-1",
            operation_id="op-1",
            input="part.par",
            source_path=output_root / "part.par",
            format="unsupported_format",
            target_relative_path="part.xyz",
            work_path=work_path,
            target_path=target_path,
        )
        ws.register_context(ctx)

        with pytest.raises(BatchWorkspaceError) as exc_info:
            ws.begin_format(ctx)

        assert exc_info.value.diagnostic.code == "INTERNAL_ERROR"
        assert "Unsupported format" in exc_info.value.diagnostic.message
        assert not work_dir.exists()

    def test_begin_format_validates_suffix_before_creating_directory(
        self, workspace_setup: tuple[Path, FilesystemOutputWorkspace]
    ) -> None:
        output_root, ws = workspace_setup
        target_path = output_root / "part.stl"
        work_dir = output_root / f"{WORK_DIR_PREFIX}fake456"
        work_path = work_dir / "part.wrong"
        ctx = BatchItemContext(
            request_id="req-1",
            operation_id="op-1",
            input="part.par",
            source_path=output_root / "part.par",
            format="stl",
            target_relative_path="part.stl",
            work_path=work_path,
            target_path=target_path,
        )
        ws.register_context(ctx)

        with pytest.raises(BatchWorkspaceError) as exc_info:
            ws.begin_format(ctx)

        assert exc_info.value.diagnostic.code == "INTERNAL_ERROR"
        assert "Work path suffix" in exc_info.value.diagnostic.message
        assert not work_dir.exists()

    def test_begin_format_rejects_double_activation(
        self, workspace_setup: tuple[Path, FilesystemOutputWorkspace]
    ) -> None:
        output_root, ws = workspace_setup
        ctx = create_sample_context(output_root)
        ws.register_context(ctx)

        ws.begin_format(ctx)
        with pytest.raises(BatchWorkspaceError) as exc_info:
            ws.begin_format(ctx)
        assert exc_info.value.diagnostic.code == "INTERNAL_ERROR"
        assert "already active" in exc_info.value.diagnostic.message

    def test_begin_format_rejects_target_already_exists(
        self, workspace_setup: tuple[Path, FilesystemOutputWorkspace]
    ) -> None:
        output_root, ws = workspace_setup
        ctx = create_sample_context(output_root)
        ctx.target_path.write_bytes(b"EXISTING")
        ws.register_context(ctx)

        with pytest.raises(BatchWorkspaceError) as exc_info:
            ws.begin_format(ctx)
        assert exc_info.value.diagnostic.code == "TARGET_ALREADY_EXISTS"
        assert not ctx.work_path.parent.exists()

    def test_begin_format_normalizes_containment_failure(
        self, workspace_setup: tuple[Path, FilesystemOutputWorkspace]
    ) -> None:
        output_root, ws = workspace_setup
        ctx = create_sample_context(output_root)
        ws.register_context(ctx)

        with (
            patch(
                "batch.output_workspace.assert_strictly_contained",
                side_effect=BatchSafetyRejectionError(
                    BatchDiagnostic(code="OUTPUT_ROOT_UNAVAILABLE", message="Target path escapes root")
                ),
            ),
            pytest.raises(BatchWorkspaceError) as exc_info,
        ):
            ws.begin_format(ctx)

        assert exc_info.value.diagnostic.code == "OUTPUT_ROOT_UNAVAILABLE"
        assert exc_info.value.diagnostic.message == "Target path escapes root"
        assert not ctx.work_path.parent.exists()


class TestFinalizeFormat:
    """Tests for finalize_format coordinator state checks and publication delegation."""

    def test_finalize_format_not_active_rejected(self, workspace_setup: tuple[Path, FilesystemOutputWorkspace]) -> None:
        output_root, ws = workspace_setup
        ctx = create_sample_context(output_root)
        ws.register_context(ctx)
        snap = OutputSnapshot(
            identity=get_path_identity(output_root),
            size_bytes=10,
            mtime_ns=1000,
            sha256="a" * 64,
        )

        with pytest.raises(BatchWorkspaceError) as exc_info:
            ws.finalize_format(ctx, snap)
        assert exc_info.value.diagnostic.code == "INTERNAL_ERROR"
        assert "Cannot finalize format in state 'allocated'" in exc_info.value.diagnostic.message

    def test_finalize_format_unexpected_inventory_rejected(
        self, workspace_setup: tuple[Path, FilesystemOutputWorkspace]
    ) -> None:
        output_root, ws = workspace_setup
        ctx = create_sample_context(output_root)
        ws.register_context(ctx)
        ws.begin_format(ctx)

        ctx.work_path.write_bytes(b"DATA")
        snap = capture_output_snapshot(ctx.work_path)

        (ctx.work_path.parent / "rogue.tmp").write_bytes(b"ROGUE")

        with pytest.raises(BatchWorkspaceError) as exc_info:
            ws.finalize_format(ctx, snap)
        assert exc_info.value.diagnostic.code == "OUTPUT_ROOT_UNAVAILABLE"
        assert "exactly the single expected work file" in exc_info.value.diagnostic.message

    def test_finalize_format_snapshot_mismatch_rejected(
        self, workspace_setup: tuple[Path, FilesystemOutputWorkspace]
    ) -> None:
        output_root, ws = workspace_setup
        ctx = create_sample_context(output_root)
        ws.register_context(ctx)
        ws.begin_format(ctx)

        ctx.work_path.write_bytes(b"DATA")
        snap = capture_output_snapshot(ctx.work_path)

        ctx.work_path.write_bytes(b"MUTATED_BYTES")

        with pytest.raises(BatchWorkspaceError) as exc_info:
            ws.finalize_format(ctx, snap)
        assert exc_info.value.diagnostic.code == "ARTIFACT_EXPORT_FAILED"
        assert "does not match validated snapshot" in exc_info.value.diagnostic.message

    def test_finalize_format_double_finalization_rejected(
        self, workspace_setup: tuple[Path, FilesystemOutputWorkspace]
    ) -> None:
        output_root, ws = workspace_setup
        ctx = create_sample_context(output_root)
        ws.register_context(ctx)
        ws.begin_format(ctx)

        ctx.work_path.write_bytes(b"DATA")
        snap = capture_output_snapshot(ctx.work_path)

        with patch("batch.output_workspace.publish_output_file", return_value=ctx.target_path):
            ws.finalize_format(ctx, snap)

        with pytest.raises(BatchWorkspaceError) as exc_info:
            ws.finalize_format(ctx, snap)
        assert exc_info.value.diagnostic.code == "INTERNAL_ERROR"
        assert "Cannot finalize format in state 'finalized'" in exc_info.value.diagnostic.message


class TestCleanupFormat:
    """Tests for cleanup_format coordinator state transitions and ownership guards."""

    def test_cleanup_format_allocated_absent_transitions_to_cleaned(
        self, workspace_setup: tuple[Path, FilesystemOutputWorkspace]
    ) -> None:
        output_root, ws = workspace_setup
        ctx = create_sample_context(output_root)
        ws.register_context(ctx)

        assert not ctx.work_path.parent.exists()
        ws.cleanup_format(ctx)
        assert not ctx.work_path.parent.exists()
        assert ws._records[(ctx.request_id, ctx.input, ctx.format)].state == "cleaned"

    def test_cleanup_format_allocated_existing_refuses_deletion_without_ownership(
        self, workspace_setup: tuple[Path, FilesystemOutputWorkspace]
    ) -> None:
        """An allocated collision winner directory must never be deleted without proven ownership."""
        output_root, ws = workspace_setup
        ctx = create_sample_context(output_root)
        ws.register_context(ctx)

        # Another process created the collision winner directory before activation
        ctx.work_path.parent.mkdir()
        ctx.work_path.write_bytes(b"OTHER_PROCESS_DATA")

        with pytest.raises(BatchWorkspaceError) as exc_info:
            ws.cleanup_format(ctx)

        assert exc_info.value.diagnostic.code == "ARTIFACT_EXPORT_FAILED"
        assert "without proven ownership" in exc_info.value.diagnostic.message
        # Crucial invariant: other process's data and directory are NEVER deleted
        assert ctx.work_path.exists()
        assert ctx.work_path.parent.exists()

    def test_cleanup_format_active_cleans_file_and_dir(
        self, workspace_setup: tuple[Path, FilesystemOutputWorkspace]
    ) -> None:
        output_root, ws = workspace_setup
        ctx = create_sample_context(output_root)
        ws.register_context(ctx)
        ws.begin_format(ctx)

        ctx.work_path.write_bytes(b"PARTIAL")
        assert ctx.work_path.exists()
        assert ctx.work_path.parent.exists()

        ws.cleanup_format(ctx)
        assert not ctx.work_path.exists()
        assert not ctx.work_path.parent.exists()
        assert ws._records[(ctx.request_id, ctx.input, ctx.format)].state == "cleaned"

    def test_cleanup_format_finalized_rejected(self, workspace_setup: tuple[Path, FilesystemOutputWorkspace]) -> None:
        output_root, ws = workspace_setup
        ctx = create_sample_context(output_root)
        ws.register_context(ctx)
        ws.begin_format(ctx)
        ctx.work_path.write_bytes(b"DATA")
        snap = capture_output_snapshot(ctx.work_path)

        with patch("batch.output_workspace.publish_output_file", return_value=ctx.target_path):
            ws.finalize_format(ctx, snap)

        with pytest.raises(BatchWorkspaceError) as exc_info:
            ws.cleanup_format(ctx)
        assert exc_info.value.diagnostic.code == "INTERNAL_ERROR"
        assert "Cannot clean up finalized workspace format" in exc_info.value.diagnostic.message

    def test_cleanup_format_cleaned_state_is_noop(
        self, workspace_setup: tuple[Path, FilesystemOutputWorkspace]
    ) -> None:
        output_root, ws = workspace_setup
        ctx = create_sample_context(output_root)
        ws.register_context(ctx)
        ws.cleanup_format(ctx)
        assert ws._records[(ctx.request_id, ctx.input, ctx.format)].state == "cleaned"

        # Idempotent call
        ws.cleanup_format(ctx)
        assert ws._records[(ctx.request_id, ctx.input, ctx.format)].state == "cleaned"

    def test_cleanup_format_rechecks_root_before_inspection_or_deletion(
        self, workspace_setup: tuple[Path, FilesystemOutputWorkspace]
    ) -> None:
        output_root, ws = workspace_setup
        ctx = create_sample_context(output_root)
        ws.register_context(ctx)
        ws.begin_format(ctx)

        with (
            patch.object(
                ws,
                "_recheck_output_root",
                side_effect=BatchWorkspaceError(
                    BatchDiagnostic(code="OUTPUT_ROOT_UNAVAILABLE", message="Root tampered")
                ),
            ),
            pytest.raises(BatchWorkspaceError) as exc_info,
        ):
            ws.cleanup_format(ctx)

        assert exc_info.value.diagnostic.code == "OUTPUT_ROOT_UNAVAILABLE"
        assert "Root tampered" in exc_info.value.diagnostic.message
