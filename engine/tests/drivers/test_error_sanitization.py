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


def test_describe_exception_masks_drive_and_unc_paths(capsys: pytest.CaptureFixture[str]) -> None:
    """Proves that Windows drive paths and UNC network paths are masked to <path>."""
    err = RuntimeError(r"Failed to open C:\Users\Admin\Desktop\secret_cad\part1.par and \\nas01\cad_share\model.par")
    sanitized = describe_exception(err, "File operation failed")

    assert "C:\\Users" not in sanitized
    assert "\\\\nas01" not in sanitized
    assert "<path>" in sanitized
    assert sanitized.startswith("File operation failed -> RuntimeError: Failed to open <path>")

    captured = capsys.readouterr()
    assert captured.out == ""  # Zero stdout contamination!
    assert "[DIAGNOSTIC LOG] File operation failed -> RuntimeError: Failed to open <path>" in captured.err


def test_describe_exception_masks_guids_and_hex_codes(capsys: pytest.CaptureFixture[str]) -> None:
    """Proves that COM GUIDs and Hex HRESULT codes are masked."""
    err = ValueError("Interface {00020813-0000-0000-C000-000000000046} failed with code 0x80010001")
    sanitized = describe_exception(err)

    assert "{00020813" not in sanitized
    assert "0x80010001" not in sanitized
    assert "<GUID>" in sanitized
    assert "<HEX>" in sanitized

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
    assert "[DIAGNOSTIC LOG] Cleanup operation -> MockCOMError:" in captured.err
    assert "C:\\CAD" not in captured.err
