"""Base body validation and face area resolution for canonical feature plans."""

from __future__ import annotations

from geometry.gate_policy import GatePolicyMode
from geometry.plan_models import (
    CylinderBaseBody,
    FeaturePlanBaseBody,
    RectangularBaseBody,
    RevolvedShaftBaseBody,
    SphereBaseBody,
    SpurGearBaseBody,
    ValidationDiagnostic,
)
from geometry.plan_parser import MAX_PROFILE_POINTS
from geometry.validators.common import (
    _reject,
    _require_finite,
    _require_positive,
    _validate_polygon_profile_sanity,
)


def _validate_base_body(
    base_body: FeaturePlanBaseBody,
    *,
    diagnostics: list[ValidationDiagnostic] | None = None,
    mode: GatePolicyMode | None = None,
) -> None:
    """Validate dimensional requirements and geometric validity of a base body primitive."""
    if isinstance(base_body, RectangularBaseBody):
        if base_body.family != "rectangular_prism":
            _reject(
                "UNSUPPORTED_BASE_BODY",
                "Canonical feature-plan contract only supports rectangular_prism, cylinder, and sphere base bodies.",
                path="base_body.family",
            )
        _require_positive(base_body.length_mm, "base_body.dimensions_mm.length")
        _require_positive(base_body.width_mm, "base_body.dimensions_mm.width")
        _require_positive(base_body.thickness_mm, "base_body.dimensions_mm.thickness")
        return

    if isinstance(base_body, CylinderBaseBody):
        _require_positive(base_body.radius_mm, "base_body.dimensions_mm.radius")
        _require_positive(base_body.height_mm, "base_body.dimensions_mm.height")
        return

    if isinstance(base_body, SphereBaseBody):
        _require_positive(base_body.radius_mm, "base_body.dimensions_mm.radius")
        return

    if isinstance(base_body, SpurGearBaseBody):
        if base_body.tooth_count < 8 or base_body.tooth_count > 120:
            _reject(
                "INVALID_GEAR_TOOTH_COUNT",
                "Concept spur gears currently support tooth_count between 8 and 120.",
                path="base_body.dimensions_mm.tooth_count",
            )
        _require_positive(base_body.module_mm, "base_body.dimensions_mm.module")
        _require_positive(base_body.face_width_mm, "base_body.dimensions_mm.face_width")
        _require_positive(base_body.pressure_angle_deg, "base_body.dimensions_mm.pressure_angle")
        if base_body.pressure_angle_deg >= 45.0:
            _reject(
                "INVALID_GEAR_PRESSURE_ANGLE",
                "Concept spur gear pressure_angle must be greater than 0 and less than 45 degrees.",
                path="base_body.dimensions_mm.pressure_angle",
            )
        _require_finite(base_body.bore_diameter_mm, "base_body.dimensions_mm.bore_diameter")
        if base_body.bore_diameter_mm < 0.0:
            _reject(
                "INVALID_DIMENSION",
                "base_body.dimensions_mm.bore_diameter must be non-negative.",
                path="base_body.dimensions_mm.bore_diameter",
            )
        root_diameter_mm = max(
            base_body.module_mm * 0.7,
            (base_body.tooth_count * base_body.module_mm) - (2.5 * base_body.module_mm),
        )
        if base_body.bore_diameter_mm >= root_diameter_mm:
            _reject(
                "GEAR_BORE_DOES_NOT_FIT",
                "Concept spur gear bore diameter must be smaller than the root diameter.",
                path="base_body.dimensions_mm.bore_diameter",
            )
        return

    if isinstance(base_body, RevolvedShaftBaseBody):
        _require_positive(base_body.radius_mm, "base_body.dimensions_mm.radius")
        _require_positive(base_body.height_mm, "base_body.dimensions_mm.height")
        _validate_polygon_profile_sanity(
            base_body.profile_points,
            path="base_body.dimensions_mm.profile_points",
            max_points=MAX_PROFILE_POINTS,
        )
        return

    _reject(
        "UNSUPPORTED_BASE_BODY",
        "Canonical feature-plan contract only supports rectangular_prism, cylinder, sphere, spur_gear, and revolved_shaft base bodies.",
        path="base_body.family",
    )


__all__ = [
    "_validate_base_body",
]
