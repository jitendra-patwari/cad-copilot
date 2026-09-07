"""Tests for request-aware provider composition and output-root preflight."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from application.models import ExampleGenerationRequest, PromptGenerationRequest
from ipc.composition import run_generation


def test_composition_missing_output_root_returns_failed_output_path_not_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proves missing CAD_OUTPUT_ROOT immediately returns failed/OUTPUT_PATH_NOT_ALLOWED."""
    monkeypatch.delenv("CAD_OUTPUT_ROOT", raising=False)
    req = ExampleGenerationRequest(
        contract_version="1.0",
        request_id="req-out-01",
        kind="example_plan",
        unit="mm",
        example_id="spur_gear",
    )

    res = run_generation(req)
    assert res["status"] == "failed"
    assert res["request_id"] == "req-out-01"
    assert res["errors"][0]["code"] == "OUTPUT_PATH_NOT_ALLOWED"


def test_composition_whitespace_output_root_returns_failed_output_path_not_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proves whitespace-only CAD_OUTPUT_ROOT immediately returns failed/OUTPUT_PATH_NOT_ALLOWED."""
    monkeypatch.setenv("CAD_OUTPUT_ROOT", "   \t\n  ")
    req = ExampleGenerationRequest(
        contract_version="1.0",
        request_id="req-out-02",
        kind="example_plan",
        unit="mm",
        example_id="spur_gear",
    )

    res = run_generation(req)
    assert res["status"] == "failed"
    assert res["request_id"] == "req-out-02"
    assert res["errors"][0]["code"] == "OUTPUT_PATH_NOT_ALLOWED"


def test_composition_example_route_does_not_inspect_gemini_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Proves example_plan route never inspects GEMINI_API_KEY or CAD_LLM_MODEL."""
    monkeypatch.setenv("CAD_OUTPUT_ROOT", str(tmp_path))
    # Trap plan_providers so any attempted import raises ModuleNotFoundError
    monkeypatch.setitem(sys.modules, "plan_providers", None)

    accessed_keys: list[str] = []
    original_getitem = os.environ.__class__.__getitem__
    original_get = os.environ.__class__.get

    def tracked_getitem(self: Any, key: str) -> str:
        accessed_keys.append(key)
        return original_getitem(self, key)

    def tracked_get(self: Any, key: str, default: Any = None) -> Any:
        accessed_keys.append(key)
        return original_get(self, key, default)

    monkeypatch.setattr(os.environ.__class__, "__getitem__", tracked_getitem)
    monkeypatch.setattr(os.environ.__class__, "get", tracked_get)

    req = ExampleGenerationRequest(
        contract_version="1.0",
        request_id="req-ex-01",
        kind="example_plan",
        unit="mm",
        example_id="unsupported_example_id",
    )
    res = run_generation(req)

    # Example route maps unsupported example ID through existing service behavior
    assert res["status"] == "rejected"
    assert res["request_id"] == "req-ex-01"
    assert res["errors"][0]["code"] == "UNSUPPORTED_REQUEST"

    # Verify Gemini environment variables were not accessed
    assert "GEMINI_API_KEY" not in accessed_keys
    assert "CAD_LLM_MODEL" not in accessed_keys


def test_composition_example_route_nonexistent_output_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Proves example route rejects nonexistent CAD_OUTPUT_ROOT before catalog resolution."""
    nonexistent_dir = tmp_path / "does_not_exist"
    monkeypatch.setenv("CAD_OUTPUT_ROOT", str(nonexistent_dir))

    req = ExampleGenerationRequest(
        contract_version="1.0",
        request_id="req-ex-02",
        kind="example_plan",
        unit="mm",
        example_id="spur_gear",
    )
    res = run_generation(req)

    assert res["status"] == "failed"
    assert res["request_id"] == "req-ex-02"
    assert res["errors"][0]["code"] == "OUTPUT_PATH_NOT_ALLOWED"


def test_composition_prompt_route_missing_key_preserves_preflight_precedence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Proves missing GEMINI_API_KEY preserves Phase A output-root preflight before missing-resolver result."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    req = PromptGenerationRequest(
        contract_version="1.0",
        request_id="req-pr-01",
        kind="prompt_to_cad",
        unit="mm",
        prompt="A simple bracket",
    )

    # 1. Nonexistent output root: Phase A preflight fails first
    nonexistent = tmp_path / "nonexistent"
    monkeypatch.setenv("CAD_OUTPUT_ROOT", str(nonexistent))
    res1 = run_generation(req)
    assert res1["status"] == "failed"
    assert res1["errors"][0]["code"] == "OUTPUT_PATH_NOT_ALLOWED"

    # 2. Existing output root: Phase B missing prompt resolver fails
    monkeypatch.setenv("CAD_OUTPUT_ROOT", str(tmp_path))
    res2 = run_generation(req)
    assert res2["status"] == "failed"
    assert res2["errors"][0]["code"] == "PROMPT_INTERPRETATION_FAILED"


def test_composition_prompt_route_whitespace_key_preserves_preflight_precedence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Proves whitespace-only GEMINI_API_KEY preserves Phase A output-root preflight precedence."""
    monkeypatch.setenv("GEMINI_API_KEY", "   \t  ")
    req = PromptGenerationRequest(
        contract_version="1.0",
        request_id="req-pr-02",
        kind="prompt_to_cad",
        unit="mm",
        prompt="A simple bracket",
    )

    # 1. Nonexistent output root
    nonexistent = tmp_path / "nonexistent"
    monkeypatch.setenv("CAD_OUTPUT_ROOT", str(nonexistent))
    res1 = run_generation(req)
    assert res1["status"] == "failed"
    assert res1["errors"][0]["code"] == "OUTPUT_PATH_NOT_ALLOWED"

    # 2. Existing output root
    monkeypatch.setenv("CAD_OUTPUT_ROOT", str(tmp_path))
    res2 = run_generation(req)
    assert res2["status"] == "failed"
    assert res2["errors"][0]["code"] == "PROMPT_INTERPRETATION_FAILED"


def test_composition_prompt_route_default_model_selected_when_absent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Proves default model gemini-3.5-flash-lite is selected when CAD_LLM_MODEL is absent."""
    monkeypatch.setenv("CAD_OUTPUT_ROOT", str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", "fake-test-key-1234")
    monkeypatch.delenv("CAD_LLM_MODEL", raising=False)

    req = PromptGenerationRequest(
        contract_version="1.0",
        request_id="req-pr-03",
        kind="prompt_to_cad",
        unit="mm",
        prompt="A cylindrical pin",
    )

    captured_model: str | None = None

    from plan_providers.gemini import GeminiPlanResolver
    from plan_providers.proposal import ProposalError

    original_init = GeminiPlanResolver.__init__

    def mock_init(self: Any, *args: Any, **kwargs: Any) -> None:
        nonlocal captured_model
        captured_model = kwargs.get("model_id")
        original_init(self, *args, **kwargs)

    with (
        patch.object(GeminiPlanResolver, "__init__", mock_init),
        patch.object(
            GeminiPlanResolver,
            "__call__",
            side_effect=ProposalError("provider_failed", "Controlled test provider failure"),
        ),
    ):
        res = run_generation(req)
        assert captured_model == "gemini-3.5-flash-lite"
        assert res["status"] == "failed"
        assert res["errors"][0]["code"] == "PROMPT_INTERPRETATION_FAILED"


def test_composition_prompt_route_custom_model_passed_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Proves valid custom CAD_LLM_MODEL is passed to GeminiPlanResolver unchanged."""
    monkeypatch.setenv("CAD_OUTPUT_ROOT", str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", "fake-test-key-1234")
    monkeypatch.setenv("CAD_LLM_MODEL", "gemini-1.5-pro-002")

    req = PromptGenerationRequest(
        contract_version="1.0",
        request_id="req-pr-04",
        kind="prompt_to_cad",
        unit="mm",
        prompt="A cylindrical pin",
    )

    captured_model: str | None = None

    from plan_providers.gemini import GeminiPlanResolver
    from plan_providers.proposal import ProposalError

    original_init = GeminiPlanResolver.__init__

    def mock_init(self: Any, *args: Any, **kwargs: Any) -> None:
        nonlocal captured_model
        captured_model = kwargs.get("model_id")
        original_init(self, *args, **kwargs)

    with (
        patch.object(GeminiPlanResolver, "__init__", mock_init),
        patch.object(
            GeminiPlanResolver,
            "__call__",
            side_effect=ProposalError("provider_failed", "Controlled test provider failure"),
        ),
    ):
        res = run_generation(req)
        assert captured_model == "gemini-1.5-pro-002"
        assert res["status"] == "failed"
        assert res["errors"][0]["code"] == "PROMPT_INTERPRETATION_FAILED"


@pytest.mark.parametrize(
    "invalid_model",
    [
        "   ",
        "model/with/slashes",
        "model:with:colons",
        "model with spaces",
        "model@bad",
    ],
)
def test_composition_prompt_route_invalid_model_fails_safely_preserving_precedence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    invalid_model: str,
) -> None:
    """Proves invalid CAD_LLM_MODEL fails safely and preserves output-root preflight precedence."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake-test-key-1234")
    monkeypatch.setenv("CAD_LLM_MODEL", invalid_model)

    req = PromptGenerationRequest(
        contract_version="1.0",
        request_id="req-pr-05",
        kind="prompt_to_cad",
        unit="mm",
        prompt="A mounting plate",
    )

    # 1. Nonexistent output root: Phase A preflight fails first
    nonexistent = tmp_path / "nonexistent"
    monkeypatch.setenv("CAD_OUTPUT_ROOT", str(nonexistent))
    res1 = run_generation(req)
    assert res1["status"] == "failed"
    assert res1["errors"][0]["code"] == "OUTPUT_PATH_NOT_ALLOWED"

    # 2. Existing output root: Phase B missing prompt resolver fails
    monkeypatch.setenv("CAD_OUTPUT_ROOT", str(tmp_path))
    res2 = run_generation(req)
    assert res2["status"] == "failed"
    assert res2["errors"][0]["code"] == "PROMPT_INTERPRETATION_FAILED"


def test_composition_prompt_route_missing_sdk_returns_prompt_interpretation_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Proves missing google-genai SDK maps to PROMPT_INTERPRETATION_FAILED."""
    monkeypatch.setenv("CAD_OUTPUT_ROOT", str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", "fake-test-key-1234")

    req = PromptGenerationRequest(
        contract_version="1.0",
        request_id="req-sdk-01",
        kind="prompt_to_cad",
        unit="mm",
        prompt="A cylindrical pin",
    )

    from plan_providers.proposal import ProposalError

    with patch(
        "plan_providers.gemini._default_client_factory",
        side_effect=ProposalError("sdk_unavailable", "The 'google-genai' package is not installed."),
    ):
        res = run_generation(req)

    assert res["status"] == "failed"
    assert res["request_id"] == "req-sdk-01"
    assert res["errors"][0]["code"] == "PROMPT_INTERPRETATION_FAILED"
