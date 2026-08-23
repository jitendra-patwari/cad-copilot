"""Canonical feature-plan data model types and AST representations.

This is a pure leaf domain module with zero external dependencies, zero COM bindings,
and zero filesystem I/O, conforming to NFR-1 (Zero I/O Purity) and NFR-4 (Strict Static Typing).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypeAlias

CANONICAL_PLAN_VERSION = "cad_copilot.single_part_feature_plan.v1"
DEFAULT_EDGE_MARGIN_MM = 0.0

Units: TypeAlias = Literal["mm"]
FeaturePlanStatus: TypeAlias = Literal["canonical"]
PlacementMode: TypeAlias = Literal["face_local_center", "face_local_offset"]
SlotOrientationAxis: TypeAlias = Literal["x", "y"]
BooleanOperationFamily: TypeAlias = Literal["union", "subtract", "intersect"]


@dataclass(frozen=True)
class ValidationDiagnostic:
    """Diagnostic message produced during plan validation or linting."""

    severity: Literal["info", "warning", "error"]
    code: str
    message: str
    path: str | None = None

    def to_dict(self) -> dict[str, str]:
        out = {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
        }
        if self.path is not None:
            out["path"] = self.path
        return out


@dataclass(frozen=True)
class DefaultApplied:
    """Record of a default value applied to an omitted plan attribute."""

    path: str
    value: object
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "value": self.value,
            "reason": self.reason,
        }


class FeaturePlanValidationError(ValueError):
    """Raised when a canonical feature plan violates syntactic or geometric contracts."""

    def __init__(self, diagnostic: ValidationDiagnostic) -> None:
        super().__init__(f"{diagnostic.code}: {diagnostic.message}")
        self.diagnostic = diagnostic
        self.code = diagnostic.code


@dataclass(frozen=True)
class PartMetadata:
    """Descriptive metadata for the generated CAD part."""

    part_id: str = "part.main"
    design_intent: str = "single-part model"
    scope: Literal["single_part"] = "single_part"
    origin: Literal["body_center"] = "body_center"
    x_axis: Literal["length"] = "length"
    y_axis: Literal["width"] = "width"
    z_axis: Literal["thickness_up"] = "thickness_up"


@dataclass(frozen=True)
class BodyPlacement:
    """Cartesian translation offsets (mm) for solid bodies relative to world origin."""

    x_mm: float = 0.0
    y_mm: float = 0.0
    z_mm: float = 0.0


@dataclass(frozen=True)
class RectangularBaseBody:
    """Parametric rectangular cuboid/prism solid body primitive."""

    id: str
    length_mm: float
    width_mm: float
    thickness_mm: float
    semantic_labels: tuple[str, ...] = ("plate",)
    family: Literal["rectangular_prism"] = "rectangular_prism"
    placement: BodyPlacement = field(default_factory=BodyPlacement)


@dataclass(frozen=True)
class CylinderBaseBody:
    """Parametric solid cylinder body primitive."""

    id: str
    radius_mm: float
    height_mm: float
    semantic_labels: tuple[str, ...] = ("cylinder",)
    family: Literal["cylinder"] = "cylinder"
    placement: BodyPlacement = field(default_factory=BodyPlacement)


@dataclass(frozen=True)
class SphereBaseBody:
    """Parametric solid sphere body primitive."""

    id: str
    radius_mm: float
    semantic_labels: tuple[str, ...] = ("sphere",)
    family: Literal["sphere"] = "sphere"
    placement: BodyPlacement = field(default_factory=BodyPlacement)


@dataclass(frozen=True)
class SpurGearBaseBody:
    """Parametric concept spur gear body primitive."""

    id: str
    tooth_count: int
    module_mm: float
    face_width_mm: float
    pressure_angle_deg: float = 20.0
    bore_diameter_mm: float = 0.0
    semantic_labels: tuple[str, ...] = ("spur_gear",)
    family: Literal["spur_gear"] = "spur_gear"
    placement: BodyPlacement = field(default_factory=BodyPlacement)


@dataclass(frozen=True)
class ProfilePoint2D:
    """A 2D coordinate point (mm) in sketch/polygon plane space."""

    x_mm: float
    y_mm: float


@dataclass(frozen=True)
class RevolvedShaftBaseBody:
    """Parametric solid body created by revolving a 2D polyline profile about an axis."""

    id: str
    radius_mm: float
    height_mm: float
    profile_points: tuple[ProfilePoint2D, ...]
    semantic_labels: tuple[str, ...] = ("revolved_shaft",)
    family: Literal["revolved_shaft"] = "revolved_shaft"
    placement: BodyPlacement = field(default_factory=BodyPlacement)


FeaturePlanBaseBody: TypeAlias = (
    RectangularBaseBody
    | CylinderBaseBody
    | SphereBaseBody
    | SpurGearBaseBody
    | RevolvedShaftBaseBody
)


@dataclass(frozen=True)
class BooleanOperation:
    """Constructive Solid Geometry (CSG) boolean operation between two solid bodies."""

    id: str
    operation: BooleanOperationFamily
    target_body_id: str
    tool_body_id: str
    result_body_id: str


@dataclass(frozen=True)
class CircularThroughHoleFeature:
    """Subtractive circular bore feature passing through a body."""

    id: str
    diameter_mm: float
    center_x_mm: float = 0.0
    center_y_mm: float = 0.0
    placement_mode: PlacementMode = "face_local_center"
    target_body_id: str = "body.main"
    target_face: str | None = None
    target_selector: str | None = None
    normal_axis: str | None = None
    family: Literal["circular_through_hole"] = "circular_through_hole"
    extent_type: str = "through_all"
    depth_mm: float = 0.0


@dataclass(frozen=True)
class RectangularThroughCutoutFeature:
    """Subtractive rectangular cutout feature passing through a body."""

    id: str
    width_mm: float
    height_mm: float
    center_x_mm: float = 0.0
    center_y_mm: float = 0.0
    placement_mode: PlacementMode = "face_local_center"
    target_body_id: str = "body.main"
    target_face: str | None = None
    target_selector: str | None = None
    normal_axis: str | None = None
    family: Literal["rectangular_through_cutout"] = "rectangular_through_cutout"
    extent_type: str = "through_all"
    depth_mm: float = 0.0


@dataclass(frozen=True)
class SlotThroughCutoutFeature:
    """Subtractive stadium/slot cutout feature passing through a body."""

    id: str
    length_mm: float
    width_mm: float
    orientation_axis: SlotOrientationAxis = "x"
    center_x_mm: float = 0.0
    center_y_mm: float = 0.0
    placement_mode: PlacementMode = "face_local_center"
    target_body_id: str = "body.main"
    target_face: str | None = None
    target_selector: str | None = None
    normal_axis: str | None = None
    family: Literal["slot_through_cutout"] = "slot_through_cutout"
    extent_type: str = "through_all"
    depth_mm: float = 0.0


@dataclass(frozen=True)
class RectangularExtrudedPadFeature:
    """Additive rectangular boss/pad feature extruded from a target face."""

    id: str
    width_mm: float
    height_mm: float
    distance_mm: float
    center_x_mm: float = 0.0
    center_y_mm: float = 0.0
    placement_mode: PlacementMode = "face_local_center"
    target_body_id: str = "body.main"
    target_face: str | None = None
    target_selector: str | None = None
    normal_axis: str | None = None
    family: Literal["rectangular_extruded_pad"] = "rectangular_extruded_pad"
    extent_type: str = "finite"


@dataclass(frozen=True)
class RevolvedProfileFeature:
    """Additive or subtractive feature generated by revolving a 2D profile about an axis."""

    id: str
    profile_points: tuple[ProfilePoint2D, ...] = ()
    axis_start: ProfilePoint2D = ProfilePoint2D(0.0, 0.0)
    axis_end: ProfilePoint2D = ProfilePoint2D(0.0, 1.0)
    angle_deg: float = 360.0
    radial_depth_mm: float = 0.0
    profile_height_mm: float = 0.0
    center_x_mm: float = 0.0
    center_y_mm: float = 0.0
    placement_mode: PlacementMode = "face_local_center"
    target_body_id: str = "body.main"
    target_face: str | None = None
    target_selector: str | None = None
    normal_axis: str | None = None
    family: Literal["revolved_profile"] = "revolved_profile"
    extent_type: str = "finite"


@dataclass(frozen=True)
class ProfileCutoutFeature:
    """Subtractive custom closed-polygon pocket or cutout feature."""

    id: str
    profile_points: tuple[ProfilePoint2D, ...] = ()
    center_x_mm: float = 0.0
    center_y_mm: float = 0.0
    placement_mode: PlacementMode = "face_local_center"
    target_body_id: str = "body.main"
    target_face: str | None = None
    target_selector: str | None = None
    normal_axis: str | None = None
    family: Literal["profile_cutout"] = "profile_cutout"
    extent_type: str = "through_all"
    depth_mm: float = 0.0


@dataclass(frozen=True)
class SweepPathSpec:
    """Trajectory specification for swept features."""

    type: Literal["full_circle", "semicircle", "quarter_arc", "straight_line", "none"] = "none"
    radius_mm: float = 0.0
    angle_deg: float = 0.0


@dataclass(frozen=True)
class SweepCrossSectionSpec:
    """Cross-section profile definition for swept features."""

    type: Literal["circle", "rectangle", "polygon"] = "circle"
    diameter_mm: float = 0.0
    width_mm: float = 0.0
    height_mm: float = 0.0
    profile_points: tuple[ProfilePoint2D, ...] = ()
    position: Literal["start", "end"] = "start"


@dataclass(frozen=True)
class SweptProtrusionFeature:
    """Feature generated by sweeping one or more cross sections along a path trajectory."""

    id: str
    path: SweepPathSpec
    cross_sections: tuple[SweepCrossSectionSpec, ...]
    target_face: str | None = None
    target_selector: str | None = None
    target_body_id: str = "body.main"
    placement_mode: PlacementMode = "face_local_center"
    center_x_mm: float = 0.0
    center_y_mm: float = 0.0
    normal_axis: str | None = None
    family: Literal["swept_protrusion"] = "swept_protrusion"
    extent_type: str = "finite"


@dataclass(frozen=True)
class UnsupportedFeature:
    """Placeholder for unmapped or forward-compatible feature definitions."""

    id: str
    family: str


FeaturePlanFeature: TypeAlias = (
    CircularThroughHoleFeature
    | RectangularThroughCutoutFeature
    | SlotThroughCutoutFeature
    | RectangularExtrudedPadFeature
    | RevolvedProfileFeature
    | ProfileCutoutFeature
    | SweptProtrusionFeature
    | UnsupportedFeature
)


@dataclass(frozen=True)
class LoweringStrategy:
    """Execution strategy guiding downstream CAD lowering."""

    status: FeaturePlanStatus = "canonical"
    preferred_current_target: Literal["cad_copilot_lowering"] = "cad_copilot_lowering"
    runtime_route_change: bool = False


@dataclass(frozen=True)
class ArtifactPolicy:
    """Output artifact generation and visibility configuration."""

    step_required: bool = True
    step_visible: bool = True
    par_visible: bool = False
    jpg_required: bool = False
    jpg_visible: bool = True
    step_sanity_runtime_gate: bool = False
    step_sanity_development_signal: bool = True

    def visible_formats(self) -> list[str]:
        formats: list[str] = []
        if self.step_visible:
            formats.append("step")
        if self.jpg_visible:
            formats.append("jpg")
        if self.par_visible:
            formats.append("par")
        return formats


@dataclass(frozen=True)
class FeaturePlan:
    """Root canonical feature plan AST representation."""

    request_id: str
    part: PartMetadata
    base_body: FeaturePlanBaseBody
    primitive_bodies: tuple[FeaturePlanBaseBody, ...] = ()
    boolean_operations: tuple[BooleanOperation, ...] = ()
    features: tuple[FeaturePlanFeature, ...] = ()
    plan_version: str = CANONICAL_PLAN_VERSION
    units: Units = "mm"
    defaults_applied: tuple[DefaultApplied, ...] = ()
    validation_diagnostics: tuple[ValidationDiagnostic, ...] = ()
    lowering_strategy: LoweringStrategy = field(default_factory=LoweringStrategy)
    artifact_policy: ArtifactPolicy = field(default_factory=ArtifactPolicy)


@dataclass(frozen=True)
class FeaturePlanBackendPortRun:
    """Bundle containing validated AST, lowered wire payload, and execution response."""

    validated_plan: FeaturePlan
    lowered_payload: dict[str, Any]
    response: dict[str, Any]


__all__ = [
    "CANONICAL_PLAN_VERSION",
    "DEFAULT_EDGE_MARGIN_MM",
    "ArtifactPolicy",
    "BodyPlacement",
    "BooleanOperation",
    "BooleanOperationFamily",
    "CircularThroughHoleFeature",
    "CylinderBaseBody",
    "DefaultApplied",
    "FeaturePlan",
    "FeaturePlanBackendPortRun",
    "FeaturePlanBaseBody",
    "FeaturePlanFeature",
    "FeaturePlanStatus",
    "FeaturePlanValidationError",
    "LoweringStrategy",
    "PartMetadata",
    "PlacementMode",
    "ProfileCutoutFeature",
    "ProfilePoint2D",
    "RectangularBaseBody",
    "RectangularExtrudedPadFeature",
    "RectangularThroughCutoutFeature",
    "RevolvedProfileFeature",
    "RevolvedShaftBaseBody",
    "SlotOrientationAxis",
    "SlotThroughCutoutFeature",
    "SphereBaseBody",
    "SpurGearBaseBody",
    "SweepCrossSectionSpec",
    "SweepPathSpec",
    "SweptProtrusionFeature",
    "Units",
    "UnsupportedFeature",
    "ValidationDiagnostic",
]
