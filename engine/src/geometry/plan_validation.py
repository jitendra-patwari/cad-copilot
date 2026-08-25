"""AST plan validation pipeline and candidate retry orchestration for CAD Copilot.

This module provides bounded AST validation, deterministic defaulting, and gate policy
propagation for canonical feature plans with zero I/O and zero CAD kernel dependencies (NFR-1, NFR-4).
"""

from __future__ import annotations

from dataclasses import replace

from geometry.gate_policy import GatePolicyMode, current_gate_policy_mode
from geometry.plan_models import (
    CANONICAL_PLAN_VERSION,
    DEFAULT_EDGE_MARGIN_MM,
    CircularThroughHoleFeature,
    DefaultApplied,
    FeaturePlan,
    FeaturePlanBaseBody,
    FeaturePlanFeature,
    ProfileCutoutFeature,
    RectangularExtrudedPadFeature,
    RectangularThroughCutoutFeature,
    RevolvedProfileFeature,
    RevolvedShaftBaseBody,
    SlotThroughCutoutFeature,
    SweptProtrusionFeature,
    ValidationDiagnostic,
)
from geometry.plan_parser import (
    MAX_FEATURES,
)
from geometry.validators import (
    _effective_edge_margin_mm,
    _normalize_revolved_axis_span,
    _normalize_revolved_profile_from_dimensions,
    _normalize_revolved_shaft_profile,
    _normalize_slot_orientation,
    _normalize_subtractive_extent,
    _reject,
    _require_finite,
    _require_positive,
    _resolve_feature_target,
    _validate_base_body,
    _validate_composition_layer,
    _validate_feature_supported_on_base_body,
    _validate_hole_fit,
    _validate_hole_separation,
    _validate_identifier_not_reserved,
    _validate_profile_cutout_fit,
    _validate_rectangular_cutout_fit,
    _validate_rectangular_pad_fit,
    _validate_revolved_profile,
    _validate_revolved_shaft_profile,
    _validate_slot_cutout_fit,
    _validate_swept_protrusion,
)


def validate_feature_plan(
    plan: FeaturePlan,
    *,
    edge_margin_mm: float = DEFAULT_EDGE_MARGIN_MM,
    mode: GatePolicyMode | None = None,
) -> FeaturePlan:
    """Validate, normalize, and populate diagnostics for a canonical feature plan AST.

    Args:
        plan: The immutable FeaturePlan AST to validate and normalize.
        edge_margin_mm: Minimum clearance margin (mm) required between features and face boundaries.
        mode: Explicit gate policy mode ('strict', 'capability_first', or 'shadow').

    Returns:
        A new immutable FeaturePlan with normalized attributes, defaults, and diagnostics.

    Raises:
        FeaturePlanValidationError: If the plan violates syntactic, geometric, or DoS safety limits.
    """
    # Strict DoS defense: prevent resource/CPU exhaustion by limiting excessive inputs (CWE-400)
    if len(plan.features) > MAX_FEATURES:
        _reject(
            "EXCESSIVE_FEATURE_COUNT",
            f"Feature count {len(plan.features)} exceeds maximum allowed limit of {MAX_FEATURES} to prevent DoS.",
            path="features",
        )

    defaults = list(plan.defaults_applied)
    diagnostics = list(plan.validation_diagnostics)
    active_edge_margin_mm = _effective_edge_margin_mm(edge_margin_mm)
    active_mode = current_gate_policy_mode(mode)

    if plan.plan_version != CANONICAL_PLAN_VERSION:
        _reject(
            "UNSUPPORTED_PLAN_VERSION",
            f"Feature plan version '{plan.plan_version}' is not supported by this contract.",
            path="plan_version",
        )
    if plan.units != "mm":
        _reject(
            "UNSUPPORTED_UNITS",
            "Canonical feature-plan contract only supports millimeters.",
            path="units",
        )
    if plan.part.scope != "single_part":
        _reject(
            "UNSUPPORTED_SCOPE",
            "Canonical feature-plan contract only supports single_part scope.",
            path="part.scope",
        )
    if plan.lowering_strategy.status != "canonical" or plan.lowering_strategy.runtime_route_change:
        _reject(
            "UNSUPPORTED_LOWERING_STRATEGY",
            "Canonical feature-plan contract must remain canonical-plan scoped and must not request a runtime route change.",
            path="lowering_strategy",
        )

    _validate_identifier_not_reserved(plan.base_body.id, "base_body.id")
    _validate_base_body(plan.base_body, diagnostics=diagnostics, mode=active_mode)
    base_body = plan.base_body
    if isinstance(base_body, RevolvedShaftBaseBody):
        base_body = _normalize_revolved_shaft_profile(base_body, defaults=defaults, path="base_body")
        _validate_revolved_shaft_profile(base_body, path="base_body", diagnostics=diagnostics, mode=active_mode)

    primitive_bodies, boolean_operations = _validate_composition_layer(
        plan,
        diagnostics=diagnostics,
        mode=active_mode,
    )

    # Ensure normalized shaft geometry is preserved in primitive_bodies for lowering
    normalized_primitive_bodies: list[FeaturePlanBaseBody] = []
    for idx, body in enumerate(primitive_bodies):
        if body.id == base_body.id and isinstance(base_body, RevolvedShaftBaseBody):
            normalized_primitive_bodies.append(base_body)
        elif isinstance(body, RevolvedShaftBaseBody):
            norm_body = _normalize_revolved_shaft_profile(body, defaults=defaults, path=f"primitive_bodies[{idx}]")
            _validate_revolved_shaft_profile(
                norm_body, path=f"primitive_bodies[{idx}]", diagnostics=diagnostics, mode=active_mode
            )
            normalized_primitive_bodies.append(norm_body)
        else:
            normalized_primitive_bodies.append(body)
    primitive_bodies = tuple(normalized_primitive_bodies)

    primitive_body_by_id = {body.id: body for body in primitive_bodies}
    normalized_features: list[FeaturePlanFeature] = []
    hole_specs: list[tuple[str, str, str, float, float, float]] = []

    seen_entity_ids: set[str] = {body.id for body in primitive_bodies}
    seen_entity_ids.update(op.id for op in boolean_operations)
    seen_entity_ids.update(op.result_body_id for op in boolean_operations)

    for index, feature in enumerate(plan.features):
        path = f"features[{index}]"
        _validate_identifier_not_reserved(feature.id, f"{path}.id")
        if feature.id in seen_entity_ids:
            _reject(
                "DUPLICATE_FEATURE_ID",
                f"Feature id '{feature.id}' is duplicated across plan entities.",
                path=f"{path}.id",
            )
        seen_entity_ids.add(feature.id)

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
                f"Unsupported feature family '{feature.family}'.",
                path=f"{path}.family",
            )

        target_body = primitive_body_by_id.get(feature.target_body_id)
        if target_body is None:
            _reject(
                "UNKNOWN_FEATURE_TARGET",
                f"Feature '{feature.id}' targets unknown primitive body '{feature.target_body_id}'.",
                path=f"{path}.target.body_id",
            )

        _validate_feature_supported_on_base_body(
            feature,
            target_body,
            path=path,
            diagnostics=diagnostics,
            mode=active_mode,
        )

        if feature.placement_mode not in {"face_local_center", "face_local_offset"}:
            _reject(
                "UNSUPPORTED_PLACEMENT",
                "Only center and explicit face-local X/Y placement are supported.",
                path=path,
            )

        _require_finite(feature.center_x_mm, f"{path}.placement.center_uv_mm.u")
        _require_finite(feature.center_y_mm, f"{path}.placement.center_uv_mm.v")

        normalized_feature = _resolve_feature_target(feature, base_body=target_body, defaults=defaults, path=path)

        if isinstance(normalized_feature, CircularThroughHoleFeature):
            _require_positive(normalized_feature.diameter_mm, f"{path}.dimensions_mm.diameter")
            normalized_feature = _normalize_subtractive_extent(
                normalized_feature,
                base_body=target_body,
                defaults=defaults,
                path=path,
                diagnostics=diagnostics,
                mode=active_mode,
            )
            radius_mm = normalized_feature.diameter_mm / 2.0
            _validate_hole_fit(
                normalized_feature,
                radius_mm=radius_mm,
                base_body=target_body,
                edge_margin_mm=active_edge_margin_mm,
                diagnostics=diagnostics,
                path=path,
                mode=active_mode,
            )
            hole_specs.append(
                (
                    normalized_feature.id,
                    normalized_feature.target_body_id,
                    normalized_feature.target_face or "+Z",
                    normalized_feature.center_x_mm,
                    normalized_feature.center_y_mm,
                    radius_mm,
                )
            )
        elif isinstance(normalized_feature, RectangularThroughCutoutFeature):
            _require_positive(normalized_feature.width_mm, f"{path}.dimensions_mm.width")
            _require_positive(normalized_feature.height_mm, f"{path}.dimensions_mm.height")
            normalized_feature = _normalize_subtractive_extent(
                normalized_feature,
                base_body=target_body,
                defaults=defaults,
                path=path,
                diagnostics=diagnostics,
                mode=active_mode,
            )
            _validate_rectangular_cutout_fit(
                normalized_feature,
                base_body=target_body,
                edge_margin_mm=active_edge_margin_mm,
                diagnostics=diagnostics,
                path=path,
                mode=active_mode,
            )
        elif isinstance(normalized_feature, ProfileCutoutFeature):
            normalized_feature = _normalize_subtractive_extent(
                normalized_feature,
                base_body=target_body,
                defaults=defaults,
                path=path,
                diagnostics=diagnostics,
                mode=active_mode,
            )
            _validate_profile_cutout_fit(
                normalized_feature,
                base_body=target_body,
                edge_margin_mm=active_edge_margin_mm,
                diagnostics=diagnostics,
                path=path,
                mode=active_mode,
            )
        elif isinstance(normalized_feature, SlotThroughCutoutFeature):
            _require_positive(normalized_feature.length_mm, f"{path}.dimensions_mm.length")
            _require_positive(normalized_feature.width_mm, f"{path}.dimensions_mm.width")
            if normalized_feature.length_mm <= normalized_feature.width_mm:
                _reject(
                    "INVALID_SLOT_DIMENSIONS",
                    "Slot length must be greater than slot width so the ends are semicircular.",
                    path=f"{path}.dimensions_mm.length",
                )
            normalized_feature = _normalize_subtractive_extent(
                normalized_feature,
                base_body=target_body,
                defaults=defaults,
                path=path,
                diagnostics=diagnostics,
                mode=active_mode,
            )
            orientation_axis = _normalize_slot_orientation(normalized_feature.orientation_axis, path=path)
            normalized_feature = replace(normalized_feature, orientation_axis=orientation_axis)
            _validate_slot_cutout_fit(
                normalized_feature,
                base_body=target_body,
                edge_margin_mm=active_edge_margin_mm,
                diagnostics=diagnostics,
                path=path,
                mode=active_mode,
            )
        elif isinstance(normalized_feature, RectangularExtrudedPadFeature):
            _require_positive(normalized_feature.width_mm, f"{path}.dimensions_mm.width")
            _require_positive(normalized_feature.height_mm, f"{path}.dimensions_mm.height")
            _require_positive(normalized_feature.distance_mm, f"{path}.dimensions_mm.length")
            if normalized_feature.extent_type != "finite":
                normalized_feature = replace(normalized_feature, extent_type="finite")
                defaults.append(
                    DefaultApplied(
                        path=f"{path}.extent.type",
                        value="finite",
                        reason="rectangular_pad_finite_extent",
                    )
                )
            _validate_rectangular_pad_fit(
                normalized_feature,
                base_body=target_body,
                edge_margin_mm=active_edge_margin_mm,
                diagnostics=diagnostics,
                path=path,
                mode=active_mode,
            )
        elif isinstance(normalized_feature, RevolvedProfileFeature):
            _require_positive(normalized_feature.angle_deg, f"{path}.revolve.angle_deg")
            if normalized_feature.angle_deg > 360.0:
                _reject(
                    "INVALID_REVOLVE_ANGLE",
                    "Revolved profile angle must be no greater than 360 degrees.",
                    path=f"{path}.revolve.angle_deg",
                )
            if normalized_feature.extent_type != "finite":
                normalized_feature = replace(normalized_feature, extent_type="finite")
                defaults.append(
                    DefaultApplied(
                        path=f"{path}.extent.type",
                        value="finite",
                        reason="revolved_profile_finite_extent",
                    )
                )
            normalized_feature = _normalize_revolved_profile_from_dimensions(
                normalized_feature,
                base_body=target_body,
                defaults=defaults,
                path=path,
            )
            normalized_feature = _normalize_revolved_axis_span(normalized_feature, defaults=defaults, path=path)
            _validate_revolved_profile(
                normalized_feature,
                base_body=target_body,
                edge_margin_mm=active_edge_margin_mm,
                path=path,
                diagnostics=diagnostics,
                mode=active_mode,
            )
        elif isinstance(normalized_feature, SweptProtrusionFeature):
            _validate_swept_protrusion(
                normalized_feature,
                base_body=target_body,
                edge_margin_mm=active_edge_margin_mm,
                path=path,
                diagnostics=diagnostics,
                mode=active_mode,
            )
        normalized_features.append(normalized_feature)

    _validate_hole_separation(
        hole_specs,
        edge_margin_mm=active_edge_margin_mm,
        diagnostics=diagnostics,
        mode=active_mode,
    )
    if len(hole_specs) > 1:
        diagnostics.append(
            ValidationDiagnostic(
                severity="info",
                code="CANONICAL_MULTI_HOLE_FAMILY",
                message=(
                    "Multiple circular through-holes are accepted by canonical validation and remain gated by "
                    "CanonicalDecision plus deterministic validation."
                ),
            )
        )

    if active_mode == "strict":
        for diag in diagnostics:
            if diag.severity == "warning":
                _reject(diag.code, diag.message, path=diag.path)

    return replace(
        plan,
        base_body=base_body,
        primitive_bodies=primitive_bodies,
        boolean_operations=boolean_operations,
        features=tuple(normalized_features),
        defaults_applied=tuple(defaults),
        validation_diagnostics=tuple(diagnostics),
    )


__all__ = [
    "validate_feature_plan",
]
