"""Tests for IPC strict contracts, schema validation, and framing."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest
from jsonschema.validators import Draft202012Validator

from application.models import ExampleGenerationRequest, PromptGenerationRequest
from ipc.contracts import (
    MAX_GENERATION_REQUEST_BYTES,
    READ_LIMIT_BYTES,
    InvalidRequestError,
    IPCConfigurationError,
    PayloadTooLargeError,
    ResponseValidationError,
    _load_packaged_schema,
    build_fallback_internal_error_response,
    build_rejected_error_response,
    build_typed_generation_request,
    decode_and_parse_request_json,
    extract_safe_request_id,
    get_request_validator,
    get_response_validator,
    is_safe_request_id,
    read_bounded_request,
    serialize_response,
    validate_response_payload,
)

ROOT_DIR = Path(__file__).resolve().parents[3]
CANONICAL_SCHEMAS_DIR = ROOT_DIR / "contracts" / "schemas" / "generation"
PACKAGED_SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "src" / "ipc" / "schemas"


# ---------------------------------------------------------------------------
# 1. Canonical Schema Parity and Meta-Validation
# ---------------------------------------------------------------------------


def test_packaged_schemas_are_byte_identical() -> None:
    """Proves that packaged IPC schemas are byte-for-byte identical to canonical contracts."""
    for name in ("generation-request.schema.json", "generation-response.schema.json"):
        canonical_file = CANONICAL_SCHEMAS_DIR / name
        packaged_file = PACKAGED_SCHEMAS_DIR / name

        assert canonical_file.is_file(), f"Missing canonical schema: {canonical_file}"
        assert packaged_file.is_file(), f"Missing packaged schema: {packaged_file}"

        assert packaged_file.read_bytes() == canonical_file.read_bytes(), (
            f"Packaged schema '{name}' does not match canonical source byte-for-byte"
        )


def test_packaged_schemas_pass_meta_validation() -> None:
    """Proves that packaged schemas conform to JSON Schema Draft 2020-12 meta-schema."""
    for name in ("generation-request.schema.json", "generation-response.schema.json"):
        schema = _load_packaged_schema(name)
        assert isinstance(schema, dict)
        Draft202012Validator.check_schema(schema)


def test_schema_validators_are_cached() -> None:
    """Proves that validator instances are compiled once and cached."""
    v_req_1 = get_request_validator()
    v_req_2 = get_request_validator()
    assert v_req_1 is v_req_2

    v_res_1 = get_response_validator()
    v_res_2 = get_response_validator()
    assert v_res_1 is v_res_2


def test_missing_schema_raises_configuration_error() -> None:
    """Proves that requesting a missing schema resource raises a fatal configuration error."""
    with pytest.raises(IPCConfigurationError, match="Failed to read packaged schema resource"):
        _load_packaged_schema("non-existent.schema.json")


# ---------------------------------------------------------------------------
# 2. Input Size Boundaries & Encoding Checks
# ---------------------------------------------------------------------------


def test_empty_or_whitespace_input_rejected() -> None:
    """Proves that empty or whitespace-only inputs are rejected as INVALID_SCHEMA."""
    with pytest.raises(InvalidRequestError, match="Request payload is empty"):
        decode_and_parse_request_json(b"")

    with pytest.raises(InvalidRequestError, match="Request payload contains only whitespace"):
        decode_and_parse_request_json(b"   \n\t  ")


def test_utf8_bom_rejected() -> None:
    """Proves that input containing a UTF-8 BOM is rejected."""
    raw = b"\xef\xbb\xbf" + json.dumps({"contract_version": "1.0"}).encode("utf-8")
    with pytest.raises(InvalidRequestError, match="forbidden UTF-8 BOM"):
        decode_and_parse_request_json(raw)


def test_invalid_utf8_rejected() -> None:
    """Proves that non-UTF-8 bytes are rejected."""
    with pytest.raises(InvalidRequestError, match="not valid UTF-8"):
        decode_and_parse_request_json(b"\xff\xfe\x00\x01\x02")


def test_payload_size_boundary_exact() -> None:
    """Proves that exactly 128 KiB is accepted, while 128 KiB + 1 byte is rejected immediately."""
    base_data = {
        "contract_version": "1.0",
        "request_id": "size-test",
        "kind": "example_plan",
        "unit": "mm",
        "example_id": "spur_gear",
        "metadata": {"label": "x" * 100},
    }
    encoded = json.dumps(base_data).encode("utf-8")

    # Pad with whitespace up to exact limit 131,072 bytes
    assert len(encoded) < MAX_GENERATION_REQUEST_BYTES
    padding_needed = MAX_GENERATION_REQUEST_BYTES - len(encoded)
    valid_exact = encoded + (b" " * padding_needed)
    assert len(valid_exact) == MAX_GENERATION_REQUEST_BYTES

    # 131,072 bytes proceeds cleanly
    parsed, req_id = decode_and_parse_request_json(valid_exact)
    assert req_id == "size-test"
    assert parsed["example_id"] == "spur_gear"

    # 131,073 bytes raises PayloadTooLargeError immediately
    oversized = valid_exact + b" "
    assert len(oversized) == MAX_GENERATION_REQUEST_BYTES + 1
    with pytest.raises(PayloadTooLargeError) as exc_info:
        decode_and_parse_request_json(oversized)
    assert exc_info.value.code == "PAYLOAD_TOO_LARGE"
    assert exc_info.value.request_id == "unknown"


def test_read_bounded_request_within_limit() -> None:
    """Proves that read_bounded_request reads streams up to exact 128 KiB limit."""
    # Under limit
    data_small = b'{"contract_version": "1.0"}'
    stream_small = io.BytesIO(data_small)
    assert read_bounded_request(stream_small) == data_small

    # Exact limit (131,072 bytes)
    data_exact = b"x" * MAX_GENERATION_REQUEST_BYTES
    stream_exact = io.BytesIO(data_exact)
    assert read_bounded_request(stream_exact) == data_exact


def test_read_bounded_request_exceeds_limit_raises_immediately() -> None:
    """Proves that read_bounded_request bounds read size at I/O boundary without reading full stream."""
    # 131,073 bytes (exact boundary)
    stream_boundary = io.BytesIO(b"x" * READ_LIMIT_BYTES)
    with pytest.raises(PayloadTooLargeError) as exc_boundary:
        read_bounded_request(stream_boundary)
    assert exc_boundary.value.code == "PAYLOAD_TOO_LARGE"

    # Large stream (500 KB): must stop reading after limit bytes, never loading 500 KB into RAM
    stream_large = io.BytesIO(b"x" * 500_000)
    with pytest.raises(PayloadTooLargeError):
        read_bounded_request(stream_large)
    # Stream position should not exceed READ_LIMIT_BYTES
    assert stream_large.tell() == READ_LIMIT_BYTES


def test_escaped_unicode_prompt_fits_128k_limit() -> None:
    """Proves that a maximum 8,000-char prompt with escaped supplementary Unicode fits under 128 KiB and builds typed model."""
    # Each supplementary char escaped as \uD83D\uDE00 is 12 bytes
    supplementary_char = "\U0001f600"
    prompt_text = supplementary_char * 8000  # 8000 chars * 12 bytes ~ 96 KiB
    assert len(prompt_text) == 8000

    data = {
        "contract_version": "1.0",
        "request_id": "unicode-prompt-test",
        "kind": "prompt_to_cad",
        "unit": "mm",
        "prompt": prompt_text,
        "metadata": {
            "source": "desktop_app",
            "label": "Supplementary unicode test",
            "job_id": "job-unicode-001",
        },
    }
    # Serialize with ascii escaping as a caller might transmit
    raw_escaped = json.dumps(data, ensure_ascii=True).encode("utf-8")
    assert len(raw_escaped) <= MAX_GENERATION_REQUEST_BYTES

    parsed, req_id = decode_and_parse_request_json(raw_escaped)
    assert req_id == "unicode-prompt-test"
    assert parsed["prompt"] == prompt_text

    typed_req = build_typed_generation_request(parsed)
    assert isinstance(typed_req, PromptGenerationRequest)
    assert len(typed_req.prompt) == 8000
    assert typed_req.prompt == prompt_text


def test_prompt_overlength_8001_rejected() -> None:
    """Proves that a prompt with 8,001 characters is rejected as INVALID_SCHEMA."""
    prompt_overlength = "x" * 8001
    data = {
        "contract_version": "1.0",
        "request_id": "req-overlength-001",
        "kind": "prompt_to_cad",
        "unit": "mm",
        "prompt": prompt_overlength,
    }
    with pytest.raises(InvalidRequestError) as exc_info:
        build_typed_generation_request(data)
    assert exc_info.value.request_id == "req-overlength-001"
    assert exc_info.value.code == "INVALID_SCHEMA"


# ---------------------------------------------------------------------------
# 3. Strict JSON Decoding Invariants
# ---------------------------------------------------------------------------


def test_duplicate_keys_rejected_at_root() -> None:
    """Proves that duplicate keys at root are rejected."""
    raw = b'{"contract_version": "1.0", "contract_version": "1.0"}'
    with pytest.raises(InvalidRequestError, match="Duplicate object key"):
        decode_and_parse_request_json(raw)


def test_duplicate_keys_rejected_in_nested_object() -> None:
    """Proves that duplicate keys in nested objects are rejected."""
    raw = b'{"contract_version": "1.0", "metadata": {"label": "a", "label": "b"}}'
    with pytest.raises(InvalidRequestError, match="Duplicate object key"):
        decode_and_parse_request_json(raw)


def test_non_finite_constants_rejected() -> None:
    """Proves that NaN, Infinity, -Infinity are rejected."""
    for constant in (b"NaN", b"Infinity", b"-Infinity"):
        raw = b'{"contract_version": "1.0", "val": ' + constant + b"}"
        with pytest.raises(InvalidRequestError, match="non-finite constant"):
            decode_and_parse_request_json(raw)


def test_trailing_data_rejected() -> None:
    """Proves that multiple JSON values or trailing non-whitespace data are rejected."""
    raw = b'{"contract_version": "1.0"} {"second_value": true}'
    with pytest.raises(InvalidRequestError, match="not valid JSON"):
        decode_and_parse_request_json(raw)

    raw_junk = b'{"contract_version": "1.0"} trailing_garbage'
    with pytest.raises(InvalidRequestError, match="not valid JSON"):
        decode_and_parse_request_json(raw_junk)


def test_non_object_root_rejected() -> None:
    """Proves that non-object JSON roots (arrays, scalars, null) are rejected."""
    for non_obj in (b"[1, 2, 3]", b'"string_root"', b"12345", b"true", b"null"):
        with pytest.raises(InvalidRequestError, match="root must be a JSON object"):
            decode_and_parse_request_json(non_obj)


# ---------------------------------------------------------------------------
# 4. Safe Request ID Extraction & Error Recovery
# ---------------------------------------------------------------------------


def test_extract_safe_request_id_valid() -> None:
    """Proves that valid request IDs are extracted correctly."""
    assert extract_safe_request_id({"request_id": "job_01.A-b"}) == "job_01.A-b"
    assert extract_safe_request_id({"request_id": "a" * 96}) == "a" * 96


def test_extract_safe_request_id_fallback_to_unknown() -> None:
    """Proves that invalid or missing IDs fall back safely to 'unknown'."""
    assert extract_safe_request_id({}) == "unknown"
    assert extract_safe_request_id({"request_id": ""}) == "unknown"
    assert extract_safe_request_id({"request_id": "a" * 97}) == "unknown"
    assert extract_safe_request_id({"request_id": "job with spaces"}) == "unknown"
    assert extract_safe_request_id({"request_id": 12345}) == "unknown"
    assert extract_safe_request_id({"request_id": None}) == "unknown"
    assert extract_safe_request_id(None) == "unknown"


def test_request_id_with_trailing_newline_rejected() -> None:
    """Proves that request IDs containing trailing newlines are rejected by fullmatch and typed construction."""
    raw_bad_id = "safe-id\n"

    # 1. is_safe_request_id rejects it
    assert is_safe_request_id(raw_bad_id) is False
    assert is_safe_request_id("safe-id") is True

    # 2. extract_safe_request_id falls back to unknown
    assert extract_safe_request_id({"request_id": raw_bad_id}) == "unknown"

    # 3. Fallback builders use unknown instead of leaking the newline
    fallback = build_fallback_internal_error_response(raw_bad_id)
    assert fallback["request_id"] == "unknown"

    rejected = build_rejected_error_response("INVALID_SCHEMA", request_id=raw_bad_id)
    assert rejected["request_id"] == "unknown"

    # 4. Typed construction rejects the payload with request_id="unknown"
    payload = {
        "contract_version": "1.0",
        "request_id": raw_bad_id,
        "kind": "example_plan",
        "unit": "mm",
        "example_id": "spur_gear",
    }
    with pytest.raises(InvalidRequestError) as exc_info:
        build_typed_generation_request(payload)
    assert exc_info.value.request_id == "unknown"
    assert exc_info.value.code == "INVALID_SCHEMA"


# ---------------------------------------------------------------------------
# 5. Typed Request Construction
# ---------------------------------------------------------------------------


def test_build_example_generation_request_success() -> None:
    """Proves successful construction of typed ExampleGenerationRequest."""
    payload = {
        "contract_version": "1.0",
        "request_id": "req-gear-001",
        "kind": "example_plan",
        "unit": "mm",
        "example_id": "spur_gear",
        "metadata": {
            "source": "desktop_app",
            "label": "Spur gear job",
            "job_id": "job-001",
        },
    }
    req = build_typed_generation_request(payload)
    assert isinstance(req, ExampleGenerationRequest)
    assert req.request_id == "req-gear-001"
    assert req.example_id == "spur_gear"
    assert req.metadata == {
        "source": "desktop_app",
        "label": "Spur gear job",
        "job_id": "job-001",
    }


def test_build_prompt_generation_request_success() -> None:
    """Proves successful construction of typed PromptGenerationRequest."""
    payload = {
        "contract_version": "1.0",
        "request_id": "req-cube-002",
        "kind": "prompt_to_cad",
        "unit": "mm",
        "prompt": "Create a 50mm cube.",
    }
    req = build_typed_generation_request(payload)
    assert isinstance(req, PromptGenerationRequest)
    assert req.request_id == "req-cube-002"
    assert req.prompt == "Create a 50mm cube."
    assert req.metadata is None


def test_build_request_schema_validation_failure() -> None:
    """Proves that schema violations raise InvalidRequestError with preserved safe request_id."""
    # Unknown example_id
    payload_bad_example = {
        "contract_version": "1.0",
        "request_id": "req-bad-001",
        "kind": "example_plan",
        "unit": "mm",
        "example_id": "unknown_widget",
    }
    with pytest.raises(InvalidRequestError) as exc_info:
        build_typed_generation_request(payload_bad_example)
    assert exc_info.value.request_id == "req-bad-001"
    assert exc_info.value.code == "INVALID_SCHEMA"

    # Additional unexpected property
    payload_extra = {
        "contract_version": "1.0",
        "request_id": "req-bad-002",
        "kind": "example_plan",
        "unit": "mm",
        "example_id": "spur_gear",
        "unexpected_field": True,
    }
    with pytest.raises(InvalidRequestError) as exc_info:
        build_typed_generation_request(payload_extra)
    assert exc_info.value.request_id == "req-bad-002"

    # Whitespace-only prompt
    payload_whitespace_prompt = {
        "contract_version": "1.0",
        "request_id": "req-bad-003",
        "kind": "prompt_to_cad",
        "unit": "mm",
        "prompt": "    \t\n  ",
    }
    with pytest.raises(InvalidRequestError) as exc_info:
        build_typed_generation_request(payload_whitespace_prompt)
    assert exc_info.value.request_id == "req-bad-003"


@pytest.mark.parametrize(
    "missing_key",
    [
        "contract_version",
        "request_id",
        "kind",
        "unit",
        "prompt",
    ],
)
def test_prompt_request_missing_required_property_rejected(missing_key: str) -> None:
    """Proves that omitting any required property from a prompt_to_cad request fails schema validation."""
    valid_payload = {
        "contract_version": "1.0",
        "request_id": "req-missing-001",
        "kind": "prompt_to_cad",
        "unit": "mm",
        "prompt": "Create a cylinder.",
    }
    del valid_payload[missing_key]
    with pytest.raises(InvalidRequestError) as exc_info:
        build_typed_generation_request(valid_payload)
    if missing_key == "request_id":
        assert exc_info.value.request_id == "unknown"
    else:
        assert exc_info.value.request_id == "req-missing-001"
    assert exc_info.value.code == "INVALID_SCHEMA"


@pytest.mark.parametrize(
    "missing_key",
    [
        "contract_version",
        "request_id",
        "kind",
        "unit",
        "example_id",
    ],
)
def test_example_request_missing_required_property_rejected(missing_key: str) -> None:
    """Proves that omitting any required property from an example_plan request fails schema validation."""
    valid_payload = {
        "contract_version": "1.0",
        "request_id": "req-missing-002",
        "kind": "example_plan",
        "unit": "mm",
        "example_id": "spur_gear",
    }
    del valid_payload[missing_key]
    with pytest.raises(InvalidRequestError) as exc_info:
        build_typed_generation_request(valid_payload)
    if missing_key == "request_id":
        assert exc_info.value.request_id == "unknown"
    else:
        assert exc_info.value.request_id == "req-missing-002"
    assert exc_info.value.code == "INVALID_SCHEMA"


@pytest.mark.parametrize(
    ("bad_metadata", "expected_code"),
    [
        ({"source": "invalid_source"}, "INVALID_SCHEMA"),
        ({"source": 123}, "INVALID_SCHEMA"),
        ({"label": "a" * 129}, "INVALID_SCHEMA"),
        ({"label": 123}, "INVALID_SCHEMA"),
        ({"job_id": "a" * 129}, "INVALID_SCHEMA"),
        ({"job_id": ""}, "INVALID_SCHEMA"),
        ({"job_id": "job with spaces"}, "INVALID_SCHEMA"),
        ({"job_id": "job@special"}, "INVALID_SCHEMA"),
        ({"job_id": "job-01\n"}, "INVALID_SCHEMA"),
        ({"extra_key": "not_allowed"}, "INVALID_SCHEMA"),
        ("not_a_dict", "INVALID_SCHEMA"),
        (12345, "INVALID_SCHEMA"),
        (["not", "a", "dict"], "INVALID_SCHEMA"),
    ],
)
def test_invalid_metadata_rejected(bad_metadata: Any, expected_code: str) -> None:
    """Proves that metadata violating the schema or regex constraints is rejected."""
    payload = {
        "contract_version": "1.0",
        "request_id": "req-meta-001",
        "kind": "example_plan",
        "unit": "mm",
        "example_id": "spur_gear",
        "metadata": bad_metadata,
    }
    with pytest.raises(InvalidRequestError) as exc_info:
        build_typed_generation_request(payload)
    assert exc_info.value.request_id == "req-meta-001"
    assert exc_info.value.code == expected_code


# ---------------------------------------------------------------------------
# 6. Response Validation & Compact Serialization
# ---------------------------------------------------------------------------


def test_serialize_valid_accepted_response() -> None:
    """Proves valid accepted response serializes to 7-bit ASCII JSON + LF."""
    response = {
        "contract_version": "1.0",
        "request_id": "req-accepted-001",
        "status": "accepted",
        "data": {
            "artifacts": [
                {"type": "native_part", "format": "par", "path": "p.par", "origin": "cad_copilot"},
                {"type": "geometry_step", "format": "step", "path": "p.step", "origin": "cad_copilot"},
                {"type": "mesh_stl", "format": "stl", "path": "p.stl", "origin": "cad_copilot"},
                {"type": "preview_image", "format": "jpg", "path": "p.jpg", "origin": "cad_copilot"},
            ]
        },
        "warnings": ["Warning test with unicode: café"],
    }
    serialized = serialize_response(response)
    assert serialized.endswith(b"\n")
    # Must be 7-bit ASCII (no raw bytes > 127)
    assert all(b < 128 for b in serialized)
    assert b"caf\\u00e9" in serialized

    # Must round-trip cleanly via json.loads
    deserialized = json.loads(serialized.decode("utf-8"))
    assert deserialized["request_id"] == "req-accepted-001"
    assert deserialized["warnings"] == ["Warning test with unicode: café"]


def test_serialize_valid_rejected_and_failed_responses() -> None:
    """Proves rejected and failed response serialization."""
    rejected = build_rejected_error_response("INVALID_SCHEMA", request_id="req-rej-001")
    serialized_rej = serialize_response(rejected)
    assert serialized_rej.endswith(b"\n")
    assert json.loads(serialized_rej)["status"] == "rejected"

    failed = build_fallback_internal_error_response("req-fail-001")
    serialized_fail = serialize_response(failed)
    assert serialized_fail.endswith(b"\n")
    assert json.loads(serialized_fail)["status"] == "failed"


def test_serialize_invalid_response_raises() -> None:
    """Proves that response payloads violating the schema fail with ResponseValidationError."""
    # Missing required 'warnings'
    bad_res_1: dict[str, Any] = {
        "contract_version": "1.0",
        "request_id": "bad-res-001",
        "status": "rejected",
        "errors": [{"code": "INVALID_SCHEMA", "message": "Failed."}],
    }
    with pytest.raises(ResponseValidationError) as exc:
        validate_response_payload(bad_res_1)
    assert exc.value.request_id == "bad-res-001"

    # Invalid status
    bad_res_2 = {
        "contract_version": "1.0",
        "request_id": "bad-res-002",
        "status": "in_progress",
        "warnings": [],
    }
    with pytest.raises(ResponseValidationError):
        serialize_response(bad_res_2)

    # Request ID with trailing newline
    bad_res_newline_id = {
        "contract_version": "1.0",
        "request_id": "safe-id\n",
        "status": "rejected",
        "errors": [{"code": "INVALID_SCHEMA", "message": "Failed."}],
        "warnings": [],
    }
    with pytest.raises(ResponseValidationError) as exc_newline:
        validate_response_payload(bad_res_newline_id)
    assert exc_newline.value.request_id == "unknown"


# ---------------------------------------------------------------------------
# 7. Distribution Wheel Packaging & Isolated Execution
# ---------------------------------------------------------------------------


def test_wheel_distribution_packaging_and_isolated_schema_loading(tmp_path: Path) -> None:
    """Proves that a built wheel packages IPC schemas and operates isolated from repo."""
    engine_root = Path(__file__).resolve().parents[2]
    wheel_dir = tmp_path / "wheel"
    install_dir = tmp_path / "install"
    wheel_dir.mkdir()
    install_dir.mkdir()

    # 1. Build wheel offline without build isolation
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "-w",
            str(wheel_dir),
            str(engine_root),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    wheels = list(wheel_dir.glob("*.whl"))
    assert len(wheels) == 1, f"Expected 1 wheel, found {wheels}"
    wheel_path = wheels[0]

    # 2. Inspect archive entries for IPC schemas
    with zipfile.ZipFile(wheel_path) as z:
        names = z.namelist()
        req_schema = "ipc/schemas/generation-request.schema.json"
        res_schema = "ipc/schemas/generation-response.schema.json"
        assert req_schema in names, f"Missing {req_schema} in wheel: {names}"
        assert res_schema in names, f"Missing {res_schema} in wheel: {names}"

        req_bytes = z.read(req_schema)
        req_dict = json.loads(req_bytes.decode("utf-8"))
        assert req_dict["title"] == "GenerationRequest"

        ep_files = [n for n in names if n.endswith("entry_points.txt")]
        assert len(ep_files) == 1, f"Missing entry_points.txt in wheel: {names}"
        ep_text = z.read(ep_files[0]).decode("utf-8")
        assert "cad-copilot-generate = ipc.stdio:main" in ep_text

    # 3. Install wheel into disposable isolated virtual environment offline
    venv_dir = tmp_path / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--system-site-packages", str(venv_dir)],
        capture_output=True,
        text=True,
        check=True,
    )
    venv_python = venv_dir / "Scripts" / "python.exe" if sys.platform == "win32" else venv_dir / "bin" / "python"
    subprocess.run(
        [
            str(venv_python),
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-index",
            str(wheel_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    site_packages_matches = list(venv_dir.glob("**/site-packages"))
    assert len(site_packages_matches) == 1, f"Expected 1 site-packages, got {site_packages_matches}"
    site_dir = site_packages_matches[0]

    # 4. Exercise isolated installation from outside repository in a clean subprocess
    verify_script = """
import sys
from pathlib import Path

site_dir = sys.argv[1]

# Verify import originates strictly from isolated install location
import ipc.contracts
assert str(Path(ipc.contracts.__file__).resolve()).startswith(str(Path(site_dir).resolve())), (
    f"Imported from {ipc.contracts.__file__}, expected {site_dir}"
)

# Verify schema loading and validation works in isolated environment
v_req = ipc.contracts.get_request_validator()
v_res = ipc.contracts.get_response_validator()

# Verify decode, validation, and serialization
raw_input = b'{"contract_version": "1.0", "request_id": "iso-01", "kind": "example_plan", "unit": "mm", "example_id": "spur_gear"}'
parsed, req_id = ipc.contracts.decode_and_parse_request_json(raw_input)
assert req_id == "iso-01"

typed_req = ipc.contracts.build_typed_generation_request(parsed)
assert typed_req.example_id == "spur_gear"

resp = ipc.contracts.build_rejected_error_response("UNSUPPORTED_REQUEST", request_id=req_id)
serialized = ipc.contracts.serialize_response(resp)
assert b'"rejected"' in serialized
print("ISOLATED_WHEEL_VERIFIED")
"""

    env = os.environ.copy()
    # Remove repo root from cwd
    proc = subprocess.run(
        [str(venv_python), "-c", verify_script, str(site_dir)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, f"Isolated verification failed:\nStdout: {proc.stdout}\nStderr: {proc.stderr}"
    assert "ISOLATED_WHEEL_VERIFIED" in proc.stdout

    # 5. Exercise the installed console entrypoint from the isolated wheel installation
    entrypoint_candidates = [
        f for f in venv_dir.glob("**/cad-copilot-generate*") if f.is_file() and not f.name.endswith(".py")
    ]
    assert len(entrypoint_candidates) >= 1, f"Installed entrypoint not found in {venv_dir}"
    installed_exe = entrypoint_candidates[0]

    # A) Verify unexpected argument fails with fatal diagnostic
    proc_fatal = subprocess.run(
        [str(installed_exe), "--unsupported-arg"],
        capture_output=True,
        env=env,
        cwd=str(tmp_path),
    )
    assert proc_fatal.returncode == 1
    assert proc_fatal.stdout == b""
    assert b"Traceback" not in proc_fatal.stderr
    diag = json.loads(proc_fatal.stderr.decode("ascii").strip())
    assert diag == {
        "type": "diagnostic",
        "phase": "fatal",
        "message": "Generation process failed before a contract response could be produced.",
    }

    # B) Verify handled request execution on stdin through the installed launch surface
    safe_input = json.dumps(
        {
            "contract_version": "1.0",
            "request_id": "iso-entry-01",
            "kind": "example_plan",
            "unit": "mm",
            "example_id": "spur_gear",
        }
    ).encode("utf-8")

    env_safe = env.copy()
    env_safe.pop("CAD_OUTPUT_ROOT", None)

    proc_safe = subprocess.run(
        [str(installed_exe)],
        input=safe_input,
        capture_output=True,
        env=env_safe,
        cwd=str(tmp_path),
    )
    assert proc_safe.returncode == 0
    assert proc_safe.stdout.endswith(b"\n")
    assert b"Traceback" not in proc_safe.stderr

    resp_obj = json.loads(proc_safe.stdout.decode("utf-8"))
    assert resp_obj["status"] == "failed"
    assert resp_obj["request_id"] == "iso-entry-01"
    assert resp_obj["errors"][0]["code"] == "OUTPUT_PATH_NOT_ALLOWED"

    stderr_lines = [json.loads(line) for line in proc_safe.stderr.decode("ascii").strip().split("\n")]
    assert len(stderr_lines) == 4
    assert stderr_lines[0]["phase"] == "request_received"
    assert stderr_lines[3]["phase"] == "response_ready"
