"""Error sanitization, COM HRESULT normalization, and safe exception suppression."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from interfaces.exceptions import (
    CADError,
    CADRuntimeBusyError,
    CADRuntimeError,
    CADRuntimeUnavailableError,
)

# Standard Windows COM HRESULTs (Canonical unsigned 32-bit representations)
RPC_E_CALL_REJECTED = 0x80010001
RPC_E_SERVERCALL_RETRYLATER = 0x8001010A
RPC_E_SERVER_DIED = 0x8001015B
MK_E_UNAVAILABLE = 0x800401E3
CO_E_SERVER_EXEC_FAILURE = 0x80080005
REGDB_E_CLASSNOTREG = 0x80040154
DISP_E_MEMBERNOTFOUND = 0x80020003

_REDACTED_EXCEPTION_DETAILS = "<details redacted>"


def describe_exception(error: BaseException, context_msg: str | None = None) -> str:
    """Format a fail-closed diagnostic without evaluating untrusted exception text.

    Vendor exception strings can contain arbitrary paths, document metadata, COM
    representations, or other workstation details. Preserve only the exception type
    and caller-supplied internal operation context; never interpolate ``str(error)``.
    """
    safe_summary = f"{type(error).__name__}: {_REDACTED_EXCEPTION_DETAILS}"
    full_msg = f"{context_msg} -> {safe_summary}" if context_msg else safe_summary

    # Write sanitized diagnostic to stderr
    print(f"[DIAGNOSTIC LOG] {full_msg}", file=sys.stderr)
    return full_msg


def _extract_hresult(error: Exception) -> int | None:
    """Extract and canonicalize the unsigned 32-bit HRESULT from an exception if present."""
    # pywintypes.com_error typically has hresult as error.hresult or error.args[0]
    hresult: Any = getattr(error, "hresult", None)
    if isinstance(hresult, int):
        return hresult & 0xFFFFFFFF

    # Check error.args
    if hasattr(error, "args") and len(error.args) > 0:
        first_arg = error.args[0]
        if isinstance(first_arg, int):
            return first_arg & 0xFFFFFFFF

    return None


def normalize_com_error(error: Exception) -> Exception:
    """Map vendor COM exceptions into normalized CAD Copilot domain exceptions."""
    if isinstance(error, CADError):
        return error

    hresult = _extract_hresult(error)
    sanitized_msg = describe_exception(error)

    if hresult is not None:
        if hresult in (RPC_E_CALL_REJECTED, RPC_E_SERVERCALL_RETRYLATER):
            return CADRuntimeBusyError(sanitized_msg)

        if hresult in (MK_E_UNAVAILABLE, CO_E_SERVER_EXEC_FAILURE, RPC_E_SERVER_DIED, REGDB_E_CLASSNOTREG):
            return CADRuntimeUnavailableError(sanitized_msg)

    return CADRuntimeError(sanitized_msg)


@contextmanager
def suppress_com_error(context_msg: str | None = None) -> Iterator[None]:
    """Context manager suppressing COM exceptions during non-critical cleanup paths."""
    try:
        yield
    except Exception as exc:
        describe_exception(exc, context_msg or "Suppressed non-critical COM error")


__all__ = [
    "CO_E_SERVER_EXEC_FAILURE",
    "DISP_E_MEMBERNOTFOUND",
    "MK_E_UNAVAILABLE",
    "REGDB_E_CLASSNOTREG",
    "RPC_E_CALL_REJECTED",
    "RPC_E_SERVERCALL_RETRYLATER",
    "RPC_E_SERVER_DIED",
    "describe_exception",
    "normalize_com_error",
    "suppress_com_error",
]
