"""Composition layer validation and B-Rep boolean lifecycle state machine."""

from __future__ import annotations

from geometry.gate_policy import GatePolicyMode
from geometry.plan_models import (
    BooleanOperation,
    FeaturePlan,
    FeaturePlanBaseBody,
    ValidationDiagnostic,
)
from geometry.plan_parser import MAX_BOOLEAN_OPERATIONS, MAX_PRIMITIVE_BODIES
from geometry.validators.base_body import _validate_base_body
from geometry.validators.common import _reject, _require_finite


def _validate_composition_layer(
    plan: FeaturePlan,
    *,
    diagnostics: list[ValidationDiagnostic] | None = None,
    mode: GatePolicyMode | None = None,
) -> tuple[tuple[FeaturePlanBaseBody, ...], tuple[BooleanOperation, ...]]:
    """Validate multi-primitive body declarations and enforce B-Rep boolean lifecycle rules."""
    primitive_bodies = plan.primitive_bodies or (plan.base_body,)
    boolean_operations = plan.boolean_operations

    if len(primitive_bodies) > MAX_PRIMITIVE_BODIES:
        _reject(
            "EXCESSIVE_PRIMITIVE_BODY_COUNT",
            f"Primitive body count {len(primitive_bodies)} exceeds limit of {MAX_PRIMITIVE_BODIES}.",
            path="primitive_bodies",
        )

    if len(boolean_operations) > MAX_BOOLEAN_OPERATIONS:
        _reject(
            "EXCESSIVE_BOOLEAN_OPERATION_COUNT",
            f"Boolean operation count {len(boolean_operations)} exceeds limit of {MAX_BOOLEAN_OPERATIONS}.",
            path="boolean_operations",
        )

    body_ids: set[str] = set()
    for index, body in enumerate(primitive_bodies):
        if body.id in body_ids:
            _reject(
                "DUPLICATE_BODY_ID",
                f"Primitive body id '{body.id}' is duplicated.",
                path=f"primitive_bodies[{index}].id",
            )
        body_ids.add(body.id)
        _validate_base_body(body, diagnostics=diagnostics, mode=mode)
        _require_finite(body.placement.x_mm, f"primitive_bodies[{index}].placement.x_mm")
        _require_finite(body.placement.y_mm, f"primitive_bodies[{index}].placement.y_mm")
        _require_finite(body.placement.z_mm, f"primitive_bodies[{index}].placement.z_mm")

    if plan.base_body.id not in body_ids:
        _reject(
            "MISSING_BASE_BODY_IN_COMPOSITION",
            "base_body must also appear in primitive_bodies when a composition layer is supplied.",
            path="primitive_bodies",
        )

    active_body_ids: set[str] = set(body_ids)
    consumed_body_ids: set[str] = set()
    operation_ids: set[str] = set()

    for index, operation in enumerate(boolean_operations):
        path = f"boolean_operations[{index}]"
        if operation.id in operation_ids:
            _reject(
                "DUPLICATE_BOOLEAN_OPERATION_ID",
                f"Boolean operation id '{operation.id}' is duplicated.",
                path=f"{path}.id",
            )
        operation_ids.add(operation.id)

        if operation.operation not in {"union", "subtract", "intersect"}:
            _reject(
                "UNSUPPORTED_BOOLEAN_OPERATION",
                f"Boolean operation '{operation.operation}' is not supported.",
                path=f"{path}.operation",
            )

        if operation.target_body_id == operation.tool_body_id:
            _reject(
                "BOOLEAN_SELF_TARGETING",
                f"Boolean operation '{operation.id}' cannot use the same body '{operation.target_body_id}' as both target and tool.",
                path=f"{path}.tool_body_id",
            )

        if operation.target_body_id in consumed_body_ids:
            _reject(
                "CONSUMED_BOOLEAN_TARGET_BODY",
                f"Boolean operation '{operation.id}' targets previously consumed body '{operation.target_body_id}'.",
                path=f"{path}.target_body_id",
            )

        if operation.target_body_id not in active_body_ids:
            _reject(
                "UNKNOWN_BOOLEAN_TARGET_BODY",
                f"Boolean operation '{operation.id}' targets unknown body '{operation.target_body_id}'.",
                path=f"{path}.target_body_id",
            )

        if operation.tool_body_id in consumed_body_ids:
            _reject(
                "CONSUMED_BOOLEAN_TOOL_BODY",
                f"Boolean operation '{operation.id}' uses previously consumed tool body '{operation.tool_body_id}'.",
                path=f"{path}.tool_body_id",
            )

        if operation.tool_body_id not in active_body_ids:
            _reject(
                "UNKNOWN_BOOLEAN_TOOL_BODY",
                f"Boolean operation '{operation.id}' uses unknown tool body '{operation.tool_body_id}'.",
                path=f"{path}.tool_body_id",
            )

        if operation.result_body_id in active_body_ids or operation.result_body_id in consumed_body_ids:
            _reject(
                "DUPLICATE_BOOLEAN_RESULT_BODY",
                f"Boolean operation '{operation.id}' result body '{operation.result_body_id}' already exists.",
                path=f"{path}.result_body_id",
            )

        # Update B-Rep lifecycle state
        active_body_ids.remove(operation.target_body_id)
        consumed_body_ids.add(operation.target_body_id)
        active_body_ids.remove(operation.tool_body_id)
        consumed_body_ids.add(operation.tool_body_id)
        active_body_ids.add(operation.result_body_id)

    return primitive_bodies, boolean_operations


__all__ = [
    "_validate_composition_layer",
]
