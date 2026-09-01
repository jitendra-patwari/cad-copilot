"""Native geometry primitive and 2D sketch profile builders for Siemens Solid Edge."""

from __future__ import annotations

import math
from typing import Any

from geometry.gear_math import spur_gear_outline_points, validate_gear_parameters
from interfaces.exceptions import CADExecutionError

from .constants import (
    BASE_PLANE_INDEX_XY,
    BASE_PLANE_INDEX_XZ,
    BASE_PLANE_INDEX_YZ,
    FEATURE_STATUS_OK,
    KEYPOINT_ARC_END,
    KEYPOINT_ARC_START,
    KEYPOINT_LINE_END,
    KEYPOINT_LINE_START,
    PROFILE_SIDE_RIGHT,
    PROFILE_STATUS_CLOSED,
    PROFILE_STATUS_OK,
    REF_PLANE_NORMAL_SIDE,
    REF_PLANE_REVERSE_SIDE,
)
from .directions import DirectionPair, resolve_cut_direction, resolve_pad_direction
from .units import deg_to_rad, mm_to_m


def get_ref_plane_offset_side(plane: str, offset_mm: float) -> int:
    """Return the Solid Edge RefPlane side constant for a given plane family and offset sign.

    In Solid Edge (ReferenceElementConstants: igReverseNormalSide=1, igNormalSide=2):
    - XY (Top, index 1, +Z normal): offset >= 0 -> NORMAL_SIDE (2), offset < 0 -> REVERSE_SIDE (1)
    - YZ (Right, index 2, +X normal): offset >= 0 -> NORMAL_SIDE (2), offset < 0 -> REVERSE_SIDE (1)
    - XZ (Front, index 3, -Y normal): offset >= 0 -> REVERSE_SIDE (1), offset < 0 -> NORMAL_SIDE (2)
    """
    plane_upper = plane.strip().upper()
    if plane_upper in ("XY", "YZ"):
        return REF_PLANE_NORMAL_SIDE if offset_mm >= 0.0 else REF_PLANE_REVERSE_SIDE
    if plane_upper == "XZ":
        return REF_PLANE_REVERSE_SIDE if offset_mm >= 0.0 else REF_PLANE_NORMAL_SIDE
    raise CADExecutionError(
        f"Unsupported reference plane family: {plane}",
        error_code="UNSUPPORTED_EXECUTION_OPERATION",
    )


def resolve_or_create_reference_plane(
    raw_doc: Any,
    worker: Any,
    plane: str,
    offset_mm: float = 0.0,
) -> Any:
    """Resolve or construct an XY, XZ, or YZ reference plane at a given normal offset."""
    if not math.isfinite(offset_mm):
        raise CADExecutionError(
            f"Reference plane offset must be finite, got {offset_mm}",
            error_code="INVALID_LOWERED_PAYLOAD",
        )

    plane_upper = plane.strip().upper()
    if plane_upper == "XY":
        base_index = BASE_PLANE_INDEX_XY
    elif plane_upper == "YZ":
        base_index = BASE_PLANE_INDEX_YZ
    elif plane_upper == "XZ":
        base_index = BASE_PLANE_INDEX_XZ
    else:
        raise CADExecutionError(
            f"Unsupported reference plane family: {plane}",
            error_code="UNSUPPORTED_EXECUTION_OPERATION",
        )

    try:
        base_plane = worker._invoke_com(lambda: raw_doc.RefPlanes.Item(base_index))
    except Exception as exc:
        raise CADExecutionError(
            f"Failed to access base reference plane {plane_upper} (index {base_index})",
            error_code="SKETCH_CREATION_FAILED",
        ) from exc

    if abs(offset_mm) < 1e-9:
        return base_plane

    distance_m = mm_to_m(abs(offset_mm))
    side = get_ref_plane_offset_side(plane_upper, offset_mm)

    try:
        parallel_plane = worker._invoke_com(
            lambda: raw_doc.RefPlanes.AddParallelByDistance(base_plane, distance_m, side)
        )
        return parallel_plane
    except Exception as exc:
        raise CADExecutionError(
            f"Failed to create parallel reference plane on {plane_upper} at distance {distance_m:.6f}m (side {side})",
            error_code="SKETCH_CREATION_FAILED",
        ) from exc


def create_profile_on_plane(
    raw_doc: Any,
    worker: Any,
    ref_plane: Any,
) -> Any:
    """Create and return a new 2D sketch profile hosted on the specified reference plane."""
    try:
        profile_set = worker._invoke_com(lambda: raw_doc.ProfileSets.Add())
        profile = worker._invoke_com(lambda: profile_set.Profiles.Add(ref_plane))
        return profile
    except Exception as exc:
        raise CADExecutionError(
            "Failed to initialize Profile on reference plane",
            error_code="PROFILE_CREATION_FAILED",
        ) from exc


def _validate_profile_end(profile: Any, worker: Any, description: str) -> None:
    """End profile in closed validation mode and require readable integer status 0 (PROFILE_STATUS_OK)."""
    try:
        status = worker._invoke_com(lambda: profile.End(PROFILE_STATUS_CLOSED))
    except Exception as exc:
        raise CADExecutionError(
            f"Native profile End({PROFILE_STATUS_CLOSED}) COM call failed for {description}",
            error_code="PROFILE_VALIDATION_FAILED",
        ) from exc

    if not isinstance(status, int) or status != PROFILE_STATUS_OK:
        raise CADExecutionError(
            f"Native {description} validation failed with status {status!r} (expected integer {PROFILE_STATUS_OK})",
            error_code="PROFILE_VALIDATION_FAILED",
        )


def draw_circle_profile(
    profile: Any,
    worker: Any,
    center_x_mm: float,
    center_y_mm: float,
    radius_mm: float,
) -> Any:
    """Draw a circular wire profile and validate the closed profile loop."""
    if not (math.isfinite(center_x_mm) and math.isfinite(center_y_mm) and math.isfinite(radius_mm)):
        raise CADExecutionError(
            f"Circle parameters must be finite: ({center_x_mm}, {center_y_mm}, r={radius_mm})",
            error_code="INVALID_LOWERED_PAYLOAD",
        )
    if radius_mm <= 0.0:
        raise CADExecutionError(
            f"Circle radius must be positive (> 0.0), got {radius_mm}",
            error_code="INVALID_LOWERED_PAYLOAD",
        )

    cx_m = mm_to_m(center_x_mm)
    cy_m = mm_to_m(center_y_mm)
    r_m = mm_to_m(radius_mm)

    try:
        circle = worker._invoke_com(lambda: profile.Circles2d.AddByCenterRadius(cx_m, cy_m, r_m))
    except Exception as exc:
        raise CADExecutionError(
            f"Failed to draw circle profile at ({cx_m:.6f}, {cy_m:.6f}) r={r_m:.6f}",
            error_code="PROFILE_CREATION_FAILED",
        ) from exc

    _validate_profile_end(profile, worker, "circle profile")
    return circle


def draw_polygon_profile(
    profile: Any,
    worker: Any,
    points_mm: list[dict[str, float]] | list[tuple[float, float]],
) -> list[Any]:
    """Draw a closed polygon wire profile from an ordered list of 2D coordinates."""
    if len(points_mm) < 3:
        raise CADExecutionError(
            f"Polygon requires at least 3 vertices to form a closed loop, got {len(points_mm)}",
            error_code="INVALID_LOWERED_PAYLOAD",
        )

    norm_pts: list[tuple[float, float]] = []
    for idx, pt in enumerate(points_mm):
        if isinstance(pt, dict):
            x, y = float(pt["x_mm"]), float(pt["y_mm"])
        elif isinstance(pt, (list, tuple)) and len(pt) >= 2:
            x, y = float(pt[0]), float(pt[1])
        else:
            raise CADExecutionError(
                f"Polygon point at index {idx} has invalid format: {pt}",
                error_code="INVALID_LOWERED_PAYLOAD",
            )
        if not (math.isfinite(x) and math.isfinite(y)):
            raise CADExecutionError(
                f"Polygon point coordinates must be finite, got ({x}, {y}) at index {idx}",
                error_code="INVALID_LOWERED_PAYLOAD",
            )
        norm_pts.append((x, y))

    n = len(norm_pts)

    # Preflight: Validate every cyclic edge is non-degenerate before any COM mutation
    for i in range(n):
        p1 = norm_pts[i]
        p2 = norm_pts[(i + 1) % n]
        edge_dist_sq = ((p2[0] - p1[0]) ** 2) + ((p2[1] - p1[1]) ** 2)
        if edge_dist_sq < 1e-12:
            raise CADExecutionError(
                f"Polygon contains degenerate zero-length edge between vertex {i} and {(i + 1) % n} "
                f"at ({p1[0]:.6f}, {p1[1]:.6f})",
                error_code="INVALID_LOWERED_PAYLOAD",
            )

    # Preflight: Verify all vertices are unique
    if len(set(norm_pts)) != n:
        raise CADExecutionError(
            f"Polygon contains duplicate/repeated vertices ({n} vertices, {len(set(norm_pts))} unique). "
            f"All polygon vertices must be unique.",
            error_code="INVALID_LOWERED_PAYLOAD",
        )

    lines: list[Any] = []

    def _create_line(x1: float, y1: float, x2: float, y2: float) -> Any:
        return worker._invoke_com(lambda: profile.Lines2d.AddBy2Points(x1, y1, x2, y2))

    try:
        for i in range(n):
            p1 = norm_pts[i]
            p2 = norm_pts[(i + 1) % n]
            x1_m, y1_m = mm_to_m(p1[0]), mm_to_m(p1[1])
            x2_m, y2_m = mm_to_m(p2[0]), mm_to_m(p2[1])

            line = _create_line(x1_m, y1_m, x2_m, y2_m)
            lines.append(line)

        # Add keypoint coincident relations connecting adjoining line endpoints
        try:
            relations = worker._invoke_com(lambda: getattr(profile, "Relations2d", None))
            if relations is not None:
                for i in range(n):
                    l_curr = lines[i]
                    l_next = lines[(i + 1) % n]
                    worker._invoke_com(
                        lambda c=l_curr, nx=l_next: relations.AddKeypoint(
                            c, KEYPOINT_LINE_END, nx, KEYPOINT_LINE_START, True
                        )
                    )
        except Exception:
            pass  # Fall through to End(1) validation

    except Exception as exc:
        if isinstance(exc, CADExecutionError):
            raise
        raise CADExecutionError(
            f"Failed to draw closed polygon profile ({len(norm_pts)} vertices)",
            error_code="PROFILE_CREATION_FAILED",
        ) from exc

    _validate_profile_end(profile, worker, "polygon profile")
    return lines


def draw_slot_profile(
    profile: Any,
    worker: Any,
    center_x_mm: float,
    center_y_mm: float,
    length_mm: float,
    width_mm: float,
    orientation_deg: float = 0.0,
) -> list[Any]:
    """Draw a racetrack/obround slot profile composed of 2 straight tangents and 2 semicircular ends."""
    if not (
        math.isfinite(center_x_mm)
        and math.isfinite(center_y_mm)
        and math.isfinite(length_mm)
        and math.isfinite(width_mm)
        and math.isfinite(orientation_deg)
    ):
        raise CADExecutionError("Slot parameters must be finite", error_code="INVALID_LOWERED_PAYLOAD")

    if width_mm <= 0.0 or length_mm <= width_mm:
        raise CADExecutionError(
            f"Slot length ({length_mm}mm) must be strictly greater than width ({width_mm}mm > 0)",
            error_code="INVALID_LOWERED_PAYLOAD",
        )

    r_mm = width_mm / 2.0
    d_mm = (length_mm - width_mm) / 2.0
    rad = deg_to_rad(orientation_deg)
    cos_a, sin_a = math.cos(rad), math.sin(rad)

    def _transform(x_local: float, y_local: float) -> tuple[float, float]:
        x_rot = (x_local * cos_a) - (y_local * sin_a) + center_x_mm
        y_rot = (x_local * sin_a) + (y_local * cos_a) + center_y_mm
        return mm_to_m(x_rot), mm_to_m(y_rot)

    p_bottom_left = _transform(-d_mm, -r_mm)
    p_bottom_right = _transform(d_mm, -r_mm)
    c_right = _transform(d_mm, 0.0)
    p_top_right = _transform(d_mm, r_mm)
    p_top_left = _transform(-d_mm, r_mm)
    c_left = _transform(-d_mm, 0.0)

    elements: list[Any] = []
    try:
        line_bottom = worker._invoke_com(
            lambda: profile.Lines2d.AddBy2Points(
                p_bottom_left[0], p_bottom_left[1], p_bottom_right[0], p_bottom_right[1]
            )
        )
        elements.append(line_bottom)

        arc_right = worker._invoke_com(
            lambda: profile.Arcs2d.AddByCenterStartEnd(
                c_right[0], c_right[1], p_bottom_right[0], p_bottom_right[1], p_top_right[0], p_top_right[1]
            )
        )
        elements.append(arc_right)

        line_top = worker._invoke_com(
            lambda: profile.Lines2d.AddBy2Points(p_top_right[0], p_top_right[1], p_top_left[0], p_top_left[1])
        )
        elements.append(line_top)

        arc_left = worker._invoke_com(
            lambda: profile.Arcs2d.AddByCenterStartEnd(
                c_left[0], c_left[1], p_top_left[0], p_top_left[1], p_bottom_left[0], p_bottom_left[1]
            )
        )
        elements.append(arc_left)

        try:
            relations = worker._invoke_com(lambda: getattr(profile, "Relations2d", None))
            if relations is not None:
                worker._invoke_com(
                    lambda: relations.AddKeypoint(elements[0], KEYPOINT_LINE_END, elements[1], KEYPOINT_ARC_START, True)
                )
                worker._invoke_com(
                    lambda: relations.AddKeypoint(elements[1], KEYPOINT_ARC_END, elements[2], KEYPOINT_LINE_START, True)
                )
                worker._invoke_com(
                    lambda: relations.AddKeypoint(elements[2], KEYPOINT_LINE_END, elements[3], KEYPOINT_ARC_START, True)
                )
                worker._invoke_com(
                    lambda: relations.AddKeypoint(elements[3], KEYPOINT_ARC_END, elements[0], KEYPOINT_LINE_START, True)
                )
        except Exception:
            pass  # Fall through to End(1) validation

    except Exception as exc:
        if isinstance(exc, CADExecutionError):
            raise
        raise CADExecutionError(
            f"Failed to draw slot profile ({length_mm}x{width_mm}mm)",
            error_code="PROFILE_CREATION_FAILED",
        ) from exc

    _validate_profile_end(profile, worker, "slot profile")
    return elements


def _validate_native_model_result(
    model: Any,
    raw_doc: Any,
    worker: Any,
    description: str,
) -> Any:
    """Validate that a newly created primitive model/protrusion is non-None, healthy, and has an accessible solid Body."""
    if model is None:
        raise CADExecutionError(
            f"Native Solid Edge protrusion returned null model for {description}",
            error_code="BODY_CREATION_FAILED",
        )

    # 1. Verify feature status on primary ExtrudedProtrusion
    try:
        protrusions = worker._invoke_com(lambda: getattr(model, "ExtrudedProtrusions", None))
        if protrusions is None or getattr(protrusions, "Count", 0) < 1:
            raise CADExecutionError(
                f"Native model contains no accessible ExtrudedProtrusions collection for {description}",
                error_code="BODY_CREATION_FAILED",
            )

        feature = worker._invoke_com(lambda: protrusions.Item(1))
        if feature is None:
            raise CADExecutionError(
                f"Primary ExtrudedProtrusion is null for {description}",
                error_code="BODY_CREATION_FAILED",
            )

        feature_status = worker._invoke_com(lambda: getattr(feature, "Status", None))
    except Exception as exc:
        if isinstance(exc, CADExecutionError):
            raise
        raise CADExecutionError(
            f"Failed to query native feature status for {description}",
            error_code="FEATURE_VALIDATION_FAILED",
        ) from exc

    if not isinstance(feature_status, int) or feature_status != FEATURE_STATUS_OK:
        raise CADExecutionError(
            f"Native feature status is unhealthy for {description}: status={feature_status!r} (expected integer {FEATURE_STATUS_OK})",
            error_code="FEATURE_VALIDATION_FAILED",
        )

    # 2. Verify solid Body accessibility
    try:
        body = worker._invoke_com(lambda: getattr(model, "Body", None))
        if body is None:
            raise CADExecutionError(
                f"Native model has no accessible solid Body for {description}",
                error_code="SOLID_INSPECTION_FAILED",
            )
    except Exception as exc:
        if isinstance(exc, CADExecutionError):
            raise
        raise CADExecutionError(
            f"Failed to inspect solid Body on created model for {description}",
            error_code="SOLID_INSPECTION_FAILED",
        ) from exc

    return model


def create_primitive_cuboid(
    raw_doc: Any,
    worker: Any,
    length_mm: float,
    width_mm: float,
    height_mm: float,
    origin_offset_mm: dict[str, float] | None = None,
) -> Any:
    """Create a rectangular prism primitive body using finite extrusion."""
    if length_mm <= 0.0 or width_mm <= 0.0 or height_mm <= 0.0:
        raise CADExecutionError(
            f"Cuboid dimensions must be strictly positive: ({length_mm} x {width_mm} x {height_mm}) mm",
            error_code="INVALID_LOWERED_PAYLOAD",
        )

    offset = origin_offset_mm or {}
    ox, oy, oz = float(offset.get("x_mm", 0.0)), float(offset.get("y_mm", 0.0)), float(offset.get("z_mm", 0.0))

    ref_plane = resolve_or_create_reference_plane(raw_doc, worker, "XY", oz)
    profile = create_profile_on_plane(raw_doc, worker, ref_plane)

    half_l = length_mm / 2.0
    half_w = width_mm / 2.0
    corners = [
        (ox - half_l, oy - half_w),
        (ox + half_l, oy - half_w),
        (ox + half_l, oy + half_w),
        (ox - half_l, oy + half_w),
    ]

    draw_polygon_profile(profile, worker, corners)

    height_m = mm_to_m(height_mm)
    try:
        model = worker._invoke_com(
            lambda: raw_doc.Models.AddFiniteExtrudedProtrusion(1, [profile], PROFILE_SIDE_RIGHT, height_m)
        )
    except Exception as exc:
        raise CADExecutionError(
            f"Failed to execute cuboid protrusion COM call ({length_mm}x{width_mm}x{height_mm}mm)",
            error_code="BODY_CREATION_FAILED",
        ) from exc

    return _validate_native_model_result(model, raw_doc, worker, f"cuboid ({length_mm}x{width_mm}x{height_mm}mm)")


def create_primitive_cylinder(
    raw_doc: Any,
    worker: Any,
    radius_mm: float,
    height_mm: float,
    origin_offset_mm: dict[str, float] | None = None,
) -> Any:
    """Create a cylinder primitive body using finite extrusion."""
    if radius_mm <= 0.0 or height_mm <= 0.0:
        raise CADExecutionError(
            f"Cylinder radius and height must be strictly positive: (r={radius_mm}, h={height_mm}) mm",
            error_code="INVALID_LOWERED_PAYLOAD",
        )

    offset = origin_offset_mm or {}
    ox, oy, oz = float(offset.get("x_mm", 0.0)), float(offset.get("y_mm", 0.0)), float(offset.get("z_mm", 0.0))

    ref_plane = resolve_or_create_reference_plane(raw_doc, worker, "XY", oz)
    profile = create_profile_on_plane(raw_doc, worker, ref_plane)

    draw_circle_profile(profile, worker, ox, oy, radius_mm)

    height_m = mm_to_m(height_mm)
    try:
        model = worker._invoke_com(
            lambda: raw_doc.Models.AddFiniteExtrudedProtrusion(1, [profile], PROFILE_SIDE_RIGHT, height_m)
        )
    except Exception as exc:
        raise CADExecutionError(
            f"Failed to execute cylinder protrusion COM call (r={radius_mm}, h={height_mm}mm)",
            error_code="BODY_CREATION_FAILED",
        ) from exc

    return _validate_native_model_result(model, raw_doc, worker, f"cylinder (r={radius_mm}, h={height_mm}) mm")


def create_primitive_extruded_profile(
    raw_doc: Any,
    worker: Any,
    points_mm: list[dict[str, float]] | list[tuple[float, float]],
    height_mm: float,
    origin_offset_mm: dict[str, float] | None = None,
) -> Any:
    """Create an extruded solid body directly from a lowered 2D outline points payload."""
    if height_mm <= 0.0:
        raise CADExecutionError(
            f"Extrusion height must be positive (> 0.0), got {height_mm}",
            error_code="INVALID_LOWERED_PAYLOAD",
        )
    if len(points_mm) < 3:
        raise CADExecutionError(
            f"Extruded profile requires at least 3 points, got {len(points_mm)}",
            error_code="INVALID_LOWERED_PAYLOAD",
        )

    offset = origin_offset_mm or {}
    ox, oy, oz = float(offset.get("x_mm", 0.0)), float(offset.get("y_mm", 0.0)), float(offset.get("z_mm", 0.0))

    if abs(ox) > 1e-9 or abs(oy) > 1e-9:
        shifted_pts: list[dict[str, float]] = []
        for p in points_mm:
            px = float(p["x_mm"] if isinstance(p, dict) else p[0]) + ox
            py = float(p["y_mm"] if isinstance(p, dict) else p[1]) + oy
            shifted_pts.append({"x_mm": px, "y_mm": py})
        poly_points: list[dict[str, float]] | list[tuple[float, float]] = shifted_pts
    else:
        poly_points = points_mm

    ref_plane = resolve_or_create_reference_plane(raw_doc, worker, "XY", oz)
    profile = create_profile_on_plane(raw_doc, worker, ref_plane)

    draw_polygon_profile(profile, worker, poly_points)

    height_m = mm_to_m(height_mm)
    try:
        model = worker._invoke_com(
            lambda: raw_doc.Models.AddFiniteExtrudedProtrusion(1, [profile], PROFILE_SIDE_RIGHT, height_m)
        )
    except Exception as exc:
        raise CADExecutionError(
            f"Failed to execute extruded profile protrusion COM call (h={height_mm}mm)",
            error_code="BODY_CREATION_FAILED",
        ) from exc

    return _validate_native_model_result(model, raw_doc, worker, f"extruded profile (h={height_mm}mm)")


def create_primitive_spur_gear(
    raw_doc: Any,
    worker: Any,
    tooth_count: int,
    module_mm: float,
    pressure_angle_deg: float,
    face_width_mm: float,
    origin_offset_mm: dict[str, float] | None = None,
) -> Any:
    """Create a conceptual spur gear primitive body using calculated outline points."""
    validate_gear_parameters(
        tooth_count=tooth_count,
        module_mm=module_mm,
        pressure_angle_deg=pressure_angle_deg,
    )
    outline_pts = spur_gear_outline_points(
        tooth_count=tooth_count,
        module_mm=module_mm,
        pressure_angle_deg=pressure_angle_deg,
        closed=False,
    )
    return create_primitive_extruded_profile(
        raw_doc,
        worker,
        points_mm=outline_pts,
        height_mm=face_width_mm,
        origin_offset_mm=origin_offset_mm,
    )


def _invoke_native_cutout_through_all(
    model: Any,
    worker: Any,
    profile: Any,
    directions: DirectionPair,
    description: str = "through-all cutout",
) -> Any:
    """Execute native ExtrudedCutouts.AddThroughAll with an explicit DirectionPair and validate feature health."""
    try:
        cutout = worker._invoke_com(
            lambda: model.ExtrudedCutouts.AddThroughAll(profile, directions.profile_side, directions.profile_plane_side)
        )
    except Exception as exc:
        raise CADExecutionError(
            f"Failed to execute AddThroughAll COM call for {description}",
            error_code="FEATURE_CREATION_FAILED",
        ) from exc

    if cutout is None:
        raise CADExecutionError(
            f"Native AddThroughAll returned null feature for {description}",
            error_code="FEATURE_CREATION_FAILED",
        )

    try:
        status = worker._invoke_com(lambda: getattr(cutout, "Status", None))
    except Exception as exc:
        raise CADExecutionError(
            f"Failed to query cutout feature status for {description}",
            error_code="FEATURE_VALIDATION_FAILED",
        ) from exc

    if not isinstance(status, int) or status != FEATURE_STATUS_OK:
        raise CADExecutionError(
            f"Native cutout feature status is unhealthy for {description}: status={status!r} (expected integer {FEATURE_STATUS_OK})",
            error_code="FEATURE_VALIDATION_FAILED",
        )

    return cutout


def _invoke_native_cutout_finite(
    model: Any,
    worker: Any,
    profile: Any,
    directions: DirectionPair,
    depth_m: float,
    description: str = "finite cutout",
) -> Any:
    """Execute native ExtrudedCutouts.AddFinite with an explicit DirectionPair and validate feature health."""
    try:
        cutout = worker._invoke_com(
            lambda: model.ExtrudedCutouts.AddFinite(
                profile, directions.profile_side, directions.profile_plane_side, depth_m
            )
        )
    except Exception as exc:
        raise CADExecutionError(
            f"Failed to execute AddFinite COM call for {description}",
            error_code="FEATURE_CREATION_FAILED",
        ) from exc

    if cutout is None:
        raise CADExecutionError(
            f"Native AddFinite returned null feature for {description}",
            error_code="FEATURE_CREATION_FAILED",
        )

    try:
        status = worker._invoke_com(lambda: getattr(cutout, "Status", None))
    except Exception as exc:
        raise CADExecutionError(
            f"Failed to query cutout feature status for {description}",
            error_code="FEATURE_VALIDATION_FAILED",
        ) from exc

    if not isinstance(status, int) or status != FEATURE_STATUS_OK:
        raise CADExecutionError(
            f"Native cutout feature status is unhealthy for {description}: status={status!r} (expected integer {FEATURE_STATUS_OK})",
            error_code="FEATURE_VALIDATION_FAILED",
        )

    return cutout


def _invoke_native_pad_protrusion(
    model: Any,
    worker: Any,
    profile: Any,
    directions: DirectionPair,
    height_m: float,
    description: str = "pad protrusion",
) -> Any:
    """Execute native ExtrudedProtrusions.AddFinite with an explicit DirectionPair and validate feature health."""
    try:
        protrusion = worker._invoke_com(
            lambda: model.ExtrudedProtrusions.AddFinite(
                profile, directions.profile_side, directions.profile_plane_side, height_m
            )
        )
    except Exception as exc:
        raise CADExecutionError(
            f"Failed to execute pad AddFinite COM call for {description}",
            error_code="FEATURE_CREATION_FAILED",
        ) from exc

    if protrusion is None:
        raise CADExecutionError(
            f"Native AddFinite pad returned null feature for {description}",
            error_code="FEATURE_CREATION_FAILED",
        )

    try:
        status = worker._invoke_com(lambda: getattr(protrusion, "Status", None))
    except Exception as exc:
        raise CADExecutionError(
            f"Failed to query pad feature status for {description}",
            error_code="FEATURE_VALIDATION_FAILED",
        ) from exc

    if not isinstance(status, int) or status != FEATURE_STATUS_OK:
        raise CADExecutionError(
            f"Native pad feature status is unhealthy for {description}: status={status!r} (expected integer {FEATURE_STATUS_OK})",
            error_code="FEATURE_VALIDATION_FAILED",
        )

    return protrusion


def create_cutout_through_all(
    model: Any,
    worker: Any,
    profile: Any,
    face: str,
    profile_family: str,
) -> Any:
    """Create a through-all extruded cutout on target Model using the exact capability row direction pair."""
    dirs = resolve_cut_direction(profile_family=profile_family, extent_type="through_all", face=face)
    return _invoke_native_cutout_through_all(
        model,
        worker,
        profile,
        dirs,
        description=f"through-all cutout on face {face} ({profile_family})",
    )


def create_cutout_finite(
    model: Any,
    worker: Any,
    profile: Any,
    depth_mm: float,
    face: str,
    profile_family: str,
) -> Any:
    """Create a finite/blind extruded cutout on target Model using the exact capability row direction pair."""
    if not math.isfinite(depth_mm) or depth_mm <= 0.0:
        raise CADExecutionError(
            f"Cutout depth must be strictly positive, got {depth_mm}",
            error_code="INVALID_LOWERED_PAYLOAD",
        )

    depth_m = mm_to_m(depth_mm)
    dirs = resolve_cut_direction(profile_family=profile_family, extent_type="finite", face=face)
    return _invoke_native_cutout_finite(
        model,
        worker,
        profile,
        dirs,
        depth_m,
        description=f"finite cutout on face {face} ({profile_family}, depth={depth_mm}mm)",
    )


def create_pad_protrusion(
    model: Any,
    worker: Any,
    profile: Any,
    height_mm: float,
    face: str,
    profile_family: str,
) -> Any:
    """Create an outward finite pad protrusion on the target Model using the exact capability row direction."""
    if not math.isfinite(height_mm) or height_mm <= 0.0:
        raise CADExecutionError(
            f"Pad height must be strictly positive, got {height_mm}",
            error_code="INVALID_LOWERED_PAYLOAD",
        )

    height_m = mm_to_m(height_mm)
    dirs = resolve_pad_direction(profile_family=profile_family, extent_type="finite", face=face)
    return _invoke_native_pad_protrusion(
        model,
        worker,
        profile,
        dirs,
        height_m,
        description=f"pad on face {face} ({profile_family}, h={height_mm}mm)",
    )


__all__ = [
    "create_cutout_finite",
    "create_cutout_through_all",
    "create_pad_protrusion",
    "create_primitive_cuboid",
    "create_primitive_cylinder",
    "create_primitive_extruded_profile",
    "create_primitive_spur_gear",
    "create_profile_on_plane",
    "draw_circle_profile",
    "draw_polygon_profile",
    "draw_slot_profile",
    "get_ref_plane_offset_side",
    "resolve_or_create_reference_plane",
]
