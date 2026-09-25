"""Bounded, provider-only recovery before canonical plan validation.

This module never changes the canonical schema or CAD execution rules. It only
removes provider-owned envelope metadata, normalizes the documented dimensions
alias, and supplies small illustrative dimensions for an underspecified base
primitive. Feature geometry must be complete in the provider proposal so
explicit user dimensions can never be replaced silently.
"""

from __future__ import annotations

import copy
from typing import Any

from geometry.plan_models import DefaultApplied

_BASE_DEFAULTS: dict[str, dict[str, float]] = {
    "rectangular_prism": {"length": 50.0, "width": 40.0, "thickness": 10.0},
    "cylinder": {"radius": 20.0, "height": 20.0},
    "sphere": {"radius": 20.0},
    "spur_gear": {"tooth_count": 20.0, "module": 2.0, "face_width": 10.0},
}
_PROVIDER_ENVELOPE_FIELDS: frozenset[str] = frozenset({"plan_version", "request_id", "units", "part", "design_intent"})


def _put_default(
    dimensions: dict[str, Any],
    name: str,
    value: float,
    path: str,
    defaults: list[DefaultApplied],
) -> None:
    aliases = _dimension_aliases(name)
    if any(alias in dimensions for alias in aliases):
        return
    dimensions[name] = int(value) if name == "tooth_count" else value
    defaults.append(DefaultApplied(path=f"{path}.{name}", value=dimensions[name], reason="inferred_default"))


def _dimension_aliases(name: str) -> tuple[str, ...]:
    aliases: tuple[str, ...] = (name, f"{name}_mm")
    if name == "thickness":
        aliases += ("height", "height_mm")
    if name == "height":
        aliases += ("length", "length_mm")
    if name == "radius":
        aliases += ("diameter", "diameter_mm")
    return aliases


def recover_provider_payload(
    raw_payload: dict[str, Any], *, allow_base_defaults: bool
) -> tuple[dict[str, Any], tuple[DefaultApplied, ...], bool]:
    """Return a bounded best-effort proposal and records for deterministic defaults.

    Explicit non-mm units and ambiguous dimension containers are never guessed.
    Unsupported families and malformed values remain for canonical rejection.
    """
    provider_units = raw_payload.get("units")
    if provider_units is not None and provider_units != "mm":
        raise ValueError("Provider declared unsupported units")

    candidate = copy.deepcopy(
        {key: value for key, value in raw_payload.items() if key not in _PROVIDER_ENVELOPE_FIELDS}
    )
    body = candidate.get("base_body")
    if isinstance(body, dict) and "dimensions" in body:
        if "dimensions_mm" in body:
            raise ValueError("Provider supplied ambiguous base dimensions")
        body = dict(body)
        body["dimensions_mm"] = body.pop("dimensions")
        candidate["base_body"] = body

    features = candidate.get("features")
    if isinstance(features, list):
        rewritten: list[Any] = []
        for feature in features:
            if isinstance(feature, dict) and "dimensions" in feature:
                if "dimensions_mm" in feature:
                    raise ValueError("Provider supplied ambiguous feature dimensions")
                feature = dict(feature)
                feature["dimensions_mm"] = feature.pop("dimensions")
            rewritten.append(feature)
        candidate["features"] = rewritten

    normalized = candidate != raw_payload

    defaults: list[DefaultApplied] = []
    base = candidate.get("base_body")
    if isinstance(base, dict) and isinstance(base.get("family"), str):
        family_defaults = _BASE_DEFAULTS.get(base["family"])
        dimensions = base.get("dimensions_mm")
        if family_defaults is not None and (dimensions is None or isinstance(dimensions, dict)):
            if dimensions:
                if any(not any(alias in dimensions for alias in _dimension_aliases(name)) for name in family_defaults):
                    raise ValueError("Provider supplied incomplete base dimensions")
            else:
                if not allow_base_defaults:
                    raise ValueError("Measured prompt requires provider-supplied base dimensions")
                dimensions = {}
                base["dimensions_mm"] = dimensions
                for name, value in family_defaults.items():
                    _put_default(dimensions, name, value, "base_body.dimensions_mm", defaults)

    return candidate, tuple(defaults), normalized or bool(defaults)
