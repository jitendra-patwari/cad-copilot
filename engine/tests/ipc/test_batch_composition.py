"""Focused unit and integration tests for batch composition and execution wiring.

Proves:
1. Explicit exactly-once service execution and exactly-once fake CAD runtime acquisition.
2. Explicit connect and teardown lifecycle calls through a non-COM fake integration.
3. Shared safety boundary feeding both bindings and service.
4. Parity between operation registry and bindings.
5. Exactly-once finalization and schema-valid response projection.
6. Zero provider/network modules touched during batch execution.
7. Early failure when engine version metadata cannot be resolved.
8. Request type contract enforcement.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from batch.composition import build_initial_operation_bindings
from batch.finalization import finalize_batch_outcome
from batch.models import BatchRequest
from batch.registry import build_initial_registry
from batch.safety import FilesystemBatchSafetyBoundary
from batch.service import BatchService
from ipc.batch_composition import run_batch
from ipc.batch_contracts import build_typed_batch_request
from tests.batch.fake_support import (
    FakeCADRuntime,
    FakeSafetyBoundary,
    make_test_bindings,
)


def test_run_batch_execution_and_fake_runtime_acquisition_counters(tmp_path: Path) -> None:
    """Proves exactly-once service execution, runtime factory call, connect, and teardown."""
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    (in_dir / "part1.par").write_bytes(b"dummy CAD file content")

    out_base = tmp_path / "out"
    out_base.mkdir()
    out_dir = out_base / "output"
    out_dir.mkdir()

    req_payload = {
        "contract_version": "1.0",
        "request_id": "req-counter-test-01",
        "kind": "batch_operation",
        "operation": {"type": "export_3d", "formats": ["step"]},
        "input": {"root": str(in_dir), "files": ["part1.par"]},
        "output_root": str(out_dir),
    }
    typed_req = build_typed_batch_request(req_payload)

    fake_runtime = FakeCADRuntime(version_build="226.00.00.106")
    runtime_factory_call_count = 0

    def spy_runtime_factory() -> Any:
        nonlocal runtime_factory_call_count
        runtime_factory_call_count += 1
        return fake_runtime

    boundary = FakeSafetyBoundary(out_base)

    # Write published artifact so finalize_batch_outcome publication check passes
    def on_format(ctx: Any) -> None:
        ctx.target_path.parent.mkdir(parents=True, exist_ok=True)
        ctx.target_path.write_bytes(b"published step artifact")

    test_bindings, _ = make_test_bindings(on_format=on_format)
    test_registry = build_initial_registry()

    service_execute_call_count = 0
    original_execute = BatchService.execute

    def spy_execute(self: BatchService, request: BatchRequest) -> Any:
        nonlocal service_execute_call_count
        service_execute_call_count += 1
        return original_execute(self, request)

    with (
        patch.object(BatchService, "execute", spy_execute),
        patch("ipc.batch_composition.finalize_batch_outcome", wraps=finalize_batch_outcome) as spy_finalize,
    ):
        result = run_batch(
            typed_req,
            _engine_version="1.2.3",
            _safety_boundary=boundary,
            _bindings=test_bindings,
            _registry=test_registry,
            _runtime_factory=spy_runtime_factory,
        )

    # 1. Verify exactly-once service execution
    assert service_execute_call_count == 1

    # 2. Verify exactly-once runtime acquisition and connection
    assert runtime_factory_call_count == 1
    assert fake_runtime.connect_calls == 1

    # 3. Verify exactly-once runtime teardown
    assert fake_runtime.teardown_calls == [True]

    # 4. Verify exactly-once outcome finalization
    assert spy_finalize.call_count == 1
    finalize_call_args = spy_finalize.call_args
    assert finalize_call_args[0][0] == typed_req
    assert finalize_call_args[1]["engine_version"] == "1.2.3"

    # 5. Verify registry and binding parity
    assert set(test_registry.operation_ids) == set(test_bindings.operation_ids)

    # 6. Verify valid projected response structure
    assert result["status"] == "completed"
    assert result["request_id"] == "req-counter-test-01"
    assert result["summary"]["total"] == 1
    assert result["summary"]["accepted"] == 1
    assert result["summary"]["failed"] == 0
    assert len(result["results"]) == 1
    assert result["results"][0]["input"] == "part1.par"
    assert result["results"][0]["status"] == "accepted"
    assert "manifest" in result
    assert result["manifest"]["path"].endswith("req-counter-test-01.batch_manifest.json")

    # 7. Verify zero network / LLM provider modules touched
    assert "google" not in sys.modules
    assert "openai" not in sys.modules
    assert "anthropic" not in sys.modules


def test_run_batch_wires_shared_safety_boundary_and_bindings(tmp_path: Path) -> None:
    """Proves default binding construction uses the shared safety boundary and satisfies parity."""
    boundary = FilesystemBatchSafetyBoundary()

    req_payload = {
        "contract_version": "1.0",
        "request_id": "req-wire-test-02",
        "kind": "batch_operation",
        "operation": {"type": "export_3d", "formats": ["step"]},
        "input": {"root": str(tmp_path), "files": ["part1.par"]},
        "output_root": str(tmp_path / "out"),
    }
    typed_req = build_typed_batch_request(req_payload)

    captured_service_kwargs: dict[str, Any] = {}
    runtime_factory_call_count = 0

    def spy_runtime_factory() -> Any:
        nonlocal runtime_factory_call_count
        runtime_factory_call_count += 1
        return FakeCADRuntime()

    class SpyService:
        def __init__(self, **kwargs: Any) -> None:
            captured_service_kwargs.update(kwargs)
            # Runtime factory must never be called during service construction
            assert runtime_factory_call_count == 0

        def execute(self, request: BatchRequest) -> Any:
            from batch.execution import BatchExecutionOutcome
            from batch.models import BatchDiagnostic

            return BatchExecutionOutcome(
                status="failed",
                request_id="req-wire-test-02",
                contract_version="1.0",
                errors=(BatchDiagnostic(code="INTERNAL_ERROR", message="Test wire outcome."),),
            )

    def spy_finalize(req: Any, outcome: Any, *, engine_version: str) -> Any:
        from batch.models import BatchDiagnostic, BatchResponse

        return BatchResponse(
            contract_version="1.0",
            request_id="req-wire-test-02",
            status="failed",
            errors=(BatchDiagnostic(code="INTERNAL_ERROR", message="Test wire outcome."),),
        )

    with (
        patch(
            "ipc.batch_composition.build_initial_operation_bindings",
            wraps=build_initial_operation_bindings,
        ) as spy_build_bindings,
        patch("ipc.batch_composition.BatchService", SpyService),
        patch("ipc.batch_composition.finalize_batch_outcome", spy_finalize),
    ):
        result = run_batch(
            typed_req,
            _engine_version="2.0.0",
            _safety_boundary=boundary,
            _runtime_factory=spy_runtime_factory,
        )

    # Verify single safety boundary feeds both bindings and service
    spy_build_bindings.assert_called_once()
    assert spy_build_bindings.call_args[0][0] is boundary
    assert captured_service_kwargs["safety_boundary"] is boundary

    # Verify registry and binding parity
    captured_bindings = captured_service_kwargs["bindings"]
    captured_registry = captured_service_kwargs["registry"]
    assert set(captured_registry.operation_ids) == set(captured_bindings.operation_ids)

    # Verify projected response
    assert result["status"] == "failed"
    assert result["request_id"] == "req-wire-test-02"


def test_run_batch_version_resolution_failure() -> None:
    """Proves run_batch returns sanitized failed response when engine version is unresolvable."""
    req_payload = {
        "contract_version": "1.0",
        "request_id": "req-ver-fail",
        "kind": "batch_operation",
        "operation": {"type": "export_3d", "formats": ["step"]},
        "input": {"root": "E:/input", "files": ["part1.par"]},
        "output_root": "E:/output",
    }
    typed_req = build_typed_batch_request(req_payload)

    runtime_factory_called = False

    def spy_runtime_factory() -> Any:
        nonlocal runtime_factory_called
        runtime_factory_called = True
        return FakeCADRuntime()

    with patch("importlib.metadata.version", side_effect=Exception("Package not found")):
        result = run_batch(
            typed_req,
            _engine_version=None,
            _runtime_factory=spy_runtime_factory,
        )

    assert result["status"] == "failed"
    assert result["request_id"] == "req-ver-fail"
    assert result["errors"][0]["code"] == "INTERNAL_ERROR"
    assert not runtime_factory_called


def test_run_batch_invalid_request_type_raises() -> None:
    """Proves run_batch rejects non-BatchRequest input with TypeError."""
    with pytest.raises(TypeError, match="Expected BatchRequest"):
        run_batch({"not": "a batch request"})  # type: ignore[arg-type]
