"""Gate policy helpers for CAD Copilot feature validation.

Provides thread-safe gate policy mode management and diagnostic builders
for capability-first, strict, and shadow validation modes.
"""

from __future__ import annotations

import os
from typing import Literal

GatePolicyMode = Literal["strict", "capability_first", "shadow"]

_GATE_MODE_ENV_VAR = "CAD_COPILOT_GATE_MODE"
_VALID_GATE_MODES: set[str] = {"strict", "capability_first", "shadow"}
_ALWAYS_HARD_CANONICAL_REJECTIONS = {"safety_or_policy", "unsupported_units"}


def current_gate_policy_mode(mode: GatePolicyMode | None = None) -> GatePolicyMode:
    """Return the active gate policy mode.

    If an explicit mode parameter is supplied, it takes precedence.
    Otherwise, the environment variable CAD_COPILOT_GATE_MODE is inspected,
    defaulting to 'capability_first'.
    """
    if mode is not None and mode in _VALID_GATE_MODES:
        return mode

    raw_mode = os.environ.get(_GATE_MODE_ENV_VAR, "capability_first").strip().lower()
    if raw_mode in _VALID_GATE_MODES:
        return raw_mode  # type: ignore[return-value]
    return "capability_first"


def should_retry_canonical_rejection(rejection_code: str | None, *, mode: GatePolicyMode | None = None) -> bool:
    """Return whether a rejected canonical decision can try the next candidate model."""
    if rejection_code is None:
        return True
    return rejection_code not in _ALWAYS_HARD_CANONICAL_REJECTIONS


def relaxed_gate_warning(
    *,
    gate_id: str,
    original_code: str,
    decision: str,
    mode: GatePolicyMode | None = None,
) -> dict[str, str]:
    """Build a standard diagnostic dictionary when a gate check is relaxed to a warning."""
    active_mode = current_gate_policy_mode(mode)
    return {
        "code": "GATE_POLICY_RELAXED",
        "message": f"{gate_id} produced {original_code}; policy mode {active_mode} chose {decision}.",
    }


def shadow_gate_warning(
    *,
    gate_id: str,
    original_code: str,
    shadow_decision: str,
    mode: GatePolicyMode | None = None,
) -> dict[str, str]:
    """Build a standard diagnostic dictionary for a gate observed in shadow mode."""
    active_mode = current_gate_policy_mode(mode)
    return {
        "code": "GATE_POLICY_SHADOW",
        "message": (
            f"{gate_id} produced {original_code}; policy mode {active_mode} observed "
            f"candidate decision {shadow_decision}."
        ),
    }


__all__ = [
    "GatePolicyMode",
    "current_gate_policy_mode",
    "relaxed_gate_warning",
    "shadow_gate_warning",
    "should_retry_canonical_rejection",
]
