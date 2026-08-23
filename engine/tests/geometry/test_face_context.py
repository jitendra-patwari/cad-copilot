"""Unit and invariant tests for FaceContext and SO(3) planar coordinate projections."""

from __future__ import annotations

import math

import pytest
from geometry import (
    BodyPlacement,
    CylinderBaseBody,
    RectangularBaseBody,
    RevolvedShaftBaseBody,
    SpurGearBaseBody,
    resolve_face_context,
)


def _cross_product(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot_product(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _det_3x3(m: tuple[tuple[float, float, float], ...]) -> float:
    return (
        m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
        - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
        + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
    )


class TestSO3FaceBasisInvariants:
    """Verify that all 6 canonical planar faces form a valid SO(3) orthonormal right-handed basis."""

    @pytest.mark.parametrize(
        ("face_alias", "canonical_face", "expected_plane", "expected_axis"),
        [
            ("+Z", "+Z", "XY", "z"),
            ("top", "+Z", "XY", "z"),
            ("-Z", "-Z", "XY", "z"),
            ("bottom", "-Z", "XY", "z"),
            ("+X", "+X", "YZ", "x"),
            ("right", "+X", "YZ", "x"),
            ("-X", "-X", "YZ", "x"),
            ("left", "-X", "YZ", "x"),
            ("+Y", "+Y", "XZ", "y"),
            ("back", "+Y", "XZ", "y"),
            ("-Y", "-Y", "XZ", "y"),
            ("front", "-Y", "XZ", "y"),
        ],
    )
    def test_face_context_so3_and_chirality(
        self,
        face_alias: str,
        canonical_face: str,
        expected_plane: str,
        expected_axis: str,
    ) -> None:
        body = RectangularBaseBody(id="body.main", length_mm=80.0, width_mm=40.0, thickness_mm=6.0)
        ctx = resolve_face_context(face_alias, body)

        assert ctx.target_face == canonical_face
        assert ctx.sketch_plane == expected_plane
        assert ctx.normal_axis == expected_axis

        # 1. Unit Length
        assert math.isclose(math.hypot(*ctx.u_axis), 1.0, rel_tol=1e-12)
        assert math.isclose(math.hypot(*ctx.v_axis), 1.0, rel_tol=1e-12)
        assert math.isclose(math.hypot(*ctx.normal_vector), 1.0, rel_tol=1e-12)

        # 2. Mutual Orthogonality
        assert math.isclose(_dot_product(ctx.u_axis, ctx.v_axis), 0.0, abs_tol=1e-12)
        assert math.isclose(_dot_product(ctx.u_axis, ctx.normal_vector), 0.0, abs_tol=1e-12)
        assert math.isclose(_dot_product(ctx.v_axis, ctx.normal_vector), 0.0, abs_tol=1e-12)

        # 3. Chirality: u x v = n (Right-Handed System)
        cross_uv = _cross_product(ctx.u_axis, ctx.v_axis)
        assert math.isclose(cross_uv[0], ctx.normal_vector[0], abs_tol=1e-12)
        assert math.isclose(cross_uv[1], ctx.normal_vector[1], abs_tol=1e-12)
        assert math.isclose(cross_uv[2], ctx.normal_vector[2], abs_tol=1e-12)

        # 4. Rotation Matrix in SO(3): det(R) = +1.0
        rot = ctx.rotation_matrix()
        det = _det_3x3(rot)
        assert math.isclose(det, 1.0, abs_tol=1e-12)


class TestFaceCentroidAccuracy:
    """Verify that (u=0, v=0) maps to the true 3D physical area centroid of planar faces."""

    def test_rectangular_face_centroids(self) -> None:
        body = RectangularBaseBody(id="body.main", length_mm=80.0, width_mm=40.0, thickness_mm=6.0)

        # +Z: Centroid is (0, 0, 6)
        ctx_top = resolve_face_context("+Z", body)
        assert ctx_top.project_uv_to_world(0.0, 0.0) == (0.0, 0.0, 6.0)

        # -Z: Centroid is (0, 0, 0)
        ctx_bot = resolve_face_context("-Z", body)
        assert ctx_bot.project_uv_to_world(0.0, 0.0) == (0.0, 0.0, 0.0)

        # +X: Centroid is (+40, 0, 3)
        ctx_right = resolve_face_context("+X", body)
        assert ctx_right.project_uv_to_world(0.0, 0.0) == (40.0, 0.0, 3.0)

        # -X: Centroid is (-40, 0, 3)
        ctx_left = resolve_face_context("-X", body)
        assert ctx_left.project_uv_to_world(0.0, 0.0) == (-40.0, 0.0, 3.0)

        # +Y: Centroid is (0, +20, 3)
        ctx_back = resolve_face_context("+Y", body)
        assert ctx_back.project_uv_to_world(0.0, 0.0) == (0.0, 20.0, 3.0)

        # -Y: Centroid is (0, -20, 3)
        ctx_front = resolve_face_context("-Y", body)
        assert ctx_front.project_uv_to_world(0.0, 0.0) == (0.0, -20.0, 3.0)


class TestBodyFaceResolutions:
    """Verify frame origin and axial extents across all primitive body families."""

    def test_cylinder_face_resolution(self) -> None:
        body = CylinderBaseBody(id="body.cyl", radius_mm=10.0, height_mm=25.0)
        ctx_top = resolve_face_context("+Z", body)
        assert ctx_top.origin_offset_mm["z_mm"] == 25.0

        ctx_bot = resolve_face_context("-Z", body)
        assert ctx_bot.origin_offset_mm["z_mm"] == 0.0

        ctx_side = resolve_face_context("+X", body)
        assert ctx_side.origin_offset_mm["z_mm"] == 12.5
        assert ctx_side.half_extents_u is None
        assert ctx_side.half_extents_v == 12.5

    def test_spur_gear_face_resolution(self) -> None:
        body = SpurGearBaseBody(id="body.gear", tooth_count=20, module_mm=2.0, face_width_mm=15.0)
        ctx_top = resolve_face_context("+Z", body)
        assert ctx_top.origin_offset_mm["z_mm"] == 15.0

        ctx_bot = resolve_face_context("-Z", body)
        assert ctx_bot.origin_offset_mm["z_mm"] == 0.0

        ctx_rim = resolve_face_context("+X", body)
        assert ctx_rim.origin_offset_mm["z_mm"] == 7.5
        assert ctx_rim.half_extents_u is None
        assert ctx_rim.half_extents_v == 7.5

    def test_revolved_shaft_face_resolution(self) -> None:
        body = RevolvedShaftBaseBody(id="body.shaft", radius_mm=8.0, height_mm=50.0, profile_points=())
        ctx_top = resolve_face_context("+Z", body)
        assert ctx_top.origin_offset_mm["z_mm"] == 50.0

        ctx_bot = resolve_face_context("-Z", body)
        assert ctx_bot.origin_offset_mm["z_mm"] == 0.0

        ctx_side = resolve_face_context("+X", body)
        assert ctx_side.origin_offset_mm["z_mm"] == 25.0
        assert ctx_side.half_extents_u is None
        assert ctx_side.half_extents_v == 25.0


class TestVectorProjections:
    """Verify bidirectional forward and inverse vector projection accuracy."""

    @pytest.mark.parametrize("face", ["+Z", "-Z", "+X", "-X", "+Y", "-Y"])
    def test_bidirectional_roundtrip_precision(self, face: str) -> None:
        body = RectangularBaseBody(
            id="body.main",
            length_mm=100.0,
            width_mm=60.0,
            thickness_mm=12.0,
            placement=BodyPlacement(x_mm=25.0, y_mm=-15.0, z_mm=4.0),
        )
        ctx = resolve_face_context(face, body)

        # Test arbitrary (u, v) points within and outside boundary
        test_points = [
            (0.0, 0.0),
            (10.5, -5.25),
            (-20.0, 15.0),
            (3.14159, -2.71828),
        ]

        for u_orig, v_orig in test_points:
            # Forward projection: (u, v) -> (x, y, z)
            wx, wy, wz = ctx.project_uv_to_world(u_orig, v_orig, body.placement)

            # Inverse projection: (x, y, z) -> (u, v)
            u_proj, v_proj = ctx.project_world_to_uv(wx, wy, wz, body.placement)

            # Error must be below 10^-12 mm (surpassing 10^-6 mm NFR-2 / AC-1 requirement)
            assert math.isclose(u_proj, u_orig, abs_tol=1e-12)
            assert math.isclose(v_proj, v_orig, abs_tol=1e-12)

            # Perpendicular distance to plane must be 0.0
            dist = ctx.distance_to_face_plane(wx, wy, wz, body.placement)
            assert math.isclose(dist, 0.0, abs_tol=1e-12)
