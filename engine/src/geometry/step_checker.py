"""Linear STEP Part 21 in-place coordinate envelope extractor and topological smoke checker.

Invariants:
    - Zero-I/O Purity (NFR-1): Operates strictly on in-memory text with zero filesystem I/O.
    - True O(1) Auxiliary Memory: Scans by string indices without materializing substrings or full-text copies.
    - Deterministic Linear Scaling: O(N) single-pass character traversal with zero backtracking.
    - Strict Bounded Limits (CWE-400): Enforces MAX_STEP_TEXT_CHARS and MAX_DIAGNOSTICS = 100 caps.
    - ISO 10303-41 Unit Standards: Resolves SI_UNIT(.MILLI., .METRE.) and SI_UNIT($, .METRE.) declarations.
    - Strict Static Typing (NFR-4): 100% annotated with explicit __all__ exports.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

MAX_STEP_TEXT_CHARS: int = 50_000_000
MAX_COORDINATE_TOKEN_LEN: int = 64
MAX_COORDINATE_TUPLE_CHARS: int = 256
MAX_UNIT_DECL_CHARS: int = 256
MAX_DIAGNOSTICS: int = 100

StepSanityKind = Literal[
    "rectangular_block",
    "centered_plate_through_hole",
    "centered_plate_blind_hole",
    "two_hole_plate_through_holes",
    "rectangular_plate_through_cutout",
    "rectangular_plate_blind_cutout",
    "slot_plate_through_cutout",
    "slot_plate_blind_cutout",
    "side_face_plate_through_hole",
    "side_face_plate_rectangular_cutout",
    "side_face_plate_slot_cutout",
    "rectangular_plate_extruded_pad",
    "mixed_plate_rectangular_cutout_two_cylinders",
    "mixed_plate_three_cylinders",
]


@dataclass(frozen=True)
class StepBoundingBox:
    """3D raw point coordinate envelope in the file's native coordinate units."""

    min_x: float
    max_x: float
    min_y: float
    max_y: float
    min_z: float
    max_z: float
    size_x: float
    size_y: float
    size_z: float

    def span_tuple(self) -> tuple[float, float, float]:
        """Return the raw span dimensions (size_x, size_y, size_z)."""
        return (self.size_x, self.size_y, self.size_z)

    def scale(self, factor: float) -> StepBoundingBox:
        """Return a new bounding box scaled by a multiplicative factor."""
        return StepBoundingBox(
            min_x=_normalize_zero(self.min_x * factor),
            max_x=_normalize_zero(self.max_x * factor),
            min_y=_normalize_zero(self.min_y * factor),
            max_y=_normalize_zero(self.max_y * factor),
            min_z=_normalize_zero(self.min_z * factor),
            max_z=_normalize_zero(self.max_z * factor),
            size_x=_normalize_zero(self.size_x * factor),
            size_y=_normalize_zero(self.size_y * factor),
            size_z=_normalize_zero(self.size_z * factor),
        )

    def to_dict(self) -> dict[str, float]:
        """Serialize envelope bounds and span extents to a dictionary."""
        return {
            "min_x": self.min_x,
            "max_x": self.max_x,
            "min_y": self.min_y,
            "max_y": self.max_y,
            "min_z": self.min_z,
            "max_z": self.max_z,
            "size_x": self.size_x,
            "size_y": self.size_y,
            "size_z": self.size_z,
        }

    @property
    def is_empty(self) -> bool:
        """Return True if all spatial span extents evaluate to zero."""
        return self.size_x == 0.0 and self.size_y == 0.0 and self.size_z == 0.0


@dataclass(frozen=True)
class StepEntitySignals:
    """Lightweight STEP topological entity counts and bounding spans."""

    advanced_faces: int = 0
    plane_surfaces: int = 0
    cylindrical_surfaces: int = 0
    line_curves: int = 0
    edge_curves: int = 0
    manifold_solid_breps: int = 0
    closed_shells: int = 0
    edge_loops: int = 0
    span_x: float = 0.0
    span_y: float = 0.0
    span_z: float = 0.0

    def to_dict(self) -> dict[str, int | float]:
        """Serialize entity signal counts and bounding spans to a dictionary."""
        return {
            "advanced_faces": self.advanced_faces,
            "plane_surfaces": self.plane_surfaces,
            "cylindrical_surfaces": self.cylindrical_surfaces,
            "line_curves": self.line_curves,
            "edge_curves": self.edge_curves,
            "manifold_solid_breps": self.manifold_solid_breps,
            "closed_shells": self.closed_shells,
            "edge_loops": self.edge_loops,
            "span_x": self.span_x,
            "span_y": self.span_y,
            "span_z": self.span_z,
        }


@dataclass(frozen=True)
class StepValidationResult:
    """Structural, dimensional, and topological validation outcome for a STEP Part 21 text payload."""

    is_valid: bool
    solid_brep_detected: bool
    bounding_box: StepBoundingBox | None = None
    unit_scale_to_mm: float | None = None
    unit_name: str = "unresolved"
    bounding_box_mm: StepBoundingBox | None = None
    entity_counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    signals: StepEntitySignals | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize validation outcome to a dictionary."""
        return {
            "is_valid": self.is_valid,
            "solid_brep_detected": self.solid_brep_detected,
            "bounding_box": self.bounding_box.to_dict() if self.bounding_box is not None else None,
            "unit_scale_to_mm": self.unit_scale_to_mm,
            "unit_name": self.unit_name,
            "bounding_box_mm": self.bounding_box_mm.to_dict() if self.bounding_box_mm is not None else None,
            "entity_counts": dict(self.entity_counts),
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "signals": self.signals.to_dict() if self.signals is not None else None,
        }


@dataclass(frozen=True)
class StepSanityResult:
    """Outcome of a shape-specific smoke verification check."""

    kind: StepSanityKind
    passed: bool
    signals: StepEntitySignals
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize smoke check result to a dictionary."""
        return {
            "kind": self.kind,
            "passed": self.passed,
            "signals": self.signals.to_dict(),
            "errors": list(self.errors),
        }


def _normalize_zero(val: float) -> float:
    """Normalize IEEE-754 signed negative zero to positive zero."""
    return 0.0 if val == 0.0 else val


def _append_diagnostic(diag_list: list[str], message: str) -> None:
    """Append a unique diagnostic message to a list, keeping total length <= MAX_DIAGNOSTICS (100)."""
    if message in diag_list:
        return

    if len(diag_list) < MAX_DIAGNOSTICS - 1:
        diag_list.append(message)
    elif len(diag_list) == MAX_DIAGNOSTICS - 1:
        trunc_msg = f"Diagnostic limit reached ({MAX_DIAGNOSTICS}); additional diagnostics suppressed."
        diag_list.append(trunc_msg)


def _find_keyword_ci(text: str, keyword: str, start: int = 0, end: int | None = None) -> int:
    """Find the first case-insensitive match of a keyword outside comments and string literals."""
    limit = len(text) if end is None else min(end, len(text))
    kw_len = len(keyword)
    if kw_len == 0 or start + kw_len > limit:
        return -1

    first_upper = keyword[0].upper()
    first_lower = keyword[0].lower()
    kw_upper = keyword.upper()

    i = start
    in_comment = False
    in_string = False

    while i <= limit - kw_len:
        if in_comment:
            if i + 1 < limit and text[i] == "*" and text[i + 1] == "/":
                in_comment = False
                i += 2
            else:
                i += 1
        elif in_string:
            if text[i] == "'":
                if i + 1 < limit and text[i + 1] == "'":
                    i += 2
                else:
                    in_string = False
                    i += 1
            else:
                i += 1
        else:
            if i + 1 < limit and text[i] == "/" and text[i + 1] == "*":
                in_comment = True
                i += 2
                continue
            elif text[i] == "'":
                in_string = True
                i += 1
                continue

            c = text[i]
            if c in (first_upper, first_lower):
                match = True
                for k in range(1, kw_len):
                    if text[i + k].upper() != kw_upper[k]:
                        match = False
                        break
                if match:
                    return i
            i += 1

    return -1


def _extract_data_section_indices(step_text: str) -> tuple[int, int, list[str]]:
    """Locate the (start_idx, end_idx) index range of the DATA section in O(1) auxiliary memory."""
    errors: list[str] = []
    text_len = len(step_text)
    if text_len == 0:
        errors.append("STEP text is empty")
        return 0, 0, errors

    if text_len > MAX_STEP_TEXT_CHARS:
        errors.append(f"STEP text exceeds maximum length limit of {MAX_STEP_TEXT_CHARS} characters")
        return 0, 0, errors

    # Check ISO-10303-21 opening marker
    header_open_idx = -1
    pos = 0
    while pos < text_len:
        match_idx = _find_keyword_ci(step_text, "ISO-10303-21;", pos)
        if match_idx == -1:
            break
        if match_idx >= 4 and step_text[match_idx - 4 : match_idx].upper() == "END-":
            pos = match_idx + len("ISO-10303-21;")
            continue
        header_open_idx = match_idx
        break

    if header_open_idx == -1:
        _append_diagnostic(errors, "missing ISO-10303-21; exchange structure opening marker")
        search_from = 0
    else:
        search_from = header_open_idx + len("ISO-10303-21;")

    header_sec_idx = _find_keyword_ci(step_text, "HEADER;", search_from)
    if header_sec_idx == -1:
        _append_diagnostic(errors, "missing HEADER; section declaration")
        header_endsec_search = search_from
    else:
        header_endsec_search = header_sec_idx + len("HEADER;")

    header_endsec_idx = _find_keyword_ci(step_text, "ENDSEC;", header_endsec_search)
    if header_endsec_idx == -1:
        _append_diagnostic(errors, "missing ENDSEC; closing marker for HEADER section")
        data_search_from = header_endsec_search
    else:
        data_search_from = header_endsec_idx + len("ENDSEC;")

    data_sec_idx = _find_keyword_ci(step_text, "DATA;", data_search_from)
    if data_sec_idx == -1:
        _append_diagnostic(errors, "missing DATA; section declaration")
        return 0, 0, errors

    data_content_start = data_sec_idx + len("DATA;")
    data_endsec_idx = _find_keyword_ci(step_text, "ENDSEC;", data_content_start)
    if data_endsec_idx == -1:
        _append_diagnostic(errors, "missing ENDSEC; closing marker for DATA section")
        return 0, 0, errors

    footer_close_idx = _find_keyword_ci(step_text, "END-ISO-10303-21;", data_endsec_idx + len("ENDSEC;"))
    if footer_close_idx == -1:
        _append_diagnostic(errors, "missing END-ISO-10303-21; exchange structure closing marker")

    return data_content_start, data_endsec_idx, errors


def _extract_length_unit_scale(step_text: str, start_idx: int, end_idx: int) -> tuple[float | None, str, list[str]]:
    """Detect length unit scale factors conforming to ISO 10303-41 with exact two-argument whitelisting."""
    warnings: list[str] = []
    text_len = len(step_text)
    limit = min(text_len, end_idx)

    scales: set[float] = set()
    unit_names: set[str] = set()

    idx = start_idx
    in_comment = False
    in_string = False
    statement_has_length_unit = False

    while idx < limit:
        if in_comment:
            if idx + 1 < limit and step_text[idx] == "*" and step_text[idx + 1] == "/":
                in_comment = False
                idx += 2
            else:
                idx += 1
        elif in_string:
            if step_text[idx] == "'":
                if idx + 1 < limit and step_text[idx + 1] == "'":
                    idx += 2
                else:
                    in_string = False
                    idx += 1
            else:
                idx += 1
        else:
            if idx + 1 < limit and step_text[idx] == "/" and step_text[idx + 1] == "*":
                in_comment = True
                idx += 2
                continue
            elif step_text[idx] == "'":
                in_string = True
                idx += 1
                continue
            elif step_text[idx] == ";":
                statement_has_length_unit = False
                idx += 1
                continue

            # Check if this token is LENGTH_UNIT
            if (
                step_text[idx] in ("L", "l")
                and idx + 11 <= limit
                and step_text[idx : idx + 11].upper() == "LENGTH_UNIT"
                and not (idx > 0 and (step_text[idx - 1].isalnum() or step_text[idx - 1] == "_"))
                and not (idx + 11 < limit and (step_text[idx + 11].isalnum() or step_text[idx + 11] == "_"))
            ):
                statement_has_length_unit = True
                idx += 11
                continue

            # Check if this token starts with SI_UNIT
            if step_text[idx] in ("S", "s") and idx + 7 <= limit and step_text[idx : idx + 7].upper() == "SI_UNIT":
                # Check left boundary
                if not (idx > 0 and (step_text[idx - 1].isalnum() or step_text[idx - 1] == "_")):
                    p = idx + 7
                    while p < limit and step_text[p].isspace():
                        p += 1
                    if p < limit and step_text[p] == "(":
                        close_p = p + 1
                        paren_depth = 1
                        while close_p < limit and paren_depth > 0:
                            if step_text[close_p] == "(":
                                paren_depth += 1
                            elif step_text[close_p] == ")":
                                paren_depth -= 1
                                if paren_depth == 0:
                                    break
                            elif step_text[close_p] == ";":
                                break
                            close_p += 1

                        if paren_depth == 0:
                            decl_len = close_p - p - 1
                            if 0 < decl_len <= MAX_UNIT_DECL_CHARS:
                                unit_decl = step_text[p + 1 : close_p].upper()
                                args = [arg.strip() for arg in unit_decl.split(",")]
                                if len(args) == 2:
                                    prefix_arg, unit_arg = args[0], args[1]
                                    if statement_has_length_unit and unit_arg == ".METRE.":
                                        if prefix_arg == ".MILLI.":
                                            scales.add(1.0)
                                            unit_names.add("millimetre")
                                        elif prefix_arg == ".NONE.":
                                            _append_diagnostic(
                                                warnings,
                                                "UNSUPPORTED_STEP_UNIT_PREFIX: .NONE. prefix is invalid under ISO 10303-41.",
                                            )
                                        elif prefix_arg == "$":
                                            scales.add(1000.0)
                                            unit_names.add("metre")
                                        else:
                                            _append_diagnostic(
                                                warnings,
                                                f"UNSUPPORTED_STEP_UNIT_PREFIX: Unsupported length prefix in SI_UNIT({prefix_arg}, {unit_arg}).",
                                            )
                        idx = close_p + 1
                        continue
                idx += 7
                continue
            idx += 1

    if len(scales) == 1:
        return next(iter(scales)), next(iter(unit_names)), warnings
    elif len(scales) > 1:
        _append_diagnostic(
            warnings,
            "AMBIGUOUS_STEP_UNIT: Multiple conflicting length unit scales detected in STEP file.",
        )
        return None, "ambiguous", warnings
    else:
        _append_diagnostic(
            warnings,
            "UNRESOLVED_STEP_UNIT: No canonical ISO 10303-41 length unit detected; assuming native units.",
        )
        return None, "unresolved", warnings


def _scan_data_section_in_place(
    step_text: str, start_idx: int, end_idx: int
) -> tuple[StepBoundingBox | None, dict[str, int], list[str]]:
    """Scan the DATA section in-place with true O(1) auxiliary memory."""
    warnings: list[str] = []
    counts: dict[str, int] = {
        "ADVANCED_FACE": 0,
        "PLANE": 0,
        "CYLINDRICAL_SURFACE": 0,
        "LINE": 0,
        "EDGE_CURVE": 0,
        "MANIFOLD_SOLID_BREP": 0,
        "CLOSED_SHELL": 0,
        "EDGE_LOOP": 0,
    }

    if start_idx >= end_idx or len(step_text) == 0:
        return None, counts, warnings

    min_x: float | None = None
    max_x: float | None = None
    min_y: float | None = None
    max_y: float | None = None
    min_z: float | None = None
    max_z: float | None = None

    idx = start_idx
    in_comment = False
    in_string = False

    while idx < end_idx:
        if in_comment:
            if idx + 1 < end_idx and step_text[idx] == "*" and step_text[idx + 1] == "/":
                in_comment = False
                idx += 2
            else:
                idx += 1
        elif in_string:
            if step_text[idx] == "'":
                if idx + 1 < end_idx and step_text[idx + 1] == "'":
                    idx += 2
                else:
                    in_string = False
                    idx += 1
            else:
                idx += 1
        else:
            if idx + 1 < end_idx and step_text[idx] == "/" and step_text[idx + 1] == "*":
                in_comment = True
                idx += 2
                continue
            elif step_text[idx] == "'":
                in_string = True
                idx += 1
                continue

            char = step_text[idx]
            # Check left boundary
            is_left_bounded = True
            if idx > start_idx:
                prev_c = step_text[idx - 1]
                if prev_c.isalnum() or prev_c == "_":
                    is_left_bounded = False

            if is_left_bounded:
                # Check entity keywords
                matched_kw: str | None = None
                if (char == "A" or char == "a") and idx + 13 <= end_idx:
                    if step_text[idx : idx + 13].upper() == "ADVANCED_FACE":
                        matched_kw = "ADVANCED_FACE"
                elif (char == "P" or char == "p") and idx + 5 <= end_idx:
                    if step_text[idx : idx + 5].upper() == "PLANE":
                        matched_kw = "PLANE"
                elif char == "C" or char == "c":
                    if idx + 19 <= end_idx and step_text[idx : idx + 19].upper() == "CYLINDRICAL_SURFACE":
                        matched_kw = "CYLINDRICAL_SURFACE"
                    elif idx + 12 <= end_idx and step_text[idx : idx + 12].upper() == "CLOSED_SHELL":
                        matched_kw = "CLOSED_SHELL"
                    elif idx + 15 <= end_idx and step_text[idx : idx + 15].upper() == "CARTESIAN_POINT":
                        kw_len = 15
                        after_kw = idx + kw_len
                        if after_kw < end_idx and not (step_text[after_kw].isalnum() or step_text[after_kw] == "_"):
                            p = after_kw
                            while p < end_idx and step_text[p].isspace():
                                p += 1
                            if p < end_idx and step_text[p] == "(":
                                curr = p + 1
                                coord_tuple_start = -1
                                coord_tuple_end = -1
                                outer_depth = 1
                                while curr < end_idx and outer_depth > 0:
                                    c = step_text[curr]
                                    if c == "'":
                                        curr += 1
                                        while curr < end_idx:
                                            if step_text[curr] == "'":
                                                if curr + 1 < end_idx and step_text[curr + 1] == "'":
                                                    curr += 2
                                                    continue
                                                curr += 1
                                                break
                                            curr += 1
                                        continue
                                    if c == ";":
                                        break
                                    if c == "(":
                                        if coord_tuple_start == -1:
                                            coord_tuple_start = curr + 1
                                            paren_depth = 1
                                            search_coord = coord_tuple_start
                                            while search_coord < end_idx and paren_depth > 0:
                                                sc = step_text[search_coord]
                                                if sc == "(":
                                                    paren_depth += 1
                                                elif sc == ")":
                                                    paren_depth -= 1
                                                    if paren_depth == 0:
                                                        coord_tuple_end = search_coord
                                                        curr = search_coord + 1
                                                        break
                                                elif sc == ";":
                                                    break
                                                search_coord += 1
                                            continue
                                        else:
                                            outer_depth += 1
                                    elif c == ")":
                                        outer_depth -= 1
                                        if outer_depth == 0:
                                            curr += 1
                                            break
                                    curr += 1

                                if coord_tuple_start != -1 and coord_tuple_end != -1:
                                    tuple_len = coord_tuple_end - coord_tuple_start
                                    if 0 < tuple_len <= MAX_COORDINATE_TUPLE_CHARS:
                                        coord_str = step_text[coord_tuple_start:coord_tuple_end]
                                        tokens = [t.strip() for t in coord_str.split(",")]
                                        if len(tokens) == 3:
                                            try:
                                                if all(0 < len(t) <= MAX_COORDINATE_TOKEN_LEN for t in tokens):
                                                    x = float(tokens[0])
                                                    y = float(tokens[1])
                                                    z = float(tokens[2])
                                                    if math.isfinite(x) and math.isfinite(y) and math.isfinite(z):
                                                        x_norm = _normalize_zero(x)
                                                        y_norm = _normalize_zero(y)
                                                        z_norm = _normalize_zero(z)
                                                        if (
                                                            min_x is None
                                                            or max_x is None
                                                            or min_y is None
                                                            or max_y is None
                                                            or min_z is None
                                                            or max_z is None
                                                        ):
                                                            min_x, max_x = x_norm, x_norm
                                                            min_y, max_y = y_norm, y_norm
                                                            min_z, max_z = z_norm, z_norm
                                                        else:
                                                            if x_norm < min_x:
                                                                min_x = x_norm
                                                            elif x_norm > max_x:
                                                                max_x = x_norm
                                                            if y_norm < min_y:
                                                                min_y = y_norm
                                                            elif y_norm > max_y:
                                                                max_y = y_norm
                                                            if z_norm < min_z:
                                                                min_z = z_norm
                                                            elif z_norm > max_z:
                                                                max_z = z_norm
                                            except ValueError:
                                                pass
                                idx = max(curr, after_kw + 1)
                                continue
                elif (char == "L" or char == "l") and idx + 4 <= end_idx:
                    if step_text[idx : idx + 4].upper() == "LINE":
                        matched_kw = "LINE"
                elif char == "E" or char == "e":
                    if idx + 10 <= end_idx and step_text[idx : idx + 10].upper() == "EDGE_CURVE":
                        matched_kw = "EDGE_CURVE"
                    elif idx + 9 <= end_idx and step_text[idx : idx + 9].upper() == "EDGE_LOOP":
                        matched_kw = "EDGE_LOOP"
                elif (
                    char in ("M", "m")
                    and idx + 19 <= end_idx
                    and step_text[idx : idx + 19].upper() == "MANIFOLD_SOLID_BREP"
                ):
                    matched_kw = "MANIFOLD_SOLID_BREP"

                if matched_kw is not None:
                    kw_len = len(matched_kw)
                    after_kw = idx + kw_len
                    p = after_kw
                    is_right_bounded = False
                    while p < end_idx:
                        c = step_text[p]
                        if c.isspace():
                            p += 1
                            continue
                        if c == "(":
                            is_right_bounded = True
                        break
                    if is_right_bounded:
                        counts[matched_kw] += 1
                    idx = after_kw
                    continue

            idx += 1

    bbox: StepBoundingBox | None = None
    if (
        min_x is not None
        and max_x is not None
        and min_y is not None
        and max_y is not None
        and min_z is not None
        and max_z is not None
    ):
        bbox = StepBoundingBox(
            min_x=_normalize_zero(min_x),
            max_x=_normalize_zero(max_x),
            min_y=_normalize_zero(min_y),
            max_y=_normalize_zero(max_y),
            min_z=_normalize_zero(min_z),
            max_z=_normalize_zero(max_z),
            size_x=_normalize_zero(max_x - min_x),
            size_y=_normalize_zero(max_y - min_y),
            size_z=_normalize_zero(max_z - min_z),
        )

    return bbox, counts, warnings


def extract_step_bounding_box(step_text: str) -> StepBoundingBox | None:
    """Extract axis-aligned raw point coordinate envelope from a STEP text payload in native units.

    Returns:
        StepBoundingBox instance with min/max/size per axis, or None if no valid 3D points exist.
    """
    if not step_text or len(step_text) > MAX_STEP_TEXT_CHARS:
        return None

    start_idx, end_idx, errors = _extract_data_section_indices(step_text)
    if start_idx >= end_idx or len(errors) > 0:
        return None

    bbox, _, _ = _scan_data_section_in_place(step_text, start_idx, end_idx)
    return bbox


def count_step_entity_signals(step_text: str) -> StepEntitySignals:
    """Count lightweight topological entity signals and compute bounding spans in native units."""
    start_idx, end_idx, errors = _extract_data_section_indices(step_text)
    if start_idx >= end_idx or len(errors) > 0:
        return StepEntitySignals()

    bbox, counts, _ = _scan_data_section_in_place(step_text, start_idx, end_idx)

    span_x = bbox.size_x if bbox is not None else 0.0
    span_y = bbox.size_y if bbox is not None else 0.0
    span_z = bbox.size_z if bbox is not None else 0.0

    return StepEntitySignals(
        advanced_faces=counts.get("ADVANCED_FACE", 0),
        plane_surfaces=counts.get("PLANE", 0),
        cylindrical_surfaces=counts.get("CYLINDRICAL_SURFACE", 0),
        line_curves=counts.get("LINE", 0),
        edge_curves=counts.get("EDGE_CURVE", 0),
        manifold_solid_breps=counts.get("MANIFOLD_SOLID_BREP", 0),
        closed_shells=counts.get("CLOSED_SHELL", 0),
        edge_loops=counts.get("EDGE_LOOP", 0),
        span_x=span_x,
        span_y=span_y,
        span_z=span_z,
    )


def validate_step_bounding_box(
    bbox: StepBoundingBox | None,
    expected_x: float,
    expected_y: float,
    expected_z: float,
    tolerance_mm: float = 0.5,
) -> list[str]:
    """Verify that an extracted STEP bounding box matches expected dimensions within tolerance.

    Compares the sorted 3D bounding spans against sorted expected dimensions to account for
    arbitrary CAD orientation frame mappings.
    """
    errors: list[str] = []

    # Strict finite and non-negative guards
    if not (
        math.isfinite(expected_x)
        and math.isfinite(expected_y)
        and math.isfinite(expected_z)
        and math.isfinite(tolerance_mm)
    ):
        errors.append("Expected dimensions and tolerance must be finite numbers.")
        return errors

    if expected_x < 0.0 or expected_y < 0.0 or expected_z < 0.0:
        errors.append("Expected bounding dimensions must be non-negative.")
        return errors

    if tolerance_mm < 0.0:
        errors.append("tolerance_mm must be non-negative.")
        return errors

    if bbox is None or bbox.is_empty:
        errors.append("Bounding box is empty or not found in STEP payload.")
        return errors

    actual_spans = sorted([bbox.size_x, bbox.size_y, bbox.size_z])
    expected_spans = sorted([abs(expected_x), abs(expected_y), abs(expected_z)])

    for actual, expected in zip(actual_spans, expected_spans, strict=True):
        diff = abs(actual - expected)
        if diff > tolerance_mm:
            _append_diagnostic(
                errors,
                f"STEP bounding span ({actual:.3f} mm) deviates from expected ({expected:.3f} mm) "
                f"by {diff:.3f} mm (tolerance {tolerance_mm:.3f} mm).",
            )

    return errors


def validate_step_text(
    step_text: str,
    expected_dimensions: Sequence[float] | None = None,
    tolerance_mm: float = 0.5,
) -> StepValidationResult:
    """Validate ISO-10303-21 envelope structure, unit scaling, dimensional tolerance, and solid B-Rep presence."""
    errors: list[str] = []
    warnings: list[str] = []

    start_idx, end_idx, envelope_errors = _extract_data_section_indices(step_text)
    for err in envelope_errors:
        _append_diagnostic(errors, err)

    if start_idx >= end_idx or len(envelope_errors) > 0:
        bbox = None
        counts: dict[str, int] = {}
        signals = StepEntitySignals()
        solid_brep_detected = False
        unit_scale_to_mm = None
        unit_name = "unresolved"
        bbox_mm = None
    else:
        unit_scale_to_mm, unit_name, unit_warnings = _extract_length_unit_scale(step_text, start_idx, end_idx)
        for w in unit_warnings:
            _append_diagnostic(warnings, w)

        bbox, counts, scan_warnings = _scan_data_section_in_place(step_text, start_idx, end_idx)
        for w in scan_warnings:
            _append_diagnostic(warnings, w)

        bbox_mm = bbox.scale(unit_scale_to_mm) if bbox is not None and unit_scale_to_mm is not None else None

        span_x = bbox.size_x if bbox is not None else 0.0
        span_y = bbox.size_y if bbox is not None else 0.0
        span_z = bbox.size_z if bbox is not None else 0.0

        signals = StepEntitySignals(
            advanced_faces=counts.get("ADVANCED_FACE", 0),
            plane_surfaces=counts.get("PLANE", 0),
            cylindrical_surfaces=counts.get("CYLINDRICAL_SURFACE", 0),
            line_curves=counts.get("LINE", 0),
            edge_curves=counts.get("EDGE_CURVE", 0),
            manifold_solid_breps=counts.get("MANIFOLD_SOLID_BREP", 0),
            closed_shells=counts.get("CLOSED_SHELL", 0),
            edge_loops=counts.get("EDGE_LOOP", 0),
            span_x=span_x,
            span_y=span_y,
            span_z=span_z,
        )
        solid_brep_detected = counts.get("MANIFOLD_SOLID_BREP", 0) > 0

    if not solid_brep_detected:
        _append_diagnostic(warnings, "no MANIFOLD_SOLID_BREP entity detected in DATA section")

    if counts.get("ADVANCED_FACE", 0) == 0:
        _append_diagnostic(warnings, "no ADVANCED_FACE entities detected in DATA section")

    # If expected dimensions are provided, validate dimensions & require resolved unit scale
    if expected_dimensions is not None:
        if len(expected_dimensions) != 3:
            _append_diagnostic(
                errors,
                "expected_dimensions must contain exactly three values (x, y, z).",
            )
        elif unit_scale_to_mm is None:
            if unit_name == "ambiguous":
                _append_diagnostic(
                    errors,
                    "AMBIGUOUS_STEP_UNIT: Multiple conflicting length unit scales detected in STEP file.",
                )
            else:
                _append_diagnostic(
                    errors,
                    "UNRESOLVED_STEP_UNIT: Cannot verify dimensions without a verified length unit scale.",
                )
        else:
            dim_errors = validate_step_bounding_box(
                bbox_mm,
                expected_dimensions[0],
                expected_dimensions[1],
                expected_dimensions[2],
                tolerance_mm=tolerance_mm,
            )
            for err in dim_errors:
                _append_diagnostic(errors, err)

    is_valid = len(errors) == 0 and solid_brep_detected

    return StepValidationResult(
        is_valid=is_valid,
        solid_brep_detected=solid_brep_detected,
        bounding_box=bbox,
        unit_scale_to_mm=unit_scale_to_mm,
        unit_name=unit_name,
        bounding_box_mm=bbox_mm,
        entity_counts=counts,
        warnings=warnings,
        errors=errors,
        signals=signals,
    )


def check_rectangular_block_step(step_text: str) -> StepSanityResult:
    """Verify canonical smoke signal for a simple rectangular solid block."""
    signals = count_step_entity_signals(step_text)
    errors: list[str] = []

    if signals.manifold_solid_breps < 1:
        errors.append("expected at least one MANIFOLD_SOLID_BREP entity for rectangular_block smoke signal")
    if signals.advanced_faces < 6:
        errors.append("expected at least 6 ADVANCED_FACE entities for rectangular_block smoke signal")
    if signals.plane_surfaces < 6:
        errors.append("expected at least 6 PLANE surface entities for rectangular_block smoke signal")
    if signals.cylindrical_surfaces != 0:
        errors.append("expected 0 CYLINDRICAL_SURFACE entities for rectangular_block smoke signal")

    return StepSanityResult(
        kind="rectangular_block",
        passed=len(errors) == 0,
        signals=signals,
        errors=errors,
    )


def check_centered_plate_through_hole_step(step_text: str) -> StepSanityResult:
    """Verify canonical smoke signal for a rectangular plate with one center through-hole."""
    signals = count_step_entity_signals(step_text)
    errors: list[str] = []

    if signals.manifold_solid_breps != 1:
        errors.append("expected exactly one MANIFOLD_SOLID_BREP entity for centered_plate_through_hole smoke signal")
    if signals.advanced_faces < 7:
        errors.append("expected at least 7 ADVANCED_FACE entities for centered_plate_through_hole smoke signal")
    if signals.plane_surfaces < 6:
        errors.append("expected at least 6 PLANE surface entities for centered_plate_through_hole smoke signal")
    if signals.cylindrical_surfaces != 1:
        errors.append("expected exactly 1 CYLINDRICAL_SURFACE entity for centered_plate_through_hole smoke signal")
    if signals.line_curves < 8:
        errors.append("expected at least 8 LINE curve entities for centered_plate_through_hole smoke signal")
    if signals.edge_curves < 10:
        errors.append("expected at least 10 EDGE_CURVE entities for centered_plate_through_hole smoke signal")

    return StepSanityResult(
        kind="centered_plate_through_hole",
        passed=len(errors) == 0,
        signals=signals,
        errors=errors,
    )


def check_centered_plate_blind_hole_step(step_text: str) -> StepSanityResult:
    """Verify canonical smoke signal for a rectangular plate with one blind hole."""
    signals = count_step_entity_signals(step_text)
    errors: list[str] = []

    if signals.manifold_solid_breps < 1:
        errors.append("expected at least one MANIFOLD_SOLID_BREP entity for centered_plate_blind_hole smoke signal")
    if signals.advanced_faces < 8:
        errors.append("expected at least 8 ADVANCED_FACE entities for centered_plate_blind_hole smoke signal")
    if signals.plane_surfaces < 7:
        errors.append("expected at least 7 PLANE surface entities for centered_plate_blind_hole smoke signal")
    if signals.cylindrical_surfaces != 1:
        errors.append("expected exactly 1 CYLINDRICAL_SURFACE entity for centered_plate_blind_hole smoke signal")
    if signals.line_curves < 12:
        errors.append("expected at least 12 LINE curve entities for centered_plate_blind_hole smoke signal")
    if signals.edge_curves < 14:
        errors.append("expected at least 14 EDGE_CURVE entities for centered_plate_blind_hole smoke signal")

    return StepSanityResult(
        kind="centered_plate_blind_hole",
        passed=len(errors) == 0,
        signals=signals,
        errors=errors,
    )


def check_two_hole_plate_through_holes_step(step_text: str) -> StepSanityResult:
    """Verify canonical smoke signal for a plate with two circular through-holes."""
    signals = count_step_entity_signals(step_text)
    errors: list[str] = []

    if signals.manifold_solid_breps < 1:
        errors.append("expected at least one MANIFOLD_SOLID_BREP entity for two_hole_plate_through_holes smoke signal")
    if signals.advanced_faces < 8:
        errors.append("expected at least 8 ADVANCED_FACE entities for two_hole_plate_through_holes smoke signal")
    if signals.plane_surfaces < 6:
        errors.append("expected at least 6 PLANE surface entities for two_hole_plate_through_holes smoke signal")
    if signals.cylindrical_surfaces != 2:
        errors.append("expected exactly 2 CYLINDRICAL_SURFACE entities for two_hole_plate_through_holes smoke signal")
    if signals.line_curves < 8:
        errors.append("expected at least 8 LINE curve entities for two_hole_plate_through_holes smoke signal")
    if signals.edge_curves < 16:
        errors.append("expected at least 16 EDGE_CURVE entities for two_hole_plate_through_holes smoke signal")

    return StepSanityResult(
        kind="two_hole_plate_through_holes",
        passed=len(errors) == 0,
        signals=signals,
        errors=errors,
    )


def check_rectangular_plate_through_cutout_step(step_text: str) -> StepSanityResult:
    """Verify canonical smoke signal for a plate with one rectangular through-cutout."""
    signals = count_step_entity_signals(step_text)
    errors: list[str] = []

    if signals.manifold_solid_breps < 1:
        errors.append(
            "expected at least one MANIFOLD_SOLID_BREP entity for rectangular_plate_through_cutout smoke signal"
        )
    if signals.advanced_faces < 10:
        errors.append("expected at least 10 ADVANCED_FACE entities for rectangular_plate_through_cutout smoke signal")
    if signals.plane_surfaces < 10:
        errors.append("expected at least 10 PLANE surface entities for rectangular_plate_through_cutout smoke signal")
    if signals.cylindrical_surfaces != 0:
        errors.append("expected 0 CYLINDRICAL_SURFACE entities for rectangular_plate_through_cutout smoke signal")
    if signals.line_curves < 16:
        errors.append("expected at least 16 LINE curve entities for rectangular_plate_through_cutout smoke signal")
    if signals.edge_curves < 16:
        errors.append("expected at least 16 EDGE_CURVE entities for rectangular_plate_through_cutout smoke signal")

    return StepSanityResult(
        kind="rectangular_plate_through_cutout",
        passed=len(errors) == 0,
        signals=signals,
        errors=errors,
    )


def check_rectangular_plate_blind_cutout_step(step_text: str) -> StepSanityResult:
    """Verify canonical smoke signal for a plate with one rectangular blind cutout."""
    signals = count_step_entity_signals(step_text)
    errors: list[str] = []

    if signals.manifold_solid_breps != 1:
        errors.append("expected exactly one MANIFOLD_SOLID_BREP entity for rectangular_plate_blind_cutout smoke signal")
    if signals.advanced_faces < 11:
        errors.append("expected at least 11 ADVANCED_FACE entities for rectangular_plate_blind_cutout smoke signal")
    if signals.plane_surfaces < 11:
        errors.append("expected at least 11 PLANE surface entities for rectangular_plate_blind_cutout smoke signal")
    if signals.cylindrical_surfaces != 0:
        errors.append("expected 0 CYLINDRICAL_SURFACE entities for rectangular_plate_blind_cutout smoke signal")
    if signals.line_curves < 24:
        errors.append("expected at least 24 LINE curve entities for rectangular_plate_blind_cutout smoke signal")
    if signals.edge_curves < 24:
        errors.append("expected at least 24 EDGE_CURVE entities for rectangular_plate_blind_cutout smoke signal")

    return StepSanityResult(
        kind="rectangular_plate_blind_cutout",
        passed=len(errors) == 0,
        signals=signals,
        errors=errors,
    )


def check_slot_plate_through_cutout_step(step_text: str) -> StepSanityResult:
    """Verify canonical smoke signal for a plate with one slot through-cutout."""
    signals = count_step_entity_signals(step_text)
    errors: list[str] = []

    if signals.manifold_solid_breps < 1:
        errors.append("expected at least one MANIFOLD_SOLID_BREP entity for slot_plate_through_cutout smoke signal")
    if signals.advanced_faces < 10:
        errors.append("expected at least 10 ADVANCED_FACE entities for slot_plate_through_cutout smoke signal")
    if signals.plane_surfaces < 6:
        errors.append("expected at least 6 PLANE surface entities for slot_plate_through_cutout smoke signal")
    if signals.cylindrical_surfaces != 2:
        errors.append("expected exactly 2 CYLINDRICAL_SURFACE entities for slot_plate_through_cutout smoke signal")
    if signals.line_curves < 12:
        errors.append("expected at least 12 LINE curve entities for slot_plate_through_cutout smoke signal")
    if signals.edge_curves < 16:
        errors.append("expected at least 16 EDGE_CURVE entities for slot_plate_through_cutout smoke signal")

    return StepSanityResult(
        kind="slot_plate_through_cutout",
        passed=len(errors) == 0,
        signals=signals,
        errors=errors,
    )


def check_slot_plate_blind_cutout_step(step_text: str) -> StepSanityResult:
    """Verify canonical smoke signal for a plate with one slot blind cutout."""
    signals = count_step_entity_signals(step_text)
    errors: list[str] = []

    if signals.manifold_solid_breps != 1:
        errors.append("expected exactly one MANIFOLD_SOLID_BREP entity for slot_plate_blind_cutout smoke signal")
    if signals.advanced_faces < 11:
        errors.append("expected at least 11 ADVANCED_FACE entities for slot_plate_blind_cutout smoke signal")
    if signals.plane_surfaces < 9:
        errors.append("expected at least 9 PLANE surface entities for slot_plate_blind_cutout smoke signal")
    if signals.cylindrical_surfaces != 2:
        errors.append("expected exactly 2 CYLINDRICAL_SURFACE entities for slot_plate_blind_cutout smoke signal")
    if signals.line_curves < 20:
        errors.append("expected at least 20 LINE curve entities for slot_plate_blind_cutout smoke signal")
    if signals.edge_curves < 24:
        errors.append("expected at least 24 EDGE_CURVE entities for slot_plate_blind_cutout smoke signal")

    return StepSanityResult(
        kind="slot_plate_blind_cutout",
        passed=len(errors) == 0,
        signals=signals,
        errors=errors,
    )


def check_side_face_plate_through_hole_step(step_text: str) -> StepSanityResult:
    """Verify canonical smoke signal for a side-face plate through-hole."""
    signals = count_step_entity_signals(step_text)
    errors: list[str] = []

    if signals.manifold_solid_breps < 1:
        errors.append("expected at least one MANIFOLD_SOLID_BREP entity for side_face_plate_through_hole smoke signal")
    if signals.advanced_faces < 4:
        errors.append("expected at least 4 ADVANCED_FACE entities for side_face_plate_through_hole smoke signal")
    if signals.plane_surfaces < 3:
        errors.append("expected at least 3 PLANE surface entities for side_face_plate_through_hole smoke signal")
    if signals.cylindrical_surfaces != 1:
        errors.append("expected exactly 1 CYLINDRICAL_SURFACE entity for side_face_plate_through_hole smoke signal")
    if signals.line_curves < 4:
        errors.append("expected at least 4 LINE curve entities for side_face_plate_through_hole smoke signal")
    if signals.edge_curves < 6:
        errors.append("expected at least 6 EDGE_CURVE entities for side_face_plate_through_hole smoke signal")

    return StepSanityResult(
        kind="side_face_plate_through_hole",
        passed=len(errors) == 0,
        signals=signals,
        errors=errors,
    )


def check_rectangular_plate_extruded_pad_step(step_text: str) -> StepSanityResult:
    """Verify canonical smoke signal for a rectangular plate with an extruded pad."""
    signals = count_step_entity_signals(step_text)
    errors: list[str] = []

    if signals.manifold_solid_breps != 1:
        errors.append("expected exactly one MANIFOLD_SOLID_BREP entity for rectangular_plate_extruded_pad smoke signal")
    if signals.advanced_faces < 11:
        errors.append("expected at least 11 ADVANCED_FACE entities for rectangular_plate_extruded_pad smoke signal")
    if signals.plane_surfaces < 11:
        errors.append("expected at least 11 PLANE surface entities for rectangular_plate_extruded_pad smoke signal")
    if signals.cylindrical_surfaces != 0:
        errors.append("expected 0 CYLINDRICAL_SURFACE entities for rectangular_plate_extruded_pad smoke signal")
    if signals.line_curves < 24:
        errors.append("expected at least 24 LINE curve entities for rectangular_plate_extruded_pad smoke signal")
    if signals.edge_curves < 24:
        errors.append("expected at least 24 EDGE_CURVE entities for rectangular_plate_extruded_pad smoke signal")

    return StepSanityResult(
        kind="rectangular_plate_extruded_pad",
        passed=len(errors) == 0,
        signals=signals,
        errors=errors,
    )


def check_side_face_plate_rectangular_cutout_step(step_text: str) -> StepSanityResult:
    """Verify canonical smoke signal for a side-face rectangular cutout."""
    signals = count_step_entity_signals(step_text)
    errors: list[str] = []

    if signals.manifold_solid_breps < 1:
        errors.append(
            "expected at least one MANIFOLD_SOLID_BREP entity for side_face_plate_rectangular_cutout smoke signal"
        )
    if signals.advanced_faces < 12:
        errors.append("expected at least 12 ADVANCED_FACE entities for side_face_plate_rectangular_cutout smoke signal")
    if signals.plane_surfaces < 12:
        errors.append("expected at least 12 PLANE surface entities for side_face_plate_rectangular_cutout smoke signal")
    if signals.cylindrical_surfaces != 0:
        errors.append("expected 0 CYLINDRICAL_SURFACE entities for side_face_plate_rectangular_cutout smoke signal")
    if signals.line_curves < 24:
        errors.append("expected at least 24 LINE curve entities for side_face_plate_rectangular_cutout smoke signal")
    if signals.edge_curves < 24:
        errors.append("expected at least 24 EDGE_CURVE entities for side_face_plate_rectangular_cutout smoke signal")

    return StepSanityResult(
        kind="side_face_plate_rectangular_cutout",
        passed=len(errors) == 0,
        signals=signals,
        errors=errors,
    )


def check_side_face_plate_slot_cutout_step(step_text: str) -> StepSanityResult:
    """Verify canonical smoke signal for a side-face slot cutout."""
    signals = count_step_entity_signals(step_text)
    errors: list[str] = []

    if signals.manifold_solid_breps < 1:
        errors.append("expected at least one MANIFOLD_SOLID_BREP entity for side_face_plate_slot_cutout smoke signal")
    if signals.advanced_faces < 6:
        errors.append("expected at least 6 ADVANCED_FACE entities for side_face_plate_slot_cutout smoke signal")
    if signals.plane_surfaces < 5:
        errors.append("expected at least 5 PLANE surface entities for side_face_plate_slot_cutout smoke signal")
    if signals.cylindrical_surfaces != 1:
        errors.append("expected exactly 1 CYLINDRICAL_SURFACE entity for side_face_plate_slot_cutout smoke signal")
    if signals.line_curves < 10:
        errors.append("expected at least 10 LINE curve entities for side_face_plate_slot_cutout smoke signal")
    if signals.edge_curves < 12:
        errors.append("expected at least 12 EDGE_CURVE entities for side_face_plate_slot_cutout smoke signal")

    return StepSanityResult(
        kind="side_face_plate_slot_cutout",
        passed=len(errors) == 0,
        signals=signals,
        errors=errors,
    )


def check_mixed_plate_rectangular_cutout_two_cylinders_step(step_text: str) -> StepSanityResult:
    """Verify canonical smoke signal for a mixed plate with rectangular cutout and two cylinders."""
    signals = count_step_entity_signals(step_text)
    errors: list[str] = []

    if signals.manifold_solid_breps < 1:
        errors.append(
            "expected at least one MANIFOLD_SOLID_BREP entity for mixed_plate_rectangular_cutout_two_cylinders smoke signal"
        )
    if signals.advanced_faces < 12:
        errors.append(
            "expected at least 12 ADVANCED_FACE entities for mixed_plate_rectangular_cutout_two_cylinders smoke signal"
        )
    if signals.plane_surfaces < 10:
        errors.append(
            "expected at least 10 PLANE surface entities for mixed_plate_rectangular_cutout_two_cylinders smoke signal"
        )
    if signals.cylindrical_surfaces != 2:
        errors.append(
            "expected exactly 2 CYLINDRICAL_SURFACE entities for mixed_plate_rectangular_cutout_two_cylinders smoke signal"
        )
    if signals.line_curves < 16:
        errors.append(
            "expected at least 16 LINE curve entities for mixed_plate_rectangular_cutout_two_cylinders smoke signal"
        )
    if signals.edge_curves < 20:
        errors.append(
            "expected at least 20 EDGE_CURVE entities for mixed_plate_rectangular_cutout_two_cylinders smoke signal"
        )

    return StepSanityResult(
        kind="mixed_plate_rectangular_cutout_two_cylinders",
        passed=len(errors) == 0,
        signals=signals,
        errors=errors,
    )


def check_mixed_plate_three_cylinders_step(step_text: str) -> StepSanityResult:
    """Verify canonical smoke signal for a mixed plate with three cylinders."""
    signals = count_step_entity_signals(step_text)
    errors: list[str] = []

    if signals.manifold_solid_breps < 1:
        errors.append("expected at least one MANIFOLD_SOLID_BREP entity for mixed_plate_three_cylinders smoke signal")
    if signals.advanced_faces < 11:
        errors.append("expected at least 11 ADVANCED_FACE entities for mixed_plate_three_cylinders smoke signal")
    if signals.plane_surfaces < 6:
        errors.append("expected at least 6 PLANE surface entities for mixed_plate_three_cylinders smoke signal")
    if signals.cylindrical_surfaces != 3:
        errors.append("expected exactly 3 CYLINDRICAL_SURFACE entities for mixed_plate_three_cylinders smoke signal")
    if signals.line_curves < 16:
        errors.append("expected at least 16 LINE curve entities for mixed_plate_three_cylinders smoke signal")
    if signals.edge_curves < 20:
        errors.append("expected at least 20 EDGE_CURVE entities for mixed_plate_three_cylinders smoke signal")

    return StepSanityResult(
        kind="mixed_plate_three_cylinders",
        passed=len(errors) == 0,
        signals=signals,
        errors=errors,
    )


__all__ = [
    "MAX_COORDINATE_TOKEN_LEN",
    "MAX_COORDINATE_TUPLE_CHARS",
    "MAX_DIAGNOSTICS",
    "MAX_STEP_TEXT_CHARS",
    "MAX_UNIT_DECL_CHARS",
    "StepBoundingBox",
    "StepEntitySignals",
    "StepSanityKind",
    "StepSanityResult",
    "StepValidationResult",
    "check_centered_plate_blind_hole_step",
    "check_centered_plate_through_hole_step",
    "check_mixed_plate_rectangular_cutout_two_cylinders_step",
    "check_mixed_plate_three_cylinders_step",
    "check_rectangular_block_step",
    "check_rectangular_plate_blind_cutout_step",
    "check_rectangular_plate_extruded_pad_step",
    "check_rectangular_plate_through_cutout_step",
    "check_side_face_plate_rectangular_cutout_step",
    "check_side_face_plate_slot_cutout_step",
    "check_side_face_plate_through_hole_step",
    "check_slot_plate_blind_cutout_step",
    "check_slot_plate_through_cutout_step",
    "check_two_hole_plate_through_holes_step",
    "count_step_entity_signals",
    "extract_step_bounding_box",
    "validate_step_bounding_box",
    "validate_step_text",
]
