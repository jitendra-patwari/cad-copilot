# System Requirements Specification: Milestones 1, 2, 3, 4 & 5

**Project**: CAD Copilot  
**Scope**: Milestone 1 (Foundation & Domain Interfaces), Milestone 2 (Pure Domain Geometry Math), Milestone 3 (Solid Edge COM Driver & Artifact Pipeline), Milestone 4 (Generation Application Orchestration, Examples, Manifest & Stdio IPC), and Milestone 5 (Batch Automation Contracts, Execution Infrastructure, Safety Boundary & Sequential Processing)
**Status (9 September 2026)**: M1/M2 domain baseline and M3.0 reconciliation implemented; M3.1 runtime/lifecycle, M3.2 primitive execution and inspection, and M3.3 multi-format artifact export and pipeline finalization implemented, hardened, and verified. Milestone 4 is fully implemented, hardened, and verified across all components: M4.1 (generation application orchestration), M4.2 (optional text-only Gemini proposal adapter), M4.3 (deterministic example catalog), M4.4 (canonical run manifest and sidecar publication), and M4.5 (strict generation stdio IPC, launchers, and packaging). Milestone 4 is complete. Milestone 5.1 (canonical batch request/response/manifest contracts, schemas, typed models, and operation metadata registry) and Milestone 5.2 (batch execution infrastructure, typed bindings, pure work allocation, tracked-document task generalization, observable teardown, and shared sequential batch service orchestration) are implemented, hardened, and verified under `contracts/schemas/batch/` and `engine/src/batch/`. Filesystem safety boundary (M5.3) and manifest publication (M5.6) remain planned.

### Current evidence boundary

- **Offline test suite**: 1,764 tests passed, 2 skipped, with 40 tests deselected (36 COM driver tests, 2 COM live IPC tests, 1 live_ai provider test, 1 live_ai IPC test); the focused runtime lifecycle/security suite passed all 73 tests; strict mypy passed across the verified component scope (138 source files checked: `mypy --config-file mypy.ini --strict src tests/manifests tests/artifacts tests/application tests/example_catalog tests/plan_providers tests/ipc tests/batch tests/contracts/test_schema_contracts.py tests/contracts/test_batch_schema_contracts.py tests/test_interfaces.py tests/drivers/test_executor.py tests/drivers/test_runtime_document_task.py tests/drivers/test_runtime_lifecycle.py tests/drivers/test_ownership_teardown_safety.py tests/drivers/test_error_sanitization.py tests/drivers/test_solidedge_live.py`); Ruff lint passed and formatting clean; `git diff --check` clean.
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

This specification defines the functional, architectural, and quality requirements for the foundational milestones of the CAD Copilot project. Milestone 5.1 establishes canonical batch contracts, schemas, typed models, Draft 2020-12 validation, and operation metadata foundation. Subsequent milestones cover bounded sequential native-file batch runtime execution (Milestone 5.2+) and the Tauri desktop showcase (Milestone 6). Only approved conditional/follow-up capabilities may extend that scope; no later package is implied to exist by this specification.

### Goals
- **Milestone 1 (Foundation & Governance)**: Establish monorepo workspace configuration (`desktop/` and `engine/`), developer tooling, MIT licensing with an explicit Siemens trademark notice, vendor-agnostic abstract domain interfaces (`engine/src/interfaces/`), and canonical JSON Schemas (`contracts/`) for cross-boundary communication.
- **Milestone 2 (Pure Domain Geometry)**: Implement a pure, deterministic geometry domain (`engine/src/geometry/`) containing planar coordinate transformations, parametric spur gear tooth math, feature plan AST parsing, lowering models, spatial containment validation, and STEP Part 21 smoke analysis.
- **Milestone 3 (Solid Edge COM Driver & Artifacts)**: Implement the Windows Solid Edge® COM automation driver (`SolidEdgeRuntime` and `SolidEdgeExecutor` in `engine/src/drivers/solidedge/`) via standard public COM Dispatch, executing confirmed 3D base primitives, localized 2D cutouts, geometric recompute verification, and atomic multi-format artifact exports (`.par`, STEP, STL, preview JPG) on live Windows workstations.
- **Milestone 4 (Generation Orchestration & Workflows — Complete)**: Orchestrate single-request 3D parametric generation through a modular application coordinator (`GenerationService`), supporting deterministic example plans (`example_catalog`) and an optional text-only Gemini proposal adapter (`plan_providers`), with canonical run manifest creation (`manifests`), safe error/warning projection, and strict stdio IPC (`ipc`). M4.1 (application orchestration), M4.2 (Gemini adapter), M4.3 (deterministic example catalog), M4.4 (run manifest publication), and M4.5 (strict generation stdio IPC) are fully implemented, hardened, and verified. Milestone 4 is complete.

### Explicit Non-Goals
- **No Network or AI Calls in Engine Driver (Milestone 3)**: Zero network requests or LLM SDK dependencies within the core driver (LLM generation pipelines and prompt-to-CAD translation are encapsulated in Milestone 4).
- **No GUI / Client Dependencies (Milestone 3)**: No Tauri, Rust native bindings, or React UI dependencies in this engine driver phase (Milestone 6).
- **No Proprietary Siemens Binary SDK / DLL Linkage**: Automation operates strictly through standard, public Windows COM Dispatch interfaces (`pywin32` / `win32com.client.Dispatch("SolidEdge.Application")`). Zero internal DLL reverse-engineering, decompilation, or undocumented binary hooking.
- **No Out-of-Scope / Deferred Operations**: Image-to-CAD, stateful prompt-edit sessions, manufacturing-grade gear analysis, and unverified batch formats remain strictly excluded from Milestone 3.

---

## 2. Functional Requirements

### FR-1: Monorepo Foundation & Governance (Milestone 1)
- **Monorepo Workspaces**: Root workspace configured via `pnpm-workspace.yaml` declaring `desktop` and `engine`; only `engine/` is implemented in the current baseline.
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
- **Runbooks & Fixtures**: Internal feature-plan examples (`contracts/examples/`) cover plates, conceptual gears, and conditional composition/sweep representations. JSON fixture integrity and public schema conformance tests do not establish end-to-end or live CAD support; the generation runbook describes the implemented M4.5 stdio IPC launcher, whereas `contracts/schemas/batch/README.md` serves as the canonical contract guide for the batch interface (M5.1 contracts, schemas, typed models, and metadata registry, and M5.2 sequential batch execution infrastructure implemented; M5.3+ filesystem safety boundary, manifest publication, and desktop client integration remain planned).

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

### FR-7: Solid Edge COM Runtime Lifecycle (M3.1 — Implemented; Live Evidence Limited)

The implementation is in `engine/src/drivers/solidedge/`. The requirements below remain acceptance criteria; see the evidence boundary above for what has actually been reported.
- **COM Initialization & Dispatch**:
  - `SolidEdgeRuntime` marshals COM operations to a dedicated STA worker with message pumping and worker-private native objects; callers receive opaque application/document handles.
  - Attach to a responsive existing instance as borrowed. A spawn path is entered only for `MK_E_UNAVAILABLE`; owned classification requires PID/creation-time proof. Unproven ownership remains `unknown`. Only verified owned instances receive `Visible=True` / `DisplayAlerts=False` writes.
- **Runtime Diagnostics & Bounded Retries**:
  - Required diagnostics capture version/build (best effort), PID when available, ownership (`owned`, `borrowed`, or `unknown`), attachment mode, visibility, and health. Unavailable version metadata must not fail an otherwise usable connection.
  - Diagnostic warnings include `VERSION_METADATA_UNAVAILABLE` when version metadata is absent or unreadable, preserving healthy connections without failing runtime attachment.
  - Bounded busy-call retry strategy with backoff handling recognized retryable busy/rejected HRESULTs (`RPC_E_CALL_REJECTED` / `RPC_E_SERVERCALL_RETRYLATER`).
- **Document Lifecycle & Session Safety**:
  - Document management operating strictly via explicit document handles (`doc = documents.Add("SolidEdge.PartDocument")`) rather than ambiguous global `ActiveDocument` references.
  - New part documents target Ordered mode (`ModelingModeConstants`: Synchronous `1`, Ordered `2`). Tracking is registered immediately after creation; setting `ModelingMode=2` is followed by immediate readback verification. Mode setup failure/mismatch triggers `Close(False)`; tracking is removed only after successful immediate closure and is retained for teardown retry if closure fails. Existing opened files must not have their mode changed.
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

### FR-16: Batch Filesystem & Source-Integrity Safety Boundary (Milestone 5.3 — In Progress)
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
