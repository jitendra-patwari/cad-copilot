# Batch Domain: Public Automation & Translation Contract

The `batch` domain defines the wire protocol and contract schemas used by client applications (Tauri Desktop GUI and local CLI) to request bulk 3D model translation and 2D drawing publication from the CAD engine.

The client application owns UI/UX, file selection, and process orchestration. The CAD engine owns local Solid Edge automation, multi-format artifact export, and per-file error handling.

---

## Process Contract

- **Input**: Caller sends one UTF-8 JSON request object conforming to `batch-request.schema.json` via `stdin` (stdio IPC).
- **Output**: The engine writes exactly one UTF-8 JSON response object conforming to `batch-response.schema.json` to `stdout`, followed by a newline.
- **Progress & Diagnostics**: Real-time progress and diagnostic traces are written to `stderr` only, prefixed with `[batch-progress]`.
- **Exit Status**: Handled `completed`, `cancelled`, `rejected`, and `failed` requests all return process exit code `0` on IPC when the subprocess completes normally.

The planned IPC launcher for Milestone 5 is `engine/scripts/batch.cmd` (or CLI entrypoint `cad-copilot-batch`).

---

## Request Shape

Canonical schema: `batch-request.schema.json` (`$id: "https://cad-copilot.dev/schemas/batch-request.schema.json"`, Draft 2020-12).

### Required Top-Level Fields
- `contract_version`: must be `"1.0"`
- `request_id`: 1–96 characters, restricted to `^[A-Za-z0-9._-]+$`
- `kind`: must be `"batch_operation"`
- `input.root`: absolute path to the directory containing source CAD files
- `input.files`: non-empty array of relative file paths under `input.root` (1–500 files)
- `output_root`: absolute path to the output directory
- `operation`: object specifying the operation type and target formats

### Supported Operation Types
1. **`export_3d`**: Bulk 3D model export (`.par`, `.psm`, `.asm`).
   - `formats`: array of format strings from `["step", "stl"]` (min 1, max 2).
2. **`publish_drawing`**: Batch 2D engineering drawing publication (`.dft`).
   - `formats`: array of format strings from `["pdf", "dxf"]` (min 1, max 2).

### Optional Fields
- `options.continue_on_error`: boolean, defaults to `true`.
- `options.max_files`: integer, defaults to `500`.
- `metadata`: optional object with `source`, `label`, `job_id`.

---

## Source Immutability & Safety Policy

1. **Source Immutability**: Batch translation is strictly read-only on source files. The engine opens documents with `SaveChanges=False` on close.
2. **Input Path Safety**: All paths in `input.files` must be relative to `input.root`. Any path containing `..` traversal segments is immediately rejected with `INPUT_PATH_NOT_ALLOWED`.
3. **Output Directory Mirroring**: Output artifacts are organized predictably under `output_root`.

---

## Response Shape & Status Accounting

Canonical schema: `batch-response.schema.json` (`$id: "https://cad-copilot.dev/schemas/batch-response.schema.json"`, Draft 2020-12).

### 1. Overall Status: `completed` or `cancelled`
- `summary`: exact counts across all categories:
  - `total`: total files in request
  - `accepted`: files where all requested formats succeeded
  - `partial`: files where at least one format succeeded and at least one format failed
  - `failed`: files where opening or all exports failed
  - `unprocessed`: files skipped due to unrecoverable CAD state
  - `cancelled`: files skipped due to user/client cancellation
- `results`: array of per-file result records:
  - `status: "accepted"` with `artifacts` array and optional `warnings`
  - `status: "partial"` with `artifacts` array, `errors` array, and optional `warnings`
  - `status: "failed"` with `errors` array and optional `warnings`
- `unprocessed_files` / `cancelled_files`: optional arrays listing unattempted file paths.

### 2. Status: `rejected`
Returned when request parameters violate schema or security bounds before CAD execution (e.g. `INVALID_SCHEMA`, `INPUT_PATH_NOT_ALLOWED`).

### 3. Status: `failed`
Returned when an unrecoverable system failure occurs (e.g. `SOLID_EDGE_UNAVAILABLE`).

---

## Error Taxonomy

Standardized error codes:
- `INVALID_SCHEMA` - Malformed JSON or schema constraint violation
- `UNSUPPORTED_OPERATION` - Unrecognized `operation.type`
- `UNSUPPORTED_FORMAT` - Unrecognized format in `operation.formats`
- `INPUT_ROOT_NOT_FOUND` - Specified `input.root` does not exist
- `INPUT_PATH_NOT_ALLOWED` - Unsafe relative path or path traversal attempt
- `INPUT_FILE_NOT_FOUND` - Source CAD file does not exist
- `OUTPUT_ROOT_UNAVAILABLE` - Cannot create or access `output_root`
- `TARGET_ALREADY_EXISTS` - Destination output file already exists and overwrite was not approved
- `SOLID_EDGE_UNAVAILABLE` - Cannot acquire Solid Edge COM application session
- `DOCUMENT_OPEN_FAILED` - Cannot open source CAD document
- `ARTIFACT_EXPORT_FAILED` - Failure exporting specific format artifact
- `DOCUMENT_CLOSE_FAILED` - Document release warning
- `INTERNAL_ERROR` - Unhandled engine exception

---

## Fixtures

Golden request and response fixtures are maintained in `fixtures/`:

### Positive Fixtures
- `basic_export.request.json` - Standard 3D export request (`export_3d` to `step`, `stl`)
- `publish_drawing.request.json` - Drawing publication request (`publish_drawing` to `pdf`, `dxf`)
- `completed.response.json` - Completed response with accepted and failed file results
- `partial_batch.response.json` - Completed response containing accepted, partial, and failed file results
- `cancelled_batch.response.json` - Cancelled batch response demonstrating cancellation accounting

### Negative & Failure Fixtures
- `rejected_no_files.request.json` / `.response.json` - Rejection for empty `files` list
- `rejected_unsafe_path.request.json` / `.response.json` - Rejection for path traversal attempt
- `rejected_legacy_operation.request.json` - Rejection for unapproved legacy operation types
- `rejected_unverified_format.request.json` - Rejection for unverified format requests
- `failed_se_unavailable.response.json` - Failure response when Solid Edge is unavailable
