"""Focused unit tests for the desktop release packager and privacy auditor."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

# Load scripts/package_desktop.py dynamically
repo_root = Path(__file__).resolve().parents[3]
script_path = repo_root / "scripts" / "package_desktop.py"
spec = importlib.util.spec_from_file_location("package_desktop", script_path)
assert spec and spec.loader
pkg_mod: Any = importlib.util.module_from_spec(spec)
sys.modules["package_desktop"] = pkg_mod
spec.loader.exec_module(pkg_mod)


def test_build_remap_flags_structure() -> None:
    fake_repo = Path("C:/dev/cad_project")
    fake_user = Path("C:/Users/alice")
    fake_cargo = fake_user / ".cargo"
    fake_rustup = fake_user / ".rustup"

    flags = pkg_mod.build_remap_flags(fake_repo, fake_user, fake_cargo, fake_rustup)
    assert len(flags) > 0
    # Check standard, posix, and verbatim prefixes
    assert any("--remap-path-prefix=C:/dev/cad_project=/cad-copilot" in f for f in flags)
    assert any("--remap-path-prefix=\\\\?\\C:\\dev\\cad_project=/cad-copilot" in f for f in flags)
    assert any("--remap-path-prefix=c:/dev/cad_project=/cad-copilot" in f for f in flags)
    assert any("--remap-path-prefix=C:/Users/alice=/user" in f for f in flags)


def test_collect_forbidden_tokens_encodings_and_dynamism() -> None:
    fake_repo = Path("D:/MySpecialWorkspace")
    fake_user = Path("C:/Users/bob")
    tokens = pkg_mod.collect_forbidden_tokens(fake_repo, fake_user, username="bob")

    token_bytes_set = {tb for tb, _ in tokens}
    # Verify UTF-8 and UTF-16LE encodings present for workspace and user
    assert b"MySpecialWorkspace" in token_bytes_set
    assert "MySpecialWorkspace".encode("utf-16le") in token_bytes_set
    assert b"C:\\Users\\bob" in token_bytes_set
    assert "C:\\Users\\bob".encode("utf-16le") in token_bytes_set
    assert b"Users\\bob" in token_bytes_set
    assert "Users\\bob".encode("utf-16le") in token_bytes_set


def test_audit_binary_privacy_detects_utf8_and_utf16le() -> None:
    fake_repo = Path("D:/secret_dir")
    fake_user = Path("C:/Users/charlie")
    tokens = pkg_mod.collect_forbidden_tokens(fake_repo, fake_user, username="charlie")

    # Clean binary data
    clean_data = b"Some normal compiled code \x00\x01\x02"
    violations = pkg_mod.audit_binary_privacy(clean_data, "test.exe", tokens)
    assert violations == []

    # Leaked UTF-8
    dirty_utf8 = b"prefix " + b"secret_dir" + b" suffix"
    v_utf8 = pkg_mod.audit_binary_privacy(dirty_utf8, "test.exe", tokens)
    assert len(v_utf8) > 0
    assert "secret_dir" in v_utf8[0]

    # Leaked UTF-16LE
    dirty_utf16 = b"prefix " + "C:\\Users\\charlie".encode("utf-16le") + b" suffix"
    v_utf16 = pkg_mod.audit_binary_privacy(dirty_utf16, "test.exe", tokens)
    assert len(v_utf16) > 0
    assert "UTF-16LE" in v_utf16[0]


def test_audit_metadata_privacy_scans_metadata_and_fails_closed(tmp_path: Path) -> None:
    staged_engine = tmp_path / "staged"
    dist_info = staged_engine / "_internal" / "pkg-1.0.dist-info"
    dist_info.mkdir(parents=True)

    metadata_file = dist_info / "METADATA"
    metadata_file.write_text("Name: pkg\nAuthor: Workstation User\nPath: /secret/path\n", encoding="utf-8")

    fake_repo = Path("C:/secret/path")
    fake_user = Path("C:/Users/david")
    tokens = pkg_mod.collect_forbidden_tokens(fake_repo, fake_user)

    violations = pkg_mod.audit_metadata_privacy(staged_engine, tokens)
    assert len(violations) > 0
    assert "METADATA" in violations[0]


def test_verify_nsis_installers(tmp_path: Path) -> None:
    nsis_dir = tmp_path / "nsis"
    nsis_dir.mkdir()

    tokens = [(b"forbidden_token", "UTF-8 'forbidden_token'")]

    # Missing installer
    v_empty, _ = pkg_mod.verify_nsis_installers(nsis_dir, tokens)
    assert len(v_empty) == 1
    assert "No NSIS installer found" in v_empty[0]

    # Too small installer
    tiny_inst = nsis_dir / "setup.exe"
    tiny_inst.write_bytes(b"MZ" + b"\x00" * 100)
    v_small, _ = pkg_mod.verify_nsis_installers(nsis_dir, tokens)
    assert any("unexpectedly small" in err for err in v_small)

    # Valid sized installer with uncompressed forbidden token
    large_inst = nsis_dir / "setup.exe"
    large_inst.write_bytes(b"forbidden_token" + b"\x00" * (11 * 1024 * 1024))
    v_leak, verified = pkg_mod.verify_nsis_installers(nsis_dir, tokens)
    assert any("uncompressed in installer" in err for err in v_leak)
    assert len(verified) == 1
    assert verified[0][0] == "setup.exe"
