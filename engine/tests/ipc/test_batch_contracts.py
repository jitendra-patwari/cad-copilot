"""Tests for batch IPC strict contracts, schema validation, and framing."""

from __future__ import annotations

import io
import json

import pytest

from batch.models import BatchRequest
from batch.schemas import (
    REQUEST_SCHEMA_NAME,
    RESPONSE_SCHEMA_NAME,
    get_batch_request_validator,
    get_batch_response_validator,
)
from ipc.batch_contracts import (
    DEFAULT_DIAGNOSTIC_MESSAGES,
    MAX_BATCH_REQUEST_BYTES,
    BatchInvalidRequestError,
    BatchPayloadTooLargeError,
    BatchResponseValidationError,
    build_fallback_failed_response,
    build_rejected_batch_response,
    build_typed_batch_request,
    decode_and_parse_request_json,
    extract_safe_request_id,
    is_safe_request_id,
    read_bounded_request,
    serialize_response,
    validate_response_payload,
)

# ---------------------------------------------------------------------------
# 1. Schema Loading and Meta-Validation
# ---------------------------------------------------------------------------


def test_batch_schema_validators_load_successfully() -> None:
    """Proves request and response schemas are successfully loaded and initialized."""
    req_val = get_batch_request_validator()
    resp_val = get_batch_response_validator()

    assert req_val is not None
    assert resp_val is not None
    assert isinstance(req_val.schema, dict)
    assert isinstance(resp_val.schema, dict)
    assert str(req_val.schema["$id"]).endswith(REQUEST_SCHEMA_NAME)
    assert str(resp_val.schema["$id"]).endswith(RESPONSE_SCHEMA_NAME)


# ---------------------------------------------------------------------------
# 2. Bounded Request Reading
# ---------------------------------------------------------------------------


def test_read_bounded_request_within_limit() -> None:
    """Proves reading within 128 KiB limit succeeds."""
    payload = b'{"contract_version":"1.0"}'
    stream = io.BytesIO(payload)
    result = read_bounded_request(stream)
    assert result == payload


def test_read_bounded_request_at_exact_limit() -> None:
    """Proves reading exactly 128 KiB (131,072 bytes) succeeds."""
    payload = b"x" * MAX_BATCH_REQUEST_BYTES
    stream = io.BytesIO(payload)
    result = read_bounded_request(stream)
    assert len(result) == MAX_BATCH_REQUEST_BYTES


def test_read_bounded_request_exceeds_limit() -> None:
    """Proves reading 128 KiB + 1 byte raises BatchPayloadTooLargeError."""
    payload = b"x" * (MAX_BATCH_REQUEST_BYTES + 1)
    stream = io.BytesIO(payload)
    with pytest.raises(BatchPayloadTooLargeError) as exc_info:
        read_bounded_request(stream)
    assert exc_info.value.code == "PAYLOAD_TOO_LARGE"


def test_read_bounded_request_large_payload() -> None:
    """Proves reading large payload raises BatchPayloadTooLargeError without unbounded memory."""
    payload = b"x" * (MAX_BATCH_REQUEST_BYTES + 10000)
    stream = io.BytesIO(payload)
    with pytest.raises(BatchPayloadTooLargeError):
        read_bounded_request(stream)


# ---------------------------------------------------------------------------
# 3. Strict JSON Decoding and Request-ID Extraction
# ---------------------------------------------------------------------------


def test_decode_and_parse_valid_json() -> None:
    """Proves valid JSON decodes and extracts request_id correctly."""
    raw = b'{"contract_version":"1.0","request_id":"req-batch-001","operation":"export_3d"}'
    parsed, req_id = decode_and_parse_request_json(raw)
    assert parsed["contract_version"] == "1.0"
    assert req_id == "req-batch-001"


def test_decode_and_parse_empty_input_rejected() -> None:
    """Proves empty input raises BatchInvalidRequestError."""
    with pytest.raises(BatchInvalidRequestError):
        decode_and_parse_request_json(b"")


def test_decode_and_parse_utf8_bom_rejected() -> None:
    """Proves UTF-8 BOM prefix is strictly rejected."""
    raw = b'\xef\xbb\xbf{"contract_version":"1.0"}'
    with pytest.raises(BatchInvalidRequestError):
        decode_and_parse_request_json(raw)


def test_decode_and_parse_duplicate_keys_rejected() -> None:
    """Proves duplicate keys in JSON are strictly rejected."""
    raw = b'{"request_id":"req-1","request_id":"req-2"}'
    with pytest.raises(BatchInvalidRequestError):
        decode_and_parse_request_json(raw)


def test_decode_and_parse_non_finite_rejected() -> None:
    """Proves non-finite numbers (NaN, Infinity) are rejected."""
    for token in (b"NaN", b"Infinity", b"-Infinity"):
        raw = b'{"value":' + token + b"}"
        with pytest.raises(BatchInvalidRequestError):
            decode_and_parse_request_json(raw)


def test_decode_and_parse_trailing_garbage_rejected() -> None:
    """Proves trailing data after root object is rejected."""
    raw = b'{"request_id":"req-1"} trailing'
    with pytest.raises(BatchInvalidRequestError):
        decode_and_parse_request_json(raw)


def test_decode_and_parse_non_object_root_rejected() -> None:
    """Proves non-object root JSON is rejected."""
    for raw in (b'["array"]', b'"string"', b"123", b"true"):
        with pytest.raises(BatchInvalidRequestError):
            decode_and_parse_request_json(raw)


def test_decode_and_parse_payload_exceeding_max_bytes() -> None:
    """Proves payloads exceeding MAX_BATCH_REQUEST_BYTES raise BatchPayloadTooLargeError."""
    oversize = b" " * (MAX_BATCH_REQUEST_BYTES + 10)
    with pytest.raises(BatchPayloadTooLargeError):
        decode_and_parse_request_json(oversize)


# ---------------------------------------------------------------------------
# 4. Safe Request ID Helpers
# ---------------------------------------------------------------------------


def test_safe_request_id_validation() -> None:
    """Proves is_safe_request_id and extract_safe_request_id enforce contract rules."""
    assert is_safe_request_id("batch-job-123_test") is True
    assert is_safe_request_id("A" * 96) is True
    assert is_safe_request_id("A" * 97) is False
    assert is_safe_request_id("") is False
    assert is_safe_request_id("req id with spaces") is False
    assert is_safe_request_id("req\nid\nnewlines") is False
    assert is_safe_request_id("req\x00null") is False
    assert is_safe_request_id(12345) is False
    assert is_safe_request_id(None) is False

    assert extract_safe_request_id({"request_id": "valid-id-1"}) == "valid-id-1"
    assert extract_safe_request_id({"request_id": "bad id with space"}) == "unknown"
    assert extract_safe_request_id({}) == "unknown"
    assert extract_safe_request_id("not-a-dict") == "unknown"


# ---------------------------------------------------------------------------
# 5. Typed Batch Request Construction
# ---------------------------------------------------------------------------


def test_build_typed_batch_request_success() -> None:
    """Proves schema-valid payload produces immutable BatchRequest model."""
    valid_payload = {
        "contract_version": "1.0",
        "request_id": "req-typed-001",
        "kind": "batch_operation",
        "operation": {
            "type": "export_3d",
            "formats": ["step", "stl"],
        },
        "input": {
            "root": "E:/models",
            "files": ["part1.par", "part2.psm"],
        },
        "output_root": "E:/output",
    }
    req = build_typed_batch_request(valid_payload)
    assert isinstance(req, BatchRequest)
    assert req.request_id == "req-typed-001"
    assert req.operation.type == "export_3d"
    assert req.operation.formats == ("step", "stl")
    assert req.input.files == ("part1.par", "part2.psm")


def test_build_typed_batch_request_schema_failure() -> None:
    """Proves payload failing schema validation raises BatchInvalidRequestError."""
    invalid_payload = {
        "contract_version": "1.0",
        "request_id": "req-bad-001",
        # Missing required fields: kind, operation, input, output_root
    }
    with pytest.raises(BatchInvalidRequestError) as exc_info:
        build_typed_batch_request(invalid_payload)
    assert exc_info.value.code == "INVALID_SCHEMA"
    assert exc_info.value.request_id == "req-bad-001"


def test_build_typed_batch_request_duplicate_files() -> None:
    """Proves payload with duplicate files raises BatchInvalidRequestError."""
    payload = {
        "contract_version": "1.0",
        "request_id": "req-bad-002",
        "kind": "batch_operation",
        "operation": {
            "type": "export_3d",
            "formats": ["step"],
        },
        "input": {
            "root": "E:/models",
            "files": ["part1.par", "part1.par"],
        },
        "output_root": "E:/output",
    }
    with pytest.raises(BatchInvalidRequestError) as exc_info:
        build_typed_batch_request(payload)
    assert exc_info.value.request_id == "req-bad-002"


# ---------------------------------------------------------------------------
# 6. Fallback Response Builders
# ---------------------------------------------------------------------------


def test_build_rejected_batch_response() -> None:
    """Proves build_rejected_batch_response creates schema-valid rejected response dictionary with curated messages."""
    resp = build_rejected_batch_response(
        code="INVALID_SCHEMA",
        request_id="req-rejected-001",
    )
    assert resp["contract_version"] == "1.0"
    assert resp["request_id"] == "req-rejected-001"
    assert resp["status"] == "rejected"
    assert "summary" not in resp
    assert "results" not in resp
    assert "manifest" not in resp
    assert "unprocessed_files" not in resp
    assert "cancelled_files" not in resp
    assert len(resp["errors"]) == 1
    assert resp["errors"][0]["code"] == "INVALID_SCHEMA"
    assert resp["errors"][0]["message"] == DEFAULT_DIAGNOSTIC_MESSAGES["INVALID_SCHEMA"]

    # Verify no custom message parameter can be supplied
    with pytest.raises(TypeError):
        build_rejected_batch_response("INVALID_SCHEMA", message="SECRET C:/Users/demo")  # type: ignore[call-arg]

    # Validate against schema
    validate_response_payload(resp)


def test_build_rejected_batch_response_with_unsafe_id() -> None:
    """Proves build_rejected_batch_response sanitizes unsafe request IDs to 'unknown'."""
    resp = build_rejected_batch_response(
        code="PAYLOAD_TOO_LARGE",
        request_id="bad id with spaces\n",
    )
    assert resp["request_id"] == "unknown"
    assert resp["errors"][0]["message"] == DEFAULT_DIAGNOSTIC_MESSAGES["PAYLOAD_TOO_LARGE"]
    validate_response_payload(resp)


def test_build_fallback_failed_response() -> None:
    """Proves build_fallback_failed_response creates schema-valid failed response dictionary with curated messages."""
    resp = build_fallback_failed_response(
        code="INTERNAL_ERROR",
        request_id="req-failed-001",
    )
    assert resp["contract_version"] == "1.0"
    assert resp["request_id"] == "req-failed-001"
    assert resp["status"] == "failed"
    assert "summary" not in resp
    assert len(resp["errors"]) == 1
    assert resp["errors"][0]["code"] == "INTERNAL_ERROR"
    assert resp["errors"][0]["message"] == DEFAULT_DIAGNOSTIC_MESSAGES["INTERNAL_ERROR"]

    # Verify no custom message parameter can be supplied
    with pytest.raises(TypeError):
        build_fallback_failed_response("INTERNAL_ERROR", message="SECRET C:/Users/demo")  # type: ignore[call-arg]

    # Validate against schema
    validate_response_payload(resp)


# ---------------------------------------------------------------------------
# 7. Response Validation and Serialization
# ---------------------------------------------------------------------------


def test_serialize_response_compact_ascii() -> None:
    """Proves serialize_response outputs 7-bit ASCII compact JSON ending with LF."""
    resp = build_rejected_batch_response("INVALID_SCHEMA", request_id="req-ser-001")
    serialized = serialize_response(resp)

    assert serialized.endswith(b"\n")
    assert all(b < 128 for b in serialized)
    assert b" " not in serialized or b": " not in serialized  # compact, no spaces after colons/commas
    parsed = json.loads(serialized.decode("ascii"))
    assert parsed["request_id"] == "req-ser-001"
    assert parsed["status"] == "rejected"


def test_serialize_response_rejects_invalid_schema() -> None:
    """Proves serialize_response rejects schema-invalid response mappings."""
    invalid_resp = {"invalid": "payload"}
    with pytest.raises(BatchResponseValidationError):
        serialize_response(invalid_resp)


def test_serialize_response_rejects_unsafe_request_id() -> None:
    """Proves serialize_response rejects response with unsafe request_id."""
    invalid_resp = {
        "contract_version": "1.0",
        "request_id": "bad id with newline\n",
        "status": "rejected",
        "results": [],
        "errors": [{"code": "INVALID_SCHEMA", "message": "Failed."}],
        "warnings": [],
    }
    with pytest.raises(BatchResponseValidationError):
        serialize_response(invalid_resp)


def test_validate_response_payload_rejects_inconsistent_summary_accounting() -> None:
    """Proves validate_response_payload rejects responses with inconsistent summary accounting."""
    bad_accounting_resp = {
        "contract_version": "1.0",
        "request_id": "req-bad-acc",
        "status": "completed",
        "summary": {
            "total": 2,
            "accepted": 1,
            "partial": 0,
            "failed": 0,
            "unprocessed": 0,
            "cancelled": 0,
        },
        "results": [
            {
                "input": "part1.par",
                "status": "accepted",
                "artifacts": [{"format": "step", "path": "E:/out/part1.step"}],
            }
        ],
        "manifest": {"path": "E:/out/req-bad-acc.batch_manifest.json"},
    }
    with pytest.raises(BatchResponseValidationError):
        validate_response_payload(bad_accounting_resp)


def test_validate_response_payload_enforces_expected_request_id() -> None:
    """Proves validate_response_payload rejects responses with mismatched request_id."""
    valid_resp = build_rejected_batch_response("INVALID_SCHEMA", request_id="req-actual-01")
    # Matching succeeds
    validate_response_payload(valid_resp, expected_request_id="req-actual-01")

    # Mismatched raises BatchResponseValidationError
    with pytest.raises(BatchResponseValidationError, match="does not match expected"):
        validate_response_payload(valid_resp, expected_request_id="req-different-02")
