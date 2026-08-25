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
    DefaultApplied,
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

    defaults_applied: list[DefaultApplied] = []
    diagnostics: list[ValidationDiagnostic] = []

    body_dimensions = _dict(body_payload.get("dimensions_mm", body_payload.get("dimensions")))
    base_body = _base_body_from_dict(
        body_payload,
        body_dimensions,
        path="base_body",
    )
    features = tuple(
        _feature_from_dict(
            item,
            index=index,
            defaults=defaults_applied,
            diagnostics=diagnostics,
        )
        for index, item in enumerate(raw_features)
    )
    primitive_bodies = _primitive_bodies_from_payload(
        payload,
        base_body=base_body,
    )
    boolean_operations = _boolean_operations_from_payload(
        payload,
        defaults=defaults_applied,
        diagnostics=diagnostics,
    )

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
        defaults_applied=tuple(defaults_applied),
        validation_diagnostics=tuple(diagnostics),
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
        _base_body_from_dict(
            body_payload,
            _dict(body_payload.get("dimensions_mm", body_payload.get("dimensions"))),
            path=f"primitive_bodies[{index}]",
        )
        for index, item in enumerate(raw_bodies)
        for body_payload in (_dict(item),)
    )
    if not bodies:
        return (base_body,)
    return bodies


def _boolean_operations_from_payload(
    payload: dict[str, Any],
    *,
    defaults: list[DefaultApplied],
    diagnostics: list[ValidationDiagnostic],
) -> tuple[BooleanOperation, ...]:
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

    return tuple(
        _boolean_operation_from_dict(
            _dict(item),
            index=index,
            defaults=defaults,
            diagnostics=diagnostics,
        )
        for index, item in enumerate(raw_operations)
    )


def _boolean_operation_from_dict(
    payload: dict[str, Any],
    *,
    index: int,
    defaults: list[DefaultApplied],
    diagnostics: list[ValidationDiagnostic],
) -> BooleanOperation:
    path = f"boolean_operations[{index}]"
    raw_op = payload.get("operation", payload.get("family"))
    operation = _normalize_boolean_operation(
        raw_op,
        path=f"{path}.operation",
        defaults=defaults,
        diagnostics=diagnostics,
    )
    return BooleanOperation(
        id=str(payload.get("id", f"boolean.{index + 1}")),
        operation=operation,
        target_body_id=str(payload.get("target_body_id", payload.get("target", ""))),
        tool_body_id=str(payload.get("tool_body_id", payload.get("tool", ""))),
        result_body_id=str(payload.get("result_body_id", payload.get("result", f"body.boolean.{index + 1}"))),
    )


def _normalize_boolean_operation(
    value: object,
    *,
    path: str,
    defaults: list[DefaultApplied],
    diagnostics: list[ValidationDiagnostic],
) -> BooleanOperationFamily:
    if value is None or str(value).strip() == "":
        return "union"
    raw = str(value).strip()
    normalized = raw.lower()
    if normalized in {"subtract", "difference", "cut", "remove", "boolean_subtract"}:
        return "subtract"
    if normalized in {"intersect", "common", "intersection", "boolean_intersect"}:
        return "intersect"
    if normalized in {"union", "add", "fuse", "join", "boolean_union"}:
        return "union"
    defaults.append(
        DefaultApplied(
            path=path,
            value="union",
            reason=f"Unknown boolean operation '{raw}' fell back to 'union'",
            original_value=raw,
        )
    )
    diagnostics.append(
        ValidationDiagnostic(
            severity="warning",
            code="UNKNOWN_BOOLEAN_OPERATION",
            message=f"Unknown boolean operation '{raw}' defaulted to 'union'.",
            path=path,
        )
    )
    return "union"


def _normalize_placement_mode(
    value: object,
    *,
    path: str,
    defaults: list[DefaultApplied],
    diagnostics: list[ValidationDiagnostic],
) -> PlacementMode:
    if value is None or str(value).strip() == "":
        return "face_local_center"
    raw = str(value).strip()
    normalized = raw.lower()
    if normalized in {"face_local_center", "center", "local_center"}:
        return "face_local_center"
    if normalized in {"face_local_offset", "offset", "local_offset"}:
        return "face_local_offset"
    defaults.append(
        DefaultApplied(
            path=path,
            value="face_local_center",
            reason=f"Unknown placement mode '{raw}' fell back to 'face_local_center'",
            original_value=raw,
        )
    )
    diagnostics.append(
        ValidationDiagnostic(
            severity="warning",
            code="UNKNOWN_PLACEMENT_MODE",
            message=f"Unknown placement mode '{raw}' defaulted to 'face_local_center'.",
            path=path,
        )
    )
    return "face_local_center"


def _normalize_face_alias(
    value: object,
    *,
    path: str,
    defaults: list[DefaultApplied],
    diagnostics: list[ValidationDiagnostic],
) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    raw = str(value).strip()
    tf = raw.lower()
    if tf in {"top", "up", "+z", "z+", "z"}:
        return "+Z"
    if tf in {"bottom", "down", "-z", "z-"}:
        return "-Z"
    if tf in {"right", "+x", "x+", "x"}:
        return "+X"
    if tf in {"left", "-x", "x-"}:
        return "-X"
    if tf in {"back", "+y", "y+", "y"}:
        return "+Y"
    if tf in {"front", "-y", "y-"}:
        return "-Y"
    defaults.append(
        DefaultApplied(
            path=path,
            value="+Z",
            reason=f"Unknown face alias '{raw}' fell back to '+Z'",
            original_value=raw,
        )
    )
    diagnostics.append(
        ValidationDiagnostic(
            severity="warning",
            code="UNKNOWN_FACE_ALIAS",
            message=f"Unknown face alias '{raw}' defaulted to '+Z'.",
            path=path,
        )
    )
    return "+Z"


def _normalize_slot_orientation(
    value: object,
    *,
    path: str,
    defaults: list[DefaultApplied],
    diagnostics: list[ValidationDiagnostic],
) -> SlotOrientationAxis:
    if value is None or str(value).strip() == "":
        return "x"
    raw = str(value).strip()
    normalized = raw.lower().replace(" ", "").replace("_", "")
    if normalized in {
        "x",
        "horizontal",
        "u",
        "axisx",
        "0",
        "0deg",
        "0degree",
        "0degrees",
        "0.0",
        "0.0deg",
        "deg0",
    }:
        return "x"
    if normalized in {
        "y",
        "vertical",
        "v",
        "axisy",
        "90",
        "90deg",
        "90degree",
        "90degrees",
        "90.0",
        "90.0deg",
        "deg90",
    }:
        return "y"
    defaults.append(
        DefaultApplied(
            path=path,
            value="x",
            reason=f"Unknown slot orientation '{raw}' fell back to 'x'",
            original_value=raw,
        )
    )
    diagnostics.append(
        ValidationDiagnostic(
            severity="warning",
            code="UNKNOWN_SLOT_ORIENTATION",
            message=f"Unknown slot orientation '{raw}' defaulted to 'x'.",
            path=path,
        )
    )
    return "x"


def _normalize_sweep_path_type(
    value: object,
    *,
    path: str,
    defaults: list[DefaultApplied],
    diagnostics: list[ValidationDiagnostic],
) -> Any:
    if value is None or str(value).strip() == "":
        return "full_circle"
    raw = str(value).strip()
    normalized = raw.lower()
    if normalized in {"full_circle", "circle", "closed_circle"}:
        return "full_circle"
    if normalized in {"semicircle", "half_circle"}:
        return "semicircle"
    if normalized in {"quarter_arc", "quarter_circle", "arc_90"}:
        return "quarter_arc"
    if normalized in {"straight_line", "linear", "line"}:
        return "straight_line"
    if normalized in {"none"}:
        return "none"
    defaults.append(
        DefaultApplied(
            path=path,
            value="full_circle",
            reason=f"Unknown sweep path type '{raw}' fell back to 'full_circle'",
            original_value=raw,
        )
    )
    diagnostics.append(
        ValidationDiagnostic(
            severity="warning",
            code="UNKNOWN_SWEEP_PATH_TYPE",
            message=f"Unknown sweep path type '{raw}' defaulted to 'full_circle'.",
            path=path,
        )
    )
    return "full_circle"


def _normalize_sweep_section_type(
    value: object,
    *,
    path: str,
    defaults: list[DefaultApplied],
    diagnostics: list[ValidationDiagnostic],
) -> Any:
    if value is None or str(value).strip() == "":
        return "circle"
    raw = str(value).strip()
    normalized = raw.lower()
    if normalized in {"circle", "round", "circular"}:
        return "circle"
    if normalized in {"rectangle", "square", "rectangular"}:
        return "rectangle"
    if normalized in {"polygon", "poly"}:
        return "polygon"
    defaults.append(
        DefaultApplied(
            path=path,
            value="circle",
            reason=f"Unknown sweep section type '{raw}' fell back to 'circle'",
            original_value=raw,
        )
    )
    diagnostics.append(
        ValidationDiagnostic(
            severity="warning",
            code="UNKNOWN_SWEEP_SECTION_TYPE",
            message=f"Unknown sweep section type '{raw}' defaulted to 'circle'.",
            path=path,
        )
    )
    return "circle"


def _normalize_sweep_section_position(
    value: object,
    *,
    path: str,
    defaults: list[DefaultApplied],
    diagnostics: list[ValidationDiagnostic],
) -> Any:
    if value is None or str(value).strip() == "":
        return "start"
    raw = str(value).strip()
    normalized = raw.lower()
    if normalized in {"start", "begin", "origin"}:
        return "start"
    if normalized in {"end", "finish", "terminal"}:
        return "end"
    defaults.append(
        DefaultApplied(
            path=path,
            value="start",
            reason=f"Unknown sweep cross-section position '{raw}' fell back to 'start'",
            original_value=raw,
        )
    )
    diagnostics.append(
        ValidationDiagnostic(
            severity="warning",
            code="UNKNOWN_SWEEP_SECTION_POSITION",
            message=f"Unknown sweep cross-section position '{raw}' defaulted to 'start'.",
            path=path,
        )
    )
    return "start"


def _feature_from_dict(
    item: object,
    *,
    index: int,
    defaults: list[DefaultApplied],
    diagnostics: list[ValidationDiagnostic],
) -> FeaturePlanFeature:
    payload = _dict(item)
    path = f"features[{index}]"
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

    raw_placement_mode = placement.get("mode")
    placement_mode = _normalize_placement_mode(
        raw_placement_mode,
        path=f"{path}.placement.mode",
        defaults=defaults,
        diagnostics=diagnostics,
    )

    raw_face = face.get("resolved_face")
    if raw_face is None:
        target_face_val = target.get("face")
        if isinstance(target_face_val, str):
            raw_face = target_face_val

    target_face = _normalize_face_alias(
        raw_face,
        path=f"{path}.target.face",
        defaults=defaults,
        diagnostics=diagnostics,
    )

    common: dict[str, Any] = {
        "id": feature_id,
        "target_body_id": str(target.get("body_id", "body.main")),
        "target_selector": _optional_str(face.get("selector")),
        "target_face": target_face,
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
            profile_points=_profile_points_from_list(
                profile.get("points", payload.get("profile_points", [])),
                path=f"{path}.profile.points",
            ),
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
            profile_points=_profile_points_from_list(
                profile.get("points", payload.get("profile_points", [])),
                path=f"{path}.profile.points",
            ),
            axis_start=_point_from_dict(_dict(axis.get("start")), path=f"{path}.revolve.axis.start"),
            axis_end=_point_from_dict(_dict(axis.get("end")), path=f"{path}.revolve.axis.end"),
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
            _reject("INVALID_SWEPT_PROTRUSION", "cross_sections must be a list.", path=f"{path}.cross_sections")
        if len(cross_sections_raw) > MAX_SWEEP_CROSS_SECTIONS:
            _reject(
                "EXCESSIVE_SWEEP_CROSS_SECTIONS",
                f"cross_sections count {len(cross_sections_raw)} exceeds limit of {MAX_SWEEP_CROSS_SECTIONS}.",
                path=f"{path}.cross_sections",
            )

        raw_path_type = path_payload.get("type")
        path_type = _normalize_sweep_path_type(
            raw_path_type,
            path=f"{path}.path.type",
            defaults=defaults,
            diagnostics=diagnostics,
        )
        path_spec = SweepPathSpec(
            type=path_type,
            radius_mm=float(path_payload.get("radius_mm", path_payload.get("radius", 0.0))),
            angle_deg=float(path_payload.get("angle_deg", 0.0)),
        )

        sections: list[SweepCrossSectionSpec] = []
        for s_index, s_item in enumerate(cross_sections_raw):
            s_payload = _dict(s_item)
            raw_sec_type = s_payload.get("type")
            sec_type = _normalize_sweep_section_type(
                raw_sec_type,
                path=f"{path}.cross_sections[{s_index}].type",
                defaults=defaults,
                diagnostics=diagnostics,
            )
            raw_pos = s_payload.get("position")
            sec_pos = _normalize_sweep_section_position(
                raw_pos,
                path=f"{path}.cross_sections[{s_index}].position",
                defaults=defaults,
                diagnostics=diagnostics,
            )
            sections.append(
                SweepCrossSectionSpec(
                    type=sec_type,
                    diameter_mm=float(s_payload.get("diameter_mm", s_payload.get("diameter", 0.0))),
                    width_mm=float(s_payload.get("width_mm", s_payload.get("width", 0.0))),
                    height_mm=float(s_payload.get("height_mm", s_payload.get("height", 0.0))),
                    profile_points=(
                        _profile_points_from_list(
                            s_payload.get("profile_points", []),
                            path=f"{path}.cross_sections[{s_index}].profile_points",
                        )
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

    raw_axis = orientation.get(
        "axis",
        payload.get("orientation_axis", payload.get("axis")),
    )
    orientation_axis = _normalize_slot_orientation(
        raw_axis,
        path=f"{path}.orientation.axis",
        defaults=defaults,
        diagnostics=diagnostics,
    )

    return SlotThroughCutoutFeature(
        **common,
        length_mm=float(dimensions.get("length", dimensions.get("length_mm", payload.get("length_mm", 0.0)))),
        width_mm=float(dimensions.get("width", dimensions.get("width_mm", payload.get("width_mm", 0.0)))),
        orientation_axis=orientation_axis,
        depth_mm=depth_mm,
    )


def _base_body_from_dict(
    body_payload: dict[str, Any],
    dimensions: dict[str, Any],
    *,
    path: str = "base_body",
) -> FeaturePlanBaseBody:
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
                body_payload.get("profile_points", dimensions.get("profile_points", [])),
                path=f"{path}.profile_points",
            ),
            placement=placement,
        )

    _reject(
        "UNKNOWN_BASE_BODY_FAMILY",
        f"Unsupported or unknown base body family '{family}'.",
        path=f"{path}.family",
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


def _profile_points_from_list(value: object, *, path: str = "profile.points") -> tuple[ProfilePoint2D, ...]:
    if not isinstance(value, list):
        _reject("INVALID_REVOLVE_PROFILE", f"{path} must be a list.", path=path)
    if len(value) > MAX_PROFILE_POINTS:
        _reject(
            "EXCESSIVE_PROFILE_POINTS",
            f"Profile points count {len(value)} exceeds maximum allowed limit of {MAX_PROFILE_POINTS}.",
            path=path,
        )
    return tuple(_point_from_dict(_dict(item), path=f"{path}[{index}]") for index, item in enumerate(value))


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
