# System Requirements Specification: Milestones 1, 2 & 3

**Project**: CAD Copilot  
**Scope**: Milestone 1 (Foundation & Domain Interfaces), Milestone 2 (Pure Domain Geometry Math), and Milestone 3 (Solid Edge COM Driver & Artifact Pipeline)
**Status (1 September 2026)**: M1/M2 domain baseline and M3.0 reconciliation implemented; M3.1 runtime/lifecycle and M3.2 primitive execution, 2D cutouts, +Z pads, recompute, and authoritative topology/property inspection implemented and verified; M3.3 multi-format artifact export remains planned.

### Current evidence boundary

- **Offline test suite**: 492 tests passed with 19 COM tests deselected; strict mypy passed (28 source files); Ruff lint passed and formatting clean (71 files); `git diff --check` clean.
- **Live integration evidence**: 19 passing COM tests on a licensed Siemens Solid Edge 2024 session (`226.00.00.106`), verifying:
  1. M3.1 lifecycle isolation, Ordered mode readback `2`, and non-destructive process preservation.
  2. M3.2 3D primitives (cuboid, cylinder, 24-tooth conceptual spur gear at origin and translated with centered through-bore).
  3. Characterized 6-face circular through cutouts, localized +Z cuts (circle, polygon, slot through/blind), and +Z rectangular extruded pads with analytic volume delta verification.
  4. Ordered sequential feature execution with stable reference tracking and 1-solid topology inspection.
  5. Controlled preflight rejection plus a native non-intersecting cut refusal, prompt-free failed-document closure, and a successful subsequent request in the same borrowed application.
  6. Preservation of an independently tracked unrelated Part document and process under borrowed teardown, plus graceful owned startup/shutdown without force cleanup.

---

## 1. Document Scope, Goals & Non-Goals

This specification defines the functional, architectural, and quality requirements for the foundational milestones of the CAD Copilot project. Later milestones cover generation orchestration and optional Gemini, bounded sequential native-file batch operations, and the Tauri desktop showcase. Only approved conditional/follow-up capabilities may extend that scope; no later package is implied to exist by this specification.

### Goals
- **Milestone 1 (Foundation & Governance)**: Establish monorepo workspace configuration (`desktop/` and `engine/`), developer tooling, MIT licensing with an explicit Siemens trademark notice, vendor-agnostic abstract domain interfaces (`engine/src/interfaces/`), and canonical JSON Schemas (`contracts/`) for cross-boundary communication.
- **Milestone 2 (Pure Domain Geometry)**: Implement a pure, deterministic geometry domain (`engine/src/geometry/`) containing planar coordinate transformations, parametric spur gear tooth math, feature plan AST parsing, lowering models, spatial containment validation, and STEP Part 21 smoke analysis.
- **Milestone 3 (Solid Edge COM Driver & Artifacts)**: Implement the Windows Solid Edge® COM automation driver (`SolidEdgeRuntime` and `SolidEdgeExecutor` in `engine/src/drivers/solidedge/`) via standard public COM Dispatch, executing confirmed 3D base primitives, localized 2D cutouts, geometric recompute verification, and atomic multi-format artifact exports (`.par`, STEP, STL, preview JPG) on live Windows workstations.

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
  - `contracts/schemas/batch/`: Batch task definitions, export configurations, and summary schemas (`batch-request.schema.json`, `batch-response.schema.json`).
  - `contracts/schemas/edit/`: Historical, non-runtime edit schemas (`edit-request.schema.json`, `edit-response.schema.json`). Their legacy sessions, images, and selectable outputs are not initial capabilities. Future M7B editing is constrained by a manifest-based revision model, not a session database or rollback system.
- **Runbooks & Fixtures**: Internal feature-plan examples (`contracts/examples/`) cover plates, conceptual gears, and conditional composition/sweep representations. JSON fixture integrity and public schema conformance tests do not establish end-to-end or live CAD support; generation and batch runbooks describe future launchers.

### FR-3: Abstract Domain Interfaces (Milestone 1)
- **CAD Runtime Port (`CADRuntimeABC` in `engine/src/interfaces/`)**:
  - Abstract interface defining the lifecycle contract for CAD processes: application connection, document opening/closing, and graceful process teardown.
- **CAD Execution Port (`CADExecutorABC` in `engine/src/interfaces/`)**:
  - Abstract interface defining modeling operations: solid body creation, localized cutouts, artifact exports (STEP, preview images), and inspection of physical properties (volume, mass, feature count).
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
  - Analytical computation of deterministic conceptual 2D spur gear tooth outlines based on module ($m$), tooth count ($z$), pressure angle ($\alpha$), and root/tip bounds with 6-point-per-tooth polygon discretization.
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
  - **Known evidence/implementation limit:** The approved diagnostic policy calls for a warning when version metadata is unavailable. Current code returns `version_build=None` without populating that warning; do not claim the warning path is implemented.
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

### FR-9: Atomic Multi-Format Artifact Export & Pipeline Finalization (M3.3 — Planned)
- **Multi-Format Export**:
  - Exports native Solid Edge part (`.par`), standard exchange STEP (`.step` / `.stp`), and mesh polygon STL (`.stl`) artifacts.
  - Best-effort preview rendering generating thumbnail preview images (`.jpg`) without invalidating otherwise successful CAD solid models upon rendering issues.
  - Native `.par` validation: file must exist, be non-empty, and pass authoritative solid body inspection (positive volume, verified feature tree).
  - STEP Part 21 validation: in-memory streaming verification via `step_checker.py` ensuring non-empty topology (`MANIFOLD_SOLID_BREP`), ISO 10303-41 length unit scale detection, and valid spatial bounds.
  - STL validation: structure check confirming recognizable ASCII or binary header (80-byte header + uint32 triangle count) with at least one valid triangle ($\ge 1$).
  - JPG preview capture: failed image capture removes any partial or corrupt temporary JPG output (regardless of size), logs a non-fatal warning diagnostic, and does not invalidate the CAD solid result.
  - Required artifact failures: any failure to generate or validate required `.par`, STEP, or STL files is fatal to the generation request (`ARTIFACT_EXPORT_FAILED`).
- **Atomic Staging & Path Safety**:
  - Writes artifacts to isolated temporary staging paths before atomically moving them to the designated final destination within `CAD_OUTPUT_ROOT`.
  - Rejects unsafe output paths attempting directory traversal outside the configured output root (`OUTPUT_PATH_NOT_ALLOWED`).

---

## 3. Non-Functional Requirements (NFRs)

| ID | Requirement | Specification & Boundary |
| :--- | :--- | :--- |
| **NFR-1** | **Zero I/O Purity** | `engine/src/geometry/` must be 100% pure computational logic with zero disk I/O, zero network calls, and zero CAD runtime imports. |
| **NFR-2** | **Mathematical Determinism** | Geometric transformations and profile generators must produce identical floating-point output across runs within a $10^{-4}\text{ mm}$ ($0.1\ \mu\text{m}$) tolerance. |
| **NFR-3** | **Clean-Room Compliance** | Zero proprietary Siemens binary files, DLLs, typelibs, or copyrighted headers. Automation relies solely on open interfaces and standard public Dispatch. |
| **NFR-4** | **Strict Static Typing** | Code in `engine/src/interfaces/`, `engine/src/geometry/`, and `engine/src/drivers/` must pass `mypy --strict` with zero type errors and pass Ruff lint checks. |
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

### Milestone 3 Acceptance Criteria (M3.1 & M3.2 Implemented; M3.3 Planned)

These are milestone-wide acceptance gates. M3.1 lifecycle and M3.2 geometric primitive execution are verified; M3.3 multi-format artifact export remains planned.

#### A. Automated Test Suite Verification (Offline & Fake COM)
1. Offline non-COM checks (`pytest -m "not com"`) continue passing. Historical M3.0 evidence was 366 tests; the latest supplied M3.1 report is 416 passed with four COM tests deselected. These counts are dated evidence, not a required fixed suite size.
2. Driver lifecycle unit tests with mock/fake COM dispatch verify state transitions, explicit document handle tracking, bounded busy-call retries, and PID ownership classification logic.
3. Artifact staging and validation unit tests verify atomic move semantics, `.par`/STEP/STL format verification, and non-fatal JPG warning handling.

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
   - *Induced Export Failure*: Simulating an export failure (e.g. read-only target path or artificial export fault) verifies that temporary staging files are removed, no incomplete artifacts remain in target destination paths, and an accurate `ARTIFACT_EXPORT_FAILED` failure response is returned.
