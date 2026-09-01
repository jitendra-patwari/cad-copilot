"""Windows OLE IMessageFilter implementation for Solid Edge COM retry management."""

from __future__ import annotations

from typing import Any, ClassVar

from .errors import describe_exception

# OLE Server Call Return Codes
SERVERCALL_ISHANDLED = 0
SERVERCALL_REJECTED = 1
SERVERCALL_RETRYLATER = 2

# OLE Message Pending Return Codes
PENDINGMSG_WAITDEFPROCESS = 2


class SEMessageFilter:
    """Windows OLE IMessageFilter implementation for Solid Edge automation.

    Handles retryable COM server busy/rejected states with attempt accounting,
    exponential backoff, and a strict 30-second bounded timeout.
    """

    _public_methods_: ClassVar[list[str]] = ["HandleInComingCall", "RetryRejectedCall", "MessagePending"]

    def __init__(self) -> None:
        self._attempt_count = 0

    def begin_call(self) -> None:
        """Reset retry attempt counter before initiating a new COM invocation."""
        self._attempt_count = 0

    def HandleInComingCall(
        self,
        dwCallType: int,
        htaskCaller: Any,
        dwTickCount: int,
        lpInterfaceInfo: Any,
    ) -> int:
        """Handle incoming call notification from OLE."""
        return SERVERCALL_ISHANDLED

    def RetryRejectedCall(
        self,
        htaskCallee: Any,
        dwTickCount: int,
        dwRejectType: int,
    ) -> int:
        """Determine whether to retry or cancel a rejected or busy COM call."""
        self._attempt_count += 1

        # Bound: 30 seconds total elapsed time or 10 retry attempts
        if dwTickCount > 30000 or self._attempt_count >= 10:
            return -1  # Cancel call -> causes COM to raise RPC_E_CALL_REJECTED

        if dwRejectType == SERVERCALL_RETRYLATER:
            return 1000  # Wait 1000ms before retrying

        if dwRejectType == SERVERCALL_REJECTED:
            if self._attempt_count <= 3:
                return 100  # 100ms
            if self._attempt_count <= 6:
                return 250  # 250ms
            return 500  # 500ms

        # Unrecognized reject type -> cancel immediately
        return -1

    def MessagePending(
        self,
        htaskCallee: Any,
        dwTickCount: int,
        dwPendingType: int,
    ) -> int:
        """Handle pending message notification while awaiting COM return."""
        return PENDINGMSG_WAITDEFPROCESS


def register_message_filter(pythoncom: Any) -> SEMessageFilter | None:
    """Register the SEMessageFilter with OLE on the current STA thread."""
    if pythoncom is None:
        return None

    filter_inst = SEMessageFilter()
    if hasattr(pythoncom, "CoRegisterMessageFilter"):
        try:
            import win32com.server.util

            iid = getattr(pythoncom, "IID_IMessageFilter", None)
            wrapped_filter = (
                win32com.server.util.wrap(filter_inst, iid)
                if iid is not None
                else win32com.server.util.wrap(filter_inst)
            )
            pythoncom.CoRegisterMessageFilter(wrapped_filter)
        except Exception as exc:
            describe_exception(exc, "Failed to register OLE SEMessageFilter")

    return filter_inst


def revoke_message_filter(pythoncom: Any, filter_obj: Any = None) -> None:
    """Revoke any active OLE IMessageFilter on the current STA thread."""
    if pythoncom is None or not hasattr(pythoncom, "CoRegisterMessageFilter"):
        return

    try:
        pythoncom.CoRegisterMessageFilter(None)
    except Exception as exc:
        describe_exception(exc, "Failed to revoke OLE SEMessageFilter")


__all__ = [
    "SEMessageFilter",
    "register_message_filter",
    "revoke_message_filter",
]
