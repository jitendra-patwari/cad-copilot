"""Unit tests for BatchService failure modes, error isolation, policy handling, and teardown."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from batch.models import BatchDiagnostic
from batch.service import BatchService
from tests.batch.fake_support import (
    FakeCADRuntime,
    FakeSafetyBoundary,
    make_execution_spec,
    make_test_bindings,
)


class TestServiceFailures:
    def test_connect_failure_returns_early_failed(self, tmp_path: Path) -> None:
        runtime = FakeCADRuntime(fail_connect=True)
        boundary = FakeSafetyBoundary(tmp_path)
        bindings, _ = make_test_bindings()
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = make_execution_spec(inputs=("part1.par", "part2.par"))
        outcome = service.execute(spec)

        assert outcome.status == "failed"
        assert outcome.summary is None
        assert len(outcome.file_results) == 0
        assert len(outcome.unprocessed_files) == 0
        assert len(outcome.cancelled_files) == 0
        assert len(outcome.errors) == 1
        assert outcome.errors[0].code == "SOLID_EDGE_UNAVAILABLE"
        assert runtime.teardown_calls == [True]

    def test_connect_returns_none_returns_early_failed(self, tmp_path: Path) -> None:
        runtime = FakeCADRuntime(connect_returns_none=True)
        boundary = FakeSafetyBoundary(tmp_path)
        bindings, _ = make_test_bindings()
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = make_execution_spec(inputs=("part.par",))
        outcome = service.execute(spec)

        assert outcome.status == "failed"
        assert outcome.summary is None
        assert outcome.errors[0].code == "SOLID_EDGE_UNAVAILABLE"
        assert runtime.teardown_calls == [True]

    def test_runtime_unhealthy_on_connect_returns_early_failed(self, tmp_path: Path) -> None:
        runtime = FakeCADRuntime(healthy=False)
        boundary = FakeSafetyBoundary(tmp_path)
        bindings, _ = make_test_bindings()
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = make_execution_spec(inputs=("part.par",))
        outcome = service.execute(spec)

        assert outcome.status == "failed"
        assert outcome.summary is None
        assert outcome.errors[0].code == "SOLID_EDGE_UNAVAILABLE"
        assert runtime.teardown_calls == [True]

    def test_preflight_file_error_skips_open_and_continues(self, tmp_path: Path) -> None:
        file_err = BatchDiagnostic(code="INPUT_FILE_NOT_FOUND", message="File does not exist")
        boundary = FakeSafetyBoundary(
            tmp_path,
            preflight_file_errors={"part1.par": file_err},
        )
        runtime = FakeCADRuntime()
        bindings, _ = make_test_bindings()
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = make_execution_spec(inputs=("part1.par", "part2.par"), continue_on_error=True)
        outcome = service.execute(spec)

        assert outcome.status == "completed"
        assert outcome.summary is not None
        assert outcome.summary.total == 2
        assert outcome.summary.failed == 1
        assert outcome.summary.accepted == 1

        assert outcome.file_results[0].status == "failed"
        assert outcome.file_results[0].errors[0].code == "INPUT_FILE_NOT_FOUND"
        assert outcome.file_results[1].status == "accepted"

        # part1 was skipped from opening; only part2 was opened
        assert len(runtime.open_calls) == 1
        assert runtime.open_calls[0].name == "part2.par"

    def test_preflight_file_error_stops_when_continue_on_error_false(self, tmp_path: Path) -> None:
        file_err = BatchDiagnostic(code="INPUT_FILE_NOT_FOUND", message="File does not exist")
        boundary = FakeSafetyBoundary(
            tmp_path,
            preflight_file_errors={"part1.par": file_err},
        )
        runtime = FakeCADRuntime()
        bindings, _ = make_test_bindings()
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = make_execution_spec(inputs=("part1.par", "part2.par"), continue_on_error=False)
        outcome = service.execute(spec)

        assert outcome.status == "completed"  # Policy honored cleanly
        assert outcome.summary is not None
        assert outcome.summary.total == 2
        assert outcome.summary.failed == 1
        assert outcome.summary.unprocessed == 1
        assert outcome.unprocessed_files == ("part2.par",)
        assert len(runtime.open_calls) == 0

    def test_pre_open_safety_check_failure(self, tmp_path: Path) -> None:
        pre_open_err = BatchDiagnostic(code="INPUT_PATH_NOT_ALLOWED", message="Path verification failed")
        boundary = FakeSafetyBoundary(
            tmp_path,
            fail_before_open={"part1.par": pre_open_err},
        )
        runtime = FakeCADRuntime()
        bindings, _ = make_test_bindings()
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = make_execution_spec(inputs=("part1.par", "part2.par"), continue_on_error=True)
        outcome = service.execute(spec)

        assert outcome.status == "completed"
        assert outcome.summary is not None
        assert outcome.summary.failed == 1
        assert outcome.summary.accepted == 1
        assert outcome.file_results[0].errors[0].code == "INPUT_PATH_NOT_ALLOWED"
        assert len(runtime.open_calls) == 1
        assert runtime.open_calls[0].name == "part2.par"

    def test_document_open_failure_honors_continue_policy(self, tmp_path: Path) -> None:
        runtime = FakeCADRuntime(fail_open_inputs={"part1.par"})
        boundary = FakeSafetyBoundary(tmp_path)
        bindings, _ = make_test_bindings()
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        # continue_on_error=True
        spec = make_execution_spec(inputs=("part1.par", "part2.par"), continue_on_error=True)
        outcome = service.execute(spec)

        assert outcome.status == "completed"
        assert outcome.summary is not None
        assert outcome.summary.total == 2
        assert outcome.summary.failed == 1
        assert outcome.summary.accepted == 1
        assert outcome.file_results[0].errors[0].code == "DOCUMENT_OPEN_FAILED"
        assert outcome.file_results[1].status == "accepted"

    def test_document_open_failure_stops_when_continue_false(self, tmp_path: Path) -> None:
        runtime = FakeCADRuntime(fail_open_inputs={"part1.par"})
        boundary = FakeSafetyBoundary(tmp_path)
        bindings, _ = make_test_bindings()
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = make_execution_spec(inputs=("part1.par", "part2.par"), continue_on_error=False)
        outcome = service.execute(spec)

        assert outcome.status == "completed"
        assert outcome.summary is not None
        assert outcome.summary.failed == 1
        assert outcome.summary.unprocessed == 1
        assert outcome.unprocessed_files == ("part2.par",)

    def test_single_format_failure_produces_partial_result(self, tmp_path: Path) -> None:
        fmt_fail = BatchDiagnostic(
            code="ARTIFACT_EXPORT_FAILED",
            message="STEP export failed",
            format="step",
        )
        runtime = FakeCADRuntime()
        boundary = FakeSafetyBoundary(tmp_path)
        bindings, _ = make_test_bindings(fail_formats={("part1.par", "step"): fmt_fail})
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = make_execution_spec(inputs=("part1.par",), formats=("step", "stl"))
        outcome = service.execute(spec)

        assert outcome.status == "completed"
        assert outcome.summary is not None
        assert outcome.summary.partial == 1
        assert outcome.summary.accepted == 0
        assert outcome.summary.failed == 0

        res = outcome.file_results[0]
        assert res.status == "partial"
        assert len(res.artifacts) == 1
        assert res.artifacts[0].format == "stl"
        assert len(res.errors) == 1
        assert res.errors[0].code == "ARTIFACT_EXPORT_FAILED"

    def test_all_formats_fail_produces_failed_result(self, tmp_path: Path) -> None:
        runtime = FakeCADRuntime()
        boundary = FakeSafetyBoundary(tmp_path)
        bindings, _ = make_test_bindings(
            fail_formats={
                ("part1.par", "step"): BatchDiagnostic(
                    code="ARTIFACT_EXPORT_FAILED", message="step fail", format="step"
                ),
                ("part1.par", "stl"): BatchDiagnostic(code="ARTIFACT_EXPORT_FAILED", message="stl fail", format="stl"),
            }
        )
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = make_execution_spec(inputs=("part1.par",), formats=("step", "stl"))
        outcome = service.execute(spec)

        assert outcome.status == "completed"
        assert outcome.summary is not None
        assert outcome.summary.failed == 1
        assert outcome.file_results[0].status == "failed"
        assert len(outcome.file_results[0].artifacts) == 0

    def test_document_close_failure_is_fatal(self, tmp_path: Path) -> None:
        runtime = FakeCADRuntime(fail_close_inputs={"part1.par"})
        boundary = FakeSafetyBoundary(tmp_path)
        bindings, _ = make_test_bindings()
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = make_execution_spec(inputs=("part1.par", "part2.par"))
        outcome = service.execute(spec)

        assert outcome.status == "failed"
        assert outcome.summary is not None
        assert outcome.summary.failed == 1
        assert outcome.summary.unprocessed == 1
        assert outcome.unprocessed_files == ("part2.par",)
        assert any(e.code == "DOCUMENT_CLOSE_FAILED" for e in outcome.errors)
        assert any(e.code == "DOCUMENT_CLOSE_FAILED" for e in outcome.file_results[0].errors)
        # Invariant: failed file result must have 0 artifacts
        assert len(outcome.file_results[0].artifacts) == 0

    def test_source_integrity_failure_is_fatal(self, tmp_path: Path) -> None:
        integrity_err = BatchDiagnostic(
            code="SOURCE_INTEGRITY_FAILED",
            message="Source modified during execution",
        )
        runtime = FakeCADRuntime()
        boundary = FakeSafetyBoundary(tmp_path, fail_after_close={"part1.par": integrity_err})
        bindings, _ = make_test_bindings()
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = make_execution_spec(inputs=("part1.par", "part2.par"))
        outcome = service.execute(spec)

        assert outcome.status == "failed"
        assert outcome.summary is not None
        assert outcome.summary.failed == 1
        assert outcome.summary.unprocessed == 1
        assert outcome.unprocessed_files == ("part2.par",)
        assert any(e.code == "SOURCE_INTEGRITY_FAILED" for e in outcome.errors)
        assert outcome.file_results[0].status == "failed"
        assert len(outcome.file_results[0].artifacts) == 0

    def test_runtime_health_loss_is_fatal(self, tmp_path: Path) -> None:
        runtime = FakeCADRuntime()
        boundary = FakeSafetyBoundary(tmp_path)

        def crash_on_part1(ctx: Any) -> None:
            if ctx.input == "part1.par":
                runtime.healthy = False
                raise RuntimeError("CAD kernel crashed")

        bindings, _ = make_test_bindings(on_format=crash_on_part1)
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = make_execution_spec(inputs=("part1.par", "part2.par"))
        outcome = service.execute(spec)

        assert outcome.status == "failed"
        assert outcome.summary is not None
        assert outcome.summary.total == 2
        assert outcome.summary.failed == 1
        assert outcome.summary.unprocessed == 1
        assert outcome.unprocessed_files == ("part2.par",)
        assert len(outcome.file_results) == 1
        assert outcome.file_results[0].input == "part1.par"
        assert outcome.file_results[0].status == "failed"
        assert any(e.code == "SOLID_EDGE_UNHEALTHY" for e in outcome.errors)

    def test_teardown_failure_upgrades_terminal_status_to_failed(self, tmp_path: Path) -> None:
        runtime = FakeCADRuntime(fail_teardown=True)
        boundary = FakeSafetyBoundary(tmp_path)
        bindings, _ = make_test_bindings()
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = make_execution_spec(inputs=("part1.par",))
        outcome = service.execute(spec)

        # Even though part1 succeeded, incomplete teardown marks terminal outcome failed
        assert outcome.status == "failed"
        assert outcome.summary is not None
        assert outcome.summary.accepted == 1
        assert any(e.code == "INTERNAL_ERROR" for e in outcome.errors)
        assert runtime.teardown_calls == [True]

    def test_safety_boundary_protocol_validation_in_init(self, tmp_path: Path) -> None:
        import pytest

        runtime = FakeCADRuntime()
        bindings, _ = make_test_bindings()

        class IncompleteBoundary:
            def prepare(self, spec: Any, work: Any) -> Any:
                pass

            def verify_before_open(self, f: Any) -> Any:
                pass

            # Missing verify_after_close

        with pytest.raises(TypeError, match="verify_after_close"):
            BatchService(
                runtime_factory=lambda: runtime,
                safety_boundary=IncompleteBoundary(),  # type: ignore[arg-type]
                bindings=bindings,
            )

        class NonCallableBoundary:
            def prepare(self, spec: Any, work: Any) -> Any:
                pass

            def verify_before_open(self, f: Any) -> Any:
                pass

            verify_after_close = "not_callable"

        with pytest.raises(TypeError, match="callable 'verify_after_close'"):
            BatchService(
                runtime_factory=lambda: runtime,
                safety_boundary=NonCallableBoundary(),  # type: ignore[arg-type]
                bindings=bindings,
            )

    def test_safety_boundary_unexpected_exception_is_fatal_internal_error(self, tmp_path: Path) -> None:
        boundary = FakeSafetyBoundary(tmp_path, throw_before_open={"part1.par"})
        runtime = FakeCADRuntime()
        bindings, _ = make_test_bindings()
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = make_execution_spec(inputs=("part1.par", "part2.par"), continue_on_error=True)
        outcome = service.execute(spec)

        # Fatal collaborator failure stops execution even when continue_on_error is True
        assert outcome.status == "failed"
        assert outcome.summary is not None
        assert outcome.summary.failed == 1
        assert outcome.summary.unprocessed == 1
        assert outcome.unprocessed_files == ("part2.par",)
        assert any(e.code == "INTERNAL_ERROR" for e in outcome.errors)
        assert any(e.code == "INTERNAL_ERROR" for e in outcome.file_results[0].errors)
        assert outcome.file_results[0].status == "failed"
        # part2.par was never attempted
        assert len(outcome.file_results) == 1

    def test_safety_boundary_invalid_return_type_pre_open_is_fatal_internal_error(self, tmp_path: Path) -> None:
        boundary = FakeSafetyBoundary(tmp_path, fail_before_open={"part1.par": "invalid_string"})
        runtime = FakeCADRuntime()
        bindings, _ = make_test_bindings()
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = make_execution_spec(inputs=("part1.par", "part2.par"), continue_on_error=True)
        outcome = service.execute(spec)

        assert outcome.status == "failed"
        assert outcome.summary is not None
        assert outcome.summary.failed == 1
        assert outcome.summary.unprocessed == 1
        assert outcome.unprocessed_files == ("part2.par",)
        assert any(
            e.code == "INTERNAL_ERROR"
            and "Safety boundary returned invalid pre-open verification result for 'part1.par'" in e.message
            for e in outcome.errors
        )
        assert len(outcome.file_results) == 1
        file_res = outcome.file_results[0]
        assert file_res.status == "failed"
        assert any(e.code == "INTERNAL_ERROR" for e in file_res.errors)
        assert len(runtime.open_calls) == 0
        assert runtime.teardown_calls == [True]

    def test_safety_boundary_invalid_return_type_post_close_is_fatal_internal_error(self, tmp_path: Path) -> None:
        boundary = FakeSafetyBoundary(tmp_path, fail_after_close={"part1.par": "invalid_string"})
        runtime = FakeCADRuntime()
        bindings, _ = make_test_bindings()
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = make_execution_spec(inputs=("part1.par", "part2.par"), continue_on_error=True)
        outcome = service.execute(spec)

        assert outcome.status == "failed"
        assert outcome.summary is not None
        assert outcome.summary.failed == 1
        assert outcome.summary.unprocessed == 1
        assert outcome.unprocessed_files == ("part2.par",)
        assert any(
            e.code == "INTERNAL_ERROR"
            and "Safety boundary returned invalid post-close verification result for 'part1.par'" in e.message
            for e in outcome.errors
        )
        assert len(outcome.file_results) == 1
        file_res = outcome.file_results[0]
        assert file_res.status == "failed"
        assert any(e.code == "INTERNAL_ERROR" for e in file_res.errors)
        assert len(runtime.close_calls) == 1
        assert runtime.teardown_calls == [True]

    def test_safety_boundary_warning_diagnostic_pre_open_is_fatal_internal_error(self, tmp_path: Path) -> None:
        warn = BatchDiagnostic(code="VERSION_METADATA_UNAVAILABLE", message="Misplaced warning")
        boundary = FakeSafetyBoundary(tmp_path, fail_before_open={"part1.par": warn})
        runtime = FakeCADRuntime()
        bindings, _ = make_test_bindings()
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = make_execution_spec(inputs=("part1.par", "part2.par"), continue_on_error=True)
        outcome = service.execute(spec)

        assert outcome.status == "failed"
        assert outcome.summary is not None
        assert outcome.summary.failed == 1
        assert outcome.summary.unprocessed == 1
        assert outcome.unprocessed_files == ("part2.par",)
        assert any(
            e.code == "INTERNAL_ERROR"
            and "Safety boundary returned invalid pre-open verification result for 'part1.par'" in e.message
            for e in outcome.errors
        )
        assert len(outcome.file_results) == 1
        file_res = outcome.file_results[0]
        assert file_res.status == "failed"
        assert any(e.code == "INTERNAL_ERROR" for e in file_res.errors)
        assert not any(e.code == "VERSION_METADATA_UNAVAILABLE" for e in file_res.errors)
        assert len(runtime.open_calls) == 0
        assert runtime.teardown_calls == [True]

    def test_safety_boundary_warning_diagnostic_post_close_is_fatal_internal_error(self, tmp_path: Path) -> None:
        warn = BatchDiagnostic(code="VERSION_METADATA_UNAVAILABLE", message="Misplaced warning")
        boundary = FakeSafetyBoundary(tmp_path, fail_after_close={"part1.par": warn})
        runtime = FakeCADRuntime()
        bindings, _ = make_test_bindings()
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = make_execution_spec(inputs=("part1.par", "part2.par"), continue_on_error=True)
        outcome = service.execute(spec)

        assert outcome.status == "failed"
        assert outcome.summary is not None
        assert outcome.summary.failed == 1
        assert outcome.summary.unprocessed == 1
        assert outcome.unprocessed_files == ("part2.par",)
        assert any(
            e.code == "INTERNAL_ERROR"
            and "Safety boundary returned invalid post-close verification result for 'part1.par'" in e.message
            for e in outcome.errors
        )
        assert len(outcome.file_results) == 1
        file_res = outcome.file_results[0]
        assert file_res.status == "failed"
        assert any(e.code == "INTERNAL_ERROR" for e in file_res.errors)
        assert not any(e.code == "VERSION_METADATA_UNAVAILABLE" for e in file_res.errors)
        assert len(runtime.close_calls) == 1
        assert runtime.teardown_calls == [True]

    def test_safety_boundary_non_source_integrity_error_post_close_is_fatal_internal_error(
        self, tmp_path: Path
    ) -> None:
        other_err = BatchDiagnostic(code="ARTIFACT_EXPORT_FAILED", message="Wrong error code for post-close")
        boundary = FakeSafetyBoundary(tmp_path, fail_after_close={"part1.par": other_err})
        runtime = FakeCADRuntime()
        bindings, _ = make_test_bindings()
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = make_execution_spec(inputs=("part1.par", "part2.par"), continue_on_error=True)
        outcome = service.execute(spec)

        assert outcome.status == "failed"
        assert outcome.summary is not None
        assert outcome.summary.failed == 1
        assert outcome.summary.unprocessed == 1
        assert outcome.unprocessed_files == ("part2.par",)
        assert any(
            e.code == "INTERNAL_ERROR"
            and "Safety boundary returned invalid post-close verification result for 'part1.par'" in e.message
            for e in outcome.errors
        )
        assert len(runtime.close_calls) == 1
        assert runtime.teardown_calls == [True]

    def test_safety_boundary_prepare_with_warning_preflight_error_returns_sanitized_failed_outcome(
        self, tmp_path: Path
    ) -> None:
        class WarningPreflightBoundary(FakeSafetyBoundary):
            def prepare(self, spec: Any, work: Any) -> Any:
                prep = super().prepare(spec, work)
                warn = BatchDiagnostic(code="VERSION_METADATA_UNAVAILABLE", message="Warning")
                object.__setattr__(prep.files[0], "preflight_error", warn)
                return prep

        boundary = WarningPreflightBoundary(tmp_path)
        runtime = FakeCADRuntime()
        bindings, _ = make_test_bindings()
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = make_execution_spec(inputs=("part1.par", "part2.par"))
        outcome = service.execute(spec)

        assert outcome.status == "failed"
        assert outcome.summary is None
        assert any(
            e.code == "INTERNAL_ERROR" and "Safety boundary produced inconsistent prepared work" in e.message
            for e in outcome.errors
        )
        assert len(runtime.open_calls) == 0
        assert len(runtime.teardown_calls) == 0

    def test_execute_single_file_rejects_warning_preflight_errors_safely(self, tmp_path: Path) -> None:
        from batch.allocation import PreparedBatchFile, PreparedBatchFormat
        from batch.file_execution import execute_single_file

        runtime = FakeCADRuntime()
        boundary = FakeSafetyBoundary(tmp_path)
        bindings, _ = make_test_bindings()
        binding = bindings.get_binding("export_3d")
        handler = binding.create_handler(runtime)
        spec = make_execution_spec(inputs=("part1.par",))

        # Test warning in file preflight error
        prep_fmt = PreparedBatchFormat(
            format="step",
            target_relative_path="part1.step",
            work_path=tmp_path / "work" / "part1.step",
            target_path=tmp_path / "out" / "part1.step",
        )
        prep_file = PreparedBatchFile(
            input="part1.par",
            source_path=tmp_path / "src" / "part1.par",
            formats=(prep_fmt,),
        )
        warn = BatchDiagnostic(code="VERSION_METADATA_UNAVAILABLE", message="Warning")
        object.__setattr__(prep_file, "preflight_error", warn)

        file_res, fatal_err = execute_single_file(
            spec=spec,
            prep_file=prep_file,
            runtime=runtime,
            app_handle="app",
            handler=handler,
            completed_files_count=0,
            safety_boundary=boundary,
            is_cancelled=lambda: False,
            is_runtime_healthy=lambda r: True,
            notify=lambda u: None,
        )
        assert file_res.status == "failed"
        assert fatal_err is not None
        assert fatal_err.code == "INTERNAL_ERROR"
        assert "Prepared file contained invalid preflight error" in fatal_err.message

        # Test warning in format preflight error
        prep_fmt_bad = PreparedBatchFormat(
            format="step",
            target_relative_path="part1.step",
            work_path=tmp_path / "work" / "part1.step",
            target_path=tmp_path / "out" / "part1.step",
        )
        object.__setattr__(prep_fmt_bad, "preflight_error", warn)
        prep_file2 = PreparedBatchFile(
            input="part1.par",
            source_path=tmp_path / "src" / "part1.par",
            formats=(prep_fmt_bad,),
        )

        file_res2, fatal_err2 = execute_single_file(
            spec=spec,
            prep_file=prep_file2,
            runtime=runtime,
            app_handle="app",
            handler=handler,
            completed_files_count=0,
            safety_boundary=boundary,
            is_cancelled=lambda: False,
            is_runtime_healthy=lambda r: True,
            notify=lambda u: None,
        )
        assert file_res2.status == "failed"
        assert fatal_err2 is not None
        assert fatal_err2.code == "INTERNAL_ERROR"
        assert "Prepared format contained invalid preflight error" in fatal_err2.message

    def test_handler_returning_invalid_type_is_fatal_internal_error(self, tmp_path: Path) -> None:
        boundary = FakeSafetyBoundary(tmp_path)
        runtime = FakeCADRuntime()

        from batch.bindings import OperationBinding, OperationBindings
        from batch.registry import build_initial_registry

        reg = build_initial_registry()

        def invalid_handler(doc: Any, ctx: Any) -> Any:
            return "not_an_outcome"

        bindings_list = [
            OperationBinding(operation_id="export_3d", factory=lambda r: invalid_handler),  # type: ignore[arg-type]
            OperationBinding(operation_id="publish_drawing", factory=lambda r: lambda d, c: None),  # type: ignore[arg-type]
        ]
        op_bindings = OperationBindings(bindings_list, registry=reg)

        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=op_bindings,
        )

        spec = make_execution_spec(inputs=("part1.par", "part2.par"), continue_on_error=True)
        outcome = service.execute(spec)

        assert outcome.status == "failed"
        assert outcome.summary is not None
        assert outcome.summary.failed == 1
        assert outcome.summary.unprocessed == 1
        assert outcome.unprocessed_files == ("part2.par",)
        assert any(e.code == "INTERNAL_ERROR" for e in outcome.errors)
        assert any(e.code == "INTERNAL_ERROR" for e in outcome.file_results[0].errors)
        assert outcome.file_results[0].status == "failed"

    def test_handler_context_mismatch_is_fatal_internal_error(self, tmp_path: Path) -> None:
        boundary = FakeSafetyBoundary(tmp_path)
        runtime = FakeCADRuntime()

        from batch.bindings import OperationBinding, OperationBindings
        from batch.execution import BatchItemOutcome
        from batch.models import BatchArtifactRecord
        from batch.registry import build_initial_registry

        reg = build_initial_registry()

        def mismatched_handler(doc: Any, ctx: Any) -> Any:
            # Context requested 'step', but handler returns 'stl'
            return BatchItemOutcome.success(
                format="stl",
                artifact=BatchArtifactRecord(format="stl", path="some/path.stl"),
            )

        bindings_list = [
            OperationBinding(operation_id="export_3d", factory=lambda r: mismatched_handler),  # type: ignore[arg-type]
            OperationBinding(operation_id="publish_drawing", factory=lambda r: lambda d, c: None),  # type: ignore[arg-type]
        ]
        op_bindings = OperationBindings(bindings_list, registry=reg)

        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=op_bindings,
        )

        spec = make_execution_spec(inputs=("part1.par", "part2.par"), formats=("step",))
        outcome = service.execute(spec)

        assert outcome.status == "failed"
        assert outcome.summary is not None
        assert outcome.summary.failed == 1
        assert outcome.summary.unprocessed == 1
        assert outcome.unprocessed_files == ("part2.par",)
        assert any(e.code == "INTERNAL_ERROR" for e in outcome.errors)
        assert any(e.code == "INTERNAL_ERROR" for e in outcome.file_results[0].errors)
        err = next(e for e in outcome.errors if e.code == "INTERNAL_ERROR")
        assert err.message == "Handler outcome for format 'step' violated dispatch context"
        assert "some/path.stl" not in err.message
        assert "part1.par" not in err.message

    def test_typed_handler_failure_with_unhealthy_runtime_stops_immediately(self, tmp_path: Path) -> None:
        boundary = FakeSafetyBoundary(tmp_path)
        runtime = FakeCADRuntime()
        dispatched_formats: list[str] = []

        from batch.bindings import OperationBinding, OperationBindings
        from batch.execution import BatchItemOutcome
        from batch.models import BatchArtifactRecord, BatchDiagnostic
        from batch.registry import build_initial_registry

        reg = build_initial_registry()

        def poisoned_handler(doc: Any, ctx: Any) -> Any:
            dispatched_formats.append(ctx.format)
            if ctx.format == "step":
                runtime.healthy = False
                return BatchItemOutcome.failure(
                    format="step",
                    errors=(BatchDiagnostic(code="EXPORT_FAILED", message="Step exporter failed", format="step"),),
                )
            return BatchItemOutcome.success(
                format="stl",
                artifact=BatchArtifactRecord(format="stl", path=str(ctx.target_path)),
            )

        bindings_list = [
            OperationBinding(operation_id="export_3d", factory=lambda r: poisoned_handler),  # type: ignore[arg-type]
            OperationBinding(operation_id="publish_drawing", factory=lambda r: lambda d, c: None),  # type: ignore[arg-type]
        ]
        op_bindings = OperationBindings(bindings_list, registry=reg)

        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=op_bindings,
        )

        spec = make_execution_spec(inputs=("part1.par", "part2.par"), formats=("step", "stl"))
        outcome = service.execute(spec)

        # Dispatched only step for part1; stl was NOT dispatched because runtime became unhealthy!
        assert dispatched_formats == ["step"]
        assert outcome.status == "failed"
        assert outcome.summary is not None
        assert outcome.summary.failed == 1
        assert outcome.summary.unprocessed == 1
        assert outcome.unprocessed_files == ("part2.par",)
        assert any(e.code == "SOLID_EDGE_UNHEALTHY" for e in outcome.errors)
        assert any(e.code == "SOLID_EDGE_UNHEALTHY" for e in outcome.file_results[0].errors)

    def test_connect_failure_and_teardown_failure_preserves_both_errors(self, tmp_path: Path) -> None:
        runtime = FakeCADRuntime(fail_connect=True, fail_teardown=True)
        boundary = FakeSafetyBoundary(tmp_path)
        bindings, _ = make_test_bindings()
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = make_execution_spec(inputs=("part1.par", "part2.par"))
        outcome = service.execute(spec)

        assert outcome.status == "failed"
        assert outcome.summary is None
        assert runtime.teardown_calls == [True]
        codes = [e.code for e in outcome.errors]
        assert "SOLID_EDGE_UNAVAILABLE" in codes
        assert "INTERNAL_ERROR" in codes

    def test_post_close_unhealthy_runtime_creates_valid_failed_file_result(self, tmp_path: Path) -> None:
        def poison_after_close(handle: Any) -> None:
            runtime.healthy = False

        runtime = FakeCADRuntime(on_close=poison_after_close)
        boundary = FakeSafetyBoundary(tmp_path)
        bindings, _ = make_test_bindings()
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = make_execution_spec(inputs=("part1.par", "part2.par"))
        outcome = service.execute(spec)

        assert outcome.status == "failed"
        assert outcome.summary is not None
        assert outcome.summary.failed == 1
        assert outcome.summary.unprocessed == 1
        assert outcome.unprocessed_files == ("part2.par",)
        # Invariant: active file result must be failed AND have at least one error
        res = outcome.file_results[0]
        assert res.status == "failed"
        assert len(res.errors) >= 1
        assert any(e.code == "SOLID_EDGE_UNHEALTHY" for e in res.errors)
        assert any(e.code == "SOLID_EDGE_UNHEALTHY" for e in outcome.errors)

    def test_runtime_metadata_sanitization_removes_sensitive_paths_and_credentials(self, tmp_path: Path) -> None:
        # Sensitive path in build version
        runtime = FakeCADRuntime(version_build="C:/Users/name/token-secret")
        boundary = FakeSafetyBoundary(tmp_path)
        bindings, _ = make_test_bindings()
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = make_execution_spec(inputs=("part1.par",))
        outcome = service.execute(spec)

        assert outcome.cad_runtime_version_build is None
        assert any(w.code == "VERSION_METADATA_UNAVAILABLE" for w in outcome.warnings)

        # None build version
        runtime_none = FakeCADRuntime(version_build=None)
        service_none = BatchService(
            runtime_factory=lambda: runtime_none,
            safety_boundary=boundary,
            bindings=bindings,
        )
        outcome_none = service_none.execute(spec)
        assert outcome_none.cad_runtime_version_build is None
        assert any(w.code == "VERSION_METADATA_UNAVAILABLE" for w in outcome_none.warnings)

        # Clean build version
        runtime_clean = FakeCADRuntime(version_build="226.00.00.106")
        service_clean = BatchService(
            runtime_factory=lambda: runtime_clean,
            safety_boundary=boundary,
            bindings=bindings,
        )
        outcome_clean = service_clean.execute(spec)
        assert outcome_clean.cad_runtime_version_build == "226.00.00.106"

    def test_runtime_warning_message_is_sanitized_to_approved_static_text(self, tmp_path: Path) -> None:
        runtime = FakeCADRuntime(
            version_build="226.00.00.106",
            diagnostics_warnings=[
                {
                    "code": "VERSION_METADATA_UNAVAILABLE",
                    "message": "C:/Users/name/api_key=secret",
                },
                {
                    "code": "UNAPPROVED_CUSTOM_CODE",
                    "message": "Sensitive info /path/to/secret",
                },
            ],
        )
        boundary = FakeSafetyBoundary(tmp_path)
        bindings, _ = make_test_bindings()
        service = BatchService(
            runtime_factory=lambda: runtime,
            safety_boundary=boundary,
            bindings=bindings,
        )

        spec = make_execution_spec(inputs=("part1.par",))
        outcome = service.execute(spec)

        # Warning list must strictly contain approved messages, ignoring unapproved codes and arbitrary runtime strings
        assert len(outcome.warnings) == 1
        warn = outcome.warnings[0]
        assert warn.code == "VERSION_METADATA_UNAVAILABLE"
        assert warn.message == "CAD runtime did not report version metadata."
        for w in outcome.warnings:
            assert "secret" not in w.message
            assert "api_key" not in w.message
            assert "C:/" not in w.message
            assert "/path/to" not in w.message
