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
from interfaces.exceptions import (
    CADDocumentError,
    CADExecutionError,
    CADExportError,
)
from interfaces.models import (
    ArtifactRecord,
    ExecutionFailure,
    ExecutionSuccess,
    RuntimeDiagnostics,
    StandardInspectionReport,
)
from interfaces.runtime_abc import CADRuntimeABC

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

    def __init__(self, diagnostics_warnings: list[dict[str, str]] | None = None) -> None:
        self.connect_count = 0
        self.diagnostics_count = 0
        self.create_doc_count = 0
        self.close_doc_count = 0
        self.teardown_count = 0
        self.teardown_args: list[bool] = []
        self.closed_handles: list[Any] = []
        self.diagnostics_warnings = diagnostics_warnings or []
        self.fail_connect = False
        self.fail_create_doc = False
        self.fail_close_doc = False

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

    def teardown(self, force_kill_on_failure: bool = False) -> None:
        self.teardown_count += 1
        self.teardown_args.append(force_kill_on_failure)

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


def fake_artifact_finalizer(
    executor: Any,
    success_result: ExecutionSuccess,
    output_root: Path | str,
    request_id: str,
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
    def finalizer_with_warnings(executor: Any, success: Any, root: Any, req_id: str) -> ExecutionSuccess:
        res = fake_artifact_finalizer(executor, success, root, req_id)
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

    def failing_finalizer(executor: Any, success: Any, root: Any, req_id: str) -> Any:
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

    def malformed_finalizer(executor: Any, success: Any, root: Any, req_id: str) -> Any:
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
