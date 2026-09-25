"""Comprehensive unit tests for GenerationService orchestration.

Verifies:
    - Phase A: request invariants, path containment, preflight collision checks, mode defaults.
    - Phase B: single resolver dispatch, proposal validation, provenance matching,
      canonical parse/validate/lower before CAD runtime acquisition, warning flow.
    - Phase C: lifecycle ordering (connect, diagnostics, create document, execute),
      error mapping for executor/runtime failures, close_request_document handling.
    - Phase D: finalizer ownership, export failure mapping, schema conformance.
    - Phase E: guaranteed teardown with force_kill_on_failure=False.
    - Security & Privacy: zero secret, prompt, or local path leakage.
"""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path
from typing import Any

import pytest

from application.models import (
    ExampleGenerationRequest,
    PlanProposal,
    PreparedPlanContext,
    PromptGenerationRequest,
)
from application.service import (
    GenerationService,
    _preflight_request,
    _prepare_canonical_plan,
)
from artifacts.paths import ArtifactPathError
from artifacts.pipeline import finalize_request_artifacts
from example_catalog import resolve_example_plan
from interfaces.exceptions import (
    CADDocumentError,
    CADExecutionError,
    CADExportError,
    TeardownIncompleteError,
)
from interfaces.models import (
    ArtifactRecord,
    ExecutionFailure,
    ExecutionSuccess,
    RuntimeDiagnostics,
    StandardInspectionReport,
)
from interfaces.runtime_abc import CADRuntimeABC
from manifests import (
    ManifestConfigurationError,
    ManifestValidationError,
    RunManifestContext,
    get_run_manifest_validator,
)

# ---------------------------------------------------------------------------
# Test Fixtures & Fakes
# ---------------------------------------------------------------------------


def make_valid_plan_payload(request_id: str = "req_valid_01") -> dict[str, Any]:
    """Produce a minimal valid FeaturePlan dict payload."""
    return {
        "plan_version": "cad_copilot.single_part_feature_plan.v1",
        "request_id": request_id,
        "units": "mm",
        "part": {
            "part_id": "part.main",
            "design_intent": "simple rectangular plate",
            "scope": "single_part",
        },
        "base_body": {
            "id": "body.main",
            "family": "rectangular_prism",
            "dimensions_mm": {
                "length": 80.0,
                "width": 40.0,
                "thickness": 6.0,
            },
        },
        "primitive_bodies": [],
        "boolean_operations": [],
        "features": [],
    }


class FakeDocumentHandle:
    """Mock CAD document handle."""

    def __init__(self, doc_id: str = "fake_doc_001") -> None:
        self.doc_id = doc_id


class FakeRuntime(CADRuntimeABC):
    """Spy runtime implementation tracking lifecycle calls."""

    def __init__(
        self,
        diagnostics_warnings: list[dict[str, str]] | None = None,
        version_build: str | None = None,
    ) -> None:
        self.connect_count = 0
        self.diagnostics_count = 0
        self.create_doc_count = 0
        self.close_doc_count = 0
        self.teardown_count = 0
        self.teardown_args: list[bool] = []
        self.closed_handles: list[Any] = []
        self.diagnostics_warnings = diagnostics_warnings or []
        self.version_build = version_build
        self.fail_connect = False
        self.fail_create_doc = False
        self.fail_close_doc = False
        self.fail_teardown = False
        self.raise_teardown = False
        self.teardown_return_override: Any = None

    def connect_application(self) -> Any:
        self.connect_count += 1
        if self.fail_connect:
            raise RuntimeError("Fake COM connect failure")
        return "fake_app_session"

    def get_diagnostics(self) -> RuntimeDiagnostics:
        self.diagnostics_count += 1
        return RuntimeDiagnostics(
            ownership="owned",
            attachment_mode="spawned_new",
            is_healthy=True,
            version_build=self.version_build,
            warnings=list(self.diagnostics_warnings),
        )

    def create_part_document(self, application: Any) -> Any:
        self.create_doc_count += 1
        if self.fail_create_doc:
            raise RuntimeError("Fake Part document creation failure")
        return FakeDocumentHandle()

    def open_document(self, application: Any, path: Path) -> Any:
        raise NotImplementedError

    def close_document(self, doc_handle: Any) -> None:
        self.close_doc_count += 1
        self.closed_handles.append(doc_handle)
        if self.fail_close_doc:
            raise RuntimeError("Fake document close failure")

    def teardown(self, force_kill_on_failure: bool = False) -> bool:
        self.teardown_count += 1
        self.teardown_args.append(force_kill_on_failure)
        if self.raise_teardown:
            raise RuntimeError("Fake teardown exception")
        if self.teardown_return_override is not None:
            return self.teardown_return_override  # type: ignore[no-any-return]
        return not self.fail_teardown

    def is_healthy(self) -> bool:
        return True


class FakeExecutor:
    """Spy executor tracking execution and document release."""

    def __init__(
        self,
        runtime: Any,
        doc_handle: Any,
        result: ExecutionSuccess | ExecutionFailure | None = None,
    ) -> None:
        self.runtime = runtime
        self.doc_handle = doc_handle
        self.execute_calls: list[tuple[Any, Any]] = []
        self.close_count = 0
        self.fail_close = False
        self.result = result or ExecutionSuccess(
            operations_executed=1,
            exported_artifacts=[],
            warnings=[],
            inspection_report=StandardInspectionReport(
                volume_mm3=20000.0,
                mass_kg=0.054,
                feature_count=1,
                body_count=1,
                solid_body_count=1,
            ),
        )

    def execute_feature_plan(self, plan: Any, *, mode: Any = None) -> Any:
        self.execute_calls.append((plan, mode))
        return self.result

    def close_request_document(self) -> None:
        self.close_count += 1
        if self.fail_close:
            raise CADDocumentError("Mock close failed", error_code="DOCUMENT_CLOSE_FAILED")
        self.runtime.close_document(self.doc_handle)


class FakeProductionExecutor(FakeExecutor):
    """Fake executor supporting export_model and capture_preview for artifact pipeline."""

    def export_model(self, format_id: str, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if format_id == "par":
            output_path.write_bytes(b"SOLID_EDGE_PART_CONTENT")
        elif format_id == "step":
            step_text = (
                "ISO-10303-21;\n"
                "HEADER;\n"
                "FILE_DESCRIPTION(('CAD Copilot Test STEP'),'2;1');\n"
                "FILE_NAME('test.step','2026-09-02T12:00:00',('Tester'),('CAD Copilot'),"
                "'Preprocessor','OriginatingSystem','Authorization');\n"
                "FILE_SCHEMA(('CONFIG_CONTROL_DESIGN'));\n"
                "ENDSEC;\n"
                "DATA;\n"
                "#1 = CARTESIAN_POINT('',(0.0,0.0,0.0));\n"
                "#2 = CARTESIAN_POINT('',(30.0,20.0,10.0));\n"
                "#10 = ( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) );\n"
                "#20 = MANIFOLD_SOLID_BREP('Body1',#30);\n"
                "#30 = CLOSED_SHELL('Shell1',());\n"
                "#40 = ADVANCED_FACE('Face1',(),#50,.T.);\n"
                "#50 = PLANE('Plane1',#60);\n"
                "#60 = AXIS2_PLACEMENT_3D('Placement1',#1,#70,#80);\n"
                "#70 = DIRECTION('Axis',(0.0,0.0,1.0));\n"
                "#80 = DIRECTION('RefDirection',(1.0,0.0,0.0));\n"
                "ENDSEC;\n"
                "END-ISO-10303-21;\n"
            )
            output_path.write_text(step_text, encoding="utf-8")
        elif format_id == "stl":
            header = b"CAD Copilot Binary STL".ljust(80, b"\x00")[:80]
            stl_data = bytearray(header)
            stl_data.extend(struct.pack("<I", 1))
            stl_data.extend(struct.pack("<3f", 0.0, 0.0, 1.0))
            stl_data.extend(struct.pack("<3f", 0.0, 0.0, 0.0))
            stl_data.extend(struct.pack("<3f", 10.0, 0.0, 0.0))
            stl_data.extend(struct.pack("<3f", 0.0, 10.0, 0.0))
            stl_data.extend(struct.pack("<H", 0))
            output_path.write_bytes(bytes(stl_data))

    def capture_preview(self, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"\xff\xd8" + b"\x00" * 252 + b"\xff\xd9")


def fake_artifact_finalizer(
    executor: Any,
    success_result: ExecutionSuccess,
    output_root: Path | str,
    request_id: str,
    *,
    manifest_context: RunManifestContext | None = None,
) -> ExecutionSuccess:
    """Mock artifact finalizer returning canonical artifact records."""
    root = Path(output_root)
    final_dir = root / request_id
    records = [
        ArtifactRecord(type="native_part", format="par", path=str(final_dir / f"{request_id}.par")),
        ArtifactRecord(type="geometry_step", format="step", path=str(final_dir / f"{request_id}.step")),
        ArtifactRecord(type="mesh_stl", format="stl", path=str(final_dir / f"{request_id}.stl")),
        ArtifactRecord(type="preview_image", format="jpg", path=str(final_dir / f"{request_id}.jpg")),
    ]
    return ExecutionSuccess(
        operations_executed=success_result.operations_executed,
        exported_artifacts=records,
        warnings=list(success_result.warnings),
        inspection_report=success_result.inspection_report,
    )


# ---------------------------------------------------------------------------
# Phase A Tests: Request & Destination Preflight
# ---------------------------------------------------------------------------


def test_preflight_valid_request(tmp_path: Path) -> None:
    req = ExampleGenerationRequest(
        contract_version="1.0",
        request_id="req_preflight_ok",
        kind="example_plan",
        unit="mm",
        example_id="cube_sample",
    )
    req_id, resolved, mode = _preflight_request(req, tmp_path, None)
    assert req_id == "req_preflight_ok"
    assert resolved == tmp_path.resolve()
    assert mode == "capability_first"


def test_preflight_rejects_nonexistent_output_root(tmp_path: Path) -> None:
    non_existent = tmp_path / "does_not_exist"
    req = ExampleGenerationRequest(
        contract_version="1.0",
        request_id="req_preflight_01",
        kind="example_plan",
        unit="mm",
        example_id="sample",
    )
    with pytest.raises(ArtifactPathError) as exc:
        _preflight_request(req, non_existent, None)
    assert exc.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"


def test_preflight_detects_existing_target_directory_collision(tmp_path: Path) -> None:
    req_id = "req_colliding"
    existing_dir = tmp_path / req_id
    existing_dir.mkdir()

    req = PromptGenerationRequest(
        contract_version="1.0",
        request_id=req_id,
        kind="prompt_to_cad",
        unit="mm",
        prompt="Create a cylinder",
    )
    with pytest.raises(ArtifactPathError) as exc:
        _preflight_request(req, tmp_path, None)
    assert exc.value.error_code == "TARGET_ALREADY_EXISTS"


def test_preflight_detects_existing_candidate_file_collision(tmp_path: Path) -> None:
    req_id = "req_file_colliding"
    target_par = tmp_path / req_id / f"{req_id}.par"
    target_par.parent.mkdir()
    target_par.write_text("dummy")

    req = ExampleGenerationRequest(
        contract_version="1.0",
        request_id=req_id,
        kind="example_plan",
        unit="mm",
        example_id="sample",
    )
    with pytest.raises(ArtifactPathError) as exc:
        _preflight_request(req, tmp_path, None)
    assert exc.value.error_code == "TARGET_ALREADY_EXISTS"


def test_preflight_explicit_strict_mode(tmp_path: Path) -> None:
    req = ExampleGenerationRequest(
        contract_version="1.0",
        request_id="req_mode_strict",
        kind="example_plan",
        unit="mm",
        example_id="sample",
    )
    _, _, mode = _preflight_request(req, tmp_path, "strict")
    assert mode == "strict"


# ---------------------------------------------------------------------------
# Phase B Tests: Dispatch & Canonical Preparation
# ---------------------------------------------------------------------------


def test_example_request_dispatches_only_to_example_resolver(tmp_path: Path) -> None:
    example_called = False
    prompt_called = False

    def mock_example_resolver(request: ExampleGenerationRequest) -> PlanProposal:
        nonlocal example_called
        example_called = True
        return PlanProposal(
            plan_payload=make_valid_plan_payload(request.request_id),
            provenance="example_plan",
            source_id="sample_cube",
        )

    def mock_prompt_resolver(request: PromptGenerationRequest) -> PlanProposal:
        nonlocal prompt_called
        prompt_called = True
        raise AssertionError("Prompt resolver should not be called for example_plan")

    runtime = FakeRuntime()
    service = GenerationService(
        example_resolver=mock_example_resolver,
        prompt_resolver=mock_prompt_resolver,
        runtime_factory=lambda: runtime,
        executor_factory=lambda rt, dh: FakeExecutor(rt, dh),
        artifact_finalizer=fake_artifact_finalizer,
    )

    req = ExampleGenerationRequest(
        contract_version="1.0",
        request_id="req_dispatch_ex",
        kind="example_plan",
        unit="mm",
        example_id="sample_cube",
    )
    response = service.generate(req, output_root=tmp_path)
    assert response["status"] == "accepted"
    assert example_called is True
    assert prompt_called is False


def test_prompt_request_dispatches_only_to_prompt_resolver(tmp_path: Path) -> None:
    example_called = False
    prompt_called = False

    def mock_example_resolver(request: ExampleGenerationRequest) -> PlanProposal:
        nonlocal example_called
        example_called = True
        raise AssertionError("Example resolver should not be called for prompt_to_cad")

    def mock_prompt_resolver(request: PromptGenerationRequest) -> PlanProposal:
        nonlocal prompt_called
        prompt_called = True
        return PlanProposal(
            plan_payload=make_valid_plan_payload(request.request_id),
            provenance="ai_proposal",
            source_id="gemini_pro_v1",
        )

    runtime = FakeRuntime()
    service = GenerationService(
        example_resolver=mock_example_resolver,
        prompt_resolver=mock_prompt_resolver,
        runtime_factory=lambda: runtime,
        executor_factory=lambda rt, dh: FakeExecutor(rt, dh),
        artifact_finalizer=fake_artifact_finalizer,
    )

    req = PromptGenerationRequest(
        contract_version="1.0",
        request_id="req_dispatch_pr",
        kind="prompt_to_cad",
        unit="mm",
        prompt="A block 50x40x10",
    )
    response = service.generate(req, output_root=tmp_path)
    assert response["status"] == "accepted"
    assert prompt_called is True
    assert example_called is False


def test_missing_example_resolver_returns_rejected_unsupported_request(tmp_path: Path) -> None:
    service = GenerationService(example_resolver=None)
    req = ExampleGenerationRequest(
        contract_version="1.0",
        request_id="req_no_ex_res",
        kind="example_plan",
        unit="mm",
        example_id="sample",
    )
    response = service.generate(req, output_root=tmp_path)
    assert response["status"] == "rejected"
    assert response["errors"][0]["code"] == "UNSUPPORTED_REQUEST"
    assert "warnings" in response


def test_missing_prompt_resolver_returns_failed_prompt_interpretation(tmp_path: Path) -> None:
    service = GenerationService(prompt_resolver=None)
    req = PromptGenerationRequest(
        contract_version="1.0",
        request_id="req_no_pr_res",
        kind="prompt_to_cad",
        unit="mm",
        prompt="Make a cube",
    )
    response = service.generate(req, output_root=tmp_path)
    assert response["status"] == "failed"
    assert response["errors"][0]["code"] == "PROMPT_INTERPRETATION_FAILED"
    assert "warnings" in response


def test_failing_prompt_resolver_returns_failed_prompt_interpretation(tmp_path: Path) -> None:
    def failing_prompt_resolver(req: PromptGenerationRequest) -> PlanProposal:
        raise RuntimeError("API key invalid or network timeout")

    service = GenerationService(prompt_resolver=failing_prompt_resolver)
    req = PromptGenerationRequest(
        contract_version="1.0",
        request_id="req_fail_pr_res",
        kind="prompt_to_cad",
        unit="mm",
        prompt="Make a cube",
    )
    response = service.generate(req, output_root=tmp_path)
    assert response["status"] == "failed"
    assert response["errors"][0]["code"] == "PROMPT_INTERPRETATION_FAILED"
    # Ensure raw exception text is not leaked
    assert "network timeout" not in str(response)
    assert "API key" not in str(response)


@pytest.mark.parametrize(
    ("resolver_code", "expected_warning"),
    [
        ("provider_request_failed", "AI provider request did not complete"),
        ("response_empty", "AI provider returned no usable text"),
        ("response_invalid", "AI provider response could not form"),
        ("configuration_invalid", "AI provider is not configured"),
    ],
)
def test_prompt_failure_reports_only_safe_category(tmp_path: Path, resolver_code: str, expected_warning: str) -> None:
    class ResolverFailure(Exception):
        code = resolver_code

    def failing_resolver(req: PromptGenerationRequest) -> PlanProposal:
        raise ResolverFailure("private prompt and credential details must not leak")

    service = GenerationService(prompt_resolver=failing_resolver)
    req = PromptGenerationRequest(
        contract_version="1.0",
        request_id="req_safe_failure",
        kind="prompt_to_cad",
        unit="mm",
        prompt="Make a cube",
    )
    response = service.generate(req, output_root=tmp_path)
    assert response["errors"][0]["code"] == "PROMPT_INTERPRETATION_FAILED"
    assert len(response["warnings"]) == 1
    assert expected_warning in response["warnings"][0]
    assert "private prompt" not in str(response)
    assert "credential" not in str(response)


def test_preparation_failure_occurs_before_runtime_construction(tmp_path: Path) -> None:
    """Proves that invalid proposals, wrong provenance, or parse failures do NOT construct runtime."""
    runtime_constructed = False

    def spy_runtime_factory() -> CADRuntimeABC:
        nonlocal runtime_constructed
        runtime_constructed = True
        return FakeRuntime()

    # Wrong provenance: example_plan with ai_proposal provenance
    def bad_prov_resolver(req: ExampleGenerationRequest) -> PlanProposal:
        return PlanProposal(
            plan_payload=make_valid_plan_payload(req.request_id),
            provenance="ai_proposal",
            source_id="mismatched",
        )

    service = GenerationService(
        example_resolver=bad_prov_resolver,
        runtime_factory=spy_runtime_factory,
    )
    req = ExampleGenerationRequest(
        contract_version="1.0",
        request_id="req_bad_prov",
        kind="example_plan",
        unit="mm",
        example_id="sample",
    )
    response = service.generate(req, output_root=tmp_path)
    assert response["status"] == "failed"
    assert response["errors"][0]["code"] == "CAD_PLAN_REJECTED"
    assert runtime_constructed is False


def test_plan_identity_mismatch_fails_before_runtime(tmp_path: Path) -> None:
    runtime_constructed = False

    def spy_runtime_factory() -> CADRuntimeABC:
        nonlocal runtime_constructed
        runtime_constructed = True
        return FakeRuntime()

    def mismatched_id_resolver(req: PromptGenerationRequest) -> PlanProposal:
        return PlanProposal(
            plan_payload=make_valid_plan_payload("totally_different_id"),
            provenance="ai_proposal",
            source_id="gemini",
        )

    service = GenerationService(
        prompt_resolver=mismatched_id_resolver,
        runtime_factory=spy_runtime_factory,
    )
    req = PromptGenerationRequest(
        contract_version="1.0",
        request_id="req_actual_id",
        kind="prompt_to_cad",
        unit="mm",
        prompt="Make a block",
    )
    response = service.generate(req, output_root=tmp_path)
    assert response["status"] == "failed"
    assert response["errors"][0]["code"] == "CAD_PLAN_REJECTED"
    assert runtime_constructed is False


def test_prepared_plan_context_retention(tmp_path: Path) -> None:
    req = PromptGenerationRequest(
        contract_version="1.0",
        request_id="req_ctx_test",
        kind="prompt_to_cad",
        unit="mm",
        prompt="Create a plate with hole",
        metadata={"source": "local_agent", "label": "test_agent", "job_id": "job-42"},
    )
    proposal = PlanProposal(
        plan_payload=make_valid_plan_payload("req_ctx_test"),
        provenance="ai_proposal",
        source_id="gemini_test",
        warnings=[{"code": "GATE_POLICY_RELAXED", "message": "Policy relaxed"}],
    )
    ctx, _warnings = _prepare_canonical_plan(req, proposal, "capability_first")
    assert isinstance(ctx, PreparedPlanContext)
    assert ctx.request_id == "req_ctx_test"
    assert ctx.kind == "prompt_to_cad"
    assert ctx.provenance == "ai_proposal"
    assert ctx.source_id == "gemini_test"
    assert ctx.mode == "capability_first"
    assert ctx.request_metadata == {"source": "local_agent", "label": "test_agent", "job_id": "job-42"}
    assert len(ctx.warnings) >= 1
    # Check no prompt string in context attributes
    assert not hasattr(ctx, "prompt")
    assert "Create a plate with hole" not in str(ctx.validated_plan)


# ---------------------------------------------------------------------------
# Phase C, D, E Tests: Runtime Lifecycle, Execution, Finalization, Teardown
# ---------------------------------------------------------------------------


def test_successful_generation_orchestration(tmp_path: Path) -> None:
    runtime = FakeRuntime(diagnostics_warnings=[{"code": "VERSION_METADATA_UNAVAILABLE", "message": "No version"}])
    executor_created = False

    def spy_executor_factory(rt: Any, dh: Any) -> FakeExecutor:
        nonlocal executor_created
        executor_created = True
        return FakeExecutor(rt, dh)

    service = GenerationService(
        example_resolver=lambda req: PlanProposal(
            plan_payload=make_valid_plan_payload(req.request_id),
            provenance="example_plan",
            source_id="sample_01",
        ),
        runtime_factory=lambda: runtime,
        executor_factory=spy_executor_factory,
        artifact_finalizer=fake_artifact_finalizer,
    )

    req = ExampleGenerationRequest(
        contract_version="1.0",
        request_id="req_full_success",
        kind="example_plan",
        unit="mm",
        example_id="sample_01",
    )
    response = service.generate(req, output_root=tmp_path)

    assert response["status"] == "accepted"
    assert response["contract_version"] == "1.0"
    assert response["request_id"] == "req_full_success"
    assert len(response["data"]["artifacts"]) == 4

    # Verify lifecycle call ordering and counts
    assert runtime.connect_count == 1
    assert runtime.diagnostics_count == 1
    assert runtime.create_doc_count == 1
    assert executor_created is True
    assert runtime.teardown_count == 1
    assert runtime.teardown_args == [False]  # force_kill_on_failure is False


def test_runtime_connect_failure_maps_to_cad_execution_failed_and_tears_down(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    runtime.fail_connect = True

    service = GenerationService(
        example_resolver=lambda req: PlanProposal(
            plan_payload=make_valid_plan_payload(req.request_id),
            provenance="example_plan",
            source_id="sample_01",
        ),
        runtime_factory=lambda: runtime,
    )
    req = ExampleGenerationRequest(
        contract_version="1.0",
        request_id="req_fail_connect",
        kind="example_plan",
        unit="mm",
        example_id="sample",
    )
    response = service.generate(req, output_root=tmp_path)
    assert response["status"] == "failed"
    assert response["errors"][0]["code"] == "CAD_EXECUTION_FAILED"
    assert runtime.teardown_count == 1
    assert runtime.teardown_args == [False]


def test_executor_preflight_rejection_maps_to_cad_plan_rejected(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    failure_result = ExecutionFailure(
        message="Unsupported feature family in executor",
        phase="preflight",
        details={"error_code": "UNSUPPORTED_EXECUTION_OPERATION"},
        warnings=[{"code": "GATE_POLICY_RELAXED", "message": "Policy relaxed"}],
    )

    spy_executor: FakeExecutor | None = None

    def spy_factory(rt: Any, dh: Any) -> FakeExecutor:
        nonlocal spy_executor
        spy_executor = FakeExecutor(rt, dh, result=failure_result)
        return spy_executor

    service = GenerationService(
        example_resolver=lambda req: PlanProposal(
            plan_payload=make_valid_plan_payload(req.request_id),
            provenance="example_plan",
            source_id="sample_01",
        ),
        runtime_factory=lambda: runtime,
        executor_factory=spy_factory,
    )
    req = ExampleGenerationRequest(
        contract_version="1.0",
        request_id="req_preflight_rej",
        kind="example_plan",
        unit="mm",
        example_id="sample",
    )
    response = service.generate(req, output_root=tmp_path)

    assert response["status"] == "failed"
    assert response["errors"][0]["code"] == "CAD_PLAN_REJECTED"
    # Document close called once on failure
    assert spy_executor is not None
    assert spy_executor.close_count == 1
    # Warnings preserved in failed response
    assert "warnings" in response
    assert any("relaxed capability-first policy" in str(w) for w in response["warnings"])
    # Teardown executed
    assert runtime.teardown_count == 1


def test_executor_execution_failure_maps_to_cad_execution_failed(tmp_path: Path) -> None:
    runtime = FakeRuntime()
    failure_result = ExecutionFailure(
        message="Native Solid Edge recompute failed",
        phase="execution",
        details={"error_code": "RECOMPUTE_FAILED"},
    )

    service = GenerationService(
        example_resolver=lambda req: PlanProposal(
            plan_payload=make_valid_plan_payload(req.request_id),
            provenance="example_plan",
            source_id="sample_01",
        ),
        runtime_factory=lambda: runtime,
        executor_factory=lambda rt, dh: FakeExecutor(rt, dh, result=failure_result),
    )
    req = ExampleGenerationRequest(
        contract_version="1.0",
        request_id="req_exec_fail",
        kind="example_plan",
        unit="mm",
        example_id="sample",
    )
    response = service.generate(req, output_root=tmp_path)

    assert response["status"] == "failed"
    assert response["errors"][0]["code"] == "CAD_EXECUTION_FAILED"
    assert runtime.teardown_count == 1


def test_finalizer_inspection_rejection_maps_to_native_qa_blocked(tmp_path: Path) -> None:
    runtime = FakeRuntime()

    def failing_finalizer(*args: Any, **kwargs: Any) -> Any:
        raise CADExecutionError("Body count was 0", error_code="NATIVE_QA_BLOCKED")

    service = GenerationService(
        example_resolver=lambda req: PlanProposal(
            plan_payload=make_valid_plan_payload(req.request_id),
            provenance="example_plan",
            source_id="sample_01",
        ),
        runtime_factory=lambda: runtime,
        executor_factory=lambda rt, dh: FakeExecutor(rt, dh),
        artifact_finalizer=failing_finalizer,
    )
    req = ExampleGenerationRequest(
        contract_version="1.0",
        request_id="req_qa_blocked",
        kind="example_plan",
        unit="mm",
        example_id="sample",
    )
    response = service.generate(req, output_root=tmp_path)

    assert response["status"] == "failed"
    assert response["errors"][0]["code"] == "NATIVE_QA_BLOCKED"
    assert runtime.teardown_count == 1


def test_finalizer_export_failure_maps_to_artifact_export_failed(tmp_path: Path) -> None:
    runtime = FakeRuntime()

    def failing_finalizer(*args: Any, **kwargs: Any) -> Any:
        raise CADExportError("Export of STEP produced 0-byte file", error_code="ARTIFACT_EXPORT_FAILED")

    service = GenerationService(
        example_resolver=lambda req: PlanProposal(
            plan_payload=make_valid_plan_payload(req.request_id),
            provenance="example_plan",
            source_id="sample_01",
        ),
        runtime_factory=lambda: runtime,
        executor_factory=lambda rt, dh: FakeExecutor(rt, dh),
        artifact_finalizer=failing_finalizer,
    )
    req = ExampleGenerationRequest(
        contract_version="1.0",
        request_id="req_export_fail",
        kind="example_plan",
        unit="mm",
        example_id="sample",
    )
    response = service.generate(req, output_root=tmp_path)

    assert response["status"] == "failed"
    assert response["errors"][0]["code"] == "ARTIFACT_EXPORT_FAILED"
    assert runtime.teardown_count == 1


def test_mode_propagation_to_executor(tmp_path: Path) -> None:
    captured_modes: list[Any] = []

    class SpyModeExecutor(FakeExecutor):
        def execute_feature_plan(self, plan: Any, *, mode: Any = None) -> Any:
            captured_modes.append(mode)
            return super().execute_feature_plan(plan, mode=mode)

    service = GenerationService(
        example_resolver=lambda req: PlanProposal(
            plan_payload=make_valid_plan_payload(req.request_id),
            provenance="example_plan",
            source_id="sample_01",
        ),
        runtime_factory=FakeRuntime,
        executor_factory=lambda rt, dh: SpyModeExecutor(rt, dh),
        artifact_finalizer=fake_artifact_finalizer,
    )

    req = ExampleGenerationRequest(
        contract_version="1.0",
        request_id="req_mode_prop",
        kind="example_plan",
        unit="mm",
        example_id="sample",
    )
    # 1. Explicit strict mode
    service.generate(req, output_root=tmp_path, mode="strict")
    assert captured_modes[-1] == "strict"

    # 2. Default mode (None) -> capability_first
    req2 = ExampleGenerationRequest(
        contract_version="1.0",
        request_id="req_mode_prop2",
        kind="example_plan",
        unit="mm",
        example_id="sample",
    )
    service.generate(req2, output_root=tmp_path)
    assert captured_modes[-1] == "capability_first"


def test_warning_flow_order_and_privacy_guarantee(tmp_path: Path) -> None:
    """Proves warnings preserve arrival order, sanitize secrets/paths, and deduplicate."""
    runtime = FakeRuntime(diagnostics_warnings=[{"code": "VERSION_METADATA_UNAVAILABLE", "message": "No metadata"}])

    # Injected resolver warning
    resolver_warn = {"code": "CANONICAL_MULTI_HOLE_FAMILY", "message": "Multiple holes"}

    # Finalizer preview warning
    def finalizer_with_warnings(
        executor: Any,
        success: Any,
        root: Any,
        req_id: str,
        *,
        manifest_context: RunManifestContext | None = None,
    ) -> ExecutionSuccess:
        res = fake_artifact_finalizer(executor, success, root, req_id, manifest_context=manifest_context)
        res.warnings.append({"code": "PREVIEW_EXPORT_FAILED", "message": "No preview"})
        return res

    service = GenerationService(
        example_resolver=lambda req: PlanProposal(
            plan_payload=make_valid_plan_payload(req.request_id),
            provenance="example_plan",
            source_id="sample_01",
            warnings=[resolver_warn],
        ),
        runtime_factory=lambda: runtime,
        executor_factory=lambda rt, dh: FakeExecutor(rt, dh),
        artifact_finalizer=finalizer_with_warnings,
    )

    req = ExampleGenerationRequest(
        contract_version="1.0",
        request_id="req_warn_order",
        kind="example_plan",
        unit="mm",
        example_id="sample",
    )
    resp = service.generate(req, output_root=tmp_path)
    assert resp["status"] == "accepted"
    warnings = resp["warnings"]

    # Invariants:
    # 1. Resolver warning arrives first
    assert "Multiple circular through-holes accepted by canonical validation." in warnings[0]
    # 2. Runtime warning arrives next
    assert any("CAD runtime did not report version metadata." in w for w in warnings)
    # 3. Preview warning arrives last
    assert "Preview image generation was skipped or unavailable" in warnings[-1]


def test_invalid_request_id_not_echoed_and_falls_back_to_unknown(tmp_path: Path) -> None:
    """Proves invalid request IDs are not reflected in error responses, falling back to unknown."""
    service = GenerationService()
    req = ExampleGenerationRequest(
        contract_version="1.0",
        request_id="bad/request/id",
        kind="example_plan",
        unit="mm",
        example_id="sample",
    )
    resp = service.generate(req, output_root=tmp_path)
    assert resp["status"] == "failed"
    assert resp["request_id"] == "unknown"
    assert resp["errors"][0]["code"] == "OUTPUT_PATH_NOT_ALLOWED"
    assert "bad/request/id" not in str(resp)


def test_executor_lowering_failure_maps_to_cad_plan_rejected(tmp_path: Path) -> None:
    """Proves executor lowering phase failures map to CAD_PLAN_REJECTED."""
    runtime = FakeRuntime()
    lowering_failure = ExecutionFailure(
        message="Lowering phase failed",
        phase="lowering",
    )
    service = GenerationService(
        example_resolver=lambda req: PlanProposal(
            plan_payload=make_valid_plan_payload(req.request_id),
            provenance="example_plan",
            source_id="sample_01",
        ),
        runtime_factory=lambda: runtime,
        executor_factory=lambda rt, dh: FakeExecutor(rt, dh, result=lowering_failure),
    )
    req = ExampleGenerationRequest(
        contract_version="1.0",
        request_id="req_lowering_fail",
        kind="example_plan",
        unit="mm",
        example_id="sample",
    )
    resp = service.generate(req, output_root=tmp_path)
    assert resp["status"] == "failed"
    assert resp["errors"][0]["code"] == "CAD_PLAN_REJECTED"
    assert resp["request_id"] == "req_lowering_fail"


def test_executor_warnings_retained_across_finalizer_failure(tmp_path: Path) -> None:
    """Proves executor warnings are retained even when artifact finalization raises an error."""
    runtime = FakeRuntime()
    exec_success = ExecutionSuccess(
        operations_executed=1,
        exported_artifacts=[],
        warnings=[{"code": "GATE_POLICY_RELAXED"}],
        inspection_report=StandardInspectionReport(
            volume_mm3=100.0,
            mass_kg=0.01,
            feature_count=1,
            body_count=1,
            solid_body_count=1,
        ),
    )

    def failing_finalizer(
        executor: Any,
        success: Any,
        root: Any,
        req_id: str,
        *,
        manifest_context: RunManifestContext | None = None,
    ) -> Any:
        raise CADExportError("Step export failed")

    service = GenerationService(
        example_resolver=lambda req: PlanProposal(
            plan_payload=make_valid_plan_payload(req.request_id),
            provenance="example_plan",
            source_id="sample_01",
        ),
        runtime_factory=lambda: runtime,
        executor_factory=lambda rt, dh: FakeExecutor(rt, dh, result=exec_success),
        artifact_finalizer=failing_finalizer,
    )
    req = ExampleGenerationRequest(
        contract_version="1.0",
        request_id="req_fin_fail_warn",
        kind="example_plan",
        unit="mm",
        example_id="sample",
    )
    resp = service.generate(req, output_root=tmp_path)
    assert resp["status"] == "failed"
    assert resp["errors"][0]["code"] == "ARTIFACT_EXPORT_FAILED"
    assert any("Feature validation evaluated under relaxed capability-first policy." in w for w in resp["warnings"])


def test_malformed_finalizer_result_maps_to_internal_error_with_warnings(tmp_path: Path) -> None:
    """Proves malformed finalizer results map to INTERNAL_ERROR while retaining executor warnings."""
    runtime = FakeRuntime()
    exec_success = ExecutionSuccess(
        operations_executed=1,
        exported_artifacts=[],
        warnings=[{"code": "GATE_POLICY_RELAXED"}],
        inspection_report=StandardInspectionReport(
            volume_mm3=100.0,
            mass_kg=0.01,
            feature_count=1,
            body_count=1,
            solid_body_count=1,
        ),
    )

    def malformed_finalizer(
        executor: Any,
        success: Any,
        root: Any,
        req_id: str,
        *,
        manifest_context: RunManifestContext | None = None,
    ) -> Any:
        return object()  # Lacks exported_artifacts and warnings attributes

    service = GenerationService(
        example_resolver=lambda req: PlanProposal(
            plan_payload=make_valid_plan_payload(req.request_id),
            provenance="example_plan",
            source_id="sample_01",
        ),
        runtime_factory=lambda: runtime,
        executor_factory=lambda rt, dh: FakeExecutor(rt, dh, result=exec_success),
        artifact_finalizer=malformed_finalizer,
    )
    req = ExampleGenerationRequest(
        contract_version="1.0",
        request_id="req_malformed_fin",
        kind="example_plan",
        unit="mm",
        example_id="sample",
    )
    resp = service.generate(req, output_root=tmp_path)
    assert resp["status"] == "failed"
    assert resp["errors"][0]["code"] == "INTERNAL_ERROR"
    assert any("Feature validation evaluated under relaxed capability-first policy." in w for w in resp["warnings"])


# ---------------------------------------------------------------------------
# Phase D Integration: Manifest Context Construction & Publishing
# ---------------------------------------------------------------------------


def test_generation_service_provides_manifest_context_for_example_request(tmp_path: Path) -> None:
    """Proves GenerationService constructs and passes RunManifestContext for example requests."""
    captured_context: RunManifestContext | None = None

    def spy_finalizer(
        executor: Any,
        success_result: ExecutionSuccess,
        output_root: Path | str,
        request_id: str,
        *,
        manifest_context: RunManifestContext | None = None,
    ) -> ExecutionSuccess:
        nonlocal captured_context
        captured_context = manifest_context
        return fake_artifact_finalizer(
            executor,
            success_result,
            output_root,
            request_id,
            manifest_context=manifest_context,
        )

    runtime = FakeRuntime(version_build="Solid Edge 2026 (226.00.00.106)")
    service = GenerationService(
        example_resolver=lambda req: PlanProposal(
            plan_payload=make_valid_plan_payload(req.request_id),
            provenance="example_plan",
            source_id="sample_01",
        ),
        runtime_factory=lambda: runtime,
        executor_factory=lambda rt, dh: FakeExecutor(rt, dh),
        artifact_finalizer=spy_finalizer,
    )

    req = ExampleGenerationRequest(
        contract_version="1.0",
        request_id="req_example_manifest_ctx",
        kind="example_plan",
        unit="mm",
        example_id="sample_01",
    )
    resp = service.generate(req, output_root=tmp_path)

    assert resp["status"] == "accepted"
    assert captured_context is not None
    assert isinstance(captured_context, RunManifestContext)
    assert captured_context.request_id == "req_example_manifest_ctx"
    assert captured_context.contract_version == "1.0"
    assert captured_context.unit == "mm"
    assert captured_context.cad_runtime_version_build == "Solid Edge 2026 (226.00.00.106)"
    assert captured_context.prepared_data.provenance_kind == "example_plan"
    assert captured_context.prepared_data.request_kind == "example_plan"
    assert captured_context.prepared_data.prompt_sha256 is None
    assert captured_context.prepared_data.source_id == "sample_01"


def test_generation_service_provides_manifest_context_for_prompt_request(tmp_path: Path) -> None:
    """Proves GenerationService constructs and passes RunManifestContext for prompt requests."""
    captured_context: RunManifestContext | None = None

    def spy_finalizer(
        executor: Any,
        success_result: ExecutionSuccess,
        output_root: Path | str,
        request_id: str,
        *,
        manifest_context: RunManifestContext | None = None,
    ) -> ExecutionSuccess:
        nonlocal captured_context
        captured_context = manifest_context
        return fake_artifact_finalizer(
            executor,
            success_result,
            output_root,
            request_id,
            manifest_context=manifest_context,
        )

    prompt_text = "Generate a base plate 80x40x6 mm"
    expected_prompt_sha256 = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()

    runtime = FakeRuntime(version_build="Solid Edge 2026 (226.00.00.106)")
    service = GenerationService(
        prompt_resolver=lambda req: PlanProposal(
            plan_payload=make_valid_plan_payload(req.request_id),
            provenance="ai_proposal",
            source_id="agent_v1",
        ),
        runtime_factory=lambda: runtime,
        executor_factory=lambda rt, dh: FakeExecutor(rt, dh),
        artifact_finalizer=spy_finalizer,
    )

    req = PromptGenerationRequest(
        contract_version="1.0",
        request_id="req_prompt_manifest_ctx",
        kind="prompt_to_cad",
        unit="mm",
        prompt=prompt_text,
    )
    resp = service.generate(req, output_root=tmp_path)

    assert resp["status"] == "accepted"
    assert captured_context is not None
    assert isinstance(captured_context, RunManifestContext)
    assert captured_context.request_id == "req_prompt_manifest_ctx"
    assert captured_context.contract_version == "1.0"
    assert captured_context.unit == "mm"
    assert captured_context.prepared_data.provenance_kind == "ai_proposal"
    assert captured_context.prepared_data.request_kind == "prompt_to_cad"
    assert captured_context.prepared_data.prompt_sha256 == expected_prompt_sha256
    # Privacy check: prompt string is not retained in serialized plan
    assert prompt_text not in json.dumps(captured_context.prepared_data.feature_plan)


def test_runtime_version_extracted_into_manifest_context(tmp_path: Path) -> None:
    """Proves CAD runtime version string is extracted safely into the manifest context."""
    captured_context: RunManifestContext | None = None

    def spy_finalizer(
        executor: Any,
        success_result: ExecutionSuccess,
        output_root: Path | str,
        request_id: str,
        *,
        manifest_context: RunManifestContext | None = None,
    ) -> ExecutionSuccess:
        nonlocal captured_context
        captured_context = manifest_context
        return fake_artifact_finalizer(
            executor,
            success_result,
            output_root,
            request_id,
            manifest_context=manifest_context,
        )

    runtime = FakeRuntime(version_build="Solid Edge 2026.1.0")
    service = GenerationService(
        example_resolver=lambda req: PlanProposal(
            plan_payload=make_valid_plan_payload(req.request_id),
            provenance="example_plan",
            source_id="sample_01",
        ),
        runtime_factory=lambda: runtime,
        executor_factory=lambda rt, dh: FakeExecutor(rt, dh),
        artifact_finalizer=spy_finalizer,
    )

    req = ExampleGenerationRequest(
        contract_version="1.0",
        request_id="req_version_extracted",
        kind="example_plan",
        unit="mm",
        example_id="sample_01",
    )
    service.generate(req, output_root=tmp_path)

    assert captured_context is not None
    assert captured_context.cad_runtime_version_build == "Solid Edge 2026.1.0"


def test_missing_runtime_version_retains_unavailable_warning(tmp_path: Path) -> None:
    """Proves missing runtime version results in None build and VERSION_METADATA_UNAVAILABLE warning."""
    captured_context: RunManifestContext | None = None

    def spy_finalizer(
        executor: Any,
        success_result: ExecutionSuccess,
        output_root: Path | str,
        request_id: str,
        *,
        manifest_context: RunManifestContext | None = None,
    ) -> ExecutionSuccess:
        nonlocal captured_context
        captured_context = manifest_context
        return fake_artifact_finalizer(
            executor,
            success_result,
            output_root,
            request_id,
            manifest_context=manifest_context,
        )

    runtime = FakeRuntime(version_build=None)
    service = GenerationService(
        example_resolver=lambda req: PlanProposal(
            plan_payload=make_valid_plan_payload(req.request_id),
            provenance="example_plan",
            source_id="sample_01",
        ),
        runtime_factory=lambda: runtime,
        executor_factory=lambda rt, dh: FakeExecutor(rt, dh),
        artifact_finalizer=spy_finalizer,
    )

    req = ExampleGenerationRequest(
        contract_version="1.0",
        request_id="req_version_missing",
        kind="example_plan",
        unit="mm",
        example_id="sample_01",
    )
    resp = service.generate(req, output_root=tmp_path)

    assert resp["status"] == "accepted"
    assert captured_context is not None
    assert captured_context.cad_runtime_version_build is None
    assert any("CAD runtime did not report version metadata." in w for w in resp["warnings"])
    assert any("CAD runtime did not report version metadata." in w for w in captured_context.warnings)


def test_pre_runtime_manifest_configuration_error_maps_to_export_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proves missing package metadata fails closed before runtime acquisition without touching CAD runtime."""
    runtime_factory_called = False

    def tracking_runtime_factory() -> FakeRuntime:
        nonlocal runtime_factory_called
        runtime_factory_called = True
        return FakeRuntime()

    def failing_prepare(*args: Any, **kwargs: Any) -> Any:
        raise ManifestConfigurationError("Package distribution metadata for 'cad-copilot' is not installed.")

    monkeypatch.setattr("application.service.prepare_manifest_data", failing_prepare)

    service = GenerationService(
        example_resolver=lambda req: PlanProposal(
            plan_payload=make_valid_plan_payload(req.request_id),
            provenance="example_plan",
            source_id="sample_01",
        ),
        runtime_factory=tracking_runtime_factory,
        executor_factory=lambda rt, dh: FakeExecutor(rt, dh),
        artifact_finalizer=fake_artifact_finalizer,
    )

    req = ExampleGenerationRequest(
        contract_version="1.0",
        request_id="req_config_fail_pre_runtime",
        kind="example_plan",
        unit="mm",
        example_id="sample_01",
    )
    resp = service.generate(req, output_root=tmp_path)

    assert resp["status"] == "failed"
    assert resp["errors"][0]["code"] == "ARTIFACT_EXPORT_FAILED"
    assert runtime_factory_called is False


def test_pre_runtime_manifest_validation_error_maps_to_plan_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proves plan metadata rejection during manifest preparation maps to CAD_PLAN_REJECTED before runtime acquisition."""
    runtime_factory_called = False

    def tracking_runtime_factory() -> FakeRuntime:
        nonlocal runtime_factory_called
        runtime_factory_called = True
        return FakeRuntime()

    def failing_prepare(*args: Any, **kwargs: Any) -> Any:
        raise ManifestValidationError("Feature plan contains invalid geometry tokens")

    monkeypatch.setattr("application.service.prepare_manifest_data", failing_prepare)

    service = GenerationService(
        example_resolver=lambda req: PlanProposal(
            plan_payload=make_valid_plan_payload(req.request_id),
            provenance="example_plan",
            source_id="sample_01",
        ),
        runtime_factory=tracking_runtime_factory,
        executor_factory=lambda rt, dh: FakeExecutor(rt, dh),
        artifact_finalizer=fake_artifact_finalizer,
    )

    req = ExampleGenerationRequest(
        contract_version="1.0",
        request_id="req_val_fail_pre_runtime",
        kind="example_plan",
        unit="mm",
        example_id="sample_01",
    )
    resp = service.generate(req, output_root=tmp_path)

    assert resp["status"] == "failed"
    assert resp["errors"][0]["code"] == "CAD_PLAN_REJECTED"
    assert runtime_factory_called is False


def test_finalizer_manifest_export_error_retains_accumulated_warnings(tmp_path: Path) -> None:
    """Proves manifest assembly or writing failure during finalization preserves accumulated warnings."""
    runtime = FakeRuntime()
    exec_success = ExecutionSuccess(
        operations_executed=1,
        exported_artifacts=[],
        warnings=[{"code": "GATE_POLICY_RELAXED"}],
        inspection_report=StandardInspectionReport(
            volume_mm3=100.0,
            mass_kg=0.01,
            feature_count=1,
            body_count=1,
            solid_body_count=1,
        ),
    )

    def failing_finalizer(
        executor: Any,
        success: Any,
        root: Any,
        req_id: str,
        *,
        manifest_context: RunManifestContext | None = None,
    ) -> Any:
        raise CADExportError("Failed to assemble or publish run manifest", error_code="ARTIFACT_EXPORT_FAILED")

    service = GenerationService(
        example_resolver=lambda req: PlanProposal(
            plan_payload=make_valid_plan_payload(req.request_id),
            provenance="example_plan",
            source_id="sample_01",
            warnings=[{"code": "CANONICAL_MULTI_HOLE_FAMILY"}],
        ),
        runtime_factory=lambda: runtime,
        executor_factory=lambda rt, dh: FakeExecutor(rt, dh, result=exec_success),
        artifact_finalizer=failing_finalizer,
    )
    req = ExampleGenerationRequest(
        contract_version="1.0",
        request_id="req_fin_manifest_fail",
        kind="example_plan",
        unit="mm",
        example_id="sample_01",
    )
    resp = service.generate(req, output_root=tmp_path)
    assert resp["status"] == "failed"
    assert resp["errors"][0]["code"] == "ARTIFACT_EXPORT_FAILED"
    # Verify warnings from resolver and executor are preserved
    assert any("Multiple circular through-holes accepted by canonical validation." in w for w in resp["warnings"])
    assert any("Feature validation evaluated under relaxed capability-first policy." in w for w in resp["warnings"])


def test_end_to_end_generation_publishes_manifest_and_validates_schema(tmp_path: Path) -> None:
    """Proves end-to-end generation with production finalizer publishes valid run_manifest.json sidecar."""
    runtime = FakeRuntime(version_build="Solid Edge 2026 (226.00.00.106)")
    req_id = "req_e2e_manifest_pub"

    service = GenerationService(
        example_resolver=lambda req: PlanProposal(
            plan_payload=make_valid_plan_payload(req.request_id),
            provenance="example_plan",
            source_id="sample_01",
        ),
        runtime_factory=lambda: runtime,
        executor_factory=lambda rt, dh: FakeProductionExecutor(rt, dh),
        artifact_finalizer=finalize_request_artifacts,
    )

    req = ExampleGenerationRequest(
        contract_version="1.0",
        request_id=req_id,
        kind="example_plan",
        unit="mm",
        example_id="sample_01",
    )
    resp = service.generate(req, output_root=tmp_path)

    assert resp["status"] == "accepted"
    # Artifact records in response contains strictly the 4 model files
    assert len(resp["data"]["artifacts"]) == 4
    for record in resp["data"]["artifacts"]:
        assert record["format"] in ("par", "step", "stl", "jpg")
        assert not record["path"].endswith("run_manifest.json")

    # On disk: 5 files published including run_manifest.json
    published_dir = tmp_path / req_id
    assert published_dir.is_dir()
    manifest_file = published_dir / "run_manifest.json"
    assert manifest_file.is_file()

    # Validate published manifest against canonical schema
    manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
    validator = get_run_manifest_validator()
    validator.validate(manifest_data)

    assert manifest_data["request"]["request_id"] == req_id
    assert manifest_data["request"]["contract_version"] == "1.0"
    assert manifest_data["request"]["unit"] == "mm"
    assert manifest_data["cad_runtime"]["version_build"] == "Solid Edge 2026 (226.00.00.106)"
    assert len(manifest_data["artifacts"]) == 4


# ---------------------------------------------------------------------------
# M4.3 Tests: Deterministic Example Catalog Integration
# ---------------------------------------------------------------------------


def test_generation_service_with_production_example_resolver_unknown_id_fails_before_runtime(tmp_path: Path) -> None:
    """Proves unknown example_id fails in Phase B before CAD runtime acquisition."""
    runtime_factory_called = False

    def spy_runtime_factory() -> CADRuntimeABC:
        nonlocal runtime_factory_called
        runtime_factory_called = True
        return FakeRuntime()

    service = GenerationService(
        example_resolver=resolve_example_plan,
        runtime_factory=spy_runtime_factory,
        executor_factory=lambda rt, dh: FakeExecutor(rt, dh),
        artifact_finalizer=fake_artifact_finalizer,
    )

    req = ExampleGenerationRequest(
        contract_version="1.0",
        request_id="req_m43_unknown_01",
        kind="example_plan",
        unit="mm",
        example_id="unknown_part_sample",
    )
    resp = service.generate(req, output_root=tmp_path)

    assert resp["status"] == "rejected"
    assert resp["request_id"] == "req_m43_unknown_01"
    assert resp["errors"][0]["code"] == "UNSUPPORTED_REQUEST"
    assert runtime_factory_called is False


def test_generation_service_with_production_example_resolver_publishes_manifest_on_disk(tmp_path: Path) -> None:
    """Proves end-to-end spur-gear generation publishes valid run_manifest.json with correct provenance."""
    runtime = FakeRuntime(version_build="Solid Edge 2026 (226.00.00.106)")
    req_id = "req_m43_e2e_manifest"

    service = GenerationService(
        example_resolver=resolve_example_plan,
        runtime_factory=lambda: runtime,
        executor_factory=lambda rt, dh: FakeProductionExecutor(rt, dh),
        artifact_finalizer=finalize_request_artifacts,
    )

    req = ExampleGenerationRequest(
        contract_version="1.0",
        request_id=req_id,
        kind="example_plan",
        unit="mm",
        example_id="spur_gear",
    )
    resp = service.generate(req, output_root=tmp_path)

    assert resp["status"] == "accepted"
    assert len(resp["data"]["artifacts"]) == 4

    published_dir = tmp_path / req_id
    assert published_dir.is_dir()
    manifest_file = published_dir / "run_manifest.json"
    assert manifest_file.is_file()

    manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
    validator = get_run_manifest_validator()
    validator.validate(manifest_data)

    assert manifest_data["request"]["request_id"] == req_id
    assert manifest_data["request"]["contract_version"] == "1.0"
    assert manifest_data["request"]["kind"] == "example_plan"
    assert manifest_data["request"]["unit"] == "mm"
    assert manifest_data["provenance"]["kind"] == "example_plan"
    assert manifest_data["provenance"]["source_id"] == "spur_gear"
    assert manifest_data["fingerprints"]["prompt_sha256"] is None
    assert manifest_data["cad_runtime"]["version_build"] == "Solid Edge 2026 (226.00.00.106)"
    assert manifest_data["feature_plan"]["base_body"]["family"] == "spur_gear"
    assert len(manifest_data["artifacts"]) == 4

    # Ensure placeholder request ID is never published
    assert "replace-with-request-id" not in json.dumps(manifest_data)

    # Document was closed by artifact finalizer
    assert runtime.close_doc_count == 1


def test_generation_service_raises_teardown_incomplete_when_teardown_returns_false(tmp_path: Path) -> None:
    """Proves that GenerationService raises TeardownIncompleteError when runtime teardown returns False."""
    runtime = FakeRuntime(version_build="Solid Edge 2026 (226.00.00.106)")
    runtime.fail_teardown = True

    service = GenerationService(
        example_resolver=resolve_example_plan,
        runtime_factory=lambda: runtime,
        executor_factory=lambda rt, dh: FakeProductionExecutor(rt, dh),
        artifact_finalizer=finalize_request_artifacts,
    )
    req = ExampleGenerationRequest(
        request_id="req-teardown-false",
        contract_version="1.0",
        kind="example_plan",
        unit="mm",
        example_id="spur_gear",
    )
    with pytest.raises(TeardownIncompleteError, match="teardown completed incompletely"):
        service.generate(req, output_root=tmp_path)

    assert runtime.teardown_count == 1
    assert runtime.teardown_args == [False]


def test_generation_service_raises_teardown_incomplete_when_teardown_raises(tmp_path: Path) -> None:
    """Proves that GenerationService raises TeardownIncompleteError when runtime teardown throws an exception."""
    runtime = FakeRuntime(version_build="Solid Edge 2026 (226.00.00.106)")
    runtime.raise_teardown = True

    service = GenerationService(
        example_resolver=resolve_example_plan,
        runtime_factory=lambda: runtime,
        executor_factory=lambda rt, dh: FakeProductionExecutor(rt, dh),
        artifact_finalizer=finalize_request_artifacts,
    )
    req = ExampleGenerationRequest(
        request_id="req-teardown-exc",
        contract_version="1.0",
        kind="example_plan",
        unit="mm",
        example_id="spur_gear",
    )
    with pytest.raises(TeardownIncompleteError, match="teardown failed with an error"):
        service.generate(req, output_root=tmp_path)

    assert runtime.teardown_count == 1
    assert runtime.teardown_args == [False]


def test_generation_service_raises_teardown_incomplete_on_early_return_path(tmp_path: Path) -> None:
    """Proves that every service early-return path that creates a runtime observes teardown failure."""
    runtime = FakeRuntime()
    runtime.fail_connect = True  # Triggers early return of CAD_EXECUTION_FAILED
    runtime.fail_teardown = True

    service = GenerationService(
        example_resolver=resolve_example_plan,
        runtime_factory=lambda: runtime,
    )
    req = ExampleGenerationRequest(
        contract_version="1.0",
        request_id="req-early-fail-teardown",
        kind="example_plan",
        unit="mm",
        example_id="spur_gear",
    )
    with pytest.raises(TeardownIncompleteError):
        service.generate(req, output_root=tmp_path)

    assert runtime.connect_count == 1
    assert runtime.teardown_count == 1
    assert runtime.teardown_args == [False]


def test_generation_service_retains_accepted_outcome_when_teardown_succeeds(tmp_path: Path) -> None:
    """Proves that an accepted outcome is returned when teardown succeeds."""
    runtime = FakeRuntime(version_build="Solid Edge 2026 (226.00.00.106)")
    runtime.fail_teardown = False

    service = GenerationService(
        example_resolver=resolve_example_plan,
        runtime_factory=lambda: runtime,
        executor_factory=lambda rt, dh: FakeProductionExecutor(rt, dh),
        artifact_finalizer=finalize_request_artifacts,
    )
    req = ExampleGenerationRequest(
        request_id="req-teardown-clean",
        contract_version="1.0",
        kind="example_plan",
        unit="mm",
        example_id="spur_gear",
    )
    resp = service.generate(req, output_root=tmp_path)
    assert resp["status"] == "accepted"
    assert runtime.teardown_count == 1
    assert runtime.teardown_args == [False]


def test_generation_service_raises_teardown_incomplete_when_teardown_returns_non_boolean_truthy(
    tmp_path: Path,
) -> None:
    """Proves that teardown check fails closed if runtime.teardown returns a non-boolean truthy value."""
    runtime = FakeRuntime(version_build="Solid Edge 2026 (226.00.00.106)")
    runtime.teardown_return_override = 1  # Truthy integer, but not literal True

    service = GenerationService(
        example_resolver=resolve_example_plan,
        runtime_factory=lambda: runtime,
        executor_factory=lambda rt, dh: FakeProductionExecutor(rt, dh),
        artifact_finalizer=finalize_request_artifacts,
    )
    req = ExampleGenerationRequest(
        request_id="req-teardown-truthy-nonbool",
        contract_version="1.0",
        kind="example_plan",
        unit="mm",
        example_id="spur_gear",
    )
    with pytest.raises(TeardownIncompleteError, match="teardown completed incompletely"):
        service.generate(req, output_root=tmp_path)

    assert runtime.teardown_count == 1
    assert runtime.teardown_args == [False]
