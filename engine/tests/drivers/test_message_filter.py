"""Unit tests for Windows OLE SEMessageFilter retry logic, backoff, and bounds."""

from __future__ import annotations

from drivers.solidedge.message_filter import (
    PENDINGMSG_WAITDEFPROCESS,
    SERVERCALL_ISHANDLED,
    SERVERCALL_REJECTED,
    SERVERCALL_RETRYLATER,
    SEMessageFilter,
    register_message_filter,
    revoke_message_filter,
)


def test_handle_incoming_call_returns_handled() -> None:
    """Proves that incoming server calls return SERVERCALL_ISHANDLED (0)."""
    filter_inst = SEMessageFilter()
    assert filter_inst.HandleInComingCall(0, None, 0, None) == SERVERCALL_ISHANDLED


def test_message_pending_returns_waitdefprocess() -> None:
    """Proves that message pending returns PENDINGMSG_WAITDEFPROCESS (2)."""
    filter_inst = SEMessageFilter()
    assert filter_inst.MessagePending(None, 0, 0) == PENDINGMSG_WAITDEFPROCESS


def test_retry_rejected_call_retrylater_fixed_wait() -> None:
    """Proves that SERVERCALL_RETRYLATER (2) returns a 1000ms delay."""
    filter_inst = SEMessageFilter()
    filter_inst.begin_call()
    result = filter_inst.RetryRejectedCall(None, 100, SERVERCALL_RETRYLATER)
    assert result == 1000


def test_retry_rejected_call_backoff_progression_and_cap() -> None:
    """Proves that SERVERCALL_REJECTED (1) steps through 100ms -> 250ms -> 500ms and caps at 10 attempts."""
    filter_inst = SEMessageFilter()
    filter_inst.begin_call()

    # Attempts 1 to 3 -> 100ms
    assert filter_inst.RetryRejectedCall(None, 100, SERVERCALL_REJECTED) == 100
    assert filter_inst.RetryRejectedCall(None, 200, SERVERCALL_REJECTED) == 100
    assert filter_inst.RetryRejectedCall(None, 300, SERVERCALL_REJECTED) == 100

    # Attempts 4 to 6 -> 250ms
    assert filter_inst.RetryRejectedCall(None, 400, SERVERCALL_REJECTED) == 250
    assert filter_inst.RetryRejectedCall(None, 500, SERVERCALL_REJECTED) == 250
    assert filter_inst.RetryRejectedCall(None, 600, SERVERCALL_REJECTED) == 250

    # Attempts 7 to 9 -> 500ms
    assert filter_inst.RetryRejectedCall(None, 700, SERVERCALL_REJECTED) == 500
    assert filter_inst.RetryRejectedCall(None, 800, SERVERCALL_REJECTED) == 500
    assert filter_inst.RetryRejectedCall(None, 900, SERVERCALL_REJECTED) == 500

    # Attempt 10 -> Canceled (-1)
    assert filter_inst.RetryRejectedCall(None, 1000, SERVERCALL_REJECTED) == -1


def test_retry_rejected_call_timeout_bound_at_30s() -> None:
    """Proves that any call exceeding 30,000ms total tick count is canceled immediately."""
    filter_inst = SEMessageFilter()
    filter_inst.begin_call()

    # dwTickCount > 30000 returns -1 even on first attempt
    assert filter_inst.RetryRejectedCall(None, 30001, SERVERCALL_RETRYLATER) == -1
    assert filter_inst.RetryRejectedCall(None, 30001, SERVERCALL_REJECTED) == -1


def test_retry_rejected_call_unrecognized_reject_type() -> None:
    """Proves that unrecognized dwRejectType values return -1 (cancel immediately)."""
    filter_inst = SEMessageFilter()
    filter_inst.begin_call()

    assert filter_inst.RetryRejectedCall(None, 100, 999) == -1
    assert filter_inst.RetryRejectedCall(None, 100, 0) == -1


def test_begin_call_resets_attempt_counter() -> None:
    """Proves that begin_call resets the attempt counter so each call starts fresh."""
    filter_inst = SEMessageFilter()
    filter_inst.begin_call()

    for _ in range(5):
        filter_inst.RetryRejectedCall(None, 100, SERVERCALL_REJECTED)

    assert filter_inst._attempt_count == 5

    filter_inst.begin_call()
    assert filter_inst._attempt_count == 0

    # First attempt after reset should return 100ms
    assert filter_inst.RetryRejectedCall(None, 100, SERVERCALL_REJECTED) == 100


def test_registration_and_revocation_helpers_none_safety() -> None:
    """Proves that register_message_filter and revoke_message_filter gracefully handle None pythoncom."""
    assert register_message_filter(None) is None
    # Should not raise
    revoke_message_filter(None)
    revoke_message_filter(None, filter_obj=None)
