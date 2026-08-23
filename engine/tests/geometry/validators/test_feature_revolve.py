"""Unit tests for revolved features, stepped shaft shoulders, and height monotonicity."""

from __future__ import annotations

import pytest

from geometry.plan_models import (
    DefaultApplied,
    FeaturePlanValidationError,
    ProfilePoint2D,
    RectangularBaseBody,
    RevolvedProfileFeature,
    RevolvedShaftBaseBody,
    ValidationDiagnostic,
)
from geometry.validators.feature_revolve import (
    _normalize_revolved_axis_span,
    _normalize_revolved_profile_from_dimensions,
    _normalize_revolved_shaft_profile,
    _validate_revolved_profile,
    _validate_revolved_shaft_profile,
)


class TestRevolvedAndShaftValidators:
    """Verify revolve geometry, stepped shaft shoulders, and monotonicity."""

    def test_revolved_profile_validation_and_axis_span(self) -> None:
        body = RectangularBaseBody(id="b1", length_mm=80.0, width_mm=40.0, thickness_mm=10.0)
        points = (ProfilePoint2D(5.0, 0.0), ProfilePoint2D(15.0, 0.0), ProfilePoint2D(10.0, 20.0))
        revolve = RevolvedProfileFeature(
            id="rev1",
            profile_points=points,
            axis_start=ProfilePoint2D(0.0, 0.0),
            axis_end=ProfilePoint2D(0.0, 1.0),
            angle_deg=360.0,
            target_face="+X",
        )
        _validate_revolved_profile(revolve, base_body=body, edge_margin_mm=0.0, path="features[0]")

        defaults: list[DefaultApplied] = []
        normalized = _normalize_revolved_axis_span(revolve, defaults=defaults, path="features[0]")
        assert normalized.axis_start.y_mm == 0.0
        assert normalized.axis_end.y_mm == 20.0
        assert len(defaults) == 1

    def test_normalize_revolved_profile_from_dimensions(self) -> None:
        body = RectangularBaseBody(id="b1", length_mm=80.0, width_mm=40.0, thickness_mm=10.0)
        revolve = RevolvedProfileFeature(
            id="rev1",
            profile_height_mm=4.0,
            radial_depth_mm=5.0,
            target_face="+X",
        )
        defaults: list[DefaultApplied] = []
        normalized = _normalize_revolved_profile_from_dimensions(revolve, base_body=body, defaults=defaults, path="features[0]")
        assert len(normalized.profile_points) == 4
        assert len(defaults) == 1

    def test_normalize_revolved_shaft_profile(self) -> None:
        points = (ProfilePoint2D(10.0, 0.0), ProfilePoint2D(-2.0, 10.0), ProfilePoint2D(5.0, 20.0))
        body = RevolvedShaftBaseBody(id="sh1", radius_mm=10.0, height_mm=20.0, profile_points=points)
        defaults: list[DefaultApplied] = []
        normalized_body = _normalize_revolved_shaft_profile(body, defaults=defaults, path="base_body")
        assert normalized_body.profile_points[1].x_mm == 0.0

    def test_stepped_shaft_accepts_radial_shoulder(self) -> None:
        points = (
            ProfilePoint2D(0.0, 0.0),
            ProfilePoint2D(10.0, 0.0),
            ProfilePoint2D(10.0, 20.0),
            ProfilePoint2D(5.0, 20.0),  # radial shoulder: same height 20.0, inward step
            ProfilePoint2D(5.0, 50.0),
            ProfilePoint2D(0.0, 50.0),
        )
        body = RevolvedShaftBaseBody(id="sh1", radius_mm=10.0, height_mm=50.0, profile_points=points)
        _validate_revolved_shaft_profile(body, path="base_body")

    def test_stepped_shaft_decreasing_height_modes(self) -> None:
        points = (
            ProfilePoint2D(0.0, 0.0),
            ProfilePoint2D(10.0, 0.0),
            ProfilePoint2D(10.0, 30.0),
            ProfilePoint2D(5.0, 20.0),  # decreasing height 30 -> 20
            ProfilePoint2D(0.0, 50.0),
        )
        body = RevolvedShaftBaseBody(id="sh1", radius_mm=10.0, height_mm=50.0, profile_points=points)

        # In strict mode, raises hard rejection
        with pytest.raises(FeaturePlanValidationError) as exc:
            _validate_revolved_shaft_profile(body, path="base_body", mode="strict")
        assert exc.value.code == "INVALID_GEOMETRY"

        # In capability_first mode with diagnostics, emits relaxed warning
        diagnostics: list[ValidationDiagnostic] = []
        _validate_revolved_shaft_profile(body, path="base_body", diagnostics=diagnostics, mode="capability_first")
        assert len(diagnostics) == 1
        assert diagnostics[0].severity == "warning"
        assert diagnostics[0].code == "GATE_POLICY_RELAXED"
        assert "INVALID_GEOMETRY" in diagnostics[0].message

    def test_stepped_shaft_rejects_duplicate_points(self) -> None:
        points = (
            ProfilePoint2D(0.0, 0.0),
            ProfilePoint2D(10.0, 0.0),
            ProfilePoint2D(10.0, 20.0),
            ProfilePoint2D(10.0, 20.0),
            ProfilePoint2D(0.0, 50.0),
        )
        body = RevolvedShaftBaseBody(id="sh1", radius_mm=10.0, height_mm=50.0, profile_points=points)
        with pytest.raises(FeaturePlanValidationError) as exc:
            _validate_revolved_shaft_profile(body, path="base_body")
        assert exc.value.code == "INVALID_GEOMETRY"
