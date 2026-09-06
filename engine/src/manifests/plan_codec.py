"""Canonical FeaturePlan codec, metadata hardening, and deterministic fingerprinting.

Provides explicit, non-generic AST serialization matching run-manifest-v1.schema.json,
pre-runtime metadata hardening, stable ID extraction, and SHA-256 fingerprinting.
"""

from __future__ import annotations

import dataclasses
import functools
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from geometry.fingerprints import sha256_canonical_json
from geometry.plan_models import (
    BodyPlacement,
    BooleanOperation,
    CircularThroughHoleFeature,
    CylinderBaseBody,
    DefaultApplied,
    FeaturePlan,
    FeaturePlanBaseBody,
    FeaturePlanFeature,
    ProfileCutoutFeature,
    ProfilePoint2D,
    RectangularBaseBody,
    RectangularExtrudedPadFeature,
    RectangularThroughCutoutFeature,
    RevolvedProfileFeature,
    RevolvedShaftBaseBody,
    SlotThroughCutoutFeature,
    SphereBaseBody,
    SpurGearBaseBody,
    SweptProtrusionFeature,
    ValidationDiagnostic,
)
from manifests.models import (
    FREE_TEXT_CREDENTIAL_PATTERN,
    LOCAL_PATH_PATTERN,
    SENSITIVE_KEY_PATTERN,
    ManifestStableIds,
    ManifestValidationError,
    PreparedManifestData,
    _validate_safe_id,
    resolve_engine_version,
)
from manifests.run_manifest import load_run_manifest_schema

SAFE_PATH_PATTERN = re.compile(r"^[A-Za-z0-9_.\[\]-]+$")
SAFE_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_+-][A-Za-z0-9._+-]*$")
SAFE_SLUG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
CREDENTIAL_PATTERN = FREE_TEXT_CREDENTIAL_PATTERN


def validate_identifier(name: str, value: str, max_length: int = 128) -> str:
    """Validate that an identifier conforms strictly to safe token patterns and contains no credentials or paths."""
    return _validate_safe_id(name, value, max_length=max_length)


def generate_structural_design_intent(plan: FeaturePlan) -> str:
    """Generate a bounded, deterministic structural summary from body and feature tokens only.

    Guarantees that raw user prompt text is never copied or inspected into the manifest summary.
    """
    body_fam = getattr(plan.base_body, "family", "base_body")
    if not plan.features:
        return f"{body_fam} with 0 features"

    counts: dict[str, int] = {}
    for f in plan.features:
        fam = getattr(f, "family", "feature")
        counts[fam] = counts.get(fam, 0) + 1

    feature_desc = ", ".join(f"{count} {fam}" for fam, count in sorted(counts.items()))
    summary = f"{body_fam} with {feature_desc}"
    return summary[:512]


def harden_plan_metadata(plan: FeaturePlan, request_kind: str) -> FeaturePlan:
    """Perform pre-validation hardening on plan metadata.

    Invariants:
      - All part, body, boolean operation, and feature IDs validated against safe identifier grammar.
      - Semantic labels validated against safe slug patterns and bounded length.
      - In prompt_to_cad: replace part.design_intent unconditionally with a purely structural summary.
      - In example_plan: validate part.design_intent against control characters, credentials, and local paths.
    """
    validate_identifier("plan.request_id", plan.request_id)
    validate_identifier("part.part_id", plan.part.part_id)
    validate_identifier("base_body.id", plan.base_body.id)

    # Validate semantic labels on base body
    if len(plan.base_body.semantic_labels) > 16:
        raise ManifestValidationError("base_body.semantic_labels exceeds max count of 16")
    if len(plan.base_body.semantic_labels) != len(set(plan.base_body.semantic_labels)):
        raise ManifestValidationError("base_body.semantic_labels contains duplicate labels")
    for lbl in plan.base_body.semantic_labels:
        validate_identifier("base_body.semantic_label", lbl, max_length=64)

    for i, pb in enumerate(plan.primitive_bodies):
        validate_identifier(f"primitive_bodies[{i}].id", pb.id)
        if len(pb.semantic_labels) > 16:
            raise ManifestValidationError(f"primitive_bodies[{i}].semantic_labels exceeds max count of 16")
        if len(pb.semantic_labels) != len(set(pb.semantic_labels)):
            raise ManifestValidationError(f"primitive_bodies[{i}].semantic_labels contains duplicate labels")
        for lbl in pb.semantic_labels:
            validate_identifier(f"primitive_bodies[{i}].semantic_label", lbl, max_length=64)

    for i, op in enumerate(plan.boolean_operations):
        validate_identifier(f"boolean_operations[{i}].id", op.id)
        validate_identifier(f"boolean_operations[{i}].target_body_id", op.target_body_id)
        validate_identifier(f"boolean_operations[{i}].tool_body_id", op.tool_body_id)
        validate_identifier(f"boolean_operations[{i}].result_body_id", op.result_body_id)

    for i, f in enumerate(plan.features):
        validate_identifier(f"features[{i}].id", f.id)
        target_body_id = getattr(f, "target_body_id", None)
        if target_body_id is not None:
            validate_identifier(f"features[{i}].target_body_id", str(target_body_id))

    if request_kind == "prompt_to_cad":
        new_design_intent = generate_structural_design_intent(plan)
    elif request_kind == "example_plan":
        intent = plan.part.design_intent
        if not isinstance(intent, str) or len(intent) > 512:
            raise ManifestValidationError("example_plan design_intent must be a string <= 512 characters")
        if any(ord(c) < 32 for c in intent):
            raise ManifestValidationError("example_plan design_intent contains prohibited control characters")
        if LOCAL_PATH_PATTERN.search(intent):
            raise ManifestValidationError("example_plan design_intent contains prohibited local path patterns")
        if FREE_TEXT_CREDENTIAL_PATTERN.search(intent):
            raise ManifestValidationError("example_plan design_intent contains prohibited credential-like patterns")
        new_design_intent = intent
    else:
        raise ManifestValidationError(f"Unsupported request_kind: {request_kind}")

    hardened_part = dataclasses.replace(plan.part, design_intent=new_design_intent)
    return dataclasses.replace(plan, part=hardened_part)


def _serialize_strict_int(
    name: str, val: Any, *, min_value: int | None = None, exclusive_min: int | None = None
) -> int:
    if isinstance(val, bool) or not isinstance(val, int):
        raise ManifestValidationError(f"{name} must be a strict integer, got {type(val).__name__}")
    if min_value is not None and val < min_value:
        raise ManifestValidationError(f"{name} must be >= {min_value}, got {val}")
    if exclusive_min is not None and val <= exclusive_min:
        raise ManifestValidationError(f"{name} must be > {exclusive_min}, got {val}")
    return val


def _serialize_float(
    name: str, val: Any, *, exclusive_min: float | None = None, min_value: float | None = None
) -> float:
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        raise ManifestValidationError(f"{name} must be a numeric float, got {type(val).__name__}")
    f_val = float(val)
    if not math.isfinite(f_val):
        raise ManifestValidationError(f"{name} must be a finite number, got {f_val}")
    if f_val == 0.0:
        f_val = 0.0
    if exclusive_min is not None and f_val <= exclusive_min:
        raise ManifestValidationError(f"{name} must be > {exclusive_min}, got {f_val}")
    if min_value is not None and f_val < min_value:
        raise ManifestValidationError(f"{name} must be >= {min_value}, got {f_val}")
    return f_val


def _serialize_point_2d(p: ProfilePoint2D) -> dict[str, float]:
    return {
        "x_mm": _serialize_float("point.x_mm", p.x_mm),
        "y_mm": _serialize_float("point.y_mm", p.y_mm),
    }


def _serialize_placement(p: BodyPlacement | None) -> dict[str, float]:
    if p is None:
        return {"x_mm": 0.0, "y_mm": 0.0, "z_mm": 0.0}
    return {
        "x_mm": _serialize_float("placement.x_mm", p.x_mm),
        "y_mm": _serialize_float("placement.y_mm", p.y_mm),
        "z_mm": _serialize_float("placement.z_mm", p.z_mm),
    }


def _serialize_base_body(body: FeaturePlanBaseBody) -> dict[str, Any]:
    placement = _serialize_placement(getattr(body, "placement", None))
    semantic_labels = list(getattr(body, "semantic_labels", ()))

    if isinstance(body, RectangularBaseBody):
        return {
            "id": body.id,
            "family": "rectangular_prism",
            "dimensions_mm": {
                "length_mm": _serialize_float("length_mm", body.length_mm, exclusive_min=0.0),
                "width_mm": _serialize_float("width_mm", body.width_mm, exclusive_min=0.0),
                "thickness_mm": _serialize_float("thickness_mm", body.thickness_mm, exclusive_min=0.0),
            },
            "placement": placement,
            "semantic_labels": semantic_labels,
        }
    if isinstance(body, CylinderBaseBody):
        return {
            "id": body.id,
            "family": "cylinder",
            "dimensions_mm": {
                "radius_mm": _serialize_float("radius_mm", body.radius_mm, exclusive_min=0.0),
                "height_mm": _serialize_float("height_mm", body.height_mm, exclusive_min=0.0),
            },
            "placement": placement,
            "semantic_labels": semantic_labels,
        }
    if isinstance(body, SphereBaseBody):
        return {
            "id": body.id,
            "family": "sphere",
            "dimensions_mm": {
                "radius_mm": _serialize_float("radius_mm", body.radius_mm, exclusive_min=0.0),
            },
            "placement": placement,
            "semantic_labels": semantic_labels,
        }
    if isinstance(body, SpurGearBaseBody):
        return {
            "id": body.id,
            "family": "spur_gear",
            "dimensions_mm": {
                "tooth_count": _serialize_strict_int("tooth_count", body.tooth_count, exclusive_min=0),
                "module_mm": _serialize_float("module_mm", body.module_mm, exclusive_min=0.0),
                "face_width_mm": _serialize_float("face_width_mm", body.face_width_mm, exclusive_min=0.0),
                "pressure_angle_deg": _serialize_float(
                    "pressure_angle_deg", body.pressure_angle_deg, exclusive_min=0.0
                ),
                "bore_diameter_mm": _serialize_float("bore_diameter_mm", body.bore_diameter_mm, min_value=0.0),
            },
            "placement": placement,
            "semantic_labels": semantic_labels,
        }
    if isinstance(body, RevolvedShaftBaseBody):
        if len(body.profile_points) < 3:
            raise ManifestValidationError(
                f"RevolvedShaftBaseBody requires at least 3 profile points, got {len(body.profile_points)}"
            )
        return {
            "id": body.id,
            "family": "revolved_shaft",
            "dimensions_mm": {
                "radius_mm": _serialize_float("radius_mm", body.radius_mm, exclusive_min=0.0),
                "height_mm": _serialize_float("height_mm", body.height_mm, exclusive_min=0.0),
            },
            "profile_points": [_serialize_point_2d(p) for p in body.profile_points],
            "placement": placement,
            "semantic_labels": semantic_labels,
        }

    raise ManifestValidationError(f"Unsupported base body type: {type(body).__name__}")


def _serialize_boolean_operation(op: BooleanOperation) -> dict[str, Any]:
    if op.operation not in ("union", "subtract", "intersect"):
        raise ManifestValidationError(f"Invalid boolean operation: {op.operation}")
    return {
        "id": op.id,
        "operation": op.operation,
        "target_body_id": op.target_body_id,
        "tool_body_id": op.tool_body_id,
        "result_body_id": op.result_body_id,
    }


def _serialize_feature(f: FeaturePlanFeature) -> dict[str, Any]:
    if isinstance(f, CircularThroughHoleFeature):
        return {
            "id": f.id,
            "family": "circular_through_hole",
            "target_body_id": f.target_body_id,
            "target_face": f.target_face,
            "target_selector": f.target_selector,
            "normal_axis": f.normal_axis,
            "diameter_mm": _serialize_float("diameter_mm", f.diameter_mm, exclusive_min=0.0),
            "depth_mm": _serialize_float("depth_mm", f.depth_mm, min_value=0.0),
            "center_x_mm": _serialize_float("center_x_mm", f.center_x_mm),
            "center_y_mm": _serialize_float("center_y_mm", f.center_y_mm),
            "placement_mode": f.placement_mode,
            "extent_type": f.extent_type,
        }
    if isinstance(f, RectangularThroughCutoutFeature):
        return {
            "id": f.id,
            "family": "rectangular_through_cutout",
            "target_body_id": f.target_body_id,
            "target_face": f.target_face,
            "target_selector": f.target_selector,
            "normal_axis": f.normal_axis,
            "width_mm": _serialize_float("width_mm", f.width_mm, exclusive_min=0.0),
            "height_mm": _serialize_float("height_mm", f.height_mm, exclusive_min=0.0),
            "depth_mm": _serialize_float("depth_mm", f.depth_mm, min_value=0.0),
            "center_x_mm": _serialize_float("center_x_mm", f.center_x_mm),
            "center_y_mm": _serialize_float("center_y_mm", f.center_y_mm),
            "placement_mode": f.placement_mode,
            "extent_type": f.extent_type,
        }
    if isinstance(f, SlotThroughCutoutFeature):
        return {
            "id": f.id,
            "family": "slot_through_cutout",
            "target_body_id": f.target_body_id,
            "target_face": f.target_face,
            "target_selector": f.target_selector,
            "normal_axis": f.normal_axis,
            "length_mm": _serialize_float("length_mm", f.length_mm, exclusive_min=0.0),
            "width_mm": _serialize_float("width_mm", f.width_mm, exclusive_min=0.0),
            "orientation_axis": f.orientation_axis,
            "depth_mm": _serialize_float("depth_mm", f.depth_mm, min_value=0.0),
            "center_x_mm": _serialize_float("center_x_mm", f.center_x_mm),
            "center_y_mm": _serialize_float("center_y_mm", f.center_y_mm),
            "placement_mode": f.placement_mode,
            "extent_type": f.extent_type,
        }
    if isinstance(f, RectangularExtrudedPadFeature):
        return {
            "id": f.id,
            "family": "rectangular_extruded_pad",
            "target_body_id": f.target_body_id,
            "target_face": f.target_face,
            "target_selector": f.target_selector,
            "normal_axis": f.normal_axis,
            "width_mm": _serialize_float("width_mm", f.width_mm, exclusive_min=0.0),
            "height_mm": _serialize_float("height_mm", f.height_mm, exclusive_min=0.0),
            "distance_mm": _serialize_float("distance_mm", f.distance_mm, exclusive_min=0.0),
            "center_x_mm": _serialize_float("center_x_mm", f.center_x_mm),
            "center_y_mm": _serialize_float("center_y_mm", f.center_y_mm),
            "placement_mode": f.placement_mode,
            "extent_type": f.extent_type,
        }
    if isinstance(f, RevolvedProfileFeature):
        if len(f.profile_points) < 3:
            raise ManifestValidationError(
                f"RevolvedProfileFeature requires at least 3 profile points, got {len(f.profile_points)}"
            )
        return {
            "id": f.id,
            "family": "revolved_profile",
            "target_body_id": f.target_body_id,
            "target_face": f.target_face,
            "target_selector": f.target_selector,
            "normal_axis": f.normal_axis,
            "profile_points": [_serialize_point_2d(p) for p in f.profile_points],
            "axis_start": _serialize_point_2d(f.axis_start),
            "axis_end": _serialize_point_2d(f.axis_end),
            "angle_deg": _serialize_float("angle_deg", f.angle_deg),
            "radial_depth_mm": _serialize_float("radial_depth_mm", f.radial_depth_mm, min_value=0.0),
            "profile_height_mm": _serialize_float("profile_height_mm", f.profile_height_mm, min_value=0.0),
            "center_x_mm": _serialize_float("center_x_mm", f.center_x_mm),
            "center_y_mm": _serialize_float("center_y_mm", f.center_y_mm),
            "placement_mode": f.placement_mode,
            "extent_type": f.extent_type,
        }
    if isinstance(f, ProfileCutoutFeature):
        if len(f.profile_points) < 3:
            raise ManifestValidationError(
                f"ProfileCutoutFeature requires at least 3 profile points, got {len(f.profile_points)}"
            )
        return {
            "id": f.id,
            "family": "profile_cutout",
            "target_body_id": f.target_body_id,
            "target_face": f.target_face,
            "target_selector": f.target_selector,
            "normal_axis": f.normal_axis,
            "profile_points": [_serialize_point_2d(p) for p in f.profile_points],
            "depth_mm": _serialize_float("depth_mm", f.depth_mm, min_value=0.0),
            "center_x_mm": _serialize_float("center_x_mm", f.center_x_mm),
            "center_y_mm": _serialize_float("center_y_mm", f.center_y_mm),
            "placement_mode": f.placement_mode,
            "extent_type": f.extent_type,
        }
    if isinstance(f, SweptProtrusionFeature):
        if len(f.cross_sections) < 1:
            raise ManifestValidationError("SweptProtrusionFeature requires at least 1 cross section")
        return {
            "id": f.id,
            "family": "swept_protrusion",
            "target_body_id": f.target_body_id,
            "target_face": f.target_face,
            "target_selector": f.target_selector,
            "normal_axis": f.normal_axis,
            "path": {
                "type": f.path.type,
                "radius_mm": _serialize_float("path.radius_mm", f.path.radius_mm),
                "angle_deg": _serialize_float("path.angle_deg", f.path.angle_deg),
            },
            "cross_sections": [
                {
                    "type": cs.type,
                    "diameter_mm": _serialize_float("cross_section.diameter_mm", cs.diameter_mm),
                    "width_mm": _serialize_float("cross_section.width_mm", cs.width_mm),
                    "height_mm": _serialize_float("cross_section.height_mm", cs.height_mm),
                    "profile_points": [_serialize_point_2d(p) for p in cs.profile_points],
                    "position": cs.position,
                }
                for cs in f.cross_sections
            ],
            "center_x_mm": _serialize_float("center_x_mm", f.center_x_mm),
            "center_y_mm": _serialize_float("center_y_mm", f.center_y_mm),
            "placement_mode": f.placement_mode,
            "extent_type": f.extent_type,
        }

    raise ManifestValidationError(f"Unsupported feature type: {type(f).__name__}")


def serialize_canonical_feature_plan(plan: FeaturePlan) -> dict[str, Any]:
    """Explicitly serialize a normalized FeaturePlan AST matching run-manifest-v1.schema.json."""
    if plan.lowering_strategy.runtime_route_change is not False:
        raise ManifestValidationError(
            f"lowering_strategy.runtime_route_change must be False, got {plan.lowering_strategy.runtime_route_change}"
        )
    return {
        "plan_version": plan.plan_version,
        "request_id": plan.request_id,
        "units": plan.units,
        "part": {
            "part_id": plan.part.part_id,
            "design_intent": plan.part.design_intent,
            "scope": plan.part.scope,
            "origin": plan.part.origin,
            "x_axis": plan.part.x_axis,
            "y_axis": plan.part.y_axis,
            "z_axis": plan.part.z_axis,
        },
        "base_body": _serialize_base_body(plan.base_body),
        "primitive_bodies": [_serialize_base_body(pb) for pb in plan.primitive_bodies],
        "boolean_operations": [_serialize_boolean_operation(op) for op in plan.boolean_operations],
        "features": [_serialize_feature(f) for f in plan.features],
        "lowering_strategy": {
            "status": plan.lowering_strategy.status,
            "preferred_current_target": plan.lowering_strategy.preferred_current_target,
            "runtime_route_change": plan.lowering_strategy.runtime_route_change,
        },
    }


def extract_manifest_stable_ids(plan: FeaturePlan) -> ManifestStableIds:
    """Extract and validate order-preserving stable identifiers from the FeaturePlan."""
    body_id_list: list[str] = [plan.base_body.id]
    for pb in plan.primitive_bodies:
        if pb.id not in body_id_list:
            body_id_list.append(pb.id)

    for op in plan.boolean_operations:
        if op.result_body_id not in body_id_list:
            body_id_list.append(op.result_body_id)

    return ManifestStableIds(
        part_id=plan.part.part_id,
        body_ids=tuple(body_id_list),
        boolean_operation_ids=tuple(op.id for op in plan.boolean_operations),
        feature_ids=tuple(f.id for f in plan.features),
    )


def compute_plan_fingerprint(feature_plan_dict: Mapping[str, Any]) -> str:
    """Compute the deterministic SHA-256 fingerprint of the canonical feature plan."""
    return sha256_canonical_json(feature_plan_dict)


KNOWN_DEFAULT_REASONS: frozenset[str] = frozenset(
    {
        "unknown_boolean_operation",
        "unknown_placement_mode",
        "unknown_face_alias",
        "unknown_slot_orientation",
        "unknown_sweep_path_type",
        "unknown_sweep_section_type",
        "unknown_sweep_section_position",
        "rectangular_pad_finite_extent",
        "revolved_profile_finite_extent",
        "revolved_profile_axis_span",
        "revolved_profile_dimensions",
        "revolved_shaft_profile_normalization",
        "canonical_subtractive_extent_alias",
        "through_all_extent_ignores_finite_depth",
        "canonical_target_selector_alias",
        "target_selector_resolved_face",
        "target_face_normal_axis",
        "design_intent_absent",
        "design_intent_scrubbed",
        "missing_extent",
        "default_depth",
        "default_applied",
        "inferred_default",
    }
)


def _normalize_default_reason(raw_reason: str) -> str:
    """Map a default reason to an explicit allowlisted reason slug."""
    if not isinstance(raw_reason, str):
        return "default_applied"

    cleaned = raw_reason.strip()
    if cleaned in KNOWN_DEFAULT_REASONS:
        return cleaned

    lower = cleaned.lower()
    if "boolean operation" in lower:
        return "unknown_boolean_operation"
    if "placement mode" in lower:
        return "unknown_placement_mode"
    if "face alias" in lower:
        return "unknown_face_alias"
    if "slot orientation" in lower:
        return "unknown_slot_orientation"
    if "sweep path" in lower:
        return "unknown_sweep_path_type"
    if "sweep cross-section position" in lower or "sweep section position" in lower:
        return "unknown_sweep_section_position"
    if "sweep section" in lower:
        return "unknown_sweep_section_type"
    if "resolved_face" in lower:
        return "target_selector_resolved_face"
    if "normal_axis" in lower:
        return "target_face_normal_axis"

    slug = re.sub(r"[^A-Za-z0-9_.-]", "_", cleaned).strip("_").lower()
    if slug in KNOWN_DEFAULT_REASONS:
        return slug

    return "default_applied"


# Collection bounds derived from authoritative geometry maxima:
# 128 max features * 16 max cross-sections * 4 attributes = 8,192
MAX_DEFAULT_ARRAY_ITEMS: int = 512
MAX_DEFAULT_MAPPING_PROPERTIES: int = 32
MAX_MANIFEST_DEFAULTS: int = 8192
MAX_MANIFEST_DIAGNOSTICS: int = 8192

ALLOWED_DEFAULT_GEOMETRY_KEYSETS: frozenset[frozenset[str]] = frozenset(
    {
        frozenset({"x_mm", "y_mm"}),
        frozenset({"start", "end"}),
        frozenset({"points", "axis"}),
    }
)


def _project_safe_default_value(val: Any, *, path: str, depth: int = 0) -> Any:
    """Safely project an applied default value, preserving strict types and bounds."""
    if depth > 4:
        raise ManifestValidationError(f"Default value at path '{path}' exceeds maximum nesting depth")

    if isinstance(val, bool):
        if depth > 0:
            raise ManifestValidationError(f"Default boolean at path '{path}' is not permitted in nested structures")
        return val
    if isinstance(val, int):
        return val
    if isinstance(val, float):
        if not math.isfinite(val):
            raise ManifestValidationError(f"Default value at path '{path}' contains non-finite float: {val}")
        return 0.0 if val == 0.0 else val
    if isinstance(val, str):
        if depth > 0:
            raise ManifestValidationError(f"Default string at path '{path}' is not permitted in nested structures")
        if not bool(SAFE_TOKEN_PATTERN.match(val)) or len(val) > 64:
            raise ManifestValidationError(f"Default value at path '{path}' contains invalid token: {val!r}")
        if (
            SENSITIVE_KEY_PATTERN.search(val)
            or FREE_TEXT_CREDENTIAL_PATTERN.search(val)
            or LOCAL_PATH_PATTERN.search(val)
        ):
            raise ManifestValidationError(
                f"Default value at path '{path}' contains forbidden credential or path: {val!r}"
            )
        return val
    if val is None:
        if depth > 0:
            raise ManifestValidationError(f"Default null at path '{path}' is not permitted in nested structures")
        return None
    if isinstance(val, (list, tuple)):
        if len(val) > MAX_DEFAULT_ARRAY_ITEMS:
            raise ManifestValidationError(
                f"Default array at path '{path}' exceeds maxItems {MAX_DEFAULT_ARRAY_ITEMS}: len={len(val)}"
            )
        arr: list[Any] = []
        for i, item in enumerate(val):
            if isinstance(item, bool):
                raise ManifestValidationError(
                    f"Default array item at path '{path}[{i}]' is boolean, expected number or mapping"
                )
            if isinstance(item, int):
                arr.append(item)
            elif isinstance(item, float):
                if not math.isfinite(item):
                    raise ManifestValidationError(f"Default array item at path '{path}[{i}]' is non-finite: {item}")
                arr.append(0.0 if item == 0.0 else item)
            elif isinstance(item, Mapping):
                arr.append(_project_safe_default_value(item, path=f"{path}[{i}]", depth=depth + 1))
            else:
                raise ManifestValidationError(
                    f"Unsupported default array item type at path '{path}[{i}]': {type(item).__name__}"
                )
        return arr
    if isinstance(val, Mapping):
        if len(val) > MAX_DEFAULT_MAPPING_PROPERTIES:
            raise ManifestValidationError(
                f"Default mapping at path '{path}' exceeds maxProperties {MAX_DEFAULT_MAPPING_PROPERTIES}: len={len(val)}"
            )
        keyset = frozenset(val.keys())
        if keyset not in ALLOWED_DEFAULT_GEOMETRY_KEYSETS:
            raise ManifestValidationError(
                f"Default mapping at path '{path}' has unrecognized geometry keys: {sorted(keyset)!r}"
            )
        proj_map: dict[str, Any] = {}
        for k, v in val.items():
            if not isinstance(k, str) or not SAFE_SLUG_PATTERN.match(k) or len(k) > 64:
                raise ManifestValidationError(f"Default mapping key '{k}' at path '{path}' is not a safe identifier")
            if SENSITIVE_KEY_PATTERN.search(k) or LOCAL_PATH_PATTERN.search(k):
                raise ManifestValidationError(
                    f"Default mapping key '{k}' at path '{path}' contains forbidden credential or path: {k!r}"
                )
            proj_map[k] = _project_safe_default_value(v, path=f"{path}.{k}", depth=depth + 1)
        return proj_map

    raise ManifestValidationError(f"Unsupported default value type at path '{path}': {type(val).__name__}")


def _project_safe_original_value(orig: Any, *, path: str) -> Any:
    """Safely project original_value, preserving safe types or returning redacted object."""
    if isinstance(orig, bool):
        return orig
    if isinstance(orig, int):
        return orig
    if isinstance(orig, float):
        if not math.isfinite(orig):
            return {"redacted": True, "sha256": sha256_canonical_json(str(orig))}
        return 0.0 if orig == 0.0 else orig
    if isinstance(orig, (list, tuple)):
        if len(orig) > MAX_DEFAULT_ARRAY_ITEMS:
            return {"redacted": True, "sha256": sha256_canonical_json(orig)}
        arr: list[Any] = []
        for item in orig:
            if isinstance(item, bool):
                return {"redacted": True, "sha256": sha256_canonical_json(orig)}
            if isinstance(item, int):
                arr.append(item)
            elif isinstance(item, float):
                if not math.isfinite(item):
                    return {"redacted": True, "sha256": sha256_canonical_json(orig)}
                arr.append(0.0 if item == 0.0 else item)
            elif isinstance(item, Mapping):
                if frozenset(item.keys()) not in ALLOWED_DEFAULT_GEOMETRY_KEYSETS:
                    return {"redacted": True, "sha256": sha256_canonical_json(orig)}
                try:
                    arr.append(_project_safe_default_value(item, path=path, depth=1))
                except Exception:
                    return {"redacted": True, "sha256": sha256_canonical_json(orig)}
            else:
                return {"redacted": True, "sha256": sha256_canonical_json(orig)}
        return arr
    if isinstance(orig, Mapping):
        if set(orig.keys()) == {"redacted", "sha256"} and orig.get("redacted") is True:
            return orig
        if frozenset(orig.keys()) not in ALLOWED_DEFAULT_GEOMETRY_KEYSETS:
            return {"redacted": True, "sha256": sha256_canonical_json(orig)}
        try:
            return _project_safe_default_value(orig, path=path, depth=0)
        except Exception:
            return {"redacted": True, "sha256": sha256_canonical_json(orig)}
    if isinstance(orig, str):
        is_credential = bool(SENSITIVE_KEY_PATTERN.search(orig) or FREE_TEXT_CREDENTIAL_PATTERN.search(orig))
        is_local_path = bool(LOCAL_PATH_PATTERN.search(orig))
        if len(orig) <= 64 and bool(SAFE_TOKEN_PATTERN.match(orig)) and not is_credential and not is_local_path:
            return orig
        return {"redacted": True, "sha256": sha256_canonical_json(orig)}

    return {"redacted": True, "sha256": sha256_canonical_json(orig)}


def project_manifest_defaults(defaults: Sequence[DefaultApplied]) -> tuple[dict[str, Any], ...]:
    """Safely project applied defaults into bounded schema-conforming records."""
    if len(defaults) > MAX_MANIFEST_DEFAULTS:
        raise ManifestValidationError(
            f"Applied defaults count {len(defaults)} exceeds maximum allowed count of {MAX_MANIFEST_DEFAULTS}"
        )
    projected: list[dict[str, Any]] = []
    for d in defaults:
        path = d.path
        if not isinstance(path, str) or not SAFE_PATH_PATTERN.match(path) or len(path) > 128:
            raise ManifestValidationError(f"Invalid default path: {path!r}")
        if (
            SENSITIVE_KEY_PATTERN.search(path)
            or FREE_TEXT_CREDENTIAL_PATTERN.search(path)
            or LOCAL_PATH_PATTERN.search(path)
        ):
            raise ManifestValidationError(f"Default path contains forbidden pattern: {path!r}")

        reason = _normalize_default_reason(d.reason)
        safe_val = _project_safe_default_value(d.value, path=path)

        item: dict[str, Any] = {
            "path": path,
            "value": safe_val,
            "reason": reason,
        }

        if d.original_value is not None:
            item["original_value"] = _project_safe_original_value(d.original_value, path=path)

        projected.append(item)

    return tuple(projected)


KNOWN_DIAGNOSTIC_MESSAGES: Mapping[str, str] = {
    "BORDERLINE_FIT": "Feature profile placement is close to the allowable boundary.",
    "CANONICAL_MULTI_HOLE_FAMILY": "Multiple circular through-holes accepted by canonical validation.",
    "CUTOUT_DOES_NOT_FIT": "Cutout profile exceeds target placement boundaries.",
    "GATE_POLICY_RELAXED": "Feature validation evaluated under relaxed capability-first policy.",
    "GATE_POLICY_SHADOW": "Feature validation evaluated under shadow gate policy.",
    "HOLE_DOES_NOT_FIT": "Hole profile exceeds target placement boundaries.",
    "INVALID_CUT_DEPTH": "Cut depth exceeds parent body thickness.",
    "INVALID_DIMENSION": "Dimension value violates boundary constraints.",
    "INVALID_FEATURES": "Features definition is invalid.",
    "INVALID_FEATURE_PLAN": "Feature plan payload is invalid.",
    "INVALID_GEOMETRY": "Geometry definition is degenerate or self-intersecting.",
    "INVALID_PRIMITIVE_BODIES": "Primitive bodies definition is invalid.",
    "INVALID_BOOLEAN_OPERATIONS": "Boolean operations definition is invalid.",
    "INVALID_REVOLVE_PROFILE": "Revolved profile definition is invalid.",
    "INVALID_SWEPT_PROTRUSION": "Swept protrusion definition is invalid.",
    "SLOT_DOES_NOT_FIT": "Slot profile exceeds target placement boundaries.",
    "SWEEP_SELF_INTERSECTS": "Swept path or profile self-intersects.",
    "UNKNOWN_BOOLEAN_OPERATION": "Unrecognized boolean operation fell back to default union.",
    "UNKNOWN_FACE_ALIAS": "Unrecognized face alias fell back to default sketch plane.",
    "UNKNOWN_FEATURE_TARGET": "Target body identifier could not be resolved.",
    "UNKNOWN_PLACEMENT_MODE": "Unrecognized placement mode fell back to default absolute placement.",
    "UNKNOWN_SLOT_ORIENTATION": "Unrecognized slot orientation fell back to default horizontal orientation.",
    "UNKNOWN_SWEEP_PATH_TYPE": "Unrecognized sweep path type fell back to default.",
    "UNKNOWN_SWEEP_SECTION_POSITION": "Unrecognized sweep section position fell back to default.",
    "UNKNOWN_SWEEP_SECTION_TYPE": "Unrecognized sweep section type fell back to default.",
    "UNSUPPORTED_BASE_BODY": "Base body family is unsupported for requested feature.",
    "UNSUPPORTED_FEATURE_ON_BASE_BODY": "Feature is unsupported on specified base body.",
    "UNSUPPORTED_SLOT_ORIENTATION": "Slot orientation is unsupported for target face.",
    "UNSUPPORTED_SPHERE_FACE": "Placement on sphere face is not supported.",
    "UNSUPPORTED_TARGET_FACE": "Target face is unsupported for requested feature.",
}
GENERIC_DIAGNOSTIC_MESSAGE: str = "A non-fatal diagnostic warning was recorded."


def project_manifest_diagnostics(diagnostics: Sequence[ValidationDiagnostic]) -> tuple[dict[str, Any], ...]:
    """Safely project validation diagnostics into bounded schema-conforming records."""
    if len(diagnostics) > MAX_MANIFEST_DIAGNOSTICS:
        raise ManifestValidationError(
            f"Validation diagnostics count {len(diagnostics)} exceeds maximum allowed count of {MAX_MANIFEST_DIAGNOSTICS}"
        )
    projected: list[dict[str, Any]] = []
    for diag in diagnostics:
        severity = diag.severity
        if severity not in ("info", "warning", "error"):
            severity = "warning"

        code = diag.code
        if not re.match(r"^[A-Za-z0-9_.-]+$", code) or len(code) > 128:
            clean_code = re.sub(r"[^A-Za-z0-9_.-]", "_", code).strip("_")
            code = clean_code[:128] if clean_code else "DIAGNOSTIC"
        if (
            SENSITIVE_KEY_PATTERN.search(code)
            or FREE_TEXT_CREDENTIAL_PATTERN.search(code)
            or LOCAL_PATH_PATTERN.search(code)
        ):
            code = "DIAGNOSTIC"

        raw_path = diag.path or "root"
        path = "root" if not SAFE_PATH_PATTERN.match(raw_path) or len(raw_path) > 128 else raw_path
        if (
            SENSITIVE_KEY_PATTERN.search(path)
            or FREE_TEXT_CREDENTIAL_PATTERN.search(path)
            or LOCAL_PATH_PATTERN.search(path)
        ):
            path = "root"

        clean_msg = KNOWN_DIAGNOSTIC_MESSAGES.get(code, GENERIC_DIAGNOSTIC_MESSAGE)

        projected.append(
            {
                "severity": severity,
                "code": code,
                "path": path,
                "message": clean_msg,
            }
        )

    return tuple(projected)


@functools.cache
def _get_feature_plan_sub_validator() -> Draft202012Validator:
    """Compile and cache Draft202012Validator for the feature_plan section."""
    root_schema = load_run_manifest_schema()
    sub_schema = {
        "$schema": root_schema.get("$schema"),
        "$defs": root_schema.get("$defs", {}),
        **root_schema["properties"]["feature_plan"],
    }
    Draft202012Validator.check_schema(sub_schema)
    return Draft202012Validator(sub_schema)


def validate_serialized_feature_plan(serialized_plan: Mapping[str, Any]) -> None:
    """Validate a serialized feature plan mapping against the canonical JSON Schema."""
    validator = _get_feature_plan_sub_validator()
    try:
        validator.validate(dict(serialized_plan))
    except ValidationError as exc:
        raise ManifestValidationError("Serialized FeaturePlan failed schema validation") from exc


def prepare_manifest_data(
    plan: FeaturePlan,
    *,
    provenance_kind: str,
    source_id: str,
    gate_mode: str,
    request_kind: str,
    prompt_sha256: str | None = None,
) -> PreparedManifestData:
    """Construct a strictly validated PreparedManifestData instance before CAD runtime acquisition."""
    validate_identifier("source_id", source_id)
    hardened_plan = harden_plan_metadata(plan, request_kind)
    serialized_plan = serialize_canonical_feature_plan(hardened_plan)
    validate_serialized_feature_plan(serialized_plan)

    stable_ids = extract_manifest_stable_ids(hardened_plan)
    plan_sha256 = compute_plan_fingerprint(serialized_plan)

    defaults_applied = project_manifest_defaults(hardened_plan.defaults_applied)
    diagnostics = project_manifest_diagnostics(hardened_plan.validation_diagnostics)
    resolved_version = resolve_engine_version()

    return PreparedManifestData(
        feature_plan=serialized_plan,
        stable_ids=stable_ids,
        plan_sha256=plan_sha256,
        prompt_sha256=prompt_sha256,
        defaults_applied=defaults_applied,
        diagnostics=diagnostics,
        provenance_kind=provenance_kind,
        source_id=source_id,
        gate_mode=gate_mode,
        request_kind=request_kind,
        engine_version=resolved_version,
    )


__all__ = [
    "MAX_MANIFEST_DEFAULTS",
    "MAX_MANIFEST_DIAGNOSTICS",
    "compute_plan_fingerprint",
    "extract_manifest_stable_ids",
    "generate_structural_design_intent",
    "harden_plan_metadata",
    "prepare_manifest_data",
    "project_manifest_defaults",
    "project_manifest_diagnostics",
    "serialize_canonical_feature_plan",
    "validate_identifier",
    "validate_serialized_feature_plan",
]
