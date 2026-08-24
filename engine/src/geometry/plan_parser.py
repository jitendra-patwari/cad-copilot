"""Dictionary parsing helpers and AST deserializer for canonical feature plans.

Includes defensive DoS input validation (CWE-400) and transparent key canonicalization.
"""

from __future__ import annotations

from typing import Any, NoReturn

from geometry.plan_models import (
    CANONICAL_PLAN_VERSION,
    BodyPlacement,
    BooleanOperation,
    BooleanOperationFamily,
    CircularThroughHoleFeature,
    CylinderBaseBody,
    FeaturePlan,
    FeaturePlanBaseBody,
    FeaturePlanFeature,
    FeaturePlanValidationError,
    PartMetadata,
    PlacementMode,
    ProfileCutoutFeature,
    ProfilePoint2D,
    RectangularBaseBody,
    RectangularExtrudedPadFeature,
    RectangularThroughCutoutFeature,
    RevolvedProfileFeature,
    RevolvedShaftBaseBody,
    SlotOrientationAxis,
    SlotThroughCutoutFeature,
    SphereBaseBody,
    SpurGearBaseBody,
    SweepCrossSectionSpec,
    SweepPathSpec,
    SweptProtrusionFeature,
    Units,
    UnsupportedFeature,
    ValidationDiagnostic,
)

# Defensive resource bounds preventing memory/CPU exhaustion from malformed or runaway payloads (CWE-400)
MAX_FEATURES: int = 128
MAX_PRIMITIVE_BODIES: int = 32
MAX_BOOLEAN_OPERATIONS: int = 32
MAX_PROFILE_POINTS: int = 512
MAX_SWEEP_CROSS_SECTIONS: int = 16


def feature_plan_from_dict(payload: dict[str, Any]) -> FeaturePlan:
    """Load a canonical feature-plan dictionary into immutable AST dataclasses.

    Raises:
        FeaturePlanValidationError: If the payload is malformed or exceeds safety bounds.
    """
    part_payload = _dict(payload.get("part"))
    body_payload = _dict(payload.get("base_body"))
    raw_features = payload.get("features", [])
    if not isinstance(raw_features, list):
        _reject("INVALID_FEATURES", "features must be a list.", path="features")

    if len(raw_features) > MAX_FEATURES:
        _reject(
            "EXCESSIVE_FEATURE_COUNT",
            f"Feature count {len(raw_features)} exceeds maximum allowed limit of {MAX_FEATURES}.",
            path="features",
        )

    body_dimensions = _dict(body_payload.get("dimensions_mm", body_payload.get("dimensions")))
    base_body = _base_body_from_dict(body_payload, body_dimensions)
    features = tuple(_feature_from_dict(item, index=index) for index, item in enumerate(raw_features))
    primitive_bodies = _primitive_bodies_from_payload(payload, base_body=base_body)
    boolean_operations = _boolean_operations_from_payload(payload)

    units: Units = "mm"

    return FeaturePlan(
        plan_version=str(payload.get("plan_version", CANONICAL_PLAN_VERSION)),
        request_id=str(payload.get("request_id", "")),
        units=units,
        part=PartMetadata(
            part_id=str(part_payload.get("part_id", "part.main")),
            design_intent=str(part_payload.get("design_intent", "single-part model")),
            scope="single_part",
        ),
        base_body=base_body,
        primitive_bodies=primitive_bodies,
        boolean_operations=boolean_operations,
        features=features,
    )


def _primitive_bodies_from_payload(
    payload: dict[str, Any],
    *,
    base_body: FeaturePlanBaseBody,
) -> tuple[FeaturePlanBaseBody, ...]:
    raw_bodies = payload.get("primitive_bodies", payload.get("bodies"))
    if raw_bodies is None:
        return (base_body,)
    if not isinstance(raw_bodies, list):
        _reject("INVALID_PRIMITIVE_BODIES", "primitive_bodies must be a list.", path="primitive_bodies")

    if len(raw_bodies) > MAX_PRIMITIVE_BODIES:
        _reject(
            "EXCESSIVE_PRIMITIVE_BODIES",
            f"Primitive bodies count {len(raw_bodies)} exceeds maximum allowed limit of {MAX_PRIMITIVE_BODIES}.",
            path="primitive_bodies",
        )

    bodies = tuple(
        _base_body_from_dict(body_payload, _dict(body_payload.get("dimensions_mm", body_payload.get("dimensions"))))
        for item in raw_bodies
        for body_payload in (_dict(item),)
    )
    if not bodies:
        return (base_body,)
    return bodies


def _boolean_operations_from_payload(payload: dict[str, Any]) -> tuple[BooleanOperation, ...]:
    raw_operations = payload.get("boolean_operations", payload.get("booleans", []))
    if raw_operations is None:
        return ()
    if not isinstance(raw_operations, list):
        _reject("INVALID_BOOLEAN_OPERATIONS", "boolean_operations must be a list.", path="boolean_operations")

    if len(raw_operations) > MAX_BOOLEAN_OPERATIONS:
        _reject(
            "EXCESSIVE_BOOLEAN_OPERATIONS",
            f"Boolean operations count {len(raw_operations)} exceeds maximum allowed limit of {MAX_BOOLEAN_OPERATIONS}.",
            path="boolean_operations",
        )

    return tuple(_boolean_operation_from_dict(_dict(item), index=index) for index, item in enumerate(raw_operations))


def _boolean_operation_from_dict(payload: dict[str, Any], *, index: int) -> BooleanOperation:
    operation = _normalize_boolean_operation(payload.get("operation", payload.get("family", "")))
    return BooleanOperation(
        id=str(payload.get("id", f"boolean.{index + 1}")),
        operation=operation,
        target_body_id=str(payload.get("target_body_id", payload.get("target", ""))),
        tool_body_id=str(payload.get("tool_body_id", payload.get("tool", ""))),
        result_body_id=str(payload.get("result_body_id", payload.get("result", f"body.boolean.{index + 1}"))),
    )


def _normalize_boolean_operation(value: object) -> BooleanOperationFamily:
    normalized = str(value).strip().lower()
    if normalized in {"subtract", "difference", "cut", "remove", "boolean_subtract"}:
        return "subtract"
    if normalized in {"intersect", "common", "intersection", "boolean_intersect"}:
        return "intersect"
    return "union"


def _feature_from_dict(item: object, *, index: int) -> FeaturePlanFeature:
    payload = _dict(item)
    family = str(payload.get("family", ""))
    feature_id = str(payload.get("id", f"feature.{index + 1}"))
    if family not in {
        "circular_through_hole",
        "rectangular_through_cutout",
        "slot_through_cutout",
        "rectangular_extruded_pad",
        "revolved_profile",
        "profile_cutout",
        "swept_protrusion",
    }:
        return UnsupportedFeature(id=feature_id, family=family)

    dimensions = _dict(payload.get("dimensions_mm", payload.get("dimensions")))
    placement = _dict(payload.get("placement"))
    center = _dict(placement.get("center_uv_mm", placement.get("center")))
    target = _dict(payload.get("target"))
    face = _dict(target.get("face"))
    extent = _dict(payload.get("extent"))
    orientation = _dict(payload.get("orientation"))

    raw_placement_mode = str(placement.get("mode", "face_local_center"))
    placement_mode: PlacementMode = (
        "face_local_offset" if raw_placement_mode == "face_local_offset" else "face_local_center"
    )

    common: dict[str, Any] = {
        "id": feature_id,
        "target_body_id": str(target.get("body_id", "body.main")),
        "target_selector": _optional_str(face.get("selector")),
        "target_face": _optional_str(face.get("resolved_face")),
        "normal_axis": _optional_str(face.get("normal_axis")),
        "placement_mode": placement_mode,
        "center_x_mm": float(center.get("u", center.get("x_mm", center.get("x", 0.0)))),
        "center_y_mm": float(center.get("v", center.get("y_mm", center.get("y", 0.0)))),
        "extent_type": str(extent.get("type", "through_all")),
    }
    depth_mm = float(extent.get("depth_mm", extent.get("depth", 0.0)))

    if family == "profile_cutout":
        profile = _dict(payload.get("profile"))
        return ProfileCutoutFeature(
            **common,
            profile_points=_profile_points_from_list(profile.get("points", payload.get("profile_points", []))),
            depth_mm=depth_mm,
        )

    if family == "circular_through_hole":
        return CircularThroughHoleFeature(
            **common,
            diameter_mm=float(
                dimensions.get("diameter", dimensions.get("diameter_mm", payload.get("diameter_mm", 0.0)))
            ),
            depth_mm=depth_mm,
        )

    if family == "rectangular_through_cutout":
        height_mm = float(dimensions.get("height", dimensions.get("height_mm", payload.get("height_mm", 0.0))))
        length_mm = float(dimensions.get("length", dimensions.get("length_mm", payload.get("length_mm", 0.0))))
        if height_mm == 0.0 and length_mm > 0.0:
            height_mm = length_mm
        return RectangularThroughCutoutFeature(
            **common,
            width_mm=float(dimensions.get("width", dimensions.get("width_mm", payload.get("width_mm", 0.0)))),
            height_mm=height_mm,
            depth_mm=depth_mm,
        )

    if family == "rectangular_extruded_pad":
        distance_mm = float(
            dimensions.get(
                "length",
                dimensions.get(
                    "length_mm",
                    dimensions.get(
                        "distance",
                        dimensions.get("distance_mm", extent.get("distance", payload.get("distance_mm", 0.0))),
                    ),
                ),
            )
        )
        return RectangularExtrudedPadFeature(
            **common,
            width_mm=float(dimensions.get("width", dimensions.get("width_mm", payload.get("width_mm", 0.0)))),
            height_mm=float(dimensions.get("height", dimensions.get("height_mm", payload.get("height_mm", 0.0)))),
            distance_mm=distance_mm,
        )

    if family == "revolved_profile":
        profile = _dict(payload.get("profile"))
        revolve = _dict(payload.get("revolve"))
        axis = _dict(revolve.get("axis"))
        return RevolvedProfileFeature(
            **common,
            profile_points=_profile_points_from_list(profile.get("points", payload.get("profile_points", []))),
            axis_start=_point_from_dict(_dict(axis.get("start")), path="revolve.axis.start"),
            axis_end=_point_from_dict(_dict(axis.get("end")), path="revolve.axis.end"),
            angle_deg=float(revolve.get("angle_deg", dimensions.get("angle_deg", payload.get("angle_deg", 360.0)))),
            radial_depth_mm=float(
                dimensions.get(
                    "length",
                    dimensions.get(
                        "length_mm",
                        dimensions.get("width", dimensions.get("width_mm", payload.get("radial_depth_mm", 0.0))),
                    ),
                )
            ),
            profile_height_mm=float(
                dimensions.get("height", dimensions.get("height_mm", payload.get("profile_height_mm", 0.0)))
            ),
        )

    if family == "swept_protrusion":
        path_payload = _dict(payload.get("path"))
        cross_sections_raw = payload.get("cross_sections", [])
        if not isinstance(cross_sections_raw, list):
            _reject("INVALID_SWEPT_PROTRUSION", "cross_sections must be a list.", path="cross_sections")
        if len(cross_sections_raw) > MAX_SWEEP_CROSS_SECTIONS:
            _reject(
                "EXCESSIVE_SWEEP_CROSS_SECTIONS",
                f"cross_sections count {len(cross_sections_raw)} exceeds limit of {MAX_SWEEP_CROSS_SECTIONS}.",
                path="cross_sections",
            )

        raw_path_type = str(path_payload.get("type", "full_circle"))
        path_type: Any = (
            raw_path_type
            if raw_path_type in {"full_circle", "semicircle", "quarter_arc", "straight_line", "none"}
            else "full_circle"
        )
        path_spec = SweepPathSpec(
            type=path_type,
            radius_mm=float(path_payload.get("radius_mm", path_payload.get("radius", 0.0))),
            angle_deg=float(path_payload.get("angle_deg", 0.0)),
        )

        sections: list[SweepCrossSectionSpec] = []
        for _index, s_item in enumerate(cross_sections_raw):
            s_payload = _dict(s_item)
            raw_sec_type = str(s_payload.get("type", "circle"))
            sec_type: Any = raw_sec_type if raw_sec_type in {"circle", "rectangle", "polygon"} else "circle"
            raw_pos = str(s_payload.get("position", "start"))
            sec_pos: Any = "end" if raw_pos == "end" else "start"
            sections.append(
                SweepCrossSectionSpec(
                    type=sec_type,
                    diameter_mm=float(s_payload.get("diameter_mm", s_payload.get("diameter", 0.0))),
                    width_mm=float(s_payload.get("width_mm", s_payload.get("width", 0.0))),
                    height_mm=float(s_payload.get("height_mm", s_payload.get("height", 0.0))),
                    profile_points=(
                        _profile_points_from_list(s_payload.get("profile_points", []))
                        if s_payload.get("profile_points")
                        else ()
                    ),
                    position=sec_pos,
                )
            )

        return SweptProtrusionFeature(
            **common,
            path=path_spec,
            cross_sections=tuple(sections),
        )

    raw_axis = (
        str(
            orientation.get(
                "axis",
                payload.get("orientation_axis", payload.get("axis", "x")),
            )
        )
        .strip()
        .lower()
    )
    orientation_axis: SlotOrientationAxis = "y" if raw_axis == "y" else "x"

    return SlotThroughCutoutFeature(
        **common,
        length_mm=float(dimensions.get("length", dimensions.get("length_mm", payload.get("length_mm", 0.0)))),
        width_mm=float(dimensions.get("width", dimensions.get("width_mm", payload.get("width_mm", 0.0)))),
        orientation_axis=orientation_axis,
        depth_mm=depth_mm,
    )


def _base_body_from_dict(body_payload: dict[str, Any], dimensions: dict[str, Any]) -> FeaturePlanBaseBody:
    family = str(body_payload.get("family", "rectangular_prism"))
    body_id = str(body_payload.get("id", "body.main"))
    labels = tuple(str(item) for item in body_payload.get("semantic_labels", [_default_label_for_base_family(family)]))
    placement = _placement_from_dict(_dict(body_payload.get("placement")))

    if family == "rectangular_prism":
        return RectangularBaseBody(
            id=body_id,
            semantic_labels=labels,
            length_mm=float(dimensions.get("length", dimensions.get("length_mm", body_payload.get("length_mm", 0.0)))),
            width_mm=float(dimensions.get("width", dimensions.get("width_mm", body_payload.get("width_mm", 0.0)))),
            thickness_mm=float(
                dimensions.get(
                    "thickness",
                    dimensions.get(
                        "thickness_mm",
                        dimensions.get("height", dimensions.get("height_mm", body_payload.get("thickness_mm", 0.0))),
                    ),
                )
            ),
            placement=placement,
        )

    if family == "cylinder":
        return CylinderBaseBody(
            id=body_id,
            semantic_labels=labels,
            radius_mm=_radius_from_dimensions(dimensions, body_payload),
            height_mm=float(dimensions.get("height", dimensions.get("height_mm", body_payload.get("height_mm", 0.0)))),
            placement=placement,
        )

    if family == "sphere":
        return SphereBaseBody(
            id=body_id,
            semantic_labels=labels,
            radius_mm=_radius_from_dimensions(dimensions, body_payload),
            placement=placement,
        )

    if family == "spur_gear":
        return SpurGearBaseBody(
            id=body_id,
            semantic_labels=labels,
            tooth_count=int(dimensions.get("tooth_count", body_payload.get("tooth_count", 24))),
            module_mm=float(dimensions.get("module", dimensions.get("module_mm", body_payload.get("module_mm", 2.0)))),
            pressure_angle_deg=float(
                dimensions.get(
                    "pressure_angle",
                    dimensions.get("pressure_angle_deg", body_payload.get("pressure_angle_deg", 20.0)),
                )
            ),
            face_width_mm=float(
                dimensions.get(
                    "face_width",
                    dimensions.get(
                        "face_width_mm",
                        dimensions.get(
                            "thickness", dimensions.get("thickness_mm", body_payload.get("face_width_mm", 10.0))
                        ),
                    ),
                )
            ),
            bore_diameter_mm=float(
                dimensions.get(
                    "bore_diameter",
                    dimensions.get("bore_diameter_mm", body_payload.get("bore_diameter_mm", 0.0)),
                )
            ),
            placement=placement,
        )

    if family == "revolved_shaft":
        return RevolvedShaftBaseBody(
            id=body_id,
            semantic_labels=labels,
            radius_mm=_radius_from_dimensions(dimensions, body_payload),
            height_mm=float(dimensions.get("height", dimensions.get("height_mm", body_payload.get("height_mm", 0.0)))),
            profile_points=_profile_points_from_list(
                body_payload.get("profile_points", dimensions.get("profile_points", []))
            ),
            placement=placement,
        )

    return RectangularBaseBody(
        id=body_id,
        family="rectangular_prism",
        semantic_labels=labels,
        length_mm=0.0,
        width_mm=0.0,
        thickness_mm=0.0,
        placement=placement,
    )


def _placement_from_dict(payload: dict[str, Any]) -> BodyPlacement:
    return BodyPlacement(
        x_mm=float(payload.get("x_mm", payload.get("x", 0.0))),
        y_mm=float(payload.get("y_mm", payload.get("y", 0.0))),
        z_mm=float(payload.get("z_mm", payload.get("z", 0.0))),
    )


def _default_label_for_base_family(family: str) -> str:
    if family in {"cylinder", "sphere", "spur_gear", "revolved_shaft"}:
        return family
    return "plate"


def _radius_from_dimensions(dimensions: dict[str, Any], body_payload: dict[str, Any]) -> float:
    radius = dimensions.get("radius", dimensions.get("radius_mm", body_payload.get("radius_mm")))
    if radius is not None:
        radius_value = float(radius)
        if radius_value > 0.0:
            return radius_value
    diameter = dimensions.get(
        "diameter",
        dimensions.get("diameter_mm", body_payload.get("diameter_mm", body_payload.get("diameter", 0.0))),
    )
    return float(diameter) / 2.0


def _profile_points_from_list(value: object) -> tuple[ProfilePoint2D, ...]:
    if not isinstance(value, list):
        _reject("INVALID_REVOLVE_PROFILE", "profile.points must be a list.", path="profile.points")
    if len(value) > MAX_PROFILE_POINTS:
        _reject(
            "EXCESSIVE_PROFILE_POINTS",
            f"Profile points count {len(value)} exceeds maximum allowed limit of {MAX_PROFILE_POINTS}.",
            path="profile.points",
        )
    return tuple(_point_from_dict(_dict(item), path=f"profile.points[{index}]") for index, item in enumerate(value))


def _point_from_dict(payload: dict[str, Any], *, path: str) -> ProfilePoint2D:
    return ProfilePoint2D(
        x_mm=float(payload.get("x_mm", payload.get("x", 0.0))),
        y_mm=float(payload.get("y_mm", payload.get("y", 0.0))),
    )


def _dict(value: object) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        _reject("INVALID_FEATURE_PLAN", "Expected a JSON object in feature-plan payload.")
    return {str(k): v for k, v in value.items()}


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _reject(code: str, message: str, *, path: str | None = None) -> NoReturn:
    raise FeaturePlanValidationError(
        ValidationDiagnostic(
            severity="error",
            code=code,
            message=message,
            path=path,
        )
    )


__all__ = [
    "MAX_BOOLEAN_OPERATIONS",
    "MAX_FEATURES",
    "MAX_PRIMITIVE_BODIES",
    "MAX_PROFILE_POINTS",
    "MAX_SWEEP_CROSS_SECTIONS",
    "feature_plan_from_dict",
]
