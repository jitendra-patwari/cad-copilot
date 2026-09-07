"""Safe provider failure categorization tests for the Gemini adapter."""

from __future__ import annotations

from typing import Any

import pytest

from application.models import PromptGenerationRequest
from plan_providers.gemini import GeminiPlanResolver
from plan_providers.proposal import ProposalError


class ProviderStatusError(RuntimeError):
    def __init__(self, *, status: Any = None, code: Any = None, status_code: Any = None) -> None:
        super().__init__("sensitive provider details must not escape")
        self.status = status
        self.code = code
        self.status_code = status_code


class FailingInteractions:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def create(self, **kwargs: Any) -> None:
        raise self._exc


class FailingClient:
    def __init__(self, exc: Exception) -> None:
        self.interactions = FailingInteractions(exc)

    def __enter__(self) -> FailingClient:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        return None


def _request() -> PromptGenerationRequest:
    return PromptGenerationRequest(
        contract_version="1.0",
        request_id="req-provider-failure-01",
        kind="prompt_to_cad",
        prompt="create a box 50x40x10",
        unit="mm",
    )


def _resolver(exc: Exception) -> GeminiPlanResolver:
    return GeminiPlanResolver(
        api_key="valid-key",
        client_factory=lambda *, api_key, timeout_ms: FailingClient(exc),
    )


@pytest.mark.parametrize(
    "status",
    [
        "DEADLINE_EXCEEDED",
        "INTERNAL",
        "INVALID_ARGUMENT",
        "NOT_FOUND",
        "PERMISSION_DENIED",
        "RESOURCE_EXHAUSTED",
        "UNAUTHENTICATED",
        "UNAVAILABLE",
    ],
)
def test_allowlisted_provider_status_is_preserved_safely(status: str) -> None:
    with pytest.raises(ProposalError) as exc_info:
        _resolver(ProviderStatusError(status=status))(_request())

    assert exc_info.value.code == "provider_request_failed"
    assert str(exc_info.value) == f"Provider request failed ({status})."
    assert "sensitive provider details" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None


def test_allowlisted_provider_http_code_is_preserved_when_status_is_absent() -> None:
    with pytest.raises(ProposalError) as exc_info:
        _resolver(ProviderStatusError(code=429))(_request())

    assert exc_info.value.code == "provider_request_failed"
    assert str(exc_info.value) == "Provider request failed (HTTP 429)."
    assert exc_info.value.__cause__ is None


def test_interactions_http_status_code_is_preserved_safely() -> None:
    with pytest.raises(ProposalError) as exc_info:
        _resolver(ProviderStatusError(status_code=400))(_request())

    assert exc_info.value.code == "provider_request_failed"
    assert str(exc_info.value) == "Provider request failed (HTTP 400)."
    assert exc_info.value.__cause__ is None


def test_untrusted_provider_status_and_code_are_not_exposed() -> None:
    with pytest.raises(ProposalError) as exc_info:
        _resolver(ProviderStatusError(status="api_key=secret-value", code=451))(_request())

    assert exc_info.value.code == "provider_request_failed"
    assert str(exc_info.value) == "Provider request failed."
    assert "secret-value" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None
