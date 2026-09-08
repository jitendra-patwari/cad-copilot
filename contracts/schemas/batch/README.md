# Batch Domain: Public Automation & Translation Contract

The `batch` domain defines the wire protocol and contract schemas used by client applications (Tauri Desktop GUI and local CLI) to request bulk 3D model translation and 2D drawing publication from the CAD engine.

The planned client application owns UI/UX, file selection, and process orchestration. The planned batch engine owns local Solid Edge automation, multi-format artifact export, per-file isolation, and atomic summary manifest publication.

**Status (8 September 2026):** Canonical wire schemas, manifest schemas, fixtures, typed immutable contract models, Draft 2020-12 validators, and the operation metadata registry foundation are established, implemented, and verified under this directory and `engine/src/batch/`. Milestone 5.1 foundation is complete. Milestone 5.2+ batch runtime execution, CLI launchers, and desktop client integration remain planned. The process and safety rules below are authoritative requirements for runtime execution, not active runtime behavior in M5.1.

---

## Process Contract

- **Input**: Caller sends one UTF-8 JSON request object conforming to `batch-request.schema.json` via `stdin` (stdio IPC).
- **Output**: The engine writes exactly one UTF-8 JSON response object conforming to `batch-response.schema.json` to `stdout`, followed by a newline (`\n`).
- **Progress & Diagnostics**: Real-time progress and diagnostic traces are emitted to `stderr` as strict single-line JSONL events matching the transport standard established in Milestone 4.5.
- **Exit Status**: Handled `completed`, `cancelled`, `rejected`, and `failed` requests all return process exit code `0` on IPC when the subprocess completes normally. Unhandled process crashes or bootstrap errors exit non-zero with a fatal diagnostic on `stderr`.

The planned IPC launcher for Milestone 5 is `engine/scripts/batch.cmd` (or CLI entry point `cad-copilot-batch`).

---

## Request Shape

Canonical schema: `batch-request.schema.json` (`$id: "https://cad-copilot.dev/schemas/batch-request.schema.json"`, Draft 2020-12).

### Required Top-Level Fields
- `contract_version`: must be `"1.0"`.
- `request_id`: 1–96 characters, restricted to `^[A-Za-z0-9._-]+$`.
- `kind`: must be `"batch_operation"`.
- `input.root`: absolute path string to the directory containing source CAD files.
- `input.files`: non-empty array of relative file paths under `input.root` (1–500 files).
- `output_root`: absolute path string to the output directory.
- `operation`: object specifying the operation type and target formats.

### Path Separator Normalization & Safety
- Callers may supply relative input paths using forward slashes (`/`), backslashes (`\`), or mixed separators.
- Raw values are strictly validated *before* normalization: NUL characters, rooted/absolute paths, drive qualification (`C:`), UNC/device paths, leading/trailing/repeated separators, and `.` or `..` segments are immediately rejected with `INPUT_PATH_NOT_ALLOWED`.
- Accepted paths are canonicalized with `PureWindowsPath(value).as_posix()` so that `BatchRequest.input.files` stores forward slashes only.
- Duplicate and case-colliding input paths are rejected after canonicalization.

### Supported Operation Types
1. **`export_3d`**: Bulk 3D model export from native Solid Edge parts and assemblies (`.par`, `.psm`, `.asm`).
   - `formats`: array of format strings from `["step", "stl"]` (min 1, max 2, unique).
2. **`publish_drawing`**: Batch 2D engineering drawing publication from native drawings (`.dft`).
   - `formats`: array of format strings from `["pdf", "dxf"]` (min 1, max 2, unique).

Requests are strictly homogeneous: mixing 3D models and 2D drawings in one request is rejected. Unapproved operations (e.g. flat pattern, automatic drafting) and unapproved formats (DWG, IGES, JT, 3MF, Parasolid) are rejected.

### Optional Fields
- `options.continue_on_error`: boolean, defaults to `true`. When `false`, the engine stops after the first input experiencing a partial or failed export, classifying remaining inputs as `unprocessed`.
- `options.max_files`: integer from 1 to 500; defaults to `100`, with `500` as the hard schema maximum. The runtime enforces this limit before CAD acquisition.
- `metadata`: optional object with `source` (`desktop_app`, `cad_copilot`, `local_agent`), `label`, `job_id`.

---

## Source Immutability & Safety Policy

1. **Source Immutability**: Batch translation must never modify source CAD files. Open explicit request-owned document handles, leave existing modeling modes unchanged, and close with `Close(False)`. Unrelated documents and borrowed applications remain protected.
2. **Input Path Safety**: All paths in `input.files` must be relative to `input.root` without traversal.
3. **Output Directory Mirroring**: Output paths mirror each input's source-relative parent directory beneath `output_root`.
4. **Collision Policy**:
   - `OUTPUT_TARGET_COLLISION`: Preflight check rejects requests before CAD execution if two inputs would project to the same output file.
   - `TARGET_ALREADY_EXISTS`: Format-level error if a destination file already exists on disk. Batch processing never silently overwrites existing files.

---

## Response Shape & Terminal States

Canonical schema: `batch-response.schema.json` (`$id: "https://cad-copilot.dev/schemas/batch-response.schema.json"`, Draft 2020-12).

### 1. Status: `completed`
Request finished processing all files (or stopped early via `continue_on_error=false`).
- Required: `contract_version`, `request_id`, `status: "completed"`, `summary`, `results`, `manifest`.
- Optional: `unprocessed_files` (only when stopped early), `warnings` (top-level diagnostics).
- `cancelled_files` is prohibited.

### 2. Status: `cancelled`
Request was stopped via cooperative client cancellation.
- Required: `contract_version`, `request_id`, `status: "cancelled"`, `summary`, `results`, `cancelled_files`, `manifest`.
- Optional: `unprocessed_files`, `warnings`.

### 3. Status: `rejected`
Request failed validation or security bounds before CAD execution.
- Required: `contract_version`, `request_id`, `status: "rejected"`, `errors`.
- Summary, results, manifest, warnings, and file lists are omitted.

### 4. Status: `failed` (Early)
Unrecoverable failure before processing began (e.g. `SOLID_EDGE_UNAVAILABLE`).
- Required: `contract_version`, `request_id`, `status: "failed"`, `errors`.
- Optional: `warnings`.
- Summary, results, and manifest are omitted.

### 5. Status: `failed` (Progressed)
Unrecoverable fatal condition after processing began (e.g. `SOLID_EDGE_UNHEALTHY`, `DOCUMENT_CLOSE_FAILED`).
- Required: `contract_version`, `request_id`, `status: "failed"`, `summary`, `results`, `errors`.
- Optional: `manifest` (omitted if manifest publication itself failed), `unprocessed_files`, `warnings`.

### Manifest Record in Responses
For `completed` and `cancelled` responses (and optionally progressed `failed`), the `manifest` property contains:
```json
{
  "manifest": {
    "path": "C:/my_cad_parts/output/batch-001.batch_manifest.json"
  }
}
```

---

## Summary Manifest Contract

Canonical schema: `batch-manifest-v1.schema.json` (`$id: "https://cad-copilot.dev/schemas/batch-manifest-v1.schema.json"`, Draft 2020-12).

- **Location**: Written directly under `output_root` as `<request_id>.batch_manifest.json`.
- **Publication**: Written to a temporary file and atomically renamed in place in Milestone 5.6.
- **Enforced Terminal Variants**:
  - `completed`: required `manifest_version`, `contract_version`, `request_id`, `status: "completed"`, `operation`, `summary`, `results`, `engine_version`; optional `unprocessed_files` and `warnings`; `cancelled_files` and `errors` are strictly prohibited.
  - `cancelled`: required `manifest_version`, `contract_version`, `request_id`, `status: "cancelled"`, `operation`, `summary`, `results`, non-empty `cancelled_files`, `engine_version`; optional `unprocessed_files` and `warnings`; `errors` is strictly prohibited.
  - `failed`: required `manifest_version`, `contract_version`, `request_id`, `status: "failed"`, `operation`, `summary`, `results`, non-empty `errors`, `engine_version`; optional `unprocessed_files` and `warnings`; `cancelled_files` is strictly prohibited.
- **Paths**: Serialized paths (`input`, `relative_path`, `unprocessed_files`, `cancelled_files`) are strictly relative, forward-slash portable paths without drive letters, absolute prefixes, traversal (`..`), or backslashes.
- **Artifact Records**: Contain `format`, `relative_path`, positive `size_bytes`, and lowercase 64-character hexadecimal `sha256`.
- **Privacy Boundary**: Contains zero prompts, API keys, CAD geometries, environment dumps, usernames, or absolute paths.

---

## Standardized Error Codes

The 20 approved product error codes are:
- `INVALID_SCHEMA` - Malformed JSON or schema constraint violation
- `PAYLOAD_TOO_LARGE` - Request payload exceeds size limits
- `UNSUPPORTED_OPERATION` - Unrecognized `operation.type`
- `UNSUPPORTED_FORMAT` - Unrecognized format in `operation.formats`
- `INPUT_ROOT_NOT_FOUND` - Specified `input.root` directory does not exist
- `INPUT_PATH_NOT_ALLOWED` - Unsafe relative path or path traversal attempt
- `INPUT_FILE_NOT_FOUND` - Source CAD file does not exist
- `INPUT_EXTENSION_NOT_ALLOWED` - Source file extension not compatible with requested operation
- `OUTPUT_ROOT_UNAVAILABLE` - Cannot create or access `output_root`
- `OUTPUT_TARGET_COLLISION` - Two selected inputs would write to the same output destination
- `TARGET_ALREADY_EXISTS` - Destination output file already exists on disk
- `SOLID_EDGE_UNAVAILABLE` - Cannot acquire Solid Edge COM application session
- `SOLID_EDGE_UNHEALTHY` - Solid Edge session became unresponsive or crashed
- `DOCUMENT_OPEN_FAILED` - Cannot open source CAD document
- `ARTIFACT_EXPORT_FAILED` - Failure exporting specific format artifact (includes `format` attribution)
- `DOCUMENT_CLOSE_FAILED` - Failure closing document or document close state uncertain
- `SOURCE_INTEGRITY_FAILED` - Source file modified or timestamp changed during operation
- `MANIFEST_PUBLICATION_FAILED` - Atomic summary manifest write or rename failed
- `VERSION_METADATA_UNAVAILABLE` - CAD runtime build version could not be queried (used in `warnings`)
- `INTERNAL_ERROR` - Unhandled engine exception

---

## Fixtures

Golden fixtures are maintained in `contracts/schemas/batch/fixtures/`:

### Request Fixtures
- `basic_export.request.json`: Standard 3D export request (`export_3d` to `step`, `stl`)
- `publish_drawing.request.json`: Drawing publication request (`publish_drawing` to `pdf`, `dxf`)
- `rejected_unsafe_path.request.json`: Rejection for path traversal attempt (`../secret/part.par`)
- `rejected_no_files.request.json`: Rejection for empty `files` array
- `rejected_deferred_flat_pattern.request.json`: Rejection for deferred flat pattern operation
- `rejected_unverified_format.request.json`: Rejection for unverified format requests

### Response Fixtures
- `completed.response.json`: Completed response with accepted file results, manifest, and version warning
- `partial_batch.response.json`: Completed response with mixed accepted, partial, and failed results, format-attributed error, and manifest
- `continue_on_error_stop.response.json`: Completed policy stop with `unprocessed_files` and manifest
- `cancelled_batch.response.json`: Cancelled batch response with `cancelled_files` and manifest
- `failed_se_unavailable.response.json`: Early failure response before processing began
- `failed_after_progress.response.json`: Progressed failure preserving attempted results and manifest
- `rejected_no_files.response.json`: Rejection response for empty files list
- `rejected_unsafe_path.response.json`: Rejection response for path traversal

### Summary Manifest Fixtures
- `completed.batch_manifest.json`: Completed manifest with SHA-256 hashes and version warning
- `cancelled.batch_manifest.json`: Cancelled manifest with cancellation accounting
- `failed_after_progress.batch_manifest.json`: Progressed failure manifest preserving completed artifacts
