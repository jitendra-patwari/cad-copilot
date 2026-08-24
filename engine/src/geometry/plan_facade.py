"""Clean public facade API for feature plan operations in CAD Copilot.

Provides unified entrypoints for AST validation and wire payload lowering.
"""

from __future__ import annotations

from typing import Any

from geometry.gate_policy import GatePolicyMode
from geometry.plan_lowering import (
    lower_feature_plan_to_payload,
    lower_validated_feature_plan_to_payload,
)
from geometry.plan_parser import feature_plan_from_dict
from geometry.plan_validation import validate_feature_plan


def parse_and_lower_feature_plan(
    payload: dict[str, Any],
    *,
    mode: GatePolicyMode | None = None,
) -> dict[str, Any]:
    """Parse a dictionary payload into an AST, validate it, and lower it to wire IR."""
    ast = feature_plan_from_dict(payload)
    return lower_feature_plan_to_payload(ast, mode=mode)


__all__ = [
    "feature_plan_from_dict",
    "lower_feature_plan_to_payload",
    "lower_validated_feature_plan_to_payload",
    "parse_and_lower_feature_plan",
    "validate_feature_plan",
]
