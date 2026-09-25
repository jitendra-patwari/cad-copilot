# CAD Copilot — Source Preview

> **Local AI-assisted Solid Edge® generation and native batch export.**

CAD Copilot is an open-source Windows desktop application that converts natural-language design intent into validated Siemens Solid Edge® parts and automates safe, multi-format local batch exports.

![CAD Copilot Generate Workspace](docs/media/generate-workspace.png)

---

## Verified Core Capabilities

- **Native Solid Edge Part Generation:** Constructs genuine 3D solid geometry in Solid Edge Ordered mode (rectangular blocks, cylinders, through/blind circular holes, rectangular and slot cutouts, planar pads, and conceptual spur gears).
- **Multi-Format Artifact Pipeline:** Automatically exports native `.par`, STEP, and STL models with a best-effort preview image and publishes a cryptographically verified `run_manifest.json` sidecar.
- **Automated Batch Translation & Publishing:** Processes native `.par`, `.psm`, and `.asm` files to STEP and STL, plus Parasolid (`.x_t`) when verification supports it, and publishes `.dft` drawings to PDF and DXF. Source files are closed without saving and checked for content changes.
- **Local-First Privacy & Optional AI:** CAD processing and output files stay local, with no telemetry. Optional Gemini prompt generation sends the text request to Google using a session-supplied API key. Deterministic examples and batch translation need no key or network connection.
- **Process Lifecycle & Safety:** A dedicated Solid Edge COM worker, targeted cancellation, bounded execution, and atomic output publication protect failed and cancelled runs.

![CAD Copilot Batch Workspace (.par Multi-File Export)](docs/media/batch-workspace.png)

---

## Run from Source

The current preview runs from source on Windows 10/11 (64-bit) with a locally installed, licensed copy of Siemens Solid Edge®. Install the Python, Node.js/pnpm, and Rust toolchains described in the **[Getting Started Guide](docs/getting-started.md)**, then launch the Tauri development app with `pnpm --filter @cad-copilot/desktop tauri dev`.

A downloadable installer is planned after the updated build passes separate installed acceptance.

---

## Architecture at a Glance

CAD Copilot uses a decoupled desktop architecture with strict IPC boundaries:

```mermaid
flowchart LR
    UI["Desktop UI\n(React 19 + Tailwind v4)"] <-->|"Tauri IPC"| Rust["Host Supervisor\n(Rust / Tauri v2)"]
    Rust <-->|"Strict Stdio IPC"| Engine["CAD Engine\n(Python 3.14)"]
    Engine <-->|"Public COM Dispatch"| SE["Siemens Solid Edge\n(Local COM Server)"]
    Engine -->|"Atomic Write"| Artifacts["Native CAD, STEP, STL\n& Manifests"]
```

- **Frontend:** Responsive React 19 interface with real-time progress streaming over Tauri window events.
- **Process Supervisor:** Rust host managing engine child processes with fail-closed cancellation and job objects.
- **Engine Core:** Deterministic Python 3.14 pipeline separating pure geometric lowering from vendor COM automation.
- **Solid Edge Driver:** Single STA execution context with message filtering, bounded busy retries, and document handle tracking.

For an in-depth breakdown of components and security boundaries, see **[System Architecture](docs/architecture.md)**.

---

## Verification & Testing Boundaries

CAD Copilot maintains strict separation between automated portable gates and live CAD acceptance:

| Layer | Scope & Test Count | Verification Boundary |
| :--- | :--- | :--- |
| **Python Engine** | 2,473 passed, 4 skipped, 56 deselected | Gemini-fix source candidate: offline pytest, strict MyPy, Ruff linter & formatter |
| **Desktop Frontend** | 90 passed across 9 test files | Vitest suites |
| **Rust Desktop Host** | 131 passed, 8 ignored | Default Cargo test suite |
| **Live Source-App Checks** | Three repeated pad-and-through-hole runs inspected | Saved Solid Edge parts and canonical manifests on a licensed workstation |
| **Hosted CI Pipeline** | 3 portable jobs configured | GitHub records results for each pull request commit |

*Hosted GitHub Actions runners execute portable checks; live Solid Edge COM automation requires a licensed Windows workstation. Installed acceptance for the updated build remains pending. See **[Verification Evidence](docs/verification.md)** for the current source-candidate boundary.*

---

## Honest Boundaries & Limitations

- **Solid Edge Prerequisite:** CAD Copilot does not include a proprietary CAD kernel. Live part generation and batch translation require a licensed installation of Siemens Solid Edge on Windows.
- **Conceptual Gear Design:** The conceptual spur gear generator produces an illustrative parametric outline for visualization and modeling workflows, not a certified manufacturing-grade or load-bearing gear.
- **Prompt Interpretation Preview:** Some multi-feature prompts may be rejected or need rewording. Check the saved part and `run_manifest.json` against the requested dimensions, features, and faces before using a generated model.
- **Deterministic AI Guardrails:** The optional Gemini adapter proposes structured JSON feature plans; all geometric validation, dimension checks, and CAD lowering are executed by deterministic code before contacting Solid Edge.

---

## Trademark & Non-Affiliation Notice

- **Siemens® & Solid Edge®:** Siemens and Solid Edge are trademarks or registered trademarks of Siemens Product Lifecycle Management Software Inc. or its subsidiaries in the United States and other countries. CAD Copilot is an independent open-source project and is not affiliated with, endorsed by, sponsored by, or supported by Siemens. CAD Copilot interacts with Solid Edge solely through its standard, publicly documented Windows COM automation interfaces.
- **Microsoft® & Copilot:** Microsoft and Copilot are trademarks of Microsoft Corporation. CAD Copilot is an independent open-source tool and is not affiliated with, endorsed by, sponsored by, or associated with Microsoft Corporation or GitHub, Inc.

---

## License

CAD Copilot is licensed under the [MIT License](LICENSE).
