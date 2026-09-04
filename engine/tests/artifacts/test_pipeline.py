"""Unit and integration test suite for artifact pipeline orchestration.

Invariants Verified:
    1. Happy Path Full Set: sequential export of PAR -> STEP -> STL -> JPG, terminal close, validation, publication.
    2. Localized Preview Capture Failure: non-fatal warning, required models published, no partial files.
    3. Localized Preview Validation Failure: malformed preview unlinked from staging after close, warning appended.
    4. Fatal Preview Runtime Failure: fatal runtime errors propagate immediately, halting publication.
    5. Preflight Failures: invalid inspection authority, invalid request IDs, existing targets fail before COM and close doc.
    6. Fail-Fast Export Checks: missing or 0-byte model files immediately abort subsequent exports and preview.
    7. Validation Failures: invalid STEP/STL syntax or entity structure blocks publication and cleans staging.
    8. Document Close Failures: close failure blocks publication and refuses recursive staging cleanup (retains residue).
    9. Exception Masking Prevention: export failures are preserved when document close also fails.
    10. Atomic Publication Race: concurrent destination creation raises TARGET_ALREADY_EXISTS, leaves foreign dir untouched.
    11. Wire Contract Projection: returned internal records project cleanly into Draft 2020-12 generation-response schema.
    12. Package Re-exports: artifacts package exports finalize_request_artifacts and REQUIRED_MODEL_FORMATS.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

import pytest
from jsonschema.validators import Draft202012Validator

import artifacts
from artifacts.paths import ArtifactPathError
from artifacts.pipeline import (
    REQUIRED_MODEL_FORMATS,
    finalize_request_artifacts,
)
from artifacts.validation import (
    JPEG_EOI,
    JPEG_SOI,
    MIN_JPG_SIZE_BYTES,
    ArtifactValidationError,
    FileSnapshot,
    ValidatedArtifact,
)
from interfaces.exceptions import (
    CADDocumentError,
    CADExecutionError,
    CADExportError,
    CADRuntimeBusyError,
)
from interfaces.executor_abc import CADExecutorABC
from interfaces.models import (
    ArtifactFormat,
    ExecutionSuccess,
    OperationResult,
    StandardInspectionReport,
)

# ---------------------------------------------------------------------------
# Test Helpers & Minimal Valid Payloads
# ---------------------------------------------------------------------------


def _create_minimal_valid_step_content() -> str:
    """Create a syntactically valid ISO-10303-21 STEP string with a MANIFOLD_SOLID_BREP."""
    return """ISO-10303-21;
HEADER;
FILE_DESCRIPTION(('CAD Copilot Test STEP'),'2;1');
FILE_NAME('test.step','2026-09-02T12:00:00',('Tester'),('CAD Copilot'),'Preprocessor','OriginatingSystem','Authorization');
FILE_SCHEMA(('CONFIG_CONTROL_DESIGN'));
ENDSEC;
DATA;
#1 = CARTESIAN_POINT('',(0.0,0.0,0.0));
#2 = CARTESIAN_POINT('',(30.0,20.0,10.0));
#10 = ( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) );
#20 = MANIFOLD_SOLID_BREP('Body1',#30);
#30 = CLOSED_SHELL('Shell1',());
#40 = ADVANCED_FACE('Face1',(),#50,.T.);
#50 = PLANE('Plane1',#60);
#60 = AXIS2_PLACEMENT_3D('Placement1',#1,#70,#80);
#70 = DIRECTION('Axis',(0.0,0.0,1.0));
#80 = DIRECTION('RefDirection',(1.0,0.0,0.0));
ENDSEC;
END-ISO-10303-21;
"""


def _create_binary_stl_bytes(triangle_count: int = 1) -> bytes:
    """Create binary STL byte sequence with 80-byte header and triangle_count facets."""
    header = b"CAD Copilot Binary STL".ljust(80, b"\x00")[:80]
    data = bytearray(header)
    data.extend(struct.pack("<I", triangle_count))

    for _ in range(triangle_count):
        data.extend(struct.pack("<3f", 0.0, 0.0, 1.0))  # normal
        data.extend(struct.pack("<3f", 0.0, 0.0, 0.0))  # v1
        data.extend(struct.pack("<3f", 10.0, 0.0, 0.0))  # v2
        data.extend(struct.pack("<3f", 0.0, 10.0, 0.0))  # v3
        data.extend(struct.pack("<H", 0))  # attribute byte count

    return bytes(data)


def _create_valid_jpeg_bytes(length: int = 256) -> bytes:
    """Create minimal plausible JPEG file byte sequence with SOI and EOI markers."""
    assert length >= MIN_JPG_SIZE_BYTES
    body = b"\x00" * (length - 4)
    return JPEG_SOI + body + JPEG_EOI


def _create_valid_inspection_report() -> StandardInspectionReport:
    """Create an authoritative M3.2 inspection report for a single healthy solid body."""
    return StandardInspectionReport(
        volume_mm3=6000.0,
        mass_kg=0.0468,
        feature_count=1,
        body_count=1,
        solid_body_count=1,
        sheet_body_count=0,
        wire_body_count=0,
    )


# ---------------------------------------------------------------------------
# Deterministic Fake CAD Executor Test Double
# ---------------------------------------------------------------------------


class FakePipelineExecutor(CADExecutorABC):
    """Deterministic fake CADExecutorABC for offline artifact pipeline testing."""

    def __init__(
        self,
        *,
        par_content: bytes = b"Fake Solid Edge PAR File Binary Data",
        step_content: str | None = None,
        stl_content: bytes | None = None,
        jpg_content: bytes | None = None,
        export_error_on: str | None = None,
        export_silent_fail_on: str | None = None,
        preview_error: Exception | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self.par_content = par_content
        self.step_content = step_content or _create_minimal_valid_step_content()
        self.stl_content = stl_content or _create_binary_stl_bytes(1)
        self.jpg_content = jpg_content or _create_valid_jpeg_bytes(256)

        self.export_error_on = export_error_on
        self.export_silent_fail_on = export_silent_fail_on
        self.preview_error = preview_error
        self.close_error = close_error

        self.call_log: list[str] = []
        self.document_closed: bool = False

    def export_model(self, format_id: ArtifactFormat, output_path: Path) -> None:
        self.call_log.append(f"export_model:{format_id}")
        if self.export_error_on == format_id:
            raise CADExportError(
                f"Fake export failure for format '{format_id}'",
                error_code="ARTIFACT_EXPORT_FAILED",
            )
        if self.export_silent_fail_on == format_id:
            # Silent failure: write 0-byte file
            output_path.touch()
            return

        if format_id == "par":
            output_path.write_bytes(self.par_content)
        elif format_id == "step":
            output_path.write_text(self.step_content, encoding="utf-8")
        elif format_id == "stl":
            output_path.write_bytes(self.stl_content)
        else:
            raise ValueError(f"Unknown format: {format_id}")

    def capture_preview(self, output_path: Path) -> None:
        self.call_log.append("capture_preview")
        if self.preview_error is not None:
            raise self.preview_error
        output_path.write_bytes(self.jpg_content)

    def close_request_document(self) -> None:
        self.call_log.append("close_request_document")
        self.document_closed = True
        if self.close_error is not None:
            raise self.close_error

    # --- Out of scope abstract methods ---
    def execute_feature_plan(self, plan: Any) -> Any:
        raise NotImplementedError

    def create_prism_body(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def add_cylindrical_cutout(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    def inspect_active_document(self) -> StandardInspectionReport:
        raise NotImplementedError

    def recompute_physical_properties(self) -> None:
        pass

    def generate_flat_pattern(self, output_path: Path) -> None:
        raise NotImplementedError

    def generate_draft(self, output_path: Path) -> None:
        raise NotImplementedError

    def publish_drawing(self, output_path: Path) -> None:
        raise NotImplementedError

    def read_custom_properties(self) -> dict[str, Any]:
        return {}

    def write_custom_properties(self, properties: dict[str, Any]) -> None:
        pass

    def update_document(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------


def test_finalize_request_artifacts_happy_path_full_set(tmp_path: Path) -> None:
    """Proves full sequential execution, release, validation, and publication of all 4 artifacts."""
    executor = FakePipelineExecutor()
    req_id = "test-req-001"
    output_root = tmp_path / "cad_output"

    initial_success = ExecutionSuccess(
        operations_executed=2,
        exported_artifacts=[],
        warnings=[{"code": "INITIAL_WARNING", "message": "Initial warning msg"}],
        operation_results=[
            OperationResult(
                patch_id="p1",
                operation="extrude_prism",
                reference_id="b1",
                reference_kind="body",
            )
        ],
        inspection_report=_create_valid_inspection_report(),
    )

    result = finalize_request_artifacts(executor, initial_success, output_root, req_id)

    # 1. Verify exact call sequence: par -> step -> stl -> capture_preview -> close
    expected_calls = [
        "export_model:par",
        "export_model:step",
        "export_model:stl",
        "capture_preview",
        "close_request_document",
    ]
    assert executor.call_log == expected_calls
    assert executor.document_closed is True

    # 2. Verify returned ExecutionSuccess
    assert result.operations_executed == 2
    assert len(result.operation_results) == 1
    assert result.inspection_report is not None
    assert result.inspection_report.volume_mm3 == 6000.0
    assert len(result.warnings) == 1
    assert result.warnings[0]["code"] == "INITIAL_WARNING"

    # 3. Verify ArtifactRecords (canonical order: par, step, stl, jpg)
    assert len(result.exported_artifacts) == 4
    formats = [r.format for r in result.exported_artifacts]
    types = [r.type for r in result.exported_artifacts]
    assert formats == ["par", "step", "stl", "jpg"]
    assert types == ["native_part", "geometry_step", "mesh_stl", "preview_image"]

    for r in result.exported_artifacts:
        assert r.origin == "cad_copilot"
        assert r.size_bytes is not None and r.size_bytes > 0
        assert r.sha256 is not None and len(r.sha256) == 64
        # Verify final path exists on disk
        target_path = Path(r.path)
        assert target_path.is_file()
        assert target_path.is_relative_to(output_root.resolve())

    # 4. Verify publication: final dir exists, no staging dir left
    final_dir = output_root / req_id
    assert final_dir.is_dir()
    staging_dirs = list(output_root.glob(".staging-*"))
    assert len(staging_dirs) == 0


def test_finalize_request_artifacts_happy_path_with_none_sheet_wire_counts(tmp_path: Path) -> None:
    """Proves finalize_request_artifacts succeeds when inspection report has unspecified (None) sheet/wire counts."""
    executor = FakePipelineExecutor()
    req_id = "test-req-none-counts"
    output_root = tmp_path / "cad_output"

    report_with_none = StandardInspectionReport(
        volume_mm3=5000.0,
        mass_kg=0.04,
        feature_count=1,
        body_count=1,
        solid_body_count=1,
        sheet_body_count=None,
        wire_body_count=None,
    )
    initial_success = ExecutionSuccess(
        operations_executed=1,
        inspection_report=report_with_none,
    )

    result = finalize_request_artifacts(executor, initial_success, output_root, req_id)
    assert len(result.exported_artifacts) == 4
    assert result.inspection_report is not None
    assert result.inspection_report.solid_body_count == 1
    assert (output_root / req_id / f"{req_id}.par").is_file()


def test_finalize_request_artifacts_localized_preview_capture_failure(tmp_path: Path) -> None:
    """Proves localized preview capture failure permits success, publishes required files, and appends warning."""
    executor = FakePipelineExecutor(
        preview_error=CADExportError(
            "Fake preview capture failed",
            error_code="PREVIEW_EXPORT_FAILED",
        )
    )
    req_id = "test-req-002"
    output_root = tmp_path / "cad_output"

    initial_success = ExecutionSuccess(
        operations_executed=1,
        inspection_report=_create_valid_inspection_report(),
    )

    result = finalize_request_artifacts(executor, initial_success, output_root, req_id)

    # Required 3 artifacts exported and published
    assert len(result.exported_artifacts) == 3
    formats = [r.format for r in result.exported_artifacts]
    assert formats == ["par", "step", "stl"]

    # Preview failure appended as warning
    assert len(result.warnings) == 1
    assert result.warnings[0]["code"] == "PREVIEW_EXPORT_FAILED"
    assert "<details redacted>" in result.warnings[0]["message"]

    # Final directory contains only the 3 required models
    final_dir = output_root / req_id
    published_files = {p.name for p in final_dir.iterdir()}
    assert published_files == {f"{req_id}.par", f"{req_id}.step", f"{req_id}.stl"}
    assert f"{req_id}.jpg" not in published_files


def test_finalize_request_artifacts_localized_preview_validation_failure(tmp_path: Path) -> None:
    """Proves malformed preview output is cleaned up post-close and converted to a warning."""
    # Invalid JPEG content: missing SOI/EOI
    invalid_jpg = b"CORRUPTED_NOT_A_JPEG_FILE" * 10
    executor = FakePipelineExecutor(jpg_content=invalid_jpg)
    req_id = "test-req-003"
    output_root = tmp_path / "cad_output"

    initial_success = ExecutionSuccess(
        operations_executed=1,
        inspection_report=_create_valid_inspection_report(),
    )

    result = finalize_request_artifacts(executor, initial_success, output_root, req_id)

    # Required 3 artifacts exported and published
    assert len(result.exported_artifacts) == 3
    assert [r.format for r in result.exported_artifacts] == ["par", "step", "stl"]

    # Preview validation failure appended as warning
    assert len(result.warnings) == 1
    assert result.warnings[0]["code"] == "PREVIEW_EXPORT_FAILED"

    # Final directory does NOT contain the corrupted .jpg
    final_dir = output_root / req_id
    assert not (final_dir / f"{req_id}.jpg").exists()


def test_finalize_request_artifacts_fatal_preview_runtime_failure(tmp_path: Path) -> None:
    """Proves fatal runtime/document failure during preview capture aborts pipeline without warning conversion."""
    executor = FakePipelineExecutor(
        preview_error=CADDocumentError("Document lost during preview", error_code="DOCUMENT_LOST")
    )
    req_id = "test-req-004"
    output_root = tmp_path / "cad_output"

    initial_success = ExecutionSuccess(
        operations_executed=1,
        inspection_report=_create_valid_inspection_report(),
    )

    with pytest.raises(CADDocumentError) as exc_info:
        finalize_request_artifacts(executor, initial_success, output_root, req_id)

    assert exc_info.value.error_code == "DOCUMENT_LOST"
    # Document release was still attempted
    assert "close_request_document" in executor.call_log
    # No final directory published
    assert not (output_root / req_id).exists()
    # Staging was cleaned up
    assert len(list(output_root.glob(".staging-*"))) == 0


def test_finalize_request_artifacts_fatal_preview_busy_failure(tmp_path: Path) -> None:
    """Proves COM busy error during preview is treated as fatal and not swallowed."""
    executor = FakePipelineExecutor(preview_error=CADRuntimeBusyError("Kernel busy", error_code="RUNTIME_BUSY_TIMEOUT"))
    req_id = "test-req-005"
    output_root = tmp_path / "cad_output"

    initial_success = ExecutionSuccess(
        operations_executed=1,
        inspection_report=_create_valid_inspection_report(),
    )

    with pytest.raises(CADRuntimeBusyError):
        finalize_request_artifacts(executor, initial_success, output_root, req_id)

    assert not (output_root / req_id).exists()


def test_finalize_request_artifacts_preflight_missing_inspection_authority(tmp_path: Path) -> None:
    """Proves missing inspection report raises NATIVE_QA_BLOCKED and still releases document."""
    executor = FakePipelineExecutor()
    req_id = "test-req-006"
    output_root = tmp_path / "cad_output"

    initial_success = ExecutionSuccess(
        operations_executed=1,
        inspection_report=None,  # Missing!
    )

    with pytest.raises(CADExecutionError) as exc_info:
        finalize_request_artifacts(executor, initial_success, output_root, req_id)

    assert exc_info.value.error_code == "NATIVE_QA_BLOCKED"
    # Terminal close was called even though preflight failed
    assert executor.call_log == ["close_request_document"]
    # No staging or final directory created
    assert not (output_root / req_id).exists()


@pytest.mark.parametrize(
    ("solid_count", "sheet_count", "wire_count", "vol"),
    [
        (0, 0, 0, 1000.0),  # 0 solid bodies
        (2, 0, 0, 1000.0),  # 2 solid bodies
        (1, 1, 0, 1000.0),  # 1 sheet body
        (1, 0, 1, 1000.0),  # 1 wire body
        (1, 0, 0, -100.0),  # negative volume
        (1, 0, 0, 0.0),  # zero volume
        (1, 0, 0, float("nan")),  # NaN volume
        (1, 0, 0, float("inf")),  # Infinite volume
    ],
)
def test_finalize_request_artifacts_preflight_invalid_inspection_topology(
    tmp_path: Path,
    solid_count: int,
    sheet_count: int,
    wire_count: int,
    vol: float,
) -> None:
    """Proves non-single-solid inspection reports raise NATIVE_QA_BLOCKED and release document."""
    executor = FakePipelineExecutor()
    req_id = "test-req-007"
    output_root = tmp_path / "cad_output"

    report = StandardInspectionReport(
        volume_mm3=vol,
        mass_kg=1.0,
        feature_count=1,
        body_count=solid_count + sheet_count + wire_count,
        solid_body_count=solid_count,
        sheet_body_count=sheet_count,
        wire_body_count=wire_count,
    )
    initial_success = ExecutionSuccess(
        operations_executed=1,
        inspection_report=report,
    )

    with pytest.raises(CADExecutionError) as exc_info:
        finalize_request_artifacts(executor, initial_success, output_root, req_id)

    assert exc_info.value.error_code == "NATIVE_QA_BLOCKED"
    assert executor.call_log == ["close_request_document"]


@pytest.mark.parametrize(
    "bad_req_id",
    [
        "CON",
        "NUL",
        "COM1",
        "..",
        ".",
        "../traversal",
        "has space",
        "bad/slash",
        "trailing_dot.",
    ],
)
def test_finalize_request_artifacts_preflight_invalid_request_id(tmp_path: Path, bad_req_id: str) -> None:
    """Proves invalid request IDs fail path preflight and still release document."""
    executor = FakePipelineExecutor()
    output_root = tmp_path / "cad_output"
    initial_success = ExecutionSuccess(
        operations_executed=1,
        inspection_report=_create_valid_inspection_report(),
    )

    with pytest.raises(ArtifactPathError) as exc_info:
        finalize_request_artifacts(executor, initial_success, output_root, bad_req_id)

    assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
    assert executor.call_log == ["close_request_document"]


def test_finalize_request_artifacts_preflight_target_already_exists(tmp_path: Path) -> None:
    """Proves collision check rejects existing final directory before any export is attempted."""
    executor = FakePipelineExecutor()
    req_id = "collision-req-001"
    output_root = tmp_path / "cad_output"
    final_dir = output_root / req_id
    final_dir.mkdir(parents=True)
    (final_dir / "existing_file.txt").write_text("prior content")

    initial_success = ExecutionSuccess(
        operations_executed=1,
        inspection_report=_create_valid_inspection_report(),
    )

    with pytest.raises(ArtifactPathError) as exc_info:
        finalize_request_artifacts(executor, initial_success, output_root, req_id)

    assert exc_info.value.error_code == "TARGET_ALREADY_EXISTS"
    assert executor.call_log == ["close_request_document"]
    # Existing content untouched
    assert (final_dir / "existing_file.txt").read_text() == "prior content"


@pytest.mark.parametrize("failing_fmt", ["par", "step", "stl"])
def test_finalize_request_artifacts_required_export_failure(tmp_path: Path, failing_fmt: str) -> None:
    """Proves required export failure is fatal, skips preview, cleans staging, and releases doc."""
    executor = FakePipelineExecutor(export_error_on=failing_fmt)
    req_id = f"test-req-fail-{failing_fmt}"
    output_root = tmp_path / "cad_output"

    initial_success = ExecutionSuccess(
        operations_executed=1,
        inspection_report=_create_valid_inspection_report(),
    )

    with pytest.raises(CADExportError) as exc_info:
        finalize_request_artifacts(executor, initial_success, output_root, req_id)

    assert exc_info.value.error_code == "ARTIFACT_EXPORT_FAILED"
    # Preview was skipped
    assert "capture_preview" not in executor.call_log
    # Document release occurred
    assert "close_request_document" in executor.call_log
    # Staging cleaned up
    assert not (output_root / req_id).exists()
    assert len(list(output_root.glob(".staging-*"))) == 0


def test_finalize_request_artifacts_fail_fast_silent_export_failure(tmp_path: Path) -> None:
    """Proves fail-fast stat check catches 0-byte file when export returns without raising."""
    executor = FakePipelineExecutor(export_silent_fail_on="step")
    req_id = "test-req-silent-fail"
    output_root = tmp_path / "cad_output"

    initial_success = ExecutionSuccess(
        operations_executed=1,
        inspection_report=_create_valid_inspection_report(),
    )

    with pytest.raises(CADExportError) as exc_info:
        finalize_request_artifacts(executor, initial_success, output_root, req_id)

    assert exc_info.value.error_code == "ARTIFACT_EXPORT_FAILED"
    assert "missing or empty file" in str(exc_info.value)
    # STL and preview were skipped
    assert "export_model:stl" not in executor.call_log
    assert "capture_preview" not in executor.call_log
    assert "close_request_document" in executor.call_log


def test_finalize_request_artifacts_validation_failure_on_step(tmp_path: Path) -> None:
    """Proves invalid STEP syntax/brep after export fails pipeline and cleans staging."""
    # Malformed STEP: no MANIFOLD_SOLID_BREP
    invalid_step = (
        "ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION(('test'),'2;1');\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n"
    )
    executor = FakePipelineExecutor(step_content=invalid_step)
    req_id = "test-req-invalid-step"
    output_root = tmp_path / "cad_output"

    initial_success = ExecutionSuccess(
        operations_executed=1,
        inspection_report=_create_valid_inspection_report(),
    )

    with pytest.raises(ArtifactValidationError) as exc_info:
        finalize_request_artifacts(executor, initial_success, output_root, req_id)

    assert exc_info.value.error_code == "ARTIFACT_EXPORT_FAILED"
    # Document closed before validation
    assert executor.document_closed is True
    # Staging cleaned up
    assert not (output_root / req_id).exists()
    assert len(list(output_root.glob(".staging-*"))) == 0


def test_finalize_request_artifacts_validation_failure_on_stl(tmp_path: Path) -> None:
    """Proves zero-triangle binary STL fails validation and cleans staging."""
    zero_triangle_stl = _create_binary_stl_bytes(0)
    executor = FakePipelineExecutor(stl_content=zero_triangle_stl)
    req_id = "test-req-invalid-stl"
    output_root = tmp_path / "cad_output"

    initial_success = ExecutionSuccess(
        operations_executed=1,
        inspection_report=_create_valid_inspection_report(),
    )

    with pytest.raises(ArtifactValidationError) as exc_info:
        finalize_request_artifacts(executor, initial_success, output_root, req_id)

    assert exc_info.value.error_code == "ARTIFACT_EXPORT_FAILED"
    assert not (output_root / req_id).exists()


def test_finalize_request_artifacts_ordinary_close_failure(tmp_path: Path) -> None:
    """Proves close failure blocks validation/publication and refuses recursive staging cleanup."""
    executor = FakePipelineExecutor(close_error=CADDocumentError("Close failed", error_code="DOCUMENT_CLOSE_FAILED"))
    req_id = "test-req-close-failed"
    output_root = tmp_path / "cad_output"

    initial_success = ExecutionSuccess(
        operations_executed=1,
        inspection_report=_create_valid_inspection_report(),
    )

    with pytest.raises(CADDocumentError) as exc_info:
        finalize_request_artifacts(executor, initial_success, output_root, req_id)

    assert exc_info.value.error_code == "DOCUMENT_CLOSE_FAILED"
    # Publication blocked
    assert not (output_root / req_id).exists()
    # Staging cleanup REFUSED because document closure failed (retains residue)
    staging_dirs = list(output_root.glob(".staging-*"))
    assert len(staging_dirs) == 1


def test_finalize_request_artifacts_export_failure_followed_by_close_failure(tmp_path: Path) -> None:
    """Proves export failure is preserved as primary exception even if document close also fails."""
    executor = FakePipelineExecutor(
        export_error_on="par",
        close_error=CADDocumentError("Close failed", error_code="DOCUMENT_CLOSE_FAILED"),
    )
    req_id = "test-req-double-fail"
    output_root = tmp_path / "cad_output"

    initial_success = ExecutionSuccess(
        operations_executed=1,
        inspection_report=_create_valid_inspection_report(),
    )

    with pytest.raises(CADExportError) as exc_info:
        finalize_request_artifacts(executor, initial_success, output_root, req_id)

    # Primary export error was preserved!
    assert exc_info.value.error_code == "ARTIFACT_EXPORT_FAILED"
    # Secondary close detail attached
    assert "secondary_close_error" in exc_info.value.details
    # Staging cleanup refused (residue retained)
    assert len(list(output_root.glob(".staging-*"))) == 1


def test_finalize_request_artifacts_publication_destination_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proves destination race during rename raises TARGET_ALREADY_EXISTS and leaves winner untouched."""
    executor = FakePipelineExecutor()
    req_id = "race-req-001"
    output_root = tmp_path / "cad_output"

    initial_success = ExecutionSuccess(
        operations_executed=1,
        inspection_report=_create_valid_inspection_report(),
    )

    # Intercept publish to simulate a foreign process creating final directory right before rename
    original_publish = artifacts.paths.ArtifactPaths.publish

    def _simulated_race_publish(self_paths: Any) -> None:
        self_paths.final_dir.mkdir(parents=True, exist_ok=True)
        (self_paths.final_dir / "winner.txt").write_text("winner content")
        original_publish(self_paths)

    monkeypatch.setattr(artifacts.paths.ArtifactPaths, "publish", _simulated_race_publish)

    with pytest.raises(ArtifactPathError) as exc_info:
        finalize_request_artifacts(executor, initial_success, output_root, req_id)

    assert exc_info.value.error_code == "TARGET_ALREADY_EXISTS"
    # Staging directory was cleaned up
    assert len(list(output_root.glob(".staging-*"))) == 0
    # Winner's directory is byte-for-byte untouched
    assert (output_root / req_id / "winner.txt").read_text() == "winner content"


def test_finalize_request_artifacts_wire_contract_projection(tmp_path: Path) -> None:
    """Proves internal ArtifactRecord items project cleanly to Draft 2020-12 generation-response schema."""
    executor = FakePipelineExecutor()
    req_id = "test-req-wire"
    output_root = tmp_path / "cad_output"

    initial_success = ExecutionSuccess(
        operations_executed=1,
        inspection_report=_create_valid_inspection_report(),
    )

    result = finalize_request_artifacts(executor, initial_success, output_root, req_id)

    # M4 public wire projection: project internal dataclass to schema-compliant dicts
    wire_artifacts = [
        {
            "type": r.type,
            "format": r.format,
            "path": r.path,
            "origin": r.origin,
        }
        for r in result.exported_artifacts
    ]

    response_payload = {
        "contract_version": "1.0",
        "request_id": req_id,
        "status": "accepted",
        "data": {
            "artifacts": wire_artifacts,
        },
        "warnings": [w.get("code") or w.get("warning") or "UNKNOWN_WARNING" for w in result.warnings],
    }

    schema_path = (
        Path(__file__).resolve().parents[3] / "contracts" / "schemas" / "generation" / "generation-response.schema.json"
    )
    assert schema_path.is_file()

    with open(schema_path, encoding="utf-8") as f:
        schema_dict = json.load(f)

    validator = Draft202012Validator(schema_dict)
    # Must validate cleanly without jsonschema.ValidationError
    validator.validate(response_payload)


def test_artifacts_package_reexports_pipeline_symbols() -> None:
    """Proves artifacts package re-exports finalize_request_artifacts and REQUIRED_MODEL_FORMATS."""
    assert hasattr(artifacts, "finalize_request_artifacts")
    assert hasattr(artifacts, "REQUIRED_MODEL_FORMATS")
    assert artifacts.finalize_request_artifacts is finalize_request_artifacts
    assert artifacts.REQUIRED_MODEL_FORMATS == REQUIRED_MODEL_FORMATS


def test_finalize_request_artifacts_preflight_inconsistent_total_body_count(tmp_path: Path) -> None:
    """Proves adversarial/inconsistent report with body_count=2 and solid_body_count=1 is rejected."""
    executor = FakePipelineExecutor()
    req_id = "test-req-inconsistent-bodies"
    output_root = tmp_path / "cad_output"

    report = StandardInspectionReport(
        volume_mm3=1000.0,
        mass_kg=1.0,
        feature_count=1,
        body_count=2,  # Inconsistent: 2 total bodies even though solid=1, sheet=0, wire=0
        solid_body_count=1,
        sheet_body_count=0,
        wire_body_count=0,
    )
    initial_success = ExecutionSuccess(
        operations_executed=1,
        inspection_report=report,
    )

    with pytest.raises(CADExecutionError) as exc_info:
        finalize_request_artifacts(executor, initial_success, output_root, req_id)

    assert exc_info.value.error_code == "NATIVE_QA_BLOCKED"
    assert "requires exactly 1 total body" in str(exc_info.value)
    assert executor.call_log == ["close_request_document"]
    assert not (output_root / req_id).exists()


def test_finalize_request_artifacts_preview_security_error_remains_fatal(tmp_path: Path) -> None:
    """Proves OUTPUT_PATH_NOT_ALLOWED during preview capture is fatal and not downgraded to warning."""
    executor = FakePipelineExecutor(
        preview_error=CADExportError("Unsafe output path target", error_code="OUTPUT_PATH_NOT_ALLOWED")
    )
    req_id = "test-req-preview-sec"
    output_root = tmp_path / "cad_output"

    initial_success = ExecutionSuccess(
        operations_executed=1,
        inspection_report=_create_valid_inspection_report(),
    )

    with pytest.raises(CADExportError) as exc_info:
        finalize_request_artifacts(executor, initial_success, output_root, req_id)

    assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
    # Terminal close was called
    assert "close_request_document" in executor.call_log
    # Publication blocked
    assert not (output_root / req_id).exists()
    # Staging cleaned up
    assert len(list(output_root.glob(".staging-*"))) == 0


def test_finalize_request_artifacts_close_failure_normalization_and_retained_staging(tmp_path: Path) -> None:
    """Proves a raw non-CADError during terminal close is normalized to CADDocumentError with retained residue."""
    executor = FakePipelineExecutor(close_error=RuntimeError("Kernel RPC severed during close"))
    req_id = "test-req-close-norm"
    output_root = tmp_path / "cad_output"

    initial_success = ExecutionSuccess(
        operations_executed=1,
        inspection_report=_create_valid_inspection_report(),
    )

    with pytest.raises(CADDocumentError) as exc_info:
        finalize_request_artifacts(executor, initial_success, output_root, req_id)

    assert exc_info.value.error_code == "DOCUMENT_CLOSE_FAILED"
    assert exc_info.value.details.get("retained_staging") is True
    # Staging left behind
    assert len(list(output_root.glob(".staging-*"))) == 1
    assert not (output_root / req_id).exists()


def test_finalize_request_artifacts_cleanup_failure_reporting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Proves staging cleanup failure is captured in primary_exc.details and does not claim clean rollback."""
    executor = FakePipelineExecutor(export_error_on="par")
    req_id = "test-req-cleanup-fail"
    output_root = tmp_path / "cad_output"

    initial_success = ExecutionSuccess(
        operations_executed=1,
        inspection_report=_create_valid_inspection_report(),
    )

    def _failing_cleanup(self: Any, document_closed: bool = True) -> None:
        raise OSError("Permission denied removing staging directory")

    monkeypatch.setattr(artifacts.paths.ArtifactPaths, "cleanup_staging", _failing_cleanup)

    with pytest.raises(CADExportError) as exc_info:
        finalize_request_artifacts(executor, initial_success, output_root, req_id)

    assert exc_info.value.error_code == "ARTIFACT_EXPORT_FAILED"
    assert "cleanup_error" in exc_info.value.details
    assert exc_info.value.details.get("retained_staging") is True


def test_finalize_request_artifacts_fail_fast_stat_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Proves OSError during post-export stat check is normalized to ARTIFACT_EXPORT_FAILED with sanitized details."""
    executor = FakePipelineExecutor()
    req_id = "test-req-stat-err"
    output_root = tmp_path / "cad_output"

    initial_success = ExecutionSuccess(
        operations_executed=1,
        inspection_report=_create_valid_inspection_report(),
    )

    orig_stat = Path.stat

    def _mock_stat(self: Path, **kwargs: Any) -> Any:
        if self.name.endswith(".par"):
            raise PermissionError("Access denied by file lock")
        return orig_stat(self, **kwargs)

    monkeypatch.setattr(Path, "stat", _mock_stat)

    with pytest.raises(CADExportError) as exc_info:
        finalize_request_artifacts(executor, initial_success, output_root, req_id)

    assert exc_info.value.error_code == "ARTIFACT_EXPORT_FAILED"
    assert exc_info.value.details.get("format") == "par"
    assert "inaccessible file" in str(exc_info.value)
    # Staging was cleaned up
    assert len(list(output_root.glob(".staging-*"))) == 0


def test_finalize_request_artifacts_export_timeout_followed_by_close_failure(tmp_path: Path) -> None:
    """Proves non-domain TimeoutError during export combined with close failure normalizes to CAD_EXECUTION_FAILED."""
    executor = FakePipelineExecutor(
        export_error_on="par",
        close_error=CADDocumentError("Close failed", error_code="DOCUMENT_CLOSE_FAILED"),
    )
    executor.export_error_on = None

    original_export_model = executor.export_model

    def _timeout_export(format_id: ArtifactFormat, output_path: Path) -> None:
        if format_id == "par":
            raise TimeoutError("Kernel IPC timed out during PAR export")
        original_export_model(format_id, output_path)

    executor.export_model = _timeout_export  # type: ignore[assignment]

    req_id = "test-req-timeout-close-fail"
    output_root = tmp_path / "cad_output"

    initial_success = ExecutionSuccess(
        operations_executed=1,
        inspection_report=_create_valid_inspection_report(),
    )

    with pytest.raises(CADExecutionError) as exc_info:
        finalize_request_artifacts(executor, initial_success, output_root, req_id)

    # Primary error normalized to CAD_EXECUTION_FAILED
    assert exc_info.value.error_code == "CAD_EXECUTION_FAILED"
    # Secondary close detail attached
    assert "secondary_close_error" in exc_info.value.details
    assert "Document release failed" in exc_info.value.details["secondary_close_error"]
    # Staging cleanup refused (residue retained)
    assert exc_info.value.details.get("retained_staging") is True
    assert len(list(output_root.glob(".staging-*"))) == 1
    assert not (output_root / req_id).exists()


def test_finalize_request_artifacts_post_release_validation_failure_with_cleanup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proves post-release validation failure combined with cleanup failure preserves primary error with cleanup details."""
    executor = FakePipelineExecutor()
    req_id = "test-req-val-cleanup-fail"
    output_root = tmp_path / "cad_output"

    initial_success = ExecutionSuccess(
        operations_executed=1,
        inspection_report=_create_valid_inspection_report(),
    )

    def _failing_validate(
        format_id: Any,
        file_path: Path,
        inspection_report: Any = None,
    ) -> tuple[str, int]:
        if format_id == "step":
            raise ArtifactValidationError("Corrupt STEP entity stream detected", error_code="ARTIFACT_EXPORT_FAILED")
        return ("0" * 64, 1024)

    def _failing_cleanup(self: Any, document_closed: bool = True) -> None:
        raise OSError("Permission denied cleaning staging directory after validation failure")

    monkeypatch.setattr("artifacts.pipeline.validate_artifact_file", _failing_validate)
    monkeypatch.setattr(artifacts.paths.ArtifactPaths, "cleanup_staging", _failing_cleanup)

    with pytest.raises(ArtifactValidationError) as exc_info:
        finalize_request_artifacts(executor, initial_success, output_root, req_id)

    assert exc_info.value.error_code == "ARTIFACT_EXPORT_FAILED"
    assert "cleanup_error" in exc_info.value.details
    assert exc_info.value.details.get("retained_staging") is True
    assert not (output_root / req_id).exists()


def test_finalize_request_artifacts_cleanup_failure_with_absent_jpg_permits_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proves cleanup warning is allowed when JPG is verified absent, publishing required formats."""
    executor = FakePipelineExecutor()
    req_id = "test-req-preview-absent-ok"
    output_root = tmp_path / "cad_output"

    initial_success = ExecutionSuccess(
        operations_executed=1,
        inspection_report=_create_valid_inspection_report(),
    )

    # Invalidate preview validation
    def _corrupt_jpg_validate(
        format_id: Any,
        file_path: Path,
        inspection_report: Any = None,
    ) -> ValidatedArtifact:
        if format_id == "jpg":
            raise ArtifactValidationError("Malformed preview JPEG", error_code="PREVIEW_EXPORT_FAILED")
        snap = FileSnapshot.capture(file_path)
        return ValidatedArtifact("0" * 64, snap.st_size, snap)

    # Cleanup unlinks the file but still raises a secondary failure
    def _unlinking_failing_cleanup(self: Any, document_closed: bool = True) -> None:
        if self.staging_jpg.exists():
            self.staging_jpg.unlink()
        raise OSError("Secondary filesystem error after partial preview unlink")

    monkeypatch.setattr("artifacts.pipeline.validate_artifact_file", _corrupt_jpg_validate)
    monkeypatch.setattr(artifacts.paths.ArtifactPaths, "cleanup_partial_preview", _unlinking_failing_cleanup)

    result = finalize_request_artifacts(executor, initial_success, output_root, req_id)

    # Required models are published
    assert isinstance(result, ExecutionSuccess)
    final_dir = output_root / req_id
    assert final_dir.is_dir()
    assert (final_dir / f"{req_id}.par").is_file()
    assert (final_dir / f"{req_id}.step").is_file()
    assert (final_dir / f"{req_id}.stl").is_file()
    assert not (final_dir / f"{req_id}.jpg").exists()
    assert not any(a.format == "jpg" for a in result.exported_artifacts)

    # Both preview validation failure and cleanup failure warnings are present
    warning_codes = [w.get("code") for w in result.warnings]
    assert "PREVIEW_EXPORT_FAILED" in warning_codes
    assert "PREVIEW_CLEANUP_FAILED" in warning_codes


def test_finalize_request_artifacts_cleanup_failure_leaving_rejected_jpg_blocks_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proves cleanup failure leaving rejected JPG present blocks publication and produces no final directory."""
    executor = FakePipelineExecutor()
    req_id = "test-req-preview-retained-blocks"
    output_root = tmp_path / "cad_output"

    initial_success = ExecutionSuccess(
        operations_executed=1,
        inspection_report=_create_valid_inspection_report(),
    )

    def _corrupt_jpg_validate(
        format_id: Any,
        file_path: Path,
        inspection_report: Any = None,
    ) -> ValidatedArtifact:
        if format_id == "jpg":
            raise ArtifactValidationError("Malformed preview JPEG", error_code="PREVIEW_EXPORT_FAILED")
        snap = FileSnapshot.capture(file_path)
        return ValidatedArtifact("0" * 64, snap.st_size, snap)

    def _failing_cleanup_retaining_file(self: Any, document_closed: bool = True) -> None:
        # Fails without deleting the file
        raise OSError("Permission denied unlinking preview")

    monkeypatch.setattr("artifacts.pipeline.validate_artifact_file", _corrupt_jpg_validate)
    monkeypatch.setattr(artifacts.paths.ArtifactPaths, "cleanup_partial_preview", _failing_cleanup_retaining_file)

    with pytest.raises(CADExportError) as exc_info:
        finalize_request_artifacts(executor, initial_success, output_root, req_id)

    assert exc_info.value.error_code == "ARTIFACT_EXPORT_FAILED"
    assert "could not be safely removed from staging" in str(exc_info.value)
    # Publication blocked: no final directory exists
    final_dir = output_root / req_id
    assert not final_dir.exists()


def test_finalize_request_artifacts_unexpected_staging_file_blocks_publication(tmp_path: Path) -> None:
    """Proves unexpected file entry in staging directory blocks publication via inventory gate."""
    executor = FakePipelineExecutor()
    req_id = "test-req-unexpected-file"
    output_root = tmp_path / "cad_output"

    initial_success = ExecutionSuccess(
        operations_executed=1,
        inspection_report=_create_valid_inspection_report(),
    )

    orig_export = executor.export_model

    def _export_with_rogue_file(format_id: ArtifactFormat, output_path: Path) -> None:
        orig_export(format_id, output_path)
        if format_id == "stl":
            rogue_file = output_path.parent / "auxiliary_rogue.log"
            rogue_file.write_text("rogue exporter residue", encoding="utf-8")

    executor.export_model = _export_with_rogue_file  # type: ignore[method-assign]

    with pytest.raises(CADExportError) as exc_info:
        finalize_request_artifacts(executor, initial_success, output_root, req_id)

    assert exc_info.value.error_code == "ARTIFACT_EXPORT_FAILED"
    assert "Unexpected entry in staging directory prior to publication" in str(exc_info.value)
    assert not (output_root / req_id).exists()


def test_finalize_request_artifacts_unexpected_staging_subdir_blocks_publication(tmp_path: Path) -> None:
    """Proves unexpected subdirectory in staging directory blocks publication via inventory gate."""
    executor = FakePipelineExecutor()
    req_id = "test-req-unexpected-subdir"
    output_root = tmp_path / "cad_output"

    initial_success = ExecutionSuccess(
        operations_executed=1,
        inspection_report=_create_valid_inspection_report(),
    )

    orig_export = executor.export_model

    def _export_with_rogue_dir(format_id: ArtifactFormat, output_path: Path) -> None:
        orig_export(format_id, output_path)
        if format_id == "stl":
            rogue_dir = output_path.parent / "subfolder"
            rogue_dir.mkdir()

    executor.export_model = _export_with_rogue_dir  # type: ignore[method-assign]

    with pytest.raises(CADExportError) as exc_info:
        finalize_request_artifacts(executor, initial_success, output_root, req_id)

    assert exc_info.value.error_code == "ARTIFACT_EXPORT_FAILED"
    assert "Unexpected entry in staging directory prior to publication" in str(exc_info.value)
    assert not (output_root / req_id).exists()


def test_finalize_request_artifacts_metadata_mutation_prior_to_publication_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proves file metadata mutation between validation and publication is rejected by final gate."""
    executor = FakePipelineExecutor()
    req_id = "test-req-mutation-rejected"
    output_root = tmp_path / "cad_output"

    initial_success = ExecutionSuccess(
        operations_executed=1,
        inspection_report=_create_valid_inspection_report(),
    )

    orig_val = artifacts.pipeline.validate_artifact_file

    def _val_with_mutation(
        format_id: Any,
        file_path: Path,
        inspection_report: Any = None,
    ) -> ValidatedArtifact:
        res = orig_val(format_id, file_path, inspection_report)
        if format_id == "stl":
            # Tamper with the previously accepted .par file
            par_file = file_path.parent / f"{req_id}.par"
            with open(par_file, "ab") as f:
                f.write(b"TAMPERED_TRAILING_BYTES")
        return res

    monkeypatch.setattr("artifacts.pipeline.validate_artifact_file", _val_with_mutation)

    with pytest.raises(CADExportError) as exc_info:
        finalize_request_artifacts(executor, initial_success, output_root, req_id)

    assert exc_info.value.error_code == "ARTIFACT_EXPORT_FAILED"
    assert "Artifact file modified prior to publication" in str(exc_info.value)
    assert not (output_root / req_id).exists()


def test_finalize_request_artifacts_accepted_inventory_matches_returned_records_exactly(tmp_path: Path) -> None:
    """Proves published inventory matches returned record formats and contains zero auxiliary files."""
    executor = FakePipelineExecutor()
    req_id = "test-req-exact-inventory"
    output_root = tmp_path / "cad_output"

    initial_success = ExecutionSuccess(
        operations_executed=1,
        inspection_report=_create_valid_inspection_report(),
    )

    result = finalize_request_artifacts(executor, initial_success, output_root, req_id)
    assert isinstance(result, ExecutionSuccess)

    final_dir = output_root / req_id
    assert final_dir.is_dir()

    disk_files = {p.name for p in final_dir.iterdir()}
    record_files = {Path(r.path).name for r in result.exported_artifacts}

    assert disk_files == record_files
    assert disk_files == {
        f"{req_id}.par",
        f"{req_id}.step",
        f"{req_id}.stl",
        f"{req_id}.jpg",
    }


def test_finalize_request_artifacts_cleans_up_auxiliary_translation_log(tmp_path: Path) -> None:
    """Proves Solid Edge translator auxiliary log (<request-id>.log) is cleaned up and not published."""

    class LogGeneratingExecutor(FakePipelineExecutor):
        def export_model(self, format_id: str, output_path: Path) -> None:
            super().export_model(format_id, output_path)
            if format_id == "step":
                # Simulate Solid Edge STEP translator generating an auxiliary translation log
                log_file = output_path.with_suffix(".log")
                log_file.write_text("Solid Edge Translation to STEP\nComplete\n", encoding="utf-8")

    executor = LogGeneratingExecutor()
    req_id = "test-req-aux-log"
    output_root = tmp_path / "cad_output"

    initial_success = ExecutionSuccess(
        operations_executed=1,
        inspection_report=_create_valid_inspection_report(),
    )

    result = finalize_request_artifacts(executor, initial_success, output_root, req_id)
    assert isinstance(result, ExecutionSuccess)

    final_dir = output_root / req_id
    assert final_dir.is_dir()
    assert not (final_dir / f"{req_id}.log").exists()
    assert {p.name for p in final_dir.iterdir()} == {
        f"{req_id}.par",
        f"{req_id}.step",
        f"{req_id}.stl",
        f"{req_id}.jpg",
    }
