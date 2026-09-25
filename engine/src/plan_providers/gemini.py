"""Gemini plan proposal adapter implementing the PromptResolver boundary.

Invariants:
    - Text-Only: transmits strictly request.prompt and static repository instructions.
    - Zero Environment/CAD Inspection: no paths, secrets, geometry, or environment sent.
    - Bounded Retry: one SDK transient retry and one invalid-proposal regeneration at most.
    - Lazy SDK Loading: google-genai is imported only in the default production factory.
    - SDK-Independent Testing: injected fake clients run completely free of Google SDK imports.
    - Bounded Timeout: timeout enforced between 1,000 ms and 120,000 ms.
    - Sanitized Errors: translates the complete client/SDK lifecycle to fixed ProposalErrors,
      preventing raw provider exceptions, credentials, endpoints, or payloads from leaking.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeAlias

from application.models import PlanProposal, PromptGenerationRequest
from plan_providers.gemini_schema import load_gemini_response_schema
from plan_providers.proposal import (
    ProposalError,
    decode_and_bind_proposal,
)

DEFAULT_GEMINI_MODEL: str = "gemini-3.5-flash-lite"
DEFAULT_TIMEOUT_MS: int = 60_000
MIN_TIMEOUT_MS: int = 1_000
MAX_TIMEOUT_MS: int = 120_000
MAX_OUTPUT_TOKENS: int = 16_384
MAX_PROPOSAL_ATTEMPTS: int = 2

_MODEL_ID_PATTERN: re.Pattern[str] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_PROVIDER_STATUSES: frozenset[str] = frozenset(
    {
        "DEADLINE_EXCEEDED",
        "INTERNAL",
        "INVALID_ARGUMENT",
        "NOT_FOUND",
        "PERMISSION_DENIED",
        "RESOURCE_EXHAUSTED",
        "UNAUTHENTICATED",
        "UNAVAILABLE",
    }
)
_SAFE_PROVIDER_HTTP_CODES: frozenset[int] = frozenset({400, 401, 403, 404, 408, 429, 500, 502, 503, 504})

SYSTEM_INSTRUCTION: str = (
    "You are an expert mechanical CAD design assistant. Your task is to interpret "
    "natural language modeling prompts and generate a valid 3D CAD feature plan proposal "
    "matching the requested JSON schema.\n\n"
    "Guidelines:\n"
    "1. All linear dimensions are in millimeters (mm), including when the user omits a unit. "
    "All angular dimensions are in degrees.\n"
    "2. Every plan proposal must include a base_body. Supported base body families are: "
    "rectangular_prism, cylinder, sphere, spur_gear, and revolved_shaft.\n"
    "3. Supported feature families are: circular_through_hole, rectangular_through_cutout, "
    "slot_through_cutout, rectangular_extruded_pad, revolved_profile, profile_cutout, "
    "and swept_protrusion.\n"
    "4. For multi-body composition, declare primitive bodies in primitive_bodies and "
    "boolean operations in boolean_operations.\n"
    "5. Do NOT output envelope fields such as plan_version, request_id, or units in the proposal.\n"
    "6. Use canonical metric keys such as length_mm, width_mm, thickness_mm, height_mm, "
    "radius_mm, diameter_mm, module_mm, face_width_mm, bore_diameter_mm, depth_mm, and distance_mm.\n"
    "7. Preserve every explicit numeric value from the user exactly in the corresponding geometry. "
    "Never replace an explicit dimension with an inferred or illustrative value.\n"
    "8. Match requested feature quantities exactly. Singular wording such as 'a hole' or 'one "
    "cutout' means exactly one feature; never duplicate or omit a requested feature.\n"
    "9. For an underspecified request, choose modest positive illustrative dimensions for "
    "supported geometry and include those dimensions explicitly. Do not invent a feature the user "
    "did not request, and do not pretend an assumed value was supplied by the user.\n"
    "10. Complete every feature for its family. Circular holes require diameter_mm; rectangular "
    "cutouts require width_mm and height_mm; slots require length_mm and width_mm; rectangular pads "
    "require width_mm, height_mm, and distance_mm.\n"
    "11. Include only fields that describe the selected family. Do not attach profile, revolve, "
    "path, or cross_sections to a circular hole, rectangular cutout, slot, or rectangular pad.\n"
    "12. Examples: '50 x 40 x 10 mm rectangular block' means one rectangular_prism with length_mm "
    "50, width_mm 40, and thickness_mm 10. '120 x 80 x 15 mm plate with a 25 mm diameter circular "
    "through-hole in the center' means one rectangular_prism and exactly one "
    "circular_through_hole with diameter_mm 25. '100 x 100 x 20 mm block with a centered 60 x 40 "
    "mm rectangular through-cutout' means exactly one rectangular_through_cutout with height_mm 60 "
    "and width_mm 40. For '150 x 100 x 12 mm base with a centered 70 x 50 mm pad 10 mm high "
    "and a 16 mm through-hole', emit one rectangular_extruded_pad with width_mm 70, height_mm 50, "
    "and distance_mm 10, followed by one circular_through_hole with diameter_mm 16. "
    "When a through-hole overlaps a top pad, place the hole after the pad and target the bottom "
    "-Z face so the through-all cut crosses both pad and base. A +Z sketch on body.main starts at "
    "the original base top and leaves the pad uncut.\n"
    "13. Apply features in the requested order. Unless the user explicitly describes separate "
    "primitive bodies, every feature targets body.main; never target another feature ID.\n"
    "14. Every feature must include target.face.resolved_face. Use +Z for top, -Z for bottom, "
    "-Y for front, +Y for back, -X for left, and +X for right. Keep each named face with its "
    "requested feature; for example, a top hole and front cutout require +Z and -Y respectively. "
    "For a feature without a named face, choose a supported face explicitly.\n"
    "15. Return ONLY a single JSON object matching the feature plan proposal schema."
)

REGENERATION_INSTRUCTION: str = (
    "\n\nThe previous response was not a complete schema-valid CAD proposal. Regenerate the proposal "
    "from the original user prompt. Preserve every explicit dimension and feature count, include "
    "all family-required geometry and each requested target face, omit family-incompatible fields, "
    "and return only one JSON object. "
    "For a rectangular pad, provide both footprint dimensions as width_mm and height_mm and its "
    "extrusion height as distance_mm."
)

GeminiClientFactory: TypeAlias = Callable[..., Any]


@dataclass(frozen=True)
class GeminiRequestConfig:
    """SDK-independent stateless interaction configuration."""

    system_instruction: str
    response_format: dict[str, Any]
    generation_config: dict[str, Any]
    store: bool
    background: bool
    timeout_seconds: float


def _build_request_config(timeout_ms: int) -> GeminiRequestConfig:
    return GeminiRequestConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        response_format={
            "type": "text",
            "mime_type": "application/json",
            "schema": load_gemini_response_schema(),
        },
        generation_config={"max_output_tokens": MAX_OUTPUT_TOKENS},
        store=False,
        background=False,
        timeout_seconds=timeout_ms / 1_000,
    )


def _sanitized_provider_failure_message(exc: Exception) -> str:
    """Return a fixed diagnostic category without retaining provider details."""
    try:
        raw_status = getattr(exc, "status", None)
    except Exception:
        raw_status = None
    if isinstance(raw_status, str):
        status = raw_status.strip().upper()
        if status in _SAFE_PROVIDER_STATUSES:
            return f"Provider request failed ({status})."

    for attribute in ("code", "status_code"):
        try:
            raw_code = getattr(exc, attribute, None)
        except Exception:
            raw_code = None
        if type(raw_code) is int and raw_code in _SAFE_PROVIDER_HTTP_CODES:
            return f"Provider request failed (HTTP {raw_code})."

    return "Provider request failed."


def _validate_api_key(api_key: Any) -> str:
    """Validate that API key is a non-empty string without surrounding whitespace."""
    if not isinstance(api_key, str) or not api_key.strip():
        raise ProposalError("configuration_invalid", "Gemini API key must be a non-empty string.")
    if api_key != api_key.strip():
        raise ProposalError(
            "configuration_invalid",
            "Gemini API key must not contain leading or trailing whitespace.",
        )
    return api_key


def _validate_model_id(model_id: Any) -> str:
    """Validate that model ID is a safe identifier without paths, colons, or whitespace."""
    if not isinstance(model_id, str):
        raise ProposalError("configuration_invalid", "Model identifier must be a string.")
    if model_id != model_id.strip():
        raise ProposalError(
            "configuration_invalid",
            "Model identifier must not contain leading or trailing whitespace.",
        )
    if "/" in model_id or "\\" in model_id:
        raise ProposalError("configuration_invalid", "Model identifier must not contain path separators.")
    if ":" in model_id or ";" in model_id or "@" in model_id or "=" in model_id:
        raise ProposalError("configuration_invalid", "Model identifier contains invalid characters.")
    if not _MODEL_ID_PATTERN.fullmatch(model_id):
        raise ProposalError("configuration_invalid", "Model identifier is invalid.")
    return model_id


def _validate_timeout_ms(timeout_ms: Any) -> int:
    """Validate that timeout is an integer in milliseconds within bounded limits."""
    if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool):
        raise ProposalError("configuration_invalid", "Timeout must be an integer in milliseconds.")
    if not (MIN_TIMEOUT_MS <= timeout_ms <= MAX_TIMEOUT_MS):
        raise ProposalError(
            "configuration_invalid",
            f"Timeout must be between {MIN_TIMEOUT_MS} and {MAX_TIMEOUT_MS} milliseconds.",
        )
    return timeout_ms


def _default_client_factory(
    *,
    api_key: str,
    timeout_ms: int,
) -> Any:
    """Load google-genai lazily and construct a stateless Interactions client."""
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        raise ProposalError("sdk_unavailable", "The 'google-genai' package is not installed.") from None

    http_options = types.HttpOptions(
        api_version="v1",
        timeout=timeout_ms,
        retry_options=types.HttpRetryOptions(attempts=1),
    )
    return genai.Client(api_key=api_key, http_options=http_options)


class GeminiPlanResolver:
    """Resolves natural language CAD prompts to feature plan proposals using Gemini.

    Conforms to the PromptResolver protocol expected by GenerationService.
    Does not read environment variables; authentication is strictly constructor-injected.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model_id: str = DEFAULT_GEMINI_MODEL,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
        client_factory: GeminiClientFactory | None = None,
    ) -> None:
        self._api_key: str = _validate_api_key(api_key)
        self._model_id: str = _validate_model_id(model_id)
        self._timeout_ms: int = _validate_timeout_ms(timeout_ms)
        self._client_factory: GeminiClientFactory | None = client_factory

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def timeout_ms(self) -> int:
        return self._timeout_ms

    def __repr__(self) -> str:
        return f"GeminiPlanResolver(model_id={self._model_id!r}, timeout_ms={self._timeout_ms})"

    def __call__(self, request: PromptGenerationRequest) -> PlanProposal:
        """Generate a feature plan proposal for the given request prompt.

        Performs one stateless context-managed interaction with at most one SDK retry
        for transient failures and one bounded regeneration for an invalid proposal.
        Every response is independently validated before the local request envelope is bound.
        """
        if not isinstance(request, PromptGenerationRequest):
            raise ProposalError("configuration_invalid", "Request must be a PromptGenerationRequest.")

        try:
            config = _build_request_config(self._timeout_ms)
            if self._client_factory is None:
                client = _default_client_factory(
                    api_key=self._api_key,
                    timeout_ms=self._timeout_ms,
                )
            else:
                client = self._client_factory(
                    api_key=self._api_key,
                    timeout_ms=self._timeout_ms,
                )

            with client:
                for attempt in range(MAX_PROPOSAL_ATTEMPTS):
                    system_instruction = config.system_instruction
                    if attempt > 0:
                        system_instruction += REGENERATION_INSTRUCTION
                    response = client.interactions.create(
                        model=self._model_id,
                        input=request.prompt,
                        system_instruction=system_instruction,
                        response_format=config.response_format,
                        generation_config=config.generation_config,
                        store=config.store,
                        background=config.background,
                        timeout=config.timeout_seconds,
                    )
                    raw_text = getattr(response, "output_text", None)

                    if not isinstance(raw_text, str) or not raw_text.strip():
                        raise ProposalError("response_empty", "Provider returned an empty response.")

                    try:
                        return decode_and_bind_proposal(
                            response_text=raw_text,
                            request=request,
                            model_id=self._model_id,
                        )
                    except ProposalError as exc:
                        if exc.code != "response_invalid" or attempt == MAX_PROPOSAL_ATTEMPTS - 1:
                            raise

        except ProposalError:
            raise
        except Exception as exc:
            raise ProposalError("provider_request_failed", _sanitized_provider_failure_message(exc)) from None

        raise ProposalError("response_invalid", "Provider response did not form a valid proposal.")
