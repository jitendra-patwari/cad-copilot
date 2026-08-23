"""Backwards-compatibility bridge module for legacy cad_interfaces imports."""

from __future__ import annotations

from .executor_abc import CADExecutorABC
from .models import StandardInspectionReport
from .runtime_abc import CADRuntimeABC

__all__ = [
    "CADExecutorABC",
    "CADRuntimeABC",
    "StandardInspectionReport",
]
