"""Shared fixtures and test helpers for manifest test suites."""

from __future__ import annotations

from typing import Any


def build_valid_golden_manifest() -> dict[str, Any]:
    """Build a complete, schema-valid golden RunManifest dictionary."""
    return {
        "manifest_version": "cad_copilot.run_manifest.v1",
        "request": {
            "request_id": "req-golden-001",
            "contract_version": "1.0",
            "kind": "prompt_to_cad",
            "unit": "mm",
        },
        "engine": {
            "name": "cad-copilot",
            "version": "0.1.0",
        },
        "cad_runtime": {
            "product": "solid_edge",
            "version_build": "Solid Edge 2024 (224.00.00.076)",
        },
        "provenance": {
            "kind": "ai_proposal",
            "source_id": "test-source",
        },
        "gate_mode": "capability_first",
        "fingerprints": {
            "plan_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            "prompt_sha256": "fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210",
        },
        "feature_plan": {
            "plan_version": "cad_copilot.single_part_feature_plan.v1",
            "request_id": "req-golden-001",
            "units": "mm",
            "part": {
                "part_id": "part.main",
                "design_intent": "box",
                "scope": "single_part",
                "origin": "body_center",
                "x_axis": "length",
                "y_axis": "width",
                "z_axis": "thickness_up",
            },
            "base_body": {
                "id": "body.base",
                "family": "rectangular_prism",
                "dimensions_mm": {
                    "length_mm": 100.0,
                    "width_mm": 50.0,
                    "thickness_mm": 20.0,
                },
                "placement": {
                    "x_mm": 0.0,
                    "y_mm": 0.0,
                    "z_mm": 0.0,
                },
                "semantic_labels": ["base"],
            },
            "primitive_bodies": [
                {
                    "id": "body.base",
                    "family": "rectangular_prism",
                    "dimensions_mm": {
                        "length_mm": 100.0,
                        "width_mm": 50.0,
                        "thickness_mm": 20.0,
                    },
                    "placement": {
                        "x_mm": 0.0,
                        "y_mm": 0.0,
                        "z_mm": 0.0,
                    },
                    "semantic_labels": ["base"],
                }
            ],
            "boolean_operations": [],
            "features": [],
            "lowering_strategy": {
                "status": "canonical",
                "preferred_current_target": "cad_copilot_lowering",
                "runtime_route_change": False,
            },
        },
        "stable_ids": {
            "part_id": "part.main",
            "body_ids": ["body.base"],
            "boolean_operation_ids": [],
            "feature_ids": [],
        },
        "defaults_applied": [],
        "diagnostics": [],
        "warnings": [],
        "execution": {
            "operations_executed": 1,
            "operation_results": [
                {
                    "patch_id": "op_0",
                    "operation": "add_box",
                    "reference_id": "body.base",
                    "reference_kind": "body",
                }
            ],
            "inspection": {
                "volume_mm3": 100000.0,
                "mass_kg": 0.27,
                "feature_count": 0,
                "body_count": 1,
                "solid_body_count": 1,
                "sheet_body_count": 0,
                "wire_body_count": 0,
            },
        },
        "artifacts": [
            {
                "type": "native_part",
                "format": "par",
                "path": "part.par",
                "size_bytes": 1024,
                "sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            },
            {
                "type": "geometry_step",
                "format": "step",
                "path": "model.step",
                "size_bytes": 2048,
                "sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            },
            {
                "type": "mesh_stl",
                "format": "stl",
                "path": "mesh.stl",
                "size_bytes": 4096,
                "sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
            },
        ],
    }
