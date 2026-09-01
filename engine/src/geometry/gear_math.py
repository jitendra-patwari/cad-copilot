"""Parametric conceptual spur gear tooth outline geometry and analytical calculations.

Invariants:
    1. Zero I/O Purity (NFR-1): Pure analytical math with zero disk/network I/O or COM imports.
    2. Mathematical Determinism (NFR-2): Deterministic coordinate generation with < 1e-6 mm drift.
    3. Defensive Preconditions: Strict parameter bounds validation against zero/negative
       tooth count, non-positive module, out-of-range pressure angles, and invalid bore geometry.
"""

from __future__ import annotations

import math

from geometry.plan_models import ProfilePoint2D


def validate_gear_parameters(
    *,
    tooth_count: int,
    module_mm: float,
    pressure_angle_deg: float = 20.0,
    bore_diameter_mm: float = 0.0,
) -> None:
    """Validate geometric and physical bounds for spur gear parameters.

    Raises:
        ValueError: If any parameter violates physical or mathematical bounds.
    """
    if not isinstance(tooth_count, int) or tooth_count < 4:
        raise ValueError(f"tooth_count must be an integer >= 4 to form a valid gear profile, got {tooth_count}")

    if not math.isfinite(module_mm) or module_mm <= 0.0:
        raise ValueError(f"module_mm must be a positive finite float (> 0.0), got {module_mm}")

    if not math.isfinite(pressure_angle_deg) or not (0.0 < pressure_angle_deg < 45.0):
        raise ValueError(f"pressure_angle_deg must be strictly between 0.0 and 45.0 degrees, got {pressure_angle_deg}")

    if not math.isfinite(bore_diameter_mm) or bore_diameter_mm < 0.0:
        raise ValueError(f"bore_diameter_mm must be a non-negative finite float, got {bore_diameter_mm}")

    if bore_diameter_mm > 0.0:
        root_rad = gear_root_radius(tooth_count, module_mm)
        if bore_diameter_mm >= 2.0 * root_rad:
            raise ValueError(
                f"bore_diameter_mm ({bore_diameter_mm:.3f} mm) cannot exceed or equal root diameter "
                f"({2.0 * root_rad:.3f} mm)."
            )


def gear_pitch_radius(tooth_count: int, module_mm: float) -> float:
    """Calculate the pitch circle radius: r_p = (z * m) / 2."""
    return tooth_count * module_mm / 2.0


def gear_base_radius(
    tooth_count: int,
    module_mm: float,
    pressure_angle_deg: float = 20.0,
) -> float:
    """Calculate the base circle radius: r_b = r_p * cos(alpha)."""
    return gear_pitch_radius(tooth_count, module_mm) * math.cos(math.radians(pressure_angle_deg))


def gear_tip_radius(tooth_count: int, module_mm: float) -> float:
    """Calculate the tip (addendum) circle radius: r_a = r_p + m."""
    return gear_pitch_radius(tooth_count, module_mm) + module_mm


def gear_root_radius(tooth_count: int, module_mm: float) -> float:
    """Calculate the root (dedendum) circle radius: r_f = max(0.35*m, r_p - 1.25*m)."""
    pitch_rad = gear_pitch_radius(tooth_count, module_mm)
    return max(module_mm * 0.35, pitch_rad - 1.25 * module_mm)


def involute_point(base_radius: float, roll_angle_rad: float) -> tuple[float, float]:
    """Calculate 2D Cartesian coordinates (x, y) for an involute curve point.

    Formula:
        x(theta) = r_b * (cos(theta) + theta * sin(theta))
        y(theta) = r_b * (sin(theta) - theta * cos(theta))
    """
    x = base_radius * (math.cos(roll_angle_rad) + roll_angle_rad * math.sin(roll_angle_rad))
    y = base_radius * (math.sin(roll_angle_rad) - roll_angle_rad * math.cos(roll_angle_rad))
    return (x, y)


def spur_gear_outline_points(
    *,
    tooth_count: int,
    module_mm: float,
    pressure_angle_deg: float = 20.0,
    bore_diameter_mm: float = 0.0,
    closed: bool = False,
) -> list[dict[str, float]]:
    """Return a 2D polygon vertex sequence for a spur gear tooth outline.

    The outline preserves key mechanical gear-scale relationships: pitch radius,
    addendum, dedendum, and a pressure-angle-influenced pitch flank.

    Args:
        tooth_count: Number of gear teeth (z >= 4).
        module_mm: Gear module in millimeters (m > 0).
        pressure_angle_deg: Standard pressure angle in degrees (0 < alpha < 45, default 20.0).
        bore_diameter_mm: Optional center shaft bore diameter (mm).
        closed: If True, appends the starting point to the end to form a closed polyline.
                If False (default), returns an open polygon matching standard sketch loop conventions.

    Returns:
        List of vertex dicts containing ``{"x_mm": float, "y_mm": float}``.
    """
    validate_gear_parameters(
        tooth_count=tooth_count,
        module_mm=module_mm,
        pressure_angle_deg=pressure_angle_deg,
        bore_diameter_mm=bore_diameter_mm,
    )

    outer_radius = gear_tip_radius(tooth_count, module_mm)
    root_radius = gear_root_radius(tooth_count, module_mm)
    pitch_flank_radius = max(root_radius, gear_base_radius(tooth_count, module_mm, pressure_angle_deg))
    tooth_angle = 2.0 * math.pi / tooth_count
    points: list[dict[str, float]] = []

    for tooth_index in range(tooth_count):
        base_angle = tooth_index * tooth_angle
        # Five unique points per tooth (omitting redundant +0.50 root endpoint
        # which is provided by the next tooth's -0.50 root start).
        tooth_points = (
            (-0.50, root_radius),
            (-0.32, pitch_flank_radius),
            (-0.18, outer_radius),
            (0.18, outer_radius),
            (0.32, pitch_flank_radius),
        )
        for angle_fraction, radius in tooth_points:
            angle = base_angle + angle_fraction * tooth_angle
            points.append(
                {
                    "x_mm": radius * math.cos(angle),
                    "y_mm": radius * math.sin(angle),
                }
            )

    if closed and points:
        points.append(dict(points[0]))

    return points


def spur_gear_profile_points(
    *,
    tooth_count: int,
    module_mm: float,
    pressure_angle_deg: float = 20.0,
    bore_diameter_mm: float = 0.0,
    closed: bool = False,
) -> list[ProfilePoint2D]:
    """Return a typed 2D ProfilePoint2D sequence for a spur gear tooth outline."""
    raw_points = spur_gear_outline_points(
        tooth_count=tooth_count,
        module_mm=module_mm,
        pressure_angle_deg=pressure_angle_deg,
        bore_diameter_mm=bore_diameter_mm,
        closed=closed,
    )
    return [ProfilePoint2D(x_mm=pt["x_mm"], y_mm=pt["y_mm"]) for pt in raw_points]


__all__ = [
    "gear_base_radius",
    "gear_pitch_radius",
    "gear_root_radius",
    "gear_tip_radius",
    "involute_point",
    "spur_gear_outline_points",
    "spur_gear_profile_points",
    "validate_gear_parameters",
]
