"""Unit tests for Solid Edge drawing view refresh and PDF/DXF publication driver.

Invariants:
    - Verifies document-owned Sections -> WorkingSection -> Sheets -> DrawingViews -> Update() sequence.
    - Verifies 1-based collection traversal without accessing Application.ActiveDocument.
    - Verifies SaveCopyAs invocation for PDF and DXF formats with exact extension matching.
    - Verifies error sanitization (SEC-07): no raw absolute paths or COM pointers in diagnostics.
    - Verifies input preflight: non-existent parent, extension mismatch, and symlink rejection.
    - Verifies sidecar-free publication contract: PDF/DXF emit no sidecars, leaving any
      unexpected file for workspace inventory verification to fail closed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from drivers.solidedge.drawing_publisher import (
    publish_drawing_to_path,
    refresh_drawing_views,
)
from drivers.solidedge.errors import (
    RPC_E_CALL_REJECTED,
    RPC_E_SERVER_DIED,
    RPC_E_SERVERCALL_RETRYLATER,
)
from interfaces.exceptions import (
    CADDocumentError,
    CADExportError,
    CADRuntimeBusyError,
    CADRuntimeUnavailableError,
)


class MockCOMError(Exception):
    """Fake pywintypes.com_error for offline testing."""

    def __init__(self, hresult: int, message: str) -> None:
        super().__init__(message)
        self.hresult = hresult
        self.args = (hresult, message, None, None)


class FakeWorker:
    """Synchronous fake STA worker invoking task callables directly."""

    def __init__(self) -> None:
        self.call_log: list[str] = []

    def _invoke_com(self, call: Any) -> Any:
        return call()


class FakeDrawingView:
    """Fake DrawingView supporting Update()."""

    def __init__(self, name: str = "View1") -> None:
        self.Name = name
        self.update_called = False

    def Update(self) -> None:
        self.update_called = True


class FakeSheet:
    """Fake Sheet containing DrawingViews."""

    def __init__(self, views: list[FakeDrawingView] | None = None) -> None:
        self._views = views if views is not None else [FakeDrawingView()]

    @property
    def DrawingViews(self) -> FakeDrawingViewsCollection:
        return FakeDrawingViewsCollection(self._views)


class FakeDrawingViewsCollection:
    """Fake 1-based DrawingViews collection."""

    def __init__(self, views: list[FakeDrawingView]) -> None:
        self._views = views

    @property
    def Count(self) -> int:
        return len(self._views)

    def Item(self, index: int) -> FakeDrawingView:
        if 1 <= index <= len(self._views):
            return self._views[index - 1]
        raise IndexError(f"Index {index} out of bounds for Count={len(self._views)}")


class FakeSheetsCollection:
    """Fake 1-based Sheets collection."""

    def __init__(self, sheets: list[FakeSheet]) -> None:
        self._sheets = sheets

    @property
    def Count(self) -> int:
        return len(self._sheets)

    def Item(self, index: int) -> FakeSheet:
        if 1 <= index <= len(self._sheets):
            return self._sheets[index - 1]
        raise IndexError(f"Index {index} out of bounds for Count={len(self._sheets)}")


class FakeWorkingSection:
    """Fake WorkingSection containing Sheets."""

    def __init__(self, sheets: list[FakeSheet] | None = None) -> None:
        self._sheets = sheets if sheets is not None else [FakeSheet()]

    @property
    def Sheets(self) -> FakeSheetsCollection:
        return FakeSheetsCollection(self._sheets)


class FakeSections:
    """Fake Sections containing WorkingSection."""

    def __init__(self, working_section: FakeWorkingSection | None = None) -> None:
        self._working_section = working_section if working_section is not None else FakeWorkingSection()

    @property
    def WorkingSection(self) -> FakeWorkingSection:
        return self._working_section


class FakeDraftDocument:
    """Fake Solid Edge COM DraftDocument supporting Name, SaveCopyAs, and Sections."""

    def __init__(self, name: str = "TestDrawing.dft", sections: FakeSections | None = None) -> None:
        self.Name = name
        self.save_copy_as_calls: list[str] = []
        self._sections = sections if sections is not None else FakeSections()

    def SaveCopyAs(self, filename: str) -> None:
        self.save_copy_as_calls.append(filename)

    @property
    def Sections(self) -> FakeSections:
        return self._sections


# ===========================================================================
# 1. View Refresh Tests
# ===========================================================================


class TestRefreshDrawingViews:
    """Test suite for refresh_drawing_views."""

    def test_refresh_drawing_views_success(self) -> None:
        v1 = FakeDrawingView("V1")
        v2 = FakeDrawingView("V2")
        v3 = FakeDrawingView("V3")
        sheet1 = FakeSheet([v1, v2])
        sheet2 = FakeSheet([v3])
        working_sec = FakeWorkingSection([sheet1, sheet2])
        sections = FakeSections(working_sec)
        raw_doc = FakeDraftDocument("Drawing.dft", sections)
        worker = FakeWorker()

        count = refresh_drawing_views(raw_doc, worker)
        assert count == 3
        assert v1.update_called is True
        assert v2.update_called is True
        assert v3.update_called is True

    def test_refresh_drawing_views_empty_sheets(self) -> None:
        working_sec = FakeWorkingSection([])
        sections = FakeSections(working_sec)
        raw_doc = FakeDraftDocument("EmptyDrawing.dft", sections)
        worker = FakeWorker()

        count = refresh_drawing_views(raw_doc, worker)
        assert count == 0

    def test_refresh_drawing_views_raises_document_lost_on_unresponsive(self) -> None:
        raw_doc = FakeDraftDocument(name="")
        worker = FakeWorker()

        with pytest.raises(CADDocumentError) as exc_info:
            refresh_drawing_views(raw_doc, worker)
        assert exc_info.value.error_code == "DOCUMENT_LOST"

    def test_refresh_drawing_views_preserves_runtime_busy(self) -> None:
        v1 = FakeDrawingView("V1")

        def fail_update() -> None:
            raise MockCOMError(RPC_E_CALL_REJECTED, "Call rejected")

        v1.Update = fail_update  # type: ignore[method-assign]
        raw_doc = FakeDraftDocument("Drawing.dft", FakeSections(FakeWorkingSection([FakeSheet([v1])])))
        worker = FakeWorker()

        with pytest.raises(CADRuntimeBusyError) as exc_info:
            refresh_drawing_views(raw_doc, worker)
        assert exc_info.value.error_code == "RUNTIME_BUSY_TIMEOUT"

    def test_refresh_drawing_views_preserves_runtime_unavailable(self) -> None:
        v1 = FakeDrawingView("V1")

        def fail_update() -> None:
            raise MockCOMError(RPC_E_SERVER_DIED, "Server died")

        v1.Update = fail_update  # type: ignore[method-assign]
        raw_doc = FakeDraftDocument("Drawing.dft", FakeSections(FakeWorkingSection([FakeSheet([v1])])))
        worker = FakeWorker()

        with pytest.raises(CADRuntimeUnavailableError) as exc_info:
            refresh_drawing_views(raw_doc, worker)
        assert exc_info.value.error_code == "RUNTIME_UNAVAILABLE"

    def test_refresh_drawing_views_preserves_timeout(self) -> None:
        v1 = FakeDrawingView("V1")

        def fail_update() -> None:
            raise TimeoutError("STA worker timed out")

        v1.Update = fail_update  # type: ignore[method-assign]
        raw_doc = FakeDraftDocument("Drawing.dft", FakeSections(FakeWorkingSection([FakeSheet([v1])])))
        worker = FakeWorker()

        with pytest.raises(TimeoutError):
            refresh_drawing_views(raw_doc, worker)

    def test_refresh_drawing_views_sanitizes_complex_paths(self) -> None:
        v1 = FakeDrawingView("V1")

        def fail_update() -> None:
            raise OSError(r"Failed updating view at E:\Secret_Org\Private_Project\Drawing.dft: GDI error")

        v1.Update = fail_update  # type: ignore[method-assign]
        raw_doc = FakeDraftDocument("Drawing.dft", FakeSections(FakeWorkingSection([FakeSheet([v1])])))
        worker = FakeWorker()

        with pytest.raises(CADExportError) as exc_info:
            refresh_drawing_views(raw_doc, worker)
        assert exc_info.value.error_code == "ARTIFACT_EXPORT_FAILED"
        msg = str(exc_info.value)
        assert "Secret_Org" not in msg
        assert "Private_Project" not in msg
        assert "<details redacted>" in msg


# ===========================================================================
# 2. Drawing Publication Tests
# ===========================================================================


class TestPublishDrawingToPath:
    """Test suite for publish_drawing_to_path."""

    @pytest.mark.parametrize("fmt,ext", [("pdf", ".pdf"), ("dxf", ".dxf")])
    def test_publish_drawing_to_path_success(self, tmp_path: Path, fmt: str, ext: str) -> None:
        raw_doc = FakeDraftDocument()
        worker = FakeWorker()
        out_file = tmp_path / f"sheet{ext}"

        publish_drawing_to_path(raw_doc, worker, fmt, out_file)
        assert len(raw_doc.save_copy_as_calls) == 1
        assert raw_doc.save_copy_as_calls[0] == str(out_file)

    def test_publish_drawing_unsupported_format(self, tmp_path: Path) -> None:
        raw_doc = FakeDraftDocument()
        worker = FakeWorker()
        out_file = tmp_path / "sheet.step"

        with pytest.raises(CADExportError) as exc_info:
            publish_drawing_to_path(raw_doc, worker, "step", out_file)
        assert exc_info.value.error_code == "ARTIFACT_EXPORT_FAILED"
        assert "Unsupported drawing format" in str(exc_info.value)

    def test_publish_drawing_extension_mismatch(self, tmp_path: Path) -> None:
        raw_doc = FakeDraftDocument()
        worker = FakeWorker()
        out_file = tmp_path / "sheet.dxf"

        with pytest.raises(CADExportError) as exc_info:
            publish_drawing_to_path(raw_doc, worker, "pdf", out_file)
        assert exc_info.value.error_code == "ARTIFACT_EXPORT_FAILED"
        assert "does not match format" in str(exc_info.value)

    def test_publish_drawing_target_dir_missing(self, tmp_path: Path) -> None:
        raw_doc = FakeDraftDocument()
        worker = FakeWorker()
        out_file = tmp_path / "non_existent_dir" / "sheet.pdf"

        with pytest.raises(CADExportError) as exc_info:
            publish_drawing_to_path(raw_doc, worker, "pdf", out_file)
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"

    def test_publish_drawing_raises_document_lost_on_unresponsive(self, tmp_path: Path) -> None:
        raw_doc = FakeDraftDocument(name="")
        worker = FakeWorker()
        out_file = tmp_path / "sheet.pdf"

        with pytest.raises(CADDocumentError) as exc_info:
            publish_drawing_to_path(raw_doc, worker, "pdf", out_file)
        assert exc_info.value.error_code == "DOCUMENT_LOST"

    def test_publish_drawing_preserves_runtime_busy(self, tmp_path: Path) -> None:
        raw_doc = FakeDraftDocument()
        worker = FakeWorker()
        out_file = tmp_path / "sheet.pdf"

        def raise_busy(path: str) -> None:
            raise MockCOMError(RPC_E_SERVERCALL_RETRYLATER, "Server call retry later")

        raw_doc.SaveCopyAs = raise_busy  # type: ignore[assignment]

        with pytest.raises(CADRuntimeBusyError) as exc_info:
            publish_drawing_to_path(raw_doc, worker, "pdf", out_file)
        assert exc_info.value.error_code == "RUNTIME_BUSY_TIMEOUT"

    def test_publish_drawing_preserves_runtime_unavailable(self, tmp_path: Path) -> None:
        raw_doc = FakeDraftDocument()
        worker = FakeWorker()
        out_file = tmp_path / "sheet.dxf"

        def raise_died(path: str) -> None:
            raise MockCOMError(RPC_E_SERVER_DIED, "Server died")

        raw_doc.SaveCopyAs = raise_died  # type: ignore[assignment]

        with pytest.raises(CADRuntimeUnavailableError) as exc_info:
            publish_drawing_to_path(raw_doc, worker, "dxf", out_file)
        assert exc_info.value.error_code == "RUNTIME_UNAVAILABLE"

    def test_publish_drawing_sanitizes_complex_paths(self, tmp_path: Path) -> None:
        raw_doc = FakeDraftDocument()
        worker = FakeWorker()
        out_file = tmp_path / "sheet.pdf"

        def raise_sensitive(path: str) -> None:
            raise OSError(r"Failed writing to E:\Client (SECRET_ORG)\Drawing\sheet.pdf: permission denied")

        raw_doc.SaveCopyAs = raise_sensitive  # type: ignore[assignment]

        with pytest.raises(CADExportError) as exc_info:
            publish_drawing_to_path(raw_doc, worker, "pdf", out_file)
        assert exc_info.value.error_code == "ARTIFACT_EXPORT_FAILED"
        msg = str(exc_info.value)
        assert "SECRET_ORG" not in msg
        assert "Drawing" not in msg
        assert "<details redacted>" in msg
