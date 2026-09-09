"""Execution value objects, context, outcomes, and progress updates for batch execution.

These immutable domain objects decouple the central execution loop from wire schemas
and vendor CAD runtimes. They contain no raw COM pointers, workers, transport state,
or mutable collections.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, get_args

from batch.models import (
    APPROVED_WARNING_CODES,
    BatchArtifactRecord,
    BatchDiagnostic,
    BatchFileResult,
    BatchRequest,
    BatchSummary,
    BatchTerminalStatus,
    BatchValidationError,
    FileResultStatus,
)

BatchProgressPhase = Literal[
    "batch_started",
    "file_started",
    "format_started",
    "format_finished",
    "file_finished",
    "batch_finished",
]

APPROVED_PROGRESS_PHASES: frozenset[BatchProgressPhase] = frozenset(get_args(BatchProgressPhase))


@dataclass(frozen=True)
class BatchExecutionSpec:
    """Immutable internal specification of batch work to execute.

    Decouples execution from the public wire BatchRequest schema and permits
    registry-validated operation extensions without changing the hub.
    """

    contract_version: str
    request_id: str
    input_root: str
    output_root: str
    inputs: tuple[str, ...]
    operation_id: str
    formats: tuple[str, ...]
    continue_on_error: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.contract_version, str) or not (1 <= len(self.contract_version) <= 64):
            raise ValueError("contract_version must be a string of length 1..64")
        if not isinstance(self.request_id, str) or not (1 <= len(self.request_id) <= 128):
            raise ValueError("request_id must be a string of length 1..128")
        if not isinstance(self.input_root, str) or not (1 <= len(self.input_root) <= 1024):
            raise ValueError("input_root must be a string of length 1..1024")
        if not isinstance(self.output_root, str) or not (1 <= len(self.output_root) <= 1024):
            raise ValueError("output_root must be a string of length 1..1024")

        if not isinstance(self.inputs, (tuple, list)):
            raise TypeError(f"inputs must be a sequence, got {type(self.inputs).__name__}")
        if not (1 <= len(self.inputs) <= 500):
            raise ValueError("inputs must contain between 1 and 500 items")

        from batch.paths import validate_and_canonicalize_input_path

        canonical_inputs: list[str] = []
        seen_casefold: set[str] = set()
        for inp in self.inputs:
            canonical = validate_and_canonicalize_input_path(inp, request_id=self.request_id)
            key = canonical.lower()
            if key in seen_casefold:
                raise BatchValidationError(
                    "Duplicate or case-colliding input file path detected in execution spec",
                    code="INPUT_PATH_NOT_ALLOWED",
                    request_id=self.request_id,
                )
            seen_casefold.add(key)
            canonical_inputs.append(canonical)

        object.__setattr__(self, "inputs", tuple(canonical_inputs))

        if not isinstance(self.operation_id, str) or not (1 <= len(self.operation_id) <= 64):
            raise ValueError("operation_id must be a string of length 1..64")

        if not isinstance(self.formats, (tuple, list)):
            raise TypeError(f"formats must be a tuple or list, got {type(self.formats).__name__}")
        object.__setattr__(self, "formats", tuple(self.formats))
        if not (1 <= len(self.formats) <= 10):
            raise ValueError("formats must contain between 1 and 10 items")
        for fmt in self.formats:
            if not isinstance(fmt, str) or not fmt:
                raise TypeError("formats items must be non-empty strings")

        if type(self.continue_on_error) is not bool:
            raise TypeError(f"continue_on_error must be a bool, got {type(self.continue_on_error).__name__}")

    @classmethod
    def from_request(cls, request: BatchRequest) -> BatchExecutionSpec:
        """Construct an internal execution spec from a validated public BatchRequest."""
        if not isinstance(request, BatchRequest):
            raise TypeError(f"request must be a BatchRequest, got {type(request).__name__}")
        return cls(
            contract_version=request.contract_version,
            request_id=request.request_id,
            input_root=request.input.root,
            output_root=request.output_root,
            inputs=tuple(request.input.files),
            operation_id=request.operation.type,
            formats=tuple(request.operation.formats),
            continue_on_error=request.options.continue_on_error,
        )


@dataclass(frozen=True)
class BatchItemContext:
    """Immutable per-format execution context supplied to an operation handler.

    Contains only the validated path parameters required to perform a single
    format export. Strictly excludes COM objects, worker threads, and transport state.
    """

    request_id: str
    operation_id: str
    input: str
    source_path: Path
    format: str
    target_relative_path: str
    work_path: Path
    target_path: Path

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str) or not self.request_id:
            raise ValueError("request_id must be a non-empty string")
        if not isinstance(self.operation_id, str) or not self.operation_id:
            raise ValueError("operation_id must be a non-empty string")
        if not isinstance(self.input, str) or not self.input:
            raise ValueError("input must be a non-empty string")
        if not isinstance(self.format, str) or not self.format:
            raise ValueError("format must be a non-empty string")
        if not isinstance(self.target_relative_path, str) or not self.target_relative_path:
            raise ValueError("target_relative_path must be a non-empty string")

        if not isinstance(self.source_path, Path) or not self.source_path.is_absolute():
            raise ValueError(f"source_path must be an absolute Path, got {self.source_path!r}")
        if not isinstance(self.work_path, Path) or not self.work_path.is_absolute():
            raise ValueError(f"work_path must be an absolute Path, got {self.work_path!r}")
        if not isinstance(self.target_path, Path) or not self.target_path.is_absolute():
            raise ValueError(f"target_path must be an absolute Path, got {self.target_path!r}")


@dataclass(frozen=True)
class BatchItemOutcome:
    """Immutable per-format outcome returned by an operation handler.

    Must represent either clean single-artifact success or failure with at least
    one approved error diagnostic.
    """

    format: str
    artifact: BatchArtifactRecord | None = None
    diagnostics: tuple[BatchDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.format, str) or not self.format:
            raise ValueError("format must be a non-empty string")

        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        for d in self.diagnostics:
            if not isinstance(d, BatchDiagnostic):
                raise TypeError(f"diagnostics items must be BatchDiagnostic, got {type(d).__name__}")
            if d.format is not None and d.format != self.format:
                raise ValueError(f"Diagnostic format '{d.format}' does not match outcome format '{self.format}'")

        errors = [d for d in self.diagnostics if d.code not in APPROVED_WARNING_CODES]

        if self.artifact is not None:
            if not isinstance(self.artifact, BatchArtifactRecord):
                raise TypeError(f"artifact must be BatchArtifactRecord, got {type(self.artifact).__name__}")
            if self.artifact.format != self.format:
                raise ValueError(
                    f"Artifact format '{self.artifact.format}' does not match outcome format '{self.format}'"
                )
            if errors:
                raise ValueError(f"Successful BatchItemOutcome must not contain error diagnostics, got {errors}")
        else:
            if not errors:
                raise ValueError("Failed BatchItemOutcome must contain at least one error diagnostic")

    @classmethod
    def success(
        cls,
        format: str,
        artifact: BatchArtifactRecord,
        warnings: Sequence[BatchDiagnostic] = (),
    ) -> BatchItemOutcome:
        """Construct a successful per-format outcome."""
        return cls(format=format, artifact=artifact, diagnostics=tuple(warnings))

    @classmethod
    def failure(
        cls,
        format: str,
        errors: Sequence[BatchDiagnostic],
        warnings: Sequence[BatchDiagnostic] = (),
    ) -> BatchItemOutcome:
        """Construct a failed per-format outcome."""
        return cls(format=format, artifact=None, diagnostics=tuple(errors) + tuple(warnings))

    @property
    def is_success(self) -> bool:
        return self.artifact is not None

    @property
    def errors(self) -> tuple[BatchDiagnostic, ...]:
        return tuple(d for d in self.diagnostics if d.code not in APPROVED_WARNING_CODES)

    @property
    def warnings(self) -> tuple[BatchDiagnostic, ...]:
        return tuple(d for d in self.diagnostics if d.code in APPROVED_WARNING_CODES)

    def validate_against_context(self, context: BatchItemContext) -> None:
        """Validate that outcome matches the dispatch context format and target path."""
        if self.format != context.format:
            raise ValueError(f"Outcome format '{self.format}' does not match context format '{context.format}'")
        if self.artifact is not None:
            expected_target_str = str(context.target_path)
            expected_target_posix = context.target_path.as_posix()
            if self.artifact.path != expected_target_str and self.artifact.path != expected_target_posix:
                raise ValueError(
                    f"Artifact path '{self.artifact.path}' does not match context target path '{expected_target_str}'"
                )


@dataclass(frozen=True)
class BatchExecutionOutcome:
    """Immutable pre-manifest terminal outcome of batch execution.

    Aggregates per-file results, partition accounting, and diagnostics.
    Retains output_root only as private context for M5.6 manifest projection.
    """

    status: BatchTerminalStatus
    request_id: str
    contract_version: str
    summary: BatchSummary | None = None
    file_results: tuple[BatchFileResult, ...] = ()
    unprocessed_files: tuple[str, ...] = ()
    cancelled_files: tuple[str, ...] = ()
    errors: tuple[BatchDiagnostic, ...] = ()
    warnings: tuple[BatchDiagnostic, ...] = ()
    cad_runtime_version_build: str | None = None
    output_root: Path | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str) or not self.request_id:
            raise ValueError("request_id must be a non-empty string")
        if not isinstance(self.contract_version, str) or not self.contract_version:
            raise ValueError("contract_version must be a non-empty string")

        object.__setattr__(self, "file_results", tuple(self.file_results))
        object.__setattr__(self, "unprocessed_files", tuple(self.unprocessed_files))
        object.__setattr__(self, "cancelled_files", tuple(self.cancelled_files))
        object.__setattr__(self, "errors", tuple(self.errors))
        object.__setattr__(self, "warnings", tuple(self.warnings))

        if self.cad_runtime_version_build is not None and (
            not isinstance(self.cad_runtime_version_build, str) or not self.cad_runtime_version_build
        ):
            raise ValueError("cad_runtime_version_build must be a non-empty string or None")

        if self.output_root is not None and (
            not isinstance(self.output_root, Path) or not self.output_root.is_absolute()
        ):
            raise ValueError(f"output_root must be an absolute Path or None, got {self.output_root!r}")

        from batch.terminal import validate_terminal_semantics_core

        validate_terminal_semantics_core(
            status=self.status,
            summary=self.summary,
            results=self.file_results,
            unprocessed_files=self.unprocessed_files,
            cancelled_files=self.cancelled_files,
            errors=self.errors,
            warnings=self.warnings,
            request_id=self.request_id,
        )


@dataclass(frozen=True)
class BatchProgressUpdate:
    """Discrete execution progress update emitted during batch execution."""

    request_id: str
    phase: BatchProgressPhase
    total_files: int
    completed_files: int
    current_file: str | None = None
    current_format: str | None = None
    file_status: FileResultStatus | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.request_id, str) or not self.request_id:
            raise ValueError("request_id must be a non-empty string")
        if self.phase not in APPROVED_PROGRESS_PHASES:
            raise ValueError(f"Invalid progress phase '{self.phase}'")
        if type(self.total_files) is not int or self.total_files < 0:
            raise ValueError("total_files must be a non-negative integer")
        if type(self.completed_files) is not int or self.completed_files < 0:
            raise ValueError("completed_files must be a non-negative integer")
        if self.completed_files > self.total_files:
            raise ValueError(f"completed_files ({self.completed_files}) cannot exceed total_files ({self.total_files})")

        if self.current_file is not None:
            from batch.paths import validate_and_canonicalize_input_path

            canonical = validate_and_canonicalize_input_path(self.current_file, request_id=self.request_id)
            if self.current_file != canonical:
                raise ValueError(f"current_file must be a canonical relative path, got {self.current_file!r}")

        if self.current_format is not None and (
            not isinstance(self.current_format, str)
            or not self.current_format
            or self.current_format != self.current_format.strip()
            or self.current_format != self.current_format.lower()
        ):
            raise ValueError("current_format must be a non-empty lowercase string without whitespace")

        if self.file_status is not None and self.file_status not in ("accepted", "partial", "failed"):
            raise ValueError(f"Invalid file_status '{self.file_status}'")

        # Enforce required and forbidden fields per phase
        if self.phase == "batch_started":
            if self.completed_files != 0:
                raise ValueError("batch_started requires completed_files == 0")
            if self.current_file is not None:
                raise ValueError("batch_started must not include current_file")
            if self.current_format is not None:
                raise ValueError("batch_started must not include current_format")
            if self.file_status is not None:
                raise ValueError("batch_started must not include file_status")

        elif self.phase == "file_started":
            if self.current_file is None:
                raise ValueError("file_started requires current_file")
            if self.current_format is not None:
                raise ValueError("file_started must not include current_format")
            if self.file_status is not None:
                raise ValueError("file_started must not include file_status")

        elif self.phase == "format_started":
            if self.current_file is None:
                raise ValueError("format_started requires current_file")
            if self.current_format is None:
                raise ValueError("format_started requires current_format")
            if self.file_status is not None:
                raise ValueError("format_started must not include file_status")

        elif self.phase == "format_finished":
            if self.current_file is None:
                raise ValueError("format_finished requires current_file")
            if self.current_format is None:
                raise ValueError("format_finished requires current_format")
            if self.file_status is not None:
                raise ValueError("format_finished must not include file_status")

        elif self.phase == "file_finished":
            if self.current_file is None:
                raise ValueError("file_finished requires current_file")
            if self.current_format is not None:
                raise ValueError("file_finished must not include current_format")
            if self.file_status is None:
                raise ValueError("file_finished requires file_status")
            if self.completed_files < 1:
                raise ValueError("file_finished requires completed_files >= 1")

        elif self.phase == "batch_finished":
            if self.current_file is not None:
                raise ValueError("batch_finished must not include current_file")
            if self.current_format is not None:
                raise ValueError("batch_finished must not include current_format")
            if self.file_status is not None:
                raise ValueError("batch_finished must not include file_status")

    def to_dict(self) -> dict[str, Any]:
        """Convert progress update to a dictionary omitting None fields."""
        res: dict[str, Any] = {
            "request_id": self.request_id,
            "phase": self.phase,
            "total_files": self.total_files,
            "completed_files": self.completed_files,
        }
        if self.current_file is not None:
            res["current_file"] = self.current_file
        if self.current_format is not None:
            res["current_format"] = self.current_format
        if self.file_status is not None:
            res["file_status"] = self.file_status
        return res
