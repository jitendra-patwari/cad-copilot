"""Unit tests for lightweight operation bindings and registry parity validation."""

from __future__ import annotations

from typing import Any

import pytest

from batch.bindings import (
    BatchHandlerFactory,
    BatchOperationHandler,
    OperationBinding,
    OperationBindings,
)
from batch.execution import BatchItemContext, BatchItemOutcome
from batch.models import BatchArtifactRecord, BatchConfigurationError
from batch.registry import (
    OperationDescriptor,
    OperationRegistry,
    build_initial_registry,
)
from interfaces.runtime_abc import CADRuntimeABC


class DummyRuntime:
    """Minimal dummy runtime for binding tests."""

    def connect(self) -> None:
        pass

    def open_document(self, path: str) -> object:
        return "dummy_doc_handle"

    def close_document(self, handle: object) -> None:
        pass

    def teardown(self, force_kill_on_failure: bool = False) -> bool:
        return True


def _make_dummy_handler() -> BatchOperationHandler:
    def handler(doc_handle: object, context: BatchItemContext) -> BatchItemOutcome:
        return BatchItemOutcome.success(
            format=context.format,
            artifact=BatchArtifactRecord(format="step", path=str(context.target_path)),
        )

    return handler


def _make_dummy_factory() -> BatchHandlerFactory:
    def factory(runtime: CADRuntimeABC) -> BatchOperationHandler:
        return _make_dummy_handler()

    return factory


class TestOperationBinding:
    def test_valid_binding(self) -> None:
        factory = _make_dummy_factory()
        binding = OperationBinding(operation_id="export_3d", factory=factory)

        assert binding.operation_id == "export_3d"
        assert binding.factory is factory

    def test_create_handler_returns_callable(self) -> None:
        factory = _make_dummy_factory()
        binding = OperationBinding(operation_id="export_3d", factory=factory)
        runtime: Any = DummyRuntime()

        handler = binding.create_handler(runtime)
        assert callable(handler)

    def test_create_handler_rejects_non_callable_handler(self) -> None:
        def bad_factory(runtime: CADRuntimeABC) -> Any:
            return "not-a-callable-handler"

        binding = OperationBinding(operation_id="export_3d", factory=bad_factory)
        runtime: Any = DummyRuntime()

        with pytest.raises(BatchConfigurationError, match="returned non-callable str"):
            binding.create_handler(runtime)

    def test_rejects_invalid_operation_id(self) -> None:
        factory = _make_dummy_factory()
        with pytest.raises(ValueError, match="must be a non-empty string"):
            OperationBinding(operation_id="", factory=factory)

        with pytest.raises(ValueError, match="must not have leading or trailing whitespace"):
            OperationBinding(operation_id=" export_3d ", factory=factory)

    def test_rejects_non_callable_factory(self) -> None:
        with pytest.raises(TypeError, match="factory must be callable"):
            OperationBinding(operation_id="export_3d", factory="not-callable")  # type: ignore[arg-type]


class TestOperationBindings:
    def test_empty_bindings_without_registry(self) -> None:
        bindings = OperationBindings()
        assert len(bindings) == 0
        assert bindings.operation_ids == ()
        assert bindings.get("export_3d") is None

    def test_lookup_methods(self) -> None:
        b1 = OperationBinding(operation_id="export_3d", factory=_make_dummy_factory())
        b2 = OperationBinding(operation_id="publish_drawing", factory=_make_dummy_factory())
        bindings = OperationBindings((b1, b2))

        assert len(bindings) == 2
        assert bindings.operation_ids == ("export_3d", "publish_drawing")
        assert "export_3d" in bindings
        assert "publish_drawing" in bindings
        assert "unknown" not in bindings
        assert bindings["export_3d"] == b1
        assert bindings.get_binding("publish_drawing") == b2
        assert list(bindings) == [b1, b2]

    def test_get_binding_raises_on_unknown(self) -> None:
        bindings = OperationBindings()
        with pytest.raises(BatchConfigurationError, match="Missing binding for operation 'unknown'"):
            bindings.get_binding("unknown")

    def test_rejects_duplicate_or_case_colliding_ids(self) -> None:
        b1 = OperationBinding(operation_id="export_3d", factory=_make_dummy_factory())
        b2 = OperationBinding(operation_id="EXPORT_3D", factory=_make_dummy_factory())

        with pytest.raises(BatchConfigurationError, match="Duplicate or case-colliding operation ID"):
            OperationBindings((b1, b2))

    def test_rejects_non_binding_items(self) -> None:
        with pytest.raises(TypeError, match="Expected OperationBinding"):
            OperationBindings(["not-a-binding"])  # type: ignore[list-item]


class TestRegistryParityValidation:
    def test_parity_matches_initial_registry(self) -> None:
        registry = build_initial_registry()
        b1 = OperationBinding(operation_id="export_3d", factory=_make_dummy_factory())
        b2 = OperationBinding(operation_id="publish_drawing", factory=_make_dummy_factory())

        # Should validate cleanly on construction
        bindings = OperationBindings((b1, b2), registry=registry)
        assert len(bindings) == 2

    def test_parity_detects_missing_binding(self) -> None:
        registry = build_initial_registry()  # has export_3d and publish_drawing
        b1 = OperationBinding(operation_id="export_3d", factory=_make_dummy_factory())

        with pytest.raises(
            BatchConfigurationError,
            match=r"Missing operation binding\(s\) for registered operations: \['publish_drawing'\]",
        ):
            OperationBindings((b1,), registry=registry)

    def test_parity_detects_unknown_binding(self) -> None:
        registry = build_initial_registry()
        b1 = OperationBinding(operation_id="export_3d", factory=_make_dummy_factory())
        b2 = OperationBinding(operation_id="publish_drawing", factory=_make_dummy_factory())
        b3 = OperationBinding(operation_id="extra_op", factory=_make_dummy_factory())

        with pytest.raises(
            BatchConfigurationError, match=r"Unknown operation binding\(s\) absent from registry: \['extra_op'\]"
        ):
            OperationBindings((b1, b2, b3), registry=registry)

    def test_extensibility_proof_without_changing_production_registry(self) -> None:
        initial = build_initial_registry()
        # Create an extended test registry
        test_desc = OperationDescriptor(
            operation_id="test_custom_export",
            input_extensions=(".par",),
            output_formats=("step",),
            progress_label="Test Custom Export",
        )
        extended_registry = OperationRegistry((*initial.descriptors, test_desc))

        b1 = OperationBinding(operation_id="export_3d", factory=_make_dummy_factory())
        b2 = OperationBinding(operation_id="publish_drawing", factory=_make_dummy_factory())
        b_custom = OperationBinding(operation_id="test_custom_export", factory=_make_dummy_factory())

        extended_bindings = OperationBindings((b1, b2, b_custom), registry=extended_registry)
        assert len(extended_bindings) == 3
        assert "test_custom_export" in extended_bindings

        # Verify production registry is completely untouched
        assert "test_custom_export" not in initial
        assert len(initial) == 2
