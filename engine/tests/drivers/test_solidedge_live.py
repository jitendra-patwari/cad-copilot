"""Controlled live integration tests against a real Siemens Solid Edge installation.

These tests execute ONLY when running with `pytest -m com` on a Windows host with
a licensed Siemens Solid Edge installation.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import sys
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

from application import (
    ExampleGenerationRequest,
    GenerationService,
    PlanProposal,
    PromptGenerationRequest,
)
from artifacts.pipeline import finalize_request_artifacts
from artifacts.validation import (
    compute_file_sha256_and_size,
    validate_jpg_artifact,
    validate_par_artifact,
    validate_step_artifact,
    validate_stl_artifact,
)
from drivers.solidedge import (
    OwnershipMode,
    SolidEdgePartDocumentHandle,
    SolidEdgeRuntime,
    is_process_alive,
)
from drivers.solidedge.builders import (
    create_cutout_finite,
    create_cutout_through_all,
    create_pad_protrusion,
    create_primitive_cuboid,
    create_primitive_cylinder,
    create_profile_on_plane,
    draw_circle_profile,
    draw_polygon_profile,
    draw_slot_profile,
    resolve_or_create_reference_plane,
)
from drivers.solidedge.errors import describe_exception
from drivers.solidedge.executor import SolidEdgeExecutor
from drivers.solidedge.units import m3_to_mm3
from example_catalog import resolve_example_plan
from geometry.plan_models import (
    BodyPlacement,
    CircularThroughHoleFeature,
    CylinderBaseBody,
    FeaturePlan,
    PartMetadata,
    ProfileCutoutFeature,
    ProfilePoint2D,
    RectangularBaseBody,
    RectangularExtrudedPadFeature,
    RectangularThroughCutoutFeature,
    SlotThroughCutoutFeature,
    SpurGearBaseBody,
)
from interfaces.exceptions import (
    CADDocumentError,
    CADError,
    CADExecutionError,
    CADExportError,
)
from interfaces.models import (
    ArtifactFormat,
    ArtifactRecord,
    ExecutionFailure,
    ExecutionSuccess,
    StandardInspectionReport,
)
from manifests import (
    FREE_TEXT_CREDENTIAL_PATTERN,
    LOCAL_PATH_PATTERN,
    compute_plan_fingerprint,
    get_run_manifest_validator,
)

pytestmark = [pytest.mark.com]


@pytest.fixture
def live_runtime() -> Generator[SolidEdgeRuntime]:
    """Provide a managed SolidEdgeRuntime instance with guaranteed post-test cleanup."""
    if sys.platform != "win32":
        pytest.skip("Live Solid Edge COM tests require a Windows platform")

    try:
        import pythoncom  # noqa: F401
        import win32com.client  # noqa: F401
    except ImportError:
        pytest.skip("pywin32 is not installed in the current Python environment")

    runtime = SolidEdgeRuntime()
    try:
        yield runtime
    finally:
        runtime.teardown(force_kill_on_failure=True)


# ---------------------------------------------------------------------------
# M3.1 Lifecycle Live Gates
# ---------------------------------------------------------------------------


def test_live_gate_1_spawn_and_connect(live_runtime: SolidEdgeRuntime) -> None:
    """Live Gate 1: Spawn a real Solid Edge instance, verify owned identity, and check diagnostics."""
    print("\n[LIVE GATE 1] Connecting to Solid Edge...", flush=True)
    handle = live_runtime.connect_application()

    assert handle is not None
    assert handle.process_identity is not None
    assert handle.process_identity.pid > 0
    assert handle.process_identity.creation_time_ft > 0

    diag = live_runtime.get_diagnostics()
    print(
        f"[LIVE GATE 1] Connected! PID: {handle.process_identity.pid} | Version: {diag.version_build} | Ownership: {handle.ownership.value}",
        flush=True,
    )
    assert diag.is_healthy is True
    assert diag.process_id == handle.process_identity.pid
    assert diag.version_build is not None
    assert len(diag.version_build) > 0


def test_live_gate_2_part_document_lifecycle(live_runtime: SolidEdgeRuntime) -> None:
    """Live Gate 2: Create a real part document in Ordered mode, verify handle, and close cleanly."""
    print("\n[LIVE GATE 2] Connecting to Solid Edge...", flush=True)
    app_handle = live_runtime.connect_application()

    print("[LIVE GATE 2] Creating a new 3D Part Document in Solid Edge UI...", flush=True)
    doc_handle = live_runtime.create_part_document(app_handle)
    assert isinstance(doc_handle, SolidEdgePartDocumentHandle)
    assert isinstance(doc_handle.handle_id, str)
    assert len(doc_handle.handle_id) > 0
    assert doc_handle.handle_id in live_runtime._open_document_handles

    # Assert real returned value is 2 (Ordered mode)
    assert live_runtime._worker is not None
    raw_doc = live_runtime._worker._document_registry[doc_handle.handle_id]
    mode_val = live_runtime._worker.call(lambda: getattr(raw_doc, "ModelingMode", None))
    print(f"[LIVE GATE 2] Verified live ModelingMode via COM: {mode_val} (Ordered = 2)", flush=True)
    assert mode_val == 2

    print(
        f"[LIVE GATE 2] -> Ordered Part Document OPEN in Solid Edge! (Handle: {doc_handle.handle_id})",
        flush=True,
    )
    time.sleep(1.0)

    print("[LIVE GATE 2] Closing the Part document cleanly without save prompt...", flush=True)
    live_runtime.close_document(doc_handle)
    assert doc_handle.handle_id not in live_runtime._open_document_handles
    print("[LIVE GATE 2] Document closed successfully.", flush=True)


def test_live_gate_3_diagnostics_query(live_runtime: SolidEdgeRuntime) -> None:
    """Live Gate 3: Verify live diagnostics query accuracy against real COM session."""
    print("\n[LIVE GATE 3] Querying live diagnostics from Solid Edge session...", flush=True)
    handle = live_runtime.connect_application()
    diag = live_runtime.get_diagnostics()

    print(f"[LIVE GATE 3] Diagnostic status: {diag}", flush=True)
    assert diag.ownership in ("owned", "borrowed")
    assert diag.attachment_mode in ("spawned_new", "attached_existing")
    assert diag.process_id == handle.process_identity.pid if handle.process_identity else True
    assert diag.is_healthy is True


def test_live_gate_4_teardown_isolation_and_cleanup(live_runtime: SolidEdgeRuntime) -> None:
    """Live Gate 4: Verify graceful teardown terminates owned instance without leaving orphan processes."""
    print("\n[LIVE GATE 4] Testing teardown lifecycle and process isolation...", flush=True)
    handle = live_runtime.connect_application()
    assert handle.process_identity is not None
    pid = handle.process_identity.pid

    if handle.ownership == OwnershipMode.OWNED:
        print("[LIVE GATE 4] Creating in-flight document on owned session...", flush=True)
        _ = live_runtime.create_part_document(handle)
        print("[LIVE GATE 4] In-flight document active. Pausing 1 second...", flush=True)
        time.sleep(1.0)

        print("[LIVE GATE 4] Executing teardown (closing document, quitting application)...", flush=True)
        live_runtime.teardown(force_kill_on_failure=True)

        time.sleep(1.0)
        assert not is_process_alive(pid)
        print(f"[LIVE GATE 4] Owned Solid Edge process (PID {pid}) cleanly terminated with zero orphans.", flush=True)
    else:
        print(
            f"[LIVE GATE 4] Borrowed Solid Edge session (PID {pid}). Executing teardown without quitting...", flush=True
        )
        live_runtime.teardown(force_kill_on_failure=True)
        assert is_process_alive(pid)
        print(f"[LIVE GATE 4] Borrowed Solid Edge process (PID {pid}) preserved and still running safely.", flush=True)


# ---------------------------------------------------------------------------
# M3.2 Live Integration & Characterization Gates
# ---------------------------------------------------------------------------


def _recompute_and_inspect_live_model(raw_doc: Any, worker: Any) -> tuple[bool, float]:
    """Recompute the live Part document and inspect topology, returning (is_solid, volume_mm3).

    Uses documented PartDocument.Recompute, verifies single Model, checks Body.IsSolid,
    and converts Body.Volume from cubic meters to cubic millimeters.
    """
    # 1. Recompute the active Part document model state
    worker._invoke_com(lambda: raw_doc.Recompute())

    # 2. Check Models collection
    models = worker._invoke_com(lambda: getattr(raw_doc, "Models", None))
    assert models is not None, "Document Models collection is null"
    count = worker._invoke_com(lambda: getattr(models, "Count", 0))
    assert count == 1, f"Expected exactly 1 Model, found {count}"

    model = worker._invoke_com(lambda: models.Item(1))
    body = worker._invoke_com(lambda: getattr(model, "Body", None))
    assert body is not None, "Model Body is null"

    # 3. Verify solid topology
    is_solid = worker._invoke_com(lambda: getattr(body, "IsSolid", False))
    assert is_solid is True, "Body is not solid (IsSolid is False)"

    # 4. Read physical volume (m^3 converted to mm^3)
    vol_m3 = worker._invoke_com(lambda: getattr(body, "Volume", 0.0))
    assert isinstance(vol_m3, (int, float)) and math.isfinite(vol_m3) and vol_m3 > 0.0
    vol_mm3 = m3_to_mm3(vol_m3)
    return is_solid, vol_mm3


def test_live_m32_01_primitive_cuboid_and_cylinder(live_runtime: SolidEdgeRuntime) -> None:
    """M32-LIVE-01: Verify cuboid and cylinder primitives create, recompute, and inspect with analytic volume."""
    app_handle = live_runtime.connect_application()
    diag = live_runtime.get_diagnostics()
    print(f"\n[M32-LIVE-01] Solid Edge Version: {diag.version_build}", flush=True)

    # 1. Test Cuboid (100 x 80 x 20 mm -> expected volume 160,000 mm3)
    doc_handle = live_runtime.create_part_document(app_handle)
    try:

        def _task_cuboid(raw_doc: Any, worker: Any) -> dict[str, Any]:
            model = create_primitive_cuboid(raw_doc, worker, length_mm=100.0, width_mm=80.0, height_mm=20.0)
            assert model is not None
            is_solid, vol_mm3 = _recompute_and_inspect_live_model(raw_doc, worker)
            return {"is_solid": is_solid, "vol_mm3": vol_mm3}

        result = live_runtime.run_document_task(doc_handle, _task_cuboid)
        assert result["is_solid"] is True
        expected_vol = 100.0 * 80.0 * 20.0  # 160,000 mm3
        assert math.isclose(result["vol_mm3"], expected_vol, rel_tol=0.01)
        print(
            f"[M32-LIVE-01] Cuboid verified: volume = {result['vol_mm3']:.2f} mm3 (expected {expected_vol})",
            flush=True,
        )
    finally:
        live_runtime.close_document(doc_handle)

    # 2. Test Cylinder (r = 25 mm, h = 40 mm -> expected volume pi * 25^2 * 40 ~= 78,539.8 mm3)
    doc_handle = live_runtime.create_part_document(app_handle)
    try:

        def _task_cylinder(raw_doc: Any, worker: Any) -> dict[str, Any]:
            model = create_primitive_cylinder(raw_doc, worker, radius_mm=25.0, height_mm=40.0)
            assert model is not None
            is_solid, vol_mm3 = _recompute_and_inspect_live_model(raw_doc, worker)
            return {"is_solid": is_solid, "vol_mm3": vol_mm3}

        result = live_runtime.run_document_task(doc_handle, _task_cylinder)
        assert result["is_solid"] is True
        expected_vol = math.pi * (25.0**2) * 40.0
        assert math.isclose(result["vol_mm3"], expected_vol, rel_tol=0.01)
        print(
            f"[M32-LIVE-01] Cylinder verified: volume = {result['vol_mm3']:.2f} mm3 (expected {expected_vol:.2f})",
            flush=True,
        )
    finally:
        live_runtime.close_document(doc_handle)


@pytest.mark.parametrize(
    ("profile_family", "extent_type", "depth_mm", "expected_delta"),
    [
        # Circle through-all: r=10 mm, depth=50 mm -> pi * 10^2 * 50 ~= 15,707.96 mm3
        ("circle", "through_all", 50.0, math.pi * (10.0**2) * 50.0),
        # Circle finite: r=10 mm, depth=15 mm -> pi * 10^2 * 15 ~= 4,712.39 mm3
        ("circle", "finite", 15.0, math.pi * (10.0**2) * 15.0),
        # Polygon through-all: 20x20 mm, depth=50 mm -> 20 * 20 * 50 = 20,000 mm3
        ("polygon", "through_all", 50.0, 20.0 * 20.0 * 50.0),
        # Polygon finite: 20x20 mm, depth=15 mm -> 20 * 20 * 15 = 6,000 mm3
        ("polygon", "finite", 15.0, 20.0 * 20.0 * 15.0),
        # Slot through-all: length=30, width=10, depth=50 mm -> area=(20*10 + pi*5^2)=278.54 mm2 -> 13,926.99 mm3
        ("slot", "through_all", 50.0, ((20.0 * 10.0) + (math.pi * (5.0**2))) * 50.0),
        # Slot finite: length=30, width=10, depth=15 mm -> 278.54 * 15 = 4,178.10 mm3
        ("slot", "finite", 15.0, ((20.0 * 10.0) + (math.pi * (5.0**2))) * 15.0),
    ],
)
def test_live_m32_02_z_cuts_volume_loss(
    live_runtime: SolidEdgeRuntime,
    profile_family: str,
    extent_type: str,
    depth_mm: float,
    expected_delta: float,
) -> None:
    """M32-LIVE-02: Verify all 6 +Z capability rows individually in disposable documents for volume loss and single solid."""
    app_handle = live_runtime.connect_application()
    diag = live_runtime.get_diagnostics()
    print(f"\n[M32-LIVE-02] Row ({profile_family}, {extent_type}) on Solid Edge {diag.version_build}", flush=True)

    # Base plate: 100 x 100 x 50 mm (Z spans from 0 to 50 mm)
    doc_handle = live_runtime.create_part_document(app_handle)
    try:

        def _task(raw_doc: Any, worker: Any) -> dict[str, Any]:
            # 1. Base solid
            model = create_primitive_cuboid(raw_doc, worker, length_mm=100.0, width_mm=100.0, height_mm=50.0)
            _, v0 = _recompute_and_inspect_live_model(raw_doc, worker)

            # 2. Place sketch profile on top face (+Z is at z = 50.0 mm)
            top_plane = resolve_or_create_reference_plane(raw_doc, worker, "XY", offset_mm=50.0)
            prof = create_profile_on_plane(raw_doc, worker, top_plane)

            if profile_family == "circle":
                draw_circle_profile(prof, worker, center_x_mm=0.0, center_y_mm=0.0, radius_mm=10.0)
            elif profile_family == "polygon":
                draw_polygon_profile(
                    prof,
                    worker,
                    [
                        {"x_mm": -10.0, "y_mm": -10.0},
                        {"x_mm": 10.0, "y_mm": -10.0},
                        {"x_mm": 10.0, "y_mm": 10.0},
                        {"x_mm": -10.0, "y_mm": 10.0},
                    ],
                )
            elif profile_family == "slot":
                draw_slot_profile(
                    prof,
                    worker,
                    center_x_mm=0.0,
                    center_y_mm=0.0,
                    length_mm=30.0,
                    width_mm=10.0,
                    orientation_deg=0.0,
                )
            else:
                raise ValueError(f"Unknown profile_family: {profile_family}")

            # 3. Execute cut operation
            if extent_type == "through_all":
                create_cutout_through_all(model, worker, prof, face="+Z", profile_family=profile_family)
            elif extent_type == "finite":
                create_cutout_finite(model, worker, prof, depth_mm=depth_mm, face="+Z", profile_family=profile_family)
            else:
                raise ValueError(f"Unknown extent_type: {extent_type}")

            is_solid, v1 = _recompute_and_inspect_live_model(raw_doc, worker)
            delta = v0 - v1
            return {"is_solid": is_solid, "delta": delta, "final_vol": v1}

        res = live_runtime.run_document_task(doc_handle, _task)
        assert res["is_solid"] is True
        assert math.isclose(res["delta"], expected_delta, rel_tol=0.01)
        print(
            f"[M32-LIVE-02] +Z {profile_family} ({extent_type}): loss = {res['delta']:.1f} mm3 "
            f"(expected {expected_delta:.1f} mm3) | 1 solid verified",
            flush=True,
        )
    finally:
        live_runtime.close_document(doc_handle)


def test_live_m32_03_six_face_circular_through_cut_characterization(live_runtime: SolidEdgeRuntime) -> None:
    """M32-LIVE-03: Characterize circular through-all cuts across all 6 principal faces with correct offsets and centers."""
    app_handle = live_runtime.connect_application()
    diag = live_runtime.get_diagnostics()
    print(f"\n[M32-LIVE-03] Solid Edge Version: {diag.version_build}", flush=True)

    # Base cube: 60 x 60 x 60 mm (X in [-30, 30], Y in [-30, 30], Z in [0, 60])
    # Centered circle r = 10 mm through thickness 60 mm -> expected volume loss = pi * 10^2 * 60 ~= 18,849.56 mm3
    expected_delta = math.pi * (10.0**2) * 60.0

    # Explicit face plane mapping matching repository coordinate conventions:
    # +Z: XY plane at z=60.0, center at (x=0, y=0)
    # -Z: XY plane at z=0.0, center at (x=0, y=0)
    # +X: YZ plane at x=+30.0, center at (y=0, z=30.0) [mid-height]
    # -X: YZ plane at x=-30.0, center at (y=0, z=30.0) [mid-height]
    # +Y: XZ plane at y=+30.0, center at (x=0, z=30.0) [mid-height]
    # -Y: XZ plane at y=-30.0, center at (x=0, z=30.0) [mid-height]
    face_config = {
        "+Z": ("XY", 60.0, 0.0, 0.0),
        "-Z": ("XY", 0.0, 0.0, 0.0),
        "+X": ("YZ", 30.0, 0.0, 30.0),
        "-X": ("YZ", -30.0, 0.0, 30.0),
        "+Y": ("XZ", 30.0, 0.0, 30.0),
        "-Y": ("XZ", -30.0, 0.0, 30.0),
    }

    characterization_log: list[dict[str, Any]] = []

    for face, (plane_family, offset, cx, cy) in face_config.items():
        doc_handle = live_runtime.create_part_document(app_handle)
        try:

            def _task_face(
                raw_doc: Any,
                worker: Any,
                f: str = face,
                p_fam: str = plane_family,
                p_off: float = offset,
                center_x: float = cx,
                center_y: float = cy,
            ) -> dict[str, Any]:
                model = create_primitive_cuboid(raw_doc, worker, length_mm=60.0, width_mm=60.0, height_mm=60.0)
                _, v0 = _recompute_and_inspect_live_model(raw_doc, worker)

                ref_plane = resolve_or_create_reference_plane(raw_doc, worker, p_fam, p_off)
                profile = create_profile_on_plane(raw_doc, worker, ref_plane)
                draw_circle_profile(profile, worker, center_x_mm=center_x, center_y_mm=center_y, radius_mm=10.0)

                create_cutout_through_all(model, worker, profile, face=f, profile_family="circle")

                is_solid, v1 = _recompute_and_inspect_live_model(raw_doc, worker)
                delta = v0 - v1
                return {
                    "face": f,
                    "delta": delta,
                    "is_solid": is_solid,
                }

            res = live_runtime.run_document_task(doc_handle, _task_face)
            assert res["is_solid"] is True
            assert math.isclose(res["delta"], expected_delta, rel_tol=0.01)
            characterization_log.append(res)
            print(
                f"[M32-LIVE-03] Face {res['face']}: volume loss = {res['delta']:.1f} mm3 "
                f"(expected {expected_delta:.1f} mm3) | 1 solid verified",
                flush=True,
            )
        finally:
            live_runtime.close_document(doc_handle)

    assert len(characterization_log) == 6
    print("[M32-LIVE-03] All 6 principal faces characterized with exact volume loss in isolated documents.", flush=True)


def test_live_m32_04_z_pad_protrusion_volume_increase(live_runtime: SolidEdgeRuntime) -> None:
    """M32-LIVE-04: Verify +Z rectangular pad at z=20 mm adds expected volume and merges into exactly one solid."""
    app_handle = live_runtime.connect_application()
    diag = live_runtime.get_diagnostics()
    print(f"\n[M32-LIVE-04] Solid Edge Version: {diag.version_build}", flush=True)

    # Base: 80 x 80 x 20 mm plate (Z in [0, 20], volume = 128,000 mm3)
    # Pad on +Z (at z = 20.0 mm): 40 x 40 x 15 mm (expected volume increase = 40 * 40 * 15 = 24,000 mm3 -> net 152,000 mm3)
    doc_handle = live_runtime.create_part_document(app_handle)
    try:

        def _task_pad(raw_doc: Any, worker: Any) -> dict[str, Any]:
            model = create_primitive_cuboid(raw_doc, worker, length_mm=80.0, width_mm=80.0, height_mm=20.0)
            _, v0 = _recompute_and_inspect_live_model(raw_doc, worker)

            # Top face of 20 mm plate is at z = 20.0 mm
            top_plane = resolve_or_create_reference_plane(raw_doc, worker, "XY", offset_mm=20.0)
            profile = create_profile_on_plane(raw_doc, worker, top_plane)
            draw_polygon_profile(
                profile,
                worker,
                [
                    {"x_mm": -20.0, "y_mm": -20.0},
                    {"x_mm": 20.0, "y_mm": -20.0},
                    {"x_mm": 20.0, "y_mm": 20.0},
                    {"x_mm": -20.0, "y_mm": 20.0},
                ],
            )

            create_pad_protrusion(model, worker, profile, height_mm=15.0, face="+Z", profile_family="polygon")

            is_solid, v1 = _recompute_and_inspect_live_model(raw_doc, worker)
            delta = v1 - v0
            expected_increase = 40.0 * 40.0 * 15.0  # 24,000 mm3
            return {"is_solid": is_solid, "delta": delta, "expected_increase": expected_increase, "final_vol": v1}

        res = live_runtime.run_document_task(doc_handle, _task_pad)
        assert res["is_solid"] is True
        assert math.isclose(res["delta"], res["expected_increase"], rel_tol=0.01)
        print(
            f"[M32-LIVE-04] Pad verified: volume increase = {res['delta']:.1f} mm3 (expected {res['expected_increase']:.1f}) | 1 solid",
            flush=True,
        )
    finally:
        live_runtime.close_document(doc_handle)


def test_live_m32_05_sequential_feature_execution_and_inspection(live_runtime: SolidEdgeRuntime) -> None:
    """M32-LIVE-05: Sequential feature execution (hole, cut, slot, pad) on a block via SolidEdgeExecutor."""
    app_handle = live_runtime.connect_application()
    diag = live_runtime.get_diagnostics()
    print(f"\n[M32-LIVE-05] Solid Edge Version: {diag.version_build}", flush=True)

    # Base: 140 x 100 x 40 mm (volume = 560,000 mm3)
    # Features (all on +Z face, non-overlapping):
    # 1. Circular through hole: d = 16 mm (r=8 mm), depth = 40 mm -> vol loss = pi * 8^2 * 40 ~= 8,042.48 mm3 at u=-40, v=0
    # 2. Rectangular through cutout: 20 x 20 mm, depth = 40 mm -> vol loss = 20 * 20 * 40 = 16,000 mm3 at u=0, v=25
    # 3. Slot through cutout: length = 30 mm, width = 10 mm, depth = 40 mm -> vol loss = (20*10 + pi*5^2) * 40 ~= 11,141.59 mm3 at u=0, v=-25
    # 4. Rectangular pad: 30 x 30 mm, height = 15 mm -> vol increase = 30 * 30 * 15 = 13,500 mm3 at u=40, v=0
    # Expected net volume = 560,000 - 8,042.48 - 16,000 - 11,141.59 + 13,500 = 538,315.93 mm3
    base_vol = 140.0 * 100.0 * 40.0
    loss_hole = math.pi * (8.0**2) * 40.0
    loss_rect = 20.0 * 20.0 * 40.0
    loss_slot = ((20.0 * 10.0) + (math.pi * (5.0**2))) * 40.0
    gain_pad = 30.0 * 30.0 * 15.0
    expected_net_vol = base_vol - loss_hole - loss_rect - loss_slot + gain_pad

    plan = FeaturePlan(
        plan_version="cad_copilot.single_part_feature_plan.v1",
        request_id="req-live-m32-05",
        part=PartMetadata(part_id="part.live.seq", design_intent="sequential live test"),
        base_body=RectangularBaseBody(
            id="body.main",
            length_mm=140.0,
            width_mm=100.0,
            thickness_mm=40.0,
        ),
        primitive_bodies=(
            RectangularBaseBody(
                id="body.main",
                length_mm=140.0,
                width_mm=100.0,
                thickness_mm=40.0,
            ),
        ),
        features=(
            CircularThroughHoleFeature(
                id="feat.hole",
                diameter_mm=16.0,
                center_x_mm=-40.0,
                center_y_mm=0.0,
                target_face="+Z",
            ),
            RectangularThroughCutoutFeature(
                id="feat.rect_cut",
                width_mm=20.0,
                height_mm=20.0,
                center_x_mm=0.0,
                center_y_mm=25.0,
                target_face="+Z",
            ),
            SlotThroughCutoutFeature(
                id="feat.slot_cut",
                length_mm=30.0,
                width_mm=10.0,
                center_x_mm=0.0,
                center_y_mm=-25.0,
                target_face="+Z",
            ),
            RectangularExtrudedPadFeature(
                id="feat.pad",
                width_mm=30.0,
                height_mm=30.0,
                distance_mm=15.0,
                center_x_mm=40.0,
                center_y_mm=0.0,
                target_face="+Z",
            ),
        ),
    )

    doc_handle = live_runtime.create_part_document(app_handle)
    try:
        executor = SolidEdgeExecutor(live_runtime, doc_handle)
        result = executor.execute_feature_plan(plan)

        assert isinstance(result, ExecutionSuccess), f"Execution failed: {result}"
        assert result.inspection_report is not None
        assert result.inspection_report.solid_body_count == 1
        assert result.inspection_report.sheet_body_count == 0
        assert result.inspection_report.wire_body_count == 0
        assert math.isclose(result.inspection_report.volume_mm3, expected_net_vol, rel_tol=0.01)
        assert len(result.operation_results) == 5  # 1 base body + 4 features

        op_refs = [op.reference_id for op in result.operation_results]
        assert op_refs == ["body.main", "feat.hole", "feat.rect_cut", "feat.slot_cut", "feat.pad"]
        print(
            f"[M32-LIVE-05] Sequential plan executed: net volume = {result.inspection_report.volume_mm3:.1f} mm3 "
            f"(expected {expected_net_vol:.1f} mm3) | 1 solid body | 5 ordered ops verified",
            flush=True,
        )
    finally:
        live_runtime.close_document(doc_handle)


def test_live_m32_06_conceptual_spur_gear_and_translated_bore(live_runtime: SolidEdgeRuntime) -> None:
    """M32-LIVE-06: 24-tooth conceptual gear at origin and translated conceptual gear with centered bore."""
    app_handle = live_runtime.connect_application()
    diag = live_runtime.get_diagnostics()
    print(f"\n[M32-LIVE-06] Solid Edge Version: {diag.version_build}", flush=True)

    # Independent analytic oracle:
    # 24-tooth conceptual gear polygon area ~= 1827.2725 mm2
    # Unbored volume (15 mm face width) ~= 27,409.09 mm3
    # Bore loss (d=12 mm -> r=6 mm, depth=15 mm) = pi * 6^2 * 15 = 540*pi ~= 1,696.46 mm3
    # Expected bored volume ~= 25,712.63 mm3
    expected_bored_vol = 25712.63
    unbored_vol = 27409.09

    # 1. Gear at Origin with centered through-bore
    gear_body_origin = SpurGearBaseBody(
        id="body.gear.origin",
        tooth_count=24,
        module_mm=2.0,
        face_width_mm=15.0,
        pressure_angle_deg=20.0,
        bore_diameter_mm=12.0,
        placement=BodyPlacement(x_mm=0.0, y_mm=0.0, z_mm=0.0),
    )
    plan_origin = FeaturePlan(
        plan_version="cad_copilot.single_part_feature_plan.v1",
        request_id="req-live-m32-06-origin",
        part=PartMetadata(part_id="part.gear.origin", design_intent="gear at origin"),
        base_body=gear_body_origin,
        primitive_bodies=(gear_body_origin,),
        features=(),
    )

    doc_handle_origin = live_runtime.create_part_document(app_handle)
    try:
        executor = SolidEdgeExecutor(live_runtime, doc_handle_origin)
        result = executor.execute_feature_plan(plan_origin)
        assert isinstance(result, ExecutionSuccess), f"Origin gear failed: {result}"
        assert result.inspection_report is not None
        assert result.inspection_report.solid_body_count == 1
        assert result.inspection_report.sheet_body_count == 0
        assert result.inspection_report.wire_body_count == 0
        assert math.isclose(result.inspection_report.volume_mm3, expected_bored_vol, rel_tol=0.01)
        assert result.inspection_report.volume_mm3 < unbored_vol - 1000.0  # Proves bore was removed
        assert len(result.operation_results) == 2  # base gear body + bore cutout
        assert [op.reference_id for op in result.operation_results] == ["body.gear.origin", "feature.gear-bore.1"]
        print(
            f"[M32-LIVE-06] Gear at origin verified: volume = {result.inspection_report.volume_mm3:.1f} mm3 "
            f"(expected {expected_bored_vol:.1f} mm3 vs unbored {unbored_vol:.1f} mm3) | 1 solid | bore verified",
            flush=True,
        )
    finally:
        live_runtime.close_document(doc_handle_origin)

    # 2. Gear translated to (50.0, 50.0, 10.0) with centered through-bore
    gear_body_trans = SpurGearBaseBody(
        id="body.gear.trans",
        tooth_count=24,
        module_mm=2.0,
        face_width_mm=15.0,
        pressure_angle_deg=20.0,
        bore_diameter_mm=12.0,
        placement=BodyPlacement(x_mm=50.0, y_mm=50.0, z_mm=10.0),
    )
    plan_trans = FeaturePlan(
        plan_version="cad_copilot.single_part_feature_plan.v1",
        request_id="req-live-m32-06-trans",
        part=PartMetadata(part_id="part.gear.trans", design_intent="translated gear"),
        base_body=gear_body_trans,
        primitive_bodies=(gear_body_trans,),
        features=(),
    )

    doc_handle_trans = live_runtime.create_part_document(app_handle)
    try:
        executor = SolidEdgeExecutor(live_runtime, doc_handle_trans)
        result_trans = executor.execute_feature_plan(plan_trans)
        assert isinstance(result_trans, ExecutionSuccess), f"Translated gear failed: {result_trans}"
        assert result_trans.inspection_report is not None
        assert result_trans.inspection_report.solid_body_count == 1
        assert result_trans.inspection_report.sheet_body_count == 0
        assert result_trans.inspection_report.wire_body_count == 0
        assert math.isclose(result_trans.inspection_report.volume_mm3, expected_bored_vol, rel_tol=0.01)
        assert math.isclose(
            result_trans.inspection_report.volume_mm3, result.inspection_report.volume_mm3, rel_tol=0.001
        )
        assert len(result_trans.operation_results) == 2
        assert [op.reference_id for op in result_trans.operation_results] == ["body.gear.trans", "feature.gear-bore.1"]
        print(
            f"[M32-LIVE-06] Translated gear verified: volume = {result_trans.inspection_report.volume_mm3:.1f} mm3 matches origin gear",
            flush=True,
        )
    finally:
        live_runtime.close_document(doc_handle_trans)


def test_live_m32_07a_preflight_rejection_and_clean_close(live_runtime: SolidEdgeRuntime) -> None:
    """M32-LIVE-07a: Verify preflight rejection of unsupported operation returns controlled error and closes cleanly."""
    app_handle = live_runtime.connect_application()
    diag = live_runtime.get_diagnostics()
    print(f"\n[M32-LIVE-07a] Solid Edge Version: {diag.version_build}", flush=True)

    # Unsupported ProfileCutoutFeature plan
    plan_invalid = FeaturePlan(
        plan_version="cad_copilot.single_part_feature_plan.v1",
        request_id="req-live-m32-07a",
        part=PartMetadata(part_id="part.invalid.preflight", design_intent="invalid cutout"),
        base_body=RectangularBaseBody(
            id="body.main",
            length_mm=50.0,
            width_mm=50.0,
            thickness_mm=20.0,
        ),
        primitive_bodies=(
            RectangularBaseBody(
                id="body.main",
                length_mm=50.0,
                width_mm=50.0,
                thickness_mm=20.0,
            ),
        ),
        features=(
            ProfileCutoutFeature(
                id="feat.bad_cut",
                profile_points=(
                    ProfilePoint2D(0.0, 0.0),
                    ProfilePoint2D(10.0, 0.0),
                    ProfilePoint2D(10.0, 10.0),
                    ProfilePoint2D(0.0, 10.0),
                ),
                target_face="+Z",
            ),
        ),
    )

    doc_handle = live_runtime.create_part_document(app_handle)
    try:
        executor = SolidEdgeExecutor(live_runtime, doc_handle)
        result = executor.execute_feature_plan(plan_invalid)

        assert isinstance(result, ExecutionFailure)
        assert result.phase == "preflight"
        assert result.details.get("error_code") == "UNSUPPORTED_EXECUTION_OPERATION"
        print(
            f"[M32-LIVE-07a] Controlled preflight failure verified: {result.details.get('error_code')} ({result.message})",
            flush=True,
        )
    finally:
        live_runtime.close_document(doc_handle)
        print("[M32-LIVE-07a] Request document closed cleanly without prompt.", flush=True)


def test_live_m32_07b_native_construction_failure_and_clean_close() -> None:
    """M32-LIVE-07b: Verify induced native feature construction failure handles cleanly on live COM with prompt-free closure."""
    setup_runtime = SolidEdgeRuntime()
    live_runtime = SolidEdgeRuntime()
    try:
        setup_handle = setup_runtime.connect_application()
        if setup_handle.ownership != OwnershipMode.OWNED:
            pytest.skip("Self-contained native-failure test requires no pre-existing Solid Edge session")
        assert setup_handle.process_identity is not None
        pid = setup_handle.process_identity.pid

        app_handle = live_runtime.connect_application()
        assert app_handle.ownership == OwnershipMode.BORROWED
        assert app_handle.process_identity is not None
        assert app_handle.process_identity.pid == pid
        diag = live_runtime.get_diagnostics()
        print(f"\n[M32-LIVE-07b] Solid Edge Version: {diag.version_build}", flush=True)

        doc_handle = live_runtime.create_part_document(app_handle)
        try:

            def _task_failing_feature(raw_doc: Any, worker: Any) -> dict[str, Any]:
                # 1. Base solid: 50x50x20 mm
                model = create_primitive_cuboid(raw_doc, worker, length_mm=50.0, width_mm=50.0, height_mm=20.0)
                assert model is not None

                # 2. Place a valid closed profile entirely outside the block footprint.
                # The positive cut depth passes local validation; Solid Edge must decide
                # that the requested subtraction cannot create a valid feature.
                top_plane = resolve_or_create_reference_plane(raw_doc, worker, "XY", offset_mm=20.0)
                prof = create_profile_on_plane(raw_doc, worker, top_plane)
                draw_circle_profile(prof, worker, center_x_mm=100.0, center_y_mm=0.0, radius_mm=5.0)
                create_cutout_finite(model, worker, prof, depth_mm=10.0, face="+Z", profile_family="circle")

                return {"unexpected_success": True}

            with pytest.raises(CADExecutionError) as exc_info:
                live_runtime.run_document_task(doc_handle, _task_failing_feature)

            assert exc_info.value.error_code in {"FEATURE_CREATION_FAILED", "FEATURE_VALIDATION_FAILED"}
            assert "0x" not in str(exc_info.value).lower()

            # The runtime remains usable after the native refusal and the original
            # solid is still queryable. No fallback feature call or retry is made.
            def _task_inspect_after_failure(raw_doc: Any, worker: Any) -> int:
                models = worker._invoke_com(lambda: getattr(raw_doc, "Models", None))
                return int(worker._invoke_com(lambda: getattr(models, "Count", 0)))

            assert live_runtime.run_document_task(doc_handle, _task_inspect_after_failure) == 1
            assert live_runtime.is_healthy()
        finally:
            live_runtime.close_document(doc_handle)
            print("[M32-LIVE-07b] Failed document closed cleanly without save prompt.", flush=True)

        # Prove that the same borrowed application accepts a subsequent request.
        recovery_doc = live_runtime.create_part_document(app_handle)
        try:

            def _task_recovery(raw_doc: Any, worker: Any) -> bool:
                model = create_primitive_cuboid(
                    raw_doc,
                    worker,
                    length_mm=20.0,
                    width_mm=20.0,
                    height_mm=10.0,
                )
                return model is not None

            assert live_runtime.run_document_task(recovery_doc, _task_recovery)
        finally:
            live_runtime.close_document(recovery_doc)

        live_runtime.teardown(force_kill_on_failure=False)
        assert is_process_alive(pid)
        print(
            f"[M32-LIVE-07b] Native non-intersecting cut refused safely: {exc_info.value.error_code} | "
            "subsequent request succeeded and borrowed session was preserved",
            flush=True,
        )

        setup_runtime.teardown(force_kill_on_failure=False)
        time.sleep(1.0)
        assert not is_process_alive(pid)
    finally:
        live_runtime.teardown(force_kill_on_failure=False)
        setup_runtime.teardown(force_kill_on_failure=False)


def test_live_m32_08a_borrowed_session_isolation_and_unrelated_document_preservation(
    live_runtime: SolidEdgeRuntime,
) -> None:
    """M32-LIVE-08a: Verify borrowed session teardown preserves unrelated document and application process without force kill."""
    print("\n[M32-LIVE-08a] Creating an independently owned setup session and unrelated document...", flush=True)
    setup_runtime = SolidEdgeRuntime()
    try:
        setup_handle = setup_runtime.connect_application()
        if setup_handle.ownership != OwnershipMode.OWNED:
            pytest.skip("Self-contained borrowed-isolation test requires no pre-existing Solid Edge session")
        assert setup_handle.process_identity is not None
        pid = setup_handle.process_identity.pid

        # This document belongs to the independent setup runtime and is never
        # registered with the runtime under test.
        doc_unrelated = setup_runtime.create_part_document(setup_handle)

        def _probe_unrelated(raw_doc: Any, worker: Any) -> dict[str, Any]:
            return {
                "name": str(worker._invoke_com(lambda: getattr(raw_doc, "Name", ""))),
                "mode": int(worker._invoke_com(lambda: getattr(raw_doc, "ModelingMode", 0))),
            }

        before = setup_runtime.run_document_task(doc_unrelated, _probe_unrelated)
        assert before["name"]
        assert before["mode"] == 2

        # The runtime under test must attach to the setup application as borrowed.
        handle = live_runtime.connect_application()
        assert handle.ownership == OwnershipMode.BORROWED
        assert handle.process_identity is not None
        assert handle.process_identity.pid == pid
        assert doc_unrelated.handle_id not in live_runtime._open_document_handles

        doc_request = live_runtime.create_part_document(handle)

        def _task(raw_doc: Any, worker: Any) -> None:
            create_primitive_cuboid(raw_doc, worker, length_mm=30.0, width_mm=30.0, height_mm=10.0)

        live_runtime.run_document_task(doc_request, _task)
        live_runtime.close_document(doc_request)
        live_runtime.teardown(force_kill_on_failure=False)

        # Prove the exact independently tracked document and borrowed process
        # survived the target runtime's teardown.
        assert is_process_alive(pid)
        after = setup_runtime.run_document_task(doc_unrelated, _probe_unrelated)
        assert after == before
        setup_runtime.close_document(doc_unrelated)
        print(
            f"[M32-LIVE-08a] Borrowed session PID {pid} and independently tracked document {before['name']} preserved.",
            flush=True,
        )
    finally:
        setup_runtime.teardown(force_kill_on_failure=False)


def test_live_m32_08b_owned_session_graceful_lifecycle() -> None:
    """M32-LIVE-08b: Verify owned session lifecycle performs graceful shutdown without force kill."""
    print("\n[M32-LIVE-08b] Connecting to Solid Edge...", flush=True)
    owned_runtime = SolidEdgeRuntime()
    try:
        handle = owned_runtime.connect_application()
        assert handle.process_identity is not None
        pid = handle.process_identity.pid

        if handle.ownership != OwnershipMode.OWNED:
            pytest.skip(f"Test requires an owned session; active borrowed session detected (PID {pid})")

        doc_handle = owned_runtime.create_part_document(handle)
        owned_runtime.close_document(doc_handle)

        print(f"[M32-LIVE-08b] Owned session PID {pid}. Executing graceful teardown...", flush=True)
        owned_runtime.teardown(force_kill_on_failure=False)
        time.sleep(1.0)
        assert not is_process_alive(pid)
        print(f"[M32-LIVE-08b] Owned session PID {pid} gracefully terminated without force kill.", flush=True)
    finally:
        # This verification path deliberately never enables force cleanup.
        owned_runtime.teardown(force_kill_on_failure=False)


# ---------------------------------------------------------------------------
# M3.3 Artifact Export & Publication Live Characterization Gates
# ---------------------------------------------------------------------------


def test_live_m33_01_export_characterization_and_staging_rename(
    live_runtime: SolidEdgeRuntime,
    tmp_path: Path,
) -> None:
    """M33-LIVE-01: Characterize public Solid Edge export calls, identity stability, and atomic directory rename."""
    print("\n[M33-LIVE-01] Connecting to Solid Edge for export characterization...", flush=True)
    handle = live_runtime.connect_application()
    assert handle is not None

    diag = live_runtime.get_diagnostics()
    assert diag.version_build is not None and len(diag.version_build) > 0
    print(f"[M33-LIVE-01] Solid Edge Version/Build: {diag.version_build}", flush=True)

    staging_dir = tmp_path / ".staging-test-req-001"
    final_dir = tmp_path / "test-req-001"
    staging_dir.mkdir(parents=True, exist_ok=True)

    doc_handle = live_runtime.create_part_document(handle)
    print(f"[M33-LIVE-01] Created disposable request part document {doc_handle.handle_id}", flush=True)
    doc_closed = False

    try:

        def _characterize_exports(raw_doc: Any, worker: Any) -> dict[str, Any]:
            # Model base geometry
            create_primitive_cuboid(raw_doc, worker, length_mm=30.0, width_mm=20.0, height_mm=10.0)
            worker._invoke_com(lambda: raw_doc.Recompute())

            initial_name = str(worker._invoke_com(lambda: getattr(raw_doc, "Name", "")))

            # 1. Native PAR export via SaveCopyAs
            par_file = staging_dir / "model.par"
            worker._invoke_com(lambda: raw_doc.SaveCopyAs(str(par_file)))
            name_after_par = str(worker._invoke_com(lambda: getattr(raw_doc, "Name", "")))

            # 2. STEP export via SaveCopyAs
            step_file = staging_dir / "model.step"
            worker._invoke_com(lambda: raw_doc.SaveCopyAs(str(step_file)))
            name_after_step = str(worker._invoke_com(lambda: getattr(raw_doc, "Name", "")))

            # 3. STL export via SaveCopyAs
            stl_file = staging_dir / "model.stl"
            worker._invoke_com(lambda: raw_doc.SaveCopyAs(str(stl_file)))
            name_after_stl = str(worker._invoke_com(lambda: getattr(raw_doc, "Name", "")))

            # 4. Preview capture bound to request-owned document window
            jpg_file = staging_dir / "preview.jpg"
            preview_method = "none"
            preview_ok = False
            try:
                # Resolve window strictly from the request document's own Windows collection
                windows = worker._invoke_com(lambda: getattr(raw_doc, "Windows", None))
                win_count = worker._invoke_com(lambda: getattr(windows, "Count", 0)) if windows is not None else 0
                target_win = None
                if win_count > 0:
                    target_win = worker._invoke_com(lambda: windows.Item(1))
                else:
                    preview_method = "no_document_window"

                if target_win is not None:
                    view = worker._invoke_com(lambda: getattr(target_win, "View", None))
                    if view is not None:
                        worker._invoke_com(lambda: view.SaveAsImage(str(jpg_file), 800, 600))
                        if jpg_file.is_file() and jpg_file.stat().st_size > 0:
                            preview_ok = True
                            preview_method = "request_doc_view.SaveAsImage"
                        else:
                            preview_method = "preview_file_missing_after_save"
            except Exception as exc:
                # Post-failure health probe: distinguish localized preview failure from broader fatal document loss
                try:
                    probe_name = worker._invoke_com(lambda: getattr(raw_doc, "Name", None))
                    if not probe_name:
                        raise CADDocumentError("Document unseated during preview capture", error_code="DOCUMENT_LOST")
                except Exception as probe_exc:
                    raise CADDocumentError(
                        f"Fatal document loss during preview capture: {describe_exception(probe_exc)}",
                        error_code="DOCUMENT_LOST",
                    ) from exc

                # Underlying document and worker remain healthy; failure is localized to preview API
                preview_method = f"localized_failure: {type(exc).__name__}"

            return {
                "initial_name": initial_name,
                "name_after_par": name_after_par,
                "name_after_step": name_after_step,
                "name_after_stl": name_after_stl,
                "preview_ok": preview_ok,
                "preview_method": preview_method,
            }

        results = live_runtime.run_document_task(doc_handle, _characterize_exports)
        print(f"[M33-LIVE-01] Export characterization results: {results}", flush=True)

        # Invariant 1: Document identity is strictly preserved (SaveCopyAs does not mutate doc name)
        assert results["initial_name"] == results["name_after_par"]
        assert results["initial_name"] == results["name_after_step"]
        assert results["initial_name"] == results["name_after_stl"]

        # Invariant 2: Required outputs exist and are non-empty
        par_out = staging_dir / "model.par"
        step_out = staging_dir / "model.step"
        stl_out = staging_dir / "model.stl"

        assert par_out.is_file() and par_out.stat().st_size > 0
        assert step_out.is_file() and step_out.stat().st_size > 0
        assert stl_out.is_file() and stl_out.stat().st_size > 0

        # Invariant 3: STEP header contains standard ISO-10303-21 signature
        step_text = step_out.read_text(encoding="utf-8", errors="replace")[:1024]
        assert "ISO-10303-21" in step_text

        # Invariant 4: Preview image (if supported) is a valid JPEG
        jpg_out = staging_dir / "preview.jpg"
        if results["preview_ok"]:
            assert jpg_out.is_file(), "Preview was reported as OK but preview.jpg is missing from disk"
            jpg_bytes = jpg_out.read_bytes()
            assert len(jpg_bytes) > 0
            assert jpg_bytes.startswith(b"\xff\xd8\xff")
            print(f"[M33-LIVE-01] Preview snapshot verified: {len(jpg_bytes)} bytes", flush=True)

        # Invariant 5: Terminal close releases all COM locks
        print("[M33-LIVE-01] Closing request document to release file handles...", flush=True)
        live_runtime.close_document(doc_handle)
        doc_closed = True

        # Invariant 6: Atomic directory rename succeeds cleanly without Windows locking errors
        print(f"[M33-LIVE-01] Renaming staging directory {staging_dir.name} -> {final_dir.name}...", flush=True)
        staging_dir.rename(final_dir)
        assert final_dir.is_dir()
        assert not staging_dir.exists()
        assert (final_dir / "model.par").is_file()
        assert (final_dir / "model.step").is_file()
        assert (final_dir / "model.stl").is_file()

        # Invariant 7: Artifact outputs satisfy Step 4 strict validators
        print("[M33-LIVE-01] Validating published artifacts with Step 4 validators...", flush=True)
        step_size = validate_step_artifact(final_dir / "model.step")
        stl_size = validate_stl_artifact(final_dir / "model.stl")
        assert step_size > 0
        assert stl_size > 0
        if (final_dir / "preview.jpg").exists():
            jpg_size = validate_jpg_artifact(final_dir / "preview.jpg")
            assert jpg_size > 0
        print(
            f"[M33-LIVE-01] Artifact validation passed! step={step_size}B, stl={stl_size}B. Characterization complete.",
            flush=True,
        )

    finally:
        if not doc_closed:
            with contextlib.suppress(Exception):
                live_runtime.close_document(doc_handle)


# ---------------------------------------------------------------------------
# M3.3 Step 8: End-to-End Pipeline Smoke Matrix & Failure Gates
# ---------------------------------------------------------------------------


def _make_cuboid_plan(req_id: str) -> FeaturePlan:
    return FeaturePlan(
        plan_version="cad_copilot.single_part_feature_plan.v1",
        request_id=req_id,
        part=PartMetadata(part_id="part.smoke.cuboid", design_intent="live smoke cuboid"),
        base_body=RectangularBaseBody(
            id="body.cuboid",
            length_mm=40.0,
            width_mm=30.0,
            thickness_mm=10.0,
        ),
        primitive_bodies=(
            RectangularBaseBody(
                id="body.cuboid",
                length_mm=40.0,
                width_mm=30.0,
                thickness_mm=10.0,
            ),
        ),
    )


def _make_cylinder_plan(req_id: str) -> FeaturePlan:
    return FeaturePlan(
        plan_version="cad_copilot.single_part_feature_plan.v1",
        request_id=req_id,
        part=PartMetadata(part_id="part.smoke.cylinder", design_intent="live smoke cylinder"),
        base_body=CylinderBaseBody(
            id="body.cylinder",
            radius_mm=15.0,
            height_mm=30.0,
        ),
        primitive_bodies=(
            CylinderBaseBody(
                id="body.cylinder",
                radius_mm=15.0,
                height_mm=30.0,
            ),
        ),
    )


def _make_plate_cut_plan(req_id: str) -> FeaturePlan:
    return FeaturePlan(
        plan_version="cad_copilot.single_part_feature_plan.v1",
        request_id=req_id,
        part=PartMetadata(part_id="part.smoke.plate_cut", design_intent="live smoke plate cut"),
        base_body=RectangularBaseBody(
            id="body.main",
            length_mm=60.0,
            width_mm=40.0,
            thickness_mm=15.0,
        ),
        primitive_bodies=(
            RectangularBaseBody(
                id="body.main",
                length_mm=60.0,
                width_mm=40.0,
                thickness_mm=15.0,
            ),
        ),
        features=(
            CircularThroughHoleFeature(
                id="feat.hole",
                target_body_id="body.main",
                diameter_mm=12.0,
                center_x_mm=0.0,
                center_y_mm=0.0,
                target_face="+Z",
            ),
        ),
    )


def _make_sequential_plan(req_id: str) -> FeaturePlan:
    return FeaturePlan(
        plan_version="cad_copilot.single_part_feature_plan.v1",
        request_id=req_id,
        part=PartMetadata(part_id="part.smoke.seq", design_intent="live smoke sequential"),
        base_body=RectangularBaseBody(
            id="body.main",
            length_mm=100.0,
            width_mm=80.0,
            thickness_mm=30.0,
        ),
        primitive_bodies=(
            RectangularBaseBody(
                id="body.main",
                length_mm=100.0,
                width_mm=80.0,
                thickness_mm=30.0,
            ),
        ),
        features=(
            CircularThroughHoleFeature(
                id="feat.hole",
                diameter_mm=12.0,
                center_x_mm=-25.0,
                center_y_mm=0.0,
                target_face="+Z",
            ),
            RectangularThroughCutoutFeature(
                id="feat.rect_cut",
                width_mm=16.0,
                height_mm=16.0,
                center_x_mm=0.0,
                center_y_mm=15.0,
                target_face="+Z",
            ),
            SlotThroughCutoutFeature(
                id="feat.slot_cut",
                length_mm=20.0,
                width_mm=8.0,
                center_x_mm=0.0,
                center_y_mm=-15.0,
                target_face="+Z",
            ),
            RectangularExtrudedPadFeature(
                id="feat.pad",
                width_mm=20.0,
                height_mm=20.0,
                distance_mm=10.0,
                center_x_mm=25.0,
                center_y_mm=0.0,
                target_face="+Z",
            ),
        ),
    )


def _make_gear_plan(req_id: str) -> FeaturePlan:
    return FeaturePlan(
        plan_version="cad_copilot.single_part_feature_plan.v1",
        request_id=req_id,
        part=PartMetadata(part_id="part.smoke.gear", design_intent="live smoke gear"),
        base_body=SpurGearBaseBody(
            id="body.gear",
            tooth_count=24,
            module_mm=2.0,
            face_width_mm=15.0,
            pressure_angle_deg=20.0,
            bore_diameter_mm=12.0,
            placement=BodyPlacement(x_mm=0.0, y_mm=0.0, z_mm=0.0),
        ),
        primitive_bodies=(
            SpurGearBaseBody(
                id="body.gear",
                tooth_count=24,
                module_mm=2.0,
                face_width_mm=15.0,
                pressure_angle_deg=20.0,
                bore_diameter_mm=12.0,
                placement=BodyPlacement(x_mm=0.0, y_mm=0.0, z_mm=0.0),
            ),
        ),
    )


@pytest.mark.parametrize(
    ("case_id", "plan_factory"),
    [
        ("cuboid", _make_cuboid_plan),
        ("cylinder", _make_cylinder_plan),
        ("plate_cut", _make_plate_cut_plan),
        ("sequential", _make_sequential_plan),
        ("gear", _make_gear_plan),
    ],
)
def test_live_m33_02_pipeline_end_to_end_smoke_matrix(
    live_runtime: SolidEdgeRuntime,
    tmp_path: Path,
    case_id: str,
    plan_factory: Any,
) -> None:
    """M33-LIVE-02: End-to-end smoke matrix verifying pipeline finalization across 5 representative geometries."""
    app_handle = live_runtime.connect_application()
    diag = live_runtime.get_diagnostics()
    req_id = f"req-live-{case_id}"
    print(f"\n[M33-LIVE-02] Case '{case_id}' on Solid Edge {diag.version_build} (req_id={req_id})", flush=True)

    plan = plan_factory(req_id)
    doc_handle = live_runtime.create_part_document(app_handle)
    executor = SolidEdgeExecutor(live_runtime, doc_handle)

    # 1. Execute plan with authoritative inspection
    exec_result = executor.execute_feature_plan(plan)
    assert isinstance(exec_result, ExecutionSuccess), f"Execution failed: {exec_result}"
    assert exec_result.inspection_report is not None
    assert exec_result.inspection_report.solid_body_count == 1
    assert exec_result.inspection_report.sheet_body_count == 0
    assert exec_result.inspection_report.wire_body_count == 0
    assert exec_result.inspection_report.volume_mm3 > 0.0

    # 2. Finalize request artifacts
    final_result = finalize_request_artifacts(
        executor=executor,
        success_result=exec_result,
        output_root=tmp_path,
        request_id=req_id,
    )
    assert isinstance(final_result, ExecutionSuccess)

    # 3. Invariant: Terminal release consumed the handle
    assert executor._doc_handle is None
    assert doc_handle.handle_id not in live_runtime._open_document_handles

    # 4. Invariant: Published directory exists and staging directory is unlinked
    request_dir = tmp_path / req_id
    assert request_dir.is_dir()
    staging_dirs = list(tmp_path.glob(f".staging-{req_id}-*"))
    assert len(staging_dirs) == 0, f"Found lingering staging directories: {staging_dirs}"

    # 5. Invariant: Required on-disk files exist and are non-empty
    par_file = request_dir / f"{req_id}.par"
    step_file = request_dir / f"{req_id}.step"
    stl_file = request_dir / f"{req_id}.stl"
    jpg_file = request_dir / f"{req_id}.jpg"

    assert par_file.is_file() and par_file.stat().st_size > 0
    assert step_file.is_file() and step_file.stat().st_size > 0
    assert stl_file.is_file() and stl_file.stat().st_size > 0

    # 6. Invariant: Artifacts satisfy Step 4 strict validators
    assert validate_par_artifact(par_file, final_result.inspection_report) == par_file.stat().st_size
    step_size = validate_step_artifact(step_file)
    assert step_size == step_file.stat().st_size
    stl_size = validate_stl_artifact(stl_file)
    assert stl_size == stl_file.stat().st_size

    step_text = step_file.read_text(encoding="utf-8", errors="replace")[:1024]
    assert "ISO-10303-21" in step_text

    # 7. Check preview status
    if jpg_file.exists():
        jpg_size = validate_jpg_artifact(jpg_file)
        assert jpg_size == jpg_file.stat().st_size
        assert any(a.format == "jpg" for a in final_result.exported_artifacts)
        print(f"[M33-LIVE-02] Case '{case_id}': preview.jpg validated ({jpg_size}B)", flush=True)
    else:
        assert any(
            w.get("code") == "PREVIEW_EXPORT_FAILED" or "PREVIEW_EXPORT_FAILED" in str(w) for w in final_result.warnings
        )
        print(f"[M33-LIVE-02] Case '{case_id}': preview skipped with warning", flush=True)

    # 8. Invariant: Exported artifact records match disk exactly
    formats = [a.format for a in final_result.exported_artifacts]
    assert formats[:3] == ["par", "step", "stl"]

    for record in final_result.exported_artifacts:
        assert isinstance(record, ArtifactRecord)
        expected_path = request_dir / f"{req_id}.{record.format}"
        assert Path(record.path) == expected_path
        assert record.size_bytes == expected_path.stat().st_size
        assert record.sha256 is not None and len(record.sha256) == 64
        assert record.origin == "cad_copilot"


def test_live_m33_03_reopen_exported_par_smoke_gate(
    live_runtime: SolidEdgeRuntime,
    tmp_path: Path,
) -> None:
    """M33-LIVE-03: Independently reopen exported native .par in Solid Edge to prove valid geometry."""
    app_handle = live_runtime.connect_application()
    diag = live_runtime.get_diagnostics()
    print(f"\n[M33-LIVE-03] Reopening exported .par on Solid Edge {diag.version_build}...", flush=True)

    req_id = "req-live-reopen-par"
    doc_handle = live_runtime.create_part_document(app_handle)
    executor = SolidEdgeExecutor(live_runtime, doc_handle)
    plan = _make_cuboid_plan(req_id)
    exec_res = executor.execute_feature_plan(plan)
    assert isinstance(exec_res, ExecutionSuccess)

    final_res = finalize_request_artifacts(executor, exec_res, tmp_path, req_id)
    assert isinstance(final_res, ExecutionSuccess)
    par_file = tmp_path / req_id / f"{req_id}.par"
    assert par_file.is_file()

    # Independently open the exported .par file
    opened_handle = live_runtime.open_document(app_handle, par_file)
    assert opened_handle is not None
    assert opened_handle.handle_id in live_runtime._open_document_handles

    try:
        worker = live_runtime._ensure_worker()
        raw_doc = worker._document_registry[opened_handle.handle_id]

        def _inspect_opened() -> dict[str, Any]:
            name = str(worker._invoke_com(lambda: getattr(raw_doc, "Name", "")))
            mode = int(worker._invoke_com(lambda: getattr(raw_doc, "ModelingMode", 0)))
            models_col = worker._invoke_com(lambda: getattr(raw_doc, "Models", None))
            models_count = worker._invoke_com(lambda: getattr(models_col, "Count", 0)) if models_col else 0
            return {"name": name, "mode": mode, "models_count": models_count}

        doc_info = worker.call(_inspect_opened, timeout=10.0)
        print(f"[M33-LIVE-03] Reopened document verified: {doc_info}", flush=True)
        assert f"{req_id}.par".lower() in doc_info["name"].lower()
        assert doc_info["mode"] == 2  # Ordered mode
        assert doc_info["models_count"] == 1  # 1 solid model
    finally:
        live_runtime.close_document(opened_handle)
        assert opened_handle.handle_id not in live_runtime._open_document_handles


def test_live_m33_04_borrowed_session_isolation_and_unrelated_document_preservation(
    live_runtime: SolidEdgeRuntime,
    tmp_path: Path,
) -> None:
    """M33-LIVE-04: Verify artifact finalization in borrowed session preserves unrelated document and application process."""
    print("\n[M33-LIVE-04] Setting up independent application and unrelated document...", flush=True)
    setup_runtime = SolidEdgeRuntime()
    try:
        setup_handle = setup_runtime.connect_application()
        if setup_handle.ownership != OwnershipMode.OWNED:
            pytest.skip("Self-contained borrowed-isolation test requires no pre-existing Solid Edge session")
        assert setup_handle.process_identity is not None
        pid = setup_handle.process_identity.pid

        # Create unrelated document in setup session
        doc_unrelated = setup_runtime.create_part_document(setup_handle)

        def _probe_unrelated(raw_doc: Any, worker: Any) -> dict[str, Any]:
            return {
                "name": str(worker._invoke_com(lambda: getattr(raw_doc, "Name", ""))),
                "mode": int(worker._invoke_com(lambda: getattr(raw_doc, "ModelingMode", 0))),
            }

        before = setup_runtime.run_document_task(doc_unrelated, _probe_unrelated)
        assert before["name"]
        assert before["mode"] == 2

        # Connect live_runtime as borrowed
        handle = live_runtime.connect_application()
        assert handle.ownership == OwnershipMode.BORROWED
        assert handle.process_identity is not None
        assert handle.process_identity.pid == pid
        assert doc_unrelated.handle_id not in live_runtime._open_document_handles

        # Run full artifact pipeline on request document
        req_id = "req-live-borrowed-01"
        doc_request = live_runtime.create_part_document(handle)
        executor = SolidEdgeExecutor(live_runtime, doc_request)
        plan = _make_cuboid_plan(req_id)
        exec_res = executor.execute_feature_plan(plan)
        assert isinstance(exec_res, ExecutionSuccess)

        final_res = finalize_request_artifacts(executor, exec_res, tmp_path, req_id)
        assert isinstance(final_res, ExecutionSuccess)
        assert (tmp_path / req_id / f"{req_id}.par").is_file()

        # Teardown live_runtime (borrowed session)
        live_runtime.teardown(force_kill_on_failure=False)

        # Verify borrowed process and unrelated document survived intact
        assert is_process_alive(pid)
        after = setup_runtime.run_document_task(doc_unrelated, _probe_unrelated)
        assert after == before
        setup_runtime.close_document(doc_unrelated)
        print(
            f"[M33-LIVE-04] Borrowed session PID {pid} and unrelated document {before['name']} preserved after artifact finalization.",
            flush=True,
        )
    finally:
        live_runtime.teardown(force_kill_on_failure=False)
        setup_runtime.teardown(force_kill_on_failure=False)


def test_live_m33_05_owned_session_graceful_lifecycle(tmp_path: Path) -> None:
    """M33-LIVE-05: Verify owned session lifecycle performs graceful shutdown without force kill after artifact finalization."""
    print("\n[M33-LIVE-05] Connecting owned session for artifact finalization...", flush=True)
    owned_runtime = SolidEdgeRuntime()
    try:
        handle = owned_runtime.connect_application()
        assert handle.process_identity is not None
        pid = handle.process_identity.pid

        if handle.ownership != OwnershipMode.OWNED:
            pytest.skip(f"Test requires an owned session; active borrowed session detected (PID {pid})")

        req_id = "req-live-owned-01"
        doc_handle = owned_runtime.create_part_document(handle)
        executor = SolidEdgeExecutor(owned_runtime, doc_handle)
        plan = _make_cuboid_plan(req_id)
        exec_res = executor.execute_feature_plan(plan)
        assert isinstance(exec_res, ExecutionSuccess)

        final_res = finalize_request_artifacts(executor, exec_res, tmp_path, req_id)
        assert isinstance(final_res, ExecutionSuccess)
        assert (tmp_path / req_id / f"{req_id}.par").is_file()

        print(f"[M33-LIVE-05] Owned session PID {pid}. Executing graceful teardown...", flush=True)
        owned_runtime.teardown(force_kill_on_failure=False)
        time.sleep(1.0)
        assert not is_process_alive(pid)
        print(f"[M33-LIVE-05] Owned session PID {pid} gracefully terminated without force kill.", flush=True)
    finally:
        owned_runtime.teardown(force_kill_on_failure=False)


def test_live_m33_06_induced_required_export_failure(
    live_runtime: SolidEdgeRuntime,
    tmp_path: Path,
) -> None:
    """M33-LIVE-06: Verify current-session recovery after an induced executor export failure."""
    app_handle = live_runtime.connect_application()
    diag = live_runtime.get_diagnostics()
    print(f"\n[M33-LIVE-06] Inducing export failure on Solid Edge {diag.version_build}...", flush=True)

    class InducedFailingExportExecutor(SolidEdgeExecutor):
        def export_model(self, format_id: ArtifactFormat, output_path: Path) -> None:
            if format_id == "step":
                raise CADExportError("Induced failure during live STEP export", error_code="ARTIFACT_EXPORT_FAILED")
            super().export_model(format_id, output_path)

    req_id = "req-live-fail-step"
    doc_handle = live_runtime.create_part_document(app_handle)
    executor = InducedFailingExportExecutor(live_runtime, doc_handle)
    plan = _make_cuboid_plan(req_id)
    exec_res = executor.execute_feature_plan(plan)
    assert isinstance(exec_res, ExecutionSuccess)

    with pytest.raises(CADExportError) as exc_info:
        finalize_request_artifacts(executor, exec_res, tmp_path, req_id)

    assert "Induced failure during live STEP export" in str(exc_info.value)
    assert exc_info.value.error_code == "ARTIFACT_EXPORT_FAILED"

    # Verify no final directory was created
    final_dir = tmp_path / req_id
    assert not final_dir.exists()

    # Verify staging directories were cleaned up
    staging_dirs = list(tmp_path.glob(f".staging-{req_id}-*"))
    assert len(staging_dirs) == 0

    # Verify document was cleanly closed
    assert executor._doc_handle is None
    assert doc_handle.handle_id not in live_runtime._open_document_handles

    # Verify subsequent request succeeds on the same live session
    next_req_id = "req-live-after-fail"
    next_doc_handle = live_runtime.create_part_document(app_handle)
    next_executor = SolidEdgeExecutor(live_runtime, next_doc_handle)
    next_plan = _make_cuboid_plan(next_req_id)
    next_exec_res = next_executor.execute_feature_plan(next_plan)
    assert isinstance(next_exec_res, ExecutionSuccess)
    next_final_res = finalize_request_artifacts(next_executor, next_exec_res, tmp_path, next_req_id)
    assert isinstance(next_final_res, ExecutionSuccess)
    assert (tmp_path / next_req_id / f"{next_req_id}.par").is_file()
    print("[M33-LIVE-06] Session remained healthy and successfully finalized subsequent request.", flush=True)


def test_live_m33_07_induced_preview_failure_graceful_degradation(
    live_runtime: SolidEdgeRuntime,
    tmp_path: Path,
) -> None:
    """M33-LIVE-07: Verify graceful degradation and warning retention under an induced executor preview failure."""
    app_handle = live_runtime.connect_application()
    diag = live_runtime.get_diagnostics()
    print(f"\n[M33-LIVE-07] Inducing preview failure on Solid Edge {diag.version_build}...", flush=True)

    class InducedFailingPreviewExecutor(SolidEdgeExecutor):
        def capture_preview(self, output_path: Path, width: int = 800, height: int = 600) -> None:
            raise CADExportError("Induced localized preview failure", error_code="PREVIEW_EXPORT_FAILED")

    req_id = "req-live-fail-preview"
    doc_handle = live_runtime.create_part_document(app_handle)
    executor = InducedFailingPreviewExecutor(live_runtime, doc_handle)
    plan = _make_cuboid_plan(req_id)
    exec_res = executor.execute_feature_plan(plan)
    assert isinstance(exec_res, ExecutionSuccess)

    final_res = finalize_request_artifacts(executor, exec_res, tmp_path, req_id)
    assert isinstance(final_res, ExecutionSuccess)

    # Required artifacts must exist
    final_dir = tmp_path / req_id
    assert final_dir.is_dir()
    assert (final_dir / f"{req_id}.par").is_file()
    assert (final_dir / f"{req_id}.step").is_file()
    assert (final_dir / f"{req_id}.stl").is_file()
    assert not (final_dir / f"{req_id}.jpg").exists()

    # Warning must be retained
    assert any(
        w.get("code") == "PREVIEW_EXPORT_FAILED" or "PREVIEW_EXPORT_FAILED" in str(w) for w in final_res.warnings
    )

    # Artifact records must only include par, step, stl (no jpg)
    formats = [a.format for a in final_res.exported_artifacts]
    assert formats == ["par", "step", "stl"]

    # Document must be closed
    assert executor._doc_handle is None
    print("[M33-LIVE-07] Degradation verified: required artifacts published, preview warning retained.", flush=True)


def test_live_m33_08_publication_collision_rejection(
    live_runtime: SolidEdgeRuntime,
    tmp_path: Path,
) -> None:
    """M33-LIVE-08: Verify preflight collision rejection with a pre-existing target directory leaves content untouched."""
    app_handle = live_runtime.connect_application()
    diag = live_runtime.get_diagnostics()
    print(f"\n[M33-LIVE-08] Testing target collision on Solid Edge {diag.version_build}...", flush=True)

    req_id = "req-live-collision"
    final_dir = tmp_path / req_id
    final_dir.mkdir(parents=True, exist_ok=True)
    sentinel_file = final_dir / "pre_existing.txt"
    sentinel_content = "ORIGINAL_CONTENT_PRESERVED"
    sentinel_file.write_text(sentinel_content, encoding="utf-8")

    doc_handle = live_runtime.create_part_document(app_handle)
    executor = SolidEdgeExecutor(live_runtime, doc_handle)
    plan = _make_cuboid_plan(req_id)
    exec_res = executor.execute_feature_plan(plan)
    assert isinstance(exec_res, ExecutionSuccess)

    with pytest.raises(CADError) as exc_info:
        finalize_request_artifacts(executor, exec_res, tmp_path, req_id)

    assert exc_info.value.error_code == "TARGET_ALREADY_EXISTS"

    # Verify sentinel file remains completely intact
    assert sentinel_file.is_file()
    assert sentinel_file.read_text(encoding="utf-8") == sentinel_content

    # Verify no CAD models were written into final_dir
    assert not (final_dir / f"{req_id}.par").exists()
    assert not (final_dir / f"{req_id}.step").exists()
    assert not (final_dir / f"{req_id}.stl").exists()

    # Verify document was cleanly closed
    assert executor._doc_handle is None
    assert doc_handle.handle_id not in live_runtime._open_document_handles
    print("[M33-LIVE-08] Collision rejected: pre-existing content untouched, document cleanly released.", flush=True)


def test_m41_live_01_prompt_to_cad_block_orchestration(
    live_runtime: SolidEdgeRuntime,
    tmp_path: Path,
) -> None:
    """M41-LIVE-01: prompt_to_cad with an injected deterministic block-plan resolver."""
    diag = live_runtime.get_diagnostics()
    req_id = "req-m41-live-01"
    print(f"\n[M41-LIVE-01] Testing prompt_to_cad on Solid Edge {diag.version_build}...", flush=True)

    block_payload = {
        "plan_version": "cad_copilot.single_part_feature_plan.v1",
        "request_id": req_id,
        "units": "mm",
        "part": {"part_id": "part.live.block", "design_intent": "deterministic block", "scope": "single_part"},
        "base_body": {
            "id": "body.main",
            "family": "rectangular_prism",
            "dimensions_mm": {"length": 50.0, "width": 40.0, "thickness": 10.0},
        },
        "primitive_bodies": [],
        "boolean_operations": [],
        "features": [],
    }

    def block_resolver(req: PromptGenerationRequest) -> PlanProposal:
        return PlanProposal(
            plan_payload=block_payload,
            provenance="ai_proposal",
            source_id="deterministic_block_test",
        )

    service = GenerationService(
        prompt_resolver=block_resolver,
        runtime_factory=lambda: live_runtime,
    )

    req = PromptGenerationRequest(
        contract_version="1.0",
        request_id=req_id,
        kind="prompt_to_cad",
        unit="mm",
        prompt="Create a rectangular prism 50x40x10 mm",
    )
    resp = service.generate(req, output_root=tmp_path)
    assert resp["status"] == "accepted"
    assert resp["request_id"] == req_id
    assert "warnings" in resp
    artifacts = resp["data"]["artifacts"]
    assert len(artifacts) >= 3

    final_dir = tmp_path / req_id
    assert (final_dir / f"{req_id}.par").is_file()
    assert (final_dir / f"{req_id}.step").is_file()
    assert (final_dir / f"{req_id}.stl").is_file()
    print("[M41-LIVE-01] Success: Block prompt_to_cad generated all valid artifacts.", flush=True)


def test_m41_live_02_example_plan_spur_gear_orchestration(
    live_runtime: SolidEdgeRuntime,
    tmp_path: Path,
) -> None:
    """M41-LIVE-02: example_plan with an injected conceptual spur-gear resolver."""
    diag = live_runtime.get_diagnostics()
    req_id = "req-m41-live-02"
    print(f"\n[M41-LIVE-02] Testing example_plan on Solid Edge {diag.version_build}...", flush=True)

    gear_payload = {
        "plan_version": "cad_copilot.single_part_feature_plan.v1",
        "request_id": req_id,
        "units": "mm",
        "part": {"part_id": "part.live.gear", "design_intent": "conceptual spur gear", "scope": "single_part"},
        "base_body": {
            "id": "body.gear",
            "family": "spur_gear",
            "dimensions_mm": {
                "tooth_count": 24,
                "module_mm": 2.0,
                "face_width_mm": 15.0,
                "pressure_angle_deg": 20.0,
                "bore_diameter_mm": 12.0,
            },
        },
        "primitive_bodies": [],
        "boolean_operations": [],
        "features": [],
    }

    def gear_resolver(req: ExampleGenerationRequest) -> PlanProposal:
        return PlanProposal(
            plan_payload=gear_payload,
            provenance="example_plan",
            source_id="spur_gear_concept",
        )

    service = GenerationService(
        example_resolver=gear_resolver,
        runtime_factory=lambda: live_runtime,
    )

    req = ExampleGenerationRequest(
        contract_version="1.0",
        request_id=req_id,
        kind="example_plan",
        unit="mm",
        example_id="spur_gear",
    )
    resp = service.generate(req, output_root=tmp_path)
    assert resp["status"] == "accepted"
    assert resp["request_id"] == req_id
    assert "warnings" in resp
    artifacts = resp["data"]["artifacts"]
    assert len(artifacts) >= 3

    final_dir = tmp_path / req_id
    assert (final_dir / f"{req_id}.par").is_file()
    assert (final_dir / f"{req_id}.step").is_file()
    assert (final_dir / f"{req_id}.stl").is_file()
    print("[M41-LIVE-02] Success: Spur gear example_plan generated all valid artifacts.", flush=True)


def test_m44_live_01_prompt_to_cad_block_manifest(
    live_runtime: SolidEdgeRuntime,
    tmp_path: Path,
) -> None:
    """M44-LIVE-01: prompt_to_cad publishes valid run_manifest.json with live runtime metadata."""
    req_id = "req-m44-live-01"
    prompt_text = "Create a rectangular prism 50x40x10 mm"
    print("\n[M44-LIVE-01] Testing prompt_to_cad with run manifest...", flush=True)

    block_payload = {
        "plan_version": "cad_copilot.single_part_feature_plan.v1",
        "request_id": req_id,
        "units": "mm",
        "part": {
            "part_id": "part.live.m44_block",
            "design_intent": "deterministic block for manifest test",
            "scope": "single_part",
        },
        "base_body": {
            "id": "body.main",
            "family": "rectangular_prism",
            "dimensions_mm": {"length": 50.0, "width": 40.0, "thickness": 10.0},
        },
        "primitive_bodies": [],
        "boolean_operations": [],
        "features": [],
    }

    def block_resolver(req: PromptGenerationRequest) -> PlanProposal:
        return PlanProposal(
            plan_payload=block_payload,
            provenance="ai_proposal",
            source_id="m44_block_deterministic",
        )

    captured_diagnostics: list[Any] = []

    def spy_executor_factory(runtime: Any, doc: Any) -> SolidEdgeExecutor:
        captured_diagnostics.append(runtime.get_diagnostics())
        return SolidEdgeExecutor(runtime, doc)

    service = GenerationService(
        prompt_resolver=block_resolver,
        runtime_factory=lambda: live_runtime,
        executor_factory=spy_executor_factory,
    )

    req = PromptGenerationRequest(
        contract_version="1.0",
        request_id=req_id,
        kind="prompt_to_cad",
        unit="mm",
        prompt=prompt_text,
    )
    resp = service.generate(req, output_root=tmp_path)
    assert resp["status"] == "accepted"
    assert resp["request_id"] == req_id
    assert "warnings" in resp

    assert len(captured_diagnostics) == 1
    diag = captured_diagnostics[0]
    assert diag.version_build is not None and len(diag.version_build.strip()) > 0

    # Artifact records in response contains strictly the model files (no run_manifest.json)
    artifacts = resp["data"]["artifacts"]
    assert len(artifacts) in (3, 4)
    for record in artifacts:
        assert record["format"] in ("par", "step", "stl", "jpg")
        assert not record["path"].endswith("run_manifest.json")

    # Staging directory must NOT remain
    staging_candidates = [p for p in tmp_path.iterdir() if p.name.startswith(".staging_")]
    assert len(staging_candidates) == 0, f"Staging directory leaked: {staging_candidates}"

    # Published directory verification
    final_dir = tmp_path / req_id
    assert final_dir.is_dir()
    manifest_path = final_dir / "run_manifest.json"
    assert manifest_path.is_file()

    # Exact on-disk inventory: len(artifacts) + 1 (the manifest)
    published_files = list(final_dir.iterdir())
    assert len(published_files) == len(artifacts) + 1

    # Validate published manifest against canonical Draft 2020-12 schema
    manifest_bytes = manifest_path.read_bytes()
    manifest_data = json.loads(manifest_bytes.decode("utf-8"))
    validator = get_run_manifest_validator()
    validator.validate(manifest_data)

    # Core manifest fields
    assert manifest_data["manifest_version"] == "cad_copilot.run_manifest.v1"
    assert manifest_data["request"]["request_id"] == req_id
    assert manifest_data["request"]["contract_version"] == "1.0"
    assert manifest_data["request"]["kind"] == "prompt_to_cad"
    assert manifest_data["request"]["unit"] == "mm"
    assert manifest_data["engine"]["name"] == "cad-copilot"
    assert manifest_data["cad_runtime"]["product"] == "solid_edge"
    assert manifest_data["cad_runtime"]["version_build"] == diag.version_build
    assert manifest_data["provenance"]["kind"] == "ai_proposal"
    assert manifest_data["provenance"]["source_id"] == "m44_block_deterministic"

    # Fingerprints & Privacy
    expected_prompt_sha256 = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
    assert manifest_data["fingerprints"]["prompt_sha256"] == expected_prompt_sha256
    assert prompt_text not in manifest_bytes.decode("utf-8"), "Raw prompt leaked into run_manifest.json"

    # Artifact consistency: sizes and hashes match disk files exactly
    assert len(manifest_data["artifacts"]) == len(artifacts)
    for art in manifest_data["artifacts"]:
        art_rel_path = art["path"]
        assert art_rel_path in (f"{req_id}.par", f"{req_id}.step", f"{req_id}.stl", f"{req_id}.jpg")
        art_disk_path = final_dir / art_rel_path
        assert art_disk_path.is_file()
        actual_sha256, actual_size = compute_file_sha256_and_size(art_disk_path)
        assert art["sha256"] == actual_sha256
        assert art["size_bytes"] == actual_size

    # Inspection consistency: single solid body with positive volume
    inspection = manifest_data["execution"]["inspection"]
    assert inspection["body_count"] == 1
    assert inspection["solid_body_count"] == 1
    assert inspection["volume_mm3"] > 0.0

    print(
        f"[M44-LIVE-01] Success: Block prompt_to_cad published valid run_manifest.json on Solid Edge {diag.version_build}.",
        flush=True,
    )


def test_m44_live_02_example_plan_spur_gear_manifest(
    live_runtime: SolidEdgeRuntime,
    tmp_path: Path,
) -> None:
    """M44-LIVE-02: example_plan publishes valid run_manifest.json with null prompt_sha256."""
    req_id = "req-m44-live-02"
    print("\n[M44-LIVE-02] Testing example_plan with run manifest...", flush=True)

    gear_payload = {
        "plan_version": "cad_copilot.single_part_feature_plan.v1",
        "request_id": req_id,
        "units": "mm",
        "part": {
            "part_id": "part.live.m44_gear",
            "design_intent": "conceptual spur gear for manifest test",
            "scope": "single_part",
        },
        "base_body": {
            "id": "body.gear",
            "family": "spur_gear",
            "dimensions_mm": {
                "tooth_count": 24,
                "module_mm": 2.0,
                "face_width_mm": 15.0,
                "pressure_angle_deg": 20.0,
                "bore_diameter_mm": 12.0,
            },
        },
        "primitive_bodies": [],
        "boolean_operations": [],
        "features": [],
    }

    def gear_resolver(req: ExampleGenerationRequest) -> PlanProposal:
        return PlanProposal(
            plan_payload=gear_payload,
            provenance="example_plan",
            source_id="spur_gear_concept",
        )

    captured_diagnostics: list[Any] = []

    def spy_executor_factory(runtime: Any, doc: Any) -> SolidEdgeExecutor:
        captured_diagnostics.append(runtime.get_diagnostics())
        return SolidEdgeExecutor(runtime, doc)

    service = GenerationService(
        example_resolver=gear_resolver,
        runtime_factory=lambda: live_runtime,
        executor_factory=spy_executor_factory,
    )

    req = ExampleGenerationRequest(
        contract_version="1.0",
        request_id=req_id,
        kind="example_plan",
        unit="mm",
        example_id="spur_gear",
    )
    resp = service.generate(req, output_root=tmp_path)
    assert resp["status"] == "accepted"
    assert resp["request_id"] == req_id
    assert "warnings" in resp

    assert len(captured_diagnostics) == 1
    diag = captured_diagnostics[0]
    assert diag.version_build is not None and len(diag.version_build.strip()) > 0

    artifacts = resp["data"]["artifacts"]
    assert len(artifacts) in (3, 4)
    for record in artifacts:
        assert not record["path"].endswith("run_manifest.json")

    # Staging directory must NOT remain
    staging_candidates = [p for p in tmp_path.iterdir() if p.name.startswith(".staging_")]
    assert len(staging_candidates) == 0, f"Staging directory leaked: {staging_candidates}"

    # Published directory verification
    final_dir = tmp_path / req_id
    assert final_dir.is_dir()
    manifest_path = final_dir / "run_manifest.json"
    assert manifest_path.is_file()

    # Exact on-disk inventory
    published_files = list(final_dir.iterdir())
    assert len(published_files) == len(artifacts) + 1

    # Validate published manifest against canonical Draft 2020-12 schema
    manifest_bytes = manifest_path.read_bytes()
    manifest_data = json.loads(manifest_bytes.decode("utf-8"))
    validator = get_run_manifest_validator()
    validator.validate(manifest_data)

    # Core manifest fields & prompt_sha256 is null for example_plan
    assert manifest_data["manifest_version"] == "cad_copilot.run_manifest.v1"
    assert manifest_data["request"]["request_id"] == req_id
    assert manifest_data["request"]["kind"] == "example_plan"
    assert manifest_data["provenance"]["kind"] == "example_plan"
    assert manifest_data["provenance"]["source_id"] == "spur_gear_concept"
    assert manifest_data["engine"]["name"] == "cad-copilot"
    assert manifest_data["cad_runtime"]["product"] == "solid_edge"
    assert manifest_data["cad_runtime"]["version_build"] == diag.version_build
    assert manifest_data["fingerprints"]["prompt_sha256"] is None
    assert manifest_data["feature_plan"]["base_body"]["family"] == "spur_gear"
    assert manifest_data["stable_ids"]["body_ids"] == ["body.gear"]

    # Artifact consistency: sizes and hashes match disk files exactly
    assert len(manifest_data["artifacts"]) == len(artifacts)
    for art in manifest_data["artifacts"]:
        art_rel_path = art["path"]
        assert art_rel_path in (f"{req_id}.par", f"{req_id}.step", f"{req_id}.stl", f"{req_id}.jpg")
        art_disk_path = final_dir / art_rel_path
        assert art_disk_path.is_file()
        actual_sha256, actual_size = compute_file_sha256_and_size(art_disk_path)
        assert art["sha256"] == actual_sha256
        assert art["size_bytes"] == actual_size

    # Inspection consistency: single solid body with positive volume
    inspection = manifest_data["execution"]["inspection"]
    assert inspection["body_count"] == 1
    assert inspection["solid_body_count"] == 1
    assert inspection["volume_mm3"] > 0.0

    print(
        f"[M44-LIVE-02] Success: Spur gear example_plan published valid run_manifest.json on Solid Edge {diag.version_build}.",
        flush=True,
    )


def test_m43_live_example_catalog_spur_gear(
    live_runtime: SolidEdgeRuntime,
    tmp_path: Path,
) -> None:
    """[M4.3-LIVE-01] Execute spur_gear example catalog plan on live Solid Edge runtime.

    Proves:
        1. GenerationService resolves ExampleGenerationRequest("spur_gear") via production resolve_example_plan.
        2. Real Solid Edge Part document is created, constructed with gear outline and center bore, and closed.
        3. Native inspection reports exactly 1 solid body with positive volume.
        4. .par, STEP, STL, and optional JPG artifacts are published and structurally validated.
        5. run_manifest.json is published with provenance.kind="example_plan", source_id="spur_gear",
           and prompt_sha256=null.
        6. No prompt, credential, workstation path, or placeholder leaked.
    """
    req_id = "live_m43_spur_gear"
    captured_diagnostics: list[Any] = []

    def spy_executor_factory(runtime: Any, doc: Any) -> SolidEdgeExecutor:
        captured_diagnostics.append(runtime.get_diagnostics())
        return SolidEdgeExecutor(runtime, doc)

    service = GenerationService(
        example_resolver=resolve_example_plan,
        runtime_factory=lambda: live_runtime,
        executor_factory=spy_executor_factory,
    )

    req = ExampleGenerationRequest(
        contract_version="1.0",
        request_id=req_id,
        kind="example_plan",
        unit="mm",
        example_id="spur_gear",
    )
    resp = service.generate(req, output_root=tmp_path)
    assert resp["status"] == "accepted"
    assert resp["request_id"] == req_id
    assert "warnings" in resp

    assert len(captured_diagnostics) == 1
    diag = captured_diagnostics[0]
    assert diag.version_build is not None and len(diag.version_build.strip()) > 0

    artifacts = resp["data"]["artifacts"]
    assert len(artifacts) in (3, 4)
    for record in artifacts:
        assert not record["path"].endswith("run_manifest.json")

    # Staging directory must NOT remain
    staging_candidates = [p for p in tmp_path.iterdir() if p.name.startswith(".staging_")]
    assert len(staging_candidates) == 0, f"Staging directory leaked: {staging_candidates}"

    # Published directory verification
    final_dir = tmp_path / req_id
    assert final_dir.is_dir()
    manifest_path = final_dir / "run_manifest.json"
    assert manifest_path.is_file()

    # Exact on-disk inventory: model artifacts + run_manifest.json
    published_files = list(final_dir.iterdir())
    assert len(published_files) == len(artifacts) + 1

    # Validate published manifest against canonical schema
    manifest_bytes = manifest_path.read_bytes()
    manifest_data = json.loads(manifest_bytes.decode("utf-8"))
    validator = get_run_manifest_validator()
    validator.validate(manifest_data)

    # Core manifest fields
    assert manifest_data["manifest_version"] == "cad_copilot.run_manifest.v1"
    assert manifest_data["request"]["request_id"] == req_id
    assert manifest_data["request"]["kind"] == "example_plan"
    assert manifest_data["provenance"]["kind"] == "example_plan"
    assert manifest_data["provenance"]["source_id"] == "spur_gear"
    assert manifest_data["engine"]["name"] == "cad-copilot"
    assert manifest_data["cad_runtime"]["product"] == "solid_edge"
    assert manifest_data["cad_runtime"]["version_build"] == diag.version_build
    assert manifest_data["fingerprints"]["prompt_sha256"] is None
    assert manifest_data["feature_plan"]["base_body"]["family"] == "spur_gear"
    assert manifest_data["feature_plan"]["base_body"]["dimensions_mm"]["tooth_count"] == 24
    assert manifest_data["feature_plan"]["base_body"]["dimensions_mm"]["module_mm"] == 2.0

    # Independently recompute and verify plan fingerprint from feature_plan
    assert manifest_data["fingerprints"]["plan_sha256"] == compute_plan_fingerprint(manifest_data["feature_plan"])

    # Independently assert expected stable IDs from spur gear catalog template
    assert manifest_data["stable_ids"]["part_id"] == "part.main"
    assert manifest_data["stable_ids"]["body_ids"] == ["body.main"]
    assert manifest_data["stable_ids"]["feature_ids"] == []

    # Privacy checks: placeholder request ID, credentials, and local paths not leaked
    manifest_json = json.dumps(manifest_data)
    assert "replace-with-request-id" not in manifest_json
    assert not FREE_TEXT_CREDENTIAL_PATTERN.search(manifest_json)
    assert not LOCAL_PATH_PATTERN.search(manifest_json)

    # Response privacy check: exclude intentional artifact destination paths
    response_non_artifact_payload = {k: v for k, v in resp.items() if k != "data"}
    assert not LOCAL_PATH_PATTERN.search(json.dumps(response_non_artifact_payload))
    assert not FREE_TEXT_CREDENTIAL_PATTERN.search(json.dumps(resp))

    # Artifact consistency: sizes and hashes match disk files exactly
    assert len(manifest_data["artifacts"]) == len(artifacts)
    for art in manifest_data["artifacts"]:
        art_rel_path = art["path"]
        assert art_rel_path in (f"{req_id}.par", f"{req_id}.step", f"{req_id}.stl", f"{req_id}.jpg")
        art_disk_path = final_dir / art_rel_path
        assert art_disk_path.is_file()
        actual_sha256, actual_size = compute_file_sha256_and_size(art_disk_path)
        assert art["sha256"] == actual_sha256
        assert art["size_bytes"] == actual_size

    # Inspection consistency: single solid body with positive finite volume
    inspection = manifest_data["execution"]["inspection"]
    assert inspection["body_count"] == 1
    assert inspection["solid_body_count"] == 1
    assert inspection["volume_mm3"] > 0.0

    # Structural validation of published model artifacts
    rep = StandardInspectionReport(
        volume_mm3=float(inspection["volume_mm3"]),
        mass_kg=float(inspection["mass_kg"]),
        feature_count=int(inspection["feature_count"]),
        body_count=int(inspection["body_count"]),
        solid_body_count=int(inspection["solid_body_count"]),
    )
    validate_par_artifact(final_dir / f"{req_id}.par", rep)
    validate_step_artifact(final_dir / f"{req_id}.step")
    validate_stl_artifact(final_dir / f"{req_id}.stl")
    jpg_path = final_dir / f"{req_id}.jpg"
    if jpg_path.is_file():
        validate_jpg_artifact(jpg_path)

    print(
        f"[M43-LIVE-01] Success: Deterministic spur gear catalog generated valid part and manifest on Solid Edge {diag.version_build}.",
        flush=True,
    )
