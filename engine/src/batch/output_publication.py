"""Atomic Windows publication helper and diagnostic normalization.

Guarantees atomic, no-replace publication of generated batch artifacts:
- publish_output_file: Windows-only same-volume atomic rename with no-replace semantics.
- ensure_safe_parent_directory: containment and symlink/reparse validation of parent directories.
- Normalizes FileExistsError, Windows error 80, and Windows error 183 to TARGET_ALREADY_EXISTS.
- Normalizes general publication failures to ARTIFACT_EXPORT_FAILED.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from batch.allocation import BatchSafetyRejectionError
from batch.execution import BatchItemContext
from batch.filesystem import (
    assert_strictly_contained,
    is_symlink_or_reparse_point,
    validate_relative_segment,
)
from batch.models import BatchContractError, BatchDiagnostic, BatchOutputFormat
from batch.output_snapshot import (
    OutputSnapshot,
    capture_output_snapshot,
    verify_output_snapshot_equality,
)


def _as_batch_output_format(fmt: str) -> BatchOutputFormat | None:
    """Safely cast string to approved BatchOutputFormat literal if supported."""
    if fmt in ("step", "stl", "parasolid", "pdf", "dxf"):
        return fmt  # type: ignore[return-value]
    return None


class BatchWorkspaceError(BatchContractError):
    """Raised when an output workspace operation fails, carrying a sanitized diagnostic."""

    def __init__(self, diagnostic: BatchDiagnostic, *, request_id: str = "unknown") -> None:
        super().__init__(diagnostic.code, diagnostic.message, request_id=request_id)
        self.diagnostic = diagnostic


def ensure_safe_parent_directory(
    target_parent: Path,
    output_root: Path,
    *,
    request_id: str = "unknown",
) -> None:
    """Ensure the target parent directory exists and contains no symlinks or reparse points."""
    try:
        rel = target_parent.relative_to(output_root)
    except ValueError as exc:
        raise BatchWorkspaceError(
            BatchDiagnostic(
                code="OUTPUT_ROOT_UNAVAILABLE",
                message="Target parent directory escapes output root",
            ),
            request_id=request_id,
        ) from exc

    if rel.parts == ():
        return

    try:
        assert_strictly_contained(target_parent, output_root, request_id=request_id)
    except BatchSafetyRejectionError as exc:
        raise BatchWorkspaceError(
            BatchDiagnostic(
                code="OUTPUT_ROOT_UNAVAILABLE",
                message=exc.message,
            ),
            request_id=request_id,
        ) from exc

    current = output_root
    for seg in rel.parts:
        try:
            validate_relative_segment(seg, request_id=request_id)
        except (BatchSafetyRejectionError, ValueError) as exc:
            raise BatchWorkspaceError(
                BatchDiagnostic(
                    code="OUTPUT_ROOT_UNAVAILABLE",
                    message="Target directory component contains invalid characters",
                ),
                request_id=request_id,
            ) from exc

        current = current / seg
        if current.exists():
            if is_symlink_or_reparse_point(current) or not current.is_dir():
                raise BatchWorkspaceError(
                    BatchDiagnostic(
                        code="OUTPUT_ROOT_UNAVAILABLE",
                        message=f"Parent component '{seg}' is not a safe directory",
                    ),
                    request_id=request_id,
                )
        else:
            try:
                os.mkdir(current)
            except FileExistsError as exc:
                if is_symlink_or_reparse_point(current) or not current.is_dir():
                    raise BatchWorkspaceError(
                        BatchDiagnostic(
                            code="OUTPUT_ROOT_UNAVAILABLE",
                            message=f"Parent component '{seg}' is not a safe directory",
                        ),
                        request_id=request_id,
                    ) from exc
            except (OSError, ValueError) as exc:
                raise BatchWorkspaceError(
                    BatchDiagnostic(
                        code="OUTPUT_ROOT_UNAVAILABLE",
                        message="Failed to create parent directory component",
                    ),
                    request_id=request_id,
                ) from exc

            # Re-verify newly created directory component
            if is_symlink_or_reparse_point(current) or not current.is_dir():
                raise BatchWorkspaceError(
                    BatchDiagnostic(
                        code="OUTPUT_ROOT_UNAVAILABLE",
                        message=f"Newly created parent component '{seg}' is not a safe directory",
                    ),
                    request_id=request_id,
                )


def publish_output_file(
    context: BatchItemContext,
    snapshot: OutputSnapshot,
    output_root: Path,
) -> Path:
    """Atomically publish a verified work file to its final destination using Windows semantics."""
    # Ensure Windows platform for production atomic publication
    if sys.platform != "win32":
        raise BatchWorkspaceError(
            BatchDiagnostic(
                code="OUTPUT_ROOT_UNAVAILABLE",
                message="Atomic publication requires Windows filesystem semantics",
                format=_as_batch_output_format(context.format),
            ),
            request_id=context.request_id,
        )

    # Re-verify containment
    try:
        assert_strictly_contained(context.target_path, output_root, request_id=context.request_id)
        assert_strictly_contained(context.work_path, output_root, request_id=context.request_id)
    except BatchSafetyRejectionError as exc:
        raise BatchWorkspaceError(
            BatchDiagnostic(
                code="OUTPUT_ROOT_UNAVAILABLE",
                message=exc.message,
                format=_as_batch_output_format(context.format),
            ),
            request_id=context.request_id,
        ) from exc

    # Create/validate mirrored final parent beneath output root
    ensure_safe_parent_directory(context.target_path.parent, output_root, request_id=context.request_id)

    # Recheck final target is absent
    if context.target_path.exists() or is_symlink_or_reparse_point(context.target_path):
        raise BatchWorkspaceError(
            BatchDiagnostic(
                code="TARGET_ALREADY_EXISTS",
                message=f"Target file already exists for format '{context.format}'",
                format=_as_batch_output_format(context.format),
            ),
            request_id=context.request_id,
        )

    # Rename work file to final target using Windows no-replace semantics
    try:
        os.rename(str(context.work_path), str(context.target_path))
    except FileExistsError as exc:
        raise BatchWorkspaceError(
            BatchDiagnostic(
                code="TARGET_ALREADY_EXISTS",
                message=f"Target file already exists for format '{context.format}'",
                format=_as_batch_output_format(context.format),
            ),
            request_id=context.request_id,
        ) from exc
    except OSError as exc:
        winerror = getattr(exc, "winerror", None)
        if winerror in (80, 183):
            raise BatchWorkspaceError(
                BatchDiagnostic(
                    code="TARGET_ALREADY_EXISTS",
                    message=f"Target file already exists for format '{context.format}'",
                    format=_as_batch_output_format(context.format),
                ),
                request_id=context.request_id,
            ) from exc
        raise BatchWorkspaceError(
            BatchDiagnostic(
                code="ARTIFACT_EXPORT_FAILED",
                message="Failed to publish artifact to final target destination",
                format=_as_batch_output_format(context.format),
            ),
            request_id=context.request_id,
        ) from exc

    # Re-verify published target identity and exact content equality
    try:
        published_snap = capture_output_snapshot(context.target_path)
    except Exception as exc:
        raise BatchWorkspaceError(
            BatchDiagnostic(
                code="ARTIFACT_EXPORT_FAILED",
                message="Failed to verify published target snapshot",
                format=_as_batch_output_format(context.format),
            ),
            request_id=context.request_id,
        ) from exc

    if not verify_output_snapshot_equality(snapshot, published_snap):
        raise BatchWorkspaceError(
            BatchDiagnostic(
                code="ARTIFACT_EXPORT_FAILED",
                message="Published artifact identity or content mismatch",
                format=_as_batch_output_format(context.format),
            ),
            request_id=context.request_id,
        )

    # Remove the now-empty private work directory without swallowing failures
    try:
        context.work_path.parent.rmdir()
    except OSError as exc:
        raise BatchWorkspaceError(
            BatchDiagnostic(
                code="ARTIFACT_EXPORT_FAILED",
                message="Failed to remove private work directory after publication",
                format=_as_batch_output_format(context.format),
            ),
            request_id=context.request_id,
        ) from exc

    return context.target_path
