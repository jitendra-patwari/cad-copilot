"""Unit tests for BatchService happy-path lifecycle, ordering, resource ownership, and extensibility."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from batch.bindings import OperationBinding, OperationBindings
from batch.execution import (
    BatchExecutionSpec,
    BatchItemContext,
    BatchItemOutcome,
)
from batch.models import BatchArtifactRecord, BatchConfigurationError
from batch.registry import OperationDescriptor, OperationRegistry
from batch.service import BatchService
from interfaces.runtime_abc import CADRuntimeABC
from tests.batch.fake_support import (
    FakeCADRuntime,
    FakeSafetyBoundary,
    make_execution_spec,
    make_test_bindings,
)


class TestServiceLifecycle:
    def test_single_file_success(self, tmp_path: Path) -> None:
        runtime = FakeCADRuntime()
        boundary = FakeSafetyBoundary(tmp_path)
        bindings, _ = make_test_bindings()
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = make_execution_spec(inputs=("bracket.par",), formats=("step", "stl"))
        outcome = service.execute(spec)

        assert outcome.status == "completed"
        assert outcome.summary is not None
        assert outcome.summary.total == 1
        assert outcome.summary.accepted == 1
        assert outcome.summary.partial == 0
        assert outcome.summary.failed == 0
        assert outcome.summary.unprocessed == 0
        assert outcome.summary.cancelled == 0

        assert len(outcome.file_results) == 1
        res = outcome.file_results[0]
        assert res.input == "bracket.par"
        assert res.status == "accepted"
        assert len(res.artifacts) == 2
        assert res.artifacts[0].format == "step"
        assert res.artifacts[1].format == "stl"

        # Verify runtime lifecycle calls
        assert runtime.connect_calls == 1
        assert len(runtime.open_calls) == 1
        assert len(runtime.close_calls) == 1
        assert runtime.teardown_calls == [True]
        assert runtime.max_simultaneous_open == 1

    def test_multi_file_caller_ordering_preserved(self, tmp_path: Path) -> None:
        runtime = FakeCADRuntime()
        boundary = FakeSafetyBoundary(tmp_path)
        bindings, recorded = make_test_bindings()
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        inputs = ("z_part.par", "a_part.par", "m_part.par")
        spec = make_execution_spec(inputs=inputs, formats=("stl", "step"))
        outcome = service.execute(spec)

        assert outcome.status == "completed"
        assert outcome.summary is not None
        assert outcome.summary.total == 3
        assert outcome.summary.accepted == 3

        # Exact caller file order
        assert [r.input for r in outcome.file_results] == list(inputs)
        for res in outcome.file_results:
            assert [a.format for a in res.artifacts] == ["stl", "step"]

        # Handler invocations preserved exact ordering
        assert [c.input for c in recorded] == [
            "z_part.par",
            "z_part.par",
            "a_part.par",
            "a_part.par",
            "m_part.par",
            "m_part.par",
        ]
        assert [c.format for c in recorded] == ["stl", "step", "stl", "step", "stl", "step"]

    def test_strict_one_document_open_at_a_time_invariant(self, tmp_path: Path) -> None:
        runtime = FakeCADRuntime()
        boundary = FakeSafetyBoundary(tmp_path)
        bindings, _ = make_test_bindings()
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = make_execution_spec(
            inputs=("f1.par", "f2.par", "f3.par", "f4.par"),
            formats=("step",),
        )
        outcome = service.execute(spec)

        assert outcome.status == "completed"
        assert runtime.max_simultaneous_open == 1
        assert len(runtime.open_calls) == 4
        assert len(runtime.close_calls) == 4
        assert len(runtime.open_handles) == 0

    def test_drawing_operation_lifecycle(self, tmp_path: Path) -> None:
        runtime = FakeCADRuntime()
        boundary = FakeSafetyBoundary(tmp_path)
        bindings, recorded = make_test_bindings()
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = make_execution_spec(
            inputs=("sheet1.dft", "sheet2.dft"),
            formats=("pdf", "dxf"),
            operation_id="publish_drawing",
        )
        outcome = service.execute(spec)

        assert outcome.status == "completed"
        assert outcome.summary is not None
        assert outcome.summary.total == 2
        assert outcome.summary.accepted == 2
        assert len(recorded) == 4

    def test_extensibility_proof_custom_operation_without_modifying_hub(self, tmp_path: Path) -> None:
        """A new operation executes through custom descriptor, registry, binding, and unchanged BatchService."""
        custom_descriptor = OperationDescriptor(
            operation_id="custom_analysis",
            input_extensions=(".par",),
            output_formats=("step",),
            progress_label="Custom Analysis",
        )
        custom_registry = OperationRegistry((custom_descriptor,))

        recorded_contexts: list[BatchItemContext] = []

        def custom_handler(doc_handle: object, context: BatchItemContext) -> BatchItemOutcome:
            recorded_contexts.append(context)
            return BatchItemOutcome.success(
                format=context.format,
                artifact=BatchArtifactRecord(format="step", path=str(context.target_path)),
            )

        def custom_factory(runtime: CADRuntimeABC) -> Any:
            return custom_handler

        custom_bindings = OperationBindings(
            (OperationBinding(operation_id="custom_analysis", factory=custom_factory),),
            registry=custom_registry,
        )

        runtime = FakeCADRuntime()
        boundary = FakeSafetyBoundary(tmp_path)

        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=custom_bindings,
            registry=custom_registry,
        )

        spec = BatchExecutionSpec(
            contract_version="1.0",
            request_id="req-custom-001",
            input_root="models",
            output_root="output",
            inputs=("custom_model.par",),
            operation_id="custom_analysis",
            formats=("step",),
            continue_on_error=True,
        )

        outcome = service.execute(spec)

        assert outcome.status == "completed"
        assert outcome.summary is not None
        assert outcome.summary.accepted == 1
        assert len(recorded_contexts) == 1
        assert recorded_contexts[0].operation_id == "custom_analysis"
        assert runtime.connect_calls == 1
        assert runtime.teardown_calls == [True]

    def test_configuration_mismatch_fails_fast(self, tmp_path: Path) -> None:
        """Unsupported format or input extension fails before any runtime connection."""
        runtime = FakeCADRuntime()
        boundary = FakeSafetyBoundary(tmp_path)
        bindings, _ = make_test_bindings()
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        # Unsupported format for export_3d (pdf is only for publish_drawing)
        bad_format_spec = make_execution_spec(inputs=("part.par",), formats=("pdf",))
        with pytest.raises(BatchConfigurationError, match="Format 'pdf' is not supported"):
            service.execute(bad_format_spec)

        assert runtime.connect_calls == 0

        # Unsupported extension for export_3d (.dft is only for publish_drawing)
        bad_ext_spec = make_execution_spec(inputs=("drawing.dft",), formats=("step",))
        with pytest.raises(BatchConfigurationError, match=r"Input extension '\.dft'"):
            service.execute(bad_ext_spec)

        assert runtime.connect_calls == 0

    def test_execute_accepts_validated_batch_request(self, tmp_path: Path) -> None:
        """execute() accepts a validated public BatchRequest and converts it via BatchExecutionSpec.from_request."""
        from batch.models import BatchInputSelection, BatchOperation, BatchOptions, BatchRequest

        runtime = FakeCADRuntime()
        boundary = FakeSafetyBoundary(tmp_path)
        bindings, _ = make_test_bindings()
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        request = BatchRequest(
            contract_version="1.0",
            request_id="req-batch-001",
            kind="batch_operation",
            input=BatchInputSelection(root="models", files=("part1.par", "part2.par")),
            output_root="output",
            operation=BatchOperation(type="export_3d", formats=("step", "stl")),
            options=BatchOptions(continue_on_error=True),
        )

        outcome = service.execute(request)
        assert outcome.status == "completed"
        assert outcome.request_id == "req-batch-001"
        assert outcome.summary is not None
        assert outcome.summary.total == 2
        assert outcome.summary.accepted == 2

    def test_execute_rejects_arbitrary_mapping_or_invalid_type(self, tmp_path: Path) -> None:
        """execute() rejects raw dictionaries, strings, or invalid types with TypeError."""
        runtime = FakeCADRuntime()
        boundary = FakeSafetyBoundary(tmp_path)
        bindings, _ = make_test_bindings()
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        with pytest.raises(TypeError, match="request_or_spec must be BatchRequest or BatchExecutionSpec"):
            service.execute({"request_id": "fake"})  # type: ignore[arg-type]

        with pytest.raises(TypeError, match="request_or_spec must be BatchRequest or BatchExecutionSpec"):
            service.execute("invalid_string")  # type: ignore[arg-type]
