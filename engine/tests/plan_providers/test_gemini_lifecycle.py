"""Unit and integration tests for Gemini plan adapter client lifecycle, error sanitization, and pipeline integration."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, cast
from unittest.mock import patch

import pytest

from application.models import PromptGenerationRequest
from application.service import GenerationService
from geometry.plan_lowering import lower_validated_feature_plan_to_payload
from geometry.plan_models import FeaturePlan
from geometry.plan_parser import feature_plan_from_dict
from geometry.plan_validation import validate_feature_plan
from plan_providers.gemini import (
    DEFAULT_TIMEOUT_MS,
    REGENERATION_INSTRUCTION,
    GeminiPlanResolver,
)
from plan_providers.proposal import ProposalError


def _make_prompt_request(
    request_id: str = "req-test-lifecycle-01",
    prompt: str = "create a box 50x40x10",
    metadata: Mapping[str, str] | None = None,
) -> PromptGenerationRequest:
    return PromptGenerationRequest(
        contract_version="1.0",
        request_id=request_id,
        kind="prompt_to_cad",
        prompt=prompt,
        unit="mm",
        metadata=metadata,
    )


def _valid_block_proposal_json() -> str:
    return json.dumps(
        {
            "base_body": {
                "family": "rectangular_prism",
                "dimensions_mm": {"length": 50.0, "width": 40.0, "thickness": 10.0},
            }
        }
    )


class FakeInteractionResponse:
    def __init__(self, text: str | None) -> None:
        self.output_text = text


class FakeInteractions:
    def __init__(
        self,
        response_text: str | list[str | None] | None = None,
        exc: Exception | None = None,
    ) -> None:
        self.response_texts = response_text if isinstance(response_text, list) else [response_text]
        self.exc = exc
        self.call_count = 0
        self.last_model: str | None = None
        self.last_contents: Any = None
        self.last_config: Any = None
        self.system_instructions: list[str] = []

    def create(self, *, model: str, input: Any, **kwargs: Any) -> FakeInteractionResponse:
        self.call_count += 1
        self.last_model = model
        self.last_contents = input
        self.last_config = kwargs
        self.system_instructions.append(kwargs["system_instruction"])
        if self.exc is not None:
            raise self.exc
        response_index = min(self.call_count - 1, len(self.response_texts) - 1)
        return FakeInteractionResponse(self.response_texts[response_index])


class FakeClient:
    def __init__(
        self,
        *,
        api_key: str = "test-api-key",
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        response_text: str | list[str | None] | None = None,
        exc: Exception | None = None,
        raise_on_enter: Exception | None = None,
        raise_on_exit: Exception | None = None,
    ) -> None:
        self.api_key = api_key
        self.timeout_ms = timeout_ms
        self.interactions = FakeInteractions(response_text=response_text, exc=exc)
        self.entered = False
        self.closed = False
        self.raise_on_enter = raise_on_enter
        self.raise_on_exit = raise_on_exit

    def __enter__(self) -> FakeClient:
        if self.raise_on_enter:
            raise self.raise_on_enter
        self.entered = True
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.closed = True
        if self.raise_on_exit:
            raise self.raise_on_exit


# ---------------------------------------------------------------------------
# SDK Availability
# ---------------------------------------------------------------------------


def test_missing_google_genai_package_raises_sdk_unavailable() -> None:
    resolver = GeminiPlanResolver(api_key="valid-key")
    with patch.dict("sys.modules", {"google": None, "google.genai": None}):
        with pytest.raises(ProposalError) as exc_info:
            resolver(_make_prompt_request())
        assert exc_info.value.code == "sdk_unavailable"
        assert "google-genai" in str(exc_info.value)
        assert exc_info.value.__cause__ is None


# ---------------------------------------------------------------------------
# Lifecycle Exception Sanitization
# ---------------------------------------------------------------------------


def test_factory_exception_sanitized_to_provider_request_failed() -> None:
    def failing_factory(*, api_key: str, timeout_ms: int) -> Any:
        raise RuntimeError("Internal low-level connection breakdown")

    resolver = GeminiPlanResolver(api_key="valid-key", client_factory=failing_factory)
    with pytest.raises(ProposalError) as exc_info:
        resolver(_make_prompt_request())
    assert exc_info.value.code == "provider_request_failed"
    assert "Provider request failed." in str(exc_info.value)
    assert "Internal low-level connection breakdown" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None


def test_client_enter_exception_sanitized() -> None:
    def factory(*, api_key: str, timeout_ms: int) -> FakeClient:
        return FakeClient(raise_on_enter=RuntimeError("gRPC handshake failed"))

    resolver = GeminiPlanResolver(api_key="valid-key", client_factory=factory)
    with pytest.raises(ProposalError) as exc_info:
        resolver(_make_prompt_request())
    assert exc_info.value.code == "provider_request_failed"
    assert "Provider request failed." in str(exc_info.value)
    assert "gRPC handshake failed" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None


def test_client_exit_exception_sanitized() -> None:
    def factory(*, api_key: str, timeout_ms: int) -> FakeClient:
        return FakeClient(
            response_text=_valid_block_proposal_json(),
            raise_on_exit=RuntimeError("Transport close error"),
        )

    resolver = GeminiPlanResolver(api_key="valid-key", client_factory=factory)
    with pytest.raises(ProposalError) as exc_info:
        resolver(_make_prompt_request())
    assert exc_info.value.code == "provider_request_failed"
    assert "Provider request failed." in str(exc_info.value)
    assert "Transport close error" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None


def test_response_text_property_exception_sanitized() -> None:
    class ExplodingResponse:
        @property
        def output_text(self) -> str:
            raise RuntimeError("Response stream deserialization crash")

    class ExplodingClient(FakeClient):
        def __init__(self) -> None:
            super().__init__()

            class ExplodingInteractions:
                def create(self, **kwargs: Any) -> Any:
                    return ExplodingResponse()

            self.interactions = ExplodingInteractions()  # type: ignore[assignment]

    resolver = GeminiPlanResolver(
        api_key="valid-key",
        client_factory=lambda *, api_key, timeout_ms: ExplodingClient(),
    )
    with pytest.raises(ProposalError) as exc_info:
        resolver(_make_prompt_request())
    assert exc_info.value.code == "provider_request_failed"
    assert "Provider request failed." in str(exc_info.value)
    assert "Response stream deserialization crash" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None


# ---------------------------------------------------------------------------
# Context Manager Cleanup Guarantees
# ---------------------------------------------------------------------------


def test_client_context_exits_on_success() -> None:
    created_client: FakeClient | None = None

    def factory(*, api_key: str, timeout_ms: int) -> FakeClient:
        nonlocal created_client
        created_client = FakeClient(
            api_key=api_key,
            timeout_ms=timeout_ms,
            response_text=_valid_block_proposal_json(),
        )
        return created_client

    resolver = GeminiPlanResolver(api_key="key", client_factory=factory)
    resolver(_make_prompt_request())

    assert created_client is not None
    assert created_client.entered is True
    assert created_client.closed is True


def test_client_context_exits_on_sdk_exception() -> None:
    created_client: FakeClient | None = None

    def factory(*, api_key: str, timeout_ms: int) -> FakeClient:
        nonlocal created_client
        created_client = FakeClient(
            api_key=api_key,
            timeout_ms=timeout_ms,
            exc=RuntimeError("Transient 503 Service Unavailable"),
        )
        return created_client

    resolver = GeminiPlanResolver(api_key="key", client_factory=factory)
    with pytest.raises(ProposalError) as exc_info:
        resolver(_make_prompt_request())

    assert exc_info.value.code == "provider_request_failed"
    assert created_client is not None
    assert created_client.entered is True
    assert created_client.closed is True


def test_client_context_exits_on_empty_response() -> None:
    created_client: FakeClient | None = None

    def factory(*, api_key: str, timeout_ms: int) -> FakeClient:
        nonlocal created_client
        created_client = FakeClient(
            api_key=api_key,
            timeout_ms=timeout_ms,
            response_text="   \n\t",
        )
        return created_client

    resolver = GeminiPlanResolver(api_key="key", client_factory=factory)
    with pytest.raises(ProposalError) as exc_info:
        resolver(_make_prompt_request())

    assert exc_info.value.code == "response_empty"
    assert created_client is not None
    assert created_client.entered is True
    assert created_client.closed is True


def test_client_context_exits_on_invalid_json() -> None:
    created_client: FakeClient | None = None

    def factory(*, api_key: str, timeout_ms: int) -> FakeClient:
        nonlocal created_client
        created_client = FakeClient(
            api_key=api_key,
            timeout_ms=timeout_ms,
            response_text="not a valid json object",
        )
        return created_client

    resolver = GeminiPlanResolver(api_key="key", client_factory=factory)
    with pytest.raises(ProposalError) as exc_info:
        resolver(_make_prompt_request())

    assert exc_info.value.code == "response_invalid"
    assert created_client is not None
    assert created_client.entered is True
    assert created_client.closed is True


def test_client_context_exits_on_oversized_response() -> None:
    created_client: FakeClient | None = None

    def factory(*, api_key: str, timeout_ms: int) -> FakeClient:
        nonlocal created_client
        created_client = FakeClient(
            api_key=api_key,
            timeout_ms=timeout_ms,
            response_text="x" * (1_000_000 + 1),
        )
        return created_client

    resolver = GeminiPlanResolver(api_key="key", client_factory=factory)
    with pytest.raises(ProposalError) as exc_info:
        resolver(_make_prompt_request())

    assert exc_info.value.code == "response_invalid"
    assert created_client is not None
    assert created_client.entered is True
    assert created_client.closed is True


def test_client_context_exits_on_schema_rejection() -> None:
    created_client: FakeClient | None = None

    def factory(*, api_key: str, timeout_ms: int) -> FakeClient:
        nonlocal created_client
        created_client = FakeClient(
            api_key=api_key,
            timeout_ms=timeout_ms,
            response_text=json.dumps({"unknown_field": "bad"}),
        )
        return created_client

    resolver = GeminiPlanResolver(api_key="key", client_factory=factory)
    with pytest.raises(ProposalError) as exc_info:
        resolver(_make_prompt_request())

    assert exc_info.value.code == "response_invalid"
    assert created_client is not None
    assert created_client.entered is True
    assert created_client.closed is True


def test_invalid_proposal_is_regenerated_once_and_preserves_explicit_geometry() -> None:
    created_client: FakeClient | None = None
    invalid = json.dumps(
        {
            "base_body": {
                "family": "rectangular_prism",
                "dimensions_mm": {"length_mm": 120, "width_mm": 80, "thickness_mm": 15},
            },
            "features": [
                {"family": "circular_through_hole"},
                {"family": "circular_through_hole", "profile": {}},
            ],
        }
    )
    valid = json.dumps(
        {
            "base_body": {
                "family": "rectangular_prism",
                "dimensions_mm": {"length_mm": 120, "width_mm": 80, "thickness_mm": 15},
            },
            "features": [
                {
                    "family": "circular_through_hole",
                    "dimensions_mm": {"diameter_mm": 25},
                }
            ],
        }
    )

    def factory(*, api_key: str, timeout_ms: int) -> FakeClient:
        nonlocal created_client
        created_client = FakeClient(
            api_key=api_key,
            timeout_ms=timeout_ms,
            response_text=[invalid, valid],
        )
        return created_client

    resolver = GeminiPlanResolver(api_key="key", client_factory=factory)
    proposal = resolver(
        _make_prompt_request(
            prompt=(
                "Create a rectangular mounting plate 120 mm long, 80 mm wide, and 15 mm thick, "
                "with a 25 mm diameter circular through-hole in the center."
            )
        )
    )

    assert created_client is not None
    assert created_client.interactions.call_count == 2
    assert created_client.interactions.system_instructions[1].endswith(REGENERATION_INSTRUCTION)
    features = proposal.plan_payload["features"]
    assert isinstance(features, list)
    typed_features = cast(list[dict[str, Any]], features)
    assert len(typed_features) == 1
    assert typed_features[0]["dimensions_mm"] == {"diameter_mm": 25}
    assert not proposal.applied_defaults


def test_invalid_proposal_regeneration_stops_after_two_attempts() -> None:
    created_client: FakeClient | None = None
    invalid = json.dumps(
        {
            "base_body": {
                "family": "rectangular_prism",
                "dimensions_mm": {"length": 120, "width": 80, "thickness": 15},
            },
            "features": [{"family": "circular_through_hole"}],
        }
    )

    def factory(*, api_key: str, timeout_ms: int) -> FakeClient:
        nonlocal created_client
        created_client = FakeClient(
            api_key=api_key,
            timeout_ms=timeout_ms,
            response_text=invalid,
        )
        return created_client

    resolver = GeminiPlanResolver(api_key="key", client_factory=factory)
    with pytest.raises(ProposalError) as exc_info:
        resolver(_make_prompt_request())

    assert exc_info.value.code == "response_invalid"
    assert created_client is not None
    assert created_client.interactions.call_count == 2


def test_incomplete_pad_proposal_is_regenerated_with_all_explicit_dimensions() -> None:
    created_client: FakeClient | None = None
    invalid = json.dumps(
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
                {"family": "circular_through_hole", "dimensions_mm": {"diameter_mm": 16}},
            ],
        }
    )
    valid = json.dumps(
        {
            "base_body": {
                "family": "rectangular_prism",
                "dimensions_mm": {"length_mm": 150, "width_mm": 100, "thickness_mm": 12},
            },
            "features": [
                {
                    "family": "rectangular_extruded_pad",
                    "dimensions_mm": {"width_mm": 70, "height_mm": 50, "distance_mm": 10},
                },
                {
                    "family": "circular_through_hole",
                    "dimensions_mm": {"diameter_mm": 16},
                    "target": {"face": {"resolved_face": "-Z"}},
                },
            ],
        }
    )

    def factory(*, api_key: str, timeout_ms: int) -> FakeClient:
        nonlocal created_client
        created_client = FakeClient(
            api_key=api_key,
            timeout_ms=timeout_ms,
            response_text=[invalid, valid],
        )
        return created_client

    resolver = GeminiPlanResolver(api_key="key", client_factory=factory)
    proposal = resolver(
        _make_prompt_request(
            prompt=(
                "Design a rectangular base 150 mm long, 100 mm wide, and 12 mm thick with a centered "
                "rectangular pad 70 mm long, 50 mm wide, and 10 mm high, and a 16 mm circular through-hole."
            )
        )
    )

    assert created_client is not None
    assert created_client.interactions.call_count == 2
    features = cast(list[dict[str, Any]], proposal.plan_payload["features"])
    assert features[0]["dimensions_mm"] == {"width_mm": 70, "height_mm": 50, "distance_mm": 10}
    assert features[1]["dimensions_mm"] == {"diameter_mm": 16}


def test_pad_top_hole_is_regenerated_from_bottom_after_pad() -> None:
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
    invalid = json.dumps(payload)
    payload["features"][1]["target"]["face"]["resolved_face"] = "-Z"
    valid = json.dumps(payload)
    created_client: FakeClient | None = None

    def factory(*, api_key: str, timeout_ms: int) -> FakeClient:
        nonlocal created_client
        created_client = FakeClient(api_key=api_key, timeout_ms=timeout_ms, response_text=[invalid, valid])
        return created_client

    prompt = (
        "Design a rectangular base 150 mm long, 100 mm wide, and 12 mm thick with a centered "
        "rectangular pad 70 mm long, 50 mm wide, and 10 mm high, and a 16 mm circular through-hole."
    )
    proposal = GeminiPlanResolver(api_key="key", client_factory=factory)(_make_prompt_request(prompt=prompt))
    assert created_client is not None
    assert created_client.interactions.call_count == 2
    features = cast(list[dict[str, Any]], proposal.plan_payload["features"])
    assert [feature["target"]["face"]["resolved_face"] for feature in features] == ["+Z", "-Z"]


def test_named_front_face_mismatch_is_regenerated_once() -> None:
    prompt = (
        "Create a rectangular block 100 mm long, 80 mm wide, and 40 mm thick with a 20 mm circular "
        "through-hole on the top face and a 30 mm wide by 15 mm high rectangular through-cutout on the front face."
    )
    payload = {
        "base_body": {
            "family": "rectangular_prism",
            "dimensions_mm": {"length_mm": 100, "width_mm": 80, "thickness_mm": 40},
        },
        "features": [
            {"family": "circular_through_hole", "dimensions_mm": {"diameter_mm": 20}},
            {"family": "rectangular_through_cutout", "dimensions_mm": {"width_mm": 30, "height_mm": 15}},
        ],
    }
    invalid = json.dumps(payload)
    payload["features"][0]["target"] = {"face": {"resolved_face": "+Z"}}
    payload["features"][1]["target"] = {"face": {"resolved_face": "-Y"}}
    valid = json.dumps(payload)
    created_client: FakeClient | None = None

    def factory(*, api_key: str, timeout_ms: int) -> FakeClient:
        nonlocal created_client
        created_client = FakeClient(api_key=api_key, timeout_ms=timeout_ms, response_text=[invalid, valid])
        return created_client

    proposal = GeminiPlanResolver(api_key="key", client_factory=factory)(_make_prompt_request(prompt=prompt))
    assert created_client is not None
    assert created_client.interactions.call_count == 2
    features = cast(list[dict[str, Any]], proposal.plan_payload["features"])
    assert [feature["target"]["face"]["resolved_face"] for feature in features] == ["+Z", "-Y"]


# ---------------------------------------------------------------------------
# Pipeline Integration
# ---------------------------------------------------------------------------


def test_pipeline_integration_with_canonical_parser_and_validator() -> None:
    def factory(*, api_key: str, timeout_ms: int) -> FakeClient:
        return FakeClient(
            api_key=api_key,
            timeout_ms=timeout_ms,
            response_text=_valid_block_proposal_json(),
        )

    resolver = GeminiPlanResolver(api_key="test-key", client_factory=factory)
    req = _make_prompt_request("req-canon-01")
    proposal = resolver(req)

    parsed = feature_plan_from_dict(dict(proposal.plan_payload))
    assert isinstance(parsed, FeaturePlan)
    assert parsed.request_id == "req-canon-01"
    assert parsed.units == "mm"

    validated = validate_feature_plan(parsed)
    assert validated.base_body.family == "rectangular_prism"

    lowered = lower_validated_feature_plan_to_payload(validated)
    assert "patches" in lowered
    assert len(lowered["patches"]) >= 1


# ---------------------------------------------------------------------------
# GenerationService Integration
# ---------------------------------------------------------------------------


def test_generation_service_maps_resolver_failure_to_prompt_interpretation_failed(tmp_path: Any) -> None:
    def failing_factory(*, api_key: str, timeout_ms: int) -> FakeClient:
        return FakeClient(
            api_key=api_key,
            timeout_ms=timeout_ms,
            exc=RuntimeError("Google Cloud unreachable"),
        )

    resolver = GeminiPlanResolver(api_key="test-key", client_factory=failing_factory)
    service = GenerationService(prompt_resolver=resolver)

    req = _make_prompt_request("req-service-fail-01")
    result = service.generate(req, output_root=tmp_path)

    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "PROMPT_INTERPRETATION_FAILED"
    assert result["request_id"] == "req-service-fail-01"


def test_generation_service_maps_invalid_geometry_proposal_to_cad_plan_rejected(tmp_path: Any) -> None:
    zero_radius_proposal = json.dumps(
        {
            "base_body": {
                "family": "cylinder",
                "dimensions_mm": {"radius": 0.0, "height": 20.0},
            }
        }
    )

    def factory(*, api_key: str, timeout_ms: int) -> FakeClient:
        return FakeClient(
            api_key=api_key,
            timeout_ms=timeout_ms,
            response_text=zero_radius_proposal,
        )

    resolver = GeminiPlanResolver(api_key="test-key", client_factory=factory)
    service = GenerationService(prompt_resolver=resolver)

    req = _make_prompt_request("req-geom-fail-01")
    result = service.generate(req, output_root=tmp_path)

    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "CAD_PLAN_REJECTED"
    assert result["request_id"] == "req-geom-fail-01"
