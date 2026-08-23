"""Pure domain planar geometry algorithms, polygon math, and AST shape helpers.

Invariants:
    1. Zero I/O Purity (NFR-1): 100% computational domain logic with zero COM/CAD or disk imports.
    2. Mathematical Determinism (NFR-2): Exact floating-point math across Shoelace, Centroid, and Ray-casting.
    3. Strict Static Typing (NFR-4): Fully annotated domain functions with polymorphic Point2DLike ingestion.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any, NoReturn, TypeAlias

from geometry.gear_math import spur_gear_outline_points
from geometry.plan_models import (
    CircularThroughHoleFeature,
    CylinderBaseBody,
    FeaturePlanBaseBody,
    FeaturePlanFeature,
    FeaturePlanValidationError,
    ProfileCutoutFeature,
    ProfilePoint2D,
    RectangularBaseBody,
    RectangularThroughCutoutFeature,
    RevolvedShaftBaseBody,
    SlotThroughCutoutFeature,
    SphereBaseBody,
    SpurGearBaseBody,
    ValidationDiagnostic,
)

Point2DLike: TypeAlias = tuple[float, float] | dict[str, float] | ProfilePoint2D

SubtractiveProfileFeature: TypeAlias = (
    CircularThroughHoleFeature
    | RectangularThroughCutoutFeature
    | SlotThroughCutoutFeature
    | ProfileCutoutFeature
)


def _to_xy_tuple(point: Point2DLike) -> tuple[float, float]:
    """Convert any supported 2D point representation into a float tuple (x, y)."""
    if isinstance(point, tuple) and len(point) >= 2:
        return (float(point[0]), float(point[1]))
    if isinstance(point, ProfilePoint2D):
        return (point.x_mm, point.y_mm)
    if isinstance(point, dict):
        x_val = point.get("x_mm")
        if x_val is None:
            x_val = point.get("x", 0.0)
        y_val = point.get("y_mm")
        if y_val is None:
            y_val = point.get("y", 0.0)
        return (float(x_val), float(y_val))
    raise TypeError(f"Unsupported point type for 2D geometry: {type(point)}")


def _point_to_dict(point: ProfilePoint2D) -> dict[str, float]:
    """Serialize a ProfilePoint2D into a canonical coordinate dictionary."""
    return {"x_mm": point.x_mm, "y_mm": point.y_mm}


def _normalize_polygon_points(polygon: Sequence[Point2DLike]) -> list[tuple[float, float]]:
    """Normalize a point sequence to (x, y) tuples, trimming any duplicate closing endpoint."""
    points = [_to_xy_tuple(p) for p in polygon]
    if len(points) > 1 and math.isclose(points[0][0], points[-1][0], abs_tol=1e-9) and math.isclose(
        points[0][1], points[-1][1], abs_tol=1e-9
    ):
        points.pop()
    return points


def point_to_segment_distance(
    px: float,
    py: float,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
) -> float:
    """Calculate the minimum Euclidean distance from point (px, py) to segment (x1, y1)-(x2, y2)."""
    dx = x2 - x1
    dy = y2 - y1
    segment_len_sq = dx * dx + dy * dy

    if segment_len_sq <= 1e-18:
        return math.hypot(px - x1, py - y1)

    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / segment_len_sq))
    proj_x = x1 + t * dx
    proj_y = y1 + t * dy
    return math.hypot(px - proj_x, py - proj_y)


def point_in_polygon(
    x: float,
    y: float,
    polygon: Sequence[Point2DLike],
    *,
    include_boundary: bool = True,
) -> bool:
    """Check if point (x, y) lies inside or on the boundary of a 2D polygon.

    Uses Jordan curve theorem with half-open intervals [y_i, y_{i+1}) for robust ray-crossing.
    """
    points = _normalize_polygon_points(polygon)
    n = len(points)
    if n < 3:
        return False

    if include_boundary:
        for i in range(n):
            j = (i + 1) % n
            if point_to_segment_distance(x, y, points[i][0], points[i][1], points[j][0], points[j][1]) < 1e-9:
                return True

    inside = False
    for i in range(n):
        j = (i + 1) % n
        xi, yi = points[i]
        xj, yj = points[j]

        # Half-open vertical interval check
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside

    return inside


def polygon_signed_area(polygon: Sequence[Point2DLike]) -> float:
    """Calculate the signed area of a 2D polygon using the Shoelace formula.

    Returns positive area for counter-clockwise (CCW) winding and negative area for clockwise (CW).
    """
    points = _normalize_polygon_points(polygon)
    n = len(points)
    if n < 3:
        return 0.0

    area2 = 0.0
    for i in range(n):
        j = (i + 1) % n
        area2 += points[i][0] * points[j][1] - points[j][0] * points[i][1]

    return 0.5 * area2


def polygon_area(polygon: Sequence[Point2DLike]) -> float:
    """Calculate the unsigned (absolute) area of a 2D polygon in square millimeters."""
    return abs(polygon_signed_area(polygon))


def is_polygon_ccw(polygon: Sequence[Point2DLike]) -> bool:
    """Return True if the polygon vertices are oriented in Counter-Clockwise (CCW) order."""
    return polygon_signed_area(polygon) > 0.0


def polygon_centroid(polygon: Sequence[Point2DLike]) -> tuple[float, float]:
    """Calculate the geometric centroid (Cx, Cy) of a 2D polygon.

    Falls back to the arithmetic vertex mean if the polygon has zero area or fewer than 3 points.
    """
    points = _normalize_polygon_points(polygon)
    n = len(points)
    if n == 0:
        return (0.0, 0.0)
    if n < 3:
        avg_x = sum(p[0] for p in points) / n
        avg_y = sum(p[1] for p in points) / n
        return (avg_x, avg_y)

    signed_a = polygon_signed_area(points)
    if abs(signed_a) < 1e-12:
        avg_x = sum(p[0] for p in points) / n
        avg_y = sum(p[1] for p in points) / n
        return (avg_x, avg_y)

    factor = 1.0 / (6.0 * signed_a)
    cx = 0.0
    cy = 0.0
    for i in range(n):
        j = (i + 1) % n
        cross = points[i][0] * points[j][1] - points[j][0] * points[i][1]
        cx += (points[i][0] + points[j][0]) * cross
        cy += (points[i][1] + points[j][1]) * cross

    return (cx * factor, cy * factor)


def polygon_bounding_box(polygon: Sequence[Point2DLike]) -> tuple[float, float, float, float]:
    """Calculate the Axis-Aligned Bounding Box (min_x, min_y, max_x, max_y) of a 2D polygon."""
    points = [_to_xy_tuple(p) for p in polygon]
    if not points:
        return (0.0, 0.0, 0.0, 0.0)

    min_x = min(p[0] for p in points)
    min_y = min(p[1] for p in points)
    max_x = max(p[0] for p in points)
    max_y = max(p[1] for p in points)
    return (min_x, min_y, max_x, max_y)


def bounding_boxes_overlap(
    box1: tuple[float, float, float, float],
    box2: tuple[float, float, float, float],
    margin_mm: float = 0.0,
) -> bool:
    """Check if two bounding boxes (min_x, min_y, max_x, max_y) intersect with optional clearance margin."""
    return not (
        box1[2] + margin_mm < box2[0]
        or box2[2] + margin_mm < box1[0]
        or box1[3] + margin_mm < box2[1]
        or box2[3] + margin_mm < box1[1]
    )


# ---------------------------------------------------------------------------
# AST Shape & Through-Depth Helpers
# ---------------------------------------------------------------------------


def _base_body_shape(base_body: FeaturePlanBaseBody) -> dict[str, Any]:
    """Generate the geometric shape descriptor dictionary for a primitive base body."""
    if isinstance(base_body, RectangularBaseBody):
        return {
            "type": "cuboid",
            "length_mm": base_body.length_mm,
            "width_mm": base_body.width_mm,
            "height_mm": base_body.thickness_mm,
        }
    if isinstance(base_body, CylinderBaseBody):
        return {
            "type": "cylinder",
            "radius_mm": base_body.radius_mm,
            "height_mm": base_body.height_mm,
        }
    if isinstance(base_body, SphereBaseBody):
        return {
            "type": "sphere",
            "radius_mm": base_body.radius_mm,
        }
    if isinstance(base_body, SpurGearBaseBody):
        return {
            "type": "extruded_profile",
            "profile_family": "spur_gear_concept",
            "points": spur_gear_outline_points(
                tooth_count=base_body.tooth_count,
                module_mm=base_body.module_mm,
                pressure_angle_deg=base_body.pressure_angle_deg,
            ),
            "height_mm": base_body.face_width_mm,
        }
    if isinstance(base_body, RevolvedShaftBaseBody):
        return {
            "type": "revolved_shaft",
            "radius_mm": base_body.radius_mm,
            "height_mm": base_body.height_mm,
            "points": [{"x_mm": point.x_mm, "y_mm": point.y_mm} for point in base_body.profile_points],
        }
    _reject("UNSUPPORTED_BASE_BODY", "Unsupported canonical base body.", path="base_body.family")


def _base_body_through_depth_mm(base_body: FeaturePlanBaseBody) -> float:
    """Calculate the maximum material through-depth (mm) for a base body along its primary axis."""
    if isinstance(base_body, RectangularBaseBody):
        return float(base_body.thickness_mm)
    if isinstance(base_body, CylinderBaseBody):
        return float(base_body.height_mm)
    if isinstance(base_body, SphereBaseBody):
        return float(base_body.radius_mm * 2.0)
    if isinstance(base_body, SpurGearBaseBody):
        return float(base_body.face_width_mm)
    if isinstance(base_body, RevolvedShaftBaseBody):
        return float(base_body.height_mm)
    _reject("UNSUPPORTED_BASE_BODY", "Unsupported canonical base body.", path="base_body.family")


def _feature_through_depth_mm(base_body: FeaturePlanBaseBody, feature: FeaturePlanFeature) -> float:
    """Calculate the physical material depth (mm) through a target face on the given base body."""
    if isinstance(base_body, RectangularBaseBody) and isinstance(
        feature,
        CircularThroughHoleFeature | RectangularThroughCutoutFeature | SlotThroughCutoutFeature | ProfileCutoutFeature,
    ):
        tf = str(feature.target_face or "+Z").lower().strip()
        if tf in {"+x", "-x", "right", "left"}:
            return float(base_body.length_mm)
        if tf in {"+y", "-y", "back", "front"}:
            return float(base_body.width_mm)
    return _base_body_through_depth_mm(base_body)


def _feature_cut_depth_mm(base_body: FeaturePlanBaseBody, feature: SubtractiveProfileFeature) -> float:
    """Calculate the resolved cut depth in millimeters for a subtractive feature."""
    if feature.extent_type == "through_all":
        return _feature_through_depth_mm(base_body, feature)
    return float(feature.depth_mm)


def _feature_cut_through_all(feature: SubtractiveProfileFeature) -> bool:
    """Check whether a feature cuts completely through the target body."""
    return bool(feature.extent_type == "through_all")


def _base_body_human_label(base_body: FeaturePlanBaseBody) -> str:
    """Generate a clean, human-readable description of a primitive solid body."""
    label = "/".join(base_body.semantic_labels) if base_body.semantic_labels else "primitive"
    if isinstance(base_body, RectangularBaseBody):
        len_val = int(base_body.length_mm) if base_body.length_mm.is_integer() else base_body.length_mm
        wid_val = int(base_body.width_mm) if base_body.width_mm.is_integer() else base_body.width_mm
        thk_val = int(base_body.thickness_mm) if base_body.thickness_mm.is_integer() else base_body.thickness_mm
        if len_val == wid_val == thk_val:
            return f"Canonical {len_val}mm cube {label} base body"
        return f"Canonical {len_val}x{wid_val}x{thk_val}mm rectangular {label} base body"
    if isinstance(base_body, CylinderBaseBody):
        rad_val = int(base_body.radius_mm) if base_body.radius_mm.is_integer() else base_body.radius_mm
        hgt_val = int(base_body.height_mm) if base_body.height_mm.is_integer() else base_body.height_mm
        return f"Canonical r{rad_val} h{hgt_val} cylindrical {label} base body"
    if isinstance(base_body, SphereBaseBody):
        rad_val = int(base_body.radius_mm) if base_body.radius_mm.is_integer() else base_body.radius_mm
        return f"Canonical r{rad_val} spherical {label} base body"
    if isinstance(base_body, SpurGearBaseBody):
        return f"Canonical concept spur gear {label} base body"
    if isinstance(base_body, RevolvedShaftBaseBody):
        return f"Canonical revolved shaft {label} base body"
    return f"Canonical {label} base body"


def _reject(code: str, message: str, *, path: str | None = None) -> NoReturn:
    """Raise a FeaturePlanValidationError diagnostic."""
    raise FeaturePlanValidationError(
        ValidationDiagnostic(
            severity="error",
            code=code,
            message=message,
            path=path,
        )
    )


__all__ = [
    "Point2DLike",
    "SubtractiveProfileFeature",
    "_base_body_human_label",
    "_base_body_shape",
    "_base_body_through_depth_mm",
    "_feature_cut_depth_mm",
    "_feature_cut_through_all",
    "_feature_through_depth_mm",
    "_point_to_dict",
    "_to_xy_tuple",
    "bounding_boxes_overlap",
    "is_polygon_ccw",
    "point_in_polygon",
    "point_to_segment_distance",
    "polygon_area",
    "polygon_bounding_box",
    "polygon_centroid",
    "polygon_signed_area",
]
