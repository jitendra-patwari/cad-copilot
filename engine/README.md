# CAD Copilot Engine

CAD Copilot is an open-source project for local Solid Edge generation and native-file batch export.

## Current implementation

As of 1 September 2026:

- M1/M2: domain interfaces, JSON contracts, pure geometry, parsing, validation, lowering, and bounded STEP text smoke checking.
- M3.0: scope, contract, governance, and Python tooling reconciliation.
- M3.1: a dedicated STA Solid Edge runtime with explicit ownership, opaque handles, Ordered part creation, and bounded lifecycle cleanup.
- M3.2: Solid Edge primitive execution (cuboid, cylinder, conceptual spur gear with centered bore), localized 2D cutouts (circular, rectangular, slot through/blind), +Z rectangular pad protrusions, ordered sequential feature execution, recompute, and authoritative topology/property inspection.

M3.3 artifact export/finalization, M4 generation orchestration and optional Gemini, M5 batch execution, and M6 desktop integration are not yet implemented. The schemas and example plans describe contracts; they are not an available end-to-end application.

## Verification boundary

- Full offline test suite: **492 passed, 19 COM tests deselected** across all domain packages.
- Strict mypy: **Success (0 issues across 28 source files)**.
- Ruff linting and formatting: clean across 71 files.
- Live integration evidence: **19 passed** on a licensed Siemens Solid Edge 2024 session (version `226.00.00.106`), including M3.1 owned lifecycle behavior and M3.2 3D primitives, 6-face cutout characterization, +Z pads, sequential feature execution, conceptual gears with centered bores, controlled preflight/native failure handling with recovery, borrowed unrelated-document preservation, and graceful owned shutdown without force cleanup.

## Development and scope

Python `>=3.14.3,<3.15` is required; the tested baseline is Python 3.14.3. Live automation requires Windows and a licensed local Solid Edge installation. Pure geometry and contract checks do not require COM; some offline driver tests use Windows process APIs. Use `-m "not com"` to exclude live CAD tests explicitly.

See [requirements](../docs/requirements.md) and [contribution guidance](../CONTRIBUTING.md). There is no mock CAD application, generation/batch launcher, or implemented desktop package in this baseline.

Solid Edge® is a registered trademark of Siemens Industry Software Inc. CAD Copilot is an independent open-source project and is not affiliated with, endorsed by, or sponsored by Siemens.
