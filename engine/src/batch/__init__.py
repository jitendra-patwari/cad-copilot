"""Batch automation contracts, typed models, validation, and metadata registry."""

from __future__ import annotations

from batch.models import (
    BatchArtifactRecord,
    BatchConfigurationError,
    BatchContractError,
    BatchDiagnostic,
    BatchFileResult,
    BatchInputSelection,
    BatchManifest,
    BatchManifestReference,
    BatchMetadata,
    BatchOperation,
    BatchOperationType,
    BatchOptions,
    BatchRequest,
    BatchResponse,
    BatchSummary,
    BatchValidationError,
    FileResultStatus,
    ManifestArtifactRecord,
    ManifestFileResult,
)
from batch.registry import (
    OperationDescriptor,
    OperationRegistry,
    build_initial_registry,
)

__all__: list[str] = [
    "BatchArtifactRecord",
    "BatchConfigurationError",
    "BatchContractError",
    "BatchDiagnostic",
    "BatchFileResult",
    "BatchInputSelection",
    "BatchManifest",
    "BatchManifestReference",
    "BatchMetadata",
    "BatchOperation",
    "BatchOperationType",
    "BatchOptions",
    "BatchRequest",
    "BatchResponse",
    "BatchSummary",
    "BatchValidationError",
    "FileResultStatus",
    "ManifestArtifactRecord",
    "ManifestFileResult",
    "OperationDescriptor",
    "OperationRegistry",
    "build_initial_registry",
]
