"""Unit conversion and dimension validation utilities for the Solid Edge driver."""

from __future__ import annotations

import math


def mm_to_m(val_mm: float) -> float:
    """Convert millimeters to meters (SI base unit for Solid Edge COM API)."""
    if not math.isfinite(val_mm):
        raise ValueError(f"Value must be a finite float, got {val_mm}")
    return float(val_mm) / 1000.0


def m_to_mm(val_m: float) -> float:
    """Convert meters to millimeters."""
    if not math.isfinite(val_m):
        raise ValueError(f"Value must be a finite float, got {val_m}")
    return float(val_m) * 1000.0


def m3_to_mm3(vol_m3: float) -> float:
    """Convert cubic meters (Solid Edge COM volume) to cubic millimeters."""
    if not math.isfinite(vol_m3):
        raise ValueError(f"Volume must be a finite float, got {vol_m3}")
    return float(vol_m3) * 1e9


def m2_to_mm2(area_m2: float) -> float:
    """Convert square meters (Solid Edge COM surface area) to square millimeters."""
    if not math.isfinite(area_m2):
        raise ValueError(f"Area must be a finite float, got {area_m2}")
    return float(area_m2) * 1e6


def deg_to_rad(deg: float) -> float:
    """Convert degrees to radians."""
    if not math.isfinite(deg):
        raise ValueError(f"Degrees must be a finite float, got {deg}")
    return math.radians(float(deg))


def rad_to_deg(rad: float) -> float:
    """Convert radians to degrees."""
    if not math.isfinite(rad):
        raise ValueError(f"Radians must be a finite float, got {rad}")
    return math.degrees(float(rad))


__all__ = [
    "deg_to_rad",
    "m2_to_mm2",
    "m3_to_mm3",
    "m_to_mm",
    "mm_to_m",
    "rad_to_deg",
]
