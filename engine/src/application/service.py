"""Authoritative generation application service coordinating request lifecycle.

Invariants:
    - Single Orchestrator: one entrypoint coordinating preparation, execution, and export.
    - Vendor Neutrality: CAD kernel and AI provider access occurs strictly via injected seams.
    - Zero COM in Preparation: request preflight, resolution, and canonical preparation
      complete before CAD runtime acquisition.
    - Authoritative M3 Execution: runtime, document, executor, and artifact finalization
      follow explicit ownership, inspection authority, atomic publication, and teardown.
    - Privacy and Containment: prompts, credentials, host paths, and exception details
      are never exposed in responses or diagnostics.
"""

from __future__ import annotations

import contextlib
import logging
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal

from application.models import (
    ArtifactFinalizer,
    ExampleGenerationRequest,
    ExampleResolver,
    ExecutorFactory,
    GenerationRequest,
    PlanProposal,
    PreparedPlanContext,
    PromptGenerationRequest,
    PromptResolver,
)
from application.projection import (
    ArtifactProjectionError,
    build_accepted_response,
    build_failed_response,
    build_rejected_response,
    project_warnings,
)
from artifacts.paths import (
    ArtifactPathError,
    assert_contained,
    check_target_collisions,
    resolve_output_root,
    validate_request_id,
)
from artifacts.pipeline import finalize_request_artifacts
from drivers.solidedge.executor import SolidEdgeExecutor
from drivers.solidedge.runtime import SolidEdgeRuntime
from geometry.plan_lowering import lower_validated_feature_plan_to_payload
from geometry.plan_parser import feature_plan_from_dict
from geometry.plan_validation import validate_feature_plan
from interfaces.exceptions import (
    CADDocumentError,
    CADExecutionError,
    CADExportError,
    TeardownIncompleteError,
)
from interfaces.models import ExecutionFailure, ExecutionSuccess
from interfaces.runtime_abc import CADRuntimeABC
from manifests import (
    FREE_TEXT_CREDENTIAL_PATTERN,
    LOCAL_PATH_PATTERN,
    SENSITIVE_KEY_PATTERN,
    ManifestConfigurationError,
    RunManifestContext,
    compute_prompt_fingerprint,
    prepare_manifest_data,
)

REQUEST_ID_PATTERN: re.Pattern[str] = re.compile(r"^[A-Za-z0-9._-]+$")
logger = logging.getLogger(__name__)


def _preflight_request(
    request: GenerationRequest,
    output_root: Path | str,
    mode: Literal["capability_first", "strict"] | None,
) -> tuple[str, Path, Literal["capability_first", "strict"]]:
    """Validate request invariants, sanitize output root, and check target collisions."""
    if not isinstance(request, (ExampleGenerationRequest, PromptGenerationRequest)):
        raise ValueError("Unsupported generation request type")

    valid_req_id = validate_request_id(request.request_id)

    if mode is None:
        active_mode: Literal["capability_first", "strict"] = "capability_first"
    elif mode in ("capability_first", "strict"):
        active_mode = mode
    else:
        raise ValueError(f"Unsupported gate policy mode: {mode}")

    resolved_root = resolve_output_root(output_root, create_if_missing=False)
    if not resolved_root.exists() or not resolved_root.is_dir():
        raise ArtifactPathError(
            f"Output root '{resolved_root.name}' must be an existing directory",
            error_code="OUTPUT_PATH_NOT_ALLOWED",
        )

    final_dir = resolved_root / valid_req_id
    assert_contained(final_dir, resolved_root)

    candidates = [
        final_dir / f"{valid_req_id}.par",
        final_dir / f"{valid_req_id}.step",
        final_dir / f"{valid_req_id}.stl",
        final_dir / f"{valid_req_id}.jpg",
    ]
    check_target_collisions(final_dir, candidates)

    return valid_req_id, resolved_root, active_mode


def _prepare_canonical_plan(
    request: GenerationRequest,
    proposal: PlanProposal,
    active_mode: Literal["capability_first", "strict"],
) -> tuple[PreparedPlanContext, list[object]]:
    """Parse, validate, normalize, and lower an untrusted plan proposal."""
    warnings_acc: list[object] = list(proposal.warnings)

    expected_provenance = "example_plan" if isinstance(request, ExampleGenerationRequest) else "ai_proposal"
    if proposal.provenance != expected_provenance:
        raise ValueError(f"Proposal provenance '{proposal.provenance}' does not match request kind '{request.kind}'")

    if not isinstance(proposal.plan_payload, Mapping):
        raise ValueError("Proposal plan payload must be a mapping")

    parsed_plan = feature_plan_from_dict(dict(proposal.plan_payload))

    if parsed_plan.request_id != request.request_id:
        raise ValueError(f"Plan request_id '{parsed_plan.request_id}' does not match request '{request.request_id}'")
    if parsed_plan.units != request.unit:
        raise ValueError(f"Plan units '{parsed_plan.units}' do not match request unit '{request.unit}'")

    validated_plan = validate_feature_plan(parsed_plan, mode=active_mode)
    lowered_payload = lower_validated_feature_plan_to_payload(validated_plan)

    warnings_acc.extend(validated_plan.validation_diagnostics)
    warnings_acc.extend(validated_plan.defaults_applied)

    sanitized_warnings = tuple(project_warnings(warnings_acc))

    prompt_sha256 = compute_prompt_fingerprint(request.prompt) if isinstance(request, PromptGenerationRequest) else None

    prepared_manifest = prepare_manifest_data(
        validated_plan,
        provenance_kind=proposal.provenance,
        source_id=proposal.source_id,
        gate_mode=active_mode,
        request_kind=request.kind,
        prompt_sha256=prompt_sha256,
    )

    context = PreparedPlanContext(
        validated_plan=validated_plan,
        lowered_payload=lowered_payload,
        request_id=request.request_id,
        kind=request.kind,
        unit=request.unit,
        provenance=proposal.provenance,
        source_id=proposal.source_id,
        mode=active_mode,
        diagnostics=tuple(validated_plan.validation_diagnostics),
        applied_defaults=tuple(validated_plan.defaults_applied),
        warnings=sanitized_warnings,
        request_metadata=request.metadata,
        prepared_manifest_data=prepared_manifest,
    )

    return context, warnings_acc


class GenerationService:
    """Application coordinator orchestrating CAD generation requests."""

    def __init__(
        self,
        *,
        example_resolver: ExampleResolver | None = None,
        prompt_resolver: PromptResolver | None = None,
        runtime_factory: Callable[[], CADRuntimeABC] = SolidEdgeRuntime,
        executor_factory: ExecutorFactory = SolidEdgeExecutor,
        artifact_finalizer: ArtifactFinalizer = finalize_request_artifacts,
    ) -> None:
        self._example_resolver = example_resolver
        self._prompt_resolver = prompt_resolver
        self._runtime_factory = runtime_factory
        self._executor_factory = executor_factory
        self._artifact_finalizer = artifact_finalizer

    def generate(
        self,
        request: GenerationRequest,
        *,
        output_root: Path | str,
        mode: Literal["capability_first", "strict"] | None = None,
    ) -> dict[str, Any]:
        """Execute a complete generation request through pure preparation, execution, and export."""
        raw_req_id = getattr(request, "request_id", None)
        safe_req_id: str = (
            raw_req_id
            if isinstance(raw_req_id, str)
            and (1 <= len(raw_req_id) <= 96)
            and bool(REQUEST_ID_PATTERN.match(raw_req_id))
            else "unknown"
        )

        # Phase A: Request and destination preflight
        try:
            valid_req_id, resolved_root, active_mode = _preflight_request(request, output_root, mode)
        except ArtifactPathError as exc:
            code = "TARGET_ALREADY_EXISTS" if exc.error_code == "TARGET_ALREADY_EXISTS" else "OUTPUT_PATH_NOT_ALLOWED"
            return build_failed_response(safe_req_id, code)
        except Exception:
            return build_rejected_response(safe_req_id, "UNSUPPORTED_REQUEST")

        accumulated_warnings: list[object] = []

        # Phase B: Dispatch and canonical preparation
        proposal: PlanProposal
        if isinstance(request, ExampleGenerationRequest):
            if self._example_resolver is None:
                return build_rejected_response(valid_req_id, "UNSUPPORTED_REQUEST")
            try:
                proposal = self._example_resolver(request)
            except Exception:
                return build_rejected_response(valid_req_id, "UNSUPPORTED_REQUEST")
        elif isinstance(request, PromptGenerationRequest):
            if self._prompt_resolver is None:
                return build_failed_response(valid_req_id, "PROMPT_INTERPRETATION_FAILED")
            try:
                proposal = self._prompt_resolver(request)
            except Exception:
                return build_failed_response(valid_req_id, "PROMPT_INTERPRETATION_FAILED")
        else:
            return build_rejected_response(valid_req_id, "UNSUPPORTED_REQUEST")

        if not isinstance(proposal, PlanProposal):
            return build_failed_response(valid_req_id, "CAD_PLAN_REJECTED")

        try:
            prepared_ctx, prep_warnings = _prepare_canonical_plan(request, proposal, active_mode)
            accumulated_warnings.extend(prep_warnings)
        except ManifestConfigurationError:
            accumulated_warnings.extend(proposal.warnings)
            return build_failed_response(valid_req_id, "ARTIFACT_EXPORT_FAILED", warnings=accumulated_warnings)
        except Exception:
            accumulated_warnings.extend(proposal.warnings)
            return build_failed_response(valid_req_id, "CAD_PLAN_REJECTED", warnings=accumulated_warnings)

        # Phase C: Runtime lifecycle and execution
        runtime: CADRuntimeABC | None = None
        executor: Any | None = None
        doc_handle: Any = None

        try:
            try:
                runtime = self._runtime_factory()
            except Exception:
                return build_failed_response(valid_req_id, "CAD_EXECUTION_FAILED", warnings=accumulated_warnings)

            try:
                app_handle = runtime.connect_application()
            except Exception:
                return build_failed_response(valid_req_id, "CAD_EXECUTION_FAILED", warnings=accumulated_warnings)

            runtime_version_build: str | None = None
            with contextlib.suppress(Exception):
                diag = runtime.get_diagnostics()
                if diag:
                    if diag.warnings:
                        accumulated_warnings.extend(diag.warnings)
                    if isinstance(diag.version_build, str) and diag.version_build.strip():
                        cleaned_build = " ".join(diag.version_build.split())
                        if (
                            cleaned_build
                            and len(cleaned_build) <= 128
                            and not LOCAL_PATH_PATTERN.search(cleaned_build)
                            and not FREE_TEXT_CREDENTIAL_PATTERN.search(cleaned_build)
                            and not SENSITIVE_KEY_PATTERN.search(cleaned_build)
                        ):
                            runtime_version_build = cleaned_build

            if runtime_version_build is None and not any(
                (isinstance(w, Mapping) and w.get("code") == "VERSION_METADATA_UNAVAILABLE")
                or w == "VERSION_METADATA_UNAVAILABLE"
                or w == "CAD runtime did not report version metadata."
                for w in accumulated_warnings
            ):
                accumulated_warnings.append({"code": "VERSION_METADATA_UNAVAILABLE"})

            try:
                doc_handle = runtime.create_part_document(app_handle)
            except Exception:
                return build_failed_response(valid_req_id, "CAD_EXECUTION_FAILED", warnings=accumulated_warnings)

            try:
                executor = self._executor_factory(runtime, doc_handle)
            except Exception:
                with contextlib.suppress(Exception):
                    runtime.close_document(doc_handle)
                return build_failed_response(valid_req_id, "CAD_EXECUTION_FAILED", warnings=accumulated_warnings)

            try:
                exec_result = executor.execute_feature_plan(prepared_ctx.validated_plan, mode=prepared_ctx.mode)
            except Exception:
                with contextlib.suppress(Exception):
                    executor.close_request_document()
                return build_failed_response(valid_req_id, "CAD_EXECUTION_FAILED", warnings=accumulated_warnings)

            if isinstance(exec_result, ExecutionFailure):
                if exec_result.warnings:
                    accumulated_warnings.extend(exec_result.warnings)
                try:
                    executor.close_request_document()
                except Exception:
                    return build_failed_response(valid_req_id, "CAD_EXECUTION_FAILED", warnings=accumulated_warnings)

                phase = getattr(exec_result, "phase", "execution")
                fail_code = (
                    "CAD_PLAN_REJECTED"
                    if phase in ("parsing", "validation", "preflight", "lowering")
                    else "CAD_EXECUTION_FAILED"
                )
                return build_failed_response(valid_req_id, fail_code, warnings=accumulated_warnings)

            if not isinstance(exec_result, ExecutionSuccess):
                with contextlib.suppress(Exception):
                    executor.close_request_document()
                return build_failed_response(valid_req_id, "INTERNAL_ERROR", warnings=accumulated_warnings)

            # Retain executor warnings before calling finalizer so they survive any finalizer failure
            if exec_result.warnings:
                accumulated_warnings.extend(exec_result.warnings)

            # Assemble manifest context if manifest data was prepared
            manifest_context: RunManifestContext | None = None
            if prepared_ctx.prepared_manifest_data is not None:
                sanitized_manifest_warnings = tuple(project_warnings(accumulated_warnings))
                manifest_context = RunManifestContext(
                    prepared_data=prepared_ctx.prepared_manifest_data,
                    request_id=valid_req_id,
                    contract_version="1.0",
                    unit=request.unit,
                    cad_runtime_version_build=runtime_version_build,
                    warnings=sanitized_manifest_warnings,
                )

            # Phase D: Artifact finalization (owns inspection, exports, publication, and document release)
            try:
                finalized = self._artifact_finalizer(
                    executor,
                    exec_result,
                    resolved_root,
                    valid_req_id,
                    manifest_context=manifest_context,
                )
                if not hasattr(finalized, "exported_artifacts") or not hasattr(finalized, "warnings"):
                    return build_failed_response(valid_req_id, "INTERNAL_ERROR", warnings=accumulated_warnings)
                if finalized.warnings:
                    accumulated_warnings.extend(finalized.warnings)
            except CADExecutionError as e:
                err_code = getattr(e, "error_code", None)
                code = "NATIVE_QA_BLOCKED" if err_code == "NATIVE_QA_BLOCKED" else "CAD_EXECUTION_FAILED"
                return build_failed_response(valid_req_id, code, warnings=accumulated_warnings)
            except CADExportError:
                return build_failed_response(valid_req_id, "ARTIFACT_EXPORT_FAILED", warnings=accumulated_warnings)
            except ArtifactPathError as e:
                code = "TARGET_ALREADY_EXISTS" if e.error_code == "TARGET_ALREADY_EXISTS" else "OUTPUT_PATH_NOT_ALLOWED"
                return build_failed_response(valid_req_id, code, warnings=accumulated_warnings)
            except CADDocumentError:
                return build_failed_response(valid_req_id, "CAD_EXECUTION_FAILED", warnings=accumulated_warnings)
            except Exception:
                return build_failed_response(valid_req_id, "INTERNAL_ERROR", warnings=accumulated_warnings)

            try:
                return build_accepted_response(
                    valid_req_id,
                    finalized.exported_artifacts,
                    warnings=accumulated_warnings,
                )
            except ArtifactProjectionError:
                return build_failed_response(valid_req_id, "INTERNAL_ERROR", warnings=accumulated_warnings)

        finally:
            # Phase E: Guaranteed Teardown
            if runtime is not None:
                clean_teardown = False
                try:
                    clean_teardown = runtime.teardown(force_kill_on_failure=False) is True
                except Exception as exc:
                    raise TeardownIncompleteError("CAD runtime teardown failed with an error") from exc

                if not clean_teardown:
                    raise TeardownIncompleteError("CAD runtime teardown completed incompletely")


__all__ = ["GenerationService"]
