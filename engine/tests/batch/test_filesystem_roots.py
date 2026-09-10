"""Tests for input and output root validation, drive classification, containment, and platform checks."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from batch.allocation import BatchSafetyRejectionError
from batch.filesystem import (
    DRIVE_CDROM,
    DRIVE_FIXED,
    DRIVE_REMOTE,
    DRIVE_UNKNOWN,
    assert_strictly_contained,
    validate_input_root,
    validate_output_root,
)


class TestValidateInputRoot:
    """Tests for validate_input_root."""

    def test_valid_input_root(self, tmp_path: Path) -> None:
        root_dir = tmp_path / "valid_input_root"
        root_dir.mkdir()

        with patch("batch.filesystem.get_windows_drive_type", return_value=DRIVE_FIXED):
            resolved = validate_input_root(root_dir, request_id="req-1")
        assert resolved == root_dir.resolve()
        assert resolved.is_dir()

    def test_empty_input_root_rejected(self) -> None:
        with pytest.raises(BatchSafetyRejectionError) as exc_info:
            validate_input_root("", request_id="req-2")
        assert exc_info.value.diagnostic.code == "INPUT_PATH_NOT_ALLOWED"
        assert "cannot be empty" in exc_info.value.diagnostic.message

    @pytest.mark.parametrize(
        "whitespace_root",
        [
            " C:\\cad\\input",
            "C:\\cad\\input ",
            "  C:\\cad\\input  ",
            "\tC:\\cad\\input",
            "C:\\cad\\input\n",
        ],
    )
    def test_input_root_whitespace_rejected_without_normalization(self, whitespace_root: str) -> None:
        with pytest.raises(BatchSafetyRejectionError) as exc_info:
            validate_input_root(whitespace_root, request_id="req-ws")
        assert exc_info.value.diagnostic.code == "INPUT_PATH_NOT_ALLOWED"
        assert "leading or trailing whitespace" in exc_info.value.diagnostic.message

    @pytest.mark.parametrize(
        "prefix_path",
        [
            "\\\\server\\share\\cad",
            "//server/share/cad",
            "\\\\?\\C:\\cad",
            "\\\\.\\C:\\cad",
            "/unix/absolute/cad",
        ],
    )
    def test_unc_and_extended_prefixes_rejected(self, prefix_path: str) -> None:
        with pytest.raises(BatchSafetyRejectionError) as exc_info:
            validate_input_root(prefix_path, request_id="req-unc")
        assert exc_info.value.diagnostic.code == "INPUT_PATH_NOT_ALLOWED"
        assert "UNC" in exc_info.value.diagnostic.message

    def test_unsupported_platform_rejected(self, tmp_path: Path) -> None:
        root_dir = tmp_path / "input_root"
        root_dir.mkdir()
        with patch("sys.platform", "linux"):
            with pytest.raises(BatchSafetyRejectionError) as exc_info:
                validate_input_root(root_dir, request_id="req-plat")
            assert exc_info.value.diagnostic.code == "INPUT_PATH_NOT_ALLOWED"
            assert "only supported on Windows" in exc_info.value.diagnostic.message

    def test_drive_root_rejected(self) -> None:
        with pytest.raises(BatchSafetyRejectionError) as exc_info:
            validate_input_root("C:\\", request_id="req-root")
        assert exc_info.value.diagnostic.code == "INPUT_PATH_NOT_ALLOWED"
        assert "filesystem drive root" in exc_info.value.diagnostic.message

    def test_non_existent_input_root_rejected(self, tmp_path: Path) -> None:
        missing = tmp_path / "missing_dir_12345"
        with (
            patch("batch.filesystem.get_windows_drive_type", return_value=DRIVE_FIXED),
            pytest.raises(BatchSafetyRejectionError) as exc_info,
        ):
            validate_input_root(missing, request_id="req-miss")
        assert exc_info.value.diagnostic.code == "INPUT_ROOT_NOT_FOUND"
        assert "does not exist" in exc_info.value.diagnostic.message
        assert str(missing) not in exc_info.value.diagnostic.message

    def test_file_as_input_root_rejected(self, tmp_path: Path) -> None:
        file_path = tmp_path / "not_a_dir.txt"
        file_path.write_text("abc", encoding="utf-8")
        with (
            patch("batch.filesystem.get_windows_drive_type", return_value=DRIVE_FIXED),
            pytest.raises(BatchSafetyRejectionError) as exc_info,
        ):
            validate_input_root(file_path, request_id="req-file")
        assert exc_info.value.diagnostic.code == "INPUT_PATH_NOT_ALLOWED"
        assert "must be a directory" in exc_info.value.diagnostic.message

    def test_mapped_network_drive_rejected_before_resolution(self) -> None:
        """Verify that remote drive is classified and rejected before any resolution or probe."""
        with (
            patch("batch.filesystem.get_windows_drive_type", return_value=DRIVE_REMOTE),
            patch.object(Path, "resolve") as mock_resolve,
        ):
            with pytest.raises(BatchSafetyRejectionError) as exc_info:
                validate_input_root("Z:\\remote\\cad\\input", request_id="req-remote")
            assert exc_info.value.diagnostic.code == "INPUT_PATH_NOT_ALLOWED"
            assert "network or mapped drive" in exc_info.value.diagnostic.message
            mock_resolve.assert_not_called()

    def test_non_local_drive_types_rejected_before_resolution(self) -> None:
        for bad_type in (DRIVE_CDROM, DRIVE_UNKNOWN):
            with (
                patch("batch.filesystem.get_windows_drive_type", return_value=bad_type),
                patch.object(Path, "resolve") as mock_resolve,
            ):
                with pytest.raises(BatchSafetyRejectionError) as exc_info:
                    validate_input_root("D:\\media\\cad\\input", request_id="req-cd")
                assert exc_info.value.diagnostic.code == "INPUT_PATH_NOT_ALLOWED"
                assert "must be on a local drive" in exc_info.value.diagnostic.message
                mock_resolve.assert_not_called()

    def test_reparse_input_root_rejected(self, tmp_path: Path) -> None:
        root_dir = tmp_path / "reparse_dir"
        root_dir.mkdir()
        with (
            patch("batch.filesystem.get_windows_drive_type", return_value=DRIVE_FIXED),
            patch("batch.filesystem.is_symlink_or_reparse_point", return_value=True),
        ):
            with pytest.raises(BatchSafetyRejectionError) as exc_info:
                validate_input_root(root_dir, request_id="req-rep")
            assert exc_info.value.diagnostic.code == "INPUT_PATH_NOT_ALLOWED"
            assert "reparse point" in exc_info.value.diagnostic.message

    def test_stream_colon_rejected(self, tmp_path: Path) -> None:
        bad_path = str(tmp_path / "dir:stream")
        with pytest.raises(BatchSafetyRejectionError) as exc_info:
            validate_input_root(bad_path, request_id="req-col")
        assert exc_info.value.diagnostic.code == "INPUT_PATH_NOT_ALLOWED"
        assert "stream colons" in exc_info.value.diagnostic.message

    @pytest.mark.parametrize(
        "reserved_path",
        [
            "C:\\CON",
            "C:\\cad\\CON\\child",
            "C:\\cad\\prn",
            "C:\\cad\\aux.txt",
            "C:\\cad\\NUL",
            "C:\\cad\\com1",
            "C:\\cad\\lpt5",
            "C:\\cad\\clock$",
        ],
    )
    def test_input_root_reserved_device_names_rejected_before_resolution(self, reserved_path: str) -> None:
        with (
            patch("batch.filesystem.get_windows_drive_type", return_value=DRIVE_FIXED),
            patch.object(Path, "resolve") as mock_resolve,
        ):
            with pytest.raises(BatchSafetyRejectionError) as exc_info:
                validate_input_root(reserved_path, request_id="req-dev")
            assert exc_info.value.diagnostic.code == "INPUT_PATH_NOT_ALLOWED"
            assert "reserved Windows device name" in exc_info.value.diagnostic.message
            mock_resolve.assert_not_called()

    @pytest.mark.parametrize(
        "trailing_dot_path",
        [
            "C:\\cad\\folder.\\child",
            "C:\\cad\\folder.",
            "C:\\cad\\sub\\test..\\child",
        ],
    )
    def test_input_root_trailing_dots_rejected_before_resolution(self, trailing_dot_path: str) -> None:
        with (
            patch("batch.filesystem.get_windows_drive_type", return_value=DRIVE_FIXED),
            patch.object(Path, "resolve") as mock_resolve,
        ):
            with pytest.raises(BatchSafetyRejectionError) as exc_info:
                validate_input_root(trailing_dot_path, request_id="req-dot")
            assert exc_info.value.diagnostic.code == "INPUT_PATH_NOT_ALLOWED"
            assert "trailing dots" in exc_info.value.diagnostic.message
            mock_resolve.assert_not_called()

    @pytest.mark.parametrize(
        "segment_ws_path",
        [
            "C:\\cad\\folder \\child",
            "C:\\cad\\ folder\\child",
        ],
    )
    def test_input_root_segment_whitespace_rejected_before_resolution(self, segment_ws_path: str) -> None:
        with (
            patch("batch.filesystem.get_windows_drive_type", return_value=DRIVE_FIXED),
            patch.object(Path, "resolve") as mock_resolve,
        ):
            with pytest.raises(BatchSafetyRejectionError) as exc_info:
                validate_input_root(segment_ws_path, request_id="req-segws")
            assert exc_info.value.diagnostic.code == "INPUT_PATH_NOT_ALLOWED"
            assert "leading or trailing whitespace" in exc_info.value.diagnostic.message
            mock_resolve.assert_not_called()

    @pytest.mark.parametrize(
        "forbidden_char_path",
        [
            "C:\\cad\\*\\child",
            "C:\\cad\\test?dir",
            "C:\\cad\\<bad>\\dir",
            'C:\\cad\\"quoted"\\dir',
            "C:\\cad\\pipe|dir",
        ],
    )
    def test_input_root_forbidden_chars_rejected_before_resolution(self, forbidden_char_path: str) -> None:
        with (
            patch("batch.filesystem.get_windows_drive_type", return_value=DRIVE_FIXED),
            patch.object(Path, "resolve") as mock_resolve,
        ):
            with pytest.raises(BatchSafetyRejectionError) as exc_info:
                validate_input_root(forbidden_char_path, request_id="req-forbid")
            assert exc_info.value.diagnostic.code == "INPUT_PATH_NOT_ALLOWED"
            assert "forbidden characters or stream colons" in exc_info.value.diagnostic.message
            mock_resolve.assert_not_called()

    @pytest.mark.parametrize(
        "control_char_path",
        [
            "C:\\cad\\ctrl\x01\\child",
            "C:\\cad\\folder\x1f\\child",
            "C:\\cad\\folder\0child",
            "C:\\cad\n\\folder",
        ],
    )
    def test_input_root_control_chars_rejected_before_resolution(self, control_char_path: str) -> None:
        with (
            patch("batch.filesystem.get_windows_drive_type", return_value=DRIVE_FIXED),
            patch.object(Path, "resolve") as mock_resolve,
        ):
            with pytest.raises(BatchSafetyRejectionError) as exc_info:
                validate_input_root(control_char_path, request_id="req-ctrl")
            assert exc_info.value.diagnostic.code == "INPUT_PATH_NOT_ALLOWED"
            assert "forbidden control characters" in exc_info.value.diagnostic.message
            mock_resolve.assert_not_called()

    @pytest.mark.parametrize(
        "consecutive_sep_path",
        [
            "C:\\\\cad\\folder",
            "C://cad/folder",
            "C:\\cad//folder",
            "C:\\cad\\\\folder",
            "C:\\cad/\\folder",
            "C:\\cad\\/folder",
        ],
    )
    def test_input_root_consecutive_separators_rejected_before_resolution(self, consecutive_sep_path: str) -> None:
        with (
            patch("batch.filesystem.get_windows_drive_type", return_value=DRIVE_FIXED),
            patch.object(Path, "resolve") as mock_resolve,
        ):
            with pytest.raises(BatchSafetyRejectionError) as exc_info:
                validate_input_root(consecutive_sep_path, request_id="req-sep")
            assert exc_info.value.diagnostic.code == "INPUT_PATH_NOT_ALLOWED"
            assert "empty components or invalid separators" in exc_info.value.diagnostic.message
            mock_resolve.assert_not_called()

    @pytest.mark.parametrize(
        "traversal_path",
        [
            "C:\\cad\\..\\folder",
            "C:\\cad\\.\\folder",
        ],
    )
    def test_input_root_traversal_segments_rejected_before_resolution(self, traversal_path: str) -> None:
        with (
            patch("batch.filesystem.get_windows_drive_type", return_value=DRIVE_FIXED),
            patch.object(Path, "resolve") as mock_resolve,
        ):
            with pytest.raises(BatchSafetyRejectionError) as exc_info:
                validate_input_root(traversal_path, request_id="req-trav")
            assert exc_info.value.diagnostic.code == "INPUT_PATH_NOT_ALLOWED"
            assert "forbidden traversal or empty segment" in exc_info.value.diagnostic.message
            mock_resolve.assert_not_called()

    def test_input_root_resolve_value_error_caught_defensively(self) -> None:
        with (
            patch("batch.filesystem.get_windows_drive_type", return_value=DRIVE_FIXED),
            patch.object(Path, "resolve", side_effect=ValueError("Windows kernel malformed path error")),
        ):
            with pytest.raises(BatchSafetyRejectionError) as exc_info:
                validate_input_root("C:\\cad\\valid_segment", request_id="req-valerr")
            assert exc_info.value.diagnostic.code == "INPUT_ROOT_NOT_FOUND"
            assert "Failed to resolve input root directory" in exc_info.value.diagnostic.message


class TestValidateOutputRoot:
    """Tests for validate_output_root."""

    def test_valid_output_root(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "valid_output_root"
        out_dir.mkdir()

        with patch("batch.filesystem.get_windows_drive_type", return_value=DRIVE_FIXED):
            resolved = validate_output_root(out_dir, request_id="req-out")
        assert resolved == out_dir.resolve()
        assert resolved.is_dir()

    def test_empty_output_root_rejected(self) -> None:
        with pytest.raises(BatchSafetyRejectionError) as exc_info:
            validate_output_root("", request_id="req-empty")
        assert exc_info.value.diagnostic.code == "OUTPUT_ROOT_UNAVAILABLE"
        assert "cannot be empty" in exc_info.value.diagnostic.message

    @pytest.mark.parametrize(
        "whitespace_root",
        [
            " C:\\cad\\output",
            "C:\\cad\\output ",
            "  C:\\cad\\output  ",
            "\tC:\\cad\\output",
            "C:\\cad\\output\n",
        ],
    )
    def test_output_root_whitespace_rejected_without_normalization(self, whitespace_root: str) -> None:
        with pytest.raises(BatchSafetyRejectionError) as exc_info:
            validate_output_root(whitespace_root, request_id="req-out-ws")
        assert exc_info.value.diagnostic.code == "OUTPUT_ROOT_UNAVAILABLE"
        assert "leading or trailing whitespace" in exc_info.value.diagnostic.message

    @pytest.mark.parametrize(
        "prefix_path",
        [
            "\\\\server\\share\\out",
            "//server/share/out",
            "\\\\?\\C:\\out",
            "\\\\.\\C:\\out",
            "/posix/absolute/out",
        ],
    )
    def test_output_unc_and_extended_prefixes_rejected(self, prefix_path: str) -> None:
        with pytest.raises(BatchSafetyRejectionError) as exc_info:
            validate_output_root(prefix_path, request_id="req-out-unc")
        assert exc_info.value.diagnostic.code == "OUTPUT_ROOT_UNAVAILABLE"
        assert "UNC" in exc_info.value.diagnostic.message

    def test_output_unsupported_platform_rejected(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "output_root"
        out_dir.mkdir()
        with patch("sys.platform", "darwin"):
            with pytest.raises(BatchSafetyRejectionError) as exc_info:
                validate_output_root(out_dir, request_id="req-plat-out")
            assert exc_info.value.diagnostic.code == "OUTPUT_ROOT_UNAVAILABLE"
            assert "only supported on Windows" in exc_info.value.diagnostic.message

    def test_output_drive_root_rejected(self) -> None:
        with pytest.raises(BatchSafetyRejectionError) as exc_info:
            validate_output_root("C:\\", request_id="req-out-drive")
        assert exc_info.value.diagnostic.code == "OUTPUT_ROOT_UNAVAILABLE"
        assert "filesystem drive root" in exc_info.value.diagnostic.message

    def test_non_existent_output_root_rejected(self, tmp_path: Path) -> None:
        missing = tmp_path / "missing_out_dir"
        with (
            patch("batch.filesystem.get_windows_drive_type", return_value=DRIVE_FIXED),
            pytest.raises(BatchSafetyRejectionError) as exc_info,
        ):
            validate_output_root(missing, request_id="req-out-miss")
        assert exc_info.value.diagnostic.code == "OUTPUT_ROOT_UNAVAILABLE"
        assert "does not exist" in exc_info.value.diagnostic.message
        assert str(missing) not in exc_info.value.diagnostic.message

    def test_file_as_output_root_rejected(self, tmp_path: Path) -> None:
        f = tmp_path / "not_a_dir.txt"
        f.write_text("data", encoding="utf-8")
        with (
            patch("batch.filesystem.get_windows_drive_type", return_value=DRIVE_FIXED),
            pytest.raises(BatchSafetyRejectionError) as exc_info,
        ):
            validate_output_root(f, request_id="req-out-file")
        assert exc_info.value.diagnostic.code == "OUTPUT_ROOT_UNAVAILABLE"
        assert "must be a directory" in exc_info.value.diagnostic.message

    def test_output_mapped_network_drive_rejected_before_resolution(self) -> None:
        with (
            patch("batch.filesystem.get_windows_drive_type", return_value=DRIVE_REMOTE),
            patch.object(Path, "resolve") as mock_resolve,
        ):
            with pytest.raises(BatchSafetyRejectionError) as exc_info:
                validate_output_root("Y:\\mapped_output", request_id="req-out-remote")
            assert exc_info.value.diagnostic.code == "OUTPUT_ROOT_UNAVAILABLE"
            assert "network or mapped drive" in exc_info.value.diagnostic.message
            mock_resolve.assert_not_called()

    def test_unwritable_output_root_rejected(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "readonly_out"
        out_dir.mkdir()
        with (
            patch("batch.filesystem.get_windows_drive_type", return_value=DRIVE_FIXED),
            patch("os.access", return_value=False),
        ):
            with pytest.raises(BatchSafetyRejectionError) as exc_info:
                validate_output_root(out_dir, request_id="req-ro")
            assert exc_info.value.diagnostic.code == "OUTPUT_ROOT_UNAVAILABLE"
            assert "not readable and writable" in exc_info.value.diagnostic.message

    def test_reparse_output_root_rejected(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "reparse_out"
        out_dir.mkdir()
        with (
            patch("batch.filesystem.get_windows_drive_type", return_value=DRIVE_FIXED),
            patch("batch.filesystem.is_symlink_or_reparse_point", return_value=True),
        ):
            with pytest.raises(BatchSafetyRejectionError) as exc_info:
                validate_output_root(out_dir, request_id="req-rep-out")
            assert exc_info.value.diagnostic.code == "OUTPUT_ROOT_UNAVAILABLE"
            assert "reparse point" in exc_info.value.diagnostic.message

    def test_output_root_reserved_device_rejected_before_resolution(self) -> None:
        with (
            patch("batch.filesystem.get_windows_drive_type", return_value=DRIVE_FIXED),
            patch.object(Path, "resolve") as mock_resolve,
        ):
            with pytest.raises(BatchSafetyRejectionError) as exc_info:
                validate_output_root("C:\\cad\\CON\\out", request_id="req-out-dev")
            assert exc_info.value.diagnostic.code == "OUTPUT_ROOT_UNAVAILABLE"
            assert "reserved Windows device name" in exc_info.value.diagnostic.message
            mock_resolve.assert_not_called()

    def test_output_root_resolve_value_error_caught_defensively(self) -> None:
        with (
            patch("batch.filesystem.get_windows_drive_type", return_value=DRIVE_FIXED),
            patch.object(Path, "resolve", side_effect=ValueError("OS resolution error")),
        ):
            with pytest.raises(BatchSafetyRejectionError) as exc_info:
                validate_output_root("C:\\cad\\valid_output_dir", request_id="req-out-valerr")
            assert exc_info.value.diagnostic.code == "OUTPUT_ROOT_UNAVAILABLE"
            assert "Failed to resolve output root directory" in exc_info.value.diagnostic.message


class TestStrictContainment:
    """Tests for assert_strictly_contained."""

    def test_strictly_contained_child(self, tmp_path: Path) -> None:
        sub = tmp_path / "nested" / "file.txt"
        sub.parent.mkdir()
        sub.write_text("x", encoding="utf-8")

        resolved = assert_strictly_contained(sub, tmp_path)
        assert resolved == sub.resolve()

    def test_child_matching_root_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(BatchSafetyRejectionError) as exc_info:
            assert_strictly_contained(tmp_path, tmp_path, request_id="req-same")
        assert exc_info.value.diagnostic.code == "INTERNAL_ERROR"
        assert "identical to root" in exc_info.value.diagnostic.message

    def test_child_escaping_root_rejected(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / "escape.txt"
        with pytest.raises(BatchSafetyRejectionError) as exc_info:
            assert_strictly_contained(outside, tmp_path, request_id="req-esc")
        assert exc_info.value.diagnostic.code == "INTERNAL_ERROR"
        assert "escapes" in exc_info.value.diagnostic.message
