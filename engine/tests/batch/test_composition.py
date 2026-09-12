"""Unit tests for production batch operation binding composition.

Verifies:
- Exact parity between initial bindings and initial registry.
- Runtime narrowing: requires callable run_document_task; non-conforming runtimes fail early.
- Zero COM acquisition or filesystem work during composition.
- Operation binding factories return request-scoped callable handlers.
- Both handlers capture the identical workspace instance.
- Additional test-only operation works without modifying central loop.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from batch.bindings import OperationBinding, OperationBindings
from batch.composition import build_initial_operation_bindings
from batch.handlers import Export3DHandler, PublishDrawingHandler
from batch.models import BatchConfigurationError
from batch.output_workspace import BatchOutputWorkspace
from batch.registry import (
    OperationDescriptor,
    OperationRegistry,
    build_initial_registry,
)
from interfaces.models import RuntimeDiagnostics
from interfaces.runtime_abc import CADRuntimeABC


class MinimalFakeWorkspace(BatchOutputWorkspace):
    """Minimal conforming BatchOutputWorkspace spy."""

    def begin_format(self, context: Any) -> None:
        pass

    def finalize_format(self, context: Any, snapshot: Any) -> Path:
        return Path("/final/out.step")

    def cleanup_format(self, context: Any) -> None:
        pass


class ConformingRuntime(CADRuntimeABC):
    """Runtime double providing the TrackedDocumentRuntime seam."""

    def __init__(self) -> None:
        self.task_calls: list[tuple[Any, Any]] = []

    def connect_application(self) -> Any:
        return "app"

    def get_diagnostics(self) -> RuntimeDiagnostics:
        return RuntimeDiagnostics(
            ownership="borrowed",
            attachment_mode="active_session",
            is_healthy=True,
            version_build="226.00.00.106",
            warnings=[],
        )

    def create_part_document(self, application: Any) -> Any:
        raise NotImplementedError

    def open_document(self, application: Any, path: Path) -> Any:
        return "doc"

    def close_document(self, doc_handle: Any) -> None:
        pass

    def teardown(self, force_kill_on_failure: bool = False) -> bool:
        return True

    def is_healthy(self) -> bool:
        return True

    def run_document_task(
        self,
        doc_handle: Any,
        task: Any,
        timeout: float = 120.0,
    ) -> Any:
        self.task_calls.append((doc_handle, task))
        # Execute task with dummy raw_doc and worker
        return task(object(), object())


class NonConformingRuntime(CADRuntimeABC):
    """Runtime that does NOT implement run_document_task."""

    def connect_application(self) -> Any:
        return "app"

    def get_diagnostics(self) -> RuntimeDiagnostics:
        return RuntimeDiagnostics(
            ownership="borrowed",
            attachment_mode="active_session",
            is_healthy=True,
            version_build="226.00.00.106",
            warnings=[],
        )

    def create_part_document(self, application: Any) -> Any:
        raise NotImplementedError

    def open_document(self, application: Any, path: Path) -> Any:
        return "doc"

    def close_document(self, doc_handle: Any) -> None:
        pass

    def teardown(self, force_kill_on_failure: bool = False) -> bool:
        return True

    def is_healthy(self) -> bool:
        return True


class TestComposition:
    """Test suite for build_initial_operation_bindings."""

    def test_build_initial_operation_bindings_parity(self) -> None:
        workspace = MinimalFakeWorkspace()
        bindings = build_initial_operation_bindings(workspace)

        registry = build_initial_registry()

        # Exact order and parity with initial registry
        assert bindings.operation_ids == registry.operation_ids
        assert bindings.operation_ids == ("export_3d", "publish_drawing")
        bindings.validate_parity(registry)

    def test_workspace_validation(self) -> None:
        class IncompleteWorkspace:
            def begin_format(self, context: Any) -> None:
                pass

        with pytest.raises(TypeError) as exc_info:
            build_initial_operation_bindings(IncompleteWorkspace())  # type: ignore[arg-type]
        assert "must implement callable" in str(exc_info.value)

    def test_handler_factories_create_request_scoped_handlers(self) -> None:
        workspace = MinimalFakeWorkspace()
        bindings = build_initial_operation_bindings(workspace)

        runtime = ConformingRuntime()

        export_binding = bindings.get_binding("export_3d")
        export_handler = export_binding.create_handler(runtime)
        assert isinstance(export_handler, Export3DHandler)
        assert export_handler._workspace is workspace

        drawing_binding = bindings.get_binding("publish_drawing")
        drawing_handler = drawing_binding.create_handler(runtime)
        assert isinstance(drawing_handler, PublishDrawingHandler)
        assert drawing_handler._workspace is workspace

        # Calling factory again returns a distinct request-scoped instance
        export_handler_2 = export_binding.create_handler(runtime)
        assert export_handler is not export_handler_2

    def test_non_conforming_runtime_fails_before_execution(self) -> None:
        workspace = MinimalFakeWorkspace()
        bindings = build_initial_operation_bindings(workspace)

        runtime = NonConformingRuntime()

        export_binding = bindings.get_binding("export_3d")
        with pytest.raises(BatchConfigurationError) as exc_info:
            export_binding.create_handler(runtime)
        assert "does not provide callable 'run_document_task'" in str(exc_info.value)

        drawing_binding = bindings.get_binding("publish_drawing")
        with pytest.raises(BatchConfigurationError) as exc_info:
            drawing_binding.create_handler(runtime)
        assert "does not provide callable 'run_document_task'" in str(exc_info.value)

    def test_extensibility_with_custom_operation(self) -> None:
        workspace = MinimalFakeWorkspace()
        registry = build_initial_registry()

        # Add custom test operation to registry
        custom_desc = OperationDescriptor(
            operation_id="custom_inspect",
            input_extensions=(".par",),
            output_formats=("step",),
            progress_label="Inspecting custom geometry",
            safety_class="read_only_source",
            document_lifecycle="open_existing_close_without_save",
            collision_policy="fail_if_exists",
        )
        extended_reg = OperationRegistry(
            descriptors=(
                registry.get("export_3d"),
                registry.get("publish_drawing"),
                custom_desc,
            )
        )

        custom_handler_mock = MagicMock()
        custom_binding = OperationBinding(
            operation_id="custom_inspect",
            factory=lambda r: custom_handler_mock,
        )

        initial_bindings = build_initial_operation_bindings(workspace, registry=registry)
        extended_bindings = OperationBindings(
            bindings=(*initial_bindings.bindings, custom_binding),
            registry=extended_reg,
        )

        assert extended_bindings.operation_ids == ("export_3d", "publish_drawing", "custom_inspect")
        extended_bindings.validate_parity(extended_reg)

    def test_production_runtime_conforms_to_tracked_document_protocol(self) -> None:
        """Static and structural proof that SolidEdgeRuntime conforms to _TrackedDocumentRuntime."""
        from batch.composition import _TrackedDocumentRuntime
        from drivers.solidedge import SolidEdgeRuntime

        def _assert_static_conformance(rt: SolidEdgeRuntime) -> _TrackedDocumentRuntime:
            return rt

        runtime = SolidEdgeRuntime()
        assert isinstance(runtime, _TrackedDocumentRuntime)
        assert callable(getattr(runtime, "run_document_task", None))

    def test_runtime_with_non_callable_run_document_task_rejected(self) -> None:
        """Verify that non-callable run_document_task attribute is rejected despite @runtime_checkable."""
        workspace = MinimalFakeWorkspace()
        bindings = build_initial_operation_bindings(workspace)

        class BrokenRuntime(NonConformingRuntime):
            run_document_task = "not a callable"

        export_binding = bindings.get_binding("export_3d")
        with pytest.raises(BatchConfigurationError, match="does not provide callable 'run_document_task'"):
            export_binding.create_handler(BrokenRuntime())
