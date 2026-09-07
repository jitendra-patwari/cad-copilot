"""Opt-in live integration test for Gemini plan proposal adapter.

This test is strictly opt-in and will not run during normal test runs or CI.
It requires explicit environment guards:
  - CAD_COPILOT_RUN_LIVE_AI="1"
  - GEMINI_API_KEY=<valid_api_key>
"""

from __future__ import annotations

import os

import pytest

from application.models import PromptGenerationRequest
from geometry.plan_lowering import lower_validated_feature_plan_to_payload
from geometry.plan_models import RectangularBaseBody
from geometry.plan_parser import feature_plan_from_dict
from geometry.plan_validation import validate_feature_plan
from plan_providers.gemini import DEFAULT_GEMINI_MODEL, GeminiPlanResolver


@pytest.mark.filterwarnings(
    r"ignore:'_UnionGenericAlias' is deprecated and slated for removal in Python 3\.17:DeprecationWarning:google\.genai\.types"
)
@pytest.mark.live_ai
def test_live_gemini_proposal_resolution(
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Live opt-in verification of GeminiPlanResolver with user session key."""
    if os.environ.get("CAD_COPILOT_RUN_LIVE_AI") != "1":
        pytest.skip("Skipping live AI test: CAD_COPILOT_RUN_LIVE_AI is not set to '1'")

    if not os.environ.get("GEMINI_API_KEY", "").strip():
        pytest.skip("Skipping live AI test: GEMINI_API_KEY environment variable is not set")

    # Do not retain the secret in a named test local that pytest could display.
    resolver = GeminiPlanResolver(api_key=os.environ["GEMINI_API_KEY"])
    request = PromptGenerationRequest(
        contract_version="1.0",
        request_id="req-live-gemini-01",
        kind="prompt_to_cad",
        unit="mm",
        prompt="Create a 50x40x10 mm rectangular block",
    )

    caplog.set_level("WARNING")
    capsys.readouterr()

    has_stdout = False
    has_stderr = False
    has_logs = False
    proposal = None

    try:
        proposal = resolver(request)
    finally:
        captured = capsys.readouterr()
        has_stdout = bool(captured.out)
        has_stderr = bool(captured.err)
        has_logs = bool(caplog.records)
        del captured
        caplog.clear()

    # Sanitized boolean assertions to prevent leaking captured content on failure
    assert not has_stdout, "Live Gemini resolution unexpectedly produced stdout output"
    assert not has_stderr, "Live Gemini resolution unexpectedly produced stderr output"
    assert not has_logs, "Live Gemini resolution unexpectedly produced logging records"
    assert proposal is not None, "Live Gemini resolution unexpectedly produced None"

    assert proposal.provenance == "ai_proposal"
    assert proposal.source_id == DEFAULT_GEMINI_MODEL
    assert proposal.plan_payload["request_id"] == "req-live-gemini-01"
    assert proposal.plan_payload["units"] == "mm"

    # Canonical pipeline verification and exact dimension checks
    plan = feature_plan_from_dict(dict(proposal.plan_payload))
    validated_plan = validate_feature_plan(plan)
    assert isinstance(validated_plan.base_body, RectangularBaseBody)
    assert validated_plan.base_body.family == "rectangular_prism"
    assert validated_plan.base_body.length_mm == 50.0
    assert validated_plan.base_body.width_mm == 40.0
    assert validated_plan.base_body.thickness_mm == 10.0

    lowered = lower_validated_feature_plan_to_payload(validated_plan)
    assert "patches" in lowered
    assert len(lowered["patches"]) >= 1
