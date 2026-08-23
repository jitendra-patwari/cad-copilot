"""Target resolution and normal axis default calculation for feature plan entities."""

from __future__ import annotations

from dataclasses import replace

from geometry.face_context import resolve_face_context
from geometry.plan_models import (
    CircularThroughHoleFeature,
    CylinderBaseBody,
    DefaultApplied,
    FeaturePlanBaseBody,
    FeaturePlanFeature,
    ProfileCutoutFeature,
    RectangularBaseBody,
    RectangularExtrudedPadFeature,
    RectangularThroughCutoutFeature,
    RevolvedProfileFeature,
    SlotThroughCutoutFeature,
    SphereBaseBody,
    SweptProtrusionFeature,
)
from geometry.validators.common import _reject


def _rectangular_prism_face_by_area(base_body: RectangularBaseBody, *, largest: bool) -> str:
    """Select the rectangular prism face with the largest or smallest surface area."""
    face_areas: list[tuple[str, float]] = [
        ("+X", base_body.width_mm * base_body.thickness_mm),
        ("+Y", base_body.length_mm * base_body.thickness_mm),
        ("+Z", base_body.length_mm * base_body.width_mm),
    ]
    best_face, _ = (
        max(face_areas, key=lambda item: item[1])
        if largest
        else min(face_areas, key=lambda item: item[1])
    )
    return best_face


def _resolve_feature_target(
    feature: (
        CircularThroughHoleFeature
        | RectangularThroughCutoutFeature
        | SlotThroughCutoutFeature
        | RectangularExtrudedPadFeature
        | RevolvedProfileFeature
        | ProfileCutoutFeature
        | SweptProtrusionFeature
    ),
    *,
    base_body: FeaturePlanBaseBody,
    defaults: list[DefaultApplied],
    path: str,
) -> (
    CircularThroughHoleFeature
    | RectangularThroughCutoutFeature
    | SlotThroughCutoutFeature
    | RectangularExtrudedPadFeature
    | RevolvedProfileFeature
    | ProfileCutoutFeature
    | SweptProtrusionFeature
):
    """Resolve target face selectors and populate deterministic normal axis defaults."""
    target_selector = feature.target_selector
    target_face = feature.target_face.strip().upper() if feature.target_face is not None else None
    normal_axis = feature.normal_axis.strip().lower() if feature.normal_axis is not None else None

    if target_selector is None:
        target_selector = "side_face" if target_face in {"+X", "-X", "+Y", "-Y"} else "default_thickness_face"
        defaults.append(
            DefaultApplied(
                path=f"{path}.target.face.selector",
                value=target_selector,
                reason=target_selector,
            )
        )

    normalized_target_selector = _normalize_target_selector(target_selector)
    if normalized_target_selector != target_selector:
        target_selector = normalized_target_selector
        defaults.append(
            DefaultApplied(
                path=f"{path}.target.face.selector",
                value=target_selector,
                reason="canonical_target_selector_alias",
            )
        )

    if target_selector not in {"default_thickness_face", "largest_face", "smallest_face", "side_face"}:
        _reject(
            "UNSUPPORTED_TARGET_FACE",
            "Only default thickness, largest, smallest, and explicit side-face selectors are supported.",
            path=path,
        )

    selector_resolved_face: str | None = None
    selector_authoritative = target_selector in {"largest_face", "smallest_face"} or (
        target_selector == "default_thickness_face"
        and isinstance(base_body, CylinderBaseBody | SphereBaseBody)
    )

    if selector_authoritative or (target_selector == "default_thickness_face" and target_face is None):
        selector_resolved_face = _default_target_face_for_selector(
            target_selector,
            base_body=base_body,
            feature=feature,
            path=path,
        )

    if selector_resolved_face is not None and target_face != selector_resolved_face:
        target_face = selector_resolved_face
        defaults.append(
            DefaultApplied(
                path=f"{path}.target.face.resolved_face",
                value=target_face,
                reason=f"{target_selector}_resolved_face",
            )
        )

    if target_face not in {"+Z", "-Z", "+X", "-X", "+Y", "-Y"}:
        _reject("UNSUPPORTED_TARGET_FACE", "Only +/-Z and rectangular-prism side faces are supported.", path=path)

    if (
        target_face not in {"+Z", "-Z"}
        and not (
            isinstance(base_body, RectangularBaseBody)
            and isinstance(
                feature,
                CircularThroughHoleFeature
                | RectangularThroughCutoutFeature
                | SlotThroughCutoutFeature
                | RevolvedProfileFeature
                | ProfileCutoutFeature,
            )
        )
        and not (
            isinstance(base_body, RectangularBaseBody)
            and isinstance(feature, RectangularExtrudedPadFeature)
            and target_face in {"+X", "-X", "+Y", "-Y"}
        )
    ):
        _reject(
            "UNSUPPORTED_TARGET_FACE",
            "Only circular holes, rectangular cutouts, slots, revolved profiles, and +/-X/+/-Y rectangular pads "
            "on rectangular-prism side faces are supported outside +Z/default.",
            path=path,
        )

    expected_axis = resolve_face_context(target_face, base_body, feature).normal_axis
    if normal_axis is None or (normal_axis != expected_axis and selector_authoritative):
        normal_axis = expected_axis
        defaults.append(
            DefaultApplied(
                path=f"{path}.target.face.normal_axis",
                value=normal_axis,
                reason=f"{target_face}_normal_axis",
            )
        )

    if normal_axis != expected_axis:
        _reject(
            "UNSUPPORTED_TARGET_AXIS",
            f"Target face {target_face} requires normal_axis '{expected_axis}'.",
            path=path,
        )

    return replace(feature, target_selector=target_selector, target_face=target_face, normal_axis=normal_axis)


def _normalize_target_selector(target_selector: str) -> str:
    """Normalize target selector string aliases to canonical selector names."""
    cleaned = target_selector.strip().lower()
    aliases = {
        "biggest": "largest_face",
        "biggest_face": "largest_face",
        "largest": "largest_face",
        "largest_face": "largest_face",
        "smallest": "smallest_face",
        "smallest_face": "smallest_face",
        "default_xy_face": "default_thickness_face",
        "default_face": "default_thickness_face",
        "default": "default_thickness_face",
        "xy_face": "default_thickness_face",
        "default_cross_section": "default_thickness_face",
        "default_xy_cross_section": "default_thickness_face",
        "axial_face": "default_thickness_face",
        "default_axial_face": "default_thickness_face",
        "axial_cross_section": "default_thickness_face",
        "default_thickness_face": "default_thickness_face",
        "top": "default_thickness_face",
        "top_face": "default_thickness_face",
        "bottom": "default_thickness_face",
        "bottom_face": "default_thickness_face",
        "side": "side_face",
        "side_face": "side_face",
        "side_faces": "side_face",
        "lateral": "side_face",
        "lateral_face": "side_face",
        "side_wall": "side_face",
        "side_walls": "side_face",
        "sidewall": "side_face",
    }
    return aliases.get(cleaned, target_selector)


def _default_target_face_for_selector(
    target_selector: str,
    *,
    base_body: FeaturePlanBaseBody,
    feature: FeaturePlanFeature,
    path: str,
) -> str:
    """Derive the deterministic target face identifier for a given selector."""
    if target_selector == "default_thickness_face":
        return "+Z"

    if target_selector == "largest_face":
        if isinstance(base_body, RectangularBaseBody):
            return _rectangular_prism_face_by_area(base_body, largest=True)
        return "+Z"

    if target_selector == "smallest_face":
        if not isinstance(base_body, RectangularBaseBody) or not isinstance(
            feature,
            CircularThroughHoleFeature
            | RectangularThroughCutoutFeature
            | SlotThroughCutoutFeature
            | RectangularExtrudedPadFeature
            | ProfileCutoutFeature,
        ):
            _reject(
                "UNSUPPORTED_TARGET_FACE",
                "Smallest-face selection currently supports rectangular-prism circular holes, rectangular cutouts, "
                "slots, and rectangular pads.",
                path=path,
            )
        return _rectangular_prism_face_by_area(base_body, largest=False)

    _reject(
        "UNSUPPORTED_TARGET_FACE",
        "Explicit side-face selection requires resolved_face to be supplied.",
        path=path,
    )
    return "+Z"


__all__ = [
    "_default_target_face_for_selector",
    "_normalize_target_selector",
    "_rectangular_prism_face_by_area",
    "_resolve_feature_target",
]
