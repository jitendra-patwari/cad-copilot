"""Frozen direction mappings and exact capability rows for Solid Edge cuts and protrusions."""

from __future__ import annotations

from dataclasses import dataclass

from interfaces.exceptions import CADExecutionError

from .constants import (
    PROFILE_SIDE_LEFT,
    PROFILE_SIDE_RIGHT,
)


@dataclass(frozen=True)
class DirectionPair:
    """Frozen pair of Solid Edge ProfileSide and ProfilePlaneSide enum constants."""

    profile_side: int
    profile_plane_side: int


# Verified, single deterministic direction pair mappings per promoted capability row.
# ProfileSide:
# - igLeft = 1: removes material inside the closed profile (hole / pocket / cut).
# - igRight = 2: removes material outside the closed profile (island).
#
# ProfilePlaneSide:
# - igLeft = 1 or igRight = 2 selecting cut/protrusion vector along or opposite the reference plane normal.
#
# Solid Edge Plane Normals:
# - XY (Top): +Z normal
# - YZ (Right): +X normal
# - XZ (Front): -Y normal

PROMOTED_CUT_CAPABILITY_ROWS: dict[tuple[str, str, str], DirectionPair] = {
    # 1. Primary +Z capability matrix (through_all and finite for circle, polygon, slot)
    ("circle", "through_all", "+Z"): DirectionPair(
        profile_side=PROFILE_SIDE_LEFT, profile_plane_side=PROFILE_SIDE_LEFT
    ),
    ("circle", "finite", "+Z"): DirectionPair(profile_side=PROFILE_SIDE_LEFT, profile_plane_side=PROFILE_SIDE_LEFT),
    ("polygon", "through_all", "+Z"): DirectionPair(
        profile_side=PROFILE_SIDE_LEFT, profile_plane_side=PROFILE_SIDE_LEFT
    ),
    ("polygon", "finite", "+Z"): DirectionPair(profile_side=PROFILE_SIDE_LEFT, profile_plane_side=PROFILE_SIDE_LEFT),
    ("slot", "through_all", "+Z"): DirectionPair(profile_side=PROFILE_SIDE_LEFT, profile_plane_side=PROFILE_SIDE_LEFT),
    ("slot", "finite", "+Z"): DirectionPair(profile_side=PROFILE_SIDE_LEFT, profile_plane_side=PROFILE_SIDE_LEFT),
    # 2. Circular through-all characterization rows on rectangular prism side/bottom faces
    ("circle", "through_all", "-Z"): DirectionPair(
        profile_side=PROFILE_SIDE_LEFT, profile_plane_side=PROFILE_SIDE_RIGHT
    ),
    ("circle", "through_all", "+X"): DirectionPair(
        profile_side=PROFILE_SIDE_LEFT, profile_plane_side=PROFILE_SIDE_LEFT
    ),
    ("circle", "through_all", "-X"): DirectionPair(
        profile_side=PROFILE_SIDE_LEFT, profile_plane_side=PROFILE_SIDE_RIGHT
    ),
    ("circle", "through_all", "+Y"): DirectionPair(
        profile_side=PROFILE_SIDE_LEFT, profile_plane_side=PROFILE_SIDE_RIGHT
    ),
    ("circle", "through_all", "-Y"): DirectionPair(
        profile_side=PROFILE_SIDE_LEFT, profile_plane_side=PROFILE_SIDE_LEFT
    ),
}

PROMOTED_PAD_CAPABILITY_ROWS: dict[tuple[str, str, str], DirectionPair] = {
    # Only +Z finite polygon (rectangular) pad is promoted in M3.2 baseline.
    # Side-pad faces remain unadvertised until individually proven in live tests.
    ("polygon", "finite", "+Z"): DirectionPair(profile_side=PROFILE_SIDE_LEFT, profile_plane_side=PROFILE_SIDE_RIGHT),
}


def resolve_cut_direction(profile_family: str, extent_type: str, face: str) -> DirectionPair:
    """Resolve the single frozen DirectionPair for a cut on the specified capability row.

    Raises CADExecutionError if the exact (profile_family, extent_type, face) row is not
    in the verified capability table. Does not guess or perform runtime trial-and-error.
    """
    key = (profile_family.strip().lower(), extent_type.strip().lower(), face.strip().upper())
    if key not in PROMOTED_CUT_CAPABILITY_ROWS:
        raise CADExecutionError(
            f"Unsupported cut capability row: profile_family='{profile_family}', "
            f"extent_type='{extent_type}', face='{face}'"
        )
    return PROMOTED_CUT_CAPABILITY_ROWS[key]


def resolve_pad_direction(profile_family: str, extent_type: str, face: str) -> DirectionPair:
    """Resolve the single frozen DirectionPair for a pad protrusion on the specified capability row.

    Raises CADExecutionError if the exact (profile_family, extent_type, face) row is not
    in the verified capability table. Does not guess or perform runtime trial-and-error.
    """
    key = (profile_family.strip().lower(), extent_type.strip().lower(), face.strip().upper())
    if key not in PROMOTED_PAD_CAPABILITY_ROWS:
        raise CADExecutionError(
            f"Unsupported pad capability row: profile_family='{profile_family}', "
            f"extent_type='{extent_type}', face='{face}'"
        )
    return PROMOTED_PAD_CAPABILITY_ROWS[key]


__all__ = [
    "PROMOTED_CUT_CAPABILITY_ROWS",
    "PROMOTED_PAD_CAPABILITY_ROWS",
    "DirectionPair",
    "resolve_cut_direction",
    "resolve_pad_direction",
]
