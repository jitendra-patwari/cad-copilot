"""Canonical JSON Schema validation, strict decoding, and serialization for batch IPC.

Invariants:
    - Bounded Request Size: Rejects raw input exceeding 128 KiB (131,072 bytes).
    - Strict JSON: Rejects duplicate keys, non-finite constants (NaN/Infinity), BOM, and multiple values.
    - Schema Encapsulation: Validates requests and responses against packaged Draft 2020-12 schemas.
    - Safe Correlation ID: Preserves valid request_id on rejection; falls back to 'unknown'.
    - Stdout Protection: Ensures compact, deterministic, 7-bit ASCII-safe JSON response serialization.
    - Clean Boundary: Decodes wire payloads and builds typed domain models without duplicating domain logic.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, BinaryIO, Final

from batch.models import (
    BatchDiagnostic,
    BatchRequest,
    BatchResponse,
    BatchValidationError,
)
from batch.parsing import parse_batch_request, parse_batch_response
from batch.projection import project_batch_response
from batch.schemas import (
    REQUEST_SCHEMA_NAME,
    RESPONSE_SCHEMA_NAME,
    get_batch_request_validator,
    safe_request_id,
)
from ipc.wire import (
    WireJSONDecodeError as _WireJSONDecodeError,
)
from ipc.wire import (
    WirePayloadTooLargeError as _WirePayloadTooLargeError,
)
from ipc.wire import (
    decode_strict_json as _decode_strict_json,
)
from ipc.wire import (
    read_bounded_bytes as _read_bounded_bytes,
)
from ipc.wire import (
    serialize_json_line as _serialize_json_line,
)

MAX_BATCH_REQUEST_BYTES: Final[int] = 128 * 1024  # 131,072 bytes
READ_LIMIT_BYTES: Final[int] = MAX_BATCH_REQUEST_BYTES + 1  # 131,073 bytes

DEFAULT_DIAGNOSTIC_MESSAGES: Final[dict[str, str]] = {
    "INVALID_SCHEMA": "Batch request payload failed schema validation.",
    "PAYLOAD_TOO_LARGE": "Batch request payload exceeds 128 KiB limit.",
    "UNSUPPORTED_OPERATION": "Requested batch operation is not supported.",
    "UNSUPPORTED_FORMAT": "Requested output format is not supported.",
    "INPUT_ROOT_NOT_FOUND": "Input root directory does not exist or is not accessible.",
    "INPUT_PATH_NOT_ALLOWED": "Input path violates containment or security policy.",
    "INPUT_FILE_NOT_FOUND": "Input file does not exist.",
    "INPUT_EXTENSION_NOT_ALLOWED": "Input file extension is not allowed.",
    "OUTPUT_ROOT_UNAVAILABLE": "Output root directory is unavailable.",
    "OUTPUT_TARGET_COLLISION": "Output target collides with another item or sentinel.",
    "TARGET_ALREADY_EXISTS": "Target output file already exists.",
    "SOLID_EDGE_UNAVAILABLE": "Solid Edge CAD runtime is unavailable.",
    "SOLID_EDGE_UNHEALTHY": "Solid Edge CAD runtime is in an unhealthy state.",
    "DOCUMENT_OPEN_FAILED": "Failed to open CAD document.",
    "ARTIFACT_EXPORT_FAILED": "Failed to export CAD artifact.",
    "DOCUMENT_CLOSE_FAILED": "Failed to close CAD document.",
    "SOURCE_INTEGRITY_FAILED": "Source file integrity verification failed.",
    "MANIFEST_PUBLICATION_FAILED": "Failed to publish batch summary manifest.",
    "INTERNAL_ERROR": "Batch process encountered an internal error.",
    "BATCH_CANCELLED": "Batch execution was cancelled.",
}


class BatchIPCContractError(Exception):
    """Base exception for batch IPC contract framing and validation failures."""

    def __init__(self, code: str, message: str, *, request_id: str = "unknown") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.request_id = request_id


class BatchPayloadTooLargeError(BatchIPCContractError):
    """Raised when batch request payload exceeds the 128 KiB wire limit."""

    def __init__(self, message: str = "Batch request payload exceeds 128 KiB limit.") -> None:
        super().__init__("PAYLOAD_TOO_LARGE", message, request_id="unknown")


class BatchInvalidRequestError(BatchIPCContractError):
    """Raised when batch request payload violates UTF-8, JSON, or request schema constraints."""

    def __init__(
        self,
        message: str = "Batch request payload failed schema validation.",
        *,
        code: str = "INVALID_SCHEMA",
        request_id: str = "unknown",
    ) -> None:
        super().__init__(code, message, request_id=request_id)


class BatchResponseValidationError(BatchIPCContractError):
    """Raised when batch response payload violates the canonical response schema."""

    def __init__(
        self,
        message: str = "Batch response payload failed schema validation.",
        *,
        request_id: str = "unknown",
    ) -> None:
        super().__init__("INTERNAL_ERROR", message, request_id=request_id)


def is_safe_request_id(val: Any) -> bool:
    """Return True if val matches the canonical request-id contract."""
    return safe_request_id({"request_id": val}) == val if isinstance(val, str) else False


def extract_safe_request_id(data: Any) -> str:
    """Extract a safe request_id conforming to contract patterns, or return 'unknown'."""
    return safe_request_id(data)


def read_bounded_request(stream: BinaryIO, limit: int = READ_LIMIT_BYTES) -> bytes:
    """Read bytes from binary input stream up to the wire limit.

    Args:
        stream: Binary input stream (such as sys.stdin.buffer).
        limit: Maximum bytes to read, defaulting to READ_LIMIT_BYTES (131,073).

    Returns:
        Raw bytes read from stream if strictly within the 128 KiB limit.

    Raises:
        BatchPayloadTooLargeError: If incoming stream contains limit or more bytes.
    """
    try:
        return _read_bounded_bytes(stream, limit)
    except _WirePayloadTooLargeError as exc:
        raise BatchPayloadTooLargeError() from exc


def decode_and_parse_request_json(raw_bytes: bytes) -> tuple[dict[str, Any], str]:
    """Strictly decode and parse raw UTF-8 JSON batch request bytes.

    Args:
        raw_bytes: Complete incoming bytes read from stdin.

    Returns:
        Tuple of (parsed_json_dict, safe_request_id).

    Raises:
        BatchPayloadTooLargeError: If raw_bytes exceeds 128 KiB.
        BatchInvalidRequestError: If input is empty, has BOM, invalid UTF-8, malformed JSON,
                                  duplicate keys, non-finite constants, non-object root, or trailing data.
    """
    try:
        parsed = _decode_strict_json(raw_bytes, max_bytes=MAX_BATCH_REQUEST_BYTES)
    except _WirePayloadTooLargeError as exc:
        raise BatchPayloadTooLargeError() from exc
    except _WireJSONDecodeError as exc:
        raise BatchInvalidRequestError(str(exc), code="INVALID_SCHEMA") from exc

    safe_req_id = extract_safe_request_id(parsed)
    return parsed, safe_req_id


def build_typed_batch_request(payload: dict[str, Any]) -> BatchRequest:
    """Validate a parsed JSON dictionary against the batch request schema and construct typed domain model.

    Args:
        payload: Parsed JSON dictionary.

    Returns:
        An immutable, semantically validated BatchRequest.

    Raises:
        BatchInvalidRequestError: If schema validation fails or typed model validation fails.
    """
    safe_req_id = extract_safe_request_id(payload)
    validator = get_batch_request_validator()

    errors = list(validator.iter_errors(payload))
    if errors:
        first_error = errors[0]
        raise BatchInvalidRequestError(
            f"Batch request schema validation failed: {first_error.message}",
            code="INVALID_SCHEMA",
            request_id=safe_req_id,
        )

    try:
        return parse_batch_request(payload)
    except BatchValidationError as exc:
        raise BatchInvalidRequestError(exc.message, code=exc.code, request_id=exc.request_id) from exc
    except Exception as exc:
        raise BatchInvalidRequestError(
            f"Invalid batch request: {exc}",
            code="INVALID_SCHEMA",
            request_id=safe_req_id,
        ) from exc


def build_rejected_batch_response(
    code: str,
    *,
    request_id: str = "unknown",
) -> dict[str, Any]:
    """Produce a canonical schema-valid rejected response dictionary for early IPC rejection.

    The diagnostic message is always strictly selected from curated diagnostic codes
    to eliminate information and path leakage.
    """
    valid_id = request_id if is_safe_request_id(request_id) else "unknown"
    msg = DEFAULT_DIAGNOSTIC_MESSAGES.get(code, "Batch request failed validation.")
    diag = BatchDiagnostic(code=code, message=msg)
    response = BatchResponse(
        contract_version="1.0",
        request_id=valid_id,
        status="rejected",
        summary=None,
        results=(),
        manifest=None,
        unprocessed_files=(),
        cancelled_files=(),
        errors=(diag,),
        warnings=(),
    )
    return project_batch_response(response)


def build_fallback_failed_response(
    code: str = "INTERNAL_ERROR",
    *,
    request_id: str = "unknown",
) -> dict[str, Any]:
    """Produce a canonical schema-valid failed response dictionary for fallback errors.

    The diagnostic message is always strictly selected from curated diagnostic codes
    to eliminate information and path leakage.
    """
    valid_id = request_id if is_safe_request_id(request_id) else "unknown"
    msg = DEFAULT_DIAGNOSTIC_MESSAGES.get(code, "Batch process encountered an internal error.")
    diag = BatchDiagnostic(code=code, message=msg)
    response = BatchResponse(
        contract_version="1.0",
        request_id=valid_id,
        status="failed",
        summary=None,
        results=(),
        manifest=None,
        unprocessed_files=(),
        cancelled_files=(),
        errors=(diag,),
        warnings=(),
    )
    return project_batch_response(response)


def validate_response_payload(
    response: Mapping[str, Any],
    *,
    expected_request_id: str | None = None,
) -> None:
    """Validate a response dictionary against schema contracts, domain semantics, and correlation ID.

    Args:
        response: Response mapping conforming to batch-response schema.
        expected_request_id: Optional request ID that response must correlate with.

    Raises:
        BatchResponseValidationError: If response payload fails schema, semantic, or correlation validation.
    """
    safe_req_id = extract_safe_request_id(response)
    try:
        parsed = parse_batch_response(response)
    except BatchValidationError as exc:
        raise BatchResponseValidationError(
            f"Response payload violated schema contract: {exc.message}",
            request_id=exc.request_id,
        ) from exc
    except Exception as exc:
        raise BatchResponseValidationError(
            f"Response payload failed semantic validation: {exc}",
            request_id=safe_req_id,
        ) from exc

    if not is_safe_request_id(parsed.request_id):
        raise BatchResponseValidationError(
            "Invalid response request_id: contains forbidden whitespace, newline, or characters.",
            request_id="unknown",
        )

    if expected_request_id is not None and parsed.request_id != expected_request_id:
        raise BatchResponseValidationError(
            f"Response request_id '{parsed.request_id}' does not match expected '{expected_request_id}'.",
            request_id=parsed.request_id,
        )


def serialize_response(
    response: Mapping[str, Any],
    *,
    expected_request_id: str | None = None,
) -> bytes:
    """Validate and serialize a batch response to compact, deterministic, 7-bit ASCII JSON + LF.

    Args:
        response: Response mapping conforming to batch-response schema.
        expected_request_id: Optional expected request ID.

    Returns:
        Encoded UTF-8 bytes terminated with b"\\n".

    Raises:
        BatchResponseValidationError: If payload fails validation or cannot be encoded.
    """
    validate_response_payload(response, expected_request_id=expected_request_id)

    try:
        return _serialize_json_line(response)
    except Exception as exc:
        safe_req_id = extract_safe_request_id(response)
        raise BatchResponseValidationError(f"Failed to serialize response: {exc}", request_id=safe_req_id) from exc


__all__ = [
    "DEFAULT_DIAGNOSTIC_MESSAGES",
    "MAX_BATCH_REQUEST_BYTES",
    "READ_LIMIT_BYTES",
    "REQUEST_SCHEMA_NAME",
    "RESPONSE_SCHEMA_NAME",
    "BatchIPCContractError",
    "BatchInvalidRequestError",
    "BatchPayloadTooLargeError",
    "BatchResponseValidationError",
    "build_fallback_failed_response",
    "build_rejected_batch_response",
    "build_typed_batch_request",
    "decode_and_parse_request_json",
    "extract_safe_request_id",
    "is_safe_request_id",
    "read_bounded_request",
    "serialize_response",
    "validate_response_payload",
]
