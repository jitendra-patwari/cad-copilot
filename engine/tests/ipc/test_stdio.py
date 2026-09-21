"""Tests for generation stdio process boundary, teardown failure isolation, and protocol contracts."""

from __future__ import annotations

import json
from typing import Any

from application.models import GenerationRequest
from interfaces.exceptions import TeardownIncompleteError
from ipc.progress import FATAL_DIAGNOSTIC_MESSAGE
from tests.ipc.support import run_stdio_in_pipes

_VALID_EXAMPLE_REQUEST_BYTES = json.dumps(
    {
        "contract_version": "1.0",
        "request_id": "req-stdio-01",
        "kind": "example_plan",
        "unit": "mm",
        "example_id": "spur_gear",
    }
).encode("utf-8")

_VALID_PROMPT_REQUEST_BYTES = json.dumps(
    {
        "contract_version": "1.0",
        "request_id": "req-stdio-prompt-01",
        "kind": "prompt_to_cad",
        "prompt": "generate a spur gear",
        "unit": "mm",
    }
).encode("utf-8")


def test_stdio_teardown_incomplete_exits_1_with_fatal_diagnostic_and_no_response_ready() -> None:
    """Proves TeardownIncompleteError exits 1, leaves stdout empty, emits fatal diagnostic, and skips response_ready."""

    def handler_raising_teardown_incomplete(req: GenerationRequest) -> dict[str, Any]:
        raise TeardownIncompleteError("CAD runtime teardown completed incompletely")

    result = run_stdio_in_pipes(
        stdin_data=_VALID_EXAMPLE_REQUEST_BYTES,
        composition_handler=handler_raising_teardown_incomplete,
    )

    assert result.returncode == 1
    assert result.stdout_bytes == b""

    diagnostics = result.stderr_diagnostics
    assert len(diagnostics) > 0
    fatal_diags = [d for d in diagnostics if d.get("phase") == "fatal"]
    assert len(fatal_diags) == 1
    assert fatal_diags[0] == {
        "type": "diagnostic",
        "phase": "fatal",
        "message": FATAL_DIAGNOSTIC_MESSAGE,
    }

    phases = [d.get("phase") for d in diagnostics]
    assert "response_ready" not in phases


def test_stdio_teardown_incomplete_bypasses_generic_internal_error_fallback() -> None:
    """Proves TeardownIncompleteError bypasses the generic Exception fallback that builds INTERNAL_ERROR."""

    def crashing_handler(req: GenerationRequest) -> dict[str, Any]:
        raise RuntimeError("Generic unhandled service failure")

    generic_result = run_stdio_in_pipes(
        stdin_data=_VALID_EXAMPLE_REQUEST_BYTES,
        composition_handler=crashing_handler,
    )

    # Generic unhandled exception falls back to schema-valid INTERNAL_ERROR response and exit 0
    assert generic_result.returncode == 0
    resp = generic_result.stdout_json
    assert resp["status"] == "failed"
    assert resp["request_id"] == "req-stdio-01"
    assert resp["errors"][0]["code"] == "INTERNAL_ERROR"
    assert any(d.get("phase") == "response_ready" for d in generic_result.stderr_diagnostics)

    # By contrast, TeardownIncompleteError must bypass the fallback and be fatal (exit 1, no stdout)
    def teardown_handler(req: GenerationRequest) -> dict[str, Any]:
        raise TeardownIncompleteError("Runtime teardown failed")

    teardown_result = run_stdio_in_pipes(
        stdin_data=_VALID_EXAMPLE_REQUEST_BYTES,
        composition_handler=teardown_handler,
    )
    assert teardown_result.returncode == 1
    assert teardown_result.stdout_bytes == b""
    assert not any(d.get("phase") == "response_ready" for d in teardown_result.stderr_diagnostics)
    assert any(d.get("phase") == "fatal" for d in teardown_result.stderr_diagnostics)


def test_stdio_ordinary_prompt_failure_emits_valid_json_and_exits_0() -> None:
    """Proves ordinary handled prompt failure produces a schema-valid response and exits 0."""

    def failed_prompt_handler(req: GenerationRequest) -> dict[str, Any]:
        return {
            "contract_version": "1.0",
            "request_id": req.request_id,
            "status": "failed",
            "errors": [
                {
                    "code": "PROMPT_INTERPRETATION_FAILED",
                    "message": "Model returned unparseable plan.",
                }
            ],
            "warnings": [],
        }

    result = run_stdio_in_pipes(
        stdin_data=_VALID_PROMPT_REQUEST_BYTES,
        composition_handler=failed_prompt_handler,
    )

    assert result.returncode == 0
    assert result.stdout_bytes.endswith(b"\n")
    resp = result.stdout_json
    assert resp["status"] == "failed"
    assert resp["request_id"] == "req-stdio-prompt-01"
    assert resp["errors"][0]["code"] == "PROMPT_INTERPRETATION_FAILED"

    phases = [d.get("phase") for d in result.stderr_diagnostics]
    assert "response_ready" in phases
    assert "fatal" not in phases


def test_stdio_accepted_response_delivered_and_exits_0() -> None:
    """Proves successful generation produces a schema-valid accepted response and exits 0."""

    def accepted_handler(req: GenerationRequest) -> dict[str, Any]:
        return {
            "contract_version": "1.0",
            "request_id": req.request_id,
            "status": "accepted",
            "data": {
                "artifacts": [
                    {
                        "type": "native_part",
                        "format": "par",
                        "path": "test.par",
                        "origin": "cad_copilot",
                    },
                    {
                        "type": "geometry_step",
                        "format": "step",
                        "path": "test.step",
                        "origin": "cad_copilot",
                    },
                    {
                        "type": "mesh_stl",
                        "format": "stl",
                        "path": "test.stl",
                        "origin": "cad_copilot",
                    },
                ]
            },
            "warnings": [],
        }

    result = run_stdio_in_pipes(
        stdin_data=_VALID_EXAMPLE_REQUEST_BYTES,
        composition_handler=accepted_handler,
    )

    assert result.returncode == 0
    assert result.stdout_bytes.endswith(b"\n")
    resp = result.stdout_json
    assert resp["status"] == "accepted"
    assert resp["request_id"] == "req-stdio-01"

    phases = [d.get("phase") for d in result.stderr_diagnostics]
    assert "response_ready" in phases
    assert "fatal" not in phases
