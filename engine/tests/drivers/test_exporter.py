"""Unit tests for driver-private Solid Edge exporter, preview capture, and health probing.

Invariants:
    - Verifies SaveCopyAs invocation for required model formats (PAR, STEP, STL).
    - Verifies document-owned Window -> View -> SaveAsImage sequence for preview.
    - Verifies localized preview failure classification vs fatal document loss.
    - Verifies error sanitization (SEC-07): no raw absolute paths or COM pointers in error messages.
    - Verifies input path preflight: non-existent parent, extension mismatch, and symlink rejection.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from drivers.solidedge.errors import (
    RPC_E_CALL_REJECTED,
    RPC_E_SERVER_DIED,
    RPC_E_SERVERCALL_RETRYLATER,
)
from drivers.solidedge.exporter import (
    DEFAULT_PREVIEW_HEIGHT,
    DEFAULT_PREVIEW_WIDTH,
    _cleanup_known_translator_sidecar,
    capture_preview_image,
    export_model_to_path,
    probe_document_health,
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


class FakePartDocument:
    """Fake Solid Edge COM PartDocument supporting Name, SaveCopyAs, and Windows."""

    def __init__(self, name: str = "TestPart.par") -> None:
        self.Name = name
        self.save_copy_as_calls: list[str] = []
        self.windows: FakeWindowsCollection | None = FakeWindowsCollection()

    def SaveCopyAs(self, filename: str) -> None:
        self.save_copy_as_calls.append(filename)

    @property
    def Windows(self) -> FakeWindowsCollection | None:
        return self.windows


class FakeWindowsCollection:
    """Fake Windows collection returning fake window items."""

    def __init__(self, items: list[FakeWindow] | None = None) -> None:
        self._items = items if items is not None else [FakeWindow()]

    @property
    def Count(self) -> int:
        return len(self._items)

    def Item(self, index: int) -> FakeWindow:
        if 1 <= index <= len(self._items):
            return self._items[index - 1]
        raise IndexError("Collection index out of range")


_DEFAULT_VIEW: Any = object()


class FakeWindow:
    """Fake Document Window containing a View."""

    def __init__(self, view: Any = _DEFAULT_VIEW) -> None:
        self.View = FakeView() if view is _DEFAULT_VIEW else view


class FakeView:
    """Fake 3D View supporting Fit and SaveAsImage."""

    def __init__(self) -> None:
        self.fit_called: bool = False
        self.save_as_image_calls: list[tuple[str, int, int]] = []

    def Fit(self) -> None:
        self.fit_called = True

    def SaveAsImage(self, filename: str, width: int, height: int) -> None:
        self.save_as_image_calls.append((filename, width, height))


# ===========================================================================
# 1. Model Export Tests
# ===========================================================================


class TestExportModelToPath:
    """Test suite for export_model_to_path."""

    @pytest.mark.parametrize(
        "format_id, filename",
        [
            ("par", "model.par"),
            ("step", "model.step"),
            ("step", "model.stp"),
            ("stl", "model.stl"),
        ],
    )
    def test_export_model_to_path_success(self, tmp_path: Path, format_id: str, filename: str) -> None:
        raw_doc = FakePartDocument()
        worker = FakeWorker()
        out_file = tmp_path / filename

        export_model_to_path(raw_doc, worker, format_id, out_file)

        assert len(raw_doc.save_copy_as_calls) == 1
        assert raw_doc.save_copy_as_calls[0] == os.fspath(out_file)

    def test_export_model_to_path_par_does_not_cleanup_log(self, tmp_path: Path) -> None:
        raw_doc = FakePartDocument()
        worker = FakeWorker()
        out_file = tmp_path / "model.par"
        same_stem_log = tmp_path / "model.log"
        same_stem_log.write_text("Unrelated log content", encoding="utf-8")

        export_model_to_path(raw_doc, worker, "par", out_file)

        # PAR export must NOT touch an unexpected same-stem .log
        assert same_stem_log.exists()
        assert same_stem_log.read_text(encoding="utf-8") == "Unrelated log content"

    def test_export_model_to_path_step_cleans_up_log(self, tmp_path: Path) -> None:
        raw_doc = FakePartDocument()
        worker = FakeWorker()
        out_file = tmp_path / "model.step"
        same_stem_log = tmp_path / "model.log"
        same_stem_log.write_text("STEP translator log", encoding="utf-8")

        export_model_to_path(raw_doc, worker, "step", out_file)

        # STEP export must clean up translator log
        assert not same_stem_log.exists()

    def test_export_model_to_path_resolves_relative_path_to_absolute(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        raw_doc = FakePartDocument()
        worker = FakeWorker()
        monkeypatch.chdir(tmp_path)
        rel_file = Path("rel_model.par")

        export_model_to_path(raw_doc, worker, "par", rel_file)

        assert len(raw_doc.save_copy_as_calls) == 1
        expected_abs = os.fspath(tmp_path / "rel_model.par")
        assert raw_doc.save_copy_as_calls[0] == expected_abs

    def test_export_model_to_path_unsupported_format_raises_cad_export_error(self, tmp_path: Path) -> None:
        raw_doc = FakePartDocument()
        worker = FakeWorker()
        out_file = tmp_path / "model.dwg"

        with pytest.raises(CADExportError) as exc_info:
            export_model_to_path(raw_doc, worker, "dwg", out_file)
        assert exc_info.value.error_code == "ARTIFACT_EXPORT_FAILED"
        assert "Unsupported export format" in str(exc_info.value)
        assert len(raw_doc.save_copy_as_calls) == 0

    def test_export_model_to_path_rejects_non_path(self, tmp_path: Path) -> None:
        raw_doc = FakePartDocument()
        worker = FakeWorker()

        with pytest.raises(CADExportError) as exc_info:
            export_model_to_path(raw_doc, worker, "par", str(tmp_path / "model.par"))  # type: ignore[arg-type]
        assert exc_info.value.error_code == "ARTIFACT_EXPORT_FAILED"
        assert "must be a Path instance" in str(exc_info.value)

    def test_export_model_to_path_rejects_missing_parent_directory(self, tmp_path: Path) -> None:
        raw_doc = FakePartDocument()
        worker = FakeWorker()
        non_existent_dir = tmp_path / "missing_dir"
        out_file = non_existent_dir / "model.par"

        with pytest.raises(CADExportError) as exc_info:
            export_model_to_path(raw_doc, worker, "par", out_file)
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
        assert "Target directory for 'model.par' does not exist" in str(exc_info.value)

    def test_export_model_to_path_rejects_parent_not_a_directory(self, tmp_path: Path) -> None:
        raw_doc = FakePartDocument()
        worker = FakeWorker()
        file_parent = tmp_path / "not_a_dir.txt"
        file_parent.write_text("dummy")
        out_file = file_parent / "model.par"

        with pytest.raises(CADExportError) as exc_info:
            export_model_to_path(raw_doc, worker, "par", out_file)
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"

    def test_export_model_to_path_rejects_extension_mismatch(self, tmp_path: Path) -> None:
        raw_doc = FakePartDocument()
        worker = FakeWorker()
        out_file = tmp_path / "model.step"

        with pytest.raises(CADExportError) as exc_info:
            export_model_to_path(raw_doc, worker, "par", out_file)
        assert exc_info.value.error_code == "ARTIFACT_EXPORT_FAILED"
        assert "does not match format" in str(exc_info.value)

    def test_export_model_to_path_rejects_symlink_target(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        raw_doc = FakePartDocument()
        worker = FakeWorker()
        out_file = tmp_path / "symlink_model.par"

        monkeypatch.setattr("drivers.solidedge.exporter._is_symlink_or_reparse", lambda p: True)
        with pytest.raises(CADExportError) as exc_info:
            export_model_to_path(raw_doc, worker, "par", out_file)
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
        assert "must not be a symbolic link or reparse point" in str(exc_info.value)

    def test_export_model_to_path_rejects_unresponsive_document(self, tmp_path: Path) -> None:
        raw_doc = FakePartDocument(name="")
        worker = FakeWorker()
        out_file = tmp_path / "model.par"

        with pytest.raises(CADDocumentError) as exc_info:
            export_model_to_path(raw_doc, worker, "par", out_file)
        assert exc_info.value.error_code == "DOCUMENT_LOST"
        assert "unresponsive or unseated" in str(exc_info.value)

    def test_export_model_to_path_sanitizes_com_exceptions(self, tmp_path: Path) -> None:
        raw_doc = FakePartDocument()
        worker = FakeWorker()
        out_file = tmp_path / "model.par"

        def raise_com_fault(path: str) -> None:
            raise OSError(f"Disk write error at {path} with pointer 0x00007FF8B3A1C000")

        raw_doc.SaveCopyAs = raise_com_fault  # type: ignore[assignment]

        with pytest.raises(CADExportError) as exc_info:
            export_model_to_path(raw_doc, worker, "par", out_file)

        assert exc_info.value.error_code == "ARTIFACT_EXPORT_FAILED"
        msg = str(exc_info.value)
        # Verify message contains basename, not full absolute path
        assert "model.par" in msg
        assert str(tmp_path) not in msg
        assert "0x00007FF8B3A1C000" not in msg

    def test_export_model_to_path_preserves_fatal_timeout(self, tmp_path: Path) -> None:
        raw_doc = FakePartDocument()
        worker = FakeWorker()
        out_file = tmp_path / "model.par"

        def raise_timeout(path: str) -> None:
            raise TimeoutError("STA worker timed out waiting for COM completion")

        raw_doc.SaveCopyAs = raise_timeout  # type: ignore[assignment]

        with pytest.raises(TimeoutError):
            export_model_to_path(raw_doc, worker, "par", out_file)

    def test_export_model_to_path_sanitizes_complex_paths_with_parentheses_and_spaces(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        raw_doc = FakePartDocument()
        worker = FakeWorker()
        out_file = tmp_path / "model.par"

        def raise_sensitive_fault(path: str) -> None:
            raise OSError(
                r"Failed to write to E:\Client (SECRET_ORGANIZATION)\Private Project\model.par: access denied"
            )

        raw_doc.SaveCopyAs = raise_sensitive_fault  # type: ignore[assignment]

        with pytest.raises(CADExportError) as exc_info:
            export_model_to_path(raw_doc, worker, "par", out_file)

        assert exc_info.value.error_code == "ARTIFACT_EXPORT_FAILED"
        msg = str(exc_info.value)
        assert "SECRET_ORGANIZATION" not in msg
        assert "Private Project" not in msg
        assert "Client" not in msg
        assert "<details redacted>" in msg

        captured = capsys.readouterr()
        assert "SECRET_ORGANIZATION" not in captured.err
        assert "Private Project" not in captured.err
        assert "Client" not in captured.err

    def test_export_model_to_path_sanitizes_paths_with_commas_semicolons_exclamations_and_unc(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        raw_doc = FakePartDocument()
        worker = FakeWorker()
        out_file = tmp_path / "model.par"

        def raise_comma_unc_fault(path: str) -> None:
            raise OSError(
                r"Failed writing to E:\Client, LLC\SECRET_PROJECT\model.par and \\nas01\share; LLC\SECRET_PROJECT!\model.par: access denied"
            )

        raw_doc.SaveCopyAs = raise_comma_unc_fault  # type: ignore[assignment]

        with pytest.raises(CADExportError) as exc_info:
            export_model_to_path(raw_doc, worker, "par", out_file)

        assert exc_info.value.error_code == "ARTIFACT_EXPORT_FAILED"
        msg = str(exc_info.value)
        assert "Client" not in msg
        assert "LLC" not in msg
        assert "SECRET_PROJECT" not in msg
        assert "nas01" not in msg
        assert "<details redacted>" in msg

        captured = capsys.readouterr()
        assert "Client" not in captured.err
        assert "LLC" not in captured.err
        assert "SECRET_PROJECT" not in captured.err
        assert "nas01" not in captured.err


# ===========================================================================
# 2. Preview Capture Tests
# ===========================================================================


class TestCapturePreviewImage:
    """Test suite for capture_preview_image."""

    def test_capture_preview_image_success(self, tmp_path: Path) -> None:
        raw_doc = FakePartDocument()
        worker = FakeWorker()
        out_file = tmp_path / "preview.jpg"

        capture_preview_image(raw_doc, worker, out_file, width=1024, height=768)

        assert raw_doc.windows is not None
        win = raw_doc.windows.Item(1)
        assert len(win.View.save_as_image_calls) == 1
        assert win.View.save_as_image_calls[0] == (os.fspath(out_file), 1024, 768)

    def test_capture_preview_image_default_dimensions(self, tmp_path: Path) -> None:
        raw_doc = FakePartDocument()
        worker = FakeWorker()
        out_file = tmp_path / "preview.jpeg"

        capture_preview_image(raw_doc, worker, out_file)

        win = raw_doc.windows.Item(1)  # type: ignore[union-attr]
        assert win.View.save_as_image_calls[0] == (os.fspath(out_file), DEFAULT_PREVIEW_WIDTH, DEFAULT_PREVIEW_HEIGHT)

    def test_capture_preview_image_resolves_relative_path_to_absolute(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        raw_doc = FakePartDocument()
        worker = FakeWorker()
        monkeypatch.chdir(tmp_path)
        rel_file = Path("rel_preview.jpg")

        capture_preview_image(raw_doc, worker, rel_file)

        win = raw_doc.windows.Item(1)  # type: ignore[union-attr]
        expected_abs = os.fspath(tmp_path / "rel_preview.jpg")
        assert win.View.save_as_image_calls[0] == (expected_abs, DEFAULT_PREVIEW_WIDTH, DEFAULT_PREVIEW_HEIGHT)

    def test_capture_preview_image_rejects_non_path(self, tmp_path: Path) -> None:
        raw_doc = FakePartDocument()
        worker = FakeWorker()

        with pytest.raises(CADExportError) as exc_info:
            capture_preview_image(raw_doc, worker, str(tmp_path / "preview.jpg"))  # type: ignore[arg-type]
        assert exc_info.value.error_code == "PREVIEW_EXPORT_FAILED"
        assert "must be a Path instance" in str(exc_info.value)

    @pytest.mark.parametrize("bad_dim", [0, -1, -500, "800", 3.14, True])
    def test_capture_preview_image_rejects_invalid_dimensions(self, tmp_path: Path, bad_dim: Any) -> None:
        raw_doc = FakePartDocument()
        worker = FakeWorker()
        out_file = tmp_path / "preview.jpg"

        with pytest.raises(CADExportError) as exc_info:
            capture_preview_image(raw_doc, worker, out_file, width=bad_dim, height=600)
        assert exc_info.value.error_code == "PREVIEW_EXPORT_FAILED"
        assert "Preview dimensions must be positive integers" in str(exc_info.value)

        with pytest.raises(CADExportError) as exc_info:
            capture_preview_image(raw_doc, worker, out_file, width=800, height=bad_dim)
        assert exc_info.value.error_code == "PREVIEW_EXPORT_FAILED"
        assert "Preview dimensions must be positive integers" in str(exc_info.value)

    def test_capture_preview_image_rejects_missing_parent_directory(self, tmp_path: Path) -> None:
        raw_doc = FakePartDocument()
        worker = FakeWorker()
        out_file = tmp_path / "nonexistent_sub" / "preview.jpg"

        with pytest.raises(CADExportError) as exc_info:
            capture_preview_image(raw_doc, worker, out_file)
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
        assert "Target directory for 'preview.jpg' does not exist" in str(exc_info.value)

    def test_capture_preview_image_rejects_invalid_extension(self, tmp_path: Path) -> None:
        raw_doc = FakePartDocument()
        worker = FakeWorker()
        out_file = tmp_path / "preview.png"

        with pytest.raises(CADExportError) as exc_info:
            capture_preview_image(raw_doc, worker, out_file)
        assert exc_info.value.error_code == "PREVIEW_EXPORT_FAILED"
        assert "must be one of" in str(exc_info.value)

    def test_capture_preview_image_rejects_symlink_target(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        raw_doc = FakePartDocument()
        worker = FakeWorker()
        out_file = tmp_path / "preview.jpg"

        monkeypatch.setattr("drivers.solidedge.exporter._is_symlink_or_reparse", lambda p: True)
        with pytest.raises(CADExportError) as exc_info:
            capture_preview_image(raw_doc, worker, out_file)
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
        assert "must not be a symbolic link or reparse point" in str(exc_info.value)

    def test_capture_preview_image_missing_window_raises_localized_failure_when_healthy(self, tmp_path: Path) -> None:
        raw_doc = FakePartDocument()
        raw_doc.windows = FakeWindowsCollection([])  # Empty collection (Count == 0)
        worker = FakeWorker()
        out_file = tmp_path / "preview.jpg"

        with pytest.raises(CADExportError) as exc_info:
            capture_preview_image(raw_doc, worker, out_file)
        assert exc_info.value.error_code == "PREVIEW_EXPORT_FAILED"
        assert "No active document window" in str(exc_info.value)

    def test_capture_preview_image_missing_view_raises_localized_failure_when_healthy(self, tmp_path: Path) -> None:
        raw_doc = FakePartDocument()
        raw_doc.windows = FakeWindowsCollection([FakeWindow(view=None)])  # Window with no View
        worker = FakeWorker()
        out_file = tmp_path / "preview.jpg"

        with pytest.raises(CADExportError) as exc_info:
            capture_preview_image(raw_doc, worker, out_file)
        assert exc_info.value.error_code == "PREVIEW_EXPORT_FAILED"
        assert "no 3D View object" in str(exc_info.value)

    def test_capture_preview_image_view_com_failure_raises_localized_failure_when_healthy(self, tmp_path: Path) -> None:
        raw_doc = FakePartDocument()
        worker = FakeWorker()
        out_file = tmp_path / "preview.jpg"

        def fail_save_image(filename: str, width: int, height: int) -> None:
            raise RuntimeError(f"GDI rendering failed at {filename} with hex 0x80004005")

        raw_doc.windows.Item(1).View.SaveAsImage = fail_save_image  # type: ignore[union-attr,method-assign]

        with pytest.raises(CADExportError) as exc_info:
            capture_preview_image(raw_doc, worker, out_file)

        assert exc_info.value.error_code == "PREVIEW_EXPORT_FAILED"
        msg = str(exc_info.value)
        assert "preview.jpg" in msg
        assert str(tmp_path) not in msg

    def test_capture_preview_image_raises_document_lost_when_health_probe_fails(self, tmp_path: Path) -> None:
        raw_doc = FakePartDocument()
        worker = FakeWorker()
        out_file = tmp_path / "preview.jpg"

        def crash_save_and_lose_doc(filename: str, width: int, height: int) -> None:
            # Simulate crash: document unseated from process
            raw_doc.Name = ""
            raise ConnectionResetError("Solid Edge process disconnected")

        raw_doc.windows.Item(1).View.SaveAsImage = crash_save_and_lose_doc  # type: ignore[union-attr,method-assign]

        with pytest.raises(CADDocumentError) as exc_info:
            capture_preview_image(raw_doc, worker, out_file)

        assert exc_info.value.error_code == "DOCUMENT_LOST"
        assert "Fatal document loss during preview capture" in str(exc_info.value)

    def test_capture_preview_image_preserves_runtime_busy_error(self, tmp_path: Path) -> None:
        raw_doc = FakePartDocument()
        worker = FakeWorker()
        out_file = tmp_path / "preview.jpg"

        def raise_busy(filename: str, width: int, height: int) -> None:
            raise CADRuntimeBusyError("Server busy timeout")

        raw_doc.windows.Item(1).View.SaveAsImage = raise_busy  # type: ignore[union-attr,method-assign]

        # Must raise CADRuntimeBusyError, NOT PREVIEW_EXPORT_FAILED
        with pytest.raises(CADRuntimeBusyError) as exc_info:
            capture_preview_image(raw_doc, worker, out_file)
        assert exc_info.value.error_code == "RUNTIME_BUSY_TIMEOUT"

    def test_capture_preview_image_preserves_raw_rpc_servercall_retrylater(self, tmp_path: Path) -> None:
        raw_doc = FakePartDocument()
        worker = FakeWorker()
        out_file = tmp_path / "preview.jpg"

        def raise_retrylater(filename: str, width: int, height: int) -> None:
            raise MockCOMError(RPC_E_SERVERCALL_RETRYLATER, "Server call retry later")

        raw_doc.windows.Item(1).View.SaveAsImage = raise_retrylater  # type: ignore[union-attr,method-assign]

        # Raw COM error must be normalized and preserved as fatal CADRuntimeBusyError
        with pytest.raises(CADRuntimeBusyError) as exc_info:
            capture_preview_image(raw_doc, worker, out_file)
        assert exc_info.value.error_code == "RUNTIME_BUSY_TIMEOUT"

    def test_capture_preview_image_preserves_raw_rpc_call_rejected(self, tmp_path: Path) -> None:
        raw_doc = FakePartDocument()
        worker = FakeWorker()
        out_file = tmp_path / "preview.jpg"

        def raise_rejected(filename: str, width: int, height: int) -> None:
            raise MockCOMError(RPC_E_CALL_REJECTED, "Server call rejected")

        raw_doc.windows.Item(1).View.SaveAsImage = raise_rejected  # type: ignore[union-attr,method-assign]

        with pytest.raises(CADRuntimeBusyError) as exc_info:
            capture_preview_image(raw_doc, worker, out_file)
        assert exc_info.value.error_code == "RUNTIME_BUSY_TIMEOUT"

    def test_capture_preview_image_preserves_runtime_unavailable_error(self, tmp_path: Path) -> None:
        raw_doc = FakePartDocument()
        worker = FakeWorker()
        out_file = tmp_path / "preview.jpg"

        def raise_unavailable(filename: str, width: int, height: int) -> None:
            raise CADRuntimeUnavailableError("Solid Edge session died")

        raw_doc.windows.Item(1).View.SaveAsImage = raise_unavailable  # type: ignore[union-attr,method-assign]

        with pytest.raises(CADRuntimeUnavailableError) as exc_info:
            capture_preview_image(raw_doc, worker, out_file)
        assert exc_info.value.error_code == "RUNTIME_UNAVAILABLE"

    def test_capture_preview_image_preserves_raw_rpc_server_died(self, tmp_path: Path) -> None:
        raw_doc = FakePartDocument()
        worker = FakeWorker()
        out_file = tmp_path / "preview.jpg"

        def raise_server_died(filename: str, width: int, height: int) -> None:
            raise MockCOMError(RPC_E_SERVER_DIED, "Server died")

        raw_doc.windows.Item(1).View.SaveAsImage = raise_server_died  # type: ignore[union-attr,method-assign]

        with pytest.raises(CADRuntimeUnavailableError) as exc_info:
            capture_preview_image(raw_doc, worker, out_file)
        assert exc_info.value.error_code == "RUNTIME_UNAVAILABLE"

    def test_capture_preview_image_sanitizes_complex_paths_with_parentheses_and_spaces(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        raw_doc = FakePartDocument()
        worker = FakeWorker()
        out_file = tmp_path / "preview.jpg"

        def raise_sensitive_image_fault(filename: str, width: int, height: int) -> None:
            raise RuntimeError(r"GDI write failed at E:\Client (SECRET_ORGANIZATION)\Private Project\preview.jpg")

        raw_doc.windows.Item(1).View.SaveAsImage = raise_sensitive_image_fault  # type: ignore[union-attr,method-assign]

        with pytest.raises(CADExportError) as exc_info:
            capture_preview_image(raw_doc, worker, out_file)

        assert exc_info.value.error_code == "PREVIEW_EXPORT_FAILED"
        msg = str(exc_info.value)
        assert "SECRET_ORGANIZATION" not in msg
        assert "Private Project" not in msg
        assert "Client" not in msg
        assert "<details redacted>" in msg

        captured = capsys.readouterr()
        assert "SECRET_ORGANIZATION" not in captured.err
        assert "Private Project" not in captured.err
        assert "Client" not in captured.err

    def test_capture_preview_image_sanitizes_paths_with_commas_semicolons_exclamations_and_unc(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        raw_doc = FakePartDocument()
        worker = FakeWorker()
        out_file = tmp_path / "preview.jpg"

        def raise_comma_unc_preview_fault(filename: str, width: int, height: int) -> None:
            raise RuntimeError(
                r"GDI write failed at E:\Client, LLC\SECRET_PROJECT\preview.jpg and \\nas01\share; LLC\SECRET_PROJECT!\preview.jpg: failed"
            )

        raw_doc.windows.Item(1).View.SaveAsImage = raise_comma_unc_preview_fault  # type: ignore[union-attr,method-assign]

        with pytest.raises(CADExportError) as exc_info:
            capture_preview_image(raw_doc, worker, out_file)

        assert exc_info.value.error_code == "PREVIEW_EXPORT_FAILED"
        msg = str(exc_info.value)
        assert "Client" not in msg
        assert "LLC" not in msg
        assert "SECRET_PROJECT" not in msg
        assert "nas01" not in msg
        assert "<details redacted>" in msg

        captured = capsys.readouterr()
        assert "Client" not in captured.err
        assert "LLC" not in captured.err
        assert "SECRET_PROJECT" not in captured.err
        assert "nas01" not in captured.err

    def test_capture_preview_image_no_capture_after_fatal_failure(self, tmp_path: Path) -> None:
        raw_doc = FakePartDocument()
        # Missing windows triggers probe_document_health; simulate broken document
        raw_doc.windows = FakeWindowsCollection([])
        raw_doc.Name = ""
        worker = FakeWorker()
        out_file = tmp_path / "preview.jpg"

        with pytest.raises(CADDocumentError) as exc_info:
            capture_preview_image(raw_doc, worker, out_file)
        assert exc_info.value.error_code == "DOCUMENT_LOST"


# ===========================================================================
# 3. Health Probing Tests
# ===========================================================================


class TestProbeDocumentHealth:
    """Test suite for probe_document_health."""

    def test_probe_document_health_true_on_responsive_document(self) -> None:
        raw_doc = FakePartDocument(name="ActiveModel.par")
        worker = FakeWorker()
        assert probe_document_health(raw_doc, worker) is True

    def test_probe_document_health_false_on_empty_name(self) -> None:
        raw_doc = FakePartDocument(name="")
        worker = FakeWorker()
        assert probe_document_health(raw_doc, worker) is False

    def test_probe_document_health_false_on_com_exception(self) -> None:
        raw_doc = MagicMock()
        type(raw_doc).Name = property(fget=MagicMock(side_effect=RuntimeError("RPC Server Unavailable")))
        worker = FakeWorker()
        assert probe_document_health(raw_doc, worker) is False

    def test_probe_document_health_false_when_doc_or_worker_none(self) -> None:
        assert probe_document_health(None, FakeWorker()) is False
        assert probe_document_health(FakePartDocument(), None) is False


# ===========================================================================
# 4. Sidecar Cleanup Tests
# ===========================================================================


class TestCleanupKnownTranslatorSidecar:
    """Test suite for _cleanup_known_translator_sidecar."""

    def test_cleanup_known_translator_sidecar_success(self, tmp_path: Path) -> None:
        output_file = tmp_path / "model.step"
        sidecar_file = tmp_path / "model.log"
        sidecar_file.write_text("Solid Edge STEP translation log", encoding="utf-8")

        _cleanup_known_translator_sidecar(output_file)
        assert not sidecar_file.exists()

    def test_cleanup_known_translator_sidecar_missing_is_noop(self, tmp_path: Path) -> None:
        output_file = tmp_path / "model.step"
        # model.log does not exist; must complete cleanly without exception
        _cleanup_known_translator_sidecar(output_file)

    def test_cleanup_known_translator_sidecar_directory_rejected(self, tmp_path: Path) -> None:
        output_file = tmp_path / "model.step"
        sidecar_dir = tmp_path / "model.log"
        sidecar_dir.mkdir()

        with pytest.raises(CADExportError) as exc_info:
            _cleanup_known_translator_sidecar(output_file)
        assert exc_info.value.error_code == "ARTIFACT_EXPORT_FAILED"
        assert "not a regular file" in str(exc_info.value)

    def test_cleanup_known_translator_sidecar_unlink_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        output_file = tmp_path / "model.step"
        sidecar_file = tmp_path / "model.log"
        sidecar_file.write_text("log content", encoding="utf-8")

        def mock_unlink(self: Path) -> None:
            raise PermissionError("Access denied unlinking log")

        monkeypatch.setattr(Path, "unlink", mock_unlink)

        with pytest.raises(CADExportError) as exc_info:
            _cleanup_known_translator_sidecar(output_file)
        assert exc_info.value.error_code == "ARTIFACT_EXPORT_FAILED"
        assert "Failed to remove translator sidecar" in str(exc_info.value)
