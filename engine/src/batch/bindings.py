"""Lightweight, immutable operation handler bindings for CAD Copilot batch operations.

Keyed by exact operation ID, a binding supplies a request-scoped handler factory
that captures the active CADRuntimeABC and returns a single-format BatchOperationHandler.
Validates exact parity against an OperationRegistry so that misconfigurations fail
before any CAD process or document is opened.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Protocol

from batch.execution import BatchItemContext, BatchItemOutcome
from batch.models import BatchConfigurationError
from batch.registry import OperationRegistry
from interfaces.runtime_abc import CADRuntimeABC


class BatchOperationHandler(Protocol):
    """Protocol for a single-format document handler.

    Called once per active format for an already-opened document.
    """

    def __call__(
        self,
        doc_handle: object,
        context: BatchItemContext,
    ) -> BatchItemOutcome: ...


class BatchHandlerFactory(Protocol):
    """Protocol for a request-scoped handler factory.

    Captures the current runtime connection and returns a bound BatchOperationHandler.
    """

    def __call__(self, runtime: CADRuntimeABC) -> BatchOperationHandler: ...


@dataclass(frozen=True)
class OperationBinding:
    """Immutable mapping from an operation ID to its handler factory."""

    operation_id: str
    factory: BatchHandlerFactory

    def __post_init__(self) -> None:
        if not isinstance(self.operation_id, str) or not self.operation_id.strip():
            raise ValueError("OperationBinding.operation_id must be a non-empty string")
        if self.operation_id != self.operation_id.strip():
            raise ValueError("OperationBinding.operation_id must not have leading or trailing whitespace")
        if not callable(self.factory):
            raise TypeError(f"OperationBinding.factory must be callable, got {type(self.factory).__name__}")

    def create_handler(self, runtime: CADRuntimeABC) -> BatchOperationHandler:
        """Invoke factory with runtime and verify returned handler is callable."""
        handler = self.factory(runtime)
        if not callable(handler):
            raise BatchConfigurationError(
                f"Handler factory for operation '{self.operation_id}' returned non-callable {type(handler).__name__}"
            )
        return handler


class OperationBindings:
    """Immutable, validated lookup of batch operation bindings.

    Enforces unique operation IDs, callable factories, and exact parity with
    the provided OperationRegistry.
    """

    def __init__(
        self,
        bindings: Sequence[OperationBinding] = (),
        *,
        registry: OperationRegistry | None = None,
    ) -> None:
        seen_ids: set[str] = set()
        validated_bindings: list[OperationBinding] = []
        lookup: dict[str, OperationBinding] = {}

        for b in bindings:
            if not isinstance(b, OperationBinding):
                raise TypeError(f"Expected OperationBinding, got {type(b).__name__}")

            canonical_id = b.operation_id
            lower_id = canonical_id.lower()
            if lower_id in seen_ids:
                raise BatchConfigurationError(f"Duplicate or case-colliding operation ID '{canonical_id}' in bindings")
            seen_ids.add(lower_id)
            validated_bindings.append(b)
            lookup[canonical_id] = b

        self._bindings: tuple[OperationBinding, ...] = tuple(validated_bindings)
        self._lookup: dict[str, OperationBinding] = lookup

        if registry is not None:
            self.validate_parity(registry)

    @property
    def bindings(self) -> tuple[OperationBinding, ...]:
        """Return an immutable ordered tuple of operation bindings."""
        return self._bindings

    @property
    def operation_ids(self) -> tuple[str, ...]:
        """Return an immutable ordered tuple of bound operation IDs."""
        return tuple(b.operation_id for b in self._bindings)

    def get(self, operation_id: str) -> OperationBinding | None:
        """Lookup an operation binding by exact canonical ID, or None if not found."""
        if not isinstance(operation_id, str):
            return None
        return self._lookup.get(operation_id)

    def get_binding(self, operation_id: str) -> OperationBinding:
        """Lookup an operation binding by exact canonical ID.

        Raises BatchConfigurationError if the operation ID is not bound.
        """
        if not isinstance(operation_id, str):
            raise BatchConfigurationError(f"Operation ID must be a string, got {type(operation_id).__name__}")
        if operation_id not in self._lookup:
            raise BatchConfigurationError(
                f"Missing binding for operation '{operation_id}'. Bound operations: {list(self._lookup.keys())}"
            )
        return self._lookup[operation_id]

    def validate_parity(self, registry: OperationRegistry) -> None:
        """Validate exact 1-to-1 parity between bound operations and registered metadata."""
        if not isinstance(registry, OperationRegistry):
            raise TypeError(f"Expected OperationRegistry, got {type(registry).__name__}")

        registry_ids = set(registry.operation_ids)
        binding_ids = set(self._lookup.keys())

        missing_in_bindings = registry_ids - binding_ids
        if missing_in_bindings:
            sorted_missing = sorted(missing_in_bindings)
            raise BatchConfigurationError(f"Missing operation binding(s) for registered operations: {sorted_missing}")

        unknown_in_bindings = binding_ids - registry_ids
        if unknown_in_bindings:
            sorted_unknown = sorted(unknown_in_bindings)
            raise BatchConfigurationError(f"Unknown operation binding(s) absent from registry: {sorted_unknown}")

    def __getitem__(self, operation_id: str) -> OperationBinding:
        return self.get_binding(operation_id)

    def __contains__(self, operation_id: object) -> bool:
        if not isinstance(operation_id, str):
            return False
        return operation_id in self._lookup

    def __len__(self) -> int:
        return len(self._bindings)

    def __iter__(self) -> Iterator[OperationBinding]:
        return iter(self._bindings)
