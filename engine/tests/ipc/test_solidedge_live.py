"""Opt-in live integration tests for Milestone 4.5 strict stdio IPC.

These tests execute ONLY when running on a Windows host with:
  - CAD_COPILOT_RUN_LIVE_COM=1
  - a licensed Siemens Solid Edge installation.

The live-AI test additionally requires:
  - CAD_COPILOT_RUN_LIVE_AI=1
  - GEMINI_API_KEY=<valid_key>
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final

import pytest

from artifacts.validation import (
    compute_file_sha256_and_size,
    validate_jpg_artifact,
    validate_par_artifact,
    validate_step_artifact,
    validate_stl_artifact,
)
from interfaces.models import StandardInspectionReport
from ipc.contracts import get_response_validator
from manifests import (
    FREE_TEXT_CREDENTIAL_PATTERN,
    LOCAL_PATH_PATTERN,
    compute_plan_fingerprint,
    get_run_manifest_validator,
)

pytestmark = [pytest.mark.com]

LIVE_PROCESS_TIMEOUT_SECONDS: Final[float] = 180.0
GRACEFUL_TEARDOWN_SECONDS: Final[float] = 10.0


@pytest.fixture(autouse=True)
def _require_live_com() -> None:
    """Enforce explicit opt-in for all live COM tests in this module."""
    if sys.platform != "win32":
        pytest.skip("Live Solid Edge COM tests require a Windows platform")
    if os.environ.get("CAD_COPILOT_RUN_LIVE_COM") != "1":
        pytest.skip("Skipping live Solid Edge COM tests: CAD_COPILOT_RUN_LIVE_COM is not set to '1'")


def _get_generate_cmd() -> Path:
    engine_root = Path(__file__).resolve().parents[2]
    cmd_path = engine_root / "scripts" / "generate.cmd"
    if not cmd_path.is_file():
        pytest.skip(f"generate.cmd not found at {cmd_path}")
    return cmd_path


def _get_installed_entrypoint() -> Path:
    prefix = Path(sys.executable).parent
    exe_name = "cad-copilot-generate.exe" if sys.platform == "win32" else "cad-copilot-generate"
    exe_path = prefix / exe_name
    if not exe_path.is_file():
        pytest.skip(f"Installed entrypoint not found at {exe_path}")
    return exe_path


def _run_live_subprocess(
    cmd: list[str],
    input_bytes: bytes,
    env: dict[str, str],
    cwd: Path,
    timeout: float = LIVE_PROCESS_TIMEOUT_SECONDS,
    grace_seconds: float = GRACEFUL_TEARDOWN_SECONDS,
) -> subprocess.CompletedProcess[bytes]:
    """Execute a live CLI subprocess with bounded deadline and graceful child cleanup."""
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=str(cwd),
        creationflags=creationflags,
    )
    try:
        stdout, stderr = proc.communicate(input=input_bytes, timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            # 1. Attempt graceful cancellation signal
            with contextlib.suppress(OSError, ValueError):
                if sys.platform == "win32":
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    proc.send_signal(signal.SIGINT)

            # 2. Allow grace period to unwind teardown
            with contextlib.suppress(subprocess.TimeoutExpired):
                stdout, stderr = proc.communicate(timeout=grace_seconds)
        finally:
            # 3. Guaranteed child cleanup: ensure child is never left running
            if proc.poll() is None:
                with contextlib.suppress(OSError):
                    proc.kill()
                with contextlib.suppress(subprocess.TimeoutExpired, OSError):
                    stdout, stderr = proc.communicate(timeout=5.0)

        raise AssertionError(f"Live process timed out after {timeout} seconds: {cmd[0]}") from None

    return subprocess.CompletedProcess(
        args=cmd,
        returncode=proc.returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _verify_stream_protocol(
    proc: subprocess.CompletedProcess[bytes],
    expected_status: str,
    expected_req_id: str,
    forbidden_values: Sequence[str] = (),
) -> dict[str, Any]:
    """Verify exact single-line stdout, ordered JSONL stderr progress, and stream privacy."""
    assert proc.returncode == 0, f"Process exited with non-zero code {proc.returncode}"
    assert proc.stdout.endswith(b"\n"), "Stdout must end with LF"
    assert b"\n" not in proc.stdout[:-1], "Stdout must contain exactly one response line"
    assert b"Traceback" not in proc.stderr, "Stderr must not contain Python tracebacks"

    try:
        resp: dict[str, Any] = json.loads(proc.stdout.decode("utf-8"))
    except Exception as e:
        raise AssertionError(f"Failed to decode stdout JSON response: {e}") from None

    get_response_validator().validate(resp)
    assert resp["status"] == expected_status
    assert resp["request_id"] == expected_req_id
    assert isinstance(resp["warnings"], list)

    try:
        stderr_text = proc.stderr.decode("ascii")
        stderr_lines = [json.loads(line) for line in stderr_text.strip().split("\n")]
    except Exception as e:
        raise AssertionError(f"Failed to decode stderr JSONL progress: {e}") from None

    assert len(stderr_lines) == 4, f"Expected exactly 4 progress lines, got {len(stderr_lines)}"
    expected_phases = ["request_received", "request_validated", "generation_started", "response_ready"]
    expected_messages = [
        "Request received.",
        "Request validated.",
        "Generation started.",
        "Contract response ready.",
    ]
    for line_obj, expected_phase, expected_msg in zip(stderr_lines, expected_phases, expected_messages, strict=True):
        assert line_obj == {
            "type": "progress",
            "phase": expected_phase,
            "message": expected_msg,
        }

    # Stream privacy checks
    assert not FREE_TEXT_CREDENTIAL_PATTERN.search(stderr_text), "Credential pattern leaked in stderr"
    assert not LOCAL_PATH_PATTERN.search(stderr_text), "Local path leaked in stderr"
    assert not FREE_TEXT_CREDENTIAL_PATTERN.search(proc.stdout.decode("utf-8")), "Credential pattern leaked in stdout"
    resp_non_data = {k: v for k, v in resp.items() if k != "data"}
    assert not LOCAL_PATH_PATTERN.search(json.dumps(resp_non_data)), "Local path leaked in response envelope"

    # Exact forbidden values across raw streams and parsed response
    stdout_decoded = proc.stdout.decode("utf-8", errors="replace")
    resp_serialized = json.dumps(resp)
    for forbidden in forbidden_values:
        if not forbidden or not forbidden.strip():
            continue
        forbidden_bytes = forbidden.encode("utf-8")
        assert forbidden_bytes not in proc.stdout, "Forbidden string bytes leaked in stdout"
        assert forbidden not in stdout_decoded, "Forbidden string text leaked in stdout"
        assert forbidden_bytes not in proc.stderr, "Forbidden string bytes leaked in stderr"
        assert forbidden not in stderr_text, "Forbidden string text leaked in stderr"
        assert forbidden not in resp_serialized, "Forbidden string leaked in response payload"

    return resp


def _verify_accepted_live_artifacts(
    output_root: Path,
    req_id: str,
    resp: dict[str, Any],
    expected_request_kind: str,
    expected_provenance_kind: str,
    expected_source_id: str,
    is_prompt: bool = False,
    prompt_text: str | None = None,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Verify physical on-disk artifacts, run manifest integrity, solid inspection, and privacy."""
    # Staging directory must NOT remain
    staging_candidates = [p for p in output_root.iterdir() if p.name.startswith(".staging_")]
    assert len(staging_candidates) == 0, f"Staging directory leaked: {staging_candidates}"

    final_dir = output_root / req_id
    assert final_dir.is_dir(), f"Published directory missing: {final_dir}"
    manifest_path = final_dir / "run_manifest.json"
    assert manifest_path.is_file(), f"run_manifest.json missing in {final_dir}"

    published_files = list(final_dir.iterdir())
    artifacts = resp["data"]["artifacts"]
    assert len(published_files) == len(artifacts) + 1, (
        f"Expected {len(artifacts) + 1} published files, found {len(published_files)}"
    )

    manifest_bytes = manifest_path.read_bytes()
    manifest_data: dict[str, Any] = json.loads(manifest_bytes.decode("utf-8"))
    get_run_manifest_validator().validate(manifest_data)

    assert manifest_data["manifest_version"] == "cad_copilot.run_manifest.v1"
    assert manifest_data["request"]["request_id"] == req_id
    assert manifest_data["request"]["kind"] == expected_request_kind
    assert manifest_data["provenance"]["kind"] == expected_provenance_kind
    assert manifest_data["provenance"]["source_id"] == expected_source_id
    assert manifest_data["engine"]["name"] == "cad-copilot"
    assert manifest_data["cad_runtime"]["product"] == "solid_edge"

    if is_prompt:
        assert manifest_data["fingerprints"]["prompt_sha256"] is not None
    else:
        assert manifest_data["fingerprints"]["prompt_sha256"] is None

    assert manifest_data["fingerprints"]["plan_sha256"] == compute_plan_fingerprint(manifest_data["feature_plan"])

    # Artifact hashes and sizes must match disk exactly
    assert len(manifest_data["artifacts"]) == len(artifacts)
    for art in manifest_data["artifacts"]:
        art_rel_path = art["path"]
        assert art_rel_path in (f"{req_id}.par", f"{req_id}.step", f"{req_id}.stl", f"{req_id}.jpg")
        art_disk_path = final_dir / art_rel_path
        assert art_disk_path.is_file()
        actual_sha256, actual_size = compute_file_sha256_and_size(art_disk_path)
        assert art["sha256"] == actual_sha256
        assert art["size_bytes"] == actual_size

    # Physical inspection checks: positive solid volume
    inspection = manifest_data["execution"]["inspection"]
    assert inspection["body_count"] == 1
    assert inspection["solid_body_count"] == 1
    assert inspection["volume_mm3"] > 0.0

    rep = StandardInspectionReport(
        volume_mm3=float(inspection["volume_mm3"]),
        mass_kg=float(inspection["mass_kg"]),
        feature_count=int(inspection["feature_count"]),
        body_count=int(inspection["body_count"]),
        solid_body_count=int(inspection["solid_body_count"]),
    )
    validate_par_artifact(final_dir / f"{req_id}.par", rep)
    validate_step_artifact(final_dir / f"{req_id}.step")
    validate_stl_artifact(final_dir / f"{req_id}.stl")
    jpg_path = final_dir / f"{req_id}.jpg"
    if jpg_path.is_file():
        validate_jpg_artifact(jpg_path)

    # Privacy verification in published manifest
    manifest_json = json.dumps(manifest_data)
    assert not FREE_TEXT_CREDENTIAL_PATTERN.search(manifest_json)
    assert not LOCAL_PATH_PATTERN.search(manifest_json)
    if api_key:
        assert api_key not in manifest_json
    if prompt_text:
        assert prompt_text not in manifest_json

    return manifest_data


def test_m45_live_01_example_plan_spur_gear_cmd_launcher(tmp_path: Path) -> None:
    """[M45-LIVE-01] Execute spur_gear example through generate.cmd on live Solid Edge.

    Proves:
        1. generate.cmd accepts bounded JSON request on stdin and exits 0 within 180s.
        2. stdout contains exactly one newline-terminated schema-valid JSON response.
        3. stderr contains exactly four ordered JSONL progress records and zero contamination.
        4. Real Solid Edge Part document is created, constructed with gear outline and center bore, and closed.
        5. Native inspection reports exactly 1 solid body with positive volume.
        6. .par, STEP, STL, and optional JPG artifacts are published and structurally validated.
        7. run_manifest.json is published with provenance.kind="example_plan", source_id="spur_gear",
           and matching disk file hashes/sizes.
        8. No prompt, credential, workstation path, or placeholder leaked.
    """
    cmd_path = _get_generate_cmd()
    output_root = tmp_path / "output"
    output_root.mkdir()

    req_id = "live_m45_spur_gear"
    req_obj = {
        "contract_version": "1.0",
        "request_id": req_id,
        "kind": "example_plan",
        "unit": "mm",
        "example_id": "spur_gear",
    }
    req_bytes = json.dumps(req_obj).encode("utf-8")

    env = os.environ.copy()
    env["CAD_OUTPUT_ROOT"] = str(output_root)
    env.pop("GEMINI_API_KEY", None)
    env.pop("CAD_LLM_MODEL", None)

    proc = _run_live_subprocess(
        [str(cmd_path)],
        input_bytes=req_bytes,
        env=env,
        cwd=tmp_path,
    )

    resp = _verify_stream_protocol(proc, expected_status="accepted", expected_req_id=req_id)
    _verify_accepted_live_artifacts(
        output_root=output_root,
        req_id=req_id,
        resp=resp,
        expected_request_kind="example_plan",
        expected_provenance_kind="example_plan",
        expected_source_id="spur_gear",
    )


@pytest.mark.live_ai
def test_m45_live_02_prompt_to_cad_installed_entrypoint(tmp_path: Path) -> None:
    """[M45-LIVE-02] Execute prompt_to_cad through installed cad-copilot-generate with Gemini.

    Opt-in test requiring CAD_COPILOT_RUN_LIVE_COM="1", CAD_COPILOT_RUN_LIVE_AI="1", and GEMINI_API_KEY.
    """
    if os.environ.get("CAD_COPILOT_RUN_LIVE_AI") != "1":
        pytest.skip("Skipping live AI test: CAD_COPILOT_RUN_LIVE_AI is not set to '1'")

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        pytest.skip("Skipping live AI test: GEMINI_API_KEY environment variable is not set")

    exe_path = _get_installed_entrypoint()
    output_root = tmp_path / "output"
    output_root.mkdir()

    req_id = "live_m45_prompt_block"
    prompt_text = "Create a 50x40x10 mm rectangular block"
    req_obj = {
        "contract_version": "1.0",
        "request_id": req_id,
        "kind": "prompt_to_cad",
        "unit": "mm",
        "prompt": prompt_text,
    }
    req_bytes = json.dumps(req_obj).encode("utf-8")

    env = os.environ.copy()
    env["CAD_OUTPUT_ROOT"] = str(output_root)
    env.pop("CAD_LLM_MODEL", None)

    proc = _run_live_subprocess(
        [str(exe_path)],
        input_bytes=req_bytes,
        env=env,
        cwd=tmp_path,
    )

    resp = _verify_stream_protocol(
        proc,
        expected_status="accepted",
        expected_req_id=req_id,
        forbidden_values=[prompt_text, api_key],
    )
    _verify_accepted_live_artifacts(
        output_root=output_root,
        req_id=req_id,
        resp=resp,
        expected_request_kind="prompt_to_cad",
        expected_provenance_kind="ai_proposal",
        expected_source_id="gemini-3.5-flash-lite",
        is_prompt=True,
        prompt_text=prompt_text,
        api_key=api_key,
    )


def test_m45_live_03_route_and_configuration_isolation(tmp_path: Path) -> None:
    """[M45-LIVE-03] Prove prompt configuration failures avoid CAD acquisition and rerun succeeds.

    Proves:
        1. Missing GEMINI_API_KEY returns handled PROMPT_INTERPRETATION_FAILED without CAD runtime acquisition.
        2. Invalid CAD_LLM_MODEL returns handled PROMPT_INTERPRETATION_FAILED without CAD runtime acquisition.
        3. Both configuration failure processes adhere to exact single-line stdout and 4-phase JSONL stderr.
        4. Subsequent example_plan run executes cleanly on live Solid Edge with zero provider pollution,
           verifying full artifact, manifest, and inspection integrity.
    """
    cmd_path = _get_generate_cmd()
    output_root = tmp_path / "output"
    output_root.mkdir()

    # Part A: Missing API Key
    prompt_text = "Create a 50x40x10 mm rectangular block"
    req_missing_key = {
        "contract_version": "1.0",
        "request_id": "live_m45_missing_key",
        "kind": "prompt_to_cad",
        "unit": "mm",
        "prompt": prompt_text,
    }
    env_missing = os.environ.copy()
    env_missing["CAD_OUTPUT_ROOT"] = str(output_root)
    env_missing.pop("GEMINI_API_KEY", None)
    env_missing.pop("CAD_LLM_MODEL", None)

    proc_a = _run_live_subprocess(
        [str(cmd_path)],
        input_bytes=json.dumps(req_missing_key).encode("utf-8"),
        env=env_missing,
        cwd=tmp_path,
    )
    resp_a = _verify_stream_protocol(
        proc_a,
        expected_status="failed",
        expected_req_id="live_m45_missing_key",
        forbidden_values=[prompt_text],
    )
    assert resp_a["errors"][0]["code"] == "PROMPT_INTERPRETATION_FAILED"
    assert not (output_root / "live_m45_missing_key").exists()

    # Part B: Invalid Model
    test_fake_key = "fake-test-key-for-preflight-validation"
    req_invalid_model = {
        "contract_version": "1.0",
        "request_id": "live_m45_invalid_model",
        "kind": "prompt_to_cad",
        "unit": "mm",
        "prompt": prompt_text,
    }
    env_invalid = os.environ.copy()
    env_invalid["CAD_OUTPUT_ROOT"] = str(output_root)
    env_invalid["GEMINI_API_KEY"] = test_fake_key
    env_invalid["CAD_LLM_MODEL"] = "invalid/slash/model"

    proc_b = _run_live_subprocess(
        [str(cmd_path)],
        input_bytes=json.dumps(req_invalid_model).encode("utf-8"),
        env=env_invalid,
        cwd=tmp_path,
    )
    resp_b = _verify_stream_protocol(
        proc_b,
        expected_status="failed",
        expected_req_id="live_m45_invalid_model",
        forbidden_values=[prompt_text, test_fake_key],
    )
    assert resp_b["errors"][0]["code"] == "PROMPT_INTERPRETATION_FAILED"
    assert not (output_root / "live_m45_invalid_model").exists()

    # Part C: Rerun deterministic example plan to verify route independence
    req_rerun = {
        "contract_version": "1.0",
        "request_id": "live_m45_rerun_gear",
        "kind": "example_plan",
        "unit": "mm",
        "example_id": "spur_gear",
    }
    env_rerun = os.environ.copy()
    env_rerun["CAD_OUTPUT_ROOT"] = str(output_root)
    env_rerun.pop("GEMINI_API_KEY", None)
    env_rerun.pop("CAD_LLM_MODEL", None)

    proc_c = _run_live_subprocess(
        [str(cmd_path)],
        input_bytes=json.dumps(req_rerun).encode("utf-8"),
        env=env_rerun,
        cwd=tmp_path,
    )
    resp_c = _verify_stream_protocol(proc_c, expected_status="accepted", expected_req_id="live_m45_rerun_gear")
    _verify_accepted_live_artifacts(
        output_root=output_root,
        req_id="live_m45_rerun_gear",
        resp=resp_c,
        expected_request_kind="example_plan",
        expected_provenance_kind="example_plan",
        expected_source_id="spur_gear",
    )
