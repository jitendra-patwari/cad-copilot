"""Domain models and value objects for CAD execution, inspection, and artifacts.

This is a pure leaf module with zero runtime or abstract class dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, NewType, TypeAlias

BodyRef = NewType("BodyRef", str)
FeatureRef = NewType("FeatureRef", str)


@dataclass
class StandardInspectionReport:
    """Physical and geometric properties of an active CAD model."""

    volume_mm3: float
    mass_kg: float
    feature_count: int
    body_count: int


@dataclass
class PhysicalProperties:
    """Comprehensive physical properties and bounding extents."""

    density: float = 0.0
    volume_mm3: float = 0.0
    mass_kg: float = 0.0
    surface_area_mm2: float = 0.0
    center_of_gravity: tuple[float, float, float] = (0.0, 0.0, 0.0)
    bounding_box_min: tuple[float, float, float] = (0.0, 0.0, 0.0)
    bounding_box_max: tuple[float, float, float] = (0.0, 0.0, 0.0)


@dataclass
class ArtifactRecord:
    """Wire-compliant record of a generated CAD artifact."""

    type: str
    format: str
    path: str
    origin: str = "cad_copilot"
    size_bytes: int | None = None
    sha256: str | None = None


@dataclass
class ExecutionSuccess:
    """Result payload returned when CAD execution succeeds."""

    operations_executed: int = 0
    exported_artifacts: list[str] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)


@dataclass
class ExecutionFailure:
    """Result payload returned when CAD execution fails."""

    message: str
    phase: str = "execution"
    details: dict[str, Any] = field(default_factory=dict)
    warnings: list[dict[str, str]] = field(default_factory=list)


@dataclass
class RuntimeDiagnostics:
    """Diagnostic metadata describing the connected CAD runtime session."""

    ownership: str = "unknown"
    attachment_mode: str = "unspecified"
    visibility: str = "unknown"
    process_id: int | None = None
    version_build: str | None = None
    is_healthy: bool = False
    warnings: list[dict[str, str]] = field(default_factory=list)


ExecutionResult: TypeAlias = ExecutionSuccess | ExecutionFailure

__all__ = [
    "ArtifactRecord",
    "BodyRef",
    "ExecutionFailure",
    "ExecutionResult",
    "ExecutionSuccess",
    "FeatureRef",
    "PhysicalProperties",
    "RuntimeDiagnostics",
    "StandardInspectionReport",
]
