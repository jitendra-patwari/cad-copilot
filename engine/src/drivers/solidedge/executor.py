"""Siemens Solid Edge concrete CAD executor implementation for Milestone 3."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from geometry.plan_lowering import lower_feature_plan_to_payload
from geometry.plan_models import (
    FeaturePlan,
    ProfileCutoutFeature,
    RevolvedProfileFeature,
    SweptProtrusionFeature,
)
from geometry.plan_parser import feature_plan_from_dict
from interfaces.exceptions import CADDocumentError, CADError, CADExecutionError
from interfaces.executor_abc import CADExecutorABC
from interfaces.models import (
    ArtifactFormat,
    BodyRef,
    ExecutionFailure,
    ExecutionResult,
    ExecutionSuccess,
    FeatureRef,
    OperationResult,
    StandardInspectionReport,
)

from .builders import (
    create_cutout_finite,
    create_cutout_through_all,
    create_pad_protrusion,
    create_primitive_cuboid,
    create_primitive_cylinder,
    create_primitive_extruded_profile,
    create_profile_on_plane,
    draw_circle_profile,
    draw_polygon_profile,
    draw_slot_profile,
    resolve_or_create_reference_plane,
)
from .constants import (
    BODY_TYPE_CURVE,
    FEATURE_STATUS_OK,
    PART_GLOBAL_ACCURACY,
    PART_GLOBAL_DENSITY,
    PHYSICAL_PROPERTIES_STATUS_MODEL,
)
from .directions import (
    PROMOTED_CUT_CAPABILITY_ROWS,
    PROMOTED_PAD_CAPABILITY_ROWS,
)
from .errors import describe_exception
from .exporter import capture_preview_image, export_model_to_path
from .runtime import SolidEdgeRuntime
from .types import SolidEdgePartDocumentHandle
from .units import m3_to_mm3


def _is_unit_vector(v: list[float] | tuple[float, ...], tol: float = 1e-2) -> bool:
    if not isinstance(v, (list, tuple)) or len(v) != 3:
        return False
    if not all(isinstance(x, (int, float)) and math.isfinite(x) for x in v):
        return False
    length_sq = sum(x * x for x in v)
    return math.isclose(length_sq, 1.0, rel_tol=tol, abs_tol=tol)


def _dot_product(v1: list[float] | tuple[float, ...], v2: list[float] | tuple[float, ...]) -> float:
    return (v1[0] * v2[0]) + (v1[1] * v2[1]) + (v1[2] * v2[2])


def _cross_product(
    v1: list[float] | tuple[float, ...], v2: list[float] | tuple[float, ...]
) -> tuple[float, float, float]:
    return (
        (v1[1] * v2[2]) - (v1[2] * v2[1]),
        (v1[2] * v2[0]) - (v1[0] * v2[2]),
        (v1[0] * v2[1]) - (v1[1] * v2[0]),
    )


def _resolve_face_from_patch(patch: dict[str, Any]) -> str:
    """Extract or deduce target face (+Z, -Z, +X, -X, +Y, -Y) from a lowered patch."""
    if "face" in patch and isinstance(patch["face"], str):
        return patch["face"].upper()

    normal = patch.get("normal_vector")
    if normal and len(normal) == 3:
        nx, ny, nz = normal[0], normal[1], normal[2]
        if math.isclose(nz, 1.0, abs_tol=1e-3):
            return "+Z"
        if math.isclose(nz, -1.0, abs_tol=1e-3):
            return "-Z"
        if math.isclose(nx, 1.0, abs_tol=1e-3):
            return "+X"
        if math.isclose(nx, -1.0, abs_tol=1e-3):
            return "-X"
        if math.isclose(ny, 1.0, abs_tol=1e-3):
            return "+Y"
        if math.isclose(ny, -1.0, abs_tol=1e-3):
            return "-Y"

    plane = patch.get("sketch_plane") or patch.get("plane")
    if plane == "XY":
        return "+Z"
    if plane == "YZ":
        return "+X"
    if plane == "XZ":
        return "+Y"

    return "+Z"


def _extract_profile_geometry(patch: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Extract profile family kind and data dict from ensure_profile patch."""
    prof_data: dict[str, Any] = patch.get("geometry") or patch.get("profile", {})
    kind = str(prof_data.get("kind") or prof_data.get("type", "unknown")).lower()
    return kind, prof_data


def _inspect_model_topology_and_properties(
    raw_doc: Any,
    worker: Any,
    tracked_features: dict[str, tuple[Any, str, str]] | None = None,
    feature_count: int | None = None,
) -> StandardInspectionReport:
    """Documented public Solid Edge recompute and authoritative topology/property inspection."""
    # 1. Invoke native Recompute
    try:
        worker._invoke_com(lambda: raw_doc.Recompute())
    except Exception as exc:
        raise CADExecutionError("Native PartDocument.Recompute() failed", error_code="RECOMPUTE_FAILED") from exc

    # 2. Enumerate and classify Models and Constructions Body topology
    models_col = worker._invoke_com(lambda: getattr(raw_doc, "Models", None))
    models_count = worker._invoke_com(lambda: getattr(models_col, "Count", 0)) if models_col else 0

    constructions_col = worker._invoke_com(lambda: getattr(raw_doc, "Constructions", None))
    constructions_count = worker._invoke_com(lambda: getattr(constructions_col, "Count", 0)) if constructions_col else 0

    solid_body_count = 0
    sheet_body_count = 0
    wire_body_count = 0

    # Enumerate models
    if models_count > 0 and models_col is not None:
        for i in range(1, models_count + 1):
            m = worker._invoke_com(lambda idx=i: models_col.Item(idx))
            body = worker._invoke_com(lambda mod=m: getattr(mod, "Body", None))
            if body is None:
                raise CADExecutionError(
                    f"Model index {i} has null Body object",
                    error_code="SOLID_INSPECTION_FAILED",
                )
            is_solid = worker._invoke_com(lambda b=body: getattr(b, "IsSolid", False))
            body_type = worker._invoke_com(lambda b=body: getattr(b, "Type", None))
            if is_solid:
                solid_body_count += 1
            elif body_type == BODY_TYPE_CURVE:
                wire_body_count += 1
            else:
                sheet_body_count += 1

    # Enumerate construction models
    if constructions_count > 0 and constructions_col is not None:
        for i in range(1, constructions_count + 1):
            c_mod = worker._invoke_com(lambda idx=i: constructions_col.Item(idx))
            c_body = worker._invoke_com(lambda mod=c_mod: getattr(mod, "Body", None))
            if c_body is None:
                raise CADExecutionError(
                    f"ConstructionModel index {i} has null Body object",
                    error_code="SOLID_INSPECTION_FAILED",
                )
            is_solid = worker._invoke_com(lambda b=c_body: getattr(b, "IsSolid", False))
            body_type = worker._invoke_com(lambda b=c_body: getattr(b, "Type", None))
            if is_solid:
                solid_body_count += 1
            elif body_type == BODY_TYPE_CURVE:
                wire_body_count += 1
            else:
                sheet_body_count += 1

    total_body_count = solid_body_count + sheet_body_count + wire_body_count

    # Topological invariant checks for single-body scope:
    if models_count != 1:
        raise CADExecutionError(
            f"Expected exactly 1 Model in document, found {models_count}",
            error_code="SOLID_INSPECTION_FAILED",
        )
    if sheet_body_count > 0 or wire_body_count > 0:
        raise CADExecutionError(
            f"Non-solid topology detected: {sheet_body_count} sheet bodies, {wire_body_count} wire bodies (total bodies: {total_body_count})",
            error_code="SOLID_INSPECTION_FAILED",
        )
    if solid_body_count != 1:
        raise CADExecutionError(
            f"Expected exactly 1 solid body, found {solid_body_count} (total bodies: {total_body_count})",
            error_code="SOLID_INSPECTION_FAILED",
        )

    primary_model = worker._invoke_com(lambda: models_col.Item(1))

    # 3. Read physical properties via GetGlobalParameter and ComputePhysicalProperties
    get_param_fn = getattr(raw_doc, "GetGlobalParameter", None)
    if not callable(get_param_fn):
        raise CADExecutionError(
            "PartDocument does not expose GetGlobalParameter method",
            error_code="SOLID_INSPECTION_FAILED",
        )

    try:
        try:
            doc_density = worker._invoke_com(lambda: raw_doc.GetGlobalParameter(PART_GLOBAL_DENSITY, 0.0))
        except TypeError:
            doc_density = worker._invoke_com(lambda: raw_doc.GetGlobalParameter(PART_GLOBAL_DENSITY))
    except Exception as exc:
        raise CADExecutionError(
            f"Failed to query PartDocument.GetGlobalParameter({PART_GLOBAL_DENSITY}): {describe_exception(exc)}",
            error_code="SOLID_INSPECTION_FAILED",
        ) from exc

    if not isinstance(doc_density, (int, float)) or not math.isfinite(doc_density) or doc_density < 0.0:
        raise CADExecutionError(
            f"PartDocument.GetGlobalParameter({PART_GLOBAL_DENSITY}) returned invalid density: {doc_density!r}",
            error_code="SOLID_INSPECTION_FAILED",
        )
    density = float(doc_density)

    try:
        try:
            doc_acc = worker._invoke_com(lambda: raw_doc.GetGlobalParameter(PART_GLOBAL_ACCURACY, 0.0001))
        except TypeError:
            doc_acc = worker._invoke_com(lambda: raw_doc.GetGlobalParameter(PART_GLOBAL_ACCURACY))
    except Exception as exc:
        raise CADExecutionError(
            f"Failed to query PartDocument.GetGlobalParameter({PART_GLOBAL_ACCURACY}): {describe_exception(exc)}",
            error_code="SOLID_INSPECTION_FAILED",
        ) from exc

    if not isinstance(doc_acc, (int, float)) or not math.isfinite(doc_acc) or doc_acc <= 0.0:
        raise CADExecutionError(
            f"PartDocument.GetGlobalParameter({PART_GLOBAL_ACCURACY}) returned invalid accuracy: {doc_acc!r}",
            error_code="SOLID_INSPECTION_FAILED",
        )
    accuracy = float(doc_acc)

    if density == 0.0:
        # Unassigned density: read authoritative native Body.Volume and report zero mass (bypass density-dependent computation)
        primary_body = worker._invoke_com(lambda: getattr(primary_model, "Body", None))
        if primary_body is None:
            raise CADExecutionError(
                "Primary model has null Body object",
                error_code="SOLID_INSPECTION_FAILED",
            )
        vol_m3 = worker._invoke_com(lambda: getattr(primary_body, "Volume", 0.0))
        if not isinstance(vol_m3, (int, float)) or not math.isfinite(vol_m3) or vol_m3 <= 0.0:
            raise CADExecutionError(
                f"Inspected non-finite or non-positive volume: {vol_m3!r} m3",
                error_code="SOLID_INSPECTION_FAILED",
            )
        mass_kg = 0.0
    else:
        # Positive density: compute full physical properties
        compute_fn = getattr(primary_model, "ComputePhysicalProperties", None)
        if not callable(compute_fn):
            raise CADExecutionError(
                "Primary Model does not expose ComputePhysicalProperties method",
                error_code="SOLID_INSPECTION_FAILED",
            )

        try:
            # Signature: Model.ComputePhysicalProperties(
            #     Density, Accuracy, Volume, Area, Mass, CenterOfGravity, CenterOfVolume,
            #     PrincipalMomentsOfInertia, RadiusOfGyration, PrincipalAxes,
            #     RadiiOfGyrationAboutPrincipalAxes, RelativeAccuracyAchieved, Status
            # )
            # Returns COM tuple of 11 elements:
            # (Volume, Area, Mass, COG, COV, Moments, Gyration, Axes, Radii, RelativeAccuracyAchieved, Status)
            res = worker._invoke_com(lambda: primary_model.ComputePhysicalProperties(density, accuracy))
        except Exception as exc:
            raise CADExecutionError(
                f"Native Model.ComputePhysicalProperties({density}, {accuracy}) failed: {describe_exception(exc)}",
                error_code="SOLID_INSPECTION_FAILED",
            ) from exc

        if not isinstance(res, (tuple, list)) or len(res) < 11:
            raise CADExecutionError(
                f"Model.ComputePhysicalProperties returned invalid output tuple of length {len(res) if isinstance(res, (tuple, list)) else type(res)} (expected >= 11)",
                error_code="SOLID_INSPECTION_FAILED",
            )

        comp_status = res[-1]
        if not isinstance(comp_status, int) or comp_status != PHYSICAL_PROPERTIES_STATUS_MODEL:
            raise CADExecutionError(
                f"Model.ComputePhysicalProperties returned non-model/unhealthy status code: {comp_status!r} "
                f"(expected {PHYSICAL_PROPERTIES_STATUS_MODEL})",
                error_code="SOLID_INSPECTION_FAILED",
            )

        vol_m3 = float(res[0])
        if not isinstance(vol_m3, (int, float)) or not math.isfinite(vol_m3) or vol_m3 <= 0.0:
            raise CADExecutionError(
                f"Inspected non-finite or non-positive volume: {vol_m3!r} m3",
                error_code="SOLID_INSPECTION_FAILED",
            )

        raw_mass = res[2]
        if not isinstance(raw_mass, (int, float)) or not math.isfinite(raw_mass) or raw_mass <= 0.0:
            raise CADExecutionError(
                f"Inspected non-finite or non-positive mass: {raw_mass!r} kg with positive density {density}",
                error_code="SOLID_INSPECTION_FAILED",
            )
        mass_kg = float(raw_mass)

    # 4. Mandatory native Features collection validation and feature status inspection
    native_features = worker._invoke_com(lambda: getattr(primary_model, "Features", None))
    if native_features is None or not hasattr(native_features, "Count"):
        raise CADExecutionError(
            "Primary model has no Features collection or Features.Count is unavailable",
            error_code="SOLID_INSPECTION_FAILED",
        )

    n_feats = worker._invoke_com(lambda: getattr(native_features, "Count", 0))
    if not isinstance(n_feats, int) or n_feats < 1:
        raise CADExecutionError(
            f"Primary model has invalid Features count: {n_feats!r}",
            error_code="SOLID_INSPECTION_FAILED",
        )

    for f_idx in range(1, n_feats + 1):
        feat = worker._invoke_com(lambda idx=f_idx: native_features.Item(idx))
        st = worker._invoke_com(lambda f=feat: getattr(f, "Status", None))
        if not isinstance(st, int) or st != FEATURE_STATUS_OK:
            raise CADExecutionError(
                f"Native feature {f_idx} status is unhealthy/unreadable after recompute: {st!r} (expected integer {FEATURE_STATUS_OK})",
                error_code="FEATURE_VALIDATION_FAILED",
            )

    reported_feat_count = n_feats if feature_count is None else feature_count

    # Check ExtrudedProtrusions collection
    protrusions = worker._invoke_com(lambda: getattr(primary_model, "ExtrudedProtrusions", None))
    if protrusions is None or getattr(protrusions, "Count", 0) < 1:
        raise CADExecutionError(
            "Primary model has no ExtrudedProtrusions collection or features",
            error_code="SOLID_INSPECTION_FAILED",
        )

    prots_count = worker._invoke_com(lambda: getattr(protrusions, "Count", 0))
    for p_idx in range(1, prots_count + 1):
        feat = worker._invoke_com(lambda idx=p_idx: protrusions.Item(idx))
        st = worker._invoke_com(lambda f=feat: getattr(f, "Status", None))
        if not isinstance(st, int) or st != FEATURE_STATUS_OK:
            raise CADExecutionError(
                f"ExtrudedProtrusion {p_idx} status is unhealthy/unreadable after recompute: {st!r} (expected integer {FEATURE_STATUS_OK})",
                error_code="FEATURE_VALIDATION_FAILED",
            )

    # Check ExtrudedCutouts collection if present
    cutouts = worker._invoke_com(lambda: getattr(primary_model, "ExtrudedCutouts", None))
    cuts_count = worker._invoke_com(lambda: getattr(cutouts, "Count", 0)) if cutouts is not None else 0
    if cutouts is not None and cuts_count > 0:
        for c_idx in range(1, cuts_count + 1):
            feat = worker._invoke_com(lambda idx=c_idx: cutouts.Item(idx))
            st = worker._invoke_com(lambda f=feat: getattr(f, "Status", None))
            if not isinstance(st, int) or st != FEATURE_STATUS_OK:
                raise CADExecutionError(
                    f"ExtrudedCutout {c_idx} status is unhealthy/unreadable after recompute: {st!r} (expected integer {FEATURE_STATUS_OK})",
                    error_code="FEATURE_VALIDATION_FAILED",
                )

    # 5. Check status of all tracked features after recompute
    if tracked_features:
        for res_ref, (feat_obj, _feat_kind, patch_id) in tracked_features.items():
            status = worker._invoke_com(lambda f=feat_obj: getattr(f, "Status", None))
            if not isinstance(status, int) or status != FEATURE_STATUS_OK:
                raise CADExecutionError(
                    f"Feature '{res_ref}' (patch '{patch_id}') has unhealthy/unreadable status {status!r} after recompute (expected integer {FEATURE_STATUS_OK})",
                    error_code="FEATURE_VALIDATION_FAILED",
                    details={"failing_patch_id": patch_id, "failing_reference_id": res_ref},
                )

    return StandardInspectionReport(
        volume_mm3=m3_to_mm3(vol_m3),
        mass_kg=mass_kg,
        feature_count=reported_feat_count,
        body_count=total_body_count,
        solid_body_count=solid_body_count,
        sheet_body_count=sheet_body_count,
        wire_body_count=wire_body_count,
    )


class SolidEdgeExecutor(CADExecutorABC):
    """Concrete CAD executor for Siemens Solid Edge Milestone 3.2 geometric primitive execution."""

    def __init__(
        self,
        runtime: SolidEdgeRuntime,
        document_handle: SolidEdgePartDocumentHandle,
        timeout_seconds: float = 120.0,
    ) -> None:
        if not isinstance(runtime, SolidEdgeRuntime):
            raise TypeError(f"runtime must be an instance of SolidEdgeRuntime, got {type(runtime)}")
        if not isinstance(document_handle, SolidEdgePartDocumentHandle):
            raise TypeError(
                f"document_handle must be an instance of SolidEdgePartDocumentHandle, got {type(document_handle)}"
            )
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0.0:
            raise ValueError(f"timeout_seconds must be a positive finite number, got {timeout_seconds}")

        self._runtime = runtime
        self._doc_handle: SolidEdgePartDocumentHandle | None = document_handle
        self._timeout_seconds = timeout_seconds
        self._close_failed: bool = False

    def _require_doc_handle(self) -> SolidEdgePartDocumentHandle:
        if self._close_failed:
            raise CADDocumentError(
                "Document close previously failed; handle is not confirmed released",
                error_code="DOCUMENT_CLOSE_FAILED",
            )
        if self._doc_handle is None:
            raise CADDocumentError(
                "Document handle has been closed and is no longer available for execution",
                error_code="DOCUMENT_CLOSED",
            )
        return self._doc_handle

    # -----------------------------------------------------------------------
    # High-Level Feature Plan Execution
    # -----------------------------------------------------------------------

    def execute_feature_plan(self, plan: FeaturePlan | dict[str, Any]) -> ExecutionResult:
        """Execute a declarative feature plan AST and return the execution result."""
        raw_warnings: list[str] = []

        def _fail(
            msg: str,
            code: str,
            phase: str = "execution",
            extra_details: dict[str, Any] | None = None,
        ) -> ExecutionFailure:
            details: dict[str, Any] = {"error_code": code}
            if extra_details:
                details.update(extra_details)
            formatted_warnings = [{"message": w} for w in raw_warnings]
            return ExecutionFailure(
                message=msg,
                phase=phase,
                details=details,
                warnings=formatted_warnings,
            )

        # 1. Parse high-level plan AST
        try:
            if isinstance(plan, dict):
                ast = feature_plan_from_dict(plan)
            elif isinstance(plan, FeaturePlan):
                ast = plan
            else:
                return _fail(
                    f"Invalid plan payload type: expected FeaturePlan or dict, got {type(plan)}",
                    "INVALID_PLAN_PAYLOAD",
                    phase="parsing",
                )
        except Exception as exc:
            return _fail(
                f"Plan parsing exception: {describe_exception(exc)}",
                "PLAN_PARSING_FAILED",
                phase="parsing",
            )

        # 2. Scope preflight on AST: Reject unsupported deferred feature families
        for feature in ast.features:
            if isinstance(feature, ProfileCutoutFeature):
                return _fail(
                    f"Arbitrary profile cutouts (ProfileCutoutFeature '{feature.id}') are out of scope for Milestone 3.2 (only circular, rectangular, and slot cuts are supported).",
                    "UNSUPPORTED_EXECUTION_OPERATION",
                    phase="preflight",
                    extra_details={"failing_feature_id": feature.id},
                )
            if isinstance(feature, (RevolvedProfileFeature, SweptProtrusionFeature)):
                return _fail(
                    f"Feature family '{feature.family}' is out of scope for Milestone 3.2",
                    "UNSUPPORTED_EXECUTION_OPERATION",
                    phase="preflight",
                    extra_details={"failing_feature_id": feature.id},
                )

        # 3. Lower plan to semantic patch sequence (validates AST internally)
        try:
            lowered = lower_feature_plan_to_payload(ast)
            contract_version = lowered.get("contract_version")
            kind = lowered.get("kind")
            unit = lowered.get("unit")
            if contract_version != "1.0" or kind != "semantic_patch_sequence":
                return _fail(
                    f"Invalid lowered contract: version='{contract_version}', kind='{kind}'",
                    "INVALID_LOWERED_PAYLOAD",
                    phase="lowering",
                )
            if unit != "mm":
                return _fail(
                    f"Unsupported lowered payload unit: expected 'mm', got '{unit}'",
                    "INVALID_LOWERED_PAYLOAD",
                    phase="lowering",
                )

            patches: list[dict[str, Any]] = lowered.get("patches", [])
            if not patches:
                return _fail(
                    "Lowered patch sequence is empty",
                    "EMPTY_PATCH_SEQUENCE",
                    phase="lowering",
                )

            raw_warnings.extend([d.message for d in ast.validation_diagnostics])

        except Exception as exc:
            return _fail(
                f"Plan lowering/validation exception: {describe_exception(exc)}",
                "PLAN_LOWERING_FAILED",
                phase="validation",
            )

        # 4. Whole-plan semantic and patch preflight
        preflight_err = self._preflight_patch_sequence(patches)
        if preflight_err is not None:
            err_msg, err_code, preflight_details = preflight_err
            return _fail(
                err_msg,
                err_code,
                phase="preflight",
                extra_details=preflight_details,
            )

        # 5. Dispatch patch sequence to STA worker seam
        def _document_task(raw_doc: Any, worker: Any) -> tuple[list[OperationResult], StandardInspectionReport]:
            return self._execute_patches_on_worker(raw_doc, worker, patches)

        try:
            doc_handle = self._require_doc_handle()
            operations, inspection = self._runtime.run_document_task(
                doc_handle,
                _document_task,
                timeout=self._timeout_seconds,
            )
            # Contract: operations_executed is the total count of successfully executed lowered patches
            return ExecutionSuccess(
                operations_executed=len(patches),
                exported_artifacts=[],
                warnings=[{"message": w} for w in raw_warnings],
                operation_results=operations,
                inspection_report=inspection,
            )

        except TimeoutError as exc:
            return _fail(
                f"Execution timed out after {self._timeout_seconds:.1f}s: {exc}",
                "EXECUTION_TIMEOUT",
                phase="execution",
            )
        except CADError as exc:
            err_code = getattr(exc, "error_code", "CAD_ERROR")
            raw_details = getattr(exc, "details", None)
            extra_details: dict[str, Any] | None = dict(raw_details) if isinstance(raw_details, dict) else None
            return _fail(
                str(exc),
                err_code,
                phase="execution",
                extra_details=extra_details,
            )
        except Exception as exc:
            return _fail(
                f"Unhandled native execution error: {describe_exception(exc)}",
                "NATIVE_COM_ERROR",
                phase="execution",
            )

    # -----------------------------------------------------------------------
    # Patch Sequence Preflight
    # -----------------------------------------------------------------------

    def _preflight_patch_sequence(self, patches: list[dict[str, Any]]) -> tuple[str, str, dict[str, Any]] | None:
        """Preflight lowered patches for single-body topology, valid references, and promoted capability rows."""
        if not patches:
            return ("No patches to execute", "EMPTY_PATCH_SEQUENCE", {})

        # 1. First patch must be ensure_primitive_body
        first = patches[0]
        if first.get("op") != "ensure_primitive_body":
            return (
                f"First patch must be 'ensure_primitive_body', got '{first.get('op')}'",
                "INVALID_PATCH_ORDER",
                {"patch_id": first.get("patch_id", "")},
            )

        base_body_ref = first.get("body_ref")
        if not base_body_ref or not isinstance(base_body_ref, str):
            return ("Missing or invalid body_ref in base body patch", "INVALID_LOWERED_PAYLOAD", {})

        seen_patch_ids: set[str] = set()
        registered_bodies: set[str] = {base_body_ref}
        seen_sketch_refs: set[str] = set()
        seen_profile_refs: dict[str, tuple[str, dict[str, Any]]] = {}  # profile_ref -> (kind, geometry_data)
        seen_result_refs: set[str] = {base_body_ref}

        for idx, patch in enumerate(patches):
            op = patch.get("op")
            patch_id = patch.get("patch_id")
            if not patch_id or not isinstance(patch_id, str):
                return (
                    f"Patch at index {idx} has missing or non-string patch_id",
                    "INVALID_LOWERED_PAYLOAD",
                    {"index": idx},
                )

            if patch_id in seen_patch_ids:
                return (
                    f"Duplicate patch_id '{patch_id}' at index {idx}",
                    "INVALID_LOWERED_PAYLOAD",
                    {"patch_id": patch_id, "index": idx},
                )
            seen_patch_ids.add(patch_id)

            # Validate replay_policy if present
            replay = patch.get("replay_policy")
            if replay is not None:
                if not isinstance(replay, dict):
                    return (
                        f"Invalid replay_policy shape in patch {patch_id}",
                        "INVALID_LOWERED_PAYLOAD",
                        {"patch_id": patch_id},
                    )
                idemp_key = replay.get("idempotency_key")
                mode = replay.get("mode")
                if not idemp_key or not isinstance(idemp_key, str):
                    return (
                        f"Missing idempotency_key in patch {patch_id}",
                        "INVALID_LOWERED_PAYLOAD",
                        {"patch_id": patch_id},
                    )
                if mode not in ("ensure_present", "verify_equivalent", "fail_on_drift"):
                    return (
                        f"Invalid replay_policy mode '{mode}' in patch {patch_id}",
                        "INVALID_LOWERED_PAYLOAD",
                        {"patch_id": patch_id},
                    )

            if op == "ensure_primitive_body":
                if idx > 0:
                    return (
                        f"Multi-body composition is unsupported in M3.2 (patch {patch_id})",
                        "UNSUPPORTED_EXECUTION_OPERATION",
                        {"patch_id": patch_id},
                    )
                shape = patch.get("shape", {})
                shape_type = shape.get("type")
                if shape_type not in ("cuboid", "cylinder", "extruded_profile"):
                    return (
                        f"Unsupported base primitive shape '{shape_type}' in patch {patch_id}",
                        "UNSUPPORTED_EXECUTION_OPERATION",
                        {"patch_id": patch_id},
                    )
                if shape_type == "cuboid":
                    len_mm, wid_mm, hgt_mm = shape.get("length_mm"), shape.get("width_mm"), shape.get("height_mm")
                    if not all(
                        isinstance(v, (int, float)) and math.isfinite(v) and v > 0.0 for v in (len_mm, wid_mm, hgt_mm)
                    ):
                        return (
                            f"Cuboid dimensions must be strictly positive finite numbers in patch {patch_id}",
                            "INVALID_LOWERED_PAYLOAD",
                            {"patch_id": patch_id},
                        )
                elif shape_type == "cylinder":
                    r, h = shape.get("radius_mm"), shape.get("height_mm")
                    if not all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0.0 for v in (r, h)):
                        return (
                            f"Cylinder dimensions must be strictly positive finite numbers in patch {patch_id}",
                            "INVALID_LOWERED_PAYLOAD",
                            {"patch_id": patch_id},
                        )
                elif shape_type == "extruded_profile":
                    h = shape.get("height_mm")
                    pts = shape.get("points")
                    if (
                        not isinstance(h, (int, float))
                        or not math.isfinite(h)
                        or h <= 0.0
                        or not isinstance(pts, list)
                        or len(pts) < 3
                    ):
                        return (
                            f"Invalid extruded_profile shape in patch {patch_id}",
                            "INVALID_LOWERED_PAYLOAD",
                            {"patch_id": patch_id},
                        )

            elif op == "ensure_sketch":
                s_ref = patch.get("sketch_ref")
                b_ref = patch.get("body_ref")
                plane = patch.get("plane")
                if not s_ref or not isinstance(s_ref, str) or not plane:
                    return (
                        f"Invalid ensure_sketch payload in patch {patch_id}",
                        "INVALID_LOWERED_PAYLOAD",
                        {"patch_id": patch_id},
                    )
                if s_ref in seen_sketch_refs:
                    return (
                        f"Duplicate sketch_ref '{s_ref}' in patch {patch_id}",
                        "INVALID_LOWERED_PAYLOAD",
                        {"patch_id": patch_id},
                    )
                if b_ref != base_body_ref:
                    return (
                        f"Sketch references non-active body '{b_ref}' in patch {patch_id}",
                        "MISSING_EXECUTION_REFERENCE",
                        {"patch_id": patch_id, "body_ref": b_ref},
                    )
                if plane.upper() not in ("XY", "YZ", "XZ"):
                    return (
                        f"Unsupported sketch plane '{plane}' in patch {patch_id}",
                        "UNSUPPORTED_EXECUTION_OPERATION",
                        {"patch_id": patch_id},
                    )
                seen_sketch_refs.add(s_ref)

            elif op == "ensure_profile":
                p_ref = patch.get("profile_ref")
                s_ref = patch.get("sketch_ref")
                prof_type, prof_data = _extract_profile_geometry(patch)

                if not p_ref or not isinstance(p_ref, str) or not s_ref or not prof_type:
                    return (
                        f"Invalid ensure_profile payload in patch {patch_id}",
                        "INVALID_LOWERED_PAYLOAD",
                        {"patch_id": patch_id},
                    )
                if p_ref in seen_profile_refs:
                    return (
                        f"Duplicate profile_ref '{p_ref}' in patch {patch_id}",
                        "INVALID_LOWERED_PAYLOAD",
                        {"patch_id": patch_id},
                    )
                if s_ref not in seen_sketch_refs:
                    return (
                        f"Profile references unregistered sketch '{s_ref}' in patch {patch_id}",
                        "MISSING_EXECUTION_REFERENCE",
                        {"patch_id": patch_id, "sketch_ref": s_ref},
                    )
                if prof_type not in ("circle", "polygon", "slot"):
                    return (
                        f"Unsupported profile type '{prof_type}' in patch {patch_id}",
                        "UNSUPPORTED_EXECUTION_OPERATION",
                        {"patch_id": patch_id},
                    )

                if prof_type == "circle":
                    r = prof_data.get("radius_mm")
                    if not isinstance(r, (int, float)) or not math.isfinite(r) or r <= 0.0:
                        return (
                            f"Circle radius must be strictly positive in patch {patch_id}",
                            "INVALID_LOWERED_PAYLOAD",
                            {"patch_id": patch_id},
                        )
                elif prof_type == "slot":
                    slot_len, slot_wid = prof_data.get("length_mm"), prof_data.get("width_mm")
                    if (
                        not isinstance(slot_len, (int, float))
                        or not isinstance(slot_wid, (int, float))
                        or not math.isfinite(slot_len)
                        or not math.isfinite(slot_wid)
                        or slot_len <= 0.0
                        or slot_wid <= 0.0
                        or slot_len <= slot_wid
                    ):
                        return (
                            f"Slot length must be strictly greater than width (> 0) in patch {patch_id}",
                            "INVALID_LOWERED_PAYLOAD",
                            {"patch_id": patch_id},
                        )
                elif prof_type == "polygon":
                    pts = prof_data.get("points") or prof_data.get("points_mm")
                    if not isinstance(pts, list) or len(pts) < 3:
                        return (
                            f"Polygon requires at least 3 points in patch {patch_id}",
                            "INVALID_LOWERED_PAYLOAD",
                            {"patch_id": patch_id},
                        )
                    # In Milestone 3.2, polygon cutouts and pads are strictly restricted to 4-point rectangular profiles.
                    # Freeform arbitrary polygon profile cutouts are deferred and out of scope.
                    if len(pts) != 4:
                        return (
                            f"Non-rectangular polygon profile with {len(pts)} vertices is unsupported in Milestone 3.2 (patch {patch_id})",
                            "UNSUPPORTED_EXECUTION_OPERATION",
                            {"patch_id": patch_id},
                        )

                seen_profile_refs[p_ref] = (prof_type, prof_data)

            elif op == "cut_hole":
                target = patch.get("body_ref") or patch.get("target_body_ref")
                p_ref = patch.get("profile_ref")
                res_ref = patch.get("result_ref")
                face = _resolve_face_from_patch(patch)
                through_all = patch.get("through_all", False) or patch.get("extent", {}).get("type") == "through_all"
                extent_type = "through_all" if through_all else "finite"

                if not target or not p_ref or not res_ref:
                    return (
                        f"Invalid cut_hole payload in patch {patch_id}",
                        "INVALID_LOWERED_PAYLOAD",
                        {"patch_id": patch_id},
                    )
                if res_ref in seen_result_refs:
                    return (
                        f"Duplicate result_ref '{res_ref}' in patch {patch_id}",
                        "INVALID_LOWERED_PAYLOAD",
                        {"patch_id": patch_id},
                    )
                if target not in registered_bodies:
                    return (
                        f"Cut target '{target}' does not match active body '{base_body_ref}'",
                        "MISSING_EXECUTION_REFERENCE",
                        {"patch_id": patch_id, "target_body": target},
                    )
                if p_ref not in seen_profile_refs:
                    return (
                        f"Cut references unregistered profile '{p_ref}' in patch {patch_id}",
                        "MISSING_EXECUTION_REFERENCE",
                        {"patch_id": patch_id, "profile_ref": p_ref},
                    )

                # Require cut_direction == "into_solid"
                cut_dir = patch.get("cut_direction")
                if cut_dir != "into_solid":
                    return (
                        f"cut_direction is required and must be 'into_solid' in patch {patch_id}, got {cut_dir!r}",
                        "INVALID_LOWERED_PAYLOAD",
                        {"patch_id": patch_id},
                    )

                # Validate mandatory frame vectors
                norm_vec = patch.get("normal_vector")
                if norm_vec is None or not _is_unit_vector(norm_vec):
                    return (
                        f"normal_vector is required and must be a finite unit 3-vector in patch {patch_id}",
                        "INVALID_LOWERED_PAYLOAD",
                        {"patch_id": patch_id},
                    )

                u_ax, v_ax = patch.get("u_axis"), patch.get("v_axis")
                if u_ax is None or not _is_unit_vector(u_ax):
                    return (
                        f"u_axis is required and must be a finite unit 3-vector in patch {patch_id}",
                        "INVALID_LOWERED_PAYLOAD",
                        {"patch_id": patch_id},
                    )
                if v_ax is None or not _is_unit_vector(v_ax):
                    return (
                        f"v_axis is required and must be a finite unit 3-vector in patch {patch_id}",
                        "INVALID_LOWERED_PAYLOAD",
                        {"patch_id": patch_id},
                    )
                if not math.isclose(_dot_product(u_ax, v_ax), 0.0, abs_tol=1e-2):
                    return (
                        f"u_axis and v_axis must be orthogonal in patch {patch_id}",
                        "INVALID_LOWERED_PAYLOAD",
                        {"patch_id": patch_id},
                    )
                cross_uv = _cross_product(u_ax, v_ax)
                if not (
                    math.isclose(cross_uv[0], norm_vec[0], abs_tol=1e-2)
                    and math.isclose(cross_uv[1], norm_vec[1], abs_tol=1e-2)
                    and math.isclose(cross_uv[2], norm_vec[2], abs_tol=1e-2)
                ):
                    return (
                        f"Frame u_axis x v_axis does not match normal_vector in patch {patch_id}",
                        "INVALID_LOWERED_PAYLOAD",
                        {"patch_id": patch_id},
                    )

                cut_vec = patch.get("cut_vector")
                if cut_vec is None or not _is_unit_vector(cut_vec):
                    return (
                        f"cut_vector is required and must be a finite unit 3-vector in patch {patch_id}",
                        "INVALID_LOWERED_PAYLOAD",
                        {"patch_id": patch_id},
                    )
                if not math.isclose(_dot_product(cut_vec, norm_vec), -1.0, rel_tol=1e-2, abs_tol=1e-2):
                    return (
                        f"cut_vector must be equal to negative normal_vector in patch {patch_id}",
                        "INVALID_LOWERED_PAYLOAD",
                        {"patch_id": patch_id},
                    )

                prof_family, _ = seen_profile_refs[p_ref]
                cap_key = (prof_family.lower(), extent_type.lower(), face.upper())
                if cap_key not in PROMOTED_CUT_CAPABILITY_ROWS:
                    return (
                        f"Unsupported cut capability row ({prof_family}, {extent_type}, {face}) in patch {patch_id}",
                        "UNSUPPORTED_EXECUTION_OPERATION",
                        {"patch_id": patch_id, "capability_key": cap_key},
                    )
                seen_result_refs.add(res_ref)

            elif op == "extrude_profile":
                target = patch.get("body_ref") or patch.get("target_body_ref")
                p_ref = patch.get("profile_ref")
                res_ref = patch.get("result_ref")
                face = _resolve_face_from_patch(patch)
                extent_type = "finite"

                if not target or not p_ref or not res_ref:
                    return (
                        f"Invalid extrude_profile payload in patch {patch_id}",
                        "INVALID_LOWERED_PAYLOAD",
                        {"patch_id": patch_id},
                    )
                if res_ref in seen_result_refs:
                    return (
                        f"Duplicate result_ref '{res_ref}' in patch {patch_id}",
                        "INVALID_LOWERED_PAYLOAD",
                        {"patch_id": patch_id},
                    )
                if target not in registered_bodies:
                    return (
                        f"Pad target '{target}' does not match active body '{base_body_ref}'",
                        "MISSING_EXECUTION_REFERENCE",
                        {"patch_id": patch_id, "target_body": target},
                    )
                if p_ref not in seen_profile_refs:
                    return (
                        f"Pad references unregistered profile '{p_ref}' in patch {patch_id}",
                        "MISSING_EXECUTION_REFERENCE",
                        {"patch_id": patch_id, "profile_ref": p_ref},
                    )

                dist_mm = float(
                    patch.get("distance_mm")
                    or patch.get("extent", {}).get("distance_mm", 0.0)
                    or patch.get("height_mm", 0.0)
                )
                if not math.isfinite(dist_mm) or dist_mm <= 0.0:
                    return (
                        f"Pad distance must be strictly positive in patch {patch_id}",
                        "INVALID_LOWERED_PAYLOAD",
                        {"patch_id": patch_id},
                    )

                prof_family, _ = seen_profile_refs[p_ref]
                cap_key = (prof_family.lower(), extent_type.lower(), face.upper())
                if cap_key not in PROMOTED_PAD_CAPABILITY_ROWS:
                    return (
                        f"Unsupported pad capability row ({prof_family}, {extent_type}, {face}) in patch {patch_id}",
                        "UNSUPPORTED_EXECUTION_OPERATION",
                        {"patch_id": patch_id, "capability_key": cap_key},
                    )
                seen_result_refs.add(res_ref)

            else:
                return (
                    f"Unsupported operation '{op}' in patch {patch_id}",
                    "UNSUPPORTED_EXECUTION_OPERATION",
                    {"patch_id": patch_id},
                )

        return None

    # -----------------------------------------------------------------------
    # Worker-Local Patch Execution
    # -----------------------------------------------------------------------

    def _execute_patches_on_worker(
        self,
        raw_doc: Any,
        worker: Any,
        patches: list[dict[str, Any]],
    ) -> tuple[list[OperationResult], StandardInspectionReport]:
        """Execute preflighted lowered patches inside the STA worker seam."""
        models: dict[str, Any] = {}
        sketches: dict[str, Any] = {}
        profiles: dict[str, tuple[Any, str]] = {}  # profile_ref -> (profile_obj, profile_family)
        tracked_features: dict[str, tuple[Any, str, str]] = {}  # result_ref -> (feature_obj, feature_type, patch_id)
        operations: list[OperationResult] = []

        for patch in patches:
            op = patch["op"]
            patch_id = patch["patch_id"]

            try:
                if op == "ensure_primitive_body":
                    shape = patch["shape"]
                    body_ref = patch["body_ref"]
                    offset = patch.get("origin_offset_mm", {})
                    shape_type = shape["type"]

                    if shape_type == "cuboid":
                        model = create_primitive_cuboid(
                            raw_doc,
                            worker,
                            length_mm=float(shape["length_mm"]),
                            width_mm=float(shape["width_mm"]),
                            height_mm=float(shape["height_mm"]),
                            origin_offset_mm=offset,
                        )
                    elif shape_type == "cylinder":
                        model = create_primitive_cylinder(
                            raw_doc,
                            worker,
                            radius_mm=float(shape["radius_mm"]),
                            height_mm=float(shape["height_mm"]),
                            origin_offset_mm=offset,
                        )
                    elif shape_type == "extruded_profile":
                        model = create_primitive_extruded_profile(
                            raw_doc,
                            worker,
                            points_mm=shape["points"],
                            height_mm=float(shape["height_mm"]),
                            origin_offset_mm=offset,
                        )
                    else:
                        raise CADExecutionError(
                            f"Unsupported base primitive shape: {shape_type}",
                            error_code="UNSUPPORTED_EXECUTION_OPERATION",
                        )

                    models[body_ref] = model
                    operations.append(
                        OperationResult(
                            patch_id=patch_id,
                            operation="ensure_primitive_body",
                            reference_id=body_ref,
                            reference_kind="body",
                        )
                    )

                elif op == "ensure_sketch":
                    sketch_ref = patch["sketch_ref"]
                    plane = patch["plane"]
                    offset_mm = float(patch.get("offset_mm", 0.0))
                    if offset_mm == 0.0 and isinstance(patch.get("origin_offset_mm"), dict):
                        orig = patch["origin_offset_mm"]
                        if plane == "XY":
                            offset_mm = float(orig.get("z_mm", 0.0))
                        elif plane == "YZ":
                            offset_mm = float(orig.get("x_mm", 0.0))
                        elif plane == "XZ":
                            offset_mm = float(orig.get("y_mm", 0.0))

                    ref_plane = resolve_or_create_reference_plane(raw_doc, worker, plane=plane, offset_mm=offset_mm)
                    sketches[sketch_ref] = ref_plane

                elif op == "ensure_profile":
                    profile_ref = patch["profile_ref"]
                    sketch_ref = patch["sketch_ref"]
                    prof_type, prof_data = _extract_profile_geometry(patch)
                    ref_plane = sketches[sketch_ref]
                    prof_obj = create_profile_on_plane(raw_doc, worker, ref_plane)

                    if prof_type == "circle":
                        center = prof_data.get("center", {})
                        cx = float(center.get("x_mm", prof_data.get("center_x_mm", 0.0)))
                        cy = float(center.get("y_mm", prof_data.get("center_y_mm", 0.0)))
                        rad = float(prof_data["radius_mm"])
                        draw_circle_profile(prof_obj, worker, center_x_mm=cx, center_y_mm=cy, radius_mm=rad)

                    elif prof_type == "polygon":
                        points = prof_data.get("points") or prof_data.get("points_mm", [])
                        draw_polygon_profile(prof_obj, worker, points_mm=points)

                    elif prof_type == "slot":
                        center = prof_data.get("center", {})
                        cx = float(center.get("x_mm", prof_data.get("center_x_mm", 0.0)))
                        cy = float(center.get("y_mm", prof_data.get("center_y_mm", 0.0)))
                        length = float(prof_data["length_mm"])
                        width = float(prof_data["width_mm"])
                        ori_axis = prof_data.get("orientation_axis", "x")
                        ori_deg = 90.0 if ori_axis == "y" else float(prof_data.get("orientation_deg", 0.0))
                        draw_slot_profile(
                            prof_obj,
                            worker,
                            center_x_mm=cx,
                            center_y_mm=cy,
                            length_mm=length,
                            width_mm=width,
                            orientation_deg=ori_deg,
                        )
                    else:
                        raise CADExecutionError(
                            f"Unsupported profile type: {prof_type}",
                            error_code="UNSUPPORTED_EXECUTION_OPERATION",
                        )

                    profiles[profile_ref] = (prof_obj, prof_type)

                elif op == "cut_hole":
                    target_ref = patch.get("body_ref") or patch.get("target_body_ref", "")
                    profile_ref = patch["profile_ref"]
                    result_ref = patch.get("result_ref", patch_id)
                    face = _resolve_face_from_patch(patch)
                    through_all = (
                        patch.get("through_all", False) or patch.get("extent", {}).get("type") == "through_all"
                    )

                    target_model = models[target_ref]
                    prof_obj, prof_family = profiles[profile_ref]

                    if through_all:
                        cut_feat = create_cutout_through_all(
                            target_model,
                            worker,
                            profile=prof_obj,
                            face=face,
                            profile_family=prof_family,
                        )
                    else:
                        depth_mm = float(patch.get("depth_mm") or patch.get("extent", {}).get("depth_mm", 0.0))
                        cut_feat = create_cutout_finite(
                            target_model,
                            worker,
                            profile=prof_obj,
                            depth_mm=depth_mm,
                            face=face,
                            profile_family=prof_family,
                        )

                    tracked_features[result_ref] = (cut_feat, "cut_hole", patch_id)
                    operations.append(
                        OperationResult(
                            patch_id=patch_id,
                            operation="cut_hole",
                            reference_id=result_ref,
                            reference_kind="feature",
                        )
                    )

                elif op == "extrude_profile":
                    target_ref = patch.get("body_ref") or patch.get("target_body_ref", "")
                    profile_ref = patch["profile_ref"]
                    result_ref = patch.get("result_ref", patch_id)
                    face = _resolve_face_from_patch(patch)
                    dist_mm = float(
                        patch.get("distance_mm")
                        or patch.get("extent", {}).get("distance_mm", 0.0)
                        or patch.get("height_mm", 0.0)
                    )

                    target_model = models[target_ref]
                    prof_obj, prof_family = profiles[profile_ref]

                    pad_feat = create_pad_protrusion(
                        target_model,
                        worker,
                        profile=prof_obj,
                        height_mm=dist_mm,
                        face=face,
                        profile_family=prof_family,
                    )

                    tracked_features[result_ref] = (pad_feat, "extrude_profile", patch_id)
                    operations.append(
                        OperationResult(
                            patch_id=patch_id,
                            operation="extrude_profile",
                            reference_id=result_ref,
                            reference_kind="feature",
                        )
                    )

            except CADExecutionError as exc:
                err_code = getattr(exc, "error_code", "CAD_EXECUTION_ERROR")
                details = dict(getattr(exc, "details", {}))
                details.setdefault("failing_patch_id", patch_id)
                details.setdefault("failing_operation", op)
                if "result_ref" in patch:
                    details.setdefault("failing_reference_id", patch["result_ref"])
                elif "body_ref" in patch:
                    details.setdefault("failing_reference_id", patch["body_ref"])
                elif "profile_ref" in patch:
                    details.setdefault("failing_reference_id", patch["profile_ref"])
                raise CADExecutionError(str(exc), error_code=err_code, details=details) from exc
            except Exception as exc:
                details = {
                    "failing_patch_id": patch_id,
                    "failing_operation": op,
                }
                if "result_ref" in patch:
                    details["failing_reference_id"] = patch["result_ref"]
                elif "body_ref" in patch:
                    details["failing_reference_id"] = patch["body_ref"]
                elif "profile_ref" in patch:
                    details["failing_reference_id"] = patch["profile_ref"]
                raise CADExecutionError(
                    f"Failed executing patch '{patch_id}' ({op}): {describe_exception(exc)}",
                    error_code="NATIVE_COM_ERROR",
                    details=details,
                ) from exc

        # Final Document Recompute and Authoritative Topology Inspection
        inspection_report = _inspect_model_topology_and_properties(
            raw_doc,
            worker,
            tracked_features=tracked_features,
        )

        return operations, inspection_report

    # -----------------------------------------------------------------------
    # Standard Inspection & Recompute Public Methods
    # -----------------------------------------------------------------------

    def inspect_active_document(self) -> StandardInspectionReport:
        """Extract standardized physical and geometric properties from the active document."""
        doc_handle = self._require_doc_handle()

        def _task(raw_doc: Any, worker: Any) -> StandardInspectionReport:
            return _inspect_model_topology_and_properties(raw_doc, worker)

        return self._runtime.run_document_task(doc_handle, _task, timeout=self._timeout_seconds)

    def recompute_physical_properties(self) -> None:
        """Force recomputation of physical and mass properties in the CAD kernel."""
        doc_handle = self._require_doc_handle()

        def _task(raw_doc: Any, worker: Any) -> None:
            _inspect_model_topology_and_properties(raw_doc, worker)
            return None

        self._runtime.run_document_task(doc_handle, _task, timeout=self._timeout_seconds)

    def update_document(self) -> None:
        """Force geometric recompute on the active document."""
        self.recompute_physical_properties()

    # -----------------------------------------------------------------------
    # Immediate Normalized Errors for Out-of-Scope Abstract Methods
    # -----------------------------------------------------------------------

    def create_prism_body(
        self,
        length_mm: float,
        width_mm: float,
        thickness_mm: float,
        placement_x_mm: float = 0.0,
        placement_y_mm: float = 0.0,
        placement_z_mm: float = 0.0,
        body_id: str = "body.main",
        **kwargs: Any,
    ) -> BodyRef:
        raise CADExecutionError(
            "Direct primitive creation method is out of scope for M3.2; use execute_feature_plan",
            error_code="UNSUPPORTED_EXECUTION_OPERATION",
        )

    def add_cylindrical_cutout(
        self,
        diameter_mm: float,
        depth_mm: float = 0.0,
        target_face: str = "+Z",
        center_u_mm: float = 0.0,
        center_v_mm: float = 0.0,
        body_id: str = "body.main",
        **kwargs: Any,
    ) -> FeatureRef:
        raise CADExecutionError(
            "Direct cutout creation method is out of scope for M3.2; use execute_feature_plan",
            error_code="UNSUPPORTED_EXECUTION_OPERATION",
        )

    def export_model(self, format_id: ArtifactFormat, output_path: Path) -> None:
        """Export the active document geometry to a required model format (PAR, STEP, or STL)."""
        if self._close_failed:
            raise CADDocumentError(
                "Document close previously failed; cannot export from unconfirmed document state",
                error_code="DOCUMENT_CLOSE_FAILED",
            )
        if self._doc_handle is None:
            raise CADDocumentError(
                "No active request document bound to executor",
                error_code="NO_ACTIVE_DOCUMENT",
            )
        self._runtime.run_document_task(
            self._doc_handle,
            lambda raw_doc, worker: export_model_to_path(raw_doc, worker, format_id, output_path),
            timeout=self._timeout_seconds,
        )

    def capture_preview(self, output_path: Path, width: int = 800, height: int = 600) -> None:
        """Capture a best-effort preview snapshot image (JPG) of the active document."""
        if self._close_failed:
            raise CADDocumentError(
                "Document close previously failed; cannot capture preview from unconfirmed document state",
                error_code="DOCUMENT_CLOSE_FAILED",
            )
        if self._doc_handle is None:
            raise CADDocumentError(
                "No active request document bound to executor",
                error_code="NO_ACTIVE_DOCUMENT",
            )
        self._runtime.run_document_task(
            self._doc_handle,
            lambda raw_doc, worker: capture_preview_image(raw_doc, worker, output_path, width=width, height=height),
            timeout=self._timeout_seconds,
        )

    def close_request_document(self) -> None:
        """Terminally release and close the bound request document handle."""
        if self._close_failed:
            raise CADDocumentError(
                "Document close previously failed; handle is not confirmed released",
                error_code="DOCUMENT_CLOSE_FAILED",
            )
        if self._doc_handle is not None:
            handle = self._doc_handle
            self._doc_handle = None
            try:
                self._runtime.close_document(handle)
            except Exception:
                self._close_failed = True
                raise

    def generate_flat_pattern(self, output_path: Path) -> None:
        raise CADExecutionError(
            "generate_flat_pattern is out of scope for Milestone 3",
            error_code="UNSUPPORTED_EXECUTION_OPERATION",
        )

    def generate_draft(self, output_path: Path) -> None:
        raise CADExecutionError(
            "generate_draft is out of scope for Milestone 3",
            error_code="UNSUPPORTED_EXECUTION_OPERATION",
        )

    def publish_drawing(self, output_path: Path) -> None:
        raise CADExecutionError(
            "publish_drawing is out of scope for Milestone 3",
            error_code="UNSUPPORTED_EXECUTION_OPERATION",
        )

    def read_custom_properties(self) -> dict[str, Any]:
        raise CADExecutionError(
            "read_custom_properties is out of scope for Milestone 3",
            error_code="UNSUPPORTED_EXECUTION_OPERATION",
        )

    def write_custom_properties(self, properties: dict[str, Any]) -> None:
        raise CADExecutionError(
            "write_custom_properties is out of scope for Milestone 3",
            error_code="UNSUPPORTED_EXECUTION_OPERATION",
        )


__all__ = ["SolidEdgeExecutor"]
