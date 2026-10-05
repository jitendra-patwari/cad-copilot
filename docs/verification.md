# Verification & Quality Evidence

This page records the **[v0.1.0 Windows preview](https://github.com/jitendra-patwari/cad-copilot/releases/tag/v0.1.0)** and its historical source checks. Automated checks, packaging checks, and live Solid Edge observations cover different boundaries.

## Portable checks

The Gemini-fixed source candidate was checked locally on 25 September 2026:

| Component | Check | Result |
| :--- | :--- | :--- |
| Python engine | `pytest -c pytest.ini -m "not com and not live_ai"` | **2,473 passed, 4 skipped, 56 deselected** |
| Python quality | Ruff lint, Ruff format, strict MyPy | **Passed** |
| Desktop frontend | Vitest | **90 tests passed** |
| Rust desktop host | Full default Cargo test suite | **131 passed, 8 ignored** |

The repository's three GitHub Actions jobs check portable Python, frontend, and Rust behavior. GitHub records their results for each pull request commit. Hosted runners do not execute Solid Edge COM or a live Gemini request.

## Desktop UI & workflow verification (1 October 2026)

The desktop UI refresh candidate was committed at `9847675` (`feat(desktop): [FR-20/21/22] refresh workspaces and harden UI workflows`). The portable frontend gates and UI workflow safeguards were verified locally:

| Suite / Gate | Command | Result |
| :--- | :--- | :--- |
| Frontend tests | `pnpm --filter @cad-copilot/desktop test` (Vitest) | **98 passed across 8 test files** (0 failed) |
| TypeScript typecheck | `pnpm --filter @cad-copilot/desktop typecheck` (`tsc -b --noEmit`) | **0 errors** |
| Frontend linter | `pnpm --filter @cad-copilot/desktop lint` (`eslint . --max-warnings=0`) | **0 warnings, 0 errors** |
| Code formatting | `pnpm --filter @cad-copilot/desktop format:check` (`prettier --check .`) | **Passed** |
| Production build | `pnpm build:desktop` (`vite build`) | **Built successfully** (`dist/index.html`, `dist/assets/`) |
| Git whitespace | `git diff --check` | **Clean** (0 whitespace errors) |

### Verified behavior & workflow safeguards

- **Batch Selection Race Protection:** Native folder and file selection operations assert `isSelectionPending`, which disables the Start button and file/directory pickers during active selection. Direct state updates were replaced with authoritative snapshots using monotonic revision checks, preventing older native scanning responses from overwriting newer selections.
- **Output Directory Alignment:** Target Output Directory cards across both Generate and Batch modes are structurally and visually aligned with clear browsing triggers, bound path presentation, validation warnings when unselected, and authoritative directory containment.
- **Help & Documentation Migration:** The foundation diagnostics panel was replaced with an accessible `HelpPanel` modal (opened from the sidebar footer) containing quickstart guidance, safety boundaries, and format specifications, with keyboard focus trapping, Escape dismissal, and focus return.
- **Terminal State Preservation:** Batch and Generate terminal outcome results are preserved on navigation or subsequent state transitions until explicitly cleared or rerun.
- **Live User Smoke Checks:** User reports successful live operation after the UI updates. *(Note: These user observations were conducted directly on the local workstation and were not independently rerun by the reviewer in this verification cycle).*

## Preview and keep-open follow-up (2 October 2026)

The source candidate fixes preview capture through the active Solid Edge application window and adds the Generate screen's **Keep part open in Solid Edge** checkbox. Focused portable checks after the changes passed:

| Component | Scope | Result |
| :--- | :--- | :--- |
| Python engine | Exporter, application, artifact pipeline, runtime lifecycle/document tasks, and generation IPC | **357 passed** |
| Rust desktop host | Generation library tests and generation lifecycle integration tests | **59 + 5 passed; 2 live tests ignored** |
| Desktop frontend | Generate and generation workflow tests | **17 passed** |
| Quality checks | TypeScript, ESLint, MyPy, Ruff lint/format, and Git whitespace | **Passed** |

The user reports that live testing of the updated app was successful. This is user-reported workstation acceptance and was not independently rerun by the reviewer. It does not establish separate Community Edition qualification or installer acceptance. The focused checks above supplement the earlier full-suite results rather than replacing them.

## Live Solid Edge checks

The source-run Tauri app was exercised on a licensed Siemens Solid Edge 2026 workstation. Saved parts and canonical `run_manifest.json` plans were inspected, including:

- A block with a circular hole on its top face and a rectangular through-cutout on its front face.
- A base with one centered pad and one circular hole open through both the pad and base. The same prompt produced the intended part in three separate runs.

These observations verify the listed prompts and runs. Gemini output varies, so every prompt-generated part should be checked for feature count, dimensions, target faces, and final geometry before use.

## Windows installer and release (5 October 2026)

The v0.1.0 Windows x64 installer was built on **4 October 2026** from clean source commit [`491f536`](https://github.com/jitendra-patwari/cad-copilot/commit/491f536296d799448d6642d2aa50c5d31bac4bc0) using `pnpm package:desktop`. The release tag identifies that source commit; subsequent documentation updates do not change the packaged application.

| Check | Result |
| :--- | :--- |
| Source CI | [All three portable jobs passed](https://github.com/jitendra-patwari/cad-copilot/actions/runs/36996167591) |
| Packaging and privacy audits | Engine payload, compiled metadata, and installer audits passed |
| Engine inventory | All 170 payload files matched their recorded sizes and SHA-256 hashes; packaged generation schema matched source |
| Packaged-engine offline lifecycle | **6 passed, 0 failed**; copied outside the checkout and exercised without developer PATH/Python configuration |
| Installed-app acceptance | **User confirmed complete on 5 October 2026**; not independently rerun by the reviewer |

The installed-app checklist covered example and Gemini generation, preview display, keep-open behavior, batch operation, and cancellation. The two recorded demos show successful gear generation and prompt-generated mounting-plate creation with the saved part kept open. These records do not establish separate Community Edition qualification for all export formats.

**Installer:** [CAD-Copilot_0.1.0_x64-setup.exe](https://github.com/jitendra-patwari/cad-copilot/releases/download/v0.1.0/CAD-Copilot_0.1.0_x64-setup.exe) (27,498,484 bytes). SHA-256:

```text
6d009ca8ad6fc27707d65155fc7d156065462c0d9786c7a5e061a92c0e3601e2
```

The [release](https://github.com/jitendra-patwari/cad-copilot/releases/tag/v0.1.0) includes the installer, both MP4 demos, and `SHA256SUMS.txt`. Follow [Getting Started](getting-started.md) for installation. Earlier M6.4 installed acceptance belongs to its earlier build; it is not substituted for this build's evidence.
