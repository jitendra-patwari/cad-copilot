"""Bounded provider-text recovery, canonical proposal validation, and envelope binding."""

from __future__ import annotations

import copy
import functools
import importlib.resources
import json
import math
import re
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from application.models import PlanProposal, PromptGenerationRequest
from geometry.plan_models import CANONICAL_PLAN_VERSION, FeaturePlanValidationError
from geometry.plan_parser import feature_plan_from_dict
from geometry.validators.feature_profiles import _validate_pad_through_hole_interactions
from plan_providers.proposal_recovery import recover_provider_payload

SCHEMA_RESOURCE_NAME: str = "feature-plan-proposal-v1.schema.json"
MAX_PROVIDER_RESPONSE_CHARS: int = 1_000_000
_JSON_FENCE_PATTERN: re.Pattern[str] = re.compile(
    r"```[ \t]*json\b[ \t\r\n]*(.*?)[ \t\r\n]*```",
    flags=re.IGNORECASE | re.DOTALL,
)
_NAMED_FACE_PATTERN: re.Pattern[str] = re.compile(r"\b(top|bottom|front|back|left|right)\s+face\b", re.IGNORECASE)
_FEATURE_FACE_PATTERN: re.Pattern[str] = re.compile(
    r"\b(hole|cutout|slot|pad)\s+on\s+(?:the\s+)?(top|bottom|front|back|left|right)\s+face\b",
    re.IGNORECASE,
)
_NAMED_FACES: dict[str, str] = {
    "top": "+Z",
    "bottom": "-Z",
    "front": "-Y",
    "back": "+Y",
    "left": "-X",
    "right": "+X",
}


def _assert_named_faces_preserved(payload: dict[str, Any], prompt: str) -> None:
    """Reject an obvious face mismatch without inventing or moving feature geometry."""
    requested = [_NAMED_FACES[match.group(1).lower()] for match in _NAMED_FACE_PATTERN.finditer(prompt)]
    if not requested:
        return

    actual: list[str | None] = []
    for feature in payload.get("features", []):
        target = feature.get("target", {})
        face = target.get("face", {})
        value = face.get("resolved_face") if isinstance(face, dict) else None
        actual.append(_NAMED_FACES.get(value.lower(), value.upper()) if isinstance(value, str) else None)

    associations = list(_FEATURE_FACE_PATTERN.finditer(prompt))
    if len(associations) == len(requested):
        expected_by_index: dict[int, str] = {}
        features = payload.get("features", [])
        for association in associations:
            kind = association.group(1).lower()
            candidates = [index for index, feature in enumerate(features) if kind in feature.get("family", "")]
            if len(candidates) != 1 or candidates[0] in expected_by_index:
                break
            expected_by_index[candidates[0]] = _NAMED_FACES[association.group(2).lower()]
        else:
            if any(actual[index] != face for index, face in expected_by_index.items()):
                raise ProposalError("response_invalid", "Provider proposal did not preserve requested feature faces")
            return

    matches = requested == actual if len(requested) == len(actual) else set(requested).issubset(actual)
    if not matches:
        raise ProposalError("response_invalid", "Provider proposal did not preserve requested feature faces")


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


def _recover_single_json_value(response_text: str, decoder: json.JSONDecoder) -> Any:
    """Recover exactly one JSON object, ignoring only non-JSON surrounding text."""
    fenced_matches = list(_JSON_FENCE_PATTERN.finditer(response_text))
    if len(fenced_matches) > 1:
        raise ValueError("Ambiguous multiple JSON fences")
    if fenced_matches:
        match = fenced_matches[0]
        candidate = match.group(1)
        surrounding = response_text[: match.start()] + response_text[match.end() :]
        for index, char in enumerate(surrounding):
            if char != "{":
                continue
            try:
                decoder.raw_decode(surrounding, index)
            except json.JSONDecodeError:
                continue
            raise ValueError("Ambiguous JSON object outside fence")
    else:
        candidate = response_text

    recovered: Any | None = None
    cursor = 0
    while True:
        object_start = candidate.find("{", cursor)
        if object_start < 0:
            break
        try:
            value, end = decoder.raw_decode(candidate, object_start)
        except json.JSONDecodeError:
            cursor = object_start + 1
            continue
        if recovered is not None:
            raise ValueError("Ambiguous multiple JSON objects")
        recovered = value
        cursor = end

    if recovered is None:
        raise ValueError("No JSON object found")
    return recovered


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
    """Recover one untrusted model plan, validate it, and bind the trusted request envelope.

    Args:
        response_text: Raw string returned by the provider.
        request: Validated PromptGenerationRequest providing request_id and unit.
        model_id: Validated model identifier providing source_id.

    Returns:
        PlanProposal with provenance="ai_proposal", source_id=model_id, and bound envelope.

    Raises:
        ProposalError: If the response is empty, oversized, ambiguous, or cannot form
                       a schema-valid supported plan.
    """
    if not isinstance(response_text, str) or not response_text.strip():
        raise ProposalError("response_empty", "Provider returned an empty or whitespace-only response")

    if len(response_text) > MAX_PROVIDER_RESPONSE_CHARS:
        raise ProposalError(
            "response_invalid",
            f"Provider response length {len(response_text)} exceeds maximum allowed limit of {MAX_PROVIDER_RESPONSE_CHARS}",
        )

    decoder = json.JSONDecoder(object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_constant)
    recovered_text = False
    try:
        raw_payload: Any = decoder.decode(response_text)
    except json.JSONDecodeError:
        try:
            raw_payload = _recover_single_json_value(response_text, decoder)
            recovered_text = True
        except Exception:
            raise ProposalError("response_invalid", "Provider response is not valid JSON") from None
    except ValueError:
        raise ProposalError("response_invalid", "Provider response is not valid JSON") from None

    try:
        _assert_all_numbers_finite(raw_payload)
    except Exception:
        raise ProposalError("response_invalid", "Provider response is not valid JSON") from None

    if not isinstance(raw_payload, dict):
        raise ProposalError("response_invalid", "Proposal payload root must be a JSON object")

    try:
        normalized_payload, applied_defaults, normalized = recover_provider_payload(
            raw_payload,
            allow_base_defaults=not any(char.isdigit() for char in request.prompt),
        )
    except ValueError:
        raise ProposalError("response_invalid", "Proposal payload could not be normalized") from None

    validator = get_proposal_validator()
    try:
        validator.validate(normalized_payload)
    except ValidationError:
        raise ProposalError("response_invalid", "Proposal payload failed schema validation") from None

    _assert_named_faces_preserved(normalized_payload, request.prompt)

    payload = copy.deepcopy(normalized_payload)
    payload["plan_version"] = CANONICAL_PLAN_VERSION
    payload["request_id"] = request.request_id
    payload["units"] = request.unit
    payload["part"] = {
        "part_id": "part.main",
        "design_intent": "parametric model proposal",
        "scope": "single_part",
    }

    try:
        _validate_pad_through_hole_interactions(feature_plan_from_dict(payload).features)
    except FeaturePlanValidationError:
        raise ProposalError("response_invalid", "Proposal feature interactions are unsupported") from None

    warnings: list[dict[str, str]] = []
    if recovered_text or normalized:
        warnings.append({"code": "AI_PROPOSAL_NORMALIZED"})
    if applied_defaults:
        warnings.append({"code": "AI_DIMENSIONS_ASSUMED"})

    return PlanProposal(
        plan_payload=payload,
        provenance="ai_proposal",
        source_id=model_id,
        warnings=warnings,
        applied_defaults=applied_defaults,
    )


__all__ = [
    "MAX_PROVIDER_RESPONSE_CHARS",
    "ProposalError",
    "decode_and_bind_proposal",
    "get_proposal_validator",
    "load_proposal_schema",
]
