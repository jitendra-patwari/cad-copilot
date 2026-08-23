# Canonical Examples: Feature Plan DSL AST Contracts

This directory contains canonical prompt/feature-plan JSON few-shot examples and Abstract Syntax Tree (AST) fixtures for the CAD Copilot AI feature lowering subsystem.

These golden fixtures serve as the authoritative interface contract for parametric geometry generation, multi-primitive boolean operations, and feature placement coordinate frames.

---

## Canonical Fixture Catalog

1. `accepted_example.json` - Single-part rectangular base plate with centered circular through-hole.
2. `l_bracket_example_plan.json` - Multi-body L-bracket assembly with base plate, upright plate, boolean union, and offset mounting holes.
3. `multi_primitive_example_plan.json` - Multi-primitive rectangular base united with a centered cylindrical boss.
4. `placement_example_features.json` - Array of parametric features demonstrating planar face UV coordinate offsets, slot cutouts, and multi-face orientations (+Z, +X, -Y).
5. `rejected_example.json` - Out-of-scope prompt decision record demonstrating clean failure rejection (`unsupported_feature`).
6. `side_pad_example_feature.json` - Planar side face (+X) extruded mounting pad.
7. `spur_gear_example_plan.json` - Parametric involute spur gear with center bore, pitch diameter, module, and tooth count.
8. `sweep_example_plan.json` - Swept circular profile along a circular trajectory (torus geometry).

---

## Runtime Synchronization & Drift Prevention

To prevent runtime path resolution issues and enforce decoupling:
- Identical golden copies of these 8 JSON files are maintained under the engine's AI package fixtures (`engine/src/ai/fixtures/`).
- Automated tests assert that every JSON fixture in `contracts/examples/` is byte-for-byte identical to its runtime engine counterpart.
- Any modification to DSL representation must be updated in both locations simultaneously.

---

## Internal DSL Versioning

The `decision_version` (`cad_copilot.canonical_decision.v1`) and `plan_version` (`cad_copilot.single_part_feature_plan.v1`) strings identify internal feature lowering AST versions and are decoupled from the public wire protocol `contract_version: "1.0"`.

