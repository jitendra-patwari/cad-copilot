"""Unit tests and fake-COM interaction tests for SolidEdgeExecutor."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from drivers.solidedge.constants import (
    BODY_TYPE_CURVE,
    BODY_TYPE_SHEET,
    BODY_TYPE_SOLID,
    FEATURE_STATUS_OK,
    PROFILE_STATUS_OK,
)
from drivers.solidedge.executor import SolidEdgeExecutor
from drivers.solidedge.runtime import SolidEdgeRuntime
from drivers.solidedge.types import SolidEdgePartDocumentHandle
from geometry.plan_models import (
    CircularThroughHoleFeature,
    CylinderBaseBody,
    FeaturePlan,
    PartMetadata,
    ProfileCutoutFeature,
    RectangularBaseBody,
    RectangularExtrudedPadFeature,
    SlotThroughCutoutFeature,
    SpurGearBaseBody,
)
from interfaces.exceptions import CADDocumentError, CADExecutionError
from interfaces.models import (
    ExecutionFailure,
    ExecutionSuccess,
    PhysicalProperties,
    StandardInspectionReport,
)


def _make_plan(
    *,
    base_body: RectangularBaseBody | CylinderBaseBody | SpurGearBaseBody,
    features: tuple[Any, ...] = (),
    design_intent: str = "test part",
) -> FeaturePlan:
    return FeaturePlan(
        plan_version="cad_copilot.single_part_feature_plan.v1",
        request_id="req-test-123",
        part=PartMetadata(part_id="part.main", design_intent=design_intent),
        base_body=base_body,
        primitive_bodies=(base_body,),
        features=features,
    )


# ---------------------------------------------------------------------------
# 1. Fake-COM Mock Framework for End-to-End Execution
# ---------------------------------------------------------------------------


class MockWorkerForExecutor:
    """Mock worker thread providing transparent COM invocation and tracking document state."""

    def __init__(self) -> None:
        self.call_log: list[str] = []
        self._document_registry: dict[str, Any] = {}
        self.is_poisoned: bool = False
        self.call_raises: Exception | None = None

    def is_alive(self) -> bool:
        return True

    def call(self, func: Any, timeout: float | None = None, **kwargs: Any) -> Any:
        if self.call_raises is not None:
            raise self.call_raises
        return func()

    def _invoke_com(self, func: Any, **kwargs: Any) -> Any:
        return func()


class FakeCOMFeature:
    def __init__(self, status: int = FEATURE_STATUS_OK) -> None:
        self.Status: int = status


class FakeCOMFeatures:
    def __init__(self, worker: MockWorkerForExecutor, initial_features: list[FakeCOMFeature] | None = None) -> None:
        self.worker = worker
        self.features: list[FakeCOMFeature] = initial_features if initial_features is not None else []

    @property
    def Count(self) -> int:
        return len(self.features)

    def Item(self, index: int) -> FakeCOMFeature:
        return self.features[index - 1]


class FakeCOMExtrudedCutouts:
    def __init__(self, worker: MockWorkerForExecutor, model: FakeCOMModel | None = None) -> None:
        self.worker = worker
        self.model = model
        self.cutouts: list[FakeCOMFeature] = []

    @property
    def Count(self) -> int:
        return len(self.cutouts)

    def Item(self, index: int) -> FakeCOMFeature:
        return self.cutouts[index - 1]

    def AddThroughAll(self, profile: Any, profile_side: int, profile_plane_side: int) -> FakeCOMFeature:
        self.worker.call_log.append(
            f"ExtrudedCutouts.AddThroughAll(side={profile_side}, plane_side={profile_plane_side})"
        )
        feat = FakeCOMFeature()
        self.cutouts.append(feat)
        if self.model is not None:
            self.model.Features.features.append(feat)
        return feat

    def AddFinite(self, profile: Any, profile_side: int, profile_plane_side: int, depth_m: float) -> FakeCOMFeature:
        self.worker.call_log.append(
            f"ExtrudedCutouts.AddFinite(side={profile_side}, plane_side={profile_plane_side}, depth={depth_m:.6f})"
        )
        feat = FakeCOMFeature()
        self.cutouts.append(feat)
        if self.model is not None:
            self.model.Features.features.append(feat)
        return feat


class FakeCOMExtrudedProtrusions:
    def __init__(self, worker: MockWorkerForExecutor, model: FakeCOMModel | None = None) -> None:
        self.worker = worker
        self.model = model
        self.protrusions: list[FakeCOMFeature] = [FakeCOMFeature()]

    @property
    def Count(self) -> int:
        return len(self.protrusions)

    def Item(self, index: int) -> FakeCOMFeature:
        return self.protrusions[index - 1]

    def AddFinite(self, profile: Any, profile_side: int, profile_plane_side: int, height_m: float) -> FakeCOMFeature:
        self.worker.call_log.append(
            f"ExtrudedProtrusions.AddFinite(side={profile_side}, plane_side={profile_plane_side}, height={height_m:.6f})"
        )
        feat = FakeCOMFeature()
        self.protrusions.append(feat)
        if self.model is not None:
            self.model.Features.features.append(feat)
        return feat


class FakeCOMBody:
    def __init__(self, volume_m3: float = 0.00016, is_solid: bool = True, body_type: int = BODY_TYPE_SOLID) -> None:
        self.Volume: float = volume_m3
        self.IsSolid: bool = is_solid
        self.Type: int = body_type
        self.Density: float = 7850.0


class FakeCOMModel:
    def __init__(self, worker: MockWorkerForExecutor, volume_m3: float = 0.00016) -> None:
        self.worker = worker
        self.Features = FakeCOMFeatures(worker)
        self.ExtrudedCutouts = FakeCOMExtrudedCutouts(worker, self)
        self.ExtrudedProtrusions = FakeCOMExtrudedProtrusions(worker, self)
        self.Features.features.append(self.ExtrudedProtrusions.protrusions[0])
        self.Body = FakeCOMBody(volume_m3=volume_m3, is_solid=True, body_type=BODY_TYPE_SOLID)

    def ComputePhysicalProperties(self, density: float, accuracy: float) -> tuple[Any, ...]:
        self.worker.call_log.append(f"Model.ComputePhysicalProperties(density={density:.6f}, accuracy={accuracy:.6f})")
        if density < 0.0:
            return (0.0, 0.0, 0.0, None, None, None, None, None, None, 0.0, -1)
        vol = self.Body.Volume
        area = 0.02
        mass = vol * density if density > 0.0 else 0.0
        cog = (0.0, 0.0, 0.0)
        cov = (0.0, 0.0, 0.0)
        moments = (0.0, 0.0, 0.0)
        gyration = (0.0, 0.0, 0.0)
        axes = (0.0, 0.0, 0.0)
        radii = (0.0, 0.0, 0.0)
        rel_acc = accuracy
        status = 1  # sePhysicalPropertiesStatus_Model
        return (vol, area, mass, cog, cov, moments, gyration, axes, radii, rel_acc, status)


class FakeCOMModels:
    def __init__(self, worker: MockWorkerForExecutor) -> None:
        self.worker = worker
        self.models: list[FakeCOMModel] = []
        self.default_volume_m3: float = 0.00016

    @property
    def Count(self) -> int:
        return len(self.models)

    def Item(self, index: int) -> FakeCOMModel:
        return self.models[index - 1]

    def AddFiniteExtrudedProtrusion(
        self, num_profiles: int, profiles: list[Any], side: int, distance_m: float
    ) -> FakeCOMModel:
        self.worker.call_log.append(
            f"Models.AddFiniteExtrudedProtrusion({num_profiles}, side={side}, dist={distance_m:.6f})"
        )
        model = FakeCOMModel(self.worker, volume_m3=self.default_volume_m3)
        self.models.append(model)
        return model


class FakeCOMRelations2d:
    def __init__(self, worker: MockWorkerForExecutor) -> None:
        self.worker = worker

    def AddKeypoint(self, obj1: Any, kp1: int, obj2: Any, kp2: int, guaranteed_ok: bool = False) -> MagicMock:
        self.worker.call_log.append(f"Relations2d.AddKeypoint({kp1}->{kp2}, ok={guaranteed_ok})")
        return MagicMock()


class FakeCOMLines2d:
    def __init__(self, worker: MockWorkerForExecutor) -> None:
        self.worker = worker

    def AddBy2Points(self, x1: float, y1: float, x2: float, y2: float) -> MagicMock:
        self.worker.call_log.append(f"Lines2d.AddBy2Points({x1:.6f}, {y1:.6f}, {x2:.6f}, {y2:.6f})")
        return MagicMock()


class FakeCOMCircles2d:
    def __init__(self, worker: MockWorkerForExecutor) -> None:
        self.worker = worker

    def AddByCenterRadius(self, cx: float, cy: float, r: float) -> MagicMock:
        self.worker.call_log.append(f"Circles2d.AddByCenterRadius({cx:.6f}, {cy:.6f}, {r:.6f})")
        return MagicMock()


class FakeCOMArcs2d:
    def __init__(self, worker: MockWorkerForExecutor) -> None:
        self.worker = worker

    def AddByCenterStartEnd(self, cx: float, cy: float, sx: float, sy: float, ex: float, ey: float) -> MagicMock:
        self.worker.call_log.append(
            f"Arcs2d.AddByCenterStartEnd({cx:.6f}, {cy:.6f}, {sx:.6f}, {sy:.6f}, {ex:.6f}, {ey:.6f})"
        )
        return MagicMock()


class FakeCOMProfile:
    def __init__(self, plane: Any, worker: MockWorkerForExecutor) -> None:
        self.plane = plane
        self.worker = worker
        self.Lines2d = FakeCOMLines2d(worker)
        self.Circles2d = FakeCOMCircles2d(worker)
        self.Arcs2d = FakeCOMArcs2d(worker)
        self.Relations2d = FakeCOMRelations2d(worker)

    def End(self, mode: int) -> int:
        self.worker.call_log.append(f"Profile.End({mode})")
        return PROFILE_STATUS_OK


class FakeCOMProfileSet:
    def __init__(self, worker: MockWorkerForExecutor) -> None:
        self.worker = worker

    @property
    def Profiles(self) -> Any:
        class _Profiles:
            def __init__(self, pset: FakeCOMProfileSet) -> None:
                self.pset = pset

            def Add(self, plane: Any) -> FakeCOMProfile:
                self.pset.worker.call_log.append("Profiles.Add()")
                return FakeCOMProfile(plane, self.pset.worker)

        return _Profiles(self)


class FakeCOMProfileSets:
    def __init__(self, worker: MockWorkerForExecutor) -> None:
        self.worker = worker

    def Add(self) -> FakeCOMProfileSet:
        self.worker.call_log.append("ProfileSets.Add()")
        return FakeCOMProfileSet(self.worker)


class FakeCOMRefPlanes:
    def __init__(self, worker: MockWorkerForExecutor) -> None:
        self.worker = worker
        self.base = {1: MagicMock(name="XY"), 2: MagicMock(name="YZ"), 3: MagicMock(name="XZ")}

    def Item(self, index: int) -> Any:
        self.worker.call_log.append(f"RefPlanes.Item({index})")
        return self.base[index]

    def AddParallelByDistance(self, parent: Any, dist_m: float, side: int) -> MagicMock:
        self.worker.call_log.append(f"RefPlanes.AddParallelByDistance({dist_m:.6f}, {side})")
        return MagicMock(name=f"Parallel_{dist_m}_{side}")


class FakeCOMConstructionModel:
    def __init__(self, worker: MockWorkerForExecutor, body: FakeCOMBody | None = None) -> None:
        self.worker = worker
        self.Body = body or FakeCOMBody(is_solid=False, body_type=BODY_TYPE_SHEET)


class FakeCOMConstructions:
    def __init__(self, worker: MockWorkerForExecutor) -> None:
        self.worker = worker
        self.items: list[FakeCOMConstructionModel] = []

    @property
    def Count(self) -> int:
        return len(self.items)

    def Item(self, index: int) -> FakeCOMConstructionModel:
        return self.items[index - 1]


class FakeCOMPartDocument:
    def __init__(self, worker: MockWorkerForExecutor) -> None:
        self.worker = worker
        self.RefPlanes = FakeCOMRefPlanes(worker)
        self.ProfileSets = FakeCOMProfileSets(worker)
        self.Models = FakeCOMModels(worker)
        self.Constructions = FakeCOMConstructions(worker)
        self.recompute_should_fail: bool = False
        self.close_should_fail: bool = False
        self.closed: bool = False

    def Close(self, save_changes: bool = False) -> None:
        self.worker.call_log.append(f"PartDocument.Close(save_changes={save_changes})")
        if self.close_should_fail:
            raise RuntimeError("Simulated kernel close error")
        self.closed = True

    def Recompute(self) -> None:
        self.worker.call_log.append("PartDocument.Recompute()")
        if self.recompute_should_fail:
            raise RuntimeError("Recompute failure in CAD kernel")

    def GetGlobalParameter(self, param_id: int, default_val: Any = None) -> float:
        self.worker.call_log.append(f"PartDocument.GetGlobalParameter({param_id})")
        if param_id == 1:
            return 0.0  # kg/m3 (default unassigned)
        if param_id == 2:
            return 0.0001  # fractional accuracy (Double)
        return 0.0


@pytest.fixture
def mock_runtime_and_handle() -> tuple[
    SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
]:
    runtime = SolidEdgeRuntime()
    worker = MockWorkerForExecutor()
    runtime._worker = worker  # type: ignore[assignment]

    doc_handle = SolidEdgePartDocumentHandle(handle_id="part_doc_test_123")
    raw_doc = FakeCOMPartDocument(worker)
    worker._document_registry[doc_handle.handle_id] = raw_doc
    runtime._open_document_handles[doc_handle.handle_id] = doc_handle

    return runtime, doc_handle, raw_doc, worker


# ---------------------------------------------------------------------------
# 2. Out-of-Scope Immediate Rejection Tests
# ---------------------------------------------------------------------------


def test_executor_out_of_scope_methods_raise_immediate_unsupported(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
) -> None:
    """Proves all out-of-scope methods immediately raise UNSUPPORTED_EXECUTION_OPERATION without COM calls."""
    runtime, doc_handle, _, worker = mock_runtime_and_handle
    executor = SolidEdgeExecutor(runtime, doc_handle)
    initial_log_len = len(worker.call_log)

    methods_to_test = [
        lambda: executor.create_prism_body(10.0, 10.0, 10.0),
        lambda: executor.add_cylindrical_cutout(5.0),
        lambda: executor.generate_flat_pattern(Path("flat.dxf")),
        lambda: executor.generate_draft(Path("draft.dft")),
        lambda: executor.publish_drawing(Path("draw.pdf")),
        lambda: executor.read_custom_properties(),
        lambda: executor.write_custom_properties({"author": "test"}),
    ]

    for meth in methods_to_test:
        with pytest.raises(CADExecutionError) as exc_info:
            meth()
        assert exc_info.value.error_code == "UNSUPPORTED_EXECUTION_OPERATION"

    # Zero COM calls made
    assert len(worker.call_log) == initial_log_len


def test_executor_close_request_document_closes_handle_and_is_idempotent(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
) -> None:
    """Proves close_request_document delegates to runtime.close_document and is safe/idempotent on multiple calls."""
    runtime, doc_handle, _, _ = mock_runtime_and_handle
    executor = SolidEdgeExecutor(runtime, doc_handle)

    assert executor._doc_handle is not None
    assert doc_handle.handle_id in runtime._open_document_handles

    # First close: releases handle from runtime and sets _doc_handle to None
    executor.close_request_document()
    assert executor._doc_handle is None
    assert doc_handle.handle_id not in runtime._open_document_handles

    # Second close: safe idempotent no-op
    executor.close_request_document()
    assert executor._doc_handle is None


def test_executor_close_request_document_retains_tracking_on_ordinary_close_failure(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
) -> None:
    """Proves ordinary close failure preserves runtime tracking for teardown retry and does not poison runtime."""
    runtime, doc_handle, fake_doc, worker = mock_runtime_and_handle
    executor = SolidEdgeExecutor(runtime, doc_handle)
    fake_doc.close_should_fail = True

    # Attempt close: raises RuntimeError from kernel Close
    with pytest.raises(RuntimeError, match="Simulated kernel close error"):
        executor.close_request_document()

    # Executor handle consumed to prevent double dispatch
    assert executor._doc_handle is None
    # Runtime still tracks handle for teardown retry
    assert doc_handle.handle_id in runtime._open_document_handles
    # Runtime is NOT poisoned on ordinary close error
    assert runtime._is_poisoned is False

    # Second close attempt must raise CADDocumentError and not re-enter COM
    initial_log_len = len(worker.call_log)
    with pytest.raises(CADDocumentError) as exc_info:
        executor.close_request_document()
    assert exc_info.value.error_code == "DOCUMENT_CLOSE_FAILED"
    assert len(worker.call_log) == initial_log_len


def test_executor_close_request_document_poisons_runtime_on_timeout(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
) -> None:
    """Proves timeout during close sets runtime poison flag, retains tracking, and prevents COM re-entry."""
    runtime, doc_handle, _, worker = mock_runtime_and_handle
    executor = SolidEdgeExecutor(runtime, doc_handle)
    worker.call_raises = TimeoutError("Simulated STA close hang")

    # Attempt close: raises TimeoutError
    with pytest.raises(TimeoutError, match="Simulated STA close hang"):
        executor.close_request_document()

    # Executor handle consumed
    assert executor._doc_handle is None
    # Runtime tracking retained
    assert doc_handle.handle_id in runtime._open_document_handles
    # Runtime IS poisoned on timeout
    assert runtime._is_poisoned is True

    # Second close attempt raises CADDocumentError without re-invoking worker
    initial_log_len = len(worker.call_log)
    with pytest.raises(CADDocumentError) as exc_info:
        executor.close_request_document()
    assert exc_info.value.error_code == "DOCUMENT_CLOSE_FAILED"
    assert len(worker.call_log) == initial_log_len


def test_executor_require_doc_handle_reports_close_failed_when_close_failed(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
) -> None:
    """Proves that _require_doc_handle() reports DOCUMENT_CLOSE_FAILED if document closure previously failed."""
    runtime, doc_handle, _, worker = mock_runtime_and_handle
    executor = SolidEdgeExecutor(runtime, doc_handle)
    worker.call_raises = RuntimeError("Simulated ordinary close error")

    with pytest.raises(RuntimeError):
        executor.close_request_document()

    with pytest.raises(CADDocumentError) as exc_info:
        executor._require_doc_handle()
    assert exc_info.value.error_code == "DOCUMENT_CLOSE_FAILED"


def test_executor_execute_feature_plan_preserves_cad_error_code(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
) -> None:
    """Proves that execute_feature_plan preserves domain CADError error codes rather than masking as NATIVE_COM_ERROR."""
    runtime, doc_handle, _, _ = mock_runtime_and_handle
    executor = SolidEdgeExecutor(runtime, doc_handle)
    executor._close_failed = True  # Simulate failed close state

    plan: dict[str, Any] = {
        "version": "1.0",
        "base_body": {
            "type": "box",
            "dimensions": {"length_mm": 50.0, "width_mm": 30.0, "height_mm": 10.0},
        },
    }
    result = executor.execute_feature_plan(plan)
    assert isinstance(result, ExecutionFailure)
    assert result.details.get("error_code") == "DOCUMENT_CLOSE_FAILED"


def test_executor_export_model_delegates_to_run_document_task(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
    tmp_path: Path,
) -> None:
    """Proves that export_model delegates to runtime.run_document_task with bound handle."""
    runtime, doc_handle, fake_doc, worker = mock_runtime_and_handle
    executor = SolidEdgeExecutor(runtime, doc_handle)
    out_file = tmp_path / "model.par"

    with patch("drivers.solidedge.executor.export_model_to_path", return_value=None) as mock_export:
        executor.export_model("par", out_file)
        mock_export.assert_called_once_with(fake_doc, worker, "par", out_file)


def test_executor_capture_preview_delegates_to_run_document_task(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
    tmp_path: Path,
) -> None:
    """Proves that capture_preview delegates to runtime.run_document_task with bound handle."""
    runtime, doc_handle, fake_doc, worker = mock_runtime_and_handle
    executor = SolidEdgeExecutor(runtime, doc_handle)
    out_file = tmp_path / "preview.jpg"

    with patch("drivers.solidedge.executor.capture_preview_image", return_value=None) as mock_preview:
        executor.capture_preview(out_file, width=1024, height=768)
        mock_preview.assert_called_once_with(fake_doc, worker, out_file, width=1024, height=768)


def test_executor_export_model_forwards_configured_timeout(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
    tmp_path: Path,
) -> None:
    """Proves export_model forwards executor timeout_seconds to run_document_task."""
    runtime, doc_handle, _, _ = mock_runtime_and_handle
    executor = SolidEdgeExecutor(runtime, doc_handle, timeout_seconds=42.0)
    out_file = tmp_path / "model.step"

    with patch.object(runtime, "run_document_task") as mock_task:
        executor.export_model("step", out_file)
        mock_task.assert_called_once()
        assert mock_task.call_args.kwargs.get("timeout") == 42.0


def test_executor_capture_preview_forwards_configured_timeout(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
    tmp_path: Path,
) -> None:
    """Proves capture_preview forwards executor timeout_seconds to run_document_task."""
    runtime, doc_handle, _, _ = mock_runtime_and_handle
    executor = SolidEdgeExecutor(runtime, doc_handle, timeout_seconds=42.0)
    out_file = tmp_path / "preview.jpg"

    with patch.object(runtime, "run_document_task") as mock_task:
        executor.capture_preview(out_file)
        mock_task.assert_called_once()
        assert mock_task.call_args.kwargs.get("timeout") == 42.0


def test_executor_export_model_refuses_when_doc_handle_is_none(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
    tmp_path: Path,
) -> None:
    """Proves export_model raises NO_ACTIVE_DOCUMENT when handle is None (e.g. after close)."""
    runtime, doc_handle, _, _ = mock_runtime_and_handle
    executor = SolidEdgeExecutor(runtime, doc_handle)
    executor.close_request_document()

    with pytest.raises(CADDocumentError) as exc_info:
        executor.export_model("par", tmp_path / "model.par")
    assert exc_info.value.error_code == "NO_ACTIVE_DOCUMENT"


def test_executor_export_model_refuses_when_close_failed(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
    tmp_path: Path,
) -> None:
    """Proves export_model raises DOCUMENT_CLOSE_FAILED when previous close failed."""
    runtime, doc_handle, _, _ = mock_runtime_and_handle
    executor = SolidEdgeExecutor(runtime, doc_handle)
    executor._close_failed = True

    with pytest.raises(CADDocumentError) as exc_info:
        executor.export_model("step", tmp_path / "model.step")
    assert exc_info.value.error_code == "DOCUMENT_CLOSE_FAILED"


def test_executor_capture_preview_refuses_when_doc_handle_is_none(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
    tmp_path: Path,
) -> None:
    """Proves capture_preview raises NO_ACTIVE_DOCUMENT when handle is None."""
    runtime, doc_handle, _, _ = mock_runtime_and_handle
    executor = SolidEdgeExecutor(runtime, doc_handle)
    executor.close_request_document()

    with pytest.raises(CADDocumentError) as exc_info:
        executor.capture_preview(tmp_path / "preview.jpg")
    assert exc_info.value.error_code == "NO_ACTIVE_DOCUMENT"


def test_executor_capture_preview_refuses_when_close_failed(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
    tmp_path: Path,
) -> None:
    """Proves capture_preview raises DOCUMENT_CLOSE_FAILED when previous close failed."""
    runtime, doc_handle, _, _ = mock_runtime_and_handle
    executor = SolidEdgeExecutor(runtime, doc_handle)
    executor._close_failed = True

    with pytest.raises(CADDocumentError) as exc_info:
        executor.capture_preview(tmp_path / "preview.jpg")
    assert exc_info.value.error_code == "DOCUMENT_CLOSE_FAILED"


# ---------------------------------------------------------------------------
# 3. Plan Preflight & Early Rejection Tests
# ---------------------------------------------------------------------------


def test_executor_preflight_rejects_empty_and_invalid_first_patch(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
) -> None:
    """Proves executor preflight rejects empty patches or non-ensure_primitive_body first patch."""
    runtime, doc_handle, _, _ = mock_runtime_and_handle
    executor = SolidEdgeExecutor(runtime, doc_handle)

    # 1. Invalid patch order (cut_hole as first patch)
    bad_patches: list[dict[str, Any]] = [
        {
            "op": "cut_hole",
            "patch_id": "p0",
            "target_body_ref": "b0",
            "profile_ref": "prof0",
            "result_ref": "cut0",
            "face": "+Z",
            "extent": {"type": "through_all"},
        }
    ]
    res = executor._preflight_patch_sequence(bad_patches)
    assert res is not None
    assert res[1] == "INVALID_PATCH_ORDER"


def test_executor_preflight_rejects_unpromoted_capability_rows(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
) -> None:
    """Proves preflight rejects unpromoted cut/pad capability rows before COM dispatch."""
    runtime, doc_handle, _, _ = mock_runtime_and_handle
    executor = SolidEdgeExecutor(runtime, doc_handle)

    # Cut with unpromoted -X finite cut
    unpromoted_cut_patches: list[dict[str, Any]] = [
        {
            "op": "ensure_primitive_body",
            "patch_id": "p0",
            "body_ref": "body.main",
            "shape": {"type": "cuboid", "length_mm": 50.0, "width_mm": 50.0, "height_mm": 20.0},
        },
        {
            "op": "ensure_sketch",
            "patch_id": "p1",
            "sketch_ref": "sk0",
            "body_ref": "body.main",
            "plane": "YZ",
            "offset_mm": 50.0,
        },
        {
            "op": "ensure_profile",
            "patch_id": "p2",
            "profile_ref": "prof0",
            "sketch_ref": "sk0",
            "profile": {"type": "circle", "radius_mm": 5.0},
        },
        {
            "op": "cut_hole",
            "patch_id": "p3",
            "target_body_ref": "body.main",
            "profile_ref": "prof0",
            "result_ref": "cut0",
            "face": "-X",
            "extent": {"type": "finite", "depth_mm": 10.0},
            "cut_direction": "into_solid",
            "normal_vector": [-1.0, 0.0, 0.0],
            "u_axis": [0.0, 1.0, 0.0],
            "v_axis": [0.0, 0.0, -1.0],
            "cut_vector": [1.0, 0.0, 0.0],
        },
    ]
    res = executor._preflight_patch_sequence(unpromoted_cut_patches)
    assert res is not None
    assert res[1] == "UNSUPPORTED_EXECUTION_OPERATION"
    assert "Unsupported cut capability row" in res[0]


# ---------------------------------------------------------------------------
# 4. End-to-End Execution Tests with Fake COM
# ---------------------------------------------------------------------------


def test_executor_executes_prism_with_through_cut(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
) -> None:
    """Proves end-to-end execution of a cuboid with +Z through-all hole returning ExecutionSuccess."""
    runtime, doc_handle, _, worker = mock_runtime_and_handle
    executor = SolidEdgeExecutor(runtime, doc_handle)

    plan = _make_plan(
        base_body=RectangularBaseBody(
            id="body.main",
            length_mm=100.0,
            width_mm=80.0,
            thickness_mm=20.0,
        ),
        features=(
            CircularThroughHoleFeature(
                id="hole_1",
                diameter_mm=16.0,
                target_face="+Z",
            ),
        ),
    )

    result = executor.execute_feature_plan(plan)
    assert isinstance(result, ExecutionSuccess)
    assert result.operations_executed == 4
    assert len(result.operation_results) == 2

    # Verify operation results
    op0 = result.operation_results[0]
    assert op0.operation == "ensure_primitive_body"
    assert op0.reference_kind == "body"

    op1 = result.operation_results[1]
    assert op1.operation == "cut_hole"
    assert op1.reference_kind == "feature"

    # Verify inspection report
    assert isinstance(result.inspection_report, StandardInspectionReport)
    assert result.inspection_report.solid_body_count == 1
    assert result.inspection_report.sheet_body_count == 0
    assert result.inspection_report.wire_body_count == 0
    assert result.inspection_report.body_count == 1
    assert math.isclose(result.inspection_report.volume_mm3, 160_000.0)

    # Verify COM call trace
    assert "PartDocument.Recompute()" in worker.call_log
    assert "ExtrudedCutouts.AddThroughAll(side=1, plane_side=1)" in worker.call_log


def test_executor_executes_cylinder_with_through_hole(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
) -> None:
    """Proves end-to-end execution of a cylinder with +Z through-all hole."""
    runtime, doc_handle, _, worker = mock_runtime_and_handle
    executor = SolidEdgeExecutor(runtime, doc_handle)

    plan = _make_plan(
        base_body=CylinderBaseBody(
            id="body.main",
            radius_mm=25.0,
            height_mm=50.0,
        ),
        features=(
            CircularThroughHoleFeature(
                id="hole_1",
                diameter_mm=10.0,
                target_face="+Z",
            ),
        ),
    )

    result = executor.execute_feature_plan(plan)
    assert isinstance(result, ExecutionSuccess)
    assert result.operations_executed == 4
    assert len(result.operation_results) == 2
    assert "ExtrudedCutouts.AddThroughAll(side=1, plane_side=1)" in worker.call_log


def test_executor_executes_prism_with_rectangular_pad(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
) -> None:
    """Proves end-to-end execution of a cuboid with +Z rectangular pad."""
    runtime, doc_handle, _, worker = mock_runtime_and_handle
    executor = SolidEdgeExecutor(runtime, doc_handle)

    plan = _make_plan(
        base_body=RectangularBaseBody(
            id="body.main",
            length_mm=80.0,
            width_mm=80.0,
            thickness_mm=20.0,
        ),
        features=(
            RectangularExtrudedPadFeature(
                id="pad_1",
                target_face="+Z",
                width_mm=40.0,
                height_mm=40.0,
                distance_mm=15.0,
            ),
        ),
    )

    result = executor.execute_feature_plan(plan)
    assert isinstance(result, ExecutionSuccess)
    assert result.operations_executed == 4
    assert len(result.operation_results) == 2
    assert "ExtrudedProtrusions.AddFinite(side=1, plane_side=2, height=0.015000)" in worker.call_log


def test_executor_executes_spur_gear_with_centered_bore(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
) -> None:
    """Proves end-to-end execution of a conceptual spur gear with centered bore."""
    runtime, doc_handle, _, worker = mock_runtime_and_handle
    executor = SolidEdgeExecutor(runtime, doc_handle)

    plan = _make_plan(
        base_body=SpurGearBaseBody(
            id="body.main",
            tooth_count=12,
            module_mm=2.0,
            face_width_mm=15.0,
            pressure_angle_deg=20.0,
            bore_diameter_mm=6.0,
        ),
        features=(),
    )

    result = executor.execute_feature_plan(plan)
    assert isinstance(result, ExecutionSuccess)
    assert result.operations_executed == 4
    assert len(result.operation_results) == 2
    assert "ExtrudedCutouts.AddThroughAll(side=1, plane_side=1)" in worker.call_log


def test_executor_executes_sequential_part_preserving_order(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
) -> None:
    """Proves ordered sequential part with hole, slot, and pad preserves operation order and returns all records."""
    runtime, doc_handle, _, _ = mock_runtime_and_handle
    executor = SolidEdgeExecutor(runtime, doc_handle)

    plan = _make_plan(
        base_body=RectangularBaseBody(
            id="body.main",
            length_mm=100.0,
            width_mm=100.0,
            thickness_mm=50.0,
        ),
        features=(
            CircularThroughHoleFeature(id="hole_1", diameter_mm=10.0, target_face="+Z"),
            SlotThroughCutoutFeature(id="slot_1", length_mm=30.0, width_mm=10.0, target_face="+Z"),
            RectangularExtrudedPadFeature(
                id="pad_1", width_mm=20.0, height_mm=20.0, distance_mm=10.0, target_face="+Z"
            ),
        ),
    )

    result = executor.execute_feature_plan(plan)
    assert isinstance(result, ExecutionSuccess)
    assert result.operations_executed == 10
    assert len(result.operation_results) == 4

    assert result.operation_results[0].operation == "ensure_primitive_body"
    assert result.operation_results[1].operation == "cut_hole"
    assert result.operation_results[2].operation == "cut_hole"
    assert result.operation_results[3].operation == "extrude_profile"


def test_executor_handles_native_recompute_failure_sanitized(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
) -> None:
    """Proves that a native recompute error returns sanitized ExecutionFailure with RECOMPUTE_FAILED."""
    runtime, doc_handle, raw_doc, _ = mock_runtime_and_handle
    raw_doc.recompute_should_fail = True
    executor = SolidEdgeExecutor(runtime, doc_handle)

    plan = _make_plan(
        base_body=RectangularBaseBody(
            id="body.main",
            length_mm=50.0,
            width_mm=50.0,
            thickness_mm=20.0,
        )
    )

    result = executor.execute_feature_plan(plan)
    assert isinstance(result, ExecutionFailure)
    assert result.details.get("error_code") == "RECOMPUTE_FAILED"
    assert "Recompute" in result.message


def test_executor_preflight_rejects_multi_body(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
) -> None:
    """Proves preflight rejects multiple ensure_primitive_body patches."""
    runtime, doc_handle, _, _ = mock_runtime_and_handle
    executor = SolidEdgeExecutor(runtime, doc_handle)

    patches: list[dict[str, Any]] = [
        {
            "op": "ensure_primitive_body",
            "patch_id": "p0",
            "body_ref": "b0",
            "shape": {"type": "cuboid", "length_mm": 50.0, "width_mm": 50.0, "height_mm": 10.0},
        },
        {
            "op": "ensure_primitive_body",
            "patch_id": "p1",
            "body_ref": "b1",
            "shape": {"type": "cuboid", "length_mm": 50.0, "width_mm": 50.0, "height_mm": 10.0},
        },
    ]
    res = executor._preflight_patch_sequence(patches)
    assert res is not None
    assert res[1] == "UNSUPPORTED_EXECUTION_OPERATION"
    assert "Multi-body" in res[0]


def test_executor_preflight_rejects_missing_references(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
) -> None:
    """Proves preflight detects missing sketch and profile references."""
    runtime, doc_handle, _, _ = mock_runtime_and_handle
    executor = SolidEdgeExecutor(runtime, doc_handle)

    # 1. Profile referencing non-existent sketch
    patches_bad_sketch: list[dict[str, Any]] = [
        {
            "op": "ensure_primitive_body",
            "patch_id": "p0",
            "body_ref": "b0",
            "shape": {"type": "cuboid", "length_mm": 50.0, "width_mm": 50.0, "height_mm": 10.0},
        },
        {
            "op": "ensure_profile",
            "patch_id": "p1",
            "profile_ref": "prof0",
            "sketch_ref": "nonexistent_sk",
            "geometry": {"kind": "circle", "radius_mm": 5.0},
        },
    ]
    res = executor._preflight_patch_sequence(patches_bad_sketch)
    assert res is not None
    assert res[1] == "MISSING_EXECUTION_REFERENCE"

    # 2. Cut referencing non-existent profile
    patches_bad_prof: list[dict[str, Any]] = [
        {
            "op": "ensure_primitive_body",
            "patch_id": "p0",
            "body_ref": "b0",
            "shape": {"type": "cuboid", "length_mm": 50.0, "width_mm": 50.0, "height_mm": 10.0},
        },
        {"op": "ensure_sketch", "patch_id": "p1", "sketch_ref": "sk0", "body_ref": "b0", "plane": "XY"},
        {
            "op": "cut_hole",
            "patch_id": "p2",
            "body_ref": "b0",
            "profile_ref": "nonexistent_prof",
            "result_ref": "c0",
            "through_all": True,
        },
    ]
    res2 = executor._preflight_patch_sequence(patches_bad_prof)
    assert res2 is not None
    assert res2[1] == "MISSING_EXECUTION_REFERENCE"


def test_executor_rejects_invalid_plan_payload_type(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
) -> None:
    """Proves passing invalid object types returns ExecutionFailure with INVALID_PLAN_PAYLOAD."""
    runtime, doc_handle, _, _ = mock_runtime_and_handle
    executor = SolidEdgeExecutor(runtime, doc_handle)

    result = executor.execute_feature_plan(12345)  # type: ignore[arg-type]
    assert isinstance(result, ExecutionFailure)
    assert result.details.get("error_code") == "INVALID_PLAN_PAYLOAD"


def test_executor_executes_dict_payload_end_to_end(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
) -> None:
    """Proves that passing a valid feature-plan dict lowers and executes cleanly."""
    runtime, doc_handle, _, _ = mock_runtime_and_handle
    executor = SolidEdgeExecutor(runtime, doc_handle)

    plan_dict = {
        "plan_version": "cad_copilot.single_part_feature_plan.v1",
        "request_id": "req-dict-test",
        "part": {"part_id": "part.main", "design_intent": "dict test part"},
        "base_body": {
            "id": "body.main",
            "family": "rectangular_prism",
            "length_mm": 60.0,
            "width_mm": 60.0,
            "thickness_mm": 20.0,
        },
        "features": [
            {
                "id": "hole_dict_1",
                "family": "circular_through_hole",
                "diameter_mm": 10.0,
                "target_face": "+Z",
            }
        ],
    }

    result = executor.execute_feature_plan(plan_dict)
    assert isinstance(result, ExecutionSuccess)
    assert len(result.operation_results) == 2


def test_executor_rejects_non_solid_or_negative_volume(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
) -> None:
    """Proves that non-solid geometry or non-positive volume fails inspection with SOLID_INSPECTION_FAILED."""
    runtime, doc_handle, raw_doc, _ = mock_runtime_and_handle
    raw_doc.Models.default_volume_m3 = -0.5
    executor = SolidEdgeExecutor(runtime, doc_handle)

    plan = _make_plan(
        base_body=RectangularBaseBody(
            id="body.main",
            length_mm=50.0,
            width_mm=50.0,
            thickness_mm=20.0,
        )
    )

    result = executor.execute_feature_plan(plan)
    assert isinstance(result, ExecutionFailure)
    assert result.details.get("error_code") == "SOLID_INSPECTION_FAILED"


def test_executor_handles_timeout_sanitized(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
) -> None:
    """Proves that TimeoutError returns sanitized ExecutionFailure with EXECUTION_TIMEOUT."""
    runtime, doc_handle, _, worker = mock_runtime_and_handle
    worker.call = MagicMock(side_effect=TimeoutError("Task exceeded time limit"))  # type: ignore[method-assign]
    executor = SolidEdgeExecutor(runtime, doc_handle, timeout_seconds=5.0)

    plan = _make_plan(
        base_body=RectangularBaseBody(
            id="body.main",
            length_mm=50.0,
            width_mm=50.0,
            thickness_mm=20.0,
        )
    )

    result = executor.execute_feature_plan(plan)
    assert isinstance(result, ExecutionFailure)
    assert result.details.get("error_code") == "EXECUTION_TIMEOUT"


def test_executor_inspect_active_document_and_recompute(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
) -> None:
    """Proves inspect_active_document, extract_physical_properties, and recompute_physical_properties."""
    runtime, doc_handle, raw_doc, worker = mock_runtime_and_handle
    raw_doc.Models.models.append(FakeCOMModel(worker, volume_m3=0.000125))  # 125,000 mm3
    executor = SolidEdgeExecutor(runtime, doc_handle)

    # 1. Standard inspection
    report = executor.inspect_active_document()
    assert isinstance(report, StandardInspectionReport)
    assert report.solid_body_count == 1
    assert math.isclose(report.volume_mm3, 125_000.0)

    # 2. Extract physical properties (calls inspect_active_document under the hood)
    props = executor.extract_physical_properties()
    assert isinstance(props, PhysicalProperties)
    assert math.isclose(props.volume_mm3, 125_000.0)

    # 3. Force recompute
    executor.recompute_physical_properties()
    assert "PartDocument.Recompute()" in worker.call_log

    # 4. Update document
    executor.update_document()


def test_executor_rejects_arbitrary_profile_cutout_feature(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
) -> None:
    """Proves AST and lowered patches with arbitrary ProfileCutoutFeature are rejected."""
    runtime, doc_handle, _, _ = mock_runtime_and_handle
    executor = SolidEdgeExecutor(runtime, doc_handle)

    # 1. AST rejection
    plan = _make_plan(
        base_body=RectangularBaseBody(
            id="body.main",
            length_mm=100.0,
            width_mm=100.0,
            thickness_mm=20.0,
        ),
        features=(
            ProfileCutoutFeature(
                id="cut_freeform",
                target_face="+Z",
                profile_points=(
                    MagicMock(x_mm=0.0, y_mm=0.0),
                    MagicMock(x_mm=10.0, y_mm=0.0),
                    MagicMock(x_mm=15.0, y_mm=5.0),
                    MagicMock(x_mm=10.0, y_mm=10.0),
                    MagicMock(x_mm=0.0, y_mm=10.0),
                ),
            ),
        ),
    )
    res = executor.execute_feature_plan(plan)
    assert isinstance(res, ExecutionFailure)
    assert res.details.get("error_code") == "UNSUPPORTED_EXECUTION_OPERATION"

    # 2. Lowered patch preflight rejection (5-point polygon)
    patches_5pt: list[dict[str, Any]] = [
        {
            "op": "ensure_primitive_body",
            "patch_id": "p0",
            "body_ref": "b0",
            "shape": {"type": "cuboid", "length_mm": 50.0, "width_mm": 50.0, "height_mm": 10.0},
        },
        {"op": "ensure_sketch", "patch_id": "p1", "sketch_ref": "sk0", "body_ref": "b0", "plane": "XY"},
        {
            "op": "ensure_profile",
            "patch_id": "p2",
            "profile_ref": "prof0",
            "sketch_ref": "sk0",
            "geometry": {
                "kind": "polygon",
                "points": [
                    {"x_mm": 0, "y_mm": 0},
                    {"x_mm": 5, "y_mm": 0},
                    {"x_mm": 8, "y_mm": 3},
                    {"x_mm": 5, "y_mm": 5},
                    {"x_mm": 0, "y_mm": 5},
                ],
            },
        },
        {
            "op": "cut_hole",
            "patch_id": "p3",
            "body_ref": "b0",
            "profile_ref": "prof0",
            "result_ref": "c0",
            "through_all": True,
        },
    ]
    err = executor._preflight_patch_sequence(patches_5pt)
    assert err is not None
    assert err[1] == "UNSUPPORTED_EXECUTION_OPERATION"
    assert "Non-rectangular" in err[0]


def test_executor_detects_sheet_or_multiple_bodies_in_inspection(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
) -> None:
    """Proves document with sheet bodies, constructions, or multiple models fails with SOLID_INSPECTION_FAILED."""
    runtime, doc_handle, raw_doc, worker = mock_runtime_and_handle
    executor = SolidEdgeExecutor(runtime, doc_handle)

    # 1. Non-solid Body (IsSolid=False)
    m1 = FakeCOMModel(worker, volume_m3=0.0001)
    m1.Body.IsSolid = False
    raw_doc.Models.models = [m1]

    with pytest.raises(CADExecutionError) as exc1:
        executor.inspect_active_document()
    assert exc1.value.error_code == "SOLID_INSPECTION_FAILED"

    # 2. Construction sheet models present (sheet bodies > 0)
    m2 = FakeCOMModel(worker, volume_m3=0.0001)
    m2.Body.IsSolid = True
    raw_doc.Models.models = [m2]
    raw_doc.Constructions.items.append(
        FakeCOMConstructionModel(worker, FakeCOMBody(is_solid=False, body_type=BODY_TYPE_SHEET))
    )

    with pytest.raises(CADExecutionError) as exc2:
        executor.inspect_active_document()
    assert exc2.value.error_code == "SOLID_INSPECTION_FAILED"
    assert "sheet bodies" in str(exc2.value)

    # 3. Multiple models present (models_count > 1)
    raw_doc.Constructions.items.clear()
    raw_doc.Models.models = [m2, FakeCOMModel(worker, volume_m3=0.0001)]

    with pytest.raises(CADExecutionError) as exc3:
        executor.inspect_active_document()
    assert exc3.value.error_code == "SOLID_INSPECTION_FAILED"


def test_executor_validates_feature_status_after_recompute(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
) -> None:
    """Proves that a feature whose status becomes unhealthy after recompute triggers FEATURE_VALIDATION_FAILED."""
    runtime, doc_handle, raw_doc, _ = mock_runtime_and_handle

    # Intercept cutout addition to corrupt its status after creation
    original_add = raw_doc.Models.AddFiniteExtrudedProtrusion

    def _custom_protrusion(*args: Any, **kwargs: Any) -> Any:
        model = original_add(*args, **kwargs)
        orig_cut = model.ExtrudedCutouts.AddThroughAll

        def _corrupt_cut(*c_args: Any, **c_kwargs: Any) -> Any:
            feat = orig_cut(*c_args, **c_kwargs)
            feat.Status = -1  # Corrupt feature status
            return feat

        model.ExtrudedCutouts.AddThroughAll = _corrupt_cut  # type: ignore[method-assign]
        return model

    raw_doc.Models.AddFiniteExtrudedProtrusion = _custom_protrusion  # type: ignore[method-assign]

    executor = SolidEdgeExecutor(runtime, doc_handle)
    plan = _make_plan(
        base_body=RectangularBaseBody(
            id="body.main",
            length_mm=100.0,
            width_mm=80.0,
            thickness_mm=20.0,
        ),
        features=(
            CircularThroughHoleFeature(
                id="hole_1",
                diameter_mm=16.0,
                target_face="+Z",
            ),
        ),
    )

    result = executor.execute_feature_plan(plan)
    assert isinstance(result, ExecutionFailure)
    assert result.details.get("error_code") == "FEATURE_VALIDATION_FAILED"


def test_executor_preflight_validates_duplicate_patch_ids_and_frames(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
) -> None:
    """Proves preflight detects duplicate patch IDs, duplicate refs, and non-orthonormal frames."""
    runtime, doc_handle, _, _ = mock_runtime_and_handle
    executor = SolidEdgeExecutor(runtime, doc_handle)

    # 1. Duplicate patch_id
    dup_patches: list[dict[str, Any]] = [
        {
            "op": "ensure_primitive_body",
            "patch_id": "dup_id",
            "body_ref": "b0",
            "shape": {"type": "cuboid", "length_mm": 50.0, "width_mm": 50.0, "height_mm": 10.0},
        },
        {"op": "ensure_sketch", "patch_id": "dup_id", "sketch_ref": "sk0", "body_ref": "b0", "plane": "XY"},
    ]
    err1 = executor._preflight_patch_sequence(dup_patches)
    assert err1 is not None
    assert err1[1] == "INVALID_LOWERED_PAYLOAD"
    assert "Duplicate patch_id" in err1[0]

    # 2. Non-orthogonal frame axes (u_axis and v_axis not orthogonal)
    non_ortho_patches: list[dict[str, Any]] = [
        {
            "op": "ensure_primitive_body",
            "patch_id": "p0",
            "body_ref": "b0",
            "shape": {"type": "cuboid", "length_mm": 50.0, "width_mm": 50.0, "height_mm": 10.0},
        },
        {"op": "ensure_sketch", "patch_id": "p1", "sketch_ref": "sk0", "body_ref": "b0", "plane": "XY"},
        {
            "op": "ensure_profile",
            "patch_id": "p2",
            "profile_ref": "prof0",
            "sketch_ref": "sk0",
            "geometry": {"kind": "circle", "radius_mm": 5.0},
        },
        {
            "op": "cut_hole",
            "patch_id": "p3",
            "body_ref": "b0",
            "profile_ref": "prof0",
            "result_ref": "c0",
            "through_all": True,
            "cut_direction": "into_solid",
            "normal_vector": [0.0, 0.0, 1.0],
            "u_axis": [1.0, 0.0, 0.0],
            "v_axis": [1.0, 0.0, 0.0],  # Parallel to u_axis, not orthogonal!
            "cut_vector": [0.0, 0.0, -1.0],
        },
    ]
    err2 = executor._preflight_patch_sequence(non_ortho_patches)
    assert err2 is not None
    assert err2[1] == "INVALID_LOWERED_PAYLOAD"
    assert "orthogonal" in err2[0]


def test_executor_rejects_unpromoted_side_pad_lowered(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
) -> None:
    """Proves that a side pad (+X) preserves its target face during lowering and is rejected by preflight."""
    runtime, doc_handle, _, _ = mock_runtime_and_handle
    executor = SolidEdgeExecutor(runtime, doc_handle)

    plan = _make_plan(
        base_body=RectangularBaseBody(
            id="body.main",
            length_mm=100.0,
            width_mm=80.0,
            thickness_mm=50.0,
        ),
        features=(
            RectangularExtrudedPadFeature(
                id="pad_side",
                target_face="+X",
                width_mm=20.0,
                height_mm=20.0,
                distance_mm=10.0,
            ),
        ),
    )

    result = executor.execute_feature_plan(plan)
    assert isinstance(result, ExecutionFailure)
    assert result.phase == "preflight"
    assert result.details.get("error_code") == "UNSUPPORTED_EXECUTION_OPERATION"
    assert "Unsupported pad capability row" in result.message


def test_executor_detects_wire_body_or_construction_model(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
) -> None:
    """Proves document with wire bodies or construction sheet models fails with SOLID_INSPECTION_FAILED."""
    runtime, doc_handle, raw_doc, worker = mock_runtime_and_handle
    executor = SolidEdgeExecutor(runtime, doc_handle)

    # 1. Wire Body (IsSolid=False, Type=BODY_TYPE_CURVE)
    m1 = FakeCOMModel(worker, volume_m3=0.0001)
    m1.Body.IsSolid = False
    m1.Body.Type = BODY_TYPE_CURVE
    raw_doc.Models.models = [m1]

    with pytest.raises(CADExecutionError) as exc1:
        executor.inspect_active_document()
    assert exc1.value.error_code == "SOLID_INSPECTION_FAILED"
    assert "wire bodies" in str(exc1.value)

    # 2. Construction Model non-empty (sheet body)
    m2 = FakeCOMModel(worker, volume_m3=0.0001)
    m2.Body.IsSolid = True
    m2.Body.Type = BODY_TYPE_SOLID
    raw_doc.Models.models = [m2]
    raw_doc.Constructions.items.append(
        FakeCOMConstructionModel(worker, FakeCOMBody(is_solid=False, body_type=BODY_TYPE_SHEET))
    )

    with pytest.raises(CADExecutionError) as exc2:
        executor.inspect_active_document()
    assert exc2.value.error_code == "SOLID_INSPECTION_FAILED"
    assert "sheet bodies" in str(exc2.value)


def test_executor_validates_base_extrusion_and_all_native_features_status(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
) -> None:
    """Proves that base primitive extrusion status failure after recompute triggers FEATURE_VALIDATION_FAILED."""
    runtime, doc_handle, raw_doc, _ = mock_runtime_and_handle

    # Intercept recompute to corrupt base protrusion status after build
    orig_recompute = raw_doc.Recompute

    def _corrupt_in_recompute() -> None:
        orig_recompute()
        if raw_doc.Models.Count > 0:
            raw_doc.Models.Item(1).ExtrudedProtrusions.protrusions[0].Status = -1

    raw_doc.Recompute = _corrupt_in_recompute  # type: ignore[method-assign]

    executor = SolidEdgeExecutor(runtime, doc_handle)
    plan = _make_plan(
        base_body=RectangularBaseBody(
            id="body.main",
            length_mm=50.0,
            width_mm=50.0,
            thickness_mm=20.0,
        )
    )

    result = executor.execute_feature_plan(plan)
    assert isinstance(result, ExecutionFailure)
    assert result.details.get("error_code") == "FEATURE_VALIDATION_FAILED"
    assert "status is unhealthy" in result.message


def test_executor_rejects_unreadable_native_feature_status(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
) -> None:
    """Proves that a feature with None or unreadable status after recompute triggers FEATURE_VALIDATION_FAILED."""
    runtime, doc_handle, raw_doc, _ = mock_runtime_and_handle

    # Intercept recompute to set status to None
    orig_recompute = raw_doc.Recompute

    def _corrupt_status_none() -> None:
        orig_recompute()
        if raw_doc.Models.Count > 0:
            raw_doc.Models.Item(1).ExtrudedProtrusions.protrusions[0].Status = None  # type: ignore[assignment]

    raw_doc.Recompute = _corrupt_status_none  # type: ignore[method-assign]

    executor = SolidEdgeExecutor(runtime, doc_handle)
    plan = _make_plan(
        base_body=RectangularBaseBody(
            id="body.main",
            length_mm=50.0,
            width_mm=50.0,
            thickness_mm=20.0,
        )
    )

    result = executor.execute_feature_plan(plan)
    assert isinstance(result, ExecutionFailure)
    assert result.details.get("error_code") == "FEATURE_VALIDATION_FAILED"
    assert "unhealthy/unreadable" in result.message


def test_executor_preflight_validates_complete_cut_frame_invariants(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
) -> None:
    """Proves preflight enforces cut_direction, frame orientation, and opposing cut_vector, rejecting any missing fields."""
    runtime, doc_handle, _, _ = mock_runtime_and_handle
    executor = SolidEdgeExecutor(runtime, doc_handle)

    base_setup: list[dict[str, Any]] = [
        {
            "op": "ensure_primitive_body",
            "patch_id": "p0",
            "body_ref": "b0",
            "shape": {"type": "cuboid", "length_mm": 50.0, "width_mm": 50.0, "height_mm": 10.0},
        },
        {"op": "ensure_sketch", "patch_id": "p1", "sketch_ref": "sk0", "body_ref": "b0", "plane": "XY"},
        {
            "op": "ensure_profile",
            "patch_id": "p2",
            "profile_ref": "prof0",
            "sketch_ref": "sk0",
            "geometry": {"kind": "circle", "radius_mm": 5.0},
        },
    ]

    # 1. Missing cut_direction
    p_no_dir = [
        *base_setup,
        {
            "op": "cut_hole",
            "patch_id": "p3",
            "body_ref": "b0",
            "profile_ref": "prof0",
            "result_ref": "c0",
            "through_all": True,
            "normal_vector": [0.0, 0.0, 1.0],
            "u_axis": [1.0, 0.0, 0.0],
            "v_axis": [0.0, 1.0, 0.0],
            "cut_vector": [0.0, 0.0, -1.0],
        },
    ]
    err_no_dir = executor._preflight_patch_sequence(p_no_dir)
    assert err_no_dir is not None
    assert err_no_dir[1] == "INVALID_LOWERED_PAYLOAD"
    assert "cut_direction is required" in err_no_dir[0]

    # 2. Missing normal_vector
    p_no_norm = [
        *base_setup,
        {
            "op": "cut_hole",
            "patch_id": "p3",
            "body_ref": "b0",
            "profile_ref": "prof0",
            "result_ref": "c0",
            "through_all": True,
            "cut_direction": "into_solid",
            "u_axis": [1.0, 0.0, 0.0],
            "v_axis": [0.0, 1.0, 0.0],
            "cut_vector": [0.0, 0.0, -1.0],
        },
    ]
    err_no_norm = executor._preflight_patch_sequence(p_no_norm)
    assert err_no_norm is not None
    assert err_no_norm[1] == "INVALID_LOWERED_PAYLOAD"
    assert "normal_vector is required" in err_no_norm[0]

    # 3. Missing u_axis
    p_no_u = [
        *base_setup,
        {
            "op": "cut_hole",
            "patch_id": "p3",
            "body_ref": "b0",
            "profile_ref": "prof0",
            "result_ref": "c0",
            "through_all": True,
            "cut_direction": "into_solid",
            "normal_vector": [0.0, 0.0, 1.0],
            "v_axis": [0.0, 1.0, 0.0],
            "cut_vector": [0.0, 0.0, -1.0],
        },
    ]
    err_no_u = executor._preflight_patch_sequence(p_no_u)
    assert err_no_u is not None
    assert err_no_u[1] == "INVALID_LOWERED_PAYLOAD"
    assert "u_axis is required" in err_no_u[0]

    # 4. Missing v_axis
    p_no_v = [
        *base_setup,
        {
            "op": "cut_hole",
            "patch_id": "p3",
            "body_ref": "b0",
            "profile_ref": "prof0",
            "result_ref": "c0",
            "through_all": True,
            "cut_direction": "into_solid",
            "normal_vector": [0.0, 0.0, 1.0],
            "u_axis": [1.0, 0.0, 0.0],
            "cut_vector": [0.0, 0.0, -1.0],
        },
    ]
    err_no_v = executor._preflight_patch_sequence(p_no_v)
    assert err_no_v is not None
    assert err_no_v[1] == "INVALID_LOWERED_PAYLOAD"
    assert "v_axis is required" in err_no_v[0]

    # 5. Missing cut_vector
    p_no_cut = [
        *base_setup,
        {
            "op": "cut_hole",
            "patch_id": "p3",
            "body_ref": "b0",
            "profile_ref": "prof0",
            "result_ref": "c0",
            "through_all": True,
            "cut_direction": "into_solid",
            "normal_vector": [0.0, 0.0, 1.0],
            "u_axis": [1.0, 0.0, 0.0],
            "v_axis": [0.0, 1.0, 0.0],
        },
    ]
    err_no_cut = executor._preflight_patch_sequence(p_no_cut)
    assert err_no_cut is not None
    assert err_no_cut[1] == "INVALID_LOWERED_PAYLOAD"
    assert "cut_vector is required" in err_no_cut[0]

    # 6. Frame u x v != normal (left-handed frame)
    p_bad_cross = [
        *base_setup,
        {
            "op": "cut_hole",
            "patch_id": "p3",
            "body_ref": "b0",
            "profile_ref": "prof0",
            "result_ref": "c0",
            "through_all": True,
            "cut_direction": "into_solid",
            "normal_vector": [0.0, 0.0, 1.0],
            "u_axis": [0.0, 1.0, 0.0],
            "v_axis": [1.0, 0.0, 0.0],
            "cut_vector": [0.0, 0.0, -1.0],
        },
    ]
    err_bad_cross = executor._preflight_patch_sequence(p_bad_cross)
    assert err_bad_cross is not None
    assert err_bad_cross[1] == "INVALID_LOWERED_PAYLOAD"
    assert "Frame u_axis x v_axis does not match normal_vector" in err_bad_cross[0]

    # 7. cut_vector != -normal_vector
    p_bad_cut_vec = [
        *base_setup,
        {
            "op": "cut_hole",
            "patch_id": "p3",
            "body_ref": "b0",
            "profile_ref": "prof0",
            "result_ref": "c0",
            "through_all": True,
            "cut_direction": "into_solid",
            "normal_vector": [0.0, 0.0, 1.0],
            "u_axis": [1.0, 0.0, 0.0],
            "v_axis": [0.0, 1.0, 0.0],
            "cut_vector": [0.0, 0.0, 1.0],
        },
    ]
    err_bad_cut_vec = executor._preflight_patch_sequence(p_bad_cut_vec)
    assert err_bad_cut_vec is not None
    assert err_bad_cut_vec[1] == "INVALID_LOWERED_PAYLOAD"
    assert "cut_vector must be equal to negative normal_vector" in err_bad_cut_vec[0]


def test_executor_preserves_failing_patch_and_reference_context(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
) -> None:
    """Proves that ExecutionFailure contains failing_patch_id, failing_operation, and failing_reference_id."""
    runtime, doc_handle, raw_doc, _ = mock_runtime_and_handle

    # Intercept cutout addition to raise an exception during cut execution
    orig_add = raw_doc.Models.AddFiniteExtrudedProtrusion

    def _raise_on_cut(*args: Any, **kwargs: Any) -> Any:
        model = orig_add(*args, **kwargs)

        def _bad_cut(*c_args: Any, **c_kwargs: Any) -> Any:
            raise RuntimeError("Underlying kernel error in cutout")

        model.ExtrudedCutouts.AddThroughAll = _bad_cut  # type: ignore[method-assign]
        return model

    raw_doc.Models.AddFiniteExtrudedProtrusion = _raise_on_cut  # type: ignore[method-assign]

    executor = SolidEdgeExecutor(runtime, doc_handle)
    plan = _make_plan(
        base_body=RectangularBaseBody(
            id="body.main",
            length_mm=100.0,
            width_mm=80.0,
            thickness_mm=20.0,
        ),
        features=(
            CircularThroughHoleFeature(
                id="hole_1",
                diameter_mm=16.0,
                target_face="+Z",
            ),
        ),
    )

    result = executor.execute_feature_plan(plan)
    assert isinstance(result, ExecutionFailure)
    assert result.phase == "execution"
    assert result.details.get("error_code") == "FEATURE_CREATION_FAILED"
    assert result.details.get("failing_operation") == "cut_hole"
    assert result.details.get("failing_reference_id") == "hole_1"
    assert "patch.cut_hole" in str(result.details.get("failing_patch_id"))


def test_executor_rejects_missing_native_features_collection(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
) -> None:
    """Proves that a model missing the Features collection fails with SOLID_INSPECTION_FAILED."""
    runtime, doc_handle, raw_doc, worker = mock_runtime_and_handle
    m = FakeCOMModel(worker, volume_m3=0.0001)
    m.Features = None  # type: ignore[assignment]
    raw_doc.Models.models = [m]

    executor = SolidEdgeExecutor(runtime, doc_handle)
    with pytest.raises(CADExecutionError) as exc:
        executor.inspect_active_document()
    assert exc.value.error_code == "SOLID_INSPECTION_FAILED"
    assert "Features collection" in str(exc.value)


def test_executor_validates_physical_property_status_failure(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
) -> None:
    """Proves that ComputePhysicalProperties returning an out-of-date/non-1 status (0) fails with SOLID_INSPECTION_FAILED."""
    runtime, doc_handle, raw_doc, worker = mock_runtime_and_handle
    m = FakeCOMModel(worker, volume_m3=0.0001)

    def _bad_compute(density: float, accuracy: float) -> tuple[Any, ...]:
        return (0.0001, 0.02, 0.785, None, None, None, None, None, None, 0.0001, 0)  # Status 0 (igStatusNone)

    m.ComputePhysicalProperties = _bad_compute  # type: ignore[method-assign]
    raw_doc.Models.models = [m]
    # Set positive density so ComputePhysicalProperties is called
    raw_doc.GetGlobalParameter = lambda param_id, *args: 7850.0 if param_id == 1 else 0.0001  # type: ignore[method-assign]

    executor = SolidEdgeExecutor(runtime, doc_handle)
    with pytest.raises(CADExecutionError) as exc:
        executor.inspect_active_document()
    assert exc.value.error_code == "SOLID_INSPECTION_FAILED"
    assert "status code: 0" in str(exc.value)
    assert "(expected 1)" in str(exc.value)


def test_executor_validates_physical_property_invalid_output(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
) -> None:
    """Proves that ComputePhysicalProperties returning an invalid tuple length fails with SOLID_INSPECTION_FAILED."""
    runtime, doc_handle, raw_doc, worker = mock_runtime_and_handle
    m = FakeCOMModel(worker, volume_m3=0.0001)

    def _bad_output(density: float, accuracy: float) -> tuple[Any, ...]:
        return (0.0001, 0.02, 0.0, None, None, None, None, None, None, 0.0)  # 10 elements instead of 11

    m.ComputePhysicalProperties = _bad_output  # type: ignore[method-assign]
    raw_doc.Models.models = [m]
    # Set positive density so ComputePhysicalProperties is called
    raw_doc.GetGlobalParameter = lambda param_id, *args: 7850.0 if param_id == 1 else 0.0001  # type: ignore[method-assign]

    executor = SolidEdgeExecutor(runtime, doc_handle)
    with pytest.raises(CADExecutionError) as exc:
        executor.inspect_active_document()
    assert exc.value.error_code == "SOLID_INSPECTION_FAILED"
    assert "invalid output tuple" in str(exc.value)


def test_executor_allows_zero_mass_when_density_is_unassigned_and_computes_mass_when_assigned(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
) -> None:
    """Proves that 0 mass is accepted when density is unassigned (0.0), and positive mass is computed when density > 0."""
    runtime, doc_handle, raw_doc, worker = mock_runtime_and_handle
    m = FakeCOMModel(worker, volume_m3=0.0001)
    raw_doc.Models.models = [m]
    executor = SolidEdgeExecutor(runtime, doc_handle)

    # 1. Unset density (0.0) -> mass is 0.0, Body.Volume is used directly without calling ComputePhysicalProperties
    raw_doc.GetGlobalParameter = lambda param_id, *args: 0.0 if param_id == 1 else 0.0001  # type: ignore[method-assign]
    report1 = executor.inspect_active_document()
    assert report1.mass_kg == 0.0
    assert report1.volume_mm3 == pytest.approx(0.0001 * 1e9)

    # 2. Set density (7850.0) -> mass is computed via ComputePhysicalProperties
    raw_doc.GetGlobalParameter = lambda param_id, *args: 7850.0 if param_id == 1 else 0.0001  # type: ignore[method-assign]
    report2 = executor.inspect_active_document()
    assert report2.mass_kg == pytest.approx(0.0001 * 7850.0)


def test_executor_rejects_missing_or_failing_get_global_parameter(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
) -> None:
    """Proves that missing or failing GetGlobalParameter lookup fails with SOLID_INSPECTION_FAILED."""
    runtime, doc_handle, raw_doc, worker = mock_runtime_and_handle
    m = FakeCOMModel(worker, volume_m3=0.0001)
    raw_doc.Models.models = [m]
    executor = SolidEdgeExecutor(runtime, doc_handle)

    # 1. Missing GetGlobalParameter method
    raw_doc.GetGlobalParameter = None  # type: ignore[assignment]
    with pytest.raises(CADExecutionError) as exc1:
        executor.inspect_active_document()
    assert exc1.value.error_code == "SOLID_INSPECTION_FAILED"
    assert "GetGlobalParameter" in str(exc1.value)

    # 2. GetGlobalParameter raising exception
    def _raise_param(param_id: int, *args: Any) -> float:
        raise RuntimeError("COM HRESULT parameter failure")

    raw_doc.GetGlobalParameter = _raise_param  # type: ignore[method-assign,assignment]
    with pytest.raises(CADExecutionError) as exc2:
        executor.inspect_active_document()
    assert exc2.value.error_code == "SOLID_INSPECTION_FAILED"

    # 3. GetGlobalParameter returning negative density
    raw_doc.GetGlobalParameter = lambda param_id, *args: -1.0 if param_id == 1 else 0.0001  # type: ignore[method-assign]
    with pytest.raises(CADExecutionError) as exc3:
        executor.inspect_active_document()
    assert exc3.value.error_code == "SOLID_INSPECTION_FAILED"
    assert "invalid density" in str(exc3.value)

    # 4. GetGlobalParameter returning non-positive accuracy
    raw_doc.GetGlobalParameter = lambda param_id, *args: 0.0 if param_id == 1 else -0.5  # type: ignore[method-assign]
    with pytest.raises(CADExecutionError) as exc4:
        executor.inspect_active_document()
    assert exc4.value.error_code == "SOLID_INSPECTION_FAILED"
    assert "invalid accuracy" in str(exc4.value)


def test_executor_mode_continuity_capability_first_vs_strict(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
) -> None:
    """Proves mode keyword continuity: capability_first allows edge margin relaxation while strict rejects."""
    runtime, doc_handle, _raw_doc, _worker = mock_runtime_and_handle
    executor = SolidEdgeExecutor(runtime, doc_handle)

    body = RectangularBaseBody(id="body.main", length_mm=100.0, width_mm=100.0, thickness_mm=10.0)
    # Hole placed with center_x=48, diameter=10 (radius=5): outer edge exceeds boundary
    # In capability_first mode: allowed with GATE_POLICY_RELAXED warning
    # In strict mode: hard HOLE_DOES_NOT_FIT validation error
    h1 = CircularThroughHoleFeature(
        id="hole_1", diameter_mm=10.0, center_x_mm=48.0, center_y_mm=0.0, target_body_id="body.main"
    )
    plan = FeaturePlan(request_id="req_mode_test", part=PartMetadata(), base_body=body, features=(h1,))

    # In strict mode: lowering fails due to strict clearance violation
    res_strict = executor.execute_feature_plan(plan, mode="strict")
    assert isinstance(res_strict, ExecutionFailure)
    assert res_strict.phase == "validation"
    assert res_strict.details.get("error_code") == "PLAN_LOWERING_FAILED"

    # In capability_first mode: lowering passes and emits relaxed warning
    res_cap = executor.execute_feature_plan(plan, mode="capability_first")
    assert isinstance(res_cap, ExecutionSuccess)
    assert any("GATE_POLICY_RELAXED" in str(w) for w in res_cap.warnings)


def test_executor_mode_continuity_default_is_capability_first(
    mock_runtime_and_handle: tuple[
        SolidEdgeRuntime, SolidEdgePartDocumentHandle, FakeCOMPartDocument, MockWorkerForExecutor
    ],
) -> None:
    """Proves mode keyword defaults to capability_first when omitted."""
    runtime, doc_handle, _raw_doc, _worker = mock_runtime_and_handle
    executor = SolidEdgeExecutor(runtime, doc_handle)

    body = RectangularBaseBody(id="body.main", length_mm=100.0, width_mm=100.0, thickness_mm=10.0)
    h1 = CircularThroughHoleFeature(
        id="hole_1", diameter_mm=10.0, center_x_mm=48.0, center_y_mm=0.0, target_body_id="body.main"
    )
    plan = FeaturePlan(request_id="req_mode_test", part=PartMetadata(), base_body=body, features=(h1,))

    # In default mode (mode=None): defaults to capability_first
    res_default = executor.execute_feature_plan(plan)
    assert isinstance(res_default, ExecutionSuccess)
    assert any("GATE_POLICY_RELAXED" in str(w) for w in res_default.warnings)
