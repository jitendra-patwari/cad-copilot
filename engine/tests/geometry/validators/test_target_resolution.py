"""Unit tests for target selector normalization and face resolution."""

from __future__ import annotations

from geometry.plan_models import (
    DEFAULT_EDGE_MARGIN_MM,
    CircularThroughHoleFeature,
    DefaultApplied,
    RectangularBaseBody,
)
from geometry.validators.common import _effective_edge_margin_mm
from geometry.validators.target_resolution import (
    _default_target_face_for_selector,
    _normalize_target_selector,
    _resolve_feature_target,
)


class TestTargetResolution:
    """Verify target selector normalization and face resolution."""

    def test_target_selector_normalization(self) -> None:
        assert _normalize_target_selector("biggest") == "largest_face"
        assert _normalize_target_selector("biggest_face") == "largest_face"
        assert _normalize_target_selector("default_face") == "default_thickness_face"
        assert _normalize_target_selector("top_face") == "default_thickness_face"
        assert _normalize_target_selector("  Top_Face  ") == "default_thickness_face"
        assert _normalize_target_selector("DEFAULT_THICKNESS_FACE") == "default_thickness_face"
        assert _normalize_target_selector("  SIDE  ") == "side_face"
        assert _normalize_target_selector("Lateral_Face") == "side_face"
        assert _normalize_target_selector("side_wall") == "side_face"

    def test_default_target_face_for_selector(self) -> None:
        body = RectangularBaseBody(id="b1", length_mm=100.0, width_mm=50.0, thickness_mm=10.0)
        hole = CircularThroughHoleFeature(id="h1", diameter_mm=8.0)
        assert (
            _default_target_face_for_selector("largest_face", base_body=body, feature=hole, path="features[0]") == "+Z"
        )
        assert (
            _default_target_face_for_selector("smallest_face", base_body=body, feature=hole, path="features[0]") == "+X"
        )

    def test_resolve_feature_target(self) -> None:
        body = RectangularBaseBody(id="b1", length_mm=80.0, width_mm=40.0, thickness_mm=6.0)
        hole = CircularThroughHoleFeature(id="h1", diameter_mm=8.0)
        defaults: list[DefaultApplied] = []

        resolved = _resolve_feature_target(hole, base_body=body, defaults=defaults, path="features[0]")
        assert resolved.target_face == "+Z"

    def test_resolve_feature_target_casing_and_whitespace_robustness(self) -> None:
        body = RectangularBaseBody(id="b1", length_mm=80.0, width_mm=40.0, thickness_mm=6.0)
        hole = CircularThroughHoleFeature(
            id="h1",
            diameter_mm=8.0,
            target_face=" +x ",
            target_selector=" Lateral_Face ",
            normal_axis=" X ",
        )
        defaults: list[DefaultApplied] = []
        resolved = _resolve_feature_target(hole, base_body=body, defaults=defaults, path="features[0]")
        assert resolved.target_face == "+X"
        assert resolved.target_selector == "side_face"
        assert resolved.normal_axis == "x"

    def test_effective_edge_margin(self) -> None:
        assert _effective_edge_margin_mm(DEFAULT_EDGE_MARGIN_MM, mode="capability_first") == 0.0
        assert _effective_edge_margin_mm(DEFAULT_EDGE_MARGIN_MM, mode="strict") == DEFAULT_EDGE_MARGIN_MM
        assert _effective_edge_margin_mm(2.0, mode="capability_first") == 2.0
