# Getting Started with CAD Copilot

CAD Copilot is a local-first Windows desktop application that generates native Solid Edge parts from structured feature plans and automates multi-format batch exports.

---

## 1. Prerequisites

This source preview runs on Windows 10/11 (64-bit). A downloadable installer is planned after the updated build has its own installed acceptance.

### CAD and Network

- **Operating System:** Windows 10 or Windows 11 (64-bit).
- **CAD Kernel:** Locally installed and licensed Siemens Solid Edge® (tested on Solid Edge 2026, build `226.00.00.106`).
- **Network / API Key:** None required for deterministic examples or batch export workflows. An optional Google Gemini API key is required only if you use natural-language prompt generation.

### Development Toolchains

- **Python:** `3.14.3` (tested baseline: CPython 64-bit). Make `python` available on `PATH` before running the commands below.
- **Node.js & pnpm:** Node.js `24.15.0` and pnpm `12.4.1`.
- **Rust:** `1.95.0` (stable-x86_64-pc-windows-msvc) with Cargo.
- **Siemens Solid Edge®:** Required for live desktop runs and COM integration tests. Pure geometry and contract suites run offline.

## 2. Run the Desktop App from Source

### 1. Clone the Repository

```powershell
git clone https://github.com/jitendra-patwari/cad-copilot.git
Set-Location cad-copilot
```

### 2. Install Desktop Workspace Dependencies

```powershell
pnpm install --frozen-lockfile
```

### 3. Set Up the Python Engine

```powershell
python -m venv .venv

# Install pinned build tools matching the verified CI baseline
.\.venv\Scripts\python.exe -m pip install --constraint engine/packaging/constraints.txt --constraint engine/ci-constraints.txt pip setuptools wheel

# Install engine in editable mode with pinned developer dependencies
.\.venv\Scripts\python.exe -m pip install --no-build-isolation --constraint engine/packaging/constraints.txt --constraint engine/ci-constraints.txt -e "engine[dev]"

# Optional: Install Gemini provider dependencies
.\.venv\Scripts\python.exe -m pip install --no-build-isolation --constraint engine/packaging/constraints.txt --constraint engine/ci-constraints.txt -e "engine[dev,gemini]"
```

### 4. Run the Desktop Application

```powershell
pnpm --filter @cad-copilot/desktop tauri dev
```

### First-Run Guidance

- **Solid Edge Session:** Ensure Siemens Solid Edge is installed. If Solid Edge is not already running, CAD Copilot will launch a visible session upon the first generation or batch operation.
- **Ordered Mode:** If Solid Edge shows a Synchronous/Ordered mode choice, select **Ordered** and check **"Do not show this dialog again"** so the modal does not pause automation.
- **Gemini (Optional):** Open **Configure Gemini** in Generate to enter an API key for this session. Prompt text is sent to Google only when you run prompt generation; CAD Copilot does not save the key to disk.

Review the saved Solid Edge part and `run_manifest.json` to confirm feature count, dimensions, and faces for prompt-generated models.

### Running Tests & Quality Gates

For complete instructions on running pytest suites, mypy typechecking, Ruff linting, Vitest frontend tests, and Cargo test/clippy suites, see [CONTRIBUTING.md](../CONTRIBUTING.md).
