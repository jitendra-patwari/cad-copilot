# Getting Started with CAD Copilot

CAD Copilot is a local-first Windows desktop application that generates native Solid Edge parts from structured feature plans and automates multi-format batch exports.

---

## 1. Prerequisites

This source preview runs on Windows 10/11 (64-bit). A downloadable installer is planned after the updated build has its own installed acceptance.

### CAD and Network

- **Operating System:** Windows 10 or Windows 11 (64-bit).
- **CAD Kernel:** Locally installed Siemens Solid Edge® (tested on Solid Edge 2026, build `226.00.00.106`). Siemens also offers a free personal-use **[Community Edition](solid-edge-community-edition.md)**.
- **Network / API Key:** None required for deterministic examples or batch export workflows. Natural-language prompt generation requires internet access and a Google Gemini API key. Follow **[Gemini API Key Setup](gemini-api-key.md)** to obtain a key and test with an eligible free-tier project.

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

- **Recommended First Run:** In the Design Workspace, start with the default **Example (Spur Gear)** mode, choose an output folder, and click **Run CAD Generation**. This deterministic example exercises the live COM interface and exports complete part, STEP, and STL models without requiring an API key or network access.
- **Solid Edge Session:** Ensure Siemens Solid Edge is installed on Windows. If Solid Edge is not already running, CAD Copilot will automatically launch a visible session upon the first generation or batch operation.
- **Ordered Mode:** If Solid Edge presents a Synchronous/Ordered mode choice dialog on startup, choose **Ordered** and check **"Do not show this dialog again"** so the modal does not block automation.
- **Gemini (Optional):** Follow **[Gemini API Key Setup](gemini-api-key.md)** for Google AI Studio key creation, free-tier limits, and a first test prompt. Install the Gemini dependencies above, switch to **Prompt to CAD**, enter the key in the automatically expanded editor, and click **Save**. Your prompt is transmitted to Google Gemini solely during prompt generation; CAD Copilot keeps the key in memory only and never saves it to disk. Enter it again after restarting the app.
- **Keep Part Open:** Check **Keep part open in Solid Edge** before generation to reopen the saved part after a successful run. Leave it unchecked to close the part after export. If reopening fails, the generated files remain available and the app shows a warning.

Review the saved Solid Edge part and `run_manifest.json` to confirm feature count, dimensions, and faces for prompt-generated models.

### Running Tests & Quality Gates

For complete instructions on running pytest suites, mypy typechecking, Ruff linting, Vitest frontend tests, and Cargo test/clippy suites, see [CONTRIBUTING.md](../CONTRIBUTING.md).
