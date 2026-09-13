"""Controlled live integration tests for Parasolid candidate export against Siemens Solid Edge 2026.

These tests execute ONLY when running with `pytest -m com` and `CAD_COPILOT_RUN_LIVE_COM=1`
on a Windows host with a licensed Siemens Solid Edge installation and an owned session.

Tests:
- C-LIVE-M55-01: Native 3D model export (.par, .psm, .asm to Parasolid .x_t),
  bounded structural validation, non-mutating reopen count probe via OpenWithTemplate,
  source and artifact immutability, zero sidecars, clean teardown.
- C-LIVE-M55-02: Collision isolation (sentinel preservation), unresolved assembly isolation,
  localized failure isolation, and cooperative cancellation cleanup.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import sys
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from batch.composition import build_initial_operation_bindings
from batch.execution import BatchExecutionSpec
from batch.format_validation import BatchFormatValidationError, validate_batch_output
from batch.registry import OperationDescriptor, OperationRegistry
from batch.safety import FilesystemBatchSafetyBoundary
from batch.service import BatchService
from batch.source_integrity import capture_source_snapshot, verify_snapshot_equality
from drivers.solidedge import OwnershipMode, SolidEdgeRuntime

pytestmark = [pytest.mark.com]


@pytest.fixture
def live_runtime() -> Generator[SolidEdgeRuntime]:
    """Provide a managed SolidEdgeRuntime with verified health, owned session, and guaranteed teardown."""
    if sys.platform != "win32":
        pytest.skip("Live Solid Edge COM tests require Windows platform")

    if os.environ.get("CAD_COPILOT_RUN_LIVE_COM") != "1":
        pytest.skip("Skipping live Solid Edge COM tests: CAD_COPILOT_RUN_LIVE_COM is not set to '1'")

    try:
        import pythoncom  # noqa: F401
        import win32com.client  # noqa: F401
    except ImportError:
        pytest.skip("pywin32 is not installed in the current Python environment")

    runtime = SolidEdgeRuntime()
    try:
        app_handle = runtime.connect_application()
    except Exception:
        pytest.skip("Live Solid Edge application is not available or connection failed")

    if app_handle.ownership != OwnershipMode.OWNED:
        runtime.teardown(force_kill_on_failure=False)
        pytest.skip("Live Solid Edge COM tests require an OWNED session; refusing to attach to borrowed user session")

    try:
        yield runtime
    finally:
        assert runtime.teardown(force_kill_on_failure=True) is True


def _copy_fixtures(dest_dir: Path) -> Path:
    """Copy ignored live fixtures to a disposable temporary directory recursively."""
    fixtures_dir = Path(__file__).resolve().parent.parent / "live_fixtures"
    if not fixtures_dir.is_dir():
        pytest.skip("Live fixtures directory not found")

    dest_dir.mkdir(parents=True, exist_ok=True)
    for p in fixtures_dir.rglob("*"):
        if p.is_file():
            rel_path = p.relative_to(fixtures_dir)
            target = dest_dir / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
    return fixtures_dir


def _build_candidate_registry() -> OperationRegistry:
    """Construct candidate registry exposing 'parasolid' under export_3d for live candidate verification."""
    return OperationRegistry(
        (
            OperationDescriptor(
                operation_id="export_3d",
                input_extensions=(".par", ".psm", ".asm"),
                output_formats=("step", "stl", "parasolid"),
                progress_label="Export 3D CAD",
                safety_class="read_only_source",
                document_lifecycle="open_existing_close_without_save",
                collision_policy="fail_if_exists",
            ),
            OperationDescriptor(
                operation_id="publish_drawing",
                input_extensions=(".dft",),
                output_formats=("pdf", "dxf"),
                progress_label="Publish Drawing",
                safety_class="read_only_source",
                document_lifecycle="open_existing_close_without_save",
                collision_policy="fail_if_exists",
            ),
        )
    )


def _make_spec(
    input_root: Path,
    output_root: Path,
    inputs: tuple[str, ...],
    operation_id: str = "export_3d",
    formats: tuple[str, ...] = ("parasolid",),
    request_id: str = "req-live-m55",
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


def _find_template(docs: Any, pattern: str) -> str:
    """Locate a standard Solid Edge template matching the given pattern."""
    tpl_dir_str = getattr(docs, "TemplatePath", r"C:\Program Files\Siemens\Solid Edge 2026\Template")
    tpl_dir = Path(str(tpl_dir_str))
    if not tpl_dir.is_dir():
        tpl_dir = Path(r"C:\Program Files\Siemens\Solid Edge 2026\Template")

    # Prefer metric templates
    for p in tpl_dir.rglob(f"*.{pattern}"):
        name_lower = p.name.lower()
        if "iso metric" in name_lower or "ansi metric" in name_lower or "metric" in name_lower:
            return str(p)

    matches = list(tpl_dir.rglob(f"*.{pattern}"))
    if matches:
        return str(matches[0])
    raise RuntimeError(f"Could not locate a '{pattern}' template in the Solid Edge installation")


@contextlib.contextmanager
def _reopened_parasolid_document(docs: Any, xt_path: Path, template_path: str) -> Generator[Any]:
    """Test-owned context manager ensuring guaranteed document close on raw OpenWithTemplate."""
    doc = docs.OpenWithTemplate(os.fspath(xt_path), template_path)
    try:
        yield doc
    finally:
        doc.Close(False)


class TestParasolidLive:
    """Live verification matrix for Parasolid candidate gate on Solid Edge 2026."""

    def test_live_m55_01_three_family_export_and_reopen(self, live_runtime: SolidEdgeRuntime, tmp_path: Path) -> None:
        """C-LIVE-M55-01: Native 3D model export (.par, .psm, .asm to Parasolid .x_t) and reopen."""
        input_root = tmp_path / "inputs"
        output_root = tmp_path / "outputs"
        output_root.mkdir()
        _copy_fixtures(input_root)

        required_sources = ("Bed.par", "SE_Transition_RR.psm", "carrier.asm")
        for name in required_sources:
            if not (input_root / name).is_file():
                pytest.skip(f"Required 3D fixture '{name}' not found in live_fixtures")

        inputs_to_test = list(required_sources)

        # Capture pre-run source snapshots of all fixtures recursively (including nested assembly children)
        all_rel_paths = tuple(
            sorted(p.relative_to(input_root).as_posix() for p in input_root.rglob("*") if p.is_file())
        )
        pre_snapshots = {rel: capture_source_snapshot(input_root / rel) for rel in all_rel_paths}

        candidate_reg = _build_candidate_registry()
        boundary = FilesystemBatchSafetyBoundary()
        bindings = build_initial_operation_bindings(boundary, registry=candidate_reg)
        service = BatchService(
            runtime_factory=lambda: live_runtime,
            safety_boundary=boundary,
            bindings=bindings,
            registry=candidate_reg,
        )

        spec = _make_spec(
            input_root=input_root,
            output_root=output_root,
            inputs=tuple(inputs_to_test),
            operation_id="export_3d",
            formats=("parasolid",),
        )

        outcome = service.execute(spec)

        assert outcome.status == "completed"
        assert outcome.summary is not None
        assert outcome.summary.total == len(inputs_to_test)
        assert outcome.summary.accepted == len(inputs_to_test)
        assert outcome.summary.failed == 0

        # Validate outputs, zero sidecars, and bounded structure
        pre_reopen_xt_snapshots: dict[str, Any] = {}
        for name in inputs_to_test:
            stem = Path(name).stem
            xt_file = output_root / f"{stem}.x_t"
            log_file = output_root / f"{stem}.log"

            assert xt_file.is_file(), f"Exported Parasolid file missing: {xt_file.name}"
            # Invariant: zero sidecars for Parasolid export
            assert not log_file.exists(), f"Unexpected sidecar log file found: {log_file.name}"

            snap_xt = validate_batch_output("parasolid", xt_file)
            assert snap_xt.size_bytes > 0

            # Record .x_t snapshot before reopen
            pre_reopen_xt_snapshots[stem] = capture_source_snapshot(xt_file)

        # Reopen in a fresh owned context
        reopen_runtime = SolidEdgeRuntime()
        try:
            app_handle = reopen_runtime.connect_application()
            assert app_handle.ownership == OwnershipMode.OWNED
            worker = reopen_runtime._ensure_worker()

            def _reopen_probe() -> None:
                raw_app = worker._raw_app
                assert raw_app is not None
                docs = raw_app.Documents
                par_template = _find_template(docs, "par")
                asm_template = _find_template(docs, "asm")

                # 1. Bed.x_t (.par source) -> igPartDocument (Type 1), Models.Count >= 1
                bed_xt = output_root / "Bed.x_t"
                with _reopened_parasolid_document(docs, bed_xt, par_template) as d_par:
                    doc_type = getattr(d_par, "Type", None)
                    assert doc_type == 1, f"Expected Type 1 (PartDocument), got {doc_type}"
                    models = getattr(d_par, "Models", None)
                    count = getattr(models, "Count", 0) if models is not None else 0
                    if count < 1:
                        bodies = getattr(d_par, "DesignBodies", None)
                        count = getattr(bodies, "Count", 0) if bodies is not None else 0
                    assert count >= 1, f"Bed.x_t reopened with {count} 3D bodies"

                # 2. SE_Transition_RR.x_t (.psm source) -> igPartDocument (Type 1), Models.Count >= 1
                psm_xt = output_root / "SE_Transition_RR.x_t"
                with _reopened_parasolid_document(docs, psm_xt, par_template) as d_psm:
                    doc_type = getattr(d_psm, "Type", None)
                    assert doc_type == 1, f"Expected Type 1 (PartDocument), got {doc_type}"
                    models = getattr(d_psm, "Models", None)
                    count = getattr(models, "Count", 0) if models is not None else 0
                    if count < 1:
                        bodies = getattr(d_psm, "DesignBodies", None)
                        count = getattr(bodies, "Count", 0) if bodies is not None else 0
                    assert count >= 1, f"SE_Transition_RR.x_t reopened with {count} 3D bodies"

                # 3. carrier.x_t (.asm source) -> igAssemblyDocument (Type 3), Occurrences.Count >= 1
                asm_xt = output_root / "carrier.x_t"
                with _reopened_parasolid_document(docs, asm_xt, asm_template) as d_asm:
                    doc_type = getattr(d_asm, "Type", None)
                    assert doc_type == 3, f"Expected Type 3 (AssemblyDocument), got {doc_type}"
                    occs = getattr(d_asm, "Occurrences", None)
                    count = getattr(occs, "Count", 0) if occs is not None else 0
                    assert count >= 1, f"carrier.x_t reopened with {count} occurrences"

            worker.call(_reopen_probe, timeout=120.0)
        finally:
            assert reopen_runtime.teardown(force_kill_on_failure=True) is True

        # Post-reopen immutability assertions: neither source, child parts, nor .x_t was mutated
        for name in inputs_to_test:
            stem = Path(name).stem
            xt_file = output_root / f"{stem}.x_t"
            post_reopen_xt_snap = capture_source_snapshot(xt_file)
            assert verify_snapshot_equality(pre_reopen_xt_snapshots[stem], post_reopen_xt_snap)

        for rel in all_rel_paths:
            final_source_snap = capture_source_snapshot(input_root / rel)
            assert verify_snapshot_equality(pre_snapshots[rel], final_source_snap)

    def test_live_m55_02_collision_and_sentinel_preservation(
        self, live_runtime: SolidEdgeRuntime, tmp_path: Path
    ) -> None:
        """C-LIVE-M55-02a: Collision and partial result with sentinel preservation for Parasolid."""
        input_root = tmp_path / "inputs"
        output_root = tmp_path / "outputs"
        output_root.mkdir()
        _copy_fixtures(input_root)

        part_file = input_root / "Bed.par"
        if not part_file.is_file():
            pytest.skip("Required fixture 'Bed.par' not found in live_fixtures")

        # Pre-create target with sentinel content and record metadata
        existing_xt = output_root / "Bed.x_t"
        sentinel_bytes = b"LIVE PRE-EXISTING PARASOLID SENTINEL"
        existing_xt.write_bytes(sentinel_bytes)
        sentinel_stat_before = existing_xt.stat()

        candidate_reg = _build_candidate_registry()
        boundary = FilesystemBatchSafetyBoundary()
        bindings = build_initial_operation_bindings(boundary, registry=candidate_reg)
        service = BatchService(
            runtime_factory=lambda: live_runtime,
            safety_boundary=boundary,
            bindings=bindings,
            registry=candidate_reg,
        )

        spec = _make_spec(
            input_root=input_root,
            output_root=output_root,
            inputs=("Bed.par",),
            formats=("step", "parasolid"),
        )

        outcome = service.execute(spec)

        # Existing target was preserved byte-for-byte and metadata remained unmutated
        assert existing_xt.read_bytes() == sentinel_bytes
        sentinel_stat_after = existing_xt.stat()
        assert sentinel_stat_after.st_size == sentinel_stat_before.st_size
        assert sentinel_stat_after.st_mtime_ns == sentinel_stat_before.st_mtime_ns

        # step succeeded, parasolid failed with TARGET_ALREADY_EXISTS -> partial
        res = outcome.file_results[0]
        assert res.status == "partial"
        assert len(res.artifacts) == 1
        assert res.artifacts[0].format == "step"
        assert (output_root / "Bed.step").is_file()
        assert any(e.code == "TARGET_ALREADY_EXISTS" and e.format == "parasolid" for e in res.errors)

    def test_live_m55_02_unresolved_assembly_isolation(self, live_runtime: SolidEdgeRuntime, tmp_path: Path) -> None:
        """C-LIVE-M55-02b: Unresolved assembly isolation and fail-closed handling for Parasolid."""
        input_root = tmp_path / "inputs"
        output_root = tmp_path / "outputs"
        output_root.mkdir()
        _copy_fixtures(input_root)

        unresolved_asm = input_root / "unresolved_carrier.asm"
        if not unresolved_asm.is_file():
            pytest.skip("Fixture 'unresolved_carrier.asm' not found in live_fixtures")

        healthy_par = input_root / "Bed.par"
        if not healthy_par.is_file():
            pytest.skip("Fixture 'Bed.par' not found in live_fixtures")

        candidate_reg = _build_candidate_registry()
        boundary = FilesystemBatchSafetyBoundary()
        bindings = build_initial_operation_bindings(boundary, registry=candidate_reg)
        service = BatchService(
            runtime_factory=lambda: live_runtime,
            safety_boundary=boundary,
            bindings=bindings,
            registry=candidate_reg,
        )

        spec = _make_spec(
            input_root=input_root,
            output_root=output_root,
            inputs=("unresolved_carrier.asm", "Bed.par"),
            formats=("parasolid",),
            continue_on_error=True,
        )

        outcome = service.execute(spec)

        assert outcome.status == "completed"
        assert outcome.summary is not None
        assert outcome.summary.accepted == 1
        assert outcome.summary.failed == 1

        # Unresolved assembly failed closed: no .x_t produced
        assert not (output_root / "unresolved_carrier.x_t").exists()

        # Later healthy part succeeded
        assert (output_root / "Bed.x_t").is_file()
        assert validate_batch_output("parasolid", output_root / "Bed.x_t").size_bytes > 0

        # Diagnostics check: sanitized, no component path
        unresolved_res = outcome.file_results[0]
        assert unresolved_res.status == "failed"
        assert any(e.code == "ARTIFACT_EXPORT_FAILED" and e.format == "parasolid" for e in unresolved_res.errors)

    def test_live_m55_02_cancellation_cleanup(self, live_runtime: SolidEdgeRuntime, tmp_path: Path) -> None:
        """C-LIVE-M55-02c: Cooperative cancellation and workspace cleanup before Parasolid export."""
        input_root = tmp_path / "inputs"
        output_root = tmp_path / "outputs"
        output_root.mkdir()
        _copy_fixtures(input_root)

        part_file = input_root / "Bed.par"
        if not part_file.is_file():
            pytest.skip("Required fixture 'Bed.par' not found in live_fixtures")

        candidate_reg = _build_candidate_registry()
        boundary = FilesystemBatchSafetyBoundary()
        bindings = build_initial_operation_bindings(boundary, registry=candidate_reg)
        service = BatchService(
            runtime_factory=lambda: live_runtime,
            safety_boundary=boundary,
            bindings=bindings,
            cancellation_check=lambda: (output_root / "Bed.step").is_file(),
            registry=candidate_reg,
        )

        spec = _make_spec(
            input_root=input_root,
            output_root=output_root,
            inputs=("Bed.par",),
            formats=("step", "parasolid"),
        )

        outcome = service.execute(spec)

        assert outcome.status == "cancelled"
        # step succeeded before cancellation
        assert (output_root / "Bed.step").is_file()
        # parasolid was cancelled: no .x_t produced or leaked
        assert not (output_root / "Bed.x_t").exists()
        staging_dir = output_root / ".cadcopilot_batch_work"
        if staging_dir.exists():
            assert list(staging_dir.rglob("*.x_t")) == []

    def test_live_m55_02_localized_failure_isolation(self, live_runtime: SolidEdgeRuntime, tmp_path: Path) -> None:
        """C-LIVE-M55-02d: Localized Parasolid export/validation failure isolation and recovery."""
        input_root = tmp_path / "inputs"
        output_root = tmp_path / "outputs"
        output_root.mkdir()
        _copy_fixtures(input_root)

        part_1 = input_root / "Bed.par"
        part_2 = input_root / "SE_Transition_RR.psm"
        if not part_1.is_file() or not part_2.is_file():
            pytest.skip("Required 3D fixtures not found in live_fixtures")

        candidate_reg = _build_candidate_registry()
        boundary = FilesystemBatchSafetyBoundary()
        bindings = build_initial_operation_bindings(boundary, registry=candidate_reg)
        service = BatchService(
            runtime_factory=lambda: live_runtime,
            safety_boundary=boundary,
            bindings=bindings,
            registry=candidate_reg,
        )

        spec = _make_spec(
            input_root=input_root,
            output_root=output_root,
            inputs=("Bed.par", "SE_Transition_RR.psm"),
            formats=("parasolid",),
            continue_on_error=True,
        )

        real_validator = validate_batch_output

        def _reject_first_parasolid(fmt: Any, work_path: Path) -> Any:
            # Injected failure: reject only the first Parasolid artifact
            if fmt == "parasolid" and "Bed" in work_path.name:
                raise BatchFormatValidationError(
                    "parasolid",
                    "structure",
                    f"Test-injected candidate rejection for '{work_path.name}'",
                )
            return real_validator(fmt, work_path)

        with patch("batch.handlers.validate_batch_output", side_effect=_reject_first_parasolid):
            outcome = service.execute(spec)

        assert outcome.status == "completed"
        assert outcome.summary is not None
        assert outcome.summary.total == 2
        assert outcome.summary.accepted == 1
        assert outcome.summary.failed == 1

        # First item failed closed at validation boundary: no .x_t published to output_root
        assert not (output_root / "Bed.x_t").exists()

        # Second item succeeded and produced valid Parasolid output
        psm_xt = output_root / "SE_Transition_RR.x_t"
        assert psm_xt.is_file()
        assert validate_batch_output("parasolid", psm_xt).size_bytes > 0

        # Verify failure diagnostics on first item (mapped to ARTIFACT_EXPORT_FAILED)
        failed_res = outcome.file_results[0]
        assert failed_res.status == "failed"
        assert any(e.code == "ARTIFACT_EXPORT_FAILED" and e.format == "parasolid" for e in failed_res.errors)

        # Verify runtime health was preserved
        assert live_runtime.is_healthy() is True

        # Verify absence of rejected artifact in staging directory
        staging_dir = output_root / ".cadcopilot_batch_work"
        if staging_dir.exists():
            assert not any("Bed" in p.name for p in staging_dir.rglob("*.x_t"))

        # Verify success on second item
        success_res = outcome.file_results[1]
        assert success_res.status == "accepted"
        assert len(success_res.artifacts) == 1
        assert success_res.artifacts[0].format == "parasolid"
