"""Compact Gemini guidance schema derived from the canonical proposal schema."""

from __future__ import annotations

import copy
import functools
from typing import Any

from plan_providers.proposal import ProposalError, load_proposal_schema


def _schema_object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProposalError("configuration_invalid", f"Proposal schema {path} must be an object")
    return value


def _schema_properties(value: dict[str, Any], path: str) -> dict[str, Any]:
    return _schema_object(value.get("properties"), f"{path}.properties")


def _scalar_property(source: dict[str, Any], name: str, path: str) -> dict[str, Any]:
    prop = _schema_object(source.get(name), f"{path}.{name}")
    projected = {"type": copy.deepcopy(prop.get("type"))}
    if projected["type"] is None:
        raise ProposalError("configuration_invalid", f"Proposal schema {path}.{name} must declare a type")
    if "enum" in prop:
        projected["enum"] = copy.deepcopy(prop["enum"])
    return projected


def _numeric_object() -> dict[str, Any]:
    return {"type": "object", "additionalProperties": {"type": "number"}}


def _compact_nested_schema(source: dict[str, Any], definitions: dict[str, Any]) -> dict[str, Any]:
    """Inline one canonical nested schema without provider-hostile references or unions."""
    reference = source.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/$defs/"):
        source = _schema_object(definitions.get(reference.removeprefix("#/$defs/")), reference)

    projected: dict[str, Any] = {}
    for key in ("type", "enum", "const", "required", "additionalProperties", "minProperties"):
        if key in source:
            projected[key] = copy.deepcopy(source[key])

    properties = source.get("properties")
    if isinstance(properties, dict):
        projected["properties"] = {
            name: _compact_nested_schema(_schema_object(value, name), definitions) for name, value in properties.items()
        }

    items = source.get("items")
    if isinstance(items, dict):
        projected["items"] = _compact_nested_schema(items, definitions)
    return projected


def _compact_base_body(base_body: dict[str, Any]) -> dict[str, Any]:
    props = _schema_properties(base_body, "$defs.baseBody")
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["family", "dimensions_mm"],
        "properties": {
            "id": _scalar_property(props, "id", "$defs.baseBody.properties"),
            "family": _scalar_property(props, "family", "$defs.baseBody.properties"),
            "semantic_labels": {"type": "array", "items": {"type": "string"}},
            "placement": _numeric_object(),
            "dimensions_mm": _numeric_object(),
            "profile_points": {"type": "array", "items": _numeric_object()},
        },
    }


def _compact_boolean_operation(boolean_operation: dict[str, Any]) -> dict[str, Any]:
    props = _schema_properties(boolean_operation, "$defs.booleanOperation")
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["operation", "target_body_id", "tool_body_id", "result_body_id"],
        "properties": {
            name: _scalar_property(props, name, "$defs.booleanOperation.properties")
            for name in ("id", "operation", "target_body_id", "tool_body_id", "result_body_id")
        },
    }


def _required_dimensions(names: tuple[str, ...], description: str) -> dict[str, Any]:
    return {
        "type": "object",
        "description": description,
        "additionalProperties": False,
        "required": list(names),
        "properties": {
            name: {
                "type": "number",
                "description": f"Required {name.removesuffix('_mm').replace('_', ' ')} in millimeters.",
            }
            for name in names
        },
    }


def _compact_feature(feature: dict[str, Any], definitions: dict[str, Any]) -> dict[str, Any]:
    props = _schema_properties(feature, "$defs.feature")
    nested = {
        name: _compact_nested_schema(_schema_object(props.get(name), name), definitions)
        for name in (
            "target",
            "placement",
            "orientation",
            "extent",
            "profile",
            "revolve",
            "path",
            "cross_sections",
        )
    }
    target = nested["target"]
    target["required"] = ["face"]
    face = _schema_properties(target, "$defs.featureTarget")["face"]
    face["required"] = ["resolved_face"]
    _schema_properties(face, "$defs.targetFaceObject")["resolved_face"]["enum"] = ["+Z", "-Z", "+X", "-X", "+Y", "-Y"]
    shared = {
        "id": _scalar_property(props, "id", "$defs.feature.properties"),
        "target": nested["target"],
        "placement": nested["placement"],
        "orientation": nested["orientation"],
        "extent": nested["extent"],
    }

    def variant(
        family: str,
        *,
        required: tuple[str, ...],
        specific: dict[str, Any],
        description: str,
    ) -> dict[str, Any]:
        return {
            "type": "object",
            "description": description,
            "additionalProperties": False,
            "required": ["family", "target", *required],
            "properties": {
                **copy.deepcopy(shared),
                "family": {
                    "type": "string",
                    "enum": [family],
                    "description": description,
                },
                **specific,
            },
        }

    return {
        "anyOf": [
            variant(
                "circular_through_hole",
                required=("dimensions_mm",),
                specific={
                    "dimensions_mm": _required_dimensions(
                        ("diameter_mm",),
                        "Circular through-hole size. Preserve the requested diameter exactly.",
                    )
                },
                description="One circular hole cut through the target body.",
            ),
            variant(
                "rectangular_through_cutout",
                required=("dimensions_mm",),
                specific={
                    "dimensions_mm": _required_dimensions(
                        ("width_mm", "height_mm"),
                        "Two rectangular face dimensions for the through-cutout.",
                    )
                },
                description="One rectangular cutout passing through the target body.",
            ),
            variant(
                "slot_through_cutout",
                required=("dimensions_mm",),
                specific={
                    "dimensions_mm": _required_dimensions(
                        ("length_mm", "width_mm"),
                        "Overall slot length and slot width.",
                    )
                },
                description="One stadium-shaped slot passing through the target body.",
            ),
            variant(
                "rectangular_extruded_pad",
                required=("dimensions_mm",),
                specific={
                    "dimensions_mm": _required_dimensions(
                        ("width_mm", "height_mm", "distance_mm"),
                        "Pad face dimensions and extrusion height. For a 70 by 50 pad 10 high, use "
                        "width_mm 70, height_mm 50, and distance_mm 10.",
                    )
                },
                description="One additive rectangular pad extruded from the target face.",
            ),
            variant(
                "profile_cutout",
                required=("profile",),
                specific={"profile": nested["profile"]},
                description="One cutout defined by a closed two-dimensional profile.",
            ),
            variant(
                "revolved_profile",
                required=("revolve",),
                specific={
                    "profile": nested["profile"],
                    "revolve": nested["revolve"],
                },
                description="One feature formed by revolving a profile around an axis.",
            ),
            variant(
                "swept_protrusion",
                required=("cross_sections",),
                specific={
                    "path": nested["path"],
                    "cross_sections": nested["cross_sections"],
                },
                description="One additive feature swept along a path through cross-sections.",
            ),
        ]
    }


@functools.cache
def _load_cached_gemini_response_schema() -> dict[str, Any]:
    canonical = load_proposal_schema()
    root_props = _schema_properties(canonical, "root")
    definitions = _schema_object(canonical.get("$defs"), "$defs")

    base_body = _compact_base_body(_schema_object(definitions.get("baseBody"), "$defs.baseBody"))
    boolean_operation = _compact_boolean_operation(
        _schema_object(definitions.get("booleanOperation"), "$defs.booleanOperation")
    )
    feature = _compact_feature(
        _schema_object(definitions.get("feature"), "$defs.feature"),
        definitions,
    )

    if set(root_props) != {"base_body", "primitive_bodies", "boolean_operations", "features"}:
        raise ProposalError("configuration_invalid", "Proposal schema root properties changed unexpectedly")

    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["base_body"],
        "properties": {
            "base_body": base_body,
            "primitive_bodies": {"type": "array", "items": copy.deepcopy(base_body)},
            "boolean_operations": {"type": "array", "items": boolean_operation},
            "features": {"type": "array", "items": feature},
        },
    }


def load_gemini_response_schema() -> dict[str, Any]:
    """Return an isolated, compact provider schema; local validation remains canonical."""
    return copy.deepcopy(_load_cached_gemini_response_schema())
