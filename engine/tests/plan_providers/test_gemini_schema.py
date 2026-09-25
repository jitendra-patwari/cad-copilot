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


def _feature_variants(projected: dict[str, Any]) -> dict[str, dict[str, Any]]:
    variants = projected["properties"]["features"]["items"]["anyOf"]
    assert isinstance(variants, list)
    return {variant["properties"]["family"]["enum"][0]: variant for variant in variants}


def test_guidance_schema_derives_current_family_enums() -> None:
    definitions = _canonical_definitions()
    projected = load_gemini_response_schema()

    expected_base_families = definitions["baseBody"]["properties"]["family"]["enum"]
    expected_feature_families = definitions["feature"]["properties"]["family"]["enum"]
    assert projected["properties"]["base_body"]["properties"]["family"]["enum"] == expected_base_families
    assert set(_feature_variants(projected)) == set(expected_feature_families)


def test_guidance_schema_tracks_public_object_property_names() -> None:
    definitions = _canonical_definitions()
    projected = load_gemini_response_schema()
    variants = _feature_variants(projected)

    assert set(projected["properties"]["base_body"]["properties"]) == set(definitions["baseBody"]["properties"])
    assert set(projected["properties"]["boolean_operations"]["items"]["properties"]) == set(
        definitions["booleanOperation"]["properties"]
    )
    projected_feature_properties = set().union(*(variant["properties"].keys() for variant in variants.values()))
    assert projected_feature_properties == set(definitions["feature"]["properties"])


def test_guidance_schema_uses_one_supported_union_and_omits_other_complex_constructs() -> None:
    canonical_text = json.dumps(load_proposal_schema())
    projected_text = json.dumps(load_gemini_response_schema())

    assert all(keyword in canonical_text for keyword in ('"$defs"', '"$ref"', '"anyOf"', '"maxItems"'))
    assert projected_text.count('"anyOf"') == 1
    assert all(
        keyword not in projected_text for keyword in ('"$defs"', '"$ref"', '"allOf"', '"if"', '"then"', '"maxItems"')
    )


def test_guidance_schema_keeps_family_specific_values_required_and_typed() -> None:
    projected = load_gemini_response_schema()
    base = projected["properties"]["base_body"]
    variants = _feature_variants(projected)
    hole_dimensions = variants["circular_through_hole"]["properties"]["dimensions_mm"]
    pad_dimensions = variants["rectangular_extruded_pad"]["properties"]["dimensions_mm"]

    assert base["properties"]["dimensions_mm"]["additionalProperties"] == {"type": "number"}
    assert hole_dimensions["required"] == ["diameter_mm"]
    assert hole_dimensions["additionalProperties"] is False
    assert pad_dimensions["required"] == ["width_mm", "height_mm", "distance_mm"]
    assert "70 by 50 pad 10 high" in pad_dimensions["description"]
    for variant in variants.values():
        assert "target" in variant["required"]
        target = variant["properties"]["target"]
        assert target["required"] == ["face"]
        face = target["properties"]["face"]
        assert face["required"] == ["resolved_face"]
        assert face["properties"]["resolved_face"]["enum"] == ["+Z", "-Z", "+X", "-X", "+Y", "-Y"]
    assert variants["profile_cutout"]["properties"]["profile"]["required"] == ["points"]
    profile_points = variants["profile_cutout"]["properties"]["profile"]["properties"]["points"]
    assert profile_points["items"]["additionalProperties"] is False
    assert variants["revolved_profile"]["properties"]["revolve"]["required"] == ["axis"]
    assert variants["swept_protrusion"]["properties"]["cross_sections"]["items"]["type"] == "object"


def test_guidance_schema_returns_an_isolated_copy() -> None:
    first = load_gemini_response_schema()
    second = load_gemini_response_schema()

    first["properties"].clear()

    assert second["properties"]
