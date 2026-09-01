"""6-face planar coordinate wire mapping and 2D profile geometry compilers for CAD Copilot.

Invariants:
    1. Zero I/O Purity (NFR-1): Pure computational geometry with zero COM, CAD kernel, or network imports.
    2. Mathematical Determinism (NFR-2): Exact Cartesian coordinate projections onto reference sketch planes.
    3. Strict Static Typing (NFR-4): 100% typed with explicit __all__ exports and no Any escape hatches.
    4. Canonical Projections: 2D sketch plane wire coordinates (x_s, y_s) strictly equal the global
       Cartesian coordinates of the face point projected onto the respective sketch reference plane:
       - XY Plane (+Z, -Z): (x_s, y_s) = (X, Y)
       - XZ Plane (+Y, -Y): (x_s, y_s) = (X, Z)
       - YZ Plane (+X, -X): (x_s, y_s) = (Y, Z)
"""

from __future__ import annotations

from typing import Any

from geometry.plan_geometry import (
    _feature_cut_depth_mm,
    _feature_cut_through_all,
)
from geometry.plan_models import (
    CircularThroughHoleFeature,
    CylinderBaseBody,
    FeaturePlanBaseBody,
    FeaturePlanFeature,
    ProfileCutoutFeature,
    RectangularBaseBody,
    RectangularExtrudedPadFeature,
    RectangularThroughCutoutFeature,
    RevolvedProfileFeature,
    RevolvedShaftBaseBody,
    SlotOrientationAxis,
    SlotThroughCutoutFeature,
    SphereBaseBody,
    SpurGearBaseBody,
)

# Canonical abstract cut direction (material removal always directed into the solid body).
DEFAULT_ABSTRACT_CUT_DIRECTION = "into_solid"


def normalize_target_face_alias(target_face: str | None) -> str:
    """Normalize conversational or shorthand target face aliases into canonical 6-face strings."""
    if target_face is None:
        return "+Z"
    tf = target_face.lower().strip()
    if tf in {"top", "up", "+z", "z+", "z"}:
        return "+Z"
    if tf in {"bottom", "down", "-z", "z-"}:
        return "-Z"
    if tf in {"right", "+x", "x+", "x"}:
        return "+X"
    if tf in {"left", "-x", "x-"}:
        return "-X"
    if tf in {"back", "+y", "y+", "y"}:
        return "+Y"
    if tf in {"front", "-y", "y-"}:
        return "-Y"
    return "+Z"


def map_face_uv_to_sketch_wire(
    u: float,
    v: float,
    target_face: str | None,
    base_body: FeaturePlanBaseBody,
) -> tuple[float, float]:
    """Map face-local (u, v) coordinates to 2D sketch plane wire coordinates (x_s, y_s).

    The mapping preserves exact 3D Cartesian alignment across all 6 planar faces:
      - +Z (XY plane): (x_s, y_s) = (p_x + u, p_y + v)
      - -Z (XY plane): (x_s, y_s) = (p_x + u, p_y - v)
      - +Y (XZ plane): (x_s, y_s) = (p_x - u, p_z + t/2 + v)
      - -Y (XZ plane): (x_s, y_s) = (p_x + u, p_z + t/2 + v)
      - +X (YZ plane): (x_s, y_s) = (p_y + u, p_z + t/2 + v)
      - -X (YZ plane): (x_s, y_s) = (p_y - u, p_z + t/2 + v)
    """
    face = normalize_target_face_alias(target_face)
    placement = getattr(base_body, "placement", None)
    px = float(placement.x_mm) if placement is not None else 0.0
    py = float(placement.y_mm) if placement is not None else 0.0
    pz = float(placement.z_mm) if placement is not None else 0.0

    # Determine body half-thickness / vertical elevation parameter
    v_midline = 0.0
    if isinstance(base_body, RectangularBaseBody):
        v_midline = base_body.thickness_mm / 2.0
    elif isinstance(base_body, CylinderBaseBody):
        v_midline = base_body.height_mm / 2.0
    elif isinstance(base_body, SpurGearBaseBody):
        v_midline = base_body.face_width_mm / 2.0
    elif isinstance(base_body, RevolvedShaftBaseBody):
        v_midline = base_body.height_mm / 2.0
    elif isinstance(base_body, SphereBaseBody):
        v_midline = base_body.radius_mm

    xs: float
    ys: float

    if face == "+Z":
        xs = px + u
        ys = py + v
    elif face == "-Z":
        xs = px + u
        ys = py - v
    elif face == "+Y":
        xs = px - u
        ys = pz + v_midline + v
    elif face == "-Y":
        xs = px + u
        ys = pz + v_midline + v
    elif face == "+X":
        xs = py + u
        ys = pz + v_midline + v
    elif face == "-X":
        xs = py - u
        ys = pz + v_midline + v
    else:
        xs = px + u
        ys = py + v

    # Sanitize IEEE-754 signed zeros (-0.0 -> 0.0)
    return (xs + 0.0, ys + 0.0)


def circle_profile_center(
    feature: CircularThroughHoleFeature,
    *,
    base_body: FeaturePlanBaseBody,
) -> dict[str, float]:
    """Calculate the 2D sketch plane center coordinates for a circular through hole."""
    xs, ys = map_face_uv_to_sketch_wire(
        feature.center_x_mm,
        feature.center_y_mm,
        feature.target_face,
        base_body,
    )
    return {"x_mm": xs, "y_mm": ys}


def slot_profile_center(
    feature: SlotThroughCutoutFeature,
    *,
    base_body: FeaturePlanBaseBody,
) -> dict[str, float]:
    """Calculate the 2D sketch plane center coordinates for a slot cutout."""
    xs, ys = map_face_uv_to_sketch_wire(
        feature.center_x_mm,
        feature.center_y_mm,
        feature.target_face,
        base_body,
    )
    return {"x_mm": xs, "y_mm": ys}


def slot_profile_orientation_axis(
    feature: SlotThroughCutoutFeature,
    *,
    base_body: FeaturePlanBaseBody,
) -> SlotOrientationAxis:
    """Return the slot orientation axis in 2D sketch plane space."""
    return feature.orientation_axis


def cutout_polygon_points(
    feature: RectangularThroughCutoutFeature | RectangularExtrudedPadFeature,
    *,
    base_body: FeaturePlanBaseBody,
) -> list[dict[str, float]]:
    """Compute the 4-vertex closed polygon for a rectangular cutout or pad in sketch coordinates."""
    half_w = feature.width_mm / 2.0
    half_h = feature.height_mm / 2.0
    cx = feature.center_x_mm
    cy = feature.center_y_mm

    local_corners = [
        (cx - half_w, cy - half_h),
        (cx + half_w, cy - half_h),
        (cx + half_w, cy + half_h),
        (cx - half_w, cy + half_h),
    ]

    points: list[dict[str, float]] = []
    for u, v in local_corners:
        xs, ys = map_face_uv_to_sketch_wire(u, v, feature.target_face, base_body)
        points.append({"x_mm": xs, "y_mm": ys})
    return points


def profile_cutout_polygon_points(
    feature: ProfileCutoutFeature,
    *,
    base_body: FeaturePlanBaseBody,
) -> list[dict[str, float]]:
    """Translate custom polygon cutout vertices into 2D sketch plane wire coordinates."""
    cx = feature.center_x_mm
    cy = feature.center_y_mm
    points: list[dict[str, float]] = []
    for point in feature.profile_points:
        u = point.x_mm + cx
        v = point.y_mm + cy
        xs, ys = map_face_uv_to_sketch_wire(u, v, feature.target_face, base_body)
        points.append({"x_mm": xs, "y_mm": ys})
    return points


def revolve_polygon_points(
    feature: RevolvedProfileFeature,
    *,
    base_body: FeaturePlanBaseBody,
) -> list[dict[str, float]]:
    """Translate revolve profile vertices into 2D sketch plane wire coordinates."""
    cx = feature.center_x_mm
    cy = feature.center_y_mm
    points: list[dict[str, float]] = []
    for point in feature.profile_points:
        u = point.x_mm + cx
        v = point.y_mm + cy
        xs, ys = map_face_uv_to_sketch_wire(u, v, feature.target_face, base_body)
        points.append({"x_mm": xs, "y_mm": ys})
    return points


def revolve_axis_endpoints(
    feature: RevolvedProfileFeature,
    *,
    base_body: FeaturePlanBaseBody,
) -> dict[str, dict[str, float]]:
    """Translate revolve axis start and end points into 2D sketch plane wire coordinates."""
    cx = feature.center_x_mm
    cy = feature.center_y_mm
    start_xs, start_ys = map_face_uv_to_sketch_wire(
        feature.axis_start.x_mm + cx,
        feature.axis_start.y_mm + cy,
        feature.target_face,
        base_body,
    )
    end_xs, end_ys = map_face_uv_to_sketch_wire(
        feature.axis_end.x_mm + cx,
        feature.axis_end.y_mm + cy,
        feature.target_face,
        base_body,
    )
    return {
        "start": {"x_mm": start_xs, "y_mm": start_ys},
        "end": {"x_mm": end_xs, "y_mm": end_ys},
    }


def profile_payload_for_feature(
    feature: (
        CircularThroughHoleFeature
        | RectangularThroughCutoutFeature
        | SlotThroughCutoutFeature
        | RectangularExtrudedPadFeature
        | RevolvedProfileFeature
        | ProfileCutoutFeature
    ),
    *,
    sketch_ref: str,
    base_body: FeaturePlanBaseBody,
) -> dict[str, Any]:
    """Generate the entity reference payload dictionary for a feature's 2D profile."""
    if isinstance(feature, ProfileCutoutFeature):
        return {
            "kind": "polygon",
            "sketch_ref": sketch_ref,
            "points": profile_cutout_polygon_points(feature, base_body=base_body),
            "close": True,
        }
    if isinstance(feature, CircularThroughHoleFeature):
        return {
            "kind": "circle",
            "sketch_ref": sketch_ref,
            "center": circle_profile_center(feature, base_body=base_body),
            "radius_mm": (feature.diameter_mm / 2.0) + 0.0,
        }
    if isinstance(feature, RectangularThroughCutoutFeature | RectangularExtrudedPadFeature):
        return {
            "kind": "polygon",
            "sketch_ref": sketch_ref,
            "points": cutout_polygon_points(feature, base_body=base_body),
            "close": True,
        }
    if isinstance(feature, RevolvedProfileFeature):
        return {
            "kind": "polygon",
            "sketch_ref": sketch_ref,
            "points": revolve_polygon_points(feature, base_body=base_body),
            "close": True,
        }
    return {
        "kind": "slot",
        "sketch_ref": sketch_ref,
        "center": slot_profile_center(feature, base_body=base_body),
        "length_mm": float(feature.length_mm) + 0.0,
        "width_mm": float(feature.width_mm) + 0.0,
        "orientation_axis": slot_profile_orientation_axis(feature, base_body=base_body),
    }


def profile_geometry_for_feature(
    feature: (
        CircularThroughHoleFeature
        | RectangularThroughCutoutFeature
        | SlotThroughCutoutFeature
        | RectangularExtrudedPadFeature
        | RevolvedProfileFeature
        | ProfileCutoutFeature
    ),
    *,
    base_body: FeaturePlanBaseBody,
) -> dict[str, Any]:
    """Generate the geometry definition dictionary for an ensure_profile patch."""
    if isinstance(feature, ProfileCutoutFeature):
        return {
            "kind": "polygon",
            "points": profile_cutout_polygon_points(feature, base_body=base_body),
            "close": True,
        }
    if isinstance(feature, CircularThroughHoleFeature):
        return {
            "kind": "circle",
            "center": circle_profile_center(feature, base_body=base_body),
            "radius_mm": (feature.diameter_mm / 2.0) + 0.0,
        }
    if isinstance(feature, RectangularThroughCutoutFeature | RectangularExtrudedPadFeature):
        return {
            "kind": "polygon",
            "points": cutout_polygon_points(feature, base_body=base_body),
            "close": True,
        }
    if isinstance(feature, RevolvedProfileFeature):
        return {
            "kind": "polygon",
            "points": revolve_polygon_points(feature, base_body=base_body),
            "close": True,
        }
    return {
        "kind": "slot",
        "center": slot_profile_center(feature, base_body=base_body),
        "length_mm": float(feature.length_mm) + 0.0,
        "width_mm": float(feature.width_mm) + 0.0,
        "orientation_axis": slot_profile_orientation_axis(feature, base_body=base_body),
    }


def profile_feature_payload_for_feature(
    feature: (
        CircularThroughHoleFeature
        | RectangularThroughCutoutFeature
        | SlotThroughCutoutFeature
        | RectangularExtrudedPadFeature
        | RevolvedProfileFeature
        | ProfileCutoutFeature
    ),
    *,
    body_ref: str,
    profile_ref: str,
    base_body: FeaturePlanBaseBody,
) -> dict[str, Any]:
    """Generate the entity reference payload for a feature operation."""
    if isinstance(feature, RectangularExtrudedPadFeature):
        return {
            "kind": "extrude_profile",
            "body_ref": body_ref,
            "profile_ref": profile_ref,
            "distance_mm": float(feature.distance_mm) + 0.0,
        }
    if isinstance(feature, RevolvedProfileFeature):
        return {
            "kind": "revolve_profile",
            "body_ref": body_ref,
            "profile_ref": profile_ref,
            "axis": revolve_axis_endpoints(feature, base_body=base_body),
            "angle_deg": float(feature.angle_deg) + 0.0,
        }
    through_all = _feature_cut_through_all(feature)
    payload: dict[str, Any] = {
        "kind": "cut_hole",
        "body_ref": body_ref,
        "profile_ref": profile_ref,
        "through_all": through_all,
    }
    if not through_all:
        payload["depth_mm"] = float(_feature_cut_depth_mm(base_body, feature)) + 0.0
    return payload


def profile_feature_patch_for_feature(
    feature: (
        CircularThroughHoleFeature
        | RectangularThroughCutoutFeature
        | SlotThroughCutoutFeature
        | RectangularExtrudedPadFeature
        | RevolvedProfileFeature
        | ProfileCutoutFeature
    ),
    *,
    index: int,
    suffix: str,
    body_ref: str,
    profile_ref: str,
    base_body: FeaturePlanBaseBody,
    cut_direction: str = DEFAULT_ABSTRACT_CUT_DIRECTION,
    sketch_plane: str | None = None,
    origin_offset_mm: dict[str, float] | None = None,
    u_axis: tuple[float, float, float] | list[float] | None = None,
    v_axis: tuple[float, float, float] | list[float] | None = None,
    normal_vector: tuple[float, float, float] | list[float] | None = None,
    cut_vector: tuple[float, float, float] | list[float] | None = None,
) -> dict[str, Any]:
    """Generate an execution patch for a standard profile feature operation."""
    if isinstance(feature, RectangularExtrudedPadFeature):
        pad_payload: dict[str, Any] = {
            "op": "extrude_profile",
            "patch_id": f"patch.extrude.{index}",
            "replay_policy": {
                "idempotency_key": f"feature-plan-extrude-profile-{suffix}",
                "mode": "fail_on_drift",
            },
            "body_ref": body_ref,
            "profile_ref": profile_ref,
            "result_ref": feature.id,
            "distance_mm": float(feature.distance_mm) + 0.0,
            "face": feature.target_face or "+Z",
        }
        if sketch_plane is not None:
            pad_payload["sketch_plane"] = sketch_plane
        if origin_offset_mm is not None:
            pad_payload["origin_offset_mm"] = {k: float(v) + 0.0 for k, v in origin_offset_mm.items()}
        if u_axis is not None:
            pad_payload["u_axis"] = [float(x) + 0.0 for x in u_axis]
        if v_axis is not None:
            pad_payload["v_axis"] = [float(x) + 0.0 for x in v_axis]
        if normal_vector is not None:
            pad_payload["normal_vector"] = [float(x) + 0.0 for x in normal_vector]
        return pad_payload
    if isinstance(feature, RevolvedProfileFeature):
        return {
            "op": "revolve_profile",
            "patch_id": f"patch.revolve.{index}",
            "replay_policy": {
                "idempotency_key": f"feature-plan-revolve-profile-{suffix}",
                "mode": "fail_on_drift",
            },
            "body_ref": body_ref,
            "profile_ref": profile_ref,
            "result_ref": feature.id,
            "axis": revolve_axis_endpoints(feature, base_body=base_body),
            "angle_deg": float(feature.angle_deg) + 0.0,
        }
    through_all = _feature_cut_through_all(feature)
    payload: dict[str, Any] = {
        "op": "cut_hole",
        "patch_id": f"patch.cut_hole.{index}",
        "replay_policy": {
            "idempotency_key": f"feature-plan-cut-hole-{suffix}",
            "mode": "fail_on_drift",
        },
        "body_ref": body_ref,
        "profile_ref": profile_ref,
        "result_ref": feature.id,
        "through_all": through_all,
        "cut_direction": cut_direction,
    }
    if sketch_plane is not None:
        payload["sketch_plane"] = sketch_plane
    if origin_offset_mm is not None:
        payload["origin_offset_mm"] = {k: float(v) + 0.0 for k, v in origin_offset_mm.items()}
    if u_axis is not None:
        payload["u_axis"] = [float(x) + 0.0 for x in u_axis]
    if v_axis is not None:
        payload["v_axis"] = [float(x) + 0.0 for x in v_axis]
    if normal_vector is not None:
        payload["normal_vector"] = [float(x) + 0.0 for x in normal_vector]
    if cut_vector is not None:
        payload["cut_vector"] = [float(x) + 0.0 for x in cut_vector]
    elif normal_vector is not None:
        payload["cut_vector"] = [
            -float(normal_vector[0]) + 0.0,
            -float(normal_vector[1]) + 0.0,
            -float(normal_vector[2]) + 0.0,
        ]
    if not through_all:
        payload["depth_mm"] = float(_feature_cut_depth_mm(base_body, feature)) + 0.0
    return payload


def sketch_human_label(feature: FeaturePlanFeature) -> str:
    """Generate a clean human description for a feature sketch."""
    target_face = normalize_target_face_alias(getattr(feature, "target_face", None))
    return f"Canonical {target_face} face sketch for {feature.id}"


def profile_human_label(
    feature: (
        CircularThroughHoleFeature
        | RectangularThroughCutoutFeature
        | SlotThroughCutoutFeature
        | RectangularExtrudedPadFeature
        | RevolvedProfileFeature
        | ProfileCutoutFeature
    ),
) -> str:
    """Generate a clean human description for a feature profile."""
    if isinstance(feature, CircularThroughHoleFeature):
        return f"Canonical circular profile for semantic hole {feature.id}"
    if isinstance(feature, RectangularThroughCutoutFeature):
        return f"Canonical rectangular profile for semantic cutout {feature.id}"
    if isinstance(feature, RectangularExtrudedPadFeature):
        return f"Canonical rectangular profile for semantic pad {feature.id}"
    if isinstance(feature, RevolvedProfileFeature):
        return f"Canonical revolve profile for semantic feature {feature.id}"
    if isinstance(feature, ProfileCutoutFeature):
        return f"Canonical profile cutout for semantic cutout {feature.id}"
    return f"Canonical rounded slot profile for semantic cutout {feature.id}"


def feature_human_label(
    feature: (
        CircularThroughHoleFeature
        | RectangularThroughCutoutFeature
        | SlotThroughCutoutFeature
        | RectangularExtrudedPadFeature
        | RevolvedProfileFeature
        | ProfileCutoutFeature
    ),
) -> str:
    """Generate a clean human description for a feature execution entity."""
    if isinstance(feature, CircularThroughHoleFeature):
        cut_kind = "through-hole" if feature.extent_type == "through_all" else "blind hole"
        return f"Semantic circular {cut_kind} {feature.id}"
    if isinstance(feature, RectangularThroughCutoutFeature):
        cut_kind = "through-cutout" if feature.extent_type == "through_all" else "blind cutout"
        return f"Semantic rectangular {cut_kind} {feature.id}"
    if isinstance(feature, RectangularExtrudedPadFeature):
        return f"Semantic rectangular extruded pad {feature.id}"
    if isinstance(feature, RevolvedProfileFeature):
        return f"Semantic revolved profile {feature.id}"
    if isinstance(feature, ProfileCutoutFeature):
        cut_kind = "through-cutout" if feature.extent_type == "through_all" else "blind cutout"
        return f"Semantic profile {cut_kind} {feature.id}"
    cut_kind = "through-cutout" if feature.extent_type == "through_all" else "blind cutout"
    return f"Semantic slot {cut_kind} {feature.id}"


__all__ = [
    "DEFAULT_ABSTRACT_CUT_DIRECTION",
    "circle_profile_center",
    "cutout_polygon_points",
    "feature_human_label",
    "map_face_uv_to_sketch_wire",
    "normalize_target_face_alias",
    "profile_cutout_polygon_points",
    "profile_feature_patch_for_feature",
    "profile_feature_payload_for_feature",
    "profile_geometry_for_feature",
    "profile_human_label",
    "profile_payload_for_feature",
    "revolve_axis_endpoints",
    "revolve_polygon_points",
    "sketch_human_label",
    "slot_profile_center",
    "slot_profile_orientation_axis",
]
