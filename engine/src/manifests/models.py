"""Frozen domain models and value types for run manifest context and data.

This module provides immutable, strictly-validated data structures for
manifest construction, ensuring fail-fast validation before CAD runtime
acquisition or file publication.
"""

from __future__ import annotations

import copy
import importlib.metadata
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# Bounded identifier and format validation patterns
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]([A-Za-z0-9._-]*[A-Za-z0-9_-])?$")
SHA256_HEX_PATTERN = re.compile(r"^[a-f0-9]{64}$")
ARTIFACT_FILENAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+\.(par|step|stl|jpg)$")
ENGINE_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+.*$")

MAX_IDENTIFIER_LENGTH = 128
MAX_REQUEST_ID_LENGTH = 96
MAX_BUILD_STRING_LENGTH = 128
MAX_ENGINE_VERSION_LENGTH = 64

CANONICAL_MANIFEST_VERSION = "cad_copilot.run_manifest.v1"
CANONICAL_CONTRACT_VERSION = "1.0"
CANONICAL_UNIT = "mm"
CANONICAL_ENGINE_NAME = "cad-copilot"
CANONICAL_CAD_RUNTIME_PRODUCT = "solid_edge"

ARTIFACT_FORMAT_TO_TYPE: dict[str, str] = {
    "par": "native_part",
    "step": "geometry_step",
    "stl": "mesh_stl",
    "jpg": "preview_image",
}
VALID_GATE_MODES = frozenset({"capability_first", "strict"})
VALID_REQUEST_KINDS = frozenset({"example_plan", "prompt_to_cad"})
VALID_PROVENANCE_KINDS = frozenset({"example_plan", "ai_proposal"})
VALID_ARTIFACT_TYPES = frozenset(ARTIFACT_FORMAT_TO_TYPE.values())
VALID_ARTIFACT_FORMATS = frozenset(ARTIFACT_FORMAT_TO_TYPE.keys())
VALID_REFERENCE_KINDS = frozenset({"body", "feature"})


# Free-text credential pattern: requires assignment/key boundaries or high-entropy token formats.
# Avoids substring false positives on innocent words like 'authoring', 'authentication', or 'tokenized'.
FREE_TEXT_CREDENTIAL_PATTERN = re.compile(
    r"(?i)(?:"
    r"bearer\s+[a-z0-9_.-]+"
    r"|sk-[a-z0-9_-]{8,}"
    r"|aiza[0-9a-z_-]{16,}"
    r"|\b(?:"
    r"api[\s_-]?key"
    r"|access[\s_-]?token"
    r"|refresh[\s_-]?token"
    r"|auth(?:orization)?[\s_-]?token"
    r"|client[\s_-]?secret"
    r"|secret[\s_-]?key"
    r"|private[\s_-]?key"
    r"|password"
    r"|passwd"
    r"|authorization"
    r"|auth"
    r"|token"
    r"|secret"
    r")\s*[:=]\s*\S+"
    r"|\bapi[\s_-]?key\b"
    r")"
)

# Sensitive key/token pattern: exact word-boundary detection for mapping keys and isolated tokens.
SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)\b(?:"
    r"password|passwd|secret|token|auth"
    r"|api[\s_-]?key|access[\s_-]?token|refresh[\s_-]?token|auth(?:orization)?[\s_-]?token"
    r"|client[\s_-]?secret|secret[\s_-]?key|private[\s_-]?key|authorization"
    r")\b"
)

# Backward-compatible alias
CREDENTIAL_PATTERN = FREE_TEXT_CREDENTIAL_PATTERN

LOCAL_PATH_PATTERN = re.compile(
    r"([A-Za-z]:[/\\]"
    r"|/(?:home|users|etc|tmp|var|usr|opt|bin)[/\\]"
    r"|\\\\[A-Za-z0-9._-]+[/\\][A-Za-z0-9._$-]+"
    r"|^\\\\[A-Za-z0-9._-]+)",
    re.IGNORECASE,
)


class ManifestError(Exception):
    """Base exception for all run manifest errors."""


class ManifestConfigurationError(ManifestError):
    """Raised when environment or package distribution metadata is missing/invalid."""


class ManifestValidationError(ManifestError):
    """Raised when manifest data violates structural, typing, or consistency rules."""


def resolve_engine_version(distribution_name: str = "cad-copilot") -> str:
    """Resolve installed engine version from distribution package metadata.

    Fails closed without fallback if package distribution metadata is unavailable.
    """
    try:
        ver = importlib.metadata.version(distribution_name)
    except importlib.metadata.PackageNotFoundError as exc:
        raise ManifestConfigurationError(
            f"Required package distribution metadata for '{distribution_name}' is not installed."
        ) from exc

    if not ver or not ver.strip():
        raise ManifestConfigurationError(
            f"Package distribution '{distribution_name}' returned an empty version string."
        )
    return ver.strip()


def _validate_safe_id(name: str, value: str, max_length: int = MAX_IDENTIFIER_LENGTH) -> str:
    if not isinstance(value, str):
        raise ManifestValidationError(f"{name} must be a string, got {type(value).__name__}")
    if len(value) < 1 or len(value) > max_length:
        raise ManifestValidationError(f"{name} length must be between 1 and {max_length} characters, got {len(value)}")
    if FREE_TEXT_CREDENTIAL_PATTERN.search(value) or LOCAL_PATH_PATTERN.search(value):
        raise ManifestValidationError(f"{name} contains forbidden credential or path pattern")
    if not IDENTIFIER_PATTERN.match(value):
        raise ManifestValidationError(f"{name} does not match safe identifier pattern '^[A-Za-z0-9][A-Za-z0-9._-]*$'")
    return value


def _validate_safe_id_collection(name: str, value: Any) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (tuple, list)):
        raise ManifestValidationError(f"{name} must be a sequence of strings, got {type(value).__name__}")
    normalized: list[str] = []
    for i, item in enumerate(value):
        if not isinstance(item, str):
            raise ManifestValidationError(f"{name}[{i}] must be a string, got {type(item).__name__}")
        _validate_safe_id(f"{name}[{i}]", item)
        normalized.append(item)
    if len(normalized) != len(set(normalized)):
        raise ManifestValidationError(f"{name} contains duplicate identifiers")
    return tuple(normalized)


def _validate_safe_text(name: str, value: str, max_length: int = 512, *, allow_newlines: bool = False) -> None:
    if not isinstance(value, str):
        raise ManifestValidationError(f"{name} must be a string, got {type(value).__name__}")
    if len(value) > max_length:
        raise ManifestValidationError(f"{name} exceeds max length of {max_length} characters")
    if allow_newlines:
        if any(ord(c) < 32 and c not in "\t\n\r" for c in value):
            raise ManifestValidationError(f"{name} contains prohibited control characters")
    else:
        if any(ord(c) < 32 for c in value):
            raise ManifestValidationError(f"{name} contains prohibited control characters")


def _validate_sha256(name: str, value: str | None, required: bool = True) -> None:
    if value is None:
        if required:
            raise ManifestValidationError(f"{name} is required and cannot be None")
        return
    if not isinstance(value, str):
        raise ManifestValidationError(f"{name} must be a string, got {type(value).__name__}")
    if not SHA256_HEX_PATTERN.match(value):
        raise ManifestValidationError(f"{name} must be exactly 64 lowercase hex characters")


def _validate_strict_int(name: str, value: Any, min_value: int = 0) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ManifestValidationError(f"{name} must be a strict integer, got {type(value).__name__}")
    if value < min_value:
        raise ManifestValidationError(f"{name} must be >= {min_value}, got {value}")


def _validate_strict_float(
    name: str,
    value: Any,
    exclusive_min: float | None = None,
    min_value: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ManifestValidationError(f"{name} must be a numeric float, got {type(value).__name__}")
    f_val = float(value)
    if not math.isfinite(f_val):
        raise ManifestValidationError(f"{name} must be a finite number, got {f_val}")
    if f_val == 0.0:
        f_val = 0.0  # Normalize negative zero (-0.0) to canonical 0.0
    if exclusive_min is not None and f_val <= exclusive_min:
        raise ManifestValidationError(f"{name} must be > {exclusive_min}, got {f_val}")
    if min_value is not None and f_val < min_value:
        raise ManifestValidationError(f"{name} must be >= {min_value}, got {f_val}")
    return f_val


@dataclass(frozen=True)
class ManifestStableIds:
    """Ordered stable identifiers associated with the FeaturePlan entities."""

    part_id: str
    body_ids: tuple[str, ...]
    boolean_operation_ids: tuple[str, ...]
    feature_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_safe_id("part_id", self.part_id)
        object.__setattr__(self, "body_ids", _validate_safe_id_collection("body_ids", self.body_ids))
        object.__setattr__(
            self,
            "boolean_operation_ids",
            _validate_safe_id_collection("boolean_operation_ids", self.boolean_operation_ids),
        )
        object.__setattr__(self, "feature_ids", _validate_safe_id_collection("feature_ids", self.feature_ids))

    def to_dict(self) -> dict[str, Any]:
        return {
            "part_id": self.part_id,
            "body_ids": list(self.body_ids),
            "boolean_operation_ids": list(self.boolean_operation_ids),
            "feature_ids": list(self.feature_ids),
        }


@dataclass(frozen=True)
class ManifestArtifactRecord:
    """Descriptor of an exported CAD or visual artifact."""

    type: str
    format: str
    path: str
    size_bytes: int
    sha256: str

    def __post_init__(self) -> None:
        if self.format not in ARTIFACT_FORMAT_TO_TYPE:
            raise ManifestValidationError(f"Invalid artifact format; must be one of {sorted(ARTIFACT_FORMAT_TO_TYPE)}")
        expected_type = ARTIFACT_FORMAT_TO_TYPE[self.format]
        if self.type != expected_type:
            raise ManifestValidationError(
                f"Artifact format '{self.format}' requires type '{expected_type}', got '{self.type}'"
            )
        if not isinstance(self.path, str) or not ARTIFACT_FILENAME_PATTERN.match(self.path):
            raise ManifestValidationError(
                f"Artifact path must be a canonical relative filename matching {ARTIFACT_FILENAME_PATTERN.pattern}"
            )
        expected_ext = f".{self.format}"
        if not self.path.endswith(expected_ext):
            raise ManifestValidationError(
                f"Artifact path must end with '{expected_ext}' matching format '{self.format}'"
            )
        _validate_strict_int("size_bytes", self.size_bytes, min_value=1)
        _validate_sha256("sha256", self.sha256, required=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "format": self.format,
            "path": self.path,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class ManifestOperationResult:
    """Result record for an executed operation step."""

    patch_id: str
    operation: str
    reference_id: str
    reference_kind: str

    def __post_init__(self) -> None:
        _validate_safe_id("patch_id", self.patch_id, max_length=128)
        _validate_safe_id("operation", self.operation, max_length=64)
        _validate_safe_id("reference_id", self.reference_id, max_length=128)
        if self.reference_kind not in VALID_REFERENCE_KINDS:
            raise ManifestValidationError("Invalid reference_kind; must be 'body' or 'feature'")

    def to_dict(self) -> dict[str, Any]:
        return {
            "patch_id": self.patch_id,
            "operation": self.operation,
            "reference_id": self.reference_id,
            "reference_kind": self.reference_kind,
        }


@dataclass(frozen=True)
class ManifestInspection:
    """Inspection metrics captured after execution."""

    volume_mm3: float
    mass_kg: float
    feature_count: int
    body_count: int
    solid_body_count: int | None
    sheet_body_count: int | None
    wire_body_count: int | None

    def __post_init__(self) -> None:
        norm_vol = _validate_strict_float("volume_mm3", self.volume_mm3, exclusive_min=0.0)
        norm_mass = _validate_strict_float("mass_kg", self.mass_kg, min_value=0.0)
        object.__setattr__(self, "volume_mm3", norm_vol)
        object.__setattr__(self, "mass_kg", norm_mass)
        _validate_strict_int("feature_count", self.feature_count, min_value=0)
        _validate_strict_int("body_count", self.body_count, min_value=1)

        for name, val in [
            ("solid_body_count", self.solid_body_count),
            ("sheet_body_count", self.sheet_body_count),
            ("wire_body_count", self.wire_body_count),
        ]:
            if val is not None:
                _validate_strict_int(name, val, min_value=0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "volume_mm3": 0.0 if self.volume_mm3 == 0.0 else float(self.volume_mm3),
            "mass_kg": 0.0 if self.mass_kg == 0.0 else float(self.mass_kg),
            "feature_count": self.feature_count,
            "body_count": self.body_count,
            "solid_body_count": self.solid_body_count,
            "sheet_body_count": self.sheet_body_count,
            "wire_body_count": self.wire_body_count,
        }


@dataclass(frozen=True)
class ManifestExecution:
    """Full execution section including operation results and inspection."""

    operations_executed: int
    operation_results: tuple[ManifestOperationResult, ...]
    inspection: ManifestInspection

    def __post_init__(self) -> None:
        _validate_strict_int("operations_executed", self.operations_executed, min_value=0)
        if isinstance(self.operation_results, (str, bytes)) or not isinstance(self.operation_results, (tuple, list)):
            raise ManifestValidationError(
                f"operation_results must be a sequence, got {type(self.operation_results).__name__}"
            )
        for i, r in enumerate(self.operation_results):
            if not isinstance(r, ManifestOperationResult):
                raise ManifestValidationError(
                    f"operation_results[{i}] must be an instance of ManifestOperationResult, got {type(r).__name__}"
                )
        object.__setattr__(self, "operation_results", tuple(self.operation_results))
        if not isinstance(self.inspection, ManifestInspection):
            raise ManifestValidationError("inspection must be an instance of ManifestInspection")

    def to_dict(self) -> dict[str, Any]:
        return {
            "operations_executed": self.operations_executed,
            "operation_results": [r.to_dict() for r in self.operation_results],
            "inspection": self.inspection.to_dict(),
        }


@dataclass(frozen=True)
class PreparedManifestData:
    """Pre-runtime manifest payload prepared immediately after canonical validation.

    Contains normalized plan serialization, stable IDs, hashes, defaults,
    and provenance. Performs no Solid Edge COM operations and retains no raw prompts.
    """

    feature_plan: Mapping[str, Any]
    stable_ids: ManifestStableIds
    plan_sha256: str
    prompt_sha256: str | None
    defaults_applied: tuple[Mapping[str, Any], ...]
    diagnostics: tuple[Mapping[str, Any], ...]
    provenance_kind: str
    source_id: str
    gate_mode: str
    request_kind: str
    engine_version: str

    def __post_init__(self) -> None:
        if isinstance(self.feature_plan, (str, bytes)) or not isinstance(self.feature_plan, Mapping):
            raise ManifestValidationError("feature_plan must be a mapping")
        if not isinstance(self.stable_ids, ManifestStableIds):
            raise ManifestValidationError("stable_ids must be an instance of ManifestStableIds")

        if isinstance(self.defaults_applied, (str, bytes)) or not isinstance(self.defaults_applied, (tuple, list)):
            raise ManifestValidationError(
                f"defaults_applied must be a sequence of mappings, got {type(self.defaults_applied).__name__}"
            )
        for i, d in enumerate(self.defaults_applied):
            if isinstance(d, (str, bytes)) or not isinstance(d, Mapping):
                raise ManifestValidationError(f"defaults_applied[{i}] must be a mapping, got {type(d).__name__}")

        if isinstance(self.diagnostics, (str, bytes)) or not isinstance(self.diagnostics, (tuple, list)):
            raise ManifestValidationError(
                f"diagnostics must be a sequence of mappings, got {type(self.diagnostics).__name__}"
            )
        for i, diag in enumerate(self.diagnostics):
            if isinstance(diag, (str, bytes)) or not isinstance(diag, Mapping):
                raise ManifestValidationError(f"diagnostics[{i}] must be a mapping, got {type(diag).__name__}")

        # Defensive deep copy to isolate state from external caller mutation after fingerprint computation
        object.__setattr__(self, "feature_plan", copy.deepcopy(dict(self.feature_plan)))
        object.__setattr__(self, "defaults_applied", tuple(copy.deepcopy(dict(d)) for d in self.defaults_applied))
        object.__setattr__(self, "diagnostics", tuple(copy.deepcopy(dict(diag)) for diag in self.diagnostics))

        _validate_sha256("plan_sha256", self.plan_sha256, required=True)

        if self.request_kind not in VALID_REQUEST_KINDS:
            raise ManifestValidationError(f"Invalid request_kind; must be one of {sorted(VALID_REQUEST_KINDS)}")

        if self.request_kind == "example_plan":
            if self.prompt_sha256 is not None:
                raise ManifestValidationError("prompt_sha256 must be None for example_plan requests")
            if self.provenance_kind != "example_plan":
                raise ManifestValidationError("provenance_kind must be 'example_plan' for example_plan requests")
        elif self.request_kind == "prompt_to_cad":
            _validate_sha256("prompt_sha256", self.prompt_sha256, required=True)
            if self.provenance_kind != "ai_proposal":
                raise ManifestValidationError("provenance_kind must be 'ai_proposal' for prompt_to_cad requests")

        if self.provenance_kind not in VALID_PROVENANCE_KINDS:
            raise ManifestValidationError(f"Invalid provenance_kind; must be one of {sorted(VALID_PROVENANCE_KINDS)}")

        _validate_safe_id("source_id", self.source_id, max_length=MAX_IDENTIFIER_LENGTH)

        if self.gate_mode not in VALID_GATE_MODES:
            raise ManifestValidationError(f"Invalid gate_mode; must be one of {sorted(VALID_GATE_MODES)}")

        if not isinstance(self.engine_version, str) or not self.engine_version.strip():
            raise ManifestValidationError("engine_version must be a non-empty string")
        if len(self.engine_version) > MAX_ENGINE_VERSION_LENGTH or not ENGINE_VERSION_PATTERN.match(
            self.engine_version
        ):
            raise ManifestValidationError("engine_version does not match semver pattern '^[0-9]+\\.[0-9]+\\.[0-9]+.*$'")


@dataclass(frozen=True)
class RunManifestContext:
    """Request-local context passed to the artifact finalizer to assemble run_manifest.json."""

    prepared_data: PreparedManifestData
    request_id: str
    contract_version: str = "1.0"
    unit: str = "mm"
    cad_runtime_version_build: str | None = None
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.prepared_data, PreparedManifestData):
            raise ManifestValidationError("prepared_data must be an instance of PreparedManifestData")

        if not isinstance(self.request_id, str):
            raise ManifestValidationError("request_id must be a string")
        if len(self.request_id) < 1 or len(self.request_id) > MAX_REQUEST_ID_LENGTH:
            raise ManifestValidationError(
                f"request_id length must be between 1 and {MAX_REQUEST_ID_LENGTH}, got {len(self.request_id)}"
            )
        if not REQUEST_ID_PATTERN.match(self.request_id):
            raise ManifestValidationError(f"request_id does not match pattern '{REQUEST_ID_PATTERN.pattern}'")
        if self.request_id in (".", ".."):
            raise ManifestValidationError("request_id cannot be a reserved path traversal token")
        if self.request_id.endswith("."):
            raise ManifestValidationError("request_id must not end with a dot")

        # Plan request_id consistency check if present
        plan_req_id = self.prepared_data.feature_plan.get("request_id")
        if plan_req_id is not None and plan_req_id != self.request_id:
            raise ManifestValidationError("request_id mismatch between context and feature_plan")

        if self.contract_version != "1.0":
            raise ManifestValidationError("contract_version must be '1.0'")

        if self.unit != "mm":
            raise ManifestValidationError("unit must be 'mm'")

        if self.cad_runtime_version_build is not None:
            _validate_safe_text(
                "cad_runtime_version_build", self.cad_runtime_version_build, max_length=MAX_BUILD_STRING_LENGTH
            )

        if isinstance(self.warnings, (str, bytes)) or not isinstance(self.warnings, (tuple, list)):
            raise ManifestValidationError(f"warnings must be a sequence of strings, got {type(self.warnings).__name__}")
        clean_warnings: list[str] = []
        for i, w in enumerate(self.warnings):
            if not isinstance(w, str):
                raise ManifestValidationError(f"warnings[{i}] must be a string, got {type(w).__name__}")
            _validate_safe_text(f"warnings[{i}]", w, max_length=512)
            clean_warnings.append(w)
        object.__setattr__(self, "warnings", tuple(clean_warnings))
