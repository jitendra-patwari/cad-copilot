# Contributing to CAD Copilot

Thank you for your interest in contributing to **CAD Copilot**! We welcome bug reports, feature requests, documentation improvements, and code contributions.

---

## Code of Conduct

We are committed to providing a welcoming, inclusive, and harassment-free environment for all contributors. Please be respectful and constructive in all communications.

---

## Development Setup

### Prerequisites
- **Python**: `>=3.14.3,<3.15` (tested baseline: Python 3.14.3)
- **Node.js / pnpm**: Verified workstation baseline Node.js `24.15.0` and pnpm `12.4.1` for monorepo workspace scripts.
- **Rust**: Verified workstation baseline `rustc 1.95.0` / `cargo 1.95.0` (required for desktop Tauri host compilation).
- **Operating System**: Windows 10/11 x64 with licensed Siemens Solid Edge® for live COM automation. Pure geometry and contract checks do not require COM; some offline driver tests use Windows process APIs. Exclude live tests explicitly with `-m "not com and not live_ai"`.

As of 25 September 2026: Milestones 1 through 6.5 are implemented. The earlier M6.4 installer completed its historical acceptance, but the Gemini-fixed source candidate needs a new installer and installed acceptance before distribution. GitHub records portable CI results for each pull request commit. Milestone 6.6 public documentation is in progress. See [engine status](engine/README.md).

### Initializing the Workspace
```powershell
# Clone the repository
git clone https://github.com/jitendra-patwari/cad-copilot.git
cd cad-copilot

# Install workspace dependencies (frozen lockfile)
pnpm install --frozen-lockfile

# Create and activate a Python virtual environment
python -m venv .venv
.venv\Scripts\activate

# Install Python engine developer tooling (standard offline installation)
pip install -e "engine[dev]"

# Optional: install the Gemini extra for AI plan proposal adapter development
pip install -e "engine[dev,gemini]"

# Launch the desktop application in development mode
pnpm --filter @cad-copilot/desktop tauri dev

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

### Requirements Traceability (`<TAG>`)
Every commit must cite the corresponding **Requirement or Authority ID** from [`docs/requirements.md`](docs/requirements.md) or roadmap governance (e.g. `[FR-1]`, `[FR-2]`, `[FR-19]`, `[FR-20]`, `[FR-21/22]`, `[RUN-10]`, `[QA-08]`). For general infrastructure or maintenance, use `[INFRA]` or the applicable `[RUN-*]` / `[QA-*]` tag.

### Examples
* `chore(root): [FR-1] initialize root workspace configuration, pnpm toolchains and dev tooling`
* `legal(license): [FR-1] add MIT license with Siemens trademark disclaimer, security policies and requirements spec`
* `feat(contracts): [FR-2] define public JSON-Schema contracts and golden runbooks`
* `feat(interfaces): [FR-3] implement abstract domain interfaces (CADExecutorABC, CADRuntimeABC) in engine`
* `fix(geometry): [FR-4] resolve floating point precision boundary in FaceContext UV projection`
* `feat(application): [FR-10] implement generation application orchestration`
* `docs(desktop): [FR-20] define clean desktop foundation`
* `ci(root): [RUN-10] add portable quality workflow`
* `docs(ci): [QA-08] record portable verification boundaries`

---

## Code Quality Standards

All submitted code must pass strict static analysis and testing before review:

```powershell
# Python Linting, Formatting & Type Checking (from engine/ using root .venv)
cd engine
..\.venv\Scripts\python.exe -m ruff check src tests
..\.venv\Scripts\python.exe -m ruff format --check src tests
..\.venv\Scripts\python.exe -m mypy --config-file mypy.ini --strict src tests\batch tests\ipc tests\drivers

# Python Unit & Contract Tests (offline, non-COM and non-live-AI)
..\.venv\Scripts\python.exe -m pytest -c pytest.ini -m "not com and not live_ai"
cd ..

# Desktop Frontend Verification (from repo root)
pnpm install --frozen-lockfile
pnpm test:desktop
pnpm lint:desktop
pnpm typecheck:desktop
pnpm format:check:desktop
pnpm build:desktop

# Rust Tauri Host Checks & Native Build (from repo root)
cargo fmt --manifest-path desktop\src-tauri\Cargo.toml --all -- --check
cargo test --manifest-path desktop\src-tauri\Cargo.toml --locked
cargo clippy --manifest-path desktop\src-tauri\Cargo.toml --locked --all-targets -- -D warnings
cargo test --manifest-path desktop\src-tauri\Cargo.toml --locked --features packaged-engine
cargo clippy --manifest-path desktop\src-tauri\Cargo.toml --locked --all-targets --features packaged-engine -- -D warnings
pnpm --filter @cad-copilot/desktop tauri build --no-bundle
```

Offline checks are not live COM or AI provider evidence. Default Cargo test runs report live Solid Edge tests and staged packaged-engine lifecycle tests as ignored. Run `com`-marked and `live_ai`-marked tests only with explicit authorization, valid credentials, and a suitable Windows/Solid Edge session:

```powershell
# Explicit local native qualification gates (outside automated CI):
$env:CAD_COPILOT_LIVE_TESTS = "1"
cargo test --manifest-path desktop\src-tauri\Cargo.toml --locked --test generation_lifecycle -- --ignored --test-threads=1
Remove-Item Env:CAD_COPILOT_LIVE_TESTS

# Staged engine lifecycle qualification (requires packaged engine payload):
pnpm --filter @cad-copilot/engine package:desktop
cargo test --manifest-path desktop\src-tauri\Cargo.toml --locked --test packaged_engine_lifecycle -- --ignored --test-threads=1
```

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
