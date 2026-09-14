"""Test support utilities for IPC stdio and descriptor tests."""

from __future__ import annotations

import contextlib
import io
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ipc.descriptors import ControlledDescriptors
from ipc.stdio import _run_stdio


def make_pipes() -> tuple[int, int, int, int]:
    """Create two OS pipe pairs (stdout_r, stdout_w, stderr_r, stderr_w)."""
    out_r, out_w = os.pipe()
    err_r, err_w = os.pipe()
    return out_r, out_w, err_r, err_w


def drain_pipe(fd: int) -> bytes:
    """Read all available data from a pipe file descriptor until empty or closed."""
    chunks: list[bytes] = []
    while True:
        try:
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            chunks.append(chunk)
        except OSError:
            break
    return b"".join(chunks)


@dataclass(frozen=True)
class PipeRunResult:
    """Result of running _run_stdio captured via OS pipes."""

    returncode: int
    stdout_bytes: bytes
    stderr_bytes: bytes

    @property
    def stdout_json(self) -> dict[str, Any]:
        """Decode stdout as UTF-8 JSON object."""
        res = json.loads(self.stdout_bytes.decode("utf-8"))
        if not isinstance(res, dict):
            raise TypeError(f"Expected dict, got {type(res)}")
        return res

    @property
    def stderr_diagnostics(self) -> list[dict[str, Any]]:
        """Decode stderr JSONL lines as dictionaries."""
        lines = self.stderr_bytes.decode("ascii").strip().split("\n")
        diagnostics: list[dict[str, Any]] = []
        for line in lines:
            if line.strip():
                item = json.loads(line)
                if isinstance(item, dict):
                    diagnostics.append(item)
        return diagnostics


def run_stdio_in_pipes(
    *,
    argv: list[str] | None = None,
    stdin_data: bytes | io.BytesIO = b"",
    composition_handler: Callable[[Any], dict[str, Any]] | None = None,
) -> PipeRunResult:
    """Run _run_stdio with controlled descriptors backed by OS pipes and collect output.

    Guarantees all pipe ends and descriptors are cleanly closed even on test failure.
    """
    out_r, out_w, err_r, err_w = make_pipes()
    null_fd = os.open(os.devnull, os.O_RDWR)
    desc = ControlledDescriptors(out_w, err_w, null_fd)

    stdin_stream = stdin_data if isinstance(stdin_data, io.BytesIO) else io.BytesIO(stdin_data)

    try:
        code = _run_stdio(
            argv=argv if argv is not None else [],
            _composition_handler=composition_handler,
            _stdin_stream=stdin_stream,
            _descriptors=desc,
        )
        # Close write ends before draining read ends
        os.close(out_w)
        os.close(err_w)

        stdout_bytes = drain_pipe(out_r)
        stderr_bytes = drain_pipe(err_r)

        return PipeRunResult(
            returncode=code,
            stdout_bytes=stdout_bytes,
            stderr_bytes=stderr_bytes,
        )
    finally:
        for fd in (out_r, err_r):
            with contextlib.suppress(OSError):
                os.close(fd)
        desc.close()


def run_batch_stdio_in_pipes(
    *,
    argv: list[str] | None = None,
    stdin_data: bytes | io.BytesIO = b"",
    composition_handler: Callable[..., Mapping[str, Any]] | None = None,
) -> PipeRunResult:
    """Run batch _run_stdio with controlled descriptors backed by OS pipes and collect output.

    Guarantees all pipe ends and descriptors are cleanly closed even on test failure.
    """
    from ipc.batch_stdio import _run_stdio as _run_batch_stdio

    out_r, out_w, err_r, err_w = make_pipes()
    null_fd = os.open(os.devnull, os.O_RDWR)
    desc = ControlledDescriptors(out_w, err_w, null_fd)

    stdin_stream = stdin_data if isinstance(stdin_data, io.BytesIO) else io.BytesIO(stdin_data)

    try:
        code = _run_batch_stdio(
            argv=argv if argv is not None else [],
            _composition_handler=composition_handler,
            _stdin_stream=stdin_stream,
            _descriptors=desc,
        )
        # Close write ends before draining read ends
        os.close(out_w)
        os.close(err_w)

        stdout_bytes = drain_pipe(out_r)
        stderr_bytes = drain_pipe(err_r)

        return PipeRunResult(
            returncode=code,
            stdout_bytes=stdout_bytes,
            stderr_bytes=stderr_bytes,
        )
    finally:
        for fd in (out_r, err_r):
            with contextlib.suppress(OSError):
                os.close(fd)
        desc.close()
