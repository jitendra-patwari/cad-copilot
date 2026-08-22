# Contributing to CAD Copilot

Thank you for your interest in contributing to **CAD Copilot**! We welcome bug reports, feature requests, documentation improvements, and code contributions.

---

## Code of Conduct

We are committed to providing a welcoming, inclusive, and harassment-free environment for all contributors. Please be respectful and constructive in all communications.

---

## Development Setup

### Prerequisites
- **Python**: `>=3.11` (Python 3.11, 3.12, or 3.13)
- **Node.js**: `>=20` (Node 20 or 22 LTS)
- **pnpm**: `>=9`
- **Rust**: `>=1.78` (with `cargo` for Tauri v2 desktop application builds)
- **Siemens Solid Edge®**: (Optional — local development and testing on macOS/Linux can use `CAD_MOCK_MODE=1` without Solid Edge)

### Initializing the Workspace
```bash
# Clone the repository
git clone https://github.com/jitendra-patwari/cad-copilot.git
cd cad-copilot

# Install Node/TypeScript frontend dependencies
pnpm install

# Install Python engine developer tooling (in editable mode)
pip install -e "engine[dev]"
```

---

## Commit Message Conventions

We enforce the **Conventional Commits** standard combined with **Functional Requirements Traceability**:

```text
<type>(<scope>): [<FR-ID>] <short summary>

[optional body describing technical decisions or rationale]

[optional footer]
```

### Allowed Types
* `feat`: A new feature, interface, schema, or functional capability
* `fix`: A bug fix or regression correction
* `docs`: Documentation updates or requirements specifications
* `style`: Formatting changes (whitespace, linting) with no code behavioral alterations
* `refactor`: Code refactoring without behavioral alterations
* `test`: Adding or correcting unit/regression tests
* `chore`: Workspace maintenance, build configuration, or dependency upgrades
* `legal`: Licensing, trademark notices, and governance policies
* `ci`: GitHub Actions or CI/CD workflow changes

### Standard Scopes (Domain Architecture)
* `root`: Monorepo root workspace, pnpm, and top-level configs
* `desktop`: Tauri v2 host and React 19 frontend UI
* `engine`: Core Python engine workspace and tooling
* `interfaces`: Abstract domain ports (`CADExecutorABC`, `CADRuntimeABC`)
* `geometry`: Pure deterministic geometry math, UV transforms, spur gear algorithms
* `drivers`: Solid Edge COM automation and offline mock drivers
* `session`: Semantic part state store and revision timeline
* `editor`: Natural language parametric edit pipeline
* `ai`: Foundation model pipelines (Gemini/OpenAI), vision QA evaluators
* `ipc`: Stdio JSON-RPC protocol server for desktop bridge
* `batch`: High-throughput batch processing and DXF flattening
* `api`: Local FastAPI REST service
* `contracts`: Public JSON-Schema contracts and golden plan fixtures

### Requirements Traceability (`<FR-ID>`)
Every commit must cite the corresponding **Functional Requirement ID** from [`docs/requirements.md`](docs/requirements.md) (e.g. `[FR-1]`, `[FR-2]`, `[FR-3]`, `[FR-4]`, `[FR-5]`, `[FR-6]`). For infrastructure or root maintenance, use `[FR-1]` or `[INFRA]`.

### Examples
* `chore(root): [FR-1] initialize root workspace configuration, pnpm toolchains and dev tooling`
* `legal(license): [FR-1] add MIT license with Siemens trademark disclaimer, security policies and requirements spec`
* `feat(contracts): [FR-2] define public JSON-Schema contracts and golden runbooks`
* `feat(interfaces): [FR-3] implement abstract domain interfaces (CADExecutorABC, CADRuntimeABC) in engine`
* `fix(geometry): [FR-4] resolve floating point precision boundary in FaceContext UV projection`

---

## Code Quality Standards

All submitted code must pass strict static analysis and testing before review:

```bash
# Python Linting & Type Checking (from repo root)
ruff check engine
mypy --strict engine/src

# Python Unit & Contract Tests (from repo root)
pytest engine

# Frontend Formatting & Unit Tests (from repo root)
pnpm -r test
```

---

## Clean-Room, Licensing & Intellectual Property Guidelines

1. **User-Furnished Solid Edge® License**:
   * CAD Copilot does **not** provide, bypass, crack, or redistribute Siemens software licenses.
   * Live CAD execution requires the user to have a valid, legally acquired license for Siemens Solid Edge® installed locally on their Windows workstation.
   * Contributors on macOS, Linux, or systems without Solid Edge must develop against `CAD_MOCK_MODE=1` (offline simulation mode).

2. **No Proprietary Binaries**:
   * Do not commit Siemens binary files (`*.par`, `*.psm`, `*.asm`, `*.dft`), typelib headers (`*.tlb`), or SDK DLLs.

3. **Public COM Dispatch Only (Clean-Room Boundary)**:
   * All CAD automation operates strictly via standard, public Windows Component Object Model (COM) Dispatch interfaces (`pywin32` / `win32com.client.Dispatch("SolidEdge.Application")`).
   * This is the standard public automation mechanism provided by Siemens for developer scripting (identical to official Excel/Word COM automation). We do not reverse engineer, decompile, or hook internal undocumented binaries.

4. **Trademark Notice**:
   * *Solid Edge® is a registered trademark of Siemens Industry Software Inc.*
   * CAD Copilot is an independent open-source tool and is not affiliated with, endorsed by, or sponsored by Siemens. All references to Solid Edge® must respect this trademark ownership.
