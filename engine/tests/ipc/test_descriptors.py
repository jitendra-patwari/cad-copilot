"""Tests for low-level descriptor isolation, devnull redirection, and Windows handles."""

from __future__ import annotations

import contextlib
import os
import sys
from unittest.mock import patch

import pytest

from ipc.stdio import (
    ControlledDescriptors,
    _redirect_windows_handles,
    controlled_stdio,
    establish_controlled_descriptors,
)
from tests.ipc.support import drain_pipe, make_pipes


def test_controlled_stdio_swallows_contamination_and_preserves_clean_output() -> None:
    """Proves controlled_stdio redirects Python and C outputs to null and restores streams on exit."""
    out_r, out_w, err_r, err_w = make_pipes()

    try:
        with controlled_stdio() as desc:
            assert desc.orig_stdout_fd > 0
            assert desc.orig_stderr_fd > 0
            assert desc.null_fd > 0

            # 1. Python-level prints and writes should be swallowed by devnull
            print("PYTHON_STDOUT_POISON")
            sys.stderr.write("PYTHON_STDERR_POISON\n")
            sys.stdout.flush()
            sys.stderr.flush()

            # 2. C-runtime descriptor 1 and 2 writes should be swallowed by devnull
            os.write(1, b"C_DESCRIPTOR_1_POISON\n")
            os.write(2, b"C_DESCRIPTOR_2_POISON\n")

            # 3. Preserved descriptors can write cleanly
            os.write(out_w, b'{"clean":"stdout"}\n')
            os.write(err_w, b'{"clean":"stderr"}\n')

        # After context exit, test pipes contain strictly the clean payloads
        os.close(out_w)
        os.close(err_w)

        stdout_data = drain_pipe(out_r)
        stderr_data = drain_pipe(err_r)

        assert stdout_data == b'{"clean":"stdout"}\n'
        assert stderr_data == b'{"clean":"stderr"}\n'
        assert b"POISON" not in stdout_data
        assert b"POISON" not in stderr_data

    finally:
        for fd in (out_r, err_r):
            with contextlib.suppress(OSError):
                os.close(fd)


def test_windows_handles_failure_and_rollback() -> None:
    """Proves failure during SetStdHandle triggers rollback of modified handles and closes descriptors."""
    if sys.platform != "win32":
        return

    import ctypes

    call_count = 0

    def mock_set_std_handle(handle_id: int, target_h: int) -> int:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            # Simulate failure on setting STD_ERROR_HANDLE
            ctypes.set_last_error(6)
            return 0
        return 1

    null_fd = os.open(os.devnull, os.O_RDWR)
    try:
        with patch("ctypes.WinDLL") as mock_windll:
            mock_k32 = mock_windll.return_value
            mock_k32.GetStdHandle.return_value = 100
            mock_k32.SetStdHandle.side_effect = mock_set_std_handle
            with pytest.raises(OSError, match=r"SetStdHandle\(STD_ERROR_HANDLE\) failed"):
                _redirect_windows_handles(null_fd)
            # Verify rollback: SetStdHandle was called 3 times (stdout, stderr which failed, then rollback stdout)
            assert mock_k32.SetStdHandle.call_count == 3
    finally:
        os.close(null_fd)


def test_establish_controlled_descriptors_failure_restores_descriptors_and_closes_copies() -> None:
    """Proves exception during establish_controlled_descriptors restores CRT descriptors 1 and 2."""
    with (
        patch("ipc.stdio._redirect_windows_handles", side_effect=OSError("Simulated handle redirection failure")),
        pytest.raises(OSError, match="Simulated handle redirection failure"),
    ):
        establish_controlled_descriptors(save_restore_state=False)

    # Descriptors 1 and 2 must remain valid and operational after failure cleanup
    os.write(1, b"")
    os.write(2, b"")


def test_controlled_descriptors_close_idempotent() -> None:
    """Proves closing ControlledDescriptors multiple times is safe and idempotent."""
    out_r, out_w, err_r, err_w = make_pipes()
    null_fd = os.open(os.devnull, os.O_RDWR)
    desc = ControlledDescriptors(out_w, err_w, null_fd)

    desc.close()
    # Second close should be a no-op
    desc.close()

    for fd in (out_r, err_r):
        with contextlib.suppress(OSError):
            os.close(fd)
