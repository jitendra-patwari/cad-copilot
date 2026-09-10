"""Integration tests for BatchService with production FilesystemBatchSafetyBoundary."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from pathlib import Path
from typing import cast
from unittest.mock import patch

from batch.allocation import PreparedBatchFile
from batch.bindings import BatchHandlerFactory, BatchOperationHandler, OperationBinding, OperationBindings
from batch.execution import BatchExecutionSpec, BatchItemContext, BatchItemOutcome, BatchProgressUpdate
from batch.models import BatchArtifactRecord, BatchDiagnostic, BatchOutputFormat
from batch.output_snapshot import capture_output_snapshot
from batch.output_workspace import BatchOutputWorkspace, BatchWorkspaceError
from batch.registry import build_initial_registry
from batch.safety import FilesystemBatchSafetyBoundary
from batch.service import BatchService
from batch.source_integrity import capture_source_snapshot
from interfaces.runtime_abc import CADRuntimeABC
from tests.batch.fake_support import FakeCADRuntime


def _make_spec(
    input_root: Path,
    output_root: Path,
    inputs: tuple[str, ...],
    formats: tuple[str, ...] = ("step", "stl"),
    request_id: str = "req-svc-fs-001",
    continue_on_error: bool = True,
) -> BatchExecutionSpec:
    return BatchExecutionSpec(
        contract_version="1.0",
        request_id=request_id,
        input_root=str(input_root),
        output_root=str(output_root),
        inputs=inputs,
        operation_id="export_3d",
        formats=formats,
        continue_on_error=continue_on_error,
    )


def _make_workspace_test_bindings(
    workspace: BatchOutputWorkspace,
    *,
    mutate_source_during_format: dict[str, Path] | None = None,
    fail_on_format: set[str] | None = None,
    on_export: Callable[[BatchItemContext], None] | None = None,
) -> tuple[OperationBindings, list[BatchItemContext]]:
    """Build OperationBindings wired with a handler exercising the BatchOutputWorkspace staging lifecycle."""
    recorded_calls: list[BatchItemContext] = []
    mutate_map = mutate_source_during_format or {}
    fail_set = fail_on_format or set()

    def make_handler(op_id: str) -> BatchOperationHandler:
        def handler(doc_handle: object, context: BatchItemContext) -> BatchItemOutcome:
            recorded_calls.append(context)
            if on_export is not None:
                on_export(context)

            # Check if source should be mutated during this format export
            if context.input in mutate_map:
                source_to_mutate = mutate_map[context.input]
                source_to_mutate.write_bytes(b"MUTATED DURING COM EXPORT")

            fmt_literal = cast(BatchOutputFormat, context.format)
            if context.format in fail_set:
                return BatchItemOutcome.failure(
                    format=context.format,
                    errors=(
                        BatchDiagnostic(
                            code="ARTIFACT_EXPORT_FAILED",
                            message=f"Simulated handler export failure for '{context.format}'",
                            format=fmt_literal,
                        ),
                    ),
                )

            # Real staging through the boundary workspace
            try:
                workspace.begin_format(context)
                context.work_path.write_bytes(f"generated content for {context.format}".encode())
                snap = capture_output_snapshot(context.work_path)
                published = workspace.finalize_format(context, snap)
                return BatchItemOutcome.success(
                    format=context.format,
                    artifact=BatchArtifactRecord(format=fmt_literal, path=str(published)),
                )
            except BatchWorkspaceError as exc:
                with contextlib.suppress(Exception):
                    workspace.cleanup_format(context)
                return BatchItemOutcome.failure(
                    format=context.format,
                    errors=(exc.diagnostic,),
                )

        return handler

    def make_factory(op_id: str) -> BatchHandlerFactory:
        def factory(runtime: CADRuntimeABC) -> BatchOperationHandler:
            return make_handler(op_id)

        return factory

    registry = build_initial_registry()
    bindings_list: list[OperationBinding] = []
    for op_id in registry.operation_ids:
        bindings_list.append(OperationBinding(operation_id=op_id, factory=make_factory(op_id)))

    return OperationBindings(bindings_list, registry=registry), recorded_calls


class TestServiceFilesystemIntegration:
    """Integration tests combining BatchService with production FilesystemBatchSafetyBoundary."""

    def test_service_single_file_success(self, tmp_path: Path) -> None:
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        (in_dir / "bracket.par").write_bytes(b"original cad model data")

        runtime = FakeCADRuntime()
        boundary = FilesystemBatchSafetyBoundary()
        bindings, _ = _make_workspace_test_bindings(boundary)

        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = _make_spec(in_dir, out_dir, inputs=("bracket.par",), formats=("step", "stl"))
        outcome = service.execute(spec)

        assert outcome.status == "completed"
        assert outcome.summary is not None
        assert outcome.summary.total == 1
        assert outcome.summary.accepted == 1
        assert outcome.summary.failed == 0
        assert outcome.summary.unprocessed == 0

        assert len(outcome.file_results) == 1
        file_res = outcome.file_results[0]
        assert file_res.status == "accepted"
        assert len(file_res.artifacts) == 2

        # Verify published targets exist on disk and private work directories are removed
        step_target = out_dir / "bracket.step"
        stl_target = out_dir / "bracket.stl"
        assert step_target.exists()
        assert stl_target.exists()
        assert step_target.read_bytes() == b"generated content for step"
        assert stl_target.read_bytes() == b"generated content for stl"

        # Source file remained unmodified
        assert (in_dir / "bracket.par").read_bytes() == b"original cad model data"

        # Runtime had at most one document open
        assert runtime.connect_calls == 1
        assert runtime.max_simultaneous_open == 1
        assert len(runtime.open_calls) == 1
        assert len(runtime.close_calls) == 1

    def test_service_multi_file_caller_ordering_preserved(self, tmp_path: Path) -> None:
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        (in_dir / "first.par").write_bytes(b"first file content")
        (in_dir / "second.psm").write_bytes(b"second file content")
        (in_dir / "third.asm").write_bytes(b"third file content")

        runtime = FakeCADRuntime()
        boundary = FilesystemBatchSafetyBoundary()
        bindings, _ = _make_workspace_test_bindings(boundary)

        progress_events: list[BatchProgressUpdate] = []
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
            observer=progress_events.append,
        )

        spec = _make_spec(
            in_dir,
            out_dir,
            inputs=("first.par", "second.psm", "third.asm"),
            formats=("step",),
        )
        outcome = service.execute(spec)

        assert outcome.status == "completed"
        assert outcome.summary is not None
        assert outcome.summary.total == 3
        assert outcome.summary.accepted == 3

        # Preserve exact input ordering
        assert [r.input for r in outcome.file_results] == ["first.par", "second.psm", "third.asm"]

    def test_service_root_rejection_before_runtime(self, tmp_path: Path) -> None:
        missing_in = tmp_path / "missing_input"
        out_dir = tmp_path / "output"
        out_dir.mkdir()

        runtime_factory_called = False

        def runtime_factory() -> CADRuntimeABC:
            nonlocal runtime_factory_called
            runtime_factory_called = True
            return FakeCADRuntime()

        boundary = FilesystemBatchSafetyBoundary()
        bindings, _ = _make_workspace_test_bindings(boundary)

        service = BatchService(
            runtime_factory=runtime_factory,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = _make_spec(missing_in, out_dir, inputs=("part.par",))
        outcome = service.execute(spec)

        assert outcome.status == "rejected"
        assert len(outcome.errors) == 1
        assert outcome.errors[0].code == "INPUT_ROOT_NOT_FOUND"

        # Runtime factory was NEVER called
        assert not runtime_factory_called

    def test_service_missing_source_file_error_isolation(self, tmp_path: Path) -> None:
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        (in_dir / "present.par").write_bytes(b"present file content")

        runtime = FakeCADRuntime()
        boundary = FilesystemBatchSafetyBoundary()
        bindings, _ = _make_workspace_test_bindings(boundary)

        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = _make_spec(
            in_dir,
            out_dir,
            inputs=("missing.par", "present.par"),
            formats=("step",),
            continue_on_error=True,
        )
        outcome = service.execute(spec)

        assert outcome.status == "completed"
        assert outcome.summary is not None
        assert outcome.summary.total == 2
        assert outcome.summary.accepted == 1
        assert outcome.summary.failed == 1

        res0 = outcome.file_results[0]
        assert res0.input == "missing.par"
        assert res0.status == "failed"
        assert res0.errors[0].code == "INPUT_FILE_NOT_FOUND"

        res1 = outcome.file_results[1]
        assert res1.input == "present.par"
        assert res1.status == "accepted"
        assert (out_dir / "present.step").exists()

    def test_service_target_already_exists_format_isolation(self, tmp_path: Path) -> None:
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        (in_dir / "bracket.par").write_bytes(b"part data")
        (out_dir / "bracket.step").write_bytes(b"pre-existing step target")

        runtime = FakeCADRuntime()
        boundary = FilesystemBatchSafetyBoundary()
        bindings, _ = _make_workspace_test_bindings(boundary)

        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = _make_spec(
            in_dir,
            out_dir,
            inputs=("bracket.par",),
            formats=("step", "stl"),
        )
        outcome = service.execute(spec)

        assert outcome.status == "completed"
        assert outcome.summary is not None
        assert outcome.summary.total == 1
        assert outcome.summary.partial == 1
        assert outcome.summary.accepted == 0

        res = outcome.file_results[0]
        assert res.status == "partial"
        # 1 error on step format
        assert len(res.errors) == 1
        assert res.errors[0].code == "TARGET_ALREADY_EXISTS"
        assert res.errors[0].format == "step"
        # 1 successful artifact on stl format
        assert len(res.artifacts) == 1
        assert res.artifacts[0].format == "stl"

        # Existing target was preserved byte-for-byte
        assert (out_dir / "bracket.step").read_bytes() == b"pre-existing step target"
        # Sibling target was published
        assert (out_dir / "bracket.stl").read_bytes() == b"generated content for stl"

    def test_service_source_mutation_fatal_abort(self, tmp_path: Path) -> None:
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        file1 = in_dir / "part1.par"
        file2 = in_dir / "part2.par"
        file1.write_bytes(b"original part 1")
        file2.write_bytes(b"original part 2")

        runtime = FakeCADRuntime()
        boundary = FilesystemBatchSafetyBoundary()

        # Mutate file1 during format export
        bindings, _ = _make_workspace_test_bindings(
            boundary,
            mutate_source_during_format={"part1.par": file1},
        )

        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = _make_spec(
            in_dir,
            out_dir,
            inputs=("part1.par", "part2.par"),
            formats=("step",),
        )
        outcome = service.execute(spec)

        # Fatal failure stops remaining batch
        assert outcome.status == "failed"
        assert outcome.summary is not None
        assert outcome.summary.total == 2
        assert outcome.summary.failed == 1
        assert outcome.summary.unprocessed == 1

        # File 1 failed with SOURCE_INTEGRITY_FAILED
        res1 = outcome.file_results[0]
        assert res1.input == "part1.par"
        assert res1.status == "failed"
        assert any(e.code == "SOURCE_INTEGRITY_FAILED" for e in res1.errors)

        # File 2 was moved to unprocessed
        assert len(outcome.unprocessed_files) == 1
        assert outcome.unprocessed_files[0] == "part2.par"

        # Runtime closed document
        assert len(runtime.close_calls) == 1

    def test_service_cancellation_avoids_unopened_file_hashing(self, tmp_path: Path) -> None:
        """Cancellation after one completed file proves untouched next file is neither hashed nor staged."""
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        (in_dir / "part1.par").write_bytes(b"part 1 cad data")
        (in_dir / "part2.par").write_bytes(b"part 2 cad data")

        runtime = FakeCADRuntime()
        boundary = FilesystemBatchSafetyBoundary()
        bindings, _ = _make_workspace_test_bindings(boundary)

        cancelled = False

        def check_cancellation() -> bool:
            return cancelled

        def on_progress(update: BatchProgressUpdate) -> None:
            nonlocal cancelled
            # Trigger cancellation immediately after file 1 finishes completely
            if update.phase == "file_finished" and update.current_file == "part1.par":
                cancelled = True

        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
            cancellation_check=check_cancellation,
            observer=on_progress,
        )

        spec = _make_spec(
            in_dir,
            out_dir,
            inputs=("part1.par", "part2.par"),
            formats=("step",),
        )

        # Spy on capture_source_snapshot to verify file 2 was never hashed
        with patch("batch.safety.capture_source_snapshot", wraps=capture_source_snapshot) as spy_snapshot:
            outcome = service.execute(spec)

        assert outcome.status == "cancelled"
        assert outcome.summary is not None
        assert outcome.summary.total == 2
        assert outcome.summary.accepted == 1
        assert outcome.summary.cancelled == 1
        assert outcome.cancelled_files == ("part2.par",)

        # File 1 completed and published
        assert (out_dir / "part1.step").exists()
        assert (out_dir / "part1.step").read_bytes() == b"generated content for step"

        # File 2 was untouched: target does not exist
        assert not (out_dir / "part2.step").exists()

        # Zero private work directories or staging residue remain in output tree
        work_dirs = list(out_dir.glob(".cad-copilot-work-*"))
        assert len(work_dirs) == 0

        # Snapshot hashing was called for part1.par (pre-open and post-close), but NEVER for part2.par
        hashed_files = [call.args[0].name for call in spy_snapshot.call_args_list]
        assert "part2.par" not in hashed_files
        assert hashed_files == ["part1.par", "part1.par"]

        # Runtime opened only part1.par; part2.par was never opened
        opened_names = [p.name for p in runtime.open_calls]
        assert "part2.par" not in opened_names
        assert opened_names == ["part1.par"]

    def test_service_sec07_diagnostic_sanitization(self, tmp_path: Path) -> None:
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        (in_dir / "bad.par").write_bytes(b"bad part")

        runtime = FakeCADRuntime()
        boundary = FilesystemBatchSafetyBoundary()
        bindings, _ = _make_workspace_test_bindings(boundary, fail_on_format={"step"})

        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = _make_spec(
            in_dir,
            out_dir,
            inputs=("bad.par",),
            formats=("step",),
        )
        outcome = service.execute(spec)

        for res in outcome.file_results:
            for err in res.errors:
                assert str(in_dir) not in err.message
                assert str(out_dir) not in err.message
                assert "C:\\" not in err.message
                assert "E:\\" not in err.message

    def test_service_hook_ordering_lifecycle_instrumentation(self, tmp_path: Path) -> None:
        """Verify strict ordering: verify_before_open -> runtime.open_document -> handler -> runtime.close_document -> verify_after_close."""
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        (in_dir / "part.par").write_bytes(b"cad part content")

        events: list[str] = []

        class LifecycleInstrumentedRuntime(FakeCADRuntime):
            def open_document(self, app_handle: object, file_path: Path) -> object:
                events.append(f"runtime:open_document:{file_path.name}")
                return super().open_document(app_handle, file_path)

            def close_document(self, doc_handle: object) -> None:
                events.append("runtime:close_document")
                super().close_document(doc_handle)

        class InstrumentedBoundary(FilesystemBatchSafetyBoundary):
            def verify_before_open(self, file: PreparedBatchFile) -> BatchDiagnostic | None:
                events.append(f"boundary:verify_before_open:{file.input}")
                return super().verify_before_open(file)

            def verify_after_close(self, file: PreparedBatchFile) -> BatchDiagnostic | None:
                events.append(f"boundary:verify_after_close:{file.input}")
                return super().verify_after_close(file)

        boundary = InstrumentedBoundary()

        def on_export(context: BatchItemContext) -> None:
            events.append(f"handler:export:{context.format}")

        bindings, _ = _make_workspace_test_bindings(boundary, on_export=on_export)
        runtime = LifecycleInstrumentedRuntime()

        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = _make_spec(
            in_dir,
            out_dir,
            inputs=("part.par",),
            formats=("step", "stl"),
        )
        outcome = service.execute(spec)

        assert outcome.status == "completed"
        assert outcome.summary is not None
        assert outcome.summary.accepted == 1

        assert events == [
            "boundary:verify_before_open:part.par",
            "runtime:open_document:part.par",
            "handler:export:step",
            "handler:export:stl",
            "runtime:close_document",
            "boundary:verify_after_close:part.par",
        ]

    def test_service_first_file_open_failure_retains_snapshot_and_isolates_second_file(self, tmp_path: Path) -> None:
        """First file runtime.open_document failure retains before-open snapshot and isolates second file."""
        in_dir = tmp_path / "input"
        out_dir = tmp_path / "output"
        in_dir.mkdir()
        out_dir.mkdir()

        (in_dir / "part1.par").write_bytes(b"cad model 1")
        (in_dir / "part2.par").write_bytes(b"cad model 2")

        boundary = FilesystemBatchSafetyBoundary()
        bindings, _ = _make_workspace_test_bindings(boundary)

        class OpenFailureRuntime(FakeCADRuntime):
            def open_document(self, app_handle: object, file_path: Path) -> object:
                if file_path.name == "part1.par":
                    raise RuntimeError("Simulated document open failure for part1.par")
                return super().open_document(app_handle, file_path)

        runtime = OpenFailureRuntime()
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = _make_spec(
            in_dir,
            out_dir,
            inputs=("part1.par", "part2.par"),
            formats=("step",),
            continue_on_error=True,
        )

        outcome = service.execute(spec)

        assert outcome.status == "completed"
        assert outcome.summary is not None
        assert outcome.summary.total == 2
        assert outcome.summary.accepted == 1
        assert outcome.summary.failed == 1
        assert outcome.summary.unprocessed == 0

        res1, res2 = outcome.file_results
        assert res1.input == "part1.par"
        assert res1.status == "failed"
        assert len(res1.artifacts) == 0
        assert any(e.code == "DOCUMENT_OPEN_FAILED" for e in res1.errors)

        assert res2.input == "part2.par"
        assert res2.status == "accepted"
        assert len(res2.artifacts) == 1
        assert (out_dir / "part2.step").exists()
        assert (out_dir / "part2.step").read_bytes() == b"generated content for step"

        # Boundary state verification:
        # File 1 never reached post-close verification, so before-open snapshot is retained and state is 'opened'
        info1 = boundary._files["part1.par"]
        assert info1.state == "opened"
        assert info1.before_open_snapshot is not None

        # File 2 completed lifecycle normally, so snapshot is cleared and state is 'closed'
        info2 = boundary._files["part2.par"]
        assert info2.state == "closed"
        assert info2.before_open_snapshot is None
