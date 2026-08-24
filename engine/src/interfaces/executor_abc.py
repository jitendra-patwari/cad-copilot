"""Abstract base class defining the CAD execution and parametric modeling contract."""

from __future__ import annotations

import abc
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .models import (
    ArtifactRecord,
    BodyRef,
    ExecutionResult,
    FeatureRef,
    PhysicalProperties,
    StandardInspectionReport,
)

if TYPE_CHECKING:
    from geometry.plan_models import FeaturePlan


class CADExecutorABC(abc.ABC):
    """Abstract interface for executing CAD modeling, inspection, and export tasks."""

    # --- High-Level Feature Plan Execution ---
    @abc.abstractmethod
    def execute_feature_plan(self, plan: FeaturePlan) -> ExecutionResult:
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

    # --- Artifact Export Methods ---
    @abc.abstractmethod
    def export_step(self, output_path: Path) -> None:
        """Export the active document geometry to a STEP file."""

    @abc.abstractmethod
    def export_preview(self, output_path: Path) -> None:
        """Export a preview image or thumbnail of the active document."""

    @abc.abstractmethod
    def export_preview_images(self, output_dir: Path, views: list[str]) -> list[Path]:
        """Export multi-angle preview snapshot images (e.g. isometric, top, front)."""

    @abc.abstractmethod
    def export_artifacts(self, formats: list[str], output_dir: Path) -> list[ArtifactRecord]:
        """Export requested formats and return wire-compliant artifact records."""

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

    @abc.abstractmethod
    def save_document(self) -> None:
        """Save the active document to disk."""


__all__ = ["CADExecutorABC"]
