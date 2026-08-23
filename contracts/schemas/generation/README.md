# Generation Domain: Public IPC & REST API Contract

The `generation` domain defines the wire protocol and contract schemas used by client applications (Tauri Desktop GUI, CLI, and REST API) to request prompt-to-CAD 3D parametric generation from the CAD engine.

The client application owns UI/UX, user prompt entry, 3D viewport rendering, and transport orchestration. The CAD engine owns multimodal prompt interpretation, geometric planning, Solid Edge parametric execution, and artifact generation.

---

## Process Contract

- **Input**: Caller sends one UTF-8 JSON request object conforming to `generation-request.schema.json` via `stdin` (for stdio IPC) or HTTP POST `/api/v1/generate` (for REST).
- **Output**: The engine writes exactly one UTF-8 JSON response object conforming to `generation-response.schema.json` to `stdout` (or HTTP response body), followed by a newline.
- **Diagnostics**: Non-contract runtime diagnostics and progress logs are written to `stderr` only.
- **Exit Status**: Handled `accepted`, `rejected`, and `failed` requests all return status code `0` on IPC when the subprocess completes normally.

The IPC launcher is `engine/scripts/generate.cmd` (or CLI entrypoint `cad-copilot-generate`).

---

## Required Configuration

* `CAD_OUTPUT_ROOT`: Output directory where the CAD engine creates isolated per-request artifact folders named after the `request_id`.
* The engine resolves the output root to an absolute path, creates it if necessary, and ensures all emitted artifact paths reside safely within it.

---

## Request Shape

Canonical schema: `generation-request.schema.json` (`$id: "https://cad-copilot.dev/schemas/generation-request.schema.json"`, Draft 2020-12).

### Required Fields
- `contract_version`: must be `"1.0"`
- `request_id`: 1-96 characters, restricted to `^[A-Za-z0-9._-]+$`
- `kind`: must be `"prompt_to_cad"`
- `unit`: must be `"mm"`
- `prompt`: non-empty natural language CAD modeling prompt (1-8000 characters)

### Optional Fields
- `visible_artifacts.formats`: array of format strings from `["par", "step", "jpg", "stl"]`.
  - Default when omitted: `["step", "jpg"]`
  - Solid Edge generates native `.par` parts directly. STEP and STL exports are flexible and optional.
- `image`: multimodal reference image with `base64_data` and `mime_type` (`"image/png"` or `"image/jpeg"`).
- `qa_policy`: `"vision_off"`, `"vision_warning"`, or `"strict_vision"`.
- `vision_qa_model_id`: specific vision model identifier for visual inspection.
- `metadata.source`: `"desktop_app"`, `"cad_copilot"`, or `"local_agent"`.
- `metadata.label`: caller label, up to 128 characters.
- `metadata.job_id`: caller job identifier.

---

## Response Shape

Canonical schema: `generation-response.schema.json` (`$id: "https://cad-copilot.dev/schemas/generation-response.schema.json"`, Draft 2020-12).

All responses include `contract_version: "1.0"`, `request_id`, and `status`.

### Status: `accepted`
Returned when prompt interpretation, feature planning, CAD execution, and artifact generation complete successfully.
- `data.artifacts`: array of produced artifact records (`native_part`, `geometry_step`, `preview_image`, `mesh_stl`) with `origin: "cad_copilot"`.
- `warnings`: array of nonfatal diagnostic strings (e.g., preview degradation, model fallback).

### Status: `rejected`
Returned when the request is syntactically or semantically invalid before entering CAD execution (e.g., schema validation failure, unsupported format).
- `errors`: array of error records with `code`, `message`, and optional `field`.

### Status: `failed`
Returned when request validation succeeds, but prompt interpretation, geometry lowering, Solid Edge execution, or artifact export encounters an unrecoverable error.
- `errors`: array of error records with `code` and `message`.

---

## Artifact Security Policy

1. **Path Traversal Guard**: Every artifact path returned by the engine is resolved and verified. If any artifact path escapes `CAD_OUTPUT_ROOT`, the request fails immediately with error code `OUTPUT_PATH_NOT_ALLOWED`.
2. **Caller Path Isolation**: The engine never accepts caller-chosen artifact write paths.

---

## Error Taxonomy

Standardized error codes:
- `INVALID_SCHEMA` - Malformed JSON or schema constraint violation
- `UNSUPPORTED_REQUEST` - Unsupported request configuration or invalid format
- `PROMPT_INTERPRETATION_FAILED` - LLM could not parse or lower the prompt into geometry
- `CAD_PLAN_REJECTED` - Feature plan violates spatial containment or topology rules
- `CAD_EXECUTION_FAILED` - Solid Edge COM kernel execution error
- `ARTIFACT_EXPORT_FAILED` - Failure exporting requested artifact (STEP, STL, JPG)
- `OUTPUT_PATH_NOT_ALLOWED` - Output path escaped allowed directory boundary
- `INTERNAL_ERROR` - Unhandled engine exception
- `NATIVE_QA_BLOCKED` - Solid Edge native physical property inspection failed
- `VISION_QA_BLOCKED` - Screenshot Vision QA evaluator rejected the output
- `CLOUD_AUTH_FAILED` - Cloud API authentication failure
- `CLOUD_UNAVAILABLE` - AI provider service unavailable
- `PAYLOAD_TOO_LARGE` - Request payload exceeds size limits
- `VISION_QA_SYSTEM_ERROR` - Vision evaluation infrastructure failure

---

## Fixtures

Golden request and response fixtures are maintained in `fixtures/`:

### Standard & Format Variants
- `prompt_default_artifacts.request.json` - Prompt omitting `visible_artifacts` (relies on default formats)
- `accepted_default_step_jpg.response.json` - Default response producing STEP and JPG artifacts
- `requested_step.request.json` - Request explicitly selecting STEP format only
- `accepted_step.response.json` - Accepted response returning STEP artifact
- `requested_step_jpg.request.json` - Request explicitly selecting STEP and JPG formats
- `accepted_step_jpg.response.json` - Accepted response returning STEP and JPG artifacts
- `requested_jpg_only.request.json` - Fast preview request without STEP export overhead
- `accepted_jpg_only.response.json` - Accepted fast preview response returning JPG artifact
- `requested_par_only.request.json` - Native-only request returning native Solid Edge `.par` model
- `accepted_par_only.response.json` - Accepted native response returning `.par` artifact
- `requested_par_jpg.request.json` - Native-first primary workflow request with `.par` and fast preview `.jpg`
- `accepted_par_jpg.response.json` - Accepted response returning `.par` and `.jpg` artifacts
- `requested_stl.request.json` - 3D print mesh export request selecting STL format
- `accepted_stl.response.json` - Accepted response returning STL mesh artifact

### Multimodal Sketch-to-CAD
- `prompt_image_sketch.request.json` - Multimodal generation request with embedded PNG reference sketch
- `accepted_image_sketch.response.json` - Accepted sketch-to-CAD response with `.par` and `.jpg` artifacts

### Negative & Failure Tests
- `unsupported_visible_artifact.request.json` - Request payload with invalid format string
- `rejected_unsupported_visible_artifact.response.json` - Schema rejection response with `INVALID_SCHEMA`
- `failed_missing_step.response.json` - Runtime export failure response with `ARTIFACT_EXPORT_FAILED`
- `failed_output_path_escape.response.json` - Path containment security failure with `OUTPUT_PATH_NOT_ALLOWED`
