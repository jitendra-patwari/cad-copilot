"""Pure response projection, error classification, and warning sanitization for CAD Copilot.

Invariants:
    - Zero I/O Purity: 100% pure computational logic with zero filesystem, COM, or driver imports.
    - Warning Parity: every response variant (accepted, rejected, failed) includes warnings: string[].
    - Redaction & Sanitization: errors use stable allowlisted messages; no raw exception/provider leaks.
    - Deterministic Warning Deduplication: exact duplicate warnings removed while preserving order.
    - Schema Conformance: produced payloads conform strictly to Draft 2020-12 generation-response schema.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from geometry.plan_models import DefaultApplied, ValidationDiagnostic
from interfaces.models import ArtifactRecord

# Canonical error codes and their stable public descriptions (Section 6.2)
PUBLIC_ERROR_MESSAGES: Mapping[str, str] = {
    "UNSUPPORTED_REQUEST": "The requested example or generation type is not supported.",
    "PROMPT_INTERPRETATION_FAILED": "Natural language prompt interpretation failed.",
    "CAD_PLAN_REJECTED": "The feature plan is invalid or unsupported by the CAD executor.",
    "CAD_EXECUTION_FAILED": "CAD runtime execution or document operation failed.",
    "NATIVE_QA_BLOCKED": "Native CAD geometry quality inspection failed.",
    "ARTIFACT_EXPORT_FAILED": "Export or verification of required CAD artifacts failed.",
    "OUTPUT_PATH_NOT_ALLOWED": "Requested output path is not allowed outside the configured output root.",
    "TARGET_ALREADY_EXISTS": "Output destination already exists.",
    "INTERNAL_ERROR": "An internal service error occurred.",
    "INVALID_SCHEMA": "Request payload failed schema validation.",
    "PAYLOAD_TOO_LARGE": "Request payload exceeds maximum size limit.",
}

# Status-specific error code allowlists (Section 6.2)
REJECTED_ERROR_CODES: frozenset[str] = frozenset(
    {
        "UNSUPPORTED_REQUEST",
        "INVALID_SCHEMA",
        "PAYLOAD_TOO_LARGE",
    }
)

FAILED_ERROR_CODES: frozenset[str] = frozenset(
    {
        "PROMPT_INTERPRETATION_FAILED",
        "CAD_PLAN_REJECTED",
        "CAD_EXECUTION_FAILED",
        "NATIVE_QA_BLOCKED",
        "ARTIFACT_EXPORT_FAILED",
        "OUTPUT_PATH_NOT_ALLOWED",
        "TARGET_ALREADY_EXISTS",
        "INTERNAL_ERROR",
    }
)

# Recognized structured warning codes mapped to stable public descriptions (Section 6.3)
PUBLIC_WARNING_MESSAGES: Mapping[str, str] = {
    # Artifact & Preview warnings
    "PREVIEW_EXPORT_FAILED": "Preview image generation was skipped or unavailable; CAD geometry exported successfully.",
    "PREVIEW_CLEANUP_FAILED": "Temporary preview export file cleanup failed.",
    # Runtime warnings
    "VERSION_METADATA_UNAVAILABLE": "CAD runtime did not report version metadata.",
    # Validation & Gate Policy warnings
    "GATE_POLICY_RELAXED": "Feature validation evaluated under relaxed capability-first policy.",
    "GATE_POLICY_SHADOW": "Feature validation evaluated under shadow gate policy.",
    "CANONICAL_MULTI_HOLE_FAMILY": "Multiple circular through-holes accepted by canonical validation.",
    "UNKNOWN_BOOLEAN_OPERATION": "Unrecognized boolean operation fell back to default union.",
    "UNKNOWN_PLACEMENT_MODE": "Unrecognized placement mode fell back to default absolute placement.",
    "UNKNOWN_FACE_ALIAS": "Unrecognized face alias fell back to default sketch plane.",
    "UNKNOWN_SLOT_ORIENTATION": "Unrecognized slot orientation fell back to default horizontal orientation.",
    "UNKNOWN_SWEEP_PATH_TYPE": "Unrecognized sweep path type fell back to default.",
    "UNKNOWN_SWEEP_SECTION_TYPE": "Unrecognized sweep section type fell back to default.",
    "UNKNOWN_SWEEP_SECTION_POSITION": "Unrecognized sweep section position fell back to default.",
    "AI_PROPOSAL_NORMALIZED": (
        "The AI proposal included extra formatting or envelope metadata; supported plan content was normalized."
    ),
    "AI_DIMENSIONS_ASSUMED": ("The AI proposal omitted base dimensions; illustrative millimeter defaults were used."),
    "PROMPT_PROVIDER_UNAVAILABLE": "The AI provider request did not complete. Check connectivity, access, and retry later.",
    "PROMPT_CONFIGURATION_INVALID": "The AI provider is not configured or its runtime dependency is unavailable.",
    "PROMPT_RESPONSE_EMPTY": "The AI provider returned no usable text for a CAD plan.",
    "PROMPT_RESPONSE_INVALID": "The AI provider response could not form a supported CAD plan.",
}

GENERIC_WARNING_MESSAGE: str = "A non-fatal diagnostic warning was recorded."
KNOWN_PUBLIC_WARNING_STRINGS: frozenset[str] = frozenset(PUBLIC_WARNING_MESSAGES.values()) | {GENERIC_WARNING_MESSAGE}

# Canonical artifact constraints
CANONICAL_FORMAT_ORDER: tuple[str, ...] = ("par", "step", "stl", "jpg")
FORMAT_TO_TYPE: Mapping[str, str] = {
    "par": "native_part",
    "step": "geometry_step",
    "stl": "mesh_stl",
    "jpg": "preview_image",
}
REQUIRED_FORMATS: frozenset[str] = frozenset({"par", "step", "stl"})
ALLOWED_FORMATS: frozenset[str] = frozenset({"par", "step", "stl", "jpg"})

# Path and credential sanitization patterns (including paths with spaces and UNC shares)
WINDOWS_DRIVE_PATTERN: re.Pattern[str] = re.compile(r"\b[A-Za-z]:[\\/][^,\n\r]*")
UNC_PATH_PATTERN: re.Pattern[str] = re.compile(r"\\\\[^,\n\r]+")
POSIX_PATH_PATTERN: re.Pattern[str] = re.compile(r"/(?:tmp|home|Users|var|etc|workspace)/[^,\n\r]*")

# Bounded, safe field patterns for DefaultApplied projection
SAFE_PLAN_PATH_PATTERN: re.Pattern[str] = re.compile(r"^[a-zA-Z][a-zA-Z0-9_.\[\]]{0,63}$")
SAFE_VALUE_PATTERN: re.Pattern[str] = re.compile(r"^[+\-]?[a-zA-Z0-9_]{1,32}$")
SECRET_KEY_PATTERN: re.Pattern[str] = re.compile(
    r"(?i)(?:AIza[0-9A-Za-z\-_]{16,}|sentinel|api[_-]?key|secret|password|bearer\s+[A-Za-z0-9._~+/-]+)"
)


class ArtifactProjectionError(ValueError):
    """Raised when artifact records violate accepted response contract invariants."""


def _format_safe_default_applied(record: DefaultApplied) -> str:
    """Format a concrete DefaultApplied record into a bounded, safe description.

    Exposes only stable text plus the safe canonical path and applied value.
    Never emits raw original_value or reason fields to prevent secret/path leakage.
    """
    path = record.path
    if not isinstance(path, str) or not SAFE_PLAN_PATH_PATTERN.match(path) or SECRET_KEY_PATTERN.search(path):
        return GENERIC_WARNING_MESSAGE

    val = record.value
    if isinstance(val, bool):
        val_str = str(val)
    elif isinstance(val, (int, float)):
        if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
            return GENERIC_WARNING_MESSAGE
        val_str = str(val)
    elif isinstance(val, str) and SAFE_VALUE_PATTERN.match(val) and not SECRET_KEY_PATTERN.search(val):
        val_str = val
    else:
        return GENERIC_WARNING_MESSAGE

    return f"Default applied to {path}: applied {val_str}."


def project_warning(warning_obj: object) -> str:
    """Convert an untrusted warning into a stable public string.

    Never passes raw strings or arbitrary payloads through. Maps recognized structured
    codes to stable public descriptions; maps concrete canonical DefaultApplied records
    to deterministic descriptions with bounded, safe fields; unknown structures, raw strings,
    prompts, or credentials are mapped safely to the generic warning message.
    """
    text: str = GENERIC_WARNING_MESSAGE

    if isinstance(warning_obj, DefaultApplied):
        text = _format_safe_default_applied(warning_obj)
    elif isinstance(warning_obj, ValidationDiagnostic):
        if warning_obj.code in PUBLIC_WARNING_MESSAGES:
            text = PUBLIC_WARNING_MESSAGES[warning_obj.code]
    elif isinstance(warning_obj, Mapping):
        code_val = warning_obj.get("code") or warning_obj.get("warning")
        if isinstance(code_val, str) and code_val in PUBLIC_WARNING_MESSAGES:
            text = PUBLIC_WARNING_MESSAGES[code_val]
    elif isinstance(warning_obj, str):
        if warning_obj in PUBLIC_WARNING_MESSAGES:
            text = PUBLIC_WARNING_MESSAGES[warning_obj]
        elif warning_obj in KNOWN_PUBLIC_WARNING_STRINGS:
            text = warning_obj

    # Clean control characters and normalize whitespace
    cleaned = "".join(ch if (ch.isprintable() or ch in " \t") else " " for ch in text)
    normalized = " ".join(cleaned.split())
    if not normalized:
        return ""

    # Redact local workstation and UNC paths to prevent host environment leaks (SEC-07, CWE-209)
    sanitized = WINDOWS_DRIVE_PATTERN.sub("<sanitized-path>", normalized)
    sanitized = UNC_PATH_PATTERN.sub("<sanitized-path>", sanitized)
    sanitized = POSIX_PATH_PATTERN.sub("<sanitized-path>", sanitized)
    if "<sanitized-path>" in sanitized:
        return GENERIC_WARNING_MESSAGE
    return sanitized


def project_warnings(raw_warnings: Sequence[object]) -> list[str]:
    """Project, sanitize, and deduplicate warnings while strictly preserving arrival order."""
    seen: set[str] = set()
    result: list[str] = []
    for item in raw_warnings:
        projected = project_warning(item)
        if projected and projected not in seen:
            seen.add(projected)
            result.append(projected)
    return result


def project_artifacts(artifacts: Sequence[ArtifactRecord]) -> list[dict[str, str]]:
    """Project exported artifact records into canonical response order.

    Requires exactly one .par, one .step, and one .stl, with at most one .jpg preview.
    Origin is strictly validated and canonicalized to 'cad_copilot'.
    Raises ArtifactProjectionError on missing, duplicate, or unrecognized artifact formats.
    """
    if not isinstance(artifacts, Sequence) or len(artifacts) < 3 or len(artifacts) > 4:
        raise ArtifactProjectionError(
            f"Accepted response requires 3 or 4 artifact records, got {len(artifacts) if isinstance(artifacts, Sequence) else 'invalid sequence'}"
        )

    by_format: dict[str, dict[str, str]] = {}
    for record in artifacts:
        if isinstance(record, ArtifactRecord):
            fmt = record.format
            typ = record.type
            path_val = record.path
            origin = record.origin
        else:
            raise ArtifactProjectionError(f"Artifact record must be ArtifactRecord, got {type(record).__name__}")

        if fmt not in ALLOWED_FORMATS:
            raise ArtifactProjectionError(f"Unsupported artifact format '{fmt}'")
        if fmt in by_format:
            raise ArtifactProjectionError(f"Duplicate artifact format '{fmt}' detected")
        expected_type = FORMAT_TO_TYPE[fmt]
        if typ != expected_type:
            raise ArtifactProjectionError(f"Artifact format '{fmt}' requires type '{expected_type}', got '{typ}'")
        if not isinstance(path_val, str) or not path_val.strip() or "\x00" in path_val:
            raise ArtifactProjectionError(f"Artifact path for format '{fmt}' must be a valid non-empty string")
        if origin != "cad_copilot":
            raise ArtifactProjectionError(f"Artifact origin must be 'cad_copilot', got '{origin}'")

        by_format[fmt] = {
            "type": expected_type,
            "format": fmt,
            "path": path_val.strip(),
            "origin": "cad_copilot",
        }

    # Verify all required formats are present
    missing = REQUIRED_FORMATS - set(by_format.keys())
    if missing:
        missing_fmt = sorted(missing)[0]
        raise ArtifactProjectionError(f"Missing required artifact format '{missing_fmt}'")

    # Assemble strictly in canonical format order
    return [by_format[fmt] for fmt in CANONICAL_FORMAT_ORDER if fmt in by_format]


def build_accepted_response(
    request_id: str,
    artifacts: Sequence[ArtifactRecord],
    warnings: Sequence[object] = (),
) -> dict[str, Any]:
    """Build a Draft 2020-12 schema-compliant accepted generation response payload."""
    if not isinstance(request_id, str) or not (1 <= len(request_id) <= 96):
        raise ValueError("request_id must be a string of 1-96 characters")

    return {
        "contract_version": "1.0",
        "request_id": request_id,
        "status": "accepted",
        "data": {
            "artifacts": project_artifacts(artifacts),
        },
        "warnings": project_warnings(warnings),
    }


def build_rejected_response(
    request_id: str,
    error_code: str,
    *,
    warnings: Sequence[object] = (),
    field: str | None = None,
) -> dict[str, Any]:
    """Build a Draft 2020-12 schema-compliant rejected generation response payload."""
    if not isinstance(request_id, str) or not (1 <= len(request_id) <= 96):
        raise ValueError("request_id must be a string of 1-96 characters")

    code = error_code if error_code in REJECTED_ERROR_CODES else "UNSUPPORTED_REQUEST"
    msg = PUBLIC_ERROR_MESSAGES[code]
    error_record: dict[str, str] = {"code": code, "message": msg}
    if field is not None and isinstance(field, str) and field.strip():
        error_record["field"] = field.strip()

    return {
        "contract_version": "1.0",
        "request_id": request_id,
        "status": "rejected",
        "errors": [error_record],
        "warnings": project_warnings(warnings),
    }


def build_failed_response(
    request_id: str,
    error_code: str,
    *,
    warnings: Sequence[object] = (),
    field: str | None = None,
) -> dict[str, Any]:
    """Build a Draft 2020-12 schema-compliant failed generation response payload."""
    if not isinstance(request_id, str) or not (1 <= len(request_id) <= 96):
        raise ValueError("request_id must be a string of 1-96 characters")

    code = error_code if error_code in FAILED_ERROR_CODES else "INTERNAL_ERROR"
    msg = PUBLIC_ERROR_MESSAGES[code]
    error_record: dict[str, str] = {"code": code, "message": msg}
    if field is not None and isinstance(field, str) and field.strip():
        error_record["field"] = field.strip()

    return {
        "contract_version": "1.0",
        "request_id": request_id,
        "status": "failed",
        "errors": [error_record],
        "warnings": project_warnings(warnings),
    }
