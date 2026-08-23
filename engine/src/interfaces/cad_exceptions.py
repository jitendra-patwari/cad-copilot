"""Backwards-compatibility bridge module for legacy cad_exceptions imports."""

from __future__ import annotations

from .exceptions import (
    CADContainmentError,
    CADDocumentError,
    CADError,
    CADExecutionError,
    CADExportError,
    CADRuntimeError,
)

__all__ = [
    "CADContainmentError",
    "CADDocumentError",
    "CADError",
    "CADExecutionError",
    "CADExportError",
    "CADRuntimeError",
]
