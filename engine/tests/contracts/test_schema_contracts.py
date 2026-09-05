"""Automated test suite for JSON Schema Draft 2020-12 meta-validation and contract fixture conformance.

Invariants Verified:
    1. Schema Meta-Validation: All schemas in contracts/schemas/ conform to JSON Schema Draft 2020-12.
    2. URI Namespace Standard: All schemas use canonical https://cad-copilot.dev/schemas/ IDs.
    3. Generation Contract Conformance: Request and response fixtures validate against generation schemas.
    4. Batch Contract Conformance: Request and response fixtures validate against batch schemas.
    5. Edit Contract Conformance: Request and response fixtures validate against edit schemas.
    6. Golden Examples Integrity: Canonical DSL example plans parse and load cleanly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from jsonschema.validators import Draft202012Validator

CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "contracts"
SCHEMAS_ROOT = CONTRACTS_ROOT / "schemas"
EXAMPLES_ROOT = CONTRACTS_ROOT / "examples"


def _load_json(path: Path) -> dict[str, Any]:
    """Helper to load and parse a JSON file."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# 1. JSON Schema Draft 2020-12 Meta-Validation
# ---------------------------------------------------------------------------


def test_contracts_directory_exists() -> None:
    """Proves that the contracts/ root directory and subdirectories exist."""
    assert CONTRACTS_ROOT.is_dir(), f"Contracts root missing at {CONTRACTS_ROOT}"
    assert SCHEMAS_ROOT.is_dir(), f"Schemas directory missing at {SCHEMAS_ROOT}"
    assert EXAMPLES_ROOT.is_dir(), f"Examples directory missing at {EXAMPLES_ROOT}"


def test_all_schemas_conform_to_draft_202012() -> None:
    """Proves that every .schema.json in the repository is a valid Draft 2020-12 schema."""
    schema_files = list(SCHEMAS_ROOT.rglob("*.schema.json"))
    assert len(schema_files) >= 6, f"Expected at least 6 schema files, found {len(schema_files)}"

    for schema_file in schema_files:
        schema_dict = _load_json(schema_file)
        # Check standard Draft 2020-12 meta-schema compliance
        Draft202012Validator.check_schema(schema_dict)

        # Check canonical ID namespace
        schema_id = schema_dict.get("$id", "")
        assert schema_id.startswith("https://cad-copilot.dev/schemas/"), (
            f"Schema {schema_file.name} has non-canonical $id: '{schema_id}'"
        )


# ---------------------------------------------------------------------------
# 2. Generation Contract Fixtures Conformance Tests
# ---------------------------------------------------------------------------


def test_generation_request_fixtures_conformance() -> None:
    """Verifies generation request fixtures against generation-request.schema.json."""
    req_schema = _load_json(SCHEMAS_ROOT / "generation" / "generation-request.schema.json")
    validator = Draft202012Validator(req_schema)
    fixtures_dir = SCHEMAS_ROOT / "generation" / "fixtures"

    positive_fixtures = [
        "prompt_default_artifacts.request.json",
        "example_spur_gear.request.json",
    ]

    for fname in positive_fixtures:
        fixture_path = fixtures_dir / fname
        assert fixture_path.is_file(), f"Missing positive fixture: {fname}"
        payload = _load_json(fixture_path)
        validator.validate(payload)

    # Intentional negative fixtures: unsupported inputs must fail schema validation
    negative_fixtures = [
        "rejected_image_field.request.json",
        "rejected_visible_artifacts_field.request.json",
        "rejected_unknown_example.request.json",
    ]
    for fname in negative_fixtures:
        fixture_path = fixtures_dir / fname
        assert fixture_path.is_file(), f"Missing negative fixture: {fname}"
        bad_payload = _load_json(fixture_path)
        with pytest.raises(jsonschema.ValidationError):
            validator.validate(bad_payload)


def test_generation_response_fixtures_conformance() -> None:
    """Verifies generation response fixtures against generation-response.schema.json."""
    res_schema = _load_json(SCHEMAS_ROOT / "generation" / "generation-response.schema.json")
    validator = Draft202012Validator(res_schema)
    fixtures_dir = SCHEMAS_ROOT / "generation" / "fixtures"

    response_fixtures = list(fixtures_dir.glob("*.response.json"))
    assert len(response_fixtures) == 6, f"Expected exactly 6 response fixtures, found {len(response_fixtures)}"

    for fixture_path in response_fixtures:
        payload = _load_json(fixture_path)
        validator.validate(payload)


def test_generation_response_schema_rejects_invalid_artifact_combinations() -> None:
    """Proves that accepted generation responses strictly require one par, one step, and one stl."""
    res_schema = _load_json(SCHEMAS_ROOT / "generation" / "generation-response.schema.json")
    validator = Draft202012Validator(res_schema)

    base_response: dict[str, Any] = {
        "contract_version": "1.0",
        "request_id": "test-req-001",
        "status": "accepted",
        "warnings": [],
    }

    # Case 1: Missing STL (only par and step) -> Must fail
    missing_stl = {
        **base_response,
        "data": {
            "artifacts": [
                {"type": "native_part", "format": "par", "path": "p.par", "origin": "cad_copilot"},
                {"type": "geometry_step", "format": "step", "path": "p.step", "origin": "cad_copilot"},
            ]
        },
    }
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(missing_stl)

    # Case 2: Three PAR records (missing step and stl) -> Must fail
    three_pars = {
        **base_response,
        "data": {
            "artifacts": [
                {"type": "native_part", "format": "par", "path": "1.par", "origin": "cad_copilot"},
                {"type": "native_part", "format": "par", "path": "2.par", "origin": "cad_copilot"},
                {"type": "native_part", "format": "par", "path": "3.par", "origin": "cad_copilot"},
            ]
        },
    }
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(three_pars)

    # Case 3: Duplicate STEP (two step, one par, one stl -> total 4, but duplicate step and missing jpg) -> Must fail
    duplicate_step = {
        **base_response,
        "data": {
            "artifacts": [
                {"type": "native_part", "format": "par", "path": "p.par", "origin": "cad_copilot"},
                {"type": "geometry_step", "format": "step", "path": "p1.step", "origin": "cad_copilot"},
                {"type": "geometry_step", "format": "step", "path": "p2.step", "origin": "cad_copilot"},
                {"type": "mesh_stl", "format": "stl", "path": "p.stl", "origin": "cad_copilot"},
            ]
        },
    }
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(duplicate_step)

    # Case 4: Missing PAR (step, stl, jpg) -> Must fail
    missing_par = {
        **base_response,
        "data": {
            "artifacts": [
                {"type": "geometry_step", "format": "step", "path": "p.step", "origin": "cad_copilot"},
                {"type": "mesh_stl", "format": "stl", "path": "p.stl", "origin": "cad_copilot"},
                {"type": "preview_image", "format": "jpg", "path": "p.jpg", "origin": "cad_copilot"},
            ]
        },
    }
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(missing_par)


def test_generation_response_schema_rejects_unrecognized_error_codes() -> None:
    """Proves that generation-response schema rejects arbitrary error codes and accepts all valid taxonomy codes."""
    res_schema = _load_json(SCHEMAS_ROOT / "generation" / "generation-response.schema.json")
    validator = Draft202012Validator(res_schema)

    # Valid failure payload with TARGET_ALREADY_EXISTS and warnings array
    valid_payload = {
        "contract_version": "1.0",
        "request_id": "test-req-err-01",
        "status": "failed",
        "errors": [
            {
                "code": "TARGET_ALREADY_EXISTS",
                "message": "Output destination already exists.",
            }
        ],
        "warnings": [],
    }
    validator.validate(valid_payload)

    # Invalid payload with unknown error code -> Must raise ValidationError
    invalid_payload = {
        "contract_version": "1.0",
        "request_id": "test-req-err-02",
        "status": "failed",
        "errors": [
            {
                "code": "ARBITRARY_UNKNOWN_ERROR",
                "message": "Some error.",
            }
        ],
        "warnings": [],
    }
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(invalid_payload)


def test_generation_response_schema_requires_warnings_on_rejected_and_failed() -> None:
    """Proves that rejected and failed responses strictly require the warnings array for schema parity."""
    res_schema = _load_json(SCHEMAS_ROOT / "generation" / "generation-response.schema.json")
    validator = Draft202012Validator(res_schema)

    # Failed payload missing warnings -> Must fail
    failed_missing_warnings = {
        "contract_version": "1.0",
        "request_id": "test-req-no-warn-01",
        "status": "failed",
        "errors": [
            {
                "code": "CAD_EXECUTION_FAILED",
                "message": "Execution failed.",
            }
        ],
    }
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(failed_missing_warnings)

    # Rejected payload missing warnings -> Must fail
    rejected_missing_warnings = {
        "contract_version": "1.0",
        "request_id": "test-req-no-warn-02",
        "status": "rejected",
        "errors": [
            {
                "code": "UNSUPPORTED_REQUEST",
                "message": "Request unsupported.",
            }
        ],
    }
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(rejected_missing_warnings)

    # Adding warnings: [] makes them valid
    validator.validate({**failed_missing_warnings, "warnings": []})
    validator.validate({**rejected_missing_warnings, "warnings": ["Prior warning message"]})


# ---------------------------------------------------------------------------
# 3. Batch Contract Fixtures Conformance Tests
# ---------------------------------------------------------------------------


def test_batch_request_and_response_fixtures_conformance() -> None:
    """Verifies batch contract fixtures against batch schemas."""
    req_schema = _load_json(SCHEMAS_ROOT / "batch" / "batch-request.schema.json")
    res_schema = _load_json(SCHEMAS_ROOT / "batch" / "batch-response.schema.json")
    req_validator = Draft202012Validator(req_schema)
    res_validator = Draft202012Validator(res_schema)
    fixtures_dir = SCHEMAS_ROOT / "batch" / "fixtures"

    # Positive request fixtures
    positive_requests = [
        "basic_export.request.json",
        "publish_drawing.request.json",
        "rejected_unsafe_path.request.json",
    ]
    for fname in positive_requests:
        fixture_path = fixtures_dir / fname
        assert fixture_path.is_file(), f"Missing batch fixture: {fname}"
        req_validator.validate(_load_json(fixture_path))

    # Negative request fixtures: must fail schema validation
    negative_requests = [
        "rejected_no_files.request.json",
        "rejected_legacy_operation.request.json",
        "rejected_unverified_format.request.json",
    ]
    for fname in negative_requests:
        fixture_path = fixtures_dir / fname
        assert fixture_path.is_file(), f"Missing negative fixture: {fname}"
        with pytest.raises(jsonschema.ValidationError):
            req_validator.validate(_load_json(fixture_path))

    # Response fixtures
    response_fixtures = list(fixtures_dir.glob("*.response.json"))
    assert len(response_fixtures) == 6, f"Expected exactly 6 batch response fixtures, found {len(response_fixtures)}"
    for fixture_path in response_fixtures:
        res_validator.validate(_load_json(fixture_path))


# ---------------------------------------------------------------------------
# 4. Edit Contract Fixtures Conformance Tests
# ---------------------------------------------------------------------------


def test_edit_request_and_response_fixtures_conformance() -> None:
    """Verifies edit contract fixtures against edit schemas."""
    req_schema = _load_json(SCHEMAS_ROOT / "edit" / "edit-request.schema.json")
    res_schema = _load_json(SCHEMAS_ROOT / "edit" / "edit-response.schema.json")
    req_validator = Draft202012Validator(req_schema)
    res_validator = Draft202012Validator(res_schema)
    fixtures_dir = SCHEMAS_ROOT / "edit" / "fixtures"

    # Request fixtures
    request_fixtures = list(fixtures_dir.glob("*.request.json"))
    assert len(request_fixtures) >= 3, f"Expected at least 3 edit request fixtures, found {len(request_fixtures)}"
    for fixture_path in request_fixtures:
        req_validator.validate(_load_json(fixture_path))

    # Response fixtures
    response_fixtures = list(fixtures_dir.glob("*.response.json"))
    assert len(response_fixtures) >= 4, f"Expected at least 4 edit response fixtures, found {len(response_fixtures)}"
    for fixture_path in response_fixtures:
        res_validator.validate(_load_json(fixture_path))


# ---------------------------------------------------------------------------
# 5. Golden DSL Examples Integrity Tests
# ---------------------------------------------------------------------------


def test_golden_dsl_examples_integrity() -> None:
    """Verifies all golden DSL example plans in contracts/examples/ load as valid JSON."""
    expected_examples = [
        "l_bracket_example_plan.json",
        "multi_primitive_example_plan.json",
        "spur_gear_example_plan.json",
        "sweep_example_plan.json",
        "placement_example_features.json",
        "side_pad_example_feature.json",
        "accepted_example.json",
        "rejected_example.json",
    ]

    for fname in expected_examples:
        example_path = EXAMPLES_ROOT / fname
        assert example_path.is_file(), f"Missing golden example fixture: {fname}"
        data = _load_json(example_path)
        assert isinstance(data, (dict, list)), f"Expected JSON object or list in {fname}, got {type(data)}"
        assert len(data) > 0, f"Example fixture {fname} is empty"


# ---------------------------------------------------------------------------
# 6. Schema Defensive Limits Negative Tests
# ---------------------------------------------------------------------------


def test_batch_request_schema_rejects_excessive_files() -> None:
    """Verifies batch-request.schema.json rejects requests with more than 500 input files."""
    schema = _load_json(SCHEMAS_ROOT / "batch" / "batch-request.schema.json")
    validator = jsonschema.Draft202012Validator(schema)

    payload = {
        "contract_version": "1.0",
        "request_id": "req-batch-overflow",
        "kind": "batch_operation",
        "input": {
            "root": "C:/cad/models",
            "files": [f"part_{i}.par" for i in range(501)],
        },
        "output_root": "C:/cad/output",
        "operation": {
            "type": "export_3d",
            "formats": ["step"],
        },
    }
    assert not validator.is_valid(payload)
    errors = list(validator.iter_errors(payload))
    assert any("files" in str(e.path) for e in errors)


def test_batch_request_schema_rejects_oversized_path() -> None:
    """Verifies batch-request.schema.json rejects paths exceeding 1024 characters."""
    schema = _load_json(SCHEMAS_ROOT / "batch" / "batch-request.schema.json")
    validator = jsonschema.Draft202012Validator(schema)

    oversized_root = "C:/" + "a" * 1025
    payload = {
        "contract_version": "1.0",
        "request_id": "req-batch-oversized-path",
        "kind": "batch_operation",
        "input": {
            "root": oversized_root,
            "files": ["part_1.par"],
        },
        "output_root": "C:/cad/output",
        "operation": {
            "type": "export_3d",
            "formats": ["step"],
        },
    }
    assert not validator.is_valid(payload)
    errors = list(validator.iter_errors(payload))
    assert any("root" in str(e.path) for e in errors)


def test_batch_request_schema_rejects_empty_formats() -> None:
    """Verifies batch-request.schema.json rejects operations with empty format lists."""
    schema = _load_json(SCHEMAS_ROOT / "batch" / "batch-request.schema.json")
    validator = jsonschema.Draft202012Validator(schema)

    payload = {
        "contract_version": "1.0",
        "request_id": "req-batch-empty-formats",
        "kind": "batch_operation",
        "input": {
            "root": "C:/cad/models",
            "files": ["part_1.par"],
        },
        "output_root": "C:/cad/output",
        "operation": {
            "type": "export_3d",
            "formats": [],
        },
    }
    assert not validator.is_valid(payload)


def test_batch_response_schema_rejects_incomplete_summary() -> None:
    """Verifies batch-response.schema.json requires all 6 summary count fields."""
    schema = _load_json(SCHEMAS_ROOT / "batch" / "batch-response.schema.json")
    validator = jsonschema.Draft202012Validator(schema)

    # Missing 'cancelled' and 'unprocessed' in summary
    payload = {
        "contract_version": "1.0",
        "request_id": "batch-001",
        "status": "completed",
        "summary": {
            "total": 1,
            "accepted": 1,
            "partial": 0,
            "failed": 0,
        },
        "results": [
            {
                "input": "part1.par",
                "status": "accepted",
                "artifacts": [{"format": "step", "path": "p.step"}],
                "warnings": [],
            }
        ],
    }
    assert not validator.is_valid(payload)
