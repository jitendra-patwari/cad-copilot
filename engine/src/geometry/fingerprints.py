"""Pure deterministic cryptographic fingerprints and canonical JSON encoding.

This is a pure leaf domain module with zero external dependencies, zero COM bindings,
and zero filesystem I/O, conforming to NFR-1 (Zero I/O Purity) and NFR-4 (Strict Static Typing).
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any


def normalize_float_signed_zero(val: float) -> float:
    """Normalize IEEE-754 signed zero (-0.0) to canonical positive zero (0.0)."""
    if val == 0.0:
        return 0.0
    return val


def normalize_canonical_data(obj: Any) -> Any:
    """Recursively normalize a data structure for canonical JSON encoding.

    Invariants:
      - Normalizes IEEE-754 -0.0 floats to canonical 0.0.
      - Rejects non-finite floats (NaN, +inf, -inf) with ValueError.
      - Preserves booleans as booleans without coercing to int.
      - Preserves integers as integers without coercing to float.
      - Preserves strings and None.
      - Converts Mappings to dicts with string keys.
      - Converts Sequences (list, tuple) to lists preserving element order.
      - Rejects non-serializable objects with TypeError.
    """
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, int):
        return obj
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ValueError(f"Non-finite float value {obj} cannot be encoded in canonical JSON")
        return 0.0 if obj == 0.0 else obj
    if isinstance(obj, str):
        return obj
    if obj is None:
        return None
    if isinstance(obj, Mapping):
        return {str(k): normalize_canonical_data(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [normalize_canonical_data(item) for item in obj]
    raise TypeError(f"Object of type {type(obj).__name__} is not canonical JSON serializable")


def canonical_json_bytes(obj: Any) -> bytes:
    """Encode an object into deterministic canonical JSON UTF-8 bytes.

    Invariants:
      - UTF-8 encoding
      - Keys sorted lexicographically
      - Compact separators (',', ':') without whitespace
      - ensure_ascii=True
      - allow_nan=False
      - Recursive IEEE-754 -0.0 normalization to 0.0
      - List/tuple ordering preserved
      - Strict rejection of non-finite floats
    """
    normalized = normalize_canonical_data(obj)
    json_str = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return json_str.encode("utf-8")


def sha256_canonical_json(obj: Any) -> str:
    """Compute the lowercase SHA-256 hex digest of the canonical JSON bytes."""
    return hashlib.sha256(canonical_json_bytes(obj)).hexdigest()


def compute_prompt_fingerprint(prompt: str | None) -> str | None:
    """Compute exact lowercase SHA-256 hex digest for prompt, or None if prompt is absent."""
    if prompt is None:
        return None
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


__all__ = [
    "canonical_json_bytes",
    "compute_prompt_fingerprint",
    "normalize_canonical_data",
    "normalize_float_signed_zero",
    "sha256_canonical_json",
]
