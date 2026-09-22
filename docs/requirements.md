# System Requirements Specification: Milestones 1, 2, 3, 4, 5, 6.1, 6.2, 6.3, 6.4 & 6.5

**Project**: CAD Copilot  
**Scope**: Milestone 1 (Foundation & Domain Interfaces), Milestone 2 (Pure Domain Geometry Math), Milestone 3 (Solid Edge COM Driver & Artifact Pipeline), Milestone 4 (Generation Application Orchestration, Examples, Manifest & Stdio IPC), Milestone 5 (Batch Automation Contracts, Execution Infrastructure, Safety Boundary, Format Handlers, Parasolid Promotion, Summary Manifest Publication & Strict Stdio IPC), Milestone 6.1 (Clean Tauri/React Desktop Foundation), Milestone 6.2 (Generate Vertical Slice), Milestone 6.3 (Batch Vertical Slice), Milestone 6.4 (Desktop Packaging & Production Release Validation), and Milestone 6.5 (Automated Portable Quality Gates & CI Configuration).
**Status (22 September 2026)**: M1/M2 domain baseline and M3.0 reconciliation implemented; M3.1 runtime/lifecycle, M3.2 primitive execution and inspection, and M3.3 multi-format artifact export and pipeline finalization implemented, hardened, and verified. Milestone 4 is fully implemented, hardened, and verified across all components: M4.1 (generation application orchestration), M4.2 (optional text-only Gemini proposal adapter), M4.3 (deterministic example catalog), M4.4 (canonical run manifest and sidecar publication), and M4.5 (strict generation stdio IPC, launchers, and packaging). Milestone 4 is complete. Milestone 5 is fully implemented, hardened, and verified across all components: M5.1 (canonical batch request/response/manifest contracts, schemas, typed models, and operation metadata registry), M5.2 (batch execution infrastructure, typed bindings, pure work allocation, tracked-document task generalization, observable teardown, and shared sequential batch service orchestration), M5.3 (batch filesystem safety boundary, streaming SHA-256 source snapshots, guarded workspaces, atomic no-replace publication, and document close lifecycle hardening), M5.4 (genuine batch format handlers and native export operations for 3D model export [STEP/STL] and 2D drawing publication [PDF/DXF], drawing view refresh, assembly reference verification via FileMissing, and closed-output validation), M5.5 (conditional Parasolid export gate, promoted schema/model/registry alignment, and verified text Parasolid export [.x_t] across .par/.psm/.asm), and M5.6 (summary manifest publication, strict batch stdio transport, cooperative signal cancellation, launchers, packaging, and live integration gates). Milestone 5 is complete. Milestone 6.1 (Clean Tauri/React Desktop Foundation, FR-20), Milestone 6.2 (Generate Vertical Slice, FR-21), and Milestone 6.3 (Batch Vertical Slice, FR-22) are fully implemented, hardened, and verified across all automated, native, live COM, and desktop GUI acceptance gates (both Tauri dev and windowed release hosts). Milestone 6.4 (Desktop Packaging & Production Release Validation, FR-21/22) is in progress: M6.4 packaging architecture, NSIS configuration, offline verification, per-user installation, installed Generate and Gemini success paths, and installed Generate cancellation are verified; the remaining installed failure, Batch, path, and uninstall acceptance gates remain pending. Milestone 6.5 (Automated Portable Quality Gates & CI Configuration, QA-08, RUN-10) is fully implemented, hardened, and verified: dedicated read-only three-job GitHub Actions workflow (`.github/workflows/ci.yml`), pinned action SHAs, Python CI developer constraints, honest native test-gate classification (`#[ignore]`), pre-checkout line-ending normalization on Windows, and clean hosted execution across Python 3.14.3 (`windows-2025`), Node 24.15.0/pnpm 12.4.1 (`ubuntu-24.04`), and Rust 1.95.0/Tauri (`windows-2025`) passing 100% green. Milestone 6.6 publication has not started.

### Current evidence boundary

- **Offline test suite**: **2,434 passed, 4 skipped, 55 deselected in 74.05s** across all domain packages (`pytest -c pytest.ini -m "not com and not live_ai"`, the 4 skips being Windows symlink-permission guards); the dedicated batch test suite recorded **814 passed, 2 skipped** across 30 test modules; the focused runtime lifecycle/security suite passed all **84 tests**; strict mypy passed across the verified component scope (**0 issues in 174 source files** checked across `src`, `tests/batch`, `tests/ipc`, and `tests/drivers`); Ruff lint passed and formatting clean across all engine files; `git diff --check` clean.
- **Desktop Generate workflow verification evidence (Milestone 6.2, FR-21)**:
  1. **Automated Rust & Native Baseline**: **62 passed, 0 failed** across unit and integration suites (`cargo test --manifest-path desktop/src-tauri/Cargo.toml`): 58 unit tests, 2 synthetic process lifecycle integration tests (`test_explicit_spawn_and_bounded_pipes`, `test_targeted_cancellation_unwinds_target_and_preserves_unrelated`), and 2 live IPC cancellation qualification tests (`test_live_ipc_cancellation_shared_console`, `test_live_ipc_cancellation_private_console`). Under `$env:CAD_COPILOT_LIVE_TESTS="1"`, all 4 integration tests execute live against genuine Python child processes, verifying targeted `CTRL_BREAK_EVENT` delivery and 130 exit code with empty stdout and clean output root. Strict Clippy clean with 0 warnings (`cargo clippy -- -D warnings`); canonical formatting clean (`cargo fmt -- --check`); native Windows binary compiles cleanly (`cargo check`).
  2. **Frontend Test & Production Build**: **53 passed, 0 failed** across 9 Vitest suites (`pnpm test:desktop`); TypeScript typecheck clean with 0 errors (`tsc -b --noEmit`); ESLint clean with 0 warnings (`eslint . --max-warnings=0`); Prettier clean (`prettier --check .`); production Vite build clean (`dist/index.html`, `dist/assets/`).
  3. **Live Solid Edge 2026 CLI & IPC Integration (19 September 2026)**: Verified end-to-end against licensed Siemens Solid Edge 2026 (`226.00.00.106`) via automated native CLI launcher and lifecycle suites:
     - `test_m45_live_01_example_plan_spur_gear_cmd_launcher` passed in 31.44s: genuine Solid Edge Part document created, Ordered 24-tooth spur gear with center through-bore constructed, 1 solid body with positive volume inspected (`volume_mm3 > 0.0`), all 4 published artifacts (`.par`, `.step`, `.stl`, `.jpg`) verified, schema-valid `run_manifest.json` sidecar published with matching cryptographic SHA-256 digests, clean staging removal, and zero orphan `Edge.exe` processes.
     - `test_m45_live_03_route_and_configuration_isolation` passed in 22.68s: missing API key fails fast with `PROMPT_INTERPRETATION_FAILED` before CAD runtime acquisition; invalid model fails fast; subsequent example rerun executes cleanly on live Solid Edge with zero cross-run interference.
     - Synthetic & live cancellation: synthetic console isolation (`test_targeted_cancellation_unwinds_target_and_preserves_unrelated`) and live child console signal delivery (`test_live_ipc_cancellation_*` under `CAD_COPILOT_LIVE_TESTS=1`) verify `CTRL_BREAK_EVENT` unwinds target processes without signal bleed to unrelated control children.
  4. **Live Windows 11 GUI Smoke & End-to-End Acceptance (19 September 2026)**: Verified running native desktop host via `pnpm --filter @cad-copilot/desktop tauri dev`:
     - *Output Folder Selection*: Native Windows folder picker (`rfd`) verified; selected local directory path rendered, warning resolved, and path containment locked.
     - *Deterministic Example Generation*: Default `Conceptual Spur Gear (Example)` executed on live Siemens Solid Edge 2026 (`build 226.00.00.106`); real-time 4-phase progress updates observed (`Request Received` -> `Request Validated` -> `Generating in Solid Edge` -> `Finalizing Artifacts` -> `Checking`); `CAD Model Successfully Generated` result card rendered; verified 3 primary CAD artifacts (`.par` 420.0 KB, `.step` 181.5 KB, `.stl` 26.1 KB) against published `run_manifest.json`; non-fatal preview warning and fallback placeholder rendered per specification; **Show in folder** Explorer reveal verified.
     - *Live Gemini AI Prompt-to-CAD Generation*: Session API key saved in memory; natural-language prompt `Create a 50x40x10 mm rectangular block` delivered to Gemini 3.5 Flash Lite over HTTPS; structured proposal decoded and validated; genuine Solid Edge Part generated, volume inspected, and artifacts published.
     - *Route Isolation & Fast-Fail Security*: Invalid prompt/schema variations fail fast with `PROMPT_INTERPRETATION_FAILED` before CAD runtime acquisition, leaving zero corrupt directories and preserving zero-secret non-disclosure.
     - *In-Flight Active CAD Cancellation*: Clicking red "Cancel Generation" button in the UI during active feature creation (`Generating in Solid Edge`) cleanly delivers `CTRL_BREAK_EVENT`; child process unwinds with exit code 130; UI returns cleanly to idle form; zero orphan `python.exe` or `Edge.exe` processes remain; subsequent rerun succeeds immediately without application restart.
     - *In-Flight Window Close Interception & Teardown*: Attempting to close application window (`X` / `Alt+F4`) during active CAD execution triggers confirmation dialog ("Generation in Progress"); clicking "Keep Generating" (or Escape) resumes generation uninterrupted; clicking "Cancel & Exit" cleanly terminates child process and exits with zero orphan processes.
     - *In-Flight Provider-Wait Cancellation*: Clicking red "Cancel Generation" button during `Request Validated` while awaiting Gemini HTTPS response cleanly delivers `CTRL_BREAK_EVENT`; child process unwinds with exit code 130; Solid Edge is never contacted or opened; zero orphan `python.exe` processes remain.
     - *Release Host Qualification & GUI Subsystem Probe Hardening*: Standalone compiled release binary `cad-copilot-desktop.exe` launched directly from Windows Explorer (without attached parent console); prerequisite probe hardened with explicit `Stdio::null()` stdin redirection, eliminating Windows GUI subsystem `ERROR_INVALID_HANDLE (os error 6)`; verified embedded frontend asset loading without development server; verified child process spawn in hidden console (`CREATE_NEW_CONSOLE` + `SW_HIDE`) with zero flashing console windows; verified release in-flight cancellation.
- **Desktop foundation verification evidence (Milestone 6.1)**:
  1. **Automated & Static Baseline**: **41 passed, 0 failed** across 7 Vitest component and integration suites (`pnpm test:desktop`); strict Rust checks clean (`cargo test` 1 passed, `cargo fmt --check`, `cargo clippy -- -D warnings`); TypeScript (`tsc -b --noEmit`) and ESLint (`eslint . --max-warnings=0`) clean with 0 errors and 0 warnings; production Vite build clean (`dist/index.html`, `dist/assets/`); authentic multi-layer Windows icon (`desktop/src-tauri/icons/icon.ico`) compiled into debug and release native binaries (`cad-copilot-desktop.exe`); automated assertions confirm zero network requests, zero native IPC, and zero persistent storage in the test harness.
  2. **Live Windows 11 GUI Smoke Acceptance (16 September 2026, commit `a4a1146` / `bff2f7d`)**: Verified running native host via `pnpm --filter @cad-copilot/desktop tauri dev`:
     - *Window & Identity*: Default `1200x800` launch dimensions and `900x600` minimum size constraint enforced; at minimum `900x600` dimensions, the layout remains completely unclipped with zero horizontal scrolling; authentic CAD Copilot Mobius brand icon verified in the native Windows titlebar and taskbar from compiled multi-layer ICO (`16, 24, 32, 48, 64, 256` px).
     - *Navigation & Collapsed Usability*: Keyboard Tab and Shift+Tab navigation traverses all interactive elements; `Enter` and `Space` keys reliably activate navigation links and the collapse toggle; view switching operates cleanly between `Generate` (default) and `Batch`; sidebar collapse (`w-72` to `w-16`) smoothly transitions while preserving accessible `aria-label`s and tooltips on icon triggers without layout clipping.
     - *Dialog Focus Management & Containment*: Opening the Settings/Diagnostics dialog places initial focus directly on the Close button; subsequent Tab and Shift+Tab traversal is strictly contained within the dialog without escaping to background content; pressing `Escape` or clicking close dismisses the modal and reliably restores focus to the triggering diagnostics button.
     - *Runtime & Process Isolation*: Native process inspection observed only expected WebView2 subsystem processes (browser, renderer, GPU helper) under `cad-copilot-desktop.exe`; zero spawned Python interpreters, zero CLI subprocesses (`cad-copilot-generate`, `cad-copilot-batch`), and zero background CAD processes (`Edge.exe`).
     - *Network, HMR & Security Policy*: Zero outgoing external network requests observed; runtime traffic is strictly confined to local development Vite HMR (`ws://localhost:1420` and `http://localhost:1420`) permitted by `devCsp`; HMR reconnects cleanly upon dev server reload with zero CSP violation errors in the WebView2 console; production CSP permits only local assets (`default-src 'self'`) and required Tauri IPC (`connect-src 'self' ipc: http://ipc.localhost`); zero secret keys, tokens, or history persisted to `localStorage`, `sessionStorage`, or IndexedDB across relaunch.
- **Combined live Solid Edge 2026 verification evidence (15 September 2026)**: **14 passed, 0 skipped in 390.10s** across the combined live batch CLI suite and live regression matrix on a licensed local Siemens Solid Edge 2026 installation (build `226.00.00.106`, tested snapshot byte-identical to commit `34dec71`), verifying:
  1. B-LIVE-M56-01 native 3D model CLI export (`.par`, `.psm`, `.asm` to STEP, STL, Parasolid `.x_t`): exact single-line stdout response, exact byte-for-byte canonical JSONL progress stream on stderr, durable atomic summary manifest publication with cryptographic SHA-256 digests matching disk, exact response-manifest parity across all terminal fields, zero temporary file leakage, and 100% cryptographic source immutability.
  2. B-LIVE-M56-02 2D drawing publication CLI (`.dft` to PDF and DXF): in-memory drawing view refresh, exact progress stream framing, zero disk saves of source drawing, full response-manifest parity, and source immutability.
  3. B-LIVE-M56-03 isolation & pre-existing manifest sentinel collision: normal artifacts complete before publication failure, atomic no-replace publication fails closed with `failed / MANIFEST_PUBLICATION_FAILED` and `manifest=None` preserving pre-existing sentinel file unchanged, sanitized diagnostics without path or secret leakage, followed by subsequent healthy batch execution with distinct request ID in the same output root.
  4. B-LIVE-M56-04 cooperative console cancellation: multi-file batch receiving `CTRL_BREAK_EVENT` synchronization during execution cleanly unwinds, produces valid `status="cancelled"` terminal response and published cancelled manifest with exact cancelled file accounting (`cancelled_files` includes `carrier.asm` matching the published manifest), zero artifact leakage for unstarted files, clean child process exit, and zero orphan `Edge.exe` processes.
  5. Live regression suites: M5.4 formats matrix (`test_batch_formats_live.py`, 5 passed) and M5.5 Parasolid matrix (`test_parasolid_live.py`, 5 passed) executed within the combined live run with zero process leaks and clean teardown.
- **Live integration evidence**: 31 passed, 0 skipped across 31 COM tests on a licensed Siemens Solid Edge 2026 session (`226.00.00.106`), verifying:
  1. M3.1 lifecycle isolation, Ordered mode readback `2`, and non-destructive process preservation (4 live gates).
  2. M3.2 3D primitives (cuboid, cylinder, 24-tooth conceptual spur gear at origin and translated with centered through-bore), 6-face circular cuts, localized +Z cuts, and sequential feature execution (15 live gates).
  3. M3.3 multi-format artifact export characterization, identity preservation, and atomic staging directory rename (1 live gate).
  4. M3.3 end-to-end smoke matrix verifying pipeline finalization across 5 representative geometries (cuboid, cylinder, plate with cut, sequential features, gear) producing valid `.par`, `.step`, `.stl`, and `.jpg` (5 live gates).
  5. M3.3 independent reopening of exported native `.par` in Solid Edge proving valid 3D Ordered geometry with 1 solid model (1 live gate).
  6. M3.3 borrowed session isolation and unrelated document preservation during artifact finalization (1 live gate).
  7. M3.3 owned session graceful lifecycle without force kill after artifact finalization (1 live gate).
  8. M3.3 induced required export failure verifying staging cleanup, absence of final directory, and session preservation for subsequent requests (1 live gate).
  9. M3.3 induced preview failure verifying graceful degradation, retention of `PREVIEW_EXPORT_FAILED` warning, and required model publication (1 live gate).
  10. M3.3 publication collision rejection verifying `TARGET_ALREADY_EXISTS` without mutating pre-existing targets (1 live gate).
- **M5.5 live Solid Edge 2026 verification evidence (14 September 2026)**: **5 passed, 0 skipped in 149.11s** across the complete live Parasolid matrix (`test_parasolid_live.py`) on a licensed local Siemens Solid Edge 2026 installation (build `226.00.00.106`), verifying:
  1. C-LIVE-M55-01 native 3D export matrix: bulk export of `.par`, `.psm`, and `.asm` to Parasolid text (`.x_t`) with structural ASCII transmission validation, non-mutating `OpenWithTemplate` reopen count probes, zero auxiliary sidecar generation, and 100% cryptographic source and artifact immutability.
  2. C-LIVE-M55-02a target collision & partial outcome: atomic collision rejection against pre-existing `.x_t` targets preserving sentinel contents and producing valid `partial` status without mutating outputs.
  3. C-LIVE-M55-02b unresolved assembly isolation: dedicated unresolved assembly with missing child component detected via `FileMissing()`, failed closed with zero exported `.x_t` artifacts, followed by successful batch continuation to healthy `.par`.
  4. C-LIVE-M55-02c cooperative cancellation cleanup: mid-batch cancellation cleanly purges private staging workspaces and prevents partial artifact leakage into `output_root`.
  5. C-LIVE-M55-02d localized failure isolation & teardown: test-injected validation failure triggers immediate workspace purge and format failure isolation, while verifying healthy runtime preservation and confirmed clean teardown (`runtime.teardown() is True`).
- **M5.4 live Solid Edge 2026 verification evidence (12 September 2026)**: **5 passed, 0 skipped in 100.06s** across the complete live batch format matrix (`test_batch_formats_live.py`) on a licensed local Siemens Solid Edge 2026 installation (build `226.00.00.106`), verifying:
  1. B-LIVE-M54-01 native 3D export matrix: bulk export of `.par`, `.psm`, and `.asm` to STEP and STL (observed binary STL) with 100% cryptographic source immutability (pre- and post-export SHA-256 matches disk).
  2. B-LIVE-M54-02 2D drawing publication: `.dft` to vector PDF v1.4 and text ASCII DXF with in-memory view refresh and zero disk save.
  3. B-LIVE-M54-03 unresolved assembly isolation: dedicated unresolved assembly with missing component detected via `FileMissing()`, failed closed with zero exported artifacts and zero path/component leakage, followed by successful continuation to healthy `.par`.
  4. B-LIVE-M54-04 collision & partial outcome: atomic collision rejection against pre-existing targets preserving sentinel files, producing valid `partial` result without mutating outputs.
  5. B-LIVE-M54-05 cooperative cancellation cleanup: mid-batch cancellation cleanly removes private staging workspaces and discards in-flight work with zero partial artifact leakage.
  6. Live runtime teardown assertion: all 5 live gates verified `runtime.teardown() is True`, enforcing zero leaked document handles, unquiesced workers, or orphaned owned processes.
- **M4 focused live component evidence (6 September 2026)**: 5 passed, 0 skipped across the focused M4 component suite on a licensed local Solid Edge 2026 installation (`test_m41_live_01`, `test_m41_live_02`, `test_m44_live_01`, `test_m44_live_02`, `test_m43_live_example_catalog_spur_gear`), verifying:
  1. Injected deterministic `prompt_to_cad` block and `example_plan` conceptual spur-gear orchestration (2 live gates).
  2. End-to-end prompt-to-CAD and example-plan generation with atomic `run_manifest.json` publication, draft 2020-12 schema validation, exact file hash/size consistency, prompt SHA-256 fingerprinting, positive-volume solid inspection, and ownership-safe teardown (2 live gates).
  3. Production deterministic example catalog loading (`resolve_example_plan(request)` with `request.example_id="spur_gear"`) driving full `GenerationService` orchestration, native Solid Edge 3D Ordered geometry creation, atomic multi-format artifact export (`.par`, `.step`, `.stl`, `.jpg`), schema-valid `run_manifest.json` sidecar publication with `provenance.kind="example_plan"` and `source_id="spur_gear"`, and zero-leak privacy boundary (1 live gate).
- **M4.2 live Gemini verification evidence (7 September 2026)**: 1 passed in 3.87s (`test_live_gemini_proposal_resolution`), verifying:
  1. Text-only `gemini-3.5-flash-lite` plan proposal generation via `google-genai` with sampling parameters omitted per Gemini 3.x guidance.
  2. Bounded strict JSON decoding, sealed proposal schema validation, local envelope binding, canonical AST parsing, geometric validation, exact dimension checks (50.0 x 40.0 x 10.0 mm), and canonical lowering.
  3. Clean stdout, stderr, and log output (zero leakage of prompts, keys, or provider logs).
  4. Process-level environment key removal verified after test execution.
- **M4.5 live CLI verification evidence (8 September 2026)**: 3 passed, 0 skipped across live CLI gates on licensed local Siemens Solid Edge 2026 (`test_m45_live_01_example_plan_spur_gear_cmd_launcher`, `test_m45_live_02_prompt_to_cad_installed_entrypoint`, `test_m45_live_03_route_and_configuration_isolation`), verifying:
  1. End-to-end execution of `spur_gear` example through `engine\scripts\generate.cmd` against live Solid Edge: byte-exact single-line stdout ending in LF, exactly 4 ordered JSONL progress records on stderr, zero console contamination, positive-volume solid inspection (`volume_mm3 > 0.0`), all 4 published artifacts (`.par`, STEP, STL, JPG), valid `run_manifest.json` sidecar with cryptographic SHA-256 hashes matching disk files, and staging cleanup.
  2. End-to-end installed-entrypoint prompt execution (`cad-copilot-generate`) with opt-in live Gemini: resolves natural-language block prompt via `gemini-3.5-flash-lite`, generates 3D solid model in live Solid Edge with positive volume inspection (`volume_mm3 > 0.0`), publishes `.par`, `.step`, `.stl`, `.jpg` and schema-valid `run_manifest.json` sidecar (`provenance.kind="ai_proposal"`, cryptographic file hashes, prompt SHA-256 fingerprint), cleans staging, and enforces zero leakage of prompts or API keys across stdout, stderr, response, and manifest.
  3. Configuration and route isolation through installed entrypoint (`cad-copilot-generate`): missing key and invalid model fail fast before CAD runtime acquisition, and subsequent example rerun executes cleanly on live Solid Edge.

---

## 1. Document Scope, Goals & Non-Goals

This specification defines the functional, architectural, and quality requirements for the foundational milestones of the CAD Copilot project. Milestone 5 (Batch Automation Contracts, Safety Boundary, Format Handlers, Parasolid Promotion, Summary Manifest Publication & Strict Stdio IPC) is complete. Milestone 6.1 (Clean Desktop Foundation, `@cad-copilot/desktop`) is complete, followed by the M6.2 Generate vertical slice, M6.3 Batch vertical slice, and M6.4 shared hardening. Only approved conditional/follow-up capabilities may extend that scope; no later package or unverified capability is implied to exist by this specification.

### Goals
- **Milestone 1 (Foundation & Governance)**: Establish monorepo workspace configuration (`desktop/` and `engine/`), developer tooling, MIT licensing with an explicit Siemens trademark notice, vendor-agnostic abstract domain interfaces (`engine/src/interfaces/`), and canonical JSON Schemas (`contracts/`) for cross-boundary communication.
- **Milestone 2 (Pure Domain Geometry)**: Implement a pure, deterministic geometry domain (`engine/src/geometry/`) containing planar coordinate transformations, parametric spur gear tooth math, feature plan AST parsing, lowering models, spatial containment validation, and STEP Part 21 smoke analysis.
- **Milestone 3 (Solid Edge COM Driver & Artifacts)**: Implement the Windows Solid Edge® COM automation driver (`SolidEdgeRuntime` and `SolidEdgeExecutor` in `engine/src/drivers/solidedge/`) via standard public COM Dispatch, executing confirmed 3D base primitives, localized 2D cutouts, geometric recompute verification, and atomic multi-format artifact exports (`.par`, STEP, STL, preview JPG) on live Windows workstations.
- **Milestone 4 (Generation Orchestration & Workflows — Complete)**: Orchestrate single-request 3D parametric generation through a modular application coordinator (`GenerationService`), supporting deterministic example plans (`example_catalog`) and an optional text-only Gemini proposal adapter (`plan_providers`), with canonical run manifest creation (`manifests`), safe error/warning projection, and strict stdio IPC (`ipc`). M4.1 (application orchestration), M4.2 (Gemini adapter), M4.3 (deterministic example catalog), M4.4 (run manifest publication), and M4.5 (strict generation stdio IPC) are fully implemented, hardened, and verified. Milestone 4 is complete.
- **Milestone 5 (Batch Automation Contracts, Safety, Formats & IPC — Complete)**: Provide robust, local-first sequential native-file batch export and drawing publication for Solid Edge parts, sheet metals, assemblies, and drawings. M5.1 (contracts and operation registry), M5.2 (execution service and task generalization), M5.3 (filesystem and source-integrity safety boundary), M5.4 (genuine format handlers for STEP, STL, PDF, DXF), M5.5 (Parasolid `.x_t` promotion and verification), and M5.6 (summary manifest publication, strict batch stdio transport, signal cancellation, and packaging) are fully implemented, hardened, and verified. Milestone 5 is complete.
- **Milestone 6.1 (Clean Tauri/React Desktop Foundation — Complete)**: Establish a focused, source-runnable Windows desktop foundation in `@cad-copilot/desktop` using Tauri v2 and React 19/TypeScript. Provide honest two-screen navigation (`Generate` and `Batch`), a settings/diagnostics panel reflecting typed baseline status without secret persistence or fake connections, a top-level error boundary preventing blank-window failures, a restrictive local-only capability and CSP baseline, and automated verification across config, Rust, and frontend components.

### Explicit Non-Goals
- **No Network or AI Calls in Engine Driver**: Zero network requests or LLM SDK dependencies within the core driver (LLM generation pipelines and prompt-to-CAD translation are encapsulated in Milestone 4).
- **No Engine Invocation or Subprocess Spawning in M6.1**: No Python child process execution, stdio parsing, or CAD generation/batch integration in the M6.1 desktop foundation (deferred to vertical slices M6.2 and M6.3).
- **No Filesystem Dialogs or Arbitrary File Access in M6.1**: No native file/folder pickers, arbitrary path inspection, or broad Tauri filesystem capabilities in M6.1.
- **No Secret Persistence or Mock Execution in M6.1**: No Gemini API key input/storage, no fake CAD execution, no simulated success states, and no local/cloud authentication or backend servers.
- **No Proprietary Siemens Binary SDK / DLL Linkage**: Automation operates strictly through standard, public Windows COM Dispatch interfaces (`pywin32` / `win32com.client.Dispatch("SolidEdge.Application")`). Zero internal DLL reverse-engineering, decompilation, or undocumented binary hooking.
- **No Out-of-Scope / Deferred Operations**: Image-to-CAD, stateful prompt-edit sessions, manufacturing-grade gear analysis, and unverified batch formats remain strictly excluded.

---

## 2. Functional Requirements

### FR-1: Monorepo Foundation & Governance (Milestone 1)
- **Monorepo Workspaces**: Root workspace configured via `pnpm-workspace.yaml` declaring `desktop` and `engine`; both workspaces are established and integrated into root convenience orchestration.
- **Python Configuration**: Base engine managed via PEP 508 / PEP 621 compliant `engine/pyproject.toml` targeting Python `>=3.14.3,<3.15` (tested baseline: Python 3.14.3) with platform-specific markers (`pywin32>=306; sys_platform == 'win32'`).
- **Developer Tooling**: Shared configuration files for static typing (`engine/mypy.ini`), linting/formatting (`engine/ruff.toml`), and test discovery (`engine/pytest.ini`).
- **Legal & Governance**:
  - `LICENSE`: Standard MIT License with formal trademark notice:
    > *Solid Edge® is a registered trademark of Siemens Industry Software Inc. CAD Copilot is an independent open-source project and is not affiliated with, endorsed by, or sponsored by Siemens.*
  - `SECURITY.md`: Vulnerability reporting instructions and secret handling policies.
  - `CONTRIBUTING.md`: Development setup, coding conventions, and Conventional Commit traceability standards (`<type>(<scope>): [<FR-ID>] <summary>`).
  - `.github/ISSUE_TEMPLATE/`: Standardized YAML issue templates for bug reports (`bug_report.yml`) and feature requests (`feature_request.yml`).
- **Repository Hygiene**: Multi-stack `.gitignore` preventing commit of CAD binaries (`*.par`, `*.psm`, `*.asm`), secrets, and build artifacts. `.env.example` template for runtime configuration.

### FR-2: Domain Contracts & DSL Schemas (Milestone 1)
- **JSON Schema Namespace**: Canonical schemas defined under `https://cad-copilot.dev/schemas/` conforming to JSON Schema Draft 2020-12.
- **Wire Protocol Standard**: Semantic `"contract_version": "1.0"` decoupled from domain operation `kind` across all payloads.
- **Schema Contracts (`contracts/`)**:
  - `contracts/schemas/generation/`: 3D CAD prompt generation request/response envelopes (`generation-request.schema.json`, `generation-response.schema.json`, `"origin": "cad_copilot"`).
  - `contracts/schemas/batch/`: Batch task definitions, export configurations, and summary manifest schemas (`batch-request.schema.json`, `batch-response.schema.json`, `batch-manifest-v1.schema.json`).
  - `contracts/schemas/edit/`: Historical, non-runtime edit schemas (`edit-request.schema.json`, `edit-response.schema.json`). Their legacy sessions, images, and selectable outputs are not initial capabilities. Future M7B editing is constrained by a manifest-based revision model, not a session database or rollback system.
- **Runbooks & Fixtures**: Internal feature-plan examples (`contracts/examples/`) cover plates, conceptual gears, and conditional composition/sweep representations. JSON fixture integrity and public schema conformance tests do not establish end-to-end or live CAD support; the generation runbook describes the implemented M4.5 stdio IPC launcher, whereas `contracts/schemas/batch/README.md` serves as the canonical contract guide for the batch interface (M5.1 through M5.6 implemented and verified; M6.3 desktop client integration fully implemented, hardened, and verified across automated, live COM, and desktop GUI acceptance gates in both dev and release hosts; M6.4 shared consolidation, desktop packaging, and release verification in progress).

### FR-3: Abstract Domain Interfaces (Milestone 1)
- **CAD Runtime Port (`CADRuntimeABC` in `engine/src/interfaces/`)**:
  - Abstract interface defining the lifecycle contract for CAD processes: application connection, document opening/closing, and graceful process teardown.
- **CAD Execution Port (`CADExecutorABC` in `engine/src/interfaces/`)**:
  - Abstract interface defining modeling operations: solid body creation, localized cutouts, multi-format model exports (PAR, STEP, STL), best-effort preview capture (JPG), terminal document release, and authoritative inspection of physical properties (volume, mass, feature count).
- **Normalized Exception Hierarchy**:
  - Normalized exception taxonomy (`CADError`, `CADRuntimeError`, `CADExecutionError`, `CADDocumentError`) isolating caller domains from vendor-specific COM/kernel errors.

### FR-4: Planar Coordinate Mapping & Feature Lowering (Milestone 2)
- **Planar Coordinate Projection (`FaceContext` in `engine/src/geometry/`)**:
  - Bidirectional coordinate mapping between 2D sketch plane coordinates $(u, v)$ on canonical planar faces and global Cartesian $(x, y, z)$ space.
  - Supports canonical rectangular prism faces (`+Z`, `-Z`, `+X`, `-X`, `+Y`, `-Y`), cylinder and sphere default axial `+Z` faces, and spur gear `+Z` faces.
  - Complete 6-face origin matrix conforming to base body placement ($p_x, p_y, p_z$) and thickness $t$ ($+Z \to p_z + t$, $-Z \to p_z$, $\pm X/\pm Y \to p_z + t/2$).
  - Full execution cut frame generation including explicit origin offset (`origin_offset_mm`), orthonormal $U/V$ sketch axes, face normal vector, and inward cut vector $\mathbf{c} = -\mathbf{n}$.
- **Feature Plan AST & Domain Models**:
  - Strongly typed domain models for concrete parametric primitives:
    - Base bodies: `RectangularBaseBody` (`rectangular_prism`), `CylinderBaseBody` (`cylinder`), `SphereBaseBody` (`sphere`), `SpurGearBaseBody` (`spur_gear`), `RevolvedShaftBaseBody` (`revolved_shaft`).
    - Features: `CircularThroughHoleFeature` (`circular_through_hole`), `RectangularThroughCutoutFeature` (`rectangular_through_cutout`), `SlotThroughCutoutFeature` (`slot_through_cutout`), `RectangularExtrudedPadFeature` (`rectangular_extruded_pad`), `RevolvedProfileFeature` (`revolved_profile`), `ProfileCutoutFeature` (`profile_cutout`), `SweptProtrusionFeature` (`swept_protrusion`).
  - AST parsing and lowering pipeline translating declarative JSON feature plans into ordered semantic patches. Domain support is broader than the guaranteed M3.2 handler registry; conditional families do not become advertised capabilities merely because the parser accepts them.
  - Mode-neutral parser recognizing clean operation, face, and axis aliases without spurious warnings, while explicitly tracking domain fallback diagnostics with original input values.
  - Multi-primitive composition and B-Rep boolean lifecycle state tracking with unified global identifier namespace and reserved prefix protection (`RESERVED_IDENTIFIER_PREFIX`).

### FR-5: Parametric Spur Gear Geometry (Milestone 2)
- **Deterministic Conceptual Spur Gear Outline (`gear_math.py` in `engine/src/geometry/`)**:
  - Analytical computation of deterministic conceptual 2D spur gear tooth outlines based on module ($m$), tooth count ($z$), pressure angle ($\alpha$), and root/tip bounds with 5-point-per-tooth polygon discretization (5 unique cyclic points per tooth).
  - Generates conceptual polygon vertex sequences, optionally repeating the first point for explicit closure; a center bore is lowered as a separate cut. This is not a manufacturing-grade involute profile or live-kernel validity guarantee.
  - Automatic $+Z$ elevation sketch and cut frame generation aligned with gear face width and placement.

### FR-6: Spatial Boundary, Containment & Warning Propagation (Milestone 2)
- **Pre-Execution Boundary Checking (`engine/src/geometry/validators/`)**:
  - Universal polygon sanity validation ($3 \le N \le 512$, finite coordinates, non-duplicate vertices across all edges including closing edge $N-1 \to 0$, and non-zero Shoelace area $> 10^{-6}\text{ mm}^2$) across profile cutouts, revolved features, revolved shafts, and swept cross-sections.
  - Geometric fit checks for supported child-feature/parent-face combinations; capability-first mode may retain fit or clearance warnings instead of rejecting, while strict mode rejects them.
  - Configured edge-clearance checks and circular sibling-hole overlap checks on the same body/face. These are not general feature-intersection or CAD topology proofs.
- **Warning & Fallback Diagnostic Propagation Protocol**:
  - **Parser Fallbacks**: When the parser encounters an unrecognized domain value, it emits a `ValidationDiagnostic(severity="warning", code=..., message=..., path=...)` and records a `DefaultApplied(path=..., value=..., reason=..., original_value=...)` preserving the raw unparsed input.
  - **Boundary Margin Relaxation**: When geometric fit or edge margins are relaxed under capability-first mode, validators emit a soft warning `ValidationDiagnostic(severity="warning", code="GATE_POLICY_RELAXED", message=..., path=...)` without mutating values.
  - **Gate Policy Modes**:
    - `capability_first`: Preserves warnings as soft non-fatal diagnostics, allowing valid geometry to proceed to execution.
    - `strict`: Hard-rejects any accumulated fallback or margin warnings with `FeaturePlanValidationError`.
  - **Lowering IR Propagation**: Lowered execution payloads carry accumulated `diagnostics` and `defaults_applied` within the top-level `metadata` dictionary.
  - **Execution Interface Exposure**: Domain response and failure models (`ExecutionSuccess`, `ExecutionFailure`) expose `warnings: list[dict[str, str]]`. Mapping to public JSON stdio responses belongs to the later application layer; no RPC server is implemented.
- **STEP Sanity Verification (`step_checker.py` in `engine/src/geometry/`)**:
  - In-memory pure-domain structural envelope, topological entity presence (`MANIFOLD_SOLID_BREP`, `ADVANCED_FACE`), ISO 10303-41 length unit scale, and dimensional sanity validation on exported STEP Part 21 text.

### FR-7: Solid Edge COM Runtime Lifecycle (M3.1 — Implemented and Verified)

The implementation is in `engine/src/drivers/solidedge/`. The requirements below remain acceptance criteria; see the evidence boundary above for verified live execution details.
- **COM Initialization & Dispatch**:
  - `SolidEdgeRuntime` marshals COM operations to a dedicated STA worker with message pumping and worker-private native objects; callers receive opaque application/document handles.
  - Attach to a responsive existing instance as borrowed. A spawn path is entered only for `MK_E_UNAVAILABLE`; owned classification requires PID/creation-time proof. Unproven ownership remains `unknown`. Only verified owned instances receive `Visible=True` / `DisplayAlerts=False` writes.
- **Runtime Diagnostics & Bounded Retries**:
  - Required diagnostics capture version/build (best effort), PID when available, ownership (`owned`, `borrowed`, or `unknown`), attachment mode, visibility, and health. Unavailable version metadata must not fail an otherwise usable connection.
  - Diagnostic warnings include `VERSION_METADATA_UNAVAILABLE` when version metadata is absent or unreadable, preserving healthy connections without failing runtime attachment.
  - Bounded busy-call retry strategy with backoff handling recognized retryable busy/rejected HRESULTs (`RPC_E_CALL_REJECTED` / `RPC_E_SERVERCALL_RETRYLATER`).
- **Document Lifecycle & Session Safety**:
  - Document management operating strictly via explicit document handles (`doc = documents.Add("SolidEdge.PartDocument")`) rather than ambiguous global `ActiveDocument` references.
  - New part documents target Ordered mode (`ModelingModeConstants`: Synchronous `1`, Ordered `2`). Tracking is registered immediately after creation; setting `ModelingMode=2` is followed by immediate readback verification. Mode setup failure/mismatch triggers `Close(False)`; tracking is removed only after successful immediate closure and is retained for teardown retry if closure fails. Existing opened files must not have their mode changed. On fresh Solid Edge user profiles, the vendor's one-time Ordered-mode confirmation dialog must be completed before unattended generation.
  - The offline regression `test_create_part_document_retains_tracking_for_teardown_when_immediate_close_fails` covers mode mismatch, failed immediate close, retained tracking, and successful teardown retry.
  - Ownership-aware teardown closes only tracked request documents without saving and attempts graceful shutdown for owned applications. Force cleanup requires `force_kill_on_failure=True`, a recorded verified owned PID, and a bounded failure/still-alive condition; creation identity is rechecked before termination. Borrowed/unknown applications are never quit or killed, and unrelated documents are not closed.

### FR-8: Solid Edge Geometric Primitive Execution & Validation (M3.2 — Implemented and Verified)
- **Base Body Creation**:
  - Implements `CADExecutorABC` via `SolidEdgeExecutor` consuming lowered feature plans from `engine/src/geometry/plan_lowering.py`.
  - Constructs confirmed initial base bodies in native Solid Edge documents:
    - Rectangular prisms/plates (`rectangular_prism`) via profile sketches and finite extrusions.
    - Cylinders (`cylinder`) via circular profiles extruded along the designated axis.
    - Conceptual spur gears (`spur_gear`) via discretized multi-segment tooth profile sketches with optional axial center bores.
- **Localized Planar Feature Lowering**:
  - Executes localized 2D cutouts and pads on canonical reference planes and planar faces using `FaceContext` projection and cut frames:
    - Circular through and blind holes (`circular_through_hole`).
    - Rectangular through and blind cutouts (`rectangular_through_cutout`).
    - Slot through and blind cutouts (`slot_through_cutout`).
    - Extruded rectangular pads/bosses (`rectangular_extruded_pad`).
- **Geometric Health & Recompute Verification**:
  - Updates/recomputes and checks the explicit request-owned document after construction; native `Features` collection health, body type classification (`igSolidBody`, `igSheetBody`, `igCurveBody`), positive finite volume, and `PhysicalPropertiesStatusConstants` (`sePhysicalPropertiesStatus_Model=1`) are authoritatively inspected. `ActiveDocument` is not ownership authority.
  - Inspects model topology to verify expected solid body count and enforce 3D manifold solid body classification (hard-rejecting non-manifold wire or sheet bodies).

### FR-9: Atomic Multi-Format Artifact Export & Pipeline Finalization (M3.3 — Implemented and Verified)
- **Multi-Format Export**:
  - Exports native Solid Edge part (`.par`), standard exchange STEP (`.step` / `.stp`), and mesh polygon STL (`.stl`) artifacts.
  - Best-effort preview rendering generating thumbnail preview images (`.jpg`) without invalidating otherwise successful CAD solid models upon rendering issues.
  - Native `.par` validation: file must exist, be non-empty, and pass authoritative solid body inspection (positive volume, verified feature tree).
  - STEP Part 21 validation: bounded text verification via `step_checker.py` ensuring non-empty topology (`MANIFOLD_SOLID_BREP`), ISO 10303-41 length unit scale detection, and valid spatial bounds.
  - STL validation: structure check confirming recognizable ASCII or binary header (80-byte header + uint32 triangle count) with at least one valid triangle ($\ge 1$).
  - JPG preview capture: failed image capture removes any partial or corrupt temporary JPG output (regardless of size), logs a non-fatal warning diagnostic, and does not invalidate the CAD solid result.
  - Required artifact failures: any failure to generate or validate required `.par`, STEP, or STL files is fatal to the generation request (`ARTIFACT_EXPORT_FAILED`).
- **Atomic Staging & Path Safety**:
  - Writes artifacts to isolated temporary staging paths before atomically moving them to the designated final destination within `CAD_OUTPUT_ROOT`.
  - Rejects unsafe output paths attempting directory traversal outside the configured output root (`OUTPUT_PATH_NOT_ALLOWED`).

---

### FR-10: Generation Application Orchestration (M4.1 — Implemented and Verified)
- **Application Coordinator**:
  - Provides a single, reusable Python generation coordinator (`GenerationService`) that accepts validated generation requests (`ExampleGenerationRequest`, `PromptGenerationRequest`). There is no internal direct-plan request variant; offline tests and live component checks use injected resolvers through these two production request paths.
  - Dispatches requests to injected resolver callables (`example_resolver`, `prompt_resolver`) without coupling to concrete AI SDKs or catalog files.
  - `SolidEdgeExecutor` remains the sole execution-capability authority; the application layer does not maintain a duplicate capability table or perform speculative feature-family AST filtering. Executor capability rejection maps to `failed/CAD_PLAN_REJECTED`.
  - Performs canonical parse (`feature_plan_from_dict`), validation/normalization (`validate_feature_plan`), defaulting, and lowering (`lower_validated_feature_plan_to_payload`) before CAD runtime construction; captures active gate mode (`capability_first` by default, or explicit `strict`) once and threads it identically to both preparation and executor passes.
- **Lifecycle & Safety**:
  - Manages single-request CAD runtime lifecycle: acquires runtime, connects application, creates one request-owned Part document, executes the feature plan, finalizes artifacts, and guarantees ownership-safe teardown (`force_kill_on_failure=False`).
  - Preserves borrowed and unknown CAD sessions and unrelated documents.
  - Treats request-document close failures as fatal lifecycle failures (`CAD_EXECUTION_FAILED`).
- **Response & Diagnostic Projection**:
  - Pure deterministic response builders (`projection.py`) generate schema-compliant JSON payloads for `accepted`, `rejected`, and `failed` variants against Draft 2020-12 `generation-response.schema.json`.
  - Enforces response warning parity: every status variant emits required `warnings: string[]` (empty array when none exist), preserving prior diagnostics across failures.
  - Enforces strict local path sanitization (SEC-07, CWE-209): error messages use standardized descriptions without exposing local drive letters, workstation paths, or raw exception strings.
  - Maps native inspection failures to canonical distinct code `NATIVE_QA_BLOCKED`.

### FR-11: Optional Text-Only Gemini Plan Proposal (M4.2 — Implemented and Verified)
- **Untrusted Proposal Boundary**: Integrates an optional Google Gemini API proposal adapter (`GeminiPlanResolver`) that resolves natural language text prompts into an untrusted structured proposal (`PlanProposal`). The proposal payload is bounded (maximum 1,000,000 characters), strictly validated against `feature-plan-proposal-v1.schema.json` without extra provider properties, and safely envelope-bound with canonical metadata (`plan_version`, `request_id`, `units`, sanitized `part`). The bound plan must pass mandatory local canonical parsing, validation/normalization, and lowering through the deterministic geometry pipeline before any CAD runtime acquisition or document creation.
- **Privacy & BYOK Boundary**: Operates under a strict Bring-Your-Own-Key (BYOK) model with memory/session-only constructor injection. The API key is used strictly for provider authentication over HTTPS and is never included in model content, logged, persisted, or exposed in diagnostics. The only user-derived model content is the natural-language prompt; the request also contains static system instructions and the response schema; zero local file paths, CAD geometry, system environment variables, or session tokens cross the network.
- **Model & Sampling Default**: Uses verified stable default `gemini-3.5-flash-lite` via `google-genai>=2.19.0,<3.0.0` with sampling parameters deliberately omitted per Gemini 3.x guidance and a bounded timeout (60,000 ms).
- **Bounded Invocation & No Model Fallback**: One adapter call, with at most one SDK retry for transient failures; no adapter retry loop, response repair, or model fallback. If the proposal fails parsing, validation, or CAD execution, the failure is returned deterministically.
- **Explicit Lifecycle**: The API client is initialized and scoped within caller-provided execution boundaries via context manager without global persistent connection pools or background daemons.
- **Workflow Boundary**: Completes the M4.2 plan proposal adapter component; end-to-end stdio invocation is implemented and verified via M4.5.

### FR-12: Deterministic Example Catalog (M4.3 — Implemented and Verified)
- **Deterministic Catalog Resolution**: Resolves schema-approved example IDs into canonical FeaturePlan AST payloads offline without AI providers, Gemini dependencies, API keys, network calls, or disk directory traversal.
- **Resource Encapsulation & Immutability**: Uses Python standard `importlib.resources.files("example_catalog").joinpath("resources", resource_name)` to read bundled templates (initially `spur_gear_example_plan.json`). Catalog mappings are encapsulated in private, immutable `MappingProxyType` structures (`_CATALOG_RESOURCES`) preventing runtime mutation of supported IDs.
- **Request-ID Dynamic Substitution**: Dynamically injects the caller's validated `request_id` into the resolved feature plan, replacing template identifiers while preserving all deterministic geometric dimensions and entities.
- **Schema & Enum Parity**: Public catalog surface (`available_example_ids()`, `resolve_example_plan()`) strictly mirrors schema enum declarations (`contracts/schemas/generation/generation-request.schema.json`). Unsupported IDs fail deterministically before CAD runtime acquisition.
- **Privacy & Sanitization Boundary**: Resource resolution errors and missing template faults are mapped to package-local exceptions with sanitized error strings, preventing internal filesystem paths, credentials, or environment details from leaking to callers.
- **Distribution Independence**: Verified inside standalone wheel builds (`cad_copilot-0.1.0-py3-none-any.whl`), proving package data resources resolve cleanly in isolated external environments without depending on repository checkouts or `contracts/` fixtures.

### FR-13: Canonical Run Manifest (M4.4 — Implemented and Verified)
- **Reproducible Execution Record**: Generates a canonical `run_manifest.json` recording the full provenance and execution record for every accepted generation run:
  - Explicit request ID, schema version, engine software version, and CAD runtime version diagnostics.
  - Canonical normalized FeaturePlan AST payload.
  - Stable entity IDs, plan fingerprint, and prompt fingerprint only.
  - Request provenance (`example_plan` or `ai_proposal`) with bounded, safe source identifier.
  - Applied geometric defaults and active gate mode (`capability_first` or `strict`).
  - Ordered, sanitized diagnostic and warning records.
  - CAD execution and native inspection results (body counts, volume, physical properties).
  - Portable relative artifact paths, artifact sizes, and SHA-256 hashes (strictly relative to the request directory, never exposing local absolute workstation paths).
- **Privacy Enforcement**: Manifest never contains raw user prompts, API keys, credentials, provider response payloads, workstation paths, or local host identifiers.
- **Atomic Publication Invariant**: The manifest is validated against its canonical schema inside the isolated staging directory and published atomically alongside `.par`, STEP, STL, and optional JPG artifacts in the final publication transaction.

### FR-14: Strict Generation Stdio IPC (M4.5 — Implemented and Verified)
- **Stdio Framing & Protocol**:
  - Accepts exactly one bounded UTF-8 JSON request via `stdin` conforming to `generation-request.schema.json`.
  - Enforces a 128 KiB raw payload limit (`131,072` bytes) before unbounded decoding or schema parsing, rejecting larger inputs with `rejected/PAYLOAD_TOO_LARGE`.
  - Emits exactly one newline-terminated UTF-8 JSON response to `stdout` conforming to `generation-response.schema.json`.
  - Emits non-contract runtime progress events and fatal diagnostics exclusively to `stderr` as bounded, compact JSON Lines objects (`{"type":"progress","phase":"...","message":"..."}`).
  - Emits exactly 4 ordered progress events on handled execution: `request_received`, `request_validated`, `generation_started`, and `response_ready`.
- **Strict Stdout & Stderr Isolation**:
  - Establishes early C-runtime descriptor duplication and redirection (`os.dup2`) to null devices.
  - On Windows, replaces `STD_OUTPUT_HANDLE` and `STD_ERROR_HANDLE` via `SetStdHandle` before production modules are imported.
  - Guarantees zero console contamination: Python `print`, logging, SDK messages, COM client outputs, native extension writes, direct Win32 standard handle writes, and tracebacks cannot corrupt `stdout` or `stderr`.
- **Packaging & Launch Surfaces**:
  - Distributes byte-identical request and response schema resources under `ipc/schemas/` packaged in wheel data.
  - Provides two supported launch surfaces sharing `ipc.stdio:main`:
    1. Installed console entrypoint: `cad-copilot-generate`.
    2. Windows source-run wrapper: `engine/scripts/generate.cmd` (requires documented editable installation, performs quiet distribution-metadata preflight, and never sets `PYTHONPATH`).
- **Environment Composition**:
  - Reads `CAD_OUTPUT_ROOT` from caller environment; requires an existing directory and delegates containment/collision checks to canonical artifact paths.
  - Route-aware composition: `example_plan` never reads Gemini configuration, imports provider modules, or accesses the network; `prompt_to_cad` composes `GEMINI_API_KEY` and optional `CAD_LLM_MODEL` (defaulting to `gemini-3.5-flash-lite`).
- **Process Lifecycle & Exit Policy**:
  - Operates as a stateless single-subprocess invocation per request (`one-subprocess-per-request`).
  - Directly reuses the internal `GenerationService` coordinator without intermediate server processes, daemons, background workers, or listeners.
  - Exit policy: exits `0` for handled responses (`accepted`, `rejected`, `failed`), exits `1` for fatal bootstrap, packaged-schema resource, response serialization, or response-write failure with empty stdout, and exits `130` for caller cancellation (`CTRL_BREAK_EVENT` / `SIGINT`).
  - Caller enforces default 180s timeout; on cancellation, allows 10s graceful teardown through `GenerationService.finally` and ensures child-scoped cleanup without terminating unrelated processes.

### FR-15: Shared Sequential Batch Execution Infrastructure (Milestone 5.2)
- **Batch Execution Models & Decoupled Specification**:
  - `BatchExecutionSpec`: Internal, immutable execution view decoupling wire contract schemas from execution logic; preserves canonical input and format order and enables test-only operation extensibility without hub modification.
  - `BatchItemContext`: Request-scoped, immutable dispatch context providing boundary-prepared absolute source, temporary work, and target `Path` values for a single format execution pass; strictly excludes COM handles, worker threads, and transport state.
  - `BatchItemOutcome`: Pure per-format handler outcome representing either clean single-artifact success or failure with approved error diagnostics; verifies artifact format and target path against dispatch context.
  - `BatchExecutionOutcome`: Pre-manifest terminal outcome aggregating per-file results, summary accounting, and partition disjointness across accepted, partial, failed, unprocessed, and cancelled sets.
  - `BatchProgressUpdate`: Structured lifecycle progress updates across 6 discrete phases (`batch_started`, `file_started`, `format_started`, `format_finished`, `file_finished`, `batch_finished`).
- **Pure Relative Work Allocation & Collision Detection**:
  - `allocate_batch_work`: Pure relative path mapping replacing only final file extensions with approved static suffixes (`.step`, `.stl`, `.pdf`, `.dxf`) and preserving directory nesting with zero filesystem I/O.
  - Case-Insensitive Collision Detection: Detects in-request target path collisions (e.g. `part.par` and `part.psm` both targeting `part.step`), failing preflight with sanitized `OUTPUT_TARGET_COLLISION` without exposing internal workstation roots.
- **Injected Safety Boundary Seam (M5.3 Seam)**:
  - `BatchSafetyBoundary` protocol defining exact lifecycle attachment points (`prepare`, `verify_before_open`, `verify_after_close`) for future filesystem containment and security checks; provides no unchecked production default in M5.2.
- **Lightweight Typed Handler Bindings**:
  - `BatchOperationHandler` and `BatchHandlerFactory` protocols defining single-format document execution and runtime binding.
  - `OperationBinding` & `OperationBindings`: Immutable lookup container validating exact 1-to-1 parity against `OperationRegistry` metadata; detects missing, duplicate, unknown, or non-callable bindings before runtime acquisition.
- **Shared Sequential Execution Hub (`BatchService`)**:
  - Orchestrates sequential batch execution through discrete lifecycle phases: preflight validation and fast configuration failure, pure work allocation and collision checking, safety boundary preparation seam, single runtime connection with health verification, caller-ordered per-file and per-format dispatch, error isolation with caller-selected continue policy, and observable teardown.
  - Enforces strict at-most-one-open-document invariant and mandatory document close without saving.
  - Enforces authoritative fatal lifecycle failure precedence (`DOCUMENT_CLOSE_FAILED`, `SOURCE_INTEGRITY_FAILED`, `SOLID_EDGE_UNHEALTHY`, teardown failure) over cooperative cancellation, preserving `BATCH_CANCELLED` markers while upgrading terminal status to `failed`.
  - Dispatches balanced 6-phase progress events with fail-safe observer exception isolation.

### FR-16: Batch Filesystem & Source-Integrity Safety Boundary (Milestone 5.3 — Implemented and Verified)
- **Bounded Local Root Enforcement**:
  - Requires `input_root` and `output_root` to be existing, non-root, absolute local drive-qualified Windows directory paths without UNC, device, DOS-device, or extended-length prefixes.
  - Verifies roots and parent components are normal directories and not symlinks, junctions, or reparse points.
  - Enforces stable filesystem identity capture `(st_dev, st_ino)` on roots during preparation.
- **Strict Relative Containment & Component Allowlisting**:
  - Rejects directory traversal (`..`), empty components, colons, NUL bytes, trailing dots, leading/trailing spaces, and alternate data streams (`:`).
  - Rejects Windows reserved device names (`CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`) across all relative segments regardless of extension.
  - Enforces strict canonical containment beneath the validated input root and output root; walks existing path components to forbid reparse points.
  - Detects duplicate selected source identities across different canonical relative paths and rejects aliases before CAD runtime acquisition.
- **Race-Resilient Source-Integrity Snapshotting**:
  - `SourceSnapshot`: Immutable snapshot containing filesystem identity `(st_dev, st_ino)`, regular file mode, size in bytes, nanosecond modification timestamp (`st_mtime_ns`), and lowercase SHA-256 digest.
  - Streaming SHA-256 calculation with fixed 1 MiB chunk buffers without loading complete CAD files into memory.
  - Race-resilient pre- and post-stream metadata verification detecting in-flight mutation during snapshot capture.
  - Exact double-verification timing: pre-open capture immediately before `open_document()`, and post-close capture immediately after no-save close with zero-tolerance equality enforcement.
- **Guarded Output Workspace & Windows Atomic Publication**:
  - Private per-format same-volume staging directory (`output_root/.cad-copilot-work-<token>/`) allocated without immediate filesystem creation.
  - Guarded cleanup removing verified work file and directory on failure or cancellation without broad recursive tree deletions.
  - Windows-only atomic publication (`MoveFileExW` with zero flags) guaranteeing atomic no-replace publication that fails closed without overwriting pre-existing targets.

### FR-17: Genuine Batch Format Handlers and Native Export Operations (Milestone 5.4 — Implemented and Verified)
- **Closed-Output Structure Validators (`engine/src/batch/format_validation.py`)**:
  - `validate_batch_output(format_id, path)`: Non-destructive structural validation reading outputs directly from guarded temporary staging workspaces without modifying file bytes.
  - STEP: Bounded full-text read and structural/geometry validation up to `MAX_STEP_TEXT_CHARS = 50_000_000` characters, verifying ISO 10303-21 structure (`ISO-10303-21;`, `HEADER;`, `END-SEC;`, `DATA;`, `END-SEC;`, `END-ISO-10303-21;`), solid B-Rep presence (`MANIFOLD_SOLID_BREP`), length units, and finite 3D bounding envelope.
  - STL: Auto-detecting binary (80-byte header, 4-byte triangle count, exact $84 + 50 \times N$ byte size check) and ASCII (`solid ... endsolid`) validation with strict finite float vertex coordinate parsing.
  - PDF: Magic header verifying versions 1.0 through 1.7 and 2.0 (`%PDF-(?:1\.[0-7]|2\.0)`) and terminal `%%EOF` marker.
  - DXF: Text ASCII DXF structure verification requiring paired group codes, valid named `SECTION`, balanced `ENDSEC`, and terminal `EOF`.
  - Rejects zero-byte files, truncated payloads, missing markers, non-regular files, and unknown format identifiers with `INVALID_BATCH_OUTPUT`.
- **Native Solid Edge Export Operations (`engine/src/drivers/solidedge/exporter.py`, `engine/src/drivers/solidedge/drawing_publisher.py`)**:
  - `export_model_to_path`: Executes `SaveCopyAs` for `.step` and `.stl` against Ordered part documents (`.par`), sheet metal documents (`.psm`), and assembly documents (`.asm`) without saving source documents to disk.
  - `refresh_drawing_views` & `publish_drawing_to_path`: Refreshes drawing views in-memory across working sheets using `DrawingView.Update()` without saving `.dft` documents to disk, and publishes `.pdf` and `.dxf` via `SaveCopyAs` to guarded staging paths.
  - Strict STA Worker Seam: All COM dispatch operations execute strictly on the dedicated worker thread within the STA COM apartment; raw COM objects remain worker-local, only pure values cross the seam, and tracked documents close through the runtime lifecycle after format processing.
  - Zero Workstation Path Leakage (SEC-07): Sanitizes all COM exception texts and error codes; never exposes raw internal filesystem paths or usernames in diagnostic messages.
- **Authoritative Assembly Reference Verification (`engine/src/drivers/solidedge/assembly_references.py`)**:
  - Recursive traversal of top-level and nested subassembly occurrences up to `MAX_ASSEMBLY_TRAVERSAL_DEPTH = 32`.
  - Authoritative missing-component detection via official Siemens `Occurrence.FileMissing()` method and `OccurrenceDocument` resolution.
  - Grounded component resilience: Correctly distinguishes grounded/fixed base occurrences (`Status == 2` / `seOccurrenceStatusFixed`) from missing components, ensuring valid assemblies with fixed components are resolved.
  - Fail-closed isolation: Unresolved occurrences fail closed before any export attempt, producing 0 exported artifacts, returning pure frozen `AssemblyReferenceCheckResult(is_resolved=False)` without leaking internal component names or paths, and allowing subsequent healthy batch inputs to proceed when `continue_on_error=True`.
- **Genuine Batch Handlers & Production Composition (`engine/src/batch/handlers.py`, `engine/src/batch/composition.py`)**:
  - `Export3DHandler`: Factory and handler for `export_3d` with formats `step` and `stl`; verifies 3D document types and preflights assembly references.
  - `PublishDrawingHandler`: Factory and handler for `publish_drawing` with formats `pdf` and `dxf`; verifies draft document type and triggers view refresh.
  - `build_initial_operation_bindings`: Factory wiring genuine format handlers, validation functions, and filesystem safety boundary into immutable `OperationBindings` matching the 1-to-1 schema operation registry.

### FR-18: Conditional Parasolid Export Gate (Milestone 5.5 — Implemented and Verified)
- **Parasolid Transmission Structural Validation (`engine/src/batch/format_validation.py`, `engine/src/batch/parasolid_validation.py`)**:
  - `validate_batch_output("parasolid", ...)`: Dispatches to `validate_parasolid_artifact()` for pure bounded structural validation of exported `.x_t` files directly from temporary staging workspaces before atomic publication without mutating file bytes.
  - Size Invariants: Requires regular non-reparse file with positive size $\ge 100$ bytes (`MIN_PARASOLID_BYTES = 100`).
  - Bounded Header Window: Scans at most 2,048 bytes (`PARASOLID_HEADER_SCAN_BYTES = 2048`) enforcing strict ASCII encoding, no NUL bytes, max line length of 256 bytes (`MAX_LINE_BYTES`), character set line starting with `**` containing alphabet `ABCDEFGHIJKLMNOPQRSTUVWXYZ`, symbol delimiter line starting with `**PARASOLID `, section markers `**PART1;` and `**PART2;` with `PART1` preceding `PART2`, format/guise markers `FORMAT=text;` and `GUISE=transmit;`, and schema marker matching `SCH=SCH_([A-Za-z0-9_]+);`.
  - Bounded Trailer Window: Scans at most 2,048 bytes (`PARASOLID_TRAILER_SCAN_BYTES = 2048`) enforcing non-empty ASCII text, no NUL bytes, max line length of 256 bytes, and newline record termination (`\r\n` or `\n`).
  - Fails closed with sanitized `BatchFormatValidationError(format="parasolid", phase="validation")` on binary files (`.x_b`), truncated payloads, corrupted markers, or non-ASCII characters.
- **Native Solid Edge Parasolid Export & Sidecar Suppression (`engine/src/drivers/solidedge/exporter.py`)**:
  - `export_model_to_path`: Routes format token `"parasolid"` to native `SaveCopyAs` for `.x_t` across Ordered part (`.par`), sheet metal (`.psm`), and assembly (`.asm`) documents on the dedicated STA worker thread.
  - Verified Source Safeguards: Source documents are protected by streaming SHA-256 pre-open and post-close integrity snapshots, exporting exclusively through `SaveCopyAs` to guarded staging paths, and closing without saving via `Close(False)`.
  - Zero Sidecar Pollution: Solid Edge produces no auxiliary translation log files for `.x_t` (unlike `.step` and `.stl`), leaving clean destination workspaces.
  - Zero Workstation Path Leakage (SEC-07): Sanitizes all COM exception texts and error codes; never exposes raw internal filesystem paths or usernames in diagnostic messages.
- **Canonical Contract & Registry Promotion (`engine/src/batch/registry.py`, `engine/src/batch/models.py`, `contracts/schemas/batch/`)**:
  - Promotes `parasolid` to an approved first-class format token under `export_3d` in canonical and packaged JSON schemas (`batch-request.schema.json`, `batch-response.schema.json`, `batch-manifest-v1.schema.json`) and typed domain models (`Export3DFormat = Literal["step", "stl", "parasolid"]`).
  - Cardinality Enforcement: `export_3d` accepts 1 to 3 items (`["step", "stl", "parasolid"]`), while `publish_drawing` remains 1 to 2 items (`["pdf", "dxf"]`).
  - Schema Parity: Canonical and packaged JSON schemas remain 100% byte-identical.
  - `build_initial_registry`: Exposes `output_formats=("step", "stl", "parasolid")`.

### FR-19: Batch Summary Manifest and Strict Stdio IPC (Milestone 5.6 — Implemented and Verified)
- **Summary Manifest Assembly & Artifact Containment**:
  - Assembles a canonical `BatchManifest` conforming to `batch-manifest-v1.schema.json` for progressed outcomes (`completed`, `cancelled`, and progressed `failed`).
  - Rejection and early failure paths never hash disk artifacts or attempt manifest assembly/publication.
  - Verifies that every successful exported artifact is an absolute path strictly contained beneath the validated `output_root` and is a regular, non-reparse file.
  - Manifest artifact records store strictly relative, forward-slash portable paths derived via `target_path.relative_to(validated_output_root).as_posix()` after strict containment verification, preserving input directory mirroring.
  - Captures fresh, post-execution disk snapshots (`OutputSnapshot`) calculating positive byte sizes and streaming SHA-256 digests with 1 MiB chunk buffers without loading complete files into memory.
  - Preserves caller request file ordering and requested format ordering.
  - Privacy boundary: manifest contains zero user prompts, API keys, CAD geometries, environment dumps, usernames, or absolute workstation paths.
- **Engine Version Resolution**:
  - Process composition resolves installed `cad-copilot` distribution metadata before CAD acquisition and injects the resulting non-empty version string into manifest assembly.
  - If distribution metadata is unavailable after request validation, returns a sanitized early `failed / INTERNAL_ERROR` response without acquiring Solid Edge, with no `0.0.0` fallback, no source-tree TOML fallback, and no workstation paths in diagnostics.
- **Atomic No-Replace Manifest Publication**:
  - Published destination: `<output_root>/<request_id>.batch_manifest.json`.
  - Stages serialization through an exclusive, randomly named temporary file directly under the validated `output_root`, guaranteeing same-volume atomic rename.
  - Serializes deterministic, ASCII-safe UTF-8 JSON (`ensure_ascii=True`, `allow_nan=False`, stable key order, 10 MiB safety cap) ending with a single LF (`\n`).
  - Performs strict read-back verification against `batch-manifest-v1.schema.json` and parsed data equality validation before attempting rename.
  - Re-verifies output root identity, target containment, target absence, and temporary-file identity immediately before atomic rename.
  - Atomically renames staging file using Windows no-replace semantics (`MoveFileExW` without replace flag), failing closed if a collision sentinel or prior file exists without modifying or overwriting pre-existing targets.
  - Captures a post-publication snapshot and proves exact byte and filesystem identity expectations.
  - Exact cleanup: cleans only the request-private staging file on publication failure without broad or recursive directory deletion; never deletes or modifies pre-existing files or sentinels.
- **Outcome Finalization & Publication Failure Precedence**:
  - Finalizes `BatchExecutionOutcome` into canonical `BatchResponse` containing `manifest: { "path": "<target_path.as_posix()>" }` for `completed` and `cancelled` outcomes (and progressed `failed` outcomes where manifest publication succeeded).
  - Manifest path references in `BatchResponse` are absolute local Windows paths formatted with forward slashes (`target_path.as_posix()`), matching canonical response fixtures.
  - Publication failure precedence: if manifest assembly, artifact hashing, serialization, read-back, collision check, rename, or post-publication verification fails, the terminal response transitions to progressed `failed` with `manifest=None`, `cancelled_files=()`, `summary.cancelled=0`, and untouched inputs moved to `unprocessed_files`.
  - For previously completed or cancelled outcomes, the top-level error diagnostic is `MANIFEST_PUBLICATION_FAILED`; for an already-progressed `failed` outcome, existing fatal errors are retained and `MANIFEST_PUBLICATION_FAILED` is appended exactly once.
- **Strict Stdio IPC Framing & Descriptor Isolation**:
  - Process entrypoint: `ipc.batch_stdio:main`.
  - Accepts exactly one bounded UTF-8 JSON request via `stdin` conforming to `batch-request.schema.json` with a 128 KiB limit (`131,072` bytes), rejecting oversize input with `rejected/PAYLOAD_TOO_LARGE`.
  - Strict JSON decoding rejecting duplicate object keys, non-finite constants (`NaN`, `Infinity`), BOM, and trailing tokens.
  - Emits exactly one schema-valid compact 7-bit ASCII JSON response followed by LF on preserved `stdout`.
  - Emits non-contract real-time progress events and fatal diagnostics exclusively on `stderr` as compact single-line JSONL objects.
  - Establishes early C-runtime descriptor duplication and redirection to null, redirecting Windows `STD_OUTPUT_HANDLE` and `STD_ERROR_HANDLE` before production modules are imported to guarantee zero stdout/stderr contamination from C-runtime, COM, or print statements.
- **Progress Protocol & Console Signal Lifecycle**:
  - Progress JSONL contract: `{"type":"progress","request_id":"...","phase":"...","total_files":...,"completed_files":...}` supporting 6 canonical phases (`batch_started`, `file_started`, `format_started`, `format_finished`, `file_finished`, `batch_finished`) and optional phase-governed fields (`current_file`, `current_format`, `file_status`).
  - Each progress event line is strictly capped at 4 KiB (`4,096` bytes) and terminated with LF.
  - Progress observation is strictly best-effort: broken or closed stderr pipes must never disrupt batch execution, corrupt accounting, alter COM cleanup, or prevent the final stdout response.
  - Fatal diagnostic envelope: `{"type":"diagnostic","phase":"fatal","message":"Batch process failed before a contract response could be produced."}` on `stderr` when a contract response cannot be produced.
  - Cooperative signal handler (`SIGINT` and Windows `SIGBREAK` / `CTRL_BREAK_EVENT`) is installed strictly after request validation and before composition starts, and restored in `finally`.
  - Handler only sets an async-safe cancellation flag (`threading.Event`); it performs zero I/O, no COM calls, no document closes, and no process termination. `SIGTERM` is not intercepted, and no in-engine force-kill is performed.
  - Exit codes: `0` for handled contract responses (`completed`, `cancelled`, `rejected`, early `failed`, progressed `failed`), `1` for fatal process bootstrap/serialization failures (empty stdout), `130` for interruptions outside the cooperative scope.
- **Launchers & Python Wheel Packaging**:
  - Console entrypoint: `[project.scripts] cad-copilot-batch = "ipc.batch_stdio:main"`.
  - Windows source wrapper: `engine/scripts/batch.cmd` with `%~dp0`-relative interpreter resolution, quiet import and distribution-metadata preflight, and fatal diagnostic on failure.
  - Package data: `batch/schemas/*.json` verified via isolated wheel installations outside repository checkouts.
  - Route isolation: zero AI provider imports, zero API keys, zero network access, and zero CAD Copilot authentication, entitlement, or licensing subsystem, while preserving caller-installed and appropriately licensed Siemens Solid Edge as the mandatory live-execution prerequisite.

### FR-20: Clean Desktop Foundation (M6.1 — Complete)
- **Dedicated Desktop Workspace (`@cad-copilot/desktop`)**:
  - Establishes the real `desktop/` pnpm workspace declared in `pnpm-workspace.yaml`.
  - Single root `pnpm-lock.yaml` remains the exclusive JavaScript dependency lockfile.
  - Package scripts provide unified `"dev": "vite"` serving both root `pnpm dev:desktop` and Tauri's `beforeDevCommand`, one-shot `test`, `lint` (`eslint . --max-warnings=0`), `typecheck` (`tsc -b --noEmit` or single-project equivalent), `format` (`prettier --write .`), `format:check` (`prettier --check .`), and `build` running the complete typecheck boundary before `vite build` (`pnpm typecheck && vite build`).
  - Tailwind CSS v4 configured via `@tailwindcss/vite` without legacy config files; zero Tailwind runtime scripts in production bundle.
  - ESLint 9 ESM flat configuration (`eslint.config.js`) using `@eslint/js`, `typescript-eslint`, and stable `eslint-plugin-react-hooks` presets.
- **Showcase Application Shell & Presentation**:
  - React 19 application bootstrapped with `createRoot` and `StrictMode`.
  - Exactly two primary navigation choices: `Generate` (default) and `Batch`, managed via local state without heavy routing frameworks.
  - Page roots render clear semantic headings and honest foundation status; no non-functional operational controls, fake loading states, or simulated execution.
  - Compact settings/diagnostics panel with keyboard navigation (Escape, focus trap/return) displaying exactly 5 typed foundation rows: Output directory (`Not selected`), Solid Edge (`Not checked`), Gemini (`Not configured`), model (`Not loaded` or default), and engine version (`Not connected`). Zero input fields, zero secret storage.
  - Accessible design tokens, semantic landmarks, high contrast, and `prefers-reduced-motion` compliance. Tree-shaken `lucide-react` icons with accessible labels.
  - Zero Firebase, sign-in, cloud URLs, telemetry, licensing/subscription UI, 3D canvas, image upload, revision timeline, or editing controls.
- **Native Tauri Host & Security Baseline**:
  - Minimal Tauri v2 Windows host with distinct Cargo target names: binary `cad-copilot-desktop` and library `cad_copilot_desktop_lib` to prevent Windows output-collision warnings (Cargo issue #8519).
  - `#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]` in `main.rs` suppresses extraneous Windows consoles in release builds while preserving diagnostics in debug builds.
  - Repository-owned master brand asset (`desktop/src/assets/logo.png`) and valid multi-layer Windows icon (`desktop/src-tauri/icons/icon.ico` with 16, 24, 32, 48, 64, and 256 pixel layers) committed in the scaffold.
  - Capability configuration in `capabilities/default.json` grants only the minimum core permissions for the single main window; zero shell/process, opener, dialog, filesystem, store, clipboard, HTTP, or notification permissions.
  - Content Security Policy (CSP): Strict production CSP permits only local bundled assets and required Tauri IPC sources; separate development CSP (`devCsp`) permits only the local Vite origin and same-port WebSocket HMR endpoint (`localhost:1420`).
  - Fixed Vite port `1420` with `strictPort: true` matching `tauri.conf.json.build.devUrl`.
  - `bundle.active` remains disabled; source-run and build verification only. Zero custom `#[tauri::command]` handlers or optional Tauri plugins in M6.1.
- **Top-Level Error Boundary & Non-Disclosure**:
  - Top-level React error boundary wraps the application tree below `StrictMode` and above page composition.
  - Catches render/lifecycle errors and presents an accessible `FatalErrorView` with a generic user-friendly message and a `Reload application` recovery action.
  - Strict non-disclosure: completely suppresses exception messages, stack traces, component stacks, filesystem paths, and environment variables from rendered DOM.
  - Avoids recording error details or secrets in browser storage or frontend history.
- **Explicit Functional Exclusions (M6.1)**:
  - FR-20 does not own engine invocation, Python subprocess spawning, stdio IPC framing, contract bindings, filesystem dialogs, safe preview/reveal, progress translation, request cancellation, full Generate behavior, full Batch behavior, CI creation, installer packaging, release automation, or public demo publication.

### FR-21: Generate Vertical Slice (Milestone 6.2 — Implemented and Verified)
- **Desktop Stdio Transport & Child Supervision**:
  - Deterministic repository and Python virtual environment resolution (`resolve_source_layout`) locating `.venv/Scripts/python.exe`, `engine/pyproject.toml`, and `engine/scripts/generate.cmd` from executable and manifest ancestry.
  - Constant prerequisite preflight probe executing `python.exe -c` to verify installed `cad-copilot` distribution version with 5-second timeout.
  - Console isolation: spawns engine with `CREATE_NEW_CONSOLE` and `SW_HIDE` on release GUI hosts to prevent flashing console windows, and `CREATE_NEW_PROCESS_GROUP` on console-attached debug hosts.
  - Targeted Windows cancellation: scoped `CTRL_BREAK_EVENT` delivery to the tracked child console with 180-second caller deadline and 10-second graceful teardown, verified to unwind targeted children without signal bleed to unrelated processes.
  - Single-run active guard: native application mutex enforces at most one running generation process at any time, rejecting concurrent or duplicate start requests.
  - Stream framing: bounded line-buffered reads on stdout (1 MiB cap) and stderr (1 KiB per line, 1 MiB aggregate cap), parsing 4 curated JSONL progress phases (`request_received`, `request_validated`, `generation_started`, `response_ready`).
- **Secret Handling & BYOK Security**:
  - In-memory session key handling: Gemini API key stored strictly in native process memory during the application session; zero persistence to disk, `localStorage`, `sessionStorage`, or IndexedDB.
  - Form input masking: masked password field with `autoComplete="off"` and `spellCheck={false}`; draft key buffer wiped on navigation, mode switch, or editor collapse.
  - Child environment isolation: `GEMINI_API_KEY` injected exclusively into prompt child process environment; stripped from example runs. Never exposed on command line, in diagnostics, error envelopes, logs, or native events.
- **Output Authority & Sidecar Validation**:
  - Native directory picker (`rfd`) locks the chosen output root; path containment and NTFS filesystem identity (`dwVolumeSerialNumber`, 64-bit `nFileIndex`) captured at run reservation.
  - `ValidatedOutputBinding` holds directory handles open without `FILE_SHARE_DELETE` across file reads, physically preventing directory rename/replacement during access.
  - Sidecar manifest verification: embedded canonical Draft 2020-12 schema validation (`run-manifest-v1.schema.json`) via `jsonschema` (with network features disabled); enforces exact 1-to-1 inventory equality with wire response artifacts.
  - Open file handle inspection: `verify_open_file_handle` checks open handles directly via Win32 `GetFileInformationByHandle` and `GetFinalPathNameByHandleW`, rejecting hardlinks (`> 1`), reparse points, volume mismatches, and out-of-run path escapes.
  - Bounded JPEG preview authority: preflights JPEG headers and dimensions (<= 8,192 px per axis, <= 32M pixels, <= 20 MB) with SHA-256 digest matching before streaming binary `Response` to frontend; browser decode failure triggers `<img onError>` fallback and revokes the Blob URL.
  - Safe Explorer reveal: validates canonical run directory containment before launching `explorer.exe` with separated arguments; zero shell interpolation.
- **Generate UI & Accessibility**:
  - Two explicit modes: `Example` (deterministic `spur_gear`, key-free, network-free) and `Prompt` (character-counted, inline expandable key editor).
  - Truthful progress and verification feedback: real-time phase updates with distinct `Checking` state during result verification, preventing premature "unavailable" messages.
  - Result view displays verified artifact cards with file formats, byte sizes, and contained "Show in folder" action.
  - Modal focus trapping, Escape dismissal, and focus restoration for close confirmation dialogs.

### FR-22: Batch Operations Vertical Slice (Milestone 6.3 — Delivered & Verified)
- **Native Source Authority & Selection**:
  - Native file and folder selection dialogs via `rfd` mediated entirely by the native Tauri layer.
  - File picker selection enforces single-parent root containment for all chosen files.
  - Folder picker performs bounded recursive scan beneath chosen root (capped at 20,000 entries, 500 supported files); exceeding limits fails closed with curated diagnostic rather than returning partial data.
  - Case-insensitive recognition of native Solid Edge formats: `.par`, `.psm`, `.asm`, and `.dft`.
  - Reparse points, symlinks, junctions, UNC shares, device paths, and alternate data streams (`:`) are detected and rejected/skipped with visible disclosure without following external links.
  - Discovered supported files (up to 500 canonical relative paths) and kind counts returned once under an opaque `selectionId`; pre-run list is display/search-only in React, while Rust retains authoritative source inventory and filesystem identity.
- **Operation Routing & Format Compatibility**:
  - Fixed operation family routing: `export_3d` accepts `.par`, `.psm`, and `.asm` inputs with STEP, STL, and Parasolid (`.x_t`) output formats (1–3 formats); `publish_drawing` accepts `.dft` inputs with PDF and text DXF output formats (1–2 formats).
  - Incompatible source kinds for the selected operation are excluded from request submission and reported clearly in the UI; mixed selections require explicit operation choice without silent splitting.
  - Default 100-file cap enforced unless explicitly raised by user (hard ceiling of 500 files); requests exceeding 500 files are rejected before child spawn.
- **Output Authority & Collision Policy**:
  - Output directory selected via native dialog returning opaque `selectionId` and display path; frontend cannot pass arbitrary filesystem paths.
  - Output directory identity bound natively and verified before launch and reveal.
  - Source-relative directory structure mirrored under output root; batch summary manifest published directly under output root.
  - Fixed no-replace collision policy (`TARGET_ALREADY_EXISTS` / `fail_if_exists`); permanent UI assurances confirm source files are never modified and existing outputs are never overwritten.
  - `continue_on_error` option exposed (default enabled); stops on first failure when disabled while preserving previously exported artifacts.
- **App-Wide Concurrency & Supervised Transport**:
  - Single app-wide `run_claim` coordinator enforces mutual exclusion between Generate and Batch; concurrent or cross-slice run starts are rejected with `RUN_ACTIVE`.
  - One owned Python subprocess per batch request launching direct `ipc.batch_stdio` under trusted source-run interpreter (`.venv\Scripts\python.exe`); zero secondary preflight subprocesses.
  - Bounded stdio communication: 128 KiB stdin request envelope, 10 MiB stdout response cap, 4 KiB per line and 20 MiB aggregate stderr cap.
  - Single Solid Edge connection per batch holding explicit document handles; sequential document processing with no-save close.
- **Curated Progress & Cooperative Signal Cancellation**:
  - Stderr stream parsed as compact JSONL adhering strictly to the 6 canonical phases: `batch_started`, `file_started`, `format_started`, `format_finished`, `file_finished`, and `batch_finished`.
  - Windows targeted cancellation delivers `CTRL_BREAK_EVENT` to the owned child process group (shared console) or hidden child console.
  - Cooperative cancellation contract: engine catches `SIGBREAK` inside `_cooperative_cancellation_scope`, completes in-flight file teardown, outputs schema-valid `BatchResponse` with `status="cancelled"` and terminal accounting, and exits with code **0**.
  - Exit code 130 handled as pre-handler / unhooked interruption with incomplete cleanup reporting.
  - Reaping and teardown never terminates `Edge.exe`, borrowed CAD sessions, or unrelated host processes.
- **Terminal Accounting, Manifest Verification & Reveal**:
  - Authoritative terminal response validation across Succeeded (`accepted`), Partial (`partial`), Failed (`failed`), Cancelled, and Unprocessed categories matching engine response.
  - Manifest derivation (`<output_root>/<request_id>.batch_manifest.json`) bounded to 10 MiB; read-back validation against canonical `batch-manifest-v1.schema.json` and semantic agreement with stdout response.
  - Clear UI distinction: "Manifest validated" vs "Manifest unavailable" (retaining valid engine response accounting).
  - Explorer reveal strictly limited to bound output root with no shell interpolation.
- **Scoped Diagnostics & Operation-Aware Window Close**:
  - Window close requests dispatched by active run claim: Batch owner sets Batch `closeRequested` and emits `batch-state`; Generate owner routes to Generate resolver.
  - Accessible confirmation dialog with operation-specific copy ("Batch Operation in Progress" vs "Generation in Progress") and Keep running / Cancel and close actions.
  - Diagnostics output path scoped to active tab: `currentView === 'batch' ? batch.snapshot.output?.displayPath : generation.snapshot.output?.displayPath` with "Not selected" fallback and zero cross-tab pollution.
  - Zero leakage of API keys, CAD file contents, or raw stderr stack traces. Local filesystem paths are restricted strictly to user-selected source/output roots and selected files displayed for in-app transparency (in Batch cards, state snapshots, active-tab Diagnostics, and local selection validation error banners) and are never exposed in external telemetry, wire diagnostics, or published manifest sidecars.

---

## 3. Non-Functional Requirements (NFRs)

| ID | Requirement | Specification & Boundary |
| :--- | :--- | :--- |
| **NFR-1** | **Zero I/O Purity** | `engine/src/geometry/` must be 100% pure computational logic with zero disk I/O, zero network calls, and zero CAD runtime imports. |
| **NFR-2** | **Mathematical Determinism** | Geometric transformations and profile generators must produce identical floating-point output across runs within a $10^{-4}\text{ mm}$ ($0.1\ \mu\text{m}$) tolerance. |
| **NFR-3** | **Clean-Room Compliance** | Zero proprietary Siemens binary files, DLLs, typelibs, or copyrighted headers. Automation relies solely on open interfaces and standard public Dispatch. |
| **NFR-4** | **Strict Static Typing** | Code across all verified engine packages (`interfaces`, `geometry`, `drivers`, `application`, `manifests`, `example_catalog`, `plan_providers`, `ipc`, `batch`) must pass `mypy --strict` with zero type errors and pass Ruff lint checks. |
| **NFR-5** | **COM Dispatch Isolation** | CAD automation is strictly confined to `pywin32` standard COM Dispatch (`win32com.client.Dispatch("SolidEdge.Application")`) without native Siemens binary SDKs or proprietary header bindings. |
| **NFR-6** | **Ownership-Safe Lifecycle & Cleanup** | Close only request-owned documents without saving. Attempt graceful owned shutdown; force cleanup requires the explicit flag and reverified recorded owned PID after bounded failure. Never quit/kill borrowed or unknown applications, change their application settings, or close unrelated documents. |

---

## 4. Acceptance Criteria & Verification Matrix

### Milestone 1 Acceptance Criteria (Verified Baseline)
1. Root monorepo configuration (`pnpm-workspace.yaml`, `package.json`, `pyproject.toml`) is valid and discoverable.
2. `LICENSE` contains the standard MIT license and the Siemens trademark disclaimer.
3. All JSON Schema contracts in `contracts/schemas/` pass structural meta-validation.
4. `engine/src/interfaces/` exposes `CADRuntimeABC`, `CADExecutorABC`, and the exception hierarchy with complete type annotations.

### Milestone 2 Domain Baseline and Verification Limits
1. `FaceContext` accurately transforms 2D sketch points to 3D global coordinates across supported canonical faces with inverse projection error $< 10^{-6}\text{ mm}$ and exact 6-face origin offsets.
2. `gear_math.py` generates deterministic conceptual outlines with 5 unique cyclic vertices per tooth (no zero-length edges) and separate $+Z$ bore frames with face-wire projection ensuring centered bores across translations.
3. Fit validators produce descriptive diagnostics; capability-first mode can allow warnings, whereas strict mode rejects them.
4. Universal polygon profile sanity enforces $3 \le N \le 512$, finite coordinates, non-duplicate vertices (including closing edge $N-1 \to 0$), and non-zero Shoelace area $> 10^{-6}\text{ mm}^2$.
5. Composition validator enforces unified global identifier namespace and reserved system prefix protection.
6. `step_checker.py` performs in-place $O(1)$-memory streaming bounding box and entity smoke validation with ISO 10303-41 unit scale detection.
7. Historical M2 completion evidence recorded 365/365 passing tests. The current reported M3.1 and M3.2 results are stated in the evidence boundary above.

### Milestone 3 Acceptance Criteria (M3.1, M3.2 & M3.3 Implemented and Verified)

These are milestone-wide acceptance gates. M3.1 lifecycle, M3.2 geometric primitive execution, and M3.3 multi-format artifact finalization are fully implemented, hardened, and verified.

#### A. Automated Test Suite Verification (Offline & Fake COM)
1. Offline non-COM checks (`pytest -c engine/pytest.ini -m "not com"`) pass with 838 tests passed (2 skipped, 31 COM tests deselected).
2. Driver lifecycle unit tests with mock/fake COM dispatch verify state transitions, explicit document handle tracking, bounded busy-call retries, and PID ownership classification logic.
3. Artifact staging, validation, and pipeline unit tests verify atomic move semantics, `.par`/STEP/STL format verification, Draft 2020-12 wire projections, and non-fatal JPG warning handling.

#### B. Mandatory Live Solid Edge COM Verification Gates (Windows Workstation)
1. **Live Runtime Connection & Lifecycle Gate**:
   - `SolidEdgeRuntime` connects to an active running Solid Edge instance (`borrowed` mode) or successfully launches a visible owned instance (`owned` mode).
   - Reports accurate diagnostic metadata (version string, PID, mode, health status).
   - Gracefully closes test part documents and tears down owned processes without leaving orphaned `Edge.exe` processes or affecting borrowed sessions.
2. **Live Geometric Execution Gate**:
   - `SolidEdgeExecutor` successfully builds confirmed base bodies (`rectangular_prism`, `cylinder`, `spur_gear`) and executes localized planar features (`circular_through_hole`, `rectangular_through_cutout`, `slot_through_cutout`, `rectangular_extruded_pad`).
   - Verifies model health through supported update/recompute and inspection calls on the explicit request-owned document; confirms the expected solid body count and rejects sheet/wire/empty or unexpected-body results.
3. **Live Artifact Export & Validation Gate**:
   - Export pipeline atomically writes and validates native `.par`, STEP, and STL files to the designated job directory in `CAD_OUTPUT_ROOT`.
   - Exported STEP files pass `step_checker.py` validation.
   - Exported STL files pass triangle count and structural validation.
   - Preview JPG rendering captures valid image or cleanly removes partial output while emitting a warning without failing CAD solid output.
   - Unsafe destination paths attempting directory traversal outside `CAD_OUTPUT_ROOT` are safely rejected with `OUTPUT_PATH_NOT_ALLOWED`.
4. **Live Induced Failure & Cleanup Gates**:
   - *Induced Construction Failure*: Injecting a controlled Solid Edge construction failure after validation succeeds and a request-owned document has been opened verifies that the active request-owned document is cleanly closed without saving, no corrupt target artifacts are finalized in `CAD_OUTPUT_ROOT`, and the session remains healthy for subsequent requests.
   - *Induced Export Failure*: Simulating an export failure (e.g. read-only target path or artificial export fault) verifies that temporary staging files are removed, no incomplete artifacts remain in target destination paths, and an accurate `ARTIFACT_EXPORT_FAILED` exception is raised and publication is blocked.

### Milestone 4.1 Component Acceptance Criteria (Implemented and Verified)

This section defines acceptance criteria specifically for the Milestone 4.1 application orchestration component. Component acceptance establishes that `GenerationService` successfully coordinates preparation, execution, finalization, and response projection across subsequent M4 components:

1. **Generation Application Orchestration (FR-10)**:
   - `GenerationService` in `engine/src/application/` coordinates request dispatch, canonical preparation, CAD execution, artifact finalization, and response projection.
   - Decomposed into modular, single-responsibility components (`models.py`, `projection.py`, `service.py`, `__init__.py`).
   - Pure response projection (`projection.py`) produces schema-compliant JSON payloads for `accepted`, `rejected`, and `failed` variants with zero CAD driver, COM, or filesystem I/O imports. Schema conformance is verified strictly within automated test suites; production projection builders must not load schema files or execute runtime JSON Schema validation.
   - Preserves warning parity (`warnings: string[]`) across all response variants.
   - Full offline verification passes with fake runtime, fake executor, and pure projection unit tests (80 tests across `test_projection.py` and `test_service.py`).
   - Focused live execution cases in `engine/tests/drivers/test_solidedge_live.py` (`test_m41_live_01_prompt_to_cad_block_orchestration` and `test_m41_live_02_example_plan_spur_gear_orchestration`) pass for rectangular block and conceptual spur gear generation on licensed Solid Edge using injected resolvers.

### Milestone 4.2 Component Acceptance Criteria (Implemented and Verified)

This section defines acceptance criteria specifically for the Milestone 4.2 optional text-only Gemini plan proposal adapter component:

1. **Untrusted Proposal Schema & Envelope Binding (FR-11)**:
   - Sealed Draft 2020-12 schema resource `feature-plan-proposal-v1.schema.json` loaded offline from package resources.
   - Decodes untrusted model output as strict JSON, validates against the proposal schema, rejects extraneous keys, and binds into canonical local envelope (`plan_version`, `request_id`, `units`, sanitized `part`).
   - Proposal must pass canonical parsing, geometric validation, and lowering before any CAD runtime acquisition or document creation.

2. **BYOK Privacy & Credential Protection (FR-11)**:
   - Memory/session-only constructor injection (`GeminiPlanResolver(api_key=...)`).
   - API key travels strictly over HTTPS for provider authentication; never logged, persisted, or exposed in diagnostics.
   - Request sends only the user-derived prompt text, static system instructions, and response schema; zero local file paths, CAD geometry, environment variables, or session tokens cross the network.

3. **Model Configuration & Bounded Lifecycle (FR-11)**:
   - Uses verified default `gemini-3.5-flash-lite` via `google-genai>=2.19.0,<3.0.0` with sampling parameters omitted per Gemini 3.x guidance and a 60-second bounded timeout.
   - One adapter invocation with at most one transient SDK retry; no model fallback, response repair, or heuristic retry loop.
   - Injected client lifecycle scoped to invocation without persistent connection pools or daemons.

4. **Automated & Live Verification Baseline**:
   - Offline test suite: 130 dedicated plan provider tests across schema, decoding, lifecycle, failure status, and configuration.
   - Live integration evidence: `test_live_gemini_proposal_resolution` passed in 3.87s, verifying live `gemini-3.5-flash-lite` resolution, 50x40x10 mm block lowering, clean streams, and key cleanup.

### Milestone 4.3 Component Acceptance Criteria (Implemented and Verified)

This section defines acceptance criteria specifically for the Milestone 4.3 deterministic example catalog loader component:

1. **Catalog Loader & Schema Parity (FR-12)**:
   - Package `engine/src/example_catalog/` exposes a minimal, explicit public surface (`available_example_ids()`, `resolve_example_plan()`).
   - Supported example ID set strictly matches Draft 2020-12 request schema enum parity (`contracts/schemas/generation/generation-request.schema.json`: strictly `{"spur_gear"}`).
   - Loads canonical FeaturePlan AST offline from packaged template `resources/spur_gear_example_plan.json` using `importlib.resources.files("example_catalog").joinpath("resources", resource_name)`.
   - Unknown or malformed example IDs fail deterministically before CAD runtime acquisition (`ExampleCatalogError`), returning `rejected / UNSUPPORTED_REQUEST` and sanitizing internal paths and inputs.

2. **Offline Resource Loading & Immutability (FR-12)**:
   - Resource mapping encapsulated via `MappingProxyType` (`_CATALOG_RESOURCES`) ensuring catalog entries cannot be modified or extended at runtime.
   - Zero network calls, zero LLM dependencies, zero API keys, and zero unverified filesystem path access.
   - Dynamic `request_id` substitution replaces template IDs with the caller's validated request identifier, ensuring complete provenance traceability.

3. **Application Service Integration (FR-10, FR-12)**:
   - `GenerationService(example_resolver=resolve_example_plan)` accepts `ExampleGenerationRequest(example_id="spur_gear")` and orchestrates canonical preparation, Solid Edge CAD execution, multi-format artifact export, and sidecar manifest publication.
   - Rejects unknown catalog IDs pre-runtime with `rejected / UNSUPPORTED_REQUEST` status and canonical warning preservation.

4. **Distribution Verification (FR-12)**:
   - Python wheel build (`cad_copilot-0.1.0-py3-none-any.whl`) contains `example_catalog/resources/spur_gear_example_plan.json` under package data.
   - Standalone installation in clean environments outside the repository successfully imports and resolves example plans without depending on `contracts/` or working tree files.

5. **Automated & Live Verification Baseline**:
   - Offline test suite: 25 dedicated catalog tests in `tests/example_catalog/test_catalog.py`, plus 2 integration tests in `tests/application/test_service.py` (totaling 1,136 passing non-COM tests).
   - Strict mypy: 0 issues across 71 source and test files in verified component scope (`mypy --config-file mypy.ini src tests/manifests tests/artifacts tests/application tests/example_catalog tests/drivers/test_solidedge_live.py`).
   - Ruff linting and formatting: clean across engine codebase (`ruff check src tests` and `ruff format --check src tests`).
   - Live integration evidence on Siemens Solid Edge 2026 (version `226.00.00.106`): `test_m43_live_example_catalog_spur_gear` passed in 11.95s, verifying full 3D Ordered geometry creation, positive-volume solid model inspection, atomic multi-format export (`.par`, `.step`, `.stl`, `.jpg`), and schema-valid `run_manifest.json` sidecar publication with `provenance.kind="example_plan"` and `source_id="spur_gear"`.

### Milestone 4.4 Component Acceptance Criteria (Implemented and Verified)

This section defines acceptance criteria specifically for the Milestone 4.4 canonical run manifest and sidecar publication component:

1. **Canonical Schema & Codec (FR-13)**:
   - Draft 2020-12 schema resource `run-manifest-v1.schema.json` loaded offline from package resources.
   - Deterministic canonical JSON serialization (`fingerprints.py`) enforcing canonical key sorting, signed zero normalization (`-0.0 -> 0.0`), explicit float typing, compact separators, and Zero-I/O purity.
   - Comprehensive codec (`plan_codec.py`) faithfully serializes FeaturePlan AST and pre-validates against schema before CAD runtime acquisition.

2. **Artifact Transaction & Publication Integrity (FR-13)**:
   - Staging manifest writer writes `run_manifest.json` with exclusive creation (`"xb"`), reads back from disk, and schema-validates before staging close.
   - Pre-publication inventory check validates `run_manifest.json` along with required model files (`.par`, `.step`, `.stl`) using lightweight filesystem stability snapshots (`FileSnapshot`).
   - Publication atomicity guarantees all model files and `run_manifest.json` publish together or staging is rolled back.
   - Wire contract projection remains unchanged: public response returns strictly 3 or 4 model artifact records, while disk publication includes the manifest sidecar.

3. **Application Service Integration (FR-10, FR-13)**:
   - `GenerationService` validates and prepares manifest data before CAD runtime acquisition ("Zero COM in Preparation").
   - Safely extracts `cad_runtime_version_build` from runtime diagnostics and guards against workstation path or credential leakage.
   - Retains all accumulated warnings through manifest failure or success; maps manifest configuration errors to `ARTIFACT_EXPORT_FAILED`.

4. **Automated & Live Verification Baseline**:
   - Full offline suite: 1,136 tests passed, 2 skipped, 36 COM tests deselected across all domain packages.
   - Strict mypy: 0 issues across 71 source and test files in verified component scope (`mypy --config-file mypy.ini src tests/manifests tests/artifacts tests/application tests/example_catalog tests/drivers/test_solidedge_live.py`).
   - Ruff linting and formatting: clean across engine codebase (`ruff check src tests` and `ruff format --check src tests`).
   - Live integration evidence on Siemens Solid Edge 2026 (version `226.00.00.106`): 5 passed live tests (`test_m41_live_01`, `test_m41_live_02`, `test_m44_live_01`, `test_m44_live_02`, `test_m43_live_example_catalog_spur_gear`) verifying end-to-end prompt and example generation with schema-valid `run_manifest.json` sidecar publication.

### Milestone 4.5 Component Acceptance Criteria (Implemented and Verified)

This section defines acceptance criteria specifically for the Milestone 4.5 strict generation stdio IPC interface component:

1. **Input Bounding, Strict Decoding & Schema Parity (FR-14)**:
   - Enforces a 128 KiB raw payload limit (`131,072` bytes) before decoding or schema traversal, returning `rejected/PAYLOAD_TOO_LARGE` on overlength input.
   - Rejects duplicate keys, non-finite numbers, malformed JSON, and invalid UTF-8 with sanitized `rejected/INVALID_SCHEMA`.
   - Distributes byte-identical Draft 2020-12 request/response schemas under `ipc/schemas/` packaged in wheel data, verifying installed loading without repository checkouts.
   - Constructs typed `ExampleGenerationRequest` or `PromptGenerationRequest` and recovers safe request IDs.

2. **Early Descriptor & Handle Redirection (FR-14)**:
   - Early C-runtime descriptor duplication and redirection (`os.dup2`) to null devices before production imports.
   - Early replacement of Windows `STD_OUTPUT_HANDLE` and `STD_ERROR_HANDLE` via `SetStdHandle` before production modules are imported.
   - Complete console purity: stdout contains exactly one newline-terminated UTF-8 JSON response; stderr contains only compact JSONL progress events and diagnostics; zero contamination from `print`, logging, SDK messages, COM clients, or tracebacks.

3. **Stderr Progress Protocol & Exit Status (FR-14)**:
   - Emits exactly 4 ordered progress events on handled execution: `request_received`, `request_validated`, `generation_started`, and `response_ready`.
   - Handled `accepted`, `rejected`, and `failed` responses exit with code `0`.
   - Fatal bootstrap, packaged-schema resource, response serialization, or response-write failure emits no invalid stdout and exits `1`.
   - Caller cancellation (`CTRL_BREAK_EVENT` / `SIGINT`) allows 10s graceful teardown through `GenerationService.finally` and exits `130`.

4. **Packaging & Launch Surfaces (FR-14)**:
   - Console entrypoint `cad-copilot-generate` mapped to `ipc.stdio:main`.
   - Windows source wrapper `engine\scripts\generate.cmd` with editable install preflight, no `PYTHONPATH` dependency, and fatal diagnostic on missing installation.

5. **Automated & Live Verification Baseline**:
   - Offline test suite: 114 dedicated IPC tests passed (contracts, composition, descriptors, launcher, orchestration, progress, subprocess).
   - Strict mypy: 0 issues across 99 source and test files in verified scope (`mypy --config-file mypy.ini src tests/manifests tests/artifacts tests/application tests/example_catalog tests/plan_providers tests/ipc tests/drivers/test_solidedge_live.py`).
   - Live CLI verification on Siemens Solid Edge 2026 (version `226.00.00.106`): 3 passed live CLI gates (`test_m45_live_01` spur gear via `generate.cmd`, `test_m45_live_02` prompt-to-CAD via installed `cad-copilot-generate`, and `test_m45_live_03` route and configuration isolation).

### Milestone 4 Milestone-Wide Acceptance Gate (Complete)

Milestone 4 is 100% implemented, hardened, and verified across all five constituent components (M4.1–M4.5):
1. **Single Orchestrator**: `GenerationService` coordinates preparation, execution, finalization, and pure response projection.
2. **Deterministic Catalog**: Bundled `spur_gear` example plan resolves offline without external dependencies or network calls.
3. **Canonical Run Manifest**: Cryptographic `run_manifest.json` sidecar published atomically with multi-format CAD artifacts.
4. **Optional Gemini Adapter**: Text-only BYOK proposal generation with verified `gemini-3.5-flash-lite` default.
5. **Strict Stdio IPC**: One-subprocess-per-request CLI and CMD launchers with descriptor isolation, 128 KiB limit, 4-phase stderr progress, and deterministic exit codes.

**Final M4 Quality Metrics**:
- **Full Offline Suite**: **1,383 passed, 2 skipped, 40 deselected in 44.53s** (`pytest -c pytest.ini -m "not com and not live_ai"`).
- **Strict Mypy**: **Success: 0 issues across 99 source files**.
- **Ruff Linter & Formatter**: Clean across all 132 engine files.
- **Live Solid Edge 2026 Evidence**: Verified across M3, M4 component, and M4.5 CLI suites on Windows 11 with Siemens Solid Edge 2026 (all 3 live CLI gates verified: `test_m45_live_01`, `test_m45_live_02`, `test_m45_live_03`).

### Milestone 5.2 Component Acceptance Criteria (Implemented and Verified)

This section defines acceptance criteria specifically for the Milestone 5.2 batch execution infrastructure component:

1. **Shared Sequential Execution Hub (FR-15)**:
   - `BatchService` in `engine/src/batch/service.py` coordinates preflight validation, work allocation, boundary seam preparation, CAD runtime acquisition, sequential document dispatch, error isolation, cancellation, and observable teardown.
   - Enforces the strict invariant that at most one CAD document is open at any time and guarantees mandatory document close without saving.
   - Preserves caller input and format ordering deterministically throughout execution.
   - Supports extensible custom batch operations and formats through decoupled `BatchExecutionSpec` and `OperationBindings` without modifying the execution hub.

2. **Error Isolation & Lifecycle Precedence (FR-15)**:
   - Per-format handler failures produce `partial` or `failed` file results without crashing the batch run, respecting `continue_on_error`.
   - Lifecycle failures (`DOCUMENT_CLOSE_FAILED`, `SOURCE_INTEGRITY_FAILED`, `SOLID_EDGE_UNHEALTHY`, teardown failure) strictly supersede cooperative cancellation: terminal status is upgraded to `failed`, untouched files are moved to `unprocessed_files`, and `cancelled_files` is cleared.
   - Mid-file cooperative cancellation emits `BATCH_CANCELLED` error diagnostics on the active file while cleanly partitioning remaining inputs to `cancelled_files`.

3. **Progress Observation & Event Safety (FR-15)**:
   - Emits 6 discrete lifecycle progress events (`batch_started`, `file_started`, `format_started`, `format_finished`, `file_finished`, `batch_finished`) with completed counts and lifecycle phase.
   - Isolates all progress observer exceptions (`contextlib.suppress(Exception)`), guaranteeing that faulty observers cannot abort or corrupt batch execution.

4. **Automated Verification Baseline**:
   - Dedicated batch test suite: **346 passed** across 14 `test_*.py` test modules (`test_accounting.py`, `test_allocation.py`, `test_bindings.py`, `test_contracts.py`, `test_execution.py`, `test_models.py`, `test_paths.py`, `test_registry.py`, `test_service_cancellation.py`, `test_service_failures.py`, `test_service_lifecycle.py`, `test_service_progress.py`, `test_terminal.py`, `test_terminal_cancellation.py`).
   - Full offline test suite: **1,764 passed, 2 skipped, 40 deselected** across all domain packages.
   - Focused runtime lifecycle/security suite: **73 passed** across `test_runtime_document_task.py`, `test_runtime_lifecycle.py`, `test_ownership_teardown_safety.py`, and `test_error_sanitization.py`.
   - Strict mypy: **Success: 0 issues across 138 source files** using the complete M5.2 verification scope.
   - Ruff linting and formatting: clean across all 164 engine files.

### Milestone 5.3 Component Acceptance Criteria (Implemented and Verified)

This section defines acceptance criteria specifically for the Milestone 5.3 batch filesystem safety boundary, source-integrity verification, guarded workspace, and runtime document close lifecycle hardening:

1. **Bounded Local Root Enforcement & Canonical Path Validation (FR-16)**:
   - Validates `input_root` and `output_root` as existing, non-root, absolute local drive-qualified Windows directory paths without UNC, device, DOS-device (`\\.\`), or extended-length (`\\?\`) prefixes.
   - Verifies roots and parent components are normal directories and not symlinks, junctions, or reparse points.
   - Captures and enforces stable filesystem identity `(st_dev, st_ino)` on roots during preparation.
   - Enforces strict canonical containment beneath validated roots for all relative file paths.
   - Rejects directory traversal (`..`), empty components, colons, NUL bytes, trailing dots, leading/trailing spaces, and alternate data streams (`:`).
   - Rejects Windows reserved device names (`CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`) across all path components regardless of file extension.
   - Detects and rejects duplicate selected source identities across different canonical relative paths (aliasing/hardlinks) before CAD runtime acquisition.

2. **Race-Resilient Source-Integrity Snapshotting (FR-16)**:
   - `SourceSnapshot` captures regular file mode, exact file size in bytes, nanosecond modification timestamp (`st_mtime_ns`), filesystem identity `(st_dev, st_ino)`, and lowercase SHA-256 digest.
   - Computes SHA-256 digests via fixed 1 MiB chunk streaming without loading entire CAD documents into memory.
   - Pre- and post-stream metadata comparison verifies that source files were not modified during snapshot computation.
   - Exact double-verification timing: pre-open snapshot immediately before `open_document()`, and post-close snapshot immediately after no-save close with zero-tolerance equality enforcement. Emits fatal `SOURCE_INTEGRITY_FAILED` diagnostic on any mismatch.

3. **Guarded Output Workspace & Windows Atomic Publication (FR-16)**:
   - Allocates private per-format same-volume staging directory (`output_root/.cad-copilot-work-<token>/`) without immediate disk creation during preparation.
   - Context-verified `GuardedOutputWorkspace` strictly governs the format lifecycle states (`allocated` -> `active` -> `finalized` / `cleaned`).
   - `begin_format` exclusively creates the private staging directory via `os.mkdir()` and validates empty directory identity.
   - Windows-native atomic publication (`output_publication.py` using Windows `MoveFileExW` with zero flags) guarantees atomic no-replace publication that fails closed with `TARGET_ALREADY_EXISTS` without overwriting pre-existing targets.
   - Guarded cleanup removes the verified work file and empty staging directory on failure or cancellation without broad recursive tree deletions.

4. **Production Batch Safety Boundary (`FilesystemBatchSafetyBoundary`) (FR-16)**:
   - Production implementation of `BatchSafetyBoundary` protocol bridging roots, validation, snapshots, and guarded workspaces.
   - Seamlessly integrates with `BatchService` lifecycle hooks (`prepare`, `verify_before_open`, `verify_after_close`).
   - Emits canonical error diagnostics conforming to `batch-response.schema.json`: `INPUT_ROOT_NOT_FOUND`, `INPUT_PATH_NOT_ALLOWED`, `OUTPUT_ROOT_UNAVAILABLE`, `INPUT_FILE_NOT_FOUND`, `TARGET_ALREADY_EXISTS`, `SOURCE_INTEGRITY_FAILED`, and `INTERNAL_ERROR`.
   - Never leaks raw workstation filesystem paths or unhandled OS error strings into diagnostics (SEC-07).

5. **Hardened Document Close Lifecycle & Application Quiescence (FR-7)**:
   - Centralized worker-side document close sequence in `SolidEdgeRuntime`:
     `Close(False) -> raw registry removal -> local reference release -> pending-idle registration -> Application.DoIdle()`.
   - Finalization boundary: `_closed_pending_idle_handles` removal and public `_open_document_handles` removal occur only after `worker.call()` returns successfully to the caller, preventing state loss on timeouts or late worker completion.
   - Threaded STA dispatch via `worker.call()` for normal closes, and direct worker invocation for failed create-mode cleanups.
   - Normal close retry is the path that retries `DoIdle()` when a handle is in `_closed_pending_idle_handles` without attempting duplicate `Close(False)` or resolving released raw documents.
   - Teardown Stage 1 safely skips duplicate `Close(False)` on handles marked pending-idle, records incomplete document cleanup (`docs_clean = False`), and continues through ownership-safe teardown without calling COM against released documents or falsely reporting `DOCUMENT_LOST`.

6. **Automated & Live Verification Baseline**:
   - Dedicated batch test suite: **657 passed, 1 skipped** across 24 test modules in `tests/batch/`.
   - Focused driver lifecycle & security suite: **84 passed** across `test_runtime_document_task.py`, `test_runtime_lifecycle.py`, `test_ownership_teardown_safety.py`, and `test_error_sanitization.py`.
   - Full offline engine test suite: **2,086 passed, 3 skipped, 41 deselected in 50.82s** (`pytest -c pytest.ini -m "not com and not live_ai"`).
   - Strict static typing: **Success: 0 issues across 157 source files** (`mypy --config-file mypy.ini --strict src tests/manifests tests/artifacts tests/application tests/example_catalog tests/plan_providers tests/ipc tests/batch tests/contracts/test_schema_contracts.py tests/contracts/test_batch_schema_contracts.py tests/test_interfaces.py tests/drivers/test_executor.py tests/drivers/test_runtime_document_task.py tests/drivers/test_runtime_lifecycle.py tests/drivers/test_ownership_teardown_safety.py tests/drivers/test_error_sanitization.py tests/drivers/test_solidedge_live.py`).
   - Ruff linting and formatting: Clean across all 183 engine files.
   - Live Solid Edge 2026 Verification: Passed live source immutability gate (`test_m53_live_01_source_immutability_and_close_lifecycle`) across all 4 native formats (`.par`, `.dft`, `.psm`, `.asm`) on Siemens Solid Edge 2026 (version `226.00.00.106`, PID `13888`, borrowed session) with 100% pre- and post-close SHA-256 and metadata preservation.

### Milestone 5.4 Component Acceptance Criteria (Implemented and Verified)

This section defines acceptance criteria specifically for the Milestone 5.4 genuine batch format handlers, native export operations, drawing view refresh, assembly reference verification, closed-output structure validation, and live Solid Edge 2026 verification:

1. **Closed-Output Structure Validators (FR-17)**:
   - `validate_batch_output` validates exported files directly from temporary staging workspaces before publication without modifying file bytes.
   - STEP: Performs bounded full-text read and structural/geometry validation up to 50,000,000 characters, verifying ISO 10303-21 structure (`ISO-10303-21;`, `HEADER;`, `END-SEC;`, `DATA;`, `END-SEC;`, `END-ISO-10303-21;`), solid B-Rep presence (`MANIFOLD_SOLID_BREP`), length units, and finite 3D bounding envelope.
   - STL: Auto-detects binary vs ASCII; validates binary STL 80-byte header, 4-byte triangle count, and exact file size ($84 + 50 \times N$ bytes); validates ASCII STL `solid ... endsolid` and parses vertex coordinates to reject non-finite floats (`NaN`/`Inf`).
   - PDF: Verifies magic header supporting versions 1.0 through 1.7 and 2.0 (`%PDF-(?:1\.[0-7]|2\.0)`) and terminal `%%EOF`.
   - DXF: Verifies text ASCII DXF structure with paired group codes, valid named `SECTION`, balanced `ENDSEC`, and terminal `EOF`.
   - Fails closed with `INVALID_BATCH_OUTPUT` on empty files, truncated content, syntax corruption, or unknown formats.

2. **Native Solid Edge Export Operations (FR-17)**:
   - Implements `export_model_to_path` (`engine/src/drivers/solidedge/exporter.py`) executing native `SaveCopyAs` for `.step` and `.stl` across Ordered part (`.par`), sheet metal (`.psm`), and assembly (`.asm`) documents on the dedicated STA worker thread.
   - Implements `refresh_drawing_views` and `publish_drawing_to_path` (`engine/src/drivers/solidedge/drawing_publisher.py`) executing in-memory drawing view refresh (iterating each `DrawingView.Update()`) and native `SaveCopyAs` for `.pdf` and `.dxf` from native draft documents (`.dft`) without disk saves.
   - Enforces strict zero-save source immutability: source documents are never saved to disk and source bytes/metadata remain unchanged on disk; any refreshed in-memory document state is discarded during no-save close.
   - Enforces zero raw workstation path or internal exception leakage into user-facing diagnostics (SEC-07).

3. **Authoritative Assembly Reference Verification & Isolation (FR-17, SEC-07)**:
   - Recursively inspects top-level and nested subassembly occurrences up to `MAX_ASSEMBLY_TRAVERSAL_DEPTH = 32`.
   - Uses official Siemens COM method `Occurrence.FileMissing()` and `OccurrenceDocument` resolution to detect missing reference components authoritatively.
   - Grounded component resilience: Correctly recognizes grounded base components (`Status == 2` / `seOccurrenceStatusFixed`) with valid documents as resolved, avoiding false-positive missing-file errors.
   - Fail-closed isolation: Unresolved occurrences fail closed before writing output files, producing zero exported artifacts, returning pure frozen `AssemblyReferenceCheckResult(is_resolved=False)` with sanitized counts, and permitting healthy subsequent inputs to succeed when `continue_on_error=True`.

4. **Production Handler Composition & Service Integration (FR-17)**:
   - Binds genuine `Export3DHandler` and `PublishDrawingHandler` into `OperationBindings` matching the 1-to-1 schema operation registry (`export_3d` -> `["step", "stl"]`, `publish_drawing` -> `["pdf", "dxf"]`).
   - Seamlessly coordinates format validation, guarded workspace allocation, atomic publication, and progress reporting through `BatchService`.

5. **Automated & Live Verification Baseline**:
   - Dedicated batch test suite: **744 passed, 2 skipped** across 25 test modules in `tests/batch/`.
   - Focused driver lifecycle & security suite: **84 passed** across `tests/drivers/`.
   - Full offline engine test suite: **2,220 passed, 4 skipped, 46 deselected in 49.58s** (`pytest -c pytest.ini -m "not com and not live_ai"`).
   - Strict static typing: **Success: 0 issues across 170 source files** (`mypy --config-file mypy.ini --strict src tests/manifests tests/artifacts tests/application tests/example_catalog tests/plan_providers tests/ipc tests/batch tests/contracts/test_schema_contracts.py tests/contracts/test_batch_schema_contracts.py tests/test_interfaces.py tests/drivers/test_executor.py tests/drivers/test_runtime_document_task.py tests/drivers/test_runtime_lifecycle.py tests/drivers/test_ownership_teardown_safety.py tests/drivers/test_error_sanitization.py tests/drivers/test_assembly_references.py tests/drivers/test_exporter.py tests/drivers/test_drawing_publisher.py tests/drivers/test_batch_formats_live.py tests/drivers/test_solidedge_live.py`).
   - Ruff linting and formatting: Clean across all 195 engine files.
   - Live Solid Edge 2026 Verification Matrix: **5 passed, 0 skipped in 100.06s** across all 5 live gates (`test_batch_formats_live.py`) on Siemens Solid Edge 2026 build `226.00.00.106` (3D matrix with 100% source immutability, drawing publication with in-memory refresh, unresolved assembly isolation with fail-closed sentinel checks, atomic collision handling, cancellation cleanup, and verified `runtime.teardown() is True`).

### Milestone 5.5 Component Acceptance Criteria (Implemented and Verified)

This section defines acceptance criteria specifically for the Milestone 5.5 conditional Parasolid export gate, non-destructive transmission structural validation, non-mutating reopen count verification, canonical contract and registry promotion, and live Solid Edge 2026 verification:

1. **Parasolid Transmission Structural Validation (FR-18)**:
   - `validate_batch_output("parasolid", ...)` dispatches to `validate_parasolid_artifact()` (`engine/src/batch/parasolid_validation.py`) to validate exported `.x_t` files directly from temporary staging workspaces before publication without mutating file bytes.
   - Enforces regular non-reparse file with minimum byte size (`MIN_PARASOLID_BYTES = 100`).
   - Scans bounded 2 KiB header window (`PARASOLID_HEADER_SCAN_BYTES = 2048`) enforcing strict ASCII decoding, zero NUL bytes, max line length of 256 bytes, valid character set line starting with `**` containing alphabet `ABCDEFGHIJKLMNOPQRSTUVWXYZ`, symbol delimiter line starting with `**PARASOLID `, section markers `**PART1;` and `**PART2;` (with `PART1` preceding `PART2`), transmission attributes `FORMAT=text;` and `GUISE=transmit;`, and schema version pattern `SCH=SCH_([A-Za-z0-9_]+);`.
   - Scans bounded 2 KiB trailer window (`PARASOLID_TRAILER_SCAN_BYTES = 2048`) enforcing non-empty ASCII text, zero NUL bytes, max line length of 256 bytes, and newline record termination (`\r\n` or `\n`).
   - Fails closed with sanitized `BatchFormatValidationError(format="parasolid", phase="validation")` on binary Parasolid files (`.x_b`), truncated content, corrupted markers, or non-ASCII characters.

2. **Native Solid Edge Parasolid Export & Sidecar Suppression (FR-18)**:
   - `export_model_to_path` (`engine/src/drivers/solidedge/exporter.py`) routes format token `"parasolid"` to native `SaveCopyAs` for `.x_t` across Ordered part (`.par`), sheet metal (`.psm`), and assembly (`.asm`) documents on the dedicated STA worker thread.
   - Source document safety is guaranteed by streaming SHA-256 pre-open and post-close integrity snapshots, exporting exclusively through `SaveCopyAs` to guarded workspaces, and closing without saving via `Close(False)`.
   - Zero sidecar pollution: Solid Edge produces no auxiliary translation log files for `.x_t` (unlike `.step` and `.stl`), leaving clean destination workspaces.
   - Zero raw workstation path or internal exception leakage into user-facing diagnostics (SEC-07).

3. **Canonical Contract & Registry Promotion (FR-18)**:
   - Promoted `parasolid` to an approved first-class format token under `export_3d` in JSON schemas (`batch-request.schema.json`, `batch-response.schema.json`, `batch-manifest-v1.schema.json`) and engine domain models (`Export3DFormat = Literal["step", "stl", "parasolid"]`).
   - Cardinality: `export_3d` accepts 1 to 3 items (`["step", "stl", "parasolid"]`), while `publish_drawing` remains 1 to 2 items (`["pdf", "dxf"]`).
   - Schema parity: Canonical (`contracts/schemas/batch/`) and packaged (`engine/src/batch/schemas/`) JSON schemas remain 100% byte-identical.
   - `build_initial_registry()` exposes `output_formats=("step", "stl", "parasolid")`.

4. **Automated & Live Verification Baseline**:
   - Dedicated batch test suite: **783 passed, 2 skipped** across 30 test modules in `tests/batch/`.
   - Focused driver lifecycle & security suite: **84 passed** across `tests/drivers/`.
   - Full offline engine test suite: **2,263 passed, 4 skipped, 51 deselected in 50.41s** (`pytest -c pytest.ini -m "not com and not live_ai"`).
   - Strict static typing: **Success: 0 issues across 173 source files** (`mypy --config-file mypy.ini --strict src tests/manifests tests/artifacts tests/application tests/example_catalog tests/plan_providers tests/ipc tests/batch tests/contracts/test_schema_contracts.py tests/contracts/test_batch_schema_contracts.py tests/test_interfaces.py tests/drivers/test_executor.py tests/drivers/test_runtime_document_task.py tests/drivers/test_runtime_lifecycle.py tests/drivers/test_ownership_teardown_safety.py tests/drivers/test_error_sanitization.py tests/drivers/test_assembly_references.py tests/drivers/test_exporter.py tests/drivers/test_drawing_publisher.py tests/drivers/test_batch_formats_live.py tests/drivers/test_parasolid_live.py tests/drivers/test_solidedge_live.py`).
   - Ruff linting and formatting: Clean across all 198 engine files.
   - Live Solid Edge 2026 Verification Matrix:
     - M5.5 live matrix (`test_parasolid_live.py`): **5 passed, 0 skipped in 149.11s** against clean commit `db19450` on Siemens Solid Edge 2026 build `226.00.00.106` (3-family `.par`/`.psm`/`.asm` export and non-mutating `OpenWithTemplate` count probe, collision isolation, unresolved assembly isolation, cooperative cancellation cleanup, and localized failure isolation).
     - M5.4 live regression matrix (`test_batch_formats_live.py`): **5 passed, 0 skipped in 115.69s** against clean commit `db19450`.
     - Zero running or orphan `Edge.exe` processes (clean teardown verified).

### Milestone 5.6 Component Acceptance Criteria (Implemented and Verified)

This section defines acceptance criteria specifically for the Milestone 5.6 batch summary manifest, strict stdio IPC, signal handling, launchers, and packaging:

1. **Summary Manifest Assembly & Containment (FR-19)**:
   - Canonical `BatchManifest` assembly conforming to `batch-manifest-v1.schema.json` for progressed outcomes (`completed`, `cancelled`, and progressed `failed`).
   - Rejection and early failure paths never hash disk artifacts or attempt manifest assembly/publication.
   - Installed `cad-copilot` distribution metadata resolved before CAD acquisition and injected into manifest; if unavailable after request validation, returns sanitized early `failed / INTERNAL_ERROR` without acquiring Solid Edge.
   - Every exported artifact is verified for strict canonical containment beneath the validated `output_root` and confirmed as a regular, non-reparse file.
   - Manifest artifact records store strictly relative, forward-slash portable paths derived via `target_path.relative_to(validated_output_root).as_posix()` after strict containment verification, preserving input directory mirroring.
   - Fresh post-execution size and streaming SHA-256 snapshots (`OutputSnapshot`) computed with 1 MiB chunk buffers without loading complete files into memory.
   - Preserves caller request file ordering and requested format ordering. Zero user prompts, API keys, CAD geometries, environment dumps, usernames, or absolute workstation paths in manifest data.

2. **Atomic Manifest Publication & Failure Precedence (FR-19)**:
   - Published destination: `<output_root>/<request_id>.batch_manifest.json`.
   - Exclusive, randomly named temporary staging directly under `output_root`, ensuring same-volume atomic rename.
   - Bounded (10 MiB cap), deterministic, ASCII-safe UTF-8 JSON serialization ending with a single LF (`\n`).
   - Strict read-back validation against `batch-manifest-v1.schema.json` and parsed data equality verification before rename.
   - Pre-rename re-verification of output root identity, target containment, target absence, and temporary-file identity.
   - Windows atomic no-replace publication (`MoveFileExW` without replace flag), followed by post-publication snapshot and byte verification; fails closed if a collision sentinel or prior file exists without overwriting.
   - Exact cleanup removing only the owned private staging file on failure; pre-existing files and sentinels are preserved.
   - Publication failure precedence: if manifest assembly, artifact hashing, serialization, read-back, collision check, rename, or post-publication verification fails, terminal response transitions to progressed `failed` with `manifest=None`, `cancelled_files=()`, `summary.cancelled=0`, and untouched inputs moved to `unprocessed_files`.
   - For previously completed or cancelled outcomes, top-level error is `MANIFEST_PUBLICATION_FAILED`; for an already-progressed `failed` outcome, existing fatal errors are retained and `MANIFEST_PUBLICATION_FAILED` is appended exactly once.

3. **Strict Stdio IPC Framing & Descriptor Isolation (FR-19)**:
   - Single-subprocess-per-request entrypoint: `ipc.batch_stdio:main`.
   - Bounded raw stdin stream up to 128 KiB limit (`131,072` bytes); rejects oversize inputs with `rejected/PAYLOAD_TOO_LARGE`.
   - Strict JSON decoding rejecting duplicate object keys, non-finite constants (`NaN`, `Infinity`), BOM, and trailing tokens.
   - Exactly one schema-valid compact 7-bit ASCII JSON response followed by LF on preserved `stdout`.
   - Preserved descriptors and early null redirection of C-runtime descriptors and Windows standard handles (`STD_OUTPUT_HANDLE`, `STD_ERROR_HANDLE`) before production module imports, guaranteeing zero stdout/stderr contamination.

4. **Progress Protocol & Console Signal Lifecycle (FR-19)**:
   - Curated 6-phase JSONL progress on stderr (`batch_started`, `file_started`, `format_started`, `format_finished`, `file_finished`, `batch_finished`) with 4 KiB line caps, best-effort delivery, canonical relative input paths, and bounded lines. Broken stderr does not disrupt batch completion.
   - Fatal diagnostic envelope: `{"type":"diagnostic","phase":"fatal","message":"Batch process failed before a contract response could be produced."}` on `stderr` on unhandled bootstrap or framing failures.
   - Cooperative signal handler (`SIGINT` and Windows `SIGBREAK` / `CTRL_BREAK_EVENT`) installed strictly between request validation and composition, setting an async-safe cancellation flag without executing I/O, COM calls, document close, or process termination; restored in `finally`.
   - Exit codes: `0` for handled contract responses (`completed`, `cancelled`, `rejected`, early `failed`, progressed `failed`), `1` for fatal bootstrap/serialization failures (empty stdout), `130` for interruptions outside the cooperative scope.

5. **Launchers & Package Verification (FR-19)**:
   - Installed console entrypoint: `[project.scripts] cad-copilot-batch = "ipc.batch_stdio:main"`.
   - Windows source wrapper: `engine/scripts/batch.cmd` with `%~dp0`-relative interpreter resolution, quiet import and distribution-metadata preflight, and fatal diagnostic on failure.
   - Wheel packaging contains and resolves all three `batch/schemas/*.json` resources byte-identical to canonical contracts in isolated installations outside repository checkouts.
   - Zero CAD Copilot authentication, entitlement, or licensing subsystem required, while preserving caller-installed and appropriately licensed Siemens Solid Edge as the mandatory live-execution prerequisite.

6. **Milestone 5.6 Verification Gate Summary**:
   - Automated offline & static suites: **2,398 passed, 4 skipped, 55 deselected** in 75.94s (the 4 skips being Windows symlink-permission guards); strict Mypy clean across all 174 checked source files; Ruff lint and format clean.
   - Wheel packaging verification: installed wheel in isolated temp virtualenv executed CLI and loaded packaged batch schema resources without repository checkout dependency.
   - Combined live Solid Edge 2026 verification matrix: **14 passed, 0 skipped in 390.10s** (tested snapshot byte-identical to commit `34dec71`), comprising M5.6 live CLI suite (`test_batch_solidedge_live.py`, 4 passed: B-LIVE-M56-01 through B-LIVE-M56-04), M5.4 formats regression suite (`test_batch_formats_live.py`, 5 passed), and M5.5 Parasolid regression suite (`test_parasolid_live.py`, 5 passed), with zero active or orphan `Edge.exe` processes remaining after teardown.

### Milestone 6.1 Component Acceptance Criteria (FR-20 — Clean Desktop Foundation)

This section defines acceptance criteria specifically for the Milestone 6.1 clean desktop foundation (FR-20):

1. **Workspace Scaffold & Toolchain Foundation (FR-20)**:
   - Dedicated `@cad-copilot/desktop` pnpm workspace created under `desktop/`, recognized by root `pnpm-workspace.yaml`.
   - Single root `pnpm-lock.yaml` remains the exclusive JavaScript dependency lockfile; zero duplicate lockfiles.
   - Package scripts define `"dev": "vite"`, one-shot `test`, `lint` (`eslint . --max-warnings=0`), `typecheck` (`tsc -b --noEmit` or single-project equivalent), `format` (`prettier --write .`), `format:check` (`prettier --check .`), and `build` running the complete typecheck boundary before `vite build` (`pnpm typecheck && vite build`).
   - Unified `dev` script serves both root `pnpm dev:desktop` and Tauri's `beforeDevCommand`.
   - Tailwind CSS v4 configured via `@tailwindcss/vite` without legacy config files; production bundle contains zero Tailwind runtime scripts.
   - ESLint 9 ESM flat config (`eslint.config.js`) configured with `@eslint/js`, `typescript-eslint`, and stable `eslint-plugin-react-hooks` presets.
   - Strict TypeScript checking covers every referenced project without emitting `.js`, `.d.ts`, or tracked `.tsbuildinfo` cache files into the repository.

2. **Application Shell & Honest Presentation (FR-20)**:
   - React 19 application bootstrapped with `createRoot` and `StrictMode`.
   - Exactly two primary navigation choices: `Generate` (default) and `Batch`, managed via local state without heavy routing libraries.
   - Page roots render clear semantic headings and honest milestone foundation status; no non-functional operational controls, fake loading states, or simulated execution.
   - Accessible settings/diagnostics modal/panel with keyboard navigation (Escape, focus trap/return) displaying exactly 5 typed foundation rows: Output directory (`Not selected`), Solid Edge (`Not checked`), Gemini (`Not configured`), model (`Not loaded` or default), and engine version (`Not connected`). Zero input fields, zero secret storage.
   - Accessible design tokens, semantic landmarks, high contrast, and `prefers-reduced-motion` compliance. Tree-shaken `lucide-react` icons with accessible labels; zero raw embedded duplicate SVG paths.
   - Zero Firebase, sign-in, cloud URLs, telemetry, licensing/subscription UI, 3D canvas, image upload, revision timeline, or editing controls.

3. **Native Tauri Host & Security Baseline (FR-20)**:
   - Minimal Tauri v2 Windows host with distinct Cargo target names: binary `cad-copilot-desktop` and library `cad_copilot_desktop_lib` to prevent Windows output-collision warnings (Cargo issue #8519).
   - `#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]` in `main.rs` suppresses extraneous Windows consoles in release builds while preserving diagnostics in debug builds.
   - Repository-owned master brand asset (`desktop/src/assets/logo.png`) and valid multi-layer Windows icon (`desktop/src-tauri/icons/icon.ico` with 16, 24, 32, 48, 64, and 256 pixel layers) committed in the scaffold.
   - Capability configuration in `capabilities/default.json` grants only the minimum core permissions for the single main window; zero shell/process, opener, dialog, filesystem, store, clipboard, HTTP, or notification permissions.
   - Content Security Policy (CSP): Strict production CSP permits only local bundled assets and required Tauri IPC sources; separate development CSP (`devCsp`) permits only the local Vite origin and same-port WebSocket HMR endpoint (`localhost:1420`).
   - Fixed Vite port `1420` with `strictPort: true` matching `tauri.conf.json.build.devUrl`.
   - `bundle.active` remains disabled; source-run and build verification only. Zero custom `#[tauri::command]` handlers or optional Tauri plugins in M6.1.

4. **Error Boundary & Non-Disclosure (FR-20)**:
   - Top-level React error boundary wraps the application tree below `StrictMode` and above page composition.
   - Catches render/lifecycle errors and presents an accessible `FatalErrorView` with a generic user-friendly message and a `Reload application` recovery action.
   - Strict non-disclosure: completely suppresses exception messages, stack traces, component stacks, filesystem paths, and environment variables from rendered DOM.
   - Avoids recording error details or secrets in browser storage or frontend history.


### Milestone 6.2 Component Acceptance Criteria (FR-21 — Generate End-to-End Vertical Slice)

This section defines acceptance criteria specifically for the Milestone 6.2 Generate vertical slice (FR-21):

1. **Scoped Native Commands & Permissions (FR-21)**:
   - Dedicated application permissions defined in `desktop/src-tauri/permissions/generation.toml` for narrow commands: `generation_snapshot`, `generation_select_output`, `generation_set_key`, `generation_start`, `generation_cancel`, `generation_result`, `generation_preview`, `generation_reveal`, `generation_resolve_close`.
   - Capability configuration in `capabilities/default.json` grants only these exact unprefixed application permissions and core `core:event:allow-listen` / `core:event:allow-unlisten` to the `main` window; zero wildcard permissions, remote origins, or plugin permissions.
   - Native commands verify the caller window label (`window.label() == "main"`).

2. **Single-Run State & Session Secret Privacy (FR-21)**:
   - Application-owned single-run state slot enforces at most one active generation; concurrent or double-submit attempts are rejected with `RUN_ACTIVE`.
   - Session Gemini API key is retained strictly in native memory for the duration of the application session; renderer memory is cleared immediately after save.
   - API key is never serialized in native snapshots, logged in diagnostics or console, passed on child command-line arguments, or persisted to disk or browser storage.
   - Key is delivered to child processes exclusively via child-only `GEMINI_API_KEY` for prompt requests, and stripped for example plan runs.

3. **Output Authority & Path Containment (FR-21)**:
   - Output directory selection is mediated via a native folder dialog returning an opaque `selectionId` and display path; frontend cannot specify arbitrary filesystem paths.
   - Native layer strictly verifies path canonicalization, regular directory status, containment, and absence of junctions, symlinks, reparse points, ADS streams, and UNC/device paths.
   - Output reveal opens only the verified run directory via fixed Explorer command execution with safely separated arguments.

4. **Strict Transport, Progress & Cancellation (FR-21)**:
   - Native launcher resolves trusted source-run interpreter `.venv/Scripts/python.exe` and validates prerequisite probe before execution.
   - Child process execution communicates via bounded pipes: 131,072-byte stdin request envelope, 1 MiB stdout response cap, 1,024-byte line cap and 1 MiB aggregate cap on stderr progress records.
   - Stdout strictly parsed as single JSON object matching generation response schema; stderr parsed as curated 4-phase progress events (`request_received`, `request_validated`, `generation_started`, `response_ready`).
   - Windows lifecycle: Targeted cancellation via `CTRL_BREAK_EVENT` delivered to the child process group (shared console) or child console (hidden console); `stdio.py` installs a scoped main-thread `SIGBREAK` handler translating to `KeyboardInterrupt`, executing teardown `finally:` blocks and exiting 130 with empty stdout; 180s deadline and 10s grace period enforced.

5. **Output Validation & Static Preview (FR-21)**:
   - Published artifacts (`.par`, `.step`, `.stl`, optional `.jpg`) verified against sidecar `run_manifest.json` schema (`cad_copilot.run_manifest.v1`) and SHA-256 digests.
   - Preview JPEG validated natively for bounded header/dimensions (max 8,192 px per axis, 32M pixels) without native pixel decode, transferred via binary IPC, and rendered via `blob:` URL with CSP `img-src 'self' blob:`.

6. **Generate UI & Accessibility (FR-21)**:
   - Generate workspace provides explicit Prompt and Example modes, defaulting to deterministic `Conceptual Spur Gear`.
   - Masked session key editor lives inline in an expandable section of `GenerateForm.tsx` with Save/Replace/Clear controls; Diagnostics panel remains read-only.
   - Full keyboard navigation, predictable focus management, and responsive layout between 900x600 and 1200x800 preserved.

7. **Milestone 6.2 Verification Gate Summary**:
   - Automated Rust & native baseline: **62 passed, 0 failed** (58 unit tests, 2 synthetic process lifecycle integration tests, and 2 live IPC cancellation qualification tests). Under `$env:CAD_COPILOT_LIVE_TESTS="1"`, both live cancellation tests execute against genuine Python child processes verifying targeted `CTRL_BREAK_EVENT` delivery and 130 exit code with empty stdout and clean output root. Clippy clean with 0 warnings (`cargo clippy -- -D warnings`); formatting clean (`cargo fmt -- --check`); native Windows binary compiles cleanly (`cargo check`).
   - Frontend test & production build: **53 passed, 0 failed** across 9 Vitest suites (`pnpm test:desktop`); TypeScript typecheck clean with 0 errors (`tsc -b --noEmit`); ESLint clean with 0 warnings (`eslint . --max-warnings=0`); Prettier clean (`prettier --check .`); production Vite build clean (`dist/index.html`, `dist/assets/`).
   - Live Solid Edge 2026 CLI & IPC integration: Verified end-to-end against licensed Siemens Solid Edge 2026 (`226.00.00.106`): `test_m45_live_01_example_plan_spur_gear_cmd_launcher` passed in 31.44s (genuine 24-tooth spur gear, positive volume inspection, all 4 published artifacts, schema-valid `run_manifest.json` sidecar, zero orphan `Edge.exe`), and `test_m45_live_03_route_and_configuration_isolation` passed in 22.68s (fast failure on missing key/invalid model, subsequent clean rerun).
   - Targeted cancellation: Targeted child console signal delivery (`CTRL_BREAK_EVENT`) unwinds the target process without signal bleed to unrelated control children (`generation_lifecycle.rs`).
   - Live Windows 11 GUI smoke acceptance (Dev & Release Hosts): Verified on running native desktop host across both development (`pnpm tauri dev`) and standalone release (`cad-copilot-desktop.exe`) environments: interactive folder selection via native dialog (`rfd`), deterministic example generation with 4 real-time progress phases, verified artifact cards (`.par`, `.step`, `.stl`), non-fatal preview handling, Explorer reveal, live Gemini 3.5 Flash Lite prompt-to-CAD generation (`Create a 50x40x10 mm rectangular block`), fast-fail route isolation with non-disclosure (`PROMPT_INTERPRETATION_FAILED`), in-flight active CAD cancellation via GUI Cancel button with clean child unwind (exit code 130) and zero orphan processes, in-flight provider-wait cancellation during `Request Validated` awaiting Gemini HTTPS response with clean exit 130 and zero CAD contact, in-flight window close interception ("Keep Generating" vs "Cancel & Exit"), release host hidden-console child spawn (`CREATE_NEW_CONSOLE` + `SW_HIDE`) with zero window flashing, release host prerequisite probe stdin handle hardening (`Stdio::null()`), release in-flight cancellation, relaunch privacy (session key wiped on restart, zero disk/storage persistence across relaunch), modal focus trapping, and keyboard Escape dismissal.

### Milestone 6.3 Component Acceptance Criteria (FR-22 — Desktop Batch Vertical Slice — Delivered & Verified)

This section defines acceptance criteria specifically for the Milestone 6.3 Batch vertical slice (FR-22), connecting the desktop frontend to the verified Milestone 5 batch engine contracts:

1. **Native Source Authority & Selection (FR-22, UI-05, SEC-06, BAT-02)**:
   - Native file and folder selection dialogs via `rfd` mediated entirely by the native Tauri layer.
   - File picker selection enforces single-parent root containment for all chosen files.
   - Folder picker performs bounded recursive scan beneath the chosen root (capped at 20,000 visited filesystem entries and 500 supported files); exceeding limits fails closed with a curated warning rather than returning silent partial data.
   - Case-insensitive recognition of native Solid Edge formats: `.par`, `.psm`, `.asm`, and `.dft`.
   - Reparse points, symlinks, junctions, UNC shares, device paths, and alternate data streams (`:`) are detected and rejected/skipped with visible disclosure without following external links.
   - Discovered supported files (up to 500 canonical relative paths) and kind counts are returned once to the frontend under an opaque `selectionId`; the pre-run list is display/search-only in React, while Rust retains authoritative source inventory and filesystem identity.

2. **Operation Routing & Format Compatibility (FR-22, BAT-03, BAT-04, BAT-C01)**:
   - Fixed operation family routing: `export_3d` accepts `.par`, `.psm`, and `.asm` inputs with STEP, STL, and Parasolid (`.x_t`) output formats (1–3 formats); `publish_drawing` accepts `.dft` inputs with PDF and text DXF output formats (1–2 formats).
   - Incompatible source kinds for the selected operation are excluded from request submission and reported clearly in the UI; mixed selections require explicit operation choice without silent splitting.
   - Default 100-file cap enforced unless explicitly raised by user (hard ceiling of 500 files); requests exceeding 500 files are rejected before child spawn.

3. **Output Authority & Collision Policy (FR-22, ART-02–08, UI-06)**:
   - Output directory selected via native dialog returning opaque `selectionId` and display path; frontend cannot pass arbitrary filesystem paths.
   - Output directory identity bound natively and verified before launch and reveal.
   - Source-relative directory structure mirrored under output root; batch summary manifest published directly under output root.
   - Fixed no-replace collision policy (`TARGET_ALREADY_EXISTS` / `fail_if_exists`); permanent UI assurances confirm source files are never modified and existing outputs are never overwritten.
   - `continue_on_error` option exposed (default enabled); stops on first failure when disabled while preserving previously exported artifacts.

4. **App-Wide Concurrency & Supervised Transport (FR-22, IPC-01, IPC-07, SE-07)**:
   - Single app-wide `run_claim` coordinator enforces mutual exclusion between Generate and Batch; concurrent or cross-slice run starts are rejected with `RUN_ACTIVE`.
   - One owned Python subprocess per batch request launching direct `ipc.batch_stdio` under trusted source-run interpreter (`.venv\Scripts\python.exe`); zero secondary preflight subprocesses.
   - Bounded stdio communication: 128 KiB stdin request envelope, 10 MiB stdout response cap, 4 KiB per line and 20 MiB aggregate stderr cap.
   - Single Solid Edge connection per batch holding explicit document handles; sequential document processing with no-save close.

5. **Curated Progress & Cooperative Signal Cancellation (FR-22, IPC-03, IPC-04, EXEC-05, EXEC-06)**:
   - Stderr stream parsed as compact JSONL adhering strictly to the 6 canonical phases: `batch_started`, `file_started`, `format_started`, `format_finished`, `file_finished`, and `batch_finished`.
   - Windows targeted cancellation delivers `CTRL_BREAK_EVENT` to the owned child process group (shared console) or hidden child console.
   - Cooperative cancellation contract: engine catches `SIGBREAK` inside `_cooperative_cancellation_scope`, completes in-flight file teardown, outputs schema-valid `BatchResponse` with `status="cancelled"` and terminal accounting, and exits with code **0**.
   - Exit code 130 handled as pre-handler / unhooked interruption with incomplete cleanup reporting.
   - Reaping and teardown never terminates `Edge.exe`, borrowed CAD sessions, or unrelated host processes.

6. **Terminal Accounting, Manifest Verification & Reveal (FR-22, OK-B01–04, ART-07, SEC-06)**:
   - Authoritative terminal response validation across Succeeded (`accepted`), Partial (`partial`), Failed (`failed`), Cancelled, and Unprocessed categories matching engine response.
   - Manifest derivation (`<output_root>/<request_id>.batch_manifest.json`) bounded to 10 MiB; read-back validation against canonical `batch-manifest-v1.schema.json` and semantic agreement with stdout response.
   - Clear UI distinction: "Manifest validated" vs "Manifest unavailable" (retaining valid engine response accounting).
   - Explorer reveal strictly limited to bound output root with no shell interpolation.

7. **Scoped Diagnostics & Operation-Aware Window Close (FR-22, SE-03, SEC-07)**:
   - Window close requests dispatched by active run claim: Batch owner sets Batch `closeRequested` and emits `batch-state`; Generate owner routes to Generate resolver.
   - Accessible confirmation dialog with operation-specific copy ("Batch Operation in Progress" vs "Generation in Progress") and Keep running / Cancel and close actions.
   - Diagnostics output path scoped to active tab: `currentView === 'batch' ? batch.snapshot.output?.displayPath : generation.snapshot.output?.displayPath` with "Not selected" fallback and zero cross-tab pollution.
   - Zero leakage of API keys, CAD file contents, or raw stderr stack traces. Local filesystem paths are restricted strictly to user-selected source/output roots and selected files displayed for in-app transparency (in Batch cards, state snapshots, active-tab Diagnostics, and local selection validation error banners) and are never exposed in external telemetry, wire diagnostics, or published manifest sidecars.

8. **Milestone 6.3 Verification Gate Summary (Automated, Native, Live COM & Live Desktop Acceptance)**:
   - Automated Rust & native baseline: **114 passed, 0 failed** (100 unit tests, 10 `batch_lifecycle` integration tests, and 4 `generation_lifecycle` integration tests). Proved real Python child processes under both Windows console topologies (shared console and private hidden console with `CREATE_NEW_CONSOLE` + `SW_HIDE`), targeted `CTRL_BREAK_EVENT` delivery, cooperative child unwind (exit code 0 with schema-valid partial accounting, and exit code 130 pre-handler), and zero signal leakage to unrelated processes. Clippy clean with 0 warnings (`cargo clippy --locked --all-targets -- -D warnings`); formatting clean (`cargo fmt --all -- --check`); native Windows compilation clean (`cargo check --locked`).
   - Frontend test & production build: **81 passed, 0 failed** across 9 Vitest suites (`pnpm test:desktop`); TypeScript typecheck clean with 0 errors (`tsc -b --noEmit`); ESLint clean with 0 warnings (`eslint . --max-warnings=0`); Prettier clean (`prettier --check .`); production Vite build clean (`pnpm build:desktop`).
   - Engine regression & static typing: **2,403 passed, 4 skipped, 55 deselected in 69.05s** (`pytest -c pytest.ini -m "not com and not live_ai"`); the dedicated batch test suite recorded **814 passed, 2 skipped** across 30 test modules; strict Mypy clean with 0 issues in 103 source files (`mypy --config-file mypy.ini --strict src`); Ruff lint clean (`ruff check src tests`); Ruff format clean across 221 files (`ruff format --check src tests`).
   - Production native release build: `cad-copilot-desktop.exe` compiled and linked cleanly via `pnpm --filter @cad-copilot/desktop tauri build --no-bundle`.
   - Live Solid Edge 2026 COM integration: Verified end-to-end against licensed Siemens Solid Edge 2026 (`226.00.00.106`): `test_batch_solidedge_live.py` passed in 116.78s across all 4 scenarios (3D export `.par/.psm/.asm` to STEP/STL/Parasolid `.x_t`, 2D drawing publication `.dft` to PDF/DXF, pre-existing manifest collision and recovery, and multi-file cooperative `CTRL_BREAK` cancellation with clean teardown and zero orphan `Edge.exe`); `test_batch_formats_live.py` and `test_parasolid_live.py` passed in 246.33s across all 10 format matrix and isolation scenarios.
   - Process isolation & resource safety: App-wide `run_claim` coordinator enforces mutual exclusion between Generate and Batch; cooperative `CTRL_BREAK_EVENT` signal delivery unwinds target process without signal bleed; zero orphan processes, clean process lifecycle teardown, and source files never modified.
   - Live Windows 11 GUI smoke acceptance (Dev & Windowed Release Hosts): Verified across both `pnpm tauri dev` and `cad-copilot-desktop.exe`:
      - *Host Parity (Tauri Dev & Release .exe)*: Verified visual and interactive parity between the Tauri dev webview (`http://localhost:1420`) and the standalone windowed release app; verified identical card layouts, clean IPC communication, and reactive state updates across both hosts.
      - *Batch 3D Run to Completion*: Verified sequential multi-file processing of native `.par` parts across STEP, STL, and Parasolid `.x_t` (9 artifacts produced); verified real-time progress updates, valid manifest generation at output root, and honest accounting (3 Total, 3 Succeeded, 0 Failed).
      - *Target Collision & Safety*: Verified that re-running against existing output files triggers `TARGET_ALREADY_EXISTS` per format/file, refusing overwrites and preserving pre-existing files without corruption.
      - *In-Flight Cooperative Cancellation*: Verified GUI Cancel button delivers targeted signal across both release and dev hosts; completed files before cancellation preserved as Succeeded with full artifact sets, in-flight file marked `BATCH_CANCELLED` with partial artifacts preserved, and queued files marked Cancelled; valid partial manifest published and verified; zero orphan `python.exe` or `Edge.exe` processes.
      - *Window Close Protection*: Intercepting window close during active run verified modal confirmation dialog ("Batch Operation in Progress"), with functional "Keep running" vs "Cancel and close".
      - *Source Selection Flows*: Verified both bounded folder scan ("Select Folder") and multi-file picker selection ("Select Files") with single-parent root containment, inventory expansion/collapse, and clean path disclosure.
      - *Drawing Publication & Text DXF Acceptance*: Verified 2D drawing publication from native `.dft` drawings to both PDF and text DXF under `MAX_DXF_LINES = 5_000_000`; verified both PDF and large text DXF (up to ~38 MB DXF payload) succeeded, valid batch manifest published, and 2 Total, 2 Succeeded, 0 Failed accounting displayed.
      - *Tab Switching State Continuity*: Switching between Generate and Batch tabs preserves active batch execution results without loss of state.

### Milestone 6.5 Component Acceptance Criteria (QA-08, RUN-10 — Automated Portable Quality Gates & CI Configuration — Delivered & Verified)

This section defines acceptance criteria for Milestone 6.5, establishing authoritative, repeatable, and truthful quality gates for the CAD Copilot repository across local developer workstations and hosted continuous integration:

1. **One Workflow, Three Portable Quality Jobs (RUN-10, QA-08)**:
   - Exactly one GitHub Actions workflow (`.github/workflows/ci.yml`) triggering on pull requests, pushes to `main`, and manual dispatch.
   - Declarative read-only security permissions (`contents: read`), no persisted credentials (`persist-credentials: false`), no repository write tokens, no scheduled triggers, and zero repository secrets or external SaaS scanners.
   - Three independent, bounded jobs:
     - `python` on `windows-2025`: Python 3.14.3 engine quality gates.
     - `frontend` on `ubuntu-24.04`: Node.js 24.15.0 / pnpm 12.4.1 desktop frontend quality gates.
     - `rust-windows` on `windows-2025`: Rust 1.95.0 / Tauri host quality gates and no-bundle production build.
   - All external GitHub actions pinned to immutable full-length commit SHAs with reviewed version comments (`actions/checkout`, `actions/setup-python`, `actions/setup-node`).

2. **Python Engine Quality Gates (QA-01, RUN-08, MIG-05)**:
   - Root `.venv` created with exact CPython 3.14.3 x64 without relying on ambient system state.
   - Dual-layer pip constraint resolution: `engine/packaging/constraints.txt` governing production runtime and `engine/ci-constraints.txt` pinning exact developer and test tooling (`pytest==9.1.1`, `mypy==2.3.1`, `ruff==0.16.8`, etc.).
   - Offline pytest suite verifying all 2,438 non-COM/non-live-AI tests (2,434 passed, 4 Windows symlink guards skipped, and 55 COM/AI tests deselected).
   - Strict static typing passing Mypy (`mypy --config-file mypy.ini --strict src tests/batch tests/ipc tests/drivers`) with zero errors across 179 source files.
   - Ruff linting and code formatting clean across all engine Python source and test files.
   - Fail-closed clean working copy verification (`git diff --check`, `git diff --exit-code`, and clean porcelain status).

3. **Frontend Desktop Quality Gates (QA-02, RUN-08)**:
   - Exact Node.js 24.15.0 and pnpm 12.4.1 asserted fail-closed before package installation.
   - Dependencies locked to committed `pnpm-lock.yaml` via `pnpm install --frozen-lockfile`.
   - Full Vitest test suite (9 suites, 90 tests) passing with zero failures.
   - Strict ESLint checks passing with `--max-warnings=0`.
   - TypeScript static typechecking (`tsc -b --noEmit`) passing with 0 errors.
   - Prettier code style formatting verified without file mutations (`prettier --check .`).
   - Production Vite client build completed cleanly into `desktop/dist/`.
   - Clean working copy verified fail-closed.

4. **Rust & Tauri Windows Quality Gates (QA-02, QA-08, SEC-08)**:
   - Exact Rust/Cargo 1.95.0 toolchain installed via `rustup` with minimal profile, `rustfmt`, and `clippy`.
   - Pre-checkout configuration preserving native repository LF line endings on Windows runners.
   - Canonical formatting enforced with `cargo fmt --manifest-path desktop\src-tauri\Cargo.toml --all -- --check`.
   - Default test suite passing all 131 portable unit and integration tests (116 library unit tests, 10 `batch_lifecycle` tests, and 5 `generation_lifecycle` tests) with 8 live/staged tests explicitly reported as ignored.
   - Packaged-engine feature test suite passing all 131 tests with `--features packaged-engine`.
   - Strict Clippy linter passing with zero warnings in both default and packaged-engine configurations (`cargo clippy --locked --all-targets -- -D warnings`).
   - Production no-bundle desktop executable compiled cleanly via `pnpm --filter @cad-copilot/desktop tauri build --no-bundle` producing `cad-copilot-desktop.exe`.
   - Clean working copy verified fail-closed.

5. **Test-Harness Truthfulness & Native Gate Boundary (QA-08, SEC-07)**:
   - Live Solid Edge CAD integration tests in `desktop/src-tauri/tests/generation_lifecycle.rs` marked with `#[ignore = "Requires live Solid Edge runtime and CAD_COPILOT_LIVE_TESTS=1"]` and failing closed immediately if invoked directly without authorization.
   - Packaged engine lifecycle tests in `desktop/src-tauri/tests/packaged_engine_lifecycle.rs` marked with `#[ignore = "Requires staged engine payload at engine/dist/cad-copilot-engine/cad-copilot-engine.exe"]` and failing closed if invoked without the staged binary.
   - Hosted CI makes zero claims of live Solid Edge automation, live Gemini API integration, NSIS installer creation, or GUI observation.
   - All 8 M6.4 installed acceptance cases remain explicitly open until tested on a licensed Solid Edge workstation.
