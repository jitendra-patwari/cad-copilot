"""Validation and normalization for revolved profile features and shaft base bodies."""

from __future__ import annotations

from dataclasses import replace

from geometry.gate_policy import GatePolicyMode
from geometry.plan_geometry import _point_to_dict
from geometry.plan_models import (
    DefaultApplied,
    FeaturePlanBaseBody,
    ProfilePoint2D,
    RectangularBaseBody,
    RevolvedProfileFeature,
    RevolvedShaftBaseBody,
    ValidationDiagnostic,
)
from geometry.plan_parser import MAX_PROFILE_POINTS
from geometry.validators.common import (
    _reject,
    _require_finite,
    _warn_allow_or_reject_geometry_fit,
)


def _validate_revolved_profile(
    feature: RevolvedProfileFeature,
    *,
    base_body: FeaturePlanBaseBody,
    edge_margin_mm: float,
    path: str,
    diagnostics: list[ValidationDiagnostic] | None = None,
    mode: GatePolicyMode | None = None,
) -> None:
    """Validate that a revolved profile feature is physically and geometrically sound."""
    if not isinstance(base_body, RectangularBaseBody):
        msg = "Revolved profile features currently support rectangular-prism base bodies only."
        if diagnostics is not None:
            _warn_allow_or_reject_geometry_fit(
                "UNSUPPORTED_FEATURE_ON_BASE_BODY",
                msg,
                diagnostics=diagnostics,
                path=path,
                mode=mode,
            )
            return
        _reject(
            "UNSUPPORTED_FEATURE_ON_BASE_BODY",
            msg,
            path=path,
        )
    if feature.target_face not in {"+X", "-X", "+Y", "-Y"}:
        _reject(
            "UNSUPPORTED_TARGET_FACE",
            "Revolved profile features currently support explicit rectangular-prism side faces only.",
            path=path,
        )
    if len(feature.profile_points) > MAX_PROFILE_POINTS:
        _reject(
            "EXCESSIVE_PROFILE_POINT_COUNT",
            f"Profile point count {len(feature.profile_points)} exceeds limit of {MAX_PROFILE_POINTS}.",
            path=f"{path}.profile.points",
        )
    if len(feature.profile_points) < 3:
        _reject(
            "INVALID_REVOLVE_PROFILE",
            "Revolved profile requires at least three polygon points.",
            path=f"{path}.profile.points",
        )

    for point_index, point in enumerate(feature.profile_points):
        _require_finite(point.x_mm, f"{path}.profile.points[{point_index}].x_mm")
        _require_finite(point.y_mm, f"{path}.profile.points[{point_index}].y_mm")
    _require_finite(feature.axis_start.x_mm, f"{path}.revolve.axis.start.x_mm")
    _require_finite(feature.axis_start.y_mm, f"{path}.revolve.axis.start.y_mm")
    _require_finite(feature.axis_end.x_mm, f"{path}.revolve.axis.end.x_mm")
    _require_finite(feature.axis_end.y_mm, f"{path}.revolve.axis.end.y_mm")

    vertical_axis = feature.axis_start.x_mm == feature.axis_end.x_mm
    horizontal_axis = feature.axis_start.y_mm == feature.axis_end.y_mm
    if not vertical_axis and not horizontal_axis:
        _reject(
            "UNSUPPORTED_REVOLVE_AXIS",
            "Canonical revolved profile support currently accepts horizontal or vertical sketch-space axes only.",
            path=f"{path}.revolve.axis",
        )
    if feature.axis_start == feature.axis_end:
        _reject(
            "INVALID_REVOLVE_AXIS",
            "Revolved profile axis start and end points must not be identical.",
            path=f"{path}.revolve.axis",
        )

    if vertical_axis:
        radial_offsets = [point.x_mm - feature.axis_start.x_mm for point in feature.profile_points]
    else:
        radial_offsets = [point.y_mm - feature.axis_start.y_mm for point in feature.profile_points]

    non_zero_offsets = [offset for offset in radial_offsets if abs(offset) > 1e-9]
    if not non_zero_offsets:
        _reject(
            "INVALID_REVOLVE_PROFILE",
            "Revolved profile must include points away from the axis.",
            path=f"{path}.profile.points",
        )
    if min(non_zero_offsets) < 0.0 < max(non_zero_offsets):
        _reject(
            "INVALID_REVOLVE_PROFILE",
            "Revolved profile points must stay on one side of the axis.",
            path=f"{path}.profile.points",
        )


def _normalize_revolved_axis_span(
    feature: RevolvedProfileFeature,
    *,
    defaults: list[DefaultApplied],
    path: str,
) -> RevolvedProfileFeature:
    """Normalize the revolution axis span to bound the extent of the profile points."""
    if len(feature.profile_points) < 2:
        return feature

    vertical_axis = feature.axis_start.x_mm == feature.axis_end.x_mm
    horizontal_axis = feature.axis_start.y_mm == feature.axis_end.y_mm
    if vertical_axis:
        axis_x = feature.axis_start.x_mm
        min_y = min(point.y_mm for point in feature.profile_points)
        max_y = max(point.y_mm for point in feature.profile_points)
        normalized_start = ProfilePoint2D(axis_x, min_y)
        normalized_end = ProfilePoint2D(axis_x, max_y)
    elif horizontal_axis:
        axis_y = feature.axis_start.y_mm
        min_x = min(point.x_mm for point in feature.profile_points)
        max_x = max(point.x_mm for point in feature.profile_points)
        normalized_start = ProfilePoint2D(min_x, axis_y)
        normalized_end = ProfilePoint2D(max_x, axis_y)
    else:
        return feature

    if feature.axis_start == normalized_start and feature.axis_end == normalized_end:
        return feature

    defaults.append(
        DefaultApplied(
            path=f"{path}.revolve.axis",
            value={
                "start": _point_to_dict(normalized_start),
                "end": _point_to_dict(normalized_end),
            },
            reason="revolved_profile_axis_span",
        )
    )
    return replace(feature, axis_start=normalized_start, axis_end=normalized_end)


def _normalize_revolved_profile_from_dimensions(
    feature: RevolvedProfileFeature,
    *,
    base_body: FeaturePlanBaseBody,
    defaults: list[DefaultApplied],
    path: str,
) -> RevolvedProfileFeature:
    """Derive full polygon profile points from parametric height/radial dimensions."""
    if not isinstance(base_body, RectangularBaseBody):
        return feature
    if feature.target_face not in {"+X", "-X", "+Y", "-Y"}:
        return feature
    if feature.target_face in {"+Y", "-Y"}:
        return feature

    profile_height_mm = feature.profile_height_mm
    radial_depth_mm = feature.radial_depth_mm

    if profile_height_mm <= 0.0 and radial_depth_mm <= 0.0:
        return feature

    if radial_depth_mm <= 0.0 and feature.profile_points:
        vertical_axis = feature.axis_start.x_mm == feature.axis_end.x_mm
        if not vertical_axis:
            return feature
        axis_x = feature.axis_start.x_mm
        radial_depth_mm = max(abs(point.x_mm - axis_x) for point in feature.profile_points)

    if profile_height_mm <= 0.0 and len(feature.profile_points) >= 2:
        profile_height_mm = max(point.y_mm for point in feature.profile_points) - min(
            point.y_mm for point in feature.profile_points
        )

    if profile_height_mm <= 0.0 or radial_depth_mm <= 0.0:
        return feature
    if profile_height_mm > base_body.thickness_mm:
        return feature

    lower_y = (base_body.thickness_mm - profile_height_mm) / 2.0
    upper_y = lower_y + profile_height_mm
    profile_points = (
        ProfilePoint2D(0.0, lower_y),
        ProfilePoint2D(radial_depth_mm, lower_y),
        ProfilePoint2D(radial_depth_mm, upper_y),
        ProfilePoint2D(0.0, upper_y),
    )
    axis_start = ProfilePoint2D(0.0, lower_y)
    axis_end = ProfilePoint2D(0.0, upper_y)

    if (
        feature.profile_points == profile_points
        and feature.axis_start == axis_start
        and feature.axis_end == axis_end
    ):
        return feature

    defaults.append(
        DefaultApplied(
            path=f"{path}.profile",
            value={
                "points": [_point_to_dict(point) for point in profile_points],
                "axis": {
                    "start": _point_to_dict(axis_start),
                    "end": _point_to_dict(axis_end),
                },
            },
            reason="revolved_profile_dimensions",
        )
    )
    return replace(feature, profile_points=profile_points, axis_start=axis_start, axis_end=axis_end)


def _normalize_revolved_shaft_profile(
    base_body: RevolvedShaftBaseBody,
    *,
    defaults: list[DefaultApplied],
    path: str,
) -> RevolvedShaftBaseBody:
    """Normalize shaft profile points by clamping negative radii and sorting monotonically by height."""
    original_points = base_body.profile_points
    if not original_points:
        return base_body

    # 1. Clamp negative radii to 0 (on rotation axis)
    clamped = tuple(
        ProfilePoint2D(x_mm=max(0.0, point.x_mm), y_mm=point.y_mm)
        for point in original_points
    )

    # 2. Sort by height (y_mm)
    sorted_points = tuple(sorted(clamped, key=lambda p: p.y_mm))

    if sorted_points == original_points:
        return base_body

    defaults.append(
        DefaultApplied(
            path=f"{path}.profile_points",
            value=[{"x_mm": p.x_mm, "y_mm": p.y_mm} for p in sorted_points],
            reason="revolved_shaft_profile_normalization",
        )
    )
    return replace(base_body, profile_points=sorted_points)


def _validate_revolved_shaft_profile(
    base_body: RevolvedShaftBaseBody,
    *,
    path: str,
    diagnostics: list[ValidationDiagnostic] | None = None,
    mode: GatePolicyMode | None = None,
) -> None:
    """Validate shaft profile invariants: non-negative radius and axial height monotonicity."""
    points = base_body.profile_points
    if not points:
        return

    for i, point in enumerate(points):
        if point.x_mm < 0.0:
            _reject(
                "INVALID_GEOMETRY",
                f"Revolved shaft profile point [{i}] has negative radius x_mm={point.x_mm:.4g}. "
                "All profile points must be on one side of the rotation axis (x_mm >= 0).",
                path=f"{path}.profile_points[{i}].x_mm",
            )

    heights = [point.y_mm for point in points]
    if len(set(heights)) < 2:
        _reject(
            "INVALID_GEOMETRY",
            "Revolved shaft profile must have at least two distinct height (y_mm) values.",
            path=f"{path}.profile_points",
        )

    for i in range(len(points) - 1):
        curr_p = points[i]
        next_p = points[i + 1]

        # Decreasing height check
        if next_p.y_mm < curr_p.y_mm:
            msg = (
                f"Revolved shaft profile height values must be monotonic, "
                f"but point [{i}] y_mm={curr_p.y_mm:.4g} > point [{i+1}] y_mm={next_p.y_mm:.4g}."
            )
            if diagnostics is not None:
                _warn_allow_or_reject_geometry_fit(
                    "INVALID_GEOMETRY",
                    msg,
                    diagnostics=diagnostics,
                    path=f"{path}.profile_points[{i+1}].y_mm",
                    mode=mode,
                )
            else:
                _reject("INVALID_GEOMETRY", msg, path=f"{path}.profile_points[{i+1}].y_mm")

        # Equal height with identical radius is a duplicate point error
        if next_p.y_mm == curr_p.y_mm and next_p.x_mm == curr_p.x_mm:
            _reject(
                "INVALID_GEOMETRY",
                f"Revolved shaft profile contains duplicate points at [{i}] and [{i+1}]: "
                f"(x_mm={curr_p.x_mm:.4g}, y_mm={curr_p.y_mm:.4g}).",
                path=f"{path}.profile_points[{i+1}]",
            )
        # Note: Equal height with different radius (next_p.y_mm == curr_p.y_mm and next_p.x_mm != curr_p.x_mm)
        # is accepted as a valid stepped shaft radial shoulder.


__all__ = [
    "_normalize_revolved_axis_span",
    "_normalize_revolved_profile_from_dimensions",
    "_normalize_revolved_shaft_profile",
    "_validate_revolved_profile",
    "_validate_revolved_shaft_profile",
]
