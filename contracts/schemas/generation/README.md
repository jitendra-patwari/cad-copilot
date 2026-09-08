# Generation Domain: Public IPC Contract

The `generation` domain defines the wire protocol and contract schemas used by client applications (Tauri Desktop GUI and local CLI) to request 3D parametric generation from the CAD engine.

A client application (such as the Tauri desktop GUI or local CLI launcher) owns UI/UX, user prompt entry, example plan selection, and process invocation. The engine application and driver path owns prompt interpretation, geometric planning, Solid Edge execution, and required artifact generation.

**Status:** Implemented and verified. M3.1 (runtime/lifecycle), M3.2 (Solid Edge executor), M3.3 (artifact export pipeline), M4.1 (generation service orchestration), M4.2 (Gemini plan proposal adapter), M4.3 (deterministic example catalog), M4.4 (canonical run manifest publication), and M4.5 (strict generation stdio IPC, launchers, and packaging) are fully implemented and verified. The process and output descriptions below define active production behavior.

---

## Process Contract

- **Input**: Caller sends one UTF-8 JSON request object conforming to `generation-request.schema.json` via `stdin` (stdio IPC). A raw input cap of 128 KiB (`131,072` bytes) is enforced; larger inputs are rejected immediately with `rejected/PAYLOAD_TOO_LARGE`. The caller closes `stdin` upon sending the request payload.
- **Output**: The engine writes exactly one UTF-8 JSON response object conforming to `generation-response.schema.json` to `stdout`, followed by a newline (`\n`). `stdout` descriptor redirection guarantees zero console contamination from logging, libraries, or native code.
- **Diagnostics**: Non-contract runtime progress events and fatal diagnostics are written to `stderr` exclusively as single-line JSONL objects (`{"type":"progress","phase":"...","message":"..."}`). Exactly 4 ordered progress events occur on handled execution (`request_received`, `request_validated`, `generation_started`, `response_ready`).
- **Exit Status**:
  - `0`: Handled `accepted`, `rejected`, and `failed` requests when the subprocess produces a valid response.
  - `1`: Fatal bootstrap, packaged-schema resource, response serialization, or response-write failure before a valid contract response can be emitted (stdout remains empty).
  - `130`: Caller-initiated cancellation (`CTRL_BREAK_EVENT` / `SIGINT`).
- **Launchers**: Production launchers are the installed console entrypoint `cad-copilot-generate`, the Windows source-run wrapper `engine\scripts\generate.cmd`, or `python -m ipc`.

---

## Required Configuration

* `CAD_OUTPUT_ROOT`: Required base output directory where the CAD engine creates isolated per-request artifact folders named after the `request_id`. Must identify an existing directory; the engine validates containment, directory type, and absence of target collisions before execution.
* `GEMINI_API_KEY`: Optional caller-managed environment variable for Google Gemini API authentication when executing `prompt_to_cad` requests. Not read or required in deterministic `example_plan` mode.
* `CAD_LLM_MODEL`: Optional model identifier for `prompt_to_cad` mode (defaults to `gemini-3.5-flash-lite` if unset; an invalid or whitespace-only value fails safely).

---

## Request Shape

Canonical schema: `generation-request.schema.json` (`$id: "https://cad-copilot.dev/schemas/generation-request.schema.json"`, Draft 2020-12).

The request schema supports two explicit variants via `oneOf`:

### Variant 1: Natural Language (`prompt_to_cad`)
- `contract_version`: must be `"1.0"`
- `request_id`: 1–96 characters, restricted to `^[A-Za-z0-9._-]+$`
- `kind`: must be `"prompt_to_cad"`
- `unit`: must be `"mm"`
- `prompt`: non-empty natural language CAD modeling prompt (1–8000 characters)
- `metadata`: optional object with `source`, `label`, `job_id`

### Variant 2: Deterministic Example Plan (`example_plan`)
- `contract_version`: must be `"1.0"`
- `request_id`: 1–96 characters, restricted to `^[A-Za-z0-9._-]+$`
- `kind`: must be `"example_plan"`
- `unit`: must be `"mm"`
- `example_id`: schema-allowed example identifier (`"spur_gear"`); verified live on Solid Edge with atomic multi-format export and manifest sidecar publication.
- `metadata`: optional object with `source`, `label`, `job_id`

> **Note on Outputs**: Generation outputs are **not** caller-selectable. Successful generation always guarantees the creation of the native Solid Edge part (`.par`), exchange geometry (`step`), and 3D print mesh (`stl`). A preview snapshot (`jpg`) is generated on a best-effort basis.


---

## Response Shape

Canonical schema: `generation-response.schema.json` (`$id: "https://cad-copilot.dev/schemas/generation-response.schema.json"`, Draft 2020-12).

All responses include `contract_version: "1.0"`, `request_id`, and `status`.

### Status: `accepted`
Returned when geometric planning, CAD execution, and required artifact export complete successfully.
- `data.artifacts`: array of produced artifact records. Must contain at least 3 records (`native_part` / `.par`, `geometry_step` / `.step`, `mesh_stl` / `.stl`), with optional 4th record (`preview_image` / `.jpg`).
- `warnings`: array of nonfatal diagnostic strings (e.g. preview generation unavailable).

### Status: `rejected`
Returned when the request is syntactically or semantically invalid before entering CAD execution (e.g. schema validation failure, unsupported or unrecognized field present).
- `errors`: array of error records with `code`, `message`, and optional `field`.
- `warnings`: array of diagnostic warning strings accumulated prior to rejection (empty array when none exist).

### Status: `failed`
Returned when request validation succeeds, but prompt interpretation, geometry lowering, Solid Edge execution, or required artifact export encounters an unrecoverable error.
- `errors`: array of error records with `code` and `message`.
- `warnings`: array of diagnostic warning strings accumulated prior to failure (empty array when none exist).

---

## Artifact Security Policy

1. **Path Traversal Guard**: Every artifact path returned by the engine is resolved and verified. If any artifact path escapes `CAD_OUTPUT_ROOT`, the request fails immediately with error code `OUTPUT_PATH_NOT_ALLOWED`.
2. **Caller Path Isolation**: The engine never accepts caller-chosen artifact write paths.

---

## Error Taxonomy

Standardized error codes:
- `INVALID_SCHEMA` - Malformed JSON or schema constraint violation
- `UNSUPPORTED_REQUEST` - Unsupported request configuration
- `PROMPT_INTERPRETATION_FAILED` - AI adapter could not parse or lower the prompt into geometry
- `CAD_PLAN_REJECTED` - Feature plan violates spatial containment or topology rules
- `CAD_EXECUTION_FAILED` - Solid Edge COM kernel execution error
- `ARTIFACT_EXPORT_FAILED` - Failure exporting a required artifact (`.par`, STEP, STL)
- `OUTPUT_PATH_NOT_ALLOWED` - Output path escaped allowed directory boundary
- `INTERNAL_ERROR` - Unhandled engine exception
- `NATIVE_QA_BLOCKED` - Solid Edge native physical property inspection failed
- `PAYLOAD_TOO_LARGE` - Request payload exceeds size limits
- `TARGET_ALREADY_EXISTS` - Destination output directory or target artifact already exists; existing targets are never overwritten

---

## Fixtures

Golden request and response fixtures are maintained in `fixtures/`:

### Positive Fixtures
- `prompt_default_artifacts.request.json` - Standard prompt-to-CAD request
- `example_spur_gear.request.json` - Deterministic example plan request (`spur_gear`)
- `accepted_default_step_jpg.response.json` - Standard accepted response returning `.par`, `step`, `stl`, and `jpg`
- `accepted_no_preview.response.json` - Accepted response returning `.par`, `step`, and `stl` with a non-fatal preview warning

### Negative & Failure Fixtures
- `rejected_image_field.request.json` - Negative test verifying rejection of unsupported `image` field
- `rejected_visible_artifacts_field.request.json` - Negative test verifying rejection of unsupported `visible_artifacts` selector
- `rejected_unknown_example.request.json` - Negative test verifying rejection of unknown `example_id`
- `failed_missing_step.response.json` - Export failure response with `ARTIFACT_EXPORT_FAILED`
- `failed_output_path_escape.response.json` - Security failure response with `OUTPUT_PATH_NOT_ALLOWED`
- `failed_target_already_exists.response.json` - Conflict failure response with `TARGET_ALREADY_EXISTS`
- `rejected_unsupported_request.response.json` - Controlled rejection response with `UNSUPPORTED_REQUEST`
