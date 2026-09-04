"""Feature plan lowering orchestrator and AST-to-wire intermediate compiler.

Translates validated canonical FeaturePlan ASTs into driver-agnostic 2D/3D wire payloads
conforming strictly to contract_version "1.0".

Invariants:
    1. Zero I/O Purity (NFR-1): Pure computational geometry with zero COM, CAD kernel, or network imports.
    2. Deterministic Cryptographic Fingerprints (NFR-2): Canonical JSON serialization with allow_nan=False,
       IEEE-754 signed zero normalization (-0.0 -> 0.0), and SHA-256 entity fingerprinting.
    3. Clean Architecture: Canonical contract_version "1.0" with pure domain lowering.
    4. CSG Context Tracking: Formally updates active_body_ref across multi-body boolean operation chains.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, NoReturn

from geometry.face_context import resolve_face_context
from geometry.gate_policy import GatePolicyMode
from geometry.lowering_sweep import lower_swept_protrusion
from geometry.lowering_wire_mapping import (
    DEFAULT_ABSTRACT_CUT_DIRECTION,
    feature_human_label,
    map_face_uv_to_sketch_wire,
    profile_feature_patch_for_feature,
    profile_feature_payload_for_feature,
    profile_geometry_for_feature,
    profile_human_label,
    profile_payload_for_feature,
    sketch_human_label,
)
from geometry.plan_geometry import (
    _base_body_human_label,
    _base_body_shape,
)
from geometry.plan_models import (
    BooleanOperation,
    CircularThroughHoleFeature,
    FeaturePlan,
    FeaturePlanBaseBody,
    FeaturePlanFeature,
    FeaturePlanValidationError,
    ProfileCutoutFeature,
    RectangularExtrudedPadFeature,
    RectangularThroughCutoutFeature,
    RevolvedProfileFeature,
    SlotThroughCutoutFeature,
    SpurGearBaseBody,
    SweptProtrusionFeature,
    ValidationDiagnostic,
)
from geometry.plan_validation import validate_feature_plan


def _clean_float(val: float) -> float:
    """Snap sub-picometer floating point residual noise to 0.0 and eliminate -0.0."""
    if abs(val) < 1e-12:
        return 0.0
    return round(val, 9) + 0.0


def _stable_fingerprint(payload: object) -> str:
    """Calculate the deterministic SHA-256 fingerprint of a canonical payload dictionary."""
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _ref_slug(ref_id: str) -> str:
    """Sanitize reference IDs for idempotency keys."""
    return ref_id.replace(".", "-")


def _body_placement_payload(base_body: FeaturePlanBaseBody) -> dict[str, float]:
    """Extract and sanitize body placement offset coordinates."""
    placement = getattr(base_body, "placement", None)
    if placement is None:
        return {"x_mm": 0.0, "y_mm": 0.0, "z_mm": 0.0}
    return {
        "x_mm": _clean_float(float(placement.x_mm)),
        "y_mm": _clean_float(float(placement.y_mm)),
        "z_mm": _clean_float(float(placement.z_mm)),
    }


def _boolean_op_name(operation: BooleanOperation) -> str:
    """Map AST boolean operation family to canonical patch operation name."""
    return {
        "union": "boolean_union",
        "subtract": "boolean_subtract",
        "intersect": "boolean_intersect",
    }[operation.operation]


def _boolean_payload(operation: BooleanOperation) -> dict[str, Any]:
    """Generate canonical state dictionary for a boolean operation entity."""
    return {
        "kind": _boolean_op_name(operation),
        "target_body_ref": operation.target_body_id,
        "tool_body_ref": operation.tool_body_id,
    }


def _boolean_patch(operation: BooleanOperation, *, index: int) -> dict[str, Any]:
    """Generate execution patch for a CSG boolean operation."""
    op = _boolean_op_name(operation)
    return {
        "op": op,
        "patch_id": f"patch.boolean.{index}",
        "replay_policy": {
            "idempotency_key": f"feature-plan-{op}-{index}",
            "mode": "fail_on_drift",
        },
        "target_body_ref": operation.target_body_id,
        "tool_body_ref": operation.tool_body_id,
        "result_body_ref": operation.result_body_id,
    }


def _boolean_human_label(operation: BooleanOperation) -> str:
    """Generate clean description for a boolean operation entity."""
    return (
        f"Canonical boolean {operation.operation} result {operation.result_body_id} "
        f"from {operation.target_body_id} and {operation.tool_body_id}"
    )


def _entity(
    *,
    ref_id: str,
    kind: str,
    fingerprint: str,
    human_label: str,
    depends_on_refs: list[str] | None = None,
) -> dict[str, Any]:
    """Construct a canonical state entity dictionary."""
    return {
        "reference": {
            "ref_id": ref_id,
            "kind": kind,
            "fingerprint": fingerprint,
            "human_label": human_label,
        },
        "depends_on_refs": depends_on_refs or [],
    }


def _reject(code: str, message: str, *, path: str | None = None) -> NoReturn:
    """Raise a structured FeaturePlanValidationError."""
    raise FeaturePlanValidationError(
        ValidationDiagnostic(
            severity="error",
            code=code,
            message=message,
            path=path,
        )
    )


def lower_validated_feature_plan_to_payload(validated: FeaturePlan) -> dict[str, Any]:
    """Lower a validated canonical FeaturePlan AST into a contract_version 1.0 wire payload."""
    primitive_bodies = validated.primitive_bodies or (validated.base_body,)
    primitive_body_by_id = {body.id: body for body in primitive_bodies}
    features_by_body_id: dict[str, list[FeaturePlanFeature]] = {body.id: [] for body in primitive_bodies}

    for feature in validated.features:
        target_id = getattr(feature, "target_body_id", validated.base_body.id)
        if target_id not in primitive_body_by_id:
            _reject(
                "UNKNOWN_FEATURE_TARGET", f"Unknown feature target body '{target_id}'.", path="features.target_body_id"
            )
        features_by_body_id.setdefault(target_id, []).append(feature)

    entities: list[dict[str, Any]] = []
    patches: list[dict[str, Any]] = []
    active_body_ref = validated.base_body.id
    active_sketch_ref: str | None = None
    active_profile_ref: str | None = None
    feature_index = 0
    bore_counter = 0

    # 1. Lower Primitive Solid Bodies
    for body in primitive_bodies:
        shape = _base_body_shape(body)
        placement = _body_placement_payload(body)
        body_payload = {"kind": "body", "shape": shape, "origin_offset_mm": placement}

        entities.append(
            _entity(
                ref_id=body.id,
                kind="body",
                fingerprint=_stable_fingerprint(body_payload),
                human_label=_base_body_human_label(body),
            )
        )
        patches.append(
            {
                "op": "ensure_primitive_body",
                "patch_id": f"patch.{body.id}",
                "replay_policy": {
                    "idempotency_key": f"feature-plan-ensure-{_ref_slug(body.id)}",
                    "mode": "verify_equivalent",
                },
                "body_ref": body.id,
                "shape": shape,
                "origin_offset_mm": placement,
            }
        )

        active_body_ref = body.id

        # 2. Lower Spur Gear Center Bore (if present on base gear)
        if isinstance(body, SpurGearBaseBody) and body.bore_diameter_mm > 0.0:
            bore_counter += 1
            sketch_ref = f"sketch.gear-bore.{bore_counter}"
            profile_ref = f"profile.gear-bore.{bore_counter}"
            feature_ref = f"feature.gear-bore.{bore_counter}"
            bore_ctx = resolve_face_context("+Z", body)
            placement = _body_placement_payload(body)
            bore_origin_offset = {
                "x_mm": placement["x_mm"] + bore_ctx.origin_offset_mm.get("x_mm", 0.0) + 0.0,
                "y_mm": placement["y_mm"] + bore_ctx.origin_offset_mm.get("y_mm", 0.0) + 0.0,
                "z_mm": placement["z_mm"] + bore_ctx.origin_offset_mm.get("z_mm", 0.0) + 0.0,
            }
            active_sketch_ref = sketch_ref
            active_profile_ref = profile_ref

            bore_center_xs, bore_center_ys = map_face_uv_to_sketch_wire(0.0, 0.0, "+Z", body)
            bore_center = {"x_mm": bore_center_xs, "y_mm": bore_center_ys}

            bore_profile_payload = {
                "kind": "circle",
                "sketch_ref": sketch_ref,
                "center": bore_center,
                "radius_mm": (body.bore_diameter_mm / 2.0) + 0.0,
            }
            bore_feature_payload = {
                "kind": "cut_hole",
                "body_ref": body.id,
                "profile_ref": profile_ref,
                "through_all": True,
            }

            entities.extend(
                [
                    _entity(
                        ref_id=sketch_ref,
                        kind="sketch",
                        fingerprint=_stable_fingerprint(
                            {
                                "kind": "sketch",
                                "plane": "XY",
                                "origin_offset_mm": bore_origin_offset,
                                "body_ref": body.id,
                            }
                        ),
                        depends_on_refs=[body.id],
                        human_label="Canonical gear bore sketch",
                    ),
                    _entity(
                        ref_id=profile_ref,
                        kind="profile",
                        fingerprint=_stable_fingerprint(bore_profile_payload),
                        depends_on_refs=[sketch_ref],
                        human_label="Canonical circular bore profile for concept spur gear",
                    ),
                    _entity(
                        ref_id=feature_ref,
                        kind="feature",
                        fingerprint=_stable_fingerprint(bore_feature_payload),
                        depends_on_refs=[body.id, profile_ref],
                        human_label="Semantic concept spur gear center bore",
                    ),
                ]
            )
            patches.extend(
                [
                    {
                        "op": "ensure_sketch",
                        "patch_id": f"patch.gear-bore.sketch.{bore_counter}",
                        "replay_policy": {
                            "idempotency_key": f"feature-plan-ensure-sketch-gear-bore-{bore_counter}",
                            "mode": "verify_equivalent",
                        },
                        "sketch_ref": sketch_ref,
                        "body_ref": body.id,
                        "plane": "XY",
                        "origin_offset_mm": bore_origin_offset,
                    },
                    {
                        "op": "ensure_profile",
                        "patch_id": f"patch.gear-bore.profile.{bore_counter}",
                        "replay_policy": {
                            "idempotency_key": f"feature-plan-ensure-profile-gear-bore-{bore_counter}",
                            "mode": "ensure_present",
                        },
                        "profile_ref": profile_ref,
                        "sketch_ref": sketch_ref,
                        "geometry": {
                            "kind": "circle",
                            "center": bore_center,
                            "radius_mm": (body.bore_diameter_mm / 2.0) + 0.0,
                        },
                    },
                    {
                        "op": "cut_hole",
                        "patch_id": f"patch.gear-bore.cut.{bore_counter}",
                        "replay_policy": {
                            "idempotency_key": f"feature-plan-cut-hole-gear-bore-{bore_counter}",
                            "mode": "fail_on_drift",
                        },
                        "body_ref": body.id,
                        "profile_ref": profile_ref,
                        "result_ref": feature_ref,
                        "through_all": True,
                        "cut_direction": DEFAULT_ABSTRACT_CUT_DIRECTION,
                        "sketch_plane": "XY",
                        "origin_offset_mm": bore_origin_offset,
                        "u_axis": [1.0, 0.0, 0.0],
                        "v_axis": [0.0, 1.0, 0.0],
                        "normal_vector": [0.0, 0.0, 1.0],
                        "cut_vector": [0.0, 0.0, -1.0],
                    },
                ]
            )

        # 3. Lower Body Features
        for feature in features_by_body_id.get(body.id, []):
            feature_index += 1
            if not isinstance(
                feature,
                CircularThroughHoleFeature
                | RectangularThroughCutoutFeature
                | SlotThroughCutoutFeature
                | RectangularExtrudedPadFeature
                | RevolvedProfileFeature
                | ProfileCutoutFeature
                | SweptProtrusionFeature,
            ):
                _reject(
                    "UNSUPPORTED_FEATURE_FAMILY",
                    f"Unsupported feature family '{getattr(feature, 'family', 'unknown')}'.",
                )

            target_body = primitive_body_by_id.get(feature.target_body_id)
            if target_body is None:
                _reject("UNKNOWN_FEATURE_TARGET", f"Unknown feature target body '{feature.target_body_id}'.")

            # Swept Protrusion Feature Dispatch
            if isinstance(feature, SweptProtrusionFeature):
                sweep_result = lower_swept_protrusion(
                    validated,
                    feature,
                    feature_index,
                    _stable_fingerprint,
                    target_body=target_body,
                )
                entities.extend(sweep_result.entities)
                patches.extend(sweep_result.patches)
                active_body_ref = feature.target_body_id
                active_sketch_ref = sweep_result.last_sketch_ref
                active_profile_ref = sweep_result.last_profile_ref
                continue

            # Standard Profile Features
            sketch_ref = f"sketch.{feature_index}"
            profile_ref = f"profile.{feature_index}"
            active_sketch_ref = sketch_ref
            active_profile_ref = profile_ref
            suffix = f"{feature.family}-{feature_index}"

            ctx = resolve_face_context(feature.target_face or "+Z", target_body, feature)
            sketch_plane = ctx.sketch_plane
            placement = _body_placement_payload(target_body)
            sketch_origin_offset = {
                "x_mm": placement["x_mm"] + ctx.origin_offset_mm.get("x_mm", 0.0) + 0.0,
                "y_mm": placement["y_mm"] + ctx.origin_offset_mm.get("y_mm", 0.0) + 0.0,
                "z_mm": placement["z_mm"] + ctx.origin_offset_mm.get("z_mm", 0.0) + 0.0,
            }
            sketch_payload = {
                "kind": "sketch",
                "plane": sketch_plane,
                "origin_offset_mm": sketch_origin_offset,
                "body_ref": feature.target_body_id,
            }
            profile_payload = profile_payload_for_feature(feature, sketch_ref=sketch_ref, base_body=target_body)
            feature_payload = profile_feature_payload_for_feature(
                feature,
                body_ref=feature.target_body_id,
                profile_ref=profile_ref,
                base_body=target_body,
            )

            entities.extend(
                [
                    _entity(
                        ref_id=sketch_ref,
                        kind="sketch",
                        fingerprint=_stable_fingerprint(sketch_payload),
                        depends_on_refs=[feature.target_body_id],
                        human_label=sketch_human_label(feature),
                    ),
                    _entity(
                        ref_id=profile_ref,
                        kind="profile",
                        fingerprint=_stable_fingerprint(profile_payload),
                        depends_on_refs=[sketch_ref],
                        human_label=profile_human_label(feature),
                    ),
                    _entity(
                        ref_id=feature.id,
                        kind="feature",
                        fingerprint=_stable_fingerprint(feature_payload),
                        depends_on_refs=[feature.target_body_id, profile_ref],
                        human_label=feature_human_label(feature),
                    ),
                ]
            )
            patches.extend(
                [
                    {
                        "op": "ensure_sketch",
                        "patch_id": f"patch.sketch.{feature_index}",
                        "replay_policy": {
                            "idempotency_key": f"feature-plan-ensure-sketch-{suffix}",
                            "mode": "verify_equivalent",
                        },
                        "sketch_ref": sketch_ref,
                        "body_ref": feature.target_body_id,
                        "plane": sketch_plane,
                        "origin_offset_mm": sketch_origin_offset,
                    },
                    {
                        "op": "ensure_profile",
                        "patch_id": f"patch.profile.{feature_index}",
                        "replay_policy": {
                            "idempotency_key": f"feature-plan-ensure-profile-{suffix}",
                            "mode": "ensure_present",
                        },
                        "profile_ref": profile_ref,
                        "sketch_ref": sketch_ref,
                        "geometry": profile_geometry_for_feature(feature, base_body=target_body),
                    },
                    profile_feature_patch_for_feature(
                        feature,
                        index=feature_index,
                        suffix=suffix,
                        body_ref=feature.target_body_id,
                        profile_ref=profile_ref,
                        base_body=target_body,
                        cut_direction=DEFAULT_ABSTRACT_CUT_DIRECTION,
                        sketch_plane=ctx.sketch_plane,
                        origin_offset_mm=sketch_origin_offset,
                        u_axis=ctx.u_axis,
                        v_axis=ctx.v_axis,
                        normal_vector=ctx.normal_vector,
                    ),
                ]
            )
            active_body_ref = feature.target_body_id

    # 4. Lower CSG Boolean Operations
    for index, operation in enumerate(validated.boolean_operations, start=1):
        active_body_ref = operation.result_body_id
        payload = _boolean_payload(operation)
        entities.append(
            _entity(
                ref_id=operation.result_body_id,
                kind="body",
                fingerprint=_stable_fingerprint(payload),
                depends_on_refs=[operation.target_body_id, operation.tool_body_id],
                human_label=_boolean_human_label(operation),
            )
        )
        patches.append(_boolean_patch(operation, index=index))

    # 5. Assemble Payload Envelope
    return {
        "contract_version": "1.0",
        "request_id": validated.request_id,
        "kind": "semantic_patch_sequence",
        "unit": "mm",
        "base_state_revision": 0,
        "capabilities": {"allow_set_active_context": True},
        "canonical_state": {
            "state_revision": 0,
            "entities": entities,
            "active_context": {
                "active_body_ref": active_body_ref,
                "active_sketch_ref": active_sketch_ref,
                "active_profile_ref": active_profile_ref,
            },
            "remaps": [],
        },
        "patches": patches,
        "metadata": {
            "source": "llm",
            "label": f"canonical feature plan: {validated.part.design_intent}"[:128],
            "diagnostics": [d.to_dict() for d in validated.validation_diagnostics],
            "defaults_applied": [d.to_dict() for d in validated.defaults_applied],
        },
    }


def lower_feature_plan_to_payload(
    plan: FeaturePlan,
    *,
    mode: GatePolicyMode | None = None,
) -> dict[str, Any]:
    """Validate a FeaturePlan AST and lower it into a canonical contract_version 1.0 payload."""
    validated = validate_feature_plan(plan, mode=mode)
    return lower_validated_feature_plan_to_payload(validated)


__all__ = [
    "lower_feature_plan_to_payload",
    "lower_validated_feature_plan_to_payload",
]
