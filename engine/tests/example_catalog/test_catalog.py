"""Automated test suite for deterministic example catalog loader (Milestone 4.3).

Verifies catalog contents, schema parity, resource synchronization,
request isolation, defensive error handling, and privacy boundary.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from application.models import ExampleGenerationRequest, PlanProposal
from example_catalog import (
    available_example_ids,
    resolve_example_plan,
)
from example_catalog.catalog import (
    _CATALOG_RESOURCES,
    ExampleCatalogError,
)
from geometry import (
    feature_plan_from_dict,
    lower_validated_feature_plan_to_payload,
    validate_feature_plan,
)
from manifests import (
    FREE_TEXT_CREDENTIAL_PATTERN,
    LOCAL_PATH_PATTERN,
    compute_plan_fingerprint,
    extract_manifest_stable_ids,
    serialize_canonical_feature_plan,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CANONICAL_FIXTURE = REPO_ROOT / "contracts" / "examples" / "spur_gear_example_plan.json"
PACKAGED_FIXTURE = REPO_ROOT / "engine" / "src" / "example_catalog" / "resources" / "spur_gear_example_plan.json"
SCHEMA_PATH = REPO_ROOT / "contracts" / "schemas" / "generation" / "generation-request.schema.json"


def _make_example_request(
    example_id: str = "spur_gear",
    request_id: str = "test-req-001",
) -> ExampleGenerationRequest:
    return ExampleGenerationRequest(
        contract_version="1.0",
        request_id=request_id,
        kind="example_plan",
        unit="mm",
        example_id=example_id,
    )


def test_available_example_ids_returns_exact_tuple() -> None:
    """Catalog exposes exactly spur_gear as the stable first and only entry."""
    ids = available_example_ids()
    assert isinstance(ids, tuple)
    assert ids == ("spur_gear",)


def test_catalog_resources_mapping_is_immutable() -> None:
    """_CATALOG_RESOURCES mapping is private and immutable at runtime via MappingProxyType."""
    with pytest.raises(TypeError):
        _CATALOG_RESOURCES["new_key"] = "new_value.json"  # type: ignore[index]

    with pytest.raises(TypeError):
        del _CATALOG_RESOURCES["spur_gear"]  # type: ignore[attr-defined]


def test_schema_enum_parity() -> None:
    """Generation request schema enum matches catalog available_example_ids exactly."""
    assert SCHEMA_PATH.is_file(), f"Missing schema at {SCHEMA_PATH}"
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        schema = json.load(f)

    # Locate example_id enum in schema oneOf variants
    enum_found: list[str] | None = None
    for variant in schema.get("oneOf", []):
        props = variant.get("properties", {})
        if "example_id" in props and "enum" in props["example_id"]:
            enum_found = list(props["example_id"]["enum"])
            break

    assert enum_found is not None, "example_id enum not found in generation request schema"
    assert tuple(enum_found) == available_example_ids()


def test_semantic_synchronization_with_canonical_fixture() -> None:
    """Packaged spur-gear resource matches contracts/examples/spur_gear_example_plan.json."""
    assert CANONICAL_FIXTURE.is_file(), f"Missing canonical fixture at {CANONICAL_FIXTURE}"
    assert PACKAGED_FIXTURE.is_file(), f"Missing packaged fixture at {PACKAGED_FIXTURE}"

    with open(CANONICAL_FIXTURE, encoding="utf-8") as f:
        canonical_data = json.load(f)

    with open(PACKAGED_FIXTURE, encoding="utf-8") as f:
        packaged_data = json.load(f)

    assert packaged_data == canonical_data, (
        "Packaged spur-gear example plan is not semantically synchronized with canonical fixture"
    )


def test_resolve_spur_gear_success() -> None:
    """Resolving spur_gear produces a valid PlanProposal with request_id substituted."""
    req = _make_example_request(request_id="custom-gear-req-42")
    proposal = resolve_example_plan(req)

    assert isinstance(proposal, PlanProposal)
    assert proposal.provenance == "example_plan"
    assert proposal.source_id == "spur_gear"
    assert proposal.warnings == ()

    payload = proposal.plan_payload
    assert payload["request_id"] == "custom-gear-req-42"
    assert payload["units"] == "mm"
    assert payload["plan_version"] == "cad_copilot.single_part_feature_plan.v1"

    base_body = payload["base_body"]
    assert isinstance(base_body, dict)
    dims = base_body.get("dimensions_mm")
    assert isinstance(dims, dict)
    assert base_body["family"] == "spur_gear"
    assert dims["tooth_count"] == 24
    assert dims["module"] == 2.0
    assert "replace-with-request-id" not in json.dumps(payload)


def test_independent_mutation_isolation() -> None:
    """Decoded plan mappings are isolated across calls and immune to downstream mutation."""
    req1 = _make_example_request(request_id="req-iso-1")
    req2 = _make_example_request(request_id="req-iso-2")

    prop1 = resolve_example_plan(req1)
    prop2 = resolve_example_plan(req2)

    assert prop1.plan_payload["request_id"] == "req-iso-1"
    assert prop2.plan_payload["request_id"] == "req-iso-2"

    # Mutate nested structure in prop1
    body1 = prop1.plan_payload.get("base_body")
    assert isinstance(body1, dict)
    dims1 = body1.get("dimensions_mm")
    assert isinstance(dims1, dict)
    dims1["tooth_count"] = 999
    assert dims1["tooth_count"] == 999

    # prop2 and subsequent resolve must remain unaffected (original tooth_count = 24)
    body2 = prop2.plan_payload.get("base_body")
    assert isinstance(body2, dict)
    dims2 = body2.get("dimensions_mm")
    assert isinstance(dims2, dict)
    assert dims2["tooth_count"] == 24

    prop3 = resolve_example_plan(_make_example_request(request_id="req-iso-3"))
    body3 = prop3.plan_payload.get("base_body")
    assert isinstance(body3, dict)
    dims3 = body3.get("dimensions_mm")
    assert isinstance(dims3, dict)
    assert dims3["tooth_count"] == 24


def test_unknown_example_id_raises_catalog_error() -> None:
    """Requesting an unregistered example_id fails closed with ExampleCatalogError."""
    req = _make_example_request(example_id="unknown_box")
    with pytest.raises(ExampleCatalogError, match="not in the deterministic catalog"):
        resolve_example_plan(req)


@pytest.mark.parametrize(
    "malicious_id",
    [
        "../spur_gear",
        "..\\spur_gear",
        "/etc/passwd",
        "C:\\models\\gear",
        "spur_gear.json",
        "spur_gear/extra",
        "spur_gear\x00",
    ],
)
def test_path_traversal_ids_rejected_defensively(malicious_id: str) -> None:
    """Traversal-like, drive-qualified, or suffix-bearing IDs are rejected by catalog lookup."""
    req = _make_example_request(example_id=malicious_id)
    with pytest.raises(ExampleCatalogError, match="not in the deterministic catalog"):
        resolve_example_plan(req)


def test_privacy_probe_error_messages() -> None:
    """Catalog error messages must not leak local paths, drive letters, or sensitive tokens."""
    req = _make_example_request(example_id="C:\\Users\\admin\\secret_part")
    with pytest.raises(ExampleCatalogError) as exc_info:
        resolve_example_plan(req)

    err_msg = str(exc_info.value)
    # Ensure neither Windows drive letters nor UNC paths appear in the error message
    assert not LOCAL_PATH_PATTERN.search(err_msg), f"Error message leaked path: {err_msg}"
    assert not FREE_TEXT_CREDENTIAL_PATTERN.search(err_msg), f"Error message leaked credential: {err_msg}"


class _FailingReadTraversable:
    """Helper to mock importlib.resources.files when read_bytes fails."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def read_bytes(self) -> bytes:
        raise self._exc

    def joinpath(self, *parts: str) -> _FailingReadTraversable:
        return self


def test_unreadable_resource_read_bytes_fails_safely() -> None:
    """Read failure in read_bytes raises generic ExampleCatalogError without path or exception leakage."""
    req = _make_example_request()
    sensitive_path = "C:\\secret\\cad_workstation\\key.json"
    mock_trav = _FailingReadTraversable(PermissionError(f"Access denied: {sensitive_path}"))

    with (
        patch("importlib.resources.files", return_value=mock_trav),
        pytest.raises(ExampleCatalogError, match="Failed to read example plan catalog resource") as exc_info,
    ):
        resolve_example_plan(req)

    err_msg = str(exc_info.value)
    assert sensitive_path not in err_msg
    assert not LOCAL_PATH_PATTERN.search(err_msg), f"Error message leaked path: {err_msg}"
    assert not FREE_TEXT_CREDENTIAL_PATTERN.search(err_msg), f"Error message leaked credential: {err_msg}"


def test_unreadable_resource_files_lookup_fails_safely() -> None:
    """Lookup failure in importlib.resources.files raises generic ExampleCatalogError without path leak."""
    req = _make_example_request()
    sensitive_path = "D:\\internal\\copilot_assets\\spur_gear.json"

    with (
        patch("importlib.resources.files", side_effect=FileNotFoundError(f"Not found: {sensitive_path}")),
        pytest.raises(ExampleCatalogError, match="Failed to read example plan catalog resource") as exc_info,
    ):
        resolve_example_plan(req)

    err_msg = str(exc_info.value)
    assert sensitive_path not in err_msg
    assert not LOCAL_PATH_PATTERN.search(err_msg), f"Error message leaked path: {err_msg}"


class _MockTraversable:
    """Helper to mock importlib.resources.files for failure injection."""

    def __init__(self, content: bytes) -> None:
        self._content = content

    def read_bytes(self) -> bytes:
        return self._content

    def joinpath(self, *parts: str) -> _MockTraversable:
        return self


def test_corrupt_resource_invalid_utf8() -> None:
    """Invalid UTF-8 bytes in resource file raise ExampleCatalogError."""
    req = _make_example_request()
    mock_trav = _MockTraversable(b"\xff\xfe\x00\x00invalid")

    with (
        patch("importlib.resources.files", return_value=mock_trav),
        pytest.raises(ExampleCatalogError, match="invalid UTF-8"),
    ):
        resolve_example_plan(req)


def test_corrupt_resource_invalid_json() -> None:
    """Malformed JSON in resource file raises ExampleCatalogError."""
    req = _make_example_request()
    mock_trav = _MockTraversable(b"{not valid json")

    with (
        patch("importlib.resources.files", return_value=mock_trav),
        pytest.raises(ExampleCatalogError, match="not valid JSON"),
    ):
        resolve_example_plan(req)


def test_corrupt_resource_non_object_root() -> None:
    """JSON array root instead of object raises ExampleCatalogError."""
    req = _make_example_request()
    mock_trav = _MockTraversable(b"[1, 2, 3]")

    with (
        patch("importlib.resources.files", return_value=mock_trav),
        pytest.raises(ExampleCatalogError, match="root must be a JSON object"),
    ):
        resolve_example_plan(req)


def test_corrupt_resource_duplicate_keys() -> None:
    """JSON with duplicate object keys raises ExampleCatalogError."""
    req = _make_example_request()
    mock_trav = _MockTraversable(b'{"key": 1, "key": 2}')

    with (
        patch("importlib.resources.files", return_value=mock_trav),
        pytest.raises(ExampleCatalogError, match="not valid JSON"),
    ):
        resolve_example_plan(req)


def test_corrupt_resource_non_finite_constant() -> None:
    """JSON with NaN or Infinity constant raises ExampleCatalogError."""
    req = _make_example_request()
    mock_trav = _MockTraversable(b'{"value": NaN}')

    with (
        patch("importlib.resources.files", return_value=mock_trav),
        pytest.raises(ExampleCatalogError, match="not valid JSON"),
    ):
        resolve_example_plan(req)


# ---------------------------------------------------------------------------
# Section 11.3: Canonical Pipeline Integration Tests
# ---------------------------------------------------------------------------


def test_proposal_passes_canonical_pipeline() -> None:
    """Production spur_gear proposal parses, validates (capability_first and strict), and lowers."""
    req = _make_example_request(request_id="canonical-pipe-req")
    proposal = resolve_example_plan(req)

    # 1. Parse into canonical FeaturePlan
    plan = feature_plan_from_dict(dict(proposal.plan_payload))
    assert plan.request_id == "canonical-pipe-req"
    assert plan.units == "mm"
    assert plan.base_body.family == "spur_gear"

    # 2. Validate under default capability_first mode
    val_cap = validate_feature_plan(plan, mode="capability_first")
    assert val_cap.base_body.family == "spur_gear"

    # 3. Validate under strict mode
    val_strict = validate_feature_plan(plan, mode="strict")
    assert val_strict.base_body.family == "spur_gear"

    # 4. Lower to execution payload
    lowered = lower_validated_feature_plan_to_payload(val_cap)
    assert lowered["kind"] == "semantic_patch_sequence"
    assert lowered["request_id"] == "canonical-pipe-req"
    assert lowered["patches"][0]["op"] == "ensure_primitive_body"
    shape = lowered["patches"][0]["shape"]
    assert shape["type"] == "extruded_profile"
    assert shape["profile_family"] == "spur_gear_concept"
    assert len(shape["points"]) > 0
    assert shape["height_mm"] == 10.0


def test_proposal_canonical_determinism_and_fingerprints() -> None:
    """Canonical validation and fingerprints are strictly deterministic across calls with identical request IDs."""
    req1 = _make_example_request(request_id="det-req-001")
    req2 = _make_example_request(request_id="det-req-001")

    prop1 = resolve_example_plan(req1)
    prop2 = resolve_example_plan(req2)

    plan1 = feature_plan_from_dict(dict(prop1.plan_payload))
    plan2 = feature_plan_from_dict(dict(prop2.plan_payload))

    val1 = validate_feature_plan(plan1)
    val2 = validate_feature_plan(plan2)

    fp1 = compute_plan_fingerprint(serialize_canonical_feature_plan(val1))
    fp2 = compute_plan_fingerprint(serialize_canonical_feature_plan(val2))
    assert fp1 == fp2

    stable_ids1 = extract_manifest_stable_ids(val1)
    stable_ids2 = extract_manifest_stable_ids(val2)
    assert stable_ids1 == stable_ids2


def test_proposal_different_request_ids_preserve_geometry() -> None:
    """Varying request IDs changes identity fields but preserves all geometry and features."""
    req_a = _make_example_request(request_id="req-alpha")
    req_b = _make_example_request(request_id="req-beta")

    prop_a = resolve_example_plan(req_a)
    prop_b = resolve_example_plan(req_b)

    plan_a = feature_plan_from_dict(dict(prop_a.plan_payload))
    plan_b = feature_plan_from_dict(dict(prop_b.plan_payload))

    assert plan_a.request_id == "req-alpha"
    assert plan_b.request_id == "req-beta"
    assert plan_a.request_id != plan_b.request_id

    # Geometry fields are strictly equal
    assert plan_a.base_body == plan_b.base_body
    assert plan_a.primitive_bodies == plan_b.primitive_bodies
    assert plan_a.boolean_operations == plan_b.boolean_operations
    assert plan_a.features == plan_b.features
