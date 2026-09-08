# Contributing to CAD Copilot

Thank you for your interest in contributing to **CAD Copilot**! We welcome bug reports, feature requests, documentation improvements, and code contributions.

---

## Code of Conduct

We are committed to providing a welcoming, inclusive, and harassment-free environment for all contributors. Please be respectful and constructive in all communications.

---

## Development Setup

### Prerequisites
- **Python**: `>=3.14.3,<3.15` (tested baseline: Python 3.14.3)
- **Node.js / pnpm**: Node.js `>=20` and pnpm `>=9` for workspace scripts; the desktop package and frontend dependencies are not yet implemented.
- **Rust**: Not required for the current Python/runtime baseline. Tauri/Rust setup belongs to the later desktop milestone.
- **Operating System**: Windows 10/11 x64 with licensed Siemens Solid Edge® for live COM automation. Pure geometry and contract checks do not require COM; some offline driver tests use Windows process APIs. Exclude live tests explicitly with `-m "not com and not live_ai"`.

M3.1–M3.3, M4.1 generation service orchestration, M4.2 Gemini plan proposal adapter, M4.3 deterministic example catalog, M4.4 canonical run manifest publication, and M4.5 strict generation stdio IPC are implemented and verified; Milestone 4 is complete. M5 batch execution and M6 desktop UI remain planned. See [engine status](engine/README.md).

### Initializing the Workspace
```powershell
# Clone the repository
git clone https://github.com/jitendra-patwari/cad-copilot.git
cd cad-copilot

# Install the current workspace lockfile (no implemented frontend package yet)
pnpm install --frozen-lockfile

# Create and activate a Python virtual environment
python -m venv .venv
.venv\Scripts\activate

# Install Python engine developer tooling (standard offline installation)
pip install -e "engine[dev]"

# Optional: install the Gemini extra for AI plan proposal adapter development
pip install -e "engine[dev,gemini]"
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

Scopes include planned domains; listing a scope does not mean its package or runtime is implemented.
* `root`: Monorepo root workspace, pnpm, and top-level configs
* `desktop`: Tauri v2 host and React 19 frontend UI
* `engine`: Core Python engine workspace and tooling
* `interfaces`: Abstract domain ports (`CADExecutorABC`, `CADRuntimeABC`)
* `geometry`: Pure deterministic geometry math, UV transforms, spur gear algorithms
* `drivers`: Solid Edge COM automation driver
* `ai`: Foundation model adapter and geometry plan generator
* `ipc`: One-request/one-response JSON stdio boundary for the desktop bridge (no REST or JSON-RPC server)
* `application`: Generation application orchestration service (`GenerationService`)
* `batch`: Sequential local native-file export and drawing publication
* `contracts`: Public JSON-Schema contracts and golden fixtures

### Requirements Traceability (`<FR-ID>`)
Every commit must cite the corresponding **Functional Requirement ID** from [`docs/requirements.md`](docs/requirements.md) (e.g. `[FR-1]`, `[FR-2]`, `[FR-3]`, `[FR-9]`, `[FR-10]`). For infrastructure or root maintenance, use `[FR-1]` or `[INFRA]`.

### Examples
* `chore(root): [FR-1] initialize root workspace configuration, pnpm toolchains and dev tooling`
* `legal(license): [FR-1] add MIT license with Siemens trademark disclaimer, security policies and requirements spec`
* `feat(contracts): [FR-2] define public JSON-Schema contracts and golden runbooks`
* `feat(interfaces): [FR-3] implement abstract domain interfaces (CADExecutorABC, CADRuntimeABC) in engine`
* `fix(geometry): [FR-4] resolve floating point precision boundary in FaceContext UV projection`
* `feat(application): [FR-10] implement generation application orchestration`

---

## Code Quality Standards

All submitted code must pass strict static analysis and testing before review:

```powershell
# Python Linting & Type Checking (from repo root)
ruff check engine/src engine/tests
mypy --config-file engine/mypy.ini --strict engine/src

# Python Unit & Contract Tests (offline, non-COM and non-live-AI)
pytest engine/tests -m "not com and not live_ai"

# Workspace Build Gate (runs only scripts that currently exist)
pnpm run build
```

A successful recursive build with `--if-present` is not evidence of a desktop build while `desktop/` is absent. Offline checks are not live COM or AI provider evidence. Run `com`-marked and `live_ai`-marked tests only with explicit authorization, valid credentials, and a suitable Windows/Solid Edge session; markers alone do not exclude them from a default pytest invocation without explicit `-m` selection.

---

## Clean-Room, Licensing & Intellectual Property Guidelines

1. **User-Furnished Solid Edge® License**:
   * CAD Copilot does **not** provide, bypass, crack, or redistribute Siemens software licenses.
   * Live CAD execution requires the user to have a valid, legally acquired license for Siemens Solid Edge® installed locally on their Windows workstation.
   * Pure geometry, AST lowering, and contract verification do not require COM or live AI providers. Use `-m "not com and not live_ai"` for offline selection; this prevents accidental provider execution in opted-in environments and does not make Windows-specific driver/process tests portable.

2. **No Proprietary Binaries**:
   * Do not commit Siemens binary files (`*.par`, `*.psm`, `*.asm`, `*.dft`), typelib headers (`*.tlb`), or SDK DLLs.

3. **Public COM Dispatch Only (Clean-Room Boundary)**:
   * All CAD automation operates strictly via standard, public Windows Component Object Model (COM) Dispatch interfaces (`pywin32` / `win32com.client.Dispatch("SolidEdge.Application")`).
   * This is the standard public automation mechanism provided by Siemens for developer scripting (identical to official Excel/Word COM automation). We do not reverse engineer, decompile, or hook internal undocumented binaries.

4. **Trademark Notice**:
   * *Solid Edge® is a registered trademark of Siemens Industry Software Inc.*
   * CAD Copilot is an independent open-source tool and is not affiliated with, endorsed by, or sponsored by Siemens. All references to Solid Edge® must respect this trademark ownership.
