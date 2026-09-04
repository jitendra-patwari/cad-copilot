"""Unit tests for error sanitization, canonical HRESULT mapping, and stderr diagnostic logging."""

from __future__ import annotations

import pytest

from drivers.solidedge.errors import (
    MK_E_UNAVAILABLE,
    RPC_E_CALL_REJECTED,
    RPC_E_SERVERCALL_RETRYLATER,
    describe_exception,
    normalize_com_error,
    suppress_com_error,
)
from interfaces.exceptions import (
    CADDocumentError,
    CADRuntimeBusyError,
    CADRuntimeError,
    CADRuntimeUnavailableError,
)


class MockCOMError(Exception):
    """Fake pywintypes.com_error for offline testing."""

    def __init__(self, hresult: int, message: str) -> None:
        super().__init__(message)
        self.hresult = hresult
        self.args = (hresult, message, None, None)


def test_describe_exception_omits_drive_and_unc_paths(capsys: pytest.CaptureFixture[str]) -> None:
    """Proves that raw Windows drive and UNC path details are never retained."""
    err = RuntimeError(r"Failed to open C:\Users\Admin\Desktop\secret_cad\part1.par and \\nas01\cad_share\model.par")
    sanitized = describe_exception(err, "File operation failed")

    assert "C:\\Users" not in sanitized
    assert "\\\\nas01" not in sanitized
    assert sanitized == "File operation failed -> RuntimeError: <details redacted>"

    captured = capsys.readouterr()
    assert captured.out == ""  # Zero stdout contamination!
    assert "[DIAGNOSTIC LOG] File operation failed -> RuntimeError: <details redacted>" in captured.err


def test_describe_exception_omits_guids_and_hex_codes(capsys: pytest.CaptureFixture[str]) -> None:
    """Proves that raw COM GUID and HRESULT details are never retained."""
    err = ValueError("Interface {00020813-0000-0000-C000-000000000046} failed with code 0x80010001")
    sanitized = describe_exception(err)

    assert "{00020813" not in sanitized
    assert "0x80010001" not in sanitized
    assert sanitized == "ValueError: <details redacted>"

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "[DIAGNOSTIC LOG]" in captured.err


def test_normalize_com_error_busy_hresults() -> None:
    """Proves that RPC_E_CALL_REJECTED (0x80010001) and RPC_E_SERVERCALL_RETRYLATER (0x8001010A) map to CADRuntimeBusyError."""
    err_rejected = MockCOMError(RPC_E_CALL_REJECTED, "Server rejected call")
    norm_rejected = normalize_com_error(err_rejected)
    assert isinstance(norm_rejected, CADRuntimeBusyError)
    assert norm_rejected.error_code == "RUNTIME_BUSY_TIMEOUT"

    err_retry = MockCOMError(RPC_E_SERVERCALL_RETRYLATER, "Server busy retry later")
    norm_retry = normalize_com_error(err_retry)
    assert isinstance(norm_retry, CADRuntimeBusyError)


def test_normalize_com_error_signed_hresult_canonicalization() -> None:
    """Proves that signed 32-bit HRESULTs from pywin32 (e.g. -2147221021) are canonicalized correctly."""
    # -2147221021 as signed 32-bit int is 0x800401E3 (MK_E_UNAVAILABLE)
    signed_mk_unavailable = -2147221021
    assert (signed_mk_unavailable & 0xFFFFFFFF) == MK_E_UNAVAILABLE

    err = MockCOMError(signed_mk_unavailable, "Operation unavailable")
    norm = normalize_com_error(err)
    assert isinstance(norm, CADRuntimeUnavailableError)
    assert norm.error_code == "RUNTIME_UNAVAILABLE"


def test_normalize_com_error_passes_cad_error_through() -> None:
    """Proves that domain CADError exceptions are returned untouched."""
    doc_err = CADDocumentError("Custom document error", error_code="DOC_NOT_FOUND")
    norm = normalize_com_error(doc_err)
    assert norm is doc_err
    assert norm.error_code == "DOC_NOT_FOUND"


def test_normalize_com_error_general_fallback() -> None:
    """Proves that unclassified exceptions fall back to CADRuntimeError."""
    generic_err = RuntimeError("Something went wrong in driver")
    norm = normalize_com_error(generic_err)
    assert isinstance(norm, CADRuntimeError)
    assert norm.error_code == "RUNTIME_ATTACH_FAILED"


def test_suppress_com_error_context_manager(capsys: pytest.CaptureFixture[str]) -> None:
    """Proves that suppress_com_error catches exceptions, suppresses them, and logs sanitized info to stderr."""
    with suppress_com_error("Cleanup operation"):
        raise MockCOMError(0x80004005, r"Non-critical error in C:\CAD\dummy.par")

    # Flow continues without raising
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "[DIAGNOSTIC LOG] Cleanup operation -> MockCOMError: <details redacted>" in captured.err
    assert "C:\\CAD" not in captured.err


def test_describe_exception_masks_paths_with_parentheses_and_spaces(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Proves path details with parentheses and spaces are completely omitted."""
    err = OSError(r"Failed to write to E:\Client (SECRET_ORGANIZATION)\Private Project\model.par: access denied")
    sanitized = describe_exception(err, "Export failed")

    # Assert no private organization or folder names leak in the returned string
    assert "SECRET_ORGANIZATION" not in sanitized
    assert "Private Project" not in sanitized
    assert "Client" not in sanitized
    assert "model.par" not in sanitized
    assert sanitized == "Export failed -> OSError: <details redacted>"

    # Assert stderr log is also completely sanitized
    captured = capsys.readouterr()
    assert "SECRET_ORGANIZATION" not in captured.err
    assert "Private Project" not in captured.err
    assert "[DIAGNOSTIC LOG] Export failed -> OSError: <details redacted>" in captured.err


def test_describe_exception_masks_paths_with_commas_semicolons_and_exclamations(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Proves punctuation and UNC details are completely omitted."""
    # 1. Drive path with comma and space: E:\Client, LLC\SECRET_PROJECT\model.par
    err_comma = OSError(r"Failed writing to E:\Client, LLC\SECRET_PROJECT\model.par: access denied")
    san_comma = describe_exception(err_comma, "Export PAR")
    assert "Client" not in san_comma
    assert "LLC" not in san_comma
    assert "SECRET_PROJECT" not in san_comma
    assert "model.par" not in san_comma
    assert san_comma == "Export PAR -> OSError: <details redacted>"

    # 2. Path with semicolon and exclamation mark
    err_semi_excl = RuntimeError(r"Failed writing to E:\Client; LLC\SECRET_PROJECT!\model.par; operation aborted")
    san_semi_excl = describe_exception(err_semi_excl)
    assert "Client" not in san_semi_excl
    assert "SECRET_PROJECT" not in san_semi_excl
    assert san_semi_excl == "RuntimeError: <details redacted>"

    # 3. UNC path with comma in share/folder
    err_unc = OSError(r"Failed writing to \\nas01\share, LLC\SECRET_PROJECT\model.par: access denied")
    san_unc = describe_exception(err_unc)
    assert "nas01" not in san_unc
    assert "share, LLC" not in san_unc
    assert "SECRET_PROJECT" not in san_unc
    assert san_unc == "OSError: <details redacted>"

    # Stderr logging verification
    captured = capsys.readouterr()
    assert "Client" not in captured.err
    assert "SECRET_PROJECT" not in captured.err
    assert "nas01" not in captured.err
    assert "[DIAGNOSTIC LOG] Export PAR -> OSError: <details redacted>" in captured.err


@pytest.mark.parametrize(
    "private_path",
    [
        r"E:\Client\model SECRET_DETAILS.par",
        r"E:\Client\model,SECRET_DETAILS.par",
        r"E:\Client\model;SECRET_DETAILS.par",
        r"E:\Client\model!SECRET_DETAILS.par",
        r"E:\Client\model'SECRET_DETAILS.par",
        "E:\\Résumé\\SECRET_DETAILS\\model.par",
        "E:\\Client\\秘密_SECRET_DETAILS_model.par",
        r"\\nas01\share\model SECRET_DETAILS.par",
    ],
)
def test_describe_exception_never_evaluates_adversarial_path_details(
    private_path: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Proves legal punctuation and Unicode cannot escape through vendor exception text."""

    class ExplosiveError(Exception):
        def __str__(self) -> str:
            raise AssertionError(f"Exception text must not be evaluated: {private_path}")

    sanitized = describe_exception(ExplosiveError(), "Export")

    assert sanitized == "Export -> ExplosiveError: <details redacted>"
    captured = capsys.readouterr()
    assert captured.err == "[DIAGNOSTIC LOG] Export -> ExplosiveError: <details redacted>\n"


def test_close_document_stderr_omits_handle_uuid(capsys: pytest.CaptureFixture[str]) -> None:
    """Proves close_document diagnostic output on stderr never contains document handle UUIDs."""
    from typing import Any

    from drivers.solidedge.runtime import SolidEdgeRuntime
    from drivers.solidedge.types import SolidEdgePartDocumentHandle

    runtime = SolidEdgeRuntime()
    secret_uuid = "12345678-abcd-ef01-2345-6789abcdef01"
    doc_handle = SolidEdgePartDocumentHandle(handle_id=secret_uuid)
    runtime._open_document_handles[secret_uuid] = doc_handle

    class MockWorker:
        def __init__(self) -> None:
            self._document_registry: dict[str, Any] = {}

        def is_alive(self) -> bool:
            return True

        def call(self, func: Any, timeout: float | None = None) -> Any:
            raise RuntimeError("Underlying COM failure on close: C:\\Users\\secret\\model.par")

    runtime._worker = MockWorker()  # type: ignore[assignment]

    with pytest.raises(RuntimeError):
        runtime.close_document(doc_handle)

    captured = capsys.readouterr()
    assert secret_uuid not in captured.err
    assert "secret" not in captured.err
    assert "Error closing document -> RuntimeError: <details redacted>" in captured.err


def test_teardown_stderr_omits_handle_uuid(capsys: pytest.CaptureFixture[str]) -> None:
    """Proves teardown diagnostic output on stderr never contains document handle UUIDs."""
    from typing import Any

    from drivers.solidedge.runtime import SolidEdgeRuntime
    from drivers.solidedge.types import SolidEdgePartDocumentHandle

    runtime = SolidEdgeRuntime()
    secret_uuid = "98765432-fedc-ba98-7654-3210fedcba98"
    doc_handle = SolidEdgePartDocumentHandle(handle_id=secret_uuid)
    runtime._open_document_handles[secret_uuid] = doc_handle

    class MockWorkerTeardown:
        def __init__(self) -> None:
            self._document_registry: dict[str, Any] = {}

        def is_alive(self) -> bool:
            return True

        def call(self, func: Any, timeout: float | None = None) -> Any:
            raise RuntimeError("Underlying COM failure during teardown: C:\\Sensitive\\path.par")

        def shutdown(self, timeout: float = 3.0) -> None:
            pass

    runtime._worker = MockWorkerTeardown()  # type: ignore[assignment]

    runtime.teardown()

    captured = capsys.readouterr()
    assert secret_uuid not in captured.err
    assert "Sensitive" not in captured.err
    assert "Teardown document close timeout/error -> RuntimeError: <details redacted>" in captured.err
