"""Unit tests for swept protrusion trajectory, cross-sections, and corner clearance."""

from __future__ import annotations

import pytest
from geometry.plan_models import (
    FeaturePlanValidationError,
    RectangularBaseBody,
    SweepCrossSectionSpec,
    SweepPathSpec,
    SweptProtrusionFeature,
    ValidationDiagnostic,
)
from geometry.validators.feature_sweep import _validate_swept_protrusion


class TestSweptProtrusionValidators:
    """Verify swept protrusion path and central-axis curvature clearance."""

    def test_swept_protrusion_accepts_valid_geometry(self) -> None:
        sweep = SweptProtrusionFeature(
            id="sw1",
            path=SweepPathSpec(type="full_circle", radius_mm=50.0),
            cross_sections=(SweepCrossSectionSpec(type="circle", diameter_mm=20.0),),
        )
        body = RectangularBaseBody(id="b1", length_mm=100.0, width_mm=100.0, thickness_mm=20.0)
        _validate_swept_protrusion(sweep, base_body=body, edge_margin_mm=0.0, path="features[0]")

    def test_swept_protrusion_self_intersecting_radius_modes(self) -> None:
        sweep = SweptProtrusionFeature(
            id="sw1",
            path=SweepPathSpec(type="full_circle", radius_mm=25.0),
            cross_sections=(SweepCrossSectionSpec(type="circle", diameter_mm=60.0),),
        )
        body = RectangularBaseBody(id="b1", length_mm=100.0, width_mm=100.0, thickness_mm=20.0)

        # In strict mode, raises hard rejection
        with pytest.raises(FeaturePlanValidationError) as exc:
            _validate_swept_protrusion(sweep, base_body=body, edge_margin_mm=0.0, path="features[0]", mode="strict")
        assert exc.value.code == "SWEEP_SELF_INTERSECTS"

        # In capability_first mode with diagnostics, emits relaxed warning
        diagnostics: list[ValidationDiagnostic] = []
        _validate_swept_protrusion(
            sweep,
            base_body=body,
            edge_margin_mm=0.0,
            path="features[0]",
            diagnostics=diagnostics,
            mode="capability_first",
        )
        assert len(diagnostics) == 1
        assert diagnostics[0].severity == "warning"
        assert diagnostics[0].code == "GATE_POLICY_RELAXED"
        assert "SWEEP_SELF_INTERSECTS" in diagnostics[0].message

    def test_swept_protrusion_rectangular_corner_diagonal_clearance(self) -> None:
        # Rectangle 20x20 has half-extent 10mm but diagonal corner radius 14.14mm
        sweep = SweptProtrusionFeature(
            id="sw_rect",
            path=SweepPathSpec(type="full_circle", radius_mm=12.0),
            cross_sections=(SweepCrossSectionSpec(type="rectangle", width_mm=20.0, height_mm=20.0),),
        )
        body = RectangularBaseBody(id="b1", length_mm=100.0, width_mm=100.0, thickness_mm=20.0)

        # In strict mode, diagonal 14.14 > 12.0 triggers SWEEP_SELF_INTERSECTS
        with pytest.raises(FeaturePlanValidationError) as exc:
            _validate_swept_protrusion(sweep, base_body=body, edge_margin_mm=0.0, path="features[0]", mode="strict")
        assert exc.value.code == "SWEEP_SELF_INTERSECTS"
