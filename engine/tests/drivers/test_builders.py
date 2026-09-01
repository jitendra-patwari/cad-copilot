"""Unit tests and fake-COM interaction tests for Solid Edge geometry and profile builders."""

from __future__ import annotations

import math
from typing import Any
from unittest.mock import MagicMock

import pytest

from drivers.solidedge.builders import (
    create_primitive_cuboid,
    create_primitive_cylinder,
    create_primitive_extruded_profile,
    create_primitive_spur_gear,
    create_profile_on_plane,
    draw_circle_profile,
    draw_polygon_profile,
    draw_slot_profile,
    get_ref_plane_offset_side,
    resolve_or_create_reference_plane,
)
from drivers.solidedge.constants import (
    BASE_PLANE_INDEX_XY,
    BASE_PLANE_INDEX_XZ,
    BASE_PLANE_INDEX_YZ,
    FEATURE_STATUS_OK,
    KEYPOINT_ARC_END,
    KEYPOINT_ARC_START,
    KEYPOINT_LINE_END,
    KEYPOINT_LINE_START,
    PROFILE_SIDE_LEFT,
    PROFILE_SIDE_RIGHT,
    PROFILE_STATUS_CLOSED,
    PROFILE_STATUS_OK,
    REF_PLANE_NORMAL_SIDE,
    REF_PLANE_REVERSE_SIDE,
)
from drivers.solidedge.units import (
    deg_to_rad,
    m2_to_mm2,
    m3_to_mm3,
    m_to_mm,
    mm_to_m,
    rad_to_deg,
)
from geometry.gear_math import spur_gear_outline_points
from interfaces.exceptions import CADExecutionError

# ---------------------------------------------------------------------------
# 1. Constants & Typelib Verification
# ---------------------------------------------------------------------------


def test_constants_independent_literal_values() -> None:
    """Proves that Solid Edge COM constants match the verified Siemens typelib values."""
    # ProfileSide: igLeft=1, igRight=2
    assert PROFILE_SIDE_LEFT == 1
    assert PROFILE_SIDE_RIGHT == 2

    # Base reference plane index order: 1=Top (XY), 2=Right (YZ), 3=Front (XZ)
    assert BASE_PLANE_INDEX_XY == 1
    assert BASE_PLANE_INDEX_YZ == 2
    assert BASE_PLANE_INDEX_XZ == 3

    # ReferenceElementConstants: igReverseNormalSide=1, igNormalSide=2
    assert REF_PLANE_REVERSE_SIDE == 1
    assert REF_PLANE_NORMAL_SIDE == 2

    # Profile validation: OK=0, Closed=1
    assert PROFILE_STATUS_OK == 0
    assert PROFILE_STATUS_CLOSED == 1

    # Relations2d Keypoints (KeypointIndexConstants: LineStart=0, LineEnd=1, ArcStart=1, ArcEnd=2)
    assert KEYPOINT_LINE_START == 0
    assert KEYPOINT_LINE_END == 1
    assert KEYPOINT_ARC_START == 1
    assert KEYPOINT_ARC_END == 2

    # FeatureStatusConstants: igFeatureOK=1216476310
    assert FEATURE_STATUS_OK == 1216476310


# ---------------------------------------------------------------------------
# 2. Units & Conversion Helper Tests
# ---------------------------------------------------------------------------


def test_unit_conversions() -> None:
    """Verifies precision and correctness of unit conversions."""
    assert mm_to_m(100.0) == 0.1
    assert mm_to_m(0.0) == 0.0
    assert m_to_mm(0.25) == 250.0

    assert m3_to_mm3(0.001) == 1_000_000.0
    assert m2_to_mm2(0.01) == 10_000.0

    assert math.isclose(deg_to_rad(180.0), math.pi)
    assert math.isclose(rad_to_deg(math.pi / 2.0), 90.0)


def test_unit_conversions_reject_non_finite() -> None:
    """Proves that non-finite values are rejected by unit converters."""
    with pytest.raises(ValueError, match="finite float"):
        mm_to_m(float("nan"))

    with pytest.raises(ValueError, match="finite float"):
        m_to_mm(float("inf"))

    with pytest.raises(ValueError, match="finite float"):
        m3_to_mm3(float("-inf"))

    with pytest.raises(ValueError, match="finite float"):
        deg_to_rad(float("nan"))


# ---------------------------------------------------------------------------
# 3. Fake-COM Mock Objects with Call Logging
# ---------------------------------------------------------------------------


class MockLoggingWorker:
    """Mock STAThreadWorker providing transparent dispatch and call logging."""

    def __init__(self) -> None:
        self.call_log: list[str] = []

    def _invoke_com(self, func: Any) -> Any:
        return func()


class FakeRelations2d:
    def __init__(self, worker: MockLoggingWorker) -> None:
        self.worker = worker
        self.created_relations: list[tuple[Any, int, Any, int, bool]] = []

    def AddKeypoint(self, obj1: Any, kp1: int, obj2: Any, kp2: int, guaranteed_ok: bool = False) -> MagicMock:
        self.worker.call_log.append(f"Relations2d.AddKeypoint({kp1}->{kp2}, ok={guaranteed_ok})")
        self.created_relations.append((obj1, kp1, obj2, kp2, guaranteed_ok))
        mock_rel = MagicMock()
        return mock_rel


class FakeLines2d:
    def __init__(self, worker: MockLoggingWorker) -> None:
        self.worker = worker
        self.created_lines: list[tuple[float, float, float, float]] = []

    def AddBy2Points(self, x1: float, y1: float, x2: float, y2: float) -> MagicMock:
        self.worker.call_log.append(f"Lines2d.AddBy2Points({x1:.6f}, {y1:.6f}, {x2:.6f}, {y2:.6f})")
        self.created_lines.append((x1, y1, x2, y2))
        mock_line = MagicMock()
        mock_line.coords = (x1, y1, x2, y2)
        return mock_line


class FakeCircles2d:
    def __init__(self, worker: MockLoggingWorker) -> None:
        self.worker = worker
        self.created_circles: list[tuple[float, float, float]] = []

    def AddByCenterRadius(self, cx: float, cy: float, r: float) -> MagicMock:
        self.worker.call_log.append(f"Circles2d.AddByCenterRadius({cx:.6f}, {cy:.6f}, {r:.6f})")
        self.created_circles.append((cx, cy, r))
        mock_circle = MagicMock()
        mock_circle.params = (cx, cy, r)
        return mock_circle


class FakeArcs2d:
    def __init__(self, worker: MockLoggingWorker) -> None:
        self.worker = worker
        self.created_arcs: list[tuple[float, float, float, float, float, float]] = []

    def AddByCenterStartEnd(self, cx: float, cy: float, sx: float, sy: float, ex: float, ey: float) -> MagicMock:
        self.worker.call_log.append(
            f"Arcs2d.AddByCenterStartEnd({cx:.6f}, {cy:.6f}, {sx:.6f}, {sy:.6f}, {ex:.6f}, {ey:.6f})"
        )
        self.created_arcs.append((cx, cy, sx, sy, ex, ey))
        mock_arc = MagicMock()
        mock_arc.params = (cx, cy, sx, sy, ex, ey)
        return mock_arc


class FakeProfile:
    def __init__(self, plane: Any, worker: MockLoggingWorker) -> None:
        self.plane = plane
        self.worker = worker
        self.Lines2d = FakeLines2d(worker)
        self.Circles2d = FakeCircles2d(worker)
        self.Arcs2d = FakeArcs2d(worker)
        self.Relations2d = FakeRelations2d(worker)
        self.end_return_status: Any = PROFILE_STATUS_OK
        self.ended_mode: int | None = None
        self.Visible = True

    def End(self, mode: int) -> Any:
        self.worker.call_log.append(f"Profile.End({mode})")
        self.ended_mode = mode
        return self.end_return_status


class FakeProfileSet:
    def __init__(self, worker: MockLoggingWorker) -> None:
        self.worker = worker
        self.profiles: list[FakeProfile] = []

    @property
    def Profiles(self) -> Any:
        class _ProfilesAccessor:
            def __init__(self, pset: FakeProfileSet) -> None:
                self.pset = pset

            def Add(self, plane: Any) -> FakeProfile:
                self.pset.worker.call_log.append(f"Profiles.Add({getattr(plane, 'name', 'Plane')})")
                prof = FakeProfile(plane, self.pset.worker)
                self.pset.profiles.append(prof)
                return prof

        return _ProfilesAccessor(self)


class FakeProfileSets:
    def __init__(self, worker: MockLoggingWorker) -> None:
        self.worker = worker
        self.sets: list[FakeProfileSet] = []

    def Add(self) -> FakeProfileSet:
        self.worker.call_log.append("ProfileSets.Add()")
        pset = FakeProfileSet(self.worker)
        self.sets.append(pset)
        return pset


class FakeRefPlanes:
    def __init__(self, worker: MockLoggingWorker) -> None:
        self.worker = worker
        self.base_planes = {
            1: MagicMock(name="BaseXY_Top"),
            2: MagicMock(name="BaseYZ_Right"),
            3: MagicMock(name="BaseXZ_Front"),
        }
        self.base_planes[1].name = "BaseXY_Top"
        self.base_planes[2].name = "BaseYZ_Right"
        self.base_planes[3].name = "BaseXZ_Front"
        self.parallel_planes: list[tuple[Any, float, int]] = []

    def Item(self, index: int) -> Any:
        self.worker.call_log.append(f"RefPlanes.Item({index})")
        if index not in self.base_planes:
            raise KeyError(f"Unknown plane index {index}")
        return self.base_planes[index]

    def AddParallelByDistance(self, parent: Any, dist_m: float, side: int) -> MagicMock:
        self.worker.call_log.append(
            f"RefPlanes.AddParallelByDistance({getattr(parent, 'name', 'Plane')}, {dist_m:.6f}, {side})"
        )
        self.parallel_planes.append((parent, dist_m, side))
        parallel_plane = MagicMock(name=f"ParallelPlane_{dist_m:.3f}_{side}")
        parallel_plane.name = f"ParallelPlane_{dist_m:.3f}_{side}"
        return parallel_plane


class FakeExtrudedProtrusion:
    def __init__(self, status: Any = FEATURE_STATUS_OK) -> None:
        self.Status: Any = status


class FakeExtrudedProtrusions:
    def __init__(self, feature: FakeExtrudedProtrusion | None = None) -> None:
        self._feature = feature

    @property
    def Count(self) -> int:
        return 1 if self._feature is not None else 0

    def Item(self, index: int) -> FakeExtrudedProtrusion | None:
        if index == 1 and self._feature is not None:
            return self._feature
        return None


class FakeModel:
    def __init__(
        self,
        worker: MockLoggingWorker,
        distance_m: float,
        feature_status: Any = FEATURE_STATUS_OK,
        has_null_body: bool = False,
        missing_protrusions: bool = False,
    ) -> None:
        self.worker = worker
        self.distance_m = distance_m
        if missing_protrusions:
            self.ExtrudedProtrusions = FakeExtrudedProtrusions(None)
        else:
            self.ExtrudedProtrusions = FakeExtrudedProtrusions(FakeExtrudedProtrusion(feature_status))

        if has_null_body:
            self.Body = None
        else:
            self.Body = MagicMock(name="SolidBody")


class FakeModels:
    def __init__(self, worker: MockLoggingWorker) -> None:
        self.worker = worker
        self.protrusions: list[tuple[int, list[Any], int, float]] = []
        self.should_return_null: bool = False
        self.should_have_null_body: bool = False
        self.should_have_missing_protrusions: bool = False
        self.feature_status: Any = FEATURE_STATUS_OK

    @property
    def Count(self) -> int:
        return len(self.protrusions)

    def AddFiniteExtrudedProtrusion(self, num_profiles: int, profiles: list[Any], side: int, distance_m: float) -> Any:
        self.worker.call_log.append(
            f"Models.AddFiniteExtrudedProtrusion({num_profiles}, side={side}, dist={distance_m:.6f})"
        )
        if self.should_return_null:
            return None

        self.protrusions.append((num_profiles, profiles, side, distance_m))
        model = FakeModel(
            self.worker,
            distance_m=distance_m,
            feature_status=self.feature_status,
            has_null_body=self.should_have_null_body,
            missing_protrusions=self.should_have_missing_protrusions,
        )
        return model


class FakeRawDoc:
    def __init__(self, worker: MockLoggingWorker) -> None:
        self.worker = worker
        self.RefPlanes = FakeRefPlanes(worker)
        self.ProfileSets = FakeProfileSets(worker)
        self.Models = FakeModels(worker)


@pytest.fixture
def fake_env() -> tuple[FakeRawDoc, MockLoggingWorker]:
    worker = MockLoggingWorker()
    doc = FakeRawDoc(worker)
    return doc, worker


# ---------------------------------------------------------------------------
# 4. Reference Plane Resolution & Orientation Tests
# ---------------------------------------------------------------------------


def test_get_ref_plane_offset_side_plane_specific_mapping() -> None:
    """Proves that XY, YZ, and XZ plane offset sides account for world-axis orientations."""
    # ReferenceElementConstants: igReverseNormalSide=1, igNormalSide=2

    # XY (Top, +Z normal): positive offset -> Normal (2), negative offset -> Reverse (1)
    assert get_ref_plane_offset_side("XY", 10.0) == 2
    assert get_ref_plane_offset_side("XY", 0.0) == 2
    assert get_ref_plane_offset_side("XY", -10.0) == 1

    # YZ (Right, +X normal): positive offset -> Normal (2), negative offset -> Reverse (1)
    assert get_ref_plane_offset_side("YZ", 25.0) == 2
    assert get_ref_plane_offset_side("YZ", -25.0) == 1

    # XZ (Front, -Y normal in Solid Edge): positive offset (world +Y) -> Reverse (1), negative offset (world -Y) -> Normal (2)
    assert get_ref_plane_offset_side("XZ", 15.0) == 1
    assert get_ref_plane_offset_side("XZ", -15.0) == 2


def test_resolve_base_reference_planes_at_zero_offset(fake_env: tuple[FakeRawDoc, MockLoggingWorker]) -> None:
    """Proves that base XY=1, YZ=2, XZ=3 planes are resolved directly when offset is zero."""
    doc, worker = fake_env

    plane_xy = resolve_or_create_reference_plane(doc, worker, "XY", 0.0)
    assert plane_xy is doc.RefPlanes.base_planes[1]
    assert len(doc.RefPlanes.parallel_planes) == 0
    assert "RefPlanes.Item(1)" in worker.call_log

    plane_yz = resolve_or_create_reference_plane(doc, worker, "YZ", 0.0)
    assert plane_yz is doc.RefPlanes.base_planes[2]
    assert "RefPlanes.Item(2)" in worker.call_log

    plane_xz = resolve_or_create_reference_plane(doc, worker, "XZ", 0.0)
    assert plane_xz is doc.RefPlanes.base_planes[3]
    assert "RefPlanes.Item(3)" in worker.call_log


def test_create_parallel_reference_planes_with_signed_offsets(
    fake_env: tuple[FakeRawDoc, MockLoggingWorker],
) -> None:
    """Proves that signed offsets construct parallel planes with exact plane-oriented sides."""
    doc, worker = fake_env

    # XY positive offset -> side 2 (igNormalSide)
    plane_xy_pos = resolve_or_create_reference_plane(doc, worker, "XY", 50.0)
    assert plane_xy_pos is not None
    assert doc.RefPlanes.parallel_planes[0][1] == 0.050
    assert doc.RefPlanes.parallel_planes[0][2] == 2

    # XY negative offset -> side 1 (igReverseNormalSide)
    plane_xy_neg = resolve_or_create_reference_plane(doc, worker, "XY", -30.0)
    assert plane_xy_neg is not None
    assert doc.RefPlanes.parallel_planes[1][1] == 0.030
    assert doc.RefPlanes.parallel_planes[1][2] == 1

    # XZ positive offset (world +Y) -> side 1 (igReverseNormalSide)
    plane_xz_pos = resolve_or_create_reference_plane(doc, worker, "XZ", 20.0)
    assert plane_xz_pos is not None
    assert doc.RefPlanes.parallel_planes[2][1] == 0.020
    assert doc.RefPlanes.parallel_planes[2][2] == 1

    # XZ negative offset (world -Y) -> side 2 (igNormalSide)
    plane_xz_neg = resolve_or_create_reference_plane(doc, worker, "XZ", -20.0)
    assert plane_xz_neg is not None
    assert doc.RefPlanes.parallel_planes[3][1] == 0.020
    assert doc.RefPlanes.parallel_planes[3][2] == 2


# ---------------------------------------------------------------------------
# 5. Profile Drawing & Validation Tests
# ---------------------------------------------------------------------------


def test_draw_circle_profile_success_and_call_order(
    fake_env: tuple[FakeRawDoc, MockLoggingWorker],
) -> None:
    """Proves circle profile draws circle in meters then closes with Profile.End(1)."""
    doc, worker = fake_env
    ref_plane = resolve_or_create_reference_plane(doc, worker, "XY", 0.0)
    profile = create_profile_on_plane(doc, worker, ref_plane)

    draw_circle_profile(profile, worker, center_x_mm=10.0, center_y_mm=-20.0, radius_mm=15.0)

    assert len(profile.Circles2d.created_circles) == 1
    cx_m, cy_m, r_m = profile.Circles2d.created_circles[0]
    assert math.isclose(cx_m, 0.010)
    assert math.isclose(cy_m, -0.020)
    assert math.isclose(r_m, 0.015)
    assert profile.ended_mode == PROFILE_STATUS_CLOSED

    # Verify exact call order
    expected_tail = [
        "Circles2d.AddByCenterRadius(0.010000, -0.020000, 0.015000)",
        f"Profile.End({PROFILE_STATUS_CLOSED})",
    ]
    assert worker.call_log[-2:] == expected_tail


def test_draw_circle_profile_validation_failure_and_none_rejection(
    fake_env: tuple[FakeRawDoc, MockLoggingWorker],
) -> None:
    """Proves that a nonzero status or None returned from Profile.End raises CADExecutionError."""
    doc, worker = fake_env
    ref_plane = resolve_or_create_reference_plane(doc, worker, "XY", 0.0)
    profile = create_profile_on_plane(doc, worker, ref_plane)

    # 1. Non-zero status code
    profile.end_return_status = 2
    with pytest.raises(CADExecutionError, match="validation failed with status 2"):
        draw_circle_profile(profile, worker, center_x_mm=0.0, center_y_mm=0.0, radius_mm=10.0)

    # 2. None return status
    profile.end_return_status = None
    with pytest.raises(CADExecutionError, match="validation failed with status None"):
        draw_circle_profile(profile, worker, center_x_mm=0.0, center_y_mm=0.0, radius_mm=10.0)


def test_draw_polygon_profile_adds_line_keypoint_relations_and_closure(
    fake_env: tuple[FakeRawDoc, MockLoggingWorker],
) -> None:
    """Proves polygon drawing creates lines in order, adds line keypoint relations (End=1 -> Start=0, ok=True), and verifies End(1)."""
    doc, worker = fake_env
    ref_plane = resolve_or_create_reference_plane(doc, worker, "XY", 0.0)
    profile = create_profile_on_plane(doc, worker, ref_plane)

    triangle_pts = [
        {"x_mm": 0.0, "y_mm": 0.0},
        {"x_mm": 100.0, "y_mm": 0.0},
        {"x_mm": 50.0, "y_mm": 80.0},
    ]

    draw_polygon_profile(profile, worker, triangle_pts)

    assert len(profile.Lines2d.created_lines) == 3
    # 3 lines -> 3 keypoint relations connecting LineEnd(1) of line i to LineStart(0) of line i+1 with ok=True
    assert len(profile.Relations2d.created_relations) == 3
    for rel in profile.Relations2d.created_relations:
        assert rel[1] == KEYPOINT_LINE_END  # 1
        assert rel[3] == KEYPOINT_LINE_START  # 0
        assert rel[4] is True  # guaranteed_ok=True
    assert profile.ended_mode == PROFILE_STATUS_CLOSED
    assert f"Profile.End({PROFILE_STATUS_CLOSED})" in worker.call_log


def test_draw_polygon_profile_rejects_degenerate_and_repeated_vertices(
    fake_env: tuple[FakeRawDoc, MockLoggingWorker],
) -> None:
    """Proves polygon builder pre-rejects zero-length edges and all repeated vertices before COM calls."""
    doc, worker = fake_env
    profile = create_profile_on_plane(doc, worker, MagicMock())
    initial_call_count = len(worker.call_log)

    # 1. Fewer than 3 points
    with pytest.raises(CADExecutionError, match="at least 3 vertices"):
        draw_polygon_profile(profile, worker, [{"x_mm": 0.0, "y_mm": 0.0}, {"x_mm": 10.0, "y_mm": 0.0}])

    # 2. Consecutive duplicate points (zero-length edge)
    with pytest.raises(CADExecutionError, match="degenerate zero-length edge"):
        draw_polygon_profile(
            profile,
            worker,
            [
                {"x_mm": 0.0, "y_mm": 0.0},
                {"x_mm": 0.0, "y_mm": 0.0},
                {"x_mm": 50.0, "y_mm": 50.0},
            ],
        )

    # 3. Non-adjacent repeated vertex (e.g. vertex 0 repeated at index 3 in 5-point loop)
    with pytest.raises(CADExecutionError, match="duplicate/repeated vertices"):
        draw_polygon_profile(
            profile,
            worker,
            [
                {"x_mm": 0.0, "y_mm": 0.0},
                {"x_mm": 10.0, "y_mm": 0.0},
                {"x_mm": 10.0, "y_mm": 10.0},
                {"x_mm": 0.0, "y_mm": 0.0},
                {"x_mm": 0.0, "y_mm": 10.0},
            ],
        )

    # Proves zero COM line creation calls occurred during preflight rejections
    assert len(profile.Lines2d.created_lines) == 0
    assert len(worker.call_log) == initial_call_count


def test_draw_slot_profile_curve_specific_keypoints_and_rotation(
    fake_env: tuple[FakeRawDoc, MockLoggingWorker],
) -> None:
    """Proves the rotated slot uses a CCW loop with outward arcs and curve-specific keypoints."""
    doc, worker = fake_env
    ref_plane = resolve_or_create_reference_plane(doc, worker, "XY", 0.0)
    profile = create_profile_on_plane(doc, worker, ref_plane)

    # Slot 60mm length, 20mm width at (0, 0) rotated 90 degrees
    draw_slot_profile(
        profile,
        worker,
        center_x_mm=0.0,
        center_y_mm=0.0,
        length_mm=60.0,
        width_mm=20.0,
        orientation_deg=90.0,
    )

    assert len(profile.Lines2d.created_lines) == 2
    assert len(profile.Arcs2d.created_arcs) == 2
    # 4 curve transitions:
    # 1. line_bottom End (1) -> arc_right Start (1)
    # 2. arc_right End (2) -> line_top Start (0)
    # 3. line_top End (1) -> arc_left Start (1)
    # 4. arc_left End (2) -> line_bottom Start (0)
    rels = profile.Relations2d.created_relations
    assert len(rels) == 4

    assert rels[0][1] == KEYPOINT_LINE_END and rels[0][3] == KEYPOINT_ARC_START and rels[0][4] is True  # 1 -> 1
    assert rels[1][1] == KEYPOINT_ARC_END and rels[1][3] == KEYPOINT_LINE_START and rels[1][4] is True  # 2 -> 0
    assert rels[2][1] == KEYPOINT_LINE_END and rels[2][3] == KEYPOINT_ARC_START and rels[2][4] is True  # 1 -> 1
    assert rels[3][1] == KEYPOINT_ARC_END and rels[3][3] == KEYPOINT_LINE_START and rels[3][4] is True  # 2 -> 0

    assert profile.ended_mode == PROFILE_STATUS_CLOSED

    # The 90-degree transform maps the CCW local stadium into these exact
    # tangent and outward-cap coordinates (all values are metres).
    line_bottom, line_top = profile.Lines2d.created_lines
    assert line_bottom == pytest.approx((0.010, -0.020, 0.010, 0.020), abs=1e-12)
    assert line_top == pytest.approx((-0.010, 0.020, -0.010, -0.020), abs=1e-12)

    arc_right, arc_left = profile.Arcs2d.created_arcs
    assert arc_right == pytest.approx((0.0, 0.020, 0.010, 0.020, -0.010, 0.020), abs=1e-12)
    assert arc_left == pytest.approx((0.0, -0.020, -0.010, -0.020, 0.010, -0.020), abs=1e-12)


# ---------------------------------------------------------------------------
# 6. Primitive Base Body & Native Model / Feature Validation Tests
# ---------------------------------------------------------------------------


def test_create_primitive_cuboid(fake_env: tuple[FakeRawDoc, MockLoggingWorker]) -> None:
    """Proves cuboid primitive creation creates a centered 4-point rectangle and extrudes with igRight=2."""
    doc, worker = fake_env

    model = create_primitive_cuboid(
        doc,
        worker,
        length_mm=100.0,
        width_mm=80.0,
        height_mm=20.0,
        origin_offset_mm={"x_mm": 10.0, "y_mm": 5.0, "z_mm": 0.0},
    )

    assert model is not None
    assert len(doc.Models.protrusions) == 1
    num_profs, _profs, side, dist_m = doc.Models.protrusions[0]
    assert num_profs == 1
    assert side == 2  # Must literally be igRight=2
    assert math.isclose(dist_m, 0.020)


def test_create_primitive_cylinder(fake_env: tuple[FakeRawDoc, MockLoggingWorker]) -> None:
    """Proves cylinder primitive creation creates a circle and extrudes with igRight=2."""
    doc, worker = fake_env

    model = create_primitive_cylinder(
        doc,
        worker,
        radius_mm=25.0,
        height_mm=50.0,
        origin_offset_mm={"x_mm": 0.0, "y_mm": 0.0, "z_mm": 10.0},
    )

    assert model is not None
    assert len(doc.Models.protrusions) == 1
    _, _, side, dist_m = doc.Models.protrusions[0]
    assert side == 2
    assert math.isclose(dist_m, 0.050)


def test_create_primitive_extruded_profile_lowered_gear_payload(
    fake_env: tuple[FakeRawDoc, MockLoggingWorker],
) -> None:
    """Proves that create_primitive_extruded_profile executes an authoritative lowered gear shape directly."""
    doc, worker = fake_env

    # 1. Lowered gear shape payload
    outline_points = spur_gear_outline_points(tooth_count=8, module_mm=2.5, pressure_angle_deg=20.0, closed=False)
    lowered_shape = {
        "type": "extruded_profile",
        "profile_family": "spur_gear_concept",
        "points": outline_points,
        "height_mm": 15.0,
    }

    height_val = lowered_shape["height_mm"]
    assert isinstance(height_val, (int, float))
    model = create_primitive_extruded_profile(
        doc,
        worker,
        points_mm=outline_points,
        height_mm=float(height_val),
        origin_offset_mm={"x_mm": 20.0, "y_mm": -10.0, "z_mm": 5.0},
    )

    assert model is not None
    assert len(doc.Models.protrusions) == 1
    num_profs, profs, side, dist_m = doc.Models.protrusions[0]
    assert num_profs == 1
    assert side == 2  # igRight=2
    assert math.isclose(dist_m, 0.015)

    # 8 teeth * 5 unique points = 40 lines and 40 keypoint relations
    prof = profs[0]
    assert len(prof.Lines2d.created_lines) == 40
    assert len(prof.Relations2d.created_relations) == 40
    assert prof.ended_mode == PROFILE_STATUS_CLOSED


def test_create_primitive_spur_gear_convenience_helper(
    fake_env: tuple[FakeRawDoc, MockLoggingWorker],
) -> None:
    """Proves create_primitive_spur_gear convenience helper delegates cleanly."""
    doc, worker = fake_env

    model = create_primitive_spur_gear(
        doc,
        worker,
        tooth_count=12,
        module_mm=2.0,
        pressure_angle_deg=20.0,
        face_width_mm=15.0,
    )

    assert model is not None
    assert len(doc.Models.protrusions) == 1
    assert len(doc.Models.protrusions[0][1][0].Lines2d.created_lines) == 12 * 5


def test_create_primitive_rejects_null_unhealthy_or_bodyless_native_model(
    fake_env: tuple[FakeRawDoc, MockLoggingWorker],
) -> None:
    """Proves that null models, missing protrusions, unhealthy feature status, or null Body raise CADExecutionError."""
    doc, worker = fake_env

    # 1. Protrusion COM call returns None
    doc.Models.should_return_null = True
    with pytest.raises(CADExecutionError, match="returned null model"):
        create_primitive_cuboid(doc, worker, length_mm=10.0, width_mm=10.0, height_mm=10.0)

    # 2. Missing/empty ExtrudedProtrusions collection
    doc.Models.should_return_null = False
    doc.Models.should_have_missing_protrusions = True
    with pytest.raises(CADExecutionError, match="no accessible ExtrudedProtrusions collection"):
        create_primitive_cylinder(doc, worker, radius_mm=10.0, height_mm=10.0)

    # 3. Unhealthy feature status (not FEATURE_STATUS_OK / 1216476310)
    doc.Models.should_have_missing_protrusions = False
    doc.Models.feature_status = 0
    with pytest.raises(CADExecutionError, match="Native feature status is unhealthy"):
        create_primitive_cylinder(doc, worker, radius_mm=10.0, height_mm=10.0)

    # 4. Missing (None) feature status
    doc.Models.feature_status = None
    with pytest.raises(CADExecutionError, match="Native feature status is unhealthy"):
        create_primitive_cylinder(doc, worker, radius_mm=10.0, height_mm=10.0)

    # 5. Protrusion COM call returns model with null Body
    doc.Models.feature_status = FEATURE_STATUS_OK
    doc.Models.should_have_null_body = True
    with pytest.raises(CADExecutionError, match="no accessible solid Body"):
        create_primitive_cylinder(doc, worker, radius_mm=10.0, height_mm=10.0)
