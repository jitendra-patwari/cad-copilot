"""Normalized exception taxonomy for the CAD domain.

Isolates caller domains (CLI, IPC bridge, AI pipeline) from vendor-specific
Windows COM HRESULTs and low-level kernel errors.
"""

from __future__ import annotations

from typing import Any


class CADError(Exception):
    """Base domain exception for all CAD operations."""

    error_code: str = "CAD_ERROR"
    details: dict[str, Any]

    def __init__(
        self,
        message: str,
        *,
        error_code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        if error_code is not None:
            self.error_code = error_code
        self.details = details or {}


class CADRuntimeError(CADError):
    """Raised when CAD process attachment, session acquisition, or lifecycle fails."""

    error_code: str = "RUNTIME_ATTACH_FAILED"


class CADRuntimeUnavailableError(CADRuntimeError):
    """Raised when a Solid Edge application session cannot be acquired or launched."""

    error_code: str = "RUNTIME_UNAVAILABLE"


class CADRuntimeBusyError(CADRuntimeError):
    """Raised when COM calls repeatedly fail due to an unresolvable busy or rejected server state."""

    error_code: str = "RUNTIME_BUSY_TIMEOUT"


class CADExecutionError(CADError):
    """Raised when geometric modeling, sketch creation, or feature execution fails."""

    error_code: str = "EXECUTION_FAILED"


class CADDocumentError(CADError):
    """Raised when creating, opening, saving, or closing a CAD document fails."""

    error_code: str = "DOCUMENT_IO_FAILED"


class CADExportError(CADError):
    """Raised when exporting CAD artifacts (STEP, STL, JPG, PAR) fails."""

    error_code: str = "EXPORT_FAILED"


class CADContainmentError(CADError):
    """Raised when a geometric feature violates spatial boundaries or containment."""

    error_code: str = "CONTAINMENT_VIOLATION"


class TeardownIncompleteError(Exception):
    """Raised internally when CAD runtime teardown is incomplete or fails."""


__all__ = [
    "CADContainmentError",
    "CADDocumentError",
    "CADError",
    "CADExecutionError",
    "CADExportError",
    "CADRuntimeBusyError",
    "CADRuntimeError",
    "CADRuntimeUnavailableError",
    "TeardownIncompleteError",
]
