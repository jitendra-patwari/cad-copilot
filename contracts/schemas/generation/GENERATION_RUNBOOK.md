# CAD Copilot Generation Integration Runbook

This runbook specifies the planned M4 generation subprocess integration for a later Tauri desktop client or local CLI.

**Status:** The generation service and adapter components (M4.1–M4.4) are implemented and verified in the engine workspace. The stdio IPC launcher (`engine/scripts/generate.cmd` / M4.5) remains planned. Commands below are planned M4.5 invocation examples, not currently runnable scripts.

---

## Subprocess Invocation

After the M4 launcher is implemented, the intended invocation from the monorepo root is:

```powershell
engine\scripts\generate.cmd
```

### Process Lifecycle Contract
1. **Request**: Write exactly one UTF-8 JSON request object on `stdin`.
2. **Response**: Read exactly one UTF-8 JSON response object from `stdout`.
3. **Diagnostics**: Treat `stderr` as real-time diagnostic output only. Do not parse `stderr` as structured response JSON.
4. **Exit Codes**: A handled `accepted`, `rejected`, or `failed` response exits with process code `0`.

---

## Example Invocations

### 1. Natural Language Prompt Request
```powershell
$env:CAD_OUTPUT_ROOT = "E:\cad-output"
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

### 2. Deterministic Example Plan Request
```powershell
$env:CAD_OUTPUT_ROOT = "E:\cad-output"
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

---

## Runtime Environment & Prerequisites

### Environment Variables
- `CAD_OUTPUT_ROOT`: Base output directory for generated CAD artifacts.
- `GEMINI_API_KEY`: Optional caller-managed environment variable for Google Gemini API authentication (for future M4.5 `prompt_to_cad` orchestration; not required for deterministic `example_plan` mode).
- `CAD_LLM_MODEL`: Reserved planned environment variable for M4.5 model selection (defaults to `gemini-3.5-flash-lite`; not inspected by the M4.2 adapter).

### System Requirements
- Windows 10/11 x64.
- Python 3.14.3 virtual environment (`.venv\Scripts\python.exe`); other Python minors are not claimed until separately verified.
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
