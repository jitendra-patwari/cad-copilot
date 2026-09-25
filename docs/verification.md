# Verification & Quality Evidence

This page describes the current **source preview**. CAD Copilot has no downloadable installer for this candidate. Automated checks and live Solid Edge observations cover different boundaries.

## Portable checks

The Gemini-fixed source candidate was checked locally on 25 September 2026:

| Component | Check | Result |
| :--- | :--- | :--- |
| Python engine | `pytest -c pytest.ini -m "not com and not live_ai"` | **2,473 passed, 4 skipped, 56 deselected** |
| Python quality | Ruff lint, Ruff format, strict MyPy | **Passed** |
| Desktop frontend | Vitest | **90 tests passed** |
| Rust desktop host | Full default Cargo test suite | **131 passed, 8 ignored** |

The repository's three GitHub Actions jobs check portable Python, frontend, and Rust behavior. GitHub records their results for each pull request commit. Hosted runners do not execute Solid Edge COM or a live Gemini request.

## Live Solid Edge checks

The source-run Tauri app was exercised on a licensed Siemens Solid Edge 2026 workstation. Saved parts and canonical `run_manifest.json` plans were inspected, including:

- A block with a circular hole on its top face and a rectangular through-cutout on its front face.
- A base with one centered pad and one circular hole open through both the pad and base. The same prompt produced the intended part in three separate runs.

These observations verify the listed prompts and runs. Gemini output varies, so every prompt-generated part should be checked for feature count, dimensions, target faces, and final geometry before use.

## Installer boundary

An earlier installer passed its own historical acceptance checks, but it predates the Gemini changes and will not be distributed. A new installer requires a fresh build, provenance check, and installed acceptance before any release claim. No installer or video is included in this source preview.
