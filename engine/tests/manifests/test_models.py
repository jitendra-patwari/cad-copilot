"""Tests for run manifest domain models, invariants, and error sanitization."""

from __future__ import annotations

import json
import math
from typing import Any, cast

import pytest

from manifests import (
    ARTIFACT_FORMAT_TO_TYPE,
    CANONICAL_MANIFEST_VERSION,
    ManifestArtifactRecord,
    ManifestConfigurationError,
    ManifestExecution,
    ManifestInspection,
    ManifestOperationResult,
    ManifestStableIds,
    ManifestValidationError,
    PreparedManifestData,
    RunManifestContext,
    resolve_engine_version,
)
from manifests.run_manifest import _load_cached_schema


def test_engine_version_resolution() -> None:
    ver = resolve_engine_version("cad-copilot")
    assert ver == "0.1.0"


def test_engine_version_resolution_missing_fails_closed() -> None:
    with pytest.raises(ManifestConfigurationError) as excinfo:
        resolve_engine_version("non_existent_pkg_cad_xyz")
    assert "not installed" in str(excinfo.value)


def test_manifest_stable_ids_validation() -> None:
    # Valid
    sids = ManifestStableIds(
        part_id="part.main",
        body_ids=("body.base", "body.cutter"),
        boolean_operation_ids=("op.cut",),
        feature_ids=("feat.hole",),
    )
    assert sids.to_dict()["part_id"] == "part.main"

    # Duplicate body_ids rejected
    with pytest.raises(ManifestValidationError, match="duplicate"):
        ManifestStableIds(
            part_id="part.main",
            body_ids=("body.base", "body.base"),
            boolean_operation_ids=(),
            feature_ids=(),
        )

    # Duplicate boolean_operation_ids rejected
    with pytest.raises(ManifestValidationError, match="duplicate"):
        ManifestStableIds(
            part_id="part.main",
            body_ids=("body.base",),
            boolean_operation_ids=("op.1", "op.1"),
            feature_ids=(),
        )

    # Duplicate feature_ids rejected
    with pytest.raises(ManifestValidationError, match="duplicate"):
        ManifestStableIds(
            part_id="part.main",
            body_ids=("body.base",),
            boolean_operation_ids=(),
            feature_ids=("feat.1", "feat.1"),
        )

    # Invalid characters in part_id
    with pytest.raises(ManifestValidationError, match="safe identifier"):
        ManifestStableIds(
            part_id="invalid/part",
            body_ids=("body.base",),
            boolean_operation_ids=(),
            feature_ids=(),
        )


def test_manifest_stable_ids_rejects_strings_and_bytes() -> None:
    # Passing a string to body_ids (duck typing vulnerability) must be rejected
    with pytest.raises(ManifestValidationError, match="sequence of strings"):
        ManifestStableIds(
            part_id="part.main",
            body_ids=cast(Any, "body.base"),
            boolean_operation_ids=(),
            feature_ids=(),
        )

    with pytest.raises(ManifestValidationError, match="sequence of strings"):
        ManifestStableIds(
            part_id="part.main",
            body_ids=("body.base",),
            boolean_operation_ids=cast(Any, "op.1"),
            feature_ids=(),
        )

    with pytest.raises(ManifestValidationError, match="sequence of strings"):
        ManifestStableIds(
            part_id="part.main",
            body_ids=("body.base",),
            boolean_operation_ids=(),
            feature_ids=cast(Any, "feat.1"),
        )


def test_prepared_manifest_data_invariants() -> None:
    sids = ManifestStableIds(
        part_id="part.main",
        body_ids=("body.base",),
        boolean_operation_ids=(),
        feature_ids=(),
    )
    plan = {"request_id": "req-1", "part": {"part_id": "part.main"}}

    # Valid prompt_to_cad
    prep_prompt = PreparedManifestData(
        feature_plan=plan,
        stable_ids=sids,
        plan_sha256="a" * 64,
        prompt_sha256="b" * 64,
        defaults_applied=(),
        diagnostics=(),
        provenance_kind="ai_proposal",
        source_id="model-gemini",
        gate_mode="capability_first",
        request_kind="prompt_to_cad",
        engine_version="0.1.0",
    )
    assert prep_prompt.request_kind == "prompt_to_cad"

    # Valid example_plan
    prep_example = PreparedManifestData(
        feature_plan=plan,
        stable_ids=sids,
        plan_sha256="a" * 64,
        prompt_sha256=None,
        defaults_applied=(),
        diagnostics=(),
        provenance_kind="example_plan",
        source_id="fixture-bracket",
        gate_mode="strict",
        request_kind="example_plan",
        engine_version="0.1.0",
    )
    assert prep_example.prompt_sha256 is None

    # Example plan with prompt_sha256 must fail
    with pytest.raises(ManifestValidationError, match="must be None for example_plan"):
        PreparedManifestData(
            feature_plan=plan,
            stable_ids=sids,
            plan_sha256="a" * 64,
            prompt_sha256="c" * 64,
            defaults_applied=(),
            diagnostics=(),
            provenance_kind="example_plan",
            source_id="bracket",
            gate_mode="strict",
            request_kind="example_plan",
            engine_version="0.1.0",
        )

    # Prompt to cad without prompt_sha256 must fail
    with pytest.raises(ManifestValidationError, match="required"):
        PreparedManifestData(
            feature_plan=plan,
            stable_ids=sids,
            plan_sha256="a" * 64,
            prompt_sha256=None,
            defaults_applied=(),
            diagnostics=(),
            provenance_kind="ai_proposal",
            source_id="model",
            gate_mode="strict",
            request_kind="prompt_to_cad",
            engine_version="0.1.0",
        )

    # Invalid gate mode
    with pytest.raises(ManifestValidationError, match="gate_mode"):
        PreparedManifestData(
            feature_plan=plan,
            stable_ids=sids,
            plan_sha256="a" * 64,
            prompt_sha256=None,
            defaults_applied=(),
            diagnostics=(),
            provenance_kind="example_plan",
            source_id="bracket",
            gate_mode="invalid_mode",
            request_kind="example_plan",
            engine_version="0.1.0",
        )

    # Invalid engine_version
    with pytest.raises(ManifestValidationError, match="engine_version"):
        PreparedManifestData(
            feature_plan=plan,
            stable_ids=sids,
            plan_sha256="a" * 64,
            prompt_sha256=None,
            defaults_applied=(),
            diagnostics=(),
            provenance_kind="example_plan",
            source_id="bracket",
            gate_mode="strict",
            request_kind="example_plan",
            engine_version="not-semver",
        )


def test_prepared_manifest_data_rejects_strings_and_non_mappings() -> None:
    sids = ManifestStableIds(
        part_id="part.main",
        body_ids=("body.base",),
        boolean_operation_ids=(),
        feature_ids=(),
    )

    # String as defaults_applied
    with pytest.raises(ManifestValidationError, match="defaults_applied must be a sequence"):
        PreparedManifestData(
            feature_plan={"request_id": "req-1"},
            stable_ids=sids,
            plan_sha256="a" * 64,
            prompt_sha256=None,
            defaults_applied=cast(Any, "not-a-sequence"),
            diagnostics=(),
            provenance_kind="example_plan",
            source_id="bracket",
            gate_mode="strict",
            request_kind="example_plan",
            engine_version="0.1.0",
        )

    # Non-mapping element in defaults_applied
    with pytest.raises(ManifestValidationError, match="defaults_applied\\[0\\] must be a mapping"):
        PreparedManifestData(
            feature_plan={"request_id": "req-1"},
            stable_ids=sids,
            plan_sha256="a" * 64,
            prompt_sha256=None,
            defaults_applied=cast(Any, ("not-a-mapping",)),
            diagnostics=(),
            provenance_kind="example_plan",
            source_id="bracket",
            gate_mode="strict",
            request_kind="example_plan",
            engine_version="0.1.0",
        )


def test_prepared_manifest_data_defensive_copies_prevent_caller_mutation() -> None:
    sids = ManifestStableIds(
        part_id="part.main",
        body_ids=("body.base",),
        boolean_operation_ids=(),
        feature_ids=(),
    )
    plan: dict[str, Any] = {"request_id": "req-1", "part": {"part_id": "part.main"}}
    defaults = [{"path": "body.width", "value": 50.0}]

    prep = PreparedManifestData(
        feature_plan=plan,
        stable_ids=sids,
        plan_sha256="a" * 64,
        prompt_sha256=None,
        defaults_applied=cast(Any, defaults),
        diagnostics=(),
        provenance_kind="example_plan",
        source_id="bracket",
        gate_mode="strict",
        request_kind="example_plan",
        engine_version="0.1.0",
    )

    # Caller mutates external plan dict after constructing PreparedManifestData
    plan["mutated"] = True
    plan["part"]["part_id"] = "part.mutated"
    assert "mutated" not in prep.feature_plan
    assert prep.feature_plan["part"]["part_id"] == "part.main"

    # Caller mutates external defaults list
    defaults[0]["value"] = 999.0
    assert prep.defaults_applied[0]["value"] == 50.0


def test_run_manifest_context_invariants() -> None:
    sids = ManifestStableIds(
        part_id="part.main",
        body_ids=("body.base",),
        boolean_operation_ids=(),
        feature_ids=(),
    )
    plan = {"request_id": "req-1", "part": {"part_id": "part.main"}}
    prep = PreparedManifestData(
        feature_plan=plan,
        stable_ids=sids,
        plan_sha256="a" * 64,
        prompt_sha256=None,
        defaults_applied=(),
        diagnostics=(),
        provenance_kind="example_plan",
        source_id="fixture-bracket",
        gate_mode="strict",
        request_kind="example_plan",
        engine_version="0.1.0",
    )

    # Valid
    ctx = RunManifestContext(
        prepared_data=prep,
        request_id="req-1",
        contract_version="1.0",
        unit="mm",
        cad_runtime_version_build="224.0.0",
        warnings=("warn1",),
    )
    assert ctx.request_id == "req-1"

    # Mismatch between context request_id and plan request_id
    with pytest.raises(ManifestValidationError, match="mismatch"):
        RunManifestContext(
            prepared_data=prep,
            request_id="req-different",
        )

    # Invalid request_id characters
    with pytest.raises(ManifestValidationError, match="pattern"):
        RunManifestContext(
            prepared_data=prep,
            request_id="req bad id!",
        )

    # Invalid contract version
    with pytest.raises(ManifestValidationError, match="contract_version"):
        RunManifestContext(
            prepared_data=prep,
            request_id="req-1",
            contract_version="2.0",
        )

    # Reject string passed to warnings
    with pytest.raises(ManifestValidationError, match="warnings must be a sequence"):
        RunManifestContext(
            prepared_data=prep,
            request_id="req-1",
            warnings=cast(Any, "not-a-sequence"),
        )


def test_artifact_record_and_execution_models() -> None:
    rec = ManifestArtifactRecord(
        type="native_part",
        format="par",
        path="part.par",
        size_bytes=100,
        sha256="d" * 64,
    )
    assert rec.to_dict()["format"] == "par"

    # Reject non-positive size
    with pytest.raises(ManifestValidationError):
        ManifestArtifactRecord(
            type="native_part",
            format="par",
            path="part.par",
            size_bytes=0,
            sha256="d" * 64,
        )

    # Reject boolean size
    with pytest.raises(ManifestValidationError):
        ManifestArtifactRecord(
            type="native_part",
            format="par",
            path="part.par",
            size_bytes=True,
            sha256="d" * 64,
        )

    # Inspection
    insp = ManifestInspection(
        volume_mm3=123.45,
        mass_kg=0.5,
        feature_count=2,
        body_count=1,
        solid_body_count=1,
        sheet_body_count=0,
        wire_body_count=0,
    )
    assert insp.volume_mm3 == 123.45

    # Reject boolean counts
    with pytest.raises(ManifestValidationError):
        ManifestInspection(
            volume_mm3=100.0,
            mass_kg=0.1,
            feature_count=False,
            body_count=1,
            solid_body_count=1,
            sheet_body_count=0,
            wire_body_count=0,
        )

    # Execution wrapper
    op = ManifestOperationResult(
        patch_id="patch_1",
        operation="add_box",
        reference_id="body.base",
        reference_kind="body",
    )
    exec_sec = ManifestExecution(
        operations_executed=1,
        operation_results=(op,),
        inspection=insp,
    )
    assert exec_sec.operations_executed == 1
    assert len(exec_sec.to_dict()["operation_results"]) == 1

    # Reject string passed to operation_results
    with pytest.raises(ManifestValidationError, match="operation_results must be a sequence"):
        ManifestExecution(
            operations_executed=1,
            operation_results=cast(Any, "invalid"),
            inspection=insp,
        )


def test_single_line_text_rejects_newlines() -> None:
    # Newlines in patch_id
    with pytest.raises(ManifestValidationError, match="control characters"):
        ManifestOperationResult(
            patch_id="patch\n1",
            operation="add_box",
            reference_id="body.base",
            reference_kind="body",
        )

    # Carriage return in runtime build string
    sids = ManifestStableIds(
        part_id="part.main",
        body_ids=("body.base",),
        boolean_operation_ids=(),
        feature_ids=(),
    )
    prep = PreparedManifestData(
        feature_plan={"request_id": "req-1", "part": {"part_id": "part.main"}},
        stable_ids=sids,
        plan_sha256="a" * 64,
        prompt_sha256=None,
        defaults_applied=(),
        diagnostics=(),
        provenance_kind="example_plan",
        source_id="bracket",
        gate_mode="strict",
        request_kind="example_plan",
        engine_version="0.1.0",
    )
    with pytest.raises(ManifestValidationError, match="control characters"):
        RunManifestContext(
            prepared_data=prep,
            request_id="req-1",
            cad_runtime_version_build="Build 123\r\nInjected",
        )


def test_artifact_record_format_type_and_extension_binding() -> None:
    rec_par = ManifestArtifactRecord(type="native_part", format="par", path="part.par", size_bytes=100, sha256="a" * 64)
    assert rec_par.format == "par"

    rec_step = ManifestArtifactRecord(
        type="geometry_step", format="step", path="part.step", size_bytes=200, sha256="b" * 64
    )
    assert rec_step.format == "step"

    rec_stl = ManifestArtifactRecord(type="mesh_stl", format="stl", path="part.stl", size_bytes=300, sha256="c" * 64)
    assert rec_stl.format == "stl"

    rec_jpg = ManifestArtifactRecord(
        type="preview_image", format="jpg", path="preview.jpg", size_bytes=400, sha256="d" * 64
    )
    assert rec_jpg.format == "jpg"

    # Mismatched type and format
    with pytest.raises(ManifestValidationError, match="requires type 'native_part'"):
        ManifestArtifactRecord(type="geometry_step", format="par", path="part.par", size_bytes=100, sha256="a" * 64)

    # Mismatched extension and format
    with pytest.raises(ManifestValidationError, match=r"must end with '\.par'"):
        ManifestArtifactRecord(type="native_part", format="par", path="part.step", size_bytes=100, sha256="a" * 64)

    # Unknown format
    with pytest.raises(ManifestValidationError, match="Invalid artifact format"):
        ManifestArtifactRecord(type="native_part", format="dxf", path="part.dxf", size_bytes=100, sha256="a" * 64)


def test_negative_zero_normalized_in_inspection() -> None:
    insp = ManifestInspection(
        volume_mm3=100.0,
        mass_kg=-0.0,
        feature_count=0,
        body_count=1,
        solid_body_count=1,
        sheet_body_count=0,
        wire_body_count=0,
    )
    assert insp.mass_kg == 0.0
    assert math.copysign(1.0, insp.mass_kg) == 1.0

    as_dict = insp.to_dict()
    assert as_dict["mass_kg"] == 0.0
    assert math.copysign(1.0, as_dict["mass_kg"]) == 1.0

    serialized = json.dumps(as_dict)
    assert "-0.0" not in serialized
    assert '"mass_kg": 0.0' in serialized


def test_request_id_rejects_dots_and_traversal() -> None:
    sids = ManifestStableIds(
        part_id="part.main",
        body_ids=("body.base",),
        boolean_operation_ids=(),
        feature_ids=(),
    )
    prep = PreparedManifestData(
        feature_plan={"request_id": "req-1", "part": {"part_id": "part.main"}},
        stable_ids=sids,
        plan_sha256="a" * 64,
        prompt_sha256=None,
        defaults_applied=(),
        diagnostics=(),
        provenance_kind="example_plan",
        source_id="bracket",
        gate_mode="strict",
        request_kind="example_plan",
        engine_version="0.1.0",
    )

    # "." token
    with pytest.raises(ManifestValidationError, match="pattern"):
        RunManifestContext(prepared_data=prep, request_id=".")

    # ".." token
    with pytest.raises(ManifestValidationError, match="pattern"):
        RunManifestContext(prepared_data=prep, request_id="..")

    # Trailing dot
    with pytest.raises(ManifestValidationError, match="pattern"):
        RunManifestContext(prepared_data=prep, request_id="req-1.")


def test_canonical_constants() -> None:
    assert CANONICAL_MANIFEST_VERSION == "cad_copilot.run_manifest.v1"
    assert ARTIFACT_FORMAT_TO_TYPE == {
        "par": "native_part",
        "step": "geometry_step",
        "stl": "mesh_stl",
        "jpg": "preview_image",
    }


def test_configuration_error_message_does_not_leak_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    # Clear lru cache on internal schema loader
    _load_cached_schema.cache_clear()

    import importlib.resources

    def mock_files(pkg: str) -> Any:
        class MockPath:
            def joinpath(self, *parts: str) -> MockPath:
                return self

            def read_bytes(self) -> bytes:
                raise FileNotFoundError("E:\\secret\\workstation\\local\\path\\schema.json not found")

        return MockPath()

    monkeypatch.setattr(importlib.resources, "files", mock_files)

    with pytest.raises(ManifestConfigurationError) as excinfo:
        _load_cached_schema()

    # Exception string must NOT contain the local workstation path!
    err_str = str(excinfo.value)
    assert "E:\\" not in err_str
    assert "workstation" not in err_str
    assert err_str == "Failed to load run manifest schema resource 'run-manifest-v1.schema.json'"

    # But __cause__ preserves the underlying error for debugging
    assert isinstance(excinfo.value.__cause__, FileNotFoundError)

    # Clear cache again so normal execution restores
    _load_cached_schema.cache_clear()
