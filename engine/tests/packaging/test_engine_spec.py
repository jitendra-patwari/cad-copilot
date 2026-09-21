"""Static assertions for PyInstaller packaging specification."""

from __future__ import annotations

from pathlib import Path


def test_spec_exists_and_enforces_console_true() -> None:
    spec_path = Path(__file__).resolve().parents[2] / "packaging" / "cad-copilot-engine.spec"
    assert spec_path.is_file(), f"Spec file must exist at {spec_path}"

    content = spec_path.read_text(encoding="utf-8")

    # Contract requires explicit console=True
    assert "console=True" in content, "cad-copilot-engine.spec must explicitly declare console=True"

    # Contract requires packaging all 5 schema directories
    assert "manifests/schemas" in content
    assert "example_catalog/resources" in content
    assert "plan_providers/schemas" in content
    assert "ipc/schemas" in content
    assert "batch/schemas" in content

    # Contract requires pywin32 system DLLs
    assert "pythoncom" in content
    assert "pywintypes" in content

    # Contract strictly rejects repository-source fallback and editable paths
    assert "src_dir" not in content, "cad-copilot-engine.spec must not reference src_dir"
    assert "pathex=[]" in content, "cad-copilot-engine.spec must declare pathex=[]"
    assert "_resolve_installed_resource_dir" in content
    assert "raise RuntimeError" in content, "cad-copilot-engine.spec must fail closed on missing components"
