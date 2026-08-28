"""Windows process identity extraction, lifetime verification, and safe termination."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from typing import Any

from .errors import describe_exception
from .types import ProcessIdentity

# Windows Process Access Rights & Constants
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_QUERY_INFORMATION = 0x0400
STILL_ACTIVE = 259


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", ctypes.c_uint32), ("dwHighDateTime", ctypes.c_uint32)]


def try_get_process_id(application: Any) -> int | None:
    """Attempt to extract the OS Process ID from a Solid Edge application COM object."""
    if application is None:
        return None

    # Primary: check ProcessID / ProcessId property
    for attr in ("ProcessID", "ProcessId", "process_id"):
        try:
            pid = getattr(application, attr, None)
            if pid is not None and isinstance(pid, int) and pid > 0:
                return pid
        except Exception:
            pass

    # Secondary: check hWnd property and query GetWindowThreadProcessId
    try:
        hwnd = getattr(application, "hWnd", None)
        if hwnd is not None and isinstance(hwnd, int) and hwnd > 0 and sys.platform == "win32":
            pid_out = ctypes.c_ulong()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_out))
            if pid_out.value > 0:
                return int(pid_out.value)
    except Exception:
        pass

    return None


def get_process_identity(pid: int) -> ProcessIdentity | None:
    """Extract immutable ProcessIdentity (PID + 64-bit creation timestamp) for a process."""
    if pid <= 0:
        return None

    if sys.platform != "win32":
        # Portable non-Windows fallback for test runners
        return ProcessIdentity(pid=pid, creation_time_ft=1000000)

    try:
        kernel32 = ctypes.windll.kernel32
        h_process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h_process:
            h_process = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
        if not h_process:
            return None

        ft_creation = _FILETIME()
        ft_exit = _FILETIME()
        ft_kernel = _FILETIME()
        ft_user = _FILETIME()

        success = kernel32.GetProcessTimes(
            h_process,
            ctypes.byref(ft_creation),
            ctypes.byref(ft_exit),
            ctypes.byref(ft_kernel),
            ctypes.byref(ft_user),
        )
        kernel32.CloseHandle(h_process)

        if not success:
            return None

        creation_time_ft = (ft_creation.dwHighDateTime << 32) | ft_creation.dwLowDateTime
        return ProcessIdentity(pid=pid, creation_time_ft=creation_time_ft)
    except Exception as exc:
        describe_exception(exc, f"Failed to extract process identity for PID {pid}")
        return None


def is_process_alive(pid: int) -> bool:
    """Check whether a process with the given PID is currently running."""
    if pid <= 0:
        return False

    if sys.platform == "win32":
        try:
            kernel32 = ctypes.windll.kernel32
            h_process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if h_process:
                exit_code = ctypes.c_ulong()
                kernel32.GetExitCodeProcess(h_process, ctypes.byref(exit_code))
                kernel32.CloseHandle(h_process)
                return exit_code.value == STILL_ACTIVE
        except Exception:
            pass

    # Fallback via os.kill(pid, 0)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        # Process exists but we lack permission to signal it
        return True
    except OSError:
        return False


def kill_orphan_processes(identities: list[ProcessIdentity] | tuple[ProcessIdentity, ...] | None = None) -> list[int]:
    """Safely terminate orphan Solid Edge processes after verifying process creation identity."""
    if not identities:
        return []

    terminated_pids: list[int] = []

    for identity in identities:
        if identity.pid <= 0:
            continue

        if not is_process_alive(identity.pid):
            # Already exited
            terminated_pids.append(identity.pid)
            continue

        # Re-verify process identity before attempting taskkill (Strict Fail-Closed)
        current_identity = get_process_identity(identity.pid)
        if (
            current_identity is None
            or identity.creation_time_ft <= 0
            or current_identity.creation_time_ft != identity.creation_time_ft
        ):
            curr_ft = current_identity.creation_time_ft if current_identity else None
            print(
                f"[DIAGNOSTIC LOG] Aborted termination: PID {identity.pid} identity unverified or creation timestamp mismatch "
                f"({curr_ft} != {identity.creation_time_ft})",
                file=sys.stderr,
            )
            continue

        try:
            if sys.platform == "win32":
                cmd = ["taskkill", "/F", "/FI", "IMAGENAME eq Edge.exe", "/PID", str(identity.pid)]
                subprocess.run(cmd, capture_output=True, timeout=5, check=False)
            else:
                os.kill(identity.pid, 9)
        except Exception as exc:
            describe_exception(exc, f"Error terminating process {identity.pid}")

        if not is_process_alive(identity.pid):
            terminated_pids.append(identity.pid)

    return terminated_pids


__all__ = [
    "get_process_identity",
    "is_process_alive",
    "kill_orphan_processes",
    "try_get_process_id",
]
