"""Filesystem validation, identity capture, reparse checks, and containment primitives.

This module provides low-level filesystem safety primitives for CAD Copilot batch workflows:
- PathIdentity: immutable device/inode/mode representation with typed failure reasons.
- Root validation for user-selected local input and output roots with Windows drive classification.
- Windows reserved device name and relative path component validation.
- Symbolic link and Windows reparse point detection (fails closed).
- Strict root containment and canonical component path walking.
- Error sanitization ensuring zero local workstation path leakage.
"""

from __future__ import annotations

import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from batch.allocation import BatchSafetyRejectionError
from batch.models import BatchDiagnostic

# Windows reserved device names (CON, PRN, AUX, NUL, COM1-9, LPT1-9)
WINDOWS_RESERVED_DEVICE_NAMES: Final[frozenset[str]] = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "CLOCK$",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)

# Forbidden characters in Windows path segments (excluding slashes and control characters)
WINDOWS_FORBIDDEN_CHARS: Final[frozenset[str]] = frozenset({"*", "?", '"', "<", ">", "|"})

# Pattern for Windows drive qualification (e.g. C:\ or C:/)
DRIVE_QUALIFIED_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z]:[\\/]")

# Windows GetDriveTypeW constants
DRIVE_UNKNOWN: Final[int] = 0
DRIVE_NO_ROOT_DIR: Final[int] = 1
DRIVE_REMOVABLE: Final[int] = 2
DRIVE_FIXED: Final[int] = 3
DRIVE_REMOTE: Final[int] = 4
DRIVE_CDROM: Final[int] = 5
DRIVE_RAMDISK: Final[int] = 6

# Allowed local drive types for CAD Copilot batch processing
ALLOWED_LOCAL_DRIVE_TYPES: Final[frozenset[int]] = frozenset({DRIVE_REMOVABLE, DRIVE_FIXED, DRIVE_RAMDISK})

PathIdentityReason = Literal["not_found", "permission_denied", "io_error"]


class PathIdentityError(OSError):
    """Base exception when filesystem identity cannot be determined, carrying a typed reason."""

    def __init__(self, reason: PathIdentityReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


class PathNotFoundError(PathIdentityError, FileNotFoundError):
    """Raised when a path does not exist during identity capture."""

    def __init__(self, message: str = "Path does not exist") -> None:
        super().__init__("not_found", message)


class PathPermissionError(PathIdentityError, PermissionError):
    """Raised when access is denied during identity capture."""

    def __init__(self, message: str = "Access denied") -> None:
        super().__init__("permission_denied", message)


@dataclass(frozen=True)
class PathIdentity:
    """Immutable filesystem identity tuple representing device, inode/file-index, and mode."""

    device: int
    inode: int
    mode: int

    def __post_init__(self) -> None:
        if not isinstance(self.device, int):
            raise TypeError(f"PathIdentity.device must be int, got {type(self.device).__name__}")
        if not isinstance(self.inode, int):
            raise TypeError(f"PathIdentity.inode must be int, got {type(self.inode).__name__}")
        if not isinstance(self.mode, int):
            raise TypeError(f"PathIdentity.mode must be int, got {type(self.mode).__name__}")

    @property
    def is_regular_file(self) -> bool:
        """Return True if mode represents a regular file."""
        return stat.S_ISREG(self.mode)

    @property
    def is_directory(self) -> bool:
        """Return True if mode represents a directory."""
        return stat.S_ISDIR(self.mode)


def get_path_identity(path: Path, *, follow_symlinks: bool = False) -> PathIdentity:
    """Capture the PathIdentity of an existing path using lstat (default) or stat.

    Raises PathNotFoundError if path does not exist, PathPermissionError if permission
    is denied, or PathIdentityError for other I/O errors.
    """
    try:
        st = path.stat() if follow_symlinks else path.lstat()
        return PathIdentity(device=st.st_dev, inode=st.st_ino, mode=st.st_mode)
    except FileNotFoundError as exc:
        raise PathNotFoundError(f"Path does not exist: {path.name}") from exc
    except PermissionError as exc:
        raise PathPermissionError(f"Access denied for path: {path.name}") from exc
    except (OSError, ValueError) as exc:
        raise PathIdentityError(
            "io_error", f"Cannot determine filesystem identity for path: {type(exc).__name__}"
        ) from exc


def is_stat_reparse_point(st: os.stat_result) -> bool:
    """Return True if stat_result indicates a symbolic link or Windows reparse point."""
    if stat.S_ISLNK(st.st_mode):
        return True
    if sys.platform == "win32":
        reparse_attr = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        attrs = getattr(st, "st_file_attributes", 0) or 0
        tag = getattr(st, "st_reparse_tag", 0) or 0
        if (attrs & reparse_attr) != 0 or tag != 0:
            return True
    return False


def is_symlink_or_reparse_point(path: Path) -> bool:
    """Check whether path is a symbolic link or Windows reparse point/junction.

    Fails closed: returns True if path attributes cannot be determined due to
    permission or I/O errors, distinguishing expected FileNotFoundError.
    """
    try:
        st = path.lstat()
    except FileNotFoundError:
        return False
    except OSError, ValueError:
        return True

    return is_stat_reparse_point(st)


def is_reserved_device_name(segment: str) -> bool:
    """Return True if a path segment or its stem matches a Windows reserved device name."""
    if not segment:
        return False
    stem = segment.split(".")[0].upper()
    if stem in WINDOWS_RESERVED_DEVICE_NAMES:
        return True
    if sys.platform == "win32":
        try:
            if os.path.isreserved(segment):
                return True
        except Exception:
            pass
    return False


def validate_relative_segment(segment: str, *, request_id: str = "unknown") -> None:
    """Validate a single relative path segment for Windows filesystem safety."""

    def _reject(msg: str) -> None:
        raise BatchSafetyRejectionError(
            BatchDiagnostic(code="INPUT_PATH_NOT_ALLOWED", message=msg),
            request_id=request_id,
        )

    if not segment or segment in (".", ".."):
        _reject("Input path contains forbidden traversal or empty segment")
    if segment.startswith(" ") or segment.endswith(" "):
        _reject("Input path segment has leading or trailing whitespace")
    if segment.endswith("."):
        _reject("Input path segment has trailing dots")
    if any(ord(c) < 32 for c in segment):
        _reject("Input path contains forbidden control characters")
    if ":" in segment or any(c in segment for c in WINDOWS_FORBIDDEN_CHARS):
        _reject("Input path contains forbidden characters or stream colons")
    if is_reserved_device_name(segment):
        _reject("Input path uses reserved Windows device name")


def get_windows_drive_type(drive_root: str) -> int:
    """Return Windows drive type integer using GetDriveTypeW.

    Returns DRIVE_UNKNOWN on non-Windows or if API call fails.
    """
    if sys.platform != "win32":
        return DRIVE_UNKNOWN
    try:
        import ctypes

        return int(ctypes.windll.kernel32.GetDriveTypeW(drive_root))
    except Exception:
        return DRIVE_UNKNOWN


def _validate_root(
    root_raw: str | Path,
    *,
    root_label: str,
    require_writable: bool,
    missing_code: str,
    invalid_code: str,
    request_id: str = "unknown",
) -> Path:
    """Consolidated root validator enforcing Windows local drive boundary, containment, and permissions."""

    def _reject(code: str, msg: str) -> None:
        raise BatchSafetyRejectionError(
            BatchDiagnostic(code=code, message=msg),
            request_id=request_id,
        )

    raw_str = str(root_raw)
    if not raw_str:
        _reject(invalid_code, f"{root_label} root path cannot be empty")
    if raw_str != raw_str.strip():
        _reject(invalid_code, f"{root_label} root cannot have leading or trailing whitespace")
    if any(ord(c) < 32 for c in raw_str):
        _reject(invalid_code, f"{root_label} root contains forbidden control characters")

    # Reject remote, UNC, device, or extended-length prefixes
    if raw_str.startswith(("\\\\", "//", "\\\\?\\", "\\\\.\\", "/")):
        _reject(invalid_code, f"{root_label} root cannot use UNC, network, device, or extended-length prefixes")

    # Production validation fails closed on unsupported platforms
    if sys.platform != "win32":
        _reject(invalid_code, "Batch operations are only supported on Windows workstations")

    # Windows drive-qualification requirement
    if not DRIVE_QUALIFIED_PATTERN.match(raw_str):
        _reject(invalid_code, f"{root_label} root must be an absolute drive-qualified path")

    # Classify the drive BEFORE any filesystem access or resolution probes
    drive_root = f"{raw_str[:2].upper()}\\"
    drive_type = get_windows_drive_type(drive_root)
    if drive_type == DRIVE_REMOTE:
        _reject(invalid_code, f"{root_label} root cannot be on a network or mapped drive")
    if drive_type not in ALLOWED_LOCAL_DRIVE_TYPES:
        _reject(invalid_code, f"{root_label} root must be on a local drive")

    # Reject stream colons beyond drive letter
    if raw_str.count(":") > 1:
        _reject(invalid_code, f"{root_label} root contains forbidden stream colons")

    # Reject consecutive separators from anchor onward
    if any(sep in raw_str[2:] for sep in ("\\\\", "//", "/\\", "\\/")):
        _reject(invalid_code, f"{root_label} root contains empty components or invalid separators")

    # Validate all path components after drive anchor BEFORE constructing Path or resolving
    after_anchor = raw_str[3:]
    clean_remainder = after_anchor.rstrip("\\/")
    if not clean_remainder:
        _reject(invalid_code, f"{root_label} root cannot be a filesystem drive root")

    for comp in re.split(r"[\\/]", clean_remainder):
        if not comp or comp in (".", ".."):
            _reject(invalid_code, f"{root_label} root contains forbidden traversal or empty segment")
        if comp.startswith(" ") or comp.endswith(" "):
            _reject(invalid_code, f"{root_label} root segment has leading or trailing whitespace")
        if comp.endswith("."):
            _reject(invalid_code, f"{root_label} root segment has trailing dots")
        if any(ord(c) < 32 for c in comp):
            _reject(invalid_code, f"{root_label} root contains forbidden control characters")
        if any(c in comp for c in WINDOWS_FORBIDDEN_CHARS) or ":" in comp:
            _reject(invalid_code, f"{root_label} root contains forbidden characters or stream colons")
        if is_reserved_device_name(comp):
            _reject(invalid_code, f"{root_label} root uses reserved Windows device name")

    path = Path(raw_str)
    try:
        resolved = path.resolve()
    except (OSError, ValueError, RuntimeError) as exc:
        raise BatchSafetyRejectionError(
            BatchDiagnostic(code=missing_code, message=f"Failed to resolve {root_label.lower()} root directory"),
            request_id=request_id,
        ) from exc

    # Reject drive/filesystem roots (e.g. C:\)
    if len(resolved.parts) <= 1:
        _reject(invalid_code, f"{root_label} root cannot be a filesystem drive root")

    if not resolved.exists():
        _reject(missing_code, f"{root_label} root directory does not exist")

    if not resolved.is_dir():
        _reject(invalid_code, f"{root_label} root must be a directory")

    if is_symlink_or_reparse_point(resolved) or is_symlink_or_reparse_point(path):
        _reject(invalid_code, f"{root_label} root cannot be a symbolic link or reparse point")

    mode = os.R_OK | os.W_OK if require_writable else os.R_OK
    action_str = "readable and writable" if require_writable else "readable"
    if not os.access(resolved, mode):
        _reject(invalid_code, f"{root_label} root directory is not {action_str}")

    return resolved


def validate_input_root(root_raw: str | Path, *, request_id: str = "unknown") -> Path:
    """Validate user-selected input root directory and return resolved absolute Path."""
    return _validate_root(
        root_raw,
        root_label="Input",
        require_writable=False,
        missing_code="INPUT_ROOT_NOT_FOUND",
        invalid_code="INPUT_PATH_NOT_ALLOWED",
        request_id=request_id,
    )


def validate_output_root(root_raw: str | Path, *, request_id: str = "unknown") -> Path:
    """Validate user-selected output root directory and return resolved absolute Path."""
    return _validate_root(
        root_raw,
        root_label="Output",
        require_writable=True,
        missing_code="OUTPUT_ROOT_UNAVAILABLE",
        invalid_code="OUTPUT_ROOT_UNAVAILABLE",
        request_id=request_id,
    )


def assert_strictly_contained(child: Path, root: Path, *, request_id: str = "unknown") -> Path:
    """Assert that child resolves strictly beneath root without escaping or matching root itself."""
    try:
        child_res = child.resolve()
        root_res = root.resolve()
    except (OSError, ValueError, RuntimeError) as exc:
        raise BatchSafetyRejectionError(
            BatchDiagnostic(code="INTERNAL_ERROR", message="Failed to resolve path containment"),
            request_id=request_id,
        ) from exc

    if child_res == root_res:
        raise BatchSafetyRejectionError(
            BatchDiagnostic(code="INTERNAL_ERROR", message="Target path cannot be identical to root directory"),
            request_id=request_id,
        )

    try:
        child_res.relative_to(root_res)
    except ValueError as exc:
        raise BatchSafetyRejectionError(
            BatchDiagnostic(code="INTERNAL_ERROR", message="Target path escapes designated root directory"),
            request_id=request_id,
        ) from exc

    return child_res


def walk_and_verify_components(root: Path, relative_posix: str, *, request_id: str = "unknown") -> Path:
    """Walk every segment of relative_posix below root, enforcing canonical POSIX relative syntax.

    Rejects absolute paths, backslashes, repeated slashes, trailing slashes, surrounding whitespace,
    control characters, and any symlink or reparse point components. Guarantees final containment.
    """

    def _reject(msg: str) -> None:
        raise BatchSafetyRejectionError(
            BatchDiagnostic(code="INPUT_PATH_NOT_ALLOWED", message=msg),
            request_id=request_id,
        )

    if not isinstance(relative_posix, str) or not relative_posix:
        _reject("Input relative path cannot be empty")
    if relative_posix != relative_posix.strip():
        _reject("Input path has leading or trailing whitespace")
    if any(ord(c) < 32 for c in relative_posix):
        _reject("Input path contains forbidden control characters")
    if relative_posix.startswith(("/", "\\")):
        _reject("Input path cannot be an absolute path")
    if "\\" in relative_posix:
        _reject("Input path must use canonical POSIX forward slashes")
    if "//" in relative_posix or relative_posix.endswith("/"):
        _reject("Input path contains empty components or trailing slash")
    if ":" in relative_posix:
        _reject("Input path contains stream colons")

    segments = relative_posix.split("/")
    current = root.resolve()

    for seg in segments:
        validate_relative_segment(seg, request_id=request_id)
        current = current / seg
        # Direct probe using fail-closed lstat reparse check; raises typed sanitized rejection on failure
        if is_symlink_or_reparse_point(current):
            _reject(f"Path component '{seg}' is a symbolic link or reparse point")

    return assert_strictly_contained(current, root, request_id=request_id)
