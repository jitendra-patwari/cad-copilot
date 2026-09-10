"""Preparation, root derivation, and input preflight validation for FilesystemBatchSafetyBoundary.

Handles:
- Deriving roots strictly from BatchExecutionSpec.
- Normalizing root identity-capture failures into typed BatchSafetyRejectionError diagnostics.
- Rejecting root and source identities with zero inode (unavailable unique identity).
- Enforcing Windows local drive containment, path traversal checks, and reparse point rejections.
- Detecting cross-input alias collisions across distinct relative input paths.
- Staging output formats, validating containment and target absence, and allocating private work paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from batch.allocation import (
    AllocatedBatchWork,
    BatchSafetyRejectionError,
    PreparedBatchFile,
    PreparedBatchFormat,
    PreparedBatchWork,
    validate_prepared_work_consistency,
)
from batch.execution import BatchExecutionSpec, BatchItemContext
from batch.filesystem import (
    PathIdentity,
    assert_strictly_contained,
    get_path_identity,
    is_symlink_or_reparse_point,
    validate_input_root,
    validate_output_root,
    validate_relative_segment,
    walk_and_verify_components,
)
from batch.models import BatchDiagnostic
from batch.output_lifecycle import allocate_private_work_path
from batch.output_publication import _as_batch_output_format
from batch.output_workspace import FilesystemOutputWorkspace
from batch.source_integrity import SourceSnapshot

FileSafetyState = Literal["prepared", "preflight_failed", "opened", "closed"]


@dataclass
class _PreparedFileInfo:
    """Private record for one prepared file's identity and verification snapshot."""

    prepared_file: PreparedBatchFile
    source_path: Path
    source_id: PathIdentity | None
    state: FileSafetyState = "prepared"
    before_open_snapshot: SourceSnapshot | None = None


@dataclass(frozen=True)
class SafetyPreparationResult:
    """Immutable result of safety boundary preparation."""

    prepared_work: PreparedBatchWork
    input_root: Path
    input_root_id: PathIdentity
    output_root: Path
    output_root_id: PathIdentity
    workspace: FilesystemOutputWorkspace
    files: dict[str, _PreparedFileInfo]


def prepare_safety_boundary(
    spec: BatchExecutionSpec,
    work: AllocatedBatchWork,
) -> SafetyPreparationResult:
    """Validate roots and inputs, stage per-format work directories, and build prepared work."""
    # Roots are strictly derived from the validated BatchExecutionSpec
    resolved_input_root = validate_input_root(spec.input_root, request_id=spec.request_id)
    resolved_output_root = validate_output_root(spec.output_root, request_id=spec.request_id)

    try:
        input_root_id = get_path_identity(resolved_input_root)
    except Exception as exc:
        raise BatchSafetyRejectionError(
            BatchDiagnostic(
                code="INPUT_PATH_NOT_ALLOWED",
                message="Failed to capture input root filesystem identity",
            ),
            request_id=spec.request_id,
        ) from exc

    if not input_root_id.is_directory:
        raise BatchSafetyRejectionError(
            BatchDiagnostic(
                code="INPUT_PATH_NOT_ALLOWED",
                message="Input root is not a directory",
            ),
            request_id=spec.request_id,
        )

    if input_root_id.inode == 0:
        raise BatchSafetyRejectionError(
            BatchDiagnostic(
                code="INPUT_PATH_NOT_ALLOWED",
                message="Input root filesystem does not support unique file identification (zero inode)",
            ),
            request_id=spec.request_id,
        )

    try:
        output_root_id = get_path_identity(resolved_output_root)
    except Exception as exc:
        raise BatchSafetyRejectionError(
            BatchDiagnostic(
                code="OUTPUT_ROOT_UNAVAILABLE",
                message="Failed to capture output root filesystem identity",
            ),
            request_id=spec.request_id,
        ) from exc

    if not output_root_id.is_directory:
        raise BatchSafetyRejectionError(
            BatchDiagnostic(
                code="OUTPUT_ROOT_UNAVAILABLE",
                message="Output root is not a directory",
            ),
            request_id=spec.request_id,
        )

    if output_root_id.inode == 0:
        raise BatchSafetyRejectionError(
            BatchDiagnostic(
                code="OUTPUT_ROOT_UNAVAILABLE",
                message="Output root filesystem does not support unique file identification (zero inode)",
            ),
            request_id=spec.request_id,
        )

    workspace = FilesystemOutputWorkspace(resolved_output_root, output_root_id)

    seen_source_identities: dict[tuple[int, int], str] = {}
    seen_source_paths: dict[Path, str] = {}
    prep_files: list[PreparedBatchFile] = []
    files_map: dict[str, _PreparedFileInfo] = {}

    for alloc_file in work.files:
        file_preflight_error: BatchDiagnostic | None = None
        source_id: PathIdentity | None = None

        # Validate component syntax, reparse tags, and containment
        try:
            source_path = walk_and_verify_components(resolved_input_root, alloc_file.input, request_id=spec.request_id)
        except BatchSafetyRejectionError as exc:
            file_preflight_error = exc.diagnostic
            source_path = resolved_input_root / PurePosixPath(alloc_file.input)

        if file_preflight_error is None:
            # Check source file existence and reparse point status
            if not source_path.exists() and not is_symlink_or_reparse_point(source_path):
                file_preflight_error = BatchDiagnostic(
                    code="INPUT_FILE_NOT_FOUND",
                    message=f"Input file not found: '{alloc_file.input}'",
                )
            elif is_symlink_or_reparse_point(source_path):
                file_preflight_error = BatchDiagnostic(
                    code="INPUT_PATH_NOT_ALLOWED",
                    message=f"Input file is a symbolic link or reparse point: '{alloc_file.input}'",
                )
            else:
                try:
                    source_id = get_path_identity(source_path)
                except Exception:
                    file_preflight_error = BatchDiagnostic(
                        code="INPUT_PATH_NOT_ALLOWED",
                        message=f"Cannot determine input file identity: '{alloc_file.input}'",
                    )

                if file_preflight_error is None and source_id is not None:
                    if not source_id.is_regular_file:
                        file_preflight_error = BatchDiagnostic(
                            code="INPUT_PATH_NOT_ALLOWED",
                            message=f"Input path is not a regular file: '{alloc_file.input}'",
                        )
                    else:
                        try:
                            if source_path.lstat().st_size == 0:
                                file_preflight_error = BatchDiagnostic(
                                    code="INPUT_PATH_NOT_ALLOWED",
                                    message=f"Input file cannot be zero bytes: '{alloc_file.input}'",
                                )
                        except Exception:
                            file_preflight_error = BatchDiagnostic(
                                code="INPUT_PATH_NOT_ALLOWED",
                                message=f"Cannot determine input file size: '{alloc_file.input}'",
                            )

                # Alias detection across distinct relative inputs: reject request before runtime
                if file_preflight_error is None and source_id is not None:
                    if source_id.inode == 0:
                        file_preflight_error = BatchDiagnostic(
                            code="INPUT_PATH_NOT_ALLOWED",
                            message=f"Input file identity unavailable (zero inode): '{alloc_file.input}'",
                        )
                    else:
                        try:
                            resolved_source = source_path.resolve()
                        except OSError:
                            resolved_source = source_path

                        if resolved_source in seen_source_paths:
                            raise BatchSafetyRejectionError(
                                BatchDiagnostic(
                                    code="INPUT_PATH_NOT_ALLOWED",
                                    message="Duplicate source file identity detected in request",
                                ),
                                request_id=spec.request_id,
                            )
                        seen_source_paths[resolved_source] = alloc_file.input

                        dev_ino = (source_id.device, source_id.inode)
                        if dev_ino in seen_source_identities:
                            raise BatchSafetyRejectionError(
                                BatchDiagnostic(
                                    code="INPUT_PATH_NOT_ALLOWED",
                                    message="Duplicate source file identity detected in request",
                                ),
                                request_id=spec.request_id,
                            )
                        seen_source_identities[dev_ino] = alloc_file.input

        # Prepare per-format outputs
        prep_formats: list[PreparedBatchFormat] = []
        for alloc_fmt in alloc_file.formats:
            fmt_preflight_error: BatchDiagnostic | None = None
            target_path = resolved_output_root / PurePosixPath(alloc_fmt.target_relative_path)

            try:
                assert_strictly_contained(target_path, resolved_output_root, request_id=spec.request_id)
            except BatchSafetyRejectionError as exc:
                fmt_preflight_error = BatchDiagnostic(
                    code="OUTPUT_ROOT_UNAVAILABLE",
                    message=exc.message,
                    format=_as_batch_output_format(alloc_fmt.format),
                )

            if fmt_preflight_error is None:
                # Validate relative segment syntax for all target path components
                rel_parts = PurePosixPath(alloc_fmt.target_relative_path).parts
                for seg in rel_parts:
                    try:
                        validate_relative_segment(seg, request_id=spec.request_id)
                    except BatchSafetyRejectionError:
                        fmt_preflight_error = BatchDiagnostic(
                            code="OUTPUT_ROOT_UNAVAILABLE",
                            message="Target directory component contains invalid characters",
                            format=_as_batch_output_format(alloc_fmt.format),
                        )
                        break

            if fmt_preflight_error is None:
                # Inspect existing parent directories beneath output root
                parent_check = resolved_output_root
                for seg in rel_parts[:-1]:
                    parent_check = parent_check / seg
                    if parent_check.exists() and (
                        is_symlink_or_reparse_point(parent_check) or not parent_check.is_dir()
                    ):
                        fmt_preflight_error = BatchDiagnostic(
                            code="OUTPUT_ROOT_UNAVAILABLE",
                            message=f"Parent component '{seg}' is not a safe directory",
                            format=_as_batch_output_format(alloc_fmt.format),
                        )
                        break

            if fmt_preflight_error is None and (target_path.exists() or is_symlink_or_reparse_point(target_path)):
                fmt_preflight_error = BatchDiagnostic(
                    code="TARGET_ALREADY_EXISTS",
                    message=f"Target file already exists for format '{alloc_fmt.format}'",
                    format=_as_batch_output_format(alloc_fmt.format),
                )

            # Allocate unpredictable, currently absent private work path
            work_path = allocate_private_work_path(
                resolved_output_root,
                target_path,
                request_id=spec.request_id,
            )

            # Register context with the internal output workspace
            context = BatchItemContext(
                request_id=spec.request_id,
                operation_id=spec.operation_id,
                input=alloc_file.input,
                source_path=source_path,
                format=alloc_fmt.format,
                target_relative_path=alloc_fmt.target_relative_path,
                work_path=work_path,
                target_path=target_path,
            )
            workspace.register_context(context)

            prep_formats.append(
                PreparedBatchFormat(
                    format=alloc_fmt.format,
                    target_relative_path=alloc_fmt.target_relative_path,
                    work_path=work_path,
                    target_path=target_path,
                    preflight_error=fmt_preflight_error,
                )
            )

        prepared_file = PreparedBatchFile(
            input=alloc_file.input,
            source_path=source_path,
            formats=tuple(prep_formats),
            preflight_error=file_preflight_error,
        )
        prep_files.append(prepared_file)

        file_state: FileSafetyState = "preflight_failed" if file_preflight_error is not None else "prepared"
        files_map[alloc_file.input] = _PreparedFileInfo(
            prepared_file=prepared_file,
            source_path=source_path,
            source_id=source_id,
            state=file_state,
        )

    prepared_work = PreparedBatchWork(
        files=tuple(prep_files),
        output_root=resolved_output_root,
    )

    validate_prepared_work_consistency(work, prepared_work)

    return SafetyPreparationResult(
        prepared_work=prepared_work,
        input_root=resolved_input_root,
        input_root_id=input_root_id,
        output_root=resolved_output_root,
        output_root_id=output_root_id,
        workspace=workspace,
        files=files_map,
    )


__all__ = [
    "FileSafetyState",
    "SafetyPreparationResult",
    "_PreparedFileInfo",
    "prepare_safety_boundary",
]
