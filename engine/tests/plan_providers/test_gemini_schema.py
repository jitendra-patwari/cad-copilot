"""Tests for the compact Gemini guidance schema."""

from __future__ import annotations

import json
from typing import Any

from jsonschema import Draft202012Validator

from plan_providers.gemini_schema import load_gemini_response_schema
from plan_providers.proposal import load_proposal_schema


def _canonical_definitions() -> dict[str, Any]:
    canonical = load_proposal_schema()
    definitions = canonical["$defs"]
    assert isinstance(definitions, dict)
    return definitions


def test_guidance_schema_is_valid_and_has_the_canonical_root_shape() -> None:
    projected = load_gemini_response_schema()

    Draft202012Validator.check_schema(projected)
    assert projected["required"] == ["base_body"]
    assert set(projected["properties"]) == set(load_proposal_schema()["properties"])
    assert projected["additionalProperties"] is False


def test_guidance_schema_derives_current_family_enums() -> None:
    definitions = _canonical_definitions()
    projected = load_gemini_response_schema()

    expected_base_families = definitions["baseBody"]["properties"]["family"]["enum"]
    expected_feature_families = definitions["feature"]["properties"]["family"]["enum"]
    assert projected["properties"]["base_body"]["properties"]["family"]["enum"] == expected_base_families
    assert projected["properties"]["features"]["items"]["properties"]["family"]["enum"] == expected_feature_families


def test_guidance_schema_tracks_public_object_property_names() -> None:
    definitions = _canonical_definitions()
    projected = load_gemini_response_schema()

    assert set(projected["properties"]["base_body"]["properties"]) == set(definitions["baseBody"]["properties"])
    assert set(projected["properties"]["boolean_operations"]["items"]["properties"]) == set(
        definitions["booleanOperation"]["properties"]
    )
    assert set(projected["properties"]["features"]["items"]["properties"]) == set(definitions["feature"]["properties"])


def test_guidance_schema_omits_high_complexity_validation_constructs() -> None:
    canonical_text = json.dumps(load_proposal_schema())
    projected_text = json.dumps(load_gemini_response_schema())

    assert all(keyword in canonical_text for keyword in ('"$defs"', '"$ref"', '"anyOf"', '"maxItems"'))
    assert all(keyword not in projected_text for keyword in ('"$defs"', '"$ref"', '"anyOf"', '"maxItems"'))


def test_guidance_schema_keeps_nested_values_typed() -> None:
    projected = load_gemini_response_schema()
    base = projected["properties"]["base_body"]
    feature = projected["properties"]["features"]["items"]

    assert base["properties"]["dimensions_mm"]["additionalProperties"] == {"type": "number"}
    assert feature["properties"]["dimensions_mm"]["additionalProperties"] == {"type": "number"}
    assert feature["properties"]["cross_sections"]["items"]["type"] == "object"


def test_guidance_schema_returns_an_isolated_copy() -> None:
    first = load_gemini_response_schema()
    second = load_gemini_response_schema()

    first["properties"].clear()

    assert second["properties"]
