"""Guarded output workspace staging lifecycle: candidate allocation, activation, and cleanup primitives.

Provides defense-in-depth ownership verification and fail-closed filesystem operations:
- allocate_private_work_path: non-creating candidate path allocator for prepared batch formats.
- activate_private_work_directory: exclusive directory creation with proven identity rollback.
- cleanup_private_work_directory: strict inventory-first cleanup requiring full identity,
  prefix, and direct-child verification.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Final

from batch.filesystem import (
    PathIdentity,
    get_path_identity,
    is_symlink_or_reparse_point,
)
from batch.models import BatchDiagnostic
from batch.output_publication import BatchWorkspaceError, _as_batch_output_format

WORK_DIR_PREFIX: Final[str] = ".cad-copilot-work-"


def allocate_private_work_path(
    output_root: Path,
    target_path: Path,
    *,
    max_candidates: int = 5,
    request_id: str = "unknown",
) -> Path:
    """Derive an unpredictable, currently absent private work path without filesystem creation."""
    for _ in range(max_candidates):
        token = secrets.token_hex(16)
        candidate_dir = output_root / f"{WORK_DIR_PREFIX}{token}"
        if candidate_dir.exists() or is_symlink_or_reparse_point(candidate_dir):
            continue
        candidate_file = candidate_dir / target_path.name
        if candidate_file.exists() or is_symlink_or_reparse_point(candidate_file):
            continue
        return candidate_file

    raise BatchWorkspaceError(
        BatchDiagnostic(
            code="OUTPUT_ROOT_UNAVAILABLE",
            message="Failed to allocate private work directory after maximum attempts",
        ),
        request_id=request_id,
    )


def activate_private_work_directory(
    work_dir: Path,
    work_path: Path,
    output_root: Path,
    *,
    request_id: str = "unknown",
    format_name: str | None = None,
) -> PathIdentity:
    """Exclusively activate a private work directory with guarded rollback on failure."""
    fmt = _as_batch_output_format(format_name) if format_name else None

    # Enforce direct-child containment
    try:
        is_direct_child = work_dir.parent.resolve() == output_root.resolve()
    except OSError, ValueError:
        is_direct_child = False

    if not is_direct_child:
        raise BatchWorkspaceError(
            BatchDiagnostic(
                code="INTERNAL_ERROR",
                message="Work directory must be a direct child of the output root",
                format=fmt,
            ),
            request_id=request_id,
        )

    if not work_dir.name.startswith(WORK_DIR_PREFIX):
        raise BatchWorkspaceError(
            BatchDiagnostic(
                code="INTERNAL_ERROR",
                message="Work directory must use the private prefix",
                format=fmt,
            ),
            request_id=request_id,
        )

    # Exclusively create the private work directory
    try:
        os.mkdir(work_dir)
    except FileExistsError as exc:
        raise BatchWorkspaceError(
            BatchDiagnostic(
                code="OUTPUT_ROOT_UNAVAILABLE",
                message=f"Work directory collision during activation for format '{format_name}'",
                format=fmt,
            ),
            request_id=request_id,
        ) from exc
    except (OSError, ValueError) as exc:
        raise BatchWorkspaceError(
            BatchDiagnostic(
                code="OUTPUT_ROOT_UNAVAILABLE",
                message=f"Failed to create work directory for format '{format_name}'",
                format=fmt,
            ),
            request_id=request_id,
        ) from exc

    # Immediately capture created directory identity.
    # If identity cannot be proven, deletion is unsafe; report incomplete rollback.
    try:
        initial_dir_id = get_path_identity(work_dir)
    except Exception as exc:
        raise BatchWorkspaceError(
            BatchDiagnostic(
                code="ARTIFACT_EXPORT_FAILED",
                message="Failed to capture work directory identity after creation; refusing unverified rollback",
                format=fmt,
            ),
            request_id=request_id,
        ) from exc

    if initial_dir_id.inode == 0:
        raise BatchWorkspaceError(
            BatchDiagnostic(
                code="ARTIFACT_EXPORT_FAILED",
                message="Work directory identity unavailable (zero inode) after creation; refusing unverified rollback",
                format=fmt,
            ),
            request_id=request_id,
        )

    if not initial_dir_id.is_directory or is_symlink_or_reparse_point(work_dir):
        raise BatchWorkspaceError(
            BatchDiagnostic(
                code="ARTIFACT_EXPORT_FAILED",
                message="Work directory is invalid or a reparse point after creation; refusing unverified rollback",
                format=fmt,
            ),
            request_id=request_id,
        )

    # Helper for fail-closed rollback after mkdir requiring exact proven identity
    def _rollback(reason: str, *, original_exc: Exception | None = None) -> None:
        try:
            if is_symlink_or_reparse_point(work_dir):
                raise OSError("Work directory is a reparse point; refusing unverified rollback")
            current_id = get_path_identity(work_dir)
            if current_id != initial_dir_id:
                raise OSError("Work directory identity changed before rollback")
            if not current_id.is_directory:
                raise OSError("Work directory is not a directory before rollback")
            work_dir.rmdir()
        except Exception as rb_exc:
            raise BatchWorkspaceError(
                BatchDiagnostic(
                    code="ARTIFACT_EXPORT_FAILED",
                    message=f"Activation rollback failed: {reason}",
                    format=fmt,
                ),
                request_id=request_id,
            ) from (original_exc or rb_exc)

        raise BatchWorkspaceError(
            BatchDiagnostic(
                code="OUTPUT_ROOT_UNAVAILABLE",
                message=reason,
                format=fmt,
            ),
            request_id=request_id,
        ) from original_exc

    # Verify inventory is completely empty
    try:
        inventory = list(work_dir.iterdir())
    except Exception as exc:
        _rollback("Failed to inspect work directory inventory", original_exc=exc)

    if inventory:
        _rollback(f"Work directory is not empty for format '{format_name}'")

    # Verify work file does not already exist
    if work_path.exists() or is_symlink_or_reparse_point(work_path):
        _rollback(f"Work file unexpectedly exists prior to export for format '{format_name}'")

    return initial_dir_id


def cleanup_private_work_directory(
    work_dir: Path,
    work_path: Path,
    output_root: Path,
    *,
    expected_dir_id: PathIdentity | None = None,
    request_id: str = "unknown",
    format_name: str | None = None,
) -> None:
    """Safely clean up a private work directory enforcing strict ownership and inventory guards."""
    fmt = _as_batch_output_format(format_name) if format_name else None

    # Enforce direct-child containment
    try:
        is_direct_child = work_dir.parent.resolve() == output_root.resolve()
    except OSError, ValueError:
        is_direct_child = False

    if not is_direct_child:
        raise BatchWorkspaceError(
            BatchDiagnostic(
                code="INTERNAL_ERROR",
                message="Work directory must be a direct child of the output root",
                format=fmt,
            ),
            request_id=request_id,
        )

    if not work_dir.name.startswith(WORK_DIR_PREFIX):
        raise BatchWorkspaceError(
            BatchDiagnostic(
                code="INTERNAL_ERROR",
                message="Work directory must use the private prefix",
                format=fmt,
            ),
            request_id=request_id,
        )

    is_reparse = is_symlink_or_reparse_point(work_dir)
    is_absent = (not work_dir.exists()) and (not is_reparse)

    # Distinguish absent active directories from unactivated ones
    if expected_dir_id is not None:
        if is_absent:
            raise BatchWorkspaceError(
                BatchDiagnostic(
                    code="ARTIFACT_EXPORT_FAILED",
                    message="Active work directory unexpectedly missing during cleanup",
                    format=fmt,
                ),
                request_id=request_id,
            )
    else:
        if is_absent:
            return
        # An unactivated record with an existing directory has no proven ownership
        raise BatchWorkspaceError(
            BatchDiagnostic(
                code="ARTIFACT_EXPORT_FAILED",
                message="Cannot clean up unowned directory without expected identity",
                format=fmt,
            ),
            request_id=request_id,
        )

    # Verify work directory identity before inspection
    try:
        current_id = get_path_identity(work_dir)
    except Exception as exc:
        raise BatchWorkspaceError(
            BatchDiagnostic(
                code="ARTIFACT_EXPORT_FAILED",
                message="Cannot verify work directory identity for cleanup",
                format=fmt,
            ),
            request_id=request_id,
        ) from exc

    if current_id.inode == 0:
        raise BatchWorkspaceError(
            BatchDiagnostic(
                code="ARTIFACT_EXPORT_FAILED",
                message="Work directory identity unavailable (zero inode) during cleanup",
                format=fmt,
            ),
            request_id=request_id,
        )

    if not current_id.is_directory or is_reparse:
        raise BatchWorkspaceError(
            BatchDiagnostic(
                code="ARTIFACT_EXPORT_FAILED",
                message="Work directory is not an ordinary directory for cleanup",
                format=fmt,
            ),
            request_id=request_id,
        )

    # Complete PathIdentity equality check (device, inode, mode)
    if expected_dir_id is not None and current_id != expected_dir_id:
        raise BatchWorkspaceError(
            BatchDiagnostic(
                code="ARTIFACT_EXPORT_FAILED",
                message="Work directory identity changed before cleanup",
                format=fmt,
            ),
            request_id=request_id,
        )

    # Validate inventory FIRST before unlinking any files
    try:
        inventory = list(work_dir.iterdir())
    except Exception as exc:
        raise BatchWorkspaceError(
            BatchDiagnostic(
                code="ARTIFACT_EXPORT_FAILED",
                message="Failed to inspect work directory inventory during cleanup",
                format=fmt,
            ),
            request_id=request_id,
        ) from exc

    if len(inventory) > 1 or (len(inventory) == 1 and inventory[0] != work_path):
        raise BatchWorkspaceError(
            BatchDiagnostic(
                code="ARTIFACT_EXPORT_FAILED",
                message="Unexpected files found in work directory during cleanup",
                format=fmt,
            ),
            request_id=request_id,
        )

    # If expected work file is present in validated inventory, check regularity and unlink
    if inventory:
        try:
            file_id = get_path_identity(work_path)
        except Exception as exc:
            raise BatchWorkspaceError(
                BatchDiagnostic(
                    code="ARTIFACT_EXPORT_FAILED",
                    message="Failed to verify work file identity during cleanup",
                    format=fmt,
                ),
                request_id=request_id,
            ) from exc

        if file_id.inode == 0:
            raise BatchWorkspaceError(
                BatchDiagnostic(
                    code="ARTIFACT_EXPORT_FAILED",
                    message="Work file identity unavailable (zero inode) during cleanup",
                    format=fmt,
                ),
                request_id=request_id,
            )

        if not file_id.is_regular_file or is_symlink_or_reparse_point(work_path):
            raise BatchWorkspaceError(
                BatchDiagnostic(
                    code="ARTIFACT_EXPORT_FAILED",
                    message="Work file is not an ordinary regular file",
                    format=fmt,
                ),
                request_id=request_id,
            )

        try:
            work_path.unlink()
        except OSError as exc:
            raise BatchWorkspaceError(
                BatchDiagnostic(
                    code="ARTIFACT_EXPORT_FAILED",
                    message="Failed to unlink work file during cleanup",
                    format=fmt,
                ),
                request_id=request_id,
            ) from exc

    # Remove empty work directory
    try:
        work_dir.rmdir()
    except OSError as exc:
        raise BatchWorkspaceError(
            BatchDiagnostic(
                code="ARTIFACT_EXPORT_FAILED",
                message="Failed to remove empty work directory during cleanup",
                format=fmt,
            ),
            request_id=request_id,
        ) from exc


__all__ = [
    "WORK_DIR_PREFIX",
    "activate_private_work_directory",
    "allocate_private_work_path",
    "cleanup_private_work_directory",
]
