"""Atomic, race-aware batch manifest publication with Windows no-replace semantics.

Guarantees atomic, durable publication of the final BatchManifest:
- Stages serialization to an unpredictable, request-private temporary file under output_root.
- Serializes deterministic, compact, sorted, indented UTF-8 JSON with terminal LF (max 10 MiB).
- Reads back staged payload, validates Draft 2020-12 schema, parses, and proves exact equality.
- Rechecks root identity, temporary identity, and target absence immediately before rename.
- Atomically moves with Windows no-replace semantics (never os.replace or overwrite).
- Verifies post-publication snapshot and cryptographic digest.
- Cleans only the private temporary file on failure; never touches target or collision sentinels.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Final

from batch.filesystem import (
    assert_strictly_contained,
    get_path_identity,
    is_symlink_or_reparse_point,
)
from batch.models import (
    BatchContractError,
    BatchManifest,
)
from batch.output_snapshot import capture_output_snapshot
from batch.parsing import parse_batch_manifest
from batch.projection import project_batch_manifest
from batch.schemas import get_batch_manifest_validator
from ipc.wire import write_all

MAX_MANIFEST_SERIALIZED_BYTES: Final[int] = 10 * 1024 * 1024  # 10 MiB safety cap


class ManifestPublicationError(BatchContractError):
    """Raised when batch manifest publication fails during staging, validation, rename, or verification."""

    def __init__(self, message: str, *, request_id: str = "unknown") -> None:
        super().__init__("MANIFEST_PUBLICATION_FAILED", message, request_id=request_id)


def publish_batch_manifest(
    manifest: BatchManifest,
    output_root: Path,
) -> Path:
    """Atomically serialize, validate, and publish a BatchManifest with Windows no-replace semantics.

    Args:
        manifest: Validated BatchManifest to publish.
        output_root: Prepared, validated absolute output root directory.

    Returns:
        Absolute Path to the published final manifest file.

    Raises:
        ManifestPublicationError: If staging, validation, collision, rename, or post-check fails.
    """
    req_id = manifest.request_id

    # 1. Output root validation
    if not isinstance(output_root, Path) or not output_root.is_absolute():
        raise ManifestPublicationError("Output root must be an absolute Path", request_id=req_id)
    if not output_root.is_dir() or is_symlink_or_reparse_point(output_root):
        raise ManifestPublicationError("Output root must be an existing, non-reparse directory", request_id=req_id)

    target_name = f"{manifest.request_id}.batch_manifest.json"
    target_path = output_root / target_name

    # Re-verify target containment beneath output root
    try:
        assert_strictly_contained(target_path, output_root, request_id=req_id)
    except Exception as exc:
        raise ManifestPublicationError("Target manifest path escapes output root", request_id=req_id) from exc

    # Pre-check target absence: collision sentinel or pre-existing manifest must not be overwritten
    if target_path.exists() or is_symlink_or_reparse_point(target_path):
        raise ManifestPublicationError(
            f"Target manifest already exists for request '{manifest.request_id}'",
            request_id=req_id,
        )

    # Capture initial root identity
    try:
        root_identity_before = get_path_identity(output_root)
    except Exception as exc:
        raise ManifestPublicationError("Failed to capture output root identity", request_id=req_id) from exc

    # 2. Serialize manifest to deterministic UTF-8 JSON
    try:
        projected = project_batch_manifest(manifest)
        formatted_json = json.dumps(
            projected,
            indent=2,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
        )
        # Exactly one trailing LF
        encoded_bytes = formatted_json.encode("utf-8") + b"\n"
    except Exception as exc:
        raise ManifestPublicationError(f"Failed to serialize manifest: {exc}", request_id=req_id) from exc

    if len(encoded_bytes) > MAX_MANIFEST_SERIALIZED_BYTES:
        raise ManifestPublicationError(
            f"Serialized manifest size ({len(encoded_bytes)} bytes) exceeds limit ({MAX_MANIFEST_SERIALIZED_BYTES} bytes)",
            request_id=req_id,
        )

    expected_sha256 = hashlib.sha256(encoded_bytes).hexdigest()

    # 3. Allocate exclusive request-private temporary file under the same output root
    random_token = uuid.uuid4().hex
    temp_filename = f".{manifest.request_id}.{random_token}.tmp"
    temp_path = output_root / temp_filename

    # Ensure containment of temp path
    try:
        assert_strictly_contained(temp_path, output_root, request_id=req_id)
    except Exception as exc:
        raise ManifestPublicationError("Temporary manifest path escapes output root", request_id=req_id) from exc

    temp_created = False
    rename_succeeded = False

    try:
        # Create exclusively directly on the output root volume
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY

        try:
            fd = os.open(temp_path, flags, 0o600)
            temp_created = True
        except Exception as exc:
            raise ManifestPublicationError(
                f"Failed to allocate temporary manifest file: {exc}", request_id=req_id
            ) from exc

        try:
            write_all(fd, encoded_bytes, max_bytes=MAX_MANIFEST_SERIALIZED_BYTES)
            with contextlib.suppress(OSError):
                os.fsync(fd)
        finally:
            with contextlib.suppress(OSError):
                os.close(fd)

        # 4. Staged read-back, schema validation, and projected-equality proof
        try:
            read_bytes = temp_path.read_bytes()
        except Exception as exc:
            raise ManifestPublicationError(f"Failed to read back staged manifest: {exc}", request_id=req_id) from exc

        if read_bytes != encoded_bytes:
            raise ManifestPublicationError(
                "Staged manifest read-back bytes do not match serialized bytes", request_id=req_id
            )

        try:
            parsed_dict = json.loads(read_bytes.decode("utf-8"))
        except Exception as exc:
            raise ManifestPublicationError(f"Staged manifest is not valid JSON: {exc}", request_id=req_id) from exc

        validator = get_batch_manifest_validator()
        errors = list(validator.iter_errors(parsed_dict))
        if errors:
            raise ManifestPublicationError(
                f"Staged manifest failed schema validation: {errors[0].message}",
                request_id=req_id,
            )

        try:
            read_back_manifest = parse_batch_manifest(parsed_dict)
            if project_batch_manifest(read_back_manifest) != projected:
                raise ManifestPublicationError(
                    "Staged manifest parsed data does not equal original manifest", request_id=req_id
                )
        except Exception as exc:
            raise ManifestPublicationError(
                f"Failed to verify staged manifest model equality: {exc}", request_id=req_id
            ) from exc

        # 5. Pre-rename identity and absence checks
        try:
            root_identity_after = get_path_identity(output_root)
            if root_identity_after != root_identity_before:
                raise ManifestPublicationError("Output root identity changed during staging", request_id=req_id)

            temp_identity = get_path_identity(temp_path)
            if not temp_identity.is_regular_file:
                raise ManifestPublicationError("Temporary manifest file is not a regular file", request_id=req_id)
            if is_symlink_or_reparse_point(temp_path):
                raise ManifestPublicationError(
                    "Temporary manifest file was replaced by a symlink or reparse point", request_id=req_id
                )
        except ManifestPublicationError:
            raise
        except Exception as exc:
            raise ManifestPublicationError(
                f"Pre-rename identity verification failed: {exc}", request_id=req_id
            ) from exc

        # Final target absence check immediately before rename
        if target_path.exists() or is_symlink_or_reparse_point(target_path):
            raise ManifestPublicationError(
                f"Target manifest already exists for request '{manifest.request_id}' (collision detected before rename)",
                request_id=req_id,
            )

        # 6. Atomic rename with Windows no-replace semantics
        try:
            os.rename(str(temp_path), str(target_path))
            rename_succeeded = True
        except FileExistsError as exc:
            raise ManifestPublicationError(
                f"Target manifest already exists (collision): '{target_name}'",
                request_id=req_id,
            ) from exc
        except OSError as exc:
            winerror = getattr(exc, "winerror", None)
            if winerror in (80, 183):
                raise ManifestPublicationError(
                    f"Target manifest already exists (collision winerror {winerror}): '{target_name}'",
                    request_id=req_id,
                ) from exc
            raise ManifestPublicationError(
                f"Failed to rename staged manifest to target: {exc}", request_id=req_id
            ) from exc

        # 7. Post-publication verification
        try:
            post_snap = capture_output_snapshot(target_path)
        except Exception as exc:
            raise ManifestPublicationError(
                f"Failed to capture post-publication snapshot: {exc}", request_id=req_id
            ) from exc

        if post_snap.identity != temp_identity:
            raise ManifestPublicationError(
                "Published manifest filesystem identity does not match staged temporary file",
                request_id=req_id,
            )
        if post_snap.size_bytes != len(encoded_bytes):
            raise ManifestPublicationError(
                f"Published manifest size ({post_snap.size_bytes}) does not match expected ({len(encoded_bytes)})",
                request_id=req_id,
            )
        if post_snap.sha256 != expected_sha256:
            raise ManifestPublicationError(
                f"Published manifest SHA-256 ('{post_snap.sha256}') does not match expected ('{expected_sha256}')",
                request_id=req_id,
            )

        return target_path

    finally:
        # Clean ONLY the owned temporary file if rename did not succeed
        if temp_created and not rename_succeeded:
            with contextlib.suppress(OSError):
                if temp_path.exists() and not is_symlink_or_reparse_point(temp_path):
                    os.unlink(temp_path)


__all__ = [
    "MAX_MANIFEST_SERIALIZED_BYTES",
    "ManifestPublicationError",
    "publish_batch_manifest",
]
