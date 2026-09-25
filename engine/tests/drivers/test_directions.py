"""Unit tests and fake-COM verification for frozen cut and pad direction capability rows."""

from __future__ import annotations

import math
from typing import Any
from unittest.mock import MagicMock

import pytest

from drivers.solidedge.builders import (
    _invoke_native_cutout_finite,
    _invoke_native_cutout_through_all,
    _invoke_native_pad_protrusion,
    create_cutout_finite,
    create_cutout_through_all,
    create_pad_protrusion,
)
from drivers.solidedge.constants import (
    FEATURE_STATUS_OK,
    PROFILE_SIDE_LEFT,
    PROFILE_SIDE_RIGHT,
)
from drivers.solidedge.directions import (
    PROMOTED_CUT_CAPABILITY_ROWS,
    PROMOTED_PAD_CAPABILITY_ROWS,
    DirectionPair,
    resolve_cut_direction,
    resolve_pad_direction,
)
from interfaces.exceptions import CADExecutionError

# ---------------------------------------------------------------------------
# 1. Capability Row Invariant Tests
# ---------------------------------------------------------------------------


def test_promoted_cut_capability_rows_literal_values() -> None:
    """Proves that all promoted cut capability rows map to exact, verified (ProfileSide, ProfilePlaneSide) pairs."""
    # ProfileSide must always be igLeft (1) to remove material inside closed profile loops
    for _key, pair in PROMOTED_CUT_CAPABILITY_ROWS.items():
        assert pair.profile_side == PROFILE_SIDE_LEFT  # 1
        assert isinstance(pair, DirectionPair)

    # 1. Primary +Z capability matrix (through_all and finite for circle, polygon, slot)
    for prof in ("circle", "polygon", "slot"):
        for extent in ("through_all", "finite"):
            assert resolve_cut_direction(prof, extent, "+Z") == DirectionPair(profile_side=1, profile_plane_side=1)

    # 2. Characterized circular through-all cuts on rectangular prism faces
    assert resolve_cut_direction("circle", "through_all", "-Z") == DirectionPair(profile_side=1, profile_plane_side=2)
    assert resolve_cut_direction("circle", "through_all", "+X") == DirectionPair(profile_side=1, profile_plane_side=1)
    assert resolve_cut_direction("circle", "through_all", "-X") == DirectionPair(profile_side=1, profile_plane_side=2)
    assert resolve_cut_direction("circle", "through_all", "+Y") == DirectionPair(profile_side=1, profile_plane_side=2)
    assert resolve_cut_direction("circle", "through_all", "-Y") == DirectionPair(profile_side=1, profile_plane_side=1)
    assert resolve_cut_direction("polygon", "through_all", "-Y") == DirectionPair(profile_side=1, profile_plane_side=1)


def test_promoted_pad_capability_rows_literal_values() -> None:
    """Proves that only the verified +Z rectangular pad capability row is promoted in M3.2 baseline."""
    assert resolve_pad_direction("polygon", "finite", "+Z") == DirectionPair(
        profile_side=PROFILE_SIDE_LEFT, profile_plane_side=PROFILE_SIDE_RIGHT
    )  # (1, 2)
    assert len(PROMOTED_PAD_CAPABILITY_ROWS) == 1


def test_resolve_directions_reject_unpromoted_capability_rows() -> None:
    """Proves that unpromoted face/extent/profile combinations fail immediately before COM."""
    # 1. Non-+Z blind cuts are unpromoted
    with pytest.raises(CADExecutionError, match="Unsupported cut capability row"):
        resolve_cut_direction("circle", "finite", "-X")

    with pytest.raises(CADExecutionError, match="Unsupported cut capability row"):
        resolve_cut_direction("slot", "finite", "+Y")

    # 2. Other side-face polygon or slot through-cuts are unpromoted without live characterization
    with pytest.raises(CADExecutionError, match="Unsupported cut capability row"):
        resolve_cut_direction("polygon", "through_all", "+X")

    with pytest.raises(CADExecutionError, match="Unsupported cut capability row"):
        resolve_cut_direction("slot", "through_all", "-Y")

    # 3. Side pads and -Z pads are unpromoted
    with pytest.raises(CADExecutionError, match="Unsupported pad capability row"):
        resolve_pad_direction("polygon", "finite", "+X")

    with pytest.raises(CADExecutionError, match="Unsupported pad capability row"):
        resolve_pad_direction("polygon", "finite", "-Z")


# ---------------------------------------------------------------------------
# 2. Fake-COM Cutout and Pad Invocation on Target Model
# ---------------------------------------------------------------------------


class MockLoggingWorker:
    def __init__(self) -> None:
        self.call_log: list[str] = []

    def _invoke_com(self, func: Any) -> Any:
        return func()


class FakeFeature:
    def __init__(self, status: Any = FEATURE_STATUS_OK) -> None:
        self.Status: Any = status


class FakeExtrudedCutouts:
    def __init__(self, worker: MockLoggingWorker) -> None:
        self.worker = worker
        self.through_all_calls: list[tuple[Any, int, int]] = []
        self.finite_calls: list[tuple[Any, int, int, float]] = []
        self.should_return_null: bool = False
        self.cutout_status: Any = FEATURE_STATUS_OK

    def AddThroughAll(self, profile: Any, profile_side: int, profile_plane_side: int) -> Any:
        self.worker.call_log.append(
            f"ExtrudedCutouts.AddThroughAll(side={profile_side}, plane_side={profile_plane_side})"
        )
        if self.should_return_null:
            return None
        self.through_all_calls.append((profile, profile_side, profile_plane_side))
        return FakeFeature(status=self.cutout_status)

    def AddFinite(self, profile: Any, profile_side: int, profile_plane_side: int, depth_m: float) -> Any:
        self.worker.call_log.append(
            f"ExtrudedCutouts.AddFinite(side={profile_side}, plane_side={profile_plane_side}, depth={depth_m:.6f})"
        )
        if self.should_return_null:
            return None
        self.finite_calls.append((profile, profile_side, profile_plane_side, depth_m))
        return FakeFeature(status=self.cutout_status)


class FakeExtrudedProtrusions:
    def __init__(self, worker: MockLoggingWorker) -> None:
        self.worker = worker
        self.finite_calls: list[tuple[Any, int, int, float]] = []
        self.should_return_null: bool = False
        self.protrusion_status: Any = FEATURE_STATUS_OK

    @property
    def Count(self) -> int:
        return 1

    def Item(self, index: int) -> Any:
        return FakeFeature(status=self.protrusion_status)

    def AddFinite(self, profile: Any, profile_side: int, profile_plane_side: int, height_m: float) -> Any:
        self.worker.call_log.append(
            f"ExtrudedProtrusions.AddFinite(side={profile_side}, plane_side={profile_plane_side}, height={height_m:.6f})"
        )
        if self.should_return_null:
            return None
        self.finite_calls.append((profile, profile_side, profile_plane_side, height_m))
        return FakeFeature(status=self.protrusion_status)


class FakeModel:
    def __init__(self, worker: MockLoggingWorker) -> None:
        self.worker = worker
        self.ExtrudedCutouts = FakeExtrudedCutouts(worker)
        self.ExtrudedProtrusions = FakeExtrudedProtrusions(worker)
        self.Body = MagicMock(name="SolidBody")


@pytest.fixture
def fake_env() -> tuple[FakeModel, MockLoggingWorker]:
    worker = MockLoggingWorker()
    model = FakeModel(worker)
    return model, worker


def test_create_cutout_through_all_invoked_on_model(
    fake_env: tuple[FakeModel, MockLoggingWorker],
) -> None:
    """Proves through-all cuts are invoked directly on the target Model with mandatory profile_family."""
    model, worker = fake_env
    mock_profile = MagicMock(name="Profile")

    # 1. +Z cut -> (1, 1)
    cut_pz = create_cutout_through_all(model, worker, mock_profile, face="+Z", profile_family="circle")
    assert cut_pz is not None
    assert model.ExtrudedCutouts.through_all_calls[-1] == (mock_profile, 1, 1)

    # 2. -Z cut -> (1, 2)
    create_cutout_through_all(model, worker, mock_profile, face="-Z", profile_family="circle")
    assert model.ExtrudedCutouts.through_all_calls[-1] == (mock_profile, 1, 2)

    # 3. +X cut -> (1, 1)
    create_cutout_through_all(model, worker, mock_profile, face="+X", profile_family="circle")
    assert model.ExtrudedCutouts.through_all_calls[-1] == (mock_profile, 1, 1)

    # 4. -X cut -> (1, 2)
    create_cutout_through_all(model, worker, mock_profile, face="-X", profile_family="circle")
    assert model.ExtrudedCutouts.through_all_calls[-1] == (mock_profile, 1, 2)

    # 5. +Y cut -> (1, 2)
    create_cutout_through_all(model, worker, mock_profile, face="+Y", profile_family="circle")
    assert model.ExtrudedCutouts.through_all_calls[-1] == (mock_profile, 1, 2)

    # 6. -Y cut -> (1, 1)
    create_cutout_through_all(model, worker, mock_profile, face="-Y", profile_family="circle")
    assert model.ExtrudedCutouts.through_all_calls[-1] == (mock_profile, 1, 1)

    # Exactly 6 COM calls made (zero retries)
    assert len(model.ExtrudedCutouts.through_all_calls) == 6
    assert len(worker.call_log) == 6


def test_create_cutout_finite_invoked_on_model(
    fake_env: tuple[FakeModel, MockLoggingWorker],
) -> None:
    """Proves finite/blind cut is invoked on target Model with meter conversion and exact direction pair."""
    model, worker = fake_env
    mock_profile = MagicMock(name="Profile")

    cut = create_cutout_finite(model, worker, mock_profile, depth_mm=12.5, face="+Z", profile_family="circle")
    assert cut is not None
    assert len(model.ExtrudedCutouts.finite_calls) == 1
    prof, side, plane_side, depth_m = model.ExtrudedCutouts.finite_calls[0]
    assert prof is mock_profile
    assert side == 1
    assert plane_side == 1
    assert math.isclose(depth_m, 0.0125)


def test_create_pad_protrusion_invoked_on_model_extruded_protrusions(
    fake_env: tuple[FakeModel, MockLoggingWorker],
) -> None:
    """Proves pad protrusion is added as an ordered feature on the existing Model's ExtrudedProtrusions collection."""
    model, worker = fake_env
    mock_profile = MagicMock(name="Profile")

    pad = create_pad_protrusion(model, worker, mock_profile, height_mm=10.0, face="+Z", profile_family="polygon")
    assert pad is not None
    assert len(model.ExtrudedProtrusions.finite_calls) == 1
    prof, side, plane_side, height_m = model.ExtrudedProtrusions.finite_calls[0]
    assert prof is mock_profile
    assert side == 1
    assert plane_side == 2
    assert math.isclose(height_m, 0.010)


def test_explicit_pair_characterization_seams_allow_disposable_testing(
    fake_env: tuple[FakeModel, MockLoggingWorker],
) -> None:
    """Proves that _invoke_native_* helpers accept arbitrary candidate DirectionPairs for live characterization without table edits."""
    model, worker = fake_env
    mock_profile = MagicMock(name="Profile")

    # 1. Test candidate through-all pair (e.g. testing (2, 2) vs (1, 1))
    candidate_pair = DirectionPair(profile_side=2, profile_plane_side=2)
    cut = _invoke_native_cutout_through_all(model, worker, mock_profile, candidate_pair, "candidate through test")
    assert cut is not None
    assert model.ExtrudedCutouts.through_all_calls[-1] == (mock_profile, 2, 2)

    # 2. Test candidate finite cut pair
    cut_fin = _invoke_native_cutout_finite(
        model, worker, mock_profile, candidate_pair, depth_m=0.02, description="candidate finite test"
    )
    assert cut_fin is not None
    assert model.ExtrudedCutouts.finite_calls[-1] == (mock_profile, 2, 2, 0.02)

    # 3. Test candidate pad pair
    pad = _invoke_native_pad_protrusion(
        model, worker, mock_profile, candidate_pair, height_m=0.015, description="candidate pad test"
    )
    assert pad is not None
    assert model.ExtrudedProtrusions.finite_calls[-1] == (mock_profile, 2, 2, 0.015)


def test_cutout_and_pad_reject_null_or_unhealthy_feature(
    fake_env: tuple[FakeModel, MockLoggingWorker],
) -> None:
    """Proves that a null or unhealthy feature status on cutouts or pads raises CADExecutionError."""
    model, worker = fake_env
    mock_profile = MagicMock(name="Profile")

    # 1. Null cutout return
    model.ExtrudedCutouts.should_return_null = True
    with pytest.raises(CADExecutionError, match="returned null feature"):
        create_cutout_through_all(model, worker, mock_profile, face="+Z", profile_family="circle")

    # 2. Unhealthy cutout status
    model.ExtrudedCutouts.should_return_null = False
    model.ExtrudedCutouts.cutout_status = 0
    with pytest.raises(CADExecutionError, match="Native cutout feature status is unhealthy"):
        create_cutout_finite(model, worker, mock_profile, depth_mm=10.0, face="+Z", profile_family="circle")

    # 3. Unhealthy pad status
    model.ExtrudedProtrusions.protrusion_status = 0
    with pytest.raises(CADExecutionError, match="Native pad feature status is unhealthy"):
        create_pad_protrusion(model, worker, mock_profile, height_mm=10.0, face="+Z", profile_family="polygon")
