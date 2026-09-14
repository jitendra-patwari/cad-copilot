"""Batch automation contracts, typed models, validation, metadata registry, and execution bindings."""

from __future__ import annotations

from batch.allocation import (
    AllocatedBatchFile,
    AllocatedBatchFormat,
    AllocatedBatchWork,
    BatchAllocationCollisionError,
    BatchSafetyBoundary,
    BatchSafetyRejectionError,
    PreparedBatchFile,
    PreparedBatchFormat,
    PreparedBatchWork,
    allocate_batch_work,
    build_collision_rejection,
    validate_prepared_work_consistency,
)
from batch.bindings import (
    BatchHandlerFactory,
    BatchOperationHandler,
    OperationBinding,
    OperationBindings,
)
from batch.composition import build_initial_operation_bindings
from batch.execution import (
    BatchExecutionOutcome,
    BatchExecutionSpec,
    BatchItemContext,
    BatchItemOutcome,
    BatchProgressPhase,
    BatchProgressUpdate,
)
from batch.filesystem import PathIdentity
from batch.finalization import finalize_batch_outcome
from batch.format_validation import (
    BatchFormatValidationError,
    BatchFormatValidator,
    validate_batch_output,
)
from batch.handlers import (
    Export3DHandler,
    PublishDrawingHandler,
)
from batch.manifest import ManifestAssemblyError, assemble_batch_manifest
from batch.manifest_publication import ManifestPublicationError, publish_batch_manifest
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
from batch.output_workspace import (
    BatchOutputWorkspace,
    BatchWorkspaceError,
    FilesystemOutputWorkspace,
    OutputSnapshot,
)
from batch.registry import (
    OperationDescriptor,
    OperationRegistry,
    build_initial_registry,
)
from batch.safety import FilesystemBatchSafetyBoundary
from batch.service import BatchService
from batch.source_integrity import SourceSnapshot

__all__: list[str] = [
    "AllocatedBatchFile",
    "AllocatedBatchFormat",
    "AllocatedBatchWork",
    "BatchAllocationCollisionError",
    "BatchArtifactRecord",
    "BatchConfigurationError",
    "BatchContractError",
    "BatchDiagnostic",
    "BatchExecutionOutcome",
    "BatchExecutionSpec",
    "BatchFileResult",
    "BatchFormatValidationError",
    "BatchFormatValidator",
    "BatchHandlerFactory",
    "BatchInputSelection",
    "BatchItemContext",
    "BatchItemOutcome",
    "BatchManifest",
    "BatchManifestReference",
    "BatchMetadata",
    "BatchOperation",
    "BatchOperationHandler",
    "BatchOperationType",
    "BatchOptions",
    "BatchOutputWorkspace",
    "BatchProgressPhase",
    "BatchProgressUpdate",
    "BatchRequest",
    "BatchResponse",
    "BatchSafetyBoundary",
    "BatchSafetyRejectionError",
    "BatchService",
    "BatchSummary",
    "BatchValidationError",
    "BatchWorkspaceError",
    "Export3DHandler",
    "FileResultStatus",
    "FilesystemBatchSafetyBoundary",
    "FilesystemOutputWorkspace",
    "ManifestArtifactRecord",
    "ManifestAssemblyError",
    "ManifestFileResult",
    "ManifestPublicationError",
    "OperationBinding",
    "OperationBindings",
    "OperationDescriptor",
    "OperationRegistry",
    "OutputSnapshot",
    "PathIdentity",
    "PreparedBatchFile",
    "PreparedBatchFormat",
    "PreparedBatchWork",
    "PublishDrawingHandler",
    "SourceSnapshot",
    "allocate_batch_work",
    "assemble_batch_manifest",
    "build_collision_rejection",
    "build_initial_operation_bindings",
    "build_initial_registry",
    "finalize_batch_outcome",
    "publish_batch_manifest",
    "validate_batch_output",
    "validate_prepared_work_consistency",
]
