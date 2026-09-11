"""Worker-local Solid Edge export adapters, preview capture, and health probing.

Invariants:
    - Dedicated STA Seam: all COM calls execute strictly within an STA thread worker.
    - Identity-Preserving Export: uses `SaveCopyAs` for all required model formats (PAR, STEP, STL),
      preserving active in-memory document identity and file path without identity mutation.
    - Document-Owned Preview: captures thumbnails strictly through the document's own window/view
      (`raw_doc.Windows.Item(1).View.SaveAsImage(...)`).
    - Localized Preview Classification: distinguishes localized preview failures (non-fatal warning
      if post-failure document health probe succeeds) from fatal document loss or worker crash.
    - Sanitized Error Diagnostics (SEC-07): strips absolute workstation paths and COM pointers from
      all exception messages.
    - Input Preflight: validates target path existence, non-symlink status, and extension parity.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import Any

from drivers.solidedge.errors import describe_exception, normalize_com_error
from interfaces.exceptions import (
    CADDocumentError,
    CADExportError,
    CADRuntimeBusyError,
    CADRuntimeUnavailableError,
)
from interfaces.models import ArtifactFormat

# Supported required model export formats
SUPPORTED_MODEL_FORMATS: frozenset[str] = frozenset({"par", "step", "stl"})

# Format to canonical allowed file extensions mapping
FORMAT_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "par": (".par",),
    "step": (".step", ".stp"),
    "stl": (".stl",),
}

PREVIEW_EXTENSIONS: tuple[str, ...] = (".jpg", ".jpeg")
DEFAULT_PREVIEW_WIDTH: int = 800
DEFAULT_PREVIEW_HEIGHT: int = 600


def _is_symlink_or_reparse(path: Path) -> bool:
    """Check whether a path is a symbolic link or Windows reparse point without external imports."""
    try:
        lstat_result = path.lstat()
    except FileNotFoundError:
        return False
    except (OSError, ValueError) as _exc:
        # Fail closed on stat permission/read errors
        return True

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


def probe_document_health(raw_doc: Any, worker: Any) -> bool:
    """Perform a minimal, non-mutating COM probe to verify the document is seated and responsive."""
    if raw_doc is None or worker is None:
        return False

    try:
        doc_name = worker._invoke_com(lambda: getattr(raw_doc, "Name", None))
        return bool(doc_name and isinstance(doc_name, str))
    except Exception:
        return False


def _cleanup_known_translator_sidecar(output_path: Path) -> None:
    """Guarded cleanup of known Solid Edge translator sidecar (<stem>.log).

    Invariants:
    - Sidecar path is strictly output_path.with_suffix('.log').
    - If present, must be a regular non-reparse file strictly inside output_path.parent.
    - If present and cannot be removed safely, fails closed by raising CADExportError.
    """
    sidecar_path = output_path.with_suffix(".log")
    try:
        lstat_res = sidecar_path.lstat()
    except FileNotFoundError:
        return
    except (OSError, ValueError) as exc:
        raise CADExportError(
            f"Failed to inspect translator sidecar metadata for '{output_path.name}': {type(exc).__name__}",
            error_code="ARTIFACT_EXPORT_FAILED",
        ) from None

    if _is_symlink_or_reparse(sidecar_path):
        raise CADExportError(
            f"Translator sidecar for '{output_path.name}' must not be a symbolic link or reparse point",
            error_code="ARTIFACT_EXPORT_FAILED",
        )

    if not stat.S_ISREG(lstat_res.st_mode):
        raise CADExportError(
            f"Translator sidecar for '{output_path.name}' is not a regular file",
            error_code="ARTIFACT_EXPORT_FAILED",
        )

    try:
        sidecar_path.unlink()
    except OSError as exc:
        raise CADExportError(
            f"Failed to remove translator sidecar for '{output_path.name}': {type(exc).__name__}",
            error_code="ARTIFACT_EXPORT_FAILED",
        ) from None


def export_model_to_path(
    raw_doc: Any,
    worker: Any,
    format_id: ArtifactFormat | str,
    output_path: Path,
) -> None:
    """Export the active document geometry to a required model format (PAR, STEP, STL).

    Args:
        raw_doc: Active Solid Edge COM PartDocument object on the STA worker thread.
        worker: STAThreadWorker running the COM apartment.
        format_id: Target model format ("par", "step", or "stl").
        output_path: Destination file path in the validated staging directory.

    Raises:
        CADExportError: If format is unsupported, path is invalid, or export fails.
        CADDocumentError: If the document is unseated or unresponsive.
    """
    normalized_format = str(format_id).strip().lower().lstrip(".")
    if normalized_format not in SUPPORTED_MODEL_FORMATS:
        raise CADExportError(
            f"Unsupported export format: '{format_id}' (must be one of {sorted(SUPPORTED_MODEL_FORMATS)})",
            error_code="ARTIFACT_EXPORT_FAILED",
        )

    if not isinstance(output_path, Path):
        raise CADExportError(
            "Output path must be a Path instance",
            error_code="ARTIFACT_EXPORT_FAILED",
        )

    # Validate parent directory exists and is a directory
    parent_dir = output_path.parent
    if not parent_dir.exists() or not parent_dir.is_dir():
        raise CADExportError(
            f"Target directory for '{output_path.name}' does not exist",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        )

    # Validate target is not an existing symlink or reparse point
    if _is_symlink_or_reparse(output_path):
        raise CADExportError(
            f"Output target '{output_path.name}' must not be a symbolic link or reparse point",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        )

    # Validate file extension matches requested format
    expected_exts = FORMAT_EXTENSIONS[normalized_format]
    if output_path.suffix.lower() not in expected_exts:
        raise CADExportError(
            f"File extension '{output_path.suffix}' does not match format '{format_id}' (expected {expected_exts})",
            error_code="ARTIFACT_EXPORT_FAILED",
        )

    # Preflight document health before invoking COM export
    if not probe_document_health(raw_doc, worker):
        raise CADDocumentError(
            "Document is unresponsive or unseated prior to export",
            error_code="DOCUMENT_LOST",
        )

    target_fspath = os.fspath(output_path.resolve(strict=False))
    try:
        worker._invoke_com(lambda: raw_doc.SaveCopyAs(target_fspath))
    except CADDocumentError, CADExportError:
        raise
    except Exception as exc:
        if isinstance(exc, TimeoutError):
            raise exc

        # Preserve fatal COM categories (busy, rejected, server died, unavailable)
        normalized = normalize_com_error(exc)
        if isinstance(normalized, (CADRuntimeBusyError, CADRuntimeUnavailableError)):
            raise normalized from None

        sanitized_diag = describe_exception(exc)
        raise CADExportError(
            f"Failed to export {normalized_format.upper()} artifact '{output_path.name}': {sanitized_diag}",
            error_code="ARTIFACT_EXPORT_FAILED",
        ) from None

    # Solid Edge STEP and STL translators generate an auxiliary translation log (<stem>.log)
    # in the destination directory. Clean up the auxiliary file with guarded fail-closed helper.
    if normalized_format in {"step", "stl"}:
        _cleanup_known_translator_sidecar(output_path)


def capture_preview_image(
    raw_doc: Any,
    worker: Any,
    output_path: Path,
    width: int = DEFAULT_PREVIEW_WIDTH,
    height: int = DEFAULT_PREVIEW_HEIGHT,
) -> None:
    """Capture a best-effort preview snapshot image (JPG) of the active document.

    Args:
        raw_doc: Active Solid Edge COM PartDocument object on the STA worker thread.
        worker: STAThreadWorker running the COM apartment.
        output_path: Destination file path in the validated staging directory.
        width: Image width in pixels (default 800).
        height: Image height in pixels (default 600).

    Raises:
        CADExportError: If preview capture fails while the underlying document remains healthy.
        CADDocumentError: If document loss or fatal runtime failure occurs.
    """
    if not isinstance(output_path, Path):
        raise CADExportError(
            "Output path must be a Path instance",
            error_code="PREVIEW_EXPORT_FAILED",
        )

    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        raise CADExportError(
            f"Preview dimensions must be positive integers (got width={width}, height={height})",
            error_code="PREVIEW_EXPORT_FAILED",
        )

    parent_dir = output_path.parent
    if not parent_dir.exists() or not parent_dir.is_dir():
        raise CADExportError(
            f"Target directory for '{output_path.name}' does not exist",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        )

    if _is_symlink_or_reparse(output_path):
        raise CADExportError(
            f"Output target '{output_path.name}' must not be a symbolic link or reparse point",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        )

    if output_path.suffix.lower() not in PREVIEW_EXTENSIONS:
        raise CADExportError(
            f"Preview file extension '{output_path.suffix}' must be one of {PREVIEW_EXTENSIONS}",
            error_code="PREVIEW_EXPORT_FAILED",
        )

    target_fspath = os.fspath(output_path.resolve(strict=False))

    try:
        # Resolve window collection strictly from the request document's own Windows collection
        windows = worker._invoke_com(lambda: getattr(raw_doc, "Windows", None))
        win_count = worker._invoke_com(lambda: getattr(windows, "Count", 0)) if windows is not None else 0

        if win_count <= 0:
            # Check document health before raising localized failure
            if not probe_document_health(raw_doc, worker):
                raise CADDocumentError(
                    "Document unseated or lost during preview window resolution",
                    error_code="DOCUMENT_LOST",
                )
            raise CADExportError(
                "No active document window available for preview capture",
                error_code="PREVIEW_EXPORT_FAILED",
            )

        target_win = worker._invoke_com(lambda: windows.Item(1))
        view = worker._invoke_com(lambda: getattr(target_win, "View", None)) if target_win is not None else None

        if view is None:
            if not probe_document_health(raw_doc, worker):
                raise CADDocumentError(
                    "Document unseated or lost during preview view resolution",
                    error_code="DOCUMENT_LOST",
                )
            raise CADExportError(
                "Document window has no 3D View object for preview capture",
                error_code="PREVIEW_EXPORT_FAILED",
            )

        worker._invoke_com(lambda: view.SaveAsImage(target_fspath, width, height))

    except (CADDocumentError, CADExportError) as _flow_exc:
        raise
    except Exception as exc:
        if isinstance(exc, TimeoutError):
            raise exc

        # Preserve fatal COM categories (busy, rejected, server died, unavailable)
        normalized = normalize_com_error(exc)
        if isinstance(normalized, (CADRuntimeBusyError, CADRuntimeUnavailableError)):
            raise normalized from None

        # Post-failure health probe to distinguish localized failure from fatal document loss
        if not probe_document_health(raw_doc, worker):
            sanitized_diag = describe_exception(exc)
            raise CADDocumentError(
                f"Fatal document loss during preview capture: {sanitized_diag}",
                error_code="DOCUMENT_LOST",
            ) from None

        sanitized_diag = describe_exception(exc)
        raise CADExportError(
            f"Preview capture failed for '{output_path.name}': {sanitized_diag}",
            error_code="PREVIEW_EXPORT_FAILED",
        ) from None


__all__ = [
    "DEFAULT_PREVIEW_HEIGHT",
    "DEFAULT_PREVIEW_WIDTH",
    "FORMAT_EXTENSIONS",
    "PREVIEW_EXTENSIONS",
    "SUPPORTED_MODEL_FORMATS",
    "capture_preview_image",
    "export_model_to_path",
    "probe_document_health",
]
