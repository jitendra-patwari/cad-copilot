"""Unit tests for base-body dimensional validation and face area selection."""

from __future__ import annotations

import pytest

from geometry.plan_models import (
    CylinderBaseBody,
    FeaturePlanValidationError,
    ProfilePoint2D,
    RectangularBaseBody,
    RevolvedShaftBaseBody,
    SphereBaseBody,
    SpurGearBaseBody,
)
from geometry.validators.base_body import _validate_base_body
from geometry.validators.target_resolution import _rectangular_prism_face_by_area


class TestBaseBodyValidators:
    """Verify base-body dimensional validation and area selection."""

    def test_valid_rectangular_base_body(self) -> None:
        body = RectangularBaseBody(id="b1", length_mm=80.0, width_mm=40.0, thickness_mm=6.0)
        _validate_base_body(body)

    def test_rectangular_base_body_rejects_non_positive_dimensions(self) -> None:
        with pytest.raises(FeaturePlanValidationError) as exc:
            _validate_base_body(RectangularBaseBody(id="b1", length_mm=0.0, width_mm=40.0, thickness_mm=6.0))
        assert exc.value.code == "INVALID_DIMENSION"

        with pytest.raises(FeaturePlanValidationError) as exc:
            _validate_base_body(RectangularBaseBody(id="b1", length_mm=-50.0, width_mm=40.0, thickness_mm=6.0))
        assert exc.value.code == "INVALID_DIMENSION"

        with pytest.raises(FeaturePlanValidationError) as exc:
            _validate_base_body(RectangularBaseBody(id="b1", length_mm=80.0, width_mm=-5.0, thickness_mm=6.0))
        assert exc.value.code == "INVALID_DIMENSION"

    def test_valid_cylinder_and_sphere_base_bodies(self) -> None:
        _validate_base_body(CylinderBaseBody(id="c1", radius_mm=15.0, height_mm=50.0))
        _validate_base_body(SphereBaseBody(id="s1", radius_mm=20.0))

    def test_spur_gear_tooth_count_and_angle_validation(self) -> None:
        with pytest.raises(FeaturePlanValidationError) as exc:
            _validate_base_body(SpurGearBaseBody(id="g1", tooth_count=6, module_mm=2.0, face_width_mm=10.0))
        assert exc.value.code == "INVALID_GEAR_TOOTH_COUNT"

        with pytest.raises(FeaturePlanValidationError) as exc:
            _validate_base_body(SpurGearBaseBody(id="g1", tooth_count=130, module_mm=2.0, face_width_mm=10.0))
        assert exc.value.code == "INVALID_GEAR_TOOTH_COUNT"

        with pytest.raises(FeaturePlanValidationError) as exc:
            _validate_base_body(
                SpurGearBaseBody(id="g1", tooth_count=20, module_mm=2.0, face_width_mm=10.0, pressure_angle_deg=50.0)
            )
        assert exc.value.code == "INVALID_GEAR_PRESSURE_ANGLE"

    def test_spur_gear_bore_diameter_validation(self) -> None:
        valid_gear = SpurGearBaseBody(
            id="g1",
            tooth_count=20,
            module_mm=2.0,
            face_width_mm=10.0,
            pressure_angle_deg=20.0,
            bore_diameter_mm=10.0,
        )
        _validate_base_body(valid_gear)

        with pytest.raises(FeaturePlanValidationError) as exc:
            _validate_base_body(
                SpurGearBaseBody(
                    id="g1",
                    tooth_count=20,
                    module_mm=2.0,
                    face_width_mm=10.0,
                    bore_diameter_mm=50.0,  # larger than root diameter
                )
            )
        assert exc.value.code == "GEAR_BORE_DOES_NOT_FIT"

    def test_revolved_shaft_requires_at_least_three_points(self) -> None:
        with pytest.raises(FeaturePlanValidationError) as exc:
            _validate_base_body(RevolvedShaftBaseBody(id="sh1", radius_mm=10.0, height_mm=50.0, profile_points=()))
        assert exc.value.code == "INVALID_PROFILE_POINTS"

    def test_revolved_shaft_rejects_collinear_zero_area_profile(self) -> None:
        collinear_points = (
            ProfilePoint2D(0.0, 0.0),
            ProfilePoint2D(1.0, 1.0),
            ProfilePoint2D(2.0, 2.0),
        )
        body = RevolvedShaftBaseBody(id="sh1", radius_mm=10.0, height_mm=50.0, profile_points=collinear_points)
        with pytest.raises(FeaturePlanValidationError) as exc:
            _validate_base_body(body)
        assert exc.value.code == "INVALID_GEOMETRY"

    def test_rectangular_prism_face_by_area(self) -> None:
        body = RectangularBaseBody(id="b1", length_mm=100.0, width_mm=50.0, thickness_mm=10.0)
        assert _rectangular_prism_face_by_area(body, largest=True) == "+Z"
        assert _rectangular_prism_face_by_area(body, largest=False) == "+X"
