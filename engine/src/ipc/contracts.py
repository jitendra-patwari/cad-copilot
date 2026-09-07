"""Canonical JSON Schema validation, strict decoding, and serialization for generation IPC.

Invariants:
    - Bounded Request Size: Rejects raw input exceeding 128 KiB (131,072 bytes).
    - Strict JSON: Rejects duplicate keys, non-finite constants (NaN/Infinity), BOM, and multiple values.
    - Schema Encapsulation: Loads Draft 2020-12 schemas from package data via importlib.resources.
    - Safe Correlation ID: Preserves valid request_id on rejection; falls back to 'unknown'.
    - Stdout Protection: Ensures compact, deterministic, 7-bit ASCII-safe JSON response serialization.
    - Zero CAD/Driver/Provider Imports: Leaf contract parsing independent of execution engine.
"""

from __future__ import annotations

import functools
import importlib.resources
import json
import re
from collections.abc import Mapping
from typing import Any, BinaryIO

from jsonschema.validators import Draft202012Validator

from application.models import (
    ExampleGenerationRequest,
    GenerationRequest,
    PromptGenerationRequest,
)
from application.projection import (
    build_failed_response,
    build_rejected_response,
)

MAX_GENERATION_REQUEST_BYTES: int = 128 * 1024  # 131,072 bytes
READ_LIMIT_BYTES: int = MAX_GENERATION_REQUEST_BYTES + 1  # 131,073 bytes

REQUEST_SCHEMA_NAME: str = "generation-request.schema.json"
RESPONSE_SCHEMA_NAME: str = "generation-response.schema.json"

REQUEST_ID_PATTERN: re.Pattern[str] = re.compile(r"^[A-Za-z0-9._-]+$")


def is_safe_request_id(val: Any) -> bool:
    """Check if a value is a string strictly matching the canonical request-id contract."""
    return isinstance(val, str) and (1 <= len(val) <= 96) and bool(REQUEST_ID_PATTERN.fullmatch(val))


def is_safe_job_id(val: Any) -> bool:
    """Check if a value is a string strictly matching the canonical job-id contract."""
    return isinstance(val, str) and (1 <= len(val) <= 128) and bool(REQUEST_ID_PATTERN.fullmatch(val))


class IPCConfigurationError(Exception):
    """Raised when packaged IPC schema resources cannot be loaded or meta-validated."""


class IPCContractError(Exception):
    """Base exception for IPC contract framing and validation failures."""

    def __init__(self, code: str, message: str, *, request_id: str = "unknown") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.request_id = request_id


class PayloadTooLargeError(IPCContractError):
    """Raised when request payload exceeds the 128 KiB wire limit."""

    def __init__(self, message: str = "Request payload exceeds maximum size limit.") -> None:
        super().__init__("PAYLOAD_TOO_LARGE", message, request_id="unknown")


class InvalidRequestError(IPCContractError):
    """Raised when request payload violates UTF-8, JSON, or request schema constraints."""

    def __init__(
        self,
        message: str = "Request payload failed schema validation.",
        *,
        code: str = "INVALID_SCHEMA",
        request_id: str = "unknown",
    ) -> None:
        super().__init__(code, message, request_id=request_id)


class ResponseValidationError(IPCContractError):
    """Raised when response payload violates the canonical response schema."""

    def __init__(
        self,
        message: str = "Response payload failed schema validation.",
        *,
        request_id: str = "unknown",
    ) -> None:
        super().__init__("INTERNAL_ERROR", message, request_id=request_id)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Parse object pairs into a dictionary, rejecting duplicate keys at any nesting level."""
    res: dict[str, Any] = {}
    for key, value in pairs:
        if key in res:
            raise ValueError(f"Duplicate object key: {key}")
        res[key] = value
    return res


def _reject_constant(val: str) -> Any:
    """Reject non-finite JSON constants such as NaN, Infinity, or -Infinity."""
    raise ValueError(f"JSON non-finite constant '{val}' is not allowed")


@functools.cache
def _load_packaged_schema(resource_name: str) -> dict[str, Any]:
    """Load and compile a JSON Schema Draft 2020-12 resource from package data."""
    try:
        schema_path = importlib.resources.files("ipc").joinpath("schemas", resource_name)
        schema_bytes = schema_path.read_bytes()
    except Exception as exc:
        raise IPCConfigurationError(f"Failed to read packaged schema resource '{resource_name}'") from exc

    try:
        schema_dict: Any = json.loads(schema_bytes.decode("utf-8"))
    except Exception as exc:
        raise IPCConfigurationError(f"Packaged schema resource '{resource_name}' is not valid JSON") from exc

    if not isinstance(schema_dict, dict):
        raise IPCConfigurationError(f"Packaged schema resource '{resource_name}' root must be a JSON object")

    try:
        Draft202012Validator.check_schema(schema_dict)
    except Exception as exc:
        raise IPCConfigurationError(f"Packaged schema '{resource_name}' failed Draft 2020-12 meta-validation") from exc

    return schema_dict


@functools.cache
def get_request_validator() -> Draft202012Validator:
    """Return cached Draft202012Validator for generation requests."""
    return Draft202012Validator(_load_packaged_schema(REQUEST_SCHEMA_NAME))


@functools.cache
def get_response_validator() -> Draft202012Validator:
    """Return cached Draft202012Validator for generation responses."""
    return Draft202012Validator(_load_packaged_schema(RESPONSE_SCHEMA_NAME))


def read_bounded_request(stream: BinaryIO, limit: int = READ_LIMIT_BYTES) -> bytes:
    """Read bytes from a binary input stream up to the wire limit.

    Reads in chunks until EOF or until limit bytes have been accumulated.
    If limit bytes are reached, PayloadTooLargeError is raised immediately,
    ensuring memory usage is strictly bounded at the I/O boundary.

    Args:
        stream: Binary input stream (such as sys.stdin.buffer).
        limit: Maximum bytes to read, defaulting to READ_LIMIT_BYTES (131,073).

    Returns:
        Raw bytes read from stream if strictly within the 128 KiB limit.

    Raises:
        PayloadTooLargeError: If incoming stream contains limit or more bytes.
    """
    chunks: list[bytes] = []
    total_read = 0
    chunk_size = 64 * 1024

    while total_read < limit:
        to_read = min(chunk_size, limit - total_read)
        chunk = stream.read(to_read)
        if not chunk:
            break
        chunks.append(chunk)
        total_read += len(chunk)

    if total_read >= limit:
        raise PayloadTooLargeError()

    return b"".join(chunks)


def extract_safe_request_id(data: Any) -> str:
    """Extract a safe request_id conforming to response-schema patterns, or return 'unknown'."""
    if isinstance(data, dict):
        raw_id = data.get("request_id")
        if is_safe_request_id(raw_id):
            return str(raw_id)
    return "unknown"


def decode_and_parse_request_json(raw_bytes: bytes) -> tuple[dict[str, Any], str]:
    """Strictly decode and parse raw UTF-8 JSON request bytes.

    Args:
        raw_bytes: Complete incoming bytes read from stdin.

    Returns:
        Tuple of (parsed_json_dict, safe_request_id).

    Raises:
        PayloadTooLargeError: If raw_bytes exceeds 128 KiB.
        InvalidRequestError: If input is empty, has BOM, invalid UTF-8, malformed JSON,
                             duplicate keys, non-finite constants, non-object root, or trailing data.
    """
    if len(raw_bytes) > MAX_GENERATION_REQUEST_BYTES:
        raise PayloadTooLargeError()

    if not raw_bytes:
        raise InvalidRequestError("Request payload is empty.")

    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        raise InvalidRequestError("Request payload contains forbidden UTF-8 BOM.")

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InvalidRequestError("Request payload is not valid UTF-8.") from exc

    if not text.strip():
        raise InvalidRequestError("Request payload contains only whitespace.")

    # Strict JSON decoding with duplicate key and non-finite rejection
    try:
        parsed: Any = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except ValueError as exc:
        raise InvalidRequestError(f"Request payload is not valid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise InvalidRequestError("Request payload root must be a JSON object.")

    safe_req_id = extract_safe_request_id(parsed)
    return parsed, safe_req_id


def build_typed_generation_request(payload: dict[str, Any]) -> GenerationRequest:
    """Validate a parsed JSON dictionary against the request schema and construct typed domain model.

    Args:
        payload: Parsed JSON dictionary.

    Returns:
        An immutable ExampleGenerationRequest or PromptGenerationRequest.

    Raises:
        InvalidRequestError: If schema validation fails or typed model validation fails.
    """
    safe_req_id = extract_safe_request_id(payload)
    validator = get_request_validator()

    errors = list(validator.iter_errors(payload))
    if errors:
        first_error = errors[0]
        raise InvalidRequestError(
            f"Request schema validation failed: {first_error.message}",
            request_id=safe_req_id,
        )

    # jsonschema pattern regex in Python re matches before trailing newline; enforce fullmatch
    raw_req_id = payload.get("request_id")
    if not is_safe_request_id(raw_req_id):
        raise InvalidRequestError(
            "Invalid request_id: contains forbidden whitespace, newline, or characters.",
            request_id="unknown",
        )

    kind = payload.get("kind")
    metadata_raw = payload.get("metadata")
    copied_metadata: dict[str, str] | None = None
    if isinstance(metadata_raw, dict):
        if "job_id" in metadata_raw and not is_safe_job_id(metadata_raw["job_id"]):
            raise InvalidRequestError(
                "Invalid metadata.job_id: contains forbidden whitespace, newline, or characters.",
                request_id=safe_req_id,
            )
        copied_metadata = {}
        for k in ("source", "label", "job_id"):
            if k in metadata_raw and isinstance(metadata_raw[k], str):
                copied_metadata[k] = metadata_raw[k]

    try:
        if kind == "example_plan":
            return ExampleGenerationRequest(
                contract_version="1.0",
                request_id=payload["request_id"],
                kind="example_plan",
                unit="mm",
                example_id=payload["example_id"],
                metadata=copied_metadata,
            )
        if kind == "prompt_to_cad":
            return PromptGenerationRequest(
                contract_version="1.0",
                request_id=payload["request_id"],
                kind="prompt_to_cad",
                unit="mm",
                prompt=payload["prompt"],
                metadata=copied_metadata,
            )
        raise InvalidRequestError(f"Unsupported request kind '{kind}'", request_id=safe_req_id)
    except ValueError as exc:
        raise InvalidRequestError(f"Invalid request properties: {exc}", request_id=safe_req_id) from exc


def validate_response_payload(response: Mapping[str, Any]) -> None:
    """Validate a response dictionary against the generation-response Draft 2020-12 schema.

    Raises:
        ResponseValidationError: If response payload fails schema validation.
    """
    safe_req_id = extract_safe_request_id(response)
    validator = get_response_validator()

    errors = list(validator.iter_errors(response))
    if errors:
        first_err = errors[0]
        raise ResponseValidationError(
            f"Response payload violated schema contract: {first_err.message}",
            request_id=safe_req_id,
        )

    raw_req_id = response.get("request_id")
    if not is_safe_request_id(raw_req_id):
        raise ResponseValidationError(
            "Invalid response request_id: contains forbidden whitespace, newline, or characters.",
            request_id="unknown",
        )


def serialize_response(response: Mapping[str, Any]) -> bytes:
    """Validate and serialize a contract response to compact, deterministic, 7-bit ASCII JSON + LF.

    Args:
        response: Response mapping conforming to generation-response schema.

    Returns:
        Encoded UTF-8 bytes terminated with b"\\n".

    Raises:
        ResponseValidationError: If payload fails schema validation or cannot be encoded.
    """
    validate_response_payload(response)

    try:
        compact_text = json.dumps(
            response,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        return compact_text.encode("utf-8") + b"\n"
    except Exception as exc:
        safe_req_id = extract_safe_request_id(response)
        raise ResponseValidationError(f"Failed to serialize response: {exc}", request_id=safe_req_id) from exc


def build_fallback_internal_error_response(request_id: str) -> dict[str, Any]:
    """Produce a safe, schema-valid INTERNAL_ERROR response dict for fatal fallback."""
    valid_id = request_id if is_safe_request_id(request_id) else "unknown"
    fallback: dict[str, Any] = build_failed_response(valid_id, "INTERNAL_ERROR")
    validate_response_payload(fallback)
    return fallback


def build_rejected_error_response(code: str, *, request_id: str = "unknown") -> dict[str, Any]:
    """Produce a schema-valid rejected response dict for early IPC rejection."""
    valid_id = request_id if is_safe_request_id(request_id) else "unknown"
    rejected: dict[str, Any] = build_rejected_response(valid_id, code)
    validate_response_payload(rejected)
    return rejected
