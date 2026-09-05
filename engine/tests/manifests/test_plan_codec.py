"""Comprehensive unit tests for canonical FeaturePlan codec, metadata hardening, and fingerprints."""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import math
from typing import Any, cast

import pytest
from jsonschema.exceptions import ValidationError

from geometry.plan_models import (
    BodyPlacement,
    BooleanOperation,
    CircularThroughHoleFeature,
    CylinderBaseBody,
    DefaultApplied,
    FeaturePlan,
    LoweringStrategy,
    PartMetadata,
    ProfileCutoutFeature,
    ProfilePoint2D,
    RectangularBaseBody,
    RectangularExtrudedPadFeature,
    RectangularThroughCutoutFeature,
    RevolvedProfileFeature,
    RevolvedShaftBaseBody,
    SlotThroughCutoutFeature,
    SphereBaseBody,
    SpurGearBaseBody,
    SweepCrossSectionSpec,
    SweepPathSpec,
    SweptProtrusionFeature,
    ValidationDiagnostic,
)
from geometry.plan_validation import validate_feature_plan
from manifests import (
    MAX_MANIFEST_DEFAULTS,
    MAX_MANIFEST_DIAGNOSTICS,
    ManifestStableIds,
    ManifestValidationError,
    PreparedManifestData,
    compute_plan_fingerprint,
    extract_manifest_stable_ids,
    generate_structural_design_intent,
    get_run_manifest_validator,
    harden_plan_metadata,
    prepare_manifest_data,
    project_manifest_defaults,
    project_manifest_diagnostics,
    serialize_canonical_feature_plan,
    validate_identifier,
    validate_serialized_feature_plan,
)


def _build_test_plan(
    *,
    base_body: Any = None,
    primitive_bodies: tuple[Any, ...] = (),
    boolean_operations: tuple[BooleanOperation, ...] = (),
    features: tuple[Any, ...] = (),
    request_id: str = "req-test-plan-001",
    design_intent: str = "test intent",
    defaults: tuple[DefaultApplied, ...] = (),
    diagnostics: tuple[ValidationDiagnostic, ...] = (),
) -> FeaturePlan:
    if base_body is None:
        base_body = RectangularBaseBody(
            id="body.main",
            length_mm=100.0,
            width_mm=80.0,
            thickness_mm=20.0,
            placement=BodyPlacement(x_mm=0.0, y_mm=0.0, z_mm=0.0),
            semantic_labels=("plate",),
        )
    return FeaturePlan(
        plan_version="cad_copilot.single_part_feature_plan.v1",
        request_id=request_id,
        part=PartMetadata(part_id="part.main", design_intent=design_intent),
        base_body=base_body,
        primitive_bodies=primitive_bodies,
        boolean_operations=boolean_operations,
        features=features,
        defaults_applied=defaults,
        validation_diagnostics=diagnostics,
        lowering_strategy=LoweringStrategy(),
    )


def test_validate_identifier() -> None:
    assert validate_identifier("test_id", "body.main") == "body.main"
    assert validate_identifier("test_id", "part-01_v2") == "part-01_v2"

    with pytest.raises(ManifestValidationError, match="must be a string"):
        validate_identifier("test_id", cast(Any, 123))

    with pytest.raises(ManifestValidationError, match="length must be between 1 and"):
        validate_identifier("test_id", "")

    with pytest.raises(ManifestValidationError, match="safe identifier pattern"):
        validate_identifier("test_id", "invalid/path")

    with pytest.raises(ManifestValidationError, match="safe identifier pattern"):
        validate_identifier("test_id", ".leading_dot")


def test_generate_structural_design_intent() -> None:
    plan_empty = _build_test_plan()
    summary_empty = generate_structural_design_intent(plan_empty)
    assert summary_empty == "rectangular_prism with 0 features"

    hole = CircularThroughHoleFeature(
        id="feat.hole.1",
        diameter_mm=10.0,
        target_face="+Z",
    )
    cutout = RectangularThroughCutoutFeature(
        id="feat.cutout.1",
        width_mm=20.0,
        height_mm=15.0,
        target_face="+Z",
    )
    plan_features = _build_test_plan(features=(hole, cutout))
    summary_features = generate_structural_design_intent(plan_features)
    assert summary_features == "rectangular_prism with 1 circular_through_hole, 1 rectangular_through_cutout"


def test_harden_plan_metadata_prompt_to_cad() -> None:
    hole = CircularThroughHoleFeature(id="feat.hole.1", diameter_mm=10.0, target_face="+Z")
    plan = _build_test_plan(features=(hole,), design_intent="create a block with a 10mm hole please")
    hardened = harden_plan_metadata(plan, request_kind="prompt_to_cad")

    # Raw prompt must be completely replaced
    assert "please" not in hardened.part.design_intent
    assert "create" not in hardened.part.design_intent
    assert hardened.part.design_intent == "rectangular_prism with 1 circular_through_hole"


@pytest.mark.parametrize(
    "safe_intent",
    [
        "standard bracket example",
        "authoring bracket",
        "authentication fixture",
        "tokenized mounting plate",
        "bracket with token slot",
        "cabinet with secret compartment",
        "passwordless enclosure",
        "authority stamp mounting tab",
    ],
)
def test_harden_plan_metadata_example_plan_safe_intent(safe_intent: str) -> None:
    plan = _build_test_plan(design_intent=safe_intent)
    hardened = harden_plan_metadata(plan, request_kind="example_plan")
    assert hardened.part.design_intent == safe_intent


@pytest.mark.parametrize(
    "cred_intent",
    [
        "test with api_key=sk-1234567890abcdef1234",
        "part with token=xyz123",
        "part with password: hunter2",
        "part with bearer 12345.abc",
        "part with sk-1234567890abcdef1234",
        "part with secret: 123456",
        "part with auth: basic123",
        "part with api_key",
        "part with api-key",
        "part with apikey",
        "part with api key: 123456",
        "part with access_token=abc123",
        "part with client_secret=abc123",
        "part with authorization: Basic abc123",
    ],
)
def test_harden_plan_metadata_example_plan_rejects_credentials(cred_intent: str) -> None:
    plan = _build_test_plan(design_intent=cred_intent)
    with pytest.raises(ManifestValidationError, match="credential-like"):
        harden_plan_metadata(plan, request_kind="example_plan")


def test_harden_plan_metadata_example_plan_rejects_paths_and_ctrl() -> None:
    plan_path = _build_test_plan(design_intent="saved at C:\\Users\\secret\\model.par")
    with pytest.raises(ManifestValidationError, match="local path"):
        harden_plan_metadata(plan_path, request_kind="example_plan")

    plan_ctrl = _build_test_plan(design_intent="test\x00corrupt")
    with pytest.raises(ManifestValidationError, match="control characters"):
        harden_plan_metadata(plan_ctrl, request_kind="example_plan")


def test_serialize_all_base_bodies() -> None:
    # 1. Rectangular prism
    box = RectangularBaseBody(
        id="body.box",
        length_mm=100.0,
        width_mm=50.0,
        thickness_mm=10.0,
        placement=BodyPlacement(x_mm=1.0, y_mm=2.0, z_mm=3.0),
        semantic_labels=("base",),
    )
    plan_box = _build_test_plan(base_body=box)
    s_box = serialize_canonical_feature_plan(plan_box)
    validate_serialized_feature_plan(s_box)
    assert s_box["base_body"]["family"] == "rectangular_prism"
    assert s_box["base_body"]["dimensions_mm"] == {"length_mm": 100.0, "width_mm": 50.0, "thickness_mm": 10.0}

    # 2. Cylinder
    cyl = CylinderBaseBody(
        id="body.cyl",
        radius_mm=25.0,
        height_mm=60.0,
        placement=BodyPlacement(x_mm=0.0, y_mm=0.0, z_mm=0.0),
        semantic_labels=("pin",),
    )
    plan_cyl = _build_test_plan(base_body=cyl)
    s_cyl = serialize_canonical_feature_plan(plan_cyl)
    validate_serialized_feature_plan(s_cyl)
    assert s_cyl["base_body"]["family"] == "cylinder"
    assert s_cyl["base_body"]["dimensions_mm"] == {"radius_mm": 25.0, "height_mm": 60.0}

    # 3. Sphere
    sph = SphereBaseBody(
        id="body.sph",
        radius_mm=30.0,
        placement=BodyPlacement(x_mm=0.0, y_mm=0.0, z_mm=0.0),
        semantic_labels=("ball",),
    )
    plan_sph = _build_test_plan(base_body=sph)
    s_sph = serialize_canonical_feature_plan(plan_sph)
    validate_serialized_feature_plan(s_sph)
    assert s_sph["base_body"]["family"] == "sphere"
    assert s_sph["base_body"]["dimensions_mm"] == {"radius_mm": 30.0}

    # 4. Spur Gear (strict tooth_count integer)
    gear = SpurGearBaseBody(
        id="body.gear",
        tooth_count=24,
        module_mm=2.5,
        face_width_mm=15.0,
        pressure_angle_deg=20.0,
        bore_diameter_mm=12.0,
        placement=BodyPlacement(x_mm=0.0, y_mm=0.0, z_mm=0.0),
        semantic_labels=("spur_gear",),
    )
    plan_gear = _build_test_plan(base_body=gear)
    s_gear = serialize_canonical_feature_plan(plan_gear)
    validate_serialized_feature_plan(s_gear)
    assert s_gear["base_body"]["family"] == "spur_gear"
    assert s_gear["base_body"]["dimensions_mm"]["tooth_count"] == 24
    assert isinstance(s_gear["base_body"]["dimensions_mm"]["tooth_count"], int)
    assert not isinstance(s_gear["base_body"]["dimensions_mm"]["tooth_count"], bool)

    # 5. Revolved Shaft
    shaft = RevolvedShaftBaseBody(
        id="body.shaft",
        radius_mm=20.0,
        height_mm=80.0,
        profile_points=(
            ProfilePoint2D(x_mm=0.0, y_mm=0.0),
            ProfilePoint2D(x_mm=20.0, y_mm=0.0),
            ProfilePoint2D(x_mm=20.0, y_mm=80.0),
            ProfilePoint2D(x_mm=0.0, y_mm=80.0),
        ),
        placement=BodyPlacement(x_mm=0.0, y_mm=0.0, z_mm=0.0),
        semantic_labels=("shaft",),
    )
    plan_shaft = _build_test_plan(base_body=shaft)
    s_shaft = serialize_canonical_feature_plan(plan_shaft)
    validate_serialized_feature_plan(s_shaft)
    assert s_shaft["base_body"]["family"] == "revolved_shaft"
    assert len(s_shaft["base_body"]["profile_points"]) == 4


def test_serialize_all_seven_features() -> None:
    f1 = CircularThroughHoleFeature(
        id="feat.hole",
        diameter_mm=10.0,
        center_x_mm=5.0,
        center_y_mm=-5.0,
        target_face="+Z",
    )
    f2 = RectangularThroughCutoutFeature(
        id="feat.cutout",
        width_mm=20.0,
        height_mm=15.0,
        target_face="+Z",
    )
    f3 = SlotThroughCutoutFeature(
        id="feat.slot",
        length_mm=30.0,
        width_mm=8.0,
        orientation_axis="y",
        target_face="+Z",
    )
    f4 = RectangularExtrudedPadFeature(
        id="feat.pad",
        width_mm=40.0,
        height_mm=25.0,
        distance_mm=10.0,
        target_face="+Z",
    )
    f5 = RevolvedProfileFeature(
        id="feat.revolve",
        target_face="+Z",
        profile_points=(
            ProfilePoint2D(x_mm=5.0, y_mm=0.0),
            ProfilePoint2D(x_mm=10.0, y_mm=0.0),
            ProfilePoint2D(x_mm=10.0, y_mm=10.0),
        ),
        axis_start=ProfilePoint2D(0.0, 0.0),
        axis_end=ProfilePoint2D(0.0, 1.0),
        angle_deg=180.0,
    )
    f6 = ProfileCutoutFeature(
        id="feat.prof_cut",
        target_face="+Z",
        profile_points=(
            ProfilePoint2D(x_mm=0.0, y_mm=0.0),
            ProfilePoint2D(x_mm=10.0, y_mm=0.0),
            ProfilePoint2D(x_mm=5.0, y_mm=10.0),
        ),
        depth_mm=5.0,
    )
    f7 = SweptProtrusionFeature(
        id="feat.sweep",
        target_face="+Z",
        path=SweepPathSpec(type="straight_line", radius_mm=0.0, angle_deg=0.0),
        cross_sections=(
            SweepCrossSectionSpec(
                type="circle",
                diameter_mm=6.0,
                width_mm=0.0,
                height_mm=0.0,
                profile_points=(),
                position="start",
            ),
        ),
    )

    plan = _build_test_plan(features=(f1, f2, f3, f4, f5, f6, f7))
    serialized = serialize_canonical_feature_plan(plan)
    validate_serialized_feature_plan(serialized)
    assert len(serialized["features"]) == 7

    # Assert canonical names
    slot_dict = serialized["features"][2]
    assert "orientation_axis" in slot_dict
    assert "slot_orientation" not in slot_dict

    hole_dict = serialized["features"][0]
    assert "target_face" in hole_dict
    assert "face" not in hole_dict


def test_serialize_csg_boolean_operations() -> None:
    cyl = CylinderBaseBody(id="body.tool", radius_mm=10.0, height_mm=50.0)
    op = BooleanOperation(
        id="op.cut.1",
        operation="subtract",
        target_body_id="body.main",
        tool_body_id="body.tool",
        result_body_id="body.main",
    )
    plan = _build_test_plan(primitive_bodies=(cyl,), boolean_operations=(op,))
    serialized = serialize_canonical_feature_plan(plan)
    validate_serialized_feature_plan(serialized)
    assert len(serialized["boolean_operations"]) == 1
    assert serialized["boolean_operations"][0] == {
        "id": "op.cut.1",
        "operation": "subtract",
        "target_body_id": "body.main",
        "tool_body_id": "body.tool",
        "result_body_id": "body.main",
    }


def test_signed_zero_normalization() -> None:
    box = RectangularBaseBody(
        id="body.main",
        length_mm=100.0,
        width_mm=50.0,
        thickness_mm=10.0,
        placement=BodyPlacement(x_mm=-0.0, y_mm=0.0, z_mm=-0.0),
    )
    hole = CircularThroughHoleFeature(
        id="feat.hole",
        diameter_mm=10.0,
        center_x_mm=-0.0,
        center_y_mm=-0.0,
        target_face="+Z",
    )
    plan = _build_test_plan(base_body=box, features=(hole,))
    serialized = serialize_canonical_feature_plan(plan)

    # Check placement
    assert serialized["base_body"]["placement"]["x_mm"] == 0.0
    assert math.copysign(1.0, serialized["base_body"]["placement"]["x_mm"]) == 1.0
    assert serialized["base_body"]["placement"]["z_mm"] == 0.0
    assert math.copysign(1.0, serialized["base_body"]["placement"]["z_mm"]) == 1.0

    # Check feature coordinates
    assert serialized["features"][0]["center_x_mm"] == 0.0
    assert math.copysign(1.0, serialized["features"][0]["center_x_mm"]) == 1.0


def test_strict_type_checks_reject_bool_in_numeric_fields() -> None:
    # Boolean in integer field
    with pytest.raises(ManifestValidationError, match="strict integer"):
        gear = SpurGearBaseBody(
            id="body.gear",
            tooth_count=cast(Any, True),
            module_mm=2.0,
            face_width_mm=10.0,
        )
        serialize_canonical_feature_plan(_build_test_plan(base_body=gear))

    # Boolean in float field
    with pytest.raises(ManifestValidationError, match="numeric float"):
        box = RectangularBaseBody(
            id="body.main",
            length_mm=cast(Any, False),
            width_mm=50.0,
            thickness_mm=10.0,
        )
        serialize_canonical_feature_plan(_build_test_plan(base_body=box))


def test_strict_float_rejects_nan_and_inf() -> None:
    with pytest.raises(ManifestValidationError, match="finite number"):
        box = RectangularBaseBody(
            id="body.main",
            length_mm=float("nan"),
            width_mm=50.0,
            thickness_mm=10.0,
        )
        serialize_canonical_feature_plan(_build_test_plan(base_body=box))

    with pytest.raises(ManifestValidationError, match="finite number"):
        box = RectangularBaseBody(
            id="body.main",
            length_mm=float("inf"),
            width_mm=50.0,
            thickness_mm=10.0,
        )
        serialize_canonical_feature_plan(_build_test_plan(base_body=box))


def test_extract_manifest_stable_ids() -> None:
    cyl1 = CylinderBaseBody(id="body.tool1", radius_mm=5.0, height_mm=10.0)
    cyl2 = CylinderBaseBody(id="body.tool2", radius_mm=8.0, height_mm=20.0)
    op1 = BooleanOperation(
        id="op.1",
        operation="subtract",
        target_body_id="body.main",
        tool_body_id="body.tool1",
        result_body_id="body.main",
    )
    f1 = CircularThroughHoleFeature(id="feat.1", diameter_mm=10.0, target_face="+Z")
    f2 = RectangularThroughCutoutFeature(id="feat.2", width_mm=10.0, height_mm=10.0, target_face="+Z")

    plan = _build_test_plan(
        primitive_bodies=(cyl1, cyl2),
        boolean_operations=(op1,),
        features=(f1, f2),
    )
    sids = extract_manifest_stable_ids(plan)
    assert sids.part_id == "part.main"
    assert sids.body_ids == ("body.main", "body.tool1", "body.tool2")
    assert sids.boolean_operation_ids == ("op.1",)
    assert sids.feature_ids == ("feat.1", "feat.2")


def test_compute_plan_fingerprint() -> None:
    plan = _build_test_plan()
    serialized = serialize_canonical_feature_plan(plan)
    fp1 = compute_plan_fingerprint(serialized)
    fp2 = compute_plan_fingerprint(serialized)
    assert fp1 == fp2
    assert len(fp1) == 64

    # Modifying coordinate changes hash
    mutated = copy.deepcopy(serialized)
    mutated["base_body"]["dimensions_mm"]["length_mm"] = 105.0
    fp_mut = compute_plan_fingerprint(mutated)
    assert fp1 != fp_mut


def test_project_manifest_defaults_and_diagnostics() -> None:
    defaults = (
        DefaultApplied(path="features[0].extent_type", value="through_all", reason="missing_extent"),
        DefaultApplied(path="features[0].depth_mm", value=0.0, reason="default_depth"),
        DefaultApplied(
            path="features[0].fallback",
            value="fallback_val",
            reason="unsafe_input",
            original_value="user raw string with spaces & secrets",
        ),
    )
    projected_defs = project_manifest_defaults(defaults)
    assert len(projected_defs) == 3
    assert projected_defs[0]["path"] == "features[0].extent_type"
    assert projected_defs[0]["value"] == "through_all"
    assert projected_defs[0]["reason"] == "missing_extent"

    # Redaction of raw unsafe original_value strings
    assert projected_defs[2]["path"] == "features[0].fallback"
    assert projected_defs[2]["value"] == "fallback_val"
    assert projected_defs[2]["reason"] == "default_applied"
    assert isinstance(projected_defs[2]["original_value"], dict)
    assert projected_defs[2]["original_value"]["redacted"] is True
    assert "sha256" in projected_defs[2]["original_value"]

    diagnostics = (
        ValidationDiagnostic(severity="warning", code="BORDERLINE_FIT", message="Hole near edge", path="features[0]"),
        ValidationDiagnostic(severity="info", code="INFO_CODE", message="Notice message", path=None),
    )
    projected_diags = project_manifest_diagnostics(diagnostics)
    assert len(projected_diags) == 2
    assert projected_diags[0]["severity"] == "warning"
    assert projected_diags[0]["code"] == "BORDERLINE_FIT"
    assert projected_diags[0]["path"] == "features[0]"
    # Masks raw validator message with safe allowlisted message
    assert projected_diags[0]["message"] == "Feature profile placement is close to the allowable boundary."

    # Path None defaulted to "root" and unknown code maps to generic message
    assert projected_diags[1]["path"] == "root"
    assert projected_diags[1]["message"] == "A non-fatal diagnostic warning was recorded."


def test_prepare_manifest_data_prompt_to_cad() -> None:
    hole = CircularThroughHoleFeature(id="feat.hole.1", diameter_mm=10.0, target_face="+Z")
    plan = _build_test_plan(features=(hole,), design_intent="create plate with 1 hole")
    prompt_digest = hashlib.sha256(b"create plate with 1 hole").hexdigest()
    prep = prepare_manifest_data(
        plan,
        provenance_kind="ai_proposal",
        source_id="model.gemini",
        gate_mode="capability_first",
        request_kind="prompt_to_cad",
        prompt_sha256=prompt_digest,
    )
    assert isinstance(prep, PreparedManifestData)
    assert prep.provenance_kind == "ai_proposal"
    assert prep.request_kind == "prompt_to_cad"
    assert prep.prompt_sha256 is not None
    assert prep.prompt_sha256 == prompt_digest
    assert prep.stable_ids.part_id == "part.main"
    assert prep.stable_ids.feature_ids == ("feat.hole.1",)
    assert prep.feature_plan["part"]["design_intent"] == "rectangular_prism with 1 circular_through_hole"


def test_prepare_manifest_data_example_plan() -> None:
    plan = _build_test_plan(design_intent="standard bracket example")
    prep = prepare_manifest_data(
        plan,
        provenance_kind="example_plan",
        source_id="example.bracket",
        gate_mode="capability_first",
        request_kind="example_plan",
        prompt_sha256=None,
    )
    assert isinstance(prep, PreparedManifestData)
    assert prep.provenance_kind == "example_plan"
    assert prep.request_kind == "example_plan"
    assert prep.prompt_sha256 is None
    assert prep.feature_plan["part"]["design_intent"] == "standard bracket example"


def test_unc_path_rejection_in_persisted_intent() -> None:
    plan = _build_test_plan(design_intent=r"\\server\share\model.par")
    with pytest.raises(ManifestValidationError, match="local path"):
        harden_plan_metadata(plan, request_kind="example_plan")


def test_extract_manifest_stable_ids_with_distinct_boolean_result_body() -> None:
    cyl1 = CylinderBaseBody(id="body.tool1", radius_mm=5.0, height_mm=10.0)
    op = BooleanOperation(
        id="op.cut",
        operation="subtract",
        target_body_id="body.main",
        tool_body_id="body.tool1",
        result_body_id="body.result.distinct",
    )
    plan = _build_test_plan(
        primitive_bodies=(cyl1,),
        boolean_operations=(op,),
    )
    sids = extract_manifest_stable_ids(plan)
    assert sids.body_ids == ("body.main", "body.tool1", "body.result.distinct")
    assert sids.boolean_operation_ids == ("op.cut",)


def test_serialize_canonical_feature_plan_rejects_runtime_route_change() -> None:
    plan = _build_test_plan()
    object.__setattr__(plan.lowering_strategy, "runtime_route_change", True)
    with pytest.raises(ManifestValidationError, match="runtime_route_change must be False"):
        serialize_canonical_feature_plan(plan)


def test_diagnostic_message_masks_all_raw_validator_messages() -> None:
    sensitive_path = r"C:\Users\Admin\Documents\confidential\design.par"
    secret_token = "Bearer secret-token-xyz-12345"
    diags = (
        ValidationDiagnostic(
            severity="error",
            code="INVALID_DIMENSION",
            message=f"Dimension error for file {sensitive_path}",
            path="base_body.length_mm",
        ),
        ValidationDiagnostic(
            severity="warning",
            code="UNKNOWN_SYNTHETIC_CODE_999",
            message=f"Synthetic message containing {secret_token}",
            path="features[0]",
        ),
    )
    projected = project_manifest_diagnostics(diags)
    assert len(projected) == 2

    assert projected[0]["code"] == "INVALID_DIMENSION"
    assert projected[0]["message"] == "Dimension value violates boundary constraints."
    assert sensitive_path not in projected[0]["message"]

    assert projected[1]["code"] == "UNKNOWN_SYNTHETIC_CODE_999"
    assert projected[1]["message"] == "A non-fatal diagnostic warning was recorded."
    assert secret_token not in projected[1]["message"]


def test_defaults_projection_redacts_credentials_and_rejects_invalid_inputs() -> None:
    token_def = DefaultApplied(
        path="features[0].extent_type",
        value="through_all",
        reason="missing_extent",
        original_value="sk-1234567890abcdef1234",
    )
    unc_orig_def = DefaultApplied(
        path="features[0].depth_mm",
        value=10.0,
        reason="default_depth",
        original_value=r"\\corp\share\file.step",
    )
    projected = project_manifest_defaults((token_def, unc_orig_def))
    assert len(projected) == 2
    assert projected[0]["original_value"] == {
        "redacted": True,
        "sha256": hashlib.sha256(b'"sk-1234567890abcdef1234"').hexdigest(),
    }
    assert projected[1]["original_value"]["redacted"] is True
    assert "sha256" in projected[1]["original_value"]

    # Invalid path raises ManifestValidationError
    with pytest.raises(ManifestValidationError, match="Invalid default path"):
        project_manifest_defaults((DefaultApplied(path="invalid path with spaces", value=1.0, reason="default_depth"),))

    # Path containing credential raises ManifestValidationError with forbidden pattern
    with pytest.raises(ManifestValidationError, match="forbidden pattern"):
        project_manifest_defaults((DefaultApplied(path="features[0].api_key", value=1.0, reason="default_depth"),))

    # Non-finite values raise ManifestValidationError
    with pytest.raises(ManifestValidationError, match="non-finite float"):
        project_manifest_defaults(
            (DefaultApplied(path="features[0].depth_mm", value=float("nan"), reason="default_depth"),)
        )

    with pytest.raises(ManifestValidationError, match="non-finite float"):
        project_manifest_defaults(
            (DefaultApplied(path="features[0].depth_mm", value=float("inf"), reason="default_depth"),)
        )


def test_prepare_manifest_data_unconditional_engine_version_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    import manifests.plan_codec

    monkeypatch.setattr(manifests.plan_codec, "resolve_engine_version", lambda: "1.2.3")
    hole = CircularThroughHoleFeature(id="feat.hole.1", diameter_mm=10.0, target_face="+Z")
    plan = _build_test_plan(features=(hole,), design_intent="create plate with 1 hole")
    prep = prepare_manifest_data(
        plan,
        provenance_kind="ai_proposal",
        source_id="model.gemini",
        gate_mode="capability_first",
        request_kind="prompt_to_cad",
        prompt_sha256=hashlib.sha256(b"create plate with 1 hole").hexdigest(),
    )
    assert prep.engine_version == "1.2.3"


def test_project_manifest_defaults_structured_and_integer_preservation() -> None:
    # 1. Strict integer preservation and float normalization in arrays
    array_def = DefaultApplied(
        path="features[0].radii",
        value=[1, 2.5, -0.0],
        reason="default_applied",
        original_value=[1, 2.5, -0.0],
    )
    projected = project_manifest_defaults((array_def,))
    assert len(projected) == 1
    # Check value
    assert projected[0]["value"] == [1, 2.5, 0.0]
    assert type(projected[0]["value"][0]) is int
    assert not isinstance(projected[0]["value"][0], bool)
    assert type(projected[0]["value"][1]) is float
    assert type(projected[0]["value"][2]) is float
    assert math.copysign(1.0, projected[0]["value"][2]) == 1.0

    # Check original_value
    assert projected[0]["original_value"] == [1, 2.5, 0.0]
    assert type(projected[0]["original_value"][0]) is int
    assert not isinstance(projected[0]["original_value"][0], bool)
    assert type(projected[0]["original_value"][1]) is float
    assert type(projected[0]["original_value"][2]) is float
    assert math.copysign(1.0, projected[0]["original_value"][2]) == 1.0

    # Reject booleans inside default numeric arrays
    with pytest.raises(ManifestValidationError, match="boolean, expected number or mapping"):
        project_manifest_defaults((DefaultApplied(path="features[0].dims", value=[1, True], reason="default_applied"),))

    # Booleans in original_value arrays cause redaction
    bool_orig_proj = project_manifest_defaults(
        (DefaultApplied(path="features[0].dims", value=[1, 2], reason="default_applied", original_value=[1, True]),)
    )
    assert isinstance(bool_orig_proj[0]["original_value"], dict)
    assert bool_orig_proj[0]["original_value"]["redacted"] is True

    # 2. Structured defaults emitted by revolved geometry
    # a. revolved_shaft_profile_normalization
    shaft_def = DefaultApplied(
        path="base_body.profile_points",
        value=[{"x_mm": 0.0, "y_mm": 0.0}, {"x_mm": 15.0, "y_mm": 30.0}],
        reason="revolved_shaft_profile_normalization",
        original_value=[{"x_mm": -2.0, "y_mm": 30.0}, {"x_mm": 0.0, "y_mm": 0.0}],
    )

    # b. revolved_profile_axis_span
    axis_def = DefaultApplied(
        path="features[0].revolve.axis",
        value={
            "start": {"x_mm": 0.0, "y_mm": 10.0},
            "end": {"x_mm": 50.0, "y_mm": 10.0},
        },
        reason="revolved_profile_axis_span",
    )

    # c. revolved_profile_dimensions
    dims_def = DefaultApplied(
        path="features[0].profile",
        value={
            "points": [{"x_mm": 0.0, "y_mm": 0.0}, {"x_mm": 20.0, "y_mm": 30.0}],
            "axis": {
                "start": {"x_mm": 0.0, "y_mm": 0.0},
                "end": {"x_mm": 0.0, "y_mm": 30.0},
            },
        },
        reason="revolved_profile_dimensions",
    )

    structured_projected = project_manifest_defaults((shaft_def, axis_def, dims_def))
    assert len(structured_projected) == 3
    assert structured_projected[0]["path"] == "base_body.profile_points"
    assert structured_projected[0]["value"] == [{"x_mm": 0.0, "y_mm": 0.0}, {"x_mm": 15.0, "y_mm": 30.0}]
    assert structured_projected[0]["original_value"] == [{"x_mm": -2.0, "y_mm": 30.0}, {"x_mm": 0.0, "y_mm": 0.0}]
    assert structured_projected[0]["reason"] == "revolved_shaft_profile_normalization"

    assert structured_projected[1]["path"] == "features[0].revolve.axis"
    assert structured_projected[1]["value"] == {
        "start": {"x_mm": 0.0, "y_mm": 10.0},
        "end": {"x_mm": 50.0, "y_mm": 10.0},
    }
    assert structured_projected[1]["reason"] == "revolved_profile_axis_span"

    assert structured_projected[2]["path"] == "features[0].profile"
    assert structured_projected[2]["value"] == {
        "points": [{"x_mm": 0.0, "y_mm": 0.0}, {"x_mm": 20.0, "y_mm": 30.0}],
        "axis": {
            "start": {"x_mm": 0.0, "y_mm": 0.0},
            "end": {"x_mm": 0.0, "y_mm": 30.0},
        },
    }
    assert structured_projected[2]["reason"] == "revolved_profile_dimensions"

    # End-to-end integration: Validated revolved-shaft plan with negative radii normalization
    raw_shaft_body = RevolvedShaftBaseBody(
        id="body.shaft",
        radius_mm=20.0,
        height_mm=50.0,
        profile_points=(
            ProfilePoint2D(x_mm=-2.0, y_mm=50.0),
            ProfilePoint2D(x_mm=10.0, y_mm=20.0),
            ProfilePoint2D(x_mm=0.0, y_mm=0.0),
        ),
    )
    raw_shaft_plan = _build_test_plan(base_body=raw_shaft_body)
    validated_shaft_plan = validate_feature_plan(raw_shaft_plan)
    assert any(d.reason == "revolved_shaft_profile_normalization" for d in validated_shaft_plan.defaults_applied)

    # prepare_manifest_data must succeed and manifest the plan without error
    prep = prepare_manifest_data(
        validated_shaft_plan,
        provenance_kind="ai_proposal",
        source_id="model.gemini",
        gate_mode="capability_first",
        request_kind="prompt_to_cad",
        prompt_sha256="0" * 64,
    )
    assert prep.defaults_applied[0]["reason"] == "revolved_shaft_profile_normalization"
    assert prep.defaults_applied[0]["value"] == [
        {"x_mm": 0.0, "y_mm": 0.0},
        {"x_mm": 10.0, "y_mm": 20.0},
        {"x_mm": 0.0, "y_mm": 50.0},
    ]


def test_structured_defaults_512_bound_and_privacy_guarantees() -> None:
    # 1. Revolved profile points array supporting up to 512 points
    points_65 = [{"x_mm": float(i), "y_mm": float(i * 2)} for i in range(65)]
    def_65 = DefaultApplied(
        path="base_body.profile_points",
        value=points_65,
        reason="revolved_shaft_profile_normalization",
        original_value=points_65,
    )
    projected = project_manifest_defaults((def_65,))
    assert len(projected) == 1
    assert len(projected[0]["value"]) == 65
    assert len(projected[0]["original_value"]) == 65

    # Exactly 512 points is allowed
    points_512 = [{"x_mm": float(i), "y_mm": float(i * 2)} for i in range(512)]
    def_512 = DefaultApplied(
        path="base_body.profile_points",
        value=points_512,
        reason="revolved_shaft_profile_normalization",
    )
    projected_512 = project_manifest_defaults((def_512,))
    assert len(projected_512[0]["value"]) == 512

    # 513 points exceeds maxItems 512
    points_513 = [{"x_mm": float(i), "y_mm": float(i * 2)} for i in range(513)]
    def_513 = DefaultApplied(
        path="base_body.profile_points",
        value=points_513,
        reason="revolved_shaft_profile_normalization",
    )
    with pytest.raises(ManifestValidationError, match="exceeds maxItems 512"):
        project_manifest_defaults((def_513,))

    # 2. Schema-level validation rejects arbitrary nested strings, credentials, and paths
    validator = get_run_manifest_validator()
    from tests.manifests.conftest import build_valid_golden_manifest

    # Probe 1: Nested credential in value
    m_probe1 = build_valid_golden_manifest()
    m_probe1["defaults_applied"] = [
        {
            "path": "features[0].dims",
            "value": {"secret": "sk-1234567890abcdef12345678"},
            "reason": "default_applied",
        }
    ]
    with pytest.raises(ValidationError):
        validator.validate(m_probe1)

    # Probe 2: Nested local path in value
    m_probe2 = build_valid_golden_manifest()
    m_probe2["defaults_applied"] = [
        {
            "path": "features[0].dims",
            "value": {"file": "C:/Users/Admin/secrets.step"},
            "reason": "default_applied",
        }
    ]
    with pytest.raises(ValidationError):
        validator.validate(m_probe2)

    # Probe 3: Array with nested credential object
    m_probe3 = build_valid_golden_manifest()
    m_probe3["defaults_applied"] = [
        {
            "path": "features[0].dims",
            "value": [{"token": "sk-1234567890abcdef12345678"}],
            "reason": "default_applied",
        }
    ]
    with pytest.raises(ValidationError):
        validator.validate(m_probe3)

    # Probe 4: original_value unredacted object with arbitrary fields
    m_probe4 = build_valid_golden_manifest()
    m_probe4["defaults_applied"] = [
        {
            "path": "features[0].dims",
            "value": 1.0,
            "reason": "default_applied",
            "original_value": {"token": "sk-1234567890abcdef12345678", "extra": "leak"},
        }
    ]
    with pytest.raises(ValidationError):
        validator.validate(m_probe4)

    # Probe 5: Projector itself rejects nested strings/credentials/booleans/unknown keys
    with pytest.raises(ManifestValidationError, match="unrecognized geometry keys"):
        project_manifest_defaults(
            (DefaultApplied(path="features[0].dims", value={"token": "sk-12345"}, reason="default_applied"),)
        )

    with pytest.raises(ManifestValidationError, match="unrecognized geometry keys"):
        project_manifest_defaults(
            (DefaultApplied(path="features[0].dims", value={"flag": True}, reason="default_applied"),)
        )

    with pytest.raises(ManifestValidationError, match="not permitted in nested structures"):
        project_manifest_defaults(
            (
                DefaultApplied(
                    path="features[0].dims", value={"x_mm": "sk-12345", "y_mm": 0.0}, reason="default_applied"
                ),
            )
        )

    with pytest.raises(ManifestValidationError, match="not permitted in nested structures"):
        project_manifest_defaults(
            (DefaultApplied(path="features[0].dims", value={"x_mm": True, "y_mm": 0.0}, reason="default_applied"),)
        )

    # Probe 6: Valid redaction-object shape in original_value validates cleanly
    m_valid_redaction = build_valid_golden_manifest()
    m_valid_redaction["defaults_applied"] = [
        {
            "path": "features[0].dims",
            "value": 1.0,
            "reason": "default_applied",
            "original_value": {"redacted": True, "sha256": "0" * 64},
        }
    ]
    validator.validate(m_valid_redaction)

    # 3. Numeric credential maps regression cases (password, api_key, token)
    for cred_key in ("password", "api_key", "token", "secret", "auth"):
        # Projector redacts credential-bearing numeric maps in original_value
        cred_orig_proj = project_manifest_defaults(
            (
                DefaultApplied(
                    path="features[0].dims",
                    value=1.0,
                    reason="default_applied",
                    original_value={cred_key: 123456},
                ),
            )
        )
        assert isinstance(cred_orig_proj[0]["original_value"], dict)
        assert cred_orig_proj[0]["original_value"]["redacted"] is True
        assert "sha256" in cred_orig_proj[0]["original_value"]

        # Projector rejects credential-bearing numeric maps in value
        with pytest.raises(ManifestValidationError, match=r"unrecognized geometry keys|forbidden credential"):
            project_manifest_defaults(
                (
                    DefaultApplied(
                        path="features[0].dims",
                        value={cred_key: 123456},
                        reason="default_applied",
                    ),
                )
            )

        # Schema validator rejects unredacted numeric credential maps in original_value
        m_cred_orig = build_valid_golden_manifest()
        m_cred_orig["defaults_applied"] = [
            {
                "path": "features[0].dims",
                "value": 1.0,
                "reason": "default_applied",
                "original_value": {cred_key: 123456},
            }
        ]
        with pytest.raises(ValidationError):
            validator.validate(m_cred_orig)

        # Schema validator rejects numeric credential maps in value
        m_cred_val = build_valid_golden_manifest()
        m_cred_val["defaults_applied"] = [
            {
                "path": "features[0].dims",
                "value": {cred_key: 123456},
                "reason": "default_applied",
            }
        ]
        with pytest.raises(ValidationError):
            validator.validate(m_cred_val)


def test_defaults_projection_avoids_substring_false_positives() -> None:
    # 1. Innocent words with substrings are preserved in original_value
    innocent_tokens = ("authoring", "tokenized", "authentication", "passwordless")
    for token in innocent_tokens:
        proj = project_manifest_defaults(
            (
                DefaultApplied(
                    path="features[0].extent_type",
                    value="through_all",
                    reason="default_applied",
                    original_value=token,
                ),
            )
        )
        assert proj[0]["original_value"] == token

    # 2. Innocent words are accepted in default value and path
    proj_innocent = project_manifest_defaults(
        (
            DefaultApplied(
                path="features[0].authoring_status",
                value="authoring",
                reason="default_applied",
            ),
        )
    )
    assert proj_innocent[0]["value"] == "authoring"
    assert proj_innocent[0]["path"] == "features[0].authoring_status"

    # 3. Sensitive tokens in original_value are redacted
    sensitive_tokens = ("password", "api_key", "token", "secret", "auth")
    for token in sensitive_tokens:
        proj_sens = project_manifest_defaults(
            (
                DefaultApplied(
                    path="features[0].extent_type",
                    value="through_all",
                    reason="default_applied",
                    original_value=token,
                ),
            )
        )
        assert isinstance(proj_sens[0]["original_value"], dict)
        assert proj_sens[0]["original_value"]["redacted"] is True

    # 4. Sensitive tokens in value are rejected
    for token in sensitive_tokens:
        with pytest.raises(ManifestValidationError, match="forbidden credential"):
            project_manifest_defaults(
                (
                    DefaultApplied(
                        path="features[0].extent_type",
                        value=token,
                        reason="default_applied",
                    ),
                )
            )

    # 5. Sensitive tokens in path are rejected
    for token in ("password", "token", "secret", "auth", "api_key"):
        with pytest.raises(ManifestValidationError, match="forbidden pattern"):
            project_manifest_defaults(
                (
                    DefaultApplied(
                        path=f"features[0].{token}",
                        value="through_all",
                        reason="default_applied",
                    ),
                )
            )


def test_screen_identifiers_and_semantic_labels_rejects_credentials_and_paths() -> None:
    # 1. request_id containing credential pattern
    plan_bad_req = _build_test_plan(request_id="sk-1234567890abcdef1234")
    with pytest.raises(ManifestValidationError, match="forbidden credential or path"):
        harden_plan_metadata(plan_bad_req, request_kind="prompt_to_cad")

    # 2. part_id containing credential pattern
    plan_bad_part = _build_test_plan()
    plan_bad_part = dataclasses.replace(
        plan_bad_part, part=dataclasses.replace(plan_bad_part.part, part_id="sk-1234567890abcdef1234")
    )
    with pytest.raises(ManifestValidationError, match="forbidden credential or path"):
        harden_plan_metadata(plan_bad_part, request_kind="prompt_to_cad")

    # 3. body_id containing credential pattern
    bad_body = dataclasses.replace(plan_bad_part.base_body, id="sk-1234567890abcdef1234")
    plan_bad_body = dataclasses.replace(plan_bad_part, base_body=bad_body)
    with pytest.raises(ManifestValidationError, match="forbidden credential or path"):
        harden_plan_metadata(plan_bad_body, request_kind="prompt_to_cad")

    # 4. semantic_labels containing credential pattern
    bad_labels_body = dataclasses.replace(plan_bad_part.base_body, semantic_labels=("sk-1234567890abcdef1234",))
    plan_bad_label = dataclasses.replace(plan_bad_part, base_body=bad_labels_body)
    with pytest.raises(ManifestValidationError, match="forbidden credential or path"):
        harden_plan_metadata(plan_bad_label, request_kind="prompt_to_cad")

    # 5. primitive body id and label containing credential pattern
    bad_pb = CylinderBaseBody(
        id="sk-1234567890abcdef1234",
        radius_mm=10.0,
        height_mm=20.0,
        placement=BodyPlacement(x_mm=0.0, y_mm=0.0, z_mm=0.0),
        semantic_labels=("tool",),
    )
    plan_bad_pb = _build_test_plan(primitive_bodies=(bad_pb,))
    with pytest.raises(ManifestValidationError, match="forbidden credential or path"):
        harden_plan_metadata(plan_bad_pb, request_kind="prompt_to_cad")

    # 6. source_id containing credential pattern in prepare_manifest_data
    valid_plan = _build_test_plan()
    with pytest.raises(ManifestValidationError, match="forbidden credential or path"):
        prepare_manifest_data(
            valid_plan,
            provenance_kind="ai_proposal",
            source_id="sk-1234567890abcdef1234",
            gate_mode="capability_first",
            request_kind="prompt_to_cad",
            prompt_sha256="0" * 64,
        )

    # 7. aiza credential format rejected
    with pytest.raises(ManifestValidationError, match="forbidden credential or path"):
        validate_identifier("body.id", "aizaSyD1234567890abcdef")


def test_screen_identifiers_accepts_legitimate_cad_names() -> None:
    """Verify validate_identifier accepts legitimate CAD semantic names containing token/auth/secret words."""
    safe_names = (
        "feature.token-slot",
        "body.authorization-cover",
        "secret-compartment",
        "auth-plate",
        "token-retention-bracket",
        "body.password-keyway",
        "mounting-plate.token-guide",
        "auth-locking-pin",
    )
    for name in safe_names:
        assert validate_identifier("identifier", name) == name

    # Build plan using legitimate CAD identifiers across part, body, and feature
    legit_body = RectangularBaseBody(
        id="body.authorization-cover",
        length_mm=100.0,
        width_mm=80.0,
        thickness_mm=20.0,
        placement=BodyPlacement(x_mm=0.0, y_mm=0.0, z_mm=0.0),
        semantic_labels=("secret-compartment", "auth-plate"),
    )
    legit_hole = CircularThroughHoleFeature(
        id="feature.token-slot",
        target_body_id="body.authorization-cover",
        diameter_mm=6.0,
        target_face="+Z",
    )
    plan = _build_test_plan(
        request_id="req-legit-cad-names-001",
        base_body=legit_body,
        features=(legit_hole,),
    )
    hardened = harden_plan_metadata(plan, request_kind="example_plan")
    assert hardened.base_body.id == "body.authorization-cover"
    assert hardened.features[0].id == "feature.token-slot"
    assert hardened.base_body.semantic_labels == ("secret-compartment", "auth-plate")


def test_sanitize_diagnostic_code_and_path() -> None:
    # A diagnostic with credential in code and path is sanitized to safe fallbacks
    diag_leak = ValidationDiagnostic(
        severity="warning",
        code="sk-1234567890abcdef1234",
        message="A raw internal secret should not leak.",
        path="sk-1234567890abcdef1234",
    )
    projected = project_manifest_diagnostics((diag_leak,))
    assert len(projected) == 1
    assert projected[0]["code"] == "DIAGNOSTIC"
    assert projected[0]["path"] == "root"
    assert projected[0]["message"] == "A non-fatal diagnostic warning was recorded."

    # A diagnostic with local path in path is sanitized to root
    diag_path = ValidationDiagnostic(
        severity="error",
        code="INVALID_GEOMETRY",
        message="Failed file",
        path=r"C:\Users\Admin\secret.step",
    )
    projected_path = project_manifest_diagnostics((diag_path,))
    assert projected_path[0]["code"] == "INVALID_GEOMETRY"
    assert projected_path[0]["path"] == "root"
    assert projected_path[0]["message"] == "Geometry definition is degenerate or self-intersecting."


def test_bound_defaults_and_diagnostics_collections() -> None:
    # 8192 defaults is accepted, 8193 is rejected
    def_single = DefaultApplied(path="features[0].extent_type", value="through_all", reason="default_applied")
    defs_8192 = tuple(def_single for _ in range(MAX_MANIFEST_DEFAULTS))
    projected_defs = project_manifest_defaults(defs_8192)
    assert len(projected_defs) == 8192

    defs_8193 = tuple(def_single for _ in range(MAX_MANIFEST_DEFAULTS + 1))
    with pytest.raises(ManifestValidationError, match="exceeds maximum allowed count of 8192"):
        project_manifest_defaults(defs_8193)

    # 8192 diagnostics is accepted, 8193 is rejected
    diag_single = ValidationDiagnostic(severity="warning", code="BORDERLINE_FIT", message="Close to boundary")
    diags_8192 = tuple(diag_single for _ in range(MAX_MANIFEST_DIAGNOSTICS))
    projected_diags = project_manifest_diagnostics(diags_8192)
    assert len(projected_diags) == 8192

    diags_8193 = tuple(diag_single for _ in range(MAX_MANIFEST_DIAGNOSTICS + 1))
    with pytest.raises(ManifestValidationError, match="exceeds maximum allowed count of 8192"):
        project_manifest_diagnostics(diags_8193)

    # Schema-level enforcement
    validator = get_run_manifest_validator()
    from tests.manifests.conftest import build_valid_golden_manifest

    m_over_defs = build_valid_golden_manifest()
    m_over_defs["defaults_applied"] = [
        {"path": "features[0].extent_type", "value": "through_all", "reason": "default_applied"} for _ in range(8193)
    ]
    with pytest.raises(ValidationError):
        validator.validate(m_over_defs)

    m_over_diags = build_valid_golden_manifest()
    m_over_diags["diagnostics"] = [
        {"severity": "warning", "code": "BORDERLINE_FIT", "message": "Close"} for _ in range(8193)
    ]
    with pytest.raises(ValidationError):
        validator.validate(m_over_diags)


def test_production_sweep_boundary_collections_project_cleanly() -> None:
    """Verify that a plan with MAX_FEATURES sweep features each having MAX_SWEEP_CROSS_SECTIONS cross-sections produces >512 defaults/diagnostics and projects cleanly."""
    from geometry.plan_parser import (
        MAX_FEATURES,
        MAX_SWEEP_CROSS_SECTIONS,
        feature_plan_from_dict,
    )
    from tests.manifests.conftest import build_valid_golden_manifest

    # Build a production-like raw payload with MAX_FEATURES sweep features, each having MAX_SWEEP_CROSS_SECTIONS cross-sections
    features_raw = []
    for f_idx in range(MAX_FEATURES):
        features_raw.append(
            {
                "id": f"feat.sweep.{f_idx}",
                "family": "swept_protrusion",
                "target": {"body_id": "body.main", "face": {"resolved_face": "unknown_face_alias"}},
                "placement": {"mode": "unknown_placement_mode"},
                "path": {"type": "unknown_sweep_path", "radius_mm": 50.0, "angle_deg": 90.0},
                "cross_sections": [
                    {
                        "type": "custom_profile_type",
                        "position": "section_midpoint",
                        "diameter_mm": 4.0,
                    }
                    for _ in range(MAX_SWEEP_CROSS_SECTIONS)
                ],
            }
        )

    payload = {
        "request_id": "req-sweep-prod-boundary-001",
        "plan_version": "cad_copilot.single_part_feature_plan.v1",
        "part": {"part_id": "part.main", "design_intent": f"{MAX_FEATURES} swept ribs boundary test"},
        "base_body": {
            "id": "body.main",
            "family": "rectangular_prism",
            "dimensions_mm": {"length_mm": 200.0, "width_mm": 200.0, "thickness_mm": 50.0},
        },
        "features": features_raw,
    }

    plan = feature_plan_from_dict(payload)
    plan = validate_feature_plan(plan, mode="capability_first")

    # Authoritative maxima probe yields 4,736 defaults and 4,608 diagnostics (>512 and <= 8192)
    assert len(plan.defaults_applied) > 512
    assert len(plan.validation_diagnostics) > 512
    assert len(plan.defaults_applied) <= MAX_MANIFEST_DEFAULTS
    assert len(plan.validation_diagnostics) <= MAX_MANIFEST_DIAGNOSTICS

    # Codec projects defaults and diagnostics without error
    projected_defs = project_manifest_defaults(plan.defaults_applied)
    projected_diags = project_manifest_diagnostics(plan.validation_diagnostics)
    assert len(projected_defs) == len(plan.defaults_applied)
    assert len(projected_diags) == len(plan.validation_diagnostics)

    # Full serialized manifest passes schema validation with large collections
    prep = prepare_manifest_data(
        plan,
        provenance_kind="example_plan",
        source_id="example.sweep.boundary",
        gate_mode="capability_first",
        request_kind="example_plan",
    )
    manifest_dict = build_valid_golden_manifest()
    manifest_dict["request"]["request_id"] = prep.feature_plan["request_id"]
    manifest_dict["feature_plan"] = prep.feature_plan
    manifest_dict["fingerprints"]["plan_sha256"] = prep.plan_sha256
    manifest_dict["stable_ids"] = {
        "part_id": prep.stable_ids.part_id,
        "body_ids": list(prep.stable_ids.body_ids),
        "boolean_operation_ids": list(prep.stable_ids.boolean_operation_ids),
        "feature_ids": list(prep.stable_ids.feature_ids),
    }
    manifest_dict["defaults_applied"] = list(prep.defaults_applied)
    manifest_dict["diagnostics"] = list(prep.diagnostics)

    validator = get_run_manifest_validator()
    validator.validate(manifest_dict)


def test_semantic_label_uniqueness_enforced_in_hardening_and_schema() -> None:
    # 1. Base body duplicate labels rejected in hardening
    base_dup = RectangularBaseBody(
        id="body.main",
        length_mm=100.0,
        width_mm=80.0,
        thickness_mm=20.0,
        placement=BodyPlacement(x_mm=0.0, y_mm=0.0, z_mm=0.0),
        semantic_labels=("plate", "plate"),
    )
    plan_dup_base = _build_test_plan(base_body=base_dup)
    with pytest.raises(ManifestValidationError, match=r"base_body\.semantic_labels contains duplicate labels"):
        harden_plan_metadata(plan_dup_base, request_kind="prompt_to_cad")

    # 2. Primitive body duplicate labels rejected in hardening
    pb_dup = CylinderBaseBody(
        id="body.cyl",
        radius_mm=10.0,
        height_mm=20.0,
        placement=BodyPlacement(x_mm=0.0, y_mm=0.0, z_mm=0.0),
        semantic_labels=("tool", "tool"),
    )
    plan_dup_pb = _build_test_plan(primitive_bodies=(pb_dup,))
    with pytest.raises(
        ManifestValidationError, match=r"primitive_bodies\[0\].semantic_labels contains duplicate labels"
    ):
        harden_plan_metadata(plan_dup_pb, request_kind="prompt_to_cad")

    # 3. Schema validator rejects duplicate labels across body families
    validator = get_run_manifest_validator()
    from tests.manifests.conftest import build_valid_golden_manifest

    m_dup = build_valid_golden_manifest()
    m_dup["feature_plan"]["base_body"]["semantic_labels"] = ["plate", "plate"]
    with pytest.raises(ValidationError):
        validator.validate(m_dup)


def test_complete_normalized_feature_plan_projection_comparison() -> None:
    """Exercise complete normalized FeaturePlan projection comparing all represented fields."""
    plan = FeaturePlan(
        plan_version="cad_copilot.single_part_feature_plan.v1",
        request_id="req-step2-complete-projection-001",
        units="mm",
        part=PartMetadata(
            part_id="part.bracket.01",
            design_intent="bracket with mounting boss, center hole, pocket, and swept rib",
            scope="single_part",
            origin="body_center",
            x_axis="length",
            y_axis="width",
            z_axis="thickness_up",
        ),
        base_body=RectangularBaseBody(
            id="body.base.plate",
            length_mm=120.0,
            width_mm=80.0,
            thickness_mm=15.0,
            placement=BodyPlacement(x_mm=0.0, y_mm=0.0, z_mm=0.0),
            semantic_labels=("mounting_plate",),
        ),
        primitive_bodies=(
            CylinderBaseBody(
                id="body.boss.tool",
                radius_mm=20.0,
                height_mm=30.0,
                placement=BodyPlacement(x_mm=25.0, y_mm=15.0, z_mm=15.0),
                semantic_labels=("boss_cylinder",),
            ),
        ),
        boolean_operations=(
            BooleanOperation(
                id="op.union.boss",
                operation="union",
                target_body_id="body.base.plate",
                tool_body_id="body.boss.tool",
                result_body_id="body.base.plate",
            ),
        ),
        features=(
            CircularThroughHoleFeature(
                id="feat.hole.center",
                diameter_mm=12.0,
                center_x_mm=0.0,
                center_y_mm=0.0,
                target_body_id="body.base.plate",
                target_face="+Z",
                target_selector=None,
                normal_axis=None,
                placement_mode="face_local_center",
                extent_type="through_all",
            ),
            ProfileCutoutFeature(
                id="feat.pocket.rec",
                target_body_id="body.base.plate",
                target_face="+Z",
                target_selector=None,
                normal_axis=None,
                profile_points=(
                    ProfilePoint2D(-20.0, -10.0),
                    ProfilePoint2D(20.0, -10.0),
                    ProfilePoint2D(20.0, 10.0),
                    ProfilePoint2D(-20.0, 10.0),
                ),
                depth_mm=6.0,
                center_x_mm=0.0,
                center_y_mm=0.0,
                placement_mode="face_local_offset",
                extent_type="finite_depth",
            ),
            SweptProtrusionFeature(
                id="feat.rib.sweep",
                target_body_id="body.base.plate",
                target_face="+Z",
                target_selector=None,
                normal_axis=None,
                path=SweepPathSpec(type="straight_line", radius_mm=0.0, angle_deg=0.0),
                cross_sections=(
                    SweepCrossSectionSpec(
                        type="circle",
                        diameter_mm=4.0,
                        width_mm=0.0,
                        height_mm=0.0,
                        profile_points=(),
                        position="start",
                    ),
                    SweepCrossSectionSpec(
                        type="circle",
                        diameter_mm=4.0,
                        width_mm=0.0,
                        height_mm=0.0,
                        profile_points=(),
                        position="end",
                    ),
                ),
                center_x_mm=0.0,
                center_y_mm=0.0,
                placement_mode="face_local_offset",
                extent_type="finite",
            ),
        ),
        lowering_strategy=LoweringStrategy(
            status="canonical",
            preferred_current_target="cad_copilot_lowering",
            runtime_route_change=False,
        ),
        defaults_applied=(
            DefaultApplied(
                path="base_body.profile_points",
                value=[{"x_mm": 0.0, "y_mm": 0.0}, {"x_mm": 10.0, "y_mm": 20.0}],
                reason="revolved_shaft_profile_normalization",
                original_value=[{"x_mm": -1.0, "y_mm": 20.0}, {"x_mm": 0.0, "y_mm": 0.0}],
            ),
            DefaultApplied(
                path="features[0].extent_type",
                value="through_all",
                reason="missing_extent",
                original_value="through_all",
            ),
            DefaultApplied(
                path="features[1].depth_mm",
                value=6.0,
                reason="default_depth",
            ),
            DefaultApplied(
                path="features[2].path.radius_mm",
                value=[1, 2.5],
                reason="default_applied",
                original_value=[1, 2.5],
            ),
        ),
        validation_diagnostics=(
            ValidationDiagnostic(
                severity="warning",
                code="BORDERLINE_FIT",
                message="Raw validator message: hole near edge",
                path="features[0]",
            ),
            ValidationDiagnostic(
                severity="info",
                code="UNKNOWN_SYNTHETIC_CODE",
                message="Notice for user",
                path=None,
            ),
        ),
    )

    # 1. Exact projection comparison without introducing a decoder
    expected_feature_plan: dict[str, Any] = {
        "plan_version": "cad_copilot.single_part_feature_plan.v1",
        "request_id": "req-step2-complete-projection-001",
        "units": "mm",
        "part": {
            "part_id": "part.bracket.01",
            "design_intent": "bracket with mounting boss, center hole, pocket, and swept rib",
            "scope": "single_part",
            "origin": "body_center",
            "x_axis": "length",
            "y_axis": "width",
            "z_axis": "thickness_up",
        },
        "base_body": {
            "id": "body.base.plate",
            "family": "rectangular_prism",
            "dimensions_mm": {
                "length_mm": 120.0,
                "width_mm": 80.0,
                "thickness_mm": 15.0,
            },
            "placement": {
                "x_mm": 0.0,
                "y_mm": 0.0,
                "z_mm": 0.0,
            },
            "semantic_labels": ["mounting_plate"],
        },
        "primitive_bodies": [
            {
                "id": "body.boss.tool",
                "family": "cylinder",
                "dimensions_mm": {
                    "radius_mm": 20.0,
                    "height_mm": 30.0,
                },
                "placement": {
                    "x_mm": 25.0,
                    "y_mm": 15.0,
                    "z_mm": 15.0,
                },
                "semantic_labels": ["boss_cylinder"],
            }
        ],
        "boolean_operations": [
            {
                "id": "op.union.boss",
                "operation": "union",
                "target_body_id": "body.base.plate",
                "tool_body_id": "body.boss.tool",
                "result_body_id": "body.base.plate",
            }
        ],
        "features": [
            {
                "id": "feat.hole.center",
                "family": "circular_through_hole",
                "target_body_id": "body.base.plate",
                "target_face": "+Z",
                "target_selector": None,
                "normal_axis": None,
                "diameter_mm": 12.0,
                "depth_mm": 0.0,
                "center_x_mm": 0.0,
                "center_y_mm": 0.0,
                "placement_mode": "face_local_center",
                "extent_type": "through_all",
            },
            {
                "id": "feat.pocket.rec",
                "family": "profile_cutout",
                "target_body_id": "body.base.plate",
                "target_face": "+Z",
                "target_selector": None,
                "normal_axis": None,
                "profile_points": [
                    {"x_mm": -20.0, "y_mm": -10.0},
                    {"x_mm": 20.0, "y_mm": -10.0},
                    {"x_mm": 20.0, "y_mm": 10.0},
                    {"x_mm": -20.0, "y_mm": 10.0},
                ],
                "depth_mm": 6.0,
                "center_x_mm": 0.0,
                "center_y_mm": 0.0,
                "placement_mode": "face_local_offset",
                "extent_type": "finite_depth",
            },
            {
                "id": "feat.rib.sweep",
                "family": "swept_protrusion",
                "target_body_id": "body.base.plate",
                "target_face": "+Z",
                "target_selector": None,
                "normal_axis": None,
                "path": {
                    "type": "straight_line",
                    "radius_mm": 0.0,
                    "angle_deg": 0.0,
                },
                "cross_sections": [
                    {
                        "type": "circle",
                        "diameter_mm": 4.0,
                        "width_mm": 0.0,
                        "height_mm": 0.0,
                        "profile_points": [],
                        "position": "start",
                    },
                    {
                        "type": "circle",
                        "diameter_mm": 4.0,
                        "width_mm": 0.0,
                        "height_mm": 0.0,
                        "profile_points": [],
                        "position": "end",
                    },
                ],
                "center_x_mm": 0.0,
                "center_y_mm": 0.0,
                "placement_mode": "face_local_offset",
                "extent_type": "finite",
            },
        ],
        "lowering_strategy": {
            "status": "canonical",
            "preferred_current_target": "cad_copilot_lowering",
            "runtime_route_change": False,
        },
    }

    serialized = serialize_canonical_feature_plan(plan)
    assert serialized == expected_feature_plan
    validate_serialized_feature_plan(serialized)

    # 2. Stable identities verified separately
    stable_ids = extract_manifest_stable_ids(plan)
    expected_stable_ids = ManifestStableIds(
        part_id="part.bracket.01",
        body_ids=("body.base.plate", "body.boss.tool"),
        boolean_operation_ids=("op.union.boss",),
        feature_ids=("feat.hole.center", "feat.pocket.rec", "feat.rib.sweep"),
    )
    assert stable_ids == expected_stable_ids

    # 3. Defaults projection verified separately (including integer preservation)
    projected_defaults = project_manifest_defaults(plan.defaults_applied)
    expected_defaults = (
        {
            "path": "base_body.profile_points",
            "value": [{"x_mm": 0.0, "y_mm": 0.0}, {"x_mm": 10.0, "y_mm": 20.0}],
            "reason": "revolved_shaft_profile_normalization",
            "original_value": [{"x_mm": -1.0, "y_mm": 20.0}, {"x_mm": 0.0, "y_mm": 0.0}],
        },
        {
            "path": "features[0].extent_type",
            "value": "through_all",
            "reason": "missing_extent",
            "original_value": "through_all",
        },
        {
            "path": "features[1].depth_mm",
            "value": 6.0,
            "reason": "default_depth",
        },
        {
            "path": "features[2].path.radius_mm",
            "value": [1, 2.5],
            "reason": "default_applied",
            "original_value": [1, 2.5],
        },
    )
    assert projected_defaults == expected_defaults
    assert type(projected_defaults[3]["value"][0]) is int
    assert not isinstance(projected_defaults[3]["value"][0], bool)
    assert type(projected_defaults[3]["value"][1]) is float
    assert type(projected_defaults[3]["original_value"][0]) is int
    assert not isinstance(projected_defaults[3]["original_value"][0], bool)
    assert type(projected_defaults[3]["original_value"][1]) is float

    # 4. Diagnostics projection verified separately
    projected_diagnostics = project_manifest_diagnostics(plan.validation_diagnostics)
    expected_diagnostics = (
        {
            "severity": "warning",
            "code": "BORDERLINE_FIT",
            "path": "features[0]",
            "message": "Feature profile placement is close to the allowable boundary.",
        },
        {
            "severity": "info",
            "code": "UNKNOWN_SYNTHETIC_CODE",
            "path": "root",
            "message": "A non-fatal diagnostic warning was recorded.",
        },
    )
    assert projected_diagnostics == expected_diagnostics

    # 5. Full run-manifest schema conformance
    from tests.manifests.conftest import build_valid_golden_manifest

    validator = get_run_manifest_validator()
    manifest_dict = build_valid_golden_manifest()
    manifest_dict["request"]["request_id"] = plan.request_id
    manifest_dict["fingerprints"]["plan_sha256"] = compute_plan_fingerprint(serialized)
    manifest_dict["feature_plan"] = serialized
    manifest_dict["stable_ids"] = {
        "part_id": stable_ids.part_id,
        "body_ids": list(stable_ids.body_ids),
        "boolean_operation_ids": list(stable_ids.boolean_operation_ids),
        "feature_ids": list(stable_ids.feature_ids),
    }
    manifest_dict["defaults_applied"] = list(projected_defaults)
    manifest_dict["diagnostics"] = list(projected_diagnostics)

    validator.validate(manifest_dict)
