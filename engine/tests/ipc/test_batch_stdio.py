"""Tests for batch stdio IPC framing, exit policy, and error isolation."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any
from unittest.mock import patch

from batch.execution import BatchProgressUpdate
from batch.models import (
    BatchArtifactRecord,
    BatchFileResult,
    BatchManifestReference,
    BatchRequest,
    BatchResponse,
    BatchSummary,
)
from batch.projection import project_batch_response
from ipc.batch_contracts import MAX_BATCH_REQUEST_BYTES
from ipc.batch_progress import BATCH_FATAL_DIAGNOSTIC_BYTES
from ipc.batch_stdio import _BATCH_FATAL_DIAGNOSTIC_BYTES, main
from tests.ipc.support import run_batch_stdio_in_pipes

# ---------------------------------------------------------------------------
# 1. Argument Handling and Fatal Diagnostic
# ---------------------------------------------------------------------------


def test_batch_stdio_fatal_diagnostic_constant_parity_and_private_ownership() -> None:
    """Proves batch_stdio fallback bytes match canonical batch_progress bytes and is not publicly re-exported."""
    import ipc.batch_stdio

    assert _BATCH_FATAL_DIAGNOSTIC_BYTES == BATCH_FATAL_DIAGNOSTIC_BYTES
    assert "BATCH_FATAL_DIAGNOSTIC_BYTES" not in ipc.batch_stdio.__all__
    assert ipc.batch_stdio.__all__ == ["main"]


def test_batch_main_unexpected_argv_exits_1_with_fatal_diagnostic() -> None:
    """Proves unexpected CLI arguments emit fatal diagnostic on stderr, nothing on stdout, and exit 1."""
    result = run_batch_stdio_in_pipes(argv=["--unexpected-flag"])
    assert result.returncode == 1
    assert result.stdout_bytes == b""
    assert result.stderr_bytes == BATCH_FATAL_DIAGNOSTIC_BYTES


def test_batch_main_public_signature() -> None:
    """Proves main() callable accepts argv."""
    code = main(argv=["--bad"])
    assert code == 1


# ---------------------------------------------------------------------------
# 2. Input Size Enforcement and Early Rejection
# ---------------------------------------------------------------------------


def test_batch_main_oversize_input_rejected_exits_0() -> None:
    """Proves input exceeding 128 KiB emits schema-valid PAYLOAD_TOO_LARGE rejection and exits 0."""
    oversize = b" " * (MAX_BATCH_REQUEST_BYTES + 10)
    result = run_batch_stdio_in_pipes(stdin_data=oversize)

    assert result.returncode == 0
    assert result.stdout_bytes.endswith(b"\n")
    resp = result.stdout_json
    assert resp["status"] == "rejected"
    assert resp["request_id"] == "unknown"
    assert resp["errors"][0]["code"] == "PAYLOAD_TOO_LARGE"


def test_batch_main_empty_input_rejected_exits_0() -> None:
    """Proves empty input emits schema-valid INVALID_SCHEMA rejection and exits 0."""
    result = run_batch_stdio_in_pipes(stdin_data=b"")

    assert result.returncode == 0
    assert result.stdout_bytes.endswith(b"\n")
    resp = result.stdout_json
    assert resp["status"] == "rejected"
    assert resp["request_id"] == "unknown"
    assert resp["errors"][0]["code"] == "INVALID_SCHEMA"


def test_batch_main_malformed_json_rejected_exits_0() -> None:
    """Proves unparseable JSON emits schema-valid INVALID_SCHEMA rejection and exits 0."""
    result = run_batch_stdio_in_pipes(stdin_data=b'{"unterminated": ')

    assert result.returncode == 0
    assert result.stdout_bytes.endswith(b"\n")
    resp = result.stdout_json
    assert resp["status"] == "rejected"
    assert resp["request_id"] == "unknown"
    assert resp["errors"][0]["code"] == "INVALID_SCHEMA"


def test_batch_main_schema_validation_failure_exits_0() -> None:
    """Proves request failing schema validation emits schema-valid INVALID_SCHEMA rejection and exits 0."""
    invalid_req = {
        "contract_version": "1.0",
        "request_id": "req-bad-schema",
        "kind": "batch_operation",
        # Missing operation, input, output_root
    }
    result = run_batch_stdio_in_pipes(stdin_data=json.dumps(invalid_req).encode("utf-8"))

    assert result.returncode == 0
    assert result.stdout_bytes.endswith(b"\n")
    resp = result.stdout_json
    assert resp["status"] == "rejected"
    assert resp["request_id"] == "req-bad-schema"
    assert resp["errors"][0]["code"] == "INVALID_SCHEMA"


# ---------------------------------------------------------------------------
# 3. Successful Execution Flow
# ---------------------------------------------------------------------------


def test_batch_main_successful_execution_flow() -> None:
    """Proves valid request executes through composition handler and emits single response."""
    request_data = {
        "contract_version": "1.0",
        "request_id": "req-exec-001",
        "kind": "batch_operation",
        "operation": {
            "type": "export_3d",
            "formats": ["step"],
        },
        "input": {
            "root": "E:/input",
            "files": ["part1.par"],
        },
        "output_root": "E:/output",
    }

    def mock_composition(
        req: BatchRequest,
        *,
        cancellation_check: Callable[[], bool],
        observer: Callable[[BatchProgressUpdate], None],
    ) -> Mapping[str, Any]:
        # Emit progress events to observer
        observer(
            BatchProgressUpdate(
                request_id=req.request_id,
                phase="batch_started",
                total_files=1,
                completed_files=0,
            )
        )
        observer(
            BatchProgressUpdate(
                request_id=req.request_id,
                phase="batch_finished",
                total_files=1,
                completed_files=1,
            )
        )

        resp = BatchResponse(
            contract_version="1.0",
            request_id=req.request_id,
            status="completed",
            summary=BatchSummary(total=1, accepted=1, partial=0, failed=0, unprocessed=0, cancelled=0),
            results=(
                BatchFileResult(
                    input="part1.par",
                    status="accepted",
                    artifacts=(BatchArtifactRecord(format="step", path="E:/output/part1.step"),),
                ),
            ),
            manifest=BatchManifestReference(path="E:/output/req-exec-001.batch_manifest.json"),
            unprocessed_files=(),
            cancelled_files=(),
            errors=(),
            warnings=(),
        )
        return project_batch_response(resp)

    result = run_batch_stdio_in_pipes(
        stdin_data=json.dumps(request_data).encode("utf-8"),
        composition_handler=mock_composition,
    )

    assert result.returncode == 0
    resp = result.stdout_json
    assert resp["status"] == "completed"
    assert resp["request_id"] == "req-exec-001"

    # Check stderr progress events
    diagnostics = result.stderr_diagnostics
    assert len(diagnostics) == 2
    assert diagnostics[0]["phase"] == "batch_started"
    assert diagnostics[1]["phase"] == "batch_finished"


# ---------------------------------------------------------------------------
# 4. Exception and Fallback Handling
# ---------------------------------------------------------------------------


def test_batch_main_handler_exception_returns_fallback_failed_response() -> None:
    """Proves unhandled exception in composition handler returns schema-valid failed response and exits 0."""
    request_data = {
        "contract_version": "1.0",
        "request_id": "req-crash-001",
        "kind": "batch_operation",
        "operation": {
            "type": "export_3d",
            "formats": ["step"],
        },
        "input": {
            "root": "E:/input",
            "files": ["part1.par"],
        },
        "output_root": "E:/output",
    }

    def crashing_composition(req: BatchRequest, **kwargs: Any) -> Mapping[str, Any]:
        raise RuntimeError("Unexpected engine crash during execution")

    result = run_batch_stdio_in_pipes(
        stdin_data=json.dumps(request_data).encode("utf-8"),
        composition_handler=crashing_composition,
    )

    assert result.returncode == 0
    resp = result.stdout_json
    assert resp["status"] == "failed"
    assert resp["request_id"] == "req-crash-001"
    assert resp["errors"][0]["code"] == "INTERNAL_ERROR"


def test_batch_main_handler_invalid_response_schema_returns_fallback_failed() -> None:
    """Proves schema-invalid dictionary returned by composition handler falls back to failed response."""
    request_data = {
        "contract_version": "1.0",
        "request_id": "req-invalid-resp-001",
        "kind": "batch_operation",
        "operation": {
            "type": "export_3d",
            "formats": ["step"],
        },
        "input": {
            "root": "E:/input",
            "files": ["part1.par"],
        },
        "output_root": "E:/output",
    }

    def invalid_dict_composition(req: BatchRequest, **kwargs: Any) -> Mapping[str, Any]:
        # Missing required fields like contract_version, request_id, status
        return {"garbage": True}

    result = run_batch_stdio_in_pipes(
        stdin_data=json.dumps(request_data).encode("utf-8"),
        composition_handler=invalid_dict_composition,
    )

    assert result.returncode == 0
    resp = result.stdout_json
    assert resp["status"] == "failed"
    assert resp["request_id"] == "req-invalid-resp-001"
    assert resp["errors"][0]["code"] == "INTERNAL_ERROR"


def test_batch_main_keyboard_interrupt_exits_130() -> None:
    """Proves top-level KeyboardInterrupt exits 130 with no stdout."""

    def interrupt_composition(req: BatchRequest, **kwargs: Any) -> Mapping[str, Any]:
        raise KeyboardInterrupt()

    result = run_batch_stdio_in_pipes(
        stdin_data=json.dumps(
            {
                "contract_version": "1.0",
                "request_id": "req-sigint-001",
                "kind": "batch_operation",
                "operation": {"type": "export_3d", "formats": ["step"]},
                "input": {"root": "E:/input", "files": ["part1.par"]},
                "output_root": "E:/output",
            }
        ).encode("utf-8"),
        composition_handler=interrupt_composition,
    )

    assert result.returncode == 130
    assert result.stdout_bytes == b""


def test_batch_main_rejection_does_not_leak_raw_request_details() -> None:
    """Proves rejected responses emit curated diagnostic messages without leaking property names or paths."""
    raw_payload = b'{"contract_version":"1.0","request_id":"req-leak-test","SECRET_C:\\\\Users\\\\demo":true}'
    result = run_batch_stdio_in_pipes(stdin_data=raw_payload)
    assert result.returncode == 0
    resp = result.stdout_json
    assert resp["status"] == "rejected"
    assert resp["request_id"] == "req-leak-test"
    assert resp["errors"][0]["code"] == "INVALID_SCHEMA"
    # Verify no leak of secret property name or paths
    assert "SECRET" not in resp["errors"][0]["message"]
    assert "demo" not in resp["errors"][0]["message"]
    assert "Users" not in resp["errors"][0]["message"]


def test_batch_main_bootstrap_failure_preserves_fatal_diagnostic() -> None:
    """Proves bootstrap failure before descriptors are established emits fatal diagnostic to fd 2."""
    with (
        patch("ipc.batch_stdio._establish_controlled_descriptors", side_effect=OSError("Descriptor setup failed")),
        patch("ipc.batch_stdio._write_all") as mock_write,
    ):
        code = main(argv=[])
        assert code == 1
        mock_write.assert_called_with(2, BATCH_FATAL_DIAGNOSTIC_BYTES)


def test_batch_main_handler_mismatched_request_id_falls_back_to_failed() -> None:
    """Proves composition returning a mismatched request_id falls back to internal error."""
    request_data = {
        "contract_version": "1.0",
        "request_id": "req-expected-001",
        "kind": "batch_operation",
        "operation": {"type": "export_3d", "formats": ["step"]},
        "input": {"root": "E:/input", "files": ["part1.par"]},
        "output_root": "E:/output",
    }

    def mismatched_id_composition(req: BatchRequest, **kwargs: Any) -> Mapping[str, Any]:
        resp = BatchResponse(
            contract_version="1.0",
            request_id="req-different-002",
            status="completed",
            summary=BatchSummary(total=1, accepted=1, partial=0, failed=0, unprocessed=0, cancelled=0),
            results=(
                BatchFileResult(
                    input="part1.par",
                    status="accepted",
                    artifacts=(BatchArtifactRecord(format="step", path="E:/output/part1.step"),),
                ),
            ),
            manifest=BatchManifestReference(path="E:/output/req-different-002.batch_manifest.json"),
        )
        return project_batch_response(resp)

    result = run_batch_stdio_in_pipes(
        stdin_data=json.dumps(request_data).encode("utf-8"),
        composition_handler=mismatched_id_composition,
    )

    assert result.returncode == 0
    resp = result.stdout_json
    assert resp["status"] == "failed"
    assert resp["request_id"] == "req-expected-001"
    assert resp["errors"][0]["code"] == "INTERNAL_ERROR"
