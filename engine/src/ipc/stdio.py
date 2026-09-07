"""Strict generation stdio IPC entrypoint and descriptor isolation.

Provides early stdout/stderr preservation, C-runtime and Windows standard-handle
null redirection, bounded request processing, exact single-response stdout delivery,
curated stderr progress events, and exit policy enforcement.
"""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import Callable, Iterator, Mapping
from typing import (
    TYPE_CHECKING,
    Any,
    BinaryIO,
)

from ipc.progress import (
    emit_fatal_diagnostic,
    emit_progress,
    write_all,
)

if TYPE_CHECKING:
    from application.models import GenerationRequest

STD_OUTPUT_HANDLE: int = 0xFFFFFFF5  # Win32 ((DWORD)-11)
STD_ERROR_HANDLE: int = 0xFFFFFFF4  # Win32 ((DWORD)-12)


class ControlledDescriptors:
    """Manages preserved stdout/stderr descriptors and devnull redirection state."""

    def __init__(
        self,
        orig_stdout_fd: int,
        orig_stderr_fd: int,
        null_fd: int,
        *,
        saved_stdout_fd: int | None = None,
        saved_stderr_fd: int | None = None,
        saved_stdout_handle: int | None = None,
        saved_stderr_handle: int | None = None,
    ) -> None:
        self.orig_stdout_fd = orig_stdout_fd
        self.orig_stderr_fd = orig_stderr_fd
        self.null_fd = null_fd
        self.saved_stdout_fd = saved_stdout_fd
        self.saved_stderr_fd = saved_stderr_fd
        self.saved_stdout_handle = saved_stdout_handle
        self.saved_stderr_handle = saved_stderr_handle
        self._closed = False

    def restore(self) -> None:
        """Restore original C-runtime descriptors and Windows standard handles."""
        if self._closed:
            return
        self._closed = True

        if self.saved_stdout_fd is not None:
            with contextlib.suppress(OSError):
                os.dup2(self.saved_stdout_fd, 1)
                os.close(self.saved_stdout_fd)
        if self.saved_stderr_fd is not None:
            with contextlib.suppress(OSError):
                os.dup2(self.saved_stderr_fd, 2)
                os.close(self.saved_stderr_fd)

        if sys.platform == "win32":
            _restore_windows_handles(self.saved_stdout_handle, self.saved_stderr_handle)

        for fd in (self.orig_stdout_fd, self.orig_stderr_fd, self.null_fd):
            with contextlib.suppress(OSError):
                os.close(fd)

    def close(self) -> None:
        """Close preserved and null descriptors without restoring standard streams."""
        if self._closed:
            return
        self._closed = True
        for fd in (self.orig_stdout_fd, self.orig_stderr_fd, self.null_fd):
            with contextlib.suppress(OSError):
                os.close(fd)


def _redirect_windows_handles(null_fd: int) -> tuple[int | None, int | None]:
    """Redirect Windows STD_OUTPUT_HANDLE and STD_ERROR_HANDLE to devnull.

    Returns:
        Tuple of (original_stdout_handle, original_stderr_handle).

    Raises:
        OSError: If getting or setting standard handles fails.
    """
    if sys.platform != "win32":
        return None, None

    import ctypes
    import msvcrt
    from ctypes import wintypes

    raw_invalid = wintypes.HANDLE(-1).value
    invalid_handle_value: int = raw_invalid if raw_invalid is not None else -1

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.SetStdHandle.argtypes = [wintypes.DWORD, wintypes.HANDLE]
    kernel32.SetStdHandle.restype = wintypes.BOOL
    kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel32.GetStdHandle.restype = wintypes.HANDLE

    orig_stdout_handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
    if orig_stdout_handle == invalid_handle_value:
        err = ctypes.get_last_error()
        raise OSError(f"GetStdHandle(STD_OUTPUT_HANDLE) failed with error {err}")

    orig_stderr_handle = kernel32.GetStdHandle(STD_ERROR_HANDLE)
    if orig_stderr_handle == invalid_handle_value:
        err = ctypes.get_last_error()
        raise OSError(f"GetStdHandle(STD_ERROR_HANDLE) failed with error {err}")

    null_os_handle = msvcrt.get_osfhandle(null_fd)
    if null_os_handle == -1:
        err = ctypes.get_last_error()
        raise OSError(f"msvcrt.get_osfhandle(null_fd) failed with error {err}")

    ret_out = kernel32.SetStdHandle(STD_OUTPUT_HANDLE, null_os_handle)
    if ret_out == 0:
        err = ctypes.get_last_error()
        raise OSError(f"SetStdHandle(STD_OUTPUT_HANDLE) failed with error {err}")

    ret_err = kernel32.SetStdHandle(STD_ERROR_HANDLE, null_os_handle)
    if ret_err == 0:
        err = ctypes.get_last_error()
        # Roll back stdout handle before raising
        kernel32.SetStdHandle(STD_OUTPUT_HANDLE, orig_stdout_handle)
        raise OSError(f"SetStdHandle(STD_ERROR_HANDLE) failed with error {err}")

    return orig_stdout_handle, orig_stderr_handle


def _restore_windows_handles(
    stdout_handle: int | None,
    stderr_handle: int | None,
) -> None:
    """Restore Windows STD_OUTPUT_HANDLE and STD_ERROR_HANDLE."""
    if sys.platform != "win32":
        return

    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.SetStdHandle.argtypes = [wintypes.DWORD, wintypes.HANDLE]
    kernel32.SetStdHandle.restype = wintypes.BOOL

    if stdout_handle is not None:
        kernel32.SetStdHandle(STD_OUTPUT_HANDLE, stdout_handle)
    if stderr_handle is not None:
        kernel32.SetStdHandle(STD_ERROR_HANDLE, stderr_handle)


def establish_controlled_descriptors(
    *,
    save_restore_state: bool = False,
) -> ControlledDescriptors:
    """Establish descriptor isolation by duplicating stdout/stderr and redirecting to devnull.

    Flushes sys.stdout and sys.stderr, duplicates descriptors 1 and 2, opens devnull,
    redirects descriptors 1 and 2 with os.dup2, and on Windows updates SetStdHandle.

    Args:
        save_restore_state: If True, saves state required for ControlledDescriptors.restore().

    Returns:
        ControlledDescriptors instance holding preserved output descriptors.

    Raises:
        OSError: If duplicating, redirecting, or setting handles fails.
    """
    with contextlib.suppress(Exception):
        sys.stdout.flush()
    with contextlib.suppress(Exception):
        sys.stderr.flush()

    saved_stdout_fd: int | None = None
    saved_stderr_fd: int | None = None
    orig_stdout_fd: int | None = None
    orig_stderr_fd: int | None = None
    null_fd: int | None = None

    try:
        if save_restore_state:
            saved_stdout_fd = os.dup(1)
            saved_stderr_fd = os.dup(2)

        orig_stdout_fd = os.dup(1)
        orig_stderr_fd = os.dup(2)

        null_fd = os.open(os.devnull, os.O_RDWR)

        os.dup2(null_fd, 1)
        os.dup2(null_fd, 2)

        saved_out_h, saved_err_h = _redirect_windows_handles(null_fd)

        return ControlledDescriptors(
            orig_stdout_fd=orig_stdout_fd,
            orig_stderr_fd=orig_stderr_fd,
            null_fd=null_fd,
            saved_stdout_fd=saved_stdout_fd,
            saved_stderr_fd=saved_stderr_fd,
            saved_stdout_handle=saved_out_h if save_restore_state else None,
            saved_stderr_handle=saved_err_h if save_restore_state else None,
        )
    except Exception:
        if orig_stdout_fd is not None:
            with contextlib.suppress(OSError):
                os.dup2(orig_stdout_fd, 1)
                os.close(orig_stdout_fd)
        if orig_stderr_fd is not None:
            with contextlib.suppress(OSError):
                os.dup2(orig_stderr_fd, 2)
                os.close(orig_stderr_fd)
        if saved_stdout_fd is not None:
            with contextlib.suppress(OSError):
                os.close(saved_stdout_fd)
        if saved_stderr_fd is not None:
            with contextlib.suppress(OSError):
                os.close(saved_stderr_fd)
        if null_fd is not None:
            with contextlib.suppress(OSError):
                os.close(null_fd)
        raise


@contextlib.contextmanager
def controlled_stdio() -> Iterator[ControlledDescriptors]:
    """Context manager establishing controlled stdio and restoring original streams on exit."""
    descriptors = establish_controlled_descriptors(save_restore_state=True)
    try:
        yield descriptors
    finally:
        descriptors.restore()


def main(
    argv: list[str] | None = None,
    *,
    _composition_handler: Callable[[GenerationRequest], Mapping[str, Any]] | None = None,
) -> int:
    """Execute the CAD Copilot Generation stdio IPC entrypoint.

    Args:
        argv: Optional command-line argument list (defaults to sys.argv[1:]).
        _composition_handler: Optional custom composition callable for test injection.

    Returns:
        Exit code: 0 for handled response, 130 for cancellation, 1 for fatal error.
    """
    return _run_stdio(
        argv=argv,
        _composition_handler=_composition_handler,
    )


def _run_stdio(
    argv: list[str] | None = None,
    *,
    _composition_handler: Callable[[GenerationRequest], Mapping[str, Any]] | None = None,
    _stdin_stream: BinaryIO | None = None,
    _descriptors: ControlledDescriptors | None = None,
) -> int:
    """Internal runner for generation stdio IPC with configurable stream and descriptor injection."""
    descriptors = _descriptors
    owns_descriptors = False

    try:
        if descriptors is None:
            descriptors = establish_controlled_descriptors(save_restore_state=False)
            owns_descriptors = True

        # Production modules are strictly imported AFTER descriptor redirection is active
        from ipc.contracts import (
            InvalidRequestError,
            PayloadTooLargeError,
            ResponseValidationError,
            build_fallback_internal_error_response,
            build_rejected_error_response,
            build_typed_generation_request,
            decode_and_parse_request_json,
            read_bounded_request,
            serialize_response,
            validate_response_payload,
        )

        # Validate arguments; unexpected flags indicate process misuse
        cmd_args = sys.argv[1:] if argv is None else argv
        if len(cmd_args) > 0:
            emit_fatal_diagnostic(descriptors.orig_stderr_fd)
            return 1

        # Read incoming request from binary input stream up to wire limit
        input_stream = _stdin_stream if _stdin_stream is not None else sys.stdin.buffer
        oversize_error = False
        raw_bytes = b""

        try:
            raw_bytes = read_bounded_request(input_stream)
        except PayloadTooLargeError:
            oversize_error = True

        # Signal request reception before payload validation
        emit_progress(descriptors.orig_stderr_fd, "request_received")

        if oversize_error:
            resp_payload = build_rejected_error_response("PAYLOAD_TOO_LARGE", request_id="unknown")
            validate_response_payload(resp_payload)
            serialized = serialize_response(resp_payload)
            emit_progress(descriptors.orig_stderr_fd, "response_ready")
            write_all(descriptors.orig_stdout_fd, serialized)
            return 0

        # Strict JSON decode and typed request construction
        try:
            parsed_dict, _safe_req_id = decode_and_parse_request_json(raw_bytes)
            typed_req = build_typed_generation_request(parsed_dict)
        except PayloadTooLargeError:
            resp_payload = build_rejected_error_response("PAYLOAD_TOO_LARGE", request_id="unknown")
            validate_response_payload(resp_payload)
            serialized = serialize_response(resp_payload)
            emit_progress(descriptors.orig_stderr_fd, "response_ready")
            write_all(descriptors.orig_stdout_fd, serialized)
            return 0
        except InvalidRequestError as exc:
            resp_payload = build_rejected_error_response(exc.code, request_id=exc.request_id)
            validate_response_payload(resp_payload)
            serialized = serialize_response(resp_payload)
            emit_progress(descriptors.orig_stderr_fd, "response_ready")
            write_all(descriptors.orig_stdout_fd, serialized)
            return 0

        # Signal successful schema and model validation
        emit_progress(descriptors.orig_stderr_fd, "request_validated")

        # Emit progress immediately before service execution
        emit_progress(descriptors.orig_stderr_fd, "generation_started")

        try:
            if _composition_handler is not None:
                service_result = _composition_handler(typed_req)
            else:
                from ipc.composition import run_generation

                service_result = run_generation(typed_req)
        except Exception:
            # Unhandled service failure: build sanitized fallback internal error
            service_result = build_fallback_internal_error_response(typed_req.request_id)

        # Enforce response schema contract; fallback to sanitized internal error on violation
        try:
            validate_response_payload(service_result)
            final_response = service_result
        except ResponseValidationError:
            fallback = build_fallback_internal_error_response(typed_req.request_id)
            validate_response_payload(fallback)
            final_response = fallback

        # Serialize compact 7-bit ASCII response
        serialized_final = serialize_response(final_response)

        # Emit contract completion progress before delivering response
        emit_progress(descriptors.orig_stderr_fd, "response_ready")

        # Deliver single response to preserved standard output
        write_all(descriptors.orig_stdout_fd, serialized_final)
        return 0

    except KeyboardInterrupt:
        # Cancellation via Ctrl+C / CTRL_BREAK: allow Python unwinding, no stdout response, exit 130
        return 130
    except BaseException:
        # Every non-KeyboardInterrupt BaseException (including SystemExit):
        # Treat as fatal: emit sanitized diagnostic, keep stdout empty, exit 1
        if descriptors is not None:
            with contextlib.suppress(Exception):
                emit_fatal_diagnostic(descriptors.orig_stderr_fd)
        else:
            with contextlib.suppress(Exception):
                write_all(
                    2,
                    b'{"type":"diagnostic","phase":"fatal","message":"Generation process failed before a contract response could be produced."}\n',
                )
        return 1
    finally:
        if owns_descriptors and descriptors is not None:
            descriptors.close()
