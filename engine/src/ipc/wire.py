"""Generic wire framing, bounded I/O, and strict JSON transport primitives.

Provides transport-neutral raw byte streaming with size limits, strict JSON
decoding rejecting duplicate keys and non-finite values, deterministic compact
ASCII JSON-line serialization, and bounded descriptor write loops.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any, BinaryIO, Final

DEFAULT_MAX_WRITE_BYTES: Final[int] = 10 * 1024 * 1024  # 10 MiB safety cap
DEFAULT_CHUNK_SIZE: Final[int] = 64 * 1024  # 64 KiB read buffer


class WireError(Exception):
    """Base exception for strict wire framing, decoding, and I/O failures."""


class WirePayloadTooLargeError(WireError):
    """Raised when incoming stream or payload exceeds the wire byte limit."""


class WireJSONDecodeError(WireError):
    """Raised when payload violates UTF-8, JSON, duplicate-key, or object-root constraints."""


def read_bounded_bytes(
    stream: BinaryIO,
    limit: int,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> bytes:
    """Read bytes from a binary input stream up to an explicit wire limit.

    Reads in chunks until EOF or until limit bytes have been accumulated.
    If limit bytes are reached, WirePayloadTooLargeError is raised immediately,
    ensuring memory allocation is strictly bounded at the I/O boundary.

    Args:
        stream: Binary input stream (e.g. sys.stdin.buffer).
        limit: Maximum bytes permitted to read.

    Returns:
        Raw bytes read from the stream if strictly within the limit.

    Raises:
        WirePayloadTooLargeError: If incoming stream contains limit or more bytes.
    """
    chunks: list[bytes] = []
    total_read = 0

    while total_read < limit:
        to_read = min(chunk_size, limit - total_read)
        chunk = stream.read(to_read)
        if not chunk:
            break
        chunks.append(chunk)
        total_read += len(chunk)

    if total_read >= limit:
        raise WirePayloadTooLargeError(f"Incoming stream reached or exceeded limit of {limit} bytes.")

    return b"".join(chunks)


def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Parse object pairs into a dictionary, rejecting duplicate keys at any nesting level.

    Args:
        pairs: Key-value tuples from json.loads object_pairs_hook.

    Returns:
        Dictionary containing unique keys.

    Raises:
        ValueError: If any key is duplicated within the object.
    """
    res: dict[str, Any] = {}
    for key, value in pairs:
        if key in res:
            raise ValueError(f"Duplicate object key: {key}")
        res[key] = value
    return res


def reject_constant(val: str) -> Any:
    """Reject non-finite JSON constants such as NaN, Infinity, or -Infinity.

    Args:
        val: Raw constant string parsed by json.loads.

    Raises:
        ValueError: Always raised to reject non-finite constants.
    """
    raise ValueError(f"JSON non-finite constant '{val}' is not allowed")


def decode_strict_json(
    raw_bytes: bytes,
    *,
    max_bytes: int | None = None,
) -> dict[str, Any]:
    """Strictly decode and parse raw UTF-8 JSON object bytes.

    Enforces:
        1. Length bounds (if max_bytes is provided).
        2. Non-empty, non-whitespace payload.
        3. No UTF-8 Byte Order Mark (BOM).
        4. Valid UTF-8 encoding.
        5. Valid JSON syntax.
        6. Rejection of duplicate keys at all nesting depths.
        7. Rejection of non-finite constants (NaN, Infinity).
        8. Root element must be a JSON object (dict).

    Args:
        raw_bytes: Raw incoming byte payload.
        max_bytes: Optional upper bound on raw byte length.

    Returns:
        Parsed JSON dictionary.

    Raises:
        WirePayloadTooLargeError: If raw_bytes length exceeds max_bytes.
        WireJSONDecodeError: If any strict JSON, encoding, or structural constraint fails.
    """
    if max_bytes is not None and len(raw_bytes) > max_bytes:
        raise WirePayloadTooLargeError(f"Payload size ({len(raw_bytes)} bytes) exceeds limit ({max_bytes} bytes).")

    if not raw_bytes:
        raise WireJSONDecodeError("Request payload is empty.")

    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        raise WireJSONDecodeError("Request payload contains forbidden UTF-8 BOM.")

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise WireJSONDecodeError("Request payload is not valid UTF-8.") from exc

    if not text.strip():
        raise WireJSONDecodeError("Request payload contains only whitespace.")

    try:
        parsed: Any = json.loads(
            text,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_constant,
        )
    except ValueError as exc:
        raise WireJSONDecodeError(f"Request payload is not valid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise WireJSONDecodeError("Request payload root must be a JSON object.")

    return parsed


def serialize_json_line(data: Mapping[str, Any]) -> bytes:
    """Serialize a mapping to compact, deterministic, 7-bit ASCII JSON + LF.

    Args:
        data: Mapping to serialize.

    Returns:
        ASCII-safe UTF-8 bytes terminated with a single newline (b"\\n").

    Raises:
        WireError: If payload cannot be serialized to JSON.
    """
    try:
        compact_text = json.dumps(
            data,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        return compact_text.encode("utf-8") + b"\n"
    except Exception as exc:
        raise WireError(f"Failed to serialize payload to JSON: {exc}") from exc


def write_all(
    fd: int,
    data: bytes,
    *,
    max_bytes: int = DEFAULT_MAX_WRITE_BYTES,
) -> None:
    """Write entire byte buffer to an OS file descriptor in a bounded loop.

    Handles partial writes from os.write and verifies that total bytes
    written does not exceed max_bytes.

    Args:
        fd: Target OS file descriptor.
        data: Byte buffer to write.
        max_bytes: Maximum total bytes permitted to be written.

    Raises:
        ValueError: If data length exceeds max_bytes.
        OSError: If an underlying os.write call fails or makes no forward progress.
    """
    if len(data) > max_bytes:
        raise ValueError(f"Data length ({len(data)} bytes) exceeds maximum permitted limit ({max_bytes} bytes).")

    total_written = 0
    total_to_write = len(data)

    while total_written < total_to_write:
        chunk = data[total_written:]
        written = os.write(fd, chunk)
        if written <= 0:
            raise OSError(f"Write to descriptor {fd} made no progress (os.write returned {written}).")
        total_written += written
