"""Unit and pipeline integration tests for package distribution and canonical feature lowering."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

from application.models import PromptGenerationRequest
from geometry.plan_lowering import lower_validated_feature_plan_to_payload
from geometry.plan_models import CANONICAL_PLAN_VERSION
from geometry.plan_parser import feature_plan_from_dict
from geometry.plan_validation import validate_feature_plan
from plan_providers.proposal import (
    decode_and_bind_proposal,
)


def _make_prompt_request(request_id: str = "req-test-pipeline-01") -> PromptGenerationRequest:
    return PromptGenerationRequest(
        contract_version="1.0",
        request_id=request_id,
        kind="prompt_to_cad",
        unit="mm",
        prompt="A 25 mm cube",
    )


TEST_SOURCE_ID: str = "test-provider-model"


# ---------------------------------------------------------------------------
# Package Data & Isolated Wheel Distribution Packaging
# ---------------------------------------------------------------------------


def test_package_data_plan_providers_schema_configured() -> None:
    import importlib.resources

    resource = importlib.resources.files("plan_providers").joinpath("schemas", "feature-plan-proposal-v1.schema.json")
    assert resource.is_file(), "Proposal schema file must be accessible as package data"

    pyproject_path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    assert pyproject_path.is_file(), f"pyproject.toml not found at {pyproject_path}"
    content = pyproject_path.read_text(encoding="utf-8")
    assert "[tool.setuptools.package-data]" in content
    assert '"plan_providers" = ["schemas/*.json"]' in content
    assert "gemini = [" in content
    assert '"google-genai>=2.19.0,<3.0.0"' in content


def test_wheel_distribution_packaging_and_isolated_install(tmp_path: Path) -> None:
    """Proves that a built wheel contains schema data and functions in an isolated environment."""
    engine_root = Path(__file__).resolve().parents[2]
    wheel_dir = tmp_path / "wheel"
    install_dir = tmp_path / "install"
    wheel_dir.mkdir()
    install_dir.mkdir()

    # 1. Build wheel offline without build isolation
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "-w",
            str(wheel_dir),
            str(engine_root),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    wheels = list(wheel_dir.glob("*.whl"))
    assert len(wheels) == 1, f"Expected 1 wheel, found {wheels}"
    wheel_path = wheels[0]

    # 2. Inspect archive entries and metadata
    with zipfile.ZipFile(wheel_path) as z:
        names = z.namelist()
        schema_file = "plan_providers/schemas/feature-plan-proposal-v1.schema.json"
        assert schema_file in names, f"Missing {schema_file} in wheel: {names}"

        schema_bytes = z.read(schema_file)
        schema_dict = json.loads(schema_bytes.decode("utf-8"))
        assert schema_dict["title"] == "FeaturePlanProposal"

        meta_name = next(n for n in names if n.endswith(".dist-info/METADATA"))
        meta_text = z.read(meta_name).decode("utf-8")
        lines = [line.strip() for line in meta_text.splitlines()]

        assert "Provides-Extra: gemini" in lines
        assert any(line.startswith("Requires-Dist: google-genai") and 'extra == "gemini"' in line for line in lines)
        assert not any(line.startswith("Requires-Dist: google-genai") and "extra ==" not in line for line in lines)

    # 3. Install wheel to isolated target directory offline
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-index",
            "--target",
            str(install_dir),
            str(wheel_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    # 4. Exercise isolated installation from outside repository in a clean subprocess
    verify_script = """
import sys
from pathlib import Path

install_dir = sys.argv[1]

# Verify import originates from isolated install location
import plan_providers
assert Path(plan_providers.__file__).resolve().is_relative_to(Path(install_dir).resolve()), (
    f"{plan_providers.__file__} is not in {install_dir}"
)

# Verify SDK-free import
assert "google" not in sys.modules
assert "google.genai" not in sys.modules

# Verify schema loading from installed package data
from plan_providers.proposal import load_proposal_schema, decode_and_bind_proposal, ProposalError
schema = load_proposal_schema()
assert schema["title"] == "FeaturePlanProposal"

# Verify deterministic resolution via packaged example catalog
from application.models import ExampleGenerationRequest
from example_catalog import resolve_example_plan

example_req = ExampleGenerationRequest(
    contract_version="1.0",
    request_id="req-whl-example-01",
    kind="example_plan",
    example_id="spur_gear",
    unit="mm",
)
example_proposal = resolve_example_plan(example_req)
assert example_proposal.plan_payload["base_body"]["family"] == "spur_gear"
assert example_proposal.provenance == "example_plan"
assert example_proposal.source_id == "spur_gear"
assert example_proposal.plan_payload["request_id"] == "req-whl-example-01"

# Verify untrusted prompt proposal decoding and binding
from application.models import PromptGenerationRequest
TEST_PROVIDER_MODEL = "test-provider-model"
prompt_req = PromptGenerationRequest(
    contract_version="1.0",
    request_id="req-whl-01",
    kind="prompt_to_cad",
    prompt="make a box",
    unit="mm",
)
proposal = decode_and_bind_proposal(
    '{"base_body": {"family": "rectangular_prism", "dimensions_mm": {"length": 10, "width": 10, "thickness": 10}}}',
    prompt_req,
    TEST_PROVIDER_MODEL,
)
assert proposal.plan_payload["base_body"]["family"] == "rectangular_prism"
assert proposal.provenance == "ai_proposal"
assert proposal.source_id == TEST_PROVIDER_MODEL

# Verify sanitized sdk_unavailable when optional SDK is uninstalled
from plan_providers.gemini import GeminiPlanResolver
sys.modules["google"] = None
sys.modules["google.genai"] = None
resolver = GeminiPlanResolver(api_key="fake-key")
try:
    resolver(prompt_req)
except ProposalError as err:
    assert err.code == "sdk_unavailable"
    assert err.__cause__ is None
else:
    raise AssertionError("Expected ProposalError('sdk_unavailable')")
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(install_dir)
    res = subprocess.run(
        [sys.executable, "-c", verify_script, str(install_dir)],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Isolated verification failed:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"


# ---------------------------------------------------------------------------
# Canonical Pipeline Integration & Feature Lowering
# ---------------------------------------------------------------------------


def test_successful_minimal_block_proposal_binding() -> None:
    req = _make_prompt_request("req-block-01")
    raw = json.dumps(
        {
            "base_body": {
                "family": "rectangular_prism",
                "dimensions_mm": {"length": 50.0, "width": 40.0, "thickness": 10.0},
            }
        }
    )
    proposal = decode_and_bind_proposal(raw, req, TEST_SOURCE_ID)

    assert proposal.provenance == "ai_proposal"
    assert proposal.source_id == TEST_SOURCE_ID
    assert proposal.plan_payload["plan_version"] == CANONICAL_PLAN_VERSION
    assert proposal.plan_payload["request_id"] == "req-block-01"
    assert proposal.plan_payload["units"] == "mm"

    parsed = feature_plan_from_dict(dict(proposal.plan_payload))
    validated = validate_feature_plan(parsed)
    lowered = lower_validated_feature_plan_to_payload(validated)
    assert "patches" in lowered


def test_successful_spur_gear_proposal_binding() -> None:
    req = _make_prompt_request("req-gear-01")
    raw = json.dumps(
        {
            "base_body": {
                "family": "spur_gear",
                "dimensions_mm": {
                    "tooth_count": 24,
                    "module": 2.0,
                    "pressure_angle": 20.0,
                    "face_width": 12.0,
                    "bore_diameter": 8.0,
                },
            }
        }
    )
    proposal = decode_and_bind_proposal(raw, req, TEST_SOURCE_ID)
    parsed = feature_plan_from_dict(dict(proposal.plan_payload))
    validated = validate_feature_plan(parsed)
    assert validated.base_body.family == "spur_gear"


def test_successful_all_base_body_families() -> None:
    req = _make_prompt_request()
    bodies: list[dict[str, Any]] = [
        {"family": "rectangular_prism", "dimensions_mm": {"length": 50, "width": 40, "thickness": 10}},
        {"family": "cylinder", "dimensions_mm": {"radius": 15, "height": 30}},
        {"family": "sphere", "dimensions_mm": {"radius": 20}},
        {
            "family": "spur_gear",
            "dimensions_mm": {"tooth_count": 20, "module": 2.5, "face_width": 8.0, "bore_diameter": 6.0},
        },
        {
            "family": "revolved_shaft",
            "dimensions_mm": {"radius": 10, "height": 20},
            "profile_points": [{"x_mm": 0.0, "y_mm": 0.0}, {"x_mm": 5.0, "y_mm": 10.0}, {"x_mm": 0.0, "y_mm": 20.0}],
        },
    ]
    for b in bodies:
        raw = json.dumps({"base_body": b})
        proposal = decode_and_bind_proposal(raw, req, TEST_SOURCE_ID)
        parsed = feature_plan_from_dict(dict(proposal.plan_payload))
        validated = validate_feature_plan(parsed)
        assert validated.base_body.family == b["family"]


def test_successful_multi_primitive_composition_proposal() -> None:
    req = _make_prompt_request("req-comp-01")
    raw = json.dumps(
        {
            "base_body": {
                "id": "body.main",
                "family": "rectangular_prism",
                "dimensions_mm": {"length": 60, "width": 40, "thickness": 10},
                "placement": {"x_mm": 0, "y_mm": 0, "z_mm": 0},
            },
            "primitive_bodies": [
                {
                    "id": "body.main",
                    "family": "rectangular_prism",
                    "dimensions_mm": {"length": 60, "width": 40, "thickness": 10},
                    "placement": {"x_mm": 0, "y_mm": 0, "z_mm": 0},
                },
                {
                    "id": "body.cylinder.1",
                    "family": "cylinder",
                    "dimensions_mm": {"radius": 8, "height": 20},
                    "placement": {"x_mm": 0, "y_mm": 0, "z_mm": 10},
                },
            ],
            "boolean_operations": [
                {
                    "id": "boolean.1",
                    "operation": "union",
                    "target_body_id": "body.main",
                    "tool_body_id": "body.cylinder.1",
                    "result_body_id": "body.composed.1",
                }
            ],
            "features": [],
        }
    )
    proposal = decode_and_bind_proposal(raw, req, TEST_SOURCE_ID)
    parsed = feature_plan_from_dict(dict(proposal.plan_payload))
    validated = validate_feature_plan(parsed)
    lowered = lower_validated_feature_plan_to_payload(validated)
    assert len(validated.primitive_bodies) == 2
    assert "patches" in lowered


def test_pipeline_positive_all_seven_features() -> None:
    """Proves canonical pipeline integration for all seven supported features."""
    req = _make_prompt_request("req-all-seven-features")
    base_block = {
        "id": "body.main",
        "family": "rectangular_prism",
        "dimensions_mm": {"length": 100.0, "width": 80.0, "thickness": 20.0},
    }

    features: list[dict[str, Any]] = [
        # 1. circular_through_hole
        {
            "id": "f.hole.1",
            "family": "circular_through_hole",
            "target": {"body_id": "body.main", "face": {"resolved_face": "+Z"}},
            "dimensions_mm": {"diameter": 10.0},
            "extent": {"type": "through_all"},
        },
        # 2. rectangular_through_cutout
        {
            "id": "f.cutout.1",
            "family": "rectangular_through_cutout",
            "target": {"body_id": "body.main", "face": {"resolved_face": "+Z"}},
            "dimensions_mm": {"width": 20.0, "height": 10.0},
            "extent": {"type": "through_all"},
        },
        # 3. slot_through_cutout
        {
            "id": "f.slot.1",
            "family": "slot_through_cutout",
            "target": {"body_id": "body.main", "face": {"resolved_face": "+Z"}},
            "dimensions_mm": {"length": 30.0, "width": 8.0},
            "orientation": {"axis": "x"},
            "extent": {"type": "through_all"},
        },
        # 4. rectangular_extruded_pad
        {
            "id": "f.pad.1",
            "family": "rectangular_extruded_pad",
            "target": {"body_id": "body.main", "face": {"resolved_face": "+Z"}},
            "dimensions_mm": {"width": 25.0, "height": 15.0, "distance": 10.0},
        },
        # 5. profile_cutout
        {
            "id": "f.profile.cut.1",
            "family": "profile_cutout",
            "target": {"body_id": "body.main", "face": {"resolved_face": "+Z"}},
            "profile": {
                "points": [
                    {"x_mm": -10.0, "y_mm": -10.0},
                    {"x_mm": 10.0, "y_mm": -10.0},
                    {"x_mm": 10.0, "y_mm": 10.0},
                    {"x_mm": -10.0, "y_mm": 10.0},
                ]
            },
            "extent": {"type": "finite", "depth_mm": 5.0},
        },
        # 6. revolved_profile
        {
            "id": "f.revolve.1",
            "family": "revolved_profile",
            "target": {"body_id": "body.main", "face": {"resolved_face": "+X"}},
            "profile": {
                "points": [
                    {"x_mm": 0.0, "y_mm": 0.0},
                    {"x_mm": 5.0, "y_mm": 0.0},
                    {"x_mm": 5.0, "y_mm": 10.0},
                    {"x_mm": 0.0, "y_mm": 10.0},
                ]
            },
            "revolve": {
                "axis": {
                    "start": {"x_mm": 0.0, "y_mm": 0.0},
                    "end": {"x_mm": 0.0, "y_mm": 1.0},
                },
                "angle_deg": 360.0,
            },
        },
        # 7. swept_protrusion
        {
            "id": "f.sweep.1",
            "family": "swept_protrusion",
            "target": {"body_id": "body.main", "face": {"resolved_face": "+Z"}},
            "path": {
                "type": "full_circle",
                "radius_mm": 30.0,
                "angle_deg": 360.0,
            },
            "cross_sections": [
                {
                    "type": "circle",
                    "diameter_mm": 6.0,
                    "position": "start",
                }
            ],
        },
    ]

    for feat in features:
        raw = json.dumps({"base_body": base_block, "features": [feat]})
        proposal = decode_and_bind_proposal(raw, req, TEST_SOURCE_ID)
        parsed = feature_plan_from_dict(dict(proposal.plan_payload))
        validated = validate_feature_plan(parsed)
        lowered = lower_validated_feature_plan_to_payload(validated)
        assert "patches" in lowered
        assert len(validated.features) == 1
        assert validated.features[0].family == feat["family"]
