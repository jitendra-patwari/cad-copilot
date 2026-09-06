# CAD Copilot Engine

CAD Copilot is an open-source project for local Solid Edge generation and native-file batch export.

## Current implementation

As of 6 September 2026:

- M1/M2: domain interfaces, JSON contracts, pure geometry, parsing, validation, lowering, and bounded STEP text smoke checking.
- M3.0: scope, contract, governance, and Python tooling reconciliation.
- M3.1: a dedicated STA Solid Edge runtime with explicit ownership, opaque handles, Ordered part creation, and bounded lifecycle cleanup.
- M3.2: Solid Edge primitive execution (cuboid, cylinder, conceptual spur gear with centered bore), localized 2D cutouts (circular, rectangular, slot through/blind), +Z rectangular pad protrusions, ordered sequential feature execution, recompute, and authoritative topology/property inspection.
- M3.3: safe multi-format artifact finalization, atomic directory publication, path containment, Draft 2020-12 wire-schema projection compatibility, closed-file validators (native `.par`, bounded STEP, binary/ASCII STL, signature-checked JPG), and pipeline orchestration (`finalize_request_artifacts`).
- M4.1: implemented and verified generation application orchestration component (`GenerationService`, typed application models, pure response/warning/error/artifact projection, mode continuity, single-request lifecycle coordination, preflight collision/containment checking, and canonical preparation).
- M4.3: deterministic example catalog loader (`example_catalog` package, immutable `MappingProxyType` catalog mapping, standard `importlib.resources` template loading, request-ID substitution, schema enum parity, distribution wheel packaging, and zero-leak privacy boundary).
- M4.4: canonical run manifest (`run_manifest.json`) and sidecar publication component (`manifests` package, deterministic canonical JSON fingerprints, Draft 2020-12 schema validation, snapshot verification, atomic artifact transaction integration, pre-runtime preparation, and fail-closed privacy boundary).

M4 concrete Gemini adapter (M4.2), stdio transport (M4.5), M5 batch execution, and M6 desktop integration remain planned. M4.1, M4.3, and M4.4 are internal engine components; a complete end-to-end generation workflow is not yet available.

## Verification boundary

- Full offline test suite: **1,136 passed, 2 skipped, 36 COM tests deselected** across all domain packages.
- Strict mypy: **Success (0 issues across 71 source files)** (`mypy --config-file mypy.ini src tests/manifests tests/artifacts tests/application tests/example_catalog tests/drivers/test_solidedge_live.py`).
- Ruff linting and formatting: clean across engine (`ruff check src tests` and `ruff format --check src tests`).
- Live integration evidence: **31 passed, 0 skipped** for the established M3 suite on a licensed Siemens Solid Edge 2026 session (version `226.00.00.106`), plus **5 passed, 0 skipped** across the focused M4 component runs covering deterministic prompt/example orchestration, production catalog-driven generation, required artifact publication, live `run_manifest.json` sidecar publication, and ownership-safe teardown.

## Development and scope

Python `>=3.14.3,<3.15` is required; the tested baseline is Python 3.14.3. Live automation requires Windows and a licensed local Solid Edge installation. Pure geometry and contract checks do not require COM; some offline driver tests use Windows process APIs. Use `-m "not com"` to exclude live CAD tests explicitly.

See [requirements](../docs/requirements.md) and [contribution guidance](../CONTRIBUTING.md). There is no mock CAD application, generation/batch launcher, or implemented desktop package in this baseline.

Solid Edge® is a registered trademark of Siemens Industry Software Inc. CAD Copilot is an independent open-source project and is not affiliated with, endorsed by, or sponsored by Siemens.
