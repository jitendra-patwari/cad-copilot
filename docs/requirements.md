# System Requirements Specification: Milestones 1 & 2

**Project**: CAD Copilot  
**Scope**: Milestone 1 (Foundation & Domain Interfaces) & Milestone 2 (Pure Domain Geometry Math)  
**Status**: Active / Iterative Agile Baseline  

---

## 1. Document Scope, Goals & Non-Goals

This specification defines the functional, architectural, and quality requirements for the first two milestones of the CAD Copilot project. Subsequent milestones will be specified iteratively as development progresses.

### Goals
- Establish monorepo workspace configuration (`desktop/` and `engine/`), developer tooling, and MIT licensing with an explicit trademark notice.
- Define vendor-agnostic abstract domain interfaces (`engine/src/interfaces/`) and canonical JSON Schemas (`contracts/`) for cross-boundary communication.
- Implement a pure, deterministic geometry domain (`engine/src/geometry/`) containing planar coordinate transformations, parametric spur gear math, feature plan AST models, and spatial containment validation.

### Explicit Non-Goals
- **No CAD Kernel Bindings**: No live Windows COM or Solid Edge API connections in this phase.
- **No Network or AI Calls**: Zero network requests, LLM provider SDK invocations, or external API dependencies in this phase.
- **No GUI / Client Dependencies**: No Tauri, Rust native bindings, or React UI dependencies in this phase.

---

## 2. Functional Requirements

### FR-1: Monorepo Foundation & Governance (Milestone 1)
- **Monorepo Workspaces**: Root workspace configured via `pnpm-workspace.yaml` declaring `desktop` and `engine`.
- **Python Configuration**: Base engine managed via PEP 508 / PEP 621 compliant `engine/pyproject.toml` targeting Python `>=3.11` with platform-specific markers (`pywin32>=306; sys_platform == 'win32'`).
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
  - Bidirectional coordinate mapping between 2D sketch plane coordinates $(u, v)$ on arbitrary 3D planar faces and global Cartesian $(x, y, z)$ space.
  - Handles planar origin offsets, face normal vectors, and orthonormal reference axes for sketch plane alignment.
- **Feature Plan AST & Domain Models**:
  - Strongly typed domain models for parametric feature primitives (`BaseExtrusion`, `Cutout`, `Hole`, `Revolve`, `Fillet`, `Chamfer`).
  - AST parsing and lowering pipeline translating declarative JSON/YAML feature plans into ordered geometric primitives.

### FR-5: Parametric Spur Gear Geometry (Milestone 2)
- **Involute Profile Generation (`gear_math.py` in `engine/src/geometry/`)**:
  - Analytical computation of standard 2D involute gear tooth profiles based on module ($m$), tooth count ($z$), pressure angle ($\alpha$), and bore parameters.
  - Generates closed polyline vertex sequences representing the complete gear profile with optional keyed center bore.

### FR-6: Spatial Boundary & Containment Validation (Milestone 2)
- **Pre-Execution Boundary Checking (`engine/src/geometry/validators/`)**:
  - Point-in-polygon checks verifying that child features (holes, cutouts) are fully contained within parent face boundaries before CAD kernel dispatch.
  - Minimum edge clearance enforcement and sibling feature collision detection.
- **STEP Sanity Verification (`step_checker.py` in `engine/src/geometry/`)**:
  - In-memory pure-domain structural envelope, topological entity presence (`MANIFOLD_SOLID_BREP`, `ADVANCED_FACE`), ISO 10303-41 length unit scale, and dimensional sanity validation on exported STEP Part 21 text.

---

## 3. Non-Functional Requirements (NFRs)

| ID | Requirement | Specification & Boundary |
| :--- | :--- | :--- |
| **NFR-1** | **Zero I/O Purity** | `engine/src/geometry/` must be 100% pure computational logic with zero disk I/O, zero network calls, and zero CAD runtime imports. |
| **NFR-2** | **Mathematical Determinism** | Geometric transformations and profile generators must produce identical floating-point output across runs within a $10^{-4}\text{ mm}$ ($0.1\ \mu\text{m}$) tolerance. |
| **NFR-3** | **Clean-Room Compliance** | Zero proprietary Siemens binary files, DLLs, typelibs, or copyrighted headers. Automation relies solely on open interfaces and standard public Dispatch. |
| **NFR-4** | **Strict Static Typing** | Code in `engine/src/interfaces/` and `engine/src/geometry/` must pass `mypy --strict` with zero type errors and pass Ruff lint checks. |

---

## 4. Acceptance Criteria & Verification Matrix

### Milestone 1 Acceptance Criteria
1. Root monorepo configuration (`pnpm-workspace.yaml`, `package.json`, `pyproject.toml`) is valid and discoverable.
2. `LICENSE` contains the standard MIT license and the Siemens trademark disclaimer.
3. All JSON Schema contracts in `contracts/schemas/` pass structural meta-validation.
4. `engine/src/interfaces/` exposes `CADRuntimeABC`, `CADExecutorABC`, and the exception hierarchy with complete type annotations.

### Milestone 2 Acceptance Criteria
1. `FaceContext` accurately transforms 2D sketch points to 3D global coordinates across arbitrary planes with inverse projection error $< 10^{-6}\text{ mm}$.
2. `gear_math.py` generates valid, closed tooth polygons for standard modules and tooth counts.
3. Containment validators detect and reject out-of-bounds features with descriptive diagnostics.
4. `step_checker.py` performs in-place $O(1)$-memory streaming bounding box and entity smoke validation with ISO 10303-41 unit scale detection.
5. Test suite in `engine/tests/geometry/` (unit, regression, fuzzing, schema validation) passes with 100% success rate.

### Milestone 3 Acceptance Criteria (Solid Edge Driver)
1. `SolidEdgeRuntime` connects to active or fresh Solid Edge instances via standard Dispatch in STA single-threaded apartment mode.
2. `SolidEdgeExecutor` verifies successful geometric recompute before reporting feature success.
3. Driver verifies that active documents contain expected solid body counts and that bodies are 3D manifold solid bodies (not sheet/wire bodies).
4. Exported STEP files are written atomically and pass `step_checker.py` sanity checks before export artifact records are finalized.
