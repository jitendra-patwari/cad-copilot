"""Tests for strict generation stdio IPC protocol orchestration and error handling."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
from typing import Any
from unittest.mock import patch

from application.models import GenerationRequest
from ipc.progress import (
    FATAL_DIAGNOSTIC_MESSAGE,
    PROGRESS_MESSAGES,
    ProgressPhase,
)
from ipc.stdio import (
    ControlledDescriptors,
    _run_stdio,
    main,
)
from tests.ipc.support import drain_pipe, make_pipes, run_stdio_in_pipes


def test_public_main_signature_callable() -> None:
    """Proves the public main() signature accepts argv and _composition_handler."""
    code = main(argv=["--extra-arg"])
    assert code == 1


def test_main_unexpected_argv_exits_1_with_fatal_diagnostic() -> None:
    """Proves unexpected CLI arguments emit fatal diagnostic on stderr, nothing on stdout, and exit 1."""
    result = run_stdio_in_pipes(argv=["--unexpected-flag"])
    assert result.returncode == 1
    assert result.stdout_bytes == b""
    assert result.stderr_bytes.endswith(b"\n")
    diag = json.loads(result.stderr_bytes.decode("ascii"))
    assert diag == {
        "type": "diagnostic",
        "phase": "fatal",
        "message": FATAL_DIAGNOSTIC_MESSAGE,
    }


def test_main_empty_input_rejected() -> None:
    """Proves 0-byte input returns code 0, schema-valid rejection, and skips execution phases."""
    result = run_stdio_in_pipes(stdin_data=b"")
    assert result.returncode == 0
    assert result.stdout_bytes.endswith(b"\n")

    res = result.stdout_json
    assert res["status"] == "rejected"
    assert res["request_id"] == "unknown"
    assert res["errors"][0]["code"] == "INVALID_SCHEMA"

    diagnostics = result.stderr_diagnostics
    assert len(diagnostics) == 2
    assert diagnostics[0]["phase"] == "request_received"
    assert diagnostics[1]["phase"] == "response_ready"


def test_main_oversize_input_rejected() -> None:
    """Proves inputs exceeding 128 KiB return rejected/PAYLOAD_TOO_LARGE with exit 0."""
    oversize_payload = b"x" * 131_073
    result = run_stdio_in_pipes(stdin_data=oversize_payload)
    assert result.returncode == 0
    assert result.stdout_bytes.endswith(b"\n")

    res = result.stdout_json
    assert res["status"] == "rejected"
    assert res["request_id"] == "unknown"
    assert res["errors"][0]["code"] == "PAYLOAD_TOO_LARGE"

    diagnostics = result.stderr_diagnostics
    assert len(diagnostics) == 2
    assert diagnostics[0]["phase"] == "request_received"
    assert diagnostics[1]["phase"] == "response_ready"


def test_main_invalid_json_rejected() -> None:
    """Proves malformed JSON returns code 0, schema-valid rejection, and safe request_id."""
    result = run_stdio_in_pipes(stdin_data=b"{malformed json")
    assert result.returncode == 0
    assert result.stdout_bytes.endswith(b"\n")

    res = result.stdout_json
    assert res["status"] == "rejected"
    assert res["request_id"] == "unknown"
    assert res["errors"][0]["code"] == "INVALID_SCHEMA"

    diagnostics = result.stderr_diagnostics
    assert len(diagnostics) == 2
    assert diagnostics[0]["phase"] == "request_received"
    assert diagnostics[1]["phase"] == "response_ready"


def test_main_schema_violation_rejected_with_extracted_request_id() -> None:
    """Proves schema violations preserve the valid extracted request_id."""
    bad_schema_payload = b'{"contract_version": "1.0", "request_id": "req-custom-99", "kind": "unsupported_kind"}'
    result = run_stdio_in_pipes(stdin_data=bad_schema_payload)
    assert result.returncode == 0
    assert result.stdout_bytes.endswith(b"\n")

    res = result.stdout_json
    assert res["status"] == "rejected"
    assert res["request_id"] == "req-custom-99"
    assert res["errors"][0]["code"] == "INVALID_SCHEMA"

    diagnostics = result.stderr_diagnostics
    assert len(diagnostics) == 2
    assert diagnostics[0]["phase"] == "request_received"
    assert diagnostics[1]["phase"] == "response_ready"


def test_main_successful_accepted_execution_and_phase_ordering() -> None:
    """Proves valid request executes through all 4 phases and suppresses in-flight prints."""
    req_payload = {
        "contract_version": "1.0",
        "request_id": "req-success-01",
        "kind": "example_plan",
        "unit": "mm",
        "example_id": "spur_gear",
    }
    raw_input = json.dumps(req_payload).encode("utf-8")

    def mock_handler(req: GenerationRequest) -> dict[str, Any]:
        print("INCIDENTAL_STDOUT_PRINT_DURING_GENERATION")
        sys.stderr.write("INCIDENTAL_STDERR_PRINT_DURING_GENERATION\n")
        return {
            "contract_version": "1.0",
            "request_id": req.request_id,
            "status": "accepted",
            "data": {
                "artifacts": [
                    {"type": "native_part", "format": "par", "path": "test.par", "origin": "cad_copilot"},
                    {"type": "geometry_step", "format": "step", "path": "test.step", "origin": "cad_copilot"},
                    {"type": "mesh_stl", "format": "stl", "path": "test.stl", "origin": "cad_copilot"},
                ]
            },
            "warnings": [],
        }

    result = run_stdio_in_pipes(stdin_data=raw_input, composition_handler=mock_handler)
    assert result.returncode == 0
    assert result.stdout_bytes.endswith(b"\n")
    assert b"INCIDENTAL" not in result.stdout_bytes

    res = result.stdout_json
    assert res["status"] == "accepted"
    assert res["request_id"] == "req-success-01"

    diagnostics = result.stderr_diagnostics
    assert len(diagnostics) == 4
    expected_phases: list[ProgressPhase] = [
        "request_received",
        "request_validated",
        "generation_started",
        "response_ready",
    ]
    for idx, phase in enumerate(expected_phases):
        assert diagnostics[idx]["type"] == "progress"
        assert diagnostics[idx]["phase"] == phase
        assert diagnostics[idx]["message"] == PROGRESS_MESSAGES[phase]

    assert b"INCIDENTAL" not in result.stderr_bytes


def test_main_service_returned_valid_rejected_response() -> None:
    """Proves valid service-returned rejected response exits 0 and emits all 4 progress phases."""
    req_payload = {
        "contract_version": "1.0",
        "request_id": "req-rej-res-01",
        "kind": "example_plan",
        "unit": "mm",
        "example_id": "spur_gear",
    }
    raw_input = json.dumps(req_payload).encode("utf-8")

    def rejected_handler(req: GenerationRequest) -> dict[str, Any]:
        return {
            "contract_version": "1.0",
            "request_id": req.request_id,
            "status": "rejected",
            "errors": [{"code": "UNSUPPORTED_REQUEST", "message": "Unsupported design parameter."}],
            "warnings": [],
        }

    result = run_stdio_in_pipes(stdin_data=raw_input, composition_handler=rejected_handler)
    assert result.returncode == 0
    assert result.stdout_bytes.endswith(b"\n")

    res = result.stdout_json
    assert res["status"] == "rejected"
    assert res["request_id"] == "req-rej-res-01"
    assert res["errors"][0]["code"] == "UNSUPPORTED_REQUEST"

    diagnostics = result.stderr_diagnostics
    assert len(diagnostics) == 4
    assert diagnostics[3]["phase"] == "response_ready"


def test_main_service_returned_valid_failed_response() -> None:
    """Proves valid service-returned failed response exits 0 and emits all 4 progress phases."""
    req_payload = {
        "contract_version": "1.0",
        "request_id": "req-fail-res-01",
        "kind": "example_plan",
        "unit": "mm",
        "example_id": "spur_gear",
    }
    raw_input = json.dumps(req_payload).encode("utf-8")

    def failed_handler(req: GenerationRequest) -> dict[str, Any]:
        return {
            "contract_version": "1.0",
            "request_id": req.request_id,
            "status": "failed",
            "errors": [{"code": "OUTPUT_PATH_NOT_ALLOWED", "message": "Target directory collision."}],
            "warnings": [],
        }

    result = run_stdio_in_pipes(stdin_data=raw_input, composition_handler=failed_handler)
    assert result.returncode == 0
    assert result.stdout_bytes.endswith(b"\n")

    res = result.stdout_json
    assert res["status"] == "failed"
    assert res["request_id"] == "req-fail-res-01"
    assert res["errors"][0]["code"] == "OUTPUT_PATH_NOT_ALLOWED"

    diagnostics = result.stderr_diagnostics
    assert len(diagnostics) == 4
    assert diagnostics[3]["phase"] == "response_ready"


def test_main_service_exception_triggers_sanitized_fallback() -> None:
    """Proves unhandled service exception returns code 0, sanitized INTERNAL_ERROR fallback, and 4 phases."""
    req_payload = {
        "contract_version": "1.0",
        "request_id": "req-crash-01",
        "kind": "example_plan",
        "unit": "mm",
        "example_id": "spur_gear",
    }
    raw_input = json.dumps(req_payload).encode("utf-8")

    def crashing_handler(req: GenerationRequest) -> dict[str, Any]:
        raise RuntimeError("Fatal Solid Edge COM disconnect with sensitive details: 0x80010105")

    result = run_stdio_in_pipes(stdin_data=raw_input, composition_handler=crashing_handler)
    assert result.returncode == 0
    assert b"0x80010105" not in result.stdout_bytes
    assert result.stdout_bytes.endswith(b"\n")

    res = result.stdout_json
    assert res["status"] == "failed"
    assert res["request_id"] == "req-crash-01"
    assert res["errors"][0]["code"] == "INTERNAL_ERROR"

    assert b"Traceback" not in result.stderr_bytes
    diagnostics = result.stderr_diagnostics
    assert len(diagnostics) == 4
    assert diagnostics[3]["phase"] == "response_ready"


def test_main_service_invalid_response_triggers_sanitized_fallback() -> None:
    """Proves invalid response from service triggers fallback rather than corrupting wire output."""
    req_payload = {
        "contract_version": "1.0",
        "request_id": "req-bad-res-01",
        "kind": "example_plan",
        "unit": "mm",
        "example_id": "spur_gear",
    }
    raw_input = json.dumps(req_payload).encode("utf-8")

    def bad_response_handler(req: GenerationRequest) -> dict[str, Any]:
        return {"invalid_key": "not a valid schema response"}

    result = run_stdio_in_pipes(stdin_data=raw_input, composition_handler=bad_response_handler)
    assert result.returncode == 0

    res = result.stdout_json
    assert res["status"] == "failed"
    assert res["request_id"] == "req-bad-res-01"
    assert res["errors"][0]["code"] == "INTERNAL_ERROR"


def test_main_service_unsafe_response_id_triggers_sanitized_fallback() -> None:
    """Proves service-returned response with trailing newline request-ID fails validation and falls back."""
    req_payload = {
        "contract_version": "1.0",
        "request_id": "req-clean-01",
        "kind": "example_plan",
        "unit": "mm",
        "example_id": "spur_gear",
    }
    raw_input = json.dumps(req_payload).encode("utf-8")

    def newline_id_handler(req: GenerationRequest) -> dict[str, Any]:
        return {
            "contract_version": "1.0",
            "request_id": "req-clean-01\n",
            "status": "accepted",
            "data": {
                "artifacts": [
                    {"type": "native_part", "format": "par", "path": "p.par", "origin": "cad_copilot"},
                    {"type": "geometry_step", "format": "step", "path": "p.step", "origin": "cad_copilot"},
                    {"type": "mesh_stl", "format": "stl", "path": "p.stl", "origin": "cad_copilot"},
                ]
            },
            "warnings": [],
        }

    result = run_stdio_in_pipes(stdin_data=raw_input, composition_handler=newline_id_handler)
    assert result.returncode == 0

    res = result.stdout_json
    assert res["status"] == "failed"
    assert res["request_id"] == "req-clean-01"
    assert "\n" not in res["request_id"]
    assert res["errors"][0]["code"] == "INTERNAL_ERROR"


def test_main_fallback_builder_exception_treated_as_fatal() -> None:
    """Proves exception within fallback builder is fatal: exit 1, empty stdout, fatal diagnostic."""
    req_payload = {
        "contract_version": "1.0",
        "request_id": "req-fb-fail-01",
        "kind": "example_plan",
        "unit": "mm",
        "example_id": "spur_gear",
    }
    raw_input = json.dumps(req_payload).encode("utf-8")

    def crashing_handler(req: GenerationRequest) -> dict[str, Any]:
        raise RuntimeError("Service crashed")

    with patch(
        "ipc.contracts.build_fallback_internal_error_response",
        side_effect=RuntimeError("Fallback builder exploded"),
    ):
        result = run_stdio_in_pipes(stdin_data=raw_input, composition_handler=crashing_handler)

    assert result.returncode == 1
    assert result.stdout_bytes == b""
    diagnostics = result.stderr_diagnostics
    assert diagnostics[-1]["type"] == "diagnostic"
    assert diagnostics[-1]["phase"] == "fatal"
    assert diagnostics[-1]["message"] == FATAL_DIAGNOSTIC_MESSAGE


def test_main_fallback_builder_invalid_response_treated_as_fatal() -> None:
    """Proves invalid fallback response failing validation is fatal: exit 1, empty stdout, fatal diagnostic."""
    req_payload = {
        "contract_version": "1.0",
        "request_id": "req-fb-inv-01",
        "kind": "example_plan",
        "unit": "mm",
        "example_id": "spur_gear",
    }
    raw_input = json.dumps(req_payload).encode("utf-8")

    def crashing_handler(req: GenerationRequest) -> dict[str, Any]:
        raise RuntimeError("Service crashed")

    with patch(
        "ipc.contracts.build_fallback_internal_error_response",
        return_value={"invalid_structure": "cannot validate against schema"},
    ):
        result = run_stdio_in_pipes(stdin_data=raw_input, composition_handler=crashing_handler)

    assert result.returncode == 1
    assert result.stdout_bytes == b""
    diagnostics = result.stderr_diagnostics
    assert diagnostics[-1]["type"] == "diagnostic"
    assert diagnostics[-1]["phase"] == "fatal"
    assert diagnostics[-1]["message"] == FATAL_DIAGNOSTIC_MESSAGE


def test_main_keyboard_interrupt_returns_130_and_unwinds_finally() -> None:
    """Proves KeyboardInterrupt cancellation unwinds finally block, produces empty stdout, and exits 130."""
    req_payload = {
        "contract_version": "1.0",
        "request_id": "req-cancel-01",
        "kind": "example_plan",
        "unit": "mm",
        "example_id": "spur_gear",
    }
    raw_input = json.dumps(req_payload).encode("utf-8")

    teardown_executed = False

    def cancelled_handler(req: GenerationRequest) -> dict[str, Any]:
        nonlocal teardown_executed
        try:
            raise KeyboardInterrupt()
        finally:
            teardown_executed = True

    result = run_stdio_in_pipes(stdin_data=raw_input, composition_handler=cancelled_handler)
    assert result.returncode == 130
    assert teardown_executed is True
    assert result.stdout_bytes == b""


def test_main_system_exit_zero_treated_as_fatal() -> None:
    """Proves handler raising SystemExit(0) is treated as fatal: code 1, empty stdout, fatal diagnostic."""
    req_payload = {
        "contract_version": "1.0",
        "request_id": "req-sysexit-0",
        "kind": "example_plan",
        "unit": "mm",
        "example_id": "spur_gear",
    }
    raw_input = json.dumps(req_payload).encode("utf-8")

    def sysexit_handler(req: GenerationRequest) -> dict[str, Any]:
        sys.exit(0)

    result = run_stdio_in_pipes(stdin_data=raw_input, composition_handler=sysexit_handler)
    assert result.returncode == 1
    assert result.stdout_bytes == b""
    diagnostics = result.stderr_diagnostics
    assert diagnostics[-1]["type"] == "diagnostic"
    assert diagnostics[-1]["phase"] == "fatal"


def test_main_system_exit_nonzero_treated_as_fatal() -> None:
    """Proves handler raising SystemExit(42) is treated as fatal: code 1, empty stdout, fatal diagnostic."""
    req_payload = {
        "contract_version": "1.0",
        "request_id": "req-sysexit-42",
        "kind": "example_plan",
        "unit": "mm",
        "example_id": "spur_gear",
    }
    raw_input = json.dumps(req_payload).encode("utf-8")

    def sysexit_handler(req: GenerationRequest) -> dict[str, Any]:
        sys.exit(42)

    result = run_stdio_in_pipes(stdin_data=raw_input, composition_handler=sysexit_handler)
    assert result.returncode == 1
    assert result.stdout_bytes == b""
    diagnostics = result.stderr_diagnostics
    assert diagnostics[-1]["type"] == "diagnostic"
    assert diagnostics[-1]["phase"] == "fatal"


def test_main_stderr_write_failure_exits_1() -> None:
    """Proves failure writing progress to stderr exits 1 without partial stdout response."""
    out_r, out_w, err_r, err_w = make_pipes()
    null_fd = os.open(os.devnull, os.O_RDWR)
    # Close stderr write end prematurely so writing progress raises OSError
    os.close(err_w)

    desc = ControlledDescriptors(out_w, err_w, null_fd)

    req_payload = {
        "contract_version": "1.0",
        "request_id": "req-err-fail-01",
        "kind": "example_plan",
        "unit": "mm",
        "example_id": "spur_gear",
    }
    raw_input = json.dumps(req_payload).encode("utf-8")

    try:
        code = _run_stdio(
            argv=[],
            _stdin_stream=io.BytesIO(raw_input),
            _descriptors=desc,
        )
        assert code == 1

        os.close(out_w)
        stdout_bytes = drain_pipe(out_r)
        assert stdout_bytes == b""
    finally:
        for fd in (out_r, err_r):
            with contextlib.suppress(OSError):
                os.close(fd)
        desc.close()


def test_main_stdout_io_failure_emits_fatal_diagnostic_and_exits_1() -> None:
    """Proves failure writing response to stdout exits 1 and emits fatal diagnostic on stderr."""
    out_r, out_w, err_r, err_w = make_pipes()
    null_fd = os.open(os.devnull, os.O_RDWR)
    # Close stdout write pipe so os.write on it fails with EBADF
    os.close(out_w)

    desc = ControlledDescriptors(out_w, err_w, null_fd)

    req_payload = {
        "contract_version": "1.0",
        "request_id": "req-io-fail-01",
        "kind": "example_plan",
        "unit": "mm",
        "example_id": "spur_gear",
    }
    raw_input = json.dumps(req_payload).encode("utf-8")

    def dummy_handler(req: GenerationRequest) -> dict[str, Any]:
        return {
            "contract_version": "1.0",
            "request_id": req.request_id,
            "status": "accepted",
            "data": {
                "artifacts": [
                    {"type": "native_part", "format": "par", "path": "test.par", "origin": "cad_copilot"},
                    {"type": "geometry_step", "format": "step", "path": "test.step", "origin": "cad_copilot"},
                    {"type": "mesh_stl", "format": "stl", "path": "test.stl", "origin": "cad_copilot"},
                ]
            },
            "warnings": [],
        }

    try:
        code = _run_stdio(
            argv=[],
            _composition_handler=dummy_handler,
            _stdin_stream=io.BytesIO(raw_input),
            _descriptors=desc,
        )
        assert code == 1

        os.close(err_w)
        stderr_bytes = drain_pipe(err_r)
        diagnostics = [json.loads(line) for line in stderr_bytes.decode("ascii").strip().split("\n")]
        assert diagnostics[-1]["type"] == "diagnostic"
        assert diagnostics[-1]["phase"] == "fatal"
    finally:
        for fd in (out_r, err_r):
            with contextlib.suppress(OSError):
                os.close(fd)
        desc.close()


def test_main_serialization_failure_treated_as_fatal() -> None:
    """Proves response serialization failure skips response write, emits fatal diagnostic, and exits 1."""
    req_payload = {
        "contract_version": "1.0",
        "request_id": "req-ser-fail-01",
        "kind": "example_plan",
        "unit": "mm",
        "example_id": "spur_gear",
    }
    raw_input = json.dumps(req_payload).encode("utf-8")

    def dummy_handler(req: GenerationRequest) -> dict[str, Any]:
        return {
            "contract_version": "1.0",
            "request_id": req.request_id,
            "status": "accepted",
            "data": {
                "artifacts": [
                    {"type": "native_part", "format": "par", "path": "test.par", "origin": "cad_copilot"},
                    {"type": "geometry_step", "format": "step", "path": "test.step", "origin": "cad_copilot"},
                    {"type": "mesh_stl", "format": "stl", "path": "test.stl", "origin": "cad_copilot"},
                ]
            },
            "warnings": [],
        }

    with patch("ipc.contracts.serialize_response", side_effect=ValueError("Serialization crash")):
        result = run_stdio_in_pipes(stdin_data=raw_input, composition_handler=dummy_handler)

    assert result.returncode == 1
    assert result.stdout_bytes == b""
    diagnostics = result.stderr_diagnostics
    assert diagnostics[-1]["type"] == "diagnostic"
    assert diagnostics[-1]["phase"] == "fatal"
