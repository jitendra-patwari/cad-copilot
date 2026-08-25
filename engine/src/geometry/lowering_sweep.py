"""Swept protrusion intermediate representation (IR) compiler for CAD Copilot.

Spatial Geometry & SO(3) Planar-Path Frame Formulation:
    1. Orthonormal Section Frame:
       For any planar trajectory lying in a face with outward normal n_face in SO(3):
       - Section Normal: n_sec = t_path(theta) (unit tangent to trajectory)
       - U Axis (Elevation): u_sec = n_face (out-of-plane normal, perpendicular to sketch)
       - V Axis (Radial): v_sec = t_path(theta) x n_face (in-plane radial normal)

    2. Chirality & Right-Handed Invariant:
       u_sec x v_sec = n_face x (t_path x n_face) = (n_face . n_face)t_path - (n_face . t_path)n_face = t_path = n_sec
       det([u_sec, v_sec, n_sec]) = +1 (strictly right-handed and orthonormal at all stations).

    3. Singularity-Free Guarantee:
       Unlike the classical Frenet-Serret frame (which suffers a 0/0 curvature singularity on straight lines
       where kappa = 0), this planar-path frame is smooth, well-defined, and non-singular across all
       linear, circular, and arc paths.

    4. Path Parameterization (CCW relative to n_face):
       r(theta) = (u_c + R*cos(theta))*u_face + (v_c + R*sin(theta))*v_face
       t_path(theta) = -sin(theta)*u_face + cos(theta)*v_face
       v_sec(theta) = t_path(theta) x n_face = cos(theta)*u_face + sin(theta)*v_face = r_outward(theta)
       Because v_sec points strictly outward from the center of curvature, the self-intersection guard:
           min(v_sec) > -R_path
       is universally sound and sign-consistent.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from geometry.face_context import resolve_face_context
from geometry.lowering_wire_mapping import (
    map_face_uv_to_sketch_wire,
    normalize_target_face_alias,
)
from geometry.plan_models import FeaturePlan, FeaturePlanBaseBody, SweptProtrusionFeature


@dataclass(frozen=True)
class LoweredSweepResult:
    """Resulting semantic entities, execution patches, and context references from sweep lowering."""

    entities: list[dict[str, Any]]
    patches: list[dict[str, Any]]
    last_sketch_ref: str
    last_profile_ref: str


def _entity(
    ref_id: str,
    kind: str,
    fingerprint: str,
    depends_on_refs: list[str],
    human_label: str,
) -> dict[str, Any]:
    """Helper to build a canonical entity dictionary with dependencies and fingerprint."""
    return {
        "ref_id": ref_id,
        "kind": kind,
        "fingerprint": fingerprint,
        "depends_on_refs": depends_on_refs,
        "human_label": human_label,
    }


def _clean_float(val: float) -> float:
    """Snap sub-picometer floating point residual noise to 0.0 and eliminate -0.0."""
    if abs(val) < 1e-12:
        return 0.0
    return round(val, 9) + 0.0


def _body_placement_origin_offset(base_body: FeaturePlanBaseBody) -> dict[str, float]:
    """Extract (x, y, z) placement coordinates from a base body."""
    placement = getattr(base_body, "placement", None)
    px = float(placement.x_mm) if placement is not None else 0.0
    py = float(placement.y_mm) if placement is not None else 0.0
    pz = float(placement.z_mm) if placement is not None else 0.0
    return {"x_mm": _clean_float(px), "y_mm": _clean_float(py), "z_mm": _clean_float(pz)}


def _compute_section_basis_vectors(
    target_face: str,
    position: str,
    path_type: str,
    base_body: FeaturePlanBaseBody,
    feature: SweptProtrusionFeature,
) -> tuple[list[float], list[float], list[float]]:
    """Derive deterministic (u_axis, v_axis, normal_vector) for a normal-to-curve section plane.

    Mathematical Invariants:
      - n_section = t_path(theta) (unit path tangent)
      - u_section = n_face (out-of-plane face normal)
      - v_section = t_path(theta) x n_face (in-plane radial normal, outward for CCW paths)
      - u_section x v_section = n_section (Right-handed orthonormal frame in SO(3))
    """
    ctx = resolve_face_context(target_face, base_body, feature)
    n_face = [float(x) for x in ctx.normal_vector]
    u_face = [float(x) for x in ctx.u_axis]
    v_face = [float(x) for x in ctx.v_axis]

    t_path: list[float]

    # Resolve parametric angle theta along CCW trajectory curve
    if path_type in {"full_circle", "semicircle", "quarter_arc"}:
        theta: float
        if position == "end":
            if path_type == "quarter_arc":
                theta = math.pi / 2.0
            elif path_type == "semicircle":
                theta = math.pi
            else:
                theta = 2.0 * math.pi
        else:
            theta = 0.0

        # t_path(theta) = -sin(theta)*u_face + cos(theta)*v_face
        sin_t = math.sin(theta)
        cos_t = math.cos(theta)
        t_path = [
            -sin_t * u_face[0] + cos_t * v_face[0],
            -sin_t * u_face[1] + cos_t * v_face[1],
            -sin_t * u_face[2] + cos_t * v_face[2],
        ]
    else:
        # Straight line trajectory along u_face
        t_path = [u_face[0], u_face[1], u_face[2]]

    # Normalize tangent vector
    t_mag = math.hypot(t_path[0], t_path[1], t_path[2])
    if t_mag > 1e-12:
        t_path = [t_path[0] / t_mag, t_path[1] / t_mag, t_path[2] / t_mag]

    # u_sec = n_face (out-of-plane elevation axis)
    u_sec = [n_face[0], n_face[1], n_face[2]]

    # v_sec = t_path x n_face (in-plane radial normal, outward)
    v_sec = [
        t_path[1] * n_face[2] - t_path[2] * n_face[1],
        t_path[2] * n_face[0] - t_path[0] * n_face[2],
        t_path[0] * n_face[1] - t_path[1] * n_face[0],
    ]

    return (
        [_clean_float(x) for x in u_sec],
        [_clean_float(x) for x in v_sec],
        [_clean_float(x) for x in t_path],
    )


def lower_swept_protrusion(
    plan: FeaturePlan,
    feature: SweptProtrusionFeature,
    index: int,
    stable_fingerprint_fn: Callable[[object], str],
    *,
    target_body: FeaturePlanBaseBody,
) -> LoweredSweepResult:
    """Lower a SweptProtrusionFeature into semantic path/section sketches and a sweep patch."""
    entities: list[dict[str, Any]] = []
    patches: list[dict[str, Any]] = []

    target_face = normalize_target_face_alias(feature.target_face)
    ctx = resolve_face_context(target_face, target_body, feature)
    sketch_plane = ctx.sketch_plane

    placement = _body_placement_origin_offset(target_body)
    sketch_origin_offset = {
        "x_mm": placement["x_mm"] + ctx.origin_offset_mm.get("x_mm", 0.0) + 0.0,
        "y_mm": placement["y_mm"] + ctx.origin_offset_mm.get("y_mm", 0.0) + 0.0,
        "z_mm": placement["z_mm"] + ctx.origin_offset_mm.get("z_mm", 0.0) + 0.0,
    }

    # 1. Path Sketch & Profile
    path_sketch_ref = f"sketch.sweep.path.{index}"
    path_profile_ref = f"profile.sweep.path.{index}"

    path_sketch_payload = {
        "kind": "sketch",
        "plane": sketch_plane,
        "origin_offset_mm": sketch_origin_offset,
        "body_ref": feature.target_body_id,
        "sketch_mode": "sketch_object",
    }

    entities.append(
        _entity(
            ref_id=path_sketch_ref,
            kind="sketch",
            fingerprint=stable_fingerprint_fn(path_sketch_payload),
            depends_on_refs=[feature.target_body_id],
            human_label=f"Canonical {sketch_plane} face path sketch for sweep {feature.id}",
        )
    )
    patches.append(
        {
            "op": "ensure_sketch",
            "patch_id": f"patch.sweep.path.sketch.{index}",
            "replay_policy": {
                "idempotency_key": f"feature-plan-ensure-sketch-sweep-path-{index}",
                "mode": "verify_equivalent",
            },
            "sketch_ref": path_sketch_ref,
            "body_ref": feature.target_body_id,
            "plane": sketch_plane,
            "origin_offset_mm": sketch_origin_offset,
            "sketch_mode": "sketch_object",
        }
    )

    path_cx, path_cy = map_face_uv_to_sketch_wire(
        feature.center_x_mm,
        feature.center_y_mm,
        feature.target_face,
        target_body,
    )
    path_geom: dict[str, Any] = {
        "kind": "circle",
        "center": {
            "x_mm": path_cx,
            "y_mm": path_cy,
        },
        "radius_mm": float(feature.path.radius_mm) + 0.0,
    }

    path_profile_payload = {
        **path_geom,
        "sketch_ref": path_sketch_ref,
    }
    entities.append(
        _entity(
            ref_id=path_profile_ref,
            kind="profile",
            fingerprint=stable_fingerprint_fn(path_profile_payload),
            depends_on_refs=[path_sketch_ref],
            human_label=f"Canonical path profile for sweep {feature.id}",
        )
    )
    patches.append(
        {
            "op": "ensure_profile",
            "patch_id": f"patch.sweep.path.profile.{index}",
            "replay_policy": {
                "idempotency_key": f"feature-plan-ensure-profile-sweep-path-{index}",
                "mode": "ensure_present",
            },
            "profile_ref": path_profile_ref,
            "sketch_ref": path_sketch_ref,
            "geometry": path_geom,
        }
    )

    section_refs: list[str] = []
    last_sketch_ref = path_sketch_ref
    last_profile_ref = path_profile_ref

    # 2. Section Reference Planes, Sketches, and Profiles
    for s_idx, section in enumerate(feature.cross_sections, start=1):
        ref_plane_ref = f"refplane.sweep.section.{index}.{s_idx}"
        section_sketch_ref = f"sketch.sweep.section.{index}.{s_idx}"
        section_profile_ref = f"profile.sweep.section.{index}.{s_idx}"
        section_refs.append(section_profile_ref)
        last_sketch_ref = section_sketch_ref
        last_profile_ref = section_profile_ref

        u_axis, v_axis, normal_vector = _compute_section_basis_vectors(
            target_face=target_face,
            position=section.position,
            path_type=feature.path.type,
            base_body=target_body,
            feature=feature,
        )

        ref_plane_payload = {
            "kind": "ref_plane",
            "type": "normal_to_curve",
            "curve_ref": path_profile_ref,
            "position": section.position,
            "orientation_plane": sketch_plane,
            "u_axis": u_axis,
            "v_axis": v_axis,
            "normal_vector": normal_vector,
        }
        entities.append(
            _entity(
                ref_id=ref_plane_ref,
                kind="ref_plane",
                fingerprint=stable_fingerprint_fn(ref_plane_payload),
                depends_on_refs=[path_profile_ref],
                human_label=f"Canonical section {s_idx} ref plane for sweep {feature.id}",
            )
        )
        patches.append(
            {
                "op": "ensure_ref_plane",
                "patch_id": f"patch.sweep.section.refplane.{index}.{s_idx}",
                "replay_policy": {
                    "idempotency_key": f"feature-plan-ensure-refplane-sweep-section-{index}-{s_idx}",
                    "mode": "ensure_present",
                },
                "ref_plane_ref": ref_plane_ref,
                "type": "normal_to_curve",
                "curve_ref": path_profile_ref,
                "position": section.position,
                "orientation_plane": sketch_plane,
                "u_axis": u_axis,
                "v_axis": v_axis,
                "normal_vector": normal_vector,
            }
        )

        # 3. Section Sketch
        section_sketch_payload = {
            "kind": "sketch",
            "ref_plane_ref": ref_plane_ref,
            "body_ref": feature.target_body_id,
        }
        entities.append(
            _entity(
                ref_id=section_sketch_ref,
                kind="sketch",
                fingerprint=stable_fingerprint_fn(section_sketch_payload),
                depends_on_refs=[feature.target_body_id, ref_plane_ref],
                human_label=f"Canonical section {s_idx} sketch for sweep {feature.id}",
            )
        )
        patches.append(
            {
                "op": "ensure_sketch",
                "patch_id": f"patch.sweep.section.sketch.{index}.{s_idx}",
                "replay_policy": {
                    "idempotency_key": f"feature-plan-ensure-sketch-sweep-section-{index}-{s_idx}",
                    "mode": "verify_equivalent",
                },
                "sketch_ref": section_sketch_ref,
                "body_ref": feature.target_body_id,
                "ref_plane_ref": ref_plane_ref,
            }
        )

        # 4. Section Profile Geometry
        sec_geom: dict[str, Any]
        offset_x = float(feature.path.radius_mm) if feature.path.type == "full_circle" else 0.0
        offset_z = 0.0

        if section.type == "circle":
            sec_geom = {
                "kind": "circle",
                "center": {"x_mm": offset_x + 0.0, "y_mm": offset_z + 0.0},
                "radius_mm": (float(section.diameter_mm) / 2.0) + 0.0,
            }
        elif section.type == "rectangle":
            half_w = float(section.width_mm) / 2.0
            half_h = float(section.height_mm) / 2.0
            sec_geom = {
                "kind": "polygon",
                "points": [
                    {"x_mm": offset_x - half_w + 0.0, "y_mm": offset_z - half_h + 0.0},
                    {"x_mm": offset_x + half_w + 0.0, "y_mm": offset_z - half_h + 0.0},
                    {"x_mm": offset_x + half_w + 0.0, "y_mm": offset_z + half_h + 0.0},
                    {"x_mm": offset_x - half_w + 0.0, "y_mm": offset_z + half_h + 0.0},
                ],
                "close": True,
            }
        else:
            pts = [
                {
                    "x_mm": float(point.x_mm) + offset_x + 0.0,
                    "y_mm": float(point.y_mm) + offset_z + 0.0,
                }
                for point in section.profile_points
            ]
            sec_geom = {
                "kind": "polygon",
                "points": pts,
                "close": True,
            }

        section_profile_payload = {
            **sec_geom,
            "sketch_ref": section_sketch_ref,
        }
        entities.append(
            _entity(
                ref_id=section_profile_ref,
                kind="profile",
                fingerprint=stable_fingerprint_fn(section_profile_payload),
                depends_on_refs=[section_sketch_ref],
                human_label=f"Canonical section {s_idx} profile for sweep {feature.id}",
            )
        )
        patches.append(
            {
                "op": "ensure_profile",
                "patch_id": f"patch.sweep.section.profile.{index}.{s_idx}",
                "replay_policy": {
                    "idempotency_key": f"feature-plan-ensure-profile-sweep-section-{index}-{s_idx}",
                    "mode": "ensure_present",
                },
                "profile_ref": section_profile_ref,
                "sketch_ref": section_sketch_ref,
                "geometry": sec_geom,
            }
        )

    # 5. Swept Protrusion Feature Execution
    sweep_payload = {
        "kind": "sweep_protrusion",
        "body_ref": feature.target_body_id,
        "path_refs": [path_profile_ref],
        "section_refs": section_refs,
        "direction": "into_solid",
    }

    entities.append(
        _entity(
            ref_id=feature.id,
            kind="feature",
            fingerprint=stable_fingerprint_fn(sweep_payload),
            depends_on_refs=[feature.target_body_id, path_profile_ref, *section_refs],
            human_label=f"Semantic swept protrusion {feature.id}",
        )
    )
    patches.append(
        {
            "op": "sweep_protrusion",
            "patch_id": f"patch.sweep_protrusion.{index}",
            "replay_policy": {
                "idempotency_key": f"feature-plan-sweep-protrusion-{index}",
                "mode": "fail_on_drift",
            },
            "body_ref": feature.target_body_id,
            "path_refs": [path_profile_ref],
            "section_refs": section_refs,
            "result_ref": feature.id,
            "direction": "into_solid",
        }
    )

    return LoweredSweepResult(
        entities=entities,
        patches=patches,
        last_sketch_ref=last_sketch_ref,
        last_profile_ref=last_profile_ref,
    )


__all__ = [
    "LoweredSweepResult",
    "lower_swept_protrusion",
]
