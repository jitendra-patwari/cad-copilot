"""Unit tests for proposal schema loading, strict JSON decoding, sealed schema validation, and envelope binding."""

from __future__ import annotations

import json
from typing import Any

import pytest

from application.models import PromptGenerationRequest
from geometry.plan_lowering import lower_validated_feature_plan_to_payload
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
    assert "failed schema validation" in str(exc_info.value)


def test_decode_rejects_missing_base_body_dimensions() -> None:
    req = _make_prompt_request()
    raw = json.dumps({"base_body": {"family": "rectangular_prism"}})
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


def test_decode_rejects_legacy_dimensions_alias() -> None:
    req = _make_prompt_request()
    raw = json.dumps(
        {
            "base_body": {
                "family": "cylinder",
                "dimensions": {"radius": 10, "height": 20},
            }
        }
    )
    with pytest.raises(ProposalError) as exc_info:
        decode_and_bind_proposal(raw, req, TEST_SOURCE_ID)
    assert exc_info.value.code == "response_invalid"


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


def test_decode_rejects_dimensionless_features() -> None:
    req = _make_prompt_request()
    raw = json.dumps(
        {
            "base_body": {"family": "cylinder", "dimensions_mm": {"radius": 10, "height": 20}},
            "features": [{"family": "circular_through_hole", "target": {"body_id": "body.main"}}],
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


def test_decode_rejects_provider_part_or_design_intent_injection() -> None:
    """Proves that provider output cannot inject part or design_intent to leak prompts."""
    req = _make_prompt_request("req-privacy-01")
    sentinel_prompt = "TOP_SECRET_PROMPT_SENTINEL_XYZ"

    raw_with_part = json.dumps(
        {
            "part": {"design_intent": sentinel_prompt},
            "base_body": {"family": "cylinder", "dimensions_mm": {"radius": 10, "height": 20}},
        }
    )
    with pytest.raises(ProposalError) as exc1:
        decode_and_bind_proposal(raw_with_part, req, TEST_SOURCE_ID)
    assert exc1.value.code == "response_invalid"

    raw_with_intent = json.dumps(
        {
            "design_intent": sentinel_prompt,
            "base_body": {"family": "cylinder", "dimensions_mm": {"radius": 10, "height": 20}},
        }
    )
    with pytest.raises(ProposalError) as exc2:
        decode_and_bind_proposal(raw_with_intent, req, TEST_SOURCE_ID)
    assert exc2.value.code == "response_invalid"


@pytest.mark.parametrize("envelope_key", ["plan_version", "request_id", "units", "part", "design_intent"])
def test_decode_rejects_provider_owned_envelope_fields(envelope_key: str) -> None:
    req = _make_prompt_request()
    raw_dict: dict[str, Any] = {
        "base_body": {
            "family": "rectangular_prism",
            "dimensions_mm": {"length": 25.0, "width": 25.0, "thickness": 25.0},
        },
        envelope_key: "injected_value",
    }
    raw = json.dumps(raw_dict)
    with pytest.raises(ProposalError) as exc_info:
        decode_and_bind_proposal(raw, req, TEST_SOURCE_ID)
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
