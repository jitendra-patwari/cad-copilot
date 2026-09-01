"""Controlled live integration tests against a real Siemens Solid Edge installation.

These tests execute ONLY when running with `pytest -m com` on a Windows host with
a licensed Siemens Solid Edge installation.
"""

from __future__ import annotations

import math
import sys
import time
from collections.abc import Generator
from typing import Any

import pytest

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
from drivers.solidedge.executor import SolidEdgeExecutor
from drivers.solidedge.units import m3_to_mm3
from geometry.plan_models import (
    BodyPlacement,
    CircularThroughHoleFeature,
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
from interfaces.exceptions import CADExecutionError
from interfaces.models import ExecutionFailure, ExecutionSuccess

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
