"""Production filesystem safety boundary for CAD Copilot batch execution.

Implements the BatchSafetyBoundary protocol for the execution hub and
the BatchOutputWorkspace protocol for per-format output lifecycle staging:
- Validates local input and output roots with Windows local drive classification.
- Rejects path traversal, reserved Windows device names, ADS colons, and reparse points.
- Detects source aliases sharing the same filesystem identity across distinct inputs.
- Captures pre-open and post-close SourceSnapshots with zero-tolerance equality verification.
- Enforces strict per-file snapshot lifecycle: pre-open exactly once, post-close requires snapshot.
- Delegates per-format staging, cleanup, and atomic no-replace publication to FilesystemOutputWorkspace.
"""

from __future__ import annotations

from pathlib import Path

from batch.allocation import (
    AllocatedBatchWork,
    BatchSafetyBoundary,
    BatchSafetyRejectionError,
    PreparedBatchFile,
    PreparedBatchWork,
)
from batch.execution import BatchExecutionSpec, BatchItemContext
from batch.filesystem import (
    PathIdentity,
    assert_strictly_contained,
    get_path_identity,
    is_symlink_or_reparse_point,
    walk_and_verify_components,
)
from batch.models import BatchDiagnostic
from batch.output_publication import _as_batch_output_format
from batch.output_snapshot import OutputSnapshot
from batch.output_workspace import (
    BatchOutputWorkspace,
    BatchWorkspaceError,
    FilesystemOutputWorkspace,
)
from batch.safety_preparation import (
    _PreparedFileInfo,
    prepare_safety_boundary,
)
from batch.source_integrity import (
    SourceIntegrityError,
    capture_source_snapshot,
    verify_snapshot_equality,
)


class FilesystemBatchSafetyBoundary(BatchSafetyBoundary, BatchOutputWorkspace):
    """Production filesystem safety and source-integrity boundary for CAD Copilot batch runs."""

    def __init__(self) -> None:
        self._input_root: Path | None = None
        self._input_root_id: PathIdentity | None = None
        self._output_root: Path | None = None
        self._output_root_id: PathIdentity | None = None

        self._workspace: FilesystemOutputWorkspace | None = None
        self._files: dict[str, _PreparedFileInfo] = {}
        self._prepared: bool = False

    @property
    def is_prepared(self) -> bool:
        """Return True if prepare() has completed successfully."""
        return self._prepared

    def prepare(self, spec: BatchExecutionSpec, work: AllocatedBatchWork) -> PreparedBatchWork:
        """Boundary-prepare filesystem paths, validate roots and inputs, and stage format outputs."""
        if not isinstance(spec, BatchExecutionSpec):
            raise TypeError(f"spec must be BatchExecutionSpec, got {type(spec).__name__}")
        if not isinstance(work, AllocatedBatchWork):
            raise TypeError(f"work must be AllocatedBatchWork, got {type(work).__name__}")

        if self._prepared:
            raise BatchSafetyRejectionError(
                BatchDiagnostic(
                    code="INTERNAL_ERROR",
                    message="Safety boundary instance has already been prepared",
                ),
                request_id=spec.request_id,
            )

        prep_result = prepare_safety_boundary(spec, work)

        self._input_root = prep_result.input_root
        self._input_root_id = prep_result.input_root_id
        self._output_root = prep_result.output_root
        self._output_root_id = prep_result.output_root_id
        self._workspace = prep_result.workspace
        self._files = prep_result.files
        self._prepared = True

        return prep_result.prepared_work

    def verify_before_open(self, prepared_file: PreparedBatchFile) -> BatchDiagnostic | None:
        """Verify source file integrity and accessibility immediately prior to opening."""
        if not self._prepared or self._input_root is None or self._input_root_id is None:
            return BatchDiagnostic(
                code="INTERNAL_ERROR",
                message=f"Safety boundary not prepared before opening '{prepared_file.input}'",
            )

        file_info = self._files.get(prepared_file.input)
        if file_info is None or file_info.prepared_file != prepared_file:
            return BatchDiagnostic(
                code="INTERNAL_ERROR",
                message=f"Prepared file context mismatch for '{prepared_file.input}'",
            )

        # Preflight errors bypass filesystem access, but enforce single-call transition
        if prepared_file.preflight_error is not None:
            if file_info.state != "preflight_failed":
                return BatchDiagnostic(
                    code="INTERNAL_ERROR",
                    message=f"Duplicate or invalid pre-open call for preflight-failed file '{prepared_file.input}'",
                )
            file_info.state = "closed"
            return prepared_file.preflight_error

        # Enforce exact 'prepared' state: reject duplicate pre-open or out-of-order calls
        if file_info.state != "prepared":
            return BatchDiagnostic(
                code="INTERNAL_ERROR",
                message=f"Duplicate or out-of-order pre-open call for '{prepared_file.input}' (state='{file_info.state}')",
            )

        # Re-verify input root identity and safety
        if not self._input_root.exists() or is_symlink_or_reparse_point(self._input_root):
            file_info.state = "closed"
            return BatchDiagnostic(
                code="INPUT_PATH_NOT_ALLOWED",
                message=f"Input root is missing or unsafe for '{prepared_file.input}'",
            )

        try:
            current_root_id = get_path_identity(self._input_root)
        except Exception:
            file_info.state = "closed"
            return BatchDiagnostic(
                code="INPUT_PATH_NOT_ALLOWED",
                message=f"Cannot verify input root identity for '{prepared_file.input}'",
            )

        if current_root_id.device != self._input_root_id.device or current_root_id.inode != self._input_root_id.inode:
            file_info.state = "closed"
            return BatchDiagnostic(
                code="INPUT_PATH_NOT_ALLOWED",
                message=f"Input root identity changed unexpectedly for '{prepared_file.input}'",
            )

        # Re-walk path components under input root to verify safety and strict containment
        try:
            source_path = walk_and_verify_components(self._input_root, prepared_file.input)
        except BatchSafetyRejectionError as exc:
            file_info.state = "closed"
            return exc.diagnostic
        except Exception:
            file_info.state = "closed"
            return BatchDiagnostic(
                code="INPUT_PATH_NOT_ALLOWED",
                message=f"Failed to verify path components for '{prepared_file.input}'",
            )

        # Check for reparse point on the leaf source file before checking existence
        if is_symlink_or_reparse_point(source_path):
            file_info.state = "closed"
            return BatchDiagnostic(
                code="INPUT_PATH_NOT_ALLOWED",
                message=f"Source file is a symbolic link or reparse point: '{prepared_file.input}'",
            )

        # Check file existence: disappearance mapped to INPUT_FILE_NOT_FOUND
        if not source_path.exists():
            file_info.state = "closed"
            return BatchDiagnostic(
                code="INPUT_FILE_NOT_FOUND",
                message=f"Source file not found: '{prepared_file.input}'",
            )

        # Capture current source identity and verify it is a regular file with valid identity
        try:
            current_source_id = get_path_identity(source_path)
        except Exception:
            file_info.state = "closed"
            return BatchDiagnostic(
                code="INPUT_PATH_NOT_ALLOWED",
                message=f"Cannot verify source file identity for '{prepared_file.input}'",
            )

        if not current_source_id.is_regular_file:
            file_info.state = "closed"
            return BatchDiagnostic(
                code="INPUT_PATH_NOT_ALLOWED",
                message=f"Input path is not a regular file: '{prepared_file.input}'",
            )

        if current_source_id.inode == 0:
            file_info.state = "closed"
            return BatchDiagnostic(
                code="INPUT_PATH_NOT_ALLOWED",
                message=f"Input file identity unavailable (zero inode): '{prepared_file.input}'",
            )

        # Require identity match with preparation record (comparing device and inode)
        if (
            file_info.source_id is None
            or current_source_id.device != file_info.source_id.device
            or current_source_id.inode != file_info.source_id.inode
        ):
            file_info.state = "closed"
            return BatchDiagnostic(
                code="INPUT_PATH_NOT_ALLOWED",
                message=f"Input file was replaced after preparation: '{prepared_file.input}'",
            )

        # Verify file is not zero bytes
        try:
            if source_path.lstat().st_size == 0:
                file_info.state = "closed"
                return BatchDiagnostic(
                    code="INPUT_PATH_NOT_ALLOWED",
                    message=f"Input file cannot be zero bytes: '{prepared_file.input}'",
                )
        except Exception:
            file_info.state = "closed"
            return BatchDiagnostic(
                code="INPUT_PATH_NOT_ALLOWED",
                message=f"Cannot determine input file size: '{prepared_file.input}'",
            )

        # Capture pre-open source snapshot
        try:
            snapshot = capture_source_snapshot(source_path)
            if not snapshot.identity.is_regular_file:
                file_info.state = "closed"
                return BatchDiagnostic(
                    code="INPUT_PATH_NOT_ALLOWED",
                    message=f"Input path is not a regular file: '{prepared_file.input}'",
                )
            if snapshot.identity.inode == 0:
                file_info.state = "closed"
                return BatchDiagnostic(
                    code="INPUT_PATH_NOT_ALLOWED",
                    message=f"Input file identity unavailable (zero inode): '{prepared_file.input}'",
                )
            if (
                file_info.source_id is None
                or snapshot.identity.device != file_info.source_id.device
                or snapshot.identity.inode != file_info.source_id.inode
            ):
                file_info.state = "closed"
                return BatchDiagnostic(
                    code="INPUT_PATH_NOT_ALLOWED",
                    message=f"Input file was replaced after preparation: '{prepared_file.input}'",
                )
            if snapshot.size_bytes <= 0:
                file_info.state = "closed"
                return BatchDiagnostic(
                    code="INPUT_PATH_NOT_ALLOWED",
                    message=f"Input file cannot be zero bytes: '{prepared_file.input}'",
                )
            file_info.before_open_snapshot = snapshot
            file_info.state = "opened"
            return None
        except SourceIntegrityError as exc:
            file_info.state = "closed"
            if exc.reason == "not_found":
                return BatchDiagnostic(
                    code="INPUT_FILE_NOT_FOUND",
                    message=f"Source file not found: '{prepared_file.input}'",
                )
            return BatchDiagnostic(
                code="INPUT_PATH_NOT_ALLOWED",
                message=f"Source file integrity check failed before open: '{prepared_file.input}' ({exc.reason})",
            )
        except Exception:
            file_info.state = "closed"
            return BatchDiagnostic(
                code="INPUT_PATH_NOT_ALLOWED",
                message=f"Failed to capture pre-open snapshot for '{prepared_file.input}'",
            )

    def verify_after_close(self, prepared_file: PreparedBatchFile) -> BatchDiagnostic | None:
        """Verify source file immutability immediately after closing CAD document."""
        if not self._prepared or self._input_root is None or self._input_root_id is None:
            return BatchDiagnostic(
                code="INTERNAL_ERROR",
                message=f"Safety boundary not prepared before closing '{prepared_file.input}'",
            )

        file_info = self._files.get(prepared_file.input)
        if file_info is None or file_info.prepared_file != prepared_file:
            return BatchDiagnostic(
                code="INTERNAL_ERROR",
                message=f"Prepared file context mismatch for '{prepared_file.input}'",
            )

        # Enforce exact 'opened' state: require a captured before-open snapshot
        if file_info.state != "opened" or file_info.before_open_snapshot is None:
            return BatchDiagnostic(
                code="INTERNAL_ERROR",
                message=f"Post-close verification called without valid pre-open snapshot for '{prepared_file.input}' (state='{file_info.state}')",
            )

        before_snap = file_info.before_open_snapshot

        try:
            # Re-verify source file existence and safety
            if not prepared_file.source_path.exists() or is_symlink_or_reparse_point(prepared_file.source_path):
                return BatchDiagnostic(
                    code="SOURCE_INTEGRITY_FAILED",
                    message=f"Source integrity verification failed for '{prepared_file.input}'",
                )

            # Re-verify input root identity and containment
            if not self._input_root.exists() or is_symlink_or_reparse_point(self._input_root):
                return BatchDiagnostic(
                    code="SOURCE_INTEGRITY_FAILED",
                    message=f"Source integrity verification failed for '{prepared_file.input}'",
                )

            current_root_id = get_path_identity(self._input_root)
            if (
                current_root_id.device != self._input_root_id.device
                or current_root_id.inode != self._input_root_id.inode
            ):
                return BatchDiagnostic(
                    code="SOURCE_INTEGRITY_FAILED",
                    message=f"Source integrity verification failed for '{prepared_file.input}'",
                )

            assert_strictly_contained(prepared_file.source_path, self._input_root)

            # Recapture post-close snapshot
            after_snap = capture_source_snapshot(prepared_file.source_path)
            if not verify_snapshot_equality(before_snap, after_snap):
                return BatchDiagnostic(
                    code="SOURCE_INTEGRITY_FAILED",
                    message=f"Source integrity verification failed for '{prepared_file.input}'",
                )

            return None
        except Exception:
            return BatchDiagnostic(
                code="SOURCE_INTEGRITY_FAILED",
                message=f"Source integrity verification failed for '{prepared_file.input}'",
            )
        finally:
            # Retain terminal lifecycle state after clearing snapshot bytes
            file_info.state = "closed"
            file_info.before_open_snapshot = None

    # BatchOutputWorkspace protocol delegation
    def begin_format(self, context: BatchItemContext) -> None:
        """Exclusively activate the private work directory and verify target absence."""
        if self._workspace is None:
            raise BatchWorkspaceError(
                BatchDiagnostic(
                    code="INTERNAL_ERROR",
                    message="Safety boundary workspace is not initialized",
                    format=_as_batch_output_format(context.format),
                ),
                request_id=context.request_id,
            )

        file_info = self._files.get(context.input)
        if file_info is None or file_info.state != "opened":
            current_state = file_info.state if file_info is not None else "unregistered"
            raise BatchWorkspaceError(
                BatchDiagnostic(
                    code="INTERNAL_ERROR",
                    message=f"Cannot begin format export for file '{context.input}' in state '{current_state}' (must be 'opened')",
                    format=_as_batch_output_format(context.format),
                ),
                request_id=context.request_id,
            )

        self._workspace.begin_format(context)

    def finalize_format(self, context: BatchItemContext, snapshot: OutputSnapshot) -> Path:
        """Verify the validated work artifact and atomically publish it to final target."""
        if self._workspace is None:
            raise BatchWorkspaceError(
                BatchDiagnostic(
                    code="INTERNAL_ERROR",
                    message="Safety boundary workspace is not initialized",
                    format=_as_batch_output_format(context.format),
                ),
                request_id=context.request_id,
            )

        file_info = self._files.get(context.input)
        if file_info is None or file_info.state != "opened":
            current_state = file_info.state if file_info is not None else "unregistered"
            raise BatchWorkspaceError(
                BatchDiagnostic(
                    code="INTERNAL_ERROR",
                    message=f"Cannot finalize format export for file '{context.input}' in state '{current_state}' (must be 'opened')",
                    format=_as_batch_output_format(context.format),
                ),
                request_id=context.request_id,
            )

        if snapshot.identity.inode == 0:
            raise BatchWorkspaceError(
                BatchDiagnostic(
                    code="ARTIFACT_EXPORT_FAILED",
                    message="Output artifact identity unavailable (zero inode)",
                    format=_as_batch_output_format(context.format),
                ),
                request_id=context.request_id,
            )

        # Reject work artifact identity collision with any prepared source file (comparing device and inode)
        for f_info in self._files.values():
            if (
                f_info.source_id is not None
                and f_info.source_id.device == snapshot.identity.device
                and f_info.source_id.inode == snapshot.identity.inode
            ):
                raise BatchWorkspaceError(
                    BatchDiagnostic(
                        code="ARTIFACT_EXPORT_FAILED",
                        message=f"Work artifact cannot alias source file '{f_info.prepared_file.input}'",
                        format=_as_batch_output_format(context.format),
                    ),
                    request_id=context.request_id,
                )

        return self._workspace.finalize_format(context, snapshot)

    def cleanup_format(self, context: BatchItemContext) -> None:
        """Safely clean up the private work directory on failure or cancellation."""
        if self._workspace is None:
            raise BatchWorkspaceError(
                BatchDiagnostic(
                    code="INTERNAL_ERROR",
                    message="Safety boundary workspace is not initialized",
                    format=_as_batch_output_format(context.format),
                ),
                request_id=context.request_id,
            )
        self._workspace.cleanup_format(context)


__all__ = [
    "FilesystemBatchSafetyBoundary",
]
