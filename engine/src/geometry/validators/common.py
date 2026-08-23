"""Common helper functions and error handlers for geometric validation.

Product Policy - Geometry Fit & Bounds Checks:
    Face-fit, bounding-box, and margin checks are treated as soft warnings in capability-first mode.
    Physical invariants (strictly positive finite dimensions, valid B-Rep booleans) are hard rejections.
"""

from __future__ import annotations

import math
from typing import NoReturn

from geometry.gate_policy import GatePolicyMode, current_gate_policy_mode, relaxed_gate_warning
from geometry.plan_models import (
    DEFAULT_EDGE_MARGIN_MM,
    FeaturePlanValidationError,
    ValidationDiagnostic,
)


def _effective_edge_margin_mm(edge_margin_mm: float, *, mode: GatePolicyMode | None = None) -> float:
    """Return the effective edge margin based on the active gate policy mode."""
    if edge_margin_mm == DEFAULT_EDGE_MARGIN_MM and current_gate_policy_mode(mode) == "capability_first":
        return 0.0
    return edge_margin_mm


def _require_positive(value: float, path: str) -> None:
    """Validate that a numerical dimension is finite and strictly positive (> 0)."""
    _require_finite(value, path)
    if value <= 0.0:
        _reject("INVALID_DIMENSION", f"{path} must be positive.", path=path)


def _require_finite(value: float, path: str) -> None:
    """Validate that a numerical value is finite (not NaN or Inf)."""
    if not math.isfinite(value):
        _reject("INVALID_DIMENSION", f"{path} must be finite.", path=path)


def _reject(code: str, message: str, *, path: str | None = None) -> NoReturn:
    """Raise a structured FeaturePlanValidationError with diagnostic metadata."""
    raise FeaturePlanValidationError(
        ValidationDiagnostic(
            severity="error",
            code=code,
            message=message,
            path=path,
        )
    )


def _warn_allow_or_reject_geometry_fit(
    code: str,
    message: str,
    *,
    diagnostics: list[ValidationDiagnostic],
    path: str | None,
    mode: GatePolicyMode | None = None,
) -> None:
    """Emit a relaxed warning diagnostic or raise a hard rejection based on gate mode."""
    active_mode = current_gate_policy_mode(mode)
    if active_mode == "strict":
        _reject(code, message, path=path)

    warning = relaxed_gate_warning(
        gate_id="feature_plan.geometry_fit",
        original_code=code,
        decision="warn_allow",
        mode=mode,
    )
    diagnostics.append(
        ValidationDiagnostic(
            severity="warning",
            code=warning["code"],
            message=f"{warning['message']} {message}",
            path=path,
        )
    )


def _validate_circular_cross_section_profile_fit(
    *,
    feature_id: str,
    center_x_mm: float,
    center_y_mm: float,
    farthest_profile_radius_mm: float,
    base_radius_mm: float,
    edge_margin_mm: float,
    failure_code: str,
    diagnostics: list[ValidationDiagnostic],
    path: str,
    mode: GatePolicyMode | None = None,
) -> None:
    """Validate radial containment on circular cross-section bodies (cylinder, sphere)."""
    if math.hypot(center_x_mm, center_y_mm) + farthest_profile_radius_mm + edge_margin_mm > base_radius_mm:
        _warn_allow_or_reject_geometry_fit(
            failure_code,
            f"Feature '{feature_id}' does not fit within the primitive circular cross-section with edge margin.",
            diagnostics=diagnostics,
            path=path,
            mode=mode,
        )


__all__ = [
    "_effective_edge_margin_mm",
    "_reject",
    "_require_finite",
    "_require_positive",
    "_validate_circular_cross_section_profile_fit",
    "_warn_allow_or_reject_geometry_fit",
]
