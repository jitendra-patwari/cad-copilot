"""Validators package for CAD Copilot feature plan validation.

Provides modular geometric constraint validators, B-Rep boolean lifecycle state tracking,
and feature extent normalizers.
"""

from __future__ import annotations

from geometry.validators.base_body import _validate_base_body
from geometry.validators.common import (
    _effective_edge_margin_mm,
    _reject,
    _require_finite,
    _require_positive,
    _validate_circular_cross_section_profile_fit,
    _warn_allow_or_reject_geometry_fit,
)
from geometry.validators.composition import _validate_composition_layer
from geometry.validators.feature_extents import _normalize_subtractive_extent
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
from geometry.validators.feature_revolve import (
    _normalize_revolved_axis_span,
    _normalize_revolved_profile_from_dimensions,
    _normalize_revolved_shaft_profile,
    _validate_revolved_profile,
    _validate_revolved_shaft_profile,
)
from geometry.validators.feature_sweep import _validate_swept_protrusion
from geometry.validators.target_resolution import (
    _default_target_face_for_selector,
    _normalize_target_selector,
    _rectangular_prism_face_by_area,
    _resolve_feature_target,
)

__all__ = [
    "_default_target_face_for_selector",
    "_effective_edge_margin_mm",
    "_normalize_revolved_axis_span",
    "_normalize_revolved_profile_from_dimensions",
    "_normalize_revolved_shaft_profile",
    "_normalize_slot_orientation",
    "_normalize_subtractive_extent",
    "_normalize_target_selector",
    "_rectangular_prism_face_by_area",
    "_reject",
    "_require_finite",
    "_require_positive",
    "_resolve_feature_target",
    "_validate_base_body",
    "_validate_circular_cross_section_profile_fit",
    "_validate_composition_layer",
    "_validate_feature_supported_on_base_body",
    "_validate_hole_fit",
    "_validate_hole_separation",
    "_validate_profile_cutout_fit",
    "_validate_rectangular_cutout_fit",
    "_validate_rectangular_pad_fit",
    "_validate_revolved_profile",
    "_validate_revolved_shaft_profile",
    "_validate_slot_cutout_fit",
    "_validate_swept_protrusion",
    "_warn_allow_or_reject_geometry_fit",
]
