# Edit Domain: Public Parametric Edit Session Contract

The `edit` domain defines the wire protocol and contract schemas used by client applications (Tauri Desktop GUI, CLI, and REST API) to request prompt-driven parametric modifications to an active CAD Copilot edit session.

Unlike `generation` (which creates a new CAD model from scratch) or `batch` (which operates on collections of files), `edit` operates interactively on a live, stateful parametric feature session with optimistic revision control.

---

## Process Contract

- **Input**: Caller sends one UTF-8 JSON request object conforming to `edit-request.schema.json` via `stdin` (for stdio IPC) or HTTP POST `/api/v1/edit` (for REST).
- **Output**: The engine writes exactly one UTF-8 JSON response object conforming to `edit-response.schema.json` to `stdout` (or HTTP response body), followed by a newline.
- **Diagnostics**: Non-contract runtime diagnostics and progress logs are written to `stderr` only.
- **Exit Status**: Handled `accepted`, `rejected`, and `failed` requests all return process exit code `0` on IPC when the subprocess completes normally.

The IPC launcher is `engine/scripts/edit.cmd` (or CLI entrypoint `cad-copilot-edit`).

---

## Session State & Concurrency Model

1. **Optimistic Revision Tracking**:
   - Every edit request requires `session_id` and `base_state_revision` (integer ≥ 0).
   - If the engine's current session state revision does not match `base_state_revision`, the request is rejected with `SEMANTIC_REVISION_CONFLICT` to prevent stale overwrites.
   - Successful edits increment the revision number in the response (`state_revision = base_state_revision + 1`).

2. **Native Part Persistence**:
   - The engine maintains and modifies the native Solid Edge part file (`.par`) under `CAD_OUTPUT_ROOT`.
   - On fresh process invocations, the client includes `native_part.path` so the engine can reload the existing parametric model.

3. **Additive & Subtractive Feature Lowering**:
   - The edit interpreter parses localized natural language modifications (e.g., adding through holes, counterbores, rectangular cutouts, slots, bosses, and mounting pads).
   - Returns updated native `.par` models alongside optional `.jpg` preview images and `.step` / `.stl` geometry.

---

## Request Shape

Canonical schema: `edit-request.schema.json` (`$id: "https://cad-copilot.dev/schemas/edit-request.schema.json"`, Draft 2020-12).

### Required Fields
- `contract_version`: must be `"1.0"`
- `request_id`: 1-96 characters, restricted to `^[A-Za-z0-9._-]+$`
- `kind`: must be `"prompt_edit_part"`
- `session_id`: 1-128 characters, restricted to `^[A-Za-z0-9._:-]+$`
- `unit`: must be `"mm"`
- `prompt`: natural language edit instruction (1-8000 characters)
- `base_state_revision`: non-negative integer representing the expected current session state

### Optional Fields
- `native_part.path`: absolute path to the existing `.par` file to be edited.
- `visible_artifacts.formats`: array of format strings from `["par", "step", "jpg", "stl"]`.
- `image`: multimodal reference image with `base64_data` and `mime_type`.
- `metadata.source`: `"desktop_app"`, `"cad_copilot"`, or `"local_agent"`.
- `metadata.label`: caller label.
- `metadata.job_id`: caller job identifier.

---

## Response Shape

Canonical schema: `edit-response.schema.json` (`$id: "https://cad-copilot.dev/schemas/edit-response.schema.json"`, Draft 2020-12).

All responses include `contract_version: "1.0"`, `request_id`, and `status`.

### Status: `accepted`
Returned when prompt interpretation, feature lowering, Solid Edge modification, and artifact export succeed.
- `data.session_id`: active session identifier
- `data.state_revision`: incremented revision integer
- `data.artifacts`: array of updated artifact records (`native_part`, `geometry_step`, `preview_image`, `mesh_stl`) with `origin: "cad_copilot"`
- `warnings`: array of structured diagnostic records (`{ code, message }`)

### Status: `rejected`
Returned when the request is syntactically invalid, references an unknown session, or encounters a revision conflict.
- `errors`: array of error records (`{ code, message, field }`)

### Status: `failed`
Returned when CAD feature lowering or Solid Edge kernel execution fails during the edit.
- `errors`: array of error records (`{ code, message }`)

---

## Error Taxonomy

Standardized error codes:
- `INVALID_SCHEMA` - Malformed JSON or schema constraint violation
- `UNSUPPORTED_REQUEST` - Unsupported request configuration
- `SESSION_STATE_NOT_FOUND` - Specified `session_id` does not exist
- `SEMANTIC_REVISION_CONFLICT` - `base_state_revision` does not match active session state
- `BACKEND_SESSION_RUNTIME_BINDINGS_UNAVAILABLE` - Session runtime storage or Solid Edge bindings unavailable
- `PROMPT_EDIT_INTERPRETATION_FAILED` - LLM could not parse the edit prompt into a valid CAD modification
- `CAD_EDIT_EXECUTION_FAILED` - Solid Edge COM kernel execution error while applying feature change
- `ARTIFACT_EXPORT_FAILED` - Failure exporting requested artifact
- `OUTPUT_PATH_NOT_ALLOWED` - Output path escaped allowed directory boundary
- `INTERNAL_ERROR` - Unhandled engine exception

---

## Fixtures

Golden fixtures are maintained in `fixtures/`:
- `prompt_edit_part.request.json` - Additive feature edit request on an active session
- `accepted_prompt_edit_part.response.json` - Successful edit response with incremented revision and updated `.par` + `.jpg` artifacts
- `rejected_revision_conflict.request.json` - Edit request with mismatched base state revision
- `rejected_revision_conflict.response.json` - Optimistic locking conflict rejection response
- `rejected_session_not_found.request.json` - Edit request targeting an unknown session ID
- `rejected_session_not_found.response.json` - Missing session state rejection response
- `failed_edit_execution.response.json` - Engine-level failure when CAD feature modification fails
