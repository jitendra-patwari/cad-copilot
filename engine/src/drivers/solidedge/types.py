"""Domain types, enums, and opaque handle wrappers for Solid Edge COM automation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class OwnershipMode(Enum):
    """Ownership relationship between CAD Copilot and a Solid Edge process."""

    OWNED = "owned"
    BORROWED = "borrowed"
    UNKNOWN = "unknown"


class AttachmentMode(Enum):
    """How the CAD Copilot runtime established connection to Solid Edge."""

    ATTACHED_EXISTING = "attached_existing"
    SPAWNED_NEW = "spawned_new"
    UNSPECIFIED = "unspecified"


@dataclass(frozen=True)
class ProcessIdentity:
    """Immutable identity proof for a Windows process (PID + 64-bit creation timestamp)."""

    pid: int
    creation_time_ft: int  # 64-bit Windows FILETIME integer


@dataclass(frozen=True)
class SolidEdgeApplicationHandle:
    """Opaque reference to an attached or spawned Solid Edge application instance."""

    handle_id: str
    ownership: OwnershipMode = OwnershipMode.UNKNOWN
    attachment_mode: AttachmentMode = AttachmentMode.UNSPECIFIED
    process_identity: ProcessIdentity | None = None
    version_build: str | None = None


@dataclass(frozen=True)
class SolidEdgeDocumentHandle:
    """Opaque token representing an open generic Solid Edge document (Assembly, Draft, etc.)."""

    handle_id: str
    doc_type: str = "unknown"
    path: Path | None = None


@dataclass(frozen=True)
class SolidEdgePartDocumentHandle:
    """Opaque token representing an open Solid Edge Part document (.par)."""

    handle_id: str
    path: Path | None = None


__all__ = [
    "AttachmentMode",
    "OwnershipMode",
    "ProcessIdentity",
    "SolidEdgeApplicationHandle",
    "SolidEdgeDocumentHandle",
    "SolidEdgePartDocumentHandle",
]
