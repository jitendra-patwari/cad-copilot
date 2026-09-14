"""Production composition and runner for CAD Copilot batch execution.

Wires engine version resolution, safety boundary, operation registry,
handler bindings, BatchService orchestration, outcome finalization, and response projection.
"""

from __future__ import annotations

import importlib.metadata
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from batch.composition import build_initial_operation_bindings
from batch.execution import BatchExecutionOutcome, BatchProgressUpdate
from batch.finalization import finalize_batch_outcome
from batch.models import BatchDiagnostic, BatchRequest
from batch.projection import project_batch_response
from batch.registry import OperationRegistry, build_initial_registry
from batch.safety import FilesystemBatchSafetyBoundary
from batch.service import BatchService

if TYPE_CHECKING:
    from drivers.base import CADRuntimeABC

    from batch.bindings import OperationBindings


def run_batch(
    request: BatchRequest,
    *,
    cancellation_check: Callable[[], bool] | None = None,
    observer: Callable[[BatchProgressUpdate], None] | None = None,
    _runtime_factory: Callable[[], CADRuntimeABC] | None = None,
    _engine_version: str | None = None,
    _safety_boundary: Any = None,
    _registry: OperationRegistry | None = None,
    _bindings: OperationBindings | None = None,
) -> Mapping[str, Any]:
    """Execute a validated BatchRequest through production composition.

    Args:
        request: Validated typed BatchRequest.
        cancellation_check: Optional cooperative cancellation probe.
        observer: Optional stderr progress update callback.
        _runtime_factory: Optional runtime factory callable (default: SolidEdgeRuntime).
        _engine_version: Optional engine version override for testing.
        _safety_boundary: Optional safety boundary override for testing.
        _registry: Optional operation registry override for testing.
        _bindings: Optional handler bindings override for testing.

    Returns:
        Schema-valid BatchResponse dictionary mapping.
    """
    if not isinstance(request, BatchRequest):
        raise TypeError(f"Expected BatchRequest, got {type(request).__name__}")

    # 1. Resolve engine distribution version before CAD acquisition
    engine_ver = _engine_version
    if engine_ver is None:
        try:
            resolved = importlib.metadata.version("cad-copilot")
            if resolved and resolved.strip() == resolved:
                engine_ver = resolved
        except Exception:
            engine_ver = None

    if not engine_ver:
        # Return sanitized early failed response if version metadata cannot be resolved
        outcome = BatchExecutionOutcome(
            status="failed",
            request_id=request.request_id,
            contract_version=request.contract_version,
            errors=(
                BatchDiagnostic(
                    code="INTERNAL_ERROR",
                    message="Engine distribution version metadata could not be resolved.",
                ),
            ),
        )
        early_resp = finalize_batch_outcome(request, outcome, engine_version="unknown")
        return project_batch_response(early_resp)

    # 2. Build safety boundary, registry, bindings, and runtime factory
    safety_boundary = _safety_boundary if _safety_boundary is not None else FilesystemBatchSafetyBoundary()
    registry = _registry if _registry is not None else build_initial_registry()
    bindings = (
        _bindings if _bindings is not None else build_initial_operation_bindings(safety_boundary, registry=registry)
    )

    if _runtime_factory is not None:
        runtime_factory = _runtime_factory
    else:
        from drivers.solidedge import SolidEdgeRuntime

        runtime_factory = SolidEdgeRuntime

    # 3. Create BatchService
    service = BatchService(
        registry=registry,
        bindings=bindings,
        safety_boundary=safety_boundary,
        runtime_factory=runtime_factory,
        cancellation_check=cancellation_check,
        observer=observer,
    )

    # 4. Execute request
    outcome = service.execute(request)

    # 5. Finalize outcome into published manifest and canonical response
    response = finalize_batch_outcome(request, outcome, engine_version=engine_ver)

    # 6. Project response to schema-valid mapping
    return project_batch_response(response)


__all__ = ["run_batch"]
