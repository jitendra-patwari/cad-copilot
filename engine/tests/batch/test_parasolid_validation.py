"""Unit tests for pure bounded Parasolid text transmission (.x_t) structural validation.

Invariants Verified:
1. Valid Minimal / Representative Payloads: Passes structurally sound Parasolid transmission files.
2. Positive Size Enforced: Rejects empty and undersized files (< 100 bytes).
3. Binary & NUL Rejection: Rejects payloads with NUL bytes in header or trailer windows.
4. Header Marker Enforcement: Rejects files missing charset line, **PARASOLID delimiter,
   **PART1;, FORMAT=text;, GUISE=transmit;, **PART2;, or SCH=SCH_ markers.
5. Bounded Read Bounds: Proves only bounded header and trailer windows are read.
6. Error Sanitization: Errors contain safe diagnostics without leaking raw file paths.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from batch.parasolid_validation import (
    ParasolidValidationError,
    validate_parasolid_artifact,
)


def _build_valid_parasolid_content(
    extra_body: str = "",
    schema: str = "SCH_3800150_37102",
) -> bytes:
    """Constructs a valid synthetic text Parasolid transmission file byte payload."""
    lines = [
        "**ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz**************************\r\n",
        "**PARASOLID !\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~0123456789**************************\r\n",
        "**PART1;\r\n",
        "MC=TEST_HOST;\r\n",
        "OS=Windows NT;\r\n",
        "APPL=SolidEdge;\r\n",
        "FORMAT=text;\r\n",
        "GUISE=transmit;\r\n",
        "**PART2;\r\n",
        f"SCH={schema};\r\n",
        "USFLD_SIZE=0;\r\n",
    ]
    header = "".join(lines)
    body = extra_body or "1 2 3 4 5 6 7 8 9 10 11 12 13 14 15\r\n20 14 10 0 0 121 207 215\r\n"
    trailer = "79 16 300 SolidEdge/Styles1 0 \r\n"
    return (header + body + trailer).encode("latin-1")


class TestParasolidValidationPositive:
    """Positive structural validation cases."""

    def test_valid_minimal_payload_passes(self, tmp_path: Path) -> None:
        target = tmp_path / "model.x_t"
        content = _build_valid_parasolid_content()
        target.write_bytes(content)

        validate_parasolid_artifact(target)
        validate_parasolid_artifact(target, size_bytes=len(content))

    def test_valid_representative_payload_with_custom_schema(self, tmp_path: Path) -> None:
        target = tmp_path / "custom.x_t"
        content = _build_valid_parasolid_content(
            extra_body="80 1 11 296 300 9000 0 0 0 0 3 5 0 0 0 FFTTTTTTFFFFTFFFF1 82 5 13 2\r\n",
            schema="SCH_3700000_36000",
        )
        target.write_bytes(content)

        validate_parasolid_artifact(target)

    def test_bounded_large_file_does_not_read_entire_payload(self, tmp_path: Path) -> None:
        """Proves that a large file (> 2MB) is validated via bounded windows only."""
        target = tmp_path / "large.x_t"
        header = _build_valid_parasolid_content()

        # Create a 2 MB file with newline-delimited padding in the middle
        padding_line = b"1 2 3 4 5 6 7 8 9 10 11 12 13 14 15\r\n"
        padding_len = 2 * 1024 * 1024
        target.write_bytes(header[:300] + (padding_line * (padding_len // len(padding_line))) + header[300:])

        total_bytes_read = 0
        real_open = open

        class _SpyFile:
            def __init__(self, raw_f: Any) -> None:
                self._raw_f = raw_f

            def read(self, *args: Any, **kwargs: Any) -> bytes:
                nonlocal total_bytes_read
                chunk = self._raw_f.read(*args, **kwargs)
                assert isinstance(chunk, bytes)
                total_bytes_read += len(chunk)
                return chunk

            def seek(self, *args: Any, **kwargs: Any) -> Any:
                return self._raw_f.seek(*args, **kwargs)

            def __enter__(self) -> _SpyFile:
                return self

            def __exit__(self, *args: Any) -> None:
                self._raw_f.close()

        with patch("builtins.open", side_effect=lambda *a, **kw: _SpyFile(real_open(*a, **kw))):
            validate_parasolid_artifact(target)

        assert 0 < total_bytes_read <= 4096
        assert total_bytes_read < padding_len

    def test_valid_payload_with_lf_line_endings(self, tmp_path: Path) -> None:
        """Proves valid Parasolid file with LF newlines passes validation."""
        target = tmp_path / "model_lf.x_t"
        content = _build_valid_parasolid_content().replace(b"\r\n", b"\n")
        target.write_bytes(content)

        validate_parasolid_artifact(target)


class TestParasolidValidationNegative:
    """Negative structural and security failure cases."""

    def test_undersized_payload_rejected(self, tmp_path: Path) -> None:
        target = tmp_path / "tiny.x_t"
        target.write_bytes(b"**ABCDEFGHIJKLMNOPQRSTUVWXYZ\r\n**PARASOLID \r\n")

        with pytest.raises(ParasolidValidationError, match="too small"):
            validate_parasolid_artifact(target)

    def test_empty_file_rejected(self, tmp_path: Path) -> None:
        target = tmp_path / "empty.x_t"
        target.write_bytes(b"")

        with pytest.raises(ParasolidValidationError, match="too small"):
            validate_parasolid_artifact(target)

    def test_header_nul_byte_rejected(self, tmp_path: Path) -> None:
        target = tmp_path / "nul_header.x_t"
        valid = _build_valid_parasolid_content()
        corrupted = valid[:50] + b"\x00" + valid[51:]
        target.write_bytes(corrupted)

        with pytest.raises(ParasolidValidationError, match="forbidden binary or NUL bytes in header"):
            validate_parasolid_artifact(target)

    def test_trailer_nul_byte_rejected(self, tmp_path: Path) -> None:
        target = tmp_path / "nul_trailer.x_t"
        valid = _build_valid_parasolid_content()
        # Pad file beyond PARASOLID_HEADER_SCAN_BYTES so NUL byte is strictly in trailer window
        padding = b"1 2 3 4 5 6 7 8 9 10\r\n" * 150
        padded = valid + padding + b"trailer_end\r\n"
        corrupted = padded[:-5] + b"\x00" + padded[-4:]
        target.write_bytes(corrupted)

        with pytest.raises(ParasolidValidationError, match="forbidden binary or NUL bytes in trailer"):
            validate_parasolid_artifact(target)

    def test_missing_charset_line_rejected(self, tmp_path: Path) -> None:
        target = tmp_path / "bad_charset.x_t"
        valid = _build_valid_parasolid_content()
        # Replace line 1 with dummy text
        corrupted = b"**NOT_THE_CHARSET_LINE*********************************************************\r\n" + valid[82:]
        target.write_bytes(corrupted)

        with pytest.raises(ParasolidValidationError, match="missing valid character set"):
            validate_parasolid_artifact(target)

    def test_missing_parasolid_delimiter_rejected(self, tmp_path: Path) -> None:
        target = tmp_path / "bad_delimiter.x_t"
        valid = _build_valid_parasolid_content()
        lines = valid.split(b"\r\n")
        lines[1] = b"**NOT_PARASOLID !\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~0123456789**************************"
        target.write_bytes(b"\r\n".join(lines))

        with pytest.raises(ParasolidValidationError, match="missing valid \\*\\*PARASOLID"):
            validate_parasolid_artifact(target)

    def test_missing_part1_marker_rejected(self, tmp_path: Path) -> None:
        target = tmp_path / "no_part1.x_t"
        valid = _build_valid_parasolid_content().replace(b"**PART1;", b"**SECTION1;")
        target.write_bytes(valid)

        with pytest.raises(ParasolidValidationError, match="missing required \\*\\*PART1;"):
            validate_parasolid_artifact(target)

    def test_missing_format_text_rejected(self, tmp_path: Path) -> None:
        target = tmp_path / "no_format_text.x_t"
        valid = _build_valid_parasolid_content().replace(b"FORMAT=text;", b"FORMAT=binary;")
        target.write_bytes(valid)

        with pytest.raises(ParasolidValidationError, match="missing required FORMAT=text;"):
            validate_parasolid_artifact(target)

    def test_missing_guise_transmit_rejected(self, tmp_path: Path) -> None:
        target = tmp_path / "no_guise.x_t"
        valid = _build_valid_parasolid_content().replace(b"GUISE=transmit;", b"GUISE=snapshot;")
        target.write_bytes(valid)

        with pytest.raises(ParasolidValidationError, match="missing required GUISE=transmit;"):
            validate_parasolid_artifact(target)

    def test_missing_part2_marker_rejected(self, tmp_path: Path) -> None:
        target = tmp_path / "no_part2.x_t"
        valid = _build_valid_parasolid_content().replace(b"**PART2;", b"**SECTION2;")
        target.write_bytes(valid)

        with pytest.raises(ParasolidValidationError, match="missing required \\*\\*PART2;"):
            validate_parasolid_artifact(target)

    def test_missing_schema_marker_rejected(self, tmp_path: Path) -> None:
        target = tmp_path / "no_schema.x_t"
        valid = _build_valid_parasolid_content().replace(b"SCH=SCH_", b"VERSION=V_")
        target.write_bytes(valid)

        with pytest.raises(ParasolidValidationError, match="missing required SCH=SCH_"):
            validate_parasolid_artifact(target)

    def test_whitespace_only_trailer_rejected(self, tmp_path: Path) -> None:
        target = tmp_path / "blank_trailer.x_t"
        valid = _build_valid_parasolid_content()
        target.write_bytes(valid + (b"   \r\n" * 800))

        with pytest.raises(ParasolidValidationError, match="trailer contains only whitespace"):
            validate_parasolid_artifact(target)

    def test_truncated_trailer_missing_newline_rejected(self, tmp_path: Path) -> None:
        target = tmp_path / "no_newline.x_t"
        valid = _build_valid_parasolid_content().rstrip(b"\r\n")
        target.write_bytes(valid)

        with pytest.raises(ParasolidValidationError, match="missing newline terminator"):
            validate_parasolid_artifact(target)

    def test_reversed_section_sequence_rejected(self, tmp_path: Path) -> None:
        """Proves PART2 appearing before PART1 is rejected by sequence verification."""
        target = tmp_path / "reversed_sections.x_t"
        lines = [
            "**ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz**************************\r\n",
            "**PARASOLID !\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~0123456789**************************\r\n",
            "**PART2;\r\n",
            "SCH=SCH_3800150_37102;\r\n",
            "**PART1;\r\n",
            "FORMAT=text;\r\n",
            "GUISE=transmit;\r\n",
        ]
        target.write_bytes("".join(lines).encode("latin-1") + b"body and trailer\r\n")

        with pytest.raises(ParasolidValidationError, match="section sequence"):
            validate_parasolid_artifact(target)

    def test_error_sanitization_no_raw_paths(self, tmp_path: Path) -> None:
        """Proves error messages do not leak workstation paths or filenames (SEC-07)."""
        nested_dir = tmp_path / "Secret_Vault" / "Confidential_Parts"
        nested_dir.mkdir(parents=True)
        target = nested_dir / "secret_part.x_t"
        target.write_bytes(b"INVALID DATA PAYLOAD")

        with pytest.raises(ParasolidValidationError) as exc_info:
            validate_parasolid_artifact(target)

        msg = str(exc_info.value)
        assert "Secret_Vault" not in msg
        assert "Confidential_Parts" not in msg
        assert "secret_part" not in msg
        assert len(msg) <= 512

    def test_malformed_schema_version_rejected(self, tmp_path: Path) -> None:
        """Proves that a schema marker missing version identifier is rejected."""
        target = tmp_path / "malformed_schema.x_t"
        content = _build_valid_parasolid_content().replace(b"SCH=SCH_3800150_37102;", b"SCH=SCH_;")
        target.write_bytes(content)

        with pytest.raises(ParasolidValidationError, match="schema version marker"):
            validate_parasolid_artifact(target)

    def test_non_ascii_encoding_in_header_rejected(self, tmp_path: Path) -> None:
        """Proves that non-ASCII byte content in header is rejected."""
        target = tmp_path / "non_ascii_header.x_t"
        content = _build_valid_parasolid_content().replace(b"FORMAT=text;", b"FORMAT=t\xe9xt;")
        target.write_bytes(content)

        with pytest.raises(ParasolidValidationError, match="non-ASCII"):
            validate_parasolid_artifact(target)

    def test_non_ascii_encoding_in_trailer_rejected(self, tmp_path: Path) -> None:
        """Proves that non-ASCII byte content in trailer is rejected."""
        target = tmp_path / "non_ascii_trailer.x_t"
        valid = _build_valid_parasolid_content()
        target.write_bytes(valid + b"\xff\xfe\xfd\r\n")

        with pytest.raises(ParasolidValidationError, match="non-ASCII"):
            validate_parasolid_artifact(target)

    def test_oversized_line_rejected(self, tmp_path: Path) -> None:
        """Proves that an oversized line exceeding MAX_LINE_BYTES (256) is rejected."""
        target = tmp_path / "oversized_line.x_t"
        oversized = "A" * 300 + "\r\n"
        content = _build_valid_parasolid_content(extra_body=oversized)
        target.write_bytes(content)

        with pytest.raises(ParasolidValidationError, match="exceeding 256 bytes"):
            validate_parasolid_artifact(target)
