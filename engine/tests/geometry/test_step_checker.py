"""Tests for pure domain linear STEP Part 21 coordinate scanner and smoke checker.

Invariants Verified:
    - Zero-I/O Purity (NFR-1): All tests operate strictly on in-memory string payloads.
    - Mathematical Precision (NFR-2): IEEE-754 coordinate intervals and span math.
    - Security & DoS Bounds (CWE-400): Character limits, coordinate token limits.
    - Topological Smoke Contracts: Accurate detection and verification for 14 canonical shapes.
"""

from __future__ import annotations

import math
import time
import tracemalloc
from collections.abc import Callable

import pytest

from geometry import (
    MAX_COORDINATE_TOKEN_LEN,
    MAX_DIAGNOSTICS,
    MAX_STEP_TEXT_CHARS,
    StepBoundingBox,
    StepEntitySignals,
    StepSanityResult,
    StepValidationResult,
    check_centered_plate_blind_hole_step,
    check_centered_plate_through_hole_step,
    check_mixed_plate_rectangular_cutout_two_cylinders_step,
    check_mixed_plate_three_cylinders_step,
    check_rectangular_block_step,
    check_rectangular_plate_blind_cutout_step,
    check_rectangular_plate_extruded_pad_step,
    check_rectangular_plate_through_cutout_step,
    check_side_face_plate_rectangular_cutout_step,
    check_side_face_plate_slot_cutout_step,
    check_side_face_plate_through_hole_step,
    check_slot_plate_blind_cutout_step,
    check_slot_plate_through_cutout_step,
    check_two_hole_plate_through_holes_step,
    count_step_entity_signals,
    extract_step_bounding_box,
    validate_step_bounding_box,
    validate_step_text,
)


def _build_step_payload(
    entities: list[str],
    *,
    header_extra: str = "",
    include_header_open: bool = True,
    include_header_sec: bool = True,
    include_header_endsec: bool = True,
    include_data_sec: bool = True,
    include_data_endsec: bool = True,
    include_footer_close: bool = True,
) -> str:
    """Helper to synthesize valid or intentionally malformed ISO-10303-21 payloads."""
    parts: list[str] = []
    if include_header_open:
        parts.append("ISO-10303-21;")
    if include_header_sec:
        parts.append("HEADER;")
        parts.append("FILE_DESCRIPTION(('Synthetic Unit Test Fixture'), '2;1');")
        parts.append("FILE_NAME('fixture.step', '2026-08-24', ('Unit Test'), ('CAD Copilot'), '', '', '');")
        parts.append("FILE_SCHEMA(('CONFIG_CONTROL_DESIGN'));")
        if header_extra:
            parts.append(header_extra)
    if include_header_endsec:
        parts.append("ENDSEC;")
    if include_data_sec:
        parts.append("DATA;")
        parts.extend(entities)
    if include_data_endsec:
        parts.append("ENDSEC;")
    if include_footer_close:
        parts.append("END-ISO-10303-21;")
    return "\n".join(parts)


def _build_shape_fixture(
    *,
    solid_breps: int = 1,
    advanced_faces: int = 6,
    planes: int = 6,
    cylinders: int = 0,
    lines: int = 0,
    edges: int = 0,
    points: list[tuple[float, float, float]] | None = None,
) -> str:
    """Build synthetic structurally representative Part 21 fixture text for smoke checking."""
    entities: list[str] = []
    eid = 1

    for _ in range(solid_breps):
        entities.append(f"#{eid} = MANIFOLD_SOLID_BREP('Solid_{eid}', #{eid + 100});")
        eid += 1

    for i in range(advanced_faces):
        entities.append(f"#{eid} = ADVANCED_FACE('Face_{i}', (#{eid + 200}), #{eid + 300}, .T.);")
        eid += 1

    for i in range(planes):
        entities.append(f"#{eid} = PLANE('Plane_{i}', #{eid + 400});")
        eid += 1

    for i in range(cylinders):
        entities.append(f"#{eid} = CYLINDRICAL_SURFACE('Cylinder_{i}', #{eid + 500}, 5.0);")
        eid += 1

    for i in range(lines):
        entities.append(f"#{eid} = LINE('Line_{i}', #{eid + 600}, #{eid + 700});")
        eid += 1

    for i in range(edges):
        entities.append(f"#{eid} = EDGE_CURVE('Edge_{i}', #{eid + 800}, #{eid + 900}, #{eid + 1000}, .T.);")
        eid += 1

    if points is not None:
        for x, y, z in points:
            entities.append(f"#{eid} = CARTESIAN_POINT('', ({x}, {y}, {z}));")
            eid += 1
    else:
        # Default bounding cube points (0,0,0) to (100, 50, 20)
        default_pts = [
            (0.0, 0.0, 0.0),
            (100.0, 0.0, 0.0),
            (100.0, 50.0, 0.0),
            (0.0, 50.0, 0.0),
            (0.0, 0.0, 20.0),
            (100.0, 0.0, 20.0),
            (100.0, 50.0, 20.0),
            (0.0, 50.0, 20.0),
        ]
        for x, y, z in default_pts:
            entities.append(f"#{eid} = CARTESIAN_POINT('', ({x}, {y}, {z}));")
            eid += 1

    return _build_step_payload(entities)


# -----------------------------------------------------------------------------
# 1. Bounding Box & Coordinate Extraction Tests
# -----------------------------------------------------------------------------


def test_extract_step_bounding_box_standard_coordinates() -> None:
    """Verify standard Cartesian point coordinate extraction and interval bounds."""
    step_text = _build_step_payload(
        [
            "#10 = CARTESIAN_POINT('Origin', (0.0, 0.0, 0.0));",
            "#11 = CARTESIAN_POINT('Corner1', (100.0, 50.0, 25.0));",
            "#12 = CARTESIAN_POINT('Corner2', (-20.0, 10.0, -5.0));",
        ]
    )
    bbox = extract_step_bounding_box(step_text)
    assert bbox is not None
    assert bbox.min_x == -20.0
    assert bbox.max_x == 100.0
    assert bbox.min_y == 0.0
    assert bbox.max_y == 50.0
    assert bbox.min_z == -5.0
    assert bbox.max_z == 25.0
    assert bbox.size_x == 120.0
    assert bbox.size_y == 50.0
    assert bbox.size_z == 30.0
    assert bbox.span_tuple() == (120.0, 50.0, 30.0)
    assert not bbox.is_empty


def test_extract_step_bounding_box_single_point_degenerate() -> None:
    """Verify single-point degenerate bounding box has zero extents."""
    step_text = _build_step_payload(
        [
            "#10 = CARTESIAN_POINT('P1', (15.5, -42.0, 8.0));",
        ]
    )
    bbox = extract_step_bounding_box(step_text)
    assert bbox is not None
    assert bbox.min_x == 15.5
    assert bbox.max_x == 15.5
    assert bbox.min_y == -42.0
    assert bbox.max_y == -42.0
    assert bbox.min_z == 8.0
    assert bbox.max_z == 8.0
    assert bbox.size_x == 0.0
    assert bbox.size_y == 0.0
    assert bbox.size_z == 0.0
    assert bbox.is_empty


def test_extract_step_bounding_box_empty_payload() -> None:
    """Verify empty text or text with no Cartesian points yields None."""
    assert extract_step_bounding_box("") is None
    no_pts = _build_step_payload(["#10 = MANIFOLD_SOLID_BREP('Solid', #20);"])
    assert extract_step_bounding_box(no_pts) is None


def test_extract_step_bounding_box_2d_points_ignored() -> None:
    """Verify 2D directional vectors or UV coordinates (2-tuples) are ignored."""
    step_text = _build_step_payload(
        [
            "#10 = CARTESIAN_POINT('2D_UV', (999.0, 888.0));",
            "#11 = CARTESIAN_POINT('3D_P1', (10.0, 20.0, 30.0));",
            "#12 = CARTESIAN_POINT('3D_P2', (40.0, 50.0, 60.0));",
        ]
    )
    bbox = extract_step_bounding_box(step_text)
    assert bbox is not None
    assert bbox.min_x == 10.0
    assert bbox.max_x == 40.0
    assert bbox.min_y == 20.0
    assert bbox.max_y == 50.0
    assert bbox.min_z == 30.0
    assert bbox.max_z == 60.0


def test_extract_step_bounding_box_scientific_notation_and_floats() -> None:
    """Verify parsing of signed scientific notation, leading/trailing dots, and signs."""
    step_text = _build_step_payload(
        [
            "#10 = CARTESIAN_POINT('', (1.25E-02, -4.5E+01, +3.0E+00));",
            "#11 = CARTESIAN_POINT('', (.5, 50., -.25));",
        ]
    )
    bbox = extract_step_bounding_box(step_text)
    assert bbox is not None
    assert pytest.approx(bbox.min_x, abs=1e-6) == 0.0125
    assert pytest.approx(bbox.max_x, abs=1e-6) == 0.5
    assert pytest.approx(bbox.min_y, abs=1e-6) == -45.0
    assert pytest.approx(bbox.max_y, abs=1e-6) == 50.0
    assert pytest.approx(bbox.min_z, abs=1e-6) == -0.25
    assert pytest.approx(bbox.max_z, abs=1e-6) == 3.0


def test_extract_step_bounding_box_negative_zero_normalization() -> None:
    """Verify IEEE-754 signed negative zero -0.0 is normalized to 0.0."""
    step_text = _build_step_payload(
        [
            "#10 = CARTESIAN_POINT('', (-0.0, -0.0, -0.0));",
            "#11 = CARTESIAN_POINT('', (0.0, 0.0, 0.0));",
        ]
    )
    bbox = extract_step_bounding_box(step_text)
    assert bbox is not None
    assert bbox.min_x == 0.0
    assert bbox.max_x == 0.0
    assert bbox.min_y == 0.0
    assert bbox.max_y == 0.0
    assert bbox.min_z == 0.0
    assert bbox.max_z == 0.0
    assert bbox.size_x == 0.0
    assert bbox.size_y == 0.0
    assert bbox.size_z == 0.0


def test_step_bounding_box_to_dict_serialization() -> None:
    """Verify StepBoundingBox serialization to JSON-compatible dictionary."""
    bbox = StepBoundingBox(
        min_x=0.0,
        max_x=10.0,
        min_y=0.0,
        max_y=20.0,
        min_z=0.0,
        max_z=30.0,
        size_x=10.0,
        size_y=20.0,
        size_z=30.0,
    )
    d = bbox.to_dict()
    assert d == {
        "min_x": 0.0,
        "max_x": 10.0,
        "min_y": 0.0,
        "max_y": 20.0,
        "min_z": 0.0,
        "max_z": 30.0,
        "size_x": 10.0,
        "size_y": 20.0,
        "size_z": 30.0,
    }


# -----------------------------------------------------------------------------
# 2. Lexical & Envelope Robustness Tests
# -----------------------------------------------------------------------------


def test_step_comments_stripped_outside_strings() -> None:
    """Verify block comments /* ... */ outside strings are stripped without phantom points."""
    step_text = _build_step_payload(
        [
            "/* #999 = CARTESIAN_POINT('Phantom', (5000.0, 5000.0, 5000.0)); */",
            "#10 = CARTESIAN_POINT('Real1', (0.0, 0.0, 0.0));",
            "#11 = CARTESIAN_POINT('Real2', (100.0, 50.0, 20.0));",
        ]
    )
    bbox = extract_step_bounding_box(step_text)
    assert bbox is not None
    assert bbox.max_x == 100.0
    assert bbox.max_y == 50.0
    assert bbox.max_z == 20.0


def test_step_comments_inside_string_literals_preserved() -> None:
    """Verify comment markers /* ... */ inside string literals are preserved as literal text."""
    step_text = _build_step_payload(
        [
            "#10 = CARTESIAN_POINT('Point with /* literal comment */ label', (10.0, 20.0, 30.0));",
        ]
    )
    bbox = extract_step_bounding_box(step_text)
    assert bbox is not None
    assert bbox.min_x == 10.0
    assert bbox.min_y == 20.0
    assert bbox.min_z == 30.0


def test_step_semicolon_inside_string_label_handled() -> None:
    """Verify entity labels containing semicolons do not terminate parameter parsing."""
    step_text = _build_step_payload(
        [
            "#10 = CARTESIAN_POINT('Point; Sector 7; Zone B', (15.0, 25.0, 35.0));",
            "#11 = CARTESIAN_POINT('Normal', (45.0, 55.0, 65.0));",
        ]
    )
    bbox = extract_step_bounding_box(step_text)
    assert bbox is not None
    assert bbox.min_x == 15.0
    assert bbox.max_x == 45.0
    assert bbox.min_y == 25.0
    assert bbox.max_y == 55.0
    assert bbox.min_z == 35.0
    assert bbox.max_z == 65.0


def test_step_escaped_single_quotes_in_labels() -> None:
    """Verify single quotes escaped with '' in labels are stepped over accurately."""
    step_text = _build_step_payload(
        [
            "#10 = CARTESIAN_POINT('User''s Point (Datum)', (12.0, 24.0, 36.0));",
        ]
    )
    bbox = extract_step_bounding_box(step_text)
    assert bbox is not None
    assert bbox.min_x == 12.0
    assert bbox.min_y == 24.0
    assert bbox.min_z == 36.0


def test_step_entity_counting_ignores_keywords_in_strings() -> None:
    """Verify entity keywords occurring inside string metadata do not increment entity counters."""
    step_text = _build_step_payload(
        [
            "#10 = PRODUCT_DESCRIPTION('ADVANCED_FACE (Hull)', 'PLANE (Upper)', $);",
            "#20 = ADVANCED_FACE('', (#30), #40, .T.);",
            "#50 = MANIFOLD_SOLID_BREP('Solid', #60);",
        ]
    )
    signals = count_step_entity_signals(step_text)
    assert signals.advanced_faces == 1
    assert signals.plane_surfaces == 0
    assert signals.manifold_solid_breps == 1


# -----------------------------------------------------------------------------
# 3. Structural Validation Tests (validate_step_text)
# -----------------------------------------------------------------------------


def test_validate_step_text_valid_payload() -> None:
    """Verify complete valid STEP Part 21 text validates successfully."""
    step_text = _build_shape_fixture(solid_breps=1, advanced_faces=6, planes=6)
    res: StepValidationResult = validate_step_text(step_text)
    assert res.is_valid
    assert res.solid_brep_detected
    assert res.bounding_box is not None
    assert len(res.errors) == 0
    assert res.entity_counts["MANIFOLD_SOLID_BREP"] == 1
    assert res.entity_counts["ADVANCED_FACE"] == 6
    assert res.entity_counts["PLANE"] == 6

    d = res.to_dict()
    assert d["is_valid"] is True
    assert d["solid_brep_detected"] is True
    assert isinstance(d["bounding_box"], dict)
    assert isinstance(d["entity_counts"], dict)


def test_validate_step_text_missing_header_marker() -> None:
    """Verify detection of missing ISO-10303-21 opening marker."""
    step_text = _build_step_payload(
        ["#10 = MANIFOLD_SOLID_BREP('Solid', #20);"],
        include_header_open=False,
    )
    res = validate_step_text(step_text)
    assert not res.is_valid
    assert any("missing ISO-10303-21;" in err for err in res.errors)


def test_validate_step_text_missing_header_section() -> None:
    """Verify detection of missing HEADER; declaration."""
    step_text = _build_step_payload(
        ["#10 = MANIFOLD_SOLID_BREP('Solid', #20);"],
        include_header_sec=False,
    )
    res = validate_step_text(step_text)
    assert not res.is_valid
    assert any("missing HEADER;" in err for err in res.errors)


def test_validate_step_text_missing_data_section() -> None:
    """Verify detection of missing DATA; section declaration."""
    step_text = _build_step_payload(
        ["#10 = MANIFOLD_SOLID_BREP('Solid', #20);"],
        include_data_sec=False,
    )
    res = validate_step_text(step_text)
    assert not res.is_valid
    assert any("missing DATA;" in err for err in res.errors)


def test_validate_step_text_missing_footer_close() -> None:
    """Verify detection of missing END-ISO-10303-21; closing marker."""
    step_text = _build_step_payload(
        ["#10 = MANIFOLD_SOLID_BREP('Solid', #20);"],
        include_footer_close=False,
    )
    res = validate_step_text(step_text)
    assert not res.is_valid
    assert any("missing END-ISO-10303-21;" in err for err in res.errors)


def test_validate_step_text_no_solid_brep_detected() -> None:
    """Verify warning and invalid status when no MANIFOLD_SOLID_BREP is present."""
    step_text = _build_shape_fixture(solid_breps=0, advanced_faces=6, planes=6)
    res = validate_step_text(step_text)
    assert not res.is_valid
    assert not res.solid_brep_detected
    assert any("no MANIFOLD_SOLID_BREP" in w for w in res.warnings)


# -----------------------------------------------------------------------------
# 4. Canonical Smoke Checker Tests (All 14 Shapes)
# -----------------------------------------------------------------------------


def test_check_rectangular_block_step_pass() -> None:
    """Verify rectangular_block canonical smoke check passes with matching signals."""
    step_text = _build_shape_fixture(
        solid_breps=1,
        advanced_faces=6,
        planes=6,
        cylinders=0,
    )
    res: StepSanityResult = check_rectangular_block_step(step_text)
    assert res.passed
    assert res.kind == "rectangular_block"
    assert len(res.errors) == 0
    assert res.signals.manifold_solid_breps == 1
    assert res.signals.advanced_faces == 6
    assert res.signals.plane_surfaces == 6
    assert res.signals.cylindrical_surfaces == 0


def test_check_rectangular_block_step_fail_on_cylinders() -> None:
    """Verify rectangular_block smoke check fails if unexpected cylindrical surfaces are present."""
    step_text = _build_shape_fixture(
        solid_breps=1,
        advanced_faces=6,
        planes=6,
        cylinders=1,
    )
    res = check_rectangular_block_step(step_text)
    assert not res.passed
    assert any("expected 0 CYLINDRICAL_SURFACE" in err for err in res.errors)


def test_check_centered_plate_through_hole_step() -> None:
    """Verify centered_plate_through_hole canonical smoke check."""
    step_text = _build_shape_fixture(
        solid_breps=1,
        advanced_faces=7,
        planes=6,
        cylinders=1,
        lines=8,
        edges=10,
    )
    res = check_centered_plate_through_hole_step(step_text)
    assert res.passed
    assert res.kind == "centered_plate_through_hole"
    assert res.signals.cylindrical_surfaces == 1


def test_check_centered_plate_blind_hole_step() -> None:
    """Verify centered_plate_blind_hole canonical smoke check."""
    step_text = _build_shape_fixture(
        solid_breps=1,
        advanced_faces=8,
        planes=7,
        cylinders=1,
        lines=12,
        edges=14,
    )
    res = check_centered_plate_blind_hole_step(step_text)
    assert res.passed
    assert res.kind == "centered_plate_blind_hole"
    assert res.signals.cylindrical_surfaces == 1


def test_check_two_hole_plate_through_holes_step() -> None:
    """Verify two_hole_plate_through_holes canonical smoke check."""
    step_text = _build_shape_fixture(
        solid_breps=1,
        advanced_faces=8,
        planes=6,
        cylinders=2,
        lines=8,
        edges=16,
    )
    res = check_two_hole_plate_through_holes_step(step_text)
    assert res.passed
    assert res.kind == "two_hole_plate_through_holes"
    assert res.signals.cylindrical_surfaces == 2


def test_check_rectangular_plate_through_cutout_step() -> None:
    """Verify rectangular_plate_through_cutout canonical smoke check."""
    step_text = _build_shape_fixture(
        solid_breps=1,
        advanced_faces=10,
        planes=10,
        cylinders=0,
        lines=16,
        edges=16,
    )
    res = check_rectangular_plate_through_cutout_step(step_text)
    assert res.passed
    assert res.kind == "rectangular_plate_through_cutout"
    assert res.signals.cylindrical_surfaces == 0


def test_check_rectangular_plate_blind_cutout_step() -> None:
    """Verify rectangular_plate_blind_cutout canonical smoke check."""
    step_text = _build_shape_fixture(
        solid_breps=1,
        advanced_faces=11,
        planes=11,
        cylinders=0,
        lines=24,
        edges=24,
    )
    res = check_rectangular_plate_blind_cutout_step(step_text)
    assert res.passed
    assert res.kind == "rectangular_plate_blind_cutout"


def test_check_slot_plate_through_cutout_step() -> None:
    """Verify slot_plate_through_cutout canonical smoke check."""
    step_text = _build_shape_fixture(
        solid_breps=1,
        advanced_faces=10,
        planes=6,
        cylinders=2,
        lines=12,
        edges=16,
    )
    res = check_slot_plate_through_cutout_step(step_text)
    assert res.passed
    assert res.kind == "slot_plate_through_cutout"
    assert res.signals.cylindrical_surfaces == 2


def test_check_slot_plate_blind_cutout_step() -> None:
    """Verify slot_plate_blind_cutout canonical smoke check."""
    step_text = _build_shape_fixture(
        solid_breps=1,
        advanced_faces=11,
        planes=9,
        cylinders=2,
        lines=20,
        edges=24,
    )
    res = check_slot_plate_blind_cutout_step(step_text)
    assert res.passed
    assert res.kind == "slot_plate_blind_cutout"
    assert res.signals.cylindrical_surfaces == 2


def test_check_side_face_plate_through_hole_step() -> None:
    """Verify side_face_plate_through_hole canonical smoke check."""
    step_text = _build_shape_fixture(
        solid_breps=1,
        advanced_faces=4,
        planes=3,
        cylinders=1,
        lines=4,
        edges=6,
    )
    res = check_side_face_plate_through_hole_step(step_text)
    assert res.passed
    assert res.kind == "side_face_plate_through_hole"


def test_check_rectangular_plate_extruded_pad_step() -> None:
    """Verify rectangular_plate_extruded_pad canonical smoke check."""
    step_text = _build_shape_fixture(
        solid_breps=1,
        advanced_faces=11,
        planes=11,
        cylinders=0,
        lines=24,
        edges=24,
    )
    res = check_rectangular_plate_extruded_pad_step(step_text)
    assert res.passed
    assert res.kind == "rectangular_plate_extruded_pad"


def test_check_side_face_plate_rectangular_cutout_step() -> None:
    """Verify side_face_plate_rectangular_cutout canonical smoke check."""
    step_text = _build_shape_fixture(
        solid_breps=1,
        advanced_faces=12,
        planes=12,
        cylinders=0,
        lines=24,
        edges=24,
    )
    res = check_side_face_plate_rectangular_cutout_step(step_text)
    assert res.passed
    assert res.kind == "side_face_plate_rectangular_cutout"


def test_check_side_face_plate_slot_cutout_step() -> None:
    """Verify side_face_plate_slot_cutout canonical smoke check."""
    step_text = _build_shape_fixture(
        solid_breps=1,
        advanced_faces=6,
        planes=5,
        cylinders=1,
        lines=10,
        edges=12,
    )
    res = check_side_face_plate_slot_cutout_step(step_text)
    assert res.passed
    assert res.kind == "side_face_plate_slot_cutout"


def test_check_mixed_plate_rectangular_cutout_two_cylinders_step() -> None:
    """Verify mixed_plate_rectangular_cutout_two_cylinders canonical smoke check."""
    step_text = _build_shape_fixture(
        solid_breps=1,
        advanced_faces=12,
        planes=10,
        cylinders=2,
        lines=16,
        edges=20,
    )
    res = check_mixed_plate_rectangular_cutout_two_cylinders_step(step_text)
    assert res.passed
    assert res.kind == "mixed_plate_rectangular_cutout_two_cylinders"


def test_check_mixed_plate_three_cylinders_step() -> None:
    """Verify mixed_plate_three_cylinders canonical smoke check."""
    step_text = _build_shape_fixture(
        solid_breps=1,
        advanced_faces=11,
        planes=6,
        cylinders=3,
        lines=16,
        edges=20,
    )
    res = check_mixed_plate_three_cylinders_step(step_text)
    assert res.passed
    assert res.kind == "mixed_plate_three_cylinders"
    assert res.signals.cylindrical_surfaces == 3


def test_step_entity_signals_to_dict_serialization() -> None:
    """Verify StepEntitySignals to_dict dictionary serialization."""
    sig = StepEntitySignals(
        advanced_faces=10,
        plane_surfaces=6,
        cylindrical_surfaces=2,
        line_curves=12,
        edge_curves=16,
        manifold_solid_breps=1,
        closed_shells=1,
        edge_loops=18,
        span_x=100.0,
        span_y=50.0,
        span_z=20.0,
    )
    d = sig.to_dict()
    assert d == {
        "advanced_faces": 10,
        "plane_surfaces": 6,
        "cylindrical_surfaces": 2,
        "line_curves": 12,
        "edge_curves": 16,
        "manifold_solid_breps": 1,
        "closed_shells": 1,
        "edge_loops": 18,
        "span_x": 100.0,
        "span_y": 50.0,
        "span_z": 20.0,
    }


def test_step_sanity_result_to_dict_serialization() -> None:
    """Verify StepSanityResult to_dict dictionary serialization."""
    sig = StepEntitySignals(advanced_faces=6, plane_surfaces=6)
    res = StepSanityResult(
        kind="rectangular_block",
        passed=True,
        signals=sig,
        errors=[],
    )
    d = res.to_dict()
    assert d["kind"] == "rectangular_block"
    assert d["passed"] is True
    assert isinstance(d["signals"], dict)
    assert d["errors"] == []


# -----------------------------------------------------------------------------
# 5. Security & Resource Bounds Tests (CWE-400)
# -----------------------------------------------------------------------------


def test_max_step_text_chars_limit_enforced() -> None:
    """Verify oversized payloads exceeding MAX_STEP_TEXT_CHARS are rejected cleanly."""
    oversized = "A" * (MAX_STEP_TEXT_CHARS + 10)
    assert extract_step_bounding_box(oversized) is None
    res = validate_step_text(oversized)
    assert not res.is_valid
    assert any("exceeds maximum length limit" in err for err in res.errors)


def test_coordinate_token_length_limit_enforced() -> None:
    """Verify abnormally long coordinate numeric tokens exceeding MAX_COORDINATE_TOKEN_LEN are ignored."""
    long_token = "1" * (MAX_COORDINATE_TOKEN_LEN + 10)
    step_text = _build_step_payload(
        [
            f"#10 = CARTESIAN_POINT('', ({long_token}, 0.0, 0.0));",
            "#11 = CARTESIAN_POINT('', (10.0, 20.0, 30.0));",
        ]
    )
    bbox = extract_step_bounding_box(step_text)
    assert bbox is not None
    # Only #11 is extracted
    assert bbox.min_x == 10.0
    assert bbox.max_x == 10.0
    assert bbox.min_y == 20.0
    assert bbox.max_y == 20.0
    assert bbox.min_z == 30.0
    assert bbox.max_z == 30.0


def test_streaming_points_beyond_100k_without_truncation() -> None:
    """Verify streaming coordinate extractor processes >100,000 points without point-cap truncation."""
    # Generate 100,005 Cartesian points where the maximum extent appears at point #100,005
    entities = [f"#{i} = CARTESIAN_POINT('', (1.0, 1.0, 1.0));" for i in range(1, 100_001)]
    entities.append("#100001 = CARTESIAN_POINT('', (0.0, 0.0, 0.0));")
    entities.append("#100005 = CARTESIAN_POINT('', (750.0, 450.0, 300.0));")
    step_text = _build_step_payload(entities)

    bbox = extract_step_bounding_box(step_text)
    assert bbox is not None
    assert bbox.min_x == 0.0
    assert bbox.max_x == 750.0
    assert bbox.min_y == 0.0
    assert bbox.max_y == 450.0
    assert bbox.min_z == 0.0
    assert bbox.max_z == 300.0
    assert bbox.size_x == 750.0
    assert bbox.size_y == 450.0
    assert bbox.size_z == 300.0


@pytest.mark.parametrize(
    "checker_fn,expected_kind",
    [
        (check_rectangular_block_step, "rectangular_block"),
        (check_centered_plate_through_hole_step, "centered_plate_through_hole"),
        (check_centered_plate_blind_hole_step, "centered_plate_blind_hole"),
        (check_two_hole_plate_through_holes_step, "two_hole_plate_through_holes"),
        (check_rectangular_plate_through_cutout_step, "rectangular_plate_through_cutout"),
        (check_rectangular_plate_blind_cutout_step, "rectangular_plate_blind_cutout"),
        (check_slot_plate_through_cutout_step, "slot_plate_through_cutout"),
        (check_slot_plate_blind_cutout_step, "slot_plate_blind_cutout"),
        (check_side_face_plate_through_hole_step, "side_face_plate_through_hole"),
        (check_rectangular_plate_extruded_pad_step, "rectangular_plate_extruded_pad"),
        (check_side_face_plate_rectangular_cutout_step, "side_face_plate_rectangular_cutout"),
        (check_side_face_plate_slot_cutout_step, "side_face_plate_slot_cutout"),
        (check_mixed_plate_rectangular_cutout_two_cylinders_step, "mixed_plate_rectangular_cutout_two_cylinders"),
        (check_mixed_plate_three_cylinders_step, "mixed_plate_three_cylinders"),
    ],
)
def test_all_14_smoke_checkers_fail_on_missing_solid_brep(
    checker_fn: Callable[[str], StepSanityResult],
    expected_kind: str,
) -> None:
    """Verify all 14 shape smoke checkers reject payloads with 0 MANIFOLD_SOLID_BREPs."""
    step_text = _build_shape_fixture(solid_breps=0, advanced_faces=12, planes=12, cylinders=0)
    res = checker_fn(step_text)
    assert not res.passed
    assert res.kind == expected_kind
    assert any("MANIFOLD_SOLID_BREP" in err for err in res.errors)


@pytest.mark.parametrize(
    "checker_fn,invalid_cylinders",
    [
        (check_centered_plate_through_hole_step, 0),
        (check_centered_plate_blind_hole_step, 0),
        (check_two_hole_plate_through_holes_step, 0),
        (check_rectangular_plate_through_cutout_step, 1),
        (check_rectangular_plate_blind_cutout_step, 1),
        (check_slot_plate_through_cutout_step, 0),
        (check_slot_plate_blind_cutout_step, 0),
        (check_side_face_plate_through_hole_step, 0),
        (check_rectangular_plate_extruded_pad_step, 1),
        (check_side_face_plate_rectangular_cutout_step, 1),
        (check_side_face_plate_slot_cutout_step, 0),
        (check_mixed_plate_rectangular_cutout_two_cylinders_step, 0),
        (check_mixed_plate_three_cylinders_step, 0),
    ],
)
def test_smoke_checkers_fail_on_invalid_cylinder_count(
    checker_fn: Callable[[str], StepSanityResult],
    invalid_cylinders: int,
) -> None:
    """Verify shape smoke checkers reject payloads with invalid cylinder counts."""
    step_text = _build_shape_fixture(
        solid_breps=1,
        advanced_faces=12,
        planes=12,
        cylinders=invalid_cylinders,
        lines=24,
        edges=24,
    )
    res = checker_fn(step_text)
    assert not res.passed
    assert any("CYLINDRICAL_SURFACE" in err for err in res.errors)


def test_validate_step_bounding_box_matching() -> None:
    """Verify that validate_step_bounding_box passes when spans match expected within tolerance."""
    bbox = StepBoundingBox(
        min_x=0.0,
        max_x=100.0,
        min_y=0.0,
        max_y=50.0,
        min_z=0.0,
        max_z=20.0,
        size_x=100.0,
        size_y=50.0,
        size_z=20.0,
    )
    # Exact match
    errors = validate_step_bounding_box(bbox, expected_x=100.0, expected_y=50.0, expected_z=20.0, tolerance_mm=0.5)
    assert errors == []

    # Permuted axis match (YZX orientation)
    errors_permuted = validate_step_bounding_box(
        bbox, expected_x=20.0, expected_y=100.0, expected_z=50.0, tolerance_mm=0.5
    )
    assert errors_permuted == []

    # Match within tolerance (0.2 mm deviation with 0.5 mm tolerance)
    errors_tol = validate_step_bounding_box(bbox, expected_x=100.2, expected_y=49.8, expected_z=20.1, tolerance_mm=0.5)
    assert errors_tol == []


def test_validate_step_bounding_box_out_of_tolerance() -> None:
    """Verify that validate_step_bounding_box detects dimensional discrepancies exceeding tolerance."""
    bbox = StepBoundingBox(
        min_x=0.0,
        max_x=100.0,
        min_y=0.0,
        max_y=50.0,
        min_z=0.0,
        max_z=20.0,
        size_x=100.0,
        size_y=50.0,
        size_z=20.0,
    )
    errors = validate_step_bounding_box(bbox, expected_x=120.0, expected_y=50.0, expected_z=20.0, tolerance_mm=0.5)
    assert len(errors) == 1
    assert "deviates from expected" in errors[0]


def test_validate_step_bounding_box_empty_or_none() -> None:
    """Verify that empty or None bounding boxes return appropriate errors."""
    assert len(validate_step_bounding_box(None, 10.0, 10.0, 10.0)) == 1
    empty_bbox = StepBoundingBox(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    assert len(validate_step_bounding_box(empty_bbox, 10.0, 10.0, 10.0)) == 1


def test_linear_scaling_dense_step_payload_median_benchmark() -> None:
    """Verify that entity counting and bounding box extraction scale linearly using median benchmarks."""

    def _make_payload(count: int) -> str:
        entities = [f"#{i} = ADVANCED_FACE('Face {i}', (#{i * 10}), #{i * 10 + 1}, .T.);" for i in range(1, count + 1)]
        entities.append("#99999 = MANIFOLD_SOLID_BREP('Solid', #1);")
        entities.append("#100000 = CARTESIAN_POINT('Origin', (0.0, 0.0, 0.0));")
        entities.append(f"#100001 = CARTESIAN_POINT('Corner', ({float(count)}, 100.0, 50.0));")
        return _build_step_payload(entities)

    payload_n = _make_payload(1000)
    payload_2n = _make_payload(2000)

    # Warm-up run
    validate_step_text(payload_n)
    validate_step_text(payload_2n)

    # Sample 3 runs for N
    times_n: list[float] = []
    for _ in range(3):
        t0 = time.perf_counter()
        validate_step_text(payload_n)
        times_n.append(time.perf_counter() - t0)

    # Sample 3 runs for 2N
    times_2n: list[float] = []
    for _ in range(3):
        t0 = time.perf_counter()
        validate_step_text(payload_2n)
        times_2n.append(time.perf_counter() - t0)

    median_n = sorted(times_n)[1]
    median_2n = sorted(times_2n)[1]

    # Verify linear complexity scaling ratio: T(2N) <= 3 * T(N) + 0.05s epsilon
    assert median_2n <= 3.0 * median_n + 0.05


def test_tracemalloc_peak_auxiliary_memory_is_constant() -> None:
    """Verify via tracemalloc that scanner auxiliary memory remains strictly bounded (O(1))."""
    entities_large = [f"#{i} = ADVANCED_FACE('Face {i}', (#{i * 10}), #{i * 10 + 1}, .T.);" for i in range(1, 5001)]
    entities_large.append("#99999 = MANIFOLD_SOLID_BREP('Solid', #1);")
    entities_large.append("#100000 = CARTESIAN_POINT('Origin', (0.0, 0.0, 0.0));")
    entities_large.append("#100001 = CARTESIAN_POINT('Corner', (100.0, 100.0, 100.0));")
    step_text = _build_step_payload(entities_large)

    tracemalloc.start()
    tracemalloc.reset_peak()

    res = validate_step_text(step_text)
    assert res.is_valid

    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # Peak auxiliary memory during validation must be below 100 KB
    assert peak < 100_000, f"Peak memory {peak} bytes exceeded 100 KB limit"


def test_step_unit_resolution_and_scaling() -> None:
    """Verify ISO 10303-41 length unit scale extraction and bounding box normalization."""
    # 1. Millimetre standard unit: SI_UNIT(.MILLI., .METRE.)
    entities_mm = [
        "( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI., .METRE.) );",
        "#1 = MANIFOLD_SOLID_BREP('Solid', #2);",
        "#2 = ADVANCED_FACE('Face', (#3), #4, .T.);",
        "#3 = CARTESIAN_POINT('P1', (0.0, 0.0, 0.0));",
        "#4 = CARTESIAN_POINT('P2', (100.0, 50.0, 25.0));",
    ]
    res_mm = validate_step_text(_build_step_payload(entities_mm))
    assert res_mm.is_valid
    assert res_mm.unit_scale_to_mm == 1.0
    assert res_mm.unit_name == "millimetre"
    assert res_mm.bounding_box is not None
    assert res_mm.bounding_box.size_x == 100.0
    assert res_mm.bounding_box_mm is not None
    assert res_mm.bounding_box_mm.size_x == 100.0

    # 2. Metre base unit: SI_UNIT($, .METRE.) -> converts 0.1 m to 100.0 mm
    entities_m = [
        "( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT($, .METRE.) );",
        "#1 = MANIFOLD_SOLID_BREP('Solid', #2);",
        "#2 = ADVANCED_FACE('Face', (#3), #4, .T.);",
        "#3 = CARTESIAN_POINT('P1', (0.0, 0.0, 0.0));",
        "#4 = CARTESIAN_POINT('P2', (0.1, 0.05, 0.025));",
    ]
    res_m = validate_step_text(_build_step_payload(entities_m))
    assert res_m.is_valid
    assert res_m.unit_scale_to_mm == 1000.0
    assert res_m.unit_name == "metre"
    assert res_m.bounding_box is not None
    assert math.isclose(res_m.bounding_box.size_x, 0.1, abs_tol=1e-6)
    assert res_m.bounding_box_mm is not None
    assert math.isclose(res_m.bounding_box_mm.size_x, 100.0, abs_tol=1e-6)
    assert math.isclose(res_m.bounding_box_mm.size_y, 50.0, abs_tol=1e-6)
    assert math.isclose(res_m.bounding_box_mm.size_z, 25.0, abs_tol=1e-6)

    # 3. Invalid prefix .NONE. is rejected
    entities_none = [
        "( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.NONE., .METRE.) );",
        "#1 = MANIFOLD_SOLID_BREP('Solid', #2);",
        "#2 = ADVANCED_FACE('Face', (#3), #4, .T.);",
    ]
    res_none = validate_step_text(_build_step_payload(entities_none))
    assert any("UNSUPPORTED_STEP_UNIT_PREFIX" in w for w in res_none.warnings)
    assert res_none.unit_scale_to_mm is None

    # 4. Conflicting units produce AMBIGUOUS_STEP_UNIT
    entities_ambig = [
        "( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI., .METRE.) );",
        "( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT($, .METRE.) );",
        "#1 = MANIFOLD_SOLID_BREP('Solid', #2);",
        "#2 = ADVANCED_FACE('Face', (#3), #4, .T.);",
    ]
    res_ambig = validate_step_text(_build_step_payload(entities_ambig))
    assert res_ambig.unit_name == "ambiguous"
    assert res_ambig.unit_scale_to_mm is None
    assert any("AMBIGUOUS_STEP_UNIT" in w for w in res_ambig.warnings)


def test_step_unit_keyword_inside_comments_and_strings() -> None:
    """Verify SI_UNIT declarations inside comments and string literals are ignored."""
    entities = [
        "/* ( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT($, .METRE.) ); */",
        "#10 = ADVANCED_FACE('SI_UNIT($, .METRE.)', (#1), #2, .T.);",
        "( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI., .METRE.) );",
        "#1 = MANIFOLD_SOLID_BREP('Solid', #10);",
        "#2 = CARTESIAN_POINT('P1', (0.0, 0.0, 0.0));",
        "#3 = CARTESIAN_POINT('P2', (50.0, 50.0, 50.0));",
    ]
    res = validate_step_text(_build_step_payload(entities))
    assert res.is_valid
    assert res.unit_scale_to_mm == 1.0
    assert res.unit_name == "millimetre"


def test_validate_step_bounding_box_finite_and_non_negative_guards() -> None:
    """Verify that NaN, infinities, and negative dimensions are rejected defensively."""
    bbox = StepBoundingBox(0.0, 100.0, 0.0, 50.0, 0.0, 25.0, 100.0, 50.0, 25.0)

    # NaN expected dimension
    errors_nan = validate_step_bounding_box(bbox, expected_x=float("nan"), expected_y=50.0, expected_z=25.0)
    assert len(errors_nan) == 1
    assert "must be finite" in errors_nan[0]

    # Infinity expected dimension
    errors_inf = validate_step_bounding_box(bbox, expected_x=float("inf"), expected_y=50.0, expected_z=25.0)
    assert len(errors_inf) == 1
    assert "must be finite" in errors_inf[0]

    # Negative expected dimension
    errors_neg = validate_step_bounding_box(bbox, expected_x=-100.0, expected_y=50.0, expected_z=25.0)
    assert len(errors_neg) == 1
    assert "must be non-negative" in errors_neg[0]

    # Negative tolerance
    errors_tol = validate_step_bounding_box(bbox, expected_x=100.0, expected_y=50.0, expected_z=25.0, tolerance_mm=-0.5)
    assert len(errors_tol) == 1
    assert "tolerance_mm must be non-negative" in errors_tol[0]


def test_validate_step_text_end_to_end_dimension_integration() -> None:
    """Verify end-to-end dimension validation within validate_step_text."""
    # 1. Matching dimensions in millimeters
    entities_match = [
        "( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI., .METRE.) );",
        "#1 = MANIFOLD_SOLID_BREP('Solid', #2);",
        "#2 = ADVANCED_FACE('Face', (#3), #4, .T.);",
        "#3 = CARTESIAN_POINT('P1', (0.0, 0.0, 0.0));",
        "#4 = CARTESIAN_POINT('P2', (100.0, 50.0, 20.0));",
    ]
    res_pass = validate_step_text(
        _build_step_payload(entities_match),
        expected_dimensions=(100.0, 50.0, 20.0),
        tolerance_mm=0.5,
    )
    assert res_pass.is_valid
    assert len(res_pass.errors) == 0

    # 2. Deviating dimensions exceeding tolerance
    res_fail = validate_step_text(
        _build_step_payload(entities_match),
        expected_dimensions=(120.0, 50.0, 20.0),
        tolerance_mm=0.5,
    )
    assert not res_fail.is_valid
    assert any("deviates from expected" in err for err in res_fail.errors)

    # 3. Missing unit with expected_dimensions causes UNRESOLVED_STEP_UNIT error
    entities_no_unit = [
        "#1 = MANIFOLD_SOLID_BREP('Solid', #2);",
        "#2 = ADVANCED_FACE('Face', (#3), #4, .T.);",
        "#3 = CARTESIAN_POINT('P1', (0.0, 0.0, 0.0));",
        "#4 = CARTESIAN_POINT('P2', (100.0, 50.0, 20.0));",
    ]
    res_no_unit = validate_step_text(
        _build_step_payload(entities_no_unit),
        expected_dimensions=(100.0, 50.0, 20.0),
    )
    assert not res_no_unit.is_valid
    assert any("UNRESOLVED_STEP_UNIT" in err for err in res_no_unit.errors)


def test_diagnostic_truncation_notice_at_limit() -> None:
    """Verify that diagnostic list length is strictly capped at MAX_DIAGNOSTICS (100)."""
    from geometry.step_checker import _append_diagnostic

    diag_list: list[str] = []
    for i in range(150):
        _append_diagnostic(diag_list, f"Diagnostic error #{i}")

    assert len(diag_list) == MAX_DIAGNOSTICS
    assert "Diagnostic limit reached (100)" in diag_list[-1]


def test_structural_markers_inside_comments_and_strings_ignored() -> None:
    """Verify that structural markers inside comments and string literals do not break section extraction."""
    payload = (
        "ISO-10303-21;\n"
        "/* Comment with ISO-10303-21; and DATA; and ENDSEC; */\n"
        "HEADER;\n"
        "FILE_DESCRIPTION(('Description with ENDSEC; inside string'), '2;1');\n"
        "FILE_NAME('part_with_DATA;_name.par', '2026-08-24', ('Author'), ('Org'), 'SE', 'SE', 'None');\n"
        "ENDSEC;\n"
        "DATA;\n"
        "/* In-data comment with ENDSEC; */\n"
        "#1 = MANIFOLD_SOLID_BREP('Solid with ENDSEC; and DATA;', #2);\n"
        "#2 = ADVANCED_FACE('Face', (#3), #4, .T.);\n"
        "#3 = CARTESIAN_POINT('P1', (0.0, 0.0, 0.0));\n"
        "#4 = CARTESIAN_POINT('P2', (100.0, 50.0, 20.0));\n"
        "( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI., .METRE.) );\n"
        "ENDSEC;\n"
        "END-ISO-10303-21;\n"
    )
    res = validate_step_text(payload, expected_dimensions=(100.0, 50.0, 20.0), tolerance_mm=0.5)
    assert res.is_valid
    assert len(res.errors) == 0
    assert res.entity_counts["MANIFOLD_SOLID_BREP"] == 1
    assert res.entity_counts["ADVANCED_FACE"] == 1
    assert res.bounding_box is not None
    assert res.bounding_box.size_x == 100.0


def test_oversized_coordinate_tuple_skipped_without_error() -> None:
    """Verify that a coordinate tuple exceeding MAX_COORDINATE_TUPLE_CHARS (256) is skipped safely."""
    oversized_tuple = ", ".join(["10.0"] * 60)  # > 300 characters
    entities = [
        f"#1 = CARTESIAN_POINT('Oversized', ({oversized_tuple}));",
        "#2 = CARTESIAN_POINT('Valid1', (0.0, 0.0, 0.0));",
        "#3 = CARTESIAN_POINT('Valid2', (50.0, 25.0, 10.0));",
    ]
    bbox = extract_step_bounding_box(_build_step_payload(entities))
    assert bbox is not None
    assert bbox.min_x == 0.0
    assert bbox.max_x == 50.0
    assert bbox.min_y == 0.0
    assert bbox.max_y == 25.0
    assert bbox.min_z == 0.0
    assert bbox.max_z == 10.0


def test_malformed_unit_declarations_remain_unresolved() -> None:
    """Verify that malformed unit declarations (wrong argument count or values) remain unresolved."""
    # 3 arguments: SI_UNIT(.KILO., .METRE., $)
    entities_3args = [
        "( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.KILO., .METRE., $) );",
        "#1 = MANIFOLD_SOLID_BREP('Solid', #2);",
    ]
    res_3args = validate_step_text(_build_step_payload(entities_3args))
    assert res_3args.unit_scale_to_mm is None
    assert res_3args.unit_name == "unresolved"

    # 1 argument: SI_UNIT(.MILLI.)
    entities_1arg = [
        "( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.) );",
        "#1 = MANIFOLD_SOLID_BREP('Solid', #2);",
    ]
    res_1arg = validate_step_text(_build_step_payload(entities_1arg))
    assert res_1arg.unit_scale_to_mm is None
    assert res_1arg.unit_name == "unresolved"


def test_header_unit_declaration_ignored() -> None:
    """Verify that unit declarations in HEADER section are ignored and do not affect DATA section units."""
    payload = (
        "ISO-10303-21;\n"
        "HEADER;\n"
        "FILE_DESCRIPTION(('( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT($, .METRE.) );'), '2;1');\n"
        "FILE_NAME('part.par', '2026-08-24', ('Author'), ('Org'), 'SE', 'SE', 'None');\n"
        "ENDSEC;\n"
        "DATA;\n"
        "#1 = MANIFOLD_SOLID_BREP('Solid', #2);\n"
        "#2 = ADVANCED_FACE('Face', (#3), #4, .T.);\n"
        "#3 = CARTESIAN_POINT('P1', (0.0, 0.0, 0.0));\n"
        "#4 = CARTESIAN_POINT('P2', (100.0, 50.0, 20.0));\n"
        "( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI., .METRE.) );\n"
        "ENDSEC;\n"
        "END-ISO-10303-21;\n"
    )
    res = validate_step_text(payload)
    assert res.is_valid
    assert res.unit_scale_to_mm == 1.0
    assert res.unit_name == "millimetre"


def test_meter_spelling_and_standalone_si_unit_unresolved() -> None:
    """Verify that .METER. spelling and SI_UNIT without LENGTH_UNIT remain unresolved."""
    # 1. Non-standard American spelling .METER.
    entities_meter = [
        "( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI., .METER.) );",
        "#1 = MANIFOLD_SOLID_BREP('Solid', #2);",
    ]
    res_meter = validate_step_text(_build_step_payload(entities_meter))
    assert res_meter.unit_scale_to_mm is None
    assert res_meter.unit_name == "unresolved"

    # 2. Standalone SI_UNIT without LENGTH_UNIT (e.g. angle unit)
    entities_standalone = [
        "( PLANE_ANGLE_UNIT() NAMED_UNIT(*) SI_UNIT($, .RADIAN.) );",
        "#10 = SI_UNIT(.MILLI., .METRE.);",
        "#1 = MANIFOLD_SOLID_BREP('Solid', #2);",
    ]
    res_standalone = validate_step_text(_build_step_payload(entities_standalone))
    assert res_standalone.unit_scale_to_mm is None
    assert res_standalone.unit_name == "unresolved"


def test_two_element_expected_dimensions_returns_error_without_throwing() -> None:
    """Verify that passing fewer or more than three expected dimensions returns an error without IndexError."""
    entities = [
        "( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI., .METRE.) );",
        "#1 = MANIFOLD_SOLID_BREP('Solid', #2);",
        "#2 = ADVANCED_FACE('Face', (#3), #4, .T.);",
        "#3 = CARTESIAN_POINT('P1', (0.0, 0.0, 0.0));",
        "#4 = CARTESIAN_POINT('P2', (100.0, 50.0, 20.0));",
    ]
    step_text = _build_step_payload(entities)

    # 2-element tuple
    res_2 = validate_step_text(step_text, expected_dimensions=(100.0, 50.0))  # type: ignore[arg-type]
    assert not res_2.is_valid
    assert any("must contain exactly three values" in err for err in res_2.errors)

    # 4-element tuple
    res_4 = validate_step_text(step_text, expected_dimensions=(100.0, 50.0, 20.0, 10.0))  # type: ignore[arg-type]
    assert not res_4.is_valid
    assert any("must contain exactly three values" in err for err in res_4.errors)


def test_length_unit_inside_comment_does_not_authorize_si_unit() -> None:
    """Verify that LENGTH_UNIT inside a comment does not authorize an otherwise uncontextualized SI_UNIT."""
    entities = [
        "/* LENGTH_UNIT() */ #10 = ( NAMED_UNIT(*) SI_UNIT(.MILLI., .METRE.) );",
        "#1 = MANIFOLD_SOLID_BREP('Solid', #2);",
    ]
    res = validate_step_text(_build_step_payload(entities))
    assert res.unit_scale_to_mm is None
    assert res.unit_name == "unresolved"


def test_duplicate_diagnostic_at_boundary_does_not_trigger_truncation() -> None:
    """Verify that attempting to append a duplicate diagnostic at size 99 does not trigger the truncation notice."""
    from geometry.step_checker import _append_diagnostic

    diag_list: list[str] = []
    for i in range(99):
        _append_diagnostic(diag_list, f"Unique diagnostic #{i}")

    assert len(diag_list) == 99

    # Attempt to append a duplicate of an existing message
    _append_diagnostic(diag_list, "Unique diagnostic #0")
    assert len(diag_list) == 99
    assert not any("Diagnostic limit reached" in d for d in diag_list)

    # Attempt to append a new (100th unique) message -> triggers truncation notice
    _append_diagnostic(diag_list, "100th unique diagnostic")
    assert len(diag_list) == 100
    assert "Diagnostic limit reached (100)" in diag_list[-1]
