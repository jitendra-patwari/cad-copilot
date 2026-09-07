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


def _open_object() -> dict[str, Any]:
    return {"type": "object", "additionalProperties": True}


def _numeric_object() -> dict[str, Any]:
    return {"type": "object", "additionalProperties": {"type": "number"}}


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


def _compact_feature(feature: dict[str, Any]) -> dict[str, Any]:
    props = _schema_properties(feature, "$defs.feature")
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["family"],
        "properties": {
            "id": _scalar_property(props, "id", "$defs.feature.properties"),
            "family": _scalar_property(props, "family", "$defs.feature.properties"),
            "target": _open_object(),
            "placement": _open_object(),
            "orientation": _open_object(),
            "extent": _open_object(),
            "dimensions_mm": _numeric_object(),
            "profile": _open_object(),
            "revolve": _open_object(),
            "path": _open_object(),
            "cross_sections": {"type": "array", "items": _open_object()},
        },
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
    feature = _compact_feature(_schema_object(definitions.get("feature"), "$defs.feature"))

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
