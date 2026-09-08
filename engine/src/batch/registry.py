"""Lightweight, immutable operation metadata registry for CAD Copilot batch operations.

This module defines operation descriptors and an immutable registry for batch
operations. It is strictly metadata-only: zero handlers, zero stubs, zero runtime
dependencies, and zero COM/Solid Edge execution.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Literal

from batch.models import BatchConfigurationError

SafetyClass = Literal["read_only_source"]
DocumentLifecycle = Literal["open_existing_close_without_save"]
CollisionPolicy = Literal["fail_if_exists"]

APPROVED_SAFETY_CLASSES: frozenset[str] = frozenset({"read_only_source"})
APPROVED_DOCUMENT_LIFECYCLES: frozenset[str] = frozenset({"open_existing_close_without_save"})
APPROVED_COLLISION_POLICIES: frozenset[str] = frozenset({"fail_if_exists"})


@dataclass(frozen=True)
class OperationDescriptor:
    """Immutable metadata descriptor defining capabilities and safety policy for an operation."""

    operation_id: str
    input_extensions: tuple[str, ...]
    output_formats: tuple[str, ...]
    progress_label: str
    safety_class: SafetyClass = "read_only_source"
    document_lifecycle: DocumentLifecycle = "open_existing_close_without_save"
    collision_policy: CollisionPolicy = "fail_if_exists"

    def __post_init__(self) -> None:
        if not isinstance(self.operation_id, str) or not self.operation_id.strip():
            raise ValueError("OperationDescriptor.operation_id must be a non-empty string")
        if self.operation_id != self.operation_id.strip():
            raise ValueError("OperationDescriptor.operation_id must not have leading or trailing whitespace")

        # input_extensions validation
        if not self.input_extensions:
            raise ValueError(f"OperationDescriptor '{self.operation_id}' must define at least one input extension")

        object.__setattr__(self, "input_extensions", tuple(self.input_extensions))
        lower_exts: list[str] = []
        for ext in self.input_extensions:
            if not isinstance(ext, str) or not ext.startswith(".") or len(ext) <= 1:
                raise ValueError(
                    f"OperationDescriptor '{self.operation_id}' extension '{ext}' must start with '.' and have content"
                )
            if ext != ext.lower():
                raise ValueError(f"OperationDescriptor '{self.operation_id}' extension '{ext}' must be lowercase")
            if ext.strip() != ext:
                raise ValueError(
                    f"OperationDescriptor '{self.operation_id}' extension '{ext}' must not have whitespace"
                )
            lower_exts.append(ext.lower())

        if len(lower_exts) != len(set(lower_exts)):
            raise ValueError(
                f"OperationDescriptor '{self.operation_id}' contains duplicate or case-colliding input extensions"
            )

        # output_formats validation
        if not self.output_formats:
            raise ValueError(f"OperationDescriptor '{self.operation_id}' must define at least one output format")

        object.__setattr__(self, "output_formats", tuple(self.output_formats))
        lower_fmts: list[str] = []
        for fmt in self.output_formats:
            if not isinstance(fmt, str) or not fmt.strip() or fmt.startswith("."):
                raise ValueError(
                    f"OperationDescriptor '{self.operation_id}' format '{fmt}' must be a non-empty format identifier without leading dot"
                )
            if fmt != fmt.lower():
                raise ValueError(f"OperationDescriptor '{self.operation_id}' format '{fmt}' must be lowercase")
            if fmt.strip() != fmt:
                raise ValueError(f"OperationDescriptor '{self.operation_id}' format '{fmt}' must not have whitespace")
            lower_fmts.append(fmt.lower())

        if len(lower_fmts) != len(set(lower_fmts)):
            raise ValueError(
                f"OperationDescriptor '{self.operation_id}' contains duplicate or case-colliding output formats"
            )

        # progress_label validation
        if not isinstance(self.progress_label, str) or not self.progress_label.strip():
            raise ValueError(f"OperationDescriptor '{self.operation_id}' progress_label must be a non-empty string")
        if self.progress_label != self.progress_label.strip():
            raise ValueError(
                f"OperationDescriptor '{self.operation_id}' progress_label must not have leading or trailing whitespace"
            )

        # Policy validation
        if self.safety_class not in APPROVED_SAFETY_CLASSES:
            raise ValueError(f"Unsupported safety_class '{self.safety_class}' for operation '{self.operation_id}'")
        if self.document_lifecycle not in APPROVED_DOCUMENT_LIFECYCLES:
            raise ValueError(
                f"Unsupported document_lifecycle '{self.document_lifecycle}' for operation '{self.operation_id}'"
            )
        if self.collision_policy not in APPROVED_COLLISION_POLICIES:
            raise ValueError(
                f"Unsupported collision_policy '{self.collision_policy}' for operation '{self.operation_id}'"
            )


class OperationRegistry:
    """Immutable, validated registry of batch operation descriptors."""

    def __init__(self, descriptors: Sequence[OperationDescriptor]) -> None:
        if not descriptors:
            raise ValueError("OperationRegistry must be initialized with at least one descriptor")

        seen_ids: set[str] = set()
        validated_descriptors: list[OperationDescriptor] = []
        lookup: dict[str, OperationDescriptor] = {}

        for desc in descriptors:
            if not isinstance(desc, OperationDescriptor):
                raise TypeError(f"Expected OperationDescriptor, got {type(desc).__name__}")

            canonical_id = desc.operation_id
            lower_id = canonical_id.lower()
            if lower_id in seen_ids:
                raise ValueError(f"Duplicate or case-colliding operation ID '{canonical_id}' in registry")
            seen_ids.add(lower_id)
            validated_descriptors.append(desc)
            lookup[canonical_id] = desc

        self._descriptors: tuple[OperationDescriptor, ...] = tuple(validated_descriptors)
        self._lookup: dict[str, OperationDescriptor] = lookup

    @property
    def descriptors(self) -> tuple[OperationDescriptor, ...]:
        """Return an immutable ordered tuple of registered operation descriptors."""
        return self._descriptors

    @property
    def operation_ids(self) -> tuple[str, ...]:
        """Return an immutable ordered tuple of registered operation IDs."""
        return tuple(d.operation_id for d in self._descriptors)

    def get(self, operation_id: str) -> OperationDescriptor:
        """Lookup an operation descriptor by its exact canonical ID.

        Raises BatchConfigurationError if the operation ID is not registered.
        """
        if not isinstance(operation_id, str):
            raise BatchConfigurationError(f"Operation ID must be a string, got {type(operation_id).__name__}")
        if operation_id not in self._lookup:
            raise BatchConfigurationError(
                f"Unknown batch operation '{operation_id}'. Approved operations: {list(self._lookup.keys())}"
            )
        return self._lookup[operation_id]

    def __getitem__(self, operation_id: str) -> OperationDescriptor:
        return self.get(operation_id)

    def __contains__(self, operation_id: object) -> bool:
        if not isinstance(operation_id, str):
            return False
        return operation_id in self._lookup

    def __len__(self) -> int:
        return len(self._descriptors)

    def __iter__(self) -> Iterator[OperationDescriptor]:
        return iter(self._descriptors)


def build_initial_registry() -> OperationRegistry:
    """Construct the canonical initial registry containing exactly the two guaranteed M5 operations.

    Operations:
    1. export_3d (.par, .psm, .asm -> step, stl)
    2. publish_drawing (.dft -> pdf, dxf)
    """
    return OperationRegistry(
        (
            OperationDescriptor(
                operation_id="export_3d",
                input_extensions=(".par", ".psm", ".asm"),
                output_formats=("step", "stl"),
                progress_label="Export 3D CAD",
                safety_class="read_only_source",
                document_lifecycle="open_existing_close_without_save",
                collision_policy="fail_if_exists",
            ),
            OperationDescriptor(
                operation_id="publish_drawing",
                input_extensions=(".dft",),
                output_formats=("pdf", "dxf"),
                progress_label="Publish Drawing",
                safety_class="read_only_source",
                document_lifecycle="open_existing_close_without_save",
                collision_policy="fail_if_exists",
            ),
        )
    )


__all__ = [
    "APPROVED_COLLISION_POLICIES",
    "APPROVED_DOCUMENT_LIFECYCLES",
    "APPROVED_SAFETY_CLASSES",
    "CollisionPolicy",
    "DocumentLifecycle",
    "OperationDescriptor",
    "OperationRegistry",
    "SafetyClass",
    "build_initial_registry",
]
