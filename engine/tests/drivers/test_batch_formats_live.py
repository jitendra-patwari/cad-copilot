"""Controlled live integration tests against Siemens Solid Edge 2026 installation.

These tests execute ONLY when running with `pytest -m com` on a Windows host with
a licensed Siemens Solid Edge installation.

Tests:
- B-LIVE-M54-01: Native 3D model export (.par, .psm, .asm to STEP and STL)
- B-LIVE-M54-02: 2D drawing publication (.dft to PDF and DXF with in-memory view refresh)
- B-LIVE-M54-03: Unresolved assembly isolation and fail-closed handling
- B-LIVE-M54-04: Collision and partial result with sentinel preservation
- B-LIVE-M54-05: Cooperative cancellation and workspace cleanup
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

from batch.composition import build_initial_operation_bindings
from batch.execution import BatchExecutionSpec
from batch.format_validation import validate_batch_output
from batch.safety import FilesystemBatchSafetyBoundary
from batch.service import BatchService
from batch.source_integrity import capture_source_snapshot, verify_snapshot_equality
from drivers.solidedge import SolidEdgeRuntime

pytestmark = [pytest.mark.com]


@pytest.fixture
def live_runtime() -> Generator[SolidEdgeRuntime]:
    """Provide a managed SolidEdgeRuntime with verified health and guaranteed teardown."""
    if sys.platform != "win32":
        pytest.skip("Live Solid Edge COM tests require Windows platform")

    try:
        import pythoncom  # noqa: F401
        import win32com.client  # noqa: F401
    except ImportError:
        pytest.skip("pywin32 is not installed in the current Python environment")

    runtime = SolidEdgeRuntime()
    try:
        runtime.connect_application()
    except Exception as exc:
        pytest.skip(f"Live Solid Edge application is not available: {exc}")

    try:
        yield runtime
    finally:
        assert runtime.teardown(force_kill_on_failure=True) is True


def _copy_fixtures(dest_dir: Path) -> Path:
    """Copy ignored live fixtures to a disposable temporary directory."""
    fixtures_dir = Path(__file__).resolve().parent.parent / "live_fixtures"
    if not fixtures_dir.is_dir():
        pytest.skip(f"Live fixtures directory not found: {fixtures_dir}")

    dest_dir.mkdir(parents=True, exist_ok=True)
    for f in fixtures_dir.iterdir():
        if f.is_file():
            shutil.copy2(f, dest_dir / f.name)
    return fixtures_dir


def _make_spec(
    input_root: Path,
    output_root: Path,
    inputs: tuple[str, ...],
    operation_id: str,
    formats: tuple[str, ...],
    request_id: str = "req-live-m54",
    continue_on_error: bool = True,
) -> BatchExecutionSpec:
    return BatchExecutionSpec(
        contract_version="1.0",
        request_id=request_id,
        input_root=str(input_root),
        output_root=str(output_root),
        inputs=inputs,
        operation_id=operation_id,
        formats=formats,
        continue_on_error=continue_on_error,
    )


class TestBatchFormatsLive:
    """Live verification matrix for Milestone 5.4 format handlers on Solid Edge 2026."""

    def test_live_m54_01_native_3d_matrix(self, live_runtime: SolidEdgeRuntime, tmp_path: Path) -> None:
        """B-LIVE-M54-01: Native 3D model export (.par, .psm, .asm to STEP and STL)."""
        input_root = tmp_path / "inputs"
        output_root = tmp_path / "outputs"
        output_root.mkdir()
        _copy_fixtures(input_root)

        required_sources = ("Bed.par", "SE_Transition_RR.psm", "carrier.asm")
        for name in required_sources:
            if not (input_root / name).is_file():
                pytest.skip(f"Required 3D fixture '{name}' not found in live_fixtures")

        inputs_to_test = list(required_sources)

        # Capture pre-run source snapshots
        pre_snapshots = {name: capture_source_snapshot(input_root / name) for name in inputs_to_test}

        boundary = FilesystemBatchSafetyBoundary()
        bindings = build_initial_operation_bindings(boundary)
        service = BatchService(
            runtime_factory=lambda: live_runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = _make_spec(
            input_root=input_root,
            output_root=output_root,
            inputs=tuple(inputs_to_test),
            operation_id="export_3d",
            formats=("step", "stl"),
        )

        outcome = service.execute(spec)

        assert outcome.status == "completed"
        assert outcome.summary is not None
        assert outcome.summary.total == len(inputs_to_test)
        assert outcome.summary.accepted == len(inputs_to_test)
        assert outcome.summary.failed == 0

        # Invariant: exact artifact order [step, stl] for every input
        for res in outcome.file_results:
            assert [a.format for a in res.artifacts] == ["step", "stl"]

        # Validate outputs and sidecar cleanup
        for name in inputs_to_test:
            stem = Path(name).stem
            step_file = output_root / f"{stem}.step"
            stl_file = output_root / f"{stem}.stl"
            log_file = output_root / f"{stem}.log"

            assert step_file.is_file()
            assert stl_file.is_file()
            # Invariant: translator log sidecar cleaned up by driver
            assert not log_file.exists()

            # Validate closed output structures
            snap_step = validate_batch_output("step", step_file)
            assert snap_step.size_bytes > 0

            snap_stl = validate_batch_output("stl", stl_file)
            assert snap_stl.size_bytes > 0

            # Verify cryptographic source immutability
            post_snapshot = capture_source_snapshot(input_root / name)
            assert verify_snapshot_equality(pre_snapshots[name], post_snapshot)

    def test_live_m54_02_drawing_publication_matrix(self, live_runtime: SolidEdgeRuntime, tmp_path: Path) -> None:
        """B-LIVE-M54-02: 2D drawing publication (.dft to PDF and DXF with in-memory view refresh)."""
        input_root = tmp_path / "inputs"
        output_root = tmp_path / "outputs"
        output_root.mkdir()
        _copy_fixtures(input_root)

        draft_file = input_root / "Bed.dft"
        if not draft_file.is_file():
            pytest.skip("Required draft fixture 'Bed.dft' not found in live_fixtures")

        pre_snapshot = capture_source_snapshot(draft_file)

        boundary = FilesystemBatchSafetyBoundary()
        bindings = build_initial_operation_bindings(boundary)
        service = BatchService(
            runtime_factory=lambda: live_runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = _make_spec(
            input_root=input_root,
            output_root=output_root,
            inputs=("Bed.dft",),
            operation_id="publish_drawing",
            formats=("pdf", "dxf"),
        )

        outcome = service.execute(spec)

        assert outcome.status == "completed"
        assert outcome.summary is not None
        assert outcome.summary.accepted == 1

        pdf_file = output_root / "Bed.pdf"
        dxf_file = output_root / "Bed.dxf"

        assert pdf_file.is_file()
        assert dxf_file.is_file()

        # Validate closed output structures
        snap_pdf = validate_batch_output("pdf", pdf_file)
        assert snap_pdf.size_bytes > 0

        snap_dxf = validate_batch_output("dxf", dxf_file)
        assert snap_dxf.size_bytes > 0

        # Cryptographic source immutability: zero save prompts, no changes to source bytes
        post_snapshot = capture_source_snapshot(draft_file)
        assert verify_snapshot_equality(pre_snapshot, post_snapshot)

    def test_live_m54_03_unresolved_assembly_isolation(self, live_runtime: SolidEdgeRuntime, tmp_path: Path) -> None:
        """B-LIVE-M54-03: Unresolved assembly isolation and fail-closed handling."""
        input_root = tmp_path / "inputs"
        output_root = tmp_path / "outputs"
        output_root.mkdir()
        _copy_fixtures(input_root)

        unresolved_asm = input_root / "unresolved_carrier.asm"
        if not unresolved_asm.is_file():
            pytest.skip("Dedicated unresolved assembly fixture 'unresolved_carrier.asm' not found in live_fixtures")

        healthy_par = input_root / "Bed.par"
        if not healthy_par.is_file():
            pytest.skip("Healthy fixture 'Bed.par' not found")

        boundary = FilesystemBatchSafetyBoundary()
        bindings = build_initial_operation_bindings(boundary)
        service = BatchService(
            runtime_factory=lambda: live_runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = _make_spec(
            input_root=input_root,
            output_root=output_root,
            inputs=("unresolved_carrier.asm", "Bed.par"),
            operation_id="export_3d",
            formats=("step", "stl"),
            continue_on_error=True,
        )

        outcome = service.execute(spec)

        assert outcome.status == "completed"
        assert outcome.summary is not None
        assert outcome.summary.accepted == 1
        assert outcome.summary.failed == 1

        # Unresolved assembly failed closed: no artifacts produced
        assert not (output_root / "unresolved_carrier.step").exists()
        assert not (output_root / "unresolved_carrier.stl").exists()

        # Later healthy part succeeded
        assert (output_root / "Bed.step").is_file()
        assert (output_root / "Bed.stl").is_file()

        # Sanitize check: diagnostic must not leak component filenames or absolute paths
        unresolved_res = outcome.file_results[0]
        assert unresolved_res.status == "failed"
        assert any(e.code == "ARTIFACT_EXPORT_FAILED" for e in unresolved_res.errors)
        for err in unresolved_res.errors:
            assert "C:\\" not in err.message and "E:\\" not in err.message
            assert "temp_missing_part" not in err.message
            assert "chead.par" not in err.message

    def test_live_m54_04_collision_and_partial_result(self, live_runtime: SolidEdgeRuntime, tmp_path: Path) -> None:
        """B-LIVE-M54-04: Collision and partial result with sentinel preservation."""
        input_root = tmp_path / "inputs"
        output_root = tmp_path / "outputs"
        output_root.mkdir()
        _copy_fixtures(input_root)

        part_file = input_root / "Bed.par"
        if not part_file.is_file():
            pytest.skip("Required fixture 'Bed.par' not found in live_fixtures")

        # Pre-create target with sentinel content
        existing_step = output_root / "Bed.step"
        sentinel_bytes = b"LIVE PRE-EXISTING SENTINEL CONTENT"
        existing_step.write_bytes(sentinel_bytes)

        boundary = FilesystemBatchSafetyBoundary()
        bindings = build_initial_operation_bindings(boundary)
        service = BatchService(
            runtime_factory=lambda: live_runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = _make_spec(
            input_root=input_root,
            output_root=output_root,
            inputs=("Bed.par",),
            operation_id="export_3d",
            formats=("step", "stl"),
        )

        outcome = service.execute(spec)

        # Invariant: existing target preserved untouched
        assert existing_step.read_bytes() == sentinel_bytes

        # Summary accounting
        assert outcome.status == "completed"
        assert outcome.summary is not None
        assert outcome.summary.total == 1
        assert outcome.summary.partial == 1
        assert outcome.summary.accepted == 0
        assert outcome.summary.failed == 0

        # STL was exported successfully, STEP was blocked by pre-existing collision
        res = outcome.file_results[0]
        assert res.status == "partial"
        assert any(e.code == "TARGET_ALREADY_EXISTS" for e in res.errors)
        assert len(res.artifacts) == 1
        assert res.artifacts[0].format == "stl"

        stl_file = output_root / "Bed.stl"
        assert stl_file.is_file()
        snap_stl = validate_batch_output("stl", stl_file)
        assert snap_stl.size_bytes > 0

        # Invariant: no residual temporary work directories
        work_dirs = list(output_root.glob(".cad-copilot-work-*"))
        assert len(work_dirs) == 0

    def test_live_m54_05_cancellation_cleanup(self, live_runtime: SolidEdgeRuntime, tmp_path: Path) -> None:
        """B-LIVE-M54-05: Cooperative cancellation and workspace cleanup."""
        input_root = tmp_path / "inputs"
        output_root = tmp_path / "outputs"
        output_root.mkdir()
        _copy_fixtures(input_root)

        part_file = input_root / "Bed.par"
        if not part_file.is_file():
            pytest.skip("Required fixture 'Bed.par' not found in live_fixtures")

        # Capture pre-run source snapshot
        pre_snapshot = capture_source_snapshot(part_file)

        cancelled = False

        def _cancel_after_first_format(update: Any) -> None:
            nonlocal cancelled
            if getattr(update, "phase", None) == "format_finished":
                cancelled = True

        boundary = FilesystemBatchSafetyBoundary()
        bindings = build_initial_operation_bindings(boundary)
        service = BatchService(
            runtime_factory=lambda: live_runtime,
            safety_boundary=boundary,
            bindings=bindings,
            cancellation_check=lambda: cancelled,
            observer=_cancel_after_first_format,
        )

        spec = _make_spec(
            input_root=input_root,
            output_root=output_root,
            inputs=("Bed.par",),
            operation_id="export_3d",
            formats=("step", "stl"),
        )

        outcome = service.execute(spec)
        assert outcome.status == "cancelled"

        # Summary and cancelled accounting
        assert outcome.summary is not None
        assert outcome.summary.total == 1
        assert outcome.summary.partial == 1
        assert outcome.summary.accepted == 0
        assert outcome.summary.failed == 0
        assert outcome.summary.cancelled == 0
        assert outcome.cancelled_files == ()

        # Partial file result accounting
        assert len(outcome.file_results) == 1
        res = outcome.file_results[0]
        assert res.status == "partial"
        assert len(res.artifacts) == 1
        assert res.artifacts[0].format == "step"
        assert any(e.code == "BATCH_CANCELLED" and e.format == "stl" for e in res.errors)

        # First format (step) was published before cancellation
        step_file = output_root / "Bed.step"
        assert step_file.is_file()
        snap_step = validate_batch_output("step", step_file)
        assert snap_step.size_bytes > 0

        # Second format (stl) was cancelled
        assert not (output_root / "Bed.stl").exists()

        # No dangling temporary work directories remain
        work_dirs = list(output_root.glob(".cad-copilot-work-*"))
        assert len(work_dirs) == 0

        # Cryptographic source immutability
        post_snapshot = capture_source_snapshot(part_file)
        assert verify_snapshot_equality(pre_snapshot, post_snapshot)
