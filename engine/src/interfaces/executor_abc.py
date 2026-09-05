"""Abstract base class defining the CAD execution and parametric modeling contract."""

from __future__ import annotations

import abc
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .models import (
    ArtifactFormat,
    BodyRef,
    ExecutionResult,
    FeatureRef,
    PhysicalProperties,
    StandardInspectionReport,
)

if TYPE_CHECKING:
    from geometry.gate_policy import GatePolicyMode
    from geometry.plan_models import FeaturePlan


class CADExecutorABC(abc.ABC):
    """Abstract interface for executing CAD modeling, inspection, and export tasks."""

    # --- High-Level Feature Plan Execution ---
    @abc.abstractmethod
    def execute_feature_plan(
        self,
        plan: FeaturePlan,
        *,
        mode: GatePolicyMode | None = None,
    ) -> ExecutionResult:
        """Execute a declarative feature plan AST and return the execution result."""

    # --- Core Modeling Primitives ---
    @abc.abstractmethod
    def create_prism_body(
        self,
        length_mm: float,
        width_mm: float,
        thickness_mm: float,
        placement_x_mm: float = 0.0,
        placement_y_mm: float = 0.0,
        placement_z_mm: float = 0.0,
        body_id: str = "body.main",
        **kwargs: Any,
    ) -> BodyRef:
        """Create a base prism or extrusion body and return its opaque body handle."""

    @abc.abstractmethod
    def add_cylindrical_cutout(
        self,
        diameter_mm: float,
        depth_mm: float = 0.0,
        target_face: str = "+Z",
        center_u_mm: float = 0.0,
        center_v_mm: float = 0.0,
        body_id: str = "body.main",
        **kwargs: Any,
    ) -> FeatureRef:
        """Add a cylindrical cutout or hole feature and return its opaque feature handle."""

    # --- Artifact Export & Terminal Lifecycle Methods ---
    @abc.abstractmethod
    def export_model(self, format_id: ArtifactFormat, output_path: Path) -> None:
        """Export the active document geometry to a required model format (PAR, STEP, or STL)."""

    @abc.abstractmethod
    def capture_preview(self, output_path: Path) -> None:
        """Capture a best-effort preview snapshot image (JPG) of the active document."""

    @abc.abstractmethod
    def close_request_document(self) -> None:
        """Terminally release and close the bound request document handle."""

    # --- QA & Mass Property Inspection ---
    @abc.abstractmethod
    def inspect_active_document(self) -> StandardInspectionReport:
        """Extract standardized physical and geometric properties from the active document."""

    def extract_physical_properties(self) -> PhysicalProperties:
        """Extract comprehensive physical properties including bounding box extents."""
        report = self.inspect_active_document()
        return PhysicalProperties(
            volume_mm3=report.volume_mm3,
            mass_kg=report.mass_kg,
        )

    @abc.abstractmethod
    def recompute_physical_properties(self) -> None:
        """Force recomputation of physical and mass properties in the CAD kernel."""

    # --- Batch Operations & Drawing Generation ---
    @abc.abstractmethod
    def generate_flat_pattern(self, output_path: Path) -> None:
        """Generate a sheet metal flat pattern (DXF)."""

    @abc.abstractmethod
    def generate_draft(self, output_path: Path) -> None:
        """Generate a 2D engineering draft/drawing."""

    @abc.abstractmethod
    def publish_drawing(self, output_path: Path) -> None:
        """Publish an engineering drawing to PDF or DWG format."""

    @abc.abstractmethod
    def read_custom_properties(self) -> dict[str, Any]:
        """Read custom document metadata and properties."""

    @abc.abstractmethod
    def write_custom_properties(self, properties: dict[str, Any]) -> None:
        """Write custom document metadata and properties."""

    @abc.abstractmethod
    def update_document(self) -> None:
        """Force geometric recompute on the active document."""


__all__ = ["CADExecutorABC"]
