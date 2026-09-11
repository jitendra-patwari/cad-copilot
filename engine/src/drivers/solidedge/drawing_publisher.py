"""Worker-local Solid Edge drawing view refresh and PDF/DXF publication adapter.

Invariants:
    - Dedicated STA Seam: all COM calls execute strictly within an STA thread worker.
    - Tracked-Document Scoped: operates exclusively on the passed raw draft document,
      never accessing Application.ActiveDocument.
    - Source Immutability: refreshes views in-memory using DrawingView.Update();
      never invokes Document.Save() or mutates source document path/properties.
    - Publication via SaveCopyAs: publishes directly to private work paths without
      identity mutation.
    - Sidecar-Free Publication: PDF/DXF publication performs no sidecar cleanup;
      unexpected files remain for workspace inventory validation to fail closed.
    - Sanitized Error Diagnostics (SEC-07): strips absolute workstation paths and
      COM pointers from all diagnostic messages.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Final

from drivers.solidedge.errors import describe_exception, normalize_com_error
from drivers.solidedge.exporter import (
    _is_symlink_or_reparse,
    probe_document_health,
)
from interfaces.exceptions import (
    CADDocumentError,
    CADExportError,
    CADRuntimeBusyError,
    CADRuntimeUnavailableError,
)

SUPPORTED_DRAWING_FORMATS: Final[frozenset[str]] = frozenset({"pdf", "dxf"})

DRAWING_FORMAT_EXTENSIONS: Final[dict[str, str]] = {
    "pdf": ".pdf",
    "dxf": ".dxf",
}


def refresh_drawing_views(raw_doc: Any, worker: Any) -> int:
    """Refresh all drawing views across all working sheets in memory once.

    Traverses raw_doc.Sections.WorkingSection.Sheets using 1-based indexing
    and invokes Update() on each DrawingView.

    Args:
        raw_doc: Tracked Solid Edge COM DraftDocument on the STA worker thread.
        worker: STAThreadWorker running the COM apartment.

    Returns:
        The total number of drawing views updated.

    Raises:
        CADDocumentError: If document is unseated or unresponsive.
        CADExportError: If view traversal or update fails.
        CADRuntimeBusyError: If Solid Edge server is busy or call is rejected.
        CADRuntimeUnavailableError: If Solid Edge process died or disconnected.
    """
    if not probe_document_health(raw_doc, worker):
        raise CADDocumentError(
            "Draft document is unresponsive or unseated prior to view refresh",
            error_code="DOCUMENT_LOST",
        )

    def _refresh() -> int:
        views_updated = 0
        try:
            sections = getattr(raw_doc, "Sections", None)
            if sections is None:
                raise CADExportError(
                    "Draft document missing required Sections collection",
                    error_code="ARTIFACT_EXPORT_FAILED",
                )

            working_section = getattr(sections, "WorkingSection", None)
            if working_section is None:
                raise CADExportError(
                    "Draft document missing required WorkingSection",
                    error_code="ARTIFACT_EXPORT_FAILED",
                )

            sheets = getattr(working_section, "Sheets", None)
            if sheets is None:
                return 0

            sheet_count = int(getattr(sheets, "Count", 0))
            for s_idx in range(1, sheet_count + 1):
                sheet = sheets.Item(s_idx)
                dviews = getattr(sheet, "DrawingViews", None)
                if dviews is None:
                    continue

                dview_count = int(getattr(dviews, "Count", 0))
                for v_idx in range(1, dview_count + 1):
                    dview = dviews.Item(v_idx)
                    dview.Update()
                    views_updated += 1

            return views_updated

        except CADDocumentError, CADExportError:
            raise
        except Exception as exc:
            if isinstance(exc, TimeoutError):
                raise exc

            normalized = normalize_com_error(exc)
            if isinstance(normalized, (CADRuntimeBusyError, CADRuntimeUnavailableError)):
                raise normalized from None

            sanitized_diag = describe_exception(exc)
            raise CADExportError(
                f"Failed refreshing draft drawing views: {sanitized_diag}",
                error_code="ARTIFACT_EXPORT_FAILED",
            ) from None

    return int(worker._invoke_com(_refresh))


def publish_drawing_to_path(
    raw_doc: Any,
    worker: Any,
    format_id: str,
    output_path: Path,
) -> None:
    """Publish the draft document to a 2D format (PDF or DXF) using SaveCopyAs.

    Args:
        raw_doc: Active Solid Edge COM DraftDocument on the STA worker thread.
        worker: STAThreadWorker running the COM apartment.
        format_id: Target format ("pdf" or "dxf").
        output_path: Destination file path in the validated private work directory.

    Raises:
        CADExportError: If format is unsupported, path is invalid, or publication fails.
        CADDocumentError: If the document is unseated or unresponsive.
    """
    normalized_format = str(format_id).strip().lower().lstrip(".")
    if normalized_format not in SUPPORTED_DRAWING_FORMATS:
        raise CADExportError(
            f"Unsupported drawing format: '{format_id}' (must be one of {sorted(SUPPORTED_DRAWING_FORMATS)})",
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
    expected_ext = DRAWING_FORMAT_EXTENSIONS[normalized_format]
    if output_path.suffix.lower() != expected_ext:
        raise CADExportError(
            f"File extension '{output_path.suffix}' does not match format '{format_id}' (expected '{expected_ext}')",
            error_code="ARTIFACT_EXPORT_FAILED",
        )

    # Preflight document health before COM export
    if not probe_document_health(raw_doc, worker):
        raise CADDocumentError(
            "Draft document is unresponsive or unseated prior to publication",
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
            f"Failed to publish {normalized_format.upper()} artifact '{output_path.name}': {sanitized_diag}",
            error_code="ARTIFACT_EXPORT_FAILED",
        ) from None


__all__ = [
    "DRAWING_FORMAT_EXTENSIONS",
    "SUPPORTED_DRAWING_FORMATS",
    "publish_drawing_to_path",
    "refresh_drawing_views",
]
