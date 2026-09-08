# CAD Copilot Generation Integration Runbook

This runbook specifies the production M4 generation subprocess integration for a client application (such as the Tauri desktop client or local CLI tools).

**Status:** Implemented and verified. The generation service (M4.1), optional Gemini proposal adapter (M4.2), deterministic example catalog (M4.3), canonical run manifest publication (M4.4), and strict generation stdio IPC interface and launchers (M4.5) are fully operational. Commands below are executable on Windows workstations with the documented environment.

---

## Subprocess Invocation

The generation engine provides two production launch surfaces:

1. **Source-Run CMD Launcher**:
   ```powershell
   engine\scripts\generate.cmd
   ```
   *Requirement*: Requires an editable installation in the workspace virtual environment (`pip install -e "engine[dev]"` or `pip install -e "engine[dev,gemini]"`). The script performs a quiet preflight check verifying the interpreter, entrypoint, and distribution metadata without modifying `PYTHONPATH`.

2. **Installed Console Entrypoint**:
   ```powershell
   cad-copilot-generate
   ```

Both launchers execute the shared `ipc.stdio:main` entrypoint over strict stdio IPC.

### Process Lifecycle Contract
1. **Request**: Write exactly one UTF-8 JSON request object on `stdin` (capped at 128 KiB / `131,072` bytes; larger inputs are rejected with `rejected/PAYLOAD_TOO_LARGE`) and close `stdin`.
2. **Response**: Read exactly one UTF-8 JSON response object from `stdout`, terminated by LF (`\n`). Stdout redirection guarantees zero console contamination.
3. **Progress & Diagnostics**: `stderr` emits compact single-line JSONL progress events (`{"type":"progress","phase":"...","message":"..."}`) across four canonical phases: `request_received`, `request_validated`, `generation_started`, and `response_ready`. Fatal failures emit a JSONL diagnostic object.
4. **Exit Codes**:
   - `0`: Handled `accepted`, `rejected`, or `failed` response.
   - `1`: Fatal bootstrap, packaged-schema resource, response serialization, or response-write failure (stdout remains empty).
   - `130`: Process cancellation (`CTRL_BREAK_EVENT` / `SIGINT`).
5. **Caller Deadline & Teardown**:
   - The caller enforces an end-to-end deadline (default: **180 seconds**).
   - On timeout or user cancellation, the caller sends `CTRL_BREAK_EVENT` to the child process group.
   - Allow a **10-second grace period** for Python stack unwinding and native Solid Edge document teardown.
   - If the child does not exit after the grace period, terminate only that child process handle. Never terminate unrelated CAD processes.

---

## Example Invocations

### 1. Deterministic Example Plan Request (No Key / No Network)
```powershell
$env:CAD_OUTPUT_ROOT = "C:\cad-output"
@'
{
  "contract_version": "1.0",
  "request_id": "example-spur-gear-001",
  "kind": "example_plan",
  "unit": "mm",
  "example_id": "spur_gear",
  "metadata": {
    "source": "desktop_app",
    "label": "Spur gear example"
  }
}
'@ | engine\scripts\generate.cmd
```

### 2. Natural Language Prompt Request (Requires Gemini API Key)
```powershell
$env:CAD_OUTPUT_ROOT = "C:\cad-output"
$env:GEMINI_API_KEY = "AIzaSy..."
@'
{
  "contract_version": "1.0",
  "request_id": "job-cube-001",
  "kind": "prompt_to_cad",
  "unit": "mm",
  "prompt": "Create a 40 mm cube with a centered 12 mm through hole.",
  "metadata": {
    "source": "desktop_app",
    "label": "User job 001",
    "job_id": "job-cube-001"
  }
}
'@ | engine\scripts\generate.cmd
```

---

## Runtime Environment & Prerequisites

### Environment Variables
- `CAD_OUTPUT_ROOT`: Required directory where the CAD engine creates isolated per-request artifact folders named after the `request_id`. Must identify an existing directory.
- `GEMINI_API_KEY`: Optional caller-managed environment variable for Google Gemini API authentication when running `prompt_to_cad` requests. Not read or required in deterministic `example_plan` mode.
- `CAD_LLM_MODEL`: Optional model identifier for `prompt_to_cad` mode (defaults to `gemini-3.5-flash-lite` if unset).

### System Requirements
- Windows 10/11 x64.
- Python 3.14.3 virtual environment (`.venv\Scripts\python.exe`).
- Siemens Solid Edge® installed and licensed.

---

## Response Handling Strategy

### 1. Status: `accepted`
- Read produced artifacts from `data.artifacts` (contains `.par`, `step`, and `stl`, plus optional `jpg`).
- Display the preview image or status in the desktop UI.
- Surface nonfatal `warnings` in the diagnostic log.

### 2. Status: `rejected`
- The request was rejected before CAD execution (e.g. `INVALID_SCHEMA`, `UNSUPPORTED_REQUEST`).
- Display `errors[].message` to the user.
- Surface nonfatal `warnings` in the diagnostic log (empty array if none exist).

### 3. Status: `failed`
- The request passed schema checks but execution failed (e.g. `CAD_PLAN_REJECTED`, `CAD_EXECUTION_FAILED`, `OUTPUT_PATH_NOT_ALLOWED`).
- Retain sanitized stderr diagnostics for developer support.
- Surface nonfatal `warnings` accumulated prior to failure in the diagnostic log.

* **Do Not Retry**:
  - `INVALID_SCHEMA` or `UNSUPPORTED_REQUEST`.
  - Geometric contradictions or impossible prompts (`CAD_PLAN_REJECTED`).
  - `OUTPUT_PATH_NOT_ALLOWED`.
