"""Run manifest subsystem for CAD Copilot.

Provides canonical schema validation, stable ID indexing, deterministic
FeaturePlan serialization, execution metrics tracking, and manifest staging/publication.
"""

from __future__ import annotations

from manifests.models import (
    ARTIFACT_FORMAT_TO_TYPE,
    CANONICAL_CAD_RUNTIME_PRODUCT,
    CANONICAL_CONTRACT_VERSION,
    CANONICAL_ENGINE_NAME,
    CANONICAL_MANIFEST_VERSION,
    CANONICAL_UNIT,
    IDENTIFIER_PATTERN,
    REQUEST_ID_PATTERN,
    SHA256_HEX_PATTERN,
    ManifestArtifactRecord,
    ManifestConfigurationError,
    ManifestError,
    ManifestExecution,
    ManifestInspection,
    ManifestOperationResult,
    ManifestStableIds,
    ManifestValidationError,
    PreparedManifestData,
    RunManifestContext,
    resolve_engine_version,
)
from manifests.run_manifest import (
    SCHEMA_RESOURCE_NAME,
    get_run_manifest_validator,
    load_run_manifest_schema,
)

__all__ = [
    "ARTIFACT_FORMAT_TO_TYPE",
    "CANONICAL_CAD_RUNTIME_PRODUCT",
    "CANONICAL_CONTRACT_VERSION",
    "CANONICAL_ENGINE_NAME",
    "CANONICAL_MANIFEST_VERSION",
    "CANONICAL_UNIT",
    "IDENTIFIER_PATTERN",
    "REQUEST_ID_PATTERN",
    "SCHEMA_RESOURCE_NAME",
    "SHA256_HEX_PATTERN",
    "ManifestArtifactRecord",
    "ManifestConfigurationError",
    "ManifestError",
    "ManifestExecution",
    "ManifestInspection",
    "ManifestOperationResult",
    "ManifestStableIds",
    "ManifestValidationError",
    "PreparedManifestData",
    "RunManifestContext",
    "get_run_manifest_validator",
    "load_run_manifest_schema",
    "resolve_engine_version",
]
