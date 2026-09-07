"""Unit and integration tests for Gemini plan adapter client lifecycle, error sanitization, and pipeline integration."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
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
    def __init__(self, response_text: str | None = None, exc: Exception | None = None) -> None:
        self.response_text = response_text
        self.exc = exc
        self.call_count = 0
        self.last_model: str | None = None
        self.last_contents: Any = None
        self.last_config: Any = None

    def create(self, *, model: str, input: Any, **kwargs: Any) -> FakeInteractionResponse:
        self.call_count += 1
        self.last_model = model
        self.last_contents = input
        self.last_config = kwargs
        if self.exc is not None:
            raise self.exc
        return FakeInteractionResponse(self.response_text)


class FakeClient:
    def __init__(
        self,
        *,
        api_key: str = "test-api-key",
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        response_text: str | None = None,
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
