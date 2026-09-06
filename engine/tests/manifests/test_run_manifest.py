"""Comprehensive unit tests for assemble_run_manifest and write_staged_run_manifest."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, cast

import pytest

from artifacts.validation import FileSnapshot
from geometry.plan_models import (
    BodyPlacement,
    FeaturePlan,
    LoweringStrategy,
    PartMetadata,
    RectangularBaseBody,
)
from interfaces.models import (
    ArtifactRecord,
    ExecutionSuccess,
    OperationResult,
    StandardInspectionReport,
)
from manifests import (
    CANONICAL_MANIFEST_VERSION,
    ManifestStableIds,
    ManifestValidationError,
    RunManifestContext,
    assemble_run_manifest,
    get_run_manifest_validator,
    prepare_manifest_data,
    write_staged_run_manifest,
)


def _build_test_feature_plan(
    *,
    request_id: str = "req-manifest-001",
    part_id: str = "part.main",
    body_id: str = "body.base",
    design_intent: str = "test block",
) -> FeaturePlan:
    """Construct a minimal valid FeaturePlan for testing."""
    return FeaturePlan(
        plan_version="cad_copilot.single_part_feature_plan.v1",
        request_id=request_id,
        part=PartMetadata(part_id=part_id, design_intent=design_intent),
        base_body=RectangularBaseBody(
            id=body_id,
            length_mm=100.0,
            width_mm=50.0,
            thickness_mm=20.0,
            placement=BodyPlacement(x_mm=0.0, y_mm=0.0, z_mm=0.0),
            semantic_labels=("base_plate",),
        ),
        primitive_bodies=(),
        boolean_operations=(),
        features=(),
        defaults_applied=(),
        validation_diagnostics=(),
        lowering_strategy=LoweringStrategy(),
    )


def _build_test_context(
    *,
    request_id: str = "req-manifest-001",
    request_kind: str = "prompt_to_cad",
    provenance_kind: str = "ai_proposal",
    source_id: str = "source-test-01",
    gate_mode: str = "capability_first",
    prompt: str | None = "A solid rectangular block 100x50x20mm",
    cad_runtime_version_build: str | None = "Solid Edge 2026 (226.00.00.106)",
    warnings: tuple[str, ...] = ("Test warning 1",),
) -> RunManifestContext:
    """Construct a strictly validated RunManifestContext for testing."""
    plan = _build_test_feature_plan(request_id=request_id)
    prompt_sha256 = (
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef" if request_kind == "prompt_to_cad" else None
    )
    prep_data = prepare_manifest_data(
        plan,
        provenance_kind=provenance_kind,
        source_id=source_id,
        gate_mode=gate_mode,
        request_kind=request_kind,
        prompt_sha256=prompt_sha256,
    )
    return RunManifestContext(
        prepared_data=prep_data,
        request_id=request_id,
        contract_version="1.0",
        unit="mm",
        cad_runtime_version_build=cad_runtime_version_build,
        warnings=warnings,
    )


def _build_test_execution_success(
    *,
    operations_executed: int = 1,
    volume_mm3: float = 100000.0,
    mass_kg: float = 0.27,
    feature_count: int = 0,
    body_count: int = 1,
    solid_body_count: int | None = 1,
    sheet_body_count: int | None = 0,
    wire_body_count: int | None = 0,
) -> ExecutionSuccess:
    """Construct a realistic ExecutionSuccess with inspection report."""
    inspection = StandardInspectionReport(
        volume_mm3=volume_mm3,
        mass_kg=mass_kg,
        feature_count=feature_count,
        body_count=body_count,
        solid_body_count=solid_body_count,
        sheet_body_count=sheet_body_count,
        wire_body_count=wire_body_count,
    )
    return ExecutionSuccess(
        operations_executed=operations_executed,
        exported_artifacts=[],
        warnings=[],
        operation_results=[
            OperationResult(
                patch_id="patch_0",
                operation="add_base_body",
                reference_id="body.base",
                reference_kind="body",
            )
        ],
        inspection_report=inspection,
    )


def _build_test_artifacts(
    request_id: str = "req-manifest-001",
    *,
    include_preview: bool = False,
    base_dir: Path | None = None,
) -> list[ArtifactRecord]:
    """Construct a canonical sequence of ArtifactRecords for testing."""
    prefix = str(base_dir / request_id) if base_dir else request_id
    records = [
        ArtifactRecord(
            type="native_part",
            format="par",
            path=f"{prefix}.par",
            origin="cad_copilot",
            size_bytes=1024,
            sha256="1111111111111111111111111111111111111111111111111111111111111111",
        ),
        ArtifactRecord(
            type="geometry_step",
            format="step",
            path=f"{prefix}.step",
            origin="cad_copilot",
            size_bytes=2048,
            sha256="2222222222222222222222222222222222222222222222222222222222222222",
        ),
        ArtifactRecord(
            type="mesh_stl",
            format="stl",
            path=f"{prefix}.stl",
            origin="cad_copilot",
            size_bytes=4096,
            sha256="3333333333333333333333333333333333333333333333333333333333333333",
        ),
    ]
    if include_preview:
        records.append(
            ArtifactRecord(
                type="preview_image",
                format="jpg",
                path=f"{prefix}.jpg",
                origin="cad_copilot",
                size_bytes=8192,
                sha256="4444444444444444444444444444444444444444444444444444444444444444",
            )
        )
    return records


# ===========================================================================
# 1. Happy Path Manifest Assembly
# ===========================================================================


def test_assemble_run_manifest_3_artifacts_prompt_to_cad() -> None:
    context = _build_test_context(request_id="req-prompt-001", request_kind="prompt_to_cad")
    execution = _build_test_execution_success()
    artifacts = _build_test_artifacts(request_id="req-prompt-001", include_preview=False)

    manifest = assemble_run_manifest(context, execution, artifacts)

    assert isinstance(manifest, dict)
    assert manifest["manifest_version"] == CANONICAL_MANIFEST_VERSION
    assert manifest["request"] == {
        "request_id": "req-prompt-001",
        "contract_version": "1.0",
        "kind": "prompt_to_cad",
        "unit": "mm",
    }
    assert manifest["engine"]["name"] == "cad-copilot"
    assert manifest["cad_runtime"]["product"] == "solid_edge"
    assert manifest["cad_runtime"]["version_build"] == "Solid Edge 2026 (226.00.00.106)"
    assert manifest["provenance"] == {
        "kind": "ai_proposal",
        "source_id": "source-test-01",
    }
    assert manifest["gate_mode"] == "capability_first"
    assert manifest["fingerprints"]["plan_sha256"] == context.prepared_data.plan_sha256
    assert manifest["fingerprints"]["prompt_sha256"] == context.prepared_data.prompt_sha256
    assert len(manifest["fingerprints"]["prompt_sha256"]) == 64

    # Check stable IDs
    assert manifest["stable_ids"]["part_id"] == "part.main"
    assert manifest["stable_ids"]["body_ids"] == ["body.base"]

    # Check execution
    assert manifest["execution"]["operations_executed"] == 1
    assert len(manifest["execution"]["operation_results"]) == 1
    assert manifest["execution"]["inspection"]["volume_mm3"] == 100000.0
    assert manifest["execution"]["inspection"]["mass_kg"] == 0.27

    # Check artifacts
    assert len(manifest["artifacts"]) == 3
    assert manifest["artifacts"][0] == {
        "type": "native_part",
        "format": "par",
        "path": "req-prompt-001.par",
        "size_bytes": 1024,
        "sha256": "1111111111111111111111111111111111111111111111111111111111111111",
    }
    assert manifest["artifacts"][1] == {
        "type": "geometry_step",
        "format": "step",
        "path": "req-prompt-001.step",
        "size_bytes": 2048,
        "sha256": "2222222222222222222222222222222222222222222222222222222222222222",
    }
    assert manifest["artifacts"][2] == {
        "type": "mesh_stl",
        "format": "stl",
        "path": "req-prompt-001.stl",
        "size_bytes": 4096,
        "sha256": "3333333333333333333333333333333333333333333333333333333333333333",
    }

    # Verify 'origin' is strictly absent from all artifact entries
    for art in manifest["artifacts"]:
        assert "origin" not in art

    # Full schema validation
    validator = get_run_manifest_validator()
    validator.validate(manifest)


def test_assemble_run_manifest_4_artifacts_example_plan(tmp_path: Path) -> None:
    context = _build_test_context(
        request_id="req-example-002",
        request_kind="example_plan",
        provenance_kind="example_plan",
        source_id="example.block",
        cad_runtime_version_build=None,
    )
    execution = _build_test_execution_success()
    # Provide artifacts with absolute paths in tmp_path to test relative projection
    artifacts = _build_test_artifacts(request_id="req-example-002", include_preview=True, base_dir=tmp_path)

    manifest = assemble_run_manifest(context, execution, artifacts)

    assert manifest["request"]["kind"] == "example_plan"
    assert manifest["provenance"]["kind"] == "example_plan"
    assert manifest["cad_runtime"]["version_build"] is None
    assert manifest["fingerprints"]["prompt_sha256"] is None

    # Check 4 artifacts
    assert len(manifest["artifacts"]) == 4
    assert manifest["artifacts"][3] == {
        "type": "preview_image",
        "format": "jpg",
        "path": "req-example-002.jpg",
        "size_bytes": 8192,
        "sha256": "4444444444444444444444444444444444444444444444444444444444444444",
    }

    for art in manifest["artifacts"]:
        # Verify only filename without parent directory
        assert "/" not in art["path"]
        assert "\\" not in art["path"]
        assert "origin" not in art

    # Schema validation
    validator = get_run_manifest_validator()
    validator.validate(manifest)


def test_assemble_preserves_inspection_and_normalizes_zero() -> None:
    context = _build_test_context()
    execution = _build_test_execution_success(
        volume_mm3=500.0,
        mass_kg=-0.0,
        feature_count=0,
        body_count=1,
        solid_body_count=1,
        sheet_body_count=0,
        wire_body_count=None,
    )
    artifacts = _build_test_artifacts(context.request_id)

    manifest = assemble_run_manifest(context, execution, artifacts)
    insp = manifest["execution"]["inspection"]

    assert insp["volume_mm3"] == 500.0
    assert insp["mass_kg"] == 0.0
    assert math.copysign(1.0, insp["mass_kg"]) == 1.0  # Normalized signed zero
    assert insp["solid_body_count"] == 1
    assert insp["sheet_body_count"] == 0
    assert insp["wire_body_count"] is None


# ===========================================================================
# 2. Consistency & Integrity Validations
# ===========================================================================


def test_assemble_rejects_mismatched_request_id_in_plan() -> None:
    context = _build_test_context(request_id="req-A")
    # Mutate the plan request_id inside prepared_data
    bad_plan = dict(context.prepared_data.feature_plan)
    bad_plan["request_id"] = "req-B"
    object.__setattr__(context.prepared_data, "feature_plan", bad_plan)

    execution = _build_test_execution_success()
    artifacts = _build_test_artifacts(request_id="req-A")

    with pytest.raises(ManifestValidationError, match="request_id mismatch"):
        assemble_run_manifest(context, execution, artifacts)


def test_assemble_rejects_mismatched_artifact_request_id() -> None:
    context = _build_test_context(request_id="req-valid")
    execution = _build_test_execution_success()
    # Artifact path uses mismatched ID
    artifacts = _build_test_artifacts(request_id="req-other")

    with pytest.raises(ManifestValidationError, match="does not match expected canonical filename"):
        assemble_run_manifest(context, execution, artifacts)


def test_assemble_rejects_plan_fingerprint_mismatch() -> None:
    context = _build_test_context()
    # Mutate plan_sha256 to simulate tampered payload
    object.__setattr__(
        context.prepared_data,
        "plan_sha256",
        "0000000000000000000000000000000000000000000000000000000000000000",
    )
    execution = _build_test_execution_success()
    artifacts = _build_test_artifacts(context.request_id)

    with pytest.raises(ManifestValidationError, match="plan_sha256 mismatch"):
        assemble_run_manifest(context, execution, artifacts)


def test_assemble_rejects_stable_ids_mismatch() -> None:
    context = _build_test_context()
    # Alter stable_ids in context to not match feature plan
    tampered_ids = ManifestStableIds(
        part_id="part.main",
        body_ids=("body.base", "body.extra_tampered"),
        boolean_operation_ids=(),
        feature_ids=(),
    )
    object.__setattr__(context.prepared_data, "stable_ids", tampered_ids)
    execution = _build_test_execution_success()
    artifacts = _build_test_artifacts(context.request_id)

    with pytest.raises(ManifestValidationError, match="stable_ids mismatch"):
        assemble_run_manifest(context, execution, artifacts)


# ===========================================================================
# 3. Artifact Validation & Canonical Order Rules
# ===========================================================================


@pytest.mark.parametrize("bad_count", [0, 1, 2, 5])
def test_assemble_rejects_invalid_artifact_count(bad_count: int) -> None:
    context = _build_test_context()
    execution = _build_test_execution_success()
    base_arts = _build_test_artifacts(context.request_id, include_preview=True)

    if bad_count < len(base_arts):
        arts = base_arts[:bad_count]
    else:
        arts = [
            *base_arts,
            ArtifactRecord(
                type="preview_image",
                format="jpg",
                path=f"{context.request_id}-extra.jpg",
                size_bytes=100,
                sha256="9" * 64,
            ),
        ]

    with pytest.raises(ManifestValidationError, match="Invalid artifact count"):
        assemble_run_manifest(context, execution, arts)


def test_assemble_rejects_out_of_order_artifacts() -> None:
    context = _build_test_context()
    execution = _build_test_execution_success()
    arts = _build_test_artifacts(context.request_id)
    # Swap STEP (index 1) and PAR (index 0)
    swapped = [arts[1], arts[0], arts[2]]

    with pytest.raises(ManifestValidationError, match="must have format 'par'"):
        assemble_run_manifest(context, execution, swapped)


def test_assemble_rejects_invalid_artifact_size() -> None:
    context = _build_test_context()
    execution = _build_test_execution_success()

    for bad_size in (0, -10, None, True):
        arts = _build_test_artifacts(context.request_id)
        arts[0].size_bytes = cast(Any, bad_size)
        with pytest.raises(ManifestValidationError, match="invalid size_bytes"):
            assemble_run_manifest(context, execution, arts)


def test_assemble_rejects_invalid_artifact_sha256() -> None:
    context = _build_test_context()
    execution = _build_test_execution_success()

    for bad_sha in (None, "not_hex", "1234", "A" * 64):
        arts = _build_test_artifacts(context.request_id)
        arts[0].sha256 = cast(Any, bad_sha)
        with pytest.raises(ManifestValidationError, match="sha256"):
            assemble_run_manifest(context, execution, arts)


def test_assemble_rejects_run_manifest_in_artifacts() -> None:
    context = _build_test_context()
    execution = _build_test_execution_success()
    arts = _build_test_artifacts(context.request_id)
    arts[0].path = "run_manifest.json"

    with pytest.raises(ManifestValidationError, match=re.escape("run_manifest.json must not be listed in artifacts")):
        assemble_run_manifest(context, execution, arts)


def test_assemble_rejects_none_artifact_path() -> None:
    context = _build_test_context()
    execution = _build_test_execution_success()
    arts = _build_test_artifacts(context.request_id)
    arts[0].path = cast(Any, None)

    with pytest.raises(ManifestValidationError, match="path cannot be None"):
        assemble_run_manifest(context, execution, arts)


# ===========================================================================
# 4. Execution & Inspection Edge Cases
# ===========================================================================


def test_assemble_rejects_missing_inspection_report() -> None:
    context = _build_test_context()
    execution = ExecutionSuccess(
        operations_executed=1,
        inspection_report=None,
    )
    artifacts = _build_test_artifacts(context.request_id)

    with pytest.raises(ManifestValidationError, match="missing required inspection_report"):
        assemble_run_manifest(context, execution, artifacts)


@pytest.mark.parametrize("bad_volume", [0.0, -100.0, float("nan"), float("inf")])
def test_assemble_rejects_invalid_inspection_volume(bad_volume: float) -> None:
    context = _build_test_context()
    execution = _build_test_execution_success(volume_mm3=bad_volume)
    artifacts = _build_test_artifacts(context.request_id)

    with pytest.raises(ManifestValidationError, match="volume_mm3"):
        assemble_run_manifest(context, execution, artifacts)


@pytest.mark.parametrize("bad_mass", [-0.01, float("nan"), float("inf")])
def test_assemble_rejects_invalid_inspection_mass(bad_mass: float) -> None:
    context = _build_test_context()
    execution = _build_test_execution_success(mass_kg=bad_mass)
    artifacts = _build_test_artifacts(context.request_id)

    with pytest.raises(ManifestValidationError, match="mass_kg"):
        assemble_run_manifest(context, execution, artifacts)


def test_assemble_rejects_invalid_argument_types() -> None:
    context = _build_test_context()
    execution = _build_test_execution_success()
    artifacts = _build_test_artifacts(context.request_id)

    with pytest.raises(ManifestValidationError, match="context must be a RunManifestContext"):
        assemble_run_manifest(cast(Any, {}), execution, artifacts)

    with pytest.raises(ManifestValidationError, match="execution must be an ExecutionSuccess"):
        assemble_run_manifest(context, cast(Any, {}), artifacts)

    with pytest.raises(ManifestValidationError, match="artifacts must be a sequence"):
        assemble_run_manifest(context, execution, cast(Any, "invalid"))


def test_assemble_run_manifest_schema_failure_sanitized() -> None:
    context = _build_test_context()
    execution = _build_test_execution_success()
    artifacts = _build_test_artifacts(context.request_id)

    # Corrupt prepared_data feature_plan with an invalid type to trigger schema failure
    bad_plan = dict(context.prepared_data.feature_plan)
    bad_plan["units"] = "invalid_unit_inches_with_secret_sk-12345"
    object.__setattr__(context.prepared_data, "feature_plan", bad_plan)
    # Also recalculate plan_sha256 so it passes fingerprint consistency and reaches schema validation
    from manifests.run_manifest import compute_plan_fingerprint

    object.__setattr__(context.prepared_data, "plan_sha256", compute_plan_fingerprint(bad_plan))

    with pytest.raises(ManifestValidationError) as excinfo:
        assemble_run_manifest(context, execution, artifacts)
    err = str(excinfo.value)
    assert err == "Assembled run manifest failed schema validation"
    assert "sk-12345" not in err
    assert "invalid_unit" not in err


# ===========================================================================
# 5. Staged Manifest Writing & Read-Back Validation
# ===========================================================================


def test_write_staged_run_manifest_success(tmp_path: Path) -> None:
    context = _build_test_context(request_id="req-stage-001")
    execution = _build_test_execution_success()
    artifacts = _build_test_artifacts("req-stage-001")
    manifest = assemble_run_manifest(context, execution, artifacts)

    staging_path = tmp_path / "run_manifest.json"
    written_path = write_staged_run_manifest(staging_path, manifest)

    assert isinstance(written_path, Path)
    assert written_path == staging_path
    assert staging_path.is_file()

    # Verify pipeline caller can capture snapshot from the returned path
    snapshot = FileSnapshot.capture(written_path)
    assert isinstance(snapshot, FileSnapshot)
    assert snapshot.st_size == staging_path.stat().st_size
    assert snapshot.st_size > 0

    # Read back and verify exact JSON contents
    content_bytes = staging_path.read_bytes()
    data = json.loads(content_bytes.decode("utf-8"))
    assert data["manifest_version"] == CANONICAL_MANIFEST_VERSION
    assert data["request"]["request_id"] == "req-stage-001"

    # Verify formatting has indentation
    assert b'\n  "manifest_version":' in content_bytes


def test_write_staged_run_manifest_collision_fails_closed(tmp_path: Path) -> None:
    context = _build_test_context()
    execution = _build_test_execution_success()
    artifacts = _build_test_artifacts(context.request_id)
    manifest = assemble_run_manifest(context, execution, artifacts)

    staging_path = tmp_path / "run_manifest.json"
    # Pre-create the file to simulate collision
    staging_path.write_text("existing content", encoding="utf-8")

    with pytest.raises(
        ManifestValidationError, match=re.escape("Staged manifest destination already exists: 'run_manifest.json'")
    ):
        write_staged_run_manifest(staging_path, manifest)

    # Content untouched
    assert staging_path.read_text(encoding="utf-8") == "existing content"


def test_write_staged_run_manifest_rejects_non_canonical_name(tmp_path: Path) -> None:
    context = _build_test_context()
    execution = _build_test_execution_success()
    artifacts = _build_test_artifacts(context.request_id)
    manifest = assemble_run_manifest(context, execution, artifacts)

    bad_path = tmp_path / "manifest.json"
    with pytest.raises(ManifestValidationError, match=re.escape("filename must be 'run_manifest.json'")):
        write_staged_run_manifest(bad_path, manifest)


def test_write_staged_run_manifest_non_path_fails() -> None:
    with pytest.raises(ManifestValidationError, match=re.escape("staging_manifest_path must be a pathlib.Path")):
        write_staged_run_manifest(cast(Any, "string_path"), {})


def test_write_staged_run_manifest_non_mapping_fails(tmp_path: Path) -> None:
    staging_path = tmp_path / "run_manifest.json"
    with pytest.raises(ManifestValidationError, match="manifest_data must be a mapping"):
        write_staged_run_manifest(staging_path, cast(Any, [1, 2, 3]))


def test_write_staged_run_manifest_read_back_schema_failure(tmp_path: Path) -> None:
    # Construct invalid manifest data (missing required sections)
    bad_manifest = {
        "manifest_version": CANONICAL_MANIFEST_VERSION,
        "incomplete": True,
    }
    staging_path = tmp_path / "run_manifest.json"

    with pytest.raises(ManifestValidationError, match="Staged run manifest failed schema validation"):
        write_staged_run_manifest(staging_path, bad_manifest)


# ===========================================================================
# 6. Privacy & Zero-Leak Assertions
# ===========================================================================


def test_assembled_manifest_zero_leaks(tmp_path: Path) -> None:
    context = _build_test_context(
        request_id="req-privacy-001",
        request_kind="prompt_to_cad",
        prompt="Secret Prompt Text: create a widget with password123",
        warnings=("Sanitized warning without internal paths",),
    )
    execution = _build_test_execution_success()
    artifacts = _build_test_artifacts("req-privacy-001", include_preview=True, base_dir=tmp_path)

    manifest = assemble_run_manifest(context, execution, artifacts)
    staging_path = tmp_path / "run_manifest.json"
    write_staged_run_manifest(staging_path, manifest)

    raw_json = staging_path.read_text(encoding="utf-8")

    # 1. No drive letters with paths (e.g. C:\ or E:/)
    assert not re.search(r"[A-Za-z]:[/\\]", raw_json)

    # 2. No UNC network paths (\\server\share)
    assert not re.search(r"\\\\[A-Za-z0-9]", raw_json)

    # 3. No user home/profile paths
    assert "Users" not in raw_json
    assert "/home/" not in raw_json
    assert "AppData" not in raw_json

    # 4. No raw prompt text leaked
    assert "Secret Prompt Text" not in raw_json
    assert "password123" not in raw_json

    # 5. No 'origin' key in artifacts
    for art in manifest["artifacts"]:
        assert "origin" not in art
    assert '"origin": "cad_copilot"' not in raw_json

    # 6. Artifact paths are relative filenames only
    for art in manifest["artifacts"]:
        assert art["path"].startswith("req-privacy-001.")
        assert "/" not in art["path"]
        assert "\\" not in art["path"]


def test_assemble_run_manifest_operation_result_privacy_probes() -> None:
    context = _build_test_context()
    artifacts = _build_test_artifacts(context.request_id)

    # 1. Windows path in patch_id fails closed without leaking path
    bad_exec_1 = ExecutionSuccess(
        operations_executed=1,
        operation_results=[
            OperationResult(
                patch_id="C:\\Users\\admin\\Desktop\\patch_0",
                operation="add_base_body",
                reference_id="body.base",
                reference_kind="body",
            )
        ],
        inspection_report=StandardInspectionReport(
            volume_mm3=100.0,
            mass_kg=0.1,
            feature_count=0,
            body_count=1,
        ),
    )
    with pytest.raises(ManifestValidationError) as excinfo1:
        assemble_run_manifest(context, bad_exec_1, artifacts)
    err1 = str(excinfo1.value)
    assert "contains forbidden credential or path pattern" in err1 or "safe identifier" in err1
    assert "admin" not in err1
    assert "Desktop" not in err1
    assert "Users" not in err1
    assert "C:\\" not in err1

    # 2. password=TOPSECRET in operation fails closed without leaking password
    bad_exec_2 = ExecutionSuccess(
        operations_executed=1,
        operation_results=[
            OperationResult(
                patch_id="patch_0",
                operation="password=TOPSECRET",
                reference_id="body.base",
                reference_kind="body",
            )
        ],
        inspection_report=StandardInspectionReport(
            volume_mm3=100.0,
            mass_kg=0.1,
            feature_count=0,
            body_count=1,
        ),
    )
    with pytest.raises(ManifestValidationError) as excinfo2:
        assemble_run_manifest(context, bad_exec_2, artifacts)
    err2 = str(excinfo2.value)
    assert "contains forbidden credential or path pattern" in err2 or "safe identifier" in err2
    assert "TOPSECRET" not in err2
    assert "password" not in err2

    # 3. sk- credential in reference_id fails closed without leaking credential
    sk_token = "sk-ant-api03-my-super-secret-key-12345"
    bad_exec_3 = ExecutionSuccess(
        operations_executed=1,
        operation_results=[
            OperationResult(
                patch_id="patch_0",
                operation="add_base_body",
                reference_id=sk_token,
                reference_kind="body",
            )
        ],
        inspection_report=StandardInspectionReport(
            volume_mm3=100.0,
            mass_kg=0.1,
            feature_count=0,
            body_count=1,
        ),
    )
    with pytest.raises(ManifestValidationError) as excinfo3:
        assemble_run_manifest(context, bad_exec_3, artifacts)
    err3 = str(excinfo3.value)
    assert "contains forbidden credential or path pattern" in err3
    assert sk_token not in err3
    assert "my-super-secret-key" not in err3

    # 4. Invalid reference_kind with secret sentinel fails closed without leaking sentinel
    sentinel_kind = "sk-bad-reference-kind"
    bad_exec_4 = ExecutionSuccess(
        operations_executed=1,
        operation_results=[
            OperationResult(
                patch_id="patch_0",
                operation="add_base_body",
                reference_id="body.base",
                reference_kind=cast(Any, sentinel_kind),
            )
        ],
        inspection_report=StandardInspectionReport(
            volume_mm3=100.0,
            mass_kg=0.1,
            feature_count=0,
            body_count=1,
        ),
    )
    with pytest.raises(ManifestValidationError) as excinfo4:
        assemble_run_manifest(context, bad_exec_4, artifacts)
    err4 = str(excinfo4.value)
    assert "Invalid reference_kind" in err4
    assert sentinel_kind not in err4


# ===========================================================================
# 7. Prompt Fingerprint Helper Tests
# ===========================================================================


def test_compute_prompt_fingerprint() -> None:
    from manifests import compute_prompt_fingerprint

    assert compute_prompt_fingerprint(None) is None

    prompt = "Create a cylinder with 50mm diameter"
    expected = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    assert compute_prompt_fingerprint(prompt) == expected

    with pytest.raises(ManifestValidationError, match="prompt must be a string or None"):
        compute_prompt_fingerprint(cast(Any, 12345))
