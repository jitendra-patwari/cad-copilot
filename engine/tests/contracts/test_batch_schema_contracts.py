"""Focused automated tests for canonical batch response and batch manifest schemas.

Invariants Verified:
    1. Progressed Failure Semantics: Requires summary, results, and errors; permits absent manifest.
    2. Manifest Reference Requirements: Completed/cancelled require manifest; aliases, extra keys,
       oversized paths, and NULs are rejected.
    3. Warning Acceptance: Completed, cancelled, early-failed, and progressed-failed accept top-level warnings;
       rejected responses strictly reject warnings and manifests.
    4. Format Attribution: Realistic paired cases (STEP+STL, STL+STEP, PDF+DXF, DXF+PDF) validate;
       unapproved format enums are rejected.
    5. Summary Accounting Completeness: All 6 summary counts are required.
    6. Manifest Terminal State Invariants: Completed prohibits errors/cancellations; cancelled requires
       cancelled_files and prohibits errors; failed requires errors and prohibits cancellations.
    7. Defensive Manifest Rules: Portable relative paths enforced; traversal, backslashes, absolute paths,
       malformed hashes, and non-positive sizes are rejected.
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
FIXTURES_DIR = SCHEMAS_ROOT / "batch" / "fixtures"


def _load_json(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Lightweight Payload Builders
# ---------------------------------------------------------------------------


def _summary(
    total: int = 1,
    accepted: int = 0,
    partial: int = 0,
    failed: int = 0,
    unprocessed: int = 0,
    cancelled: int = 0,
) -> dict[str, int]:
    return {
        "total": total,
        "accepted": accepted,
        "partial": partial,
        "failed": failed,
        "unprocessed": unprocessed,
        "cancelled": cancelled,
    }


def _file_result(
    input_file: str,
    status: str,
    artifacts: list[dict[str, str]] | None = None,
    errors: list[dict[str, Any]] | None = None,
    warnings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {"input": input_file, "status": status}
    if artifacts is not None:
        record["artifacts"] = artifacts
    if errors is not None:
        record["errors"] = errors
    if warnings is not None:
        record["warnings"] = warnings
    return record


def _manifest_artifact(
    format_id: str,
    relative_path: str,
    size_bytes: int = 1024,
    sha256: str = "bd391a89e9a0fb566662846f9e11c5ccc9aa7993a51cfb39e9481877ca2baea1",
) -> dict[str, Any]:
    return {
        "format": format_id,
        "relative_path": relative_path,
        "size_bytes": size_bytes,
        "sha256": sha256,
    }


# ---------------------------------------------------------------------------
# 1. Response Schema: Progressed Failure Semantics
# ---------------------------------------------------------------------------


def test_batch_response_schema_progressed_failure_semantics() -> None:
    """Proves progressed failures require summary, results, and errors, and permit absent manifest."""
    res_schema = _load_json(SCHEMAS_ROOT / "batch" / "batch-response.schema.json")
    validator = Draft202012Validator(res_schema)

    base_progressed_fail: dict[str, Any] = {
        "contract_version": "1.0",
        "request_id": "batch-prog-001",
        "status": "failed",
        "summary": _summary(total=2, accepted=1, unprocessed=1),
        "results": [
            _file_result("part1.par", "accepted", artifacts=[{"format": "step", "path": "p1.step"}]),
        ],
        "unprocessed_files": ["part2.par"],
        "errors": [
            {
                "code": "MANIFEST_PUBLICATION_FAILED",
                "message": "Atomic manifest rename failed on output filesystem.",
            }
        ],
    }

    # Valid without manifest (manifest publication itself failed)
    validator.validate(base_progressed_fail)

    # Valid with manifest
    validator.validate({**base_progressed_fail, "manifest": {"path": "C:/out/batch-prog-001.batch_manifest.json"}})

    # Missing errors -> rejected
    no_errors = {k: v for k, v in base_progressed_fail.items() if k != "errors"}
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(no_errors)

    # Missing summary -> rejected
    no_summary = {k: v for k, v in base_progressed_fail.items() if k != "summary"}
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(no_summary)

    # Missing results -> rejected
    no_results = {k: v for k, v in base_progressed_fail.items() if k != "results"}
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(no_results)

    # Early failure with summary -> rejected
    early_fail_with_summary = {
        "contract_version": "1.0",
        "request_id": "batch-early-001",
        "status": "failed",
        "errors": [{"code": "SOLID_EDGE_UNAVAILABLE", "message": "SE not found"}],
        "summary": base_progressed_fail["summary"],
    }
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(early_fail_with_summary)


# ---------------------------------------------------------------------------
# 2. Response Schema: Manifest Reference & Bounds
# ---------------------------------------------------------------------------


def test_batch_response_schema_completed_and_cancelled_require_manifest() -> None:
    """Proves completed and cancelled responses require manifest, reject aliases, bounds, NULs, and extra fields."""
    res_schema = _load_json(SCHEMAS_ROOT / "batch" / "batch-response.schema.json")
    validator = Draft202012Validator(res_schema)

    completed_payload = _load_json(FIXTURES_DIR / "completed.response.json")
    cancelled_payload = _load_json(FIXTURES_DIR / "cancelled_batch.response.json")

    # Missing manifest in completed -> rejected
    no_manifest_completed = {k: v for k, v in completed_payload.items() if k != "manifest"}
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(no_manifest_completed)

    # Alias manifest_path rejected in completed
    with_alias = {**no_manifest_completed, "manifest_path": "C:/out/manifest.json"}
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(with_alias)

    # Extra fields in manifest rejected (additionalProperties: false)
    extra_field_manifest = {
        **completed_payload,
        "manifest": {"path": "C:/out/manifest.json", "extra_token": 123},
    }
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(extra_field_manifest)

    # Manifest path exceeds 1024 characters -> rejected
    oversized_path_manifest = {
        **completed_payload,
        "manifest": {"path": "C:/" + "p" * 1025},
    }
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(oversized_path_manifest)

    # Manifest path contains NUL character -> rejected
    nul_path_manifest = {
        **completed_payload,
        "manifest": {"path": "C:/out/\0manifest.json"},
    }
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(nul_path_manifest)

    # Missing manifest in cancelled -> rejected
    no_manifest_cancelled = {k: v for k, v in cancelled_payload.items() if k != "manifest"}
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(no_manifest_cancelled)


# ---------------------------------------------------------------------------
# 3. Response Schema: Top-Level Warnings Acceptance
# ---------------------------------------------------------------------------


def test_batch_response_schema_top_level_warnings_acceptance() -> None:
    """Proves completed, cancelled, early-failed, and progressed-failed responses accept top-level warnings."""
    res_schema = _load_json(SCHEMAS_ROOT / "batch" / "batch-response.schema.json")
    validator = Draft202012Validator(res_schema)

    sample_warning = [{"code": "VERSION_METADATA_UNAVAILABLE", "message": "Version query warning"}]

    # Completed accepts warnings
    completed = _load_json(FIXTURES_DIR / "completed.response.json")
    completed["warnings"] = sample_warning
    validator.validate(completed)

    # Cancelled accepts warnings
    cancelled = _load_json(FIXTURES_DIR / "cancelled_batch.response.json")
    cancelled["warnings"] = sample_warning
    validator.validate(cancelled)

    # Early failed accepts warnings
    early_failed = _load_json(FIXTURES_DIR / "failed_se_unavailable.response.json")
    early_failed["warnings"] = sample_warning
    validator.validate(early_failed)

    # Progressed failed accepts warnings
    prog_failed = _load_json(FIXTURES_DIR / "failed_after_progress.response.json")
    prog_failed["warnings"] = sample_warning
    validator.validate(prog_failed)


def test_batch_response_schema_rejected_rejects_warnings_and_manifest() -> None:
    """Proves rejected responses strictly reject warnings, manifest, summary, and results."""
    res_schema = _load_json(SCHEMAS_ROOT / "batch" / "batch-response.schema.json")
    validator = Draft202012Validator(res_schema)

    base_rejected = {
        "contract_version": "1.0",
        "request_id": "batch-rej-001",
        "status": "rejected",
        "errors": [{"code": "INVALID_SCHEMA", "message": "Schema violation"}],
    }
    validator.validate(base_rejected)

    # Rejected with warnings -> rejected
    with pytest.raises(jsonschema.ValidationError):
        validator.validate({**base_rejected, "warnings": [{"code": "VERSION_METADATA_UNAVAILABLE", "message": "warn"}]})

    # Rejected with manifest -> rejected
    with pytest.raises(jsonschema.ValidationError):
        validator.validate({**base_rejected, "manifest": {"path": "C:/out/manifest.json"}})


# ---------------------------------------------------------------------------
# 4. Response Schema: Realistic Paired Format Attribution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("input_ext", "success_fmt", "failed_fmt"),
    [
        ("par", "step", "stl"),
        ("par", "stl", "step"),
        ("dft", "pdf", "dxf"),
        ("dft", "dxf", "pdf"),
    ],
)
def test_batch_response_schema_realistic_partial_format_attribution(
    input_ext: str, success_fmt: str, failed_fmt: str
) -> None:
    """Proves realistic paired format attribution in partial results (STEP+STL, STL+STEP, PDF+DXF, DXF+PDF)."""
    res_schema = _load_json(SCHEMAS_ROOT / "batch" / "batch-response.schema.json")
    validator = Draft202012Validator(res_schema)

    payload = {
        "contract_version": "1.0",
        "request_id": f"batch-fmt-{success_fmt}-{failed_fmt}",
        "status": "completed",
        "summary": _summary(total=1, partial=1),
        "results": [
            _file_result(
                f"sample.{input_ext}",
                "partial",
                artifacts=[{"format": success_fmt, "path": f"C:/out/sample.{success_fmt}"}],
                errors=[
                    {
                        "code": "ARTIFACT_EXPORT_FAILED",
                        "message": f"Export failed for format {failed_fmt}",
                        "format": failed_fmt,
                    }
                ],
            )
        ],
        "manifest": {"path": "C:/out/manifest.json"},
    }
    validator.validate(payload)


def test_batch_response_schema_progressed_failure_format_attribution() -> None:
    """Proves progressed failures attribute format on attempted files and accept batch-level fatal codes."""
    res_schema = _load_json(SCHEMAS_ROOT / "batch" / "batch-response.schema.json")
    validator = Draft202012Validator(res_schema)

    # Progressed failure where file 1 succeeded, file 2 suffered fatal export failure
    payload = {
        "contract_version": "1.0",
        "request_id": "batch-prog-fail-fmt",
        "status": "failed",
        "summary": _summary(total=3, accepted=1, failed=1, unprocessed=1),
        "results": [
            _file_result("part1.par", "accepted", artifacts=[{"format": "step", "path": "C:/out/part1.step"}]),
            _file_result(
                "part2.par",
                "failed",
                errors=[
                    {
                        "code": "ARTIFACT_EXPORT_FAILED",
                        "message": "Fatal export error on STL",
                        "format": "stl",
                    }
                ],
            ),
        ],
        "unprocessed_files": ["part3.par"],
        "errors": [
            {
                "code": "SOLID_EDGE_UNHEALTHY",
                "message": "Solid Edge session became unresponsive after STL export failure.",
            }
        ],
        "manifest": {"path": "C:/out/manifest.json"},
    }
    validator.validate(payload)


@pytest.mark.parametrize("bad_fmt", ["dwg", "iges", "par", "jt", ""])
def test_batch_response_schema_rejects_unapproved_formats(bad_fmt: str) -> None:
    """Proves errorRecord rejects unapproved or blank format strings."""
    res_schema = _load_json(SCHEMAS_ROOT / "batch" / "batch-response.schema.json")
    validator = Draft202012Validator(res_schema)

    payload = {
        "contract_version": "1.0",
        "request_id": "batch-fmt-bad",
        "status": "completed",
        "summary": _summary(total=1, partial=1),
        "results": [
            _file_result(
                "part1.par",
                "partial",
                artifacts=[{"format": "step", "path": "C:/out/part1.step"}],
                errors=[
                    {
                        "code": "ARTIFACT_EXPORT_FAILED",
                        "message": "Export failed",
                        "format": bad_fmt,
                    }
                ],
            )
        ],
        "manifest": {"path": "C:/out/manifest.json"},
    }
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(payload)


def test_batch_response_schema_accepts_parasolid_format() -> None:
    """Proves response schema artifactRecord and errorRecord accept promoted parasolid format."""
    res_schema = _load_json(SCHEMAS_ROOT / "batch" / "batch-response.schema.json")
    validator = Draft202012Validator(res_schema)

    payload = {
        "contract_version": "1.0",
        "request_id": "batch-fmt-parasolid",
        "status": "completed",
        "summary": _summary(total=1, partial=1),
        "results": [
            _file_result(
                "part1.par",
                "partial",
                artifacts=[{"format": "parasolid", "path": "C:/out/part1.x_t"}],
                errors=[
                    {
                        "code": "ARTIFACT_EXPORT_FAILED",
                        "message": "Export failed",
                        "format": "parasolid",
                    }
                ],
            )
        ],
        "manifest": {"path": "C:/out/manifest.json"},
    }
    validator.validate(payload)


# ---------------------------------------------------------------------------
# 5. Response Schema: Summary Completeness
# ---------------------------------------------------------------------------


def test_batch_response_schema_rejects_incomplete_summary() -> None:
    """Verifies batch-response.schema.json requires all 6 summary count fields."""
    schema = _load_json(SCHEMAS_ROOT / "batch" / "batch-response.schema.json")
    validator = Draft202012Validator(schema)

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
            _file_result("part1.par", "accepted", artifacts=[{"format": "step", "path": "p.step"}]),
        ],
        "manifest": {"path": "C:/cad/output/batch-001.batch_manifest.json"},
    }
    assert not validator.is_valid(payload)


# ---------------------------------------------------------------------------
# 6. Manifest Schema: Terminal State Invariants
# ---------------------------------------------------------------------------


def test_batch_manifest_schema_terminal_state_invariants() -> None:
    """Proves batch-manifest-v1.schema.json enforces terminal status invariants across completed, cancelled, and failed."""
    manifest_schema = _load_json(SCHEMAS_ROOT / "batch" / "batch-manifest-v1.schema.json")
    validator = Draft202012Validator(manifest_schema)

    completed_manifest = _load_json(FIXTURES_DIR / "completed.batch_manifest.json")
    cancelled_manifest = _load_json(FIXTURES_DIR / "cancelled.batch_manifest.json")
    failed_manifest = _load_json(FIXTURES_DIR / "failed_after_progress.batch_manifest.json")

    # 1. Completed cannot have cancelled_files or errors
    with pytest.raises(jsonschema.ValidationError):
        validator.validate({**completed_manifest, "cancelled_files": ["part2.par"]})
    with pytest.raises(jsonschema.ValidationError):
        validator.validate({**completed_manifest, "errors": [{"code": "INTERNAL_ERROR", "message": "error"}]})

    # 2. Cancelled requires cancelled_files and cannot have errors
    no_cancelled_files = {k: v for k, v in cancelled_manifest.items() if k != "cancelled_files"}
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(no_cancelled_files)
    with pytest.raises(jsonschema.ValidationError):
        validator.validate({**cancelled_manifest, "errors": [{"code": "INTERNAL_ERROR", "message": "error"}]})

    # 3. Failed requires errors and cannot have cancelled_files
    no_errors = {k: v for k, v in failed_manifest.items() if k != "errors"}
    with pytest.raises(jsonschema.ValidationError):
        validator.validate(no_errors)
    with pytest.raises(jsonschema.ValidationError):
        validator.validate({**failed_manifest, "cancelled_files": ["part2.par"]})


# ---------------------------------------------------------------------------
# 7. Manifest Schema: Defensive Rules
# ---------------------------------------------------------------------------


def test_batch_manifest_schema_defensive_negative_rules() -> None:
    """Proves batch-manifest-v1.schema.json enforces relative portable paths, hash shape, and positive sizes."""
    manifest_schema = _load_json(SCHEMAS_ROOT / "batch" / "batch-manifest-v1.schema.json")
    validator = Draft202012Validator(manifest_schema)

    valid_base: dict[str, Any] = {
        "manifest_version": "1.0",
        "contract_version": "1.0",
        "request_id": "batch-manifest-test-01",
        "status": "completed",
        "operation": {
            "type": "export_3d",
            "formats": ["step"],
        },
        "summary": _summary(total=1, accepted=1),
        "results": [
            {
                "input": "part1.par",
                "status": "accepted",
                "artifacts": [_manifest_artifact("step", "part1.step")],
            }
        ],
        "engine_version": "0.1.0",
    }
    validator.validate(valid_base)

    # 1. Reject absolute artifact path
    for bad_path in ["/absolute/part1.step", "C:/part1.step", "\\\\server\\share\\part1.step"]:
        bad_manifest = json.loads(json.dumps(valid_base))
        bad_manifest["results"][0]["artifacts"][0]["relative_path"] = bad_path
        assert not validator.is_valid(bad_manifest)

    # 2. Reject traversal in artifact path
    for traversal_path in ["../part1.step", "sub/../../part1.step", "sub/../part1.step"]:
        bad_manifest = json.loads(json.dumps(valid_base))
        bad_manifest["results"][0]["artifacts"][0]["relative_path"] = traversal_path
        assert not validator.is_valid(bad_manifest)

    # 3. Reject backslash in artifact path
    bad_manifest = json.loads(json.dumps(valid_base))
    bad_manifest["results"][0]["artifacts"][0]["relative_path"] = "sub\\part1.step"
    assert not validator.is_valid(bad_manifest)

    # 4. Reject malformed SHA-256 (uppercase, short, non-hex)
    for bad_hash in [
        "BD391A89E9A0FB566662846F9E11C5CCC9AA7993A51CFB39E9481877CA2BAEA1",  # uppercase
        "e3b0c442",  # too short
        "z" * 64,  # non-hex
    ]:
        bad_manifest = json.loads(json.dumps(valid_base))
        bad_manifest["results"][0]["artifacts"][0]["sha256"] = bad_hash
        assert not validator.is_valid(bad_manifest)

    # 5. Reject zero or negative size_bytes, or bool
    for bad_size in [0, -100, True]:
        bad_manifest = json.loads(json.dumps(valid_base))
        bad_manifest["results"][0]["artifacts"][0]["size_bytes"] = bad_size
        assert not validator.is_valid(bad_manifest)

    # 6. Reject unexpected top-level property (additionalProperties: false)
    bad_manifest = json.loads(json.dumps(valid_base))
    bad_manifest["unexpected_field"] = "not_allowed"
    assert not validator.is_valid(bad_manifest)

    # 7. Promoted parasolid format is valid; unapproved format (jt) is rejected
    promoted_manifest = json.loads(json.dumps(valid_base))
    promoted_manifest["operation"]["formats"] = ["step", "stl", "parasolid"]
    assert validator.is_valid(promoted_manifest)

    bad_manifest = json.loads(json.dumps(valid_base))
    bad_manifest["operation"]["formats"] = ["jt"]
    assert not validator.is_valid(bad_manifest)


# ---------------------------------------------------------------------------
# 8. Cancellation Schema Semantics
# ---------------------------------------------------------------------------


def test_batch_response_schema_permits_empty_cancelled_files() -> None:
    """Proves batch-response.schema.json permits empty cancelled_files array in cancelled status."""
    res_schema = _load_json(SCHEMAS_ROOT / "batch" / "batch-response.schema.json")
    validator = Draft202012Validator(res_schema)

    payload: dict[str, Any] = {
        "contract_version": "1.0",
        "request_id": "batch-cancel-empty-001",
        "status": "cancelled",
        "summary": _summary(total=1, partial=1),
        "results": [
            _file_result(
                "part1.par",
                "partial",
                artifacts=[{"format": "step", "path": "C:/out/part1.step"}],
                errors=[{"code": "BATCH_CANCELLED", "message": "cancelled", "format": "stl"}],
            )
        ],
        "cancelled_files": [],
        "manifest": {"path": "C:/out/manifest.json"},
    }
    validator.validate(payload)


def test_batch_manifest_schema_permits_empty_cancelled_files() -> None:
    """Proves batch-manifest-v1.schema.json permits empty cancelled_files array in cancelled status."""
    manifest_schema = _load_json(SCHEMAS_ROOT / "batch" / "batch-manifest-v1.schema.json")
    validator = Draft202012Validator(manifest_schema)

    payload: dict[str, Any] = {
        "manifest_version": "1.0",
        "contract_version": "1.0",
        "request_id": "batch-cancel-empty-001",
        "status": "cancelled",
        "operation": {"type": "export_3d", "formats": ["step", "stl"]},
        "summary": _summary(total=1, partial=1),
        "results": [
            {
                "input": "part1.par",
                "status": "partial",
                "artifacts": [_manifest_artifact("step", "part1.step")],
                "errors": [{"code": "BATCH_CANCELLED", "message": "cancelled", "format": "stl"}],
            }
        ],
        "cancelled_files": [],
        "engine_version": "0.1.0",
    }
    validator.validate(payload)


def test_batch_schemas_accept_batch_cancelled_error_code() -> None:
    """Proves BATCH_CANCELLED is recognized in errorRecord code enum across response and manifest schemas."""
    res_schema = _load_json(SCHEMAS_ROOT / "batch" / "batch-response.schema.json")
    man_schema = _load_json(SCHEMAS_ROOT / "batch" / "batch-manifest-v1.schema.json")
    res_validator = Draft202012Validator(res_schema)
    man_validator = Draft202012Validator(man_schema)

    res_payload: dict[str, Any] = {
        "contract_version": "1.0",
        "request_id": "batch-code-001",
        "status": "cancelled",
        "summary": _summary(total=1, partial=1),
        "results": [
            _file_result(
                "p.par",
                "partial",
                artifacts=[{"format": "step", "path": "C:/out/p.step"}],
                errors=[{"code": "BATCH_CANCELLED", "message": "cancelled", "format": "stl"}],
            )
        ],
        "cancelled_files": [],
        "manifest": {"path": "C:/out/manifest.json"},
    }
    res_validator.validate(res_payload)

    # Reject unapproved code
    bad_res = json.loads(json.dumps(res_payload))
    bad_res["results"][0]["errors"][0]["code"] = "UNAPPROVED_CODE"
    with pytest.raises(jsonschema.ValidationError):
        res_validator.validate(bad_res)

    man_payload: dict[str, Any] = {
        "manifest_version": "1.0",
        "contract_version": "1.0",
        "request_id": "batch-code-001",
        "status": "cancelled",
        "operation": {"type": "export_3d", "formats": ["step", "stl"]},
        "summary": _summary(total=1, partial=1),
        "results": [
            {
                "input": "p.par",
                "status": "partial",
                "artifacts": [_manifest_artifact("step", "p.step")],
                "errors": [{"code": "BATCH_CANCELLED", "message": "cancelled", "format": "stl"}],
            }
        ],
        "cancelled_files": [],
        "engine_version": "0.1.0",
    }
    man_validator.validate(man_payload)

    bad_man = json.loads(json.dumps(man_payload))
    bad_man["results"][0]["errors"][0]["code"] = "UNAPPROVED_CODE"
    with pytest.raises(jsonschema.ValidationError):
        man_validator.validate(bad_man)
