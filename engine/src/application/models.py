"""Typed domain and transfer models for CAD generation application orchestration.

Invariants:
    - Immutability: all request, proposal, and context structures are frozen dataclasses.
    - Zero COM/Driver Imports: leaf domain definitions with zero CAD runtime dependencies.
    - Defensive Invariants: runtime preflight checks for version, unit, source ID, and bounds.
    - Privacy Boundary: prepared context contains no raw prompts, credentials, or host paths.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Protocol, TypeAlias

from geometry.plan_models import DefaultApplied, FeaturePlan, ValidationDiagnostic
from manifests import PreparedManifestData, RunManifestContext

MAX_REQUEST_ID_LENGTH: int = 96
MAX_SOURCE_ID_LENGTH: int = 128
SOURCE_ID_PATTERN: re.Pattern[str] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True)
class ExampleGenerationRequest:
    """Request to generate CAD geometry from a deterministic built-in example."""

    contract_version: Literal["1.0"]
    request_id: str
    kind: Literal["example_plan"]
    unit: Literal["mm"]
    example_id: str
    metadata: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        if self.contract_version != "1.0":
            raise ValueError(f"Unsupported contract_version '{self.contract_version}', expected '1.0'")
        if (
            not isinstance(self.request_id, str)
            or not (1 <= len(self.request_id) <= MAX_REQUEST_ID_LENGTH)
            or not self.request_id.strip()
        ):
            raise ValueError(f"request_id must be a string of 1-{MAX_REQUEST_ID_LENGTH} characters")
        if self.kind != "example_plan":
            raise ValueError(f"Invalid kind '{self.kind}' for ExampleGenerationRequest")
        if self.unit != "mm":
            raise ValueError(f"Unsupported unit '{self.unit}', expected 'mm'")
        if not isinstance(self.example_id, str) or not self.example_id.strip():
            raise ValueError("example_id must be a non-empty string")
        if self.metadata is not None and not isinstance(self.metadata, Mapping):
            raise ValueError("metadata must be a Mapping if provided")


@dataclass(frozen=True)
class PromptGenerationRequest:
    """Request to generate CAD geometry from a natural language prompt."""

    contract_version: Literal["1.0"]
    request_id: str
    kind: Literal["prompt_to_cad"]
    unit: Literal["mm"]
    prompt: str
    metadata: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        if self.contract_version != "1.0":
            raise ValueError(f"Unsupported contract_version '{self.contract_version}', expected '1.0'")
        if (
            not isinstance(self.request_id, str)
            or not (1 <= len(self.request_id) <= MAX_REQUEST_ID_LENGTH)
            or not self.request_id.strip()
        ):
            raise ValueError(f"request_id must be a string of 1-{MAX_REQUEST_ID_LENGTH} characters")
        if self.kind != "prompt_to_cad":
            raise ValueError(f"Invalid kind '{self.kind}' for PromptGenerationRequest")
        if self.unit != "mm":
            raise ValueError(f"Unsupported unit '{self.unit}', expected 'mm'")
        if not isinstance(self.prompt, str) or not self.prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        if self.metadata is not None and not isinstance(self.metadata, Mapping):
            raise ValueError("metadata must be a Mapping if provided")


GenerationRequest: TypeAlias = ExampleGenerationRequest | PromptGenerationRequest


@dataclass(frozen=True)
class PlanProposal:
    """Untrusted plan proposal returned by an example or prompt resolver."""

    plan_payload: Mapping[str, object]
    provenance: Literal["example_plan", "ai_proposal"]
    source_id: str
    warnings: Sequence[Mapping[str, str]] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.plan_payload, Mapping):
            raise ValueError("plan_payload must be a Mapping")
        if self.provenance not in ("example_plan", "ai_proposal"):
            raise ValueError(f"Invalid provenance '{self.provenance}', expected 'example_plan' or 'ai_proposal'")
        if (
            not isinstance(self.source_id, str)
            or not (1 <= len(self.source_id) <= MAX_SOURCE_ID_LENGTH)
            or not SOURCE_ID_PATTERN.match(self.source_id)
        ):
            raise ValueError(
                f"source_id must be 1-{MAX_SOURCE_ID_LENGTH} characters matching ^[A-Za-z0-9][A-Za-z0-9._-]*$"
            )
        if isinstance(self.warnings, (str, bytes, Mapping)) or not isinstance(self.warnings, Sequence):
            raise ValueError("warnings must be a sequence of structured warning mappings")

        validated_warnings: list[Mapping[str, str]] = []
        for item in self.warnings:
            if isinstance(item, Mapping):
                if not all(isinstance(k, str) and isinstance(v, str) for k, v in item.items()):
                    raise ValueError("Warning mappings must contain only string keys and string values")
                validated_warnings.append(dict(item))
            else:
                raise ValueError(f"Warning item must be a structured string mapping, got {type(item).__name__}")

        object.__setattr__(self, "warnings", tuple(validated_warnings))


@dataclass(frozen=True)
class PreparedPlanContext:
    """Immutable context produced after canonical validation, normalization, and lowering."""

    validated_plan: FeaturePlan
    lowered_payload: Mapping[str, object]
    request_id: str
    kind: Literal["example_plan", "prompt_to_cad"]
    unit: Literal["mm"]
    provenance: Literal["example_plan", "ai_proposal"]
    source_id: str
    mode: Literal["capability_first", "strict"]
    diagnostics: tuple[ValidationDiagnostic, ...] = ()
    applied_defaults: tuple[DefaultApplied, ...] = ()
    warnings: tuple[str, ...] = ()
    request_metadata: Mapping[str, str] | None = None
    prepared_manifest_data: PreparedManifestData | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.source_id, str)
            or not (1 <= len(self.source_id) <= MAX_SOURCE_ID_LENGTH)
            or not SOURCE_ID_PATTERN.match(self.source_id)
        ):
            raise ValueError(
                f"source_id must be 1-{MAX_SOURCE_ID_LENGTH} characters matching ^[A-Za-z0-9][A-Za-z0-9._-]*$"
            )
        if not isinstance(self.diagnostics, tuple):
            object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        if not isinstance(self.applied_defaults, tuple):
            object.__setattr__(self, "applied_defaults", tuple(self.applied_defaults))
        if not isinstance(self.warnings, tuple):
            object.__setattr__(self, "warnings", tuple(self.warnings))
        if self.prepared_manifest_data is not None and not isinstance(
            self.prepared_manifest_data, PreparedManifestData
        ):
            raise ValueError("prepared_manifest_data must be a PreparedManifestData instance if provided")
        if self.request_metadata is not None:
            if not isinstance(self.request_metadata, Mapping):
                raise ValueError("request_metadata must be a Mapping if provided")
            copied: dict[str, str] = {}
            for k in ("source", "label", "job_id"):
                if k in self.request_metadata:
                    v = self.request_metadata[k]
                    if k == "source" and (
                        not isinstance(v, str) or v not in ("desktop_app", "cad_copilot", "local_agent")
                    ):
                        raise ValueError(
                            f"Invalid metadata source '{v}'; must be desktop_app, cad_copilot, or local_agent"
                        )
                    if k == "label" and (not isinstance(v, str) or len(v) > 128):
                        raise ValueError("Metadata label must be a string of at most 128 characters")
                    if k == "job_id" and (
                        not isinstance(v, str) or not (1 <= len(v) <= 128) or not re.match(r"^[A-Za-z0-9._-]+$", v)
                    ):
                        raise ValueError(
                            f"Invalid metadata job_id '{v}'; must match ^[A-Za-z0-9._-]+$ and be 1-128 characters"
                        )
                    copied[k] = v
            object.__setattr__(self, "request_metadata", MappingProxyType(copied))


# Injected resolver and factory types for clean inversion of control
ExampleResolver: TypeAlias = Callable[[ExampleGenerationRequest], PlanProposal]
PromptResolver: TypeAlias = Callable[[PromptGenerationRequest], PlanProposal]
ExecutorFactory: TypeAlias = Callable[..., Any]


class ArtifactFinalizer(Protocol):
    """Callable protocol for finalizing, validating, and publishing request artifacts."""

    def __call__(
        self,
        executor: Any,
        success_result: Any,
        output_root: Path | str,
        request_id: str,
        /,
        *,
        manifest_context: RunManifestContext | None = None,
    ) -> Any: ...
