"""Geometry-fit, dimension, and separation validation for 2D planar profile features."""

from __future__ import annotations

import math

from geometry.face_context import resolve_face_context
from geometry.gate_policy import GatePolicyMode
from geometry.plan_geometry import polygon_area
from geometry.plan_models import (
    CircularThroughHoleFeature,
    CylinderBaseBody,
    FeaturePlanBaseBody,
    FeaturePlanFeature,
    ProfileCutoutFeature,
    RectangularBaseBody,
    RectangularExtrudedPadFeature,
    RectangularThroughCutoutFeature,
    SlotOrientationAxis,
    SlotThroughCutoutFeature,
    SphereBaseBody,
    SpurGearBaseBody,
    ValidationDiagnostic,
)
from geometry.plan_parser import MAX_PROFILE_POINTS
from geometry.validators.common import (
    _reject,
    _require_finite,
    _validate_circular_cross_section_profile_fit,
    _warn_allow_or_reject_geometry_fit,
)


def _validate_feature_supported_on_base_body(
    feature: FeaturePlanFeature,
    base_body: FeaturePlanBaseBody,
    *,
    path: str,
    diagnostics: list[ValidationDiagnostic] | None = None,
    mode: GatePolicyMode | None = None,
) -> None:
    """Validate that the given feature family is supported on the target base body type."""
    if isinstance(base_body, RectangularBaseBody):
        return

    if isinstance(base_body, CylinderBaseBody | SphereBaseBody) and isinstance(
        feature,
        CircularThroughHoleFeature
        | RectangularThroughCutoutFeature
        | SlotThroughCutoutFeature
        | ProfileCutoutFeature,
    ):
        return

    if isinstance(base_body, SpurGearBaseBody):
        msg = "Spur gear base bodies currently include their concept outline and optional bore only."
        if diagnostics is not None:
            _warn_allow_or_reject_geometry_fit(
                "UNSUPPORTED_FEATURE_ON_BASE_BODY",
                msg,
                diagnostics=diagnostics,
                path=path,
                mode=mode,
            )
            return
        _reject("UNSUPPORTED_FEATURE_ON_BASE_BODY", msg, path=path)

    msg = (
        "Cylinder and sphere base bodies only support circular holes, rectangular cutouts, and rounded slots "
        "(additive pads and revolved profiles require a rectangular-prism base body)."
    )
    if diagnostics is not None:
        _warn_allow_or_reject_geometry_fit(
            "UNSUPPORTED_FEATURE_ON_BASE_BODY",
            msg,
            diagnostics=diagnostics,
            path=path,
            mode=mode,
        )
        return
    _reject("UNSUPPORTED_FEATURE_ON_BASE_BODY", msg, path=path)


def _validate_hole_fit(
    feature: CircularThroughHoleFeature,
    *,
    radius_mm: float,
    base_body: FeaturePlanBaseBody,
    edge_margin_mm: float,
    diagnostics: list[ValidationDiagnostic],
    path: str,
    mode: GatePolicyMode | None = None,
) -> None:
    """Validate that a circular through-hole fits within the target face boundary."""
    _require_finite(feature.center_x_mm, f"{path}.placement.center_uv_mm.u")
    _require_finite(feature.center_y_mm, f"{path}.placement.center_uv_mm.v")

    if isinstance(base_body, CylinderBaseBody | SphereBaseBody):
        if feature.target_face != "+Z":
            _reject(
                "UNSUPPORTED_TARGET_FACE",
                "Cylinder and sphere through-features currently support only the default axial +Z face.",
                path=path,
            )
        _validate_circular_cross_section_profile_fit(
            feature_id=feature.id,
            center_x_mm=feature.center_x_mm,
            center_y_mm=feature.center_y_mm,
            farthest_profile_radius_mm=radius_mm,
            base_radius_mm=base_body.radius_mm,
            edge_margin_mm=edge_margin_mm,
            failure_code="HOLE_DOES_NOT_FIT",
            diagnostics=diagnostics,
            path=f"{path}.dimensions_mm.diameter",
            mode=mode,
        )
        return

    if not isinstance(base_body, RectangularBaseBody):
        _reject("UNSUPPORTED_BASE_BODY", "Hole fit check requires a rectangular base body.", path=path)

    ctx = resolve_face_context(feature.target_face or "+Z", base_body, feature)
    assert ctx.half_extents_u is not None and ctx.half_extents_v is not None
    half_u, half_v = ctx.half_extents_u, ctx.half_extents_v

    if abs(feature.center_x_mm) + radius_mm + edge_margin_mm > half_u:
        _warn_allow_or_reject_geometry_fit(
            "HOLE_DOES_NOT_FIT",
            f"Feature '{feature.id}' does not fit within the target face U extent with edge margin.",
            diagnostics=diagnostics,
            path=f"{path}.placement.center_uv_mm.u",
            mode=mode,
        )
    if abs(feature.center_y_mm) + radius_mm + edge_margin_mm > half_v:
        _warn_allow_or_reject_geometry_fit(
            "HOLE_DOES_NOT_FIT",
            f"Feature '{feature.id}' does not fit within the target face V extent with edge margin.",
            diagnostics=diagnostics,
            path=f"{path}.placement.center_uv_mm.v",
            mode=mode,
        )


def _validate_rectangular_cutout_fit(
    feature: RectangularThroughCutoutFeature,
    *,
    base_body: FeaturePlanBaseBody,
    edge_margin_mm: float,
    diagnostics: list[ValidationDiagnostic],
    path: str,
    mode: GatePolicyMode | None = None,
) -> None:
    """Validate that a rectangular cutout fits within the target face boundary."""
    _require_finite(feature.center_x_mm, f"{path}.placement.center_uv_mm.u")
    _require_finite(feature.center_y_mm, f"{path}.placement.center_uv_mm.v")

    if isinstance(base_body, CylinderBaseBody | SphereBaseBody):
        if feature.target_face != "+Z":
            _reject(
                "UNSUPPORTED_TARGET_FACE",
                "Cylinder and sphere through-features currently support only the default axial +Z face.",
                path=path,
            )
        farthest_profile_radius_mm = math.hypot(
            feature.width_mm / 2.0,
            feature.height_mm / 2.0,
        )
        _validate_circular_cross_section_profile_fit(
            feature_id=feature.id,
            center_x_mm=feature.center_x_mm,
            center_y_mm=feature.center_y_mm,
            farthest_profile_radius_mm=farthest_profile_radius_mm,
            base_radius_mm=base_body.radius_mm,
            edge_margin_mm=edge_margin_mm,
            failure_code="CUTOUT_DOES_NOT_FIT",
            diagnostics=diagnostics,
            path=f"{path}.dimensions_mm",
            mode=mode,
        )
        return

    if not isinstance(base_body, RectangularBaseBody):
        _reject("UNSUPPORTED_BASE_BODY", "Rectangular cutout check requires a rectangular base body.", path=path)

    ctx = resolve_face_context(feature.target_face or "+Z", base_body, feature)
    assert ctx.half_extents_u is not None and ctx.half_extents_v is not None
    half_u, half_v = ctx.half_extents_u, ctx.half_extents_v
    half_cutout_u = feature.width_mm / 2.0
    half_cutout_v = feature.height_mm / 2.0

    if abs(feature.center_x_mm) + half_cutout_u + edge_margin_mm > half_u:
        _warn_allow_or_reject_geometry_fit(
            "CUTOUT_DOES_NOT_FIT",
            f"Feature '{feature.id}' does not fit within the target face U extent with edge margin.",
            diagnostics=diagnostics,
            path=f"{path}.dimensions_mm.width",
            mode=mode,
        )
    if abs(feature.center_y_mm) + half_cutout_v + edge_margin_mm > half_v:
        _warn_allow_or_reject_geometry_fit(
            "CUTOUT_DOES_NOT_FIT",
            f"Feature '{feature.id}' does not fit within the target face V extent with edge margin.",
            diagnostics=diagnostics,
            path=f"{path}.dimensions_mm.height",
            mode=mode,
        )


def _validate_profile_cutout_fit(
    feature: ProfileCutoutFeature,
    *,
    base_body: FeaturePlanBaseBody,
    edge_margin_mm: float,
    diagnostics: list[ValidationDiagnostic],
    path: str,
    mode: GatePolicyMode | None = None,
) -> None:
    """Validate that a custom polygon profile cutout fits within the target face boundary."""
    _require_finite(feature.center_x_mm, f"{path}.placement.center_uv_mm.u")
    _require_finite(feature.center_y_mm, f"{path}.placement.center_uv_mm.v")

    if len(feature.profile_points) > MAX_PROFILE_POINTS:
        _reject(
            "EXCESSIVE_PROFILE_POINT_COUNT",
            f"Profile point count {len(feature.profile_points)} exceeds limit of {MAX_PROFILE_POINTS}.",
            path=f"{path}.profile.points",
        )

    if len(feature.profile_points) < 3:
        _reject(
            "INVALID_PROFILE_POINTS",
            "Profile cutout requires at least three polygon points.",
            path=f"{path}.profile.points",
        )

    if polygon_area(feature.profile_points) <= 1e-6:
        _reject(
            "INVALID_GEOMETRY",
            "Profile cutout polygon must have non-zero area (points cannot be collinear or degenerate).",
            path=f"{path}.profile.points",
        )

    for point_index, point in enumerate(feature.profile_points):
        _require_finite(point.x_mm, f"{path}.profile.points[{point_index}].x_mm")
        _require_finite(point.y_mm, f"{path}.profile.points[{point_index}].y_mm")

    if isinstance(base_body, CylinderBaseBody | SphereBaseBody):
        if feature.target_face != "+Z":
            _reject(
                "UNSUPPORTED_TARGET_FACE",
                "Cylinder and sphere through-features currently support only the default axial +Z face.",
                path=path,
            )
        shifted_points = [
            math.hypot(p.x_mm + feature.center_x_mm, p.y_mm + feature.center_y_mm)
            for p in feature.profile_points
        ]
        farthest_profile_radius_mm = max(shifted_points)
        _validate_circular_cross_section_profile_fit(
            feature_id=feature.id,
            center_x_mm=0.0,
            center_y_mm=0.0,
            farthest_profile_radius_mm=farthest_profile_radius_mm,
            base_radius_mm=base_body.radius_mm,
            edge_margin_mm=edge_margin_mm,
            failure_code="CUTOUT_DOES_NOT_FIT",
            diagnostics=diagnostics,
            path=path,
            mode=mode,
        )
        return

    if not isinstance(base_body, RectangularBaseBody):
        _reject("UNSUPPORTED_BASE_BODY", "Profile cutout check requires a rectangular base body.", path=path)

    ctx = resolve_face_context(feature.target_face or "+Z", base_body, feature)
    assert ctx.half_extents_u is not None and ctx.half_extents_v is not None
    half_u, half_v = ctx.half_extents_u, ctx.half_extents_v

    shifted_xs = [p.x_mm + feature.center_x_mm for p in feature.profile_points]
    shifted_ys = [p.y_mm + feature.center_y_mm for p in feature.profile_points]
    min_x = min(shifted_xs)
    max_x = max(shifted_xs)
    min_y = min(shifted_ys)
    max_y = max(shifted_ys)

    if min_x - edge_margin_mm < -half_u or max_x + edge_margin_mm > half_u:
        _warn_allow_or_reject_geometry_fit(
            "CUTOUT_DOES_NOT_FIT",
            f"Feature '{feature.id}' does not fit within the target face U extent with edge margin.",
            diagnostics=diagnostics,
            path=path,
            mode=mode,
        )
    if min_y - edge_margin_mm < -half_v or max_y + edge_margin_mm > half_v:
        _warn_allow_or_reject_geometry_fit(
            "CUTOUT_DOES_NOT_FIT",
            f"Feature '{feature.id}' does not fit within the target face V extent with edge margin.",
            diagnostics=diagnostics,
            path=path,
            mode=mode,
        )


def _normalize_slot_orientation(
    value: object,
    *,
    path: str,
    diagnostics: list[ValidationDiagnostic] | None = None,
    mode: GatePolicyMode | None = None,
) -> SlotOrientationAxis:
    """Normalize slot orientation axis string aliases to canonical 'x' or 'y'."""
    axis = str(value).strip().lower()
    if axis in {"x", "u", "0", "0.0", "0deg", "0 degrees"}:
        return "x"
    if axis in {"y", "v", "90", "90.0", "90deg", "90 degrees"}:
        return "y"

    msg = "Slot orientation must be along the face-local X/U axis or Y/V axis."
    if diagnostics is not None:
        _warn_allow_or_reject_geometry_fit(
            "UNSUPPORTED_SLOT_ORIENTATION",
            msg,
            diagnostics=diagnostics,
            path=f"{path}.orientation.axis",
            mode=mode,
        )
        return "x"
    _reject("UNSUPPORTED_SLOT_ORIENTATION", msg, path=f"{path}.orientation.axis")


def _validate_slot_cutout_fit(
    feature: SlotThroughCutoutFeature,
    *,
    base_body: FeaturePlanBaseBody,
    edge_margin_mm: float,
    diagnostics: list[ValidationDiagnostic],
    path: str,
    mode: GatePolicyMode | None = None,
) -> None:
    """Validate that a rounded slot cutout fits within the target face boundary."""
    _require_finite(feature.center_x_mm, f"{path}.placement.center_uv_mm.u")
    _require_finite(feature.center_y_mm, f"{path}.placement.center_uv_mm.v")

    if isinstance(base_body, CylinderBaseBody | SphereBaseBody):
        if feature.target_face != "+Z":
            _reject(
                "UNSUPPORTED_TARGET_FACE",
                "Cylinder and sphere through-features currently support only the default axial +Z face.",
                path=path,
            )
        half_slot_u = feature.length_mm / 2.0 if feature.orientation_axis == "x" else feature.width_mm / 2.0
        half_slot_v = feature.width_mm / 2.0 if feature.orientation_axis == "x" else feature.length_mm / 2.0
        farthest_profile_radius_mm = math.hypot(half_slot_u, half_slot_v)
        _validate_circular_cross_section_profile_fit(
            feature_id=feature.id,
            center_x_mm=feature.center_x_mm,
            center_y_mm=feature.center_y_mm,
            farthest_profile_radius_mm=farthest_profile_radius_mm,
            base_radius_mm=base_body.radius_mm,
            edge_margin_mm=edge_margin_mm,
            failure_code="SLOT_DOES_NOT_FIT",
            diagnostics=diagnostics,
            path=f"{path}.dimensions_mm",
            mode=mode,
        )
        return

    if not isinstance(base_body, RectangularBaseBody):
        _reject("UNSUPPORTED_BASE_BODY", "Slot cutout check requires a rectangular base body.", path=path)

    ctx = resolve_face_context(feature.target_face or "+Z", base_body, feature)
    assert ctx.half_extents_u is not None and ctx.half_extents_v is not None
    half_u, half_v = ctx.half_extents_u, ctx.half_extents_v
    half_slot_u = feature.length_mm / 2.0 if feature.orientation_axis == "x" else feature.width_mm / 2.0
    half_slot_v = feature.width_mm / 2.0 if feature.orientation_axis == "x" else feature.length_mm / 2.0

    if abs(feature.center_x_mm) + half_slot_u + edge_margin_mm > half_u:
        _warn_allow_or_reject_geometry_fit(
            "SLOT_DOES_NOT_FIT",
            f"Feature '{feature.id}' does not fit within the target face U extent with edge margin.",
            diagnostics=diagnostics,
            path=f"{path}.dimensions_mm.length",
            mode=mode,
        )
    if abs(feature.center_y_mm) + half_slot_v + edge_margin_mm > half_v:
        _warn_allow_or_reject_geometry_fit(
            "SLOT_DOES_NOT_FIT",
            f"Feature '{feature.id}' does not fit within the target face V extent with edge margin.",
            diagnostics=diagnostics,
            path=f"{path}.dimensions_mm.width",
            mode=mode,
        )


def _validate_rectangular_pad_fit(
    feature: RectangularExtrudedPadFeature,
    *,
    base_body: FeaturePlanBaseBody,
    edge_margin_mm: float,
    diagnostics: list[ValidationDiagnostic],
    path: str,
    mode: GatePolicyMode | None = None,
) -> None:
    """Validate that a rectangular extruded pad fits within the target face boundary."""
    _require_finite(feature.center_x_mm, f"{path}.placement.center_uv_mm.u")
    _require_finite(feature.center_y_mm, f"{path}.placement.center_uv_mm.v")

    if not isinstance(base_body, RectangularBaseBody):
        msg = "Rectangular extruded pads currently support rectangular-prism base bodies only."
        _warn_allow_or_reject_geometry_fit(
            "UNSUPPORTED_FEATURE_ON_BASE_BODY",
            msg,
            diagnostics=diagnostics,
            path=path,
            mode=mode,
        )
        return

    if feature.target_face not in {"+Z", "+X", "-X", "+Y", "-Y"}:
        _reject(
            "UNSUPPORTED_TARGET_FACE",
            "Rectangular side pads currently support +X, -X, +Y, -Y, or deterministic positive smallest-face targets only.",
            path=path,
        )
    ctx = resolve_face_context(feature.target_face or "+Z", base_body, feature)
    assert ctx.half_extents_u is not None and ctx.half_extents_v is not None
    half_u, half_v = ctx.half_extents_u, ctx.half_extents_v
    half_pad_u = feature.width_mm / 2.0
    half_pad_v = feature.height_mm / 2.0

    if abs(feature.center_x_mm) + half_pad_u + edge_margin_mm > half_u:
        _warn_allow_or_reject_geometry_fit(
            "PAD_DOES_NOT_FIT",
            f"Feature '{feature.id}' does not fit within the target face U extent with edge margin.",
            diagnostics=diagnostics,
            path=f"{path}.dimensions_mm.width",
            mode=mode,
        )
    if abs(feature.center_y_mm) + half_pad_v + edge_margin_mm > half_v:
        _warn_allow_or_reject_geometry_fit(
            "PAD_DOES_NOT_FIT",
            f"Feature '{feature.id}' does not fit within the target face V extent with edge margin.",
            diagnostics=diagnostics,
            path=f"{path}.dimensions_mm.height",
            mode=mode,
        )


def _validate_hole_separation(
    hole_specs: list[tuple[str, str, str, float, float, float]],
    *,
    edge_margin_mm: float,
    diagnostics: list[ValidationDiagnostic],
    mode: GatePolicyMode | None = None,
) -> None:
    """Validate pairwise separation between circular holes on the same target body and face."""
    for left_index, left in enumerate(hole_specs):
        left_id, left_body_id, left_face, left_x, left_y, left_radius = left
        for right_id, right_body_id, right_face, right_x, right_y, right_radius in hole_specs[left_index + 1 :]:
            if left_body_id != right_body_id or left_face != right_face:
                continue
            distance = math.hypot(left_x - right_x, left_y - right_y)
            if distance < left_radius + right_radius + edge_margin_mm:
                _warn_allow_or_reject_geometry_fit(
                    "HOLES_OVERLAP",
                    f"Features '{left_id}' and '{right_id}' overlap or violate the minimum separation margin.",
                    diagnostics=diagnostics,
                    path="features",
                    mode=mode,
                )


__all__ = [
    "_normalize_slot_orientation",
    "_validate_feature_supported_on_base_body",
    "_validate_hole_fit",
    "_validate_hole_separation",
    "_validate_profile_cutout_fit",
    "_validate_rectangular_cutout_fit",
    "_validate_rectangular_pad_fit",
    "_validate_slot_cutout_fit",
]
