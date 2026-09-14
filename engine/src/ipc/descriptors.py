"""Low-level descriptor isolation, standard-stream preservation, and devnull redirection.

Provides generic C-runtime and Windows standard-handle redirection to devnull,
allowing strict stdio transports to prevent console output contamination from
C-runtime, COM clients, logging, or unhandled print statements.
"""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import Iterator
from typing import Final

STD_OUTPUT_HANDLE: Final[int] = 0xFFFFFFF5  # Win32 ((DWORD)-11)
STD_ERROR_HANDLE: Final[int] = 0xFFFFFFF4  # Win32 ((DWORD)-12)


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
