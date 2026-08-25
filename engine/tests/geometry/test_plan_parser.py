"""Unit and validation tests for AST plan deserialization and defensive bounds."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from geometry import (
    CANONICAL_PLAN_VERSION,
    FeaturePlanValidationError,
    feature_plan_from_dict,
)


class TestPlanParserGoldenFixtures:
    """Verify parsing of all canonical Golden AST fixtures in contracts/examples/."""

    @pytest.fixture
    def contracts_examples_dir(self) -> Path:
        return Path(__file__).resolve().parents[3] / "contracts" / "examples"

    @pytest.mark.parametrize(
        "fixture_filename",
        [
            "accepted_example.json",
            "l_bracket_example_plan.json",
            "multi_primitive_example_plan.json",
            "spur_gear_example_plan.json",
            "sweep_example_plan.json",
        ],
    )
    def test_parse_golden_example_plans(self, contracts_examples_dir: Path, fixture_filename: str) -> None:
        fixture_path = contracts_examples_dir / fixture_filename
        assert fixture_path.is_file(), f"Fixture not found: {fixture_path}"

        raw_data = json.loads(fixture_path.read_text(encoding="utf-8"))
        plan_dict = raw_data.get("feature_plan", raw_data)

        plan = feature_plan_from_dict(plan_dict)

        assert plan.plan_version == CANONICAL_PLAN_VERSION
        assert plan.units == "mm"
        assert plan.base_body is not None


class TestDefensiveBounds:
    """Verify CWE-400 defensive bounding enforcement in plan_parser.py."""

    def test_excessive_features_rejected(self) -> None:
        payload = {
            "part": {"part_id": "test"},
            "base_body": {
                "family": "rectangular_prism",
                "dimensions_mm": {"length": 80, "width": 40, "thickness": 6},
            },
            "features": [{"family": "circular_through_hole"} for _ in range(129)],
        }
        with pytest.raises(FeaturePlanValidationError) as exc:
            feature_plan_from_dict(payload)
        assert exc.value.code == "EXCESSIVE_FEATURE_COUNT"

    def test_excessive_primitive_bodies_rejected(self) -> None:
        payload = {
            "part": {"part_id": "test"},
            "base_body": {
                "family": "rectangular_prism",
                "dimensions_mm": {"length": 80, "width": 40, "thickness": 6},
            },
            "primitive_bodies": [{"family": "cylinder"} for _ in range(33)],
            "features": [],
        }
        with pytest.raises(FeaturePlanValidationError) as exc:
            feature_plan_from_dict(payload)
        assert exc.value.code == "EXCESSIVE_PRIMITIVE_BODIES"

    def test_excessive_sweep_cross_sections_rejected(self) -> None:
        payload = {
            "part": {"part_id": "test"},
            "base_body": {
                "family": "cylinder",
                "dimensions_mm": {"radius": 10, "height": 20},
            },
            "features": [
                {
                    "family": "swept_protrusion",
                    "path": {"type": "full_circle", "radius_mm": 50.0},
                    "cross_sections": [{"type": "circle", "diameter_mm": 5.0} for _ in range(17)],
                }
            ],
        }
        with pytest.raises(FeaturePlanValidationError) as exc:
            feature_plan_from_dict(payload)
        assert exc.value.code == "EXCESSIVE_SWEEP_CROSS_SECTIONS"


class TestModeNeutralParserFallbacks:
    """Verify fallback and warning recording for unrecognized domain values."""

    def test_unknown_boolean_operation_fallback(self) -> None:
        payload = {
            "part": {"part_id": "test"},
            "base_body": {"family": "cylinder", "dimensions_mm": {"radius": 10, "height": 20}},
            "primitive_bodies": [
                {"id": "body.1", "family": "cylinder", "dimensions_mm": {"radius": 10, "height": 20}},
                {"id": "body.2", "family": "sphere", "dimensions_mm": {"radius": 5}},
            ],
            "boolean_operations": [
                {"id": "b1", "operation": "unknown_combine", "target_body_id": "body.1", "tool_body_id": "body.2"}
            ],
            "features": [],
        }
        plan = feature_plan_from_dict(payload)
        assert plan.boolean_operations[0].operation == "union"
        assert any(d.original_value == "unknown_combine" for d in plan.defaults_applied)
        assert any(d.code == "UNKNOWN_BOOLEAN_OPERATION" for d in plan.validation_diagnostics)

    def test_unknown_sweep_path_and_section_fallback(self) -> None:
        payload = {
            "part": {"part_id": "test"},
            "base_body": {"family": "cylinder", "dimensions_mm": {"radius": 10, "height": 20}},
            "features": [
                {
                    "family": "swept_protrusion",
                    "path": {"type": "arbitrary_spline", "radius_mm": 50.0},
                    "cross_sections": [{"type": "hexagonal", "diameter_mm": 5.0, "position": "midpoint"}],
                }
            ],
        }
        plan = feature_plan_from_dict(payload)
        feat = plan.features[0]
        assert feat.path.type == "full_circle"
        assert feat.cross_sections[0].type == "circle"
        assert feat.cross_sections[0].position == "start"
        assert any(d.original_value == "arbitrary_spline" for d in plan.defaults_applied)
        assert any(d.original_value == "hexagonal" for d in plan.defaults_applied)
        assert any(d.original_value == "midpoint" for d in plan.defaults_applied)

    def test_unknown_placement_mode_and_slot_orientation(self) -> None:
        payload = {
            "part": {"part_id": "test"},
            "base_body": {"family": "rectangular_prism", "dimensions_mm": {"length": 50, "width": 50, "thickness": 10}},
            "features": [
                {
                    "family": "slot_through_cutout",
                    "placement": {"mode": "polar_offset"},
                    "orientation": {"axis": "diagonal_z"},
                    "dimensions_mm": {"length": 20, "width": 5},
                }
            ],
        }
        plan = feature_plan_from_dict(payload)
        feat = plan.features[0]
        assert feat.placement_mode == "face_local_center"
        assert feat.orientation_axis == "x"
        assert any(d.original_value == "polar_offset" for d in plan.defaults_applied)
        assert any(d.original_value == "diagonal_z" for d in plan.defaults_applied)

    def test_clean_slot_angle_aliases_accepted_without_warning(self) -> None:
        # Test 90deg variants mapping to 'y' without warnings
        for y_alias in ["90", "90deg", "90_deg", "90.0", "deg_90", "vertical"]:
            payload_y = {
                "part": {"part_id": "test"},
                "base_body": {
                    "family": "rectangular_prism",
                    "dimensions_mm": {"length": 50, "width": 50, "thickness": 10},
                },
                "features": [
                    {
                        "family": "slot_through_cutout",
                        "orientation": {"axis": y_alias},
                        "dimensions_mm": {"length": 20, "width": 5},
                    }
                ],
            }
            plan_y = feature_plan_from_dict(payload_y)
            assert plan_y.features[0].orientation_axis == "y"
            assert not any(d.code == "UNKNOWN_SLOT_ORIENTATION" for d in plan_y.validation_diagnostics)

        # Test 0deg variants mapping to 'x' without warnings
        for x_alias in ["0", "0deg", "0_deg", "0.0", "deg_0", "horizontal"]:
            payload_x = {
                "part": {"part_id": "test"},
                "base_body": {
                    "family": "rectangular_prism",
                    "dimensions_mm": {"length": 50, "width": 50, "thickness": 10},
                },
                "features": [
                    {
                        "family": "slot_through_cutout",
                        "orientation": {"axis": x_alias},
                        "dimensions_mm": {"length": 20, "width": 5},
                    }
                ],
            }
            plan_x = feature_plan_from_dict(payload_x)
            assert plan_x.features[0].orientation_axis == "x"
            assert not any(d.code == "UNKNOWN_SLOT_ORIENTATION" for d in plan_x.validation_diagnostics)

    def test_selector_only_face_object_emits_no_false_alias_warning(self) -> None:
        payload = {
            "part": {"part_id": "test"},
            "base_body": {
                "family": "rectangular_prism",
                "dimensions_mm": {"length": 50, "width": 50, "thickness": 10},
            },
            "features": [
                {
                    "family": "circular_through_hole",
                    "target": {"face": {"selector": "top"}},
                    "dimensions_mm": {"diameter": 10.0},
                }
            ],
        }
        plan = feature_plan_from_dict(payload)
        feat = plan.features[0]
        assert feat.target_selector == "top"
        assert feat.target_face is None
        assert not any(d.code == "UNKNOWN_FACE_ALIAS" for d in plan.validation_diagnostics)

    def test_unknown_base_body_family_hard_fails(self) -> None:
        payload = {
            "part": {"part_id": "test"},
            "base_body": {"family": "unknown_shape", "dimensions_mm": {"length": 10, "width": 10, "thickness": 5}},
            "features": [],
        }
        with pytest.raises(FeaturePlanValidationError) as exc:
            feature_plan_from_dict(payload)
        assert exc.value.code == "UNKNOWN_BASE_BODY_FAMILY"

    def test_default_applied_to_dict_includes_original_value(self) -> None:
        payload = {
            "part": {"part_id": "test"},
            "base_body": {
                "family": "rectangular_prism",
                "dimensions_mm": {"length": 50, "width": 50, "thickness": 10},
            },
            "features": [
                {
                    "family": "slot_through_cutout",
                    "placement": {"mode": "polar_offset"},
                    "dimensions_mm": {"length": 20, "width": 5},
                }
            ],
        }
        plan = feature_plan_from_dict(payload)
        assert any(d.original_value == "polar_offset" for d in plan.defaults_applied)
        defaults_dicts = [d.to_dict() for d in plan.defaults_applied]
        found = next(d for d in defaults_dicts if d.get("original_value") == "polar_offset")
        assert found["path"] == "features[0].placement.mode"
        assert found["value"] == "face_local_center"

    @pytest.mark.parametrize(
        "raw_op, expected_op",
        [
            ("cut", "subtract"),
            ("difference", "subtract"),
            ("subtract", "subtract"),
            ("union", "union"),
            ("add", "union"),
            ("intersect", "intersect"),
            ("common", "intersect"),
        ],
    )
    def test_clean_boolean_operation_aliases(self, raw_op: str, expected_op: str) -> None:
        payload = {
            "part": {"part_id": "test"},
            "base_body": {"id": "body.1", "family": "cylinder", "dimensions_mm": {"radius": 10, "height": 20}},
            "primitive_bodies": [
                {"id": "body.1", "family": "cylinder", "dimensions_mm": {"radius": 10, "height": 20}},
                {"id": "body.2", "family": "sphere", "dimensions_mm": {"radius": 5}},
            ],
            "boolean_operations": [
                {"id": "b1", "operation": raw_op, "target_body_id": "body.1", "tool_body_id": "body.2"}
            ],
            "features": [],
        }
        plan = feature_plan_from_dict(payload)
        assert plan.boolean_operations[0].operation == expected_op
        assert not any(d.code == "UNKNOWN_BOOLEAN_OPERATION" for d in plan.validation_diagnostics)
        assert not any(d.original_value == raw_op for d in plan.defaults_applied)

    @pytest.mark.parametrize(
        "raw_face, expected_face",
        [
            ("top", "+Z"),
            ("up", "+Z"),
            ("+z", "+Z"),
            ("bottom", "-Z"),
            ("down", "-Z"),
            ("-z", "-Z"),
            ("right", "+X"),
            ("left", "-X"),
            ("back", "+Y"),
            ("front", "-Y"),
        ],
    )
    def test_clean_face_aliases(self, raw_face: str, expected_face: str) -> None:
        payload = {
            "part": {"part_id": "test"},
            "base_body": {
                "family": "rectangular_prism",
                "dimensions_mm": {"length": 50, "width": 50, "thickness": 10},
            },
            "features": [
                {
                    "family": "circular_through_hole",
                    "target": {"face": {"resolved_face": raw_face}},
                    "dimensions_mm": {"diameter": 10.0},
                }
            ],
        }
        plan = feature_plan_from_dict(payload)
        assert plan.features[0].target_face == expected_face
        assert not any(d.code == "UNKNOWN_FACE_ALIAS" for d in plan.validation_diagnostics)
        assert not any(d.original_value == raw_face for d in plan.defaults_applied)
