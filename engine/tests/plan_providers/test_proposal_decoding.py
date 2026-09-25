"""Unit tests for bounded proposal recovery, canonical validation, and envelope binding."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any, cast

import pytest

from application.models import PromptGenerationRequest
from application.service import _prepare_canonical_plan
from drivers.solidedge.executor import SolidEdgeExecutor
from geometry.plan_lowering import lower_validated_feature_plan_to_payload
from geometry.plan_models import RectangularThroughCutoutFeature
from geometry.plan_parser import feature_plan_from_dict
from geometry.plan_validation import validate_feature_plan
from plan_providers.proposal import (
    MAX_PROVIDER_RESPONSE_CHARS,
    ProposalError,
    decode_and_bind_proposal,
    get_proposal_validator,
    load_proposal_schema,
)


def _make_prompt_request(request_id: str = "req-test-proposal-01") -> PromptGenerationRequest:
    return PromptGenerationRequest(
        contract_version="1.0",
        request_id=request_id,
        kind="prompt_to_cad",
        unit="mm",
        prompt="A 25 mm cube",
    )


TEST_SOURCE_ID: str = "test-provider-model"


def _require_object(value: object) -> dict[str, Any]:
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def _require_object_list(value: object) -> list[dict[str, Any]]:
    assert isinstance(value, list)
    assert all(isinstance(item, dict) for item in value)
    return cast(list[dict[str, Any]], value)


# ---------------------------------------------------------------------------
# Schema Resource Loading & Caching
# ---------------------------------------------------------------------------


def test_load_proposal_schema_returns_valid_schema() -> None:
    schema = load_proposal_schema()
    assert isinstance(schema, dict)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["title"] == "FeaturePlanProposal"
    assert "base_body" in schema["required"]
    assert schema["additionalProperties"] is False


def test_load_proposal_schema_returns_isolated_deep_copy() -> None:
    schema1 = load_proposal_schema()
    schema1["mutated_key"] = "sentinel"
    schema2 = load_proposal_schema()
    assert "mutated_key" not in schema2


def test_get_proposal_validator_caches_instance() -> None:
    val1 = get_proposal_validator()
    val2 = get_proposal_validator()
    assert val1 is val2


# ---------------------------------------------------------------------------
# Strict Input & JSON Parsing Validations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("empty_input", ["", "   ", "\n\t  \r\n"])
def test_decode_rejects_empty_or_whitespace_text(empty_input: str) -> None:
    req = _make_prompt_request()
    with pytest.raises(ProposalError) as exc_info:
        decode_and_bind_proposal(empty_input, req, TEST_SOURCE_ID)
    assert exc_info.value.code == "response_empty"
    assert "empty or whitespace" in str(exc_info.value)


def test_decode_rejects_non_string_input() -> None:
    req = _make_prompt_request()
    with pytest.raises(ProposalError) as exc_info:
        decode_and_bind_proposal(None, req, TEST_SOURCE_ID)  # type: ignore[arg-type]
    assert exc_info.value.code == "response_empty"


def test_decode_rejects_oversized_text() -> None:
    req = _make_prompt_request()
    oversized = "x" * (MAX_PROVIDER_RESPONSE_CHARS + 1)
    with pytest.raises(ProposalError) as exc_info:
        decode_and_bind_proposal(oversized, req, TEST_SOURCE_ID)
    assert exc_info.value.code == "response_invalid"
    assert "exceeds maximum allowed limit" in str(exc_info.value)


@pytest.mark.parametrize(
    "malformed_json",
    [
        "not json at all",
        "{unquoted_key: 123}",
        '{"broken": "closing"',
        '{"a": 1,}',
    ],
)
def test_decode_rejects_malformed_json(malformed_json: str) -> None:
    req = _make_prompt_request()
    with pytest.raises(ProposalError) as exc_info:
        decode_and_bind_proposal(malformed_json, req, TEST_SOURCE_ID)
    assert exc_info.value.code == "response_invalid"
    assert "not valid JSON" in str(exc_info.value)


def test_decode_rejects_duplicate_object_keys() -> None:
    req = _make_prompt_request()
    dup_json = '{"base_body": {"family": "rectangular_prism", "family": "cylinder"}}'
    with pytest.raises(ProposalError) as exc_info:
        decode_and_bind_proposal(dup_json, req, TEST_SOURCE_ID)
    assert exc_info.value.code == "response_invalid"
    assert "not valid JSON" in str(exc_info.value)


@pytest.mark.parametrize("constant_val", ["NaN", "Infinity", "-Infinity"])
def test_decode_rejects_non_finite_constants(constant_val: str) -> None:
    req = _make_prompt_request()
    raw = f'{{"base_body": {{"family": "rectangular_prism", "dimensions_mm": {{"length": {constant_val}}}}}}}'
    with pytest.raises(ProposalError) as exc_info:
        decode_and_bind_proposal(raw, req, TEST_SOURCE_ID)
    assert exc_info.value.code == "response_invalid"
    assert "not valid JSON" in str(exc_info.value)


@pytest.mark.parametrize("overflow_val", ["1e999", "-1e999", "1e1000", "-1e1000"])
def test_decode_rejects_exponent_overflow_non_finite(overflow_val: str) -> None:
    """Proves that exponent overflow yielding float('inf') is rejected."""
    req = _make_prompt_request()
    raw = f'{{"base_body": {{"family": "rectangular_prism", "dimensions_mm": {{"length": {overflow_val}, "width": 10, "thickness": 5}}}}}}'
    with pytest.raises(ProposalError) as exc_info:
        decode_and_bind_proposal(raw, req, TEST_SOURCE_ID)
    assert exc_info.value.code == "response_invalid"
    assert "not valid JSON" in str(exc_info.value)


@pytest.mark.parametrize(
    "non_object_root",
    [
        "[]",
        '[{"base_body": {"family": "cylinder"}}]',
        '"a string root"',
        "42",
        "true",
    ],
)
def test_decode_rejects_non_object_root(non_object_root: str) -> None:
    req = _make_prompt_request()
    with pytest.raises(ProposalError) as exc_info:
        decode_and_bind_proposal(non_object_root, req, TEST_SOURCE_ID)
    assert exc_info.value.code == "response_invalid"
    assert "must be a JSON object" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Strict Sealed Schema & Object Constraints
# ---------------------------------------------------------------------------


def test_decode_rejects_missing_base_body() -> None:
    req = _make_prompt_request()
    raw = json.dumps({"features": []})
    with pytest.raises(ProposalError) as exc_info:
        decode_and_bind_proposal(raw, req, TEST_SOURCE_ID)
    assert exc_info.value.code == "response_invalid"
    assert "failed schema validation" in str(exc_info.value)


def test_decode_rejects_unsupported_base_family() -> None:
    req = _make_prompt_request()
    raw = json.dumps({"base_body": {"family": "torus", "dimensions_mm": {"radius": 10}}})
    with pytest.raises(ProposalError) as exc_info:
        decode_and_bind_proposal(raw, req, TEST_SOURCE_ID)
    assert exc_info.value.code == "response_invalid"
    assert "failed schema validation" in str(exc_info.value)


def test_decode_rejects_unsupported_feature_family() -> None:
    req = _make_prompt_request()
    raw = json.dumps(
        {
            "base_body": {"family": "rectangular_prism", "dimensions_mm": {"length": 50, "width": 40, "thickness": 10}},
            "features": [{"family": "chamfer", "dimensions_mm": {"distance": 2.0}}],
        }
    )
    with pytest.raises(ProposalError) as exc_info:
        decode_and_bind_proposal(raw, req, TEST_SOURCE_ID)
    assert exc_info.value.code == "response_invalid"
    assert "failed schema validation" in str(exc_info.value)


def test_decode_rejects_unknown_base_body_properties() -> None:
    req = _make_prompt_request()
    raw = json.dumps(
        {
            "base_body": {
                "family": "cylinder",
                "dimensions_mm": {"radius": 10, "height": 20},
                "unknown_extra_property": "bad",
            }
        }
    )
    with pytest.raises(ProposalError) as exc_info:
        decode_and_bind_proposal(raw, req, TEST_SOURCE_ID)
    assert exc_info.value.code == "response_invalid"


def test_decode_defaults_missing_base_body_dimensions() -> None:
    req = replace(_make_prompt_request(), prompt="Make a rectangular block")
    raw = json.dumps({"base_body": {"family": "rectangular_prism"}})
    proposal = decode_and_bind_proposal(raw, req, TEST_SOURCE_ID)
    assert _require_object(proposal.plan_payload["base_body"])["dimensions_mm"] == {
        "length": 50.0,
        "width": 40.0,
        "thickness": 10.0,
    }
    assert len(proposal.applied_defaults) == 3
    assert {"code": "AI_DIMENSIONS_ASSUMED"} in proposal.warnings

    prepared, warnings = _prepare_canonical_plan(req, proposal, "capability_first")
    assert len(prepared.validated_plan.defaults_applied) >= 3
    assert any("illustrative millimeter defaults" in warning for warning in prepared.warnings)
    assert warnings


@pytest.mark.parametrize("dimensions", [None, {"length_mm": 150, "width_mm": 100}])
def test_decode_never_defaults_explicit_base_dimensions(dimensions: dict[str, int] | None) -> None:
    req = replace(
        _make_prompt_request(),
        prompt="Create a 150 x 100 x 12 mm rectangular base.",
    )
    base: dict[str, Any] = {"family": "rectangular_prism"}
    if dimensions is not None:
        base["dimensions_mm"] = dimensions
    with pytest.raises(ProposalError) as exc_info:
        decode_and_bind_proposal(json.dumps({"base_body": base}), req, TEST_SOURCE_ID)
    assert exc_info.value.code == "response_invalid"


def test_decode_rejects_partial_dimensions_even_for_vague_base() -> None:
    req = replace(_make_prompt_request(), prompt="Make a rectangular block")
    raw = json.dumps({"base_body": {"family": "rectangular_prism", "dimensions_mm": {"length_mm": 50}}})
    with pytest.raises(ProposalError) as exc_info:
        decode_and_bind_proposal(raw, req, TEST_SOURCE_ID)
    assert exc_info.value.code == "response_invalid"


@pytest.mark.parametrize(
    ("prompt", "base_dimensions", "features"),
    [
        (
            "Create a rectangular mounting plate 120 mm long, 80 mm wide, and 15 mm thick, "
            "with a 25 mm diameter circular through-hole in the center.",
            {"length": 120, "width": 80, "thickness": 15},
            [{"family": "circular_through_hole", "dimensions_mm": {"diameter": 25}}],
        ),
        (
            "Design a rectangular base block 100 mm long, 100 mm wide, and 20 mm high "
            "with a centered rectangular through-cutout 60 mm long and 40 mm wide.",
            {"length": 100, "width": 100, "thickness": 20},
            [{"family": "rectangular_through_cutout", "dimensions_mm": {"width": 40, "height": 60}}],
        ),
        (
            "Create a 50x40x10 rectangular block",
            {"length": 50, "width": 40, "thickness": 10},
            [],
        ),
        (
            "Design a rectangular base 150 mm long, 100 mm wide, and 12 mm thick with a centered "
            "rectangular pad 70 mm long, 50 mm wide, and 10 mm high, and a 16 mm circular through-hole.",
            {"length": 150, "width": 100, "thickness": 12},
            [
                {
                    "family": "rectangular_extruded_pad",
                    "dimensions_mm": {"width": 70, "height": 50, "distance": 10},
                },
                {
                    "family": "circular_through_hole",
                    "dimensions_mm": {"diameter": 16},
                    "target": {"face": {"resolved_face": "-Z"}},
                },
            ],
        ),
    ],
)
def test_reported_prompt_shapes_prepare_valid_plan(
    prompt: str, base_dimensions: dict[str, int], features: list[dict[str, Any]]
) -> None:
    """Representative provider plans for the reported prompts pass offline gates."""
    request = replace(_make_prompt_request(), prompt=prompt)
    response = json.dumps(
        {
            "base_body": {"family": "rectangular_prism", "dimensions_mm": base_dimensions},
            "features": features,
        }
    )
    proposal = decode_and_bind_proposal(response, request, TEST_SOURCE_ID)
    prepared, _warnings = _prepare_canonical_plan(request, proposal, "capability_first")
    assert prepared.validated_plan.base_body.family == "rectangular_prism"
    assert len(prepared.validated_plan.features) == len(features)


_MIXED_FACE_PROMPT = (
    "Create a rectangular block 100 mm long, 80 mm wide, and 40 mm thick with a 20 mm circular "
    "through-hole on the top face and a 30 mm wide by 15 mm high rectangular through-cutout on the front face."
)


_REORDERED_FACE_PROMPT = (
    "Create a rectangular block 100 mm long, 80 mm wide, and 40 mm thick with a 20 mm circular "
    "through-hole and a 30 mm wide by 15 mm high rectangular through-cutout. "
    "Put the cutout on the front face and the hole on the top face."
)
_REORDERED_SLOT_FACE_PROMPT = (
    "Create a rectangular block 100 mm long, 80 mm wide, and 40 mm thick with a 20 mm circular "
    "through-hole and a 30 mm long by 15 mm wide slot, with the slot on the front face "
    "and the hole on the top face."
)


_PAD_THROUGH_HOLE_PROMPT = (
    "Design a rectangular base 150 mm long, 100 mm wide, and 12 mm thick with a centered "
    "rectangular pad 70 mm long, 50 mm wide, and 10 mm high, and a 16 mm circular through-hole."
)


def test_reported_pad_hole_plan_rejects_base_top_cut_and_accepts_bottom_through_cut() -> None:
    request = replace(_make_prompt_request(), prompt=_PAD_THROUGH_HOLE_PROMPT)
    payload = {
        "base_body": {
            "family": "rectangular_prism",
            "dimensions_mm": {"length_mm": 150, "width_mm": 100, "thickness_mm": 12},
        },
        "features": [
            {
                "family": "rectangular_extruded_pad",
                "dimensions_mm": {"width_mm": 70, "height_mm": 50, "distance_mm": 10},
                "target": {"face": {"resolved_face": "+Z"}},
            },
            {
                "family": "circular_through_hole",
                "dimensions_mm": {"diameter_mm": 16},
                "target": {"face": {"resolved_face": "+Z"}},
            },
        ],
    }
    with pytest.raises(ProposalError) as exc_info:
        decode_and_bind_proposal(json.dumps(payload), request, TEST_SOURCE_ID)
    assert exc_info.value.code == "response_invalid"

    payload["features"][1]["target"]["face"]["resolved_face"] = "-Z"
    proposal = decode_and_bind_proposal(json.dumps(payload), request, TEST_SOURCE_ID)
    prepared, _warnings = _prepare_canonical_plan(request, proposal, "capability_first")
    assert [feature.target_face for feature in prepared.validated_plan.features] == ["+Z", "-Z"]
    lowered = lower_validated_feature_plan_to_payload(prepared.validated_plan)
    sketches = [patch for patch in lowered["patches"] if patch["op"] == "ensure_sketch"]
    assert sketches[1]["origin_offset_mm"]["z_mm"] == 0.0
    assert SolidEdgeExecutor._preflight_patch_sequence(object.__new__(SolidEdgeExecutor), lowered["patches"]) is None


@pytest.mark.parametrize("hole_face,cutout_face", [(None, None), ("+Z", "+Z"), ("-Y", "+Z")])
def test_decode_rejects_reported_mixed_face_mismatch(hole_face: str | None, cutout_face: str | None) -> None:
    request = replace(_make_prompt_request(), prompt=_MIXED_FACE_PROMPT)
    hole: dict[str, Any] = {"family": "circular_through_hole", "dimensions_mm": {"diameter_mm": 20}}
    cutout: dict[str, Any] = {
        "family": "rectangular_through_cutout",
        "dimensions_mm": {"width_mm": 30, "height_mm": 15},
    }
    if hole_face is not None:
        hole["target"] = {"face": {"resolved_face": hole_face}}
    if cutout_face is not None:
        cutout["target"] = {"face": {"resolved_face": cutout_face}}
    raw = json.dumps(
        {
            "base_body": {
                "family": "rectangular_prism",
                "dimensions_mm": {"length_mm": 100, "width_mm": 80, "thickness_mm": 40},
            },
            "features": [hole, cutout],
        }
    )
    with pytest.raises(ProposalError) as exc_info:
        decode_and_bind_proposal(raw, request, TEST_SOURCE_ID)
    assert exc_info.value.code == "response_invalid"


def test_decode_preserves_reported_top_hole_and_front_cutout_through_lowering() -> None:
    request = replace(_make_prompt_request(), prompt=_MIXED_FACE_PROMPT)
    raw = json.dumps(
        {
            "base_body": {
                "family": "rectangular_prism",
                "dimensions_mm": {"length_mm": 100, "width_mm": 80, "thickness_mm": 40},
            },
            "features": [
                {
                    "family": "circular_through_hole",
                    "dimensions_mm": {"diameter_mm": 20},
                    "target": {"face": {"resolved_face": "+Z"}},
                },
                {
                    "family": "rectangular_through_cutout",
                    "dimensions_mm": {"width_mm": 30, "height_mm": 15},
                    "target": {"face": {"resolved_face": "-Y"}},
                },
            ],
        }
    )
    proposal = decode_and_bind_proposal(raw, request, TEST_SOURCE_ID)
    prepared, _warnings = _prepare_canonical_plan(request, proposal, "capability_first")
    assert [feature.target_face for feature in prepared.validated_plan.features] == ["+Z", "-Y"]
    lowered = lower_validated_feature_plan_to_payload(prepared.validated_plan)
    sketch_patches = [patch for patch in lowered["patches"] if patch["op"] == "ensure_sketch"]
    assert [patch["plane"] for patch in sketch_patches] == ["XY", "XZ"]
    preflight = SolidEdgeExecutor._preflight_patch_sequence(object.__new__(SolidEdgeExecutor), lowered["patches"])
    assert preflight is None


@pytest.mark.parametrize(
    ("hole_face", "cutout_face", "valid"),
    [("+Z", "-Y", True), ("-Y", "+Z", False)],
)
@pytest.mark.parametrize(
    ("prompt", "cutout_family", "cutout_dimensions"),
    [
        (_REORDERED_FACE_PROMPT, "rectangular_through_cutout", {"width_mm": 30, "height_mm": 15}),
        (_REORDERED_SLOT_FACE_PROMPT, "slot_through_cutout", {"length_mm": 30, "width_mm": 15}),
    ],
)
def test_decode_matches_reordered_face_mentions_to_their_features(
    hole_face: str,
    cutout_face: str,
    valid: bool,
    prompt: str,
    cutout_family: str,
    cutout_dimensions: dict[str, int],
) -> None:
    request = replace(_make_prompt_request(), prompt=prompt)
    raw = json.dumps(
        {
            "base_body": {
                "family": "rectangular_prism",
                "dimensions_mm": {"length_mm": 100, "width_mm": 80, "thickness_mm": 40},
            },
            "features": [
                {
                    "family": "circular_through_hole",
                    "dimensions_mm": {"diameter_mm": 20},
                    "target": {"face": {"resolved_face": hole_face}},
                },
                {
                    "family": cutout_family,
                    "dimensions_mm": cutout_dimensions,
                    "target": {"face": {"resolved_face": cutout_face}},
                },
            ],
        }
    )
    if not valid:
        with pytest.raises(ProposalError) as exc_info:
            decode_and_bind_proposal(raw, request, TEST_SOURCE_ID)
        assert exc_info.value.code == "response_invalid"
        return

    proposal = decode_and_bind_proposal(raw, request, TEST_SOURCE_ID)
    prepared, _warnings = _prepare_canonical_plan(request, proposal, "capability_first")
    assert [feature.target_face for feature in prepared.validated_plan.features] == ["+Z", "-Y"]


def test_decode_recovers_fenced_json_with_preamble() -> None:
    req = _make_prompt_request()
    raw = (
        "Here is a CAD proposal:\n```json\n"
        '{"base_body":{"family":"rectangular_prism","dimensions_mm":'
        '{"length":50,"width":40,"thickness":10}}}'
        "\n```"
    )
    proposal = decode_and_bind_proposal(raw, req, TEST_SOURCE_ID)
    assert _require_object(proposal.plan_payload["base_body"])["family"] == "rectangular_prism"
    assert {"code": "AI_PROPOSAL_NORMALIZED"} in proposal.warnings


def test_decode_recovers_fenced_json_despite_braces_in_surrounding_text() -> None:
    req = _make_prompt_request()
    raw = (
        "Note: {parameter} may use a default.\n```json\n"
        '{"base_body":{"family":"rectangular_prism","dimensions_mm":'
        '{"length":50,"width":40,"thickness":10}}}'
        "\n```\nFurther changes can use {for example, depth}."
    )
    proposal = decode_and_bind_proposal(raw, req, TEST_SOURCE_ID)
    assert _require_object(proposal.plan_payload["base_body"])["family"] == "rectangular_prism"


def test_decode_recovers_unfenced_json_despite_non_json_braces() -> None:
    req = _make_prompt_request()
    body = '{"base_body":{"family":"rectangular_prism","dimensions_mm":{"length":50,"width":40,"thickness":10}}}'
    proposal = decode_and_bind_proposal(f"Note {{parameter}}. {body} End {{example}}.", req, TEST_SOURCE_ID)
    assert _require_object(proposal.plan_payload["base_body"])["family"] == "rectangular_prism"


def test_decode_rejects_ambiguous_multiple_json_objects() -> None:
    req = _make_prompt_request()
    body = '{"base_body":{"family":"rectangular_prism","dimensions_mm":{"length":50,"width":40,"thickness":10}}}'
    with pytest.raises(ProposalError) as exc_info:
        decode_and_bind_proposal(f"Here are two choices: {body} or {body}", req, TEST_SOURCE_ID)
    assert exc_info.value.code == "response_invalid"


def test_decode_rejects_ambiguous_multiple_json_fences() -> None:
    req = _make_prompt_request()
    body = '{"base_body":{"family":"rectangular_prism","dimensions_mm":{"length":50,"width":40,"thickness":10}}}'
    with pytest.raises(ProposalError) as exc_info:
        decode_and_bind_proposal(f"```json\n{body}\n```\n```json\n{body}\n```", req, TEST_SOURCE_ID)
    assert exc_info.value.code == "response_invalid"


def test_decode_rejects_fenced_json_with_another_complete_object_outside() -> None:
    req = _make_prompt_request()
    body = '{"base_body":{"family":"rectangular_prism","dimensions_mm":{"length":50,"width":40,"thickness":10}}}'
    with pytest.raises(ProposalError) as exc_info:
        decode_and_bind_proposal(f"```json\n{body}\n```\nAlternative: {body}", req, TEST_SOURCE_ID)
    assert exc_info.value.code == "response_invalid"


@pytest.mark.parametrize("base_family", ["rectangular_prism", "cylinder", "sphere"])
def test_decode_rejects_dimensionless_feature_for_every_supported_base(base_family: str) -> None:
    req = _make_prompt_request()
    base_dimensions = {
        "rectangular_prism": {"length": 100, "width": 80, "thickness": 10},
        "cylinder": {"radius": 25, "height": 20},
        "sphere": {"radius": 25},
    }[base_family]
    raw = json.dumps(
        {
            "base_body": {
                "family": base_family,
                "dimensions_mm": base_dimensions,
            },
            "features": [{"family": "circular_through_hole"}],
        }
    )
    with pytest.raises(ProposalError) as exc_info:
        decode_and_bind_proposal(raw, req, TEST_SOURCE_ID)
    assert exc_info.value.code == "response_invalid"


def test_decode_rejects_captured_pad_with_missing_footprint_and_extrusion_dimensions() -> None:
    req = _make_prompt_request()
    raw = json.dumps(
        {
            "base_body": {
                "family": "rectangular_prism",
                "dimensions_mm": {"length_mm": 150, "width_mm": 100, "thickness_mm": 12},
            },
            "features": [
                {
                    "family": "rectangular_extruded_pad",
                    "dimensions_mm": {"width_mm": 70.00000000000001},
                },
                {
                    "family": "circular_through_hole",
                    "dimensions_mm": {"diameter_mm": 16},
                },
            ],
        }
    )
    with pytest.raises(ProposalError) as exc_info:
        decode_and_bind_proposal(raw, req, TEST_SOURCE_ID)
    assert exc_info.value.code == "response_invalid"


def test_decode_preserves_rectangular_cutout_length_as_height_alias() -> None:
    req = _make_prompt_request()
    raw = json.dumps(
        {
            "base_body": {
                "family": "rectangular_prism",
                "dimensions_mm": {"length": 100, "width": 100, "thickness": 20},
            },
            "features": [
                {
                    "family": "rectangular_through_cutout",
                    "dimensions_mm": {"width": 40, "length": 60},
                }
            ],
        }
    )
    proposal = decode_and_bind_proposal(raw, req, TEST_SOURCE_ID)
    feature_dimensions = _require_object_list(proposal.plan_payload["features"])[0]["dimensions_mm"]
    assert feature_dimensions == {"width": 40, "length": 60}
    assert not proposal.applied_defaults
    prepared, _warnings = _prepare_canonical_plan(req, proposal, "capability_first")
    prepared_feature = prepared.validated_plan.features[0]
    assert isinstance(prepared_feature, RectangularThroughCutoutFeature)
    assert prepared_feature.height_mm == 60


def test_decode_rejects_trace_shape_with_duplicate_dimensionless_holes_and_incomplete_profile() -> None:
    req = _make_prompt_request()
    raw = json.dumps(
        {
            "base_body": {
                "family": "rectangular_prism",
                "dimensions_mm": {"length_mm": 120, "width_mm": 80, "thickness_mm": 15},
            },
            "features": [
                {"family": "circular_through_hole", "target": {"body_id": "body.main"}},
                {
                    "family": "circular_through_hole",
                    "placement": {"mode": "face_local_center"},
                    "profile": {},
                },
            ],
        }
    )
    with pytest.raises(ProposalError) as exc_info:
        decode_and_bind_proposal(raw, req, TEST_SOURCE_ID)
    assert exc_info.value.code == "response_invalid"


def test_decode_rejects_unknown_geometry_key_instead_of_changing_the_part() -> None:
    req = _make_prompt_request()
    raw = json.dumps(
        {
            "base_body": {
                "family": "rectangular_prism",
                "dimensions_mm": {"length": 100, "width": 80, "thickness": 10, "unrecognized_shape_hint": 9},
            }
        }
    )
    with pytest.raises(ProposalError) as exc_info:
        decode_and_bind_proposal(raw, req, TEST_SOURCE_ID)
    assert exc_info.value.code == "response_invalid"


def test_decode_rejects_arbitrarily_nested_dimensions() -> None:
    req = _make_prompt_request()
    raw = json.dumps(
        {
            "base_body": {
                "family": "rectangular_prism",
                "dimensions_mm": {"length": {"nested": 50}, "width": 40, "thickness": 10},
            }
        }
    )
    with pytest.raises(ProposalError) as exc_info:
        decode_and_bind_proposal(raw, req, TEST_SOURCE_ID)
    assert exc_info.value.code == "response_invalid"


def test_decode_normalizes_provider_dimensions_alias() -> None:
    req = _make_prompt_request()
    raw = json.dumps(
        {
            "base_body": {
                "family": "cylinder",
                "dimensions": {"radius": 10, "height": 20},
            }
        }
    )
    proposal = decode_and_bind_proposal(raw, req, TEST_SOURCE_ID)
    assert _require_object(proposal.plan_payload["base_body"])["dimensions_mm"] == {"radius": 10, "height": 20}
    assert {"code": "AI_PROPOSAL_NORMALIZED"} in proposal.warnings


@pytest.mark.parametrize("missing_field", ["target_body_id", "tool_body_id", "result_body_id"])
def test_decode_rejects_incomplete_boolean_operations(missing_field: str) -> None:
    req = _make_prompt_request()
    op = {
        "operation": "union",
        "target_body_id": "body.main",
        "tool_body_id": "body.tool",
        "result_body_id": "body.res",
    }
    del op[missing_field]
    raw = json.dumps(
        {
            "base_body": {"family": "cylinder", "dimensions_mm": {"radius": 10, "height": 20}},
            "boolean_operations": [op],
        }
    )
    with pytest.raises(ProposalError) as exc_info:
        decode_and_bind_proposal(raw, req, TEST_SOURCE_ID)
    assert exc_info.value.code == "response_invalid"


@pytest.mark.parametrize("bad_cross_section", ["string_cross_section", 42, [1, 2]])
def test_decode_rejects_invalid_sweep_cross_sections(bad_cross_section: Any) -> None:
    req = _make_prompt_request()
    raw = json.dumps(
        {
            "base_body": {"family": "cylinder", "dimensions_mm": {"radius": 10, "height": 20}},
            "features": [
                {
                    "family": "swept_protrusion",
                    "path": {"type": "full_circle", "radius_mm": 50},
                    "cross_sections": [bad_cross_section],
                }
            ],
        }
    )
    with pytest.raises(ProposalError) as exc_info:
        decode_and_bind_proposal(raw, req, TEST_SOURCE_ID)
    assert exc_info.value.code == "response_invalid"


def test_decode_rejects_unknown_target_properties() -> None:
    req = _make_prompt_request()
    raw = json.dumps(
        {
            "base_body": {"family": "cylinder", "dimensions_mm": {"radius": 10, "height": 20}},
            "features": [
                {
                    "family": "circular_through_hole",
                    "target": {"body_id": "body.main", "unknown_target_field": "bad"},
                    "dimensions_mm": {"diameter": 10},
                }
            ],
        }
    )
    with pytest.raises(ProposalError) as exc_info:
        decode_and_bind_proposal(raw, req, TEST_SOURCE_ID)
    assert exc_info.value.code == "response_invalid"


def test_decode_rejects_excessive_collections() -> None:
    req = _make_prompt_request()
    features = [{"family": "circular_through_hole", "dimensions_mm": {"diameter": 5.0}} for _ in range(129)]
    raw = json.dumps(
        {
            "base_body": {"family": "rectangular_prism", "dimensions_mm": {"length": 50, "width": 40, "thickness": 10}},
            "features": features,
        }
    )
    with pytest.raises(ProposalError) as exc_info:
        decode_and_bind_proposal(raw, req, TEST_SOURCE_ID)
    assert exc_info.value.code == "response_invalid"
    assert "failed schema validation" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Privacy Protection & Fixed Structural Metadata
# ---------------------------------------------------------------------------


def test_decode_ignores_provider_part_or_design_intent_injection() -> None:
    """Provider-owned metadata is discarded, not copied into the bound plan."""
    req = _make_prompt_request("req-privacy-01")
    sentinel_prompt = "TOP_SECRET_PROMPT_SENTINEL_XYZ"

    raw_with_part = json.dumps(
        {
            "part": {"design_intent": sentinel_prompt},
            "base_body": {"family": "cylinder", "dimensions_mm": {"radius": 10, "height": 20}},
        }
    )
    part_proposal = decode_and_bind_proposal(raw_with_part, req, TEST_SOURCE_ID)
    assert sentinel_prompt not in json.dumps(part_proposal.plan_payload)
    assert {"code": "AI_PROPOSAL_NORMALIZED"} in part_proposal.warnings

    raw_with_intent = json.dumps(
        {
            "design_intent": sentinel_prompt,
            "base_body": {"family": "cylinder", "dimensions_mm": {"radius": 10, "height": 20}},
        }
    )
    intent_proposal = decode_and_bind_proposal(raw_with_intent, req, TEST_SOURCE_ID)
    assert sentinel_prompt not in json.dumps(intent_proposal.plan_payload)
    assert {"code": "AI_PROPOSAL_NORMALIZED"} in intent_proposal.warnings


@pytest.mark.parametrize("envelope_key", ["plan_version", "request_id", "part", "design_intent"])
def test_decode_ignores_provider_owned_envelope_fields(envelope_key: str) -> None:
    req = _make_prompt_request()
    raw_dict: dict[str, Any] = {
        "base_body": {
            "family": "rectangular_prism",
            "dimensions_mm": {"length": 25.0, "width": 25.0, "thickness": 25.0},
        },
        envelope_key: "injected_value",
    }
    raw = json.dumps(raw_dict)
    proposal = decode_and_bind_proposal(raw, req, TEST_SOURCE_ID)
    assert proposal.plan_payload["request_id"] == req.request_id
    assert proposal.plan_payload["units"] == "mm"
    assert {"code": "AI_PROPOSAL_NORMALIZED"} in proposal.warnings


def test_decode_accepts_redundant_mm_but_rejects_conflicting_units() -> None:
    req = _make_prompt_request()
    body = {"family": "rectangular_prism", "dimensions_mm": {"length": 50, "width": 40, "thickness": 10}}
    accepted = decode_and_bind_proposal(json.dumps({"base_body": body, "units": "mm"}), req, TEST_SOURCE_ID)
    assert accepted.plan_payload["units"] == "mm"
    with pytest.raises(ProposalError) as exc_info:
        decode_and_bind_proposal(json.dumps({"base_body": body, "units": "inch"}), req, TEST_SOURCE_ID)
    assert exc_info.value.code == "response_invalid"


def test_bound_proposal_sanitizes_part_metadata_and_label() -> None:
    """Proves that bound proposal uses safe structural metadata and lowering contains no user prompt."""
    req = _make_prompt_request("req-clean-01")
    raw = json.dumps(
        {
            "base_body": {"family": "rectangular_prism", "dimensions_mm": {"length": 50, "width": 40, "thickness": 10}},
        }
    )
    proposal = decode_and_bind_proposal(raw, req, TEST_SOURCE_ID)
    assert proposal.plan_payload["part"] == {
        "part_id": "part.main",
        "design_intent": "parametric model proposal",
        "scope": "single_part",
    }

    parsed = feature_plan_from_dict(dict(proposal.plan_payload))
    validated = validate_feature_plan(parsed)
    lowered = lower_validated_feature_plan_to_payload(validated)
    assert lowered["metadata"]["label"] == "canonical feature plan: parametric model proposal"
    assert "cube" not in lowered["metadata"]["label"]


def test_independent_mappings_between_calls() -> None:
    req1 = _make_prompt_request("req-call-1")
    req2 = _make_prompt_request("req-call-2")
    raw = json.dumps({"base_body": {"family": "cylinder", "dimensions_mm": {"radius": 10, "height": 20}}})

    proposal1 = decode_and_bind_proposal(raw, req1, TEST_SOURCE_ID)
    proposal2 = decode_and_bind_proposal(raw, req2, TEST_SOURCE_ID)

    assert proposal1.plan_payload["request_id"] == "req-call-1"
    assert proposal2.plan_payload["request_id"] == "req-call-2"
    assert proposal1.plan_payload is not proposal2.plan_payload
