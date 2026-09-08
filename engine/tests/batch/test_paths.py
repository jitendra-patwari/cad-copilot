"""Path validation, traversal rejection, extension rules, and canonicalization tests."""

from __future__ import annotations

import pytest

from batch.models import (
    BatchInputSelection,
    BatchOperation,
    BatchRequest,
    BatchValidationError,
)
from batch.paths import (
    ALLOWED_3D_EXTENSIONS,
    ALLOWED_3D_FORMATS,
    ALLOWED_DRAWING_EXTENSIONS,
    ALLOWED_DRAWING_FORMATS,
    validate_and_canonicalize_files,
    validate_and_canonicalize_input_path,
)


class TestPathValidationAndCanonicalization:
    """Rigorous path safety, traversal rejection, and canonicalization checks."""

    @pytest.mark.parametrize(
        ("raw_path", "expected"),
        [
            ("part.par", "part.par"),
            ("sub/part.par", "sub/part.par"),
            ("sub\\part.par", "sub/part.par"),
            ("nested\\sub/part.par", "nested/sub/part.par"),
            ("a/b/c/part.asm", "a/b/c/part.asm"),
            ("a\\b\\c\\part.asm", "a/b/c/part.asm"),
        ],
    )
    def test_canonicalize_separators(self, raw_path: str, expected: str) -> None:
        assert validate_and_canonicalize_input_path(raw_path) == expected

    @pytest.mark.parametrize(
        "bad_path",
        [
            "/part.par",
            "\\part.par",
            "C:/part.par",
            "c:\\part.par",
            "D:part.par",
            "\\\\server\\share\\part.par",
            "//server/share/part.par",
            "folder/",
            "folder\\",
            "folder//part.par",
            "folder\\\\part.par",
            "folder\\/part.par",
            "folder/\\part.par",
            "../part.par",
            "..\\part.par",
            "folder/../part.par",
            "folder/./part.par",
            "folder/.",
            "folder/..",
            ".",
            "..",
            "part.par\0",
            "sub\0/part.par",
            "folder /part.par",
            " folder/part.par",
            "folder./part.par",
            "",
        ],
    )
    def test_reject_unsafe_paths(self, bad_path: str) -> None:
        with pytest.raises(BatchValidationError) as exc_info:
            validate_and_canonicalize_input_path(bad_path)
        assert exc_info.value.code == "INPUT_PATH_NOT_ALLOWED"

    def test_reject_non_string_path(self) -> None:
        with pytest.raises(BatchValidationError) as exc_info:
            validate_and_canonicalize_input_path(123)
        assert exc_info.value.code == "INPUT_PATH_NOT_ALLOWED"


class TestFilesSequenceValidation:
    """Validation of input files list, duplicate rejection, and extension enforcement."""

    def test_case_insensitive_duplicate_rejection(self) -> None:
        with pytest.raises(BatchValidationError) as exc_info:
            validate_and_canonicalize_files(["part.par", "PART.PAR"], "export_3d")
        assert exc_info.value.code == "INPUT_PATH_NOT_ALLOWED"
        assert "Duplicate" in exc_info.value.message

        with pytest.raises(BatchValidationError) as exc_info2:
            validate_and_canonicalize_files(["sub/part.par", "sub\\Part.par"], "export_3d")
        assert exc_info2.value.code == "INPUT_PATH_NOT_ALLOWED"

    def test_export_3d_allowed_extensions(self) -> None:
        files = ["a.par", "b.psm", "c.asm", "d.PAR", "e.Psm", "f.ASM"]
        result = validate_and_canonicalize_files(files, "export_3d")
        assert len(result) == 6

        bad_extensions = ["a.dft", "b.step", "c.stl", "d.x_t", "e.txt", "f.dwg"]
        for bad in bad_extensions:
            with pytest.raises(BatchValidationError) as exc_info:
                validate_and_canonicalize_files([bad], "export_3d")
            assert exc_info.value.code == "INPUT_EXTENSION_NOT_ALLOWED"

    def test_publish_drawing_allowed_extensions(self) -> None:
        files = ["sheet1.dft", "sheet2.DFT"]
        result = validate_and_canonicalize_files(files, "publish_drawing")
        assert len(result) == 2

        bad_extensions = ["a.par", "b.psm", "c.asm", "d.pdf", "e.dxf", "f.dwg"]
        for bad in bad_extensions:
            with pytest.raises(BatchValidationError) as exc_info:
                validate_and_canonicalize_files([bad], "publish_drawing")
            assert exc_info.value.code == "INPUT_EXTENSION_NOT_ALLOWED"

    def test_empty_files_sequence_rejected(self) -> None:
        with pytest.raises(BatchValidationError) as exc_info:
            validate_and_canonicalize_files([], "export_3d")
        assert exc_info.value.code == "INVALID_SCHEMA"


class TestBatchRequestPathInvariants:
    """Path safety and extension invariants during direct BatchRequest construction."""

    def test_request_traversal_path_rejected(self) -> None:
        """BatchRequest direct construction rejects path traversal."""
        with pytest.raises(ValueError, match="forbidden traversal"):
            BatchRequest(
                contract_version="1.0",
                request_id="req-trav-1",
                kind="batch_operation",
                input=BatchInputSelection(root="C:/data", files=("../secret.par",)),
                output_root="C:/data/out",
                operation=BatchOperation(type="export_3d", formats=("step",)),
            )

    def test_request_uncanonical_backslash_path_rejected(self) -> None:
        """BatchRequest direct construction rejects uncanonical backslashes."""
        with pytest.raises(ValueError, match="canonical POSIX relative format"):
            BatchRequest(
                contract_version="1.0",
                request_id="req-bs-1",
                kind="batch_operation",
                input=BatchInputSelection(root="C:/data", files=("sub\\part.par",)),
                output_root="C:/data/out",
                operation=BatchOperation(type="export_3d", formats=("step",)),
            )

    def test_request_extension_mismatch_rejected(self) -> None:
        """BatchRequest direct construction rejects drawing files for export_3d."""
        with pytest.raises(ValueError, match="not permitted for export_3d"):
            BatchRequest(
                contract_version="1.0",
                request_id="req-ext-1",
                kind="batch_operation",
                input=BatchInputSelection(root="C:/data", files=("drawing.dft",)),
                output_root="C:/data/out",
                operation=BatchOperation(type="export_3d", formats=("step",)),
            )

    def test_request_duplicate_files_rejected(self) -> None:
        """BatchRequest direct construction rejects case-colliding duplicates."""
        with pytest.raises(ValueError, match="Duplicate or case-colliding"):
            BatchRequest(
                contract_version="1.0",
                request_id="req-dup-1",
                kind="batch_operation",
                input=BatchInputSelection(root="C:/data", files=("part.par", "PART.PAR")),
                output_root="C:/data/out",
                operation=BatchOperation(type="export_3d", formats=("step",)),
            )

    def test_path_error_messages_are_bounded_and_non_reflecting(self) -> None:
        """A 1,000-character input path produces a bounded, non-reflecting error <= 128 characters."""
        long_name = "a" * 900 + ".bad"
        with pytest.raises(BatchValidationError) as exc_info:
            validate_and_canonicalize_files([long_name], "export_3d")

        msg = exc_info.value.message
        assert len(msg) <= 128
        assert long_name not in msg
        assert "export_3d" in msg

    def test_approved_format_constants_exact_equality(self) -> None:
        """Proves that ALLOWED_3D_FORMATS and ALLOWED_DRAWING_FORMATS match the approved M5.1 matrices."""
        assert frozenset({"step", "stl"}) == ALLOWED_3D_FORMATS
        assert frozenset({"pdf", "dxf"}) == ALLOWED_DRAWING_FORMATS
        assert frozenset({".par", ".psm", ".asm"}) == ALLOWED_3D_EXTENSIONS
        assert frozenset({".dft"}) == ALLOWED_DRAWING_EXTENSIONS
