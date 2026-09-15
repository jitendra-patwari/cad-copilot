"""End-to-end unit and integration tests for AST plan validation and candidate retry orchestration.

Tests all 5 golden example fixtures, DoS resource limits, individual primitive validators,
stepped shaft boundary profiles, multi-hole separation, and gate policy modes.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from geometry import (
    CANONICAL_PLAN_VERSION,
    CircularThroughHoleFeature,
    FeaturePlan,
    FeaturePlanValidationError,
    LoweringStrategy,
    PartMetadata,
    ProfilePoint2D,
    RectangularBaseBody,
    RevolvedShaftBaseBody,
    SlotThroughCutoutFeature,
    feature_plan_from_dict,
    should_retry_canonical_rejection,
    validate_feature_plan,
)
from geometry.validators.feature_revolve import _validate_revolved_shaft_profile

# Resolve path to root contracts/examples directory
EXAMPLES_DIR = Path(__file__).resolve().parents[3] / "contracts" / "examples"


def _load_example_fixture(filename: str) -> FeaturePlan:
    filepath = EXAMPLES_DIR / filename
    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)
    raw_plan = data.get("feature_plan", data)
    return feature_plan_from_dict(raw_plan)


class TestGoldenPlanValidation:
    """Validate all 5 canonical golden example fixtures from contracts/examples/."""

    def test_validate_accepted_example_fixture(self) -> None:
        plan = _load_example_fixture("accepted_example.json")
        validated = validate_feature_plan(plan)
        assert validated.plan_version == CANONICAL_PLAN_VERSION
        assert validated.units == "mm"
        assert len(validated.features) == 1
        assert isinstance(validated.features[0], CircularThroughHoleFeature)
        assert validated.features[0].target_face == "+Z"
        assert isinstance(validated.defaults_applied, tuple)

    def test_validate_l_bracket_example_fixture(self) -> None:
        plan = _load_example_fixture("l_bracket_example_plan.json")
        validated = validate_feature_plan(plan)
        assert len(validated.primitive_bodies) == 2
        assert len(validated.boolean_operations) == 1
        assert len(validated.features) == 3
        # Multiple holes produce multi-hole info diagnostic
        codes = [d.code for d in validated.validation_diagnostics]
        assert "CANONICAL_MULTI_HOLE_FAMILY" in codes

    def test_validate_multi_primitive_example_fixture(self) -> None:
        plan = _load_example_fixture("multi_primitive_example_plan.json")
        validated = validate_feature_plan(plan)
        assert len(validated.primitive_bodies) == 2
        assert len(validated.boolean_operations) == 1
        assert validated.boolean_operations[0].operation == "union"

    def test_validate_spur_gear_example_fixture(self) -> None:
        plan = _load_example_fixture("spur_gear_example_plan.json")
        validated = validate_feature_plan(plan)
        assert validated.base_body.family == "spur_gear"
        assert len(validated.features) == 0

    def test_validate_sweep_example_fixture(self) -> None:
        plan = _load_example_fixture("sweep_example_plan.json")
        validated = validate_feature_plan(plan)
        assert len(validated.features) == 1
        assert validated.features[0].family == "swept_protrusion"


class TestTopLevelContractRejections:
    """Verify schema version, units, scope, and lowering strategy invariants."""

    def test_unsupported_plan_version(self) -> None:
        plan = _load_example_fixture("accepted_example.json")
        bad_plan = replace(plan, plan_version="cad_copilot.single_part_feature_plan.v0_unsupported")
        with pytest.raises(FeaturePlanValidationError) as exc:
            validate_feature_plan(bad_plan)
        assert exc.value.code == "UNSUPPORTED_PLAN_VERSION"

    def test_unsupported_units(self) -> None:
        plan = _load_example_fixture("accepted_example.json")
        bad_plan = replace(plan, units=cast(Any, "inch"))
        with pytest.raises(FeaturePlanValidationError) as exc:
            validate_feature_plan(bad_plan)
        assert exc.value.code == "UNSUPPORTED_UNITS"

    def test_unsupported_scope(self) -> None:
        plan = _load_example_fixture("accepted_example.json")
        bad_part = replace(plan.part, scope=cast(Any, "assembly"))
        bad_plan = replace(plan, part=bad_part)
        with pytest.raises(FeaturePlanValidationError) as exc:
            validate_feature_plan(bad_plan)
        assert exc.value.code == "UNSUPPORTED_SCOPE"

    def test_unsupported_lowering_strategy_non_canonical(self) -> None:
        plan = _load_example_fixture("accepted_example.json")
        bad_strategy = replace(plan.lowering_strategy, status=cast(Any, "experimental"))
        bad_plan = replace(plan, lowering_strategy=bad_strategy)
        with pytest.raises(FeaturePlanValidationError) as exc:
            validate_feature_plan(bad_plan)
        assert exc.value.code == "UNSUPPORTED_LOWERING_STRATEGY"

    def test_unsupported_lowering_strategy_route_change(self) -> None:
        plan = _load_example_fixture("accepted_example.json")
        bad_strategy = LoweringStrategy(status="canonical", runtime_route_change=True)
        bad_plan = replace(plan, lowering_strategy=bad_strategy)
        with pytest.raises(FeaturePlanValidationError) as exc:
            validate_feature_plan(bad_plan)
        assert exc.value.code == "UNSUPPORTED_LOWERING_STRATEGY"


class TestCWE400ResourceDoSBoundaries:
    """Enforce bounded complexity limits to prevent DoS attacks (CWE-400)."""

    def test_excessive_feature_count(self) -> None:
        plan = _load_example_fixture("accepted_example.json")
        hole = plan.features[0]
        many_features = tuple(replace(hole, id=f"hole_{i}") for i in range(129))
        bad_plan = replace(plan, features=many_features)
        with pytest.raises(FeaturePlanValidationError) as exc:
            validate_feature_plan(bad_plan)
        assert exc.value.code == "EXCESSIVE_FEATURE_COUNT"

    def test_excessive_primitive_body_count(self) -> None:
        plan = _load_example_fixture("accepted_example.json")
        body = plan.base_body
        many_bodies = tuple(replace(body, id=f"body_{i}") for i in range(33))
        bad_plan = replace(plan, primitive_bodies=many_bodies)
        with pytest.raises(FeaturePlanValidationError) as exc:
            validate_feature_plan(bad_plan)
        assert exc.value.code == "EXCESSIVE_PRIMITIVE_BODY_COUNT"


class TestFeatureValidationAndRejections:
    """Verify feature id uniqueness, target resolution, and geometry invariants."""

    def test_duplicate_feature_id(self) -> None:
        plan = _load_example_fixture("accepted_example.json")
        hole = plan.features[0]
        assert isinstance(hole, CircularThroughHoleFeature)
        duplicate_features = (hole, replace(hole, center_x_mm=10.0))
        bad_plan = replace(plan, features=duplicate_features)
        with pytest.raises(FeaturePlanValidationError) as exc:
            validate_feature_plan(bad_plan)
        assert exc.value.code == "DUPLICATE_FEATURE_ID"

    def test_unknown_target_body_id(self) -> None:
        plan = _load_example_fixture("accepted_example.json")
        feat = plan.features[0]
        assert isinstance(feat, CircularThroughHoleFeature)
        hole = replace(feat, target_body_id="body.nonexistent")
        bad_plan = replace(plan, features=(hole,))
        with pytest.raises(FeaturePlanValidationError) as exc:
            validate_feature_plan(bad_plan)
        assert exc.value.code == "UNKNOWN_FEATURE_TARGET"

    def test_invalid_slot_dimensions(self) -> None:
        body = RectangularBaseBody(id="b1", length_mm=100.0, width_mm=100.0, thickness_mm=10.0)
        # Slot length must be strictly greater than slot width
        bad_slot = SlotThroughCutoutFeature(id="s1", length_mm=10.0, width_mm=20.0, target_body_id="b1")
        plan = FeaturePlan(request_id="r1", part=PartMetadata(), base_body=body, features=(bad_slot,))
        with pytest.raises(FeaturePlanValidationError) as exc:
            validate_feature_plan(plan)
        assert exc.value.code == "INVALID_SLOT_DIMENSIONS"


class TestSteppedShaftProfileSuite:
    """Verify stepped shaft axial monotonicity, radial shoulder acceptance, and duplicate point rejection."""

    def test_valid_decreasing_radius_step(self) -> None:
        # Step from radius 10mm down to 5mm at height 20mm
        points = (
            ProfilePoint2D(0.0, 0.0),
            ProfilePoint2D(10.0, 0.0),
            ProfilePoint2D(10.0, 20.0),
            ProfilePoint2D(5.0, 20.0),  # Radial step down
            ProfilePoint2D(5.0, 40.0),
            ProfilePoint2D(0.0, 40.0),
        )
        body = RevolvedShaftBaseBody(id="sh1", radius_mm=10.0, height_mm=40.0, profile_points=points)
        plan = FeaturePlan(request_id="r1", part=PartMetadata(), base_body=body)
        validated = validate_feature_plan(plan)
        assert validated.base_body.family == "revolved_shaft"

    def test_valid_increasing_radius_step(self) -> None:
        # Step from radius 5mm up to 10mm at height 20mm
        points = (
            ProfilePoint2D(0.0, 0.0),
            ProfilePoint2D(5.0, 0.0),
            ProfilePoint2D(5.0, 20.0),
            ProfilePoint2D(10.0, 20.0),  # Radial step up
            ProfilePoint2D(10.0, 40.0),
            ProfilePoint2D(0.0, 40.0),
        )
        body = RevolvedShaftBaseBody(id="sh1", radius_mm=10.0, height_mm=40.0, profile_points=points)
        plan = FeaturePlan(request_id="r1", part=PartMetadata(), base_body=body)
        validated = validate_feature_plan(plan)
        assert validated.base_body.family == "revolved_shaft"

    def test_invalid_duplicate_profile_point(self) -> None:
        points = (
            ProfilePoint2D(0.0, 0.0),
            ProfilePoint2D(10.0, 0.0),
            ProfilePoint2D(10.0, 20.0),
            ProfilePoint2D(10.0, 20.0),  # Duplicate point (zero-length segment)
            ProfilePoint2D(0.0, 40.0),
        )
        body = RevolvedShaftBaseBody(id="sh1", radius_mm=10.0, height_mm=40.0, profile_points=points)
        plan = FeaturePlan(request_id="r1", part=PartMetadata(), base_body=body)
        with pytest.raises(FeaturePlanValidationError) as exc:
            validate_feature_plan(plan)
        assert exc.value.code == "INVALID_GEOMETRY"

    def test_unsorted_shaft_profile_is_normalized(self) -> None:
        # Unsorted profile points are automatically sorted monotonically by axial height
        points = (
            ProfilePoint2D(0.0, 0.0),
            ProfilePoint2D(10.0, 0.0),
            ProfilePoint2D(10.0, 30.0),
            ProfilePoint2D(5.0, 10.0),  # Unsorted: height 10.0 after 30.0
            ProfilePoint2D(0.0, 40.0),
        )
        body = RevolvedShaftBaseBody(id="sh1", radius_mm=10.0, height_mm=40.0, profile_points=points)
        plan = FeaturePlan(request_id="r1", part=PartMetadata(), base_body=body)
        validated = validate_feature_plan(plan)
        assert any(d.reason == "revolved_shaft_profile_normalization" for d in validated.defaults_applied)
        assert isinstance(validated.base_body, RevolvedShaftBaseBody)
        assert [p.y_mm for p in validated.base_body.profile_points] == [0.0, 0.0, 10.0, 30.0, 40.0]

    def test_direct_revolved_shaft_validation_rejects_unsorted_strict(self) -> None:
        points = (
            ProfilePoint2D(0.0, 0.0),
            ProfilePoint2D(10.0, 0.0),
            ProfilePoint2D(10.0, 30.0),
            ProfilePoint2D(5.0, 10.0),
            ProfilePoint2D(0.0, 40.0),
        )
        body = RevolvedShaftBaseBody(id="sh1", radius_mm=10.0, height_mm=40.0, profile_points=points)
        with pytest.raises(FeaturePlanValidationError) as exc:
            _validate_revolved_shaft_profile(body, path="base_body", mode="strict")
        assert exc.value.code == "INVALID_GEOMETRY"


class TestMultiHoleSeparationAndDiagnostics:
    """Verify pairwise separation and multi-hole family informational diagnostics."""

    def test_overlapping_holes_strict_mode(self) -> None:
        body = RectangularBaseBody(id="b1", length_mm=100.0, width_mm=100.0, thickness_mm=10.0)
        h1 = CircularThroughHoleFeature(
            id="h1", diameter_mm=20.0, center_x_mm=0.0, center_y_mm=0.0, target_body_id="b1"
        )
        h2 = CircularThroughHoleFeature(
            id="h2", diameter_mm=20.0, center_x_mm=5.0, center_y_mm=0.0, target_body_id="b1"
        )
        plan = FeaturePlan(request_id="r1", part=PartMetadata(), base_body=body, features=(h1, h2))
        with pytest.raises(FeaturePlanValidationError) as exc:
            validate_feature_plan(plan, mode="strict")
        assert exc.value.code == "HOLES_OVERLAP"


class TestGatePolicyModesAndRetryPolicy:
    """Verify gate mode propagation and candidate retry evaluation."""

    def test_gate_policy_mode_propagation(self) -> None:
        body = RectangularBaseBody(id="b1", length_mm=100.0, width_mm=100.0, thickness_mm=10.0)
        # Hole close to edge with 0 edge margin is relaxed warning in capability_first
        h1 = CircularThroughHoleFeature(
            id="h1", diameter_mm=10.0, center_x_mm=48.0, center_y_mm=0.0, target_body_id="b1"
        )
        plan = FeaturePlan(request_id="r1", part=PartMetadata(), base_body=body, features=(h1,))

        # In capability_first mode: warning diagnostic emitted
        validated_cap = validate_feature_plan(plan, mode="capability_first")
        assert len(validated_cap.validation_diagnostics) >= 1
        assert any(d.code == "GATE_POLICY_RELAXED" for d in validated_cap.validation_diagnostics)

        # In strict mode: hard rejection raised
        with pytest.raises(FeaturePlanValidationError) as exc:
            validate_feature_plan(plan, mode="strict")
        assert exc.value.code == "HOLE_DOES_NOT_FIT"

    def test_should_retry_canonical_rejection(self) -> None:
        assert should_retry_canonical_rejection("HOLE_DOES_NOT_FIT") is True
        assert should_retry_canonical_rejection("INVALID_SLOT_DIMENSIONS") is True
        assert should_retry_canonical_rejection(None) is True
        # Safety policy and units violations are non-retryable
        assert should_retry_canonical_rejection("safety_or_policy") is False
        assert should_retry_canonical_rejection("unsupported_units") is False


class TestParserFallbacksRejectionInStrictMode:
    """Verify that mode-neutral parser fallback warnings are rejected when mode='strict'."""

    def test_strict_mode_rejects_unknown_boolean_operation_fallback(self) -> None:
        payload = {
            "part": {"part_id": "test"},
            "base_body": {"id": "body.1", "family": "cylinder", "dimensions_mm": {"radius": 10, "height": 20}},
            "primitive_bodies": [
                {"id": "body.1", "family": "cylinder", "dimensions_mm": {"radius": 10, "height": 20}},
                {"id": "body.2", "family": "sphere", "dimensions_mm": {"radius": 5}},
            ],
            "boolean_operations": [
                {"id": "b1", "operation": "bad_op", "target_body_id": "body.1", "tool_body_id": "body.2"}
            ],
            "features": [],
        }
        plan = feature_plan_from_dict(payload)
        with pytest.raises(FeaturePlanValidationError) as exc:
            validate_feature_plan(plan, mode="strict")
        assert exc.value.code == "UNKNOWN_BOOLEAN_OPERATION"

    def test_strict_mode_rejects_unknown_face_alias_fallback(self) -> None:
        payload = {
            "part": {"part_id": "test"},
            "base_body": {"family": "rectangular_prism", "dimensions_mm": {"length": 50, "width": 50, "thickness": 10}},
            "features": [
                {
                    "family": "circular_through_hole",
                    "target": {"face": {"resolved_face": "invalid_slant_face"}},
                    "dimensions_mm": {"diameter": 10.0},
                }
            ],
        }
        plan = feature_plan_from_dict(payload)
        with pytest.raises(FeaturePlanValidationError) as exc:
            validate_feature_plan(plan, mode="strict")
        assert exc.value.code == "UNKNOWN_FACE_ALIAS"

    @pytest.mark.parametrize("face_alias", ["top", "bottom", "+z", "-z", "right", "front"])
    def test_strict_mode_accepts_clean_face_aliases(self, face_alias: str) -> None:
        payload = {
            "part": {"part_id": "test"},
            "base_body": {"family": "rectangular_prism", "dimensions_mm": {"length": 50, "width": 50, "thickness": 10}},
            "features": [
                {
                    "family": "circular_through_hole",
                    "target": {"face": {"resolved_face": face_alias}},
                    "dimensions_mm": {"diameter": 10.0},
                }
            ],
        }
        plan = feature_plan_from_dict(payload)
        validated = validate_feature_plan(plan, mode="strict")
        assert validated is not None
        assert not any(d.severity == "warning" for d in validated.validation_diagnostics)

    @pytest.mark.parametrize("op_alias", ["cut", "difference", "subtract", "union", "add", "intersect"])
    def test_strict_mode_accepts_clean_boolean_operation_aliases(self, op_alias: str) -> None:
        payload = {
            "part": {"part_id": "test"},
            "base_body": {"id": "body.1", "family": "cylinder", "dimensions_mm": {"radius": 10, "height": 20}},
            "primitive_bodies": [
                {"id": "body.1", "family": "cylinder", "dimensions_mm": {"radius": 10, "height": 20}},
                {"id": "body.2", "family": "sphere", "dimensions_mm": {"radius": 5}},
            ],
            "boolean_operations": [
                {"id": "b1", "operation": op_alias, "target_body_id": "body.1", "tool_body_id": "body.2"}
            ],
            "features": [],
        }
        plan = feature_plan_from_dict(payload)
        validated = validate_feature_plan(plan, mode="strict")
        assert validated is not None
        assert not any(d.severity == "warning" for d in validated.validation_diagnostics)

    def test_revolved_shaft_normalization_propagates_to_primitive_bodies(self) -> None:
        points = (ProfilePoint2D(10.0, 0.0), ProfilePoint2D(-2.0, 10.0), ProfilePoint2D(5.0, 20.0))
        shaft = RevolvedShaftBaseBody(id="body.shaft", radius_mm=10.0, height_mm=20.0, profile_points=points)
        plan = FeaturePlan(
            request_id="req.shaft",
            part=PartMetadata(part_id="shaft_part"),
            base_body=shaft,
            primitive_bodies=(shaft,),
        )
        validated = validate_feature_plan(plan)
        assert isinstance(validated.base_body, RevolvedShaftBaseBody)
        assert validated.base_body.profile_points[1].x_mm == 0.0
        assert len(validated.primitive_bodies) == 1
        assert isinstance(validated.primitive_bodies[0], RevolvedShaftBaseBody)
        assert validated.primitive_bodies[0].profile_points[1].x_mm == 0.0

    def test_feature_id_colliding_with_body_id_rejected(self) -> None:
        box = RectangularBaseBody(id="body.main", length_mm=100.0, width_mm=100.0, thickness_mm=10.0)
        hole = CircularThroughHoleFeature(id="body.main", diameter_mm=10.0, target_body_id="body.main")
        plan = FeaturePlan(
            request_id="req.dup",
            part=PartMetadata(part_id="dup_part"),
            base_body=box,
            features=(hole,),
        )
        with pytest.raises(FeaturePlanValidationError) as exc:
            validate_feature_plan(plan)
        assert exc.value.code == "DUPLICATE_FEATURE_ID"

    def test_feature_id_reserved_prefix_rejected(self) -> None:
        box = RectangularBaseBody(id="body.main", length_mm=100.0, width_mm=100.0, thickness_mm=10.0)
        hole = CircularThroughHoleFeature(id="sketch.hole.1", diameter_mm=10.0, target_body_id="body.main")
        plan = FeaturePlan(
            request_id="req.res",
            part=PartMetadata(part_id="res_part"),
            base_body=box,
            features=(hole,),
        )
        with pytest.raises(FeaturePlanValidationError) as exc:
            validate_feature_plan(plan)
        assert exc.value.code == "RESERVED_IDENTIFIER_PREFIX"
