"""Pure work allocation, target path derivation, and M5.3 safety boundary seam.

This module is strictly free of filesystem access, COM objects, worker threads, and
network access. It maps canonical input paths to deterministic relative output targets,
detects case-insensitive collisions, and defines the injected M5.3 safety boundary protocol.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

from batch.execution import BatchExecutionOutcome, BatchExecutionSpec
from batch.models import (
    APPROVED_WARNING_CODES,
    BatchContractError,
    BatchDiagnostic,
    BatchValidationError,
)

# Approved static format-to-suffix mapping for CAD Copilot batch formats
APPROVED_FORMAT_SUFFIXES: Mapping[str, str] = {
    "step": ".step",
    "stl": ".stl",
    "pdf": ".pdf",
    "dxf": ".dxf",
}


class BatchAllocationCollisionError(BatchContractError):
    """Raised when in-request output target allocation produces duplicate relative targets."""

    def __init__(self, target_relative_path: str, *, request_id: str = "unknown") -> None:
        message = f"Output target collision detected for relative target '{target_relative_path}'"
        super().__init__("OUTPUT_TARGET_COLLISION", message, request_id=request_id)
        self.target_relative_path = target_relative_path


class BatchSafetyRejectionError(BatchContractError):
    """Raised by a safety boundary when request-wide or root validation fails preflight."""

    def __init__(self, diagnostic: BatchDiagnostic, *, request_id: str = "unknown") -> None:
        super().__init__(diagnostic.code, diagnostic.message, request_id=request_id)
        self.diagnostic = diagnostic


@dataclass(frozen=True)
class AllocatedBatchFormat:
    """Pure relative output target for a single requested format."""

    format: str
    target_relative_path: str

    def __post_init__(self) -> None:
        if not isinstance(self.format, str) or not self.format:
            raise ValueError("format must be a non-empty string")
        if not isinstance(self.target_relative_path, str) or not self.target_relative_path:
            raise ValueError("target_relative_path must be a non-empty string")
        from batch.paths import validate_and_canonicalize_input_path

        try:
            canonical = validate_and_canonicalize_input_path(self.target_relative_path)
        except BatchValidationError as err:
            raise ValueError(f"target_relative_path must be a canonical posix relative path: {err}") from err
        if self.target_relative_path != canonical:
            raise ValueError(
                f"target_relative_path must be posix-style canonical path, got {self.target_relative_path!r}"
            )


@dataclass(frozen=True)
class AllocatedBatchFile:
    """Pure relative work specification for a single input file."""

    input: str
    formats: tuple[AllocatedBatchFormat, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.input, str) or not self.input:
            raise ValueError("input must be a non-empty string")
        object.__setattr__(self, "formats", tuple(self.formats))
        if not self.formats:
            raise ValueError("formats must be a non-empty sequence")
        for fmt in self.formats:
            if not isinstance(fmt, AllocatedBatchFormat):
                raise TypeError(f"formats items must be AllocatedBatchFormat, got {type(fmt).__name__}")


@dataclass(frozen=True)
class AllocatedBatchWork:
    """Pure relative work specification for an entire batch execution request."""

    files: tuple[AllocatedBatchFile, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "files", tuple(self.files))
        if not self.files:
            raise ValueError("files must be a non-empty sequence")
        for f in self.files:
            if not isinstance(f, AllocatedBatchFile):
                raise TypeError(f"files items must be AllocatedBatchFile, got {type(f).__name__}")


def allocate_batch_work(spec: BatchExecutionSpec) -> AllocatedBatchWork:
    """Derive deterministic relative output targets and detect in-request collisions.

    Preserves caller file order and requested format order. Performs zero filesystem I/O.
    """
    seen_targets: dict[str, tuple[str, str]] = {}
    allocated_files: list[AllocatedBatchFile] = []

    for inp in spec.inputs:
        file_formats: list[AllocatedBatchFormat] = []
        posix_input = PurePosixPath(inp)

        for fmt in spec.formats:
            suffix = APPROVED_FORMAT_SUFFIXES.get(fmt)
            if suffix is None:
                raise ValueError(f"Unsupported format '{fmt}' for target suffix allocation")

            target_rel = posix_input.with_suffix(suffix).as_posix()
            collision_key = target_rel.casefold()

            if collision_key in seen_targets:
                raise BatchAllocationCollisionError(target_rel, request_id=spec.request_id)

            seen_targets[collision_key] = (inp, fmt)
            file_formats.append(AllocatedBatchFormat(format=fmt, target_relative_path=target_rel))

        allocated_files.append(AllocatedBatchFile(input=inp, formats=tuple(file_formats)))

    return AllocatedBatchWork(files=tuple(allocated_files))


def build_collision_rejection(
    spec: BatchExecutionSpec,
    collision_error: BatchAllocationCollisionError,
) -> BatchExecutionOutcome:
    """Construct a sanitized rejected BatchExecutionOutcome from an allocation collision error."""
    diagnostic = BatchDiagnostic(
        code="OUTPUT_TARGET_COLLISION",
        message=collision_error.message,
    )
    return BatchExecutionOutcome(
        status="rejected",
        request_id=spec.request_id,
        contract_version=spec.contract_version,
        errors=(diagnostic,),
    )


@dataclass(frozen=True)
class PreparedBatchFormat:
    """M5.3-prepared output target paths and optional per-format preflight error."""

    format: str
    target_relative_path: str
    work_path: Path
    target_path: Path
    preflight_error: BatchDiagnostic | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.format, str) or not self.format:
            raise ValueError("format must be a non-empty string")
        if not isinstance(self.target_relative_path, str) or not self.target_relative_path:
            raise ValueError("target_relative_path must be a non-empty string")
        from batch.paths import validate_and_canonicalize_input_path

        try:
            canonical = validate_and_canonicalize_input_path(self.target_relative_path)
        except BatchValidationError as err:
            raise ValueError(f"target_relative_path must be a canonical posix relative path: {err}") from err
        if self.target_relative_path != canonical:
            raise ValueError(
                f"target_relative_path must be posix-style canonical path, got {self.target_relative_path!r}"
            )

        if not isinstance(self.work_path, Path) or not self.work_path.is_absolute():
            raise ValueError(f"work_path must be an absolute Path, got {self.work_path!r}")
        if not isinstance(self.target_path, Path) or not self.target_path.is_absolute():
            raise ValueError(f"target_path must be an absolute Path, got {self.target_path!r}")
        if self.preflight_error is not None:
            if not isinstance(self.preflight_error, BatchDiagnostic):
                raise TypeError(
                    f"preflight_error must be BatchDiagnostic or None, got {type(self.preflight_error).__name__}"
                )
            if self.preflight_error.code in APPROVED_WARNING_CODES:
                raise ValueError(f"preflight_error must not be a warning diagnostic, got '{self.preflight_error.code}'")


@dataclass(frozen=True)
class PreparedBatchFile:
    """M5.3-prepared input file paths and optional per-file preflight error."""

    input: str
    source_path: Path
    formats: tuple[PreparedBatchFormat, ...]
    preflight_error: BatchDiagnostic | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.input, str) or not self.input:
            raise ValueError("input must be a non-empty string")
        if not isinstance(self.source_path, Path) or not self.source_path.is_absolute():
            raise ValueError(f"source_path must be an absolute Path, got {self.source_path!r}")
        object.__setattr__(self, "formats", tuple(self.formats))
        if not self.formats:
            raise ValueError("formats must be a non-empty sequence")
        for fmt in self.formats:
            if not isinstance(fmt, PreparedBatchFormat):
                raise TypeError(f"formats items must be PreparedBatchFormat, got {type(fmt).__name__}")
        if self.preflight_error is not None:
            if not isinstance(self.preflight_error, BatchDiagnostic):
                raise TypeError(
                    f"preflight_error must be BatchDiagnostic or None, got {type(self.preflight_error).__name__}"
                )
            if self.preflight_error.code in APPROVED_WARNING_CODES:
                raise ValueError(f"preflight_error must not be a warning diagnostic, got '{self.preflight_error.code}'")


@dataclass(frozen=True)
class PreparedBatchWork:
    """M5.3-prepared batch work containing boundary-prepared paths for execution."""

    files: tuple[PreparedBatchFile, ...]
    output_root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "files", tuple(self.files))
        if not self.files:
            raise ValueError("files must be a non-empty sequence")
        for f in self.files:
            if not isinstance(f, PreparedBatchFile):
                raise TypeError(f"files items must be PreparedBatchFile, got {type(f).__name__}")
        if not isinstance(self.output_root, Path) or not self.output_root.is_absolute():
            raise ValueError(f"output_root must be an absolute Path, got {self.output_root!r}")


def validate_prepared_work_consistency(
    allocated: AllocatedBatchWork,
    prepared: PreparedBatchWork,
) -> None:
    """Validate that boundary-prepared work exactly preserves the allocated request identity.

    Enforces exact sequence parity:
    - Same file count and order
    - Same input path string for each file
    - Same format count and order for each file
    - Same format identifier for each format
    - Same target_relative_path for each format

    Raises ValueError if any discrepancy or reordering is detected.
    """
    if not isinstance(allocated, AllocatedBatchWork):
        raise TypeError(f"allocated must be AllocatedBatchWork, got {type(allocated).__name__}")
    if not isinstance(prepared, PreparedBatchWork):
        raise TypeError(f"prepared must be PreparedBatchWork, got {type(prepared).__name__}")

    if len(allocated.files) != len(prepared.files):
        raise ValueError(
            f"Prepared work file count ({len(prepared.files)}) does not match "
            f"allocated file count ({len(allocated.files)})"
        )

    for i, (alloc_file, prep_file) in enumerate(zip(allocated.files, prepared.files, strict=True)):
        if alloc_file.input != prep_file.input:
            raise ValueError(
                f"Prepared work file[{i}] input '{prep_file.input}' does not match allocated input '{alloc_file.input}'"
            )

        if prep_file.preflight_error is not None and (
            not isinstance(prep_file.preflight_error, BatchDiagnostic)
            or prep_file.preflight_error.code in APPROVED_WARNING_CODES
        ):
            raise ValueError(f"Prepared work file[{i}] ('{prep_file.input}') contains invalid preflight error")

        if len(alloc_file.formats) != len(prep_file.formats):
            raise ValueError(
                f"Prepared work file[{i}] ('{alloc_file.input}') format count ({len(prep_file.formats)}) "
                f"does not match allocated format count ({len(alloc_file.formats)})"
            )

        for j, (alloc_fmt, prep_fmt) in enumerate(zip(alloc_file.formats, prep_file.formats, strict=True)):
            if alloc_fmt.format != prep_fmt.format:
                raise ValueError(
                    f"Prepared work file[{i}] ('{alloc_file.input}') format[{j}] '{prep_fmt.format}' "
                    f"does not match allocated format '{alloc_fmt.format}'"
                )
            if alloc_fmt.target_relative_path != prep_fmt.target_relative_path:
                raise ValueError(
                    f"Prepared work file[{i}] ('{alloc_file.input}') format[{j}] target_relative_path "
                    f"'{prep_fmt.target_relative_path}' does not match allocated '{alloc_fmt.target_relative_path}'"
                )
            if prep_fmt.preflight_error is not None and (
                not isinstance(prep_fmt.preflight_error, BatchDiagnostic)
                or prep_fmt.preflight_error.code in APPROVED_WARNING_CODES
            ):
                raise ValueError(
                    f"Prepared work file[{i}] ('{prep_file.input}') format[{j}] contains invalid preflight error"
                )


class BatchSafetyBoundary(Protocol):
    """Protocol defining the filesystem security and source-integrity boundary.

    Implemented in production by FilesystemBatchSafetyBoundary, and by deterministic
    test fakes in offline unit test suites.
    """

    def prepare(self, spec: BatchExecutionSpec, work: AllocatedBatchWork) -> PreparedBatchWork:
        """Boundary-prepare filesystem paths and prepare absolute work/target paths.

        Raises BatchSafetyRejectionError on request/root-wide validation failure,
        or attaches preflight_error diagnostics to individual files or formats.
        """
        ...

    def verify_before_open(self, prepared_file: PreparedBatchFile) -> BatchDiagnostic | None:
        """Verify source file integrity and accessibility immediately prior to opening."""
        ...

    def verify_after_close(self, prepared_file: PreparedBatchFile) -> BatchDiagnostic | None:
        """Verify source file was not modified or damaged by the CAD runtime during processing."""
        ...
