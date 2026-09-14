"""Batch input path canonicalization, extension checking, and request semantic validation."""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any

from batch.models import BatchOperationType, BatchValidationError

if TYPE_CHECKING:
    from batch.models import BatchRequest

PORTABLE_PATH_PATTERN: re.Pattern[str] = re.compile(r"^(?!\.{1,2}(?:/|$))[^\\/:\0]+(?:/(?!\.{1,2}(?:/|$))[^\\/:\0]+)*$")
REQUEST_ID_PATTERN: re.Pattern[str] = re.compile(r"^[A-Za-z0-9._-]+$")

ALLOWED_3D_EXTENSIONS: frozenset[str] = frozenset({".par", ".psm", ".asm"})
ALLOWED_DRAWING_EXTENSIONS: frozenset[str] = frozenset({".dft"})
ALLOWED_3D_FORMATS: frozenset[str] = frozenset({"step", "stl", "parasolid"})
ALLOWED_DRAWING_FORMATS: frozenset[str] = frozenset({"pdf", "dxf"})


def validate_and_canonicalize_input_path(raw_path: Any, *, request_id: str = "unknown") -> str:
    """Validate a raw input path and return its canonical forward-slash representation."""
    if not isinstance(raw_path, str):
        raise BatchValidationError(
            f"Input path must be a string, got {type(raw_path).__name__}",
            code="INPUT_PATH_NOT_ALLOWED",
            request_id=request_id,
        )
    if not (1 <= len(raw_path) <= 1024):
        raise BatchValidationError(
            "Input path length must be between 1 and 1024 characters",
            code="INPUT_PATH_NOT_ALLOWED",
            request_id=request_id,
        )
    if "\0" in raw_path:
        raise BatchValidationError(
            "Input path contains NUL bytes",
            code="INPUT_PATH_NOT_ALLOWED",
            request_id=request_id,
        )
    if ":" in raw_path:
        raise BatchValidationError(
            "Input path contains drive qualification or colons",
            code="INPUT_PATH_NOT_ALLOWED",
            request_id=request_id,
        )
    if raw_path.startswith(("/", "\\")):
        raise BatchValidationError(
            "Input path must be relative, cannot start with separator",
            code="INPUT_PATH_NOT_ALLOWED",
            request_id=request_id,
        )
    if raw_path.endswith(("/", "\\")):
        raise BatchValidationError(
            "Input path cannot end with separator",
            code="INPUT_PATH_NOT_ALLOWED",
            request_id=request_id,
        )
    if any(sep in raw_path for sep in ("//", "\\\\", "\\/", "/\\")):
        raise BatchValidationError(
            "Input path contains repeated consecutive separators",
            code="INPUT_PATH_NOT_ALLOWED",
            request_id=request_id,
        )

    segments = re.split(r"[\\/]", raw_path)
    for seg in segments:
        if seg in ("", ".", ".."):
            raise BatchValidationError(
                "Input path contains forbidden traversal or empty segment",
                code="INPUT_PATH_NOT_ALLOWED",
                request_id=request_id,
            )
        if seg.startswith(" ") or seg.endswith(" "):
            raise BatchValidationError(
                "Input path segment has leading or trailing whitespace",
                code="INPUT_PATH_NOT_ALLOWED",
                request_id=request_id,
            )
        if seg.endswith("."):
            raise BatchValidationError(
                "Input path segment has trailing dots",
                code="INPUT_PATH_NOT_ALLOWED",
                request_id=request_id,
            )

    canonical = PureWindowsPath(raw_path).as_posix()
    if not PORTABLE_PATH_PATTERN.fullmatch(canonical):
        raise BatchValidationError(
            "Canonical path does not conform to portable path contract",
            code="INPUT_PATH_NOT_ALLOWED",
            request_id=request_id,
        )
    return canonical


def validate_and_canonicalize_files(
    files: Sequence[Any],
    operation_type: BatchOperationType,
    *,
    request_id: str = "unknown",
) -> tuple[str, ...]:
    """Validate and canonicalize input files sequence for the given operation type."""
    if not isinstance(files, (list, tuple)) or not (1 <= len(files) <= 500):
        raise BatchValidationError(
            "Input files must be a non-empty sequence of at most 500 items",
            code="INVALID_SCHEMA",
            request_id=request_id,
        )

    canonical_list: list[str] = []
    seen_casefold: set[str] = set()

    for item in files:
        canonical = validate_and_canonicalize_input_path(item, request_id=request_id)
        key = canonical.lower()
        if key in seen_casefold:
            raise BatchValidationError(
                "Duplicate or case-colliding input file path detected",
                code="INPUT_PATH_NOT_ALLOWED",
                request_id=request_id,
            )
        seen_casefold.add(key)

        ext = PurePosixPath(canonical).suffix.lower()
        if operation_type == "export_3d":
            if ext not in ALLOWED_3D_EXTENSIONS:
                raise BatchValidationError(
                    "Input file extension is not permitted for export_3d (must be .par, .psm, or .asm)",
                    code="INPUT_EXTENSION_NOT_ALLOWED",
                    request_id=request_id,
                )
        elif operation_type == "publish_drawing":
            if ext not in ALLOWED_DRAWING_EXTENSIONS:
                raise BatchValidationError(
                    "Input file extension is not permitted for publish_drawing (must be .dft)",
                    code="INPUT_EXTENSION_NOT_ALLOWED",
                    request_id=request_id,
                )
        else:
            raise BatchValidationError(
                "Unsupported operation type",
                code="UNSUPPORTED_OPERATION",
                request_id=request_id,
            )
        canonical_list.append(canonical)

    return tuple(canonical_list)


def validate_batch_request_semantics(request: BatchRequest, *, request_id: str = "unknown") -> None:
    """Validate BatchRequest semantic relationships, path safety, and operation rules."""
    req_id = request.request_id or request_id

    if (
        not isinstance(request.request_id, str)
        or not (1 <= len(request.request_id) <= 96)
        or not REQUEST_ID_PATTERN.fullmatch(request.request_id)
    ):
        raise BatchValidationError(
            "Batch request ID must be 1-96 characters matching ^[A-Za-z0-9._-]+$",
            code="INVALID_SCHEMA",
            request_id=req_id,
        )
    if request.contract_version != "1.0":
        raise BatchValidationError(
            f"contract_version must be '1.0', got {request.contract_version!r}",
            code="INVALID_SCHEMA",
            request_id=req_id,
        )
    if request.kind != "batch_operation":
        raise BatchValidationError(
            f"kind must be 'batch_operation', got {request.kind!r}",
            code="INVALID_SCHEMA",
            request_id=req_id,
        )
    if not isinstance(request.output_root, str) or not (1 <= len(request.output_root) <= 1024):
        raise BatchValidationError(
            "output_root must be a string with length between 1 and 1024",
            code="INVALID_SCHEMA",
            request_id=req_id,
        )
    if not isinstance(request.input.root, str) or not (1 <= len(request.input.root) <= 1024):
        raise BatchValidationError(
            "input.root must be a string with length between 1 and 1024",
            code="INVALID_SCHEMA",
            request_id=req_id,
        )
    if not (1 <= len(request.input.files) <= request.options.max_files):
        raise BatchValidationError(
            f"Requested file count ({len(request.input.files)}) exceeds options.max_files ({request.options.max_files})",
            code="INVALID_SCHEMA",
            request_id=req_id,
        )
    if request.operation.type == "export_3d":
        allowed_formats = ALLOWED_3D_FORMATS
        if not (1 <= len(request.operation.formats) <= 3):
            raise BatchValidationError(
                "export_3d operation formats must have 1 to 3 items",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
    elif request.operation.type == "publish_drawing":
        allowed_formats = ALLOWED_DRAWING_FORMATS
        if not (1 <= len(request.operation.formats) <= 2):
            raise BatchValidationError(
                "publish_drawing operation formats must have 1 or 2 items",
                code="INVALID_SCHEMA",
                request_id=req_id,
            )
    else:
        raise BatchValidationError(
            f"Unsupported operation type '{request.operation.type}'",
            code="UNSUPPORTED_OPERATION",
            request_id=req_id,
        )
    if not set(request.operation.formats).issubset(allowed_formats):
        raise BatchValidationError(
            f"{request.operation.type} operation formats must only contain permitted formats",
            code="INVALID_SCHEMA",
            request_id=req_id,
        )

    # Path safety & canonicalization
    canonical_files = validate_and_canonicalize_files(request.input.files, request.operation.type, request_id=req_id)
    if tuple(request.input.files) != canonical_files:
        raise BatchValidationError(
            "Input file paths must be in canonical POSIX relative format",
            code="INPUT_PATH_NOT_ALLOWED",
            request_id=req_id,
        )


__all__ = [
    "ALLOWED_3D_EXTENSIONS",
    "ALLOWED_3D_FORMATS",
    "ALLOWED_DRAWING_EXTENSIONS",
    "ALLOWED_DRAWING_FORMATS",
    "PORTABLE_PATH_PATTERN",
    "REQUEST_ID_PATTERN",
    "validate_and_canonicalize_files",
    "validate_and_canonicalize_input_path",
    "validate_batch_request_semantics",
]
