"""Abstract base class defining the CAD application runtime lifecycle contract."""

from __future__ import annotations

import abc
from pathlib import Path
from typing import Any

from .models import RuntimeDiagnostics


class CADRuntimeABC(abc.ABC):
    """Abstract interface for managing the CAD application process lifecycle."""

    @abc.abstractmethod
    def connect_application(self) -> Any:
        """Acquire or attach to a local CAD application session."""

    @abc.abstractmethod
    def get_diagnostics(self) -> RuntimeDiagnostics:
        """Retrieve local runtime diagnostic metadata and health status."""

    @abc.abstractmethod
    def create_part_document(self, application: Any) -> Any:
        """Create a fresh request-owned part document."""

    @abc.abstractmethod
    def open_document(self, application: Any, path: Path) -> Any:
        """Open an existing CAD document by path with explicit ownership."""

    @abc.abstractmethod
    def close_document(self, doc_handle: Any) -> None:
        """Release or close an open CAD document handle unconditionally without saving."""

    @abc.abstractmethod
    def teardown(self, force_kill_on_failure: bool = False) -> None:
        """Cleanly close request documents, restore application state, and terminate owned CAD processes."""

    @abc.abstractmethod
    def is_healthy(self) -> bool:
        """Check if the CAD runtime is alive and responsive."""


__all__ = ["CADRuntimeABC"]
