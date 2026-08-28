"""Controlled live integration tests against a real Siemens Solid Edge installation.

These tests execute ONLY when running with `pytest -m com` on a Windows host with
a licensed Siemens Solid Edge installation.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Generator

import pytest

from drivers.solidedge import (
    OwnershipMode,
    SolidEdgePartDocumentHandle,
    SolidEdgeRuntime,
    is_process_alive,
)

pytestmark = [pytest.mark.com]


@pytest.fixture
def live_runtime() -> Generator[SolidEdgeRuntime]:
    """Provide a managed SolidEdgeRuntime instance with guaranteed post-test cleanup."""
    if sys.platform != "win32":
        pytest.skip("Live Solid Edge COM tests require a Windows platform")

    try:
        import pythoncom  # type: ignore[import-untyped]  # noqa: F401
        import win32com.client  # type: ignore[import-untyped]  # noqa: F401
    except ImportError:
        pytest.skip("pywin32 is not installed in the current Python environment")

    runtime = SolidEdgeRuntime()
    try:
        yield runtime
    finally:
        runtime.teardown(force_kill_on_failure=True)


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
    print(
        "[LIVE GATE 2] Pausing for 4 seconds so you can see the new Ordered Part document in the Solid Edge window...",
        flush=True,
    )
    time.sleep(4.0)

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
        print("[LIVE GATE 4] In-flight document active. Pausing 2 seconds...", flush=True)
        time.sleep(2.0)

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
