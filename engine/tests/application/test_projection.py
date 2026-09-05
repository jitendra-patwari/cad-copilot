"""Tests for application models and pure response projection.

Verifies:
    - Immutability and defensive invariants of application models.
    - Strict validation of untrusted proposal warning containers and items.
    - Preservation of typed diagnostics, applied defaults, and metadata in PreparedPlanContext.
    - Strict Draft 2020-12 schema conformance of accepted, rejected, and failed responses.
    - Status-specific error classification (REJECTED_ERROR_CODES vs FAILED_ERROR_CODES).
    - Stable public error messages without arbitrary message bypass.
    - Canonical artifact ordering (.par -> .step -> .stl -> .jpg) and origin enforcement.
    - Mapping of recognized structured warning codes to stable public descriptions.
    - Fail-closed redaction of arbitrary prompts, API keys, UNC paths, and workstation paths.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from application.models import (
    ExampleGenerationRequest,
    ExecutorFactory,
    PlanProposal,
    PreparedPlanContext,
    PromptGenerationRequest,
)
from application.projection import (
    FAILED_ERROR_CODES,
    GENERIC_WARNING_MESSAGE,
    PUBLIC_ERROR_MESSAGES,
    PUBLIC_WARNING_MESSAGES,
    REJECTED_ERROR_CODES,
    ArtifactProjectionError,
    build_accepted_response,
    build_failed_response,
    build_rejected_response,
    project_artifacts,
    project_warning,
    project_warnings,
)
from geometry.plan_models import (
    DefaultApplied,
    FeaturePlan,
    PartMetadata,
    RectangularBaseBody,
    ValidationDiagnostic,
)
from interfaces.models import ArtifactRecord

SCHEMAS_ROOT = Path(__file__).resolve().parents[3] / "contracts" / "schemas"


@pytest.fixture(scope="module")
def response_schema_validator() -> Draft202012Validator:
    schema_path = SCHEMAS_ROOT / "generation" / "generation-response.schema.json"
    with open(schema_path, encoding="utf-8") as f:
        schema = json.load(f)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


# ---------------------------------------------------------------------------
# 1. Application Models Tests
# ---------------------------------------------------------------------------


class TestApplicationModels:
    def test_example_generation_request_valid(self) -> None:
        req = ExampleGenerationRequest(
            contract_version="1.0",
            request_id="req-123",
            kind="example_plan",
            unit="mm",
            example_id="spur_gear",
            metadata={"caller": "test"},
        )
        assert req.request_id == "req-123"
        assert req.example_id == "spur_gear"
        assert req.kind == "example_plan"

    def test_example_generation_request_invalid_version(self) -> None:
        with pytest.raises(ValueError, match="Unsupported contract_version"):
            ExampleGenerationRequest(
                contract_version="2.0",  # type: ignore[arg-type]
                request_id="req-123",
                kind="example_plan",
                unit="mm",
                example_id="spur_gear",
            )

    def test_example_generation_request_invalid_kind(self) -> None:
        with pytest.raises(ValueError, match="Invalid kind"):
            ExampleGenerationRequest(
                contract_version="1.0",
                request_id="req-123",
                kind="prompt_to_cad",  # type: ignore[arg-type]
                unit="mm",
                example_id="spur_gear",
            )

    def test_example_generation_request_invalid_unit(self) -> None:
        with pytest.raises(ValueError, match="Unsupported unit"):
            ExampleGenerationRequest(
                contract_version="1.0",
                request_id="req-123",
                kind="example_plan",
                unit="inch",  # type: ignore[arg-type]
                example_id="spur_gear",
            )

    def test_example_generation_request_invalid_request_id(self) -> None:
        with pytest.raises(ValueError, match="request_id must be a string"):
            ExampleGenerationRequest(
                contract_version="1.0",
                request_id="",
                kind="example_plan",
                unit="mm",
                example_id="spur_gear",
            )

    def test_example_generation_request_invalid_example_id(self) -> None:
        with pytest.raises(ValueError, match="example_id must be a non-empty string"):
            ExampleGenerationRequest(
                contract_version="1.0",
                request_id="req-123",
                kind="example_plan",
                unit="mm",
                example_id="   ",
            )

    def test_prompt_generation_request_valid(self) -> None:
        req = PromptGenerationRequest(
            contract_version="1.0",
            request_id="req-456",
            kind="prompt_to_cad",
            unit="mm",
            prompt="A rectangular plate 100x50x10mm",
        )
        assert req.request_id == "req-456"
        assert req.prompt == "A rectangular plate 100x50x10mm"

    def test_prompt_generation_request_invalid_prompt(self) -> None:
        with pytest.raises(ValueError, match="prompt must be a non-empty string"):
            PromptGenerationRequest(
                contract_version="1.0",
                request_id="req-456",
                kind="prompt_to_cad",
                unit="mm",
                prompt="   ",
            )

    def test_plan_proposal_valid(self) -> None:
        proposal = PlanProposal(
            plan_payload={"plan_version": "1.0", "features": []},
            provenance="ai_proposal",
            source_id="gemini-flash",
            warnings=({"code": "GATE_POLICY_RELAXED"}, {"code": "CANONICAL_MULTI_HOLE_FAMILY"}),
        )
        assert proposal.provenance == "ai_proposal"
        assert proposal.source_id == "gemini-flash"
        assert isinstance(proposal.warnings, tuple)
        assert len(proposal.warnings) == 2

    def test_plan_proposal_coerces_list_warnings_at_runtime(self) -> None:
        proposal = PlanProposal(
            plan_payload={"features": []},
            provenance="example_plan",
            source_id="example-test",
            warnings=[{"code": "GATE_POLICY_RELAXED"}, {"code": "CANONICAL_MULTI_HOLE_FAMILY"}],
        )
        assert isinstance(proposal.warnings, tuple)
        assert proposal.warnings == ({"code": "GATE_POLICY_RELAXED"}, {"code": "CANONICAL_MULTI_HOLE_FAMILY"})

    def test_plan_proposal_rejects_bare_string_warnings(self) -> None:
        with pytest.raises(ValueError, match="warnings must be a sequence"):
            PlanProposal(
                plan_payload={"features": []},
                provenance="example_plan",
                source_id="example-test",
                warnings="just a string",  # type: ignore[arg-type]
            )

        with pytest.raises(ValueError, match="Warning item must be a structured string mapping"):
            PlanProposal(
                plan_payload={"features": []},
                provenance="example_plan",
                source_id="example-test",
                warnings=["just a string"],  # type: ignore[list-item]
            )

    def test_plan_proposal_rejects_non_sequence_warnings(self) -> None:
        with pytest.raises(ValueError, match="warnings must be a sequence"):
            PlanProposal(
                plan_payload={"features": []},
                provenance="example_plan",
                source_id="example-test",
                warnings=12345,  # type: ignore[arg-type]
            )

    def test_plan_proposal_rejects_invalid_warning_items(self) -> None:
        with pytest.raises(ValueError, match="Warning item must be a structured string mapping"):
            PlanProposal(
                plan_payload={"features": []},
                provenance="example_plan",
                source_id="example-test",
                warnings=[123],  # type: ignore[list-item]
            )

        with pytest.raises(ValueError, match="Warning mappings must contain only string keys and string values"):
            PlanProposal(
                plan_payload={"features": []},
                provenance="example_plan",
                source_id="example-test",
                warnings=[{"code": 123}],  # type: ignore[dict-item]
            )

    def test_plan_proposal_invalid_provenance(self) -> None:
        with pytest.raises(ValueError, match="Invalid provenance"):
            PlanProposal(
                plan_payload={"features": []},
                provenance="unknown_source",  # type: ignore[arg-type]
                source_id="test",
            )

    def test_plan_proposal_invalid_source_id(self) -> None:
        # Control character
        with pytest.raises(ValueError, match="source_id must be"):
            PlanProposal(
                plan_payload={"features": []},
                provenance="example_plan",
                source_id="control\nchar",
            )

        # Windows path
        with pytest.raises(ValueError, match="source_id must be"):
            PlanProposal(
                plan_payload={"features": []},
                provenance="example_plan",
                source_id="C:\\Users\\test\\sample.par",
            )

        # POSIX path
        with pytest.raises(ValueError, match="source_id must be"):
            PlanProposal(
                plan_payload={"features": []},
                provenance="example_plan",
                source_id="/tmp/scratch/plan.json",
            )

        # Prompt text with spaces
        with pytest.raises(ValueError, match="source_id must be"):
            PlanProposal(
                plan_payload={"features": []},
                provenance="example_plan",
                source_id="create a mounting bracket with holes",
            )

    def test_plan_proposal_invalid_payload_type(self) -> None:
        with pytest.raises(ValueError, match="plan_payload must be a Mapping"):
            PlanProposal(
                plan_payload="not-a-mapping",  # type: ignore[arg-type]
                provenance="example_plan",
                source_id="test",
            )

    def test_prepared_plan_context_preserves_typed_records(self) -> None:
        base_body = RectangularBaseBody(
            id="body.main",
            length_mm=100.0,
            width_mm=50.0,
            thickness_mm=10.0,
        )
        plan = FeaturePlan(
            request_id="req-ctx",
            part=PartMetadata(),
            base_body=base_body,
            features=(),
            plan_version="1.0",
            units="mm",
        )
        diag = ValidationDiagnostic(
            severity="warning",
            code="DEFAULT_MARGIN_APPLIED",
            message="Default clearance applied",
            path="features[0]",
        )
        default = DefaultApplied(
            path="features[0].depth",
            value=10.0,
            reason="Omitted depth populated",
            original_value=None,
        )
        ctx = PreparedPlanContext(
            validated_plan=plan,
            lowered_payload={"plan_version": "1.0"},
            request_id="req-ctx",
            kind="prompt_to_cad",
            unit="mm",
            provenance="ai_proposal",
            source_id="gemini",
            mode="capability_first",
            diagnostics=(diag,),
            applied_defaults=(default,),
            warnings=("warn-1",),
            request_metadata={"source": "desktop_app", "label": "test-suite", "job_id": "job-001"},
        )
        assert ctx.request_id == "req-ctx"
        assert ctx.mode == "capability_first"
        assert ctx.diagnostics == (diag,)
        assert ctx.applied_defaults == (default,)
        assert ctx.request_metadata == {"source": "desktop_app", "label": "test-suite", "job_id": "job-001"}
        assert ctx.diagnostics[0].path == "features[0]"
        assert ctx.applied_defaults[0].reason == "Omitted depth populated"

    def test_executor_factory_signature_compatibility(self) -> None:
        def mock_executor_factory(runtime: object, doc_handle: object, timeout_seconds: float = 120.0) -> str:
            return f"executor({runtime}, {doc_handle})"

        factory: ExecutorFactory = mock_executor_factory
        assert callable(factory)
        res = factory("fake_runtime", "fake_doc", 60.0)
        assert res == "executor(fake_runtime, fake_doc)"


# ---------------------------------------------------------------------------
# 2. Artifact Projection Invariants Tests
# ---------------------------------------------------------------------------


class TestArtifactProjection:
    def test_project_artifacts_canonical_order_enforced(self) -> None:
        raw_artifacts = [
            ArtifactRecord(type="preview_image", format="jpg", path="p.jpg", origin="cad_copilot"),
            ArtifactRecord(type="mesh_stl", format="stl", path="p.stl", origin="cad_copilot"),
            ArtifactRecord(type="native_part", format="par", path="p.par", origin="cad_copilot"),
            ArtifactRecord(type="geometry_step", format="step", path="p.step", origin="cad_copilot"),
        ]
        projected = project_artifacts(raw_artifacts)
        formats = [a["format"] for a in projected]
        assert formats == ["par", "step", "stl", "jpg"]

    def test_project_artifacts_with_artifact_record_objects(self) -> None:
        records = [
            ArtifactRecord(type="mesh_stl", format="stl", path="p.stl", origin="cad_copilot"),
            ArtifactRecord(type="native_part", format="par", path="p.par", origin="cad_copilot"),
            ArtifactRecord(type="geometry_step", format="step", path="p.step", origin="cad_copilot"),
        ]
        projected = project_artifacts(records)
        assert len(projected) == 3
        assert [a["format"] for a in projected] == ["par", "step", "stl"]
        assert all(a["origin"] == "cad_copilot" for a in projected)

    def test_project_artifacts_rejects_non_artifact_record(self) -> None:
        raw_artifacts = [
            {"type": "native_part", "format": "par", "path": "p.par", "origin": "cad_copilot"},
            {"type": "geometry_step", "format": "step", "path": "p.step", "origin": "cad_copilot"},
            {"type": "mesh_stl", "format": "stl", "path": "p.stl", "origin": "cad_copilot"},
        ]
        with pytest.raises(ArtifactProjectionError, match="Artifact record must be ArtifactRecord"):
            project_artifacts(raw_artifacts)  # type: ignore[arg-type]

    def test_project_artifacts_rejects_non_cad_copilot_origin(self) -> None:
        raw_artifacts = [
            ArtifactRecord(type="native_part", format="par", path="p.par", origin="third_party"),
            ArtifactRecord(type="geometry_step", format="step", path="p.step", origin="cad_copilot"),
            ArtifactRecord(type="mesh_stl", format="stl", path="p.stl", origin="cad_copilot"),
        ]
        with pytest.raises(ArtifactProjectionError, match="Artifact origin must be 'cad_copilot'"):
            project_artifacts(raw_artifacts)

    def test_project_artifacts_missing_step_rejected(self) -> None:
        raw_artifacts = [
            ArtifactRecord(type="native_part", format="par", path="p.par", origin="cad_copilot"),
            ArtifactRecord(type="mesh_stl", format="stl", path="p.stl", origin="cad_copilot"),
            ArtifactRecord(type="preview_image", format="jpg", path="p.jpg", origin="cad_copilot"),
        ]
        with pytest.raises(ArtifactProjectionError, match="Missing required artifact format 'step'"):
            project_artifacts(raw_artifacts)

    def test_project_artifacts_duplicate_format_rejected(self) -> None:
        raw_artifacts = [
            ArtifactRecord(type="native_part", format="par", path="p1.par", origin="cad_copilot"),
            ArtifactRecord(type="native_part", format="par", path="p2.par", origin="cad_copilot"),
            ArtifactRecord(type="geometry_step", format="step", path="p.step", origin="cad_copilot"),
            ArtifactRecord(type="mesh_stl", format="stl", path="p.stl", origin="cad_copilot"),
        ]
        with pytest.raises(ArtifactProjectionError, match="Duplicate artifact format 'par'"):
            project_artifacts(raw_artifacts)

    def test_project_artifacts_unsupported_format_rejected(self) -> None:
        raw_artifacts = [
            ArtifactRecord(type="native_part", format="par", path="p.par", origin="cad_copilot"),
            ArtifactRecord(type="geometry_step", format="step", path="p.step", origin="cad_copilot"),
            ArtifactRecord(type="mesh_stl", format="stl", path="p.stl", origin="cad_copilot"),
            ArtifactRecord(type="cad_dxf", format="dxf", path="p.dxf", origin="cad_copilot"),
        ]
        with pytest.raises(ArtifactProjectionError, match="Unsupported artifact format 'dxf'"):
            project_artifacts(raw_artifacts)

    def test_project_artifacts_mismatched_type_rejected(self) -> None:
        raw_artifacts = [
            ArtifactRecord(type="mesh_stl", format="par", path="p.par", origin="cad_copilot"),
            ArtifactRecord(type="geometry_step", format="step", path="p.step", origin="cad_copilot"),
            ArtifactRecord(type="mesh_stl", format="stl", path="p.stl", origin="cad_copilot"),
        ]
        with pytest.raises(ArtifactProjectionError, match="requires type 'native_part', got 'mesh_stl'"):
            project_artifacts(raw_artifacts)

    def test_project_artifacts_empty_path_rejected(self) -> None:
        raw_artifacts = [
            ArtifactRecord(type="native_part", format="par", path="   ", origin="cad_copilot"),
            ArtifactRecord(type="geometry_step", format="step", path="p.step", origin="cad_copilot"),
            ArtifactRecord(type="mesh_stl", format="stl", path="p.stl", origin="cad_copilot"),
        ]
        with pytest.raises(ArtifactProjectionError, match="must be a valid non-empty string"):
            project_artifacts(raw_artifacts)


# ---------------------------------------------------------------------------
# 3. Warning Projection, Code Mapping & Sanitization Tests
# ---------------------------------------------------------------------------


class TestWarningProjection:
    def test_project_warning_maps_recognized_code(self) -> None:
        raw = {"code": "PREVIEW_EXPORT_FAILED"}
        projected = project_warning(raw)
        assert projected == PUBLIC_WARNING_MESSAGES["PREVIEW_EXPORT_FAILED"]

    def test_project_warning_maps_validation_diagnostic(self) -> None:
        diag = ValidationDiagnostic(
            severity="warning",
            code="GATE_POLICY_RELAXED",
            message="Internal un-sanitized text that must not leak",
        )
        projected = project_warning(diag)
        assert projected == PUBLIC_WARNING_MESSAGES["GATE_POLICY_RELAXED"]
        assert "Internal un-sanitized text" not in projected

    def test_project_warning_maps_default_applied(self) -> None:
        default = DefaultApplied(
            path="features[0].depth",
            value=10.0,
            reason="Omitted depth populated",
        )
        projected = project_warning(default)
        assert projected == "Default applied to features[0].depth: applied 10.0."

        default_with_orig = DefaultApplied(
            path="boolean_operations[0].operation",
            value="union",
            reason="Unrecognized operation fallback",
            original_value="add",
        )
        projected_with_orig = project_warning(default_with_orig)
        assert projected_with_orig == "Default applied to boolean_operations[0].operation: applied union."

    def test_project_warning_rejects_default_applied_with_sentinel_or_secret(self) -> None:
        sentinel_key = "AIzaSyD-FakeGeminiApiKey1234567890abcdef"
        aws_token = "AKIAIOSFODNN7EXAMPLE"

        # Probe 1: API key in path
        default_bad_path = DefaultApplied(path=sentinel_key, value=10.0, reason="Valid reason")
        assert project_warning(default_bad_path) == GENERIC_WARNING_MESSAGE
        assert sentinel_key not in project_warning(default_bad_path)

        # Probe 2: API key in value
        default_bad_val = DefaultApplied(path="features[0].depth", value=sentinel_key, reason="Valid reason")
        assert project_warning(default_bad_val) == GENERIC_WARNING_MESSAGE
        assert sentinel_key not in project_warning(default_bad_val)

        # Probe 3: AWS token in reason or original_value is never exposed in output
        default_with_token = DefaultApplied(
            path="features[0].depth",
            value=10.0,
            reason=f"Failed because of {aws_token}",
            original_value=aws_token,
        )
        projected_token = project_warning(default_with_token)
        assert projected_token == "Default applied to features[0].depth: applied 10.0."
        assert aws_token not in projected_token
        assert "reason" not in projected_token
        assert "original" not in projected_token

    def test_project_warning_rejects_string_prefix_bypass(self) -> None:
        sentinel_key = "AIzaSyD-FakeGeminiApiKey1234567890abcdef"
        bypass_str = f"Default applied to key: applied {sentinel_key} (reason: bypass probe)."
        assert project_warning(bypass_str) == GENERIC_WARNING_MESSAGE
        assert sentinel_key not in project_warning(bypass_str)

    def test_project_warning_rejects_untrusted_mapping_with_default_fields(self) -> None:
        sentinel_key = "AIzaSyD-FakeGeminiApiKey1234567890abcdef"
        raw_map = {"path": "features[0].depth", "value": sentinel_key, "reason": "test probe"}
        assert project_warning(raw_map) == GENERIC_WARNING_MESSAGE
        assert sentinel_key not in project_warning(raw_map)

    def test_project_warning_preserves_recognized_public_string(self) -> None:
        pub_msg = PUBLIC_WARNING_MESSAGES["VERSION_METADATA_UNAVAILABLE"]
        assert project_warning(pub_msg) == pub_msg

    def test_project_warning_rejects_arbitrary_prompt_text(self) -> None:
        raw_prompt = "Create an involute spur gear with module 2.5 and tooth count 24"
        projected = project_warning(raw_prompt)
        assert projected == GENERIC_WARNING_MESSAGE
        assert "gear" not in projected
        assert "module" not in projected

    def test_project_warning_rejects_api_key(self) -> None:
        raw_key = "AIzaSyD-FakeGeminiApiKey1234567890abcdef"
        projected = project_warning(raw_key)
        assert projected == GENERIC_WARNING_MESSAGE
        assert "AIzaSy" not in projected

    def test_project_warning_rejects_raw_exception_text(self) -> None:
        raw_exc = "FileNotFoundError: [Errno 2] No such file or directory: 'C:\\secret\\file.txt'"
        projected = project_warning(raw_exc)
        assert projected == GENERIC_WARNING_MESSAGE
        assert "FileNotFoundError" not in projected
        assert "secret" not in projected

    def test_project_warning_redacts_workstation_paths_with_spaces_and_unc(self) -> None:
        raw_win = "C:\\Program Files\\Siemens\\Solid Edge\\Program\\edge.exe"
        assert project_warning(raw_win) == GENERIC_WARNING_MESSAGE

        raw_unc = "\\\\internal-storage\\cad-models\\project\\part.par"
        assert project_warning(raw_unc) == GENERIC_WARNING_MESSAGE

    def test_project_warnings_deduplication_preserves_order(self) -> None:
        raw_warnings = [
            {"code": "GATE_POLICY_RELAXED"},
            {"code": "CANONICAL_MULTI_HOLE_FAMILY"},
            {"code": "GATE_POLICY_RELAXED"},  # Duplicate
            {"code": "VERSION_METADATA_UNAVAILABLE"},
            {"code": "CANONICAL_MULTI_HOLE_FAMILY"},  # Duplicate
        ]
        result = project_warnings(raw_warnings)
        assert result == [
            PUBLIC_WARNING_MESSAGES["GATE_POLICY_RELAXED"],
            PUBLIC_WARNING_MESSAGES["CANONICAL_MULTI_HOLE_FAMILY"],
            PUBLIC_WARNING_MESSAGES["VERSION_METADATA_UNAVAILABLE"],
        ]


# ---------------------------------------------------------------------------
# 4. Status-Specific Schema Conformance & Response Builders Tests
# ---------------------------------------------------------------------------


class TestResponseBuildersSchemaConformance:
    def test_accepted_response_conforms_to_schema(self, response_schema_validator: Draft202012Validator) -> None:
        artifacts = [
            ArtifactRecord(type="native_part", format="par", path="out/part.par", origin="cad_copilot"),
            ArtifactRecord(type="geometry_step", format="step", path="out/part.step", origin="cad_copilot"),
            ArtifactRecord(type="mesh_stl", format="stl", path="out/part.stl", origin="cad_copilot"),
            ArtifactRecord(type="preview_image", format="jpg", path="out/part.jpg", origin="cad_copilot"),
        ]
        resp = build_accepted_response(
            request_id="req-ok-001",
            artifacts=artifacts,
            warnings=[{"code": "GATE_POLICY_RELAXED"}, {"code": "PREVIEW_EXPORT_FAILED"}],
        )
        response_schema_validator.validate(resp)
        assert resp["status"] == "accepted"
        assert len(resp["warnings"]) == 2
        assert len(resp["data"]["artifacts"]) == 4

    def test_accepted_response_empty_warnings_parity(self, response_schema_validator: Draft202012Validator) -> None:
        artifacts = [
            ArtifactRecord(type="native_part", format="par", path="out/part.par", origin="cad_copilot"),
            ArtifactRecord(type="geometry_step", format="step", path="out/part.step", origin="cad_copilot"),
            ArtifactRecord(type="mesh_stl", format="stl", path="out/part.stl", origin="cad_copilot"),
        ]
        resp = build_accepted_response(request_id="req-ok-002", artifacts=artifacts)
        response_schema_validator.validate(resp)
        assert resp["status"] == "accepted"
        assert resp["warnings"] == []

    @pytest.mark.parametrize("error_code", list(REJECTED_ERROR_CODES))
    def test_rejected_response_all_rejected_codes_conform(
        self, error_code: str, response_schema_validator: Draft202012Validator
    ) -> None:
        resp = build_rejected_response(
            request_id="req-rej-001",
            error_code=error_code,
            field="example_id" if error_code == "UNSUPPORTED_REQUEST" else None,
            warnings=[{"code": "GATE_POLICY_RELAXED"}],
        )
        response_schema_validator.validate(resp)
        assert resp["status"] == "rejected"
        assert resp["errors"][0]["code"] == error_code
        assert resp["errors"][0]["message"] == PUBLIC_ERROR_MESSAGES[error_code]
        assert resp["warnings"] == [PUBLIC_WARNING_MESSAGES["GATE_POLICY_RELAXED"]]

    @pytest.mark.parametrize("error_code", list(FAILED_ERROR_CODES))
    def test_failed_response_all_failed_codes_conform(
        self, error_code: str, response_schema_validator: Draft202012Validator
    ) -> None:
        resp = build_failed_response(
            request_id="req-fail-001",
            error_code=error_code,
            warnings=[{"code": "CANONICAL_MULTI_HOLE_FAMILY"}],
        )
        response_schema_validator.validate(resp)
        assert resp["status"] == "failed"
        assert resp["errors"][0]["code"] == error_code
        assert resp["errors"][0]["message"] == PUBLIC_ERROR_MESSAGES[error_code]
        assert resp["warnings"] == [PUBLIC_WARNING_MESSAGES["CANONICAL_MULTI_HOLE_FAMILY"]]

    def test_failed_response_rejects_rejected_only_code_and_falls_back_to_internal_error(
        self, response_schema_validator: Draft202012Validator
    ) -> None:
        resp = build_failed_response(
            request_id="req-fail-misclassified",
            error_code="UNSUPPORTED_REQUEST",  # Rejected code, illegal for failed
        )
        response_schema_validator.validate(resp)
        assert resp["status"] == "failed"
        assert resp["errors"][0]["code"] == "INTERNAL_ERROR"
        assert resp["errors"][0]["message"] == PUBLIC_ERROR_MESSAGES["INTERNAL_ERROR"]

    def test_rejected_response_rejects_failed_only_code_and_falls_back_to_unsupported_request(
        self, response_schema_validator: Draft202012Validator
    ) -> None:
        resp = build_rejected_response(
            request_id="req-rej-misclassified",
            error_code="CAD_PLAN_REJECTED",  # Failed code, illegal for rejected
        )
        response_schema_validator.validate(resp)
        assert resp["status"] == "rejected"
        assert resp["errors"][0]["code"] == "UNSUPPORTED_REQUEST"
        assert resp["errors"][0]["message"] == PUBLIC_ERROR_MESSAGES["UNSUPPORTED_REQUEST"]

    def test_invalid_request_id_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="request_id must be a string"):
            build_accepted_response(request_id="", artifacts=[])

        with pytest.raises(ValueError, match="request_id must be a string"):
            build_rejected_response(request_id="", error_code="UNSUPPORTED_REQUEST")

        with pytest.raises(ValueError, match="request_id must be a string"):
            build_failed_response(request_id="", error_code="INTERNAL_ERROR")
