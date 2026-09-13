"""Pure immutable value objects, diagnostic constants, and error types for batch processing.

All models in this module are transport-neutral, immutable dataclasses.
They have zero dependencies on COM, Solid Edge, AI providers, network,
or filesystem execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# Approved operation and format enumerations
BatchOperationType = Literal["export_3d", "publish_drawing"]
Export3DFormat = Literal["step", "stl"]
PublishDrawingFormat = Literal["pdf", "dxf"]
BatchOutputFormat = Literal["step", "stl", "parasolid", "pdf", "dxf"]

BatchTerminalStatus = Literal["completed", "cancelled", "rejected", "failed"]
FileResultStatus = Literal["accepted", "partial", "failed"]
ManifestTerminalStatus = Literal["completed", "cancelled", "failed"]
BatchMetadataSource = Literal["desktop_app", "cad_copilot", "local_agent"]

# Canonical diagnostic error codes (21 approved codes)
APPROVED_DIAGNOSTIC_CODES: frozenset[str] = frozenset(
    {
        "INVALID_SCHEMA",
        "PAYLOAD_TOO_LARGE",
        "UNSUPPORTED_OPERATION",
        "UNSUPPORTED_FORMAT",
        "INPUT_ROOT_NOT_FOUND",
        "INPUT_PATH_NOT_ALLOWED",
        "INPUT_FILE_NOT_FOUND",
        "INPUT_EXTENSION_NOT_ALLOWED",
        "OUTPUT_ROOT_UNAVAILABLE",
        "OUTPUT_TARGET_COLLISION",
        "TARGET_ALREADY_EXISTS",
        "SOLID_EDGE_UNAVAILABLE",
        "SOLID_EDGE_UNHEALTHY",
        "DOCUMENT_OPEN_FAILED",
        "ARTIFACT_EXPORT_FAILED",
        "DOCUMENT_CLOSE_FAILED",
        "SOURCE_INTEGRITY_FAILED",
        "MANIFEST_PUBLICATION_FAILED",
        "VERSION_METADATA_UNAVAILABLE",
        "INTERNAL_ERROR",
        "BATCH_CANCELLED",
    }
)

# Canonical warning codes initially approved
APPROVED_WARNING_CODES: frozenset[str] = frozenset({"VERSION_METADATA_UNAVAILABLE"})


class BatchContractError(Exception):
    """Base exception for batch contract framing and validation failures."""

    def __init__(self, code: str, message: str, *, request_id: str = "unknown") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.request_id = request_id


class BatchValidationError(BatchContractError, ValueError):
    """Raised when batch payload violates schema, typing, path, or accounting invariants."""

    def __init__(
        self,
        message: str = "Batch payload failed validation.",
        *,
        code: str = "INVALID_SCHEMA",
        request_id: str = "unknown",
    ) -> None:
        super().__init__(code, message, request_id=request_id)


class BatchConfigurationError(Exception):
    """Raised when packaged batch schema resources cannot be loaded or meta-validated."""


@dataclass(frozen=True)
class BatchDiagnostic:
    """Sanitized diagnostic record representing a single error or warning."""

    code: str
    message: str
    format: BatchOutputFormat | None = None

    def __post_init__(self) -> None:
        if self.code not in APPROVED_DIAGNOSTIC_CODES:
            raise ValueError(f"Unknown diagnostic code '{self.code}'")
        if not isinstance(self.message, str) or not (1 <= len(self.message) <= 512):
            raise ValueError("Diagnostic message must be a string with length between 1 and 512")
        if self.format is not None and self.format not in ("step", "stl", "parasolid", "pdf", "dxf"):
            raise ValueError(f"Invalid format '{self.format}' for diagnostic record")


BatchError = BatchDiagnostic
BatchWarning = BatchDiagnostic


@dataclass(frozen=True)
class BatchInputSelection:
    """Input specification with source root and caller-requested files."""

    root: str
    files: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.root, str) or not (1 <= len(self.root) <= 1024):
            raise ValueError("input.root must be a string with length between 1 and 1024")
        if not isinstance(self.files, (tuple, list)):
            raise TypeError(f"input.files must be a sequence, got {type(self.files).__name__}")
        object.__setattr__(self, "files", tuple(self.files))
        if not (1 <= len(self.files) <= 500):
            raise ValueError("input.files must be a non-empty sequence of at most 500 items")
        for f in self.files:
            if not isinstance(f, str) or not f:
                raise TypeError("input.files items must be non-empty strings")


@dataclass(frozen=True)
class BatchOperation:
    """Operation specification defining operation type and requested output formats."""

    type: BatchOperationType
    formats: tuple[BatchOutputFormat, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.formats, (tuple, list)):
            raise TypeError(f"formats must be a sequence, got {type(self.formats).__name__}")
        object.__setattr__(self, "formats", tuple(self.formats))
        for f in self.formats:
            if not isinstance(f, str):
                raise TypeError(f"formats items must be strings, got {type(f).__name__}")

        if self.type == "export_3d":
            if not self.formats or len(self.formats) > 2:
                raise ValueError("export_3d operation formats must have 1 or 2 items")
            if not all(f in ("step", "stl") for f in self.formats):
                raise ValueError("export_3d operation formats must only contain 'step' or 'stl'")
        elif self.type == "publish_drawing":
            if not self.formats or len(self.formats) > 2:
                raise ValueError("publish_drawing operation formats must have 1 or 2 items")
            if not all(f in ("pdf", "dxf") for f in self.formats):
                raise ValueError("publish_drawing operation formats must only contain 'pdf' or 'dxf'")
        else:
            raise ValueError(f"Unsupported operation type '{self.type}'")
        if len(set(self.formats)) != len(self.formats):
            raise ValueError("formats must contain unique items")


@dataclass(frozen=True)
class BatchOptions:
    """Execution options for batch processing."""

    continue_on_error: bool = True
    max_files: int = 100

    def __post_init__(self) -> None:
        if type(self.continue_on_error) is not bool:
            raise TypeError(f"continue_on_error must be a boolean, got {type(self.continue_on_error).__name__}")
        if type(self.max_files) is not int or self.max_files < 1 or self.max_files > 500:
            raise ValueError(f"max_files must be an integer between 1 and 500, got {self.max_files!r}")


@dataclass(frozen=True)
class BatchMetadata:
    """Optional metadata attached to a batch request."""

    source: BatchMetadataSource | None = None
    label: str | None = None
    job_id: str | None = None

    def __post_init__(self) -> None:
        if self.source is not None and self.source not in ("desktop_app", "cad_copilot", "local_agent"):
            raise ValueError(f"Invalid metadata source '{self.source}'")
        if self.label is not None and (not isinstance(self.label, str) or len(self.label) > 128):
            raise ValueError("metadata.label must be a string up to 128 characters")
        if self.job_id is not None and (not isinstance(self.job_id, str) or not (1 <= len(self.job_id) <= 128)):
            raise ValueError("metadata.job_id must be a string with length between 1 and 128")


@dataclass(frozen=True)
class BatchRequest:
    """Validated, immutable batch operation request."""

    contract_version: str
    request_id: str
    kind: Literal["batch_operation"]
    input: BatchInputSelection
    output_root: str
    operation: BatchOperation
    options: BatchOptions = field(default_factory=BatchOptions)
    metadata: BatchMetadata | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.input, BatchInputSelection):
            raise TypeError(f"input must be a BatchInputSelection, got {type(self.input).__name__}")
        if not isinstance(self.operation, BatchOperation):
            raise TypeError(f"operation must be a BatchOperation, got {type(self.operation).__name__}")
        if not isinstance(self.options, BatchOptions):
            raise TypeError(f"options must be a BatchOptions, got {type(self.options).__name__}")
        if self.metadata is not None and not isinstance(self.metadata, BatchMetadata):
            raise TypeError(f"metadata must be BatchMetadata or None, got {type(self.metadata).__name__}")

        from batch.paths import validate_batch_request_semantics

        validate_batch_request_semantics(self)


@dataclass(frozen=True)
class BatchArtifactRecord:
    """Wire artifact record returned in file results."""

    format: BatchOutputFormat
    path: str

    def __post_init__(self) -> None:
        if self.format not in ("step", "stl", "parasolid", "pdf", "dxf"):
            raise ValueError(f"Invalid artifact format '{self.format}'")
        if not isinstance(self.path, str) or len(self.path) < 1:
            raise ValueError("Artifact path must be a non-empty string")


@dataclass(frozen=True)
class ManifestArtifactRecord:
    """Durable manifest artifact record with relative path, size, and SHA-256 digest."""

    format: BatchOutputFormat
    relative_path: str
    size_bytes: int
    sha256: str

    def __post_init__(self) -> None:
        if self.format not in ("step", "stl", "parasolid", "pdf", "dxf"):
            raise ValueError(f"Invalid artifact format '{self.format}'")
        if not isinstance(self.relative_path, str) or len(self.relative_path) < 1:
            raise ValueError("Manifest artifact relative_path must be a non-empty string")
        if type(self.size_bytes) is not int or self.size_bytes <= 0:
            raise ValueError(f"size_bytes must be a positive integer, got {self.size_bytes!r}")
        if (
            not isinstance(self.sha256, str)
            or len(self.sha256) != 64
            or not all(c in "0123456789abcdef" for c in self.sha256)
        ):
            raise ValueError(f"sha256 must be a 64-character lowercase hex string, got {self.sha256!r}")


@dataclass(frozen=True)
class BatchSummary:
    """Strict accounting summary for batch execution."""

    total: int
    accepted: int
    partial: int
    failed: int
    unprocessed: int
    cancelled: int

    def __post_init__(self) -> None:
        for field_name in ("total", "accepted", "partial", "failed", "unprocessed", "cancelled"):
            val = getattr(self, field_name)
            if type(val) is not int:
                raise TypeError(f"BatchSummary.{field_name} must be an integer, got {type(val).__name__}")
            if val < 0:
                raise ValueError(f"BatchSummary.{field_name} must be non-negative, got {val}")
        expected_total = self.accepted + self.partial + self.failed + self.unprocessed + self.cancelled
        if self.total != expected_total:
            raise ValueError(
                f"BatchSummary categories sum to {expected_total}, which does not equal total={self.total}"
            )


@dataclass(frozen=True)
class BatchFileResult:
    """Wire result for a single attempted input file."""

    input: str
    status: FileResultStatus
    artifacts: tuple[BatchArtifactRecord, ...] = ()
    errors: tuple[BatchDiagnostic, ...] = ()
    warnings: tuple[BatchDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.input, str) or len(self.input) < 1:
            raise ValueError("FileResult input must be a non-empty string")

        # Normalize tuple fields
        object.__setattr__(self, "artifacts", tuple(self.artifacts))
        object.__setattr__(self, "errors", tuple(self.errors))
        object.__setattr__(self, "warnings", tuple(self.warnings))

        for a in self.artifacts:
            if not isinstance(a, BatchArtifactRecord):
                raise TypeError(f"artifacts item must be BatchArtifactRecord, got {type(a).__name__}")
        for e in self.errors:
            if not isinstance(e, BatchDiagnostic):
                raise TypeError(f"errors item must be BatchDiagnostic, got {type(e).__name__}")
            if e.code in APPROVED_WARNING_CODES:
                raise ValueError(f"Warning code '{e.code}' is not permitted in errors")
        for w in self.warnings:
            if not isinstance(w, BatchDiagnostic):
                raise TypeError(f"warnings item must be BatchDiagnostic, got {type(w).__name__}")
            if w.code not in APPROVED_WARNING_CODES:
                raise ValueError(f"Diagnostic code '{w.code}' is not permitted in warnings")

        if self.status == "accepted":
            if not self.artifacts:
                raise ValueError("Accepted FileResult must have at least one artifact")
            if self.errors:
                raise ValueError("Accepted FileResult must not have errors")
        elif self.status == "partial":
            if not self.artifacts:
                raise ValueError("Partial FileResult must have at least one artifact")
            if not self.errors:
                raise ValueError("Partial FileResult must have at least one error")
        elif self.status == "failed":
            if self.artifacts:
                raise ValueError("Failed FileResult must not have artifacts")
            if not self.errors:
                raise ValueError("Failed FileResult must have at least one error")
        else:
            raise ValueError(f"Invalid FileResult status '{self.status}'")


@dataclass(frozen=True)
class ManifestFileResult:
    """Durable manifest result for a single attempted input file."""

    input: str
    status: FileResultStatus
    artifacts: tuple[ManifestArtifactRecord, ...] = ()
    errors: tuple[BatchDiagnostic, ...] = ()
    warnings: tuple[BatchDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.input, str) or len(self.input) < 1:
            raise ValueError("ManifestFileResult input must be a non-empty string")

        # Normalize tuple fields
        object.__setattr__(self, "artifacts", tuple(self.artifacts))
        object.__setattr__(self, "errors", tuple(self.errors))
        object.__setattr__(self, "warnings", tuple(self.warnings))

        for a in self.artifacts:
            if not isinstance(a, ManifestArtifactRecord):
                raise TypeError(f"artifacts item must be ManifestArtifactRecord, got {type(a).__name__}")
        for e in self.errors:
            if not isinstance(e, BatchDiagnostic):
                raise TypeError(f"errors item must be BatchDiagnostic, got {type(e).__name__}")
            if e.code in APPROVED_WARNING_CODES:
                raise ValueError(f"Warning code '{e.code}' is not permitted in errors")
        for w in self.warnings:
            if not isinstance(w, BatchDiagnostic):
                raise TypeError(f"warnings item must be BatchDiagnostic, got {type(w).__name__}")
            if w.code not in APPROVED_WARNING_CODES:
                raise ValueError(f"Diagnostic code '{w.code}' is not permitted in warnings")

        if self.status == "accepted":
            if not self.artifacts:
                raise ValueError("Accepted ManifestFileResult must have at least one artifact")
            if self.errors:
                raise ValueError("Accepted ManifestFileResult must not have errors")
        elif self.status == "partial":
            if not self.artifacts:
                raise ValueError("Partial ManifestFileResult must have at least one artifact")
            if not self.errors:
                raise ValueError("Partial ManifestFileResult must have at least one error")
        elif self.status == "failed":
            if self.artifacts:
                raise ValueError("Failed ManifestFileResult must not have artifacts")
            if not self.errors:
                raise ValueError("Failed ManifestFileResult must have at least one error")
        else:
            raise ValueError(f"Invalid ManifestFileResult status '{self.status}'")


@dataclass(frozen=True)
class BatchManifestReference:
    """Enclosing reference object pointing to the published summary manifest."""

    path: str

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not (1 <= len(self.path) <= 1024) or "\0" in self.path:
            raise ValueError("Manifest reference path must be a string of length 1..1024 without NULs")


@dataclass(frozen=True)
class BatchResponse:
    """Terminal response payload for a batch execution request."""

    contract_version: str
    request_id: str
    status: BatchTerminalStatus
    summary: BatchSummary | None = None
    results: tuple[BatchFileResult, ...] = ()
    manifest: BatchManifestReference | None = None
    unprocessed_files: tuple[str, ...] = ()
    cancelled_files: tuple[str, ...] = ()
    errors: tuple[BatchDiagnostic, ...] = ()
    warnings: tuple[BatchDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        # Normalize tuple fields
        object.__setattr__(self, "results", tuple(self.results))
        object.__setattr__(self, "unprocessed_files", tuple(self.unprocessed_files))
        object.__setattr__(self, "cancelled_files", tuple(self.cancelled_files))
        object.__setattr__(self, "errors", tuple(self.errors))
        object.__setattr__(self, "warnings", tuple(self.warnings))

        for r in self.results:
            if not isinstance(r, BatchFileResult):
                raise TypeError(f"results item must be BatchFileResult, got {type(r).__name__}")
        for u in self.unprocessed_files:
            if not isinstance(u, str):
                raise TypeError(f"unprocessed_files item must be str, got {type(u).__name__}")
        for c in self.cancelled_files:
            if not isinstance(c, str):
                raise TypeError(f"cancelled_files item must be str, got {type(c).__name__}")
        for e in self.errors:
            if not isinstance(e, BatchDiagnostic):
                raise TypeError(f"errors item must be BatchDiagnostic, got {type(e).__name__}")
        for w in self.warnings:
            if not isinstance(w, BatchDiagnostic):
                raise TypeError(f"warnings item must be BatchDiagnostic, got {type(w).__name__}")

        # Shared semantic validation
        from batch.terminal import validate_batch_response_semantics

        validate_batch_response_semantics(self)


@dataclass(frozen=True)
class BatchManifest:
    """Durable summary manifest representing the final state of a completed or failed batch."""

    manifest_version: str
    contract_version: str
    request_id: str
    status: ManifestTerminalStatus
    operation: BatchOperation
    summary: BatchSummary
    results: tuple[ManifestFileResult, ...]
    engine_version: str
    cancelled_files: tuple[str, ...] = ()
    unprocessed_files: tuple[str, ...] = ()
    errors: tuple[BatchDiagnostic, ...] = ()
    warnings: tuple[BatchDiagnostic, ...] = ()
    cad_runtime_version_build: str | None = None

    def __post_init__(self) -> None:
        # Normalize tuple fields
        object.__setattr__(self, "results", tuple(self.results))
        object.__setattr__(self, "unprocessed_files", tuple(self.unprocessed_files))
        object.__setattr__(self, "cancelled_files", tuple(self.cancelled_files))
        object.__setattr__(self, "errors", tuple(self.errors))
        object.__setattr__(self, "warnings", tuple(self.warnings))

        for r in self.results:
            if not isinstance(r, ManifestFileResult):
                raise TypeError(f"results item must be ManifestFileResult, got {type(r).__name__}")
        for u in self.unprocessed_files:
            if not isinstance(u, str):
                raise TypeError(f"unprocessed_files item must be str, got {type(u).__name__}")
        for c in self.cancelled_files:
            if not isinstance(c, str):
                raise TypeError(f"cancelled_files item must be str, got {type(c).__name__}")
        for e in self.errors:
            if not isinstance(e, BatchDiagnostic):
                raise TypeError(f"errors item must be BatchDiagnostic, got {type(e).__name__}")
        for w in self.warnings:
            if not isinstance(w, BatchDiagnostic):
                raise TypeError(f"warnings item must be BatchDiagnostic, got {type(w).__name__}")

        # Shared semantic validation
        from batch.terminal import validate_batch_manifest_semantics

        validate_batch_manifest_semantics(self)


__all__ = [
    "APPROVED_DIAGNOSTIC_CODES",
    "APPROVED_WARNING_CODES",
    "BatchArtifactRecord",
    "BatchConfigurationError",
    "BatchContractError",
    "BatchDiagnostic",
    "BatchError",
    "BatchFileResult",
    "BatchInputSelection",
    "BatchManifest",
    "BatchManifestReference",
    "BatchMetadata",
    "BatchMetadataSource",
    "BatchOperation",
    "BatchOperationType",
    "BatchOptions",
    "BatchOutputFormat",
    "BatchRequest",
    "BatchResponse",
    "BatchSummary",
    "BatchTerminalStatus",
    "BatchValidationError",
    "BatchWarning",
    "Export3DFormat",
    "FileResultStatus",
    "ManifestArtifactRecord",
    "ManifestFileResult",
    "ManifestTerminalStatus",
    "PublishDrawingFormat",
]
