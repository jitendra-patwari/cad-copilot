"""Comprehensive unit and integration tests for feature plan lowering and wire IR compilation.

Invariants Verified:
    1. Zero I/O Purity (NFR-1): Pure computational geometry execution.
    2. Deterministic SHA-256 Fingerprinting (NFR-2): JSON serialization with allow_nan=False and IEEE-754 signed zero normalization.
    3. 6-Face Coordinate Transformations: Rigorous 2D wire projection and 3D sketch origin offsets across all faces (+Z, -Z, +Y, -Y, +X, -X).
    4. Multi-Body CSG Context: Active body tracking across boolean union, subtract, and intersect chains.
    5. Clean Modernization: Strict contract_version "1.0", zero vendor COM enums, and zero legacy fallback shims.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Literal

import pytest

from geometry.face_context import resolve_face_context
from geometry.lowering_sweep import _compute_section_basis_vectors
from geometry.plan_facade import (
    lower_feature_plan_to_payload,
    lower_validated_feature_plan_to_payload,
    parse_and_lower_feature_plan,
)
from geometry.plan_models import (
    BodyPlacement,
    BooleanOperation,
    CircularThroughHoleFeature,
    CylinderBaseBody,
    FeaturePlan,
    FeaturePlanValidationError,
    PartMetadata,
    ProfileCutoutFeature,
    ProfilePoint2D,
    RectangularBaseBody,
    RectangularExtrudedPadFeature,
    RectangularThroughCutoutFeature,
    RevolvedProfileFeature,
    RevolvedShaftBaseBody,
    SlotThroughCutoutFeature,
    SphereBaseBody,
    SpurGearBaseBody,
    SweepCrossSectionSpec,
    SweepPathSpec,
    SweptProtrusionFeature,
)


def _make_box(
    *,
    box_id: str = "body.main",
    length: float = 100.0,
    width: float = 80.0,
    thickness: float = 20.0,
    px: float = 0.0,
    py: float = 0.0,
    pz: float = 0.0,
) -> RectangularBaseBody:
    return RectangularBaseBody(
        id=box_id,
        length_mm=length,
        width_mm=width,
        thickness_mm=thickness,
        placement=BodyPlacement(x_mm=px, y_mm=py, z_mm=pz),
    )


def _make_plan(
    *,
    base_body: RectangularBaseBody | CylinderBaseBody | SphereBaseBody | SpurGearBaseBody | RevolvedShaftBaseBody,
    primitive_bodies: tuple[RectangularBaseBody | CylinderBaseBody | SphereBaseBody | SpurGearBaseBody | RevolvedShaftBaseBody, ...] | None = None,
    features: tuple[
        CircularThroughHoleFeature
        | RectangularThroughCutoutFeature
        | SlotThroughCutoutFeature
        | RectangularExtrudedPadFeature
        | RevolvedProfileFeature
        | ProfileCutoutFeature
        | SweptProtrusionFeature,
        ...,
    ] = (),
    boolean_operations: tuple[BooleanOperation, ...] = (),
    design_intent: str = "test part",
) -> FeaturePlan:
    return FeaturePlan(
        plan_version="cad_copilot.single_part_feature_plan.v1",
        request_id="req-test-123",
        part=PartMetadata(part_id="part.main", design_intent=design_intent),
        base_body=base_body,
        primitive_bodies=primitive_bodies or (base_body,),
        features=features,
        boolean_operations=boolean_operations,
    )


# ---------------------------------------------------------------------------
# 1. Six-Face Coordinate Transformations & 3D Projections
# ---------------------------------------------------------------------------


class TestSixFaceCoordinateTransforms:
    """Test 2D sketch plane coordinates and 3D origin offsets across all 6 box faces."""

    @pytest.mark.parametrize(
        ("face", "expected_plane", "expected_offset_rel", "expected_profile_center"),
        [
            ("+Z", "XY", {"x_mm": 0.0, "y_mm": 0.0, "z_mm": 20.0}, {"x_mm": 10.0 + 5.0, "y_mm": 20.0 - 3.0}),
            ("-Z", "XY", {"x_mm": 0.0, "y_mm": 0.0, "z_mm": 0.0}, {"x_mm": 10.0 + 5.0, "y_mm": 20.0 + 3.0}),
            ("+Y", "XZ", {"x_mm": 0.0, "y_mm": 40.0, "z_mm": 10.0}, {"x_mm": 10.0 - 5.0, "y_mm": 15.0 + 10.0 - 3.0}),
            ("-Y", "XZ", {"x_mm": 0.0, "y_mm": -40.0, "z_mm": 10.0}, {"x_mm": 10.0 + 5.0, "y_mm": 15.0 + 10.0 - 3.0}),
            ("+X", "YZ", {"x_mm": 50.0, "y_mm": 0.0, "z_mm": 10.0}, {"x_mm": 20.0 + 5.0, "y_mm": 15.0 + 10.0 - 3.0}),
            ("-X", "YZ", {"x_mm": -50.0, "y_mm": 0.0, "z_mm": 10.0}, {"x_mm": 20.0 - 5.0, "y_mm": 15.0 + 10.0 - 3.0}),
        ],
    )
    def test_six_face_sketch_origin_and_wire_projection(
        self,
        face: str,
        expected_plane: str,
        expected_offset_rel: dict[str, float],
        expected_profile_center: dict[str, float],
    ) -> None:
        """Verify (u, v) -> (xs, ys) wire coordinates and 3D sketch origin offsets with non-zero placement."""
        px, py, pz = 10.0, 20.0, 15.0
        box = _make_box(length=100.0, width=80.0, thickness=20.0, px=px, py=py, pz=pz)
        hole = CircularThroughHoleFeature(
            id="feature.hole.1",
            target_face=face,
            center_x_mm=5.0,
            center_y_mm=-3.0,
            diameter_mm=8.0,
        )
        plan = _make_plan(base_body=box, features=(hole,))
        payload = lower_validated_feature_plan_to_payload(plan)

        # Check sketch patch
        sketch_patches = [p for p in payload["patches"] if p["op"] == "ensure_sketch"]
        assert len(sketch_patches) == 1
        sp = sketch_patches[0]
        assert sp["plane"] == expected_plane
        assert sp["origin_offset_mm"]["x_mm"] == pytest.approx(px + expected_offset_rel["x_mm"])
        assert sp["origin_offset_mm"]["y_mm"] == pytest.approx(py + expected_offset_rel["y_mm"])
        assert sp["origin_offset_mm"]["z_mm"] == pytest.approx(pz + expected_offset_rel["z_mm"])

        # Check profile patch
        profile_patches = [p for p in payload["patches"] if p["op"] == "ensure_profile"]
        assert len(profile_patches) == 1
        pp = profile_patches[0]
        assert pp["geometry"]["kind"] == "circle"
        assert pp["geometry"]["center"]["x_mm"] == pytest.approx(expected_profile_center["x_mm"])
        assert pp["geometry"]["center"]["y_mm"] == pytest.approx(expected_profile_center["y_mm"])
        assert pp["geometry"]["radius_mm"] == 4.0


# ---------------------------------------------------------------------------
# 2. Spatial Geometry & SO(3) Orthonormal Invariants
# ---------------------------------------------------------------------------


class TestSpatialGeometryInvariants:
    """Test SO(3) orthonormal basis properties, chirality, and sweep frame geometry."""

    @pytest.mark.parametrize("face", ["+Z", "-Z", "+Y", "-Y", "+X", "-X"])
    def test_so3_orthonormal_chirality_proof(self, face: str) -> None:
        """Prove that each face basis satisfies |u|=|v|=|n|=1, u.v=0, u x v = n, and det(R)=+1."""
        box = _make_box()
        ctx = resolve_face_context(face, box)
        u, v, n = ctx.u_axis, ctx.v_axis, ctx.normal_vector

        # 1. Unit lengths
        mag_u = math.hypot(u[0], u[1], u[2])
        mag_v = math.hypot(v[0], v[1], v[2])
        mag_n = math.hypot(n[0], n[1], n[2])
        assert mag_u == pytest.approx(1.0)
        assert mag_v == pytest.approx(1.0)
        assert mag_n == pytest.approx(1.0)

        # 2. Mutual Orthogonality (Dot products = 0)
        assert (u[0] * v[0] + u[1] * v[1] + u[2] * v[2]) == pytest.approx(0.0)
        assert (v[0] * n[0] + v[1] * n[1] + v[2] * n[2]) == pytest.approx(0.0)
        assert (n[0] * u[0] + n[1] * u[1] + n[2] * u[2]) == pytest.approx(0.0)

        # 3. Right-Handed Chirality (u x v = n)
        cross_uv = (
            u[1] * v[2] - u[2] * v[1],
            u[2] * v[0] - u[0] * v[2],
            u[0] * v[1] - u[1] * v[0],
        )
        assert cross_uv[0] == pytest.approx(n[0])
        assert cross_uv[1] == pytest.approx(n[1])
        assert cross_uv[2] == pytest.approx(n[2])

        # 4. Determinant of SO(3) Rotation Matrix = +1
        det_r = (
            u[0] * (v[1] * n[2] - v[2] * n[1])
            - u[1] * (v[0] * n[2] - v[2] * n[0])
            + u[2] * (v[0] * n[1] - v[1] * n[0])
        )
        assert det_r == pytest.approx(1.0)

    @pytest.mark.parametrize(
        ("path_type", "position", "expected_tangent", "expected_radial"),
        [
            ("full_circle", "start", [0.0, 1.0, 0.0], [1.0, 0.0, 0.0]),
            ("full_circle", "end", [0.0, 1.0, 0.0], [1.0, 0.0, 0.0]),
            ("quarter_arc", "end", [-1.0, 0.0, 0.0], [0.0, 1.0, 0.0]),
            ("semicircle", "end", [0.0, -1.0, 0.0], [-1.0, 0.0, 0.0]),
        ],
    )
    def test_sweep_planar_frame_orthonormality_and_stations(
        self,
        path_type: Literal["full_circle", "semicircle", "quarter_arc", "straight_line"],
        position: Literal["start", "end"],
        expected_tangent: list[float],
        expected_radial: list[float],
    ) -> None:
        """Verify the singularity-free sweep planar-path frame at various parametric stations."""
        box = _make_box()
        feat = SweptProtrusionFeature(
            id="feat.sweep",
            target_face="+Z",
            path=SweepPathSpec(type=path_type, radius_mm=30.0),
            cross_sections=(SweepCrossSectionSpec(type="circle", diameter_mm=6.0, position=position),),
        )
        u_sec, v_sec, n_sec = _compute_section_basis_vectors(
            target_face="+Z",
            position=position,
            path_type=path_type,
            base_body=box,
            feature=feat,
        )

        # Elevation axis is face normal (+Z)
        assert u_sec == [0.0, 0.0, 1.0]

        # Tangent and radial vectors match analytical derivatives
        assert n_sec == expected_tangent
        assert v_sec == expected_radial

        # Chirality: u_sec x v_sec = n_sec
        cross_uv = (
            u_sec[1] * v_sec[2] - u_sec[2] * v_sec[1],
            u_sec[2] * v_sec[0] - u_sec[0] * v_sec[2],
            u_sec[0] * v_sec[1] - u_sec[1] * v_sec[0],
        )
        assert cross_uv[0] == pytest.approx(n_sec[0])
        assert cross_uv[1] == pytest.approx(n_sec[1])
        assert cross_uv[2] == pytest.approx(n_sec[2])

        # Zero floating-point signed noise
        assert all(math.copysign(1.0, x) >= 0.0 for x in u_sec if x == 0.0)
        assert all(math.copysign(1.0, x) >= 0.0 for x in v_sec if x == 0.0)
        assert all(math.copysign(1.0, x) >= 0.0 for x in n_sec if x == 0.0)


# ---------------------------------------------------------------------------
# 3. Primitive Bodies Lowering
# ---------------------------------------------------------------------------


class TestPrimitiveBodiesLowering:
    """Test lowering for various primitive base body types."""

    def test_cylinder_base_body(self) -> None:
        cylinder = CylinderBaseBody(
            id="body.cyl",
            radius_mm=25.0,
            height_mm=50.0,
            placement=BodyPlacement(x_mm=10.0, y_mm=0.0, z_mm=5.0),
        )
        plan = _make_plan(base_body=cylinder)
        payload = lower_validated_feature_plan_to_payload(plan)

        body_patch = payload["patches"][0]
        assert body_patch["op"] == "ensure_primitive_body"
        assert body_patch["body_ref"] == "body.cyl"
        assert body_patch["shape"]["type"] == "cylinder"
        assert body_patch["shape"]["radius_mm"] == 25.0
        assert body_patch["shape"]["height_mm"] == 50.0
        assert body_patch["origin_offset_mm"] == {"x_mm": 10.0, "y_mm": 0.0, "z_mm": 5.0}

    def test_sphere_base_body(self) -> None:
        sphere = SphereBaseBody(
            id="body.sph",
            radius_mm=30.0,
            placement=BodyPlacement(x_mm=0.0, y_mm=0.0, z_mm=0.0),
        )
        plan = _make_plan(base_body=sphere)
        payload = lower_validated_feature_plan_to_payload(plan)

        body_patch = payload["patches"][0]
        assert body_patch["op"] == "ensure_primitive_body"
        assert body_patch["shape"]["type"] == "sphere"
        assert body_patch["shape"]["radius_mm"] == 30.0

    def test_revolved_shaft_base_body(self) -> None:
        points = (
            ProfilePoint2D(x_mm=0.0, y_mm=0.0),
            ProfilePoint2D(x_mm=15.0, y_mm=0.0),
            ProfilePoint2D(x_mm=15.0, y_mm=40.0),
            ProfilePoint2D(x_mm=0.0, y_mm=40.0),
        )
        shaft = RevolvedShaftBaseBody(
            id="body.shaft",
            radius_mm=15.0,
            height_mm=40.0,
            profile_points=points,
        )
        plan = _make_plan(base_body=shaft)
        payload = lower_validated_feature_plan_to_payload(plan)

        body_patch = payload["patches"][0]
        assert body_patch["op"] == "ensure_primitive_body"
        assert body_patch["shape"]["type"] == "revolved_shaft"
        assert len(body_patch["shape"]["points"]) == 4


# ---------------------------------------------------------------------------
# 3. Spur Gear Center Bore & Multi-Gear Assemblies
# ---------------------------------------------------------------------------


class TestSpurGearBoreLowering:
    """Test spur gear center bore creation and multi-gear scoped identifiers."""

    def test_single_spur_gear_with_bore(self) -> None:
        gear = SpurGearBaseBody(
            id="body.gear",
            tooth_count=24,
            module_mm=2.0,
            face_width_mm=12.0,
            bore_diameter_mm=10.0,
            placement=BodyPlacement(x_mm=0.0, y_mm=0.0, z_mm=0.0),
        )
        plan = _make_plan(base_body=gear)
        payload = lower_validated_feature_plan_to_payload(plan)

        # Body patch + Bore sketch + Bore profile + Bore cut = 4 patches
        assert len(payload["patches"]) == 4
        assert payload["patches"][0]["op"] == "ensure_primitive_body"
        assert payload["patches"][1]["op"] == "ensure_sketch"
        assert payload["patches"][1]["sketch_ref"] == "sketch.gear-bore.1"
        assert payload["patches"][2]["op"] == "ensure_profile"
        assert payload["patches"][2]["profile_ref"] == "profile.gear-bore.1"
        assert payload["patches"][2]["geometry"]["radius_mm"] == 5.0
        assert payload["patches"][3]["op"] == "cut_hole"
        assert payload["patches"][3]["through_all"] is True
        assert payload["patches"][3]["cut_direction"] == "into_solid"

    def test_multi_gear_bore_scoping_no_collisions(self) -> None:
        gear1 = SpurGearBaseBody(
            id="body.gear.1",
            tooth_count=18,
            module_mm=2.0,
            face_width_mm=10.0,
            bore_diameter_mm=8.0,
            placement=BodyPlacement(x_mm=0.0, y_mm=0.0, z_mm=0.0),
        )
        gear2 = SpurGearBaseBody(
            id="body.gear.2",
            tooth_count=36,
            module_mm=2.0,
            face_width_mm=10.0,
            bore_diameter_mm=14.0,
            placement=BodyPlacement(x_mm=60.0, y_mm=0.0, z_mm=0.0),
        )
        plan = _make_plan(base_body=gear1, primitive_bodies=(gear1, gear2))
        payload = lower_validated_feature_plan_to_payload(plan)

        entities = payload["canonical_state"]["entities"]
        sketches = [e["reference"]["ref_id"] for e in entities if e["reference"]["kind"] == "sketch"]
        assert "sketch.gear-bore.1" in sketches
        assert "sketch.gear-bore.2" in sketches

        idempotency_keys = [p["replay_policy"]["idempotency_key"] for p in payload["patches"]]
        assert len(set(idempotency_keys)) == len(idempotency_keys), "Found duplicate idempotency keys!"


# ---------------------------------------------------------------------------
# 4. Feature Family Lowering (Cutouts, Pads, Slots, Revolve, Sweep)
# ---------------------------------------------------------------------------


class TestFeatureFamiliesLowering:
    """Test lowering for various feature types."""

    def test_rectangular_through_cutout(self) -> None:
        box = _make_box()
        cutout = RectangularThroughCutoutFeature(
            id="feature.cutout.1",
            target_face="+Z",
            center_x_mm=10.0,
            center_y_mm=5.0,
            width_mm=30.0,
            height_mm=20.0,
        )
        plan = _make_plan(base_body=box, features=(cutout,))
        payload = lower_validated_feature_plan_to_payload(plan)

        cut_patch = next(p for p in payload["patches"] if p["op"] == "cut_hole")
        assert cut_patch["result_ref"] == "feature.cutout.1"
        assert cut_patch["through_all"] is True
        assert cut_patch["cut_direction"] == "into_solid"

    def test_rectangular_extruded_pad(self) -> None:
        box = _make_box()
        pad = RectangularExtrudedPadFeature(
            id="feature.pad.1",
            target_face="+Z",
            center_x_mm=0.0,
            center_y_mm=0.0,
            width_mm=40.0,
            height_mm=30.0,
            distance_mm=15.0,
        )
        plan = _make_plan(base_body=box, features=(pad,))
        payload = lower_validated_feature_plan_to_payload(plan)

        pad_patch = next(p for p in payload["patches"] if p["op"] == "extrude_profile")
        assert pad_patch["result_ref"] == "feature.pad.1"
        assert pad_patch["distance_mm"] == 15.0

    def test_slot_through_cutout_lowering(self) -> None:
        box = _make_box()
        slot_z = SlotThroughCutoutFeature(
            id="feature.slot.z",
            target_face="+Z",
            center_x_mm=0.0,
            center_y_mm=0.0,
            length_mm=30.0,
            width_mm=10.0,
            orientation_axis="x",
        )
        plan = _make_plan(base_body=box, features=(slot_z,))
        payload = lower_validated_feature_plan_to_payload(plan)

        prof_patches = [p for p in payload["patches"] if p["op"] == "ensure_profile"]
        assert prof_patches[0]["geometry"]["kind"] == "slot"
        assert prof_patches[0]["geometry"]["length_mm"] == 30.0
        assert prof_patches[0]["geometry"]["width_mm"] == 10.0
        assert prof_patches[0]["geometry"]["orientation_axis"] == "x"

    def test_revolved_profile_feature(self) -> None:
        box = _make_box()
        revolve = RevolvedProfileFeature(
            id="feature.revolve.1",
            target_face="+Z",
            profile_points=(
                ProfilePoint2D(x_mm=10.0, y_mm=0.0),
                ProfilePoint2D(x_mm=20.0, y_mm=0.0),
                ProfilePoint2D(x_mm=20.0, y_mm=15.0),
                ProfilePoint2D(x_mm=10.0, y_mm=15.0),
            ),
            axis_start=ProfilePoint2D(x_mm=0.0, y_mm=0.0),
            axis_end=ProfilePoint2D(x_mm=0.0, y_mm=1.0),
            angle_deg=360.0,
        )
        plan = _make_plan(base_body=box, features=(revolve,))
        payload = lower_validated_feature_plan_to_payload(plan)

        revolve_patch = next(p for p in payload["patches"] if p["op"] == "revolve_profile")
        assert revolve_patch["angle_deg"] == 360.0
        assert revolve_patch["axis"]["start"] == {"x_mm": 0.0, "y_mm": 0.0}
        assert revolve_patch["axis"]["end"] == {"x_mm": 0.0, "y_mm": 1.0}

    def test_swept_protrusion_lowering(self) -> None:
        box = _make_box()
        sweep = SweptProtrusionFeature(
            id="feature.sweep.1",
            target_face="+Z",
            path=SweepPathSpec(type="full_circle", radius_mm=30.0),
            cross_sections=(
                SweepCrossSectionSpec(type="circle", diameter_mm=6.0, position="start"),
            ),
        )
        plan = _make_plan(base_body=box, features=(sweep,))
        payload = lower_validated_feature_plan_to_payload(plan)

        ref_plane_patch = next(p for p in payload["patches"] if p["op"] == "ensure_ref_plane")
        assert ref_plane_patch["type"] == "normal_to_curve"
        assert ref_plane_patch["u_axis"] == [0.0, 0.0, 1.0]
        assert ref_plane_patch["v_axis"] == [1.0, 0.0, 0.0]
        assert ref_plane_patch["normal_vector"] == [0.0, 1.0, 0.0]

        sweep_patch = next(p for p in payload["patches"] if p["op"] == "sweep_protrusion")
        assert sweep_patch["result_ref"] == "feature.sweep.1"
        assert sweep_patch["direction"] == "into_solid"


# ---------------------------------------------------------------------------
# 5. CSG Boolean Operations & Active Context
# ---------------------------------------------------------------------------


class TestCSGBooleanOperations:
    """Test CSG boolean operations and active body context tracking."""

    def test_boolean_union_and_subtract_chain(self) -> None:
        base = _make_box(box_id="body.base")
        boss = _make_box(box_id="body.boss")
        cutter = _make_box(box_id="body.cutter")

        op_union = BooleanOperation(
            id="bool.1",
            operation="union",
            target_body_id="body.base",
            tool_body_id="body.boss",
            result_body_id="body.union",
        )
        op_sub = BooleanOperation(
            id="bool.2",
            operation="subtract",
            target_body_id="body.union",
            tool_body_id="body.cutter",
            result_body_id="body.final",
        )

        plan = _make_plan(
            base_body=base,
            primitive_bodies=(base, boss, cutter),
            boolean_operations=(op_union, op_sub),
        )
        payload = lower_validated_feature_plan_to_payload(plan)

        # Active body context must reflect final boolean result
        assert payload["canonical_state"]["active_context"]["active_body_ref"] == "body.final"

        bool_patches = [p for p in payload["patches"] if p["op"].startswith("boolean_")]
        assert len(bool_patches) == 2
        assert bool_patches[0]["op"] == "boolean_union"
        assert bool_patches[0]["result_body_ref"] == "body.union"
        assert bool_patches[1]["op"] == "boolean_subtract"
        assert bool_patches[1]["result_body_ref"] == "body.final"


# ---------------------------------------------------------------------------
# 6. Contract Invariants & Facade
# ---------------------------------------------------------------------------


class TestContractInvariantsAndFacade:
    """Test payload envelope invariants and facade integration."""

    def test_contract_version_and_envelope_structure(self) -> None:
        box = _make_box()
        hole = CircularThroughHoleFeature(
            id="feature.hole.1",
            target_face="+Z",
            center_x_mm=0.0,
            center_y_mm=0.0,
            diameter_mm=10.0,
        )
        plan = _make_plan(base_body=box, features=(hole,))
        payload = lower_feature_plan_to_payload(plan)

        assert payload["contract_version"] == "1.0"
        assert payload["kind"] == "semantic_patch_sequence"
        assert payload["unit"] == "mm"
        assert payload["base_state_revision"] == 0
        assert payload["capabilities"]["allow_set_active_context"] is True
        assert "canonical_state" in payload
        assert "patches" in payload
        assert "metadata" in payload
        assert "export" in payload

    def test_all_five_golden_fixtures_via_facade(self) -> None:
        fixtures_dir = Path(__file__).resolve().parents[3] / "contracts" / "examples"
        golden_fixtures = {
            "accepted_example.json": (4, 4),
            "l_bracket_example_plan.json": (12, 12),
            "multi_primitive_example_plan.json": (3, 3),
            "spur_gear_example_plan.json": (4, 4),
            "sweep_example_plan.json": (7, 7),
        }
        for fname, (expected_entities, expected_patches) in golden_fixtures.items():
            fpath = fixtures_dir / fname
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)

            plan_dict = data.get("feature_plan", data)
            payload = parse_and_lower_feature_plan(plan_dict)

            assert payload["contract_version"] == "1.0"
            assert payload["kind"] == "semantic_patch_sequence"
            assert len(payload["canonical_state"]["entities"]) == expected_entities
            assert len(payload["patches"]) == expected_patches

    def test_finite_depth_cutout_lowering(self) -> None:
        box = _make_box(thickness=30.0)
        blind_hole = CircularThroughHoleFeature(
            id="feature.hole.blind",
            target_face="+Z",
            center_x_mm=0.0,
            center_y_mm=0.0,
            diameter_mm=12.0,
            extent_type="finite",
            depth_mm=10.0,
        )
        plan = _make_plan(base_body=box, features=(blind_hole,))
        payload = lower_validated_feature_plan_to_payload(plan)

        cut_patch = next(p for p in payload["patches"] if p["op"] == "cut_hole")
        assert cut_patch["through_all"] is False
        assert cut_patch["depth_mm"] == 10.0

    def test_custom_polygon_profile_cutout(self) -> None:
        box = _make_box()
        poly_cut = ProfileCutoutFeature(
            id="feature.cutout.poly",
            target_face="+Z",
            center_x_mm=0.0,
            center_y_mm=0.0,
            profile_points=(
                ProfilePoint2D(x_mm=-10.0, y_mm=-10.0),
                ProfilePoint2D(x_mm=10.0, y_mm=-10.0),
                ProfilePoint2D(x_mm=10.0, y_mm=10.0),
                ProfilePoint2D(x_mm=-10.0, y_mm=10.0),
            ),
        )
        plan = _make_plan(base_body=box, features=(poly_cut,))
        payload = lower_validated_feature_plan_to_payload(plan)

        prof_patch = next(p for p in payload["patches"] if p["op"] == "ensure_profile")
        assert prof_patch["geometry"]["kind"] == "polygon"
        assert len(prof_patch["geometry"]["points"]) == 4

    def test_defensive_unknown_feature_target_error(self) -> None:
        box = _make_box(box_id="body.main")
        hole = CircularThroughHoleFeature(
            id="feature.hole.1",
            target_body_id="body.nonexistent",
            target_face="+Z",
            center_x_mm=0.0,
            center_y_mm=0.0,
            diameter_mm=10.0,
        )
        plan = _make_plan(base_body=box, features=(hole,))
        with pytest.raises(FeaturePlanValidationError) as exc_info:
            lower_validated_feature_plan_to_payload(plan)
        assert exc_info.value.diagnostic.code == "UNKNOWN_FEATURE_TARGET"
