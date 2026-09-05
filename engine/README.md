# CAD Copilot Engine

CAD Copilot is an open-source project for local Solid Edge generation and native-file batch export.

## Current implementation

As of 5 September 2026:

- M1/M2: domain interfaces, JSON contracts, pure geometry, parsing, validation, lowering, and bounded STEP text smoke checking.
- M3.0: scope, contract, governance, and Python tooling reconciliation.
- M3.1: a dedicated STA Solid Edge runtime with explicit ownership, opaque handles, Ordered part creation, and bounded lifecycle cleanup.
- M3.2: Solid Edge primitive execution (cuboid, cylinder, conceptual spur gear with centered bore), localized 2D cutouts (circular, rectangular, slot through/blind), +Z rectangular pad protrusions, ordered sequential feature execution, recompute, and authoritative topology/property inspection.
- M3.3: safe multi-format artifact finalization, atomic directory publication, path containment, Draft 2020-12 wire-schema projection compatibility, closed-file validators (native `.par`, bounded STEP, binary/ASCII STL, signature-checked JPG), and pipeline orchestration (`finalize_request_artifacts`).
- M4.1: implemented and verified generation application orchestration component (`GenerationService`, typed application models, pure response/warning/error/artifact projection, mode continuity, single-request lifecycle coordination, preflight collision/containment checking, and canonical preparation).

M4 concrete Gemini adapter (M4.2), deterministic example catalog (M4.3), run manifest (M4.4), stdio transport (M4.5), M5 batch execution, and M6 desktop integration remain planned. M4.1 is an internal component; a complete end-to-end generation workflow is not yet available.

## Verification boundary

- Full offline test suite: **921 passed, 2 skipped, 33 COM tests deselected** across all domain packages.
- Strict mypy: **Success (0 issues across 48 source files)** (`mypy src`).
- Ruff linting and formatting: clean across 90 files in engine (98 files repo-wide).
- Live integration evidence: **31 passed, 0 skipped** for the established M3 suite on a licensed Siemens Solid Edge 2026 session (version `226.00.00.106`), plus **2 passed, 0 skipped** in the focused M4.1 component run covering deterministic prompt/example orchestration, required artifact publication, and ownership-safe teardown.

## Development and scope

Python `>=3.14.3,<3.15` is required; the tested baseline is Python 3.14.3. Live automation requires Windows and a licensed local Solid Edge installation. Pure geometry and contract checks do not require COM; some offline driver tests use Windows process APIs. Use `-m "not com"` to exclude live CAD tests explicitly.

See [requirements](../docs/requirements.md) and [contribution guidance](../CONTRIBUTING.md). There is no mock CAD application, generation/batch launcher, or implemented desktop package in this baseline.

Solid Edge® is a registered trademark of Siemens Industry Software Inc. CAD Copilot is an independent open-source project and is not affiliated with, endorsed by, or sponsored by Siemens.
