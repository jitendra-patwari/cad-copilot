# Batch Domain: Public Automation & Translation Contract

The `batch` domain defines the wire protocol and contract schemas used by client applications (Tauri Desktop GUI, CLI, and REST API) to request bulk translation, drawing publication, property extraction, and drafting automation from the CAD Copilot engine.

The client application owns UI/UX, file selection, and transport orchestration. The CAD Copilot engine owns Solid Edge automation, multi-format artifact export, and per-file error handling.

---

## Process Contract

- **Input**: Caller sends one UTF-8 JSON request object conforming to `batch-request.schema.json` via `stdin` (for stdio IPC) or HTTP POST `/api/v1/batch` (for REST).
- **Output**: The engine writes exactly one UTF-8 JSON response object conforming to `batch-response.schema.json` to `stdout` (or HTTP response body), followed by a newline.
- **Progress & Diagnostics**: Real-time progress and diagnostic traces are written to `stderr` only, prefixed with `[batch-progress]`.
- **Exit Status**: Handled `completed`, `rejected`, and `failed` requests all return process exit code `0` on IPC when the subprocess completes normally.

The IPC launcher is `engine/scripts/batch.cmd` (or CLI entrypoint `cad-copilot-batch`).

---

## Request Shape

Canonical schema: `batch-request.schema.json` (`$id: "https://cad-copilot.dev/schemas/batch-request.schema.json"`, Draft 2020-12).

### Required Top-Level Fields
- `contract_version`: must be `"1.0"`
- `request_id`: 1-96 characters, restricted to `^[A-Za-z0-9._-]+$`
- `kind`: must be `"batch_operation"`
- `input.root`: absolute path to the directory containing source files
- `input.files`: non-empty array of relative file paths under `input.root`
- `output_root`: absolute path to the output directory
- `operation`: object specifying the operation type and parameters

### Supported Operation Types
1. `export` - Bulk translation to neutral formats (`step`, `stl`, `parasolid`, `jt`, `iges`, `3mf`).
2. `publish_drawing` - Batch draft publication to `pdf`, `dwg`, `dxf` with automatic view updating.
3. `flat_pattern` - Sheet metal flat pattern DXF extraction.
4. `read_properties` - Bulk physical & custom metadata extraction to `json` or `csv`.
5. `open_save` - Batch geometry version migration and feature recomputation.
6. `physical_properties_update` - Mass, volume, center of gravity, and surface area recalculation.
7. `workspace_init` - Scaffold standard directory structure and configuration templates.
8. `write_properties` - Mass property injection and custom attribute updates.
9. `generate_draft` - Automated manufacturing drawing generation from 3D models (`.dft`).

### Optional Fields
- `options.continue_on_error`: boolean, defaults to `true`.
- `options.max_files`: integer, defaults to `100`, maximum `500`.
- `metadata.source`: `"desktop_app"`, `"cad_copilot"`, or `"local_agent"`.

---

## Input Path Safety & Containment

All entries in `input.files` must be:
- Non-empty relative paths without drive letters or absolute prefixes.
- Strictly contained within `input.root` (path traversal using `..` segments is strictly rejected).
- Resolved output artifact paths must reside safely under `output_root`.

---

## Output Path Policy & Flat Layout

Exported artifacts are written in a predictable flat layout under `output_root`:

```
input.root/part1.par           -> output_root/part1.step
                                  output_root/part1.stl
                                  output_root/part1.x_t

input.root/subfolder/part2.par -> output_root/part2.step
                                  output_root/part2.stl
```

To prevent silent overwrites in flat output layouts, multiple input files sharing the same filename stem are rejected before execution with error code `DUPLICATE_OUTPUT_TARGET`.

---

## Response Shapes

Canonical schema: `batch-response.schema.json` (`$id: "https://cad-copilot.dev/schemas/batch-response.schema.json"`, Draft 2020-12).

### 1. Status: `completed`
Returned when batch initialization succeeded and all per-file tasks executed (individual files may have succeeded or failed).
- `summary`: `{ total, accepted, failed }`
- `results`: array of per-file result records with status, generated artifact paths, warnings, and errors.

### 2. Status: `rejected`
Returned when the request is syntactically invalid or violates safety rules before Solid Edge starts (e.g. `INVALID_SCHEMA`, `INPUT_PATH_NOT_ALLOWED`, `DUPLICATE_OUTPUT_TARGET`).

### 3. Status: `failed`
Returned when a catastrophic runtime failure occurs before the per-file execution loop can complete (e.g. `SOLID_EDGE_UNAVAILABLE`, `OUTPUT_ROOT_UNAVAILABLE`).

---

## Progress Reporting Protocol

The backend streams flushed progress updates over `stderr`:

```
[batch-progress] Starting batch batch-001: 2 files
[batch-progress] Processing 1/2: part1.par
[batch-progress] Exported step: part1.step
[batch-progress] Exported stl: part1.stl
[batch-progress] Processing 2/2: subfolder/part2.par
[batch-progress] Completed batch batch-001: 2 accepted, 0 failed
```

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
- `DUPLICATE_OUTPUT_TARGET` - Collision on flat output filename stems
- `SOLID_EDGE_UNAVAILABLE` - Cannot acquire Solid Edge COM application session
- `DOCUMENT_OPEN_FAILED` - Cannot open source CAD document
- `EXPORT_FAILED` - Failure exporting specific format artifact
- `DOCUMENT_CLOSE_FAILED` - Document release warning
- `INTERNAL_ERROR` - Unhandled engine exception

---

## Fixtures

Golden fixtures are maintained in `fixtures/`:
- `basic_export.request.json` - Standard multi-format translation request
- `completed.response.json` - Partial success response with per-file result records
- `rejected_no_files.request.json` - Rejection request payload for empty file list
- `rejected_no_files.response.json` - Schema rejection response for empty file list
- `rejected_unsafe_path.request.json` - Rejection request payload for path traversal
- `rejected_unsafe_path.response.json` - Security boundary rejection response for path traversal
- `failed_se_unavailable.response.json` - Engine-level failure when Solid Edge is uninstalled
