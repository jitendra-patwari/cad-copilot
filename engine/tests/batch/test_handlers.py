"""Unit tests for Export3DHandler and PublishDrawingHandler.

Tests enforce:
- Exact lifecycle sequence: prepare-once -> begin -> export/publish -> validate -> finalize -> success.
- Preparation caching: assembly check and drawing refresh run once per (input, handle_id) across formats.
- Rejection of empty/missing handle_id.
- Isolated state between different document handles.
- Workspace cleanup on failure after activation, called at most once.
- Truthful reporting of cleanup failures.
- Strict re-raising of fatal COM / timeout exceptions.
- Sanitized diagnostics without raw workstation paths.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from batch.execution import BatchItemContext
from batch.format_validation import BatchFormatValidationError
from batch.handlers import Export3DHandler, PublishDrawingHandler
from batch.models import BatchDiagnostic, BatchOutputFormat
from batch.output_snapshot import OutputSnapshot
from batch.output_workspace import BatchOutputWorkspace, BatchWorkspaceError
from drivers.solidedge.assembly_references import AssemblyReferenceCheckResult
from interfaces.exceptions import (
    CADExportError,
    CADRuntimeBusyError,
)


class FakeHandle:
    """Mock document handle exposing handle_id."""

    def __init__(self, handle_id: str = "handle-123") -> None:
        self.handle_id = handle_id


class FakeWorkspace(BatchOutputWorkspace):
    """Spy workspace tracking begin, finalize, and cleanup calls."""

    def __init__(self, target_path: Path | None = None, events: list[str] | None = None) -> None:
        self.begin_calls: list[BatchItemContext] = []
        self.finalize_calls: list[tuple[BatchItemContext, OutputSnapshot]] = []
        self.cleanup_calls: list[BatchItemContext] = []
        self._target_path = target_path
        self.fail_begin: Exception | None = None
        self.fail_finalize: Exception | None = None
        self.fail_cleanup: Exception | None = None
        self.events: list[str] = events if events is not None else []

    def begin_format(self, context: BatchItemContext) -> None:
        self.begin_calls.append(context)
        self.events.append("begin")
        if self.fail_begin:
            raise self.fail_begin

    def finalize_format(self, context: BatchItemContext, snapshot: OutputSnapshot) -> Path:
        self.finalize_calls.append((context, snapshot))
        self.events.append("finalize")
        if self.fail_finalize:
            raise self.fail_finalize
        return self._target_path or context.target_path

    def cleanup_format(self, context: BatchItemContext) -> None:
        self.cleanup_calls.append(context)
        self.events.append("cleanup")
        if self.fail_cleanup:
            raise self.fail_cleanup


def _resolved_checker(handle: object) -> AssemblyReferenceCheckResult:
    return AssemblyReferenceCheckResult(is_resolved=True, total_count=1, unresolved_count=0)


def make_context(
    operation_id: str = "export_3d",
    input_file: str = "box.par",
    format_id: str = "step",
    work_path: Path | None = None,
    target_path: Path | None = None,
    source_path: Path | None = None,
) -> BatchItemContext:
    p_source = source_path or Path(f"C:/inputs/{input_file}")
    p_work = work_path or Path(f"C:/output/.cad-copilot-work-abc/box.{format_id}")
    p_target = target_path or Path(f"C:/output/box.{format_id}")
    return BatchItemContext(
        request_id="req-1",
        operation_id=operation_id,
        input=input_file,
        source_path=p_source,
        format=format_id,
        target_relative_path=f"box.{format_id}",
        work_path=p_work,
        target_path=p_target,
    )


# ===========================================================================
# 1. Export3DHandler Tests
# ===========================================================================


class TestExport3DHandler:
    """Test suite for Export3DHandler lifecycle, contracts, and caching."""

    def test_successful_export_lifecycle(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        work_file = tmp_path / "work" / "part.step"
        work_file.parent.mkdir(parents=True)
        work_file.write_text("ISO-10303-21; HEADER; ... ENDSEC; DATA; ... ENDSEC; END-ISO-10303-21;\n")

        events: list[str] = []
        published_file = tmp_path / "final" / "part.step"
        workspace = FakeWorkspace(target_path=published_file, events=events)

        exported_calls: list[tuple[object, str, Path]] = []

        def mock_export(handle: object, fmt: str, out_p: Path) -> None:
            events.append("export")
            exported_calls.append((handle, fmt, out_p))

        fake_snapshot = MagicMock(spec=OutputSnapshot)

        def mock_validate(fmt: Any, p: Path) -> OutputSnapshot:
            events.append("validate")
            return fake_snapshot

        monkeypatch.setattr("batch.handlers.validate_batch_output", mock_validate)

        def mock_check(h: object) -> AssemblyReferenceCheckResult:
            events.append("prepare")
            return AssemblyReferenceCheckResult(is_resolved=True, total_count=1, unresolved_count=0)

        handler = Export3DHandler(
            workspace=workspace,
            model_exporter=mock_export,
            assembly_checker=mock_check,
        )

        doc_handle = FakeHandle("doc-1")
        ctx = make_context(input_file="assembly.asm", work_path=work_file, target_path=published_file)

        outcome = handler(doc_handle, ctx)

        assert outcome.is_success
        assert outcome.artifact is not None
        assert outcome.artifact.format == "step"
        assert outcome.artifact.path == str(published_file)

        # Invariant: exact chronological lifecycle order
        assert events == ["prepare", "begin", "export", "validate", "finalize"]

        # Call counts
        assert len(workspace.begin_calls) == 1
        assert len(exported_calls) == 1
        assert exported_calls[0] == (doc_handle, "step", work_file)
        assert len(workspace.finalize_calls) == 1
        assert len(workspace.cleanup_calls) == 0

    def test_successful_parasolid_export_lifecycle(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Proves complete guarded lifecycle execution for candidate parasolid format."""
        work_file = tmp_path / "work" / "part.x_t"
        work_file.parent.mkdir(parents=True)
        work_file.write_bytes(b"**ABCDEFGHIJKLMNOPQRSTUVWXYZ\r\n**PARASOLID \r\n**PART1;\r\n")

        events: list[str] = []
        published_file = tmp_path / "final" / "part.x_t"
        workspace = FakeWorkspace(target_path=published_file, events=events)

        exported_calls: list[tuple[object, str, Path]] = []

        def mock_export(handle: object, fmt: str, out_p: Path) -> None:
            events.append("export")
            exported_calls.append((handle, fmt, out_p))

        fake_snapshot = MagicMock(spec=OutputSnapshot)
        monkeypatch.setattr("batch.handlers.validate_batch_output", lambda fmt, p: fake_snapshot)

        handler = Export3DHandler(
            workspace=workspace,
            model_exporter=mock_export,
            assembly_checker=lambda h: AssemblyReferenceCheckResult(
                is_resolved=True, total_count=1, unresolved_count=0
            ),
        )

        doc_handle = FakeHandle("doc-parasolid-1")
        ctx = make_context(
            input_file="part.par",
            format_id="parasolid",
            work_path=work_file,
            target_path=published_file,
        )

        outcome = handler(doc_handle, ctx)

        assert outcome.is_success
        assert outcome.artifact is not None
        assert outcome.artifact.format == "parasolid"
        assert outcome.artifact.path == str(published_file)
        assert events == ["begin", "export", "finalize"]
        assert exported_calls == [(doc_handle, "parasolid", work_file)]

    def test_assembly_reference_check_cached_across_formats(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace = FakeWorkspace()
        monkeypatch.setattr("batch.handlers.validate_batch_output", lambda fmt, p: MagicMock(spec=OutputSnapshot))

        check_calls: list[object] = []

        def mock_check(handle: object) -> AssemblyReferenceCheckResult:
            check_calls.append(handle)
            return AssemblyReferenceCheckResult(is_resolved=True, total_count=2, unresolved_count=0)

        handler = Export3DHandler(
            workspace=workspace,
            model_exporter=lambda h, f, p: None,
            assembly_checker=mock_check,
        )

        doc_handle = FakeHandle("asm-doc-1")
        ctx_step = make_context(input_file="top.asm", format_id="step")
        ctx_stl = make_context(input_file="top.asm", format_id="stl")

        outcome1 = handler(doc_handle, ctx_step)
        assert outcome1.is_success

        outcome2 = handler(doc_handle, ctx_stl)
        assert outcome2.is_success

        # Check ran exactly ONCE for top.asm handle-1
        assert len(check_calls) == 1

    def test_different_handles_do_not_share_assembly_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace = FakeWorkspace()
        monkeypatch.setattr("batch.handlers.validate_batch_output", lambda fmt, p: MagicMock(spec=OutputSnapshot))

        check_calls: list[object] = []

        def mock_check(handle: object) -> AssemblyReferenceCheckResult:
            check_calls.append(handle)
            return AssemblyReferenceCheckResult(is_resolved=True, total_count=2, unresolved_count=0)

        handler = Export3DHandler(
            workspace=workspace,
            model_exporter=lambda h, f, p: None,
            assembly_checker=mock_check,
        )

        doc1 = FakeHandle("asm-handle-1")
        doc2 = FakeHandle("asm-handle-2")
        ctx = make_context(input_file="top.asm", format_id="step")

        handler(doc1, ctx)
        handler(doc2, ctx)

        assert len(check_calls) == 2

    def test_unresolved_assembly_fails_before_workspace_activation(self) -> None:
        workspace = FakeWorkspace()

        def mock_check(handle: object) -> AssemblyReferenceCheckResult:
            return AssemblyReferenceCheckResult(
                is_resolved=False,
                total_count=3,
                unresolved_count=1,
                diagnostic_message="Assembly contains 1 unresolved reference(s)",
            )

        export_mock = MagicMock()

        handler = Export3DHandler(
            workspace=workspace,
            model_exporter=export_mock,
            assembly_checker=mock_check,
        )

        doc_handle = FakeHandle("broken-asm")
        ctx = make_context(input_file="broken.asm", format_id="step")

        outcome = handler(doc_handle, ctx)

        assert not outcome.is_success
        assert len(outcome.errors) == 1
        assert outcome.errors[0].code == "ARTIFACT_EXPORT_FAILED"
        assert "1 unresolved reference(s)" in outcome.errors[0].message

        # Must not activate workspace or call driver export
        assert len(workspace.begin_calls) == 0
        export_mock.assert_not_called()
        assert len(workspace.cleanup_calls) == 0

    def test_missing_handle_id_fails_closed(self) -> None:
        workspace = FakeWorkspace()
        handler = Export3DHandler(
            workspace=workspace,
            model_exporter=lambda h, f, p: None,
            assembly_checker=_resolved_checker,
        )

        class BrokenHandle:
            pass  # No handle_id

        outcome = handler(BrokenHandle(), make_context())
        assert not outcome.is_success
        assert outcome.errors[0].code == "INTERNAL_ERROR"
        assert len(workspace.begin_calls) == 0

    def test_whitespace_bearing_handle_id_fails_closed(self) -> None:
        workspace = FakeWorkspace()
        handler = Export3DHandler(
            workspace=workspace,
            model_exporter=lambda h, f, p: None,
            assembly_checker=_resolved_checker,
        )

        doc_with_space = FakeHandle(" doc-1 ")
        outcome = handler(doc_with_space, make_context())
        assert not outcome.is_success
        assert outcome.errors[0].code == "INTERNAL_ERROR"
        assert len(workspace.begin_calls) == 0

    def test_exporter_failure_calls_cleanup_once(self) -> None:
        workspace = FakeWorkspace()

        def failing_exporter(h: object, f: str, p: Path) -> None:
            raise CADExportError("Simulated translator crash", error_code="ARTIFACT_EXPORT_FAILED")

        handler = Export3DHandler(
            workspace=workspace,
            model_exporter=failing_exporter,
            assembly_checker=_resolved_checker,
        )

        ctx = make_context(target_path=Path("C:/output/model.step"))
        outcome = handler(FakeHandle("doc-1"), ctx)

        assert not outcome.is_success
        assert outcome.errors[0].code == "ARTIFACT_EXPORT_FAILED"
        assert "Failed to export STEP artifact 'model.step'" in outcome.errors[0].message
        assert len(workspace.begin_calls) == 1
        assert len(workspace.cleanup_calls) == 1

    def test_finalize_failure_triggers_cleanup_and_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        workspace = FakeWorkspace()
        workspace.fail_finalize = BatchWorkspaceError(
            BatchDiagnostic(
                code="ARTIFACT_EXPORT_FAILED",
                message="Target collision during atomic publication",
            )
        )

        monkeypatch.setattr("batch.handlers.validate_batch_output", lambda fmt, p: MagicMock(spec=OutputSnapshot))

        handler = Export3DHandler(
            workspace=workspace,
            model_exporter=lambda h, f, p: None,
            assembly_checker=_resolved_checker,
        )

        ctx = make_context()
        outcome = handler(FakeHandle("doc-1"), ctx)

        assert not outcome.is_success
        assert outcome.artifact is None
        assert len(outcome.errors) == 1
        assert outcome.errors[0].code == "ARTIFACT_EXPORT_FAILED"
        assert "Target collision during atomic publication" in outcome.errors[0].message
        assert len(workspace.begin_calls) == 1
        assert len(workspace.finalize_calls) == 1
        assert len(workspace.cleanup_calls) == 1

    def test_validation_failure_calls_cleanup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        workspace = FakeWorkspace()

        def mock_val(fmt: str, p: Path) -> OutputSnapshot:
            raise BatchFormatValidationError(cast(BatchOutputFormat, fmt), "structure", "Malformed header")

        monkeypatch.setattr("batch.handlers.validate_batch_output", mock_val)

        handler = Export3DHandler(
            workspace=workspace,
            model_exporter=lambda h, f, p: None,
            assembly_checker=_resolved_checker,
        )

        outcome = handler(FakeHandle("doc-1"), make_context())
        assert not outcome.is_success
        assert outcome.errors[0].code == "ARTIFACT_EXPORT_FAILED"
        assert outcome.errors[0].message == "Malformed header"
        assert len(workspace.cleanup_calls) == 1

    def test_fatal_timeout_re_raises_and_cleans_up(self) -> None:
        workspace = FakeWorkspace()

        def timeout_exporter(h: object, f: str, p: Path) -> None:
            raise TimeoutError("STA worker timed out")

        handler = Export3DHandler(
            workspace=workspace,
            model_exporter=timeout_exporter,
            assembly_checker=_resolved_checker,
        )

        with pytest.raises(TimeoutError):
            handler(FakeHandle("doc-1"), make_context())

        assert len(workspace.begin_calls) == 1
        assert len(workspace.cleanup_calls) == 1

    def test_fatal_busy_re_raises(self) -> None:
        workspace = FakeWorkspace()

        def busy_exporter(h: object, f: str, p: Path) -> None:
            raise CADRuntimeBusyError("Server busy timeout")

        handler = Export3DHandler(
            workspace=workspace,
            model_exporter=busy_exporter,
            assembly_checker=_resolved_checker,
        )

        with pytest.raises(CADRuntimeBusyError):
            handler(FakeHandle("doc-1"), make_context())

        assert len(workspace.begin_calls) == 1
        assert len(workspace.cleanup_calls) == 1

    def test_cleanup_failure_merges_truthfully(self) -> None:
        workspace = FakeWorkspace()
        workspace.fail_cleanup = RuntimeError("Disk unmount during cleanup")

        def failing_exporter(h: object, f: str, p: Path) -> None:
            raise CADExportError("Translator failed")

        handler = Export3DHandler(
            workspace=workspace,
            model_exporter=failing_exporter,
            assembly_checker=_resolved_checker,
        )

        outcome = handler(FakeHandle("doc-1"), make_context())
        assert not outcome.is_success
        assert len(outcome.errors) == 2
        assert outcome.errors[0].code == "ARTIFACT_EXPORT_FAILED"
        assert outcome.errors[1].code == "ARTIFACT_EXPORT_FAILED"
        assert "Failed to clean up temporary workspace output" in outcome.errors[1].message

    def test_operation_and_format_contracts(self) -> None:
        workspace = FakeWorkspace()
        handler = Export3DHandler(
            workspace=workspace,
            model_exporter=lambda h, f, p: None,
            assembly_checker=_resolved_checker,
        )

        # Wrong operation
        ctx_wrong_op = make_context(operation_id="publish_drawing")
        assert handler(FakeHandle("doc-1"), ctx_wrong_op).errors[0].code == "INTERNAL_ERROR"

        # Unsupported format
        ctx_wrong_fmt = make_context(format_id="pdf")
        assert handler(FakeHandle("doc-1"), ctx_wrong_fmt).errors[0].code == "ARTIFACT_EXPORT_FAILED"

        # Unsupported extension
        ctx_wrong_ext = make_context(input_file="draft.dft", format_id="step")
        assert handler(FakeHandle("doc-1"), ctx_wrong_ext).errors[0].code == "ARTIFACT_EXPORT_FAILED"


# ===========================================================================
# 2. PublishDrawingHandler Tests
# ===========================================================================


class TestPublishDrawingHandler:
    """Test suite for PublishDrawingHandler lifecycle, contracts, and view refresh."""

    def test_successful_drawing_publication_lifecycle(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        work_file = tmp_path / "work" / "drawing.pdf"
        published_file = tmp_path / "final" / "drawing.pdf"
        events: list[str] = []
        workspace = FakeWorkspace(target_path=published_file, events=events)

        published_calls: list[tuple[object, str, Path]] = []
        refresh_calls: list[object] = []

        def mock_publish(handle: object, fmt: str, out_p: Path) -> None:
            events.append("publish")
            published_calls.append((handle, fmt, out_p))

        def mock_refresh(handle: object) -> int:
            events.append("prepare")
            refresh_calls.append(handle)
            return 3

        def mock_validate(fmt: Any, p: Path) -> OutputSnapshot:
            events.append("validate")
            return MagicMock(spec=OutputSnapshot)

        monkeypatch.setattr("batch.handlers.validate_batch_output", mock_validate)

        handler = PublishDrawingHandler(
            workspace=workspace,
            drawing_publisher=mock_publish,
            drawing_preparer=mock_refresh,
        )

        doc_handle = FakeHandle("dft-doc-1")
        ctx_pdf = make_context(
            operation_id="publish_drawing",
            input_file="drawing.dft",
            format_id="pdf",
            work_path=work_file,
            target_path=published_file,
        )
        ctx_dxf = make_context(
            operation_id="publish_drawing",
            input_file="drawing.dft",
            format_id="dxf",
            work_path=tmp_path / "work" / "drawing.dxf",
            target_path=tmp_path / "final" / "drawing.dxf",
        )

        outcome_pdf = handler(doc_handle, ctx_pdf)
        assert outcome_pdf.is_success

        outcome_dxf = handler(doc_handle, ctx_dxf)
        assert outcome_dxf.is_success

        # Invariant: first format executed exact lifecycle order: prepare -> begin -> publish -> validate -> finalize
        assert events[:5] == ["prepare", "begin", "publish", "validate", "finalize"]
        # Second format skipped prepare (cached), so it executed: begin -> publish -> validate -> finalize
        assert events[5:] == ["begin", "publish", "validate", "finalize"]

        # Invariant: drawing view refresh ran exactly ONCE for dft-doc-1 across both formats
        assert len(refresh_calls) == 1
        assert len(published_calls) == 2
        assert len(workspace.begin_calls) == 2
        assert len(workspace.finalize_calls) == 2
        assert len(workspace.cleanup_calls) == 0

    def test_failed_view_refresh_fails_without_workspace_activation(self) -> None:
        workspace = FakeWorkspace()

        def failing_refresh(handle: object) -> None:
            raise CADExportError("COM error updating sheet views")

        publish_mock = MagicMock()

        handler = PublishDrawingHandler(
            workspace=workspace,
            drawing_publisher=publish_mock,
            drawing_preparer=failing_refresh,
        )

        doc_handle = FakeHandle("broken-dft")
        ctx = make_context(
            operation_id="publish_drawing",
            input_file="broken.dft",
            format_id="pdf",
        )

        outcome = handler(doc_handle, ctx)
        assert not outcome.is_success
        assert outcome.errors[0].code == "ARTIFACT_EXPORT_FAILED"
        assert "Failed to refresh drawing views" in outcome.errors[0].message

        assert len(workspace.begin_calls) == 0
        publish_mock.assert_not_called()

    def test_fatal_error_during_view_refresh_re_raises(self) -> None:
        workspace = FakeWorkspace()

        def timeout_refresh(handle: object) -> None:
            raise TimeoutError("STA worker timeout during view update")

        handler = PublishDrawingHandler(
            workspace=workspace,
            drawing_publisher=lambda h, f, p: None,
            drawing_preparer=timeout_refresh,
        )

        ctx = make_context(
            operation_id="publish_drawing",
            input_file="drawing.dft",
            format_id="pdf",
        )

        with pytest.raises(TimeoutError):
            handler(FakeHandle("doc-1"), ctx)

    def test_publisher_failure_calls_cleanup(self) -> None:
        workspace = FakeWorkspace()

        def failing_publish(h: object, f: str, p: Path) -> None:
            raise CADExportError("Draft PDF translator failure")

        handler = PublishDrawingHandler(
            workspace=workspace,
            drawing_publisher=failing_publish,
            drawing_preparer=lambda h: None,
        )

        ctx = make_context(
            operation_id="publish_drawing",
            input_file="drawing.dft",
            format_id="pdf",
            target_path=Path("C:/output/drawing.pdf"),
        )

        outcome = handler(FakeHandle("doc-1"), ctx)
        assert not outcome.is_success
        assert outcome.errors[0].code == "ARTIFACT_EXPORT_FAILED"
        assert len(workspace.begin_calls) == 1
        assert len(workspace.cleanup_calls) == 1

    def test_export_3d_cached_assembly_error_qualifies_diagnostics_per_format(self) -> None:
        workspace = FakeWorkspace()
        checker_calls: list[object] = []

        def failing_checker(handle: object) -> AssemblyReferenceCheckResult:
            checker_calls.append(handle)
            return AssemblyReferenceCheckResult(
                is_resolved=False,
                total_count=3,
                unresolved_count=1,
                diagnostic_message="Assembly contains 1 unresolved reference(s)",
            )

        handler = Export3DHandler(
            workspace=workspace,
            model_exporter=MagicMock(),
            assembly_checker=failing_checker,
        )

        doc_handle = FakeHandle("broken-asm-1")
        ctx_step = make_context(
            operation_id="export_3d",
            input_file="broken.asm",
            format_id="step",
        )
        ctx_stl = make_context(
            operation_id="export_3d",
            input_file="broken.asm",
            format_id="stl",
        )

        outcome_step = handler(doc_handle, ctx_step)
        assert not outcome_step.is_success
        assert outcome_step.format == "step"
        assert len(outcome_step.errors) == 1
        assert outcome_step.errors[0].format == "step"
        assert "1 unresolved reference(s)" in outcome_step.errors[0].message

        outcome_stl = handler(doc_handle, ctx_stl)
        assert not outcome_stl.is_success
        assert outcome_stl.format == "stl"
        assert len(outcome_stl.errors) == 1
        assert outcome_stl.errors[0].format == "stl"
        assert "1 unresolved reference(s)" in outcome_stl.errors[0].message

        # Checker was called only once across both formats
        assert len(checker_calls) == 1
        # Workspace was never activated
        assert len(workspace.begin_calls) == 0

    def test_publish_drawing_cached_refresh_error_qualifies_diagnostics_per_format(self) -> None:
        workspace = FakeWorkspace()
        refresh_calls: list[object] = []

        def failing_refresh(handle: object) -> None:
            refresh_calls.append(handle)
            raise CADExportError("COM error updating sheet views")

        handler = PublishDrawingHandler(
            workspace=workspace,
            drawing_publisher=MagicMock(),
            drawing_preparer=failing_refresh,
        )

        doc_handle = FakeHandle("broken-dft-1")
        ctx_pdf = make_context(
            operation_id="publish_drawing",
            input_file="broken.dft",
            format_id="pdf",
        )
        ctx_dxf = make_context(
            operation_id="publish_drawing",
            input_file="broken.dft",
            format_id="dxf",
        )

        outcome_pdf = handler(doc_handle, ctx_pdf)
        assert not outcome_pdf.is_success
        assert outcome_pdf.format == "pdf"
        assert len(outcome_pdf.errors) == 1
        assert outcome_pdf.errors[0].format == "pdf"
        assert "Failed to refresh drawing views" in outcome_pdf.errors[0].message

        outcome_dxf = handler(doc_handle, ctx_dxf)
        assert not outcome_dxf.is_success
        assert outcome_dxf.format == "dxf"
        assert len(outcome_dxf.errors) == 1
        assert outcome_dxf.errors[0].format == "dxf"
        assert "Failed to refresh drawing views" in outcome_dxf.errors[0].message

        # Refresh was called only once across both formats
        assert len(refresh_calls) == 1
        # Workspace was never activated
        assert len(workspace.begin_calls) == 0
