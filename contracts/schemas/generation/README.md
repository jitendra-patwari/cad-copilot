# Generation Domain: Public IPC Contract

The `generation` domain defines the wire protocol and contract schemas used by client applications (Tauri Desktop GUI and local CLI) to request 3D parametric generation from the CAD engine.

The planned client application owns UI/UX, user prompt entry, example plan selection, and process execution. The planned application/driver path owns prompt interpretation, geometric planning, Solid Edge execution, and required artifact generation.

**Status:** Schemas and fixtures exist. M3.1 (runtime/lifecycle), M3.2 (Solid Edge executor), and M3.3 (artifact export pipeline) are implemented and verified; M4 generation service/launcher is planned. The process and output descriptions below define the target contract, not currently available end-to-end behavior.

---

## Process Contract

- **Input**: Caller sends one UTF-8 JSON request object conforming to `generation-request.schema.json` via `stdin` (stdio IPC).
- **Output**: The engine writes exactly one UTF-8 JSON response object conforming to `generation-response.schema.json` to `stdout`, followed by a newline.
- **Diagnostics**: Non-contract runtime diagnostics and progress logs are written to `stderr` only.
- **Exit Status**: Handled `accepted`, `rejected`, and `failed` requests all return status code `0` on IPC when the subprocess completes normally.

The planned IPC launcher for Milestone 4 is `engine/scripts/generate.cmd` (or CLI entrypoint `cad-copilot-generate`).

---

## Required Configuration

* `CAD_OUTPUT_ROOT`: Output directory where the CAD engine creates isolated per-request artifact folders named after the `request_id`.
* The engine resolves the output root to an absolute path, creates it if necessary, and ensures all emitted artifact paths reside safely within it.

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
- `example_id`: schema-allowed example identifier (`"spur_gear"`); live execution is not established by schema acceptance.
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
Returned when the request is syntactically or semantically invalid before entering CAD execution (e.g. schema validation failure, legacy field present).
- `errors`: array of error records with `code`, `message`, and optional `field`.

### Status: `failed`
Returned when request validation succeeds, but prompt interpretation, geometry lowering, Solid Edge execution, or required artifact export encounters an unrecoverable error.
- `errors`: array of error records with `code` and `message`.

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
- `rejected_image_field.request.json` - Negative test verifying rejection of legacy `image` field
- `rejected_visible_artifacts_field.request.json` - Negative test verifying rejection of legacy `visible_artifacts` selector
- `rejected_unknown_example.request.json` - Negative test verifying rejection of unknown `example_id`
- `failed_missing_step.response.json` - Export failure response with `ARTIFACT_EXPORT_FAILED`
- `failed_output_path_escape.response.json` - Security failure response with `OUTPUT_PATH_NOT_ALLOWED`
- `failed_target_already_exists.response.json` - Conflict failure response with `TARGET_ALREADY_EXISTS`
