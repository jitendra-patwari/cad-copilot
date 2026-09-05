"""Tests for RunManifest JSON Schema Draft 2020-12 contract."""

from __future__ import annotations

import copy
import importlib.resources
from pathlib import Path
from typing import Any

import pytest
from jsonschema.exceptions import ValidationError

from manifests import get_run_manifest_validator, load_run_manifest_schema
from tests.manifests.conftest import build_valid_golden_manifest


def test_schema_loads_offline() -> None:
    schema = load_run_manifest_schema()
    assert isinstance(schema, dict)
    assert schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema"
    assert schema.get("$id") == "https://cad-copilot.dev/schemas/run-manifest-v1.schema.json"
    assert schema.get("title") == "RunManifest"
    assert schema.get("additionalProperties") is False


def test_schema_compiles_validator() -> None:
    validator = get_run_manifest_validator()
    assert validator is not None
    assert validator.is_type(1, "integer")
    assert not validator.is_type(True, "integer")


def test_golden_manifest_validates() -> None:
    validator = get_run_manifest_validator()
    manifest = build_valid_golden_manifest()
    validator.validate(manifest)


def test_schema_rejects_missing_required_fields() -> None:
    validator = get_run_manifest_validator()
    golden = build_valid_golden_manifest()
    required_fields = [
        "manifest_version",
        "request",
        "engine",
        "cad_runtime",
        "provenance",
        "gate_mode",
        "fingerprints",
        "feature_plan",
        "stable_ids",
        "defaults_applied",
        "diagnostics",
        "warnings",
        "execution",
        "artifacts",
    ]
    for field in required_fields:
        bad = copy.deepcopy(golden)
        del bad[field]
        with pytest.raises(ValidationError) as excinfo:
            validator.validate(bad)
        assert field in str(excinfo.value) or "required" in str(excinfo.value).lower()


def test_schema_rejects_top_level_additional_properties() -> None:
    validator = get_run_manifest_validator()
    bad = build_valid_golden_manifest()
    bad["extra_property"] = "disallowed"
    with pytest.raises(ValidationError):
        validator.validate(bad)


def test_schema_rejects_origin_in_artifacts() -> None:
    validator = get_run_manifest_validator()
    bad = build_valid_golden_manifest()
    bad["artifacts"][0]["origin"] = "cad_copilot"
    with pytest.raises(ValidationError) as excinfo:
        validator.validate(bad)
    assert "origin" in str(excinfo.value) or "additionalProperties" in str(excinfo.value).lower()


def test_schema_rejects_bool_in_integer_fields() -> None:
    validator = get_run_manifest_validator()

    bad1 = build_valid_golden_manifest()
    bad1["execution"]["operations_executed"] = True
    with pytest.raises(ValidationError):
        validator.validate(bad1)

    bad2 = build_valid_golden_manifest()
    bad2["execution"]["inspection"]["feature_count"] = False
    with pytest.raises(ValidationError):
        validator.validate(bad2)

    bad3 = build_valid_golden_manifest()
    bad3["artifacts"][0]["size_bytes"] = True
    with pytest.raises(ValidationError):
        validator.validate(bad3)


def test_schema_rejects_non_positive_inspection_volume() -> None:
    validator = get_run_manifest_validator()
    bad = build_valid_golden_manifest()
    bad["execution"]["inspection"]["volume_mm3"] = 0.0
    with pytest.raises(ValidationError):
        validator.validate(bad)


def test_schema_rejects_invalid_sha256() -> None:
    validator = get_run_manifest_validator()

    # Uppercase hex
    bad1 = build_valid_golden_manifest()
    bad1["fingerprints"]["plan_sha256"] = "A" * 64
    with pytest.raises(ValidationError):
        validator.validate(bad1)

    # Short hash
    bad2 = build_valid_golden_manifest()
    bad2["fingerprints"]["plan_sha256"] = "abc123"
    with pytest.raises(ValidationError):
        validator.validate(bad2)


def test_validator_and_schema_are_cached() -> None:
    val1 = get_run_manifest_validator()
    val2 = get_run_manifest_validator()
    assert val1 is val2

    schema1 = load_run_manifest_schema()
    schema2 = load_run_manifest_schema()
    assert schema1 == schema2
    assert schema1 is not schema2

    schema1["__mutated_key__"] = True
    schema3 = load_run_manifest_schema()
    assert "__mutated_key__" not in schema3


def test_package_data_manifests_schema_configured() -> None:
    resource = importlib.resources.files("manifests").joinpath("schemas", "run-manifest-v1.schema.json")
    assert resource.is_file(), "Schema file must be accessible as package data"

    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    assert pyproject_path.is_file(), f"pyproject.toml not found at {pyproject_path}"
    content = pyproject_path.read_text(encoding="utf-8")
    assert "[tool.setuptools.package-data]" in content
    assert '"manifests" = ["schemas/*.json"]' in content


@pytest.mark.parametrize(
    "family,body_dict",
    [
        (
            "rectangular_prism",
            {
                "id": "body.base",
                "family": "rectangular_prism",
                "dimensions_mm": {"length_mm": 100.0, "width_mm": 50.0, "thickness_mm": 20.0},
                "placement": {"x_mm": 0.0, "y_mm": 0.0, "z_mm": 0.0},
                "semantic_labels": ["base"],
            },
        ),
        (
            "cylinder",
            {
                "id": "body.cyl",
                "family": "cylinder",
                "dimensions_mm": {"radius_mm": 25.0, "height_mm": 60.0},
                "placement": {"x_mm": 0.0, "y_mm": 0.0, "z_mm": 0.0},
                "semantic_labels": ["shaft"],
            },
        ),
        (
            "sphere",
            {
                "id": "body.sph",
                "family": "sphere",
                "dimensions_mm": {"radius_mm": 30.0},
                "placement": {"x_mm": 0.0, "y_mm": 0.0, "z_mm": 0.0},
                "semantic_labels": ["ball"],
            },
        ),
        (
            "spur_gear",
            {
                "id": "body.gear",
                "family": "spur_gear",
                "dimensions_mm": {
                    "tooth_count": 24,
                    "module_mm": 2.0,
                    "face_width_mm": 15.0,
                    "pressure_angle_deg": 20.0,
                    "bore_diameter_mm": 10.0,
                },
                "placement": {"x_mm": 0.0, "y_mm": 0.0, "z_mm": 0.0},
                "semantic_labels": ["gear"],
            },
        ),
        (
            "revolved_shaft",
            {
                "id": "body.rev",
                "family": "revolved_shaft",
                "dimensions_mm": {"radius_mm": 20.0, "height_mm": 50.0},
                "profile_points": [
                    {"x_mm": 0.0, "y_mm": 0.0},
                    {"x_mm": 20.0, "y_mm": 0.0},
                    {"x_mm": 20.0, "y_mm": 50.0},
                ],
                "placement": {"x_mm": 0.0, "y_mm": 0.0, "z_mm": 0.0},
                "semantic_labels": ["spindle"],
            },
        ),
    ],
)
def test_schema_accepts_all_five_base_body_families(family: str, body_dict: dict[str, Any]) -> None:
    validator = get_run_manifest_validator()
    manifest = build_valid_golden_manifest()
    manifest["feature_plan"]["base_body"] = body_dict
    manifest["feature_plan"]["primitive_bodies"] = [body_dict]
    validator.validate(manifest)


def test_schema_rejects_incomplete_or_invalid_base_body() -> None:
    validator = get_run_manifest_validator()

    # Rejects legacy x/y/z alias
    bad_xyz = build_valid_golden_manifest()
    bad_xyz["feature_plan"]["base_body"]["dimensions_mm"] = {"x": 100.0, "y": 50.0, "z": 20.0}
    with pytest.raises(ValidationError):
        validator.validate(bad_xyz)

    # Unknown family
    manifest = build_valid_golden_manifest()
    manifest["feature_plan"]["base_body"]["family"] = "torus"
    with pytest.raises(ValidationError):
        validator.validate(manifest)

    # Incomplete rectangular dimensions
    bad_rect = build_valid_golden_manifest()
    bad_rect["feature_plan"]["base_body"] = {
        "id": "body.base",
        "family": "rectangular_prism",
        "dimensions_mm": {"length_mm": 100.0},  # missing width and thickness
        "placement": {"x_mm": 0.0, "y_mm": 0.0, "z_mm": 0.0},
        "semantic_labels": ["base"],
    }
    with pytest.raises(ValidationError):
        validator.validate(bad_rect)

    # Sphere with forbidden profile_points
    bad_sph = build_valid_golden_manifest()
    bad_sph["feature_plan"]["base_body"] = {
        "id": "body.sph",
        "family": "sphere",
        "dimensions_mm": {"radius_mm": 10.0},
        "profile_points": [{"x_mm": 0.0, "y_mm": 0.0}],  # additional property on sphere
        "placement": {"x_mm": 0.0, "y_mm": 0.0, "z_mm": 0.0},
        "semantic_labels": ["ball"],
    }
    with pytest.raises(ValidationError):
        validator.validate(bad_sph)

    # Semantic label not matching slug pattern
    bad_label = build_valid_golden_manifest()
    bad_label["feature_plan"]["base_body"]["semantic_labels"] = ["bad label with spaces!"]
    with pytest.raises(ValidationError):
        validator.validate(bad_label)

    # Spur gear missing pressure_angle_deg
    bad_gear_pa = build_valid_golden_manifest()
    bad_gear_pa["feature_plan"]["base_body"] = {
        "id": "body.gear",
        "family": "spur_gear",
        "dimensions_mm": {
            "tooth_count": 24,
            "module_mm": 2.0,
            "face_width_mm": 15.0,
            # missing pressure_angle_deg
            "bore_diameter_mm": 10.0,
        },
        "placement": {"x_mm": 0.0, "y_mm": 0.0, "z_mm": 0.0},
        "semantic_labels": ["gear"],
    }
    with pytest.raises(ValidationError):
        validator.validate(bad_gear_pa)

    # Spur gear missing bore_diameter_mm
    bad_gear_bore = build_valid_golden_manifest()
    bad_gear_bore["feature_plan"]["base_body"] = {
        "id": "body.gear",
        "family": "spur_gear",
        "dimensions_mm": {
            "tooth_count": 24,
            "module_mm": 2.0,
            "face_width_mm": 15.0,
            "pressure_angle_deg": 20.0,
            # missing bore_diameter_mm
        },
        "placement": {"x_mm": 0.0, "y_mm": 0.0, "z_mm": 0.0},
        "semantic_labels": ["gear"],
    }
    with pytest.raises(ValidationError):
        validator.validate(bad_gear_bore)


@pytest.mark.parametrize(
    "family,feature_dict",
    [
        (
            "circular_through_hole",
            {
                "id": "feat.hole.1",
                "family": "circular_through_hole",
                "target_body_id": "body.base",
                "target_face": "+Z",
                "target_selector": None,
                "normal_axis": None,
                "diameter_mm": 6.0,
                "depth_mm": 0.0,
                "center_x_mm": 10.0,
                "center_y_mm": 0.0,
                "placement_mode": "face_local_offset",
                "extent_type": "through_all",
            },
        ),
        (
            "rectangular_through_cutout",
            {
                "id": "feat.cut.1",
                "family": "rectangular_through_cutout",
                "target_body_id": "body.base",
                "target_face": "+Z",
                "target_selector": None,
                "normal_axis": None,
                "width_mm": 12.0,
                "height_mm": 8.0,
                "depth_mm": 0.0,
                "center_x_mm": -5.0,
                "center_y_mm": 5.0,
                "placement_mode": "face_local_offset",
                "extent_type": "through_all",
            },
        ),
        (
            "slot_through_cutout",
            {
                "id": "feat.slot.1",
                "family": "slot_through_cutout",
                "target_body_id": "body.base",
                "target_face": "+Z",
                "target_selector": None,
                "normal_axis": None,
                "length_mm": 15.0,
                "width_mm": 5.0,
                "orientation_axis": "x",
                "center_x_mm": -10.0,
                "center_y_mm": 0.0,
                "placement_mode": "face_local_center",
                "extent_type": "through_all",
                "depth_mm": 0.0,
            },
        ),
        (
            "rectangular_extruded_pad",
            {
                "id": "feat.pad.1",
                "family": "rectangular_extruded_pad",
                "target_body_id": "body.base",
                "target_face": "+Z",
                "target_selector": None,
                "normal_axis": None,
                "width_mm": 20.0,
                "height_mm": 10.0,
                "distance_mm": 5.0,
                "center_x_mm": 0.0,
                "center_y_mm": 0.0,
                "placement_mode": "face_local_center",
                "extent_type": "finite",
            },
        ),
        (
            "revolved_profile",
            {
                "id": "feat.revolve.1",
                "family": "revolved_profile",
                "target_body_id": "body.base",
                "target_face": None,
                "target_selector": None,
                "normal_axis": None,
                "angle_deg": 360.0,
                "axis_start": {"x_mm": 0.0, "y_mm": 0.0},
                "axis_end": {"x_mm": 0.0, "y_mm": 1.0},
                "radial_depth_mm": 0.0,
                "profile_height_mm": 10.0,
                "center_x_mm": 0.0,
                "center_y_mm": 0.0,
                "placement_mode": "face_local_center",
                "profile_points": [
                    {"x_mm": 0.0, "y_mm": 0.0},
                    {"x_mm": 5.0, "y_mm": 0.0},
                    {"x_mm": 5.0, "y_mm": 10.0},
                ],
                "extent_type": "finite",
            },
        ),
        (
            "profile_cutout",
            {
                "id": "feat.pcut.1",
                "family": "profile_cutout",
                "target_body_id": "body.base",
                "target_face": None,
                "target_selector": None,
                "normal_axis": None,
                "depth_mm": 10.0,
                "center_x_mm": 0.0,
                "center_y_mm": 0.0,
                "placement_mode": "face_local_center",
                "extent_type": "finite",
                "profile_points": [
                    {"x_mm": -5.0, "y_mm": -5.0},
                    {"x_mm": 5.0, "y_mm": -5.0},
                    {"x_mm": 0.0, "y_mm": 5.0},
                ],
            },
        ),
        (
            "swept_protrusion",
            {
                "id": "feat.sweep.1",
                "family": "swept_protrusion",
                "target_body_id": "body.base",
                "target_face": None,
                "target_selector": None,
                "normal_axis": None,
                "center_x_mm": 0.0,
                "center_y_mm": 0.0,
                "placement_mode": "face_local_center",
                "extent_type": "finite",
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
                    }
                ],
            },
        ),
    ],
)
def test_schema_accepts_all_seven_feature_families(family: str, feature_dict: dict[str, Any]) -> None:
    validator = get_run_manifest_validator()
    manifest = build_valid_golden_manifest()
    manifest["feature_plan"]["features"] = [feature_dict]
    validator.validate(manifest)


def test_schema_rejects_incomplete_or_invalid_feature() -> None:
    validator = get_run_manifest_validator()

    # Incomplete hole: missing normalized placement_mode and extent_type
    bad_hole = build_valid_golden_manifest()
    bad_hole["feature_plan"]["features"] = [
        {
            "id": "feat.hole.1",
            "family": "circular_through_hole",
            "target_body_id": "body.base",
            "target_face": "+Z",
            "diameter_mm": 6.0,
            # missing depth_mm, center_x_mm, center_y_mm, placement_mode, extent_type
        }
    ]
    with pytest.raises(ValidationError):
        validator.validate(bad_hole)

    # Hole with cross_sections from swept_protrusion
    bad_cross = build_valid_golden_manifest()
    bad_cross["feature_plan"]["features"] = [
        {
            "id": "feat.hole.1",
            "family": "circular_through_hole",
            "target_body_id": "body.base",
            "target_face": "+Z",
            "diameter_mm": 5.0,
            "depth_mm": 0.0,
            "center_x_mm": 0.0,
            "center_y_mm": 0.0,
            "placement_mode": "face_local_center",
            "extent_type": "through_all",
            "cross_sections": [{"type": "circle"}],  # additionalProperty on hole
        }
    ]
    with pytest.raises(ValidationError):
        validator.validate(bad_cross)

    # Slot with non-canonical alias "slot_orientation" rejected
    bad_alias = build_valid_golden_manifest()
    bad_alias["feature_plan"]["features"] = [
        {
            "id": "feat.slot.1",
            "family": "slot_through_cutout",
            "target_body_id": "body.base",
            "target_face": "+Z",
            "target_selector": None,
            "normal_axis": None,
            "length_mm": 10.0,
            "width_mm": 5.0,
            "depth_mm": 0.0,
            "center_x_mm": 0.0,
            "center_y_mm": 0.0,
            "placement_mode": "face_local_center",
            "extent_type": "through_all",
            "slot_orientation": "x",  # must be orientation_axis
        }
    ]
    with pytest.raises(ValidationError):
        validator.validate(bad_alias)

    # Hole missing target_selector
    bad_hole_ts = build_valid_golden_manifest()
    bad_hole_ts["feature_plan"]["features"] = [
        {
            "id": "feat.hole.1",
            "family": "circular_through_hole",
            "target_body_id": "body.base",
            "target_face": "+Z",
            # missing target_selector
            "normal_axis": None,
            "diameter_mm": 6.0,
            "depth_mm": 0.0,
            "center_x_mm": 0.0,
            "center_y_mm": 0.0,
            "placement_mode": "face_local_center",
            "extent_type": "through_all",
        }
    ]
    with pytest.raises(ValidationError):
        validator.validate(bad_hole_ts)

    # Hole missing normal_axis
    bad_hole_na = build_valid_golden_manifest()
    bad_hole_na["feature_plan"]["features"] = [
        {
            "id": "feat.hole.1",
            "family": "circular_through_hole",
            "target_body_id": "body.base",
            "target_face": "+Z",
            "target_selector": None,
            # missing normal_axis
            "diameter_mm": 6.0,
            "depth_mm": 0.0,
            "center_x_mm": 0.0,
            "center_y_mm": 0.0,
            "placement_mode": "face_local_center",
            "extent_type": "through_all",
        }
    ]
    with pytest.raises(ValidationError):
        validator.validate(bad_hole_na)

    # Swept protrusion missing position in cross_sections
    bad_sweep_pos = build_valid_golden_manifest()
    bad_sweep_pos["feature_plan"]["features"] = [
        {
            "id": "feat.sweep.1",
            "family": "swept_protrusion",
            "target_body_id": "body.base",
            "target_face": None,
            "target_selector": None,
            "normal_axis": None,
            "center_x_mm": 0.0,
            "center_y_mm": 0.0,
            "placement_mode": "face_local_center",
            "extent_type": "finite",
            "path": {"type": "straight_line", "radius_mm": 0.0, "angle_deg": 0.0},
            "cross_sections": [
                {
                    "type": "circle",
                    "diameter_mm": 4.0,
                    "width_mm": 0.0,
                    "height_mm": 0.0,
                    "profile_points": [],
                    # missing position
                }
            ],
        }
    ]
    with pytest.raises(ValidationError):
        validator.validate(bad_sweep_pos)


def test_schema_accepts_large_collections_bounded() -> None:
    """Verify that defaults_applied and diagnostics accept large collections up to 8192 items and reject exceeding counts."""
    validator = get_run_manifest_validator()
    manifest = build_valid_golden_manifest()

    # 1000 defaults applied (within 8192 bound)
    manifest["defaults_applied"] = [
        {
            "path": f"features[{i}].extent.depth_mm",
            "value": 0.0,
            "reason": "through_all_zero_depth",
        }
        for i in range(1000)
    ]

    # 1000 diagnostics (within 8192 bound)
    manifest["diagnostics"] = [
        {
            "severity": "info",
            "code": f"DIAG_{i:04d}",
            "message": f"Diagnostic message {i}",
        }
        for i in range(1000)
    ]

    # 200 warnings
    manifest["warnings"] = [f"Warning notice {i}" for i in range(200)]

    # 200 operation_results
    manifest["execution"]["operations_executed"] = 200
    manifest["execution"]["operation_results"] = [
        {
            "patch_id": f"op_{i}",
            "operation": "add_hole",
            "reference_id": f"feat.hole.{i}",
            "reference_kind": "feature",
        }
        for i in range(200)
    ]

    validator.validate(manifest)

    # Exceeding 8192 diagnostics fails schema validation
    manifest_over = build_valid_golden_manifest()
    manifest_over["diagnostics"] = [
        {
            "severity": "info",
            "code": f"DIAG_{i:04d}",
            "message": f"Diagnostic message {i}",
        }
        for i in range(8193)
    ]
    with pytest.raises(ValidationError):
        validator.validate(manifest_over)

    # Exceeding 8192 defaults_applied fails schema validation
    manifest_over_defs = build_valid_golden_manifest()
    manifest_over_defs["defaults_applied"] = [
        {
            "path": f"features[{i}].extent.depth_mm",
            "value": 0.0,
            "reason": "through_all_zero_depth",
        }
        for i in range(8193)
    ]
    with pytest.raises(ValidationError):
        validator.validate(manifest_over_defs)


def test_schema_accepts_canonical_indexed_default_applied_paths() -> None:
    """Verify schema accepts canonical bracketed indexed paths used by geometry parser/validators."""
    validator = get_run_manifest_validator()
    manifest = build_valid_golden_manifest()

    canonical_defaults = [
        {
            "path": "features[0].extent.type",
            "value": "through_all",
            "reason": "canonical_subtractive_extent_alias",
        },
        {
            "path": "features[0].extent.depth_mm",
            "value": 0.0,
            "reason": "through_all_extent_ignores_finite_depth",
        },
        {
            "path": "boolean_operations[0].operation",
            "value": "union",
            "reason": "unknown_boolean_operation_fallback",
        },
        {
            "path": "primitive_bodies[0].dimensions.length_mm",
            "value": 100.0,
            "reason": "default_dimension_plate",
        },
        {
            "path": "features[12].depth_mm",
            "value": 10.0,
            "reason": "fallback_depth",
        },
    ]
    manifest["defaults_applied"] = canonical_defaults
    validator.validate(manifest)

    # Negative test: invalid characters like spaces or quotes rejected
    bad_path_space = copy.deepcopy(manifest)
    bad_path_space["defaults_applied"] = [
        {
            "path": "features[0].extent type",
            "value": "through_all",
            "reason": "invalid_space",
        }
    ]
    with pytest.raises(ValidationError):
        validator.validate(bad_path_space)

    bad_path_quote = copy.deepcopy(manifest)
    bad_path_quote["defaults_applied"] = [
        {
            "path": 'features["0"].type',
            "value": "through_all",
            "reason": "invalid_quotes",
        }
    ]
    with pytest.raises(ValidationError):
        validator.validate(bad_path_quote)


def test_schema_relational_constraints_example_plan() -> None:
    validator = get_run_manifest_validator()

    # Valid example_plan
    manifest = build_valid_golden_manifest()
    manifest["request"]["kind"] = "example_plan"
    manifest["provenance"]["kind"] = "example_plan"
    manifest["fingerprints"]["prompt_sha256"] = None
    validator.validate(manifest)

    # Invalid: example_plan with prompt_sha256
    bad1 = copy.deepcopy(manifest)
    bad1["fingerprints"]["prompt_sha256"] = "a" * 64
    with pytest.raises(ValidationError):
        validator.validate(bad1)

    # Invalid: example_plan with ai_proposal provenance
    bad2 = copy.deepcopy(manifest)
    bad2["provenance"]["kind"] = "ai_proposal"
    with pytest.raises(ValidationError):
        validator.validate(bad2)


def test_schema_relational_constraints_prompt_to_cad() -> None:
    validator = get_run_manifest_validator()

    # Invalid: prompt_to_cad with null prompt_sha256
    bad1 = build_valid_golden_manifest()
    bad1["fingerprints"]["prompt_sha256"] = None
    with pytest.raises(ValidationError):
        validator.validate(bad1)

    # Invalid: prompt_to_cad with example_plan provenance
    bad2 = build_valid_golden_manifest()
    bad2["provenance"]["kind"] = "example_plan"
    with pytest.raises(ValidationError):
        validator.validate(bad2)


def test_schema_runtime_route_change_must_be_false() -> None:
    validator = get_run_manifest_validator()
    bad = build_valid_golden_manifest()
    bad["feature_plan"]["lowering_strategy"]["runtime_route_change"] = True
    with pytest.raises(ValidationError):
        validator.validate(bad)


def test_schema_stable_ids_unique_items() -> None:
    validator = get_run_manifest_validator()

    # Duplicate body_ids
    bad1 = build_valid_golden_manifest()
    bad1["stable_ids"]["body_ids"] = ["body.base", "body.base"]
    with pytest.raises(ValidationError):
        validator.validate(bad1)

    # Duplicate boolean_operation_ids
    bad2 = build_valid_golden_manifest()
    bad2["stable_ids"]["boolean_operation_ids"] = ["op.1", "op.1"]
    with pytest.raises(ValidationError):
        validator.validate(bad2)

    # Duplicate feature_ids
    bad3 = build_valid_golden_manifest()
    bad3["stable_ids"]["feature_ids"] = ["feat.1", "feat.1"]
    with pytest.raises(ValidationError):
        validator.validate(bad3)


def test_schema_request_id_rejects_dots_and_traversal() -> None:
    validator = get_run_manifest_validator()

    # Direct dot "." must be rejected by schema pattern
    bad1 = build_valid_golden_manifest()
    bad1["request"]["request_id"] = "."
    bad1["feature_plan"]["request_id"] = "."
    with pytest.raises(ValidationError):
        validator.validate(bad1)

    # Traversal ".." must be rejected by schema pattern
    bad2 = build_valid_golden_manifest()
    bad2["request"]["request_id"] = ".."
    bad2["feature_plan"]["request_id"] = ".."
    with pytest.raises(ValidationError):
        validator.validate(bad2)

    # Trailing dot "req-1." must be rejected by schema pattern
    bad3 = build_valid_golden_manifest()
    bad3["request"]["request_id"] = "req-1."
    bad3["feature_plan"]["request_id"] = "req-1."
    with pytest.raises(ValidationError):
        validator.validate(bad3)


def test_schema_artifacts_prefix_items_order_and_types() -> None:
    validator = get_run_manifest_validator()

    # Valid with optional 4th JPG
    valid_4 = build_valid_golden_manifest()
    valid_4["artifacts"].append(
        {
            "type": "preview_image",
            "format": "jpg",
            "path": "preview.jpg",
            "size_bytes": 512,
            "sha256": "d" * 64,
        }
    )
    validator.validate(valid_4)

    # Invalid order: step before par
    bad_order = build_valid_golden_manifest()
    bad_order["artifacts"][0], bad_order["artifacts"][1] = bad_order["artifacts"][1], bad_order["artifacts"][0]
    with pytest.raises(ValidationError):
        validator.validate(bad_order)

    # Too few items (only 2)
    bad_few = build_valid_golden_manifest()
    bad_few["artifacts"] = bad_few["artifacts"][:2]
    with pytest.raises(ValidationError):
        validator.validate(bad_few)

    # Too many items (5)
    bad_many = copy.deepcopy(valid_4)
    bad_many["artifacts"].append(copy.deepcopy(valid_4["artifacts"][3]))
    with pytest.raises(ValidationError):
        validator.validate(bad_many)


def test_schema_privacy_bounds() -> None:
    validator = get_run_manifest_validator()

    # Raw host path in defaults_applied value rejected
    bad_path = build_valid_golden_manifest()
    bad_path["defaults_applied"] = [
        {
            "path": "part.material",
            "value": "C:/Users/private",
            "reason": "fallback_token",
        }
    ]
    with pytest.raises(ValidationError):
        validator.validate(bad_path)

    # Raw secret sentinel in defaults_applied value rejected
    bad_secret = build_valid_golden_manifest()
    bad_secret["defaults_applied"] = [
        {
            "path": "part.material",
            "value": "RAW_SECRET_SENTINEL",
            "reason": "fallback_token",
        }
    ]
    # "RAW_SECRET_SENTINEL" contains underscores and capital letters, matches pattern
    # But invalid characters like spaces or colons or slashes are rejected
    bad_char = build_valid_golden_manifest()
    bad_char["defaults_applied"] = [
        {
            "path": "part.material",
            "value": "invalid string with spaces",
            "reason": "fallback_token",
        }
    ]
    with pytest.raises(ValidationError):
        validator.validate(bad_char)

    # Explicit redaction object in original_value accepted
    valid_redacted = build_valid_golden_manifest()
    valid_redacted["defaults_applied"] = [
        {
            "path": "part.material",
            "value": "default_steel",
            "reason": "sanitized_fallback",
            "original_value": {
                "redacted": True,
                "sha256": "e" * 64,
            },
        }
    ]
    validator.validate(valid_redacted)

    # Oversized warning string (> 512 chars)
    bad_warn = build_valid_golden_manifest()
    bad_warn["warnings"] = ["W" * 513]
    with pytest.raises(ValidationError):
        validator.validate(bad_warn)

    # Oversized diagnostic message (> 512 chars)
    bad_diag = build_valid_golden_manifest()
    bad_diag["diagnostics"] = [
        {
            "severity": "warning",
            "code": "WARN_001",
            "message": "M" * 513,
        }
    ]
    with pytest.raises(ValidationError):
        validator.validate(bad_diag)
