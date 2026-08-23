"""Unit and regression tests for planar geometry algorithms and AST shape helpers."""

from __future__ import annotations

import math
from typing import cast

import pytest

from geometry import (
    CircularThroughHoleFeature,
    CylinderBaseBody,
    Point2DLike,
    ProfilePoint2D,
    RectangularBaseBody,
    RectangularThroughCutoutFeature,
    RevolvedShaftBaseBody,
    SphereBaseBody,
    SpurGearBaseBody,
    bounding_boxes_overlap,
    is_polygon_ccw,
    point_in_polygon,
    point_to_segment_distance,
    polygon_area,
    polygon_bounding_box,
    polygon_centroid,
    polygon_signed_area,
)
from geometry.plan_geometry import (
    _base_body_human_label,
    _base_body_shape,
    _base_body_through_depth_mm,
    _feature_cut_depth_mm,
    _feature_cut_through_all,
    _feature_through_depth_mm,
    _point_to_dict,
    _to_xy_tuple,
)


class TestPointConversions:
    """Verify polymorphic Point2DLike ingestion and serialization."""

    def test_to_xy_tuple(self) -> None:
        assert _to_xy_tuple((10.0, 20.0)) == (10.0, 20.0)
        assert _to_xy_tuple({"x_mm": 5.5, "y_mm": -3.2}) == (5.5, -3.2)
        assert _to_xy_tuple({"x": 1.0, "y": 2.0}) == (1.0, 2.0)
        assert _to_xy_tuple(ProfilePoint2D(x_mm=12.0, y_mm=34.0)) == (12.0, 34.0)

    def test_to_xy_tuple_invalid(self) -> None:
        with pytest.raises(TypeError):
            _to_xy_tuple(cast(Point2DLike, "invalid_point"))

    def test_point_to_dict(self) -> None:
        pt = ProfilePoint2D(x_mm=15.0, y_mm=-8.0)
        assert _point_to_dict(pt) == {"x_mm": 15.0, "y_mm": -8.0}


class TestASTShapeAndDepthHelpers:
    """Verify base-body shape descriptors and material through-depth calculations."""

    def test_rectangular_base_body_shape_and_depth(self) -> None:
        body = RectangularBaseBody(id="body.main", length_mm=80.0, width_mm=40.0, thickness_mm=6.0)
        shape = _base_body_shape(body)
        assert shape == {"type": "cuboid", "length_mm": 80.0, "width_mm": 40.0, "height_mm": 6.0}
        assert _base_body_through_depth_mm(body) == 6.0

    def test_cylinder_base_body_shape_and_depth(self) -> None:
        body = CylinderBaseBody(id="body.cyl", radius_mm=15.0, height_mm=50.0)
        shape = _base_body_shape(body)
        assert shape == {"type": "cylinder", "radius_mm": 15.0, "height_mm": 50.0}
        assert _base_body_through_depth_mm(body) == 50.0

    def test_sphere_base_body_shape_and_depth(self) -> None:
        body = SphereBaseBody(id="body.sph", radius_mm=20.0)
        shape = _base_body_shape(body)
        assert shape == {"type": "sphere", "radius_mm": 20.0}
        assert _base_body_through_depth_mm(body) == 40.0

    def test_spur_gear_base_body_shape_and_depth(self) -> None:
        body = SpurGearBaseBody(id="body.gear", tooth_count=20, module_mm=2.0, face_width_mm=12.0)
        shape = _base_body_shape(body)
        assert shape["type"] == "extruded_profile"
        assert shape["profile_family"] == "spur_gear_concept"
        assert shape["height_mm"] == 12.0
        assert len(shape["points"]) == 20 * 6
        assert _base_body_through_depth_mm(body) == 12.0

    def test_revolved_shaft_base_body_shape_and_depth(self) -> None:
        body = RevolvedShaftBaseBody(
            id="body.shaft",
            radius_mm=10.0,
            height_mm=60.0,
            profile_points=(ProfilePoint2D(0, 0), ProfilePoint2D(10, 0), ProfilePoint2D(10, 60), ProfilePoint2D(0, 60)),
        )
        shape = _base_body_shape(body)
        assert shape["type"] == "revolved_shaft"
        assert shape["radius_mm"] == 10.0
        assert shape["height_mm"] == 60.0
        assert len(shape["points"]) == 4
        assert _base_body_through_depth_mm(body) == 60.0

    def test_feature_through_depth_across_faces(self) -> None:
        body = RectangularBaseBody(id="body.main", length_mm=100.0, width_mm=50.0, thickness_mm=10.0)

        hole_z = CircularThroughHoleFeature(id="f1", diameter_mm=8.0, target_face="+Z")
        assert _feature_through_depth_mm(body, hole_z) == 10.0

        hole_x = CircularThroughHoleFeature(id="f2", diameter_mm=8.0, target_face="+X")
        assert _feature_through_depth_mm(body, hole_x) == 100.0

        hole_y = CircularThroughHoleFeature(id="f3", diameter_mm=8.0, target_face="-Y")
        assert _feature_through_depth_mm(body, hole_y) == 50.0

    def test_feature_cut_depth_and_through_all(self) -> None:
        body = RectangularBaseBody(id="body.main", length_mm=80.0, width_mm=40.0, thickness_mm=6.0)

        through_hole = CircularThroughHoleFeature(id="f1", diameter_mm=10.0, extent_type="through_all", depth_mm=0.0)
        assert _feature_cut_through_all(through_hole) is True
        assert _feature_cut_depth_mm(body, through_hole) == 6.0

        blind_cutout = RectangularThroughCutoutFeature(
            id="f2", width_mm=20.0, height_mm=10.0, extent_type="finite", depth_mm=3.5
        )
        assert _feature_cut_through_all(blind_cutout) is False
        assert _feature_cut_depth_mm(body, blind_cutout) == 3.5

    def test_base_body_human_labels(self) -> None:
        cube = RectangularBaseBody(id="b1", length_mm=20.0, width_mm=20.0, thickness_mm=20.0)
        assert "20mm cube" in _base_body_human_label(cube)

        rect = RectangularBaseBody(id="b2", length_mm=80.0, width_mm=40.0, thickness_mm=6.0)
        assert "80x40x6mm rectangular" in _base_body_human_label(rect)

        cyl = CylinderBaseBody(id="b3", radius_mm=10.0, height_mm=30.0)
        assert "r10 h30 cylindrical" in _base_body_human_label(cyl)

        sph = SphereBaseBody(id="b4", radius_mm=15.0)
        assert "r15 spherical" in _base_body_human_label(sph)

        gear = SpurGearBaseBody(id="b5", tooth_count=20, module_mm=2.0, face_width_mm=10.0)
        assert "concept spur gear" in _base_body_human_label(gear)


class TestPointInPolygonAlgorithms:
    """Verify point-in-polygon ray-casting and boundary detection."""

    def test_square_point_in_polygon(self) -> None:
        square = [(-10.0, -10.0), (10.0, -10.0), (10.0, 10.0), (-10.0, 10.0)]

        # Interior point
        assert point_in_polygon(0.0, 0.0, square) is True
        assert point_in_polygon(5.0, -5.0, square) is True

        # Exterior point
        assert point_in_polygon(15.0, 0.0, square) is False
        assert point_in_polygon(-12.0, 12.0, square) is False

        # Boundary point
        assert point_in_polygon(10.0, 0.0, square, include_boundary=True) is True
        assert point_in_polygon(10.0, 10.0, square, include_boundary=True) is True

    def test_concave_l_shape_point_in_polygon(self) -> None:
        # L-shape: [0,0] -> [10,0] -> [10,4] -> [4,4] -> [4,10] -> [0,10]
        l_shape = [(0.0, 0.0), (10.0, 0.0), (10.0, 4.0), (4.0, 4.0), (4.0, 10.0), (0.0, 10.0)]

        # Inside lower wing
        assert point_in_polygon(8.0, 2.0, l_shape) is True
        # Inside left column
        assert point_in_polygon(2.0, 8.0, l_shape) is True
        # In the outer cutout corner (outside L)
        assert point_in_polygon(8.0, 8.0, l_shape) is False

    def test_polygon_with_mixed_point_types(self) -> None:
        polygon = [
            ProfilePoint2D(0.0, 0.0),
            {"x_mm": 20.0, "y_mm": 0.0},
            (20.0, 20.0),
            ProfilePoint2D(0.0, 20.0),
        ]
        assert point_in_polygon(10.0, 10.0, polygon) is True
        assert point_in_polygon(25.0, 10.0, polygon) is False


class TestShoelaceAreaAndCentroid:
    """Verify Shoelace formula polygon area and centroid calculations."""

    def test_polygon_area_rectangle(self) -> None:
        rect = [(0.0, 0.0), (30.0, 0.0), (30.0, 20.0), (0.0, 20.0)]
        assert math.isclose(polygon_area(rect), 600.0, abs_tol=1e-12)

        # Closed representation must give identical area
        rect_closed = [*rect, (0.0, 0.0)]
        assert math.isclose(polygon_area(rect_closed), 600.0, abs_tol=1e-12)

    def test_polygon_area_triangle(self) -> None:
        # Base=10, Height=10 -> Area=50
        tri = [(0.0, 0.0), (10.0, 0.0), (0.0, 10.0)]
        assert math.isclose(polygon_area(tri), 50.0, abs_tol=1e-12)

    def test_polygon_signed_area_and_ccw(self) -> None:
        ccw_poly = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
        assert polygon_signed_area(ccw_poly) > 0.0
        assert is_polygon_ccw(ccw_poly) is True

        cw_poly = list(reversed(ccw_poly))
        assert polygon_signed_area(cw_poly) < 0.0
        assert is_polygon_ccw(cw_poly) is False

    def test_polygon_centroid_centered_and_offset(self) -> None:
        # Centered square [-10, 10] x [-10, 10] -> Centroid (0, 0)
        sq_centered = [(-10.0, -10.0), (10.0, -10.0), (10.0, 10.0), (-10.0, 10.0)]
        cx, cy = polygon_centroid(sq_centered)
        assert math.isclose(cx, 0.0, abs_tol=1e-12)
        assert math.isclose(cy, 0.0, abs_tol=1e-12)

        # Offset rectangle [10, 50] x [20, 40] -> Centroid (30, 30)
        rect_offset = [(10.0, 20.0), (50.0, 20.0), (50.0, 40.0), (10.0, 40.0)]
        cx, cy = polygon_centroid(rect_offset)
        assert math.isclose(cx, 30.0, abs_tol=1e-12)
        assert math.isclose(cy, 30.0, abs_tol=1e-12)

    def test_polygon_centroid_degenerate(self) -> None:
        # Collinear points fallback to mean
        collinear = [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0)]
        cx, cy = polygon_centroid(collinear)
        assert math.isclose(cx, 10.0, abs_tol=1e-12)
        assert math.isclose(cy, 0.0, abs_tol=1e-12)


class TestBoundingBoxesAndDistances:
    """Verify Axis-Aligned Bounding Box and Euclidean distance calculations."""

    def test_polygon_bounding_box(self) -> None:
        poly = [(-5.0, 10.0), (15.0, -20.0), (30.0, 40.0)]
        min_x, min_y, max_x, max_y = polygon_bounding_box(poly)
        assert (min_x, min_y, max_x, max_y) == (-5.0, -20.0, 30.0, 40.0)

    def test_bounding_boxes_overlap(self) -> None:
        box1 = (0.0, 0.0, 10.0, 10.0)
        box2 = (5.0, 5.0, 15.0, 15.0)
        box3 = (20.0, 20.0, 30.0, 30.0)

        assert bounding_boxes_overlap(box1, box2) is True
        assert bounding_boxes_overlap(box1, box3) is False
        assert bounding_boxes_overlap(box1, box3, margin_mm=15.0) is True

    def test_point_to_segment_distance(self) -> None:
        # Horizontal segment from (0, 0) to (10, 0)
        # Point above midpoint
        assert math.isclose(point_to_segment_distance(5.0, 4.0, 0.0, 0.0, 10.0, 0.0), 4.0, abs_tol=1e-12)

        # Point beyond right endpoint (15, 0) -> distance to (10, 0) is 5.0
        assert math.isclose(point_to_segment_distance(15.0, 0.0, 0.0, 0.0, 10.0, 0.0), 5.0, abs_tol=1e-12)

        # Point directly on segment
        assert math.isclose(point_to_segment_distance(3.0, 0.0, 0.0, 0.0, 10.0, 0.0), 0.0, abs_tol=1e-12)
