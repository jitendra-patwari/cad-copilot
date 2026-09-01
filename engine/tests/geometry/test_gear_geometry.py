"""Unit and regression tests for deterministic conceptual spur gear outline geometry and analytical calculations."""

from __future__ import annotations

import math

import pytest

from geometry import (
    ProfilePoint2D,
    gear_base_radius,
    gear_pitch_radius,
    gear_root_radius,
    gear_tip_radius,
    involute_point,
    is_polygon_ccw,
    polygon_area,
    spur_gear_outline_points,
    spur_gear_profile_points,
    validate_gear_parameters,
)


class TestGearAnalyticalRadii:
    """Verify analytical circle radius formulas for spur gears."""

    @pytest.mark.parametrize(
        ("tooth_count", "module_mm", "expected_pitch_r"),
        [
            (12, 1.0, 6.0),
            (20, 2.0, 20.0),
            (24, 2.5, 30.0),
            (60, 0.5, 15.0),
        ],
    )
    def test_gear_pitch_radius(self, tooth_count: int, module_mm: float, expected_pitch_r: float) -> None:
        assert math.isclose(gear_pitch_radius(tooth_count, module_mm), expected_pitch_r, abs_tol=1e-12)

    def test_gear_base_radius(self) -> None:
        # z=20, m=2.0 -> pitch_r = 20.0. For alpha=20 deg: base_r = 20 * cos(20 deg)
        expected_base_r = 20.0 * math.cos(math.radians(20.0))
        assert math.isclose(gear_base_radius(20, 2.0, 20.0), expected_base_r, abs_tol=1e-12)

    def test_gear_tip_radius(self) -> None:
        # r_a = r_p + m = 20.0 + 2.0 = 22.0
        assert math.isclose(gear_tip_radius(20, 2.0), 22.0, abs_tol=1e-12)

    def test_gear_root_radius(self) -> None:
        # r_f = max(0.35*m, r_p - 1.25*m) = max(0.7, 20.0 - 2.5) = 17.5
        assert math.isclose(gear_root_radius(20, 2.0), 17.5, abs_tol=1e-12)

    def test_gear_root_radius_small_pinion_guard(self) -> None:
        # For very small tooth count where r_p - 1.25*m <= 0, root_r = 0.35*m
        assert math.isclose(gear_root_radius(2, 2.0), 0.70, abs_tol=1e-12)


class TestInvoluteMath:
    """Verify analytical involute curve point generation."""

    def test_involute_origin(self) -> None:
        # At theta=0, involute starts at (r_b, 0)
        base_r = 25.0
        x, y = involute_point(base_r, 0.0)
        assert math.isclose(x, base_r, abs_tol=1e-12)
        assert math.isclose(y, 0.0, abs_tol=1e-12)

    def test_involute_radial_distance_growth(self) -> None:
        # r(theta) = r_b * sqrt(1 + theta^2)
        base_r = 30.0
        theta = 0.5
        x, y = involute_point(base_r, theta)
        r_actual = math.hypot(x, y)
        r_expected = base_r * math.sqrt(1.0 + theta * theta)
        assert math.isclose(r_actual, r_expected, abs_tol=1e-12)


class TestSpurGearOutlinePoints:
    """Verify 2D deterministic conceptual spur gear polyline outline generation and geometric invariants."""

    def test_outline_point_count_open_and_closed(self) -> None:
        tooth_count = 16
        # Open outline: 5 points per tooth = 80 points
        open_pts = spur_gear_outline_points(tooth_count=tooth_count, module_mm=1.5, closed=False)
        assert len(open_pts) == tooth_count * 5

        # Closed outline: 80 + 1 closing point = 81 points
        closed_pts = spur_gear_outline_points(tooth_count=tooth_count, module_mm=1.5, closed=True)
        assert len(closed_pts) == tooth_count * 5 + 1
        assert closed_pts[0] == closed_pts[-1]

    def test_outline_radial_confinement(self) -> None:
        tooth_count = 24
        module_mm = 2.0
        pts = spur_gear_outline_points(tooth_count=tooth_count, module_mm=module_mm)
        root_r = gear_root_radius(tooth_count, module_mm)
        tip_r = gear_tip_radius(tooth_count, module_mm)

        for pt in pts:
            rad = math.hypot(pt["x_mm"], pt["y_mm"])
            assert rad >= root_r - 1e-9
            assert rad <= tip_r + 1e-9

    def test_spur_gear_profile_points_typed(self) -> None:
        pts = spur_gear_profile_points(tooth_count=12, module_mm=2.0)
        assert len(pts) == 12 * 5
        assert all(isinstance(p, ProfilePoint2D) for p in pts)

    def test_rotational_symmetry(self) -> None:
        tooth_count = 8
        module_mm = 3.0
        pts = spur_gear_outline_points(tooth_count=tooth_count, module_mm=module_mm)
        tooth_angle = 2.0 * math.pi / tooth_count

        # Check that rotating tooth 0 points by tooth_angle matches tooth 1 points (stride 5)
        for i in range(5):
            p0 = pts[i]
            p1 = pts[i + 5]
            x_rot = p0["x_mm"] * math.cos(tooth_angle) - p0["y_mm"] * math.sin(tooth_angle)
            y_rot = p0["x_mm"] * math.sin(tooth_angle) + p0["y_mm"] * math.cos(tooth_angle)
            assert math.isclose(x_rot, p1["x_mm"], abs_tol=1e-9)
            assert math.isclose(y_rot, p1["y_mm"], abs_tol=1e-9)

    def test_no_consecutive_duplicate_points_including_closing_edge(self) -> None:
        """Prove that adjacent points (and closing segment) have non-zero edge length > 1e-6 mm."""
        for tooth_count in (4, 8, 16, 24, 120):
            pts = spur_gear_outline_points(tooth_count=tooth_count, module_mm=2.0, closed=False)
            num_pts = len(pts)
            for i in range(num_pts):
                p_curr = pts[i]
                p_next = pts[(i + 1) % num_pts]
                dist = math.hypot(p_next["x_mm"] - p_curr["x_mm"], p_next["y_mm"] - p_curr["y_mm"])
                assert dist > 1e-6, f"Duplicate or near-zero edge detected between vertex {i} and {(i + 1) % num_pts}"

    def test_outline_winding_and_positive_area(self) -> None:
        """Verify that the spur gear outline has strictly CCW winding and positive area bounded by root/tip circles."""
        for tooth_count in (4, 8, 16, 24, 120):
            module_mm = 2.0
            pts = spur_gear_outline_points(tooth_count=tooth_count, module_mm=module_mm, closed=False)
            assert is_polygon_ccw(pts) is True

            area = polygon_area(pts)
            assert area > 0.0

            root_r = gear_root_radius(tooth_count, module_mm)
            tip_r = gear_tip_radius(tooth_count, module_mm)
            min_expected_area = math.pi * root_r * root_r
            max_expected_area = math.pi * tip_r * tip_r
            assert min_expected_area < area < max_expected_area

    def test_typed_versus_dictionary_point_equivalence(self) -> None:
        """Verify exact coordinate equivalence between typed ProfilePoint2D and dictionary point outputs."""
        for tooth_count in (6, 12, 24):
            for closed in (False, True):
                raw = spur_gear_outline_points(tooth_count=tooth_count, module_mm=2.5, closed=closed)
                typed = spur_gear_profile_points(tooth_count=tooth_count, module_mm=2.5, closed=closed)
                assert len(raw) == len(typed)
                for r_pt, t_pt in zip(raw, typed, strict=True):
                    assert math.isclose(r_pt["x_mm"], t_pt.x_mm, abs_tol=1e-12)
                    assert math.isclose(r_pt["y_mm"], t_pt.y_mm, abs_tol=1e-12)

    def test_exact_120_tooth_product_limit_vertex_count(self) -> None:
        """Verify the 120-tooth maximum product limit produces exactly 600 open vertices (5/tooth) and 601 closed."""
        open_pts = spur_gear_outline_points(tooth_count=120, module_mm=1.0, closed=False)
        assert len(open_pts) == 600

        closed_pts = spur_gear_outline_points(tooth_count=120, module_mm=1.0, closed=True)
        assert len(closed_pts) == 601
        assert closed_pts[0] == closed_pts[-1]

        typed_pts = spur_gear_profile_points(tooth_count=120, module_mm=1.0, closed=False)
        assert len(typed_pts) == 600

    def test_mathematical_determinism_nfr_2(self) -> None:
        # Two successive runs must produce bitwise identical float sequences
        pts1 = spur_gear_outline_points(tooth_count=20, module_mm=2.0)
        pts2 = spur_gear_outline_points(tooth_count=20, module_mm=2.0)
        assert pts1 == pts2


class TestGearParameterValidation:
    """Verify ZeroDivision and parameter boundary guards for spur gears."""

    @pytest.mark.parametrize("invalid_z", [0, -1, -10, 1, 2, 3])
    def test_rejects_invalid_tooth_count(self, invalid_z: int) -> None:
        with pytest.raises(ValueError, match="tooth_count must be an integer >= 4"):
            validate_gear_parameters(tooth_count=invalid_z, module_mm=2.0)

        with pytest.raises(ValueError, match="tooth_count must be an integer >= 4"):
            spur_gear_outline_points(tooth_count=invalid_z, module_mm=2.0)

    @pytest.mark.parametrize("invalid_m", [0.0, -1.0, -0.001, float("nan"), float("inf")])
    def test_rejects_invalid_module(self, invalid_m: float) -> None:
        with pytest.raises(ValueError, match="module_mm must be a positive finite float"):
            validate_gear_parameters(tooth_count=20, module_mm=invalid_m)

    @pytest.mark.parametrize("invalid_alpha", [0.0, -5.0, 45.0, 50.0, 90.0, float("nan")])
    def test_rejects_invalid_pressure_angle(self, invalid_alpha: float) -> None:
        with pytest.raises(ValueError, match=r"pressure_angle_deg must be strictly between 0\.0 and 45\.0"):
            validate_gear_parameters(tooth_count=20, module_mm=2.0, pressure_angle_deg=invalid_alpha)

    def test_rejects_negative_bore_diameter(self) -> None:
        with pytest.raises(ValueError, match="bore_diameter_mm must be a non-negative finite float"):
            validate_gear_parameters(tooth_count=20, module_mm=2.0, bore_diameter_mm=-5.0)

    def test_rejects_bore_diameter_exceeding_root(self) -> None:
        # z=20, m=2.0 -> root_r = 17.5 mm -> root_diameter = 35.0 mm
        with pytest.raises(ValueError, match="cannot exceed or equal root diameter"):
            validate_gear_parameters(tooth_count=20, module_mm=2.0, bore_diameter_mm=36.0)

    def test_accepts_valid_gear_parameters(self) -> None:
        # Valid parameters should pass without exception
        validate_gear_parameters(tooth_count=24, module_mm=2.0, pressure_angle_deg=20.0, bore_diameter_mm=10.0)
