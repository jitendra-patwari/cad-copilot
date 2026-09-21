# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for CAD Copilot one-folder engine distribution.

Builds a standalone, console-enabled one-folder payload (cad-copilot-engine)
containing pythoncom/pywintypes runtime DLLs, canonical JSON schemas,
and the fixed CLI dispatcher.
"""

from __future__ import annotations

import importlib.util
import os
from PyInstaller.utils.hooks import collect_submodules, copy_metadata

# Explicit pywin32 system DLLs
binaries: list[tuple[str, str]] = []
try:
    import pythoncom
    import pywintypes

    if not pywintypes.__file__ or not pythoncom.__file__:
        raise RuntimeError("Required pywin32 system DLLs (pythoncom/pywintypes) missing file path.")
    binaries.append((pywintypes.__file__, "."))
    binaries.append((pythoncom.__file__, "."))
except Exception as exc:
    raise RuntimeError(f"Failed to resolve required pywin32 system DLLs: {exc}") from exc


def _resolve_installed_resource_dir(pkg_name: str, subpath: str) -> str:
    """Resolve directory strictly from the installed package. Fails closed if missing."""
    spec = importlib.util.find_spec(pkg_name)
    if not spec or not spec.submodule_search_locations:
        raise RuntimeError(f"Required package '{pkg_name}' is not installed in the packaging environment.")
    for loc in spec.submodule_search_locations:
        candidate = os.path.join(loc, subpath)
        if os.path.isdir(candidate):
            return candidate
    raise RuntimeError(f"Required resource directory '{subpath}' not found in installed package '{pkg_name}'.")


# Collect explicit JSON schema and resource directories strictly from installed distribution
datas: list[tuple[str, str]] = [
    (_resolve_installed_resource_dir("manifests", "schemas"), "manifests/schemas"),
    (_resolve_installed_resource_dir("example_catalog", "resources"), "example_catalog/resources"),
    (_resolve_installed_resource_dir("plan_providers", "schemas"), "plan_providers/schemas"),
    (_resolve_installed_resource_dir("ipc", "schemas"), "ipc/schemas"),
    (_resolve_installed_resource_dir("batch", "schemas"), "batch/schemas"),
]

metadata_datas = copy_metadata("cad-copilot")
if not metadata_datas:
    raise RuntimeError("Required 'cad-copilot' distribution metadata missing from packaging environment.")
datas.extend(metadata_datas)

# Hidden imports required across engine execution
hiddenimports: list[str] = [
    "desktop_entrypoint",
    "ipc",
    "ipc.stdio",
    "ipc.batch_stdio",
    "ipc.composition",
    "ipc.batch_composition",
    "ipc.descriptors",
    "ipc.progress",
    "ipc.wire",
    "application",
    "application.service",
    "application.models",
    "geometry",
    "drivers",
    "drivers.solidedge",
    "manifests",
    "example_catalog",
    "plan_providers",
    "batch",
    "interfaces",
    "win32com",
    "win32com.client",
    "win32com.client.dynamic",
    "win32com.client.gencache",
    "jsonschema",
    "google.genai",
    "pydantic",
    "PIL",
    "PIL.Image",
]

for pkg in ["jsonschema", "google.genai", "pydantic", "referencing"]:
    submods = collect_submodules(pkg)
    if not submods:
        raise RuntimeError(f"Failed to collect required submodules for package '{pkg}'.")
    hiddenimports.extend(submods)

# Resolve entrypoint script strictly from installed package (no source fallback)
entrypoint_spec = importlib.util.find_spec("desktop_entrypoint")
if not entrypoint_spec or not entrypoint_spec.origin or not os.path.isfile(entrypoint_spec.origin):
    raise RuntimeError("Required entrypoint module 'desktop_entrypoint' is not installed in the packaging environment.")
entrypoint_script = entrypoint_spec.origin

a = Analysis(
    [entrypoint_script],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "pytest_cov",
        "mypy",
        "ruff",
        "unittest",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="cad-copilot-engine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="cad-copilot-engine",
)
