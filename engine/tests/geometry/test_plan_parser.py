"""Unit and validation tests for AST plan deserialization and defensive bounds."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from geometry import (
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

        assert plan.plan_version.startswith("main_cad.") or plan.plan_version.startswith("cad_copilot.")
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
