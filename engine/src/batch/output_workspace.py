"""Guarded output workspace, private per-format staging, and lifecycle coordination.

This module provides the M5.3 output workspace capability:
- BatchWorkspaceError: typed contract exception carrying validated sanitized diagnostics.
- BatchOutputWorkspace: protocol defining the handler export and publication lifecycle.
- FilesystemOutputWorkspace: production implementation enforcing private work directories,
  exclusive activation, guarded cleanup, and Windows atomic no-replace publication.
- allocate_private_work_path: re-exported candidate allocator for prepared batch formats.
- OutputSnapshot and capture primitives re-exported from batch.output_snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, Protocol

from batch.allocation import APPROVED_FORMAT_SUFFIXES, BatchSafetyRejectionError
from batch.execution import BatchItemContext
from batch.filesystem import (
    PathIdentity,
    assert_strictly_contained,
    get_path_identity,
    is_symlink_or_reparse_point,
)
from batch.models import BatchDiagnostic
from batch.output_lifecycle import (
    WORK_DIR_PREFIX,
    activate_private_work_directory,
    allocate_private_work_path,
    cleanup_private_work_directory,
)
from batch.output_publication import (
    BatchWorkspaceError,
    _as_batch_output_format,
    publish_output_file,
)
from batch.output_snapshot import (
    OutputSnapshot,
    capture_output_snapshot,
    verify_output_snapshot_equality,
)

FormatWorkspaceState = Literal["allocated", "active", "finalized", "cleaned"]


class BatchOutputWorkspace(Protocol):
    """Protocol for per-format temporary staging, validation, cleanup, and publication."""

    def begin_format(self, context: BatchItemContext) -> None:
        """Exclusively activate the private work directory and confirm work target is absent."""
        ...

    def finalize_format(self, context: BatchItemContext, snapshot: OutputSnapshot) -> Path:
        """Verify the validated work artifact and atomically publish it to final target."""
        ...

    def cleanup_format(self, context: BatchItemContext) -> None:
        """Safely clean up the private work directory on failure or cancellation."""
        ...


@dataclass
class _FormatRecord:
    """Private mutable tracking record for one format's staging workspace."""

    context: BatchItemContext
    work_dir: Path
    state: FormatWorkspaceState = "allocated"
    work_dir_identity: PathIdentity | None = None


class FilesystemOutputWorkspace:
    """Production filesystem output workspace enforcing guarded staging and atomic publication."""

    def __init__(
        self,
        output_root: Path,
        output_root_identity: PathIdentity,
    ) -> None:
        self._output_root: Final[Path] = output_root.resolve()
        self._output_root_identity: Final[PathIdentity] = output_root_identity
        self._records: dict[tuple[str, str, str], _FormatRecord] = {}

    def register_context(self, context: BatchItemContext) -> None:
        """Register an allocated format context with the workspace prior to execution."""
        key = (context.request_id, context.input, context.format)
        if key in self._records:
            raise BatchWorkspaceError(
                BatchDiagnostic(
                    code="INTERNAL_ERROR",
                    message=f"Workspace format '{context.format}' is already registered",
                    format=_as_batch_output_format(context.format),
                ),
                request_id=context.request_id,
            )

        self._records[key] = _FormatRecord(
            context=context,
            work_dir=context.work_path.parent,
            state="allocated",
        )

    def _validate_and_get_record(self, context: BatchItemContext) -> _FormatRecord:
        key = (context.request_id, context.input, context.format)
        record = self._records.get(key)
        if record is None:
            raise BatchWorkspaceError(
                BatchDiagnostic(
                    code="INTERNAL_ERROR",
                    message=f"No workspace allocated for format '{context.format}'",
                    format=_as_batch_output_format(context.format),
                ),
                request_id=context.request_id,
            )

        ctx = record.context
        if (
            ctx.request_id != context.request_id
            or ctx.operation_id != context.operation_id
            or ctx.input != context.input
            or ctx.source_path != context.source_path
            or ctx.format != context.format
            or ctx.target_relative_path != context.target_relative_path
            or ctx.work_path != context.work_path
            or ctx.target_path != context.target_path
        ):
            raise BatchWorkspaceError(
                BatchDiagnostic(
                    code="INTERNAL_ERROR",
                    message="Context fields do not match registered allocation record",
                    format=_as_batch_output_format(context.format),
                ),
                request_id=context.request_id,
            )
        return record

    def _recheck_output_root(self, *, request_id: str) -> None:
        if not self._output_root.exists() or is_symlink_or_reparse_point(self._output_root):
            raise BatchWorkspaceError(
                BatchDiagnostic(
                    code="OUTPUT_ROOT_UNAVAILABLE",
                    message="Output root is missing or has become a symbolic link / reparse point",
                ),
                request_id=request_id,
            )
        try:
            current_id = get_path_identity(self._output_root)
        except Exception as exc:
            raise BatchWorkspaceError(
                BatchDiagnostic(
                    code="OUTPUT_ROOT_UNAVAILABLE",
                    message="Cannot verify output root identity",
                ),
                request_id=request_id,
            ) from exc

        if (
            current_id.device != self._output_root_identity.device
            or current_id.inode != self._output_root_identity.inode
        ):
            raise BatchWorkspaceError(
                BatchDiagnostic(
                    code="OUTPUT_ROOT_UNAVAILABLE",
                    message="Output root identity changed unexpectedly",
                ),
                request_id=request_id,
            )

    def _check_root_and_containment(
        self,
        paths: tuple[Path, ...],
        *,
        context: BatchItemContext,
    ) -> None:
        self._recheck_output_root(request_id=context.request_id)
        for p in paths:
            try:
                assert_strictly_contained(p, self._output_root, request_id=context.request_id)
            except BatchSafetyRejectionError as exc:
                raise BatchWorkspaceError(
                    BatchDiagnostic(
                        code="OUTPUT_ROOT_UNAVAILABLE",
                        message=exc.message,
                        format=_as_batch_output_format(context.format),
                    ),
                    request_id=context.request_id,
                ) from exc

    def _validate_format_and_suffixes(self, context: BatchItemContext, record: _FormatRecord) -> None:
        if context.format not in APPROVED_FORMAT_SUFFIXES:
            raise BatchWorkspaceError(
                BatchDiagnostic(
                    code="INTERNAL_ERROR",
                    message=f"Unsupported format '{context.format}'",
                    format=_as_batch_output_format(context.format),
                ),
                request_id=context.request_id,
            )

        expected_suffix = APPROVED_FORMAT_SUFFIXES[context.format]
        if context.work_path.suffix.lower() != expected_suffix:
            raise BatchWorkspaceError(
                BatchDiagnostic(
                    code="INTERNAL_ERROR",
                    message=f"Work path suffix '{context.work_path.suffix}' does not match format '{context.format}'",
                    format=_as_batch_output_format(context.format),
                ),
                request_id=context.request_id,
            )
        if context.target_path.suffix.lower() != expected_suffix:
            raise BatchWorkspaceError(
                BatchDiagnostic(
                    code="INTERNAL_ERROR",
                    message=f"Target path suffix '{context.target_path.suffix}' does not match format '{context.format}'",
                    format=_as_batch_output_format(context.format),
                ),
                request_id=context.request_id,
            )
        if context.work_path.parent != record.work_dir:
            raise BatchWorkspaceError(
                BatchDiagnostic(
                    code="INTERNAL_ERROR",
                    message="Work path is not located directly in assigned work directory",
                    format=_as_batch_output_format(context.format),
                ),
                request_id=context.request_id,
            )

    def begin_format(self, context: BatchItemContext) -> None:
        """Exclusively activate the private work directory and verify target absence."""
        record = self._validate_and_get_record(context)
        if record.state != "allocated":
            raise BatchWorkspaceError(
                BatchDiagnostic(
                    code="INTERNAL_ERROR",
                    message=f"Workspace format '{context.format}' is already {record.state}",
                    format=_as_batch_output_format(context.format),
                ),
                request_id=context.request_id,
            )

        self._validate_format_and_suffixes(context, record)
        self._check_root_and_containment((context.target_path, record.work_dir), context=context)

        if context.target_path.exists() or is_symlink_or_reparse_point(context.target_path):
            raise BatchWorkspaceError(
                BatchDiagnostic(
                    code="TARGET_ALREADY_EXISTS",
                    message=f"Target file already exists for format '{context.format}'",
                    format=_as_batch_output_format(context.format),
                ),
                request_id=context.request_id,
            )

        work_dir_id = activate_private_work_directory(
            record.work_dir,
            context.work_path,
            self._output_root,
            request_id=context.request_id,
            format_name=context.format,
        )

        record.work_dir_identity = work_dir_id
        record.state = "active"

    def finalize_format(self, context: BatchItemContext, snapshot: OutputSnapshot) -> Path:
        """Verify the validated work artifact and atomically publish it to final target."""
        if not isinstance(snapshot, OutputSnapshot):
            raise TypeError(f"snapshot must be OutputSnapshot, got {type(snapshot).__name__}")

        record = self._validate_and_get_record(context)
        if record.state != "active":
            raise BatchWorkspaceError(
                BatchDiagnostic(
                    code="INTERNAL_ERROR",
                    message=f"Cannot finalize format in state '{record.state}'",
                    format=_as_batch_output_format(context.format),
                ),
                request_id=context.request_id,
            )

        if record.work_dir_identity is None:
            raise BatchWorkspaceError(
                BatchDiagnostic(
                    code="INTERNAL_ERROR",
                    message="Work directory identity missing for active format record",
                    format=_as_batch_output_format(context.format),
                ),
                request_id=context.request_id,
            )

        self._check_root_and_containment((context.target_path, context.work_path), context=context)

        try:
            current_work_dir_id = get_path_identity(record.work_dir)
        except Exception as exc:
            raise BatchWorkspaceError(
                BatchDiagnostic(
                    code="OUTPUT_ROOT_UNAVAILABLE",
                    message="Cannot verify work directory identity during export",
                    format=_as_batch_output_format(context.format),
                ),
                request_id=context.request_id,
            ) from exc

        if current_work_dir_id != record.work_dir_identity:
            raise BatchWorkspaceError(
                BatchDiagnostic(
                    code="OUTPUT_ROOT_UNAVAILABLE",
                    message="Work directory identity changed during export",
                    format=_as_batch_output_format(context.format),
                ),
                request_id=context.request_id,
            )

        try:
            inventory = list(record.work_dir.iterdir())
        except Exception as exc:
            raise BatchWorkspaceError(
                BatchDiagnostic(
                    code="OUTPUT_ROOT_UNAVAILABLE",
                    message="Failed to inspect work directory inventory",
                    format=_as_batch_output_format(context.format),
                ),
                request_id=context.request_id,
            ) from exc

        if len(inventory) != 1 or inventory[0] != context.work_path:
            raise BatchWorkspaceError(
                BatchDiagnostic(
                    code="OUTPUT_ROOT_UNAVAILABLE",
                    message="Work directory must contain exactly the single expected work file",
                    format=_as_batch_output_format(context.format),
                ),
                request_id=context.request_id,
            )

        try:
            current_snap = capture_output_snapshot(context.work_path)
        except Exception as exc:
            raise BatchWorkspaceError(
                BatchDiagnostic(
                    code="ARTIFACT_EXPORT_FAILED",
                    message="Failed to recapture work file snapshot prior to publication",
                    format=_as_batch_output_format(context.format),
                ),
                request_id=context.request_id,
            ) from exc

        if not verify_output_snapshot_equality(snapshot, current_snap):
            raise BatchWorkspaceError(
                BatchDiagnostic(
                    code="ARTIFACT_EXPORT_FAILED",
                    message="Work artifact snapshot does not match validated snapshot",
                    format=_as_batch_output_format(context.format),
                ),
                request_id=context.request_id,
            )

        published_path = publish_output_file(context, snapshot, self._output_root)
        record.state = "finalized"
        return published_path

    def cleanup_format(self, context: BatchItemContext) -> None:
        """Safely clean up the private work directory on failure or cancellation."""
        record = self._validate_and_get_record(context)
        self._check_root_and_containment((record.work_dir,), context=context)

        if record.state == "allocated":
            if not record.work_dir.exists() and not is_symlink_or_reparse_point(record.work_dir):
                record.state = "cleaned"
                return
            raise BatchWorkspaceError(
                BatchDiagnostic(
                    code="ARTIFACT_EXPORT_FAILED",
                    message="Cannot clean up existing work directory in allocated state without proven ownership",
                    format=_as_batch_output_format(context.format),
                ),
                request_id=context.request_id,
            )

        if record.state == "active":
            cleanup_private_work_directory(
                record.work_dir,
                context.work_path,
                self._output_root,
                expected_dir_id=record.work_dir_identity,
                request_id=context.request_id,
                format_name=context.format,
            )
            record.state = "cleaned"
            return

        if record.state == "cleaned":
            return

        if record.state == "finalized":
            raise BatchWorkspaceError(
                BatchDiagnostic(
                    code="INTERNAL_ERROR",
                    message="Cannot clean up finalized workspace format",
                    format=_as_batch_output_format(context.format),
                ),
                request_id=context.request_id,
            )

        raise BatchWorkspaceError(
            BatchDiagnostic(
                code="INTERNAL_ERROR",
                message=f"Unexpected workspace state '{record.state}' during cleanup",
                format=_as_batch_output_format(context.format),
            ),
            request_id=context.request_id,
        )


__all__ = [
    "WORK_DIR_PREFIX",
    "BatchOutputWorkspace",
    "BatchWorkspaceError",
    "FilesystemOutputWorkspace",
    "OutputSnapshot",
    "allocate_private_work_path",
    "capture_output_snapshot",
    "verify_output_snapshot_equality",
]
