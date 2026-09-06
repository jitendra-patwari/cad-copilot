# Canonical Examples: Feature Plan DSL AST Contracts

This directory contains internal feature-plan JSON examples and Abstract Syntax Tree (AST) fixtures for CAD Copilot's pure parsing, validation, and lowering domain. Application orchestration exists (`GenerationService`), while the concrete AI proposal adapter (M4.2) and deterministic catalog loading (M4.3) remain planned.

Current typed models, parser, validation, and lowering code define executable domain semantics. These examples illustrate that representation; their presence does not establish live CAD support. Boolean composition, sweep, and broader face combinations remain conditional, regardless of fixture coverage.

---

## Canonical Fixture Catalog

1. `accepted_example.json` - Single-part rectangular base plate with centered circular through-hole.
2. `l_bracket_example_plan.json` - Conceptual single-part Boolean composition of a base plate and upright with offset mounting holes; conditional live support, not assembly generation.
3. `multi_primitive_example_plan.json` - Multi-primitive rectangular base united with a centered cylindrical boss.
4. `placement_example_features.json` - Array of parametric features demonstrating planar face UV coordinate offsets, slot cutouts, and multi-face orientations (+Z, +X, -Y).
5. `rejected_example.json` - Out-of-scope prompt decision record demonstrating clean failure rejection (`unsupported_feature`).
6. `side_pad_example_feature.json` - Planar side face (+X) extruded mounting pad.
7. `spur_gear_example_plan.json` - Conceptual spur-gear outline with center bore, module, pressure-angle parameter, face width, and tooth count; no manufacturing-grade involute or meshing claim.
8. `sweep_example_plan.json` - Swept circular profile along a circular trajectory (torus geometry).

---

## Fixture Coverage and Current Limits

The eight JSON files are maintained here. There is no `engine/src/ai/fixtures/` mirror in the current checkout.

`engine/tests/contracts/test_schema_contracts.py::test_golden_dsl_examples_integrity` checks that the named files exist and load as non-empty JSON objects or lists. It does not assert byte-for-byte runtime synchronization, validate every example through the domain pipeline, or prove live Solid Edge execution.

Geometry tests separately cover parsing, validation, and lowering. Application orchestration (`GenerationService`) accepts example plan requests via injected resolvers, while production catalog loading belongs to M4.3; changes to these fixtures should be reviewed against current domain semantics.

---

## Internal DSL Versioning

The `decision_version` (`cad_copilot.canonical_decision.v1`) and `plan_version` (`cad_copilot.single_part_feature_plan.v1`) strings identify internal feature lowering AST versions and are decoupled from the public wire protocol `contract_version: "1.0"`.
