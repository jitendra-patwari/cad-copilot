"""Strict JSON decoding, proposal-schema validation, and envelope binding for AI plan providers."""

from __future__ import annotations

import copy
import functools
import importlib.resources
import json
import math
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from application.models import PlanProposal, PromptGenerationRequest
from geometry.plan_models import CANONICAL_PLAN_VERSION

SCHEMA_RESOURCE_NAME: str = "feature-plan-proposal-v1.schema.json"
MAX_PROVIDER_RESPONSE_CHARS: int = 1_000_000

_ENVELOPE_FIELDS: tuple[str, ...] = (
    "plan_version",
    "request_id",
    "units",
    "part",
    "design_intent",
)


class ProposalError(Exception):
    """Base exception for provider proposal decoding, validation, or binding failures."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Parse object pairs into a dictionary, rejecting duplicate keys."""
    res: dict[str, Any] = {}
    for key, value in pairs:
        if key in res:
            raise ValueError(f"Duplicate object key: {key}")
        res[key] = value
    return res


def _reject_constant(val: str) -> Any:
    """Reject non-finite JSON constants such as NaN, Infinity, or -Infinity."""
    raise ValueError(f"JSON non-finite constant '{val}' is not allowed")


def _assert_all_numbers_finite(data: Any) -> None:
    """Recursively assert that all floating point numbers are finite."""
    if isinstance(data, float):
        if not math.isfinite(data):
            raise ValueError("Non-finite float value detected")
    elif isinstance(data, dict):
        for key, val in data.items():
            _assert_all_numbers_finite(key)
            _assert_all_numbers_finite(val)
    elif isinstance(data, list):
        for item in data:
            _assert_all_numbers_finite(item)


@functools.cache
def _load_cached_proposal_schema() -> dict[str, Any]:
    """Load and cache canonical FeaturePlanProposal Draft 2020-12 schema object."""
    try:
        schema_path = importlib.resources.files("plan_providers").joinpath("schemas", SCHEMA_RESOURCE_NAME)
        schema_bytes = schema_path.read_bytes()
    except Exception:
        raise ProposalError(
            "configuration_invalid",
            f"Failed to load proposal schema resource '{SCHEMA_RESOURCE_NAME}'",
        ) from None

    try:
        schema_obj = json.loads(schema_bytes.decode("utf-8"))
    except Exception:
        raise ProposalError(
            "configuration_invalid",
            f"Proposal schema resource '{SCHEMA_RESOURCE_NAME}' is not valid JSON",
        ) from None

    if not isinstance(schema_obj, dict):
        raise ProposalError(
            "configuration_invalid",
            f"Proposal schema resource '{SCHEMA_RESOURCE_NAME}' root must be a JSON object",
        )

    return schema_obj


def load_proposal_schema() -> dict[str, Any]:
    """Load the proposal-body Draft 2020-12 schema from package data.

    Returns an isolated deep copy of the cached schema to prevent mutation.
    """
    return copy.deepcopy(_load_cached_proposal_schema())


@functools.cache
def get_proposal_validator() -> Draft202012Validator:
    """Return a cached Draft 2020-12 validator instance for the proposal-body schema."""
    schema = _load_cached_proposal_schema()
    return Draft202012Validator(schema)


def decode_and_bind_proposal(
    response_text: str,
    request: PromptGenerationRequest,
    model_id: str,
) -> PlanProposal:
    """Strictly decode untrusted model text, validate against proposal schema, and bind request envelope.

    Args:
        response_text: Raw string returned by the provider.
        request: Validated PromptGenerationRequest providing request_id and unit.
        model_id: Validated model identifier providing source_id.

    Returns:
        PlanProposal with provenance="ai_proposal", source_id=model_id, and bound envelope.

    Raises:
        ProposalError: If the response is empty, oversized, malformed, schema-invalid,
                       or contains envelope fields.
    """
    if not isinstance(response_text, str) or not response_text.strip():
        raise ProposalError("response_empty", "Provider returned an empty or whitespace-only response")

    if len(response_text) > MAX_PROVIDER_RESPONSE_CHARS:
        raise ProposalError(
            "response_invalid",
            f"Provider response length {len(response_text)} exceeds maximum allowed limit of {MAX_PROVIDER_RESPONSE_CHARS}",
        )

    try:
        raw_payload: Any = json.loads(
            response_text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
        _assert_all_numbers_finite(raw_payload)
    except Exception:
        raise ProposalError("response_invalid", "Provider response is not valid JSON") from None

    if not isinstance(raw_payload, dict):
        raise ProposalError("response_invalid", "Proposal payload root must be a JSON object")

    validator = get_proposal_validator()
    try:
        validator.validate(raw_payload)
    except ValidationError:
        raise ProposalError("response_invalid", "Proposal payload failed schema validation") from None

    for env_field in _ENVELOPE_FIELDS:
        if env_field in raw_payload:
            raise ProposalError(
                "response_invalid",
                f"Provider output must not include envelope field '{env_field}'",
            )

    payload = copy.deepcopy(raw_payload)
    payload["plan_version"] = CANONICAL_PLAN_VERSION
    payload["request_id"] = request.request_id
    payload["units"] = request.unit
    payload["part"] = {
        "part_id": "part.main",
        "design_intent": "parametric model proposal",
        "scope": "single_part",
    }

    return PlanProposal(
        plan_payload=payload,
        provenance="ai_proposal",
        source_id=model_id,
        warnings=(),
    )


__all__ = [
    "MAX_PROVIDER_RESPONSE_CHARS",
    "ProposalError",
    "decode_and_bind_proposal",
    "get_proposal_validator",
    "load_proposal_schema",
]
