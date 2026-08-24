"""Unit tests for composition layer and B-Rep boolean lifecycle state machine."""

from __future__ import annotations

import pytest

from geometry.plan_models import (
    BooleanOperation,
    FeaturePlan,
    FeaturePlanValidationError,
    PartMetadata,
    RectangularBaseBody,
)
from geometry.validators.composition import _validate_composition_layer


class TestCompositionLayerAndBooleanLifecycle:
    """Verify multi-primitive body composition and B-Rep boolean lifecycle state tracking."""

    def test_valid_multi_body_composition(self) -> None:
        plan = FeaturePlan(
            request_id="req.1",
            part=PartMetadata(part_id="bracket"),
            base_body=RectangularBaseBody(id="body.base", length_mm=80.0, width_mm=40.0, thickness_mm=6.0),
            primitive_bodies=(
                RectangularBaseBody(id="body.base", length_mm=80.0, width_mm=40.0, thickness_mm=6.0),
                RectangularBaseBody(id="body.boss", length_mm=20.0, width_mm=20.0, thickness_mm=10.0),
            ),
            boolean_operations=(
                BooleanOperation(
                    id="bool.1",
                    operation="union",
                    target_body_id="body.base",
                    tool_body_id="body.boss",
                    result_body_id="body.final",
                ),
            ),
        )
        bodies, ops = _validate_composition_layer(plan)
        assert len(bodies) == 2
        assert len(ops) == 1

    def test_composition_rejects_duplicate_body_ids(self) -> None:
        plan = FeaturePlan(
            request_id="req.2",
            part=PartMetadata(part_id="dup"),
            base_body=RectangularBaseBody(id="body.base", length_mm=80.0, width_mm=40.0, thickness_mm=6.0),
            primitive_bodies=(
                RectangularBaseBody(id="body.base", length_mm=80.0, width_mm=40.0, thickness_mm=6.0),
                RectangularBaseBody(id="body.base", length_mm=20.0, width_mm=20.0, thickness_mm=10.0),
            ),
        )
        with pytest.raises(FeaturePlanValidationError) as exc:
            _validate_composition_layer(plan)
        assert exc.value.code == "DUPLICATE_BODY_ID"

    def test_boolean_lifecycle_rejects_self_targeting(self) -> None:
        plan = FeaturePlan(
            request_id="req.3",
            part=PartMetadata(part_id="self_target"),
            base_body=RectangularBaseBody(id="body.base", length_mm=80.0, width_mm=40.0, thickness_mm=6.0),
            primitive_bodies=(RectangularBaseBody(id="body.base", length_mm=80.0, width_mm=40.0, thickness_mm=6.0),),
            boolean_operations=(
                BooleanOperation(
                    id="bool.1",
                    operation="union",
                    target_body_id="body.base",
                    tool_body_id="body.base",
                    result_body_id="body.res",
                ),
            ),
        )
        with pytest.raises(FeaturePlanValidationError) as exc:
            _validate_composition_layer(plan)
        assert exc.value.code == "BOOLEAN_SELF_TARGETING"

    def test_boolean_lifecycle_rejects_reusing_consumed_tool_body(self) -> None:
        plan = FeaturePlan(
            request_id="req.4",
            part=PartMetadata(part_id="consumed"),
            base_body=RectangularBaseBody(id="body.base", length_mm=80.0, width_mm=40.0, thickness_mm=6.0),
            primitive_bodies=(
                RectangularBaseBody(id="body.base", length_mm=80.0, width_mm=40.0, thickness_mm=6.0),
                RectangularBaseBody(id="body.tool", length_mm=20.0, width_mm=20.0, thickness_mm=10.0),
            ),
            boolean_operations=(
                BooleanOperation(
                    id="bool.1",
                    operation="union",
                    target_body_id="body.base",
                    tool_body_id="body.tool",
                    result_body_id="body.res1",
                ),
                BooleanOperation(
                    id="bool.2",
                    operation="union",
                    target_body_id="body.res1",
                    tool_body_id="body.tool",
                    result_body_id="body.res2",
                ),
            ),
        )
        with pytest.raises(FeaturePlanValidationError) as exc:
            _validate_composition_layer(plan)
        assert exc.value.code == "CONSUMED_BOOLEAN_TOOL_BODY"
