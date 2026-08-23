"""Unit tests for gate policy modes and diagnostic generators."""

from __future__ import annotations

import pytest

from geometry.gate_policy import (
    current_gate_policy_mode,
    relaxed_gate_warning,
    shadow_gate_warning,
    should_retry_canonical_rejection,
)


def test_default_gate_policy_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CAD_COPILOT_GATE_MODE", raising=False)
    assert current_gate_policy_mode() == "capability_first"


def test_explicit_gate_policy_mode_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAD_COPILOT_GATE_MODE", "strict")
    assert current_gate_policy_mode("shadow") == "shadow"
    assert current_gate_policy_mode("capability_first") == "capability_first"


def test_gate_policy_mode_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAD_COPILOT_GATE_MODE", "strict")
    assert current_gate_policy_mode() == "strict"

    monkeypatch.setenv("CAD_COPILOT_GATE_MODE", "shadow")
    assert current_gate_policy_mode() == "shadow"


def test_invalid_gate_policy_mode_falls_back_to_capability_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAD_COPILOT_GATE_MODE", "unknown_mode_xyz")
    assert current_gate_policy_mode() == "capability_first"


def test_should_retry_canonical_rejection() -> None:
    assert should_retry_canonical_rejection(None) is True
    assert should_retry_canonical_rejection("syntax_error") is True
    assert should_retry_canonical_rejection("unsupported_scope") is True
    assert should_retry_canonical_rejection("safety_or_policy") is False
    assert should_retry_canonical_rejection("unsupported_units") is False


def test_relaxed_gate_warning_generation() -> None:
    warning = relaxed_gate_warning(
        gate_id="feature_plan.geometry_fit",
        original_code="HOLE_DOES_NOT_FIT",
        decision="warn_allow",
        mode="capability_first",
    )
    assert warning["code"] == "GATE_POLICY_RELAXED"
    assert "HOLE_DOES_NOT_FIT" in warning["message"]
    assert "capability_first" in warning["message"]
    assert "warn_allow" in warning["message"]


def test_shadow_gate_warning_generation() -> None:
    warning = shadow_gate_warning(
        gate_id="feature_plan.geometry_fit",
        original_code="CUTOUT_DOES_NOT_FIT",
        shadow_decision="reject",
        mode="shadow",
    )
    assert warning["code"] == "GATE_POLICY_SHADOW"
    assert "CUTOUT_DOES_NOT_FIT" in warning["message"]
    assert "shadow" in warning["message"]
    assert "reject" in warning["message"]
