"""Comprehensive tests for safe paths, staging allocation, and guarded containment."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from artifacts.paths import (
    MAX_REQUEST_ID_LENGTH,
    MIN_REQUEST_ID_LENGTH,
    REQUEST_ID_PATTERN,
    REQUEST_ID_REGEX,
    STAGING_PREFIX,
    WINDOWS_RESERVED_NAMES,
    ArtifactPathError,
    allocate_staging_dir,
    assert_contained,
    check_target_collisions,
    cleanup_staging_dir,
    cleanup_staging_file,
    get_path_identity,
    is_symlink_or_reparse_point,
    prepare_artifact_paths,
    publish_staging_dir,
    resolve_output_root,
    validate_request_id,
)
from interfaces.exceptions import CADError

# ===========================================================================
# 0. Module Exports, Constants & Taxonomy Tests
# ===========================================================================


class TestModuleExportsAndConstants:
    """Test module-level constants, exports, and exception taxonomy."""

    def test_constants_and_types(self) -> None:
        assert issubclass(ArtifactPathError, CADError)
        assert MIN_REQUEST_ID_LENGTH == 1
        assert MAX_REQUEST_ID_LENGTH == 96
        assert REQUEST_ID_PATTERN == r"^[A-Za-z0-9._-]+$"
        assert REQUEST_ID_REGEX.pattern == REQUEST_ID_PATTERN
        assert "CON" in WINDOWS_RESERVED_NAMES
        assert "NUL" in WINDOWS_RESERVED_NAMES
        assert "COM1" in WINDOWS_RESERVED_NAMES
        assert "LPT9" in WINDOWS_RESERVED_NAMES

    def test_is_symlink_or_reparse_point_helper(self, tmp_path: Path) -> None:
        file_path = tmp_path / "regular.txt"
        file_path.write_text("content", encoding="utf-8")
        assert not is_symlink_or_reparse_point(file_path)
        assert not is_symlink_or_reparse_point(tmp_path)
        assert not is_symlink_or_reparse_point(tmp_path / "nonexistent")


# ===========================================================================
# 1. Request ID Validation & Hardening Tests
# ===========================================================================


class TestRequestIdValidation:
    """Test suite for canonical request ID format, bounds, and reserved device names."""

    @pytest.mark.parametrize(
        "valid_id",
        [
            "job-001",
            "req_123",
            "part.model-A",
            "12345",
            "a",
            "A",
            "Z_0-9.test",
            "a" * MAX_REQUEST_ID_LENGTH,
            "continuous-gear",
            "nullify_operation",
            "auxiliary_bracket",
            "connect_pin",
            "com10_port",
            "lpt10_bus",
        ],
    )
    def test_validate_request_id_valid_cases(self, valid_id: str) -> None:
        assert validate_request_id(valid_id) == valid_id

    @pytest.mark.parametrize(
        "invalid_type",
        [None, 123, 45.6, [], {}, True],
    )
    def test_validate_request_id_rejects_non_string(self, invalid_type: object) -> None:
        with pytest.raises(ArtifactPathError) as exc_info:
            validate_request_id(invalid_type)  # type: ignore[arg-type]
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
        assert "must be a string" in str(exc_info.value)

    def test_validate_request_id_rejects_empty(self) -> None:
        with pytest.raises(ArtifactPathError) as exc_info:
            validate_request_id("")
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
        assert "between 1 and 96" in str(exc_info.value)

    def test_validate_request_id_rejects_excessive_length(self) -> None:
        long_id = "a" * (MAX_REQUEST_ID_LENGTH + 1)
        with pytest.raises(ArtifactPathError) as exc_info:
            validate_request_id(long_id)
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
        assert "between 1 and 96" in str(exc_info.value)

    @pytest.mark.parametrize(
        "traversal_token",
        [".", ".."],
    )
    def test_validate_request_id_rejects_path_traversal_tokens(self, traversal_token: str) -> None:
        with pytest.raises(ArtifactPathError) as exc_info:
            validate_request_id(traversal_token)
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
        assert "reserved path traversal token" in str(exc_info.value)

    @pytest.mark.parametrize(
        "trailing_dot_id",
        ["job.", "req..", "part.model.", "..."],
    )
    def test_validate_request_id_rejects_trailing_dots(self, trailing_dot_id: str) -> None:
        with pytest.raises(ArtifactPathError) as exc_info:
            validate_request_id(trailing_dot_id)
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
        assert "must not end with a dot" in str(exc_info.value)

    @pytest.mark.parametrize(
        "whitespace_id",
        [" job", "job ", "job 001", "job\t001", "job\n", "\rjob"],
    )
    def test_validate_request_id_rejects_whitespace(self, whitespace_id: str) -> None:
        with pytest.raises(ArtifactPathError) as exc_info:
            validate_request_id(whitespace_id)
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"

    @pytest.mark.parametrize(
        "illegal_char_id",
        [
            "job/001",
            "job\\001",
            "job:001",
            "job*001",
            "job?001",
            'job"001',
            "job<001",
            "job>001",
            "job|001",
            "job$001",
            "job@001",
            "job#001",
            "job!001",
            "job;001",
        ],
    )
    def test_validate_request_id_rejects_illegal_characters(self, illegal_char_id: str) -> None:
        with pytest.raises(ArtifactPathError) as exc_info:
            validate_request_id(illegal_char_id)
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
        assert "invalid characters" in str(exc_info.value)

    @pytest.mark.parametrize(
        "reserved_name",
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
            "COM5",
            "com9",
            "LPT1",
            "lpt1",
            "LPT5",
            "lpt9",
            "con.par",
            "CON.txt",
            "nul.step",
            "NUL.stl",
            "aux.jpg",
            "prn.json",
            "com1.step",
            "lpt2.par",
        ],
    )
    def test_validate_request_id_rejects_windows_reserved_names(self, reserved_name: str) -> None:
        with pytest.raises(ArtifactPathError) as exc_info:
            validate_request_id(reserved_name)
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
        assert "reserved Windows device name" in str(exc_info.value)

    @pytest.mark.parametrize(
        "valid_suffix_name",
        [
            "job.con",
            "job.nul.001",
            "gear_con_01",
            "job.lpt1.sub",
        ],
    )
    def test_validate_request_id_accepts_valid_device_suffixes(self, valid_suffix_name: str) -> None:
        assert validate_request_id(valid_suffix_name) == valid_suffix_name


# ===========================================================================
# 2. Output Root Resolution & Containment Tests
# ===========================================================================


class TestOutputRootResolution:
    """Test suite for output root resolution, creation, and containment verification."""

    def test_resolve_output_root_existing_directory(self, tmp_path: Path) -> None:
        root = tmp_path / "cad_output"
        root.mkdir()
        resolved = resolve_output_root(root)
        assert resolved == root.resolve()
        assert resolved.is_dir()

    def test_resolve_output_root_rejects_empty_string(self) -> None:
        with pytest.raises(ArtifactPathError) as exc_info:
            resolve_output_root("   ")
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
        assert "cannot be empty" in str(exc_info.value)

    def test_resolve_output_root_non_existing_without_create(self, tmp_path: Path) -> None:
        root = tmp_path / "cad_output_nonexistent"
        resolved = resolve_output_root(root, create_if_missing=False)
        assert resolved == root.resolve()
        assert not resolved.exists()

    def test_resolve_output_root_non_existing_with_create(self, tmp_path: Path) -> None:
        root = tmp_path / "nested" / "deep" / "cad_output"
        assert not root.exists()
        resolved = resolve_output_root(root, create_if_missing=True)
        assert resolved.is_dir()
        assert resolved.exists()

    def test_resolve_output_root_rejects_existing_regular_file(self, tmp_path: Path) -> None:
        file_path = tmp_path / "not_a_dir.txt"
        file_path.write_text("hello", encoding="utf-8")
        with pytest.raises(ArtifactPathError) as exc_info:
            resolve_output_root(file_path)
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
        assert "exists and is not a directory" in str(exc_info.value)

    def test_resolve_output_root_rejects_file_in_parent_chain(self, tmp_path: Path) -> None:
        file_path = tmp_path / "blocking_file.txt"
        file_path.write_text("blocking", encoding="utf-8")
        child_path = file_path / "child_dir"
        with pytest.raises(ArtifactPathError) as exc_info:
            resolve_output_root(child_path, create_if_missing=True)
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
        assert "not a safe directory" in str(exc_info.value)

    def test_assert_contained_passes_for_strictly_contained_child(self, tmp_path: Path) -> None:
        child = tmp_path / "sub" / "file.txt"
        assert_contained(child, tmp_path)

    def test_assert_contained_rejects_identical_path(self, tmp_path: Path) -> None:
        with pytest.raises(ArtifactPathError) as exc_info:
            assert_contained(tmp_path, tmp_path)
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
        assert "cannot be identical" in str(exc_info.value)

    def test_assert_contained_rejects_parent_of_parent(self, tmp_path: Path) -> None:
        child = tmp_path.parent
        with pytest.raises(ArtifactPathError) as exc_info:
            assert_contained(child, tmp_path)
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
        assert "escapes output root" in str(exc_info.value)

    def test_assert_contained_rejects_lexical_traversal_escape(self, tmp_path: Path) -> None:
        child = tmp_path / ".." / "escaped_folder"
        with pytest.raises(ArtifactPathError) as exc_info:
            assert_contained(child, tmp_path)
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
        assert "escapes output root" in str(exc_info.value)


# ===========================================================================
# 3. Collision Preflight Tests
# ===========================================================================


class TestCollisionChecking:
    """Test suite for existing target and directory collision detection."""

    def test_check_target_collisions_passes_when_nothing_exists(self, tmp_path: Path) -> None:
        final_dir = tmp_path / "job-001"
        targets = [final_dir / "job-001.par", final_dir / "job-001.step"]
        check_target_collisions(final_dir, targets)

    def test_check_target_collisions_rejects_existing_final_dir(self, tmp_path: Path) -> None:
        final_dir = tmp_path / "job-001"
        final_dir.mkdir()
        with pytest.raises(ArtifactPathError) as exc_info:
            check_target_collisions(final_dir)
        assert exc_info.value.error_code == "TARGET_ALREADY_EXISTS"
        assert "Target directory already exists" in str(exc_info.value)
        assert exc_info.value.details.get("target") == "job-001"

    def test_check_target_collisions_rejects_existing_candidate_file(self, tmp_path: Path) -> None:
        final_dir = tmp_path / "job-001"
        target_file = tmp_path / "preexisting.par"
        target_file.write_bytes(b"dummy")
        with pytest.raises(ArtifactPathError) as exc_info:
            check_target_collisions(final_dir, [target_file])
        assert exc_info.value.error_code == "TARGET_ALREADY_EXISTS"
        assert "Target artifact already exists" in str(exc_info.value)
        assert exc_info.value.details.get("target") == "preexisting.par"


# ===========================================================================
# 4. Staging Allocation Tests
# ===========================================================================


class TestStagingAllocation:
    """Test suite for exclusive staging directory creation."""

    def test_allocate_staging_dir_creates_unique_directory(self, tmp_path: Path) -> None:
        staging_dir = allocate_staging_dir(tmp_path, "job-001")
        assert staging_dir.is_dir()
        assert staging_dir.exists()
        assert staging_dir.name.startswith(f"{STAGING_PREFIX}job-001-")
        assert len(staging_dir.name) == len(f"{STAGING_PREFIX}job-001-") + 12

    def test_allocate_staging_dir_creates_multiple_unique_dirs(self, tmp_path: Path) -> None:
        s1 = allocate_staging_dir(tmp_path, "job-001")
        s2 = allocate_staging_dir(tmp_path, "job-001")
        assert s1 != s2
        assert s1.exists() and s2.exists()

    def test_allocate_staging_dir_retries_on_collision(self, tmp_path: Path) -> None:
        call_count = 0
        original_mkdir = os.mkdir

        def mock_mkdir(path: os.PathLike[str] | str, *args: Any, **kwargs: Any) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise FileExistsError("Simulated collision")
            original_mkdir(path, *args, **kwargs)

        with patch("artifacts.paths.os.mkdir", side_effect=mock_mkdir):
            staging_dir = allocate_staging_dir(tmp_path, "job-001")
            assert call_count == 2
            assert staging_dir.is_dir()

    def test_allocate_staging_dir_exhausts_attempts(self, tmp_path: Path) -> None:
        with patch("artifacts.paths.os.mkdir", side_effect=FileExistsError("Continuous collision")):
            with pytest.raises(ArtifactPathError) as exc_info:
                allocate_staging_dir(tmp_path, "job-001", max_attempts=3)
            assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
            assert "Failed to allocate exclusive staging directory after 3 attempts" in str(exc_info.value)


# ===========================================================================
# 5. Atomic Publication Tests
# ===========================================================================


class TestAtomicPublication:
    """Test suite for atomic publication of validated staging directory to final directory."""

    def test_publish_staging_dir_success(self, tmp_path: Path) -> None:
        root = tmp_path / "outputs"
        paths = prepare_artifact_paths(root, "job-publish-001")

        # Simulate export writes into staging
        paths.staging_par.write_bytes(b"par_data")
        paths.staging_step.write_bytes(b"step_data")
        paths.staging_stl.write_bytes(b"stl_data")
        paths.staging_jpg.write_bytes(b"jpg_data")

        # Publish
        paths.publish()

        # Invariants:
        assert paths.final_dir.is_dir()
        assert not paths.staging_dir.exists()
        assert paths.final_par.read_bytes() == b"par_data"
        assert paths.final_step.read_bytes() == b"step_data"
        assert paths.final_stl.read_bytes() == b"stl_data"
        assert paths.final_jpg.read_bytes() == b"jpg_data"

    def test_publish_staging_dir_rejects_preexisting_destination(self, tmp_path: Path) -> None:
        root = tmp_path / "outputs"
        paths = prepare_artifact_paths(root, "job-preexist")
        # Create final_dir behind its back before publish
        paths.final_dir.mkdir(parents=True)
        with pytest.raises(ArtifactPathError) as exc_info:
            paths.publish()
        assert exc_info.value.error_code == "TARGET_ALREADY_EXISTS"
        assert "Target directory already exists" in str(exc_info.value)
        # Staging is preserved
        assert paths.staging_dir.exists()

    def test_publish_staging_dir_handles_destination_race(self, tmp_path: Path) -> None:
        root = tmp_path / "outputs"
        paths = prepare_artifact_paths(root, "job-race")

        # Simulate rename failing with FileExistsError (Windows ERROR_ALREADY_EXISTS)
        with patch.object(Path, "rename", side_effect=FileExistsError("Simulated race collision")):
            with pytest.raises(ArtifactPathError) as exc_info:
                paths.publish()
            assert exc_info.value.error_code == "TARGET_ALREADY_EXISTS"

    def test_publish_staging_dir_normalizes_winerror_183(self, tmp_path: Path) -> None:
        root = tmp_path / "outputs"
        paths = prepare_artifact_paths(root, "job-winerror")

        os_err = OSError("Cannot create a file when that file already exists")
        os_err.winerror = 183

        with patch.object(Path, "rename", side_effect=os_err):
            with pytest.raises(ArtifactPathError) as exc_info:
                paths.publish()
            assert exc_info.value.error_code == "TARGET_ALREADY_EXISTS"

    def test_publish_staging_dir_raises_on_missing_staging_dir(self, tmp_path: Path) -> None:
        staging_dir = tmp_path / f"{STAGING_PREFIX}job-missing-1234567890ab"
        final_dir = tmp_path / "job-missing"
        root_id = get_path_identity(tmp_path)
        with pytest.raises(ArtifactPathError) as exc_info:
            publish_staging_dir(
                staging_dir,
                final_dir,
                tmp_path,
                expected_staging_identity=(1, 2),
                expected_root_identity=root_id,
            )
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
        assert "does not exist or is not a directory" in str(exc_info.value)

    def test_publish_staging_dir_non_collision_io_error_raises_artifact_export_failed(self, tmp_path: Path) -> None:
        root = tmp_path / "outputs"
        paths = prepare_artifact_paths(root, "job-io-fail")

        with patch.object(Path, "rename", side_effect=OSError("Disk write fault")):
            with pytest.raises(ArtifactPathError) as exc_info:
                paths.publish()
            assert exc_info.value.error_code == "ARTIFACT_EXPORT_FAILED"
            assert "Failed to publish staging directory" in str(exc_info.value)
            assert "OSError" in str(exc_info.value)


# ===========================================================================
# 6. Guarded Staging & File Cleanup Tests
# ===========================================================================


class TestGuardedCleanup:
    """Test suite for guarded cleanup of staging directories and partial files."""

    def test_cleanup_staging_dir_refuses_when_document_not_closed(self, tmp_path: Path) -> None:
        staging_dir = tmp_path / f"{STAGING_PREFIX}job-001-1234567890ab"
        staging_dir.mkdir()
        root_id = get_path_identity(tmp_path)
        staging_id = get_path_identity(staging_dir)
        with pytest.raises(ArtifactPathError) as exc_info:
            cleanup_staging_dir(
                staging_dir,
                tmp_path,
                "job-001",
                document_closed=False,
                expected_staging_identity=staging_id,
                expected_root_identity=root_id,
            )
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
        assert "document closure has not been confirmed" in str(exc_info.value)
        assert staging_dir.exists()

    def test_cleanup_staging_dir_success_when_document_closed(self, tmp_path: Path) -> None:
        staging_dir = tmp_path / f"{STAGING_PREFIX}job-001-1234567890ab"
        staging_dir.mkdir()
        root_id = get_path_identity(tmp_path)
        staging_id = get_path_identity(staging_dir)
        (staging_dir / "temp.par").write_bytes(b"temp")
        cleanup_staging_dir(
            staging_dir,
            tmp_path,
            "job-001",
            document_closed=True,
            expected_staging_identity=staging_id,
            expected_root_identity=root_id,
        )
        assert not staging_dir.exists()

    def test_cleanup_staging_dir_refuses_output_root(self, tmp_path: Path) -> None:
        root_id = get_path_identity(tmp_path)
        with pytest.raises(ArtifactPathError) as exc_info:
            cleanup_staging_dir(
                tmp_path,
                tmp_path,
                "job-001",
                document_closed=True,
                expected_staging_identity=root_id,
                expected_root_identity=root_id,
            )
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
        assert "not strictly beneath output root" in str(exc_info.value)

    def test_cleanup_staging_dir_refuses_final_dir(self, tmp_path: Path) -> None:
        final_dir = tmp_path / "job-001"
        final_dir.mkdir()
        root_id = get_path_identity(tmp_path)
        final_id = get_path_identity(final_dir)
        with pytest.raises(ArtifactPathError) as exc_info:
            cleanup_staging_dir(
                final_dir,
                tmp_path,
                "job-001",
                document_closed=True,
                expected_staging_identity=final_id,
                expected_root_identity=root_id,
            )
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
        assert "does not match expected staging prefix" in str(exc_info.value)

    def test_cleanup_staging_dir_refuses_external_path(self, tmp_path: Path) -> None:
        root1 = tmp_path / "root1"
        root2 = tmp_path / "root2"
        root1.mkdir()
        root2.mkdir()
        staging_in_root2 = root2 / f"{STAGING_PREFIX}job-001-1234567890ab"
        staging_in_root2.mkdir()
        root1_id = get_path_identity(root1)
        staging_id = get_path_identity(staging_in_root2)
        with pytest.raises(ArtifactPathError) as exc_info:
            cleanup_staging_dir(
                staging_in_root2,
                root1,
                "job-001",
                document_closed=True,
                expected_staging_identity=staging_id,
                expected_root_identity=root1_id,
            )
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
        assert "not strictly beneath output root" in str(exc_info.value)

    def test_cleanup_staging_dir_refuses_unrelated_prefix(self, tmp_path: Path) -> None:
        unrelated = tmp_path / "regular_folder"
        unrelated.mkdir()
        root_id = get_path_identity(tmp_path)
        unrelated_id = get_path_identity(unrelated)
        with pytest.raises(ArtifactPathError) as exc_info:
            cleanup_staging_dir(
                unrelated,
                tmp_path,
                document_closed=True,
                expected_staging_identity=unrelated_id,
                expected_root_identity=root_id,
            )
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
        assert "does not match staging prefix" in str(exc_info.value)

    def test_cleanup_staging_dir_refuses_mismatched_request_id(self, tmp_path: Path) -> None:
        staging_dir = tmp_path / f"{STAGING_PREFIX}job-OTHER-1234567890ab"
        staging_dir.mkdir()
        root_id = get_path_identity(tmp_path)
        staging_id = get_path_identity(staging_dir)
        with pytest.raises(ArtifactPathError) as exc_info:
            cleanup_staging_dir(
                staging_dir,
                tmp_path,
                "job-001",
                document_closed=True,
                expected_staging_identity=staging_id,
                expected_root_identity=root_id,
            )
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
        assert "does not match expected staging prefix" in str(exc_info.value)

    def test_cleanup_staging_dir_noop_when_dir_does_not_exist(self, tmp_path: Path) -> None:
        nonexistent = tmp_path / f"{STAGING_PREFIX}job-001-1234567890ab"
        root_id = get_path_identity(tmp_path)
        # Must not raise error
        cleanup_staging_dir(
            nonexistent,
            tmp_path,
            "job-001",
            document_closed=True,
            expected_staging_identity=(1, 2),
            expected_root_identity=root_id,
        )

    def test_cleanup_staging_dir_injected_oserror(self, tmp_path: Path) -> None:
        staging_dir = tmp_path / f"{STAGING_PREFIX}job-001-1234567890ab"
        staging_dir.mkdir()
        root_id = get_path_identity(tmp_path)
        staging_id = get_path_identity(staging_dir)
        with patch("artifacts.paths.shutil.rmtree", side_effect=PermissionError("Locked file")):
            with pytest.raises(ArtifactPathError) as exc_info:
                cleanup_staging_dir(
                    staging_dir,
                    tmp_path,
                    "job-001",
                    document_closed=True,
                    expected_staging_identity=staging_id,
                    expected_root_identity=root_id,
                )
            assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
            assert "Failed to remove staging directory" in str(exc_info.value)

    def test_cleanup_staging_file_refuses_when_document_not_closed(self, tmp_path: Path) -> None:
        staging_dir = tmp_path / f"{STAGING_PREFIX}job-001-1234567890ab"
        staging_dir.mkdir()
        partial_jpg = staging_dir / "job-001.jpg"
        partial_jpg.write_bytes(b"corrupted")
        root_id = get_path_identity(tmp_path)
        staging_id = get_path_identity(staging_dir)
        with pytest.raises(ArtifactPathError) as exc_info:
            cleanup_staging_file(
                partial_jpg,
                staging_dir,
                tmp_path,
                document_closed=False,
                expected_staging_identity=staging_id,
                expected_root_identity=root_id,
            )
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
        assert "document closure has not been confirmed" in str(exc_info.value)

    def test_cleanup_staging_file_success_when_document_closed(self, tmp_path: Path) -> None:
        staging_dir = tmp_path / f"{STAGING_PREFIX}job-001-1234567890ab"
        staging_dir.mkdir()
        partial_jpg = staging_dir / "job-001.jpg"
        partial_jpg.write_bytes(b"corrupted")
        root_id = get_path_identity(tmp_path)
        staging_id = get_path_identity(staging_dir)
        cleanup_staging_file(
            partial_jpg,
            staging_dir,
            tmp_path,
            document_closed=True,
            expected_staging_identity=staging_id,
            expected_root_identity=root_id,
        )
        assert not partial_jpg.exists()

    def test_cleanup_staging_file_refuses_file_outside_staging(self, tmp_path: Path) -> None:
        staging_dir = tmp_path / f"{STAGING_PREFIX}job-001-1234567890ab"
        staging_dir.mkdir()
        external_file = tmp_path / "outside.txt"
        external_file.write_bytes(b"data")
        root_id = get_path_identity(tmp_path)
        staging_id = get_path_identity(staging_dir)
        with pytest.raises(ArtifactPathError) as exc_info:
            cleanup_staging_file(
                external_file,
                staging_dir,
                tmp_path,
                document_closed=True,
                expected_staging_identity=staging_id,
                expected_root_identity=root_id,
            )
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
        assert "not contained within staging directory" in str(exc_info.value)


# ===========================================================================
# 7. ArtifactPaths Container & Factory Tests
# ===========================================================================


class TestArtifactPathsContainer:
    """Test suite for prepare_artifact_paths and ArtifactPaths helper methods."""

    def test_prepare_artifact_paths_full_flow(self, tmp_path: Path) -> None:
        paths = prepare_artifact_paths(tmp_path, "job-full-001")

        assert paths.request_id == "job-full-001"
        assert paths.output_root == tmp_path.resolve()
        assert paths.final_dir == tmp_path.resolve() / "job-full-001"
        assert paths.staging_dir.name.startswith(f"{STAGING_PREFIX}job-full-001-")

        # Staging paths
        assert paths.get_staging_path("par") == paths.staging_dir / "job-full-001.par"
        assert paths.get_staging_path("step") == paths.staging_dir / "job-full-001.step"
        assert paths.get_staging_path("stl") == paths.staging_dir / "job-full-001.stl"
        assert paths.get_staging_path("jpg") == paths.staging_dir / "job-full-001.jpg"

        # Final paths
        assert paths.get_final_path("par") == paths.final_dir / "job-full-001.par"
        assert paths.get_final_path("step") == paths.final_dir / "job-full-001.step"
        assert paths.get_final_path("stl") == paths.final_dir / "job-full-001.stl"
        assert paths.get_final_path("jpg") == paths.final_dir / "job-full-001.jpg"

        # Case-insensitivity & format variations
        assert paths.get_staging_path("PAR") == paths.staging_par
        assert paths.get_staging_path(".step") == paths.staging_step
        assert paths.get_final_path("STL ") == paths.final_stl
        assert paths.get_final_path(".JPG") == paths.final_jpg

    def test_prepare_artifact_paths_rejects_unsupported_format(self, tmp_path: Path) -> None:
        paths = prepare_artifact_paths(tmp_path, "job-fmt-test")
        with pytest.raises(ArtifactPathError, match="Unsupported artifact format"):
            paths.get_staging_path("dxf")
        with pytest.raises(ArtifactPathError, match="Unsupported artifact format"):
            paths.get_final_path("obj")

    def test_prepare_artifact_paths_preflight_collision_prevents_staging(self, tmp_path: Path) -> None:
        # If final_dir already exists, prepare_artifact_paths fails BEFORE staging is created
        final_dir = tmp_path / "job-colliding"
        final_dir.mkdir()

        with pytest.raises(ArtifactPathError) as exc_info:
            prepare_artifact_paths(tmp_path, "job-colliding")
        assert exc_info.value.error_code == "TARGET_ALREADY_EXISTS"

        # No staging dir created
        staging_dirs = list(tmp_path.glob(f"{STAGING_PREFIX}*"))
        assert len(staging_dirs) == 0

    def test_artifact_paths_cleanup_methods(self, tmp_path: Path) -> None:
        paths = prepare_artifact_paths(tmp_path, "job-clean-methods")
        paths.staging_jpg.write_bytes(b"bad_preview")

        # 1. Cleanup partial preview
        paths.cleanup_partial_preview(document_closed=True)
        assert not paths.staging_jpg.exists()
        assert paths.staging_dir.exists()

        # 2. Cleanup staging directory
        paths.cleanup_staging(document_closed=True)
        assert not paths.staging_dir.exists()

    def test_prepare_artifact_paths_manifest_properties(self, tmp_path: Path) -> None:
        paths = prepare_artifact_paths(tmp_path, "job-manifest-paths")
        assert paths.staging_manifest == paths.staging_dir / "run_manifest.json"
        assert paths.final_manifest == paths.final_dir / "run_manifest.json"
        assert paths.get_manifest_staging_path() == paths.staging_dir / "run_manifest.json"

    def test_get_manifest_staging_path_rejects_symlink(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = tmp_path / "outputs"
        paths = prepare_artifact_paths(root, "job-manifest-link")

        monkeypatch.setattr(
            "artifacts.paths.is_symlink_or_reparse_point",
            lambda p: p == paths.staging_manifest,
        )
        with pytest.raises(ArtifactPathError) as exc_info:
            paths.get_manifest_staging_path()
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
        assert "must not be a symbolic link or reparse point" in str(exc_info.value)

    def test_get_manifest_staging_path_rejects_containment_escape(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "outputs"
        paths = prepare_artifact_paths(root, "job-manifest-escape")

        escaped = tmp_path / "escaped_run_manifest.json"
        orig_resolve = Path.resolve

        def mock_resolve(self: Path, strict: bool = False) -> Path:
            if self == paths.staging_manifest:
                return escaped
            return orig_resolve(self, strict=strict)

        monkeypatch.setattr(Path, "resolve", mock_resolve)
        with pytest.raises(ArtifactPathError) as exc_info:
            paths.get_manifest_staging_path()
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"

    def test_check_collisions_includes_final_manifest(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        paths = prepare_artifact_paths(tmp_path, "job-col-man")

        def mock_is_symlink(p: Path) -> bool:
            return p == paths.final_manifest

        monkeypatch.setattr("artifacts.paths.is_symlink_or_reparse_point", mock_is_symlink)
        with pytest.raises(ArtifactPathError) as exc_info:
            paths.check_collisions()
        assert exc_info.value.error_code == "TARGET_ALREADY_EXISTS"
        assert exc_info.value.details.get("target") == "run_manifest.json"


# ===========================================================================
# 8. Sanitized Error Messaging (SEC-07)
# ===========================================================================


class TestErrorSanitization:
    """Prove that private workstation paths are never leaked in error messages."""

    def test_collision_error_sanitizes_absolute_paths(self, tmp_path: Path) -> None:
        final_dir = tmp_path / "secret-cad-job"
        final_dir.mkdir()

        with pytest.raises(ArtifactPathError) as exc_info:
            check_target_collisions(final_dir)

        msg = str(exc_info.value)
        # Message must include only basename, not full absolute path
        assert "secret-cad-job" in msg
        assert str(tmp_path) not in msg


# ===========================================================================
# 9. Broken Symlink / Reparse Point Hardening
# ===========================================================================


class TestBrokenSymlinkHardening:
    """Prove that broken (dangling) symlinks are caught and rejected."""

    def test_broken_symlink_detection(self, tmp_path: Path) -> None:
        broken_link = tmp_path / "broken_link"
        target = tmp_path / "nonexistent_target"
        try:
            os.symlink(target, broken_link)
        except OSError:
            pytest.skip("Symlink creation requires administrative privileges or Developer Mode on Windows")

        # Invariant: Path.exists() is False for broken links, but is_symlink_or_reparse_point detects them
        assert not broken_link.exists()
        assert is_symlink_or_reparse_point(broken_link)

        with pytest.raises(ArtifactPathError) as exc_info:
            assert_contained(broken_link, tmp_path)
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"

        with pytest.raises(ArtifactPathError) as exc_info:
            resolve_output_root(broken_link)
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"


# ===========================================================================
# 10. Identity Binding & Substitution Hardening (P1 / P2)
# ===========================================================================


class TestIdentityBindingAndSubstitutionHardening:
    """Prove that staging and output root are bound to filesystem identities and reject substitution."""

    def test_publish_rejects_substituted_staging_identity(self, tmp_path: Path) -> None:
        root = tmp_path / "outputs"
        paths = prepare_artifact_paths(root, "job-subst-pub")

        # Mock staging identity returning a different inode/dev (simulating directory substitution)
        fake_id = (999999, 888888)
        assert paths.staging_identity != fake_id

        with patch("artifacts.paths.get_path_identity", return_value=fake_id):
            with pytest.raises(ArtifactPathError) as exc_info:
                paths.publish()
            assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
            assert "identity mismatch" in str(exc_info.value)

    def test_cleanup_staging_rejects_substituted_staging_identity(self, tmp_path: Path) -> None:
        root = tmp_path / "outputs"
        paths = prepare_artifact_paths(root, "job-subst-clean")

        fake_id = (999999, 888888)
        with patch("artifacts.paths.get_path_identity", return_value=fake_id):
            with pytest.raises(ArtifactPathError) as exc_info:
                paths.cleanup_staging(document_closed=True)
            assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
            assert "identity mismatch" in str(exc_info.value)

    def test_cleanup_staging_file_rejects_substituted_staging_identity(self, tmp_path: Path) -> None:
        root = tmp_path / "outputs"
        paths = prepare_artifact_paths(root, "job-subst-file")
        paths.staging_jpg.write_bytes(b"content")

        fake_id = (999999, 888888)
        with patch("artifacts.paths.get_path_identity", return_value=fake_id):
            with pytest.raises(ArtifactPathError) as exc_info:
                paths.cleanup_partial_preview(document_closed=True)
            assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
            assert "identity mismatch" in str(exc_info.value)

    def test_cleanup_staging_dir_rejects_mocked_reparse_point(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "outputs"
        paths = prepare_artifact_paths(root, "job-reparse-clean")

        # Mock is_symlink_or_reparse_point returning True for staging_dir without needing OS privileges
        def mock_is_symlink(p: Path) -> bool:
            return p == paths.staging_dir

        monkeypatch.setattr("artifacts.paths.is_symlink_or_reparse_point", mock_is_symlink)
        with pytest.raises(ArtifactPathError) as exc_info:
            paths.cleanup_staging(document_closed=True)
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
        assert "symbolic link or reparse point" in str(exc_info.value)

    def test_cleanup_staging_file_rejects_mocked_reparse_point_parent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "outputs"
        paths = prepare_artifact_paths(root, "job-reparse-file")
        paths.staging_jpg.write_bytes(b"content")

        # Mock staging_dir as a reparse point
        def mock_is_symlink(p: Path) -> bool:
            return p == paths.staging_dir

        monkeypatch.setattr("artifacts.paths.is_symlink_or_reparse_point", mock_is_symlink)
        with pytest.raises(ArtifactPathError) as exc_info:
            paths.cleanup_partial_preview(document_closed=True)
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
        assert "staging directory" in str(exc_info.value)
        assert "symbolic link or reparse point" in str(exc_info.value)

    def test_is_symlink_or_reparse_point_fails_closed_on_permission_error(self, tmp_path: Path) -> None:
        target = tmp_path / "locked_file.txt"
        target.write_text("hello", encoding="utf-8")

        with patch.object(Path, "lstat", side_effect=PermissionError("Access Denied")):
            with pytest.raises(ArtifactPathError) as exc_info:
                is_symlink_or_reparse_point(target)
            assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
            assert "Cannot verify path metadata" in str(exc_info.value)

    def test_validate_request_id_sanitizes_rejection_messages(self) -> None:
        malicious_input = "C:\\Users\\SecretUser\\PrivateWorkstation\\job\nInjectedHeader: value"
        with pytest.raises(ArtifactPathError) as exc_info:
            validate_request_id(malicious_input)

        msg = str(exc_info.value)
        assert "SecretUser" not in msg
        assert "PrivateWorkstation" not in msg
        assert "\n" not in msg
        assert "InjectedHeader" not in msg

    def test_resolve_output_root_normalizes_null_bytes_and_value_errors(self, tmp_path: Path) -> None:
        with pytest.raises(ArtifactPathError) as exc_info:
            resolve_output_root(str(tmp_path) + "\x00bad")
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
        assert "null byte" in str(exc_info.value)

    def test_resolve_output_root_rejects_filesystem_root(self) -> None:
        # Drive root or filesystem root must be rejected
        root_cand = Path(Path.cwd().anchor)
        assert root_cand == root_cand.parent
        with pytest.raises(ArtifactPathError) as exc_info:
            resolve_output_root(root_cand)
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
        assert "cannot be filesystem or drive root" in str(exc_info.value)

    def test_resolve_output_root_sanitizes_unc_filesystem_root(self, monkeypatch: pytest.MonkeyPatch) -> None:
        unc_mock = Path(r"\\private-nas.corp.internal\secret-share")
        # Isolate fully from network access by intercepting link metadata checks and resolution
        monkeypatch.setattr("artifacts.paths.is_symlink_or_reparse_point", lambda p: False)
        monkeypatch.setattr(Path, "resolve", lambda self, strict=False: unc_mock)
        with pytest.raises(ArtifactPathError) as exc_info:
            resolve_output_root(r"\\private-nas.corp.internal\secret-share")
        msg = str(exc_info.value)
        assert "private-nas" not in msg
        assert "secret-share" not in msg
        assert "cannot be filesystem or drive root" in msg

    def test_check_target_collisions_rejects_mocked_symlink(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        final_dir = tmp_path / "mock_dir"

        def mock_is_symlink(p: Path) -> bool:
            return p == final_dir

        monkeypatch.setattr("artifacts.paths.is_symlink_or_reparse_point", mock_is_symlink)
        with pytest.raises(ArtifactPathError) as exc_info:
            check_target_collisions(final_dir)
        assert exc_info.value.error_code == "TARGET_ALREADY_EXISTS"

    def test_artifact_paths_get_path_unsupported_format_raises_artifact_path_error(self, tmp_path: Path) -> None:
        paths = prepare_artifact_paths(tmp_path, "job-fmt-test")
        with pytest.raises(ArtifactPathError) as exc_info:
            paths.get_staging_path("dwg")
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
        assert "Unsupported artifact format" in str(exc_info.value)

        with pytest.raises(ArtifactPathError) as exc_info:
            paths.get_final_path("dwg")
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
        assert "Unsupported artifact format" in str(exc_info.value)

    def test_get_staging_path_rechecks_ownership_and_rejects_substitution(self, tmp_path: Path) -> None:
        root = tmp_path / "outputs"
        paths = prepare_artifact_paths(root, "job-subst-get")

        # Mock staging identity mismatch
        fake_id = (999999, 888888)
        with patch("artifacts.paths.get_path_identity", return_value=fake_id):
            with pytest.raises(ArtifactPathError) as exc_info:
                paths.get_staging_path("par")
            assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
            assert "identity mismatch" in str(exc_info.value)

    def test_get_staging_path_rejects_mocked_staging_symlink(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "outputs"
        paths = prepare_artifact_paths(root, "job-link-get")

        def mock_is_symlink(p: Path) -> bool:
            return p == paths.staging_dir

        monkeypatch.setattr("artifacts.paths.is_symlink_or_reparse_point", mock_is_symlink)
        with pytest.raises(ArtifactPathError) as exc_info:
            paths.get_staging_path("par")
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
        assert "Staging directory" in str(exc_info.value)
        assert "symbolic link or reparse point" in str(exc_info.value)

    @pytest.mark.parametrize("format_id", ["par", "step", "stl", "jpg"])
    def test_get_staging_path_rejects_filename_level_symlink(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, format_id: str
    ) -> None:
        """Prove that get_staging_path rejects pre-existing symlinks for any artifact target before exporter write."""
        root = tmp_path / "outputs"
        paths = prepare_artifact_paths(root, "job-file-link")
        target_path = {
            "par": paths.staging_par,
            "step": paths.staging_step,
            "stl": paths.staging_stl,
            "jpg": paths.staging_jpg,
        }[format_id]

        def mock_is_symlink(p: Path) -> bool:
            return p == target_path

        monkeypatch.setattr("artifacts.paths.is_symlink_or_reparse_point", mock_is_symlink)
        with pytest.raises(ArtifactPathError) as exc_info:
            paths.get_staging_path(format_id)
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
        assert "must not be a symbolic link or reparse point" in str(exc_info.value)

    @pytest.mark.parametrize("format_id", ["par", "step", "stl", "jpg"])
    def test_get_staging_path_rejects_filename_level_external_resolution(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, format_id: str
    ) -> None:
        """Prove that get_staging_path rejects artifact targets whose resolution escapes staging."""
        root = tmp_path / "outputs"
        paths = prepare_artifact_paths(root, "job-file-escape")
        target_path = {
            "par": paths.staging_par,
            "step": paths.staging_step,
            "stl": paths.staging_stl,
            "jpg": paths.staging_jpg,
        }[format_id]

        escaped_dest = tmp_path / "outside_destination" / target_path.name
        orig_resolve = Path.resolve

        def mock_resolve(self: Path, strict: bool = False) -> Path:
            if self == target_path:
                return escaped_dest
            return orig_resolve(self, strict=strict)

        monkeypatch.setattr(Path, "resolve", mock_resolve)
        with pytest.raises(ArtifactPathError) as exc_info:
            paths.get_staging_path(format_id)
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
        assert "escapes" in str(exc_info.value) or "cannot be identical" in str(exc_info.value)

    def test_cleanup_partial_preview_rejects_substituted_root_or_root_junction(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "outputs"
        paths = prepare_artifact_paths(root, "job-root-clean")
        paths.staging_jpg.write_bytes(b"temp")

        # 1. Output root junction refusal
        def mock_is_symlink(p: Path) -> bool:
            return p == paths.output_root

        monkeypatch.setattr("artifacts.paths.is_symlink_or_reparse_point", mock_is_symlink)
        with pytest.raises(ArtifactPathError) as exc_info:
            paths.cleanup_partial_preview(document_closed=True)
        assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
        assert "output root" in str(exc_info.value)

        # 2. Output root identity mismatch
        monkeypatch.undo()
        fake_root_id = (777777, 666666)
        with patch("artifacts.paths.get_path_identity", side_effect=[paths.staging_identity, fake_root_id]):
            with pytest.raises(ArtifactPathError) as exc_info:
                paths.cleanup_partial_preview(document_closed=True)
            assert exc_info.value.error_code == "OUTPUT_PATH_NOT_ALLOWED"
            assert "output root" in str(exc_info.value)
            assert "identity mismatch" in str(exc_info.value)

    def test_prepare_artifact_paths_cleans_up_staging_on_post_allocation_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "outputs"
        root.mkdir()

        orig_assert = assert_contained

        def mock_assert_contained(child: Path, parent: Path) -> None:
            if child.name.endswith(".par"):
                raise ArtifactPathError(
                    "Injected post-allocation containment failure",
                    error_code="OUTPUT_PATH_NOT_ALLOWED",
                )
            orig_assert(child, parent)

        monkeypatch.setattr("artifacts.paths.assert_contained", mock_assert_contained)

        with pytest.raises(ArtifactPathError, match="Injected post-allocation containment failure"):
            prepare_artifact_paths(root, "job-post-alloc-fail")

        # Staging directory must have been cleanly removed by failsafe handler
        remaining_staging = list(root.glob(".staging-*"))
        assert len(remaining_staging) == 0

    def test_prepare_artifact_paths_retains_unexpectedly_non_empty_staging(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Proves that an unexpectedly non-empty staging directory is retained rather than recursively deleted."""
        root = tmp_path / "outputs"
        root.mkdir()

        orig_assert = assert_contained

        def mock_assert_contained(child: Path, parent: Path) -> None:
            if child.name.endswith(".par") and child.parent.name.startswith(STAGING_PREFIX):
                # Plant an unexpected file inside staging before post-allocation failure triggers
                rogue_file = child.parent / "unexpected_planted.txt"
                rogue_file.write_text("planted content")
                raise ArtifactPathError(
                    "Injected containment failure with non-empty staging",
                    error_code="OUTPUT_PATH_NOT_ALLOWED",
                )
            orig_assert(child, parent)

        monkeypatch.setattr("artifacts.paths.assert_contained", mock_assert_contained)

        with pytest.raises(ArtifactPathError, match="Injected containment failure with non-empty staging") as exc_info:
            prepare_artifact_paths(root, "job-post-alloc-non-empty")

        # Non-recursive rmdir refused to delete non-empty directory; staging is safely retained
        remaining_staging = list(root.glob(".staging-*"))
        assert len(remaining_staging) == 1
        assert (remaining_staging[0] / "unexpected_planted.txt").is_file()
        assert exc_info.value.details.get("retained_staging") is True
        assert "cleanup_error" in exc_info.value.details

    def test_prepare_artifact_paths_retains_substituted_reparse_point_staging(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Proves that a substituted reparse point/symlink staging path is retained and reported on post-allocation failure."""
        root = tmp_path / "outputs"
        root.mkdir()

        allocated = False
        orig_allocate = allocate_staging_dir

        def mock_allocate(resolved_root: Path, req_id: str) -> Path:
            nonlocal allocated
            res = orig_allocate(resolved_root, req_id)
            allocated = True
            return res

        orig_is_symlink = is_symlink_or_reparse_point

        def mock_is_symlink(p: Path) -> bool:
            if allocated and p.name.startswith(STAGING_PREFIX):
                return True
            return orig_is_symlink(p)

        monkeypatch.setattr("artifacts.paths.allocate_staging_dir", mock_allocate)
        monkeypatch.setattr("artifacts.paths.is_symlink_or_reparse_point", mock_is_symlink)

        with pytest.raises(ArtifactPathError, match="must not be a symbolic link or reparse point") as exc_info:
            prepare_artifact_paths(root, "job-post-alloc-reparse")

        # Cleanup refused to rmdir substituted reparse point; residue is retained and reported
        remaining_staging = list(root.glob(".staging-*"))
        assert len(remaining_staging) == 1
        assert exc_info.value.details.get("retained_staging") is True
        assert "cleanup_error" in exc_info.value.details
        assert "Refused post-allocation removal" in exc_info.value.details["cleanup_error"]

    def test_prepare_artifact_paths_retains_substituted_dangling_symlink_staging(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Proves that a dangling symlink staging path (where exists() is False) is retained and reported on post-allocation failure."""
        root = tmp_path / "outputs"
        root.mkdir()

        allocated = False
        orig_allocate = allocate_staging_dir

        def mock_allocate(resolved_root: Path, req_id: str) -> Path:
            nonlocal allocated
            res = orig_allocate(resolved_root, req_id)
            allocated = True
            # Remove directory so exists() is False, simulating a broken/dangling symlink entry
            res.rmdir()
            return res

        orig_is_symlink = is_symlink_or_reparse_point

        def mock_is_symlink(p: Path) -> bool:
            if allocated and p.name.startswith(STAGING_PREFIX):
                return True
            return orig_is_symlink(p)

        monkeypatch.setattr("artifacts.paths.allocate_staging_dir", mock_allocate)
        monkeypatch.setattr("artifacts.paths.is_symlink_or_reparse_point", mock_is_symlink)

        with pytest.raises(ArtifactPathError, match=r"Cannot inspect identity|must not be a symbolic link") as exc_info:
            prepare_artifact_paths(root, "job-post-alloc-dangling")

        assert exc_info.value.details.get("retained_staging") is True
        assert "cleanup_error" in exc_info.value.details
        assert "Refused post-allocation removal" in exc_info.value.details["cleanup_error"]
