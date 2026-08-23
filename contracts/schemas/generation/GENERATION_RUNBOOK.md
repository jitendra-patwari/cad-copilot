# CAD Copilot Generation Integration Runbook

This runbook guides client integration (Tauri Desktop App, REST clients, and CLI agents) with the CAD Copilot prompt-to-CAD `generation` domain subprocess boundary.

---

## Subprocess Invocation

From the monorepo root or engine directory, invoke the standard Windows execution launcher:

```powershell
engine\scripts\generate.cmd
```

### Process Lifecycle Contract
1. **Request**: Write exactly one UTF-8 JSON request object on `stdin`.
2. **Response**: Read exactly one UTF-8 JSON response object from `stdout`.
3. **Diagnostics**: Treat `stderr` as real-time diagnostic output only. Do not parse `stderr` as structured response JSON.
4. **Exit Codes**: A handled `accepted`, `rejected`, or `failed` response exits with process code `0`.

---

## Example PowerShell Invocation

```powershell
$env:CAD_OUTPUT_ROOT = "E:\cad-output"
@'
{
  "contract_version": "1.0",
  "request_id": "job-cube-001",
  "kind": "prompt_to_cad",
  "unit": "mm",
  "prompt": "Create a 40 mm cube with a centered 12 mm through hole.",
  "visible_artifacts": {
    "formats": ["par", "step", "jpg"]
  },
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

### Required Environment Variables
- `CAD_OUTPUT_ROOT`: Base output directory for generated CAD artifacts.
- `GOOGLE_GENAI_API_KEY` / `OPENAI_API_KEY`: API keys for foundation model providers (or set `CAD_MOCK_MODE=1` for offline mock simulation on macOS/Linux).
- `CAD_LLM_PROVIDER`: Active provider (`google`, `openai`, `mock`).
- `CAD_LLM_MODEL`: Active model identifier (e.g. `gemini-3.7-flash`, `gemini-2.5-flash`, `gpt-4o`).

### System Requirements
- Windows 10/11 x64 (for live Solid Edge COM automation).
- Python 3.11+ virtual environment (`.venv\Scripts\python.exe`).
- Siemens Solid Edge (2020+) installed and licensed (or `CAD_MOCK_MODE=1` for synthetic preview generation).

---

## Timeout Boundaries & Latency Budgets

Recommended timeout budgets for prompt-to-CAD tasks:

| Execution Phase | Timeout | Description |
| :--- | :--- | :--- |
| **LLM Inference & Planning** | **30 seconds** | Multimodal reasoning, AST generation, and fallback model retry |
| **Solid Edge Attach / Warmup** | **15 seconds** | Connecting to running instance or spawning STA process |
| **CAD Feature Lowering** | **30 seconds** | Sketch extrusion, holes, cutouts, coordinate mapping |
| **Artifact Export & Verification** | **15 seconds** | Exporting requested formats (STEP, STL, JPG) and property QA |
| **Total Subprocess Limit** | **120 seconds** | Hard timeout for total job lifecycle before process kill |

---

## Response Handling Strategy

### 1. Status: `accepted`
- Read produced artifacts from `data.artifacts`.
- Load the preview JPG or 3D mesh directly into the Three.js viewport.
- If native `.par` was generated, preserve the file path for subsequent parametric edit sessions (`edit/` domain).
- Surface nonfatal `warnings` in the UI log console.

### 2. Status: `rejected`
- The request was rejected before CAD execution (e.g. `INVALID_SCHEMA`, `UNSUPPORTED_REQUEST`).
- Non-retryable without altering request parameters.
- Display `errors[].message` to the user.

### 3. Status: `failed`
- The request passed schema checks but execution failed (e.g. `CAD_PLAN_REJECTED`, `CAD_EXECUTION_FAILED`, `OUTPUT_PATH_NOT_ALLOWED`).
- Log diagnostic trace from `stderr` for developer support.

---

## Retry Guidelines

* **Safe to Retry**:
  - Transient provider rate-limits (HTTP 429) or quota errors.
  - Solid Edge warm-attach timeout (subsequent run will attach cleanly).
* **Do Not Retry**:
  - `INVALID_SCHEMA` or `UNSUPPORTED_REQUEST`.
  - Geometric contradictions or impossible prompts (`CAD_PLAN_REJECTED`).
  - `OUTPUT_PATH_NOT_ALLOWED`.
