# Batch Domain: Public Automation & Translation Contract

The `batch` domain defines the wire protocol and contract schemas used by client applications (Tauri Desktop GUI and local CLI) to request bulk 3D model translation and 2D drawing publication from the CAD engine.

The desktop client application owns UI/UX, file selection, and process orchestration. The implemented batch engine owns local Solid Edge automation, multi-format artifact export, per-file isolation, and atomic summary manifest publication.

**Status (20 September 2026):** Canonical wire schemas, manifest schemas, fixtures, typed immutable contract models, Draft 2020-12 validators, operation metadata registry foundation (M5.1), sequential batch execution infrastructure (`BatchService`, typed bindings, work allocation, error isolation, cancellation, and observable teardown under M5.2), batch filesystem & source-integrity safety boundary (`FilesystemBatchSafetyBoundary`, bounded root enforcement, streaming SHA-256 source snapshots, guarded workspaces, and Windows atomic no-replace publication under M5.3), genuine batch format handlers and native export operations (`export_3d` [STEP/STL], `publish_drawing` [PDF/DXF] under M5.4), conditional Parasolid batch export promotion (`export_3d` [.x_t] under M5.5), and batch summary manifest publication, strict batch stdio transport, cooperative signal cancellation, and Python packaging (M5.6) are established, implemented, and verified under this directory and `engine/src/batch/`. Milestone 5 scope is implemented and verified. Milestone 6 desktop client integration (M6.1 Clean Foundation, M6.2 Generate Slice, M6.3 Batch Slice) is implemented and verified; downstream shared consolidation and packaging (M6.4) remains planned.

---

## Process Contract

- **Input**: Caller sends one bounded UTF-8 JSON request object conforming to `batch-request.schema.json` via `stdin` (stdio IPC). Maximum payload size is strictly capped at 128 KiB (`131,072` bytes) and read up to a bounded `limit + 1` byte buffer. Strict JSON decoding rejects BOM, duplicate keys, non-finite constants (`NaN`, `Infinity`), non-object roots, and trailing tokens. Payloads exceeding 128 KiB are rejected with `rejected/PAYLOAD_TOO_LARGE`.
- **Output**: The engine writes exactly one compact 7-bit ASCII JSON response object conforming to `batch-response.schema.json` to preserved `stdout`, followed by a single newline (`\n`). Early descriptor isolation redirects CRT descriptors and Windows standard handles (`STD_OUTPUT_HANDLE`, `STD_ERROR_HANDLE`) to null before production imports, guaranteeing zero stdout contamination from C-runtime or COM output.
- **Progress Protocol**: Real-time progress updates are emitted to `stderr` as bounded, compact, single-line ASCII-safe JSONL objects (capped at 4 KiB per line, LF-terminated):
  ```json
  {"type":"progress","request_id":"batch-001","phase":"file_started","total_files":2,"completed_files":0,"current_file":"parts/bracket.par"}
  ```
  The protocol supports 6 deterministic phases (`batch_started`, `file_started`, `format_started`, `format_finished`, `file_finished`, `batch_finished`) with optional phase-governed fields (`current_file`, `current_format`, `file_status`). Stderr output contains zero CAD geometry, prompts, environment dumps, or absolute paths. Progress delivery is best-effort: broken or closed stderr pipes must not abort execution, corrupt accounting, or affect the stdout response.
- **Fatal Diagnostics**: When a fatal bootstrap, descriptor, schema resource, serialization, or write failure occurs before a contract response can be produced, stderr receives a fixed envelope and stdout remains empty:
  ```json
  {"type":"diagnostic","phase":"fatal","message":"Batch process failed before a contract response could be produced."}
  ```
- **Console Signals & Cooperative Cancellation**: A request-scoped signal handler for `SIGINT` (and Windows `SIGBREAK` / `CTRL_BREAK_EVENT`) is installed strictly after request validation and before composition starts, and restored in `finally`. The handler only sets an async-safe cancellation flag (`threading.Event`) observed by `BatchService` between files and formats without raising into COM, performing I/O, closing documents, or mutating response structures. `SIGTERM` is not intercepted, and no in-engine force-kill is performed. Supported signals produce a valid `cancelled` (or fatal-precedence `failed`) response and exit `0`.
- **Exit Status**:
  - `0`: handled contract response delivered to stdout (`completed`, `cancelled`, `rejected`, early `failed`, progressed `failed`).
  - `1`: fatal bootstrap, descriptor, schema resource, serialization, or stdout-write failure (stdout remains empty).
  - `130`: console interrupt occurring before cooperative signal handler installation or after restoration.

The verified IPC launchers for the batch engine are `engine/scripts/batch.cmd` (source wrapper) and `cad-copilot-batch` (installed console entry point `cad-copilot-batch = "ipc.batch_stdio:main"`).

### Launcher Usage

Callers provide a single bounded UTF-8 JSON request via stdin. The engine writes exactly one compact 7-bit ASCII JSON response to stdout, while streaming discrete JSONL progress events to stderr.

#### Raw Command Redirection (Universal: PowerShell 5.1+, PowerShell 7+, or CMD)
Because Windows PowerShell 5.1 defaults native process pipe redirection (`|`) to US-ASCII, use raw stream redirection via `cmd /d /c` to ensure byte-pure UTF-8 stdin transmission:

```powershell
# 1. Using the repository source wrapper:
cmd /d /c "engine\scripts\batch.cmd < request.json > response.json 2> progress.log"

# 2. Using the installed console entry point (virtual environment active or on PATH):
cmd /d /c "cad-copilot-batch < request.json > response.json 2> progress.log"

# Inspect the single-line stdout contract response:
Get-Content .\response.json | ConvertFrom-Json

# Inspect the real-time stderr progress events:
Get-Content .\progress.log
```

#### PowerShell 7+ Pipeline Usage
In PowerShell 7+ (or Windows PowerShell 5.1 with `$OutputEncoding = [System.Text.Encoding]::UTF8`), piping is UTF-8 clean:

```powershell
Get-Content -Raw -Encoding utf8 .\request.json | .\engine\scripts\batch.cmd 1> response.json 2> progress.log
```

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
   - `formats`: array of format strings from `["step", "stl", "parasolid"]` (min 1, max 3, unique). Produces `.step`, `.stl`, and `.x_t` artifacts.
2. **`publish_drawing`**: Batch 2D engineering drawing publication from native drawings (`.dft`).
   - `formats`: array of format strings from `["pdf", "dxf"]` (min 1, max 2, unique).

Requests are strictly homogeneous: mixing 3D models and 2D drawings in one request is rejected. Unapproved operations (e.g. flat pattern, automatic drafting) and unapproved formats (DWG, IGES, JT, 3MF) are rejected.

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
- Cancellation is permitted when either `cancelled_files` is non-empty or at least one per-file result contains the format-qualified `BATCH_CANCELLED` diagnostic.

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
- **Publication**: Staged through an exclusive, randomly named temporary file directly under the validated `output_root`, read-back validated against `batch-manifest-v1.schema.json`, re-verified for root identity, target containment, target absence, and staging identity immediately before rename, and atomically renamed into place using Windows no-replace semantics (`MoveFileExW` without replace flag) in Milestone 5.6. Followed by post-publication snapshot and byte verification. Pre-existing files and collision sentinels are strictly preserved without overwrite. On failure, only the request-private staging file is removed.
- **Publication Failure Precedence**: If manifest assembly, artifact hashing, serialization, read-back, collision check, rename, or post-publication verification fails, terminal response transitions to progressed `failed` with `manifest=None`, `cancelled_files=()`, `summary.cancelled=0`, and untouched inputs moved to `unprocessed_files`. For previously completed or cancelled outcomes, the top-level error is `MANIFEST_PUBLICATION_FAILED`; for an already-progressed `failed` outcome, existing fatal errors are retained and `MANIFEST_PUBLICATION_FAILED` is appended exactly once.
- **Manifest Reference in Responses**: When present in `BatchResponse`, `manifest.path` is an absolute local Windows path normalized with forward slashes (`target_path.as_posix()`), matching canonical response fixtures.
- **Engine Version**: `engine_version` is resolved from installed `cad-copilot` distribution metadata before CAD acquisition; if unavailable after request validation, an early `failed / INTERNAL_ERROR` response is returned without acquiring Solid Edge.
- **Enforced Terminal Variants**:
  - `completed`: required `manifest_version`, `contract_version`, `request_id`, `status: "completed"`, `operation`, `summary`, `results`, `engine_version`; optional `unprocessed_files` and `warnings`; `cancelled_files` and `errors` are strictly prohibited.
  - `cancelled`: required `manifest_version`, `contract_version`, `request_id`, `status: "cancelled"`, `operation`, `summary`, `results`, `cancelled_files`, `engine_version`; optional `unprocessed_files` and `warnings`; `errors` is strictly prohibited. Requires either non-empty `cancelled_files` or at least one per-file `BATCH_CANCELLED` error.
  - `failed`: required `manifest_version`, `contract_version`, `request_id`, `status: "failed"`, `operation`, `summary`, `results`, non-empty `errors`, `engine_version`; optional `unprocessed_files` and `warnings`; `cancelled_files` is strictly prohibited.
- **Paths**: All serialized paths are strictly relative, forward-slash portable paths without drive letters, absolute prefixes, traversal (`..`), or backslashes. Source-relative paths (`input` in file results, `unprocessed_files`, and `cancelled_files`) preserve the validated canonical relative paths from the request (relative to `input.root`). Only artifact `relative_path` entries are derived via `target_path.relative_to(output_root).as_posix()` from the verified published target beneath `output_root`.
- **Artifact Records**: Contain `format`, `relative_path`, positive `size_bytes`, and lowercase 64-character hexadecimal `sha256`.
- **Privacy Boundary**: Contains zero prompts, API keys, CAD geometries, environment dumps, usernames, or absolute paths.

---

## Standardized Error Codes

The 21 approved product error codes are:
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
- `BATCH_CANCELLED` - Cooperative cancellation observed before starting a format (format-qualified per-file error)

---

## Fixtures

Golden fixtures are maintained in `contracts/schemas/batch/fixtures/`:

### Request Fixtures
- `basic_export.request.json`: Standard 3D export request (`export_3d` to `step`, `stl`, `parasolid`)
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
- `cancelled_mid_file.response.json`: Cancelled response where cancellation was observed mid-file with partial result, format-attributed BATCH_CANCELLED, and empty cancelled_files
- `failed_se_unavailable.response.json`: Early failure response before processing began
- `failed_after_progress.response.json`: Progressed failure preserving attempted results and manifest
- `rejected_no_files.response.json`: Rejection response for empty files list
- `rejected_unsafe_path.response.json`: Rejection response for path traversal

### Summary Manifest Fixtures
- `completed.batch_manifest.json`: Completed manifest with SHA-256 hashes and version warning
- `cancelled.batch_manifest.json`: Cancelled manifest with cancellation accounting
- `cancelled_mid_file.batch_manifest.json`: Cancelled manifest where cancellation occurred mid-file
- `failed_after_progress.batch_manifest.json`: Progressed failure manifest preserving completed artifacts
