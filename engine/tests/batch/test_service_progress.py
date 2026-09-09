"""Unit tests for BatchService progress observation, phase ordering, and error isolation."""

from __future__ import annotations

from pathlib import Path

from batch.execution import BatchProgressUpdate
from batch.models import BatchDiagnostic
from batch.service import BatchService
from tests.batch.fake_support import (
    FakeCADRuntime,
    FakeSafetyBoundary,
    make_execution_spec,
    make_test_bindings,
)


class TestServiceProgress:
    def test_single_file_deterministic_phase_ordering(self, tmp_path: Path) -> None:
        updates: list[BatchProgressUpdate] = []
        runtime = FakeCADRuntime()
        boundary = FakeSafetyBoundary(tmp_path)
        bindings, _ = make_test_bindings()

        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
            observer=updates.append,
        )

        spec = make_execution_spec(inputs=("part1.par",), formats=("step", "stl"))
        outcome = service.execute(spec)
        assert outcome.status == "completed"

        phases = [u.phase for u in updates]
        assert phases == [
            "batch_started",
            "file_started",
            "format_started",  # step
            "format_finished",  # step
            "format_started",  # stl
            "format_finished",  # stl
            "file_finished",
            "batch_finished",
        ]

        # Verify phase fields
        assert updates[0].phase == "batch_started"
        assert updates[0].total_files == 1
        assert updates[0].completed_files == 0
        assert updates[0].current_file is None

        assert updates[1].phase == "file_started"
        assert updates[1].current_file == "part1.par"
        assert updates[1].current_format is None

        assert updates[2].phase == "format_started"
        assert updates[2].current_file == "part1.par"
        assert updates[2].current_format == "step"

        assert updates[3].phase == "format_finished"
        assert updates[3].current_format == "step"

        assert updates[4].phase == "format_started"
        assert updates[4].current_format == "stl"

        assert updates[5].phase == "format_finished"
        assert updates[5].current_format == "stl"

        assert updates[6].phase == "file_finished"
        assert updates[6].current_file == "part1.par"
        assert updates[6].file_status == "accepted"
        assert updates[6].completed_files == 1

        assert updates[7].phase == "batch_finished"
        assert updates[7].completed_files == 1

    def test_multi_file_deterministic_phase_ordering(self, tmp_path: Path) -> None:
        updates: list[BatchProgressUpdate] = []
        runtime = FakeCADRuntime()
        boundary = FakeSafetyBoundary(tmp_path)
        bindings, _ = make_test_bindings()

        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
            observer=updates.append,
        )

        spec = make_execution_spec(inputs=("f1.par", "f2.par"), formats=("step",))
        outcome = service.execute(spec)
        assert outcome.status == "completed"

        phases = [u.phase for u in updates]
        assert phases == [
            "batch_started",
            "file_started",
            "format_started",
            "format_finished",
            "file_finished",
            "file_started",
            "format_started",
            "format_finished",
            "file_finished",
            "batch_finished",
        ]

        # File 1 completed_files == 1, File 2 completed_files == 2
        file_finished_events = [u for u in updates if u.phase == "file_finished"]
        assert len(file_finished_events) == 2
        assert file_finished_events[0].completed_files == 1
        assert file_finished_events[0].current_file == "f1.par"
        assert file_finished_events[1].completed_files == 2
        assert file_finished_events[1].current_file == "f2.par"

    def test_preflight_blocked_format_emits_balanced_events(self, tmp_path: Path) -> None:
        """A preflight-blocked target format emits balanced format_started and format_finished."""
        blocked_err = BatchDiagnostic(code="TARGET_ALREADY_EXISTS", message="Target already exists")
        boundary = FakeSafetyBoundary(
            tmp_path,
            preflight_format_errors={("part1.par", "stl"): blocked_err},
        )
        runtime = FakeCADRuntime()
        bindings, _ = make_test_bindings()
        updates: list[BatchProgressUpdate] = []

        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
            observer=updates.append,
        )

        spec = make_execution_spec(inputs=("part1.par",), formats=("step", "stl"))
        outcome = service.execute(spec)

        assert outcome.status == "completed"
        # Balanced format_started and format_finished for blocked stl
        stl_events = [u for u in updates if u.current_format == "stl"]
        assert len(stl_events) == 2
        assert stl_events[0].phase == "format_started"
        assert stl_events[1].phase == "format_finished"

    def test_faulty_observer_exceptions_do_not_disrupt_execution(self, tmp_path: Path) -> None:
        """An observer throwing an exception must never interrupt execution or corrupt outcome."""
        runtime = FakeCADRuntime()
        boundary = FakeSafetyBoundary(tmp_path)
        bindings, _ = make_test_bindings()

        def exploding_observer(update: BatchProgressUpdate) -> None:
            raise RuntimeError(f"Observer crashed on {update.phase}")

        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
            observer=exploding_observer,
        )

        spec = make_execution_spec(inputs=("part1.par", "part2.par"))
        outcome = service.execute(spec)

        # Execution succeeded completely despite exploding observer
        assert outcome.status == "completed"
        assert outcome.summary is not None
        assert outcome.summary.accepted == 2
        assert len(outcome.file_results) == 2
        assert runtime.teardown_calls == [True]

    def test_progress_update_serialization_to_dict(self) -> None:
        """Progress update to_dict serialization omits None fields and matches wire payload."""
        update = BatchProgressUpdate(
            request_id="req-123",
            phase="file_started",
            total_files=5,
            completed_files=2,
            current_file="parts/bracket.par",
        )

        d = update.to_dict()
        assert d == {
            "request_id": "req-123",
            "phase": "file_started",
            "total_files": 5,
            "completed_files": 2,
            "current_file": "parts/bracket.par",
        }
        assert "current_format" not in d
        assert "file_status" not in d
