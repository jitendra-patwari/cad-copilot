"""Solid Edge COM automation constant definitions and enumeration wrappers."""

from __future__ import annotations

# Solid Edge Document Modeling Modes
MODELING_MODE_SYNCHRONOUS: int = 1
MODELING_MODE_ORDERED: int = 2

# Profile Validation Constants (igProfileOK = 0, igProfileClosed = 1)
PROFILE_STATUS_OK: int = 0
PROFILE_STATUS_CLOSED: int = 1

# 2D Relations Keypoint Constants (KeypointIndexConstants)
# For Line2d: igLineStart = 0, igLineEnd = 1
KEYPOINT_LINE_START: int = 0
KEYPOINT_LINE_END: int = 1
# For Arc2d: igArcStart = 1, igArcEnd = 2
KEYPOINT_ARC_START: int = 1
KEYPOINT_ARC_END: int = 2

# Solid Edge Feature Property Constants (ProfileSide: igProfileSideNone=0, igLeft=1, igRight=2, igSymmetric=3)
PROFILE_SIDE_NONE: int = 0
PROFILE_SIDE_LEFT: int = 1
PROFILE_SIDE_RIGHT: int = 2
PROFILE_SIDE_SYMMETRIC: int = 3

# Reference Element Plane Side Offsets (ReferenceElementConstants: igReverseNormalSide=1, igNormalSide=2)
REF_PLANE_REVERSE_SIDE: int = 1
REF_PLANE_NORMAL_SIDE: int = 2

# Base Reference Plane Indices (1-based in Solid Edge PartDocument.RefPlanes: 1=Top/XY, 2=Right/YZ, 3=Front/XZ)
BASE_PLANE_INDEX_XY: int = 1  # Top (Z-normal)
BASE_PLANE_INDEX_YZ: int = 2  # Right (X-normal)
BASE_PLANE_INDEX_XZ: int = 3  # Front (Y-normal)

# Solid Edge Feature Status Constants (FeatureStatusConstants: igFeatureOK=1216476310)
FEATURE_STATUS_OK: int = 1216476310

# Solid Edge Body Type Constants (BodyTypeConstants: igSolidBody=1216476307, igSheetBody=1216476308, igCurveBody=-1020639371)
BODY_TYPE_SOLID: int = 1216476307
BODY_TYPE_SHEET: int = 1216476308
BODY_TYPE_CURVE: int = -1020639371

# Solid Edge Part Global Parameter Constants (PartGlobalParameterConstants)
PART_GLOBAL_DENSITY: int = 1
PART_GLOBAL_ACCURACY: int = 2

# Solid Edge PhysicalPropertiesStatusConstants
# (sePhysicalPropertiesStatus_None=0, sePhysicalPropertiesStatus_Model=1)
PHYSICAL_PROPERTIES_STATUS_NONE: int = 0
PHYSICAL_PROPERTIES_STATUS_MODEL: int = 1

__all__ = [
    "BASE_PLANE_INDEX_XY",
    "BASE_PLANE_INDEX_XZ",
    "BASE_PLANE_INDEX_YZ",
    "BODY_TYPE_CURVE",
    "BODY_TYPE_SHEET",
    "BODY_TYPE_SOLID",
    "FEATURE_STATUS_OK",
    "KEYPOINT_ARC_END",
    "KEYPOINT_ARC_START",
    "KEYPOINT_LINE_END",
    "KEYPOINT_LINE_START",
    "MODELING_MODE_ORDERED",
    "MODELING_MODE_SYNCHRONOUS",
    "PART_GLOBAL_ACCURACY",
    "PART_GLOBAL_DENSITY",
    "PHYSICAL_PROPERTIES_STATUS_MODEL",
    "PHYSICAL_PROPERTIES_STATUS_NONE",
    "PROFILE_SIDE_LEFT",
    "PROFILE_SIDE_NONE",
    "PROFILE_SIDE_RIGHT",
    "PROFILE_SIDE_SYMMETRIC",
    "PROFILE_STATUS_CLOSED",
    "PROFILE_STATUS_OK",
    "REF_PLANE_NORMAL_SIDE",
    "REF_PLANE_REVERSE_SIDE",
]
