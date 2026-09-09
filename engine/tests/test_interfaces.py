"""Comprehensive unit tests for CAD Copilot domain interfaces, models, and exceptions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from interfaces import (
    ArtifactFormat,
    ArtifactRecord,
    BodyRef,
    CADContainmentError,
    CADDocumentError,
    CADError,
    CADExecutionError,
    CADExecutorABC,
    CADExportError,
    CADRuntimeABC,
    CADRuntimeBusyError,
    CADRuntimeError,
    CADRuntimeUnavailableError,
    ExecutionFailure,
    ExecutionResult,
    ExecutionSuccess,
    FeatureRef,
    OperationResult,
    PhysicalProperties,
    RuntimeDiagnostics,
    StandardInspectionReport,
)

# ---------------------------------------------------------------------------
# Test Fixtures & Mock Implementations
# ---------------------------------------------------------------------------


class ConcreteCADRuntime(CADRuntimeABC):
    """Complete concrete implementation of CADRuntimeABC for testing."""

    def __init__(self) -> None:
        self.connected = False
        self.open_docs: list[Path] = []
        self.created_docs: list[str] = []

    def connect_application(self) -> Any:
        self.connected = True
        return "mock_app_handle"

    def get_diagnostics(self) -> RuntimeDiagnostics:
        return RuntimeDiagnostics(
            ownership="owned" if self.connected else "unknown",
            attachment_mode="spawned_new" if self.connected else "unspecified",
            visibility="visible" if self.connected else "unknown",
            process_id=1234 if self.connected else None,
            version_build="226.00.00.00" if self.connected else None,
            is_healthy=self.connected,
        )

    def create_part_document(self, application: Any) -> Any:
        doc_id = f"mock_part_doc_{len(self.created_docs) + 1}"
        self.created_docs.append(doc_id)
        return doc_id

    def open_document(self, application: Any, path: Path) -> Any:
        self.open_docs.append(path)
        return f"mock_doc_handle:{path.name}"

    def close_document(self, doc_handle: Any) -> None:
        pass

    def teardown(self, force_kill_on_failure: bool = False) -> bool:
        self.connected = False
        return True

    def is_healthy(self) -> bool:
        return self.connected


class ConcreteCADExecutor(CADExecutorABC):
    """Complete concrete implementation of CADExecutorABC for testing."""

    def __init__(self) -> None:
        self.exported_models: list[tuple[str, Path]] = []
        self.preview_captures: list[Path] = []
        self.document_closed = False

    def execute_feature_plan(self, plan: Any, *, mode: Any = None) -> ExecutionResult:
        return ExecutionSuccess(
            operations_executed=1,
            exported_artifacts=[ArtifactRecord(type="geometry_step", format="step", path="mock.step")],
        )

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
        return BodyRef(body_id)

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
        return FeatureRef("feature.cutout.1")

    def export_model(self, format_id: ArtifactFormat, output_path: Path) -> None:
        self.exported_models.append((format_id, output_path))

    def capture_preview(self, output_path: Path) -> None:
        self.preview_captures.append(output_path)

    def close_request_document(self) -> None:
        self.document_closed = True

    def inspect_active_document(self) -> StandardInspectionReport:
        return StandardInspectionReport(
            volume_mm3=1500.0,
            mass_kg=1.17,
            feature_count=4,
            body_count=1,
        )

    def recompute_physical_properties(self) -> None:
        pass

    def update_document(self) -> None:
        pass


# ---------------------------------------------------------------------------
# 1. ABC Instantiation & Contract Enforcement Tests
# ---------------------------------------------------------------------------


def test_cad_runtime_abc_cannot_be_instantiated_directly() -> None:
    """Proves that CADRuntimeABC cannot be instantiated without implementing abstract methods."""
    with pytest.raises(TypeError, match="Can't instantiate abstract class CADRuntimeABC"):
        CADRuntimeABC()  # type: ignore[abstract]


def test_cad_runtime_partial_implementation_fails() -> None:
    """Proves that a partial subclass of CADRuntimeABC raises TypeError on instantiation."""

    class IncompleteRuntime(CADRuntimeABC):
        def connect_application(self) -> Any:
            return "app"

    with pytest.raises(TypeError, match="Can't instantiate abstract class IncompleteRuntime"):
        IncompleteRuntime()  # type: ignore[abstract]


def test_concrete_cad_runtime_lifecycle() -> None:
    """Proves that a fully implemented CADRuntimeABC operates cleanly."""
    runtime = ConcreteCADRuntime()
    assert runtime.is_healthy() is False
    assert runtime.connected is False

    diag_initial = runtime.get_diagnostics()
    assert diag_initial.ownership == "unknown"
    assert diag_initial.is_healthy is False

    app = runtime.connect_application()
    assert app == "mock_app_handle"
    assert runtime.connected is True
    assert runtime.is_healthy() is True

    diag_connected = runtime.get_diagnostics()
    assert diag_connected.ownership == "owned"
    assert diag_connected.attachment_mode == "spawned_new"
    assert diag_connected.visibility == "visible"
    assert diag_connected.process_id == 1234
    assert diag_connected.version_build == "226.00.00.00"
    assert diag_connected.is_healthy is True

    part_doc = runtime.create_part_document(app)
    assert part_doc == "mock_part_doc_1"
    assert runtime.created_docs == ["mock_part_doc_1"]

    doc = runtime.open_document(app, Path("test.par"))
    assert doc == "mock_doc_handle:test.par"
    assert runtime.open_docs == [Path("test.par")]

    clean = runtime.teardown(force_kill_on_failure=False)
    assert clean is True
    assert runtime.connected is False
    assert runtime.is_healthy() is False


def test_cad_executor_abc_cannot_be_instantiated_directly() -> None:
    """Proves that CADExecutorABC cannot be instantiated without implementing abstract methods."""
    with pytest.raises(TypeError, match="Can't instantiate abstract class CADExecutorABC"):
        CADExecutorABC()  # type: ignore[abstract]


def test_cad_executor_partial_implementation_fails() -> None:
    """Proves that a partial subclass of CADExecutorABC raises TypeError on instantiation."""

    class IncompleteExecutor(CADExecutorABC):
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
            return BodyRef("prism")

    with pytest.raises(TypeError, match="Can't instantiate abstract class IncompleteExecutor"):
        IncompleteExecutor()  # type: ignore[abstract]


def test_concrete_cad_executor_methods_and_defaults() -> None:
    """Proves that a concrete executor executes modeling, extraction, and export operations."""
    executor = ConcreteCADExecutor()

    assert executor.create_prism_body(length_mm=100.0, width_mm=80.0, thickness_mm=20.0) == BodyRef("body.main")
    assert executor.add_cylindrical_cutout(diameter_mm=10.0, depth_mm=20.0) == FeatureRef("feature.cutout.1")

    # Test high-level plan execution
    result = executor.execute_feature_plan({"steps": []})
    assert isinstance(result, ExecutionSuccess)
    assert result.operations_executed == 1

    # Test inspection and physical properties extraction
    report = executor.inspect_active_document()
    assert report.volume_mm3 == 1500.0
    assert report.mass_kg == 1.17
    assert report.feature_count == 4
    assert report.body_count == 1

    props = executor.extract_physical_properties()
    assert isinstance(props, PhysicalProperties)
    assert props.volume_mm3 == 1500.0
    assert props.mass_kg == 1.17

    # Test export model, preview capture, and close
    executor.export_model("step", Path("out/part.step"))
    assert executor.exported_models == [("step", Path("out/part.step"))]

    executor.capture_preview(Path("out/preview.jpg"))
    assert executor.preview_captures == [Path("out/preview.jpg")]

    assert executor.document_closed is False
    executor.close_request_document()
    assert executor.document_closed is True


# ---------------------------------------------------------------------------
# 2. Exception Hierarchy & Wire Error Code Tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exception_cls", "expected_code"),
    [
        (CADError, "CAD_ERROR"),
        (CADRuntimeError, "RUNTIME_ATTACH_FAILED"),
        (CADRuntimeUnavailableError, "RUNTIME_UNAVAILABLE"),
        (CADRuntimeBusyError, "RUNTIME_BUSY_TIMEOUT"),
        (CADExecutionError, "EXECUTION_FAILED"),
        (CADDocumentError, "DOCUMENT_IO_FAILED"),
        (CADExportError, "EXPORT_FAILED"),
        (CADContainmentError, "CONTAINMENT_VIOLATION"),
    ],
)
def test_exception_inheritance_and_error_codes(exception_cls: type[CADError], expected_code: str) -> None:
    """Proves that all domain exceptions inherit from CADError and define standard error codes."""
    err = exception_cls("Test error message")
    assert isinstance(err, CADError)
    assert isinstance(err, Exception)
    assert str(err) == "Test error message"
    if isinstance(err, CADError):
        assert err.error_code == expected_code


def test_exception_custom_error_code_override() -> None:
    """Proves that error_code can be customized during instantiation."""
    err = CADRuntimeError("Connection refused", error_code="CUSTOM_ATTACH_TIMEOUT")
    assert err.error_code == "CUSTOM_ATTACH_TIMEOUT"
    assert str(err) == "Connection refused"


def test_catch_all_via_cad_error() -> None:
    """Proves that catching base CADError intercepts all domain exceptions."""
    exceptions_to_test: list[CADError] = [
        CADRuntimeError("runtime failure"),
        CADExecutionError("execution failure"),
        CADDocumentError("document failure"),
        CADExportError("export failure"),
        CADContainmentError("containment failure"),
    ]

    for exc in exceptions_to_test:
        try:
            raise exc
        except CADError as caught:
            assert caught is exc
            assert caught.error_code != ""


# ---------------------------------------------------------------------------
# 3. Domain Models & Value Objects Tests
# ---------------------------------------------------------------------------


def test_standard_inspection_report_dataclass() -> None:
    """Verifies StandardInspectionReport attributes and fail-closed topology defaults."""
    # Test with unmeasured/default topology counts
    report = StandardInspectionReport(
        volume_mm3=25000.5,
        mass_kg=0.195,
        feature_count=12,
        body_count=1,
    )
    assert report.volume_mm3 == 25000.5
    assert report.mass_kg == 0.195
    assert report.feature_count == 12
    assert report.body_count == 1
    assert report.solid_body_count is None
    assert report.sheet_body_count is None
    assert report.wire_body_count is None

    # Test with explicit measured topology counts
    measured_report = StandardInspectionReport(
        volume_mm3=1500.0,
        mass_kg=1.17,
        feature_count=4,
        body_count=1,
        solid_body_count=1,
        sheet_body_count=0,
        wire_body_count=0,
    )
    assert measured_report.solid_body_count == 1
    assert measured_report.sheet_body_count == 0
    assert measured_report.wire_body_count == 0


def test_operation_result_dataclass() -> None:
    """Verifies OperationResult attributes, literal reference_kind, and frozen immutability."""
    op_body = OperationResult(
        patch_id="patch.body.1",
        operation="ensure_primitive_body",
        reference_id="body.main",
        reference_kind="body",
    )
    assert op_body.patch_id == "patch.body.1"
    assert op_body.operation == "ensure_primitive_body"
    assert op_body.reference_id == "body.main"
    assert op_body.reference_kind == "body"

    op_feat = OperationResult(
        patch_id="patch.cut_hole.1",
        operation="cut_hole",
        reference_id="feature.hole.1",
        reference_kind="feature",
    )
    assert op_feat.reference_kind == "feature"

    # Verify frozen immutability
    with pytest.raises(AttributeError):
        op_body.operation = "mutated"  # type: ignore[misc]


def test_execution_success_positional_compatibility() -> None:
    """Proves that ExecutionSuccess preserves positional constructor binding with typed ArtifactRecord."""
    rec_par = ArtifactRecord(type="native_part", format="par", path="out.par")
    rec_step = ArtifactRecord(type="geometry_step", format="step", path="out.step")

    # Positional instantiation with 1, 2, and 3 arguments
    success_1 = ExecutionSuccess(5)
    assert success_1.operations_executed == 5
    assert success_1.exported_artifacts == []
    assert success_1.warnings == []
    assert success_1.operation_results == []
    assert success_1.inspection_report is None

    success_2 = ExecutionSuccess(3, [rec_par, rec_step])
    assert success_2.operations_executed == 3
    assert success_2.exported_artifacts == [rec_par, rec_step]
    assert success_2.warnings == []

    success_3 = ExecutionSuccess(2, [rec_step], [{"code": "WARN_01", "message": "Notice"}])
    assert success_3.operations_executed == 2
    assert success_3.exported_artifacts == [rec_step]
    assert success_3.warnings == [{"code": "WARN_01", "message": "Notice"}]
    assert success_3.operation_results == []
    assert success_3.inspection_report is None


def test_artifact_format_type_invariants() -> None:
    """Proves that ArtifactFormat type alias includes exactly 'par', 'step', and 'stl'."""
    from typing import get_args

    assert set(get_args(ArtifactFormat)) == {"par", "step", "stl"}


def test_physical_properties_dataclass_defaults_and_custom() -> None:
    """Verifies PhysicalProperties zeroed defaults and custom attributes."""
    # Test zeroed defaults
    defaults = PhysicalProperties()
    assert defaults.density == 0.0
    assert defaults.volume_mm3 == 0.0
    assert defaults.mass_kg == 0.0
    assert defaults.surface_area_mm2 == 0.0
    assert defaults.center_of_gravity == (0.0, 0.0, 0.0)
    assert defaults.bounding_box_min == (0.0, 0.0, 0.0)
    assert defaults.bounding_box_max == (0.0, 0.0, 0.0)

    # Test custom values
    props = PhysicalProperties(
        density=7850.0,
        volume_mm3=1000.0,
        mass_kg=7.85,
        surface_area_mm2=600.0,
        center_of_gravity=(10.0, 20.0, 30.0),
        bounding_box_min=(0.0, 0.0, 0.0),
        bounding_box_max=(10.0, 10.0, 10.0),
    )
    assert props.density == 7850.0
    assert props.center_of_gravity == (10.0, 20.0, 30.0)
    assert props.bounding_box_max == (10.0, 10.0, 10.0)


def test_artifact_record_schema_compliance() -> None:
    """Verifies ArtifactRecord matches JSON Schema contract structure."""
    # Test minimal required parameters
    minimal = ArtifactRecord(type="native_part", format="par", path="out/part.par")
    assert minimal.origin == "cad_copilot"
    assert minimal.type == "native_part"
    assert minimal.format == "par"
    assert minimal.path == "out/part.par"
    assert minimal.size_bytes is None
    assert minimal.sha256 is None

    # Test full optional parameters
    full = ArtifactRecord(
        type="geometry_step",
        format="step",
        path="output/part.step",
        size_bytes=1048576,
        sha256="abc123def456",
    )
    assert full.origin == "cad_copilot"
    assert full.type == "geometry_step"
    assert full.format == "step"
    assert full.path == "output/part.step"
    assert full.size_bytes == 1048576
    assert full.sha256 == "abc123def456"


def test_execution_result_union_polymorphism() -> None:
    """Verifies ExecutionResult discriminated union behavior and M3.2 fields."""
    report = StandardInspectionReport(
        volume_mm3=1000.0,
        mass_kg=0.078,
        feature_count=2,
        body_count=1,
    )
    op_results = [
        OperationResult(
            patch_id="patch.body.1",
            operation="ensure_primitive_body",
            reference_id="body.main",
            reference_kind="body",
        ),
        OperationResult(
            patch_id="patch.cut_hole.1",
            operation="cut_hole",
            reference_id="feature.hole.1",
            reference_kind="feature",
        ),
    ]
    success: ExecutionResult = ExecutionSuccess(
        operations_executed=2,
        operation_results=op_results,
        inspection_report=report,
        exported_artifacts=[],
        warnings=[{"code": "WARN_1", "message": "Low clearance"}],
    )
    assert isinstance(success, ExecutionSuccess)
    assert success.operations_executed == 2
    assert len(success.operation_results) == 2
    assert success.operation_results[0].reference_id == "body.main"
    assert success.operation_results[1].reference_id == "feature.hole.1"
    assert success.inspection_report is not None
    assert success.inspection_report.volume_mm3 == 1000.0
    assert success.exported_artifacts == []
    assert success.warnings == [{"code": "WARN_1", "message": "Low clearance"}]

    failure: ExecutionResult = ExecutionFailure(
        message="Failed to cut hole",
        phase="modeling",
        details={"step_index": 2},
        warnings=[{"code": "WARN_2", "message": "Near boundary"}],
    )
    assert isinstance(failure, ExecutionFailure)
    assert failure.message == "Failed to cut hole"
    assert failure.phase == "modeling"
    assert failure.details == {"step_index": 2}
    assert failure.warnings == [{"code": "WARN_2", "message": "Near boundary"}]


def test_runtime_diagnostics_dataclass() -> None:
    """Verifies RuntimeDiagnostics default values and custom assignments."""
    default_diag = RuntimeDiagnostics()
    assert default_diag.ownership == "unknown"
    assert default_diag.attachment_mode == "unspecified"
    assert default_diag.visibility == "unknown"
    assert default_diag.process_id is None
    assert default_diag.version_build is None
    assert default_diag.is_healthy is False
    assert default_diag.warnings == []

    custom_diag = RuntimeDiagnostics(
        ownership="borrowed",
        attachment_mode="attached_existing",
        visibility="visible",
        process_id=5678,
        version_build="226.00.01.03",
        is_healthy=True,
        warnings=[{"code": "WARN_SE", "message": "Addin warning"}],
    )
    assert custom_diag.ownership == "borrowed"
    assert custom_diag.attachment_mode == "attached_existing"
    assert custom_diag.visibility == "visible"
    assert custom_diag.process_id == 5678
    assert custom_diag.version_build == "226.00.01.03"
    assert custom_diag.is_healthy is True
    assert len(custom_diag.warnings) == 1
