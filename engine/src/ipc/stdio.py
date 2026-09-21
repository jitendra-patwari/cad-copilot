"""Strict generation stdio IPC entrypoint.

Provides bounded request processing, exact single-response stdout delivery,
curated stderr progress events, and exit policy enforcement.
"""

from __future__ import annotations

import contextlib
import signal
import sys
import threading
from collections.abc import Callable, Iterator, Mapping
from typing import (
    TYPE_CHECKING,
    Any,
    BinaryIO,
)

from ipc.descriptors import (
    ControlledDescriptors as _ControlledDescriptors,
)
from ipc.descriptors import (
    establish_controlled_descriptors as _establish_controlled_descriptors,
)
from ipc.progress import (
    emit_fatal_diagnostic as _emit_fatal_diagnostic,
)
from ipc.progress import (
    emit_progress as _emit_progress,
)
from ipc.wire import write_all as _write_all

__all__ = ["main"]


@contextlib.contextmanager
def _scoped_generation_cancellation() -> Iterator[None]:
    """Scoped signal handler translating Windows SIGBREAK to KeyboardInterrupt.

    Ensures that targeted Windows CTRL_BREAK_EVENT unwinds GenerationService
    try/finally blocks and cleans up CAD resources, exiting 130 with empty stdout.
    Repeated signals during unwinding are suppressed with SIG_IGN to prevent
    interrupting teardown.
    """
    signals_to_hook: list[int] = []
    if hasattr(signal, "SIGBREAK"):
        signals_to_hook.append(int(signal.SIGBREAK))

    def _sigbreak_handler(signum: int, frame: Any) -> None:
        # Suppress subsequent signals so teardown is not interrupted
        for sig in signals_to_hook:
            with contextlib.suppress(Exception):
                signal.signal(sig, signal.SIG_IGN)
        raise KeyboardInterrupt()

    original_handlers: dict[int, Any] = {}
    try:
        for sig in signals_to_hook:
            original_handlers[sig] = signal.signal(sig, _sigbreak_handler)
    except (ValueError, OSError) as exc:
        original_handlers.clear()
        if threading.current_thread() is threading.main_thread():
            raise RuntimeError(f"Failed to install required cancellation signal handler on main thread: {exc}") from exc

    try:
        yield
    finally:
        for sig, prev_handler in original_handlers.items():
            with contextlib.suppress(Exception):
                signal.signal(sig, prev_handler)


if TYPE_CHECKING:
    from application.models import GenerationRequest


def main(
    argv: list[str] | None = None,
    *,
    _composition_handler: Callable[[GenerationRequest], Mapping[str, Any]] | None = None,
) -> int:
    """Execute the CAD Copilot Generation stdio IPC entrypoint.

    Args:
        argv: Optional command-line argument list (defaults to sys.argv[1:]).
        _composition_handler: Optional custom composition callable for test injection.

    Returns:
        Exit code: 0 for handled response, 130 for cancellation, 1 for fatal error.
    """
    return _run_stdio(
        argv=argv,
        _composition_handler=_composition_handler,
    )


def _run_stdio(
    argv: list[str] | None = None,
    *,
    _composition_handler: Callable[[GenerationRequest], Mapping[str, Any]] | None = None,
    _stdin_stream: BinaryIO | None = None,
    _descriptors: _ControlledDescriptors | None = None,
) -> int:
    """Internal runner for generation stdio IPC with configurable stream and descriptor injection."""
    descriptors = _descriptors
    owns_descriptors = False

    try:
        if descriptors is None:
            descriptors = _establish_controlled_descriptors(save_restore_state=False)
            owns_descriptors = True

        with _scoped_generation_cancellation():
            # Production modules are strictly imported AFTER descriptor redirection is active
            from ipc.contracts import (
                InvalidRequestError,
                PayloadTooLargeError,
                ResponseValidationError,
                build_fallback_internal_error_response,
                build_rejected_error_response,
                build_typed_generation_request,
                decode_and_parse_request_json,
                read_bounded_request,
                serialize_response,
                validate_response_payload,
            )

            # Validate arguments; unexpected flags indicate process misuse
            cmd_args = sys.argv[1:] if argv is None else argv
            if len(cmd_args) > 0:
                _emit_fatal_diagnostic(descriptors.orig_stderr_fd)
                return 1

            # Verify installed package metadata matches baseline before CAD activation
            try:
                import importlib.metadata

                installed_version = importlib.metadata.version("cad-copilot")
                if installed_version != "0.1.0":
                    _emit_fatal_diagnostic(descriptors.orig_stderr_fd)
                    return 1
            except Exception:
                _emit_fatal_diagnostic(descriptors.orig_stderr_fd)
                return 1

            # In packaged desktop runtime, verify bundled AI SDK is importable before request ingestion
            try:
                import google.genai  # noqa: F401
            except ImportError:
                if getattr(sys, "frozen", False):
                    _emit_fatal_diagnostic(descriptors.orig_stderr_fd)
                    return 1

            # Read incoming request from binary input stream up to wire limit
            input_stream = _stdin_stream if _stdin_stream is not None else sys.stdin.buffer
            oversize_error = False
            raw_bytes = b""

            try:
                raw_bytes = read_bounded_request(input_stream)
            except PayloadTooLargeError:
                oversize_error = True

            # Signal request reception before payload validation
            _emit_progress(descriptors.orig_stderr_fd, "request_received")

            if oversize_error:
                resp_payload = build_rejected_error_response("PAYLOAD_TOO_LARGE", request_id="unknown")
                validate_response_payload(resp_payload)
                serialized = serialize_response(resp_payload)
                _emit_progress(descriptors.orig_stderr_fd, "response_ready")
                _write_all(descriptors.orig_stdout_fd, serialized)
                return 0

            # Strict JSON decode and typed request construction
            try:
                parsed_dict, _safe_req_id = decode_and_parse_request_json(raw_bytes)
                typed_req = build_typed_generation_request(parsed_dict)
            except PayloadTooLargeError:
                resp_payload = build_rejected_error_response("PAYLOAD_TOO_LARGE", request_id="unknown")
                validate_response_payload(resp_payload)
                serialized = serialize_response(resp_payload)
                _emit_progress(descriptors.orig_stderr_fd, "response_ready")
                _write_all(descriptors.orig_stdout_fd, serialized)
                return 0
            except InvalidRequestError as exc:
                resp_payload = build_rejected_error_response(exc.code, request_id=exc.request_id)
                validate_response_payload(resp_payload)
                serialized = serialize_response(resp_payload)
                _emit_progress(descriptors.orig_stderr_fd, "response_ready")
                _write_all(descriptors.orig_stdout_fd, serialized)
                return 0

            # Signal successful schema and model validation
            _emit_progress(descriptors.orig_stderr_fd, "request_validated")

            # Emit progress immediately before service execution
            _emit_progress(descriptors.orig_stderr_fd, "generation_started")

            try:
                if _composition_handler is not None:
                    service_result = _composition_handler(typed_req)
                else:
                    from ipc.composition import run_generation

                    service_result = run_generation(typed_req)
            except KeyboardInterrupt:
                raise
            except Exception:
                # Unhandled service failure: build sanitized fallback internal error
                service_result = build_fallback_internal_error_response(typed_req.request_id)

            # Enforce response schema contract; fallback to sanitized internal error on violation
            try:
                validate_response_payload(service_result)
                final_response = service_result
            except ResponseValidationError:
                fallback = build_fallback_internal_error_response(typed_req.request_id)
                validate_response_payload(fallback)
                final_response = fallback

            # Serialize compact 7-bit ASCII response
            serialized_final = serialize_response(final_response)

            # Emit contract completion progress before delivering response
            _emit_progress(descriptors.orig_stderr_fd, "response_ready")

            # Deliver single response to preserved standard output
            _write_all(descriptors.orig_stdout_fd, serialized_final)
            return 0

    except KeyboardInterrupt:
        # Cancellation via Ctrl+C / CTRL_BREAK: allow Python unwinding, no stdout response, exit 130
        return 130
    except BaseException:
        # Every non-KeyboardInterrupt BaseException (including SystemExit):
        # Treat as fatal: emit sanitized diagnostic, keep stdout empty, exit 1
        if descriptors is not None:
            with contextlib.suppress(Exception):
                _emit_fatal_diagnostic(descriptors.orig_stderr_fd)
        else:
            with contextlib.suppress(Exception):
                _write_all(
                    2,
                    b'{"type":"diagnostic","phase":"fatal","message":"Generation process failed before a contract response could be produced."}\n',
                )
        return 1
    finally:
        if owns_descriptors and descriptors is not None:
            descriptors.close()


if __name__ == "__main__":
    sys.exit(main())
