"""Unit tests for process identity extraction, process liveness, and safe teardown."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from drivers.solidedge.process import (
    get_process_identity,
    is_process_alive,
    kill_orphan_processes,
    try_get_process_id,
)
from drivers.solidedge.types import ProcessIdentity


class MockApplication:
    """Mock Solid Edge COM Application object."""

    def __init__(self, pid: int | None = 1234, hwnd: int | None = None) -> None:
        if pid is not None:
            self.ProcessID = pid
        if hwnd is not None:
            self.hWnd = hwnd


def test_try_get_process_id_property_inspection() -> None:
    """Proves that try_get_process_id extracts PID from ProcessID property."""
    app = MockApplication(pid=4321)
    assert try_get_process_id(app) == 4321


def test_try_get_process_id_none_or_missing() -> None:
    """Proves that try_get_process_id returns None when application is None or has no PID."""
    assert try_get_process_id(None) is None

    empty_app = object()
    assert try_get_process_id(empty_app) is None


def test_get_process_identity_invalid_pid() -> None:
    """Proves that get_process_identity returns None for non-positive PIDs."""
    assert get_process_identity(0) is None
    assert get_process_identity(-1) is None


def test_get_process_identity_current_process() -> None:
    """Proves that get_process_identity extracts valid creation timestamp for current process."""
    current_pid = os.getpid()
    identity = get_process_identity(current_pid)
    assert identity is not None
    assert identity.pid == current_pid
    assert identity.creation_time_ft > 0


def test_is_process_alive_current_and_invalid() -> None:
    """Proves is_process_alive correctly reports True for current process and False for non-existent."""
    assert is_process_alive(os.getpid()) is True
    assert is_process_alive(0) is False
    assert is_process_alive(-99) is False
    assert is_process_alive(9999999) is False


def test_kill_orphan_processes_empty_or_none() -> None:
    """Proves that kill_orphan_processes safely returns empty list on None or empty input."""
    assert kill_orphan_processes(None) == []
    assert kill_orphan_processes([]) == []


def test_kill_orphan_processes_recycled_pid_protection(capsys: pytest.CaptureFixture[str]) -> None:
    """Proves that kill_orphan_processes aborts termination if creation timestamp does not match (recycled PID)."""
    fake_identity = ProcessIdentity(pid=os.getpid(), creation_time_ft=123456789)

    # Current process has a different creation_time_ft
    real_identity = get_process_identity(os.getpid())
    assert real_identity is not None
    assert real_identity.creation_time_ft != fake_identity.creation_time_ft

    with patch("subprocess.run") as mock_subproc:
        _ = kill_orphan_processes([fake_identity])
        # Taskkill should NEVER have been called!
        assert mock_subproc.call_count == 0

    captured = capsys.readouterr()
    assert "[DIAGNOSTIC LOG] Aborted termination: PID" in captured.err
    assert "creation timestamp mismatch" in captured.err


def test_kill_orphan_processes_fails_closed_when_identity_unqueryable(capsys: pytest.CaptureFixture[str]) -> None:
    """Proves that kill_orphan_processes strictly aborts if get_process_identity returns None (Fail-Closed)."""
    identity = ProcessIdentity(pid=os.getpid(), creation_time_ft=123456789)

    with (
        patch("drivers.solidedge.process.is_process_alive", return_value=True),
        patch("drivers.solidedge.process.get_process_identity", return_value=None),
        patch("subprocess.run") as mock_subproc,
    ):
        result = kill_orphan_processes([identity])
        # Taskkill must NEVER be called when identity cannot be verified!
        assert mock_subproc.call_count == 0
        assert result == []

    captured = capsys.readouterr()
    assert "[DIAGNOSTIC LOG] Aborted termination: PID" in captured.err
    assert "identity unverified" in captured.err


def test_kill_orphan_processes_already_exited() -> None:
    """Proves that kill_orphan_processes handles already-exited PIDs gracefully."""
    dead_identity = ProcessIdentity(pid=9999998, creation_time_ft=12345)
    with patch("subprocess.run") as mock_subproc:
        result = kill_orphan_processes([dead_identity])
        assert mock_subproc.call_count == 0
        assert result == [9999998]


def test_kill_orphan_processes_executes_taskkill_when_verified() -> None:
    """Proves that kill_orphan_processes executes targeted taskkill when identity matches."""
    current_identity = get_process_identity(os.getpid())
    assert current_identity is not None

    with (
        patch("drivers.solidedge.process.is_process_alive", side_effect=[True, False]),
        patch("subprocess.run") as mock_subproc,
    ):
        result = kill_orphan_processes([current_identity])
        assert mock_subproc.call_count == 1
        call_args = mock_subproc.call_args[0][0]
        assert call_args == ["taskkill", "/F", "/FI", "IMAGENAME eq Edge.exe", "/PID", str(current_identity.pid)]
        assert result == [current_identity.pid]
