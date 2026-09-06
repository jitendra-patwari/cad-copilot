"""Deterministic offline example plan catalog loader.

Provides catalog lookup, packaged JSON resource loading, and request-bound
PlanProposal resolution without AI, API key, or network dependencies.
"""

from __future__ import annotations

import importlib.resources
import json
from types import MappingProxyType
from typing import Any

from application.models import ExampleGenerationRequest, PlanProposal

_CATALOG_RESOURCES: MappingProxyType[str, str] = MappingProxyType(
    {
        "spur_gear": "spur_gear_example_plan.json",
    }
)


class ExampleCatalogError(Exception):
    """Base exception for all example catalog loading and resolution failures."""


def available_example_ids() -> tuple[str, ...]:
    """Return the stable ordered tuple of available deterministic example identifiers."""
    return tuple(_CATALOG_RESOURCES.keys())


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


def resolve_example_plan(request: ExampleGenerationRequest) -> PlanProposal:
    """Resolve a schema-approved example request into an untrusted PlanProposal.

    Args:
        request: Validated ExampleGenerationRequest containing example_id and request_id.

    Returns:
        PlanProposal with fresh request_id, provenance="example_plan", and safe source_id.

    Raises:
        ExampleCatalogError: If the example_id is unknown or the packaged resource is unreadable.
    """
    resource_name = _CATALOG_RESOURCES.get(request.example_id)
    if resource_name is None:
        raise ExampleCatalogError("Requested example is not in the deterministic catalog")

    try:
        resource_path = importlib.resources.files("example_catalog").joinpath("resources", resource_name)
        resource_bytes = resource_path.read_bytes()
    except Exception as exc:
        raise ExampleCatalogError("Failed to read example plan catalog resource") from exc

    try:
        text = resource_bytes.decode("utf-8")
    except Exception as exc:
        raise ExampleCatalogError("Catalog resource contains invalid UTF-8") from exc

    try:
        raw_payload: Any = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except Exception as exc:
        raise ExampleCatalogError("Catalog resource is not valid JSON") from exc

    if not isinstance(raw_payload, dict):
        raise ExampleCatalogError("Catalog resource root must be a JSON object")

    payload: dict[str, Any] = raw_payload
    payload["request_id"] = request.request_id

    return PlanProposal(
        plan_payload=payload,
        provenance="example_plan",
        source_id=request.example_id,
        warnings=(),
    )
