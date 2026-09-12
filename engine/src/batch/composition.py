"""Production operation binding composition for CAD Copilot batch operations.

Wires the sequential batch execution hub to the Solid Edge COM driver via
the narrow TrackedDocumentRuntime protocol and shared BatchOutputWorkspace boundary.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, TypeAlias, TypeVar, cast, runtime_checkable

from batch.bindings import BatchOperationHandler, OperationBinding, OperationBindings
from batch.handlers import Export3DHandler, PublishDrawingHandler
from batch.models import BatchConfigurationError
from batch.output_workspace import BatchOutputWorkspace
from batch.registry import OperationRegistry, build_initial_registry
from drivers.solidedge import (
    SolidEdgeDocumentHandle,
    SolidEdgePartDocumentHandle,
    STAThreadWorker,
)
from drivers.solidedge.assembly_references import (
    AssemblyReferenceCheckResult,
    check_assembly_references,
)
from drivers.solidedge.drawing_publisher import publish_drawing_to_path, refresh_drawing_views
from drivers.solidedge.exporter import export_model_to_path
from interfaces.runtime_abc import CADRuntimeABC

_T = TypeVar("_T")
_SolidEdgeHandle: TypeAlias = SolidEdgeDocumentHandle | SolidEdgePartDocumentHandle


@runtime_checkable
class _TrackedDocumentRuntime(Protocol):
    """Narrow structural protocol for CAD runtimes exposing tracked-document tasks."""

    def run_document_task(
        self,
        doc_handle: _SolidEdgeHandle,
        task: Callable[[Any, STAThreadWorker], _T],
        timeout: float = ...,
    ) -> _T: ...


def _narrow_tracked_runtime(runtime: CADRuntimeABC) -> _TrackedDocumentRuntime:
    """Verify that the supplied CAD runtime satisfies the _TrackedDocumentRuntime seam."""
    run_task = getattr(runtime, "run_document_task", None)
    if not callable(run_task) or not isinstance(runtime, _TrackedDocumentRuntime):
        runtime_type = type(runtime).__name__
        raise BatchConfigurationError(
            f"Runtime '{runtime_type}' does not provide callable 'run_document_task' required for batch operations"
        )
    return runtime


def build_initial_operation_bindings(
    workspace: BatchOutputWorkspace,
    *,
    registry: OperationRegistry | None = None,
) -> OperationBindings:
    """Build immutable operation bindings with exact parity to the initial registry.

    Captures the shared BatchOutputWorkspace and wires request-scoped handler factories
    to worker-local Solid Edge driver operations without opening any documents or processes.
    """
    for method in ("begin_format", "finalize_format", "cleanup_format"):
        if not hasattr(workspace, method) or not callable(getattr(workspace, method)):
            raise TypeError(f"workspace must implement callable '{method}' of BatchOutputWorkspace protocol")

    def _export_3d_factory(runtime: CADRuntimeABC) -> BatchOperationHandler:
        tracked_runtime = _narrow_tracked_runtime(runtime)

        def _model_exporter(doc_handle: object, format_id: str, work_path: Path) -> None:
            tracked_runtime.run_document_task(
                cast(_SolidEdgeHandle, doc_handle),
                lambda raw_doc, worker: export_model_to_path(raw_doc, worker, format_id, work_path),
            )

        def _assembly_checker(doc_handle: object) -> AssemblyReferenceCheckResult:
            return tracked_runtime.run_document_task(
                cast(_SolidEdgeHandle, doc_handle),
                lambda raw_doc, worker: check_assembly_references(raw_doc, worker),
            )

        return Export3DHandler(
            workspace=workspace,
            model_exporter=_model_exporter,
            assembly_checker=_assembly_checker,
        )

    def _publish_drawing_factory(runtime: CADRuntimeABC) -> BatchOperationHandler:
        tracked_runtime = _narrow_tracked_runtime(runtime)

        def _drawing_publisher(doc_handle: object, format_id: str, work_path: Path) -> None:
            tracked_runtime.run_document_task(
                cast(_SolidEdgeHandle, doc_handle),
                lambda raw_doc, worker: publish_drawing_to_path(raw_doc, worker, format_id, work_path),
            )

        def _drawing_preparer(doc_handle: object) -> Any:
            return tracked_runtime.run_document_task(
                cast(_SolidEdgeHandle, doc_handle),
                lambda raw_doc, worker: refresh_drawing_views(raw_doc, worker),
            )

        return PublishDrawingHandler(
            workspace=workspace,
            drawing_publisher=_drawing_publisher,
            drawing_preparer=_drawing_preparer,
        )

    export_3d_binding = OperationBinding(
        operation_id="export_3d",
        factory=_export_3d_factory,
    )
    publish_drawing_binding = OperationBinding(
        operation_id="publish_drawing",
        factory=_publish_drawing_factory,
    )

    reg = registry if registry is not None else build_initial_registry()

    return OperationBindings(
        (export_3d_binding, publish_drawing_binding),
        registry=reg,
    )


__all__ = [
    "build_initial_operation_bindings",
]
