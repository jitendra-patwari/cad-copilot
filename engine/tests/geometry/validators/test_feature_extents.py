"""Unit tests for subtractive feature extent normalization and depth bounds."""

from __future__ import annotations

import pytest

from geometry.plan_models import (
    CircularThroughHoleFeature,
    DefaultApplied,
    FeaturePlanValidationError,
    RectangularBaseBody,
    ValidationDiagnostic,
)
from geometry.validators.feature_extents import _normalize_subtractive_extent


class TestFeatureExtentNormalization:
    """Verify subtractive extent type and depth normalizations."""

    def test_normalize_through_all_extent(self) -> None:
        body = RectangularBaseBody(id="b1", length_mm=80.0, width_mm=40.0, thickness_mm=6.0)
        defaults: list[DefaultApplied] = []
        hole = CircularThroughHoleFeature(id="h1", diameter_mm=8.0, extent_type="through", depth_mm=10.0)

        normalized = _normalize_subtractive_extent(hole, base_body=body, defaults=defaults, path="features[0]")
        assert normalized.extent_type == "through_all"
        assert normalized.depth_mm == 0.0
        assert len(defaults) == 2

    def test_normalize_blind_extent_depth_modes(self) -> None:
        body = RectangularBaseBody(id="b1", length_mm=80.0, width_mm=40.0, thickness_mm=6.0)
        defaults: list[DefaultApplied] = []
        hole = CircularThroughHoleFeature(id="h1", diameter_mm=8.0, extent_type="blind", depth_mm=8.0, target_face="+Z")

        # In strict mode, raises hard error
        with pytest.raises(FeaturePlanValidationError) as exc:
            _normalize_subtractive_extent(hole, base_body=body, defaults=defaults, path="features[0]", mode="strict")
        assert exc.value.code == "INVALID_CUT_DEPTH"

        # In capability_first mode with diagnostics, emits relaxed warning
        diagnostics: list[ValidationDiagnostic] = []
        _normalize_subtractive_extent(
            hole,
            base_body=body,
            defaults=defaults,
            path="features[0]",
            diagnostics=diagnostics,
            mode="capability_first",
        )
        assert len(diagnostics) == 1
        assert diagnostics[0].severity == "warning"
        assert diagnostics[0].code == "GATE_POLICY_RELAXED"
        assert "INVALID_CUT_DEPTH" in diagnostics[0].message
