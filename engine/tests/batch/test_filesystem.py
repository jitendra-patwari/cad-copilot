"""Tests for filesystem identity, reparse checks, device names, control chars, and component walking."""

from __future__ import annotations

import os
import stat
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from batch.allocation import BatchSafetyRejectionError
from batch.filesystem import (
    PathIdentity,
    PathIdentityError,
    PathNotFoundError,
    PathPermissionError,
    get_path_identity,
    is_reserved_device_name,
    is_symlink_or_reparse_point,
    validate_relative_segment,
    walk_and_verify_components,
)


class TestPathIdentity:
    """Tests for PathIdentity immutable dataclass and factory."""

    def test_identity_attributes_and_immutability(self, tmp_path: Path) -> None:
        file_path = tmp_path / "sample.txt"
        file_path.write_text("hello", encoding="utf-8")

        identity = get_path_identity(file_path)
        assert isinstance(identity.device, int)
        assert isinstance(identity.inode, int)
        assert isinstance(identity.mode, int)
        assert identity.is_regular_file is True
        assert identity.is_directory is False

        dir_identity = get_path_identity(tmp_path)
        assert dir_identity.is_regular_file is False
        assert dir_identity.is_directory is True

        with pytest.raises(FrozenInstanceError):
            identity.inode = 999  # type: ignore[misc]

    def test_identity_validation(self) -> None:
        with pytest.raises(TypeError, match="device must be int"):
            PathIdentity(device="bad", inode=1, mode=0o100644)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="inode must be int"):
            PathIdentity(device=1, inode="bad", mode=0o100644)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="mode must be int"):
            PathIdentity(device=1, inode=1, mode="bad")  # type: ignore[arg-type]

    def test_get_path_identity_missing_file_raises_typed_not_found(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist.txt"
        with pytest.raises(PathNotFoundError) as exc_info:
            get_path_identity(missing)
        assert exc_info.value.reason == "not_found"
        assert isinstance(exc_info.value, FileNotFoundError)
        assert isinstance(exc_info.value, PathIdentityError)

    def test_get_path_identity_permission_denied_raises_typed_permission_error(self, tmp_path: Path) -> None:
        file_path = tmp_path / "denied.txt"
        file_path.write_text("data", encoding="utf-8")
        with patch.object(Path, "lstat", side_effect=PermissionError("Permission denied")):
            with pytest.raises(PathPermissionError) as exc_info:
                get_path_identity(file_path)
            assert exc_info.value.reason == "permission_denied"
            assert isinstance(exc_info.value, PermissionError)
            assert isinstance(exc_info.value, PathIdentityError)

    def test_get_path_identity_generic_os_error_raises_typed_identity_error(self, tmp_path: Path) -> None:
        file_path = tmp_path / "os_err.txt"
        file_path.write_text("data", encoding="utf-8")
        with patch.object(Path, "lstat", side_effect=OSError("Hardware I/O error")):
            with pytest.raises(PathIdentityError) as exc_info:
                get_path_identity(file_path)
            assert exc_info.value.reason == "io_error"

    def test_hard_link_identity_equality(self, tmp_path: Path) -> None:
        src = tmp_path / "original.par"
        src.write_bytes(b"SOLID_EDGE_PART_BYTES")
        dst = tmp_path / "linked.par"
        try:
            os.link(src, dst)
        except OSError, NotImplementedError, AttributeError:
            pytest.skip("Hard links not supported or permitted in this environment")

        id_src = get_path_identity(src)
        id_dst = get_path_identity(dst)
        assert id_src == id_dst
        assert id_src.device == id_dst.device
        assert id_src.inode == id_dst.inode
        assert id_src.mode == id_dst.mode


class TestReparseAndSymlinkChecks:
    """Tests for is_symlink_or_reparse_point."""

    def test_normal_file_and_dir_not_reparse(self, tmp_path: Path) -> None:
        f = tmp_path / "file.txt"
        f.write_text("content", encoding="utf-8")
        assert is_symlink_or_reparse_point(f) is False
        assert is_symlink_or_reparse_point(tmp_path) is False

    def test_missing_path_returns_false(self, tmp_path: Path) -> None:
        missing = tmp_path / "non_existent.txt"
        assert is_symlink_or_reparse_point(missing) is False

    def test_permission_or_os_error_fails_closed(self, tmp_path: Path) -> None:
        f = tmp_path / "file.txt"
        f.write_text("content", encoding="utf-8")
        with patch.object(Path, "lstat", side_effect=PermissionError("denied")):
            assert is_symlink_or_reparse_point(f) is True

    def test_symlink_detected(self, tmp_path: Path) -> None:
        target = tmp_path / "target.txt"
        target.write_text("data", encoding="utf-8")
        link = tmp_path / "link.txt"
        try:
            link.symlink_to(target)
            assert is_symlink_or_reparse_point(link) is True
        except OSError, NotImplementedError:
            pytest.skip("Symlink creation not permitted in this environment")

    def test_windows_reparse_attribute_detected(self, tmp_path: Path) -> None:
        f = tmp_path / "file.txt"
        f.write_text("content", encoding="utf-8")
        mock_stat = MagicMock()
        mock_stat.st_mode = stat.S_IFREG | 0o644
        mock_stat.st_file_attributes = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT
        mock_stat.st_reparse_tag = 0

        with (
            patch("sys.platform", "win32"),
            patch.object(Path, "lstat", return_value=mock_stat),
        ):
            assert is_symlink_or_reparse_point(f) is True

    def test_windows_reparse_tag_detected(self, tmp_path: Path) -> None:
        f = tmp_path / "file.txt"
        f.write_text("content", encoding="utf-8")
        mock_stat = MagicMock()
        mock_stat.st_mode = stat.S_IFREG | 0o644
        mock_stat.st_file_attributes = 0
        mock_stat.st_reparse_tag = 0xA000000C  # IO_REPARSE_TAG_SYMLINK

        with (
            patch("sys.platform", "win32"),
            patch.object(Path, "lstat", return_value=mock_stat),
        ):
            assert is_symlink_or_reparse_point(f) is True


class TestReservedDeviceNames:
    """Tests for is_reserved_device_name and segment validation."""

    @pytest.mark.parametrize(
        "name",
        [
            "CON",
            "con",
            "PRN",
            "prn",
            "AUX",
            "aux",
            "NUL",
            "nul",
            "COM1",
            "com1",
            "COM9",
            "LPT1",
            "lpt1",
            "LPT9",
            "con.par",
            "CON.PAR",
            "nul.step",
            "com1.dft",
            "aux.stl",
            "prn.asm",
        ],
    )
    def test_reserved_names_identified(self, name: str) -> None:
        assert is_reserved_device_name(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "content.par",
            "printer.psm",
            "auxiliary.par",
            "null_part.par",
            "com10.step",
            "lpt0.step",
            "valid_model.par",
            "",
        ],
    )
    def test_non_reserved_names_identified(self, name: str) -> None:
        assert is_reserved_device_name(name) is False


class TestRelativeSegmentValidation:
    """Tests for validate_relative_segment."""

    @pytest.mark.parametrize("valid", ["part.par", "sub_folder", "Nested-123", "model.psm"])
    def test_valid_segments(self, valid: str) -> None:
        validate_relative_segment(valid)

    @pytest.mark.parametrize(
        ("invalid", "match_msg"),
        [
            ("", "forbidden traversal or empty segment"),
            (".", "forbidden traversal or empty segment"),
            ("..", "forbidden traversal or empty segment"),
            (" part", "leading or trailing whitespace"),
            ("part ", "leading or trailing whitespace"),
            ("part.", "trailing dots"),
            ("part..", "trailing dots"),
            ("part:stream", "forbidden characters or stream colons"),
            ("part*1", "forbidden characters or stream colons"),
            ("part?1", "forbidden characters or stream colons"),
            ('part"1', "forbidden characters or stream colons"),
            ("part<1", "forbidden characters or stream colons"),
            ("part>1", "forbidden characters or stream colons"),
            ("part|1", "forbidden characters or stream colons"),
            ("part\0name", "forbidden control characters"),
            ("part\x01name", "forbidden control characters"),
            ("part\tname", "forbidden control characters"),
            ("part\nname", "forbidden control characters"),
            ("part\rname", "forbidden control characters"),
            ("part\x1fname", "forbidden control characters"),
            ("con.par", "reserved Windows device name"),
            ("NUL", "reserved Windows device name"),
            ("aux.step", "reserved Windows device name"),
        ],
    )
    def test_invalid_segments_rejected(self, invalid: str, match_msg: str) -> None:
        with pytest.raises(BatchSafetyRejectionError) as exc_info:
            validate_relative_segment(invalid, request_id="req-seg")
        assert exc_info.value.diagnostic.code == "INPUT_PATH_NOT_ALLOWED"
        assert match_msg in exc_info.value.diagnostic.message
        assert exc_info.value.request_id == "req-seg"


class TestWalkAndVerifyComponents:
    """Tests for walk_and_verify_components."""

    def test_walk_valid_components(self, tmp_path: Path) -> None:
        sub = tmp_path / "a" / "b"
        sub.mkdir(parents=True)
        target = sub / "part.par"
        target.write_text("data", encoding="utf-8")

        result = walk_and_verify_components(tmp_path, "a/b/part.par")
        assert result == target

    @pytest.mark.parametrize(
        ("bad_input", "match_msg"),
        [
            ("/escape/part.par", "cannot be an absolute path"),
            ("\\escape\\part.par", "cannot be an absolute path"),
            ("sub\\part.par", "must use canonical POSIX forward slashes"),
            ("sub//part.par", "empty components or trailing slash"),
            ("sub/part.par/", "empty components or trailing slash"),
            (" sub/part.par ", "leading or trailing whitespace"),
            ("sub/part.par ", "leading or trailing whitespace"),
            ("sub/part:stream", "stream colons"),
            ("sub/part\t.par", "forbidden control characters"),
            ("sub/part\n.par", "forbidden control characters"),
            ("sub/part\x01.par", "forbidden control characters"),
            ("", "cannot be empty"),
        ],
    )
    def test_walk_rejects_non_canonical_relative_syntax(self, tmp_path: Path, bad_input: str, match_msg: str) -> None:
        with pytest.raises(BatchSafetyRejectionError) as exc_info:
            walk_and_verify_components(tmp_path, bad_input)
        assert exc_info.value.diagnostic.code == "INPUT_PATH_NOT_ALLOWED"
        assert match_msg in exc_info.value.diagnostic.message

    def test_walk_rejects_forbidden_segment(self, tmp_path: Path) -> None:
        with pytest.raises(BatchSafetyRejectionError) as exc_info:
            walk_and_verify_components(tmp_path, "a/../part.par")
        assert exc_info.value.diagnostic.code == "INPUT_PATH_NOT_ALLOWED"

    def test_walk_rejects_reparse_component_with_typed_error(self, tmp_path: Path) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        part = sub / "part.par"
        part.write_text("cad", encoding="utf-8")

        with patch("batch.filesystem.is_symlink_or_reparse_point", side_effect=lambda p: p.name == "sub"):
            with pytest.raises(BatchSafetyRejectionError) as exc_info:
                walk_and_verify_components(tmp_path, "sub/part.par")
            assert exc_info.value.diagnostic.code == "INPUT_PATH_NOT_ALLOWED"
            assert "symbolic link or reparse point" in exc_info.value.diagnostic.message

    def test_walk_metadata_failure_fails_closed(self, tmp_path: Path) -> None:
        sub = tmp_path / "inaccessible"
        sub.mkdir()
        part = sub / "part.par"
        part.write_text("cad", encoding="utf-8")

        # Simulate permission error or inaccessible metadata on lstat
        with patch.object(Path, "lstat", side_effect=PermissionError("denied")):
            with pytest.raises(BatchSafetyRejectionError) as exc_info:
                walk_and_verify_components(tmp_path, "inaccessible/part.par")
            assert exc_info.value.diagnostic.code == "INPUT_PATH_NOT_ALLOWED"
            assert "symbolic link or reparse point" in exc_info.value.diagnostic.message
