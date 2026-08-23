"""Feature extent normalization and validation for subtractive features."""

from __future__ import annotations

from dataclasses import replace
from typing import TypeVar

from geometry.gate_policy import GatePolicyMode
from geometry.plan_geometry import SubtractiveProfileFeature, _feature_through_depth_mm
from geometry.plan_models import (
    CylinderBaseBody,
    DefaultApplied,
    FeaturePlanBaseBody,
    RectangularBaseBody,
    SphereBaseBody,
    ValidationDiagnostic,
)
from geometry.validators.common import (
    _reject,
    _require_positive,
    _warn_allow_or_reject_geometry_fit,
)

T = TypeVar("T", bound=SubtractiveProfileFeature)


def _normalize_subtractive_extent(
    feature: T,
    *,
    base_body: FeaturePlanBaseBody,
    defaults: list[DefaultApplied],
    path: str,
    diagnostics: list[ValidationDiagnostic] | None = None,
    mode: GatePolicyMode | None = None,
) -> T:
    """Normalize and validate the cut extent type and depth of a subtractive feature."""
    extent_type = feature.extent_type.strip().lower()
    normalized_extent = {
        "through": "through_all",
        "throughall": "through_all",
        "through_all": "through_all",
        "blind": "blind",
        "finite": "blind",
        "finite_depth": "blind",
        "partial_depth": "blind",
        "partial": "blind",
    }.get(extent_type)

    if normalized_extent is None:
        _reject(
            "UNSUPPORTED_CUT_EXTENT",
            "Subtractive profile features currently support through_all or blind finite-depth extents.",
            path=f"{path}.extent.type",
        )

    if normalized_extent != feature.extent_type:
        defaults.append(
            DefaultApplied(
                path=f"{path}.extent.type",
                value=normalized_extent,
                reason="canonical_subtractive_extent_alias",
            )
        )

    if normalized_extent == "through_all":
        if feature.depth_mm != 0.0:
            defaults.append(
                DefaultApplied(
                    path=f"{path}.extent.depth_mm",
                    value=0.0,
                    reason="through_all_extent_ignores_finite_depth",
                )
            )
        return replace(feature, extent_type="through_all", depth_mm=0.0)

    if not isinstance(base_body, RectangularBaseBody | CylinderBaseBody | SphereBaseBody):
        _reject(
            "UNSUPPORTED_FEATURE_ON_BASE_BODY",
            "Blind finite-depth cuts currently support rectangular-prism, cylinder, and sphere base bodies only.",
            path=path,
        )

    if feature.target_face != "+Z":
        _reject(
            "UNSUPPORTED_TARGET_FACE",
            "Blind finite-depth cuts currently support the +Z/default axial face only.",
            path=path,
        )

    _require_positive(feature.depth_mm, f"{path}.extent.depth_mm")
    through_depth_mm = _feature_through_depth_mm(base_body, feature)
    if feature.depth_mm >= through_depth_mm:
        msg = "Blind finite-depth cut depth is greater than or equal to the target body thickness."
        if diagnostics is not None:
            _warn_allow_or_reject_geometry_fit(
                "INVALID_CUT_DEPTH",
                msg,
                diagnostics=diagnostics,
                path=f"{path}.extent.depth_mm",
                mode=mode,
            )
        else:
            _reject("INVALID_CUT_DEPTH", msg, path=f"{path}.extent.depth_mm")

    return replace(feature, extent_type="blind")


__all__ = [
    "_normalize_subtractive_extent",
]
