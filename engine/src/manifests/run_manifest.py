"""Run manifest assembly, schema validation, and persistence routines."""

from __future__ import annotations

import copy
import functools
import hashlib
import importlib.resources
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from geometry.fingerprints import sha256_canonical_json
from interfaces.models import ArtifactRecord, ExecutionSuccess
from manifests.models import (
    CANONICAL_CAD_RUNTIME_PRODUCT,
    CANONICAL_ENGINE_NAME,
    CANONICAL_MANIFEST_VERSION,
    ManifestArtifactRecord,
    ManifestConfigurationError,
    ManifestError,
    ManifestExecution,
    ManifestInspection,
    ManifestOperationResult,
    ManifestStableIds,
    ManifestValidationError,
    RunManifestContext,
)

SCHEMA_RESOURCE_NAME = "run-manifest-v1.schema.json"


CANONICAL_ARTIFACT_SPECS: tuple[tuple[str, str], ...] = (
    ("native_part", "par"),
    ("geometry_step", "step"),
    ("mesh_stl", "stl"),
    ("preview_image", "jpg"),
)


def compute_plan_fingerprint(feature_plan_dict: Mapping[str, Any]) -> str:
    """Compute the deterministic SHA-256 fingerprint of the canonical feature plan."""
    return sha256_canonical_json(feature_plan_dict)


def compute_prompt_fingerprint(prompt: str | None) -> str | None:
    """Compute exact lowercase SHA-256 hex digest of UTF-8 prompt bytes, or None."""
    if prompt is None:
        return None
    if not isinstance(prompt, str):
        raise ManifestValidationError(f"prompt must be a string or None, got {type(prompt).__name__}")
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


@functools.cache
def _load_cached_schema() -> dict[str, Any]:
    """Load and cache canonical RunManifest Draft 2020-12 schema object."""
    try:
        schema_path = importlib.resources.files("manifests").joinpath("schemas", SCHEMA_RESOURCE_NAME)
        schema_bytes = schema_path.read_bytes()
    except Exception as exc:
        raise ManifestConfigurationError(
            f"Failed to load run manifest schema resource '{SCHEMA_RESOURCE_NAME}'"
        ) from exc

    try:
        schema_obj = json.loads(schema_bytes.decode("utf-8"))
    except Exception as exc:
        raise ManifestConfigurationError(f"Run manifest schema '{SCHEMA_RESOURCE_NAME}' is not valid JSON") from exc

    if not isinstance(schema_obj, dict):
        raise ManifestConfigurationError(f"Run manifest schema '{SCHEMA_RESOURCE_NAME}' root must be a JSON object")

    return schema_obj


def load_run_manifest_schema() -> dict[str, Any]:
    """Load canonical RunManifest Draft 2020-12 schema from package data.

    Returns an isolated deep copy of the cached schema to prevent mutation.
    """
    return copy.deepcopy(_load_cached_schema())


@functools.cache
def get_run_manifest_validator() -> Draft202012Validator:
    """Create and return a compiled Draft202012Validator for the canonical schema."""
    schema = _load_cached_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _extract_stable_ids_from_serialized_plan(plan_dict: Mapping[str, Any]) -> ManifestStableIds:
    """Extract stable identifiers directly from serialized FeaturePlan dictionary."""
    part_dict = plan_dict.get("part")
    if not isinstance(part_dict, Mapping):
        raise ManifestValidationError("FeaturePlan missing 'part' mapping")
    part_id = part_dict.get("part_id")
    if not isinstance(part_id, str):
        raise ManifestValidationError("FeaturePlan missing 'part.part_id'")

    body_ids: list[str] = []
    base_body = plan_dict.get("base_body")
    if isinstance(base_body, Mapping) and "id" in base_body:
        base_id = base_body["id"]
        if isinstance(base_id, str):
            body_ids.append(base_id)

    primitive_bodies = plan_dict.get("primitive_bodies", [])
    if isinstance(primitive_bodies, Sequence):
        for pb in primitive_bodies:
            if isinstance(pb, Mapping) and "id" in pb:
                pb_id = pb["id"]
                if isinstance(pb_id, str) and pb_id not in body_ids:
                    body_ids.append(pb_id)

    boolean_operations = plan_dict.get("boolean_operations", [])
    bool_op_ids: list[str] = []
    if isinstance(boolean_operations, Sequence):
        for op in boolean_operations:
            if isinstance(op, Mapping):
                res_id = op.get("result_body_id")
                if isinstance(res_id, str) and res_id not in body_ids:
                    body_ids.append(res_id)
                op_id = op.get("id")
                if isinstance(op_id, str):
                    bool_op_ids.append(op_id)

    features = plan_dict.get("features", [])
    feature_ids: list[str] = []
    if isinstance(features, Sequence):
        for f in features:
            if isinstance(f, Mapping) and "id" in f:
                f_id = f["id"]
                if isinstance(f_id, str):
                    feature_ids.append(f_id)

    return ManifestStableIds(
        part_id=part_id,
        body_ids=tuple(body_ids),
        boolean_operation_ids=tuple(bool_op_ids),
        feature_ids=tuple(feature_ids),
    )


def assemble_run_manifest(
    context: RunManifestContext,
    execution: ExecutionSuccess,
    artifacts: Sequence[ArtifactRecord],
) -> dict[str, Any]:
    """Assemble authoritative run_manifest.json v1 document with consistency and schema validation.

    Args:
        context: Request-local manifest context containing prepared plan data, IDs, and warnings.
        execution: Successful CAD execution result with operation outcomes and inspection metrics.
        artifacts: Validated artifact records in canonical order (PAR, STEP, STL, optional JPG).

    Returns:
        Complete schema-valid run manifest dictionary.

    Raises:
        ManifestValidationError: If any consistency, bounds, ordering, or schema validation check fails.
    """
    if not isinstance(context, RunManifestContext):
        raise ManifestValidationError(f"context must be a RunManifestContext, got {type(context).__name__}")
    if not isinstance(execution, ExecutionSuccess):
        raise ManifestValidationError(f"execution must be an ExecutionSuccess, got {type(execution).__name__}")
    if isinstance(artifacts, (str, bytes)) or not isinstance(artifacts, Sequence):
        raise ManifestValidationError(f"artifacts must be a sequence of ArtifactRecord, got {type(artifacts).__name__}")

    # 1. Consistency: Request ID
    plan_req_id = context.prepared_data.feature_plan.get("request_id")
    if plan_req_id != context.request_id:
        raise ManifestValidationError(
            f"request_id mismatch between context ('{context.request_id}') and feature_plan ('{plan_req_id}')"
        )

    # 2. Consistency: Plan fingerprint
    computed_plan_hash = compute_plan_fingerprint(context.prepared_data.feature_plan)
    if computed_plan_hash != context.prepared_data.plan_sha256:
        raise ManifestValidationError(
            f"plan_sha256 mismatch: context has '{context.prepared_data.plan_sha256}', "
            f"recomputed is '{computed_plan_hash}'"
        )

    # 3. Consistency: Stable IDs
    extracted_stable_ids = _extract_stable_ids_from_serialized_plan(context.prepared_data.feature_plan)
    if context.prepared_data.stable_ids != extracted_stable_ids:
        raise ManifestValidationError(
            f"stable_ids mismatch between context and feature_plan: "
            f"context={context.prepared_data.stable_ids.to_dict()}, "
            f"extracted={extracted_stable_ids.to_dict()}"
        )

    # 4. Artifact Validation & Canonical Relative Projection
    if len(artifacts) not in (3, 4):
        raise ManifestValidationError(
            f"Invalid artifact count: expected 3 (CAD models) or 4 (with preview JPG), got {len(artifacts)}"
        )

    manifest_artifacts: list[dict[str, Any]] = []
    for i, art in enumerate(artifacts):
        if not isinstance(art, ArtifactRecord):
            raise ManifestValidationError(f"artifacts[{i}] must be an ArtifactRecord, got {type(art).__name__}")

        expected_type, expected_format = CANONICAL_ARTIFACT_SPECS[i]
        if art.format != expected_format:
            raise ManifestValidationError(
                f"Artifact at index {i} must have format '{expected_format}', got '{art.format}'"
            )
        if art.type != expected_type:
            raise ManifestValidationError(f"Artifact at index {i} must have type '{expected_type}', got '{art.type}'")

        if art.path is None:
            raise ManifestValidationError(f"Artifact at index {i} path cannot be None")

        filename = Path(art.path).name
        if filename == "run_manifest.json":
            raise ManifestValidationError("run_manifest.json must not be listed in artifacts")

        expected_filename = f"{context.request_id}.{expected_format}"
        if filename != expected_filename:
            raise ManifestValidationError(
                f"Artifact at index {i} filename does not match expected canonical filename '{expected_filename}'"
            )

        if (
            art.size_bytes is None
            or isinstance(art.size_bytes, bool)
            or not isinstance(art.size_bytes, int)
            or art.size_bytes <= 0
        ):
            raise ManifestValidationError(f"Artifact at index {i} has invalid size_bytes; must be an integer > 0")

        if art.sha256 is None or not isinstance(art.sha256, str):
            raise ManifestValidationError(f"Artifact at index {i} has missing or non-string sha256")

        # ManifestArtifactRecord validates type, format, path pattern, size_bytes, and sha256 hex
        # and its to_dict() strictly omits 'origin'
        record = ManifestArtifactRecord(
            type=expected_type,
            format=expected_format,
            path=expected_filename,
            size_bytes=art.size_bytes,
            sha256=art.sha256,
        )
        manifest_artifacts.append(record.to_dict())

    # 5. Execution & Inspection Projection
    if execution.inspection_report is None:
        raise ManifestValidationError("ExecutionSuccess missing required inspection_report")

    inspection = ManifestInspection(
        volume_mm3=execution.inspection_report.volume_mm3,
        mass_kg=execution.inspection_report.mass_kg,
        feature_count=execution.inspection_report.feature_count,
        body_count=execution.inspection_report.body_count,
        solid_body_count=execution.inspection_report.solid_body_count,
        sheet_body_count=execution.inspection_report.sheet_body_count,
        wire_body_count=execution.inspection_report.wire_body_count,
    )

    op_results: list[ManifestOperationResult] = []
    for op in execution.operation_results:
        op_results.append(
            ManifestOperationResult(
                patch_id=op.patch_id,
                operation=op.operation,
                reference_id=op.reference_id,
                reference_kind=op.reference_kind,
            )
        )

    manifest_execution = ManifestExecution(
        operations_executed=execution.operations_executed,
        operation_results=tuple(op_results),
        inspection=inspection,
    )

    # 6. Assemble Full V1 Document
    manifest_data: dict[str, Any] = {
        "manifest_version": CANONICAL_MANIFEST_VERSION,
        "request": {
            "request_id": context.request_id,
            "contract_version": context.contract_version,
            "kind": context.prepared_data.request_kind,
            "unit": context.unit,
        },
        "engine": {
            "name": CANONICAL_ENGINE_NAME,
            "version": context.prepared_data.engine_version,
        },
        "cad_runtime": {
            "product": CANONICAL_CAD_RUNTIME_PRODUCT,
            "version_build": context.cad_runtime_version_build,
        },
        "provenance": {
            "kind": context.prepared_data.provenance_kind,
            "source_id": context.prepared_data.source_id,
        },
        "gate_mode": context.prepared_data.gate_mode,
        "fingerprints": {
            "plan_sha256": context.prepared_data.plan_sha256,
            "prompt_sha256": context.prepared_data.prompt_sha256,
        },
        "feature_plan": copy.deepcopy(dict(context.prepared_data.feature_plan)),
        "stable_ids": context.prepared_data.stable_ids.to_dict(),
        "defaults_applied": [copy.deepcopy(dict(d)) for d in context.prepared_data.defaults_applied],
        "diagnostics": [copy.deepcopy(dict(d)) for d in context.prepared_data.diagnostics],
        "warnings": list(context.warnings),
        "execution": manifest_execution.to_dict(),
        "artifacts": manifest_artifacts,
    }

    # In-memory schema pre-validation
    validator = get_run_manifest_validator()
    try:
        validator.validate(manifest_data)
    except ValidationError as exc:
        raise ManifestValidationError("Assembled run manifest failed schema validation") from exc

    return manifest_data


def write_staged_run_manifest(
    staging_manifest_path: Path,
    manifest_data: Mapping[str, Any],
) -> Path:
    """Write run_manifest.json exclusively in staging, read back, and validate schema.

    Args:
        staging_manifest_path: Target path inside staging directory (must be named 'run_manifest.json').
        manifest_data: Assembled manifest document dictionary.

    Returns:
        Validated staging_manifest_path.

    Raises:
        ManifestValidationError: If path is invalid, target already exists, or read-back validation fails.
        ManifestError: If I/O operations fail.
    """
    if not isinstance(staging_manifest_path, Path):
        raise ManifestValidationError(
            f"staging_manifest_path must be a pathlib.Path, got {type(staging_manifest_path).__name__}"
        )

    if staging_manifest_path.name != "run_manifest.json":
        raise ManifestValidationError(
            f"staging_manifest_path filename must be 'run_manifest.json', got '{staging_manifest_path.name}'"
        )

    if not isinstance(manifest_data, Mapping):
        raise ManifestValidationError("manifest_data must be a mapping")

    # 1. Canonical formatted JSON bytes
    try:
        json_text = json.dumps(
            dict(manifest_data),
            indent=2,
            ensure_ascii=True,
            allow_nan=False,
        )
        payload_bytes = json_text.encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ManifestValidationError(f"Failed to serialize manifest data to JSON: {type(exc).__name__}") from exc

    # 2. Exclusive staged file creation
    try:
        with open(staging_manifest_path, "xb") as f:
            f.write(payload_bytes)
            f.flush()
    except FileExistsError as exc:
        raise ManifestValidationError(
            f"Staged manifest destination already exists: '{staging_manifest_path.name}'"
        ) from exc
    except OSError as exc:
        raise ManifestError(
            f"Failed to write staged run manifest '{staging_manifest_path.name}': {type(exc).__name__}"
        ) from exc

    # 3. Read back from staging
    try:
        read_bytes = staging_manifest_path.read_bytes()
    except OSError as exc:
        raise ManifestError(
            f"Failed to read back staged run manifest '{staging_manifest_path.name}': {type(exc).__name__}"
        ) from exc

    try:
        read_text = read_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ManifestValidationError("Staged run manifest is not valid UTF-8") from exc

    try:
        read_obj = json.loads(read_text)
    except Exception as exc:
        raise ManifestValidationError("Staged run manifest is not valid JSON") from exc

    if not isinstance(read_obj, dict):
        raise ManifestValidationError("Staged run manifest root must be a JSON object")

    # 4. Schema validation against canonical schema
    validator = get_run_manifest_validator()
    try:
        validator.validate(read_obj)
    except ValidationError as exc:
        raise ManifestValidationError("Staged run manifest failed schema validation") from exc

    return staging_manifest_path


__all__ = [
    "CANONICAL_ARTIFACT_SPECS",
    "SCHEMA_RESOURCE_NAME",
    "assemble_run_manifest",
    "compute_plan_fingerprint",
    "compute_prompt_fingerprint",
    "get_run_manifest_validator",
    "load_run_manifest_schema",
    "write_staged_run_manifest",
]
