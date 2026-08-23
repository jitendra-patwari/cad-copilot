"""Validation for swept protrusion features and 3D trajectory clearance."""

from __future__ import annotations

import math

from geometry.gate_policy import GatePolicyMode
from geometry.plan_models import (
    FeaturePlanBaseBody,
    SweptProtrusionFeature,
    ValidationDiagnostic,
)
from geometry.validators.common import _reject, _warn_allow_or_reject_geometry_fit


def _validate_swept_protrusion(
    feature: SweptProtrusionFeature,
    *,
    base_body: FeaturePlanBaseBody,
    edge_margin_mm: float,
    path: str,
    diagnostics: list[ValidationDiagnostic] | None = None,
    mode: GatePolicyMode | None = None,
) -> None:
    """Validate swept protrusion trajectory, cross-section profiles, and self-intersection clearance."""
    if feature.path.type in {"semicircle", "quarter_arc", "straight_line"}:
        _reject(
            "UNSUPPORTED_SWEEP_PATH",
            f"Sweep path type '{feature.path.type}' is not fully supported yet.",
            path=f"{path}.path.type",
        )

    if feature.path.radius_mm <= 0.0 and feature.path.type != "straight_line":
        _reject(
            "INVALID_SWEEP_PATH",
            "Sweep path radius must be positive for curved paths.",
            path=f"{path}.path.radius_mm",
        )

    if not feature.cross_sections:
        _reject(
            "INVALID_SWEEP_SECTIONS",
            "Swept protrusion must have at least one cross-section.",
            path=f"{path}.cross_sections",
        )

    for i, section in enumerate(feature.cross_sections):
        section_path = f"{path}.cross_sections[{i}]"
        section_radius_mm = 0.0

        if section.type == "circle":
            if section.diameter_mm <= 0.0:
                _reject(
                    "INVALID_SECTION_DIMENSIONS",
                    "Circle section diameter must be positive.",
                    path=f"{section_path}.diameter_mm",
                )
            section_radius_mm = section.diameter_mm / 2.0
        elif section.type == "rectangle":
            if section.width_mm <= 0.0 or section.height_mm <= 0.0:
                _reject(
                    "INVALID_SECTION_DIMENSIONS",
                    "Rectangle section width and height must be positive.",
                    path=section_path,
                )
            section_radius_mm = math.hypot(section.width_mm / 2.0, section.height_mm / 2.0)
        elif section.type == "polygon":
            if len(section.profile_points) < 3:
                _reject(
                    "INVALID_SECTION_DIMENSIONS",
                    "Polygon section requires at least 3 points.",
                    path=f"{section_path}.profile_points",
                )
            section_radius_mm = max(math.hypot(p.x_mm, p.y_mm) for p in section.profile_points)

        # Central-axis clearance invariant: r_section < R_path to avoid self-intersection
        if feature.path.type != "straight_line" and section_radius_mm >= feature.path.radius_mm:
            msg = (
                f"Cross-section radius ({section_radius_mm:.2f}) must be strictly less than path radius "
                f"({feature.path.radius_mm:.2f}) to prevent self-intersection."
            )
            if diagnostics is not None:
                _warn_allow_or_reject_geometry_fit(
                    "SWEEP_SELF_INTERSECTS",
                    msg,
                    diagnostics=diagnostics,
                    path=section_path,
                    mode=mode,
                )
            else:
                _reject("SWEEP_SELF_INTERSECTS", msg, path=section_path)


__all__ = [
    "_validate_swept_protrusion",
]
