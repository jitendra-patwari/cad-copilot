"""Integration tests for BatchService with production handlers and FilesystemBatchSafetyBoundary.

Proves:
- .par, .psm, .asm accepted for STEP/STL; .dft accepted for PDF/DXF.
- Requested format order and artifact order preserved.
- Partial and failed file status transitions.
- Continue-on-error behavior across resolved, unresolved, and healthy inputs.
- Fatal runtime-unhealthy termination marking subsequent files unprocessed.
- Existing target preservation without overwrite.
- Cooperation cancellation leaving clean workspace state.
- Source integrity mutation detection and cleanup.
- Atomic publication and sidecar cleanup.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, PropertyMock

from batch.composition import build_initial_operation_bindings
from batch.execution import BatchExecutionSpec
from batch.format_validation import validate_batch_output
from batch.registry import build_initial_registry
from batch.safety import FilesystemBatchSafetyBoundary
from batch.service import BatchService
from interfaces.exceptions import (
    CADExportError,
    CADRuntimeUnavailableError,
)
from tests.batch.fake_support import (
    FakeDocumentHandle,
    FakeTrackedDocumentRuntime,
)
from tests.batch.test_format_validation import (
    _write_minimal_binary_stl,
    _write_minimal_valid_dxf,
    _write_minimal_valid_parasolid,
    _write_minimal_valid_pdf,
    _write_minimal_valid_step,
)


def _make_spec(
    input_root: Path,
    output_root: Path,
    inputs: tuple[str, ...],
    operation_id: str = "export_3d",
    formats: tuple[str, ...] = ("step", "stl"),
    request_id: str = "req-fmt-001",
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


class FakeCOMDocument:
    """Mock raw COM document simulating SaveCopyAs and Solid Edge sidecars."""

    def __init__(
        self,
        name: str = "doc.par",
        *,
        unresolved_asm: bool = False,
        fail_formats: set[str] | None = None,
        on_save_copy_as: Callable[[str], None] | None = None,
    ) -> None:
        self.Name = name
        self._unresolved_asm = unresolved_asm
        self._fail_formats = fail_formats or set()
        self._on_save_copy_as = on_save_copy_as

        # Sections for drawing view refresh
        self.Sections = MagicMock()
        sheet = MagicMock()
        dview = MagicMock()
        sheet.DrawingViews.Count = 1
        sheet.DrawingViews.Item.return_value = dview
        self.Sections.WorkingSection.Sheets.Count = 1
        self.Sections.WorkingSection.Sheets.Item.return_value = sheet

        # Occurrences for assembly check
        self.Occurrences = MagicMock()
        if unresolved_asm:
            occ = MagicMock()
            occ.Subassembly = False
            occ.FileMissing.return_value = True
            type(occ).OccurrenceDocument = PropertyMock(side_effect=RuntimeError("Missing component"))
            self.Occurrences.Count = 1
            self.Occurrences.Item.return_value = occ
        else:
            occ = MagicMock()
            occ.Subassembly = False
            occ.FileMissing.return_value = False
            occ.OccurrenceDocument = MagicMock()
            self.Occurrences.Count = 1
            self.Occurrences.Item.return_value = occ

    def SaveCopyAs(self, target_path: str) -> None:
        if self._on_save_copy_as is not None:
            self._on_save_copy_as(target_path)
            return

        p = Path(target_path)
        ext = p.suffix.lower().lstrip(".")
        if ext in self._fail_formats:
            raise CADExportError(f"Simulated export failure for format '{ext}'")

        if ext == "step":
            _write_minimal_valid_step(p)
            # Solid Edge step translator writes .log sidecar
            p.with_suffix(".log").write_text("STEP Translation Log")
        elif ext == "stl":
            _write_minimal_binary_stl(p)
            # Solid Edge stl translator writes .log sidecar
            p.with_suffix(".log").write_text("STL Translation Log")
        elif ext in ("parasolid", "x_t"):
            _write_minimal_valid_parasolid(p)
        elif ext == "pdf":
            _write_minimal_valid_pdf(p)
        elif ext == "dxf":
            _write_minimal_valid_dxf(p)


class FakeCOMWorker:
    """Worker double dispatching COM invocations."""

    def _invoke_com(self, fn: Callable[[], Any]) -> Any:
        return fn()


def make_fake_doc_opener(
    runtime: FakeTrackedDocumentRuntime,
    *,
    unresolved_predicate: Callable[[Path], bool] | None = None,
    fail_formats: set[str] | None = None,
    on_save_copy_as: Callable[[Path, str], None] | None = None,
) -> Callable[[Any, Path], FakeDocumentHandle]:
    """Factory creating an open_document stub wired with FakeCOMDocument and FakeCOMWorker."""

    def _open_doc(app_handle: Any, path: Path) -> FakeDocumentHandle:
        is_unresolved = unresolved_predicate(path) if unresolved_predicate else False
        doc_save_cb = (lambda target: on_save_copy_as(path, target)) if on_save_copy_as is not None else None
        raw_doc = FakeCOMDocument(
            name=path.name,
            unresolved_asm=is_unresolved,
            fail_formats=fail_formats,
            on_save_copy_as=doc_save_cb,
        )
        handle = FakeDocumentHandle(
            path=path,
            handle_id=f"handle-{path.name}",
            raw_doc=raw_doc,
            worker=FakeCOMWorker(),
        )
        runtime.open_handles.add(handle)
        runtime.open_calls.append(path)
        return handle

    return _open_doc


class TestServiceFormats:
    """Integration test suite for batch format export and drawing publication."""

    def test_3d_formats_par_psm_asm_step_stl_end_to_end(self, tmp_path: Path) -> None:
        input_root = tmp_path / "inputs"
        output_root = tmp_path / "outputs"
        input_root.mkdir()
        output_root.mkdir()

        (input_root / "part.par").write_bytes(b"SOLID EDGE PART")
        (input_root / "sheet.psm").write_bytes(b"SOLID EDGE SHEET METAL")
        (input_root / "assy.asm").write_bytes(b"SOLID EDGE ASSEMBLY")

        runtime = FakeTrackedDocumentRuntime()
        runtime.open_callback = make_fake_doc_opener(runtime)

        boundary = FilesystemBatchSafetyBoundary()
        bindings = build_initial_operation_bindings(boundary)
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = _make_spec(
            input_root=input_root,
            output_root=output_root,
            inputs=("part.par", "sheet.psm", "assy.asm"),
            operation_id="export_3d",
            formats=("step", "stl"),
        )

        outcome = service.execute(spec)

        assert outcome.status == "completed"
        assert outcome.summary is not None
        assert outcome.summary.total == 3
        assert outcome.summary.accepted == 3
        assert outcome.summary.partial == 0
        assert outcome.summary.failed == 0

        # Output files exist and are verified
        assert (output_root / "part.step").is_file()
        assert (output_root / "part.stl").is_file()
        assert (output_root / "sheet.step").is_file()
        assert (output_root / "sheet.stl").is_file()
        assert (output_root / "assy.step").is_file()
        assert (output_root / "assy.stl").is_file()

        # Invariant: translator sidecars (.log) were cleaned up
        assert not (output_root / "part.log").exists()
        assert not (output_root / "sheet.log").exists()
        assert not (output_root / "assy.log").exists()

        # Format order preserved
        for file_res in outcome.file_results:
            assert len(file_res.artifacts) == 2
            assert file_res.artifacts[0].format == "step"
            assert file_res.artifacts[1].format == "stl"

    def test_drawing_formats_dft_pdf_dxf_end_to_end(self, tmp_path: Path) -> None:
        input_root = tmp_path / "inputs"
        output_root = tmp_path / "outputs"
        input_root.mkdir()
        output_root.mkdir()

        sub_dir = input_root / "sub"
        sub_dir.mkdir()
        (input_root / "drawing1.dft").write_bytes(b"SOLID EDGE DRAFT 1")
        (sub_dir / "drawing2.dft").write_bytes(b"SOLID EDGE DRAFT 2")

        runtime = FakeTrackedDocumentRuntime()
        runtime.open_callback = make_fake_doc_opener(runtime)

        boundary = FilesystemBatchSafetyBoundary()
        bindings = build_initial_operation_bindings(boundary)
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = _make_spec(
            input_root=input_root,
            output_root=output_root,
            inputs=("drawing1.dft", "sub/drawing2.dft"),
            operation_id="publish_drawing",
            formats=("pdf", "dxf"),
        )

        outcome = service.execute(spec)

        assert outcome.status == "completed"
        assert outcome.summary is not None
        assert outcome.summary.accepted == 2

        assert (output_root / "drawing1.pdf").is_file()
        assert (output_root / "drawing1.dxf").is_file()
        assert (output_root / "sub" / "drawing2.pdf").is_file()
        assert (output_root / "sub" / "drawing2.dxf").is_file()

    def test_partial_outcome_one_format_succeeds_one_fails(self, tmp_path: Path) -> None:
        input_root = tmp_path / "inputs"
        output_root = tmp_path / "outputs"
        input_root.mkdir()
        output_root.mkdir()

        (input_root / "box.par").write_bytes(b"SOLID EDGE PART")

        runtime = FakeTrackedDocumentRuntime()
        runtime.open_callback = make_fake_doc_opener(runtime, fail_formats={"stl"})

        boundary = FilesystemBatchSafetyBoundary()
        bindings = build_initial_operation_bindings(boundary)
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = _make_spec(
            input_root=input_root,
            output_root=output_root,
            inputs=("box.par",),
            formats=("step", "stl"),
        )

        outcome = service.execute(spec)

        assert outcome.status == "completed"
        assert outcome.summary is not None
        assert outcome.summary.accepted == 0
        assert outcome.summary.partial == 1
        assert outcome.summary.failed == 0

        res = outcome.file_results[0]
        assert res.status == "partial"
        assert len(res.artifacts) == 1
        assert res.artifacts[0].format == "step"
        assert len(res.errors) == 1
        assert res.errors[0].code == "ARTIFACT_EXPORT_FAILED"

        assert (output_root / "box.step").is_file()
        assert not (output_root / "box.stl").exists()

    def test_both_formats_fail_produces_failed(self, tmp_path: Path) -> None:
        input_root = tmp_path / "inputs"
        output_root = tmp_path / "outputs"
        input_root.mkdir()
        output_root.mkdir()

        (input_root / "box.par").write_bytes(b"SOLID EDGE PART")

        runtime = FakeTrackedDocumentRuntime()
        runtime.open_callback = make_fake_doc_opener(runtime, fail_formats={"step", "stl"})

        boundary = FilesystemBatchSafetyBoundary()
        bindings = build_initial_operation_bindings(boundary)
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = _make_spec(
            input_root=input_root,
            output_root=output_root,
            inputs=("box.par",),
            formats=("step", "stl"),
        )

        outcome = service.execute(spec)

        assert outcome.status == "completed"
        assert outcome.summary is not None
        assert outcome.summary.accepted == 0
        assert outcome.summary.failed == 1

        res = outcome.file_results[0]
        assert res.status == "failed"
        assert len(res.artifacts) == 0
        assert len(res.errors) == 2

        assert not (output_root / "box.step").exists()
        assert not (output_root / "box.stl").exists()

    def test_continue_on_error_with_unresolved_assembly(self, tmp_path: Path) -> None:
        input_root = tmp_path / "inputs"
        output_root = tmp_path / "outputs"
        input_root.mkdir()
        output_root.mkdir()

        (input_root / "good.asm").write_bytes(b"SOLID EDGE GOOD ASM")
        (input_root / "broken.asm").write_bytes(b"SOLID EDGE BROKEN ASM")
        (input_root / "tail.par").write_bytes(b"SOLID EDGE TAIL PAR")

        runtime = FakeTrackedDocumentRuntime()
        runtime.open_callback = make_fake_doc_opener(
            runtime,
            unresolved_predicate=lambda p: "broken" in p.name,
        )

        boundary = FilesystemBatchSafetyBoundary()
        bindings = build_initial_operation_bindings(boundary)
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = _make_spec(
            input_root=input_root,
            output_root=output_root,
            inputs=("good.asm", "broken.asm", "tail.par"),
            formats=("step", "stl"),
            continue_on_error=True,
        )

        outcome = service.execute(spec)

        assert outcome.status == "completed"
        assert outcome.summary is not None
        assert outcome.summary.accepted == 2
        assert outcome.summary.failed == 1

        # broken.asm failed without output
        assert (output_root / "good.step").is_file()
        assert (output_root / "good.stl").is_file()
        assert not (output_root / "broken.step").exists()
        assert not (output_root / "broken.stl").exists()
        assert (output_root / "tail.step").is_file()
        assert (output_root / "tail.stl").is_file()

        # broken.asm diagnostic check: format-qualified, sanitized, no component path
        broken_res = outcome.file_results[1]
        assert broken_res.input == "broken.asm"
        assert broken_res.status == "failed"
        assert len(broken_res.errors) == 2
        assert broken_res.errors[0].format == "step"
        assert broken_res.errors[0].code == "ARTIFACT_EXPORT_FAILED"
        assert "unresolved reference" in broken_res.errors[0].message
        assert broken_res.errors[1].format == "stl"
        assert broken_res.errors[1].code == "ARTIFACT_EXPORT_FAILED"
        assert "unresolved reference" in broken_res.errors[1].message

    def test_continue_on_error_false_stops_after_failure(self, tmp_path: Path) -> None:
        input_root = tmp_path / "inputs"
        output_root = tmp_path / "outputs"
        input_root.mkdir()
        output_root.mkdir()

        (input_root / "broken.asm").write_bytes(b"SOLID EDGE BROKEN ASM")
        (input_root / "tail.par").write_bytes(b"SOLID EDGE TAIL PAR")

        runtime = FakeTrackedDocumentRuntime()
        runtime.open_callback = make_fake_doc_opener(
            runtime,
            unresolved_predicate=lambda p: "broken" in p.name,
        )

        boundary = FilesystemBatchSafetyBoundary()
        bindings = build_initial_operation_bindings(boundary)
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = _make_spec(
            input_root=input_root,
            output_root=output_root,
            inputs=("broken.asm", "tail.par"),
            formats=("step", "stl"),
            continue_on_error=False,
        )

        outcome = service.execute(spec)

        assert outcome.status == "completed"
        assert outcome.summary is not None
        assert outcome.summary.failed == 1
        assert outcome.summary.unprocessed == 1
        assert outcome.unprocessed_files == ("tail.par",)

    def test_runtime_unhealthy_stops_and_marks_remaining_unprocessed(self, tmp_path: Path) -> None:
        input_root = tmp_path / "inputs"
        output_root = tmp_path / "outputs"
        input_root.mkdir()
        output_root.mkdir()

        (input_root / "part1.par").write_bytes(b"SOLID EDGE PART 1")
        (input_root / "part2.par").write_bytes(b"SOLID EDGE PART 2")

        runtime = FakeTrackedDocumentRuntime()

        def _failing_save_copy_as(p: Path, target: str) -> None:
            runtime.healthy = False
            raise CADRuntimeUnavailableError("Solid Edge process crashed during export")

        runtime.open_callback = make_fake_doc_opener(
            runtime,
            on_save_copy_as=_failing_save_copy_as,
        )

        boundary = FilesystemBatchSafetyBoundary()
        bindings = build_initial_operation_bindings(boundary)
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = _make_spec(
            input_root=input_root,
            output_root=output_root,
            inputs=("part1.par", "part2.par"),
            formats=("step", "stl"),
        )

        outcome = service.execute(spec)

        assert outcome.status == "failed"
        assert outcome.unprocessed_files == ("part2.par",)
        assert len(outcome.file_results) == 1
        res = outcome.file_results[0]
        # Should record SOLID_EDGE_UNHEALTHY
        assert any(e.code == "SOLID_EDGE_UNHEALTHY" for e in res.errors)

    def test_existing_target_preserves_target_and_fails_safely(self, tmp_path: Path) -> None:
        input_root = tmp_path / "inputs"
        output_root = tmp_path / "outputs"
        input_root.mkdir()
        output_root.mkdir()

        (input_root / "part.par").write_bytes(b"SOLID EDGE PART")
        # Pre-create target with sentinel content
        existing_step = output_root / "part.step"
        sentinel_bytes = b"PRE-EXISTING PROTECTED ARTIFACT BYTES"
        existing_step.write_bytes(sentinel_bytes)

        runtime = FakeTrackedDocumentRuntime()
        runtime.open_callback = make_fake_doc_opener(runtime)

        boundary = FilesystemBatchSafetyBoundary()
        bindings = build_initial_operation_bindings(boundary)
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = _make_spec(
            input_root=input_root,
            output_root=output_root,
            inputs=("part.par",),
            formats=("step", "stl"),
        )

        outcome = service.execute(spec)

        # Existing target was preserved
        assert existing_step.read_bytes() == sentinel_bytes

        # part.step failed with TARGET_ALREADY_EXISTS, part.stl was accepted
        res = outcome.file_results[0]
        assert res.status == "partial"
        assert any(e.code == "TARGET_ALREADY_EXISTS" for e in res.errors)
        assert len(res.artifacts) == 1
        assert res.artifacts[0].format == "stl"

    def test_source_integrity_mismatch_clears_artifacts_and_becomes_fatal(self, tmp_path: Path) -> None:
        input_root = tmp_path / "inputs"
        output_root = tmp_path / "outputs"
        input_root.mkdir()
        output_root.mkdir()

        source_file = input_root / "part.par"
        source_file.write_bytes(b"ORIGINAL BYTES BEFORE BATCH")

        runtime = FakeTrackedDocumentRuntime()

        def _mutating_save_copy_as(p: Path, target: str) -> None:
            # Mutate source file during COM operation
            source_file.write_bytes(b"MUTATED SOURCE BYTES")
            tgt = Path(target)
            if tgt.suffix.lower() == ".step":
                _write_minimal_valid_step(tgt)
                tgt.with_suffix(".log").write_text("STEP Log")
            else:
                _write_minimal_binary_stl(tgt)
                tgt.with_suffix(".log").write_text("STL Log")

        runtime.open_callback = make_fake_doc_opener(
            runtime,
            on_save_copy_as=_mutating_save_copy_as,
        )

        boundary = FilesystemBatchSafetyBoundary()
        bindings = build_initial_operation_bindings(boundary)
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = _make_spec(
            input_root=input_root,
            output_root=output_root,
            inputs=("part.par",),
            formats=("step", "stl"),
        )

        outcome = service.execute(spec)

        assert outcome.status == "failed"
        res = outcome.file_results[0]
        assert res.status == "failed"
        assert any(e.code == "SOURCE_INTEGRITY_FAILED" for e in res.errors)
        # Artifacts cleared on source integrity mismatch
        assert len(res.artifacts) == 0

    def test_parasolid_export_par_psm_asm_end_to_end(self, tmp_path: Path) -> None:
        """Prove export_3d with canonical registry exports parasolid for .par, .psm, .asm."""
        input_root = tmp_path / "inputs"
        output_root = tmp_path / "outputs"
        input_root.mkdir()
        output_root.mkdir()

        (input_root / "part.par").write_bytes(b"SOLID EDGE PART")
        (input_root / "sheet.psm").write_bytes(b"SOLID EDGE SHEET METAL")
        (input_root / "assy.asm").write_bytes(b"SOLID EDGE ASSEMBLY")

        runtime = FakeTrackedDocumentRuntime()
        runtime.open_callback = make_fake_doc_opener(runtime)

        registry = build_initial_registry()
        boundary = FilesystemBatchSafetyBoundary()
        bindings = build_initial_operation_bindings(boundary, registry=registry)
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
            registry=registry,
        )

        spec = _make_spec(
            input_root=input_root,
            output_root=output_root,
            inputs=("part.par", "sheet.psm", "assy.asm"),
            operation_id="export_3d",
            formats=("parasolid",),
        )

        outcome = service.execute(spec)

        assert outcome.status == "completed"
        assert outcome.summary is not None
        assert outcome.summary.total == 3
        assert outcome.summary.accepted == 3
        assert outcome.summary.partial == 0
        assert outcome.summary.failed == 0

        # Output files exist and are verified
        part_xt = output_root / "part.x_t"
        sheet_xt = output_root / "sheet.x_t"
        assy_xt = output_root / "assy.x_t"
        assert part_xt.is_file()
        assert sheet_xt.is_file()
        assert assy_xt.is_file()

        # Invariant: zero sidecars for parasolid
        assert not (output_root / "part.log").exists()
        assert not (output_root / "sheet.log").exists()
        assert not (output_root / "assy.log").exists()

        # Structural validation passes
        assert validate_batch_output("parasolid", part_xt).size_bytes > 0
        assert validate_batch_output("parasolid", sheet_xt).size_bytes > 0
        assert validate_batch_output("parasolid", assy_xt).size_bytes > 0

        for file_res in outcome.file_results:
            assert len(file_res.artifacts) == 1
            assert file_res.artifacts[0].format == "parasolid"

    def test_three_format_mixed_export_preserves_ordering(self, tmp_path: Path) -> None:
        """Prove step, stl, parasolid mixed export preserves requested format ordering."""
        input_root = tmp_path / "inputs"
        output_root = tmp_path / "outputs"
        input_root.mkdir()
        output_root.mkdir()

        (input_root / "model.par").write_bytes(b"SOLID EDGE PART")

        runtime = FakeTrackedDocumentRuntime()
        runtime.open_callback = make_fake_doc_opener(runtime)

        registry = build_initial_registry()
        boundary = FilesystemBatchSafetyBoundary()
        bindings = build_initial_operation_bindings(boundary, registry=registry)
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
            registry=registry,
        )

        spec = _make_spec(
            input_root=input_root,
            output_root=output_root,
            inputs=("model.par",),
            operation_id="export_3d",
            formats=("step", "stl", "parasolid"),
        )

        outcome = service.execute(spec)

        assert outcome.status == "completed"
        assert outcome.summary is not None
        assert outcome.summary.accepted == 1

        assert (output_root / "model.step").is_file()
        assert (output_root / "model.stl").is_file()
        assert (output_root / "model.x_t").is_file()

        # Invariant: step and stl logs cleaned up, no parasolid log
        assert not (output_root / "model.log").exists()

        res = outcome.file_results[0]
        assert len(res.artifacts) == 3
        assert [a.format for a in res.artifacts] == ["step", "stl", "parasolid"]

    def test_parasolid_target_collision_preserves_sentinel(self, tmp_path: Path) -> None:
        """Pre-existing .x_t fails with TARGET_ALREADY_EXISTS without overwrite."""
        input_root = tmp_path / "inputs"
        output_root = tmp_path / "outputs"
        input_root.mkdir()
        output_root.mkdir()

        (input_root / "part.par").write_bytes(b"SOLID EDGE PART")
        existing_xt = output_root / "part.x_t"
        sentinel_bytes = b"PRE-EXISTING PARASOLID SENTINEL"
        existing_xt.write_bytes(sentinel_bytes)

        runtime = FakeTrackedDocumentRuntime()
        runtime.open_callback = make_fake_doc_opener(runtime)

        registry = build_initial_registry()
        boundary = FilesystemBatchSafetyBoundary()
        bindings = build_initial_operation_bindings(boundary, registry=registry)
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
            registry=registry,
        )

        spec = _make_spec(
            input_root=input_root,
            output_root=output_root,
            inputs=("part.par",),
            formats=("step", "parasolid"),
        )

        outcome = service.execute(spec)

        # Sentinel remains pristine
        assert existing_xt.read_bytes() == sentinel_bytes

        # step succeeded, parasolid failed with TARGET_ALREADY_EXISTS -> partial
        res = outcome.file_results[0]
        assert res.status == "partial"
        assert len(res.artifacts) == 1
        assert res.artifacts[0].format == "step"
        assert any(e.code == "TARGET_ALREADY_EXISTS" and e.format == "parasolid" for e in res.errors)

    def test_unresolved_assembly_preflight_blocks_parasolid(self, tmp_path: Path) -> None:
        """Unresolved assembly fails preflight and blocks Parasolid export."""
        input_root = tmp_path / "inputs"
        output_root = tmp_path / "outputs"
        input_root.mkdir()
        output_root.mkdir()

        (input_root / "broken.asm").write_bytes(b"BROKEN ASM")
        (input_root / "good.par").write_bytes(b"GOOD PART")

        runtime = FakeTrackedDocumentRuntime()
        runtime.open_callback = make_fake_doc_opener(
            runtime,
            unresolved_predicate=lambda p: "broken" in p.name,
        )

        registry = build_initial_registry()
        boundary = FilesystemBatchSafetyBoundary()
        bindings = build_initial_operation_bindings(boundary, registry=registry)
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
            registry=registry,
        )

        spec = _make_spec(
            input_root=input_root,
            output_root=output_root,
            inputs=("broken.asm", "good.par"),
            formats=("parasolid",),
            continue_on_error=True,
        )

        outcome = service.execute(spec)

        assert outcome.status == "completed"
        assert outcome.summary is not None
        assert outcome.summary.accepted == 1
        assert outcome.summary.failed == 1

        # broken.asm produced no output
        assert not (output_root / "broken.x_t").exists()
        # good.par succeeded
        assert (output_root / "good.x_t").is_file()

        broken_res = outcome.file_results[0]
        assert broken_res.status == "failed"
        assert any(e.code == "ARTIFACT_EXPORT_FAILED" and e.format == "parasolid" for e in broken_res.errors)

    def test_cancellation_cleans_parasolid_staging(self, tmp_path: Path) -> None:
        """Mid-file cancellation before parasolid leaves zero .x_t files and clean staging."""
        input_root = tmp_path / "inputs"
        output_root = tmp_path / "outputs"
        input_root.mkdir()
        output_root.mkdir()

        (input_root / "part.par").write_bytes(b"SOLID EDGE PART")

        cancelled = False

        def _step_then_cancel(p: Path, target: str) -> None:
            nonlocal cancelled
            tgt = Path(target)
            if tgt.suffix.lower() == ".step":
                _write_minimal_valid_step(tgt)
                tgt.with_suffix(".log").write_text("STEP Translation Log")
                cancelled = True

        runtime = FakeTrackedDocumentRuntime()
        runtime.open_callback = make_fake_doc_opener(runtime, on_save_copy_as=_step_then_cancel)

        registry = build_initial_registry()
        boundary = FilesystemBatchSafetyBoundary()
        bindings = build_initial_operation_bindings(boundary, registry=registry)
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
            cancellation_check=lambda: cancelled,
            registry=registry,
        )

        spec = _make_spec(
            input_root=input_root,
            output_root=output_root,
            inputs=("part.par",),
            formats=("step", "parasolid"),
        )

        outcome = service.execute(spec)

        assert outcome.status == "cancelled"
        assert (output_root / "part.step").is_file()
        assert not (output_root / "part.x_t").exists()
        staging_dir = output_root / ".cadcopilot_batch_work"
        if staging_dir.exists():
            assert list(staging_dir.rglob("*.x_t")) == []

        res = outcome.file_results[0]
        assert any(e.code == "BATCH_CANCELLED" and e.format == "parasolid" for e in res.errors)
