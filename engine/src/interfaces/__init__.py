"""Domain interfaces, models, and exception taxonomy for CAD Copilot."""

from __future__ import annotations

from .exceptions import (
    CADContainmentError,
    CADDocumentError,
    CADError,
    CADExecutionError,
    CADExportError,
    CADRuntimeBusyError,
    CADRuntimeError,
    CADRuntimeUnavailableError,
)
from .executor_abc import CADExecutorABC
from .models import (
    ArtifactFormat,
    ArtifactRecord,
    BodyRef,
    ExecutionFailure,
    ExecutionResult,
    ExecutionSuccess,
    FeatureRef,
    OperationResult,
    PhysicalProperties,
    RuntimeDiagnostics,
    StandardInspectionReport,
)
from .runtime_abc import CADRuntimeABC

__all__ = [
    "ArtifactFormat",
    "ArtifactRecord",
    "BodyRef",
    "CADContainmentError",
    "CADDocumentError",
    "CADError",
    "CADExecutionError",
    "CADExecutorABC",
    "CADExportError",
    "CADRuntimeABC",
    "CADRuntimeBusyError",
    "CADRuntimeError",
    "CADRuntimeUnavailableError",
    "ExecutionFailure",
    "ExecutionResult",
    "ExecutionSuccess",
    "FeatureRef",
    "OperationResult",
    "PhysicalProperties",
    "RuntimeDiagnostics",
    "StandardInspectionReport",
]
