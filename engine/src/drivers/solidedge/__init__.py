"""Siemens Solid Edge COM runtime driver and lifecycle package."""

from __future__ import annotations

from .errors import describe_exception, normalize_com_error, suppress_com_error
from .message_filter import (
    SEMessageFilter,
    register_message_filter,
    revoke_message_filter,
)
from .process import (
    get_process_identity,
    is_process_alive,
    kill_orphan_processes,
    try_get_process_id,
)
from .runtime import (
    SolidEdgeRuntime,
    STAThreadWorker,
)
from .types import (
    AttachmentMode,
    OwnershipMode,
    ProcessIdentity,
    SolidEdgeApplicationHandle,
    SolidEdgeDocumentHandle,
    SolidEdgePartDocumentHandle,
)

__all__ = [
    "AttachmentMode",
    "OwnershipMode",
    "ProcessIdentity",
    "SEMessageFilter",
    "STAThreadWorker",
    "SolidEdgeApplicationHandle",
    "SolidEdgeDocumentHandle",
    "SolidEdgePartDocumentHandle",
    "SolidEdgeRuntime",
    "describe_exception",
    "get_process_identity",
    "is_process_alive",
    "kill_orphan_processes",
    "normalize_com_error",
    "register_message_filter",
    "revoke_message_filter",
    "suppress_com_error",
    "try_get_process_id",
]
