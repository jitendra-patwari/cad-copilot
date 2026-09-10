"""Unit tests for FilesystemBatchSafetyBoundary workspace delegation and publication safety."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from batch.allocation import allocate_batch_work
from batch.execution import BatchExecutionSpec, BatchItemContext
from batch.filesystem import PathIdentity
from batch.output_snapshot import OutputSnapshot, capture_output_snapshot
from batch.output_workspace import BatchWorkspaceError
from batch.safety import FilesystemBatchSafetyBoundary


def _make_spec(
    input_root: Path,
    output_root: Path,
    inputs: tuple[str, ...],
    formats: tuple[str, ...] = ("step", "stl"),
    request_id: str = "req-safety-ws-001",
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


class TestSafetyBoundaryWorkspaceDelegation:
    """Tests for FilesystemBatchSafetyBoundary delegation to FilesystemOutputWorkspace."""

    def test_workspace_delegation_lifecycle(self, tmp_path: Path) -> None:
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        (in_dir / "part.par").write_bytes(b"cad part")

        spec = _make_spec(in_dir, out_dir, inputs=("part.par",), formats=("step",))
        work = allocate_batch_work(spec)
        boundary = FilesystemBatchSafetyBoundary()
        prepared = boundary.prepare(spec, work)

        prep_file = prepared.files[0]
        prep_fmt = prep_file.formats[0]

        context = BatchItemContext(
            request_id=spec.request_id,
            operation_id=spec.operation_id,
            input=prep_file.input,
            source_path=prep_file.source_path,
            format=prep_fmt.format,
            target_relative_path=prep_fmt.target_relative_path,
            work_path=prep_fmt.work_path,
            target_path=prep_fmt.target_path,
        )

        assert boundary.verify_before_open(prep_file) is None

        # 1. begin_format
        boundary.begin_format(context)
        assert context.work_path.parent.exists()
        assert not context.work_path.exists()

        # Write generated artifact to work path
        context.work_path.write_bytes(b"generated step artifact bytes")
        snapshot = capture_output_snapshot(context.work_path)

        # 2. finalize_format
        published = boundary.finalize_format(context, snapshot)
        assert published == context.target_path
        assert published.exists()
        assert published.read_bytes() == b"generated step artifact bytes"
        assert not context.work_path.parent.exists()

        # Post-close verification succeeds and transitions to 'closed'
        assert boundary.verify_after_close(prep_file) is None

    def test_cleanup_format_through_boundary(self, tmp_path: Path) -> None:
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        (in_dir / "part.par").write_bytes(b"cad part")

        spec = _make_spec(in_dir, out_dir, inputs=("part.par",), formats=("step",))
        work = allocate_batch_work(spec)
        boundary = FilesystemBatchSafetyBoundary()
        prepared = boundary.prepare(spec, work)

        prep_file = prepared.files[0]
        prep_fmt = prep_file.formats[0]

        context = BatchItemContext(
            request_id=spec.request_id,
            operation_id=spec.operation_id,
            input=prep_file.input,
            source_path=prep_file.source_path,
            format=prep_fmt.format,
            target_relative_path=prep_fmt.target_relative_path,
            work_path=prep_fmt.work_path,
            target_path=prep_fmt.target_path,
        )

        assert boundary.verify_before_open(prep_file) is None

        boundary.begin_format(context)
        context.work_path.write_bytes(b"failed artifact")
        assert context.work_path.parent.exists()

        boundary.cleanup_format(context)
        assert not context.work_path.exists()
        assert not context.work_path.parent.exists()

    def test_work_artifact_aliasing_source_file_rejected(self, tmp_path: Path) -> None:
        """Publication fails closed if the work artifact aliases any prepared source file."""
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        source_file = in_dir / "part.par"
        source_bytes = b"cad part source bytes"
        source_file.write_bytes(source_bytes)

        spec = _make_spec(in_dir, out_dir, inputs=("part.par",), formats=("step",))
        work = allocate_batch_work(spec)
        boundary = FilesystemBatchSafetyBoundary()
        prepared = boundary.prepare(spec, work)

        prep_file = prepared.files[0]
        prep_fmt = prep_file.formats[0]

        context = BatchItemContext(
            request_id=spec.request_id,
            operation_id=spec.operation_id,
            input=prep_file.input,
            source_path=prep_file.source_path,
            format=prep_fmt.format,
            target_relative_path=prep_fmt.target_relative_path,
            work_path=prep_fmt.work_path,
            target_path=prep_fmt.target_path,
        )

        assert boundary.verify_before_open(prep_file) is None
        boundary.begin_format(context)

        # Hard-link the source file into the private work path
        os.link(context.source_path, context.work_path)
        snapshot = capture_output_snapshot(context.work_path)

        with pytest.raises(BatchWorkspaceError) as exc_info:
            boundary.finalize_format(context, snapshot)

        assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
        assert "cannot alias source file" in exc_info.value.message
        assert not context.target_path.exists()
        assert context.source_path.exists()
        assert context.source_path.read_bytes() == source_bytes

        # Cleanup removes the alias link and private work directory without deleting the source
        boundary.cleanup_format(context)
        assert not context.work_path.exists()
        assert not context.work_path.parent.exists()
        assert context.source_path.exists()
        assert context.source_path.read_bytes() == source_bytes

    def test_work_artifact_aliasing_source_file_rejected_with_differing_mode(self, tmp_path: Path) -> None:
        """Alias detection compares device and inode explicitly, rejecting aliasing even when file mode differs."""
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        source_file = in_dir / "part.par"
        source_bytes = b"cad part source bytes"
        source_file.write_bytes(source_bytes)

        spec = _make_spec(in_dir, out_dir, inputs=("part.par",), formats=("step",))
        work = allocate_batch_work(spec)
        boundary = FilesystemBatchSafetyBoundary()
        prepared = boundary.prepare(spec, work)

        prep_file = prepared.files[0]
        prep_fmt = prep_file.formats[0]

        context = BatchItemContext(
            request_id=spec.request_id,
            operation_id=spec.operation_id,
            input=prep_file.input,
            source_path=prep_file.source_path,
            format=prep_fmt.format,
            target_relative_path=prep_fmt.target_relative_path,
            work_path=prep_fmt.work_path,
            target_path=prep_fmt.target_path,
        )

        assert boundary.verify_before_open(prep_file) is None
        boundary.begin_format(context)

        # Hard-link the source file into the private work path
        os.link(context.source_path, context.work_path)

        # Modify the source file record's mode in boundary memory to simulate differing mode
        file_info = boundary._files[context.input]
        assert file_info.source_id is not None
        differing_mode = file_info.source_id.mode ^ 0o111  # toggle execute bits
        file_info.source_id = PathIdentity(
            device=file_info.source_id.device,
            inode=file_info.source_id.inode,
            mode=differing_mode,
        )

        snapshot = capture_output_snapshot(context.work_path)
        assert snapshot.identity.mode != differing_mode
        assert snapshot.identity.device == file_info.source_id.device
        assert snapshot.identity.inode == file_info.source_id.inode

        # Finalize must STILL reject the alias because device and inode match
        with pytest.raises(BatchWorkspaceError) as exc_info:
            boundary.finalize_format(context, snapshot)

        assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
        assert "cannot alias source file" in exc_info.value.message
        assert not context.target_path.exists()

        boundary.cleanup_format(context)
        assert not context.work_path.exists()
        assert context.source_path.exists()

    def test_finalize_format_rejects_zero_inode_snapshot(self, tmp_path: Path) -> None:
        """finalize_format rejects output snapshots with unavailable identity (zero inode)."""
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        (in_dir / "part.par").write_bytes(b"cad part")

        spec = _make_spec(in_dir, out_dir, inputs=("part.par",), formats=("step",))
        work = allocate_batch_work(spec)
        boundary = FilesystemBatchSafetyBoundary()
        prepared = boundary.prepare(spec, work)

        prep_file = prepared.files[0]
        prep_fmt = prep_file.formats[0]

        context = BatchItemContext(
            request_id=spec.request_id,
            operation_id=spec.operation_id,
            input=prep_file.input,
            source_path=prep_file.source_path,
            format=prep_fmt.format,
            target_relative_path=prep_fmt.target_relative_path,
            work_path=prep_fmt.work_path,
            target_path=prep_fmt.target_path,
        )

        assert boundary.verify_before_open(prep_file) is None
        boundary.begin_format(context)
        context.work_path.write_bytes(b"valid step data")
        real_snap = capture_output_snapshot(context.work_path)

        # Construct an object with zero-inode identity
        zero_id = PathIdentity(device=real_snap.identity.device, inode=0, mode=real_snap.identity.mode)
        # Use object.__new__ to simulate bypassing dataclass __post_init__ or an external mock
        zero_snap = object.__new__(OutputSnapshot)
        object.__setattr__(zero_snap, "identity", zero_id)
        object.__setattr__(zero_snap, "size_bytes", real_snap.size_bytes)
        object.__setattr__(zero_snap, "mtime_ns", real_snap.mtime_ns)
        object.__setattr__(zero_snap, "sha256", real_snap.sha256)

        with pytest.raises(BatchWorkspaceError) as exc_info:
            boundary.finalize_format(context, zero_snap)

        assert exc_info.value.code == "ARTIFACT_EXPORT_FAILED"
        assert "zero inode" in exc_info.value.message

        boundary.cleanup_format(context)

    def test_uninitialized_boundary_workspace_raises_typed_error(self, tmp_path: Path) -> None:
        """Calling workspace methods before prepare() must raise BatchWorkspaceError with INTERNAL_ERROR."""
        out_dir = tmp_path / "output"
        boundary = FilesystemBatchSafetyBoundary()

        dummy_context = BatchItemContext(
            request_id="req-uninit-001",
            operation_id="export_3d",
            input="part.par",
            source_path=tmp_path / "part.par",
            format="step",
            target_relative_path="part.step",
            work_path=out_dir / "work" / "part.step",
            target_path=out_dir / "part.step",
        )

        with pytest.raises(BatchWorkspaceError) as exc_info:
            boundary.begin_format(dummy_context)
        assert exc_info.value.code == "INTERNAL_ERROR"
        assert exc_info.value.request_id == "req-uninit-001"

        dummy_file = tmp_path / "artifact.step"
        dummy_file.write_bytes(b"bytes")
        snap = capture_output_snapshot(dummy_file)

        with pytest.raises(BatchWorkspaceError) as exc_info:
            boundary.finalize_format(dummy_context, snap)
        assert exc_info.value.code == "INTERNAL_ERROR"

        with pytest.raises(BatchWorkspaceError) as exc_info:
            boundary.cleanup_format(dummy_context)
        assert exc_info.value.code == "INTERNAL_ERROR"

    def test_workspace_rejects_format_when_file_not_opened(self, tmp_path: Path) -> None:
        """Format staging and publication must fail closed if input file is not in 'opened' state."""
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        (in_dir / "part.par").write_bytes(b"cad part")

        spec = _make_spec(in_dir, out_dir, inputs=("part.par",), formats=("step",))
        work = allocate_batch_work(spec)
        boundary = FilesystemBatchSafetyBoundary()
        prepared = boundary.prepare(spec, work)

        prep_file = prepared.files[0]
        prep_fmt = prep_file.formats[0]

        context = BatchItemContext(
            request_id=spec.request_id,
            operation_id=spec.operation_id,
            input=prep_file.input,
            source_path=prep_file.source_path,
            format=prep_fmt.format,
            target_relative_path=prep_fmt.target_relative_path,
            work_path=prep_fmt.work_path,
            target_path=prep_fmt.target_path,
        )

        # 1. Calling begin_format before verify_before_open (file is 'prepared')
        with pytest.raises(BatchWorkspaceError) as exc_info:
            boundary.begin_format(context)
        assert exc_info.value.code == "INTERNAL_ERROR"
        assert "in state 'prepared' (must be 'opened')" in exc_info.value.message

        # 2. Transition to opened, then closed
        assert boundary.verify_before_open(prep_file) is None
        assert boundary.verify_after_close(prep_file) is None

        # 3. Calling begin_format after verify_after_close (file is 'closed')
        with pytest.raises(BatchWorkspaceError) as exc_info:
            boundary.begin_format(context)
        assert exc_info.value.code == "INTERNAL_ERROR"
        assert "in state 'closed' (must be 'opened')" in exc_info.value.message
