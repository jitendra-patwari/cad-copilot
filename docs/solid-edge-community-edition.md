# Solid Edge® Community Edition Setup & Availability

Siemens offers **Solid Edge® Community Edition** free of charge for personal, hobbyist, and non-commercial educational use. This document outlines the official download resource, known edition restrictions, and CAD Copilot's current compatibility boundary.

---

## 1. Official Download & Licensing

You can obtain Solid Edge Community Edition directly from Siemens:

- **Official Download Portal:** [Siemens — Solid Edge Community Edition Download](https://resources.sw.siemens.com/en-US/download-solid-edge-community-edition/)
- **Eligibility:** Free for personal, non-commercial use (individual makers, hobbyists, and learners).
- **Operating System:** Windows 10 or Windows 11 (64-bit). Siemens specifies supported Windows Professional and Enterprise editions and excludes Windows Home; see [Siemens Solid Edge System Requirements](https://solidedge.siemens.com/en/resources/system-requirements/).
- **Source Prerequisites:** For the complete developer environment toolchains (Python, Node.js/pnpm, Rust), see the **[Getting Started Guide](getting-started.md)**.

---

## 2. Siemens Edition Boundaries & Restrictions

Siemens specifies distinct boundaries for Community Edition compared to commercial licenses:

1. **Non-Commercial Use Only:** Community Edition is strictly intended for personal, non-commercial projects.
2. **File Interoperability Boundary:** Native part (`.par`), sheet metal (`.psm`), and assembly (`.asm`) files saved in Community Edition cannot be opened in commercial installations of Solid Edge.
3. **2D Drawing Watermarks:** 2D drawings (`.dft`) created with Solid Edge Community Edition include a visible watermark.

---

## 3. CAD Copilot Compatibility Status

CAD Copilot interacts with local Solid Edge installations through standard public Windows COM Dispatch interfaces (`SolidEdge.Application`).

> **Compatibility Boundary Note:**
> The official availability of Community Edition is established above. However, complete end-to-end COM automation and all multi-format batch conversions (`.step`, `.stl`, `.x_t`, `.pdf`, `.dxf`) have not yet been separately certified against Community Edition in this preview. Historical live automated qualification was conducted on a licensed installation of Siemens Solid Edge 2026 (`build 226.00.00.106`).

---

## 4. Recommended First Run

When testing your local Solid Edge installation with CAD Copilot:

1. Launch Solid Edge once manually to confirm proper Windows registration and set your default environment to **Ordered** mode.
2. Launch CAD Copilot via `pnpm --filter @cad-copilot/desktop tauri dev`.
3. In the **Design Workspace**, select the default **Example (Spur Gear)** mode and choose an output directory.
4. Click **Run CAD Generation**. This deterministic path verifies COM connection, part creation, and artifact export completely locally without requiring an API key or network access.
