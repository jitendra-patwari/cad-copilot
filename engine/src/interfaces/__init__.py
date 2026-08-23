"""Domain interfaces, models, and exception taxonomy for CAD Copilot."""

from __future__ import annotations

from .exceptions import (
    CADContainmentError,
    CADDocumentError,
    CADError,
    CADExecutionError,
    CADExportError,
    CADRuntimeError,
)
from .executor_abc import CADExecutorABC
from .models import (
    ArtifactRecord,
    ExecutionFailure,
    ExecutionResult,
    ExecutionSuccess,
    PhysicalProperties,
    StandardInspectionReport,
)
from .runtime_abc import CADRuntimeABC

__all__ = [
    "ArtifactRecord",
    "CADContainmentError",
    "CADDocumentError",
    "CADError",
    "CADExecutionError",
    "CADExecutorABC",
    "CADExportError",
    "CADRuntimeABC",
    "CADRuntimeError",
    "ExecutionFailure",
    "ExecutionResult",
    "ExecutionSuccess",
    "PhysicalProperties",
    "StandardInspectionReport",
]
