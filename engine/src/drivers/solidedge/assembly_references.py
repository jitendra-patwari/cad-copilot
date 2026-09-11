"""Worker-local Solid Edge assembly occurrence/reference resolution verification.

Invariants:
    - Dedicated STA Seam: all COM calls execute strictly within an STA thread worker.
    - Pure Immutable Outcome: returns frozen AssemblyReferenceCheckResult; never leaks
      raw COM dispatch objects, pointers, or workstation file paths (SEC-07).
    - Authoritative In-Process Check: uses live Solid Edge 2026 occurrence status
      (Status == 2 / seOccurrenceStatusMissing) and OccurrenceDocument resolution.
    - Zero Modification: never repairs, relinks, suppresses, saves, or mutates
      the assembly document or its reference links.
    - Fail-Closed: if reference state cannot be determined with certainty, reports
      unresolved rather than attempting an ambiguous or unverified export.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from drivers.solidedge.errors import describe_exception, normalize_com_error
from drivers.solidedge.exporter import probe_document_health
from interfaces.exceptions import (
    CADDocumentError,
    CADExportError,
    CADRuntimeBusyError,
    CADRuntimeUnavailableError,
)

# Solid Edge API constant for missing occurrence status
SE_OCCURRENCE_STATUS_MISSING: Final[int] = 2


@dataclass(frozen=True)
class AssemblyReferenceCheckResult:
    """Immutable result of assembly occurrence reference resolution inspection."""

    is_resolved: bool
    total_count: int
    unresolved_count: int
    diagnostic_message: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.is_resolved, bool):
            raise TypeError("is_resolved must be a bool")
        if not isinstance(self.total_count, int) or self.total_count < 0:
            raise ValueError("total_count must be a non-negative int")
        if not isinstance(self.unresolved_count, int) or self.unresolved_count < 0:
            raise ValueError("unresolved_count must be a non-negative int")
        if self.unresolved_count > self.total_count:
            raise ValueError("unresolved_count cannot exceed total_count")
        if not self.is_resolved and self.diagnostic_message is None:
            raise ValueError("diagnostic_message is required when is_resolved is False")


# Maximum supported recursion depth for nested subassembly traversal
MAX_ASSEMBLY_TRAVERSAL_DEPTH: Final[int] = 32


def _reraise_if_fatal(exc: Exception) -> None:
    """Rethrow timeout, busy, and unavailable errors across occurrence inspection boundaries."""
    if isinstance(exc, TimeoutError):
        raise exc
    normalized = normalize_com_error(exc)
    if isinstance(normalized, (CADRuntimeBusyError, CADRuntimeUnavailableError)):
        raise normalized from None


def check_assembly_references(raw_doc: Any, worker: Any) -> AssemblyReferenceCheckResult:
    """Inspect all occurrences of an assembly document to verify reference resolution.

    Executes strictly on the STA worker thread against the explicit tracked raw_doc.
    Recursively inspects subassembly occurrences up to MAX_ASSEMBLY_TRAVERSAL_DEPTH.
    Checks each occurrence for missing status (SE_OCCURRENCE_STATUS_MISSING = 2) or
    unresolvable OccurrenceDocument COM object.

    Args:
        raw_doc: Tracked Solid Edge COM AssemblyDocument on the STA worker thread.
        worker: STAThreadWorker running the COM apartment.

    Returns:
        AssemblyReferenceCheckResult capturing resolution status and bounded counts.

    Raises:
        CADDocumentError: If document is unseated or unresponsive.
        CADRuntimeBusyError: If Solid Edge server is busy or call is rejected.
        CADRuntimeUnavailableError: If Solid Edge process died or disconnected.
        CADExportError: If reference verification encounters an unexpected fatal COM error.
    """
    if not probe_document_health(raw_doc, worker):
        raise CADDocumentError(
            "Assembly document is unresponsive or unseated prior to reference check",
            error_code="DOCUMENT_LOST",
        )

    def _check() -> AssemblyReferenceCheckResult:
        try:
            root_occs = getattr(raw_doc, "Occurrences", None)
            if root_occs is None:
                raise CADExportError(
                    "Assembly document missing required Occurrences collection",
                    error_code="ARTIFACT_EXPORT_FAILED",
                )

            total_count = 0
            unresolved_count = 0
            visited_docs: set[int] = set()

            def _traverse_occurrences(current_occs: Any, depth: int) -> None:
                nonlocal total_count, unresolved_count

                if depth > MAX_ASSEMBLY_TRAVERSAL_DEPTH:
                    raise CADExportError(
                        f"Assembly occurrence nesting exceeded maximum supported depth ({MAX_ASSEMBLY_TRAVERSAL_DEPTH})",
                        error_code="ARTIFACT_EXPORT_FAILED",
                    )

                try:
                    c_count = int(getattr(current_occs, "Count", 0))
                except Exception as count_exc:
                    _reraise_if_fatal(count_exc)
                    raise CADExportError(
                        f"Failed reading occurrence collection count: {describe_exception(count_exc)}",
                        error_code="ARTIFACT_EXPORT_FAILED",
                    ) from None

                for idx in range(1, c_count + 1):
                    total_count += 1
                    try:
                        occ = current_occs.Item(idx)
                    except Exception as item_exc:
                        _reraise_if_fatal(item_exc)
                        unresolved_count += 1
                        continue

                    # 1. Check explicit Status property (Status == 2 indicates missing component)
                    is_missing = False
                    try:
                        status_val = getattr(occ, "Status", None)
                        if status_val is not None and int(status_val) == SE_OCCURRENCE_STATUS_MISSING:
                            is_missing = True
                    except Exception as stat_exc:
                        _reraise_if_fatal(stat_exc)
                        is_missing = True

                    # 2. Check OccurrenceDocument resolvable
                    occ_doc = None
                    if not is_missing:
                        try:
                            occ_doc = getattr(occ, "OccurrenceDocument", None)
                            if occ_doc is None:
                                is_missing = True
                        except Exception as doc_exc:
                            _reraise_if_fatal(doc_exc)
                            is_missing = True

                    if is_missing:
                        unresolved_count += 1
                    elif occ_doc is not None:
                        # Check whether this occurrence is a subassembly without fallback
                        is_sub = False
                        sub_readable = False
                        try:
                            is_sub = bool(occ.Subassembly)
                            sub_readable = True
                        except Exception as sub_flag_exc:
                            _reraise_if_fatal(sub_flag_exc)
                            unresolved_count += 1

                        if sub_readable and is_sub:
                            # Inspect subassembly occurrences
                            sub_occs = None
                            try:
                                sub_occs = getattr(occ_doc, "Occurrences", None)
                            except Exception as sub_exc:
                                _reraise_if_fatal(sub_exc)
                                sub_occs = None

                            if sub_occs is None:
                                # A confirmed subassembly that cannot expose its occurrences must fail closed
                                unresolved_count += 1
                            else:
                                doc_id = id(occ_doc)
                                if doc_id not in visited_docs:
                                    visited_docs.add(doc_id)
                                    _traverse_occurrences(sub_occs, depth + 1)

            _traverse_occurrences(root_occs, depth=1)

            if unresolved_count > 0:
                return AssemblyReferenceCheckResult(
                    is_resolved=False,
                    total_count=total_count,
                    unresolved_count=unresolved_count,
                    diagnostic_message=f"Assembly contains {unresolved_count} unresolved reference(s)",
                )

            return AssemblyReferenceCheckResult(
                is_resolved=True,
                total_count=total_count,
                unresolved_count=0,
            )

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
                f"Failed to inspect assembly occurrences: {sanitized_diag}",
                error_code="ARTIFACT_EXPORT_FAILED",
            ) from None

    res = worker._invoke_com(_check)
    if not isinstance(res, AssemblyReferenceCheckResult):
        raise CADExportError(
            "Invalid assembly reference check outcome type",
            error_code="ARTIFACT_EXPORT_FAILED",
        )
    return res


__all__ = [
    "MAX_ASSEMBLY_TRAVERSAL_DEPTH",
    "SE_OCCURRENCE_STATUS_MISSING",
    "AssemblyReferenceCheckResult",
    "check_assembly_references",
]
