"""Safe path resolution, request ID hardening, staging allocation, and guarded containment.

Invariants:
    - Zero Unvalidated Identifiers: request_id strictly validated against canonical schema (1-96 chars, ^[A-Za-z0-9._-]+$).
    - Windows Device Name Immunity: strict rejection of CON, PRN, AUX, NUL, COM1-9, LPT1-9 basenames and extensions.
    - Output Root Containment (CWE-22 / CWE-59): all paths resolve strictly beneath the resolved output root.
    - No Caller-Supplied Filenames: all target filenames are generated internally using canonical patterns.
    - Preflight Collision Checking: reject existing final directories or files before any export is attempted (TARGET_ALREADY_EXISTS).
    - Exclusive Staging Isolation: unique .staging-{request_id}-{uuid} directory with exclusive allocation (exist_ok=False).
    - Mandatory Inode/Device Identity Binding: bind operations to recorded filesystem identities, preventing substitution attacks.
    - Atomic Directory Publication: single directory rename after confirmed document release and validation.
    - Guarded Fails-Closed Cleanup: refuse deletion of root, final directory, unclosed documents, symlinks, or external paths.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import sys
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from interfaces.exceptions import CADError
from interfaces.models import ArtifactFormat

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REQUEST_ID_PATTERN: str = r"^[A-Za-z0-9._-]+$"
REQUEST_ID_REGEX: re.Pattern[str] = re.compile(REQUEST_ID_PATTERN)
MIN_REQUEST_ID_LENGTH: int = 1
MAX_REQUEST_ID_LENGTH: int = 96
STAGING_PREFIX: str = ".staging-"
MAX_STAGING_ALLOCATION_ATTEMPTS: int = 5

WINDOWS_RESERVED_NAMES: frozenset[str] = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ArtifactPathError(CADError):
    """Raised when an artifact path or output directory violates containment, security rules, or collision policies."""

    error_code: str = "OUTPUT_PATH_NOT_ALLOWED"


# ---------------------------------------------------------------------------
# Validation Helpers
# ---------------------------------------------------------------------------


def get_path_identity(path: Path) -> tuple[int, int]:
    """Retrieve the filesystem device and inode/file-index identity tuple."""
    try:
        stat_result = path.stat()
        return (stat_result.st_dev, stat_result.st_ino)
    except OSError as exc:
        raise ArtifactPathError(
            f"Cannot inspect identity for path '{path.name}': {type(exc).__name__}",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        ) from exc


def is_symlink_or_reparse_point(path: Path) -> bool:
    """Check whether a path is a symbolic link or Windows reparse point/junction.

    Fails closed by raising ArtifactPathError if file status cannot be determined
    due to permission or I/O errors, distinguishing expected FileNotFoundError.
    """
    try:
        lstat_result = path.lstat()
    except FileNotFoundError:
        return False
    except (OSError, ValueError) as exc:
        raise ArtifactPathError(
            f"Cannot verify path metadata for '{path.name}': {type(exc).__name__}",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        ) from exc

    if stat.S_ISLNK(lstat_result.st_mode):
        return True

    if sys.platform == "win32":
        reparse_attr = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        file_attrs = getattr(lstat_result, "st_file_attributes", 0)
        if (file_attrs & reparse_attr) != 0:
            return True
        if getattr(lstat_result, "st_reparse_tag", 0) != 0:
            return True

    return False


def validate_request_id(request_id: str) -> str:
    """Validate request ID against canonical schema, Windows device names, and path traversal tokens."""
    if not isinstance(request_id, str):
        raise ArtifactPathError(
            "Request ID must be a string",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        )

    if len(request_id) < MIN_REQUEST_ID_LENGTH or len(request_id) > MAX_REQUEST_ID_LENGTH:
        raise ArtifactPathError(
            f"Request ID length ({len(request_id)}) must be between {MIN_REQUEST_ID_LENGTH} and {MAX_REQUEST_ID_LENGTH} characters",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        )

    if request_id in (".", ".."):
        raise ArtifactPathError(
            "Request ID is a reserved path traversal token",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        )

    if request_id.endswith("."):
        raise ArtifactPathError(
            "Request ID must not end with a dot",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        )

    if (
        request_id.endswith(" ")
        or request_id.startswith(" ")
        or " " in request_id
        or "\n" in request_id
        or "\r" in request_id
        or "\t" in request_id
    ):
        raise ArtifactPathError(
            "Request ID must not contain whitespace or control characters",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        )

    if not REQUEST_ID_REGEX.fullmatch(request_id):
        raise ArtifactPathError(
            "Request ID contains invalid characters (must match canonical pattern)",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        )

    # Check for Windows reserved device basenames (e.g. CON, NUL, COM1, etc. with or without extension)
    stem = request_id.split(".")[0].upper()
    if stem in WINDOWS_RESERVED_NAMES or (sys.platform == "win32" and os.path.isreserved(request_id)):
        raise ArtifactPathError(
            "Request ID uses reserved Windows device name",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        )

    return request_id


def assert_contained(child: Path, parent: Path) -> None:
    """Assert that child resolves strictly beneath parent without escaping or matching parent."""
    try:
        child_res = child.resolve(strict=False)
        parent_res = parent.resolve(strict=False)
    except (OSError, ValueError, RuntimeError) as exc:
        raise ArtifactPathError(
            f"Failed to resolve path containment: {type(exc).__name__}",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        ) from exc

    if child_res == parent_res:
        raise ArtifactPathError(
            f"Child path '{child.name}' cannot be identical to parent '{parent.name}'",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        )

    if not child_res.is_relative_to(parent_res):
        raise ArtifactPathError(
            f"Path '{child.name}' escapes output root '{parent.name}'",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        )

    # Verify neither path is an unexpected symlink or reparse point
    if is_symlink_or_reparse_point(child):
        raise ArtifactPathError(
            f"Path '{child.name}' must not be a symbolic link or reparse point",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        )

    if is_symlink_or_reparse_point(parent):
        raise ArtifactPathError(
            f"Parent path '{parent.name}' must not be a symbolic link or reparse point",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        )


def resolve_output_root(
    output_root: Path | str,
    *,
    create_if_missing: bool = False,
) -> Path:
    """Resolve and verify the output root directory, rejecting links, roots, and non-directories."""
    if isinstance(output_root, str):
        if not output_root.strip():
            raise ArtifactPathError(
                "Output root cannot be empty",
                error_code="OUTPUT_PATH_NOT_ALLOWED",
            )
        if "\x00" in output_root:
            raise ArtifactPathError(
                "Output root contains invalid null byte",
                error_code="OUTPUT_PATH_NOT_ALLOWED",
            )
        try:
            root_path = Path(output_root)
        except (OSError, ValueError) as exc:
            raise ArtifactPathError(
                f"Invalid output root path: {type(exc).__name__}",
                error_code="OUTPUT_PATH_NOT_ALLOWED",
            ) from exc
    else:
        root_path = output_root

    if "\x00" in str(root_path):
        raise ArtifactPathError(
            "Output root contains invalid null byte",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        )

    try:
        expanded = root_path.expanduser()
    except (OSError, ValueError, RuntimeError) as exc:
        raise ArtifactPathError(
            f"Failed to expand output root path: {type(exc).__name__}",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        ) from exc

    # Check pre-resolution link status
    if is_symlink_or_reparse_point(expanded):
        raise ArtifactPathError(
            f"Output root '{expanded.name}' must not be a symbolic link or reparse point",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        )

    try:
        resolved = expanded.resolve(strict=False)
    except (OSError, ValueError, RuntimeError) as exc:
        raise ArtifactPathError(
            f"Failed to resolve output root: {type(exc).__name__}",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        ) from exc

    if resolved == resolved.parent:
        raise ArtifactPathError(
            "Output root cannot be filesystem or drive root",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        )

    if resolved.exists():
        if is_symlink_or_reparse_point(resolved):
            raise ArtifactPathError(
                f"Resolved output root '{resolved.name}' must not be a symbolic link or reparse point",
                error_code="OUTPUT_PATH_NOT_ALLOWED",
            )
        if not resolved.is_dir():
            raise ArtifactPathError(
                f"Output root '{resolved.name}' exists and is not a directory",
                error_code="OUTPUT_PATH_NOT_ALLOWED",
            )
    else:
        # Verify existing parent chain resolves safely
        curr = resolved.parent
        while not curr.exists() and curr != curr.parent:
            curr = curr.parent

        if curr.exists() and (is_symlink_or_reparse_point(curr) or not curr.is_dir()):
            raise ArtifactPathError(
                f"Output root ancestor '{curr.name}' is not a safe directory",
                error_code="OUTPUT_PATH_NOT_ALLOWED",
            )

        if create_if_missing:
            try:
                resolved.mkdir(parents=True, exist_ok=True)
            except (OSError, ValueError) as exc:
                raise ArtifactPathError(
                    f"Failed to create output root '{resolved.name}': {type(exc).__name__}",
                    error_code="OUTPUT_PATH_NOT_ALLOWED",
                ) from exc

    return resolved


def check_target_collisions(
    final_dir: Path,
    candidate_targets: Sequence[Path] | None = None,
) -> None:
    """Check if final target directory or any candidate artifact target already exists."""
    if final_dir.exists() or is_symlink_or_reparse_point(final_dir):
        raise ArtifactPathError(
            f"Target directory already exists: '{final_dir.name}'",
            error_code="TARGET_ALREADY_EXISTS",
            details={"target": final_dir.name},
        )

    if candidate_targets is not None:
        for target in candidate_targets:
            if target.exists() or is_symlink_or_reparse_point(target):
                raise ArtifactPathError(
                    f"Target artifact already exists: '{target.name}'",
                    error_code="TARGET_ALREADY_EXISTS",
                    details={"target": target.name},
                )


# ---------------------------------------------------------------------------
# Staging & Publication
# ---------------------------------------------------------------------------


def allocate_staging_dir(
    output_root: Path,
    request_id: str,
    *,
    max_attempts: int = MAX_STAGING_ALLOCATION_ATTEMPTS,
) -> Path:
    """Exclusively allocate a private staging directory as a sibling beneath output_root."""
    valid_req_id = validate_request_id(request_id)
    root = resolve_output_root(output_root, create_if_missing=True)

    for _ in range(max_attempts):
        token = uuid.uuid4().hex[:12]
        staging_dir = root / f"{STAGING_PREFIX}{valid_req_id}-{token}"

        assert_contained(staging_dir, root)

        try:
            os.mkdir(staging_dir)
            return staging_dir
        except FileExistsError:
            continue
        except OSError as exc:
            raise ArtifactPathError(
                f"Failed to create staging directory '{staging_dir.name}': {type(exc).__name__}",
                error_code="OUTPUT_PATH_NOT_ALLOWED",
            ) from exc

    raise ArtifactPathError(
        f"Failed to allocate exclusive staging directory after {max_attempts} attempts",
        error_code="OUTPUT_PATH_NOT_ALLOWED",
    )


def publish_staging_dir(
    staging_dir: Path,
    final_dir: Path,
    output_root: Path,
    *,
    expected_staging_identity: tuple[int, int],
    expected_root_identity: tuple[int, int],
) -> None:
    """Atomically publish the validated staging directory to final directory via rename."""
    assert_contained(staging_dir, output_root)
    assert_contained(final_dir, output_root)

    if not staging_dir.exists() or not staging_dir.is_dir():
        raise ArtifactPathError(
            f"Staging directory '{staging_dir.name}' does not exist or is not a directory",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        )

    if is_symlink_or_reparse_point(staging_dir):
        raise ArtifactPathError(
            f"Staging directory '{staging_dir.name}' must not be a symbolic link or reparse point",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        )

    if is_symlink_or_reparse_point(output_root):
        raise ArtifactPathError(
            f"Output root '{output_root.name}' must not be a symbolic link or reparse point",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        )

    # Recheck mandatory filesystem identities
    current_staging_id = get_path_identity(staging_dir)
    if current_staging_id != expected_staging_identity:
        raise ArtifactPathError(
            f"Staging directory '{staging_dir.name}' identity mismatch (directory substituted)",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        )

    current_root_id = get_path_identity(output_root)
    if current_root_id != expected_root_identity:
        raise ArtifactPathError(
            f"Output root '{output_root.name}' identity mismatch (root substituted)",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        )

    if final_dir.exists():
        raise ArtifactPathError(
            f"Target directory already exists: '{final_dir.name}'",
            error_code="TARGET_ALREADY_EXISTS",
            details={"target": final_dir.name},
        )

    try:
        staging_dir.rename(final_dir)
    except FileExistsError as exc:
        raise ArtifactPathError(
            f"Target directory already exists: '{final_dir.name}'",
            error_code="TARGET_ALREADY_EXISTS",
            details={"target": final_dir.name},
        ) from exc
    except OSError as exc:
        if getattr(exc, "winerror", None) == 183 or final_dir.exists():
            raise ArtifactPathError(
                f"Target directory already exists: '{final_dir.name}'",
                error_code="TARGET_ALREADY_EXISTS",
                details={"target": final_dir.name},
            ) from exc
        # Non-collision publication failure is classified as ARTIFACT_EXPORT_FAILED
        raise ArtifactPathError(
            f"Failed to publish staging directory '{staging_dir.name}' -> '{final_dir.name}': {type(exc).__name__}",
            error_code="ARTIFACT_EXPORT_FAILED",
        ) from exc


# ---------------------------------------------------------------------------
# Guarded Cleanup Routines
# ---------------------------------------------------------------------------


def cleanup_staging_dir(
    staging_dir: Path,
    output_root: Path,
    request_id: str | None = None,
    *,
    document_closed: bool = False,
    expected_staging_identity: tuple[int, int],
    expected_root_identity: tuple[int, int],
) -> None:
    """Safely clean up a staging directory, strictly failing closed if document closure unconfirmed."""
    if not document_closed:
        raise ArtifactPathError(
            "Recursive staging cleanup refused: request document closure has not been confirmed",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        )

    # Verify link/reparse points early before path resolution
    if is_symlink_or_reparse_point(output_root):
        raise ArtifactPathError(
            f"Cleanup refused: output root '{output_root.name}' is a symbolic link or reparse point",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        )

    if is_symlink_or_reparse_point(staging_dir):
        raise ArtifactPathError(
            f"Cleanup refused: staging directory '{staging_dir.name}' is a symbolic link or reparse point",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        )

    # Recheck mandatory filesystem identities
    if output_root.exists():
        current_root_id = get_path_identity(output_root)
        if current_root_id != expected_root_identity:
            raise ArtifactPathError(
                f"Cleanup refused: output root '{output_root.name}' identity mismatch (root substituted)",
                error_code="OUTPUT_PATH_NOT_ALLOWED",
            )

    if staging_dir.exists():
        current_staging_id = get_path_identity(staging_dir)
        if current_staging_id != expected_staging_identity:
            raise ArtifactPathError(
                f"Cleanup refused: staging directory '{staging_dir.name}' identity mismatch (directory substituted)",
                error_code="OUTPUT_PATH_NOT_ALLOWED",
            )

    try:
        staging_res = staging_dir.resolve(strict=False)
        root_res = output_root.resolve(strict=False)
    except (OSError, ValueError, RuntimeError) as exc:
        raise ArtifactPathError(
            f"Failed to resolve paths for staging cleanup: {type(exc).__name__}",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        ) from exc

    # Strictly refuse root or outside output_root
    if staging_res == root_res or not staging_res.is_relative_to(root_res):
        raise ArtifactPathError(
            f"Cleanup refused: staging path '{staging_dir.name}' is not strictly beneath output root",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        )

    # Strictly refuse filesystem root
    if staging_res == staging_res.parent:
        raise ArtifactPathError(
            f"Cleanup refused: staging path '{staging_dir.name}' cannot be filesystem root",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        )

    # Must match staging prefix pattern
    if request_id is not None:
        expected_prefix = f"{STAGING_PREFIX}{request_id}-"
        if not staging_dir.name.startswith(expected_prefix):
            raise ArtifactPathError(
                f"Cleanup refused: directory '{staging_dir.name}' does not match expected staging prefix '{expected_prefix}'",
                error_code="OUTPUT_PATH_NOT_ALLOWED",
            )
        final_dir_res = (root_res / request_id).resolve(strict=False)
        if staging_res == final_dir_res:
            raise ArtifactPathError(
                f"Cleanup refused: target is final directory '{final_dir_res.name}'",
                error_code="OUTPUT_PATH_NOT_ALLOWED",
            )
    else:
        if not staging_dir.name.startswith(STAGING_PREFIX):
            raise ArtifactPathError(
                f"Cleanup refused: directory '{staging_dir.name}' does not match staging prefix '{STAGING_PREFIX}'",
                error_code="OUTPUT_PATH_NOT_ALLOWED",
            )

    if not staging_dir.exists():
        return

    if not staging_dir.is_dir():
        raise ArtifactPathError(
            f"Cleanup refused: staging path '{staging_dir.name}' is not a directory",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        )

    try:
        shutil.rmtree(staging_dir)
    except OSError as exc:
        raise ArtifactPathError(
            f"Failed to remove staging directory '{staging_dir.name}': {type(exc).__name__}",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        ) from exc


def cleanup_staging_file(
    file_path: Path,
    staging_dir: Path,
    output_root: Path,
    *,
    document_closed: bool = False,
    expected_staging_identity: tuple[int, int],
    expected_root_identity: tuple[int, int],
) -> None:
    """Safely remove a specific staging file (e.g. rejected partial preview) after document closure."""
    if not document_closed:
        raise ArtifactPathError(
            "Staging file cleanup refused: request document closure has not been confirmed",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        )

    if is_symlink_or_reparse_point(output_root):
        raise ArtifactPathError(
            f"Cleanup refused: output root '{output_root.name}' is a symbolic link or reparse point",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        )

    if is_symlink_or_reparse_point(staging_dir):
        raise ArtifactPathError(
            f"Cleanup refused: staging directory '{staging_dir.name}' is a symbolic link or reparse point",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        )

    if output_root.exists():
        current_root_id = get_path_identity(output_root)
        if current_root_id != expected_root_identity:
            raise ArtifactPathError(
                f"Cleanup refused: output root '{output_root.name}' identity mismatch (root substituted)",
                error_code="OUTPUT_PATH_NOT_ALLOWED",
            )

    if staging_dir.exists():
        current_staging_id = get_path_identity(staging_dir)
        if current_staging_id != expected_staging_identity:
            raise ArtifactPathError(
                f"Cleanup refused: staging directory '{staging_dir.name}' identity mismatch (directory substituted)",
                error_code="OUTPUT_PATH_NOT_ALLOWED",
            )

    try:
        file_res = file_path.resolve(strict=False)
        staging_res = staging_dir.resolve(strict=False)
        root_res = output_root.resolve(strict=False)
    except (OSError, ValueError, RuntimeError) as exc:
        raise ArtifactPathError(
            f"Failed to resolve file path for cleanup: {type(exc).__name__}",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        ) from exc

    if staging_res == root_res or not staging_res.is_relative_to(root_res):
        raise ArtifactPathError(
            f"Staging directory '{staging_dir.name}' is not strictly beneath output root '{output_root.name}'",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        )

    if file_res == staging_res or not file_res.is_relative_to(staging_res):
        raise ArtifactPathError(
            f"File '{file_path.name}' is not contained within staging directory '{staging_dir.name}'",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        )

    if is_symlink_or_reparse_point(file_path):
        raise ArtifactPathError(
            f"File '{file_path.name}' must not be a symbolic link or reparse point",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        )

    if not file_path.exists():
        return

    if not file_path.is_file():
        raise ArtifactPathError(
            f"Target '{file_path.name}' is not a regular file",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        )

    try:
        file_path.unlink()
    except OSError as exc:
        raise ArtifactPathError(
            f"Failed to remove staging file '{file_path.name}': {type(exc).__name__}",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        ) from exc


# ---------------------------------------------------------------------------
# Canonical Paths Container & Factory
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArtifactPaths:
    """Canonical artifact paths container for an export request."""

    request_id: str
    output_root: Path
    final_dir: Path
    staging_dir: Path
    staging_par: Path
    staging_step: Path
    staging_stl: Path
    staging_jpg: Path
    final_par: Path
    final_step: Path
    final_stl: Path
    final_jpg: Path
    root_identity: tuple[int, int]
    staging_identity: tuple[int, int]

    def assert_staging_active(self) -> None:
        """Verify staging directory and output root ownership, link safety, and identity."""
        if is_symlink_or_reparse_point(self.output_root):
            raise ArtifactPathError(
                f"Output root '{self.output_root.name}' must not be a symbolic link or reparse point",
                error_code="OUTPUT_PATH_NOT_ALLOWED",
            )
        if is_symlink_or_reparse_point(self.staging_dir):
            raise ArtifactPathError(
                f"Staging directory '{self.staging_dir.name}' must not be a symbolic link or reparse point",
                error_code="OUTPUT_PATH_NOT_ALLOWED",
            )
        if get_path_identity(self.output_root) != self.root_identity:
            raise ArtifactPathError(
                f"Output root '{self.output_root.name}' identity mismatch (root substituted)",
                error_code="OUTPUT_PATH_NOT_ALLOWED",
            )
        if get_path_identity(self.staging_dir) != self.staging_identity:
            raise ArtifactPathError(
                f"Staging directory '{self.staging_dir.name}' identity mismatch (directory substituted)",
                error_code="OUTPUT_PATH_NOT_ALLOWED",
            )
        assert_contained(self.staging_dir, self.output_root)

    def get_staging_path(self, format_id: ArtifactFormat | str) -> Path:
        """Get the verified staging path for a given format, rechecking active ownership and target containment."""
        self.assert_staging_active()
        normalized = format_id.lower().strip().lstrip(".")
        if normalized == "par":
            target = self.staging_par
        elif normalized == "step":
            target = self.staging_step
        elif normalized == "stl":
            target = self.staging_stl
        elif normalized == "jpg":
            target = self.staging_jpg
        else:
            raise ArtifactPathError(
                f"Unsupported artifact format: '{format_id}'",
                error_code="OUTPUT_PATH_NOT_ALLOWED",
            )

        if is_symlink_or_reparse_point(target):
            raise ArtifactPathError(
                f"Staging target '{target.name}' must not be a symbolic link or reparse point",
                error_code="OUTPUT_PATH_NOT_ALLOWED",
            )
        assert_contained(target, self.staging_dir)
        return target

    def get_final_path(self, format_id: ArtifactFormat | str) -> Path:
        """Get the final path for a given format."""
        normalized = format_id.lower().strip().lstrip(".")
        if normalized == "par":
            target = self.final_par
        elif normalized == "step":
            target = self.final_step
        elif normalized == "stl":
            target = self.final_stl
        elif normalized == "jpg":
            target = self.final_jpg
        else:
            raise ArtifactPathError(
                f"Unsupported artifact format: '{format_id}'",
                error_code="OUTPUT_PATH_NOT_ALLOWED",
            )

        if is_symlink_or_reparse_point(target):
            raise ArtifactPathError(
                f"Final target '{target.name}' must not be a symbolic link or reparse point",
                error_code="OUTPUT_PATH_NOT_ALLOWED",
            )
        assert_contained(target, self.output_root)
        return target

    def check_collisions(self) -> None:
        """Check if the final directory or any final artifact files already exist."""
        check_target_collisions(
            self.final_dir,
            [self.final_par, self.final_step, self.final_stl, self.final_jpg],
        )

    def publish(self) -> None:
        """Publish staging directory to final directory via atomic rename."""
        publish_staging_dir(
            self.staging_dir,
            self.final_dir,
            self.output_root,
            expected_staging_identity=self.staging_identity,
            expected_root_identity=self.root_identity,
        )

    def cleanup_staging(self, *, document_closed: bool = False) -> None:
        """Clean up staging directory if document closure is confirmed."""
        cleanup_staging_dir(
            self.staging_dir,
            self.output_root,
            self.request_id,
            document_closed=document_closed,
            expected_staging_identity=self.staging_identity,
            expected_root_identity=self.root_identity,
        )

    def cleanup_partial_preview(self, *, document_closed: bool = False) -> None:
        """Clean up partial preview JPG if document closure is confirmed."""
        cleanup_staging_file(
            self.staging_jpg,
            self.staging_dir,
            self.output_root,
            document_closed=document_closed,
            expected_staging_identity=self.staging_identity,
            expected_root_identity=self.root_identity,
        )


def prepare_artifact_paths(
    output_root: Path | str,
    request_id: str,
) -> ArtifactPaths:
    """Validate request ID and output root, preflight collisions, and allocate staging directory."""
    valid_req_id = validate_request_id(request_id)
    resolved_root = resolve_output_root(output_root, create_if_missing=True)

    final_dir = resolved_root / valid_req_id
    final_par = final_dir / f"{valid_req_id}.par"
    final_step = final_dir / f"{valid_req_id}.step"
    final_stl = final_dir / f"{valid_req_id}.stl"
    final_jpg = final_dir / f"{valid_req_id}.jpg"

    # Containment verification on all final paths
    for p in (final_dir, final_par, final_step, final_stl, final_jpg):
        assert_contained(p, resolved_root)

    # Check preflight collisions before allocating staging
    check_target_collisions(final_dir, [final_par, final_step, final_stl, final_jpg])

    # Record root identity
    root_identity = get_path_identity(resolved_root)

    # Exclusively allocate staging directory
    staging_dir = allocate_staging_dir(resolved_root, valid_req_id)
    try:
        staging_identity = get_path_identity(staging_dir)

        staging_par = staging_dir / f"{valid_req_id}.par"
        staging_step = staging_dir / f"{valid_req_id}.step"
        staging_stl = staging_dir / f"{valid_req_id}.stl"
        staging_jpg = staging_dir / f"{valid_req_id}.jpg"

        # Containment verification on all staging paths
        for p in (staging_dir, staging_par, staging_step, staging_stl, staging_jpg):
            assert_contained(p, resolved_root)
    except BaseException as alloc_exc:
        cleanup_error: str | None = None
        retained: bool = False
        try:
            if is_symlink_or_reparse_point(staging_dir):
                retained = True
                cleanup_error = "Refused post-allocation removal of substituted link or reparse point"
            elif staging_dir.exists():
                if not staging_dir.is_dir():
                    retained = True
                    cleanup_error = "Refused post-allocation removal of non-directory staging residue"
                else:
                    os.rmdir(staging_dir)
        except OSError as rmdir_exc:
            retained = True
            cleanup_error = f"Safe post-allocation rmdir failed: {type(rmdir_exc).__name__}"
        except BaseException as rmdir_exc:
            retained = True
            cleanup_error = f"Safe post-allocation rmdir failed: {type(rmdir_exc).__name__}"

        if isinstance(alloc_exc, CADError):
            if retained:
                alloc_exc.details["retained_staging"] = True
            if cleanup_error is not None:
                alloc_exc.details["cleanup_error"] = cleanup_error
        raise alloc_exc

    return ArtifactPaths(
        request_id=valid_req_id,
        output_root=resolved_root,
        final_dir=final_dir,
        staging_dir=staging_dir,
        staging_par=staging_par,
        staging_step=staging_step,
        staging_stl=staging_stl,
        staging_jpg=staging_jpg,
        final_par=final_par,
        final_step=final_step,
        final_stl=final_stl,
        final_jpg=final_jpg,
        root_identity=root_identity,
        staging_identity=staging_identity,
    )


__all__ = [
    "MAX_REQUEST_ID_LENGTH",
    "MAX_STAGING_ALLOCATION_ATTEMPTS",
    "MIN_REQUEST_ID_LENGTH",
    "REQUEST_ID_PATTERN",
    "REQUEST_ID_REGEX",
    "STAGING_PREFIX",
    "WINDOWS_RESERVED_NAMES",
    "ArtifactPathError",
    "ArtifactPaths",
    "allocate_staging_dir",
    "assert_contained",
    "check_target_collisions",
    "cleanup_staging_dir",
    "cleanup_staging_file",
    "get_path_identity",
    "is_symlink_or_reparse_point",
    "prepare_artifact_paths",
    "publish_staging_dir",
    "resolve_output_root",
    "validate_request_id",
]
