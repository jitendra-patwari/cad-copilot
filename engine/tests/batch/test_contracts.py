"""Contract, schema parity, fixture round-trips, and projection tests for batch processing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from batch.contracts import (
    get_batch_manifest_validator,
    get_batch_request_validator,
    get_batch_response_validator,
    load_batch_manifest_schema,
    load_batch_request_schema,
    load_batch_response_schema,
    parse_batch_manifest,
    parse_batch_request,
    parse_batch_response,
    project_batch_manifest,
    project_batch_request,
    project_batch_response,
)
from batch.models import (
    BatchArtifactRecord,
    BatchDiagnostic,
    BatchFileResult,
    BatchInputSelection,
    BatchManifest,
    BatchManifestReference,
    BatchOperation,
    BatchRequest,
    BatchResponse,
    BatchSummary,
    BatchValidationError,
    ManifestArtifactRecord,
    ManifestFileResult,
)

CANONICAL_DIR: Path = Path(__file__).resolve().parents[3] / "contracts" / "schemas" / "batch"
PACKAGED_DIR: Path = Path(__file__).resolve().parents[2] / "src" / "batch" / "schemas"
FIXTURES_DIR: Path = CANONICAL_DIR / "fixtures"

SCHEMA_NAMES: tuple[str, ...] = (
    "batch-request.schema.json",
    "batch-response.schema.json",
    "batch-manifest-v1.schema.json",
)


class TestSchemaPackagingAndParity:
    """Byte-for-byte schema parity between canonical and packaged copies, and package loading."""

    @pytest.mark.parametrize("schema_name", SCHEMA_NAMES)
    def test_schema_files_byte_for_byte_parity(self, schema_name: str) -> None:
        canonical_path = CANONICAL_DIR / schema_name
        packaged_path = PACKAGED_DIR / schema_name

        assert canonical_path.exists(), f"Canonical schema missing: {canonical_path}"
        assert packaged_path.exists(), f"Packaged schema missing: {packaged_path}"

        canonical_bytes = canonical_path.read_bytes()
        packaged_bytes = packaged_path.read_bytes()

        assert canonical_bytes == packaged_bytes, f"Byte-for-byte mismatch between {canonical_path} and {packaged_path}"

    def test_packaged_schemas_load_via_importlib(self) -> None:
        req_schema = load_batch_request_schema()
        resp_schema = load_batch_response_schema()
        man_schema = load_batch_manifest_schema()

        assert req_schema["$id"] == "https://cad-copilot.dev/schemas/batch-request.schema.json"
        assert resp_schema["$id"] == "https://cad-copilot.dev/schemas/batch-response.schema.json"
        assert man_schema["$id"] == "https://cad-copilot.dev/schemas/batch-manifest-v1.schema.json"

    def test_schema_isolation_prevents_cache_corruption(self) -> None:
        schema1 = load_batch_request_schema()
        schema1["mutated"] = True

        schema2 = load_batch_request_schema()
        assert "mutated" not in schema2

    def test_cached_validators_return_singleton(self) -> None:
        v1 = get_batch_request_validator()
        v2 = get_batch_request_validator()
        assert v1 is v2

        v3 = get_batch_response_validator()
        v4 = get_batch_response_validator()
        assert v3 is v4

        v5 = get_batch_manifest_validator()
        v6 = get_batch_manifest_validator()
        assert v5 is v6


class TestRequestFixtureRoundTrips:
    """Round-trip conversion and validation for positive and negative request fixtures."""

    @pytest.mark.parametrize(
        "fixture_name",
        [
            "basic_export.request.json",
            "publish_drawing.request.json",
        ],
    )
    def test_positive_request_fixtures(self, fixture_name: str) -> None:
        raw_bytes = (FIXTURES_DIR / fixture_name).read_bytes()
        data: dict[str, Any] = json.loads(raw_bytes.decode("utf-8"))

        req = parse_batch_request(data)
        assert req.contract_version == "1.0"
        assert req.kind == "batch_operation"
        assert len(req.input.files) >= 1

        projected = project_batch_request(req)
        assert projected["request_id"] == data["request_id"]
        assert projected["operation"]["type"] == data["operation"]["type"]

    @pytest.mark.parametrize(
        ("fixture_name", "expected_code"),
        [
            ("rejected_deferred_flat_pattern.request.json", "INVALID_SCHEMA"),
            ("rejected_unverified_format.request.json", "INVALID_SCHEMA"),
            ("rejected_no_files.request.json", "INVALID_SCHEMA"),
            ("rejected_unsafe_path.request.json", "INPUT_PATH_NOT_ALLOWED"),
        ],
    )
    def test_negative_request_fixtures(self, fixture_name: str, expected_code: str) -> None:
        raw_bytes = (FIXTURES_DIR / fixture_name).read_bytes()
        data: dict[str, Any] = json.loads(raw_bytes.decode("utf-8"))

        with pytest.raises(BatchValidationError) as exc_info:
            parse_batch_request(data)
        assert exc_info.value.code == expected_code


class TestResponseFixtureRoundTrips:
    """Round-trip conversion and validation for positive and negative response fixtures."""

    @pytest.mark.parametrize(
        "fixture_name",
        [
            "completed.response.json",
            "partial_batch.response.json",
            "continue_on_error_stop.response.json",
            "cancelled_batch.response.json",
            "failed_se_unavailable.response.json",
            "failed_after_progress.response.json",
        ],
    )
    def test_positive_response_fixtures(self, fixture_name: str) -> None:
        raw_bytes = (FIXTURES_DIR / fixture_name).read_bytes()
        data: dict[str, Any] = json.loads(raw_bytes.decode("utf-8"))

        resp = parse_batch_response(data)
        assert resp.contract_version == "1.0"
        assert resp.status == data["status"]

        projected = project_batch_response(resp)
        assert projected["status"] == data["status"]
        assert projected["request_id"] == data["request_id"]

    @pytest.mark.parametrize(
        "fixture_name",
        [
            "rejected_no_files.response.json",
            "rejected_unsafe_path.response.json",
        ],
    )
    def test_rejected_response_fixtures(self, fixture_name: str) -> None:
        raw_bytes = (FIXTURES_DIR / fixture_name).read_bytes()
        data: dict[str, Any] = json.loads(raw_bytes.decode("utf-8"))

        resp = parse_batch_response(data)
        assert resp.status == "rejected"
        assert len(resp.errors) >= 1

        projected = project_batch_response(resp)
        assert projected["status"] == "rejected"
        assert len(projected["errors"]) >= 1


class TestManifestFixtureRoundTrips:
    """Round-trip conversion and validation for positive manifest fixtures."""

    @pytest.mark.parametrize(
        "fixture_name",
        [
            "completed.batch_manifest.json",
            "cancelled.batch_manifest.json",
            "failed_after_progress.batch_manifest.json",
        ],
    )
    def test_positive_manifest_fixtures(self, fixture_name: str) -> None:
        raw_bytes = (FIXTURES_DIR / fixture_name).read_bytes()
        data: dict[str, Any] = json.loads(raw_bytes.decode("utf-8"))

        manifest = parse_batch_manifest(data)
        assert manifest.manifest_version == "1.0"
        assert manifest.contract_version == "1.0"
        assert manifest.status == data["status"]

        projected = project_batch_manifest(manifest)
        assert projected["status"] == data["status"]
        assert projected["request_id"] == data["request_id"]
        assert projected["engine_version"] == data["engine_version"]


class TestContractStrictnessAndSecurity:
    """Negative and boundary conditions enforcing safety and schema strictness."""

    def test_warning_code_strictness_rejects_error_code_in_warnings(self) -> None:
        payload: dict[str, Any] = {
            "contract_version": "1.0",
            "request_id": "req-1",
            "status": "completed",
            "summary": {
                "total": 1,
                "accepted": 1,
                "partial": 0,
                "failed": 0,
                "unprocessed": 0,
                "cancelled": 0,
            },
            "results": [
                {
                    "input": "p.par",
                    "status": "accepted",
                    "artifacts": [{"format": "step", "path": "out/p.step"}],
                    "warnings": [{"code": "INTERNAL_ERROR", "message": "not a warning"}],
                }
            ],
            "manifest": {"path": "out/m.json"},
        }
        with pytest.raises(BatchValidationError, match="not an approved warning code"):
            parse_batch_response(payload)

    def test_completed_response_forbids_cancelled_files(self) -> None:
        payload: dict[str, Any] = {
            "contract_version": "1.0",
            "request_id": "req-1",
            "status": "completed",
            "summary": {
                "total": 2,
                "accepted": 1,
                "partial": 0,
                "failed": 0,
                "unprocessed": 0,
                "cancelled": 1,
            },
            "results": [
                {
                    "input": "p.par",
                    "status": "accepted",
                    "artifacts": [{"format": "step", "path": "out/p.step"}],
                }
            ],
            "cancelled_files": ["p2.par"],
            "manifest": {"path": "out/m.json"},
        }
        with pytest.raises(BatchValidationError):
            parse_batch_response(payload)

    def test_cancelled_response_requires_cancelled_files(self) -> None:
        payload: dict[str, Any] = {
            "contract_version": "1.0",
            "request_id": "req-1",
            "status": "cancelled",
            "summary": {
                "total": 1,
                "accepted": 1,
                "partial": 0,
                "failed": 0,
                "unprocessed": 0,
                "cancelled": 0,
            },
            "results": [
                {
                    "input": "p.par",
                    "status": "accepted",
                    "artifacts": [{"format": "step", "path": "out/p.step"}],
                }
            ],
            "manifest": {"path": "out/m.json"},
        }
        with pytest.raises(BatchValidationError):
            parse_batch_response(payload)


class TestSchemaFailureNonReflectionAndBounding:
    """Proves that schema failures produce bounded stable messages and never reflect untrusted keys/values."""

    def test_request_schema_failure_does_not_reflect_hostile_key(self) -> None:
        hostile_key = "x" * 2104
        payload = {
            "contract_version": "1.0",
            "request_id": "req-1",
            "kind": "batch_operation",
            "input": {"root": "C:/data", "files": ["p.par"]},
            "output_root": "C:/out",
            "operation": {"type": "export_3d", "formats": ["step"]},
            hostile_key: "untrusted_payload",
        }
        with pytest.raises(BatchValidationError) as exc_info:
            parse_batch_request(payload)

        assert exc_info.value.code == "INVALID_SCHEMA"
        assert exc_info.value.message == "Batch request failed schema validation."
        assert len(exc_info.value.message) <= 512
        assert hostile_key not in exc_info.value.message
        assert "untrusted_payload" not in exc_info.value.message

    def test_response_schema_failure_does_not_reflect_hostile_key(self) -> None:
        hostile_key = "evil_property_" + ("y" * 2000)
        payload = {
            "contract_version": "1.0",
            "request_id": "req-1",
            "status": "rejected",
            "errors": [{"code": "INVALID_SCHEMA", "message": "Rejected"}],
            hostile_key: 12345,
        }
        with pytest.raises(BatchValidationError) as exc_info:
            parse_batch_response(payload)

        assert exc_info.value.code == "INVALID_SCHEMA"
        assert exc_info.value.message == "Batch response failed schema validation."
        assert len(exc_info.value.message) <= 512
        assert hostile_key not in exc_info.value.message

    def test_manifest_schema_failure_does_not_reflect_hostile_key(self) -> None:
        hostile_key = "hostile_meta_" + ("z" * 2000)
        payload = {
            "manifest_version": "1.0",
            "contract_version": "1.0",
            "request_id": "req-1",
            "status": "completed",
            "operation": {"type": "export_3d", "formats": ["step"]},
            "summary": {"total": 0, "accepted": 0, "partial": 0, "failed": 0, "unprocessed": 0, "cancelled": 0},
            "results": [],
            "engine_version": "0.1.0",
            hostile_key: "danger",
        }
        with pytest.raises(BatchValidationError) as exc_info:
            parse_batch_manifest(payload)

        assert exc_info.value.code == "INVALID_SCHEMA"
        assert exc_info.value.message == "Batch manifest failed schema validation."
        assert len(exc_info.value.message) <= 512
        assert hostile_key not in exc_info.value.message


class TestAdversarialProjectionValidation:
    """Proves that project_batch_response and project_batch_manifest enforce semantic invariants before wire emission."""

    def test_projector_rejects_completed_response_with_cancelled_mismatch(self) -> None:
        """Completed response reporting cancelled count bypassed via object.__setattr__ is rejected."""
        resp = BatchResponse(
            contract_version="1.0",
            request_id="req-p-1",
            status="completed",
            summary=BatchSummary(total=1, accepted=1, partial=0, failed=0, unprocessed=0, cancelled=0),
            results=(
                BatchFileResult(
                    input="a.par",
                    status="accepted",
                    artifacts=(BatchArtifactRecord(format="step", path="a.step"),),
                ),
            ),
            manifest=BatchManifestReference(path="out/m.json"),
        )
        object.__setattr__(
            resp,
            "summary",
            BatchSummary(total=2, accepted=1, partial=0, failed=0, unprocessed=0, cancelled=1),
        )

        with pytest.raises(BatchValidationError, match="Completed response must have zero cancelled files"):
            project_batch_response(resp)

    def test_projector_rejects_internal_error_in_warnings(self) -> None:
        """Error-only code inside warnings bypassed via object.__setattr__ is rejected."""
        resp = BatchResponse(
            contract_version="1.0",
            request_id="req-p-2",
            status="completed",
            summary=BatchSummary(total=1, accepted=1, partial=0, failed=0, unprocessed=0, cancelled=0),
            results=(
                BatchFileResult(
                    input="a.par",
                    status="accepted",
                    artifacts=(BatchArtifactRecord(format="step", path="a.step"),),
                ),
            ),
            manifest=BatchManifestReference(path="out/m.json"),
        )
        object.__setattr__(
            resp,
            "warnings",
            (BatchDiagnostic(code="INTERNAL_ERROR", message="sneaky error"),),
        )

        with pytest.raises(BatchValidationError, match="not permitted as a warning"):
            project_batch_response(resp)

    def test_projector_rejects_completed_response_with_errors(self) -> None:
        resp = BatchResponse(
            contract_version="1.0",
            request_id="req-p-3",
            status="completed",
            summary=BatchSummary(total=1, accepted=1, partial=0, failed=0, unprocessed=0, cancelled=0),
            results=(
                BatchFileResult(
                    input="a.par",
                    status="accepted",
                    artifacts=(BatchArtifactRecord(format="step", path="a.step"),),
                ),
            ),
            manifest=BatchManifestReference(path="out/m.json"),
        )
        object.__setattr__(
            resp,
            "errors",
            (BatchDiagnostic(code="INTERNAL_ERROR", message="error"),),
        )

        with pytest.raises(BatchValidationError, match="must not have top-level errors"):
            project_batch_response(resp)

    def test_projector_rejects_manifest_with_accounting_mismatch(self) -> None:
        man = BatchManifest(
            manifest_version="1.0",
            contract_version="1.0",
            request_id="req-p-4",
            status="completed",
            operation=BatchOperation(type="export_3d", formats=("step",)),
            summary=BatchSummary(total=1, accepted=1, partial=0, failed=0, unprocessed=0, cancelled=0),
            results=(
                ManifestFileResult(
                    input="a.par",
                    status="accepted",
                    artifacts=(
                        ManifestArtifactRecord(format="step", relative_path="a.step", size_bytes=10, sha256="0" * 64),
                    ),
                ),
            ),
            engine_version="0.1.0",
        )
        object.__setattr__(
            man,
            "summary",
            BatchSummary(total=2, accepted=1, partial=0, failed=0, unprocessed=1, cancelled=0),
        )

        with pytest.raises(BatchValidationError, match="Unprocessed files length"):
            project_batch_manifest(man)

    def test_project_batch_request_enforces_path_safety(self) -> None:
        """project_batch_request rejects traversal even if bypassed into BatchRequest."""
        req = BatchRequest(
            contract_version="1.0",
            request_id="req-p-safe-1",
            kind="batch_operation",
            input=BatchInputSelection(root="C:/data", files=("part.par",)),
            output_root="C:/data/out",
            operation=BatchOperation(type="export_3d", formats=("step",)),
        )
        object.__setattr__(
            req,
            "input",
            BatchInputSelection(root="C:/data", files=("../secret.par",)),
        )

        with pytest.raises(BatchValidationError, match="forbidden traversal"):
            project_batch_request(req)

    def test_project_batch_request_enforces_canonical_posix(self) -> None:
        """project_batch_request rejects backslash paths if bypassed."""
        req = BatchRequest(
            contract_version="1.0",
            request_id="req-p-safe-2",
            kind="batch_operation",
            input=BatchInputSelection(root="C:/data", files=("part.par",)),
            output_root="C:/data/out",
            operation=BatchOperation(type="export_3d", formats=("step",)),
        )
        object.__setattr__(
            req,
            "input",
            BatchInputSelection(root="C:/data", files=("sub\\part.par",)),
        )

        with pytest.raises(BatchValidationError, match="canonical POSIX relative format"):
            project_batch_request(req)

    def test_project_batch_request_enforces_extension_compatibility(self) -> None:
        """project_batch_request rejects drawing files under export_3d if bypassed."""
        req = BatchRequest(
            contract_version="1.0",
            request_id="req-p-safe-3",
            kind="batch_operation",
            input=BatchInputSelection(root="C:/data", files=("part.par",)),
            output_root="C:/data/out",
            operation=BatchOperation(type="export_3d", formats=("step",)),
        )
        object.__setattr__(
            req,
            "input",
            BatchInputSelection(root="C:/data", files=("drawing.dft",)),
        )

        with pytest.raises(BatchValidationError, match="not permitted for export_3d"):
            project_batch_request(req)
