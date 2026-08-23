"""Unit tests for 2D planar profile feature fit, slot normalization, and separation."""

from __future__ import annotations

import pytest
from geometry.plan_models import (
    CircularThroughHoleFeature,
    CylinderBaseBody,
    FeaturePlanValidationError,
    ProfileCutoutFeature,
    ProfilePoint2D,
    RectangularBaseBody,
    RectangularExtrudedPadFeature,
    RectangularThroughCutoutFeature,
    SlotThroughCutoutFeature,
    SpurGearBaseBody,
    ValidationDiagnostic,
)
from geometry.validators.feature_profiles import (
    _normalize_slot_orientation,
    _validate_feature_supported_on_base_body,
    _validate_hole_fit,
    _validate_hole_separation,
    _validate_profile_cutout_fit,
    _validate_rectangular_cutout_fit,
    _validate_rectangular_pad_fit,
    _validate_slot_cutout_fit,
)


class TestPlanarProfileFitAndSeparation:
    """Verify 2D planar profile feature containment, orientation, and separation."""

    def test_feature_support_on_base_bodies(self) -> None:
        cyl = CylinderBaseBody(id="c1", radius_mm=20.0, height_mm=50.0)
        pad = RectangularExtrudedPadFeature(id="p1", width_mm=10.0, height_mm=10.0, distance_mm=5.0)

        # In strict mode, unsupported pad on cylinder raises hard error
        with pytest.raises(FeaturePlanValidationError) as exc:
            _validate_feature_supported_on_base_body(pad, cyl, path="features[0]", mode="strict")
        assert exc.value.code == "UNSUPPORTED_FEATURE_ON_BASE_BODY"

        # In capability_first mode with diagnostics, emits relaxed warning
        diagnostics: list[ValidationDiagnostic] = []
        _validate_feature_supported_on_base_body(
            pad, cyl, path="features[0]", diagnostics=diagnostics, mode="capability_first"
        )
        assert len(diagnostics) == 1
        assert diagnostics[0].severity == "warning"
        assert diagnostics[0].code == "GATE_POLICY_RELAXED"

        # Spur gear base body test
        gear = SpurGearBaseBody(id="g1", tooth_count=20, module_mm=2.0, face_width_mm=10.0)
        diagnostics.clear()
        _validate_feature_supported_on_base_body(
            pad, gear, path="features[0]", diagnostics=diagnostics, mode="capability_first"
        )
        assert len(diagnostics) == 1
        assert "UNSUPPORTED_FEATURE_ON_BASE_BODY" in diagnostics[0].message

    def test_slot_orientation_normalization(self) -> None:
        assert _normalize_slot_orientation("u", path="features[0]") == "x"
        assert _normalize_slot_orientation("90deg", path="features[0]") == "y"

        # Non-standard orientation in strict mode raises hard error
        with pytest.raises(FeaturePlanValidationError) as exc:
            _normalize_slot_orientation("diagonal", path="features[0]", mode="strict")
        assert exc.value.code == "UNSUPPORTED_SLOT_ORIENTATION"

        # In capability_first mode with diagnostics, emits relaxed warning and defaults to 'x'
        diagnostics: list[ValidationDiagnostic] = []
        result = _normalize_slot_orientation(
            "diagonal", path="features[0]", diagnostics=diagnostics, mode="capability_first"
        )
        assert result == "x"
        assert len(diagnostics) == 1
        assert diagnostics[0].severity == "warning"
        assert diagnostics[0].code == "GATE_POLICY_RELAXED"

    def test_hole_fit_emits_relaxed_warning_on_boundary_overflow(self) -> None:
        body = RectangularBaseBody(id="b1", length_mm=80.0, width_mm=40.0, thickness_mm=6.0)
        hole = CircularThroughHoleFeature(id="h1", diameter_mm=8.0, center_x_mm=38.0, center_y_mm=0.0, target_face="+Z")

        # In capability_first mode, boundary overflow is a relaxed warning
        diagnostics: list[ValidationDiagnostic] = []
        _validate_hole_fit(hole, radius_mm=4.0, base_body=body, edge_margin_mm=0.0, diagnostics=diagnostics, path="features[0]")
        assert len(diagnostics) == 1
        assert diagnostics[0].severity == "warning"
        assert diagnostics[0].code == "GATE_POLICY_RELAXED"
        assert "HOLE_DOES_NOT_FIT" in diagnostics[0].message

    def test_rectangular_cutout_and_slot_fit(self) -> None:
        body = RectangularBaseBody(id="b1", length_mm=80.0, width_mm=40.0, thickness_mm=6.0)
        cutout = RectangularThroughCutoutFeature(
            id="cut1", width_mm=20.0, height_mm=10.0, center_x_mm=0.0, center_y_mm=0.0, target_face="+Z"
        )
        diagnostics: list[ValidationDiagnostic] = []
        _validate_rectangular_cutout_fit(cutout, base_body=body, edge_margin_mm=0.0, diagnostics=diagnostics, path="features[0]")
        assert len(diagnostics) == 0

        slot = SlotThroughCutoutFeature(
            id="slot1", length_mm=30.0, width_mm=10.0, center_x_mm=30.0, center_y_mm=0.0, target_face="+Z"
        )
        diagnostics.clear()
        _validate_slot_cutout_fit(slot, base_body=body, edge_margin_mm=0.0, diagnostics=diagnostics, path="features[0]")
        assert len(diagnostics) == 1

    def test_profile_cutout_and_pad_fit(self) -> None:
        body = RectangularBaseBody(id="b1", length_mm=80.0, width_mm=40.0, thickness_mm=6.0)
        points = (ProfilePoint2D(-10, -5), ProfilePoint2D(10, -5), ProfilePoint2D(0, 10))
        profile_cutout = ProfileCutoutFeature(id="pcut1", profile_points=points, target_face="+Z")
        diagnostics: list[ValidationDiagnostic] = []
        _validate_profile_cutout_fit(profile_cutout, base_body=body, edge_margin_mm=0.0, diagnostics=diagnostics, path="features[0]")
        assert len(diagnostics) == 0

        pad = RectangularExtrudedPadFeature(id="pad1", width_mm=20.0, height_mm=4.0, distance_mm=5.0, target_face="+X")
        diagnostics.clear()
        _validate_rectangular_pad_fit(pad, base_body=body, edge_margin_mm=0.0, diagnostics=diagnostics, path="features[0]")
        assert len(diagnostics) == 0

    def test_hole_separation_detects_overlapping_holes(self) -> None:
        diagnostics: list[ValidationDiagnostic] = []
        hole_specs = [
            ("h1", "body.main", "+Z", 0.0, 0.0, 5.0),
            ("h2", "body.main", "+Z", 6.0, 0.0, 5.0),
        ]
        _validate_hole_separation(hole_specs, edge_margin_mm=0.0, diagnostics=diagnostics)
        assert len(diagnostics) == 1
        assert "HOLES_OVERLAP" in diagnostics[0].message

    def test_planar_profile_rejects_nan_center_coordinates(self) -> None:
        body = RectangularBaseBody(id="b1", length_mm=80.0, width_mm=40.0, thickness_mm=6.0)
        hole = CircularThroughHoleFeature(id="h_nan", diameter_mm=8.0, center_x_mm=float("nan"), center_y_mm=0.0)
        diagnostics: list[ValidationDiagnostic] = []
        with pytest.raises(FeaturePlanValidationError) as exc:
            _validate_hole_fit(hole, radius_mm=4.0, base_body=body, edge_margin_mm=0.0, diagnostics=diagnostics, path="features[0]")
        assert exc.value.code == "INVALID_DIMENSION"
