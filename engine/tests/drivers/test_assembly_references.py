"""Unit tests for Solid Edge assembly reference checking driver module.

Invariants:
    - Dedicated STA Seam: all COM calls execute strictly within an STA thread worker.
    - Pure Immutable Outcome: returns frozen AssemblyReferenceCheckResult without leaking COM objects.
    - Zero Path Leakage (SEC-07): component paths and filenames never enter diagnostic messages.
    - Robust Resolution: flags occurrence as unresolved if FileMissing() is True, OccurrenceDocument is None,
      or OccurrenceDocument access raises a COM exception.
    - Fatal COM Preserved: preserves CADRuntimeBusyError and CADRuntimeUnavailableError.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from drivers.solidedge.assembly_references import (
    AssemblyReferenceCheckResult,
    check_assembly_references,
)
from drivers.solidedge.errors import (
    RPC_E_SERVER_DIED,
    RPC_E_SERVERCALL_RETRYLATER,
)
from interfaces.exceptions import (
    CADDocumentError,
    CADExportError,
    CADRuntimeBusyError,
    CADRuntimeUnavailableError,
)


class MockCOMError(Exception):
    """Fake pywintypes.com_error for offline testing."""

    def __init__(self, hresult: int, message: str) -> None:
        super().__init__(message)
        self.hresult = hresult
        self.args = (hresult, message, None, None)


class FakeWorker:
    """Synchronous fake STA worker invoking task callables directly."""

    def __init__(self) -> None:
        self.call_log: list[str] = []

    def _invoke_com(self, call: Any) -> Any:
        return call()


class FakeOccurrenceDoc:
    """Fake resolved sub-document dispatch (part or subassembly)."""

    def __init__(
        self,
        name: str = "Part1.par",
        occurrences: FakeOccurrencesCollection | None = None,
    ) -> None:
        self.Name = name
        self._occurrences = occurrences

    @property
    def Occurrences(self) -> FakeOccurrencesCollection | None:
        return self._occurrences


class FakeOccurrence:
    """Fake Solid Edge COM Occurrence object."""

    def __init__(
        self,
        name: str = "Part1.par:1",
        filename: str = r"C:\Users\cad_user\Projects\Part1.par",
        status: int = 0,
        doc: Any | None = None,
        doc_exc: Exception | None = None,
        subassembly: bool = False,
        subassembly_exc: Exception | None = None,
        file_missing: bool = False,
        file_missing_exc: Exception | None = None,
    ) -> None:
        self.Name = name
        self.OccurrenceFileName = filename
        self.Status = status
        self._doc = doc if doc is not None and doc_exc is None else (FakeOccurrenceDoc() if doc_exc is None else None)
        self._doc_exc = doc_exc
        self._subassembly = subassembly
        self._subassembly_exc = subassembly_exc
        self._file_missing = file_missing
        self._file_missing_exc = file_missing_exc

    def FileMissing(self) -> bool:
        if self._file_missing_exc is not None:
            raise self._file_missing_exc
        return self._file_missing

    @property
    def Subassembly(self) -> bool:
        if self._subassembly_exc is not None:
            raise self._subassembly_exc
        return self._subassembly

    @property
    def OccurrenceDocument(self) -> Any:
        if self._doc_exc is not None:
            raise self._doc_exc
        return self._doc


class FakeOccurrencesCollection:
    """Fake 1-based Occurrences collection."""

    def __init__(self, occs: list[FakeOccurrence]) -> None:
        self._occs = occs

    @property
    def Count(self) -> int:
        return len(self._occs)

    def Item(self, index: int) -> FakeOccurrence:
        if 1 <= index <= len(self._occs):
            return self._occs[index - 1]
        raise IndexError(f"Index {index} out of bounds for Count={len(self._occs)}")


class FakeAssemblyDocument:
    """Fake Solid Edge COM AssemblyDocument supporting Name and Occurrences."""

    def __init__(
        self,
        name: str = "TopAssembly.asm",
        occs: list[FakeOccurrence] | None = None,
        has_occurrences: bool = True,
    ) -> None:
        self.Name = name
        self._occs = FakeOccurrencesCollection(occs) if occs is not None else FakeOccurrencesCollection([])
        self._has_occurrences = has_occurrences

    @property
    def Occurrences(self) -> FakeOccurrencesCollection | None:
        if not self._has_occurrences:
            return None
        return self._occs


# ===========================================================================
# Assembly Reference Check Tests
# ===========================================================================


class TestCheckAssemblyReferences:
    """Test suite for check_assembly_references."""

    def test_check_assembly_references_resolved(self) -> None:
        o1 = FakeOccurrence("Part1:1")
        o2 = FakeOccurrence("Part2:1")
        raw_doc = FakeAssemblyDocument("Assembly.asm", [o1, o2])
        worker = FakeWorker()

        result = check_assembly_references(raw_doc, worker)
        assert isinstance(result, AssemblyReferenceCheckResult)
        assert result.is_resolved is True
        assert result.total_count == 2
        assert result.unresolved_count == 0
        assert result.diagnostic_message is None

    def test_check_assembly_references_empty(self) -> None:
        raw_doc = FakeAssemblyDocument("Empty.asm", [])
        worker = FakeWorker()

        result = check_assembly_references(raw_doc, worker)
        assert result.is_resolved is True
        assert result.total_count == 0
        assert result.unresolved_count == 0
        assert result.diagnostic_message is None

    def test_check_assembly_references_file_missing(self) -> None:
        o1 = FakeOccurrence("Part1:1")
        # Missing occurrence signaled by FileMissing() == True
        o2 = FakeOccurrence(
            "BrokenPart:1",
            filename=r"C:\Secret_Drive\Users\confidential_user\BrokenPart.par",
            file_missing=True,
        )
        raw_doc = FakeAssemblyDocument("Assembly.asm", [o1, o2])
        worker = FakeWorker()

        result = check_assembly_references(raw_doc, worker)
        assert result.is_resolved is False
        assert result.total_count == 2
        assert result.unresolved_count == 1
        assert result.diagnostic_message is not None
        assert "1 unresolved reference(s)" in result.diagnostic_message
        # SEC-07: ensure filename and path never leak
        assert "BrokenPart" not in result.diagnostic_message
        assert "Secret_Drive" not in result.diagnostic_message
        assert "confidential_user" not in result.diagnostic_message

    def test_check_assembly_references_fixed_occurrence_with_resolvable_doc_is_resolved(self) -> None:
        # In Solid Edge, grounded base parts have Status == 2 (seOccurrenceStatusFixed).
        # When FileMissing() is False and OccurrenceDocument is valid, it must be resolved.
        fixed_occ = FakeOccurrence(
            "GroundedPart:1",
            status=2,
            file_missing=False,
            doc=FakeOccurrenceDoc("GroundedPart.par"),
        )
        raw_doc = FakeAssemblyDocument("Assembly.asm", [fixed_occ])
        worker = FakeWorker()

        result = check_assembly_references(raw_doc, worker)
        assert result.is_resolved is True
        assert result.total_count == 1
        assert result.unresolved_count == 0
        assert result.diagnostic_message is None

    def test_check_assembly_references_doc_none(self) -> None:
        # OccurrenceDocument returns None
        o1 = FakeOccurrence("MissingDocPart:1", doc=None)
        # explicitly set _doc to None
        o1._doc = None
        raw_doc = FakeAssemblyDocument("Assembly.asm", [o1])
        worker = FakeWorker()

        result = check_assembly_references(raw_doc, worker)
        assert result.is_resolved is False
        assert result.total_count == 1
        assert result.unresolved_count == 1
        assert "1 unresolved reference(s)" in str(result.diagnostic_message)

    def test_check_assembly_references_doc_com_exception(self) -> None:
        # OccurrenceDocument raises STG_E_FILENOTFOUND com_error
        com_exc = MockCOMError(-2147352567, "Exception occurred.")
        o1 = FakeOccurrence("MissingPart:1", doc_exc=com_exc)
        raw_doc = FakeAssemblyDocument("Assembly.asm", [o1])
        worker = FakeWorker()

        result = check_assembly_references(raw_doc, worker)
        assert result.is_resolved is False
        assert result.total_count == 1
        assert result.unresolved_count == 1
        assert "1 unresolved reference(s)" in str(result.diagnostic_message)
        # Confirm COM text or pointers are not leaked
        assert "-2147352567" not in str(result.diagnostic_message)

    def test_check_assembly_references_item_access_error(self) -> None:
        # Simulates Item(idx) raising an error
        occs = [FakeOccurrence("Part1:1")]
        raw_doc = FakeAssemblyDocument("Assembly.asm", occs)

        def fail_item(idx: int) -> FakeOccurrence:
            raise MockCOMError(0x80004005, "Unspecified error")

        raw_doc.Occurrences.Item = fail_item  # type: ignore[union-attr,method-assign]
        worker = FakeWorker()

        result = check_assembly_references(raw_doc, worker)
        assert result.is_resolved is False
        assert result.total_count == 1
        assert result.unresolved_count == 1

    def test_check_assembly_references_unresponsive_document(self) -> None:
        raw_doc = FakeAssemblyDocument(name="")
        worker = FakeWorker()

        with pytest.raises(CADDocumentError) as exc_info:
            check_assembly_references(raw_doc, worker)
        assert exc_info.value.error_code == "DOCUMENT_LOST"

    def test_check_assembly_references_missing_collection(self) -> None:
        raw_doc = FakeAssemblyDocument(name="Corrupt.asm", has_occurrences=False)
        worker = FakeWorker()

        with pytest.raises(CADExportError) as exc_info:
            check_assembly_references(raw_doc, worker)
        assert exc_info.value.error_code == "ARTIFACT_EXPORT_FAILED"
        assert "missing required Occurrences collection" in str(exc_info.value)

    def test_check_assembly_references_preserves_runtime_busy(self) -> None:
        raw_doc = MagicMock()
        type(raw_doc).Name = property(fget=MagicMock(return_value="Assembly.asm"))
        type(raw_doc).Occurrences = property(
            fget=MagicMock(side_effect=MockCOMError(RPC_E_SERVERCALL_RETRYLATER, "Server call retry later"))
        )
        worker = FakeWorker()

        with pytest.raises(CADRuntimeBusyError) as exc_info:
            check_assembly_references(raw_doc, worker)
        assert exc_info.value.error_code == "RUNTIME_BUSY_TIMEOUT"

    def test_check_assembly_references_preserves_runtime_unavailable(self) -> None:
        raw_doc = MagicMock()
        type(raw_doc).Name = property(fget=MagicMock(return_value="Assembly.asm"))
        type(raw_doc).Occurrences = property(fget=MagicMock(side_effect=MockCOMError(RPC_E_SERVER_DIED, "Server died")))
        worker = FakeWorker()

        with pytest.raises(CADRuntimeUnavailableError) as exc_info:
            check_assembly_references(raw_doc, worker)
        assert exc_info.value.error_code == "RUNTIME_UNAVAILABLE"

    def test_check_assembly_references_preserves_timeout(self) -> None:
        raw_doc = MagicMock()
        type(raw_doc).Name = property(fget=MagicMock(return_value="Assembly.asm"))
        type(raw_doc).Occurrences = property(fget=MagicMock(side_effect=TimeoutError("STA worker timed out")))
        worker = FakeWorker()

        with pytest.raises(TimeoutError):
            check_assembly_references(raw_doc, worker)

    def test_check_assembly_references_nested_resolved(self) -> None:
        child_p1 = FakeOccurrence("ChildPart1:1")
        child_p2 = FakeOccurrence("ChildPart2:1")
        sub_doc = FakeOccurrenceDoc("SubAssembly.asm", occurrences=FakeOccurrencesCollection([child_p1, child_p2]))
        sub_occ = FakeOccurrence("SubAssy:1", doc=sub_doc, subassembly=True)
        root_part = FakeOccurrence("RootPart:1")

        raw_doc = FakeAssemblyDocument("Top.asm", [sub_occ, root_part])
        worker = FakeWorker()

        result = check_assembly_references(raw_doc, worker)
        assert result.is_resolved is True
        # sub_occ + child_p1 + child_p2 + root_part = 4 total occurrences
        assert result.total_count == 4
        assert result.unresolved_count == 0

    def test_check_assembly_references_nested_unresolved(self) -> None:
        child_p1 = FakeOccurrence("ChildPart1:1")
        child_p2_broken = FakeOccurrence(
            "ChildBroken:1",
            filename=r"C:\Secret\ChildBroken.par",
            file_missing=True,
        )
        sub_doc = FakeOccurrenceDoc(
            "SubAssembly.asm", occurrences=FakeOccurrencesCollection([child_p1, child_p2_broken])
        )
        sub_occ = FakeOccurrence("SubAssy:1", doc=sub_doc, subassembly=True)
        root_part = FakeOccurrence("RootPart:1")

        raw_doc = FakeAssemblyDocument("Top.asm", [sub_occ, root_part])
        worker = FakeWorker()

        result = check_assembly_references(raw_doc, worker)
        assert result.is_resolved is False
        assert result.total_count == 4
        assert result.unresolved_count == 1
        assert "1 unresolved reference(s)" in str(result.diagnostic_message)
        assert "Secret" not in str(result.diagnostic_message)

    def test_check_assembly_references_subassembly_cannot_expose_occurrences_fails_closed(self) -> None:
        # A confirmed subassembly whose Occurrences collection cannot be retrieved
        sub_doc = FakeOccurrenceDoc("SubAssemblyBroken.asm", occurrences=None)
        sub_occ = FakeOccurrence("SubAssyBroken:1", doc=sub_doc, subassembly=True)
        root_part = FakeOccurrence("RootPart:1")

        raw_doc = FakeAssemblyDocument("Top.asm", [sub_occ, root_part])
        worker = FakeWorker()

        result = check_assembly_references(raw_doc, worker)
        assert result.is_resolved is False
        assert result.unresolved_count == 1
        assert "Assembly contains 1 unresolved reference(s)" in str(result.diagnostic_message)

    def test_check_assembly_references_subassembly_property_read_failure_fails_closed(self) -> None:
        # An occurrence whose Subassembly property raises a non-fatal COM exception
        occ_err = FakeOccurrence(
            "IndeterminatePart:1",
            subassembly_exc=MockCOMError(-2147467259, "Unspecified COM error reading Subassembly"),
        )
        raw_doc = FakeAssemblyDocument("Top.asm", [occ_err])
        worker = FakeWorker()

        result = check_assembly_references(raw_doc, worker)
        assert result.is_resolved is False
        assert result.unresolved_count == 1
        assert "Assembly contains 1 unresolved reference(s)" in str(result.diagnostic_message)

    def test_check_assembly_references_subassembly_fatal_busy(self) -> None:
        occ_fatal = FakeOccurrence(
            "FatalPart:1",
            subassembly_exc=MockCOMError(RPC_E_SERVERCALL_RETRYLATER, "Server busy"),
        )
        raw_doc = FakeAssemblyDocument("Top.asm", [occ_fatal])
        worker = FakeWorker()

        with pytest.raises(CADRuntimeBusyError) as exc_info:
            check_assembly_references(raw_doc, worker)
        assert exc_info.value.error_code == "RUNTIME_BUSY_TIMEOUT"

    def test_check_assembly_references_item_fatal_busy(self) -> None:
        occs = [FakeOccurrence("Part1:1")]
        raw_doc = FakeAssemblyDocument("Assembly.asm", occs)

        def raise_busy(idx: int) -> FakeOccurrence:
            raise MockCOMError(RPC_E_SERVERCALL_RETRYLATER, "Server busy")

        raw_doc.Occurrences.Item = raise_busy  # type: ignore[union-attr,method-assign]
        worker = FakeWorker()

        with pytest.raises(CADRuntimeBusyError) as exc_info:
            check_assembly_references(raw_doc, worker)
        assert exc_info.value.error_code == "RUNTIME_BUSY_TIMEOUT"

    def test_check_assembly_references_item_fatal_unavailable(self) -> None:
        occs = [FakeOccurrence("Part1:1")]
        raw_doc = FakeAssemblyDocument("Assembly.asm", occs)

        def raise_died(idx: int) -> FakeOccurrence:
            raise MockCOMError(RPC_E_SERVER_DIED, "Server died")

        raw_doc.Occurrences.Item = raise_died  # type: ignore[union-attr,method-assign]
        worker = FakeWorker()

        with pytest.raises(CADRuntimeUnavailableError) as exc_info:
            check_assembly_references(raw_doc, worker)
        assert exc_info.value.error_code == "RUNTIME_UNAVAILABLE"

    def test_check_assembly_references_item_fatal_timeout(self) -> None:
        occs = [FakeOccurrence("Part1:1")]
        raw_doc = FakeAssemblyDocument("Assembly.asm", occs)

        def raise_timeout(idx: int) -> FakeOccurrence:
            raise TimeoutError("STA worker timed out")

        raw_doc.Occurrences.Item = raise_timeout  # type: ignore[union-attr,method-assign]
        worker = FakeWorker()

        with pytest.raises(TimeoutError):
            check_assembly_references(raw_doc, worker)

    def test_check_assembly_references_file_missing_fatal_unavailable(self) -> None:
        occ = MagicMock()
        occ.Name = "Part1:1"
        occ.FileMissing = MagicMock(side_effect=MockCOMError(RPC_E_SERVER_DIED, "Server died"))
        raw_doc = FakeAssemblyDocument("Assembly.asm")
        raw_doc.Occurrences.Item = lambda idx: occ  # type: ignore[union-attr,method-assign]
        type(raw_doc.Occurrences).Count = property(fget=lambda self: 1)  # type: ignore[union-attr,method-assign]
        worker = FakeWorker()

        with pytest.raises(CADRuntimeUnavailableError) as exc_info:
            check_assembly_references(raw_doc, worker)
        assert exc_info.value.error_code == "RUNTIME_UNAVAILABLE"

    def test_check_assembly_references_file_missing_fatal_timeout(self) -> None:
        occ = MagicMock()
        occ.Name = "Part1:1"
        occ.FileMissing = MagicMock(side_effect=TimeoutError("STA worker timeout"))
        raw_doc = FakeAssemblyDocument("Assembly.asm")
        raw_doc.Occurrences.Item = lambda idx: occ  # type: ignore[union-attr,method-assign]
        type(raw_doc.Occurrences).Count = property(fget=lambda self: 1)  # type: ignore[union-attr,method-assign]
        worker = FakeWorker()

        with pytest.raises(TimeoutError):
            check_assembly_references(raw_doc, worker)

    def test_check_assembly_references_doc_fatal_busy(self) -> None:
        occ = FakeOccurrence(
            "Part1:1",
            doc_exc=MockCOMError(RPC_E_SERVERCALL_RETRYLATER, "Server call retry later"),
        )
        raw_doc = FakeAssemblyDocument("Assembly.asm", [occ])
        worker = FakeWorker()

        with pytest.raises(CADRuntimeBusyError) as exc_info:
            check_assembly_references(raw_doc, worker)
        assert exc_info.value.error_code == "RUNTIME_BUSY_TIMEOUT"

    def test_check_assembly_references_doc_fatal_unavailable(self) -> None:
        occ = FakeOccurrence(
            "Part1:1",
            doc_exc=MockCOMError(RPC_E_SERVER_DIED, "Server process crashed"),
        )
        raw_doc = FakeAssemblyDocument("Assembly.asm", [occ])
        worker = FakeWorker()

        with pytest.raises(CADRuntimeUnavailableError) as exc_info:
            check_assembly_references(raw_doc, worker)
        assert exc_info.value.error_code == "RUNTIME_UNAVAILABLE"

    def test_check_assembly_references_doc_fatal_timeout(self) -> None:
        occ = FakeOccurrence(
            "Part1:1",
            doc_exc=TimeoutError("STA thread timeout accessing OccurrenceDocument"),
        )
        raw_doc = FakeAssemblyDocument("Assembly.asm", [occ])
        worker = FakeWorker()

        with pytest.raises(TimeoutError):
            check_assembly_references(raw_doc, worker)
