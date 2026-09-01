"""Siemens Solid Edge COM runtime driver and lifecycle package."""

from __future__ import annotations

from .builders import (
    create_cutout_finite,
    create_cutout_through_all,
    create_pad_protrusion,
    create_primitive_cuboid,
    create_primitive_cylinder,
    create_primitive_extruded_profile,
    create_primitive_spur_gear,
    create_profile_on_plane,
    draw_circle_profile,
    draw_polygon_profile,
    draw_slot_profile,
    get_ref_plane_offset_side,
    resolve_or_create_reference_plane,
)
from .directions import (
    PROMOTED_CUT_CAPABILITY_ROWS,
    PROMOTED_PAD_CAPABILITY_ROWS,
    DirectionPair,
    resolve_cut_direction,
    resolve_pad_direction,
)
from .errors import describe_exception, normalize_com_error, suppress_com_error
from .executor import SolidEdgeExecutor
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
    "PROMOTED_CUT_CAPABILITY_ROWS",
    "PROMOTED_PAD_CAPABILITY_ROWS",
    "AttachmentMode",
    "DirectionPair",
    "OwnershipMode",
    "ProcessIdentity",
    "SEMessageFilter",
    "STAThreadWorker",
    "SolidEdgeApplicationHandle",
    "SolidEdgeDocumentHandle",
    "SolidEdgeExecutor",
    "SolidEdgePartDocumentHandle",
    "SolidEdgeRuntime",
    "create_cutout_finite",
    "create_cutout_through_all",
    "create_pad_protrusion",
    "create_primitive_cuboid",
    "create_primitive_cylinder",
    "create_primitive_extruded_profile",
    "create_primitive_spur_gear",
    "create_profile_on_plane",
    "describe_exception",
    "draw_circle_profile",
    "draw_polygon_profile",
    "draw_slot_profile",
    "get_process_identity",
    "get_ref_plane_offset_side",
    "is_process_alive",
    "kill_orphan_processes",
    "normalize_com_error",
    "register_message_filter",
    "resolve_cut_direction",
    "resolve_or_create_reference_plane",
    "resolve_pad_direction",
    "revoke_message_filter",
    "suppress_com_error",
    "try_get_process_id",
]
