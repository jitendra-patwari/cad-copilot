"""Unit tests for Gemini plan adapter configuration, validation, and request contracts."""

from __future__ import annotations

import json
import types
from collections.abc import Mapping
from typing import Any
from unittest.mock import patch

import pytest

from application.models import PromptGenerationRequest
from plan_providers.gemini import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_TIMEOUT_MS,
    MAX_OUTPUT_TOKENS,
    MAX_TIMEOUT_MS,
    MIN_TIMEOUT_MS,
    SYSTEM_INSTRUCTION,
    GeminiPlanResolver,
    _default_client_factory,
)
from plan_providers.gemini_schema import load_gemini_response_schema
from plan_providers.proposal import ProposalError


class FakeInteractionResponse:
    def __init__(self, text: str | None) -> None:
        self.output_text = text


class FakeInteractions:
    def __init__(self, response_text: str | None = None, exc: Exception | None = None) -> None:
        self.response_text = response_text
        self.exc = exc
        self.call_count = 0
        self.last_model: str | None = None
        self.last_input: Any = None
        self.last_system_instruction: Any = None
        self.last_response_format: Any = None
        self.last_generation_config: Any = None
        self.last_store: Any = None
        self.last_background: Any = None
        self.last_timeout: Any = None

    def create(
        self,
        *,
        model: str,
        input: Any,
        system_instruction: Any,
        response_format: Any,
        generation_config: Any,
        store: bool,
        background: bool,
        timeout: float,
    ) -> FakeInteractionResponse:
        self.call_count += 1
        self.last_model = model
        self.last_input = input
        self.last_system_instruction = system_instruction
        self.last_response_format = response_format
        self.last_generation_config = generation_config
        self.last_store = store
        self.last_background = background
        self.last_timeout = timeout
        if self.exc is not None:
            raise self.exc
        return FakeInteractionResponse(self.response_text)


class FakeClient:
    def __init__(
        self,
        *,
        api_key: str,
        timeout_ms: int,
        response_text: str | None = None,
        exc: Exception | None = None,
    ) -> None:
        self.api_key = api_key
        self.timeout_ms = timeout_ms
        self.interactions = FakeInteractions(response_text=response_text, exc=exc)
        self.entered = False
        self.closed = False

    def __enter__(self) -> FakeClient:
        self.entered = True
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.closed = True


def _make_prompt_request(
    request_id: str = "req-test-config-01",
    prompt: str = "create a box",
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


# ---------------------------------------------------------------------------
# API Key Validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("invalid_key", ["", "   ", "\t\n", None, 123, False])
def test_constructor_rejects_invalid_api_key(invalid_key: Any) -> None:
    with pytest.raises(ProposalError) as exc_info:
        GeminiPlanResolver(api_key=invalid_key)
    assert exc_info.value.code == "configuration_invalid"


@pytest.mark.parametrize(
    "whitespace_key",
    [" key", "key ", "  key  ", "\tkey", "key\n"],
)
def test_constructor_rejects_surrounding_whitespace_in_api_key(whitespace_key: str) -> None:
    with pytest.raises(ProposalError) as exc_info:
        GeminiPlanResolver(api_key=whitespace_key)
    assert exc_info.value.code == "configuration_invalid"
    assert "whitespace" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Model Identifier Validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "invalid_model",
    [
        "",
        "   ",
        "gemini/2.5-flash",
        "/gemini-2.5-flash",
        "models/gemini-2.5-flash",
        "gemini\\flash",
        "gemini:latest",
        "gemini@flash",
        "gemini=2.5",
        "gemini 2.5",
        "a" * 129,
        123,
        None,
    ],
)
def test_constructor_rejects_invalid_model_id(invalid_model: Any) -> None:
    with pytest.raises(ProposalError) as exc_info:
        GeminiPlanResolver(api_key="valid-key", model_id=invalid_model)
    assert exc_info.value.code == "configuration_invalid"


# ---------------------------------------------------------------------------
# Timeout Bounds Validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "invalid_timeout",
    [0, -1, -500, MIN_TIMEOUT_MS - 1, MAX_TIMEOUT_MS + 1, 300_000, "60000", True, False, 3.14],
)
def test_constructor_rejects_invalid_timeout(invalid_timeout: Any) -> None:
    with pytest.raises(ProposalError) as exc_info:
        GeminiPlanResolver(api_key="valid-key", timeout_ms=invalid_timeout)
    assert exc_info.value.code == "configuration_invalid"


@pytest.mark.parametrize("valid_timeout", [1_000, 30_000, 60_000, 120_000])
def test_constructor_accepts_valid_bounded_timeout(valid_timeout: int) -> None:
    resolver = GeminiPlanResolver(api_key="valid-key", timeout_ms=valid_timeout)
    assert resolver.timeout_ms == valid_timeout


# ---------------------------------------------------------------------------
# Valid Configuration & Representation Masking
# ---------------------------------------------------------------------------


def test_constructor_valid_defaults() -> None:
    assert DEFAULT_GEMINI_MODEL == "gemini-3.5-flash-lite"
    resolver = GeminiPlanResolver(api_key="valid-test-key")
    assert resolver.model_id == "gemini-3.5-flash-lite"
    assert resolver.model_id == DEFAULT_GEMINI_MODEL
    assert resolver.timeout_ms == DEFAULT_TIMEOUT_MS


def test_system_instruction_preserves_explicit_dimensions_and_feature_counts() -> None:
    assert "Preserve every explicit numeric value" in SYSTEM_INSTRUCTION
    assert "Match requested feature quantities exactly" in SYSTEM_INSTRUCTION
    assert "exactly one circular_through_hole with diameter_mm 25" in SYSTEM_INSTRUCTION
    assert "Do not attach profile, revolve, path, or cross_sections" in SYSTEM_INSTRUCTION
    assert "width_mm 70, height_mm 50" in SYSTEM_INSTRUCTION
    assert "distance_mm 10" in SYSTEM_INSTRUCTION
    assert "every feature targets body.main" in SYSTEM_INSTRUCTION


def test_constructor_valid_custom_model_and_timeout() -> None:
    resolver = GeminiPlanResolver(
        api_key="valid-test-key",
        model_id="gemini-2.5-pro",
        timeout_ms=30_000,
    )
    assert resolver.model_id == "gemini-2.5-pro"
    assert resolver.timeout_ms == 30_000


def test_repr_hides_api_key() -> None:
    secret_key = "AIzaSySecretApiKey12345"
    resolver = GeminiPlanResolver(api_key=secret_key, model_id="gemini-3.5-flash-lite")
    repr_str = repr(resolver)
    assert secret_key not in repr_str
    assert "gemini-3.5-flash-lite" in repr_str
    assert "GeminiPlanResolver" in repr_str


# ---------------------------------------------------------------------------
# Request Construction & Payload Invariants (SDK Isolation)
# ---------------------------------------------------------------------------


def test_request_payload_invariants_without_sdk_import() -> None:
    created_client: FakeClient | None = None

    def client_factory(*, api_key: str, timeout_ms: int) -> FakeClient:
        nonlocal created_client
        created_client = FakeClient(
            api_key=api_key,
            timeout_ms=timeout_ms,
            response_text=_valid_block_proposal_json(),
        )
        return created_client

    resolver = GeminiPlanResolver(
        api_key="secret-api-key-xyz",
        model_id="gemini-3.5-flash-lite",
        client_factory=client_factory,
    )

    metadata = {"session": "sess-99", "secret_env": "top_secret_value"}
    req = _make_prompt_request(
        request_id="req-privacy-check-01",
        prompt="Create a cylinder radius 10 height 30",
        metadata=metadata,
    )

    proposal = resolver(req)

    assert created_client is not None
    assert created_client.api_key == "secret-api-key-xyz"
    assert created_client.timeout_ms == DEFAULT_TIMEOUT_MS

    interaction = created_client.interactions
    assert interaction.call_count == 1
    assert interaction.last_model == "gemini-3.5-flash-lite"
    assert interaction.last_input == "Create a cylinder radius 10 height 30"
    assert interaction.last_system_instruction == SYSTEM_INSTRUCTION
    assert interaction.last_response_format == {
        "type": "text",
        "mime_type": "application/json",
        "schema": load_gemini_response_schema(),
    }
    assert interaction.last_generation_config == {"max_output_tokens": MAX_OUTPUT_TOKENS}
    assert interaction.last_store is False
    assert interaction.last_background is False
    assert interaction.last_timeout == DEFAULT_TIMEOUT_MS / 1_000

    base_body = proposal.plan_payload["base_body"]
    assert isinstance(base_body, dict)
    assert base_body["family"] == "rectangular_prism"
    assert proposal.plan_payload["request_id"] == "req-privacy-check-01"
    assert proposal.source_id == "gemini-3.5-flash-lite"
    assert proposal.provenance == "ai_proposal"


# ---------------------------------------------------------------------------
# Production Default Factory Verification (SDK Constructor Argument Inspection)
# ---------------------------------------------------------------------------


def test_production_default_factory_configures_bounded_retry_and_timeout() -> None:
    """Prove that the production client uses v1, bounded timeout, and one transient retry."""
    captured: dict[str, Any] = {}

    class FakeHttpRetryOptions:
        def __init__(self, *, attempts: int) -> None:
            captured["retry_attempts"] = attempts

    class FakeHttpOptions:
        def __init__(
            self,
            *,
            api_version: str,
            timeout: int,
            retry_options: Any,
        ) -> None:
            captured["api_version"] = api_version
            captured["timeout"] = timeout
            captured["retry_options"] = retry_options

    class FakeGenaiClient:
        def __init__(self, *, api_key: str, http_options: Any) -> None:
            captured["client_api_key"] = api_key
            captured["client_http_options"] = http_options

    fake_genai_mod = types.ModuleType("google.genai")
    fake_genai_mod.Client = FakeGenaiClient  # type: ignore[attr-defined]

    fake_types_mod = types.ModuleType("google.genai.types")
    fake_types_mod.HttpRetryOptions = FakeHttpRetryOptions  # type: ignore[attr-defined]
    fake_types_mod.HttpOptions = FakeHttpOptions  # type: ignore[attr-defined]

    fake_google_mod = types.ModuleType("google")
    fake_google_mod.genai = fake_genai_mod  # type: ignore[attr-defined]

    with patch.dict(
        "sys.modules",
        {
            "google": fake_google_mod,
            "google.genai": fake_genai_mod,
            "google.genai.types": fake_types_mod,
        },
    ):
        client = _default_client_factory(api_key="test-api-key", timeout_ms=DEFAULT_TIMEOUT_MS)

    assert isinstance(client, FakeGenaiClient)
    assert captured["client_api_key"] == "test-api-key"
    assert captured["api_version"] == "v1"
    assert captured["timeout"] == DEFAULT_TIMEOUT_MS
    assert captured["retry_attempts"] == 1
