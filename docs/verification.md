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

## Live Solid Edge checks

The source-run Tauri app was exercised on a licensed Siemens Solid Edge 2026 workstation. Saved parts and canonical `run_manifest.json` plans were inspected, including:

- A block with a circular hole on its top face and a rectangular through-cutout on its front face.
- A base with one centered pad and one circular hole open through both the pad and base. The same prompt produced the intended part in three separate runs.

These observations verify the listed prompts and runs. Gemini output varies, so every prompt-generated part should be checked for feature count, dimensions, target faces, and final geometry before use.

## Installer boundary

An earlier installer passed its own historical acceptance checks, but it predates the Gemini changes and will not be distributed. A new installer requires a fresh build, provenance check, and installed acceptance before any release claim. No installer or video is included in this source preview.
