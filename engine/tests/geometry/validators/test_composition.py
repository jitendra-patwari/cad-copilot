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

    @pytest.mark.parametrize(
        "reserved_id", ["sketch.1", "profile.custom", "patch.extrude", "refplane.xy", "feature.gear-bore.1"]
    )
    def test_composition_rejects_reserved_prefixes(self, reserved_id: str) -> None:
        plan = FeaturePlan(
            request_id="req.res",
            part=PartMetadata(part_id="res_prefix"),
            base_body=RectangularBaseBody(id=reserved_id, length_mm=80.0, width_mm=40.0, thickness_mm=6.0),
        )
        with pytest.raises(FeaturePlanValidationError) as exc:
            _validate_composition_layer(plan)
        assert exc.value.code == "RESERVED_IDENTIFIER_PREFIX"

    def test_composition_rejects_operation_id_colliding_with_body_id(self) -> None:
        plan = FeaturePlan(
            request_id="req.dup_op",
            part=PartMetadata(part_id="dup_op"),
            base_body=RectangularBaseBody(id="body.base", length_mm=80.0, width_mm=40.0, thickness_mm=6.0),
            primitive_bodies=(
                RectangularBaseBody(id="body.base", length_mm=80.0, width_mm=40.0, thickness_mm=6.0),
                RectangularBaseBody(id="body.tool", length_mm=20.0, width_mm=20.0, thickness_mm=10.0),
            ),
            boolean_operations=(
                BooleanOperation(
                    id="body.base",  # Collision with body.base
                    operation="union",
                    target_body_id="body.base",
                    tool_body_id="body.tool",
                    result_body_id="body.res",
                ),
            ),
        )
        with pytest.raises(FeaturePlanValidationError) as exc:
            _validate_composition_layer(plan)
        assert exc.value.code == "DUPLICATE_BOOLEAN_OPERATION_ID"

    def test_composition_rejects_operation_id_equals_its_result_body_id(self) -> None:
        plan = FeaturePlan(
            request_id="req.dup_self",
            part=PartMetadata(part_id="dup_self"),
            base_body=RectangularBaseBody(id="body.base", length_mm=80.0, width_mm=40.0, thickness_mm=6.0),
            primitive_bodies=(
                RectangularBaseBody(id="body.base", length_mm=80.0, width_mm=40.0, thickness_mm=6.0),
                RectangularBaseBody(id="body.tool", length_mm=20.0, width_mm=20.0, thickness_mm=10.0),
            ),
            boolean_operations=(
                BooleanOperation(
                    id="collision",
                    operation="union",
                    target_body_id="body.base",
                    tool_body_id="body.tool",
                    result_body_id="collision",
                ),
            ),
        )
        with pytest.raises(FeaturePlanValidationError) as exc:
            _validate_composition_layer(plan)
        assert exc.value.code == "DUPLICATE_BOOLEAN_RESULT_BODY"

    def test_composition_rejects_later_operation_id_colliding_with_earlier_result_body_id(self) -> None:
        plan = FeaturePlan(
            request_id="req.dup_later_op",
            part=PartMetadata(part_id="dup_later_op"),
            base_body=RectangularBaseBody(id="body.base", length_mm=80.0, width_mm=40.0, thickness_mm=6.0),
            primitive_bodies=(
                RectangularBaseBody(id="body.base", length_mm=80.0, width_mm=40.0, thickness_mm=6.0),
                RectangularBaseBody(id="body.tool1", length_mm=20.0, width_mm=20.0, thickness_mm=10.0),
                RectangularBaseBody(id="body.tool2", length_mm=20.0, width_mm=20.0, thickness_mm=10.0),
            ),
            boolean_operations=(
                BooleanOperation(
                    id="op.1",
                    operation="union",
                    target_body_id="body.base",
                    tool_body_id="body.tool1",
                    result_body_id="body.res1",
                ),
                BooleanOperation(
                    id="body.res1",  # Collision with op.1 result_body_id
                    operation="union",
                    target_body_id="body.res1",
                    tool_body_id="body.tool2",
                    result_body_id="body.res2",
                ),
            ),
        )
        with pytest.raises(FeaturePlanValidationError) as exc:
            _validate_composition_layer(plan)
        assert exc.value.code == "DUPLICATE_BOOLEAN_OPERATION_ID"

    def test_composition_rejects_later_result_body_id_colliding_with_earlier_operation_id(self) -> None:
        plan = FeaturePlan(
            request_id="req.dup_later_res",
            part=PartMetadata(part_id="dup_later_res"),
            base_body=RectangularBaseBody(id="body.base", length_mm=80.0, width_mm=40.0, thickness_mm=6.0),
            primitive_bodies=(
                RectangularBaseBody(id="body.base", length_mm=80.0, width_mm=40.0, thickness_mm=6.0),
                RectangularBaseBody(id="body.tool1", length_mm=20.0, width_mm=20.0, thickness_mm=10.0),
                RectangularBaseBody(id="body.tool2", length_mm=20.0, width_mm=20.0, thickness_mm=10.0),
            ),
            boolean_operations=(
                BooleanOperation(
                    id="op.1",
                    operation="union",
                    target_body_id="body.base",
                    tool_body_id="body.tool1",
                    result_body_id="body.res1",
                ),
                BooleanOperation(
                    id="op.2",
                    operation="union",
                    target_body_id="body.res1",
                    tool_body_id="body.tool2",
                    result_body_id="op.1",  # Collision with earlier operation id
                ),
            ),
        )
        with pytest.raises(FeaturePlanValidationError) as exc:
            _validate_composition_layer(plan)
        assert exc.value.code == "DUPLICATE_BOOLEAN_RESULT_BODY"
