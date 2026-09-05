# CAD Copilot Generation Integration Runbook

This runbook specifies the planned M4 generation subprocess integration for a later Tauri desktop client or local CLI.

**Status:** `engine/scripts/generate.cmd` and the generation service do not exist in the current baseline (M3.1, M3.2, and M3.3 completed and verified). Commands below are planned M4 integration examples, not runnable setup instructions or evidence of generated artifacts.

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

### Required Environment Variables
- `CAD_OUTPUT_ROOT`: Base output directory for generated CAD artifacts.
- `GOOGLE_GENAI_API_KEY`: API key for Gemini foundation model (required for `prompt_to_cad` mode; not required for deterministic `example_plan` mode or M4.1 component testing).
- `CAD_LLM_MODEL`: Configurable model identifier.

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
