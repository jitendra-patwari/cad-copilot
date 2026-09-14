"""Tests for low-level IPC transport primitives: wire framing and descriptor isolation."""

from __future__ import annotations

import contextlib
import io
import json
import os
from unittest.mock import patch

import pytest

from ipc.descriptors import (
    ControlledDescriptors,
    controlled_stdio,
)
from ipc.wire import (
    WireError,
    WireJSONDecodeError,
    WirePayloadTooLargeError,
    decode_strict_json,
    read_bounded_bytes,
    reject_constant,
    reject_duplicate_keys,
    serialize_json_line,
    write_all,
)
from tests.ipc.support import drain_pipe, make_pipes

# ---------------------------------------------------------------------------
# 1. read_bounded_bytes
# ---------------------------------------------------------------------------


def test_read_bounded_bytes_empty_stream() -> None:
    """Proves reading from an empty stream returns empty bytes without error."""
    stream = io.BytesIO(b"")
    result = read_bounded_bytes(stream, limit=100)
    assert result == b""


def test_read_bounded_bytes_within_limit() -> None:
    """Proves reading data strictly below limit returns exact content."""
    payload = b"hello, cad copilot!"
    stream = io.BytesIO(payload)
    result = read_bounded_bytes(stream, limit=len(payload) + 10)
    assert result == payload


def test_read_bounded_bytes_with_custom_chunk_size() -> None:
    """Proves reading in small chunks properly accumulates the complete stream."""
    payload = b"abcdefghijklmnopqrstuvwxyz"
    stream = io.BytesIO(payload)
    result = read_bounded_bytes(stream, limit=100, chunk_size=4)
    assert result == payload


def test_read_bounded_bytes_reaches_or_exceeds_limit_raises() -> None:
    """Proves stream reaching or exceeding limit raises WirePayloadTooLargeError immediately."""
    limit = 10
    # Exact limit bytes in stream
    stream_exact = io.BytesIO(b"x" * limit)
    with pytest.raises(WirePayloadTooLargeError, match="reached or exceeded limit"):
        read_bounded_bytes(stream_exact, limit=limit)

    # Oversized stream bounds reading without exhausting stream
    stream_large = io.BytesIO(b"x" * 1000)
    with pytest.raises(WirePayloadTooLargeError, match="reached or exceeded limit"):
        read_bounded_bytes(stream_large, limit=limit, chunk_size=5)
    assert stream_large.tell() == limit


# ---------------------------------------------------------------------------
# 2. Strict JSON Helper Hooks
# ---------------------------------------------------------------------------


def test_reject_duplicate_keys_accepts_unique() -> None:
    """Proves reject_duplicate_keys preserves distinct key pairs."""
    pairs = [("a", 1), ("b", 2), ("c", 3)]
    assert reject_duplicate_keys(pairs) == {"a": 1, "b": 2, "c": 3}


def test_reject_duplicate_keys_rejects_duplicates() -> None:
    """Proves reject_duplicate_keys raises ValueError on duplicated keys."""
    pairs = [("a", 1), ("b", 2), ("a", 3)]
    with pytest.raises(ValueError, match="Duplicate object key: a"):
        reject_duplicate_keys(pairs)


def test_reject_constant_always_raises() -> None:
    """Proves reject_constant rejects non-finite constants with ValueError."""
    with pytest.raises(ValueError, match="non-finite constant 'NaN'"):
        reject_constant("NaN")
    with pytest.raises(ValueError, match="non-finite constant 'Infinity'"):
        reject_constant("Infinity")


# ---------------------------------------------------------------------------
# 3. decode_strict_json
# ---------------------------------------------------------------------------


def test_decode_strict_json_valid_payload() -> None:
    """Proves valid JSON payload decodes into expected dictionary."""
    data = {"kind": "test", "count": 42, "nested": {"key": "val"}}
    raw = json.dumps(data).encode("utf-8")
    assert decode_strict_json(raw) == data


def test_decode_strict_json_max_bytes_enforced() -> None:
    """Proves raw payload exceeding max_bytes raises WirePayloadTooLargeError."""
    data = b'{"a": 1}'
    with pytest.raises(WirePayloadTooLargeError, match="exceeds limit"):
        decode_strict_json(data, max_bytes=5)


def test_decode_strict_json_empty_payload() -> None:
    """Proves empty byte string raises WireJSONDecodeError."""
    with pytest.raises(WireJSONDecodeError, match="Request payload is empty"):
        decode_strict_json(b"")


def test_decode_strict_json_whitespace_only() -> None:
    """Proves whitespace-only payload raises WireJSONDecodeError."""
    with pytest.raises(WireJSONDecodeError, match="contains only whitespace"):
        decode_strict_json(b"  \t\r\n  ")


def test_decode_strict_json_utf8_bom() -> None:
    """Proves UTF-8 BOM is detected and rejected."""
    raw = b"\xef\xbb\xbf" + b'{"valid": true}'
    with pytest.raises(WireJSONDecodeError, match="forbidden UTF-8 BOM"):
        decode_strict_json(raw)


def test_decode_strict_json_invalid_utf8() -> None:
    """Proves non-UTF-8 bytes raise WireJSONDecodeError."""
    with pytest.raises(WireJSONDecodeError, match="not valid UTF-8"):
        decode_strict_json(b'{"key": "\xff\xfe"}')


def test_decode_strict_json_malformed_syntax() -> None:
    """Proves malformed JSON syntax raises WireJSONDecodeError."""
    with pytest.raises(WireJSONDecodeError, match="not valid JSON"):
        decode_strict_json(b'{"unclosed": "brace"')


def test_decode_strict_json_non_object_root() -> None:
    """Proves root elements that are not objects raise WireJSONDecodeError."""
    for root_val in (b"[1, 2, 3]", b'"string"', b"123", b"true", b"null"):
        with pytest.raises(WireJSONDecodeError, match="root must be a JSON object"):
            decode_strict_json(root_val)


def test_decode_strict_json_duplicate_keys_rejected() -> None:
    """Proves duplicate keys in object payload raise WireJSONDecodeError."""
    raw = b'{"k": 1, "k": 2}'
    with pytest.raises(WireJSONDecodeError, match="Duplicate object key: k"):
        decode_strict_json(raw)


def test_decode_strict_json_non_finite_rejected() -> None:
    """Proves non-finite constants raise WireJSONDecodeError."""
    for const in (b"NaN", b"Infinity", b"-Infinity"):
        raw = b'{"k": ' + const + b"}"
        with pytest.raises(WireJSONDecodeError, match="non-finite constant"):
            decode_strict_json(raw)


# ---------------------------------------------------------------------------
# 4. serialize_json_line
# ---------------------------------------------------------------------------


def test_serialize_json_line_deterministic() -> None:
    """Proves serialization produces deterministic, sorted, compact ASCII JSON ending with LF."""
    data = {"z": 1, "a": "text", "m": [3, 2, 1]}
    serialized = serialize_json_line(data)
    assert serialized == b'{"a":"text","m":[3,2,1],"z":1}\n'
    assert serialized.endswith(b"\n")


def test_serialize_json_line_escapes_non_ascii() -> None:
    """Proves non-ASCII characters are escaped to 7-bit ASCII representations."""
    data = {"unicode": "gear \u2699"}
    serialized = serialize_json_line(data)
    assert all(b < 128 for b in serialized)
    assert b"gear \\u2699" in serialized


def test_serialize_json_line_non_serializable_raises_wire_error() -> None:
    """Proves non-serializable objects raise WireError."""
    data = {"bad": {1, 2, 3}}  # sets are not JSON serializable
    with pytest.raises(WireError, match="Failed to serialize payload to JSON"):
        serialize_json_line(data)


# ---------------------------------------------------------------------------
# 5. write_all
# ---------------------------------------------------------------------------


def test_write_all_full_and_partial_writes() -> None:
    """Proves write_all handles partial writes in a loop until buffer is exhausted."""
    written_chunks: list[bytes] = []

    def mock_partial_write(_fd: int, buf: bytes) -> int:
        chunk = buf[:4]
        written_chunks.append(chunk)
        return len(chunk)

    data = b"0123456789ABCDEF"  # 16 bytes = 4 chunks of 4
    with patch("os.write", side_effect=mock_partial_write):
        write_all(999, data)

    assert len(written_chunks) == 4
    assert b"".join(written_chunks) == data


def test_write_all_zero_progress_raises_os_error() -> None:
    """Proves write_all raises OSError if os.write returns 0 without progressing."""
    with patch("os.write", return_value=0), pytest.raises(OSError, match="made no progress"):
        write_all(999, b"data")


def test_write_all_exceeds_max_bytes_raises_value_error() -> None:
    """Proves write_all rejects payloads exceeding max_bytes limit."""
    with pytest.raises(ValueError, match="exceeds maximum permitted limit"):
        write_all(999, b"x" * 100, max_bytes=50)


# ---------------------------------------------------------------------------
# 6. ControlledDescriptors & controlled_stdio
# ---------------------------------------------------------------------------


def test_controlled_descriptors_lifecycle_and_idempotency() -> None:
    """Proves ControlledDescriptors closes preserved descriptors idempotently."""
    out_r, out_w, err_r, err_w = make_pipes()
    null_fd = os.open(os.devnull, os.O_RDWR)

    desc = ControlledDescriptors(out_w, err_w, null_fd)
    desc.close()
    # Second close must be safe no-op
    desc.close()

    for fd in (out_r, err_r):
        with contextlib.suppress(OSError):
            os.close(fd)


def test_controlled_stdio_preserves_clean_io() -> None:
    """Proves controlled_stdio isolates stdout/stderr and permits clean writes on preserved fds."""
    out_r, out_w, err_r, err_w = make_pipes()

    try:
        with controlled_stdio() as desc:
            assert desc.orig_stdout_fd > 0
            assert desc.orig_stderr_fd > 0
            assert desc.null_fd > 0

            # Direct writes to C runtime stdout (1) and stderr (2) go to devnull
            os.write(1, b"CRT_POISON\n")
            os.write(2, b"CRT_POISON\n")

            # Preserved handles can write cleanly to test pipes
            os.write(out_w, b'{"clean":true}\n')
            os.write(err_w, b'{"diag":true}\n')

        os.close(out_w)
        os.close(err_w)

        out_data = drain_pipe(out_r)
        err_data = drain_pipe(err_r)

        assert out_data == b'{"clean":true}\n'
        assert err_data == b'{"diag":true}\n'
        assert b"CRT_POISON" not in out_data
        assert b"CRT_POISON" not in err_data
    finally:
        for fd in (out_r, err_r):
            with contextlib.suppress(OSError):
                os.close(fd)


# ---------------------------------------------------------------------------
# 7. Absence Assertions for Retired / Extracted Import Paths
# ---------------------------------------------------------------------------


def test_retired_import_paths_are_strictly_absent() -> None:
    """Proves retired or extracted symbols are not leaked or re-exported by original modules."""
    import ipc.contracts
    import ipc.progress
    import ipc.stdio

    # ipc.stdio must only expose its public entrypoint contract
    assert hasattr(ipc.stdio, "main")
    assert not hasattr(ipc.stdio, "ControlledDescriptors")
    assert not hasattr(ipc.stdio, "establish_controlled_descriptors")
    assert not hasattr(ipc.stdio, "controlled_stdio")
    assert not hasattr(ipc.stdio, "_redirect_windows_handles")
    assert not hasattr(ipc.stdio, "_restore_windows_handles")
    assert not hasattr(ipc.stdio, "STD_OUTPUT_HANDLE")
    assert not hasattr(ipc.stdio, "STD_ERROR_HANDLE")
    assert not hasattr(ipc.stdio, "write_all")
    assert not hasattr(ipc.stdio, "emit_progress")
    assert not hasattr(ipc.stdio, "emit_fatal_diagnostic")

    # ipc.progress must not re-export generic descriptor write helpers
    assert not hasattr(ipc.progress, "write_all")
    assert not hasattr(ipc.progress, "DEFAULT_MAX_WRITE_BYTES")

    # ipc.contracts must not re-export wire transport primitives
    assert not hasattr(ipc.contracts, "read_bounded_bytes")
    assert not hasattr(ipc.contracts, "decode_strict_json")
    assert not hasattr(ipc.contracts, "serialize_json_line")
    assert not hasattr(ipc.contracts, "write_all")
    assert not hasattr(ipc.contracts, "reject_duplicate_keys")
    assert not hasattr(ipc.contracts, "reject_constant")
    assert not hasattr(ipc.contracts, "WireError")
    assert not hasattr(ipc.contracts, "WirePayloadTooLargeError")
    assert not hasattr(ipc.contracts, "WireJSONDecodeError")
