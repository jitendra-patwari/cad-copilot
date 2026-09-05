"""Unit tests for pure deterministic geometry fingerprints and canonical JSON encoding."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

import pytest

from geometry.fingerprints import (
    canonical_json_bytes,
    compute_prompt_fingerprint,
    normalize_canonical_data,
    normalize_float_signed_zero,
    sha256_canonical_json,
)


def test_normalize_canonical_data() -> None:
    data = {"neg_zero": -0.0, "nested": [-0.0, {"inner": -0.0}]}
    norm = normalize_canonical_data(data)
    assert norm["neg_zero"] == 0.0
    assert math.copysign(1.0, norm["neg_zero"]) == 1.0
    assert norm["nested"][0] == 0.0
    assert math.copysign(1.0, norm["nested"][0]) == 1.0
    assert norm["nested"][1]["inner"] == 0.0
    assert math.copysign(1.0, norm["nested"][1]["inner"]) == 1.0


def test_normalize_float_signed_zero() -> None:
    neg_zero = -0.0
    pos_zero = 0.0
    normalized = normalize_float_signed_zero(neg_zero)
    assert math.copysign(1.0, normalized) == 1.0
    assert normalized == pos_zero


def test_canonical_json_bytes_determinism_and_key_sorting() -> None:
    dict_a = {"z": 1, "a": 2, "m": {"b": 3, "a": 4}}
    dict_b = {"a": 2, "m": {"a": 4, "b": 3}, "z": 1}
    bytes_a = canonical_json_bytes(dict_a)
    bytes_b = canonical_json_bytes(dict_b)
    assert bytes_a == bytes_b
    assert bytes_a == b'{"a":2,"m":{"a":4,"b":3},"z":1}'


def test_canonical_json_bytes_signed_zero_normalization() -> None:
    data_neg = {"val": -0.0, "nested": [-0.0, 1.5, -0.0]}
    data_pos = {"val": 0.0, "nested": [0.0, 1.5, 0.0]}
    bytes_neg = canonical_json_bytes(data_neg)
    bytes_pos = canonical_json_bytes(data_pos)
    assert bytes_neg == bytes_pos
    assert b"-0.0" not in bytes_neg
    assert b"0.0" in bytes_neg


def test_canonical_json_bytes_int_and_bool_preservation() -> None:
    data = {"int_zero": 0, "float_zero": 0.0, "bool_true": True, "bool_false": False}
    bytes_data = canonical_json_bytes(data)
    decoded = json.loads(bytes_data.decode("utf-8"))
    assert decoded["int_zero"] == 0
    assert isinstance(decoded["int_zero"], int) and not isinstance(decoded["int_zero"], bool)
    assert decoded["float_zero"] == 0.0
    assert decoded["bool_true"] is True
    assert decoded["bool_false"] is False
    assert bytes_data == b'{"bool_false":false,"bool_true":true,"float_zero":0.0,"int_zero":0}'


def test_canonical_json_bytes_rejects_nan_and_infinity() -> None:
    with pytest.raises(ValueError, match="Non-finite float"):
        canonical_json_bytes({"val": float("nan")})

    with pytest.raises(ValueError, match="Non-finite float"):
        canonical_json_bytes({"val": float("inf")})

    with pytest.raises(ValueError, match="Non-finite float"):
        canonical_json_bytes({"val": float("-inf")})


def test_canonical_json_bytes_rejects_unserializable_types() -> None:
    class CustomObj:
        pass

    with pytest.raises(TypeError, match="not canonical JSON serializable"):
        canonical_json_bytes({"obj": CustomObj()})

    with pytest.raises(TypeError, match="not canonical JSON serializable"):
        canonical_json_bytes({"set": {1, 2, 3}})


def test_sha256_canonical_json() -> None:
    data = {"b": 2, "a": 1}
    expected_bytes = b'{"a":1,"b":2}'
    expected_hex = hashlib.sha256(expected_bytes).hexdigest()
    assert sha256_canonical_json(data) == expected_hex
    assert len(sha256_canonical_json(data)) == 64


def test_compute_prompt_fingerprint() -> None:
    assert compute_prompt_fingerprint(None) is None
    prompt = "Create a 50x50 mounting plate with 4 counterbore holes"
    expected = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    assert compute_prompt_fingerprint(prompt) == expected
    assert len(expected) == 64


def test_pre_refactor_entity_fingerprint_golden_fixtures() -> None:
    """Lock exact byte-for-byte SHA-256 fingerprints against pre-refactor entity lowering fixtures."""

    def _legacy_fingerprint(payload: object) -> str:
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    fixtures: list[tuple[dict[str, Any], str]] = [
        # 1. Box primitive base body payload
        (
            {
                "kind": "body",
                "shape": {"type": "cuboid", "length_mm": 100.0, "width_mm": 80.0, "height_mm": 20.0},
                "origin_offset_mm": {"x_mm": 0.0, "y_mm": 0.0, "z_mm": 0.0},
            },
            "c41c038bfd98c6eb7e9d28e72ca09002e7536731afead61856b2161fdbc13c5e",
        ),
        # 2. Cylinder primitive body payload
        (
            {
                "kind": "body",
                "shape": {"type": "cylinder", "radius_mm": 25.0, "height_mm": 50.0},
                "origin_offset_mm": {"x_mm": 0.0, "y_mm": 0.0, "z_mm": 0.0},
            },
            "0deaeb80c3473c8e0244dedcbf3ecd1e5a568152f80937bf6157ad9cd15daf0f",
        ),
        # 3. Hole feature payload
        (
            {
                "kind": "cut_hole",
                "body_ref": "body.main",
                "profile_ref": "profile.1",
                "through_all": True,
            },
            "25ae996416c9b598122687cf2a0f73691683fbe0cfe889cac99e7a881faac38b",
        ),
        # 4. Gear bore feature payload
        (
            {
                "kind": "cut_hole",
                "body_ref": "body.main",
                "profile_ref": "profile.gear-bore.1",
                "through_all": True,
            },
            "cb2186b2983644fc76e2047d0ff922141cf1f5b5512acfd61fd5d35c155504a0",
        ),
        # 5. Swept protrusion feature payload
        (
            {
                "body_ref": "body.main",
                "direction": "into_solid",
                "kind": "sweep_protrusion",
                "path_refs": ["profile.sweep.path.1"],
                "section_refs": ["profile.sweep.section.1.1"],
            },
            "897de7dacd862d5eb6aca96b97daa1ff71aa5734412d377b76454856e6260d5f",
        ),
        # 6. Boolean operation entity payload
        (
            {
                "kind": "boolean_subtract",
                "target_body_ref": "body.main",
                "tool_body_ref": "body.tool",
            },
            "6f7b60692431e0ef213f638b5fe952a2a5210652858ce809654ab6729e8e763a",
        ),
    ]

    for payload, golden_sha256 in fixtures:
        computed = sha256_canonical_json(payload)
        legacy = _legacy_fingerprint(payload)
        assert computed == golden_sha256
        assert computed == legacy


def test_zero_io_purity_and_leaf_boundary() -> None:
    """Verify geometry.fingerprints has no forbidden subsystem imports."""
    import sys

    mod = sys.modules["geometry.fingerprints"]
    imported_modules = {name for name in dir(mod) if isinstance(getattr(mod, name), type(sys))}
    forbidden = {"manifests", "application", "artifacts", "drivers", "win32com", "pythoncom"}
    assert not (imported_modules & forbidden)
