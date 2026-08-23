"""Abstract base class defining the CAD application runtime lifecycle contract."""

from __future__ import annotations

import abc
from pathlib import Path
from typing import Any


class CADRuntimeABC(abc.ABC):
    """Abstract interface for managing the CAD application process lifecycle."""

    @abc.abstractmethod
    def connect_application(self) -> Any:
        """Acquire or attach to a local CAD application session."""

    @abc.abstractmethod
    def open_document(self, application: Any, path: Path) -> Any:
        """Open an existing CAD document by path."""

    @abc.abstractmethod
    def close_document(self, doc_handle: Any) -> None:
        """Release or close an open CAD document handle."""

    @abc.abstractmethod
    def teardown(self, force_kill_on_failure: bool) -> None:
        """Cleanly terminate or force-kill orphan CAD processes."""

    def is_healthy(self) -> bool:
        """Check if the CAD runtime is alive and responsive."""
        return True


__all__ = ["CADRuntimeABC"]
