"""Tests for atomic batch manifest publication with Windows no-replace semantics."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from batch.filesystem import PathIdentity
from batch.manifest_publication import (
    ManifestPublicationError,
    publish_batch_manifest,
)
from batch.models import (
    BatchManifest,
    BatchOperation,
    BatchSummary,
    ManifestArtifactRecord,
    ManifestFileResult,
)
from batch.output_snapshot import OutputSnapshot, capture_output_snapshot
from batch.parsing import parse_batch_manifest
from batch.projection import project_batch_manifest
from batch.schemas import get_batch_manifest_validator


def _make_valid_manifest(
    *,
    request_id: str = "req-pub-01",
    status: str = "completed",
    total: int = 1,
) -> BatchManifest:
    return BatchManifest(
        manifest_version="1.0",
        contract_version="1.0",
        request_id=request_id,
        status=status,  # type: ignore[arg-type]
        operation=BatchOperation(type="export_3d", formats=("step",)),
        summary=BatchSummary(total=total, accepted=total, partial=0, failed=0, unprocessed=0, cancelled=0),
        results=(
            ManifestFileResult(
                input="part.par",
                status="accepted",
                artifacts=(
                    ManifestArtifactRecord(
                        format="step",
                        relative_path="part.step",
                        size_bytes=100,
                        sha256="a" * 64,
                    ),
                ),
            ),
        ),
        engine_version="1.0.0",
    )


# ---------------------------------------------------------------------------
# 1. Successful Publication & Invariants
# ---------------------------------------------------------------------------


def test_publish_batch_manifest_happy_path(tmp_path: Path) -> None:
    """Proves successful atomic publication of a BatchManifest to the expected target path."""
    out_root = tmp_path / "out"
    out_root.mkdir(parents=True, exist_ok=True)

    manifest = _make_valid_manifest(request_id="req-pub-success")
    published_path = publish_batch_manifest(manifest, out_root)

    expected_target = out_root / "req-pub-success.batch_manifest.json"
    assert published_path == expected_target
    assert published_path.is_file()

    # Read back raw bytes
    raw = published_path.read_bytes()
    assert raw.endswith(b"\n")
    # Must be 7-bit ASCII
    assert all(b < 128 for b in raw)

    # Valid JSON & Draft 2020-12 schema validation
    parsed_dict = json.loads(raw.decode("utf-8"))
    validator = get_batch_manifest_validator()
    errors = list(validator.iter_errors(parsed_dict))
    assert not errors

    # Parsed model equals original manifest projected data
    read_manifest = parse_batch_manifest(parsed_dict)
    assert project_batch_manifest(read_manifest) == project_batch_manifest(manifest)

    # Post-publication snapshot passes
    snap = capture_output_snapshot(published_path)
    assert snap.size_bytes == len(raw)

    # No temporary files remain in output root
    remaining_files = list(out_root.iterdir())
    assert remaining_files == [expected_target]


# ---------------------------------------------------------------------------
# 2. Collision & Sentinel Preservation
# ---------------------------------------------------------------------------


def test_publish_batch_manifest_pre_existing_sentinel_preserved(tmp_path: Path) -> None:
    """Proves pre-existing final manifest (collision sentinel) causes rejection and is NEVER modified or deleted."""
    out_root = tmp_path / "out"
    out_root.mkdir(parents=True, exist_ok=True)

    target_sentinel = out_root / "req-collision.batch_manifest.json"
    sentinel_content = b'{"sentinel": "DO_NOT_OVERWRITE"}\n'
    target_sentinel.write_bytes(sentinel_content)
    sentinel_mtime_before = target_sentinel.stat().st_mtime_ns

    # Also place a sibling file to verify sibling non-interference
    sibling_file = out_root / "sibling.txt"
    sibling_file.write_bytes(b"SIBLING_CONTENT")

    manifest = _make_valid_manifest(request_id="req-collision")

    with pytest.raises(ManifestPublicationError, match="Target manifest already exists"):
        publish_batch_manifest(manifest, out_root)

    # Sentinel must remain completely untouched
    assert target_sentinel.exists()
    assert target_sentinel.read_bytes() == sentinel_content
    assert target_sentinel.stat().st_mtime_ns == sentinel_mtime_before

    # Sibling file untouched
    assert sibling_file.exists()
    assert sibling_file.read_bytes() == b"SIBLING_CONTENT"

    # Zero temporary files remain
    assert sorted(p.name for p in out_root.iterdir()) == ["req-collision.batch_manifest.json", "sibling.txt"]


def test_publish_batch_manifest_collision_during_rename_preserves_sentinel(tmp_path: Path) -> None:
    """Proves that a collision created between precheck and rename raises error and cleans temp file."""
    out_root = tmp_path / "out"
    out_root.mkdir(parents=True, exist_ok=True)

    target = out_root / "req-race.batch_manifest.json"
    manifest = _make_valid_manifest(request_id="req-race")

    def mock_rename_collision(_src: str, _dst: str) -> None:
        # Simulate target appearing and Windows throwing FileExistsError
        target.write_bytes(b"LATE_COLLISION_SENTINEL\n")
        raise FileExistsError("Cannot create a file when that file already exists")

    with (
        patch("os.rename", side_effect=mock_rename_collision),
        pytest.raises(ManifestPublicationError, match="Target manifest already exists"),
    ):
        publish_batch_manifest(manifest, out_root)

    # Sentinel must be preserved
    assert target.exists()
    assert target.read_bytes() == b"LATE_COLLISION_SENTINEL\n"

    # No leftover temporary files
    assert list(out_root.iterdir()) == [target]


def test_publish_batch_manifest_post_rename_identity_mismatch_raises(tmp_path: Path) -> None:
    """Proves that a file replacement race detected by post-rename identity mismatch raises ManifestPublicationError."""
    out_root = tmp_path / "out"
    out_root.mkdir(parents=True, exist_ok=True)

    manifest = _make_valid_manifest(request_id="req-race-replace")

    orig_capture = capture_output_snapshot

    def mock_capture_mismatched_identity(path: Path) -> OutputSnapshot:
        real_snap = orig_capture(path)
        fake_identity = PathIdentity(
            device=real_snap.identity.device,
            inode=real_snap.identity.inode + 999999,
            mode=real_snap.identity.mode,
        )
        return OutputSnapshot(
            identity=fake_identity,
            size_bytes=real_snap.size_bytes,
            mtime_ns=real_snap.mtime_ns,
            sha256=real_snap.sha256,
        )

    with (
        patch("batch.manifest_publication.capture_output_snapshot", side_effect=mock_capture_mismatched_identity),
        pytest.raises(ManifestPublicationError, match="filesystem identity does not match"),
    ):
        publish_batch_manifest(manifest, out_root)


# ---------------------------------------------------------------------------
# 3. Validation & Temporary File Cleanup on Staging Failure
# ---------------------------------------------------------------------------


def test_publish_batch_manifest_staged_schema_failure_cleans_temp(tmp_path: Path) -> None:
    """Proves that if staged content fails schema validation, temp file is cleaned and error is raised."""
    out_root = tmp_path / "out"
    out_root.mkdir(parents=True, exist_ok=True)

    manifest = _make_valid_manifest(request_id="req-schema-fail")

    # Mock project_batch_manifest to return invalid payload lacking required field
    def mock_bad_projection(_m: BatchManifest) -> dict[str, object]:
        return {"manifest_version": "1.0", "invalid_root": True}

    with (
        patch("batch.manifest_publication.project_batch_manifest", side_effect=mock_bad_projection),
        pytest.raises(ManifestPublicationError, match="failed schema validation"),
    ):
        publish_batch_manifest(manifest, out_root)

    # Output root must have zero files (temp file cleaned up)
    assert list(out_root.iterdir()) == []


def test_publish_batch_manifest_exceeding_max_bytes_raises_and_cleans(tmp_path: Path) -> None:
    """Proves manifest serialization exceeding 10 MiB limit raises ManifestPublicationError without publishing."""
    out_root = tmp_path / "out"
    out_root.mkdir(parents=True, exist_ok=True)

    manifest = _make_valid_manifest(request_id="req-huge")

    # Patch MAX_MANIFEST_SERIALIZED_BYTES to a tiny number for testing
    with (
        patch("batch.manifest_publication.MAX_MANIFEST_SERIALIZED_BYTES", 50),
        pytest.raises(ManifestPublicationError, match="exceeds limit"),
    ):
        publish_batch_manifest(manifest, out_root)

    assert list(out_root.iterdir()) == []


def test_publish_batch_manifest_invalid_output_root_raises(tmp_path: Path) -> None:
    """Proves non-absolute or non-directory output root raises ManifestPublicationError."""
    manifest = _make_valid_manifest()

    # Relative path
    with pytest.raises(ManifestPublicationError, match="must be an absolute Path"):
        publish_batch_manifest(manifest, Path("relative/out"))

    # Non-directory (regular file)
    file_path = tmp_path / "not_a_dir.txt"
    file_path.write_text("hello", encoding="utf-8")
    with pytest.raises(ManifestPublicationError, match="must be an existing, non-reparse directory"):
        publish_batch_manifest(manifest, file_path)
