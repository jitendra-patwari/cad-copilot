"""Strict batch stdio IPC entrypoint.

Provides bounded request processing, exact single-response stdout delivery,
curated stderr progress events, cooperative signal cancellation, and exit policy enforcement.
"""

from __future__ import annotations

import contextlib
import signal
import sys
import threading
from collections.abc import Callable, Iterator, Mapping
from typing import (
    Any,
    BinaryIO,
    Final,
)

from ipc.descriptors import (
    ControlledDescriptors as _ControlledDescriptors,
)
from ipc.descriptors import (
    establish_controlled_descriptors as _establish_controlled_descriptors,
)
from ipc.wire import write_all as _write_all

_BATCH_FATAL_DIAGNOSTIC_BYTES: Final[bytes] = (
    b'{"type":"diagnostic","phase":"fatal","message":"Batch process failed before a contract response could be produced."}\n'
)

__all__ = ["main"]


@contextlib.contextmanager
def _cooperative_cancellation_scope() -> Iterator[threading.Event]:
    """Scoped signal handler that sets a private cancellation Event upon SIGINT or SIGBREAK.

    Async-safe: performs only Event.set(), avoiding raising exceptions inside COM or during teardown.
    Installs transactionally and rolls back on failure. Fails closed if installation or restoration fails.
    """
    cancel_event = threading.Event()

    def _signal_handler(signum: int, frame: Any) -> None:
        cancel_event.set()

    original_handlers: dict[int, Any] = {}
    signals_to_hook: list[int] = [int(signal.SIGINT)]
    if hasattr(signal, "SIGBREAK"):
        signals_to_hook.append(int(signal.SIGBREAK))

    try:
        for sig in signals_to_hook:
            prev = signal.signal(sig, _signal_handler)
            original_handlers[sig] = prev
    except Exception as exc:
        for sig_num, prev_handler in original_handlers.items():
            with contextlib.suppress(Exception):
                signal.signal(sig_num, prev_handler)
        raise RuntimeError(f"Failed to install cooperative batch signal handlers: {exc}") from exc

    try:
        yield cancel_event
    finally:
        restoration_error: Exception | None = None
        for sig_num, prev_handler in original_handlers.items():
            try:
                signal.signal(sig_num, prev_handler)
            except Exception as exc:
                if restoration_error is None:
                    restoration_error = exc
        if restoration_error is not None:
            raise RuntimeError(f"Failed to restore batch signal handlers: {restoration_error}") from restoration_error


def main(
    argv: list[str] | None = None,
    *,
    _composition_handler: Callable[..., Mapping[str, Any]] | None = None,
) -> int:
    """Execute the CAD Copilot Batch stdio IPC entrypoint.

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
    _composition_handler: Callable[..., Mapping[str, Any]] | None = None,
    _stdin_stream: BinaryIO | None = None,
    _descriptors: _ControlledDescriptors | None = None,
) -> int:
    """Internal runner for batch stdio IPC with configurable stream and descriptor injection."""
    descriptors = _descriptors
    owns_descriptors = False

    try:
        if descriptors is None:
            descriptors = _establish_controlled_descriptors(save_restore_state=False)
            owns_descriptors = True

        # Production modules are strictly imported AFTER descriptor redirection is active
        from ipc.batch_contracts import (
            BatchInvalidRequestError,
            BatchPayloadTooLargeError,
            BatchResponseValidationError,
            build_fallback_failed_response,
            build_rejected_batch_response,
            build_typed_batch_request,
            decode_and_parse_request_json,
            is_safe_request_id,
            read_bounded_request,
            serialize_response,
            validate_response_payload,
        )
        from ipc.batch_progress import (
            emit_batch_fatal_diagnostic,
            emit_batch_progress,
        )

        # Validate arguments; unexpected flags indicate process misuse
        cmd_args = sys.argv[1:] if argv is None else argv
        if len(cmd_args) > 0:
            emit_batch_fatal_diagnostic(descriptors.orig_stderr_fd)
            return 1

        # Read incoming request from binary input stream up to wire limit
        input_stream = _stdin_stream if _stdin_stream is not None else sys.stdin.buffer
        oversize_error = False
        raw_bytes = b""

        try:
            raw_bytes = read_bounded_request(input_stream)
        except BatchPayloadTooLargeError:
            oversize_error = True

        if oversize_error:
            resp_payload = build_rejected_batch_response("PAYLOAD_TOO_LARGE", request_id="unknown")
            validate_response_payload(resp_payload)
            serialized = serialize_response(resp_payload)
            _write_all(descriptors.orig_stdout_fd, serialized)
            return 0

        # Strict JSON decode and typed request construction
        try:
            parsed_dict, _safe_req_id = decode_and_parse_request_json(raw_bytes)
            typed_req = build_typed_batch_request(parsed_dict)
        except BatchPayloadTooLargeError:
            resp_payload = build_rejected_batch_response("PAYLOAD_TOO_LARGE", request_id="unknown")
            validate_response_payload(resp_payload)
            serialized = serialize_response(resp_payload)
            _write_all(descriptors.orig_stdout_fd, serialized)
            return 0
        except BatchInvalidRequestError as exc:
            resp_payload = build_rejected_batch_response(exc.code, request_id=exc.request_id)
            validate_response_payload(
                resp_payload,
                expected_request_id=exc.request_id if is_safe_request_id(exc.request_id) else "unknown",
            )
            serialized = serialize_response(resp_payload)
            _write_all(descriptors.orig_stdout_fd, serialized)
            return 0

        def _progress_observer(update: Any) -> None:
            emit_batch_progress(descriptors.orig_stderr_fd, update)

        # Install cooperative signal handling for execution lifetime
        with _cooperative_cancellation_scope() as cancel_event:
            try:
                if _composition_handler is not None:
                    service_result = _composition_handler(
                        typed_req,
                        cancellation_check=cancel_event.is_set,
                        observer=_progress_observer,
                    )
                else:
                    from ipc.batch_composition import run_batch

                    service_result = run_batch(
                        typed_req,
                        cancellation_check=cancel_event.is_set,
                        observer=_progress_observer,
                    )
            except Exception:
                # Unhandled execution failure: build sanitized fallback failed response
                service_result = build_fallback_failed_response("INTERNAL_ERROR", request_id=typed_req.request_id)

        # Enforce response schema and semantic contract; fallback to sanitized internal error on violation
        try:
            validate_response_payload(service_result, expected_request_id=typed_req.request_id)
            final_response = service_result
        except BatchResponseValidationError:
            fallback = build_fallback_failed_response("INTERNAL_ERROR", request_id=typed_req.request_id)
            validate_response_payload(fallback, expected_request_id=typed_req.request_id)
            final_response = fallback

        # Serialize compact 7-bit ASCII response
        serialized_final = serialize_response(final_response, expected_request_id=typed_req.request_id)

        # Deliver single response to preserved standard output
        _write_all(descriptors.orig_stdout_fd, serialized_final)
        return 0

    except KeyboardInterrupt:
        # Pre-handler or unhooked console cancellation: exit 130 with no stdout
        return 130
    except BaseException:
        # Every fatal unhandled exception: emit sanitized diagnostic and exit 1
        if descriptors is not None:
            with contextlib.suppress(Exception):
                _write_all(descriptors.orig_stderr_fd, _BATCH_FATAL_DIAGNOSTIC_BYTES)
        else:
            with contextlib.suppress(Exception):
                _write_all(2, _BATCH_FATAL_DIAGNOSTIC_BYTES)
        return 1
    finally:
        if owns_descriptors and descriptors is not None:
            descriptors.close()


if __name__ == "__main__":
    sys.exit(main())
