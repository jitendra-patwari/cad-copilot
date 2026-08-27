# System Requirements Specification: Milestones 1, 2 & 3

**Project**: CAD Copilot  
**Scope**: Milestone 1 (Foundation & Domain Interfaces), Milestone 2 (Pure Domain Geometry Math), and Milestone 3 (Solid Edge COM Driver & Artifact Pipeline)
**Status**: Milestone 1 & 2 Active Baseline (Verified) / Milestone 3 Implementation Baseline (Planned)

---

## 1. Document Scope, Goals & Non-Goals

This specification defines the functional, architectural, and quality requirements for the foundational milestones of the CAD Copilot project. Subsequent milestones (Generation Application & LLM Adapter, High-Throughput Batch Automation, Tauri Desktop UI, and Advanced Parametric Capabilities) will be specified iteratively as development progresses.

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
- **Monorepo Workspaces**: Root workspace configured via `pnpm-workspace.yaml` declaring `desktop` and `engine`.
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
  - `contracts/schemas/edit/`: Parametric feature edit requests, session diff payloads, and rollback specifications (`edit-request.schema.json`, `edit-response.schema.json`).
- **Golden Runbooks & Fixtures**: Canonical feature plan DSL examples (`contracts/examples/`) covering standard mechanical parts (e.g., flange plates, brackets, spur gears) for contract validation.

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
  - AST parsing and lowering pipeline translating declarative JSON feature plans into ordered geometric primitives.
  - Mode-neutral parser recognizing clean operation, face, and axis aliases without spurious warnings, while explicitly tracking domain fallback diagnostics with original input values.
  - Multi-primitive composition and B-Rep boolean lifecycle state tracking with unified global identifier namespace and reserved prefix protection (`RESERVED_IDENTIFIER_PREFIX`).

### FR-5: Parametric Spur Gear Geometry (Milestone 2)
- **Deterministic Conceptual Spur Gear Outline (`gear_math.py` in `engine/src/geometry/`)**:
  - Analytical computation of deterministic conceptual 2D spur gear tooth outlines based on module ($m$), tooth count ($z$), pressure angle ($\alpha$), and root/tip bounds with 6-point-per-tooth polygon discretization.
  - Generates closed polyline vertex sequences representing the complete gear profile with optional axial center bore.
  - Automatic $+Z$ elevation sketch and cut frame generation aligned with gear face width and placement.

### FR-6: Spatial Boundary, Containment & Warning Propagation (Milestone 2)
- **Pre-Execution Boundary Checking (`engine/src/geometry/validators/`)**:
  - Universal polygon sanity validation ($3 \le N \le 512$, finite coordinates, non-duplicate vertices across all edges including closing edge $N-1 \to 0$, and non-zero Shoelace area $> 10^{-6}\text{ mm}^2$) across profile cutouts, revolved features, revolved shafts, and swept cross-sections.
  - Point-in-polygon containment checks verifying child features (holes, cutouts) are fully contained within parent face boundaries before CAD kernel dispatch.
  - Minimum edge clearance enforcement and sibling feature collision detection.
- **Warning & Fallback Diagnostic Propagation Protocol**:
  - **Parser Fallbacks**: When the parser encounters an unrecognized domain value, it emits a `ValidationDiagnostic(severity="warning", code=..., message=..., path=...)` and records a `DefaultApplied(path=..., value=..., reason=..., original_value=...)` preserving the raw unparsed input.
  - **Boundary Margin Relaxation**: When geometric fit or edge margins are relaxed under capability-first mode, validators emit a soft warning `ValidationDiagnostic(severity="warning", code="GATE_POLICY_RELAXED", message=..., path=...)` without mutating values.
  - **Gate Policy Modes**:
    - `capability_first`: Preserves warnings as soft non-fatal diagnostics, allowing valid geometry to proceed to execution.
    - `strict`: Hard-rejects any accumulated fallback or margin warnings with `FeaturePlanValidationError`.
  - **Lowering IR Propagation**: Lowered execution payloads carry accumulated `diagnostics` and `defaults_applied` within the top-level `metadata` dictionary.
  - **Execution Interface Exposure**: Domain response and failure models (`ExecutionSuccess`, `ExecutionFailure`) expose `warnings: list[dict[str, str]]` across driver and RPC boundaries.
- **STEP Sanity Verification (`step_checker.py` in `engine/src/geometry/`)**:
  - In-memory pure-domain structural envelope, topological entity presence (`MANIFOLD_SOLID_BREP`, `ADVANCED_FACE`), ISO 10303-41 length unit scale, and dimensional sanity validation on exported STEP Part 21 text.

### FR-7: Solid Edge COM Runtime Lifecycle (Milestone 3 — Planned)
- **COM Initialization & Dispatch**:
  - Single-Threaded Apartment (STA) COM runtime initialization (`SolidEdgeRuntime`) communicating with Siemens Solid Edge® via public Windows COM Dispatch interfaces (`win32com.client.Dispatch("SolidEdge.Application")`).
  - Dual attachment strategy: connects seamlessly to an active running Solid Edge instance (`GetActiveObject`) or starts a visible owned Solid Edge process when no active instance is present (hidden/background operation is deferred).
- **Runtime Diagnostics & Bounded Retries**:
  - Diagnostic reporting capturing Solid Edge version/build string (best-effort; unavailable metadata emits a warning without failing connection), process ID (PID), ownership status (`owned` vs `borrowed`), attachment mode, and live health check status.
  - Bounded busy-call retry strategy with backoff handling recognized retryable busy/rejected HRESULTs (`RPC_E_CALL_REJECTED` / `RPC_E_SERVERCALL_RETRYLATER`).
- **Document Lifecycle & Session Safety**:
  - Document management operating strictly via explicit document handles (`doc = documents.Add("SolidEdge.PartDocument")`) rather than ambiguous global `ActiveDocument` references.
  - Active synchronous/ordered part document creation and deterministic closing.
  - Ownership-aware teardown: safely closes request-owned documents without saving on execution failure, executes graceful shutdown of owned Solid Edge processes on exit, and restricts force termination strictly to the identified CAD Copilot-owned PID if graceful shutdown times out (guaranteeing borrowed user Solid Edge sessions and unrelated open documents are never terminated or closed).

### FR-8: Solid Edge Geometric Primitive Execution & Validation (Milestone 3 — Planned)
- **Base Body Creation**:
  - Implements `CADExecutorABC` via `SolidEdgeExecutor` consuming lowered feature plans from `engine/src/geometry/lowering.py`.
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
  - Executes active document model recompute (`Recompute()`) after each feature step and validates error-free status.
  - Inspects model topology to verify expected solid body count and enforce 3D manifold solid body classification (hard-rejecting non-manifold wire or sheet bodies).

### FR-9: Atomic Multi-Format Artifact Export & Pipeline Finalization (Milestone 3 — Planned)
- **Multi-Format Export**:
  - Exports native Solid Edge part (`.par`), standard exchange STEP (`.step` / `.stp`), and mesh polygon STL (`.stl`) artifacts.
  - Best-effort preview rendering generating thumbnail preview images (`.jpg`) without invalidating otherwise successful CAD solid models upon rendering issues.
- **Artifact Validation Integrity**:
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
| **NFR-6** | **Ownership-Safe Lifecycle & Cleanup** | CAD Copilot must deterministically manage CAD process lifecycles. Owned instances must attempt graceful shutdown first, with force termination permitted strictly for the recorded owned PID only after a bounded graceful timeout. Borrowed user instances must never be terminated or modified. |

---

## 4. Acceptance Criteria & Verification Matrix

### Milestone 1 Acceptance Criteria (Verified Baseline)
1. Root monorepo configuration (`pnpm-workspace.yaml`, `package.json`, `pyproject.toml`) is valid and discoverable.
2. `LICENSE` contains the standard MIT license and the Siemens trademark disclaimer.
3. All JSON Schema contracts in `contracts/schemas/` pass structural meta-validation.
4. `engine/src/interfaces/` exposes `CADRuntimeABC`, `CADExecutorABC`, and the exception hierarchy with complete type annotations.

### Milestone 2 Acceptance Criteria (Verified Baseline)
1. `FaceContext` accurately transforms 2D sketch points to 3D global coordinates across supported canonical faces with inverse projection error $< 10^{-6}\text{ mm}$ and exact 6-face origin offsets.
2. `gear_math.py` generates valid, closed tooth polygons for standard modules and tooth counts with automatic $+Z$ bore elevation frames.
3. Containment validators detect and reject out-of-bounds features with descriptive diagnostics.
4. Universal polygon profile sanity enforces $3 \le N \le 512$, finite coordinates, non-duplicate vertices (including closing edge $N-1 \to 0$), and non-zero Shoelace area $> 10^{-6}\text{ mm}^2$.
5. Composition validator enforces unified global identifier namespace and reserved system prefix protection.
6. `step_checker.py` performs in-place $O(1)$-memory streaming bounding box and entity smoke validation with ISO 10303-41 unit scale detection.
7. Pure geometry and contract test suite in `engine/tests/` passes with 100% success rate (365/365 tests passing at Milestone 2 completion).

### Milestone 3 Acceptance Criteria (Planned — Solid Edge COM Driver & Artifact Pipeline)
*(Planned acceptance criteria for subsequent live driver implementation)*

#### A. Automated Test Suite Verification (Offline & Fake COM)
1. Offline non-COM suite (`pytest -m "not com"`) continues passing 100% of tests (366+ tests baseline), verifying contracts, geometry math, lowering AST, and schema constraints without live CAD dependencies.
2. Driver lifecycle unit tests with mock/fake COM dispatch verify state transitions, explicit document handle tracking, bounded busy-call retries, and PID ownership classification logic.
3. Artifact staging and validation unit tests verify atomic move semantics, `.par`/STEP/STL format verification, and non-fatal JPG warning handling.

#### B. Mandatory Live Solid Edge COM Verification Gates (Windows Workstation)
1. **Live Runtime Connection & Lifecycle Gate**:
   - `SolidEdgeRuntime` connects to an active running Solid Edge instance (`borrowed` mode) or successfully launches a visible owned instance (`owned` mode).
   - Reports accurate diagnostic metadata (version string, PID, mode, health status).
   - Gracefully closes test part documents and tears down owned processes without leaving orphaned `Edge.exe` processes or affecting borrowed sessions.
2. **Live Geometric Execution Gate**:
   - `SolidEdgeExecutor` successfully builds confirmed base bodies (`rectangular_prism`, `cylinder`, `spur_gear`) and executes localized planar features (`circular_through_hole`, `rectangular_through_cutout`, `slot_through_cutout`, `rectangular_extruded_pad`).
   - Verifies model health via post-feature `Recompute()` inspection and confirms 3D manifold solid body status (rejecting sheet/wire bodies).
3. **Live Artifact Export & Validation Gate**:
   - Export pipeline atomically writes and validates native `.par`, STEP, and STL files to the designated job directory in `CAD_OUTPUT_ROOT`.
   - Exported STEP files pass `step_checker.py` validation.
   - Exported STL files pass triangle count and structural validation.
   - Preview JPG rendering captures valid image or cleanly removes partial output while emitting a warning without failing CAD solid output.
   - Unsafe destination paths attempting directory traversal outside `CAD_OUTPUT_ROOT` are safely rejected with `OUTPUT_PATH_NOT_ALLOWED`.
4. **Live Induced Failure & Cleanup Gates**:
   - *Induced Construction Failure*: Injecting a controlled Solid Edge construction failure after validation succeeds and a request-owned document has been opened verifies that the active request-owned document is cleanly closed without saving, no corrupt target artifacts are finalized in `CAD_OUTPUT_ROOT`, and the session remains healthy for subsequent requests.
   - *Induced Export Failure*: Simulating an export failure (e.g. read-only target path or artificial export fault) verifies that temporary staging files are removed, no incomplete artifacts remain in target destination paths, and an accurate `ARTIFACT_EXPORT_FAILED` failure response is returned.
