"""Unified face context abstraction and SO(3) planar coordinate transforms.

This module provides bidirectional mapping between 2D sketch plane coordinates (u, v)
and 3D global Cartesian (x, y, z) space across all canonical CAD reference planes.

Invariants:
    1. Orthonormal Basis: |u| = |v| = |n| = 1 and u . v = v . n = n . u = 0
    2. Chirality: u x v = n (Right-Handed Coordinate Frame)
    3. Rotation Matrix: R = [u v n] belongs to the Special Orthogonal Group SO(3) with det(R) = +1.
    4. Orientation: 2D CCW profiles retain their positive orientation relative to the
       target face outward normal when mapped into 3D.
    5. Pure Domain Geometry: FaceContext represents the intrinsic rigid geometry of the
       solid body face and is completely decoupled from feature modification hacks.
    6. Frame Origin Invariant: (u=0, v=0) corresponds precisely to the face frame origin
       (the exact physical area centroid for planar faces; the axial midline reference point
       for curved cylindrical and revolved surfaces).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from geometry.plan_models import (
    BodyPlacement,
    CylinderBaseBody,
    FeaturePlanBaseBody,
    FeaturePlanFeature,
    RectangularBaseBody,
    RevolvedShaftBaseBody,
    SpurGearBaseBody,
)


@dataclass(frozen=True)
class FaceContext:
    """Rigid planar coordinate frame and bounding extents for a solid body face."""

    target_face: str
    normal_axis: Literal["x", "y", "z"]
    sketch_plane: Literal["XY", "XZ", "YZ"]
    half_extents_u: float | None
    half_extents_v: float | None
    origin_offset_mm: dict[str, float]
    u_axis: tuple[float, float, float]
    v_axis: tuple[float, float, float]
    normal_vector: tuple[float, float, float]

    def project_uv_to_world(
        self,
        u: float,
        v: float,
        body_placement: BodyPlacement | None = None,
    ) -> tuple[float, float, float]:
        """Project a 2D sketch plane point (u, v) into 3D world Cartesian coordinates (x, y, z).

        Formula:
            P_world = P_body_placement + O_frame_origin + u * u_axis + v * v_axis
        """
        px = body_placement.x_mm if body_placement is not None else 0.0
        py = body_placement.y_mm if body_placement is not None else 0.0
        pz = body_placement.z_mm if body_placement is not None else 0.0

        ox = self.origin_offset_mm.get("x_mm", 0.0)
        oy = self.origin_offset_mm.get("y_mm", 0.0)
        oz = self.origin_offset_mm.get("z_mm", 0.0)

        wx = px + ox + u * self.u_axis[0] + v * self.v_axis[0]
        wy = py + oy + u * self.u_axis[1] + v * self.v_axis[1]
        wz = pz + oz + u * self.u_axis[2] + v * self.v_axis[2]

        return (wx, wy, wz)

    def project_world_to_uv(
        self,
        x: float,
        y: float,
        z: float,
        body_placement: BodyPlacement | None = None,
    ) -> tuple[float, float]:
        """Project a 3D world point (x, y, z) onto the 2D sketch plane coordinates (u, v).

        Because (u_axis, v_axis, normal_vector) form an orthonormal basis in SO(3),
        the inverse projection is an exact dot product with zero numerical solve:
            d = P_world - (P_body_placement + O_frame_origin)
            u = d . u_axis
            v = d . v_axis
        """
        px = body_placement.x_mm if body_placement is not None else 0.0
        py = body_placement.y_mm if body_placement is not None else 0.0
        pz = body_placement.z_mm if body_placement is not None else 0.0

        ox = self.origin_offset_mm.get("x_mm", 0.0)
        oy = self.origin_offset_mm.get("y_mm", 0.0)
        oz = self.origin_offset_mm.get("z_mm", 0.0)

        dx = x - (px + ox)
        dy = y - (py + oy)
        dz = z - (pz + oz)

        u = dx * self.u_axis[0] + dy * self.u_axis[1] + dz * self.u_axis[2]
        v = dx * self.v_axis[0] + dy * self.v_axis[1] + dz * self.v_axis[2]

        return (u, v)

    def distance_to_face_plane(
        self,
        x: float,
        y: float,
        z: float,
        body_placement: BodyPlacement | None = None,
    ) -> float:
        """Calculate signed perpendicular distance from a 3D point to the face plane."""
        px = body_placement.x_mm if body_placement is not None else 0.0
        py = body_placement.y_mm if body_placement is not None else 0.0
        pz = body_placement.z_mm if body_placement is not None else 0.0

        ox = self.origin_offset_mm.get("x_mm", 0.0)
        oy = self.origin_offset_mm.get("y_mm", 0.0)
        oz = self.origin_offset_mm.get("z_mm", 0.0)

        dx = x - (px + ox)
        dy = y - (py + oy)
        dz = z - (pz + oz)

        return (
            dx * self.normal_vector[0]
            + dy * self.normal_vector[1]
            + dz * self.normal_vector[2]
        )

    def rotation_matrix(
        self,
    ) -> tuple[
        tuple[float, float, float],
        tuple[float, float, float],
        tuple[float, float, float],
    ]:
        """Return the 3x3 rotation matrix R = [u v n] representing the face coordinate frame."""
        return (
            (self.u_axis[0], self.v_axis[0], self.normal_vector[0]),
            (self.u_axis[1], self.v_axis[1], self.normal_vector[1]),
            (self.u_axis[2], self.v_axis[2], self.normal_vector[2]),
        )


def resolve_face_context(
    target_face: str,
    base_body: FeaturePlanBaseBody,
    feature: FeaturePlanFeature | None = None,
) -> FaceContext:
    """Resolve face context (SO(3) basis, reference plane, extents, and frame origin) for a target face.

    Returns a FaceContext whose ``origin_offset_mm`` is the face-relative frame origin from the
    body's own local origin — it does not include body placement. Callers can use
    ``FaceContext.project_uv_to_world(u, v, body.placement)`` to obtain world-space coordinates.
    """
    normal_axis: Literal["x", "y", "z"]
    sketch_plane: Literal["XY", "XZ", "YZ"]
    u_axis: tuple[float, float, float]
    v_axis: tuple[float, float, float]
    normal_vector: tuple[float, float, float]

    # 0. Normalize conversational aliases
    tf = target_face.lower().strip()
    if tf in {"top", "up", "+z", "z+", "z"}:
        target_face = "+Z"
    elif tf in {"bottom", "down", "-z", "z-"}:
        target_face = "-Z"
    elif tf in {"right", "+x", "x+", "x"}:
        target_face = "+X"
    elif tf in {"left", "-x", "x-"}:
        target_face = "-X"
    elif tf in {"back", "+y", "y+", "y"}:
        target_face = "+Y"
    elif tf in {"front", "-y", "y-"}:
        target_face = "-Y"
    else:
        target_face = "+Z"

    # 1. Canonical CAD Orthonormal Right-Handed Face Basis (SO(3) with Vertical V = +Z)
    if target_face == "+Z":
        normal_axis = "z"
        sketch_plane = "XY"
        normal_vector = (0.0, 0.0, 1.0)
        u_axis = (1.0, 0.0, 0.0)
        v_axis = (0.0, 1.0, 0.0)
    elif target_face == "-Z":
        normal_axis = "z"
        sketch_plane = "XY"
        normal_vector = (0.0, 0.0, -1.0)
        u_axis = (1.0, 0.0, 0.0)
        v_axis = (0.0, -1.0, 0.0)
    elif target_face == "+X":
        normal_axis = "x"
        sketch_plane = "YZ"
        normal_vector = (1.0, 0.0, 0.0)
        u_axis = (0.0, 1.0, 0.0)
        v_axis = (0.0, 0.0, 1.0)
    elif target_face == "-X":
        normal_axis = "x"
        sketch_plane = "YZ"
        normal_vector = (-1.0, 0.0, 0.0)
        u_axis = (0.0, -1.0, 0.0)
        v_axis = (0.0, 0.0, 1.0)
    elif target_face == "+Y":
        normal_axis = "y"
        sketch_plane = "XZ"
        normal_vector = (0.0, 1.0, 0.0)
        u_axis = (-1.0, 0.0, 0.0)
        v_axis = (0.0, 0.0, 1.0)
    else:  # "-Y"
        normal_axis = "y"
        sketch_plane = "XZ"
        normal_vector = (0.0, -1.0, 0.0)
        u_axis = (1.0, 0.0, 0.0)
        v_axis = (0.0, 0.0, 1.0)

    # 2. Extents & Face Frame Origin Offsets
    half_extents_u: float | None = None
    half_extents_v: float | None = None
    relative_offset: dict[str, float] = {"x_mm": 0.0, "y_mm": 0.0, "z_mm": 0.0}

    if isinstance(base_body, RectangularBaseBody):
        if target_face in {"+X", "-X"}:
            half_extents_u = base_body.width_mm / 2.0
            half_extents_v = base_body.thickness_mm / 2.0
            relative_offset["x_mm"] = base_body.length_mm / 2.0 if target_face == "+X" else -base_body.length_mm / 2.0
            relative_offset["z_mm"] = base_body.thickness_mm / 2.0
        elif target_face in {"+Y", "-Y"}:
            half_extents_u = base_body.length_mm / 2.0
            half_extents_v = base_body.thickness_mm / 2.0
            relative_offset["y_mm"] = base_body.width_mm / 2.0 if target_face == "+Y" else -base_body.width_mm / 2.0
            relative_offset["z_mm"] = base_body.thickness_mm / 2.0
        elif target_face == "+Z":
            half_extents_u = base_body.length_mm / 2.0
            half_extents_v = base_body.width_mm / 2.0
            relative_offset["z_mm"] = base_body.thickness_mm
        else:  # "-Z"
            half_extents_u = base_body.length_mm / 2.0
            half_extents_v = base_body.width_mm / 2.0
            relative_offset["z_mm"] = 0.0

    elif isinstance(base_body, CylinderBaseBody):
        if target_face == "+Z":
            relative_offset["z_mm"] = base_body.height_mm
        elif target_face == "-Z":
            relative_offset["z_mm"] = 0.0
        else:  # Curved lateral faces (+X, -X, +Y, -Y)
            half_extents_u = None
            half_extents_v = base_body.height_mm / 2.0
            relative_offset["z_mm"] = base_body.height_mm / 2.0

    elif isinstance(base_body, SpurGearBaseBody):
        if target_face == "+Z":
            relative_offset["z_mm"] = base_body.face_width_mm
        elif target_face == "-Z":
            relative_offset["z_mm"] = 0.0
        else:  # Rim lateral faces
            half_extents_u = None
            half_extents_v = base_body.face_width_mm / 2.0
            relative_offset["z_mm"] = base_body.face_width_mm / 2.0

    elif isinstance(base_body, RevolvedShaftBaseBody):
        if target_face == "+Z":
            relative_offset["z_mm"] = base_body.height_mm
        elif target_face == "-Z":
            relative_offset["z_mm"] = 0.0
        else:  # Curved lateral faces
            half_extents_u = None
            half_extents_v = base_body.height_mm / 2.0
            relative_offset["z_mm"] = base_body.height_mm / 2.0

    return FaceContext(
        target_face=target_face,
        normal_axis=normal_axis,
        sketch_plane=sketch_plane,
        half_extents_u=half_extents_u,
        half_extents_v=half_extents_v,
        origin_offset_mm=relative_offset,
        u_axis=u_axis,
        v_axis=v_axis,
        normal_vector=normal_vector,
    )


__all__ = [
    "FaceContext",
    "resolve_face_context",
]
