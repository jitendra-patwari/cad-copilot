"""Unit tests for BatchService cooperative cancellation, precedence rules, and mid-file semantics."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from batch.execution import BatchItemContext
from batch.service import BatchService
from tests.batch.fake_support import (
    FakeCADRuntime,
    FakeSafetyBoundary,
    make_execution_spec,
    make_test_bindings,
)


class TestServiceCancellation:
    def test_cancellation_before_connect(self, tmp_path: Path) -> None:
        runtime = FakeCADRuntime()
        boundary = FakeSafetyBoundary(tmp_path)
        bindings, _ = make_test_bindings()

        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
            cancellation_check=lambda: True,  # Already cancelled
        )

        spec = make_execution_spec(inputs=("part1.par", "part2.par"))
        outcome = service.execute(spec)

        assert outcome.status == "cancelled"
        assert outcome.summary is not None
        assert outcome.summary.total == 2
        assert outcome.summary.cancelled == 2
        assert outcome.summary.accepted == 0
        assert outcome.cancelled_files == ("part1.par", "part2.par")
        assert len(outcome.file_results) == 0
        assert len(outcome.errors) == 0

        # Runtime never connected or created
        assert runtime.connect_calls == 0
        assert len(runtime.open_calls) == 0

    def test_cancellation_between_files(self, tmp_path: Path) -> None:
        cancel_requested = False

        def close_and_cancel(handle: Any) -> None:
            nonlocal cancel_requested
            if "part1.par" in str(handle.path):
                cancel_requested = True

        runtime = FakeCADRuntime(on_close=close_and_cancel)
        boundary = FakeSafetyBoundary(tmp_path)
        bindings, _ = make_test_bindings()

        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
            cancellation_check=lambda: cancel_requested,
        )

        spec = make_execution_spec(inputs=("part1.par", "part2.par", "part3.par"))
        outcome = service.execute(spec)

        assert outcome.status == "cancelled"
        assert outcome.summary is not None
        assert outcome.summary.total == 3
        assert outcome.summary.accepted == 1
        assert outcome.summary.cancelled == 2
        assert outcome.cancelled_files == ("part2.par", "part3.par")

        assert len(outcome.file_results) == 1
        assert outcome.file_results[0].input == "part1.par"
        assert outcome.file_results[0].status == "accepted"

        # part2 and part3 were never opened
        assert len(runtime.open_calls) == 1
        assert runtime.open_calls[0].name == "part1.par"
        assert runtime.teardown_calls == [True]

    def test_mid_file_cancellation_after_success_yields_partial_and_cancelled_batch(self, tmp_path: Path) -> None:
        cancel_requested = False

        def cancel_after_step(ctx: BatchItemContext) -> None:
            nonlocal cancel_requested
            if ctx.input == "part1.par" and ctx.format == "step":
                cancel_requested = True

        runtime = FakeCADRuntime()
        boundary = FakeSafetyBoundary(tmp_path)
        bindings, _ = make_test_bindings(on_format=cancel_after_step)

        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
            cancellation_check=lambda: cancel_requested,
        )

        spec = make_execution_spec(inputs=("part1.par", "part2.par"), formats=("step", "stl"))
        outcome = service.execute(spec)

        assert outcome.status == "cancelled"
        assert outcome.summary is not None
        assert outcome.summary.total == 2
        assert outcome.summary.partial == 1
        assert outcome.summary.cancelled == 1
        assert outcome.cancelled_files == ("part2.par",)

        assert len(outcome.file_results) == 1
        res = outcome.file_results[0]
        assert res.input == "part1.par"
        assert res.status == "partial"
        assert len(res.artifacts) == 1
        assert res.artifacts[0].format == "step"

        # Invariant: BATCH_CANCELLED attached to the first skipped format (stl)
        assert len(res.errors) == 1
        assert res.errors[0].code == "BATCH_CANCELLED"
        assert res.errors[0].format == "stl"

    def test_mid_file_cancellation_on_final_file(self, tmp_path: Path) -> None:
        """Mid-file cancellation on the final file yields empty cancelled_files with valid cancelled outcome."""
        cancel_requested = False

        def cancel_on_step(ctx: BatchItemContext) -> None:
            nonlocal cancel_requested
            if ctx.format == "step":
                cancel_requested = True

        runtime = FakeCADRuntime()
        boundary = FakeSafetyBoundary(tmp_path)
        bindings, _ = make_test_bindings(on_format=cancel_on_step)

        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
            cancellation_check=lambda: cancel_requested,
        )

        # Single file request: part1 only
        spec = make_execution_spec(inputs=("part1.par",), formats=("step", "stl"))
        outcome = service.execute(spec)

        assert outcome.status == "cancelled"
        assert outcome.summary is not None
        assert outcome.summary.total == 1
        assert outcome.summary.partial == 1
        assert outcome.summary.cancelled == 0
        assert outcome.cancelled_files == ()

        res = outcome.file_results[0]
        assert res.status == "partial"
        assert res.errors[0].code == "BATCH_CANCELLED"
        assert res.errors[0].format == "stl"

    def test_late_cancellation_after_final_file_completed_remains_completed(self, tmp_path: Path) -> None:
        """Cancellation observed after all formats of final file completed does not cancel completed work."""
        cancel_requested = False

        def cancel_on_close(handle: Any) -> None:
            nonlocal cancel_requested
            cancel_requested = True

        runtime = FakeCADRuntime(on_close=cancel_on_close)
        boundary = FakeSafetyBoundary(tmp_path)
        bindings, _ = make_test_bindings()

        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
            cancellation_check=lambda: cancel_requested,
        )

        spec = make_execution_spec(inputs=("part1.par",), formats=("step", "stl"))
        outcome = service.execute(spec)

        # All formats ran successfully and closed cleanly; remains completed
        assert outcome.status == "completed"
        assert outcome.summary is not None
        assert outcome.summary.accepted == 1
        assert outcome.summary.cancelled == 0

    def test_fatal_close_failure_supersedes_cancellation(self, tmp_path: Path) -> None:
        """Fatal close failure overrides cancellation: terminal status is failed, untouched files are unprocessed."""
        cancel_requested = False

        def cancel_on_step(ctx: BatchItemContext) -> None:
            nonlocal cancel_requested
            if ctx.format == "step":
                cancel_requested = True

        runtime = FakeCADRuntime(fail_close_inputs={"part1.par"})
        boundary = FakeSafetyBoundary(tmp_path)
        bindings, _ = make_test_bindings(on_format=cancel_on_step)

        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
            cancellation_check=lambda: cancel_requested,
        )

        spec = make_execution_spec(inputs=("part1.par", "part2.par"), formats=("step", "stl"))
        outcome = service.execute(spec)

        # Close failed on part1 -> fatal failure supersedes cancellation
        assert outcome.status == "failed"
        assert outcome.summary is not None
        assert outcome.summary.failed == 1
        assert outcome.summary.unprocessed == 1
        assert outcome.summary.cancelled == 0
        assert outcome.cancelled_files == ()
        assert outcome.unprocessed_files == ("part2.par",)

        # BATCH_CANCELLED was recorded before close failure, so it remains in file_results
        res = outcome.file_results[0]
        assert any(e.code == "BATCH_CANCELLED" for e in res.errors)
        assert any(e.code == "DOCUMENT_CLOSE_FAILED" for e in res.errors)
        assert any(e.code == "DOCUMENT_CLOSE_FAILED" for e in outcome.errors)

    def test_cancellation_check_exception_fails_closed(self, tmp_path: Path) -> None:
        """If cancellation_check raises, it fails closed as an INTERNAL_ERROR."""
        runtime = FakeCADRuntime()
        boundary = FakeSafetyBoundary(tmp_path)
        bindings, _ = make_test_bindings()

        def exploding_check() -> bool:
            raise RuntimeError("Corrupted cancellation channel")

        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
            cancellation_check=exploding_check,
        )

        spec = make_execution_spec(inputs=("part1.par", "part2.par"))
        outcome = service.execute(spec)

        assert outcome.status == "failed"
        assert any(e.code == "INTERNAL_ERROR" for e in outcome.errors)

    def test_cancellation_check_exception_during_format_iteration_creates_valid_failed_file_result(
        self, tmp_path: Path
    ) -> None:
        """If cancellation_check raises mid-file during format dispatch, active file is failed with INTERNAL_ERROR."""
        runtime = FakeCADRuntime()
        boundary = FakeSafetyBoundary(tmp_path)
        bindings, _ = make_test_bindings()

        check_count = 0

        def explode_on_format_check() -> bool:
            nonlocal check_count
            check_count += 1
            # 1: before connect, 2: before file 1 open, 3: before format 1 dispatch
            if check_count >= 3:
                raise RuntimeError("Corrupted cancellation channel during format iteration")
            return False

        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
            cancellation_check=explode_on_format_check,
        )

        spec = make_execution_spec(inputs=("part1.par", "part2.par"), formats=("step", "stl"))
        outcome = service.execute(spec)

        assert outcome.status == "failed"
        assert outcome.summary is not None
        assert outcome.summary.failed == 1
        assert outcome.summary.unprocessed == 1
        assert outcome.unprocessed_files == ("part2.par",)
        assert len(outcome.file_results) == 1
        res = outcome.file_results[0]
        assert res.status == "failed"
        assert len(res.errors) >= 1
        assert any(e.code == "INTERNAL_ERROR" for e in res.errors)
        assert any(e.code == "INTERNAL_ERROR" for e in outcome.errors)
