"""Deterministic packaging and staging script for CAD Copilot desktop engine.

Enforces Python 3.14.3 baseline, recreates an isolated packaging virtual
environment, installs complete pinned binary dependencies, builds the project
wheel using pinned tooling, verifies complete payload inventory and schemas,
generates dynamic redistribution notices covering all bundled libraries, and
atomically replaces `engine/dist/cad-copilot-engine/`.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def compute_sha256(file_path: Path) -> str:
    """Computes SHA-256 hex digest for a file."""
    hasher = hashlib.sha256()
    with file_path.open("rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def validate_packaging_environment(pkg_python: Path, constraints_file: Path) -> None:
    """Validates that packaging interpreter, PyInstaller version, and bootloader meet baseline."""
    val_script = """
import importlib.metadata
from pathlib import Path
import sys
import PyInstaller

ver = sys.version_info
if (ver.major, ver.minor, ver.micro) != (3, 14, 3):
    sys.stderr.write(f"ERROR: Expected Python 3.14.3 for desktop packaging, found {ver.major}.{ver.minor}.{ver.micro}\\n")
    sys.exit(1)

if PyInstaller.__version__ != "6.22.3":
    sys.stderr.write(f"ERROR: Expected pinned PyInstaller 6.22.3 for desktop packaging, found {PyInstaller.__version__}\\n")
    sys.exit(1)

bootloader_dir = Path(PyInstaller.__file__).parent / "bootloader" / "Windows-64bit-intel"
run_exe = bootloader_dir / "run.exe"
if not run_exe.is_file():
    sys.stderr.write(f"ERROR: Official precompiled Windows 64-bit bootloader not found at {run_exe}\\n")
    sys.exit(1)

constraints_file = Path(sys.argv[1])
for line in constraints_file.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    if "==" in line:
        pkg_name, expected_ver = line.split("==", 1)
        pkg_name = pkg_name.strip()
        expected_ver = expected_ver.strip()
        try:
            installed_ver = importlib.metadata.version(pkg_name)
            if installed_ver != expected_ver:
                sys.stderr.write(f"ERROR: Packaging dependency {pkg_name} mismatch: expected {expected_ver}, found {installed_ver}\\n")
                sys.exit(1)
        except importlib.metadata.PackageNotFoundError:
            sys.stderr.write(f"ERROR: Required packaging dependency {pkg_name} is not installed\\n")
            sys.exit(1)
"""
    res = subprocess.run([str(pkg_python), "-c", val_script, str(constraints_file)])
    if res.returncode != 0:
        sys.exit(res.returncode)


def setup_isolated_packaging_environment(
    pkg_env_dir: Path,
    constraints_file: Path,
) -> Path:
    """Sets up a freshly recreated isolated packaging environment with pinned binary constraints."""
    scripts_dir_name = "Scripts" if sys.platform == "win32" else "bin"
    exe_name = "python.exe" if sys.platform == "win32" else "python"
    pkg_python = pkg_env_dir / scripts_dir_name / exe_name

    print(f"Creating fresh isolated packaging virtual environment at {pkg_env_dir}...")
    if pkg_env_dir.exists():
        shutil.rmtree(pkg_env_dir, ignore_errors=True)

    res_venv = subprocess.run([sys.executable, "-m", "venv", "--clear", str(pkg_env_dir)])
    if res_venv.returncode != 0:
        sys.stderr.write(f"ERROR: Failed to create packaging virtual environment: {res_venv.returncode}\n")
        sys.exit(res_venv.returncode)

    print("Installing locked direct and transitive binary dependencies into packaging environment...")
    res_deps = subprocess.run(
        [
            str(pkg_python),
            "-m",
            "pip",
            "install",
            "--no-warn-script-location",
            "--only-binary=:all:",
            "-c",
            str(constraints_file),
            "-r",
            str(constraints_file),
        ]
    )
    if res_deps.returncode != 0:
        sys.stderr.write(f"ERROR: Failed to install packaging dependencies: {res_deps.returncode}\n")
        sys.exit(res_deps.returncode)

    validate_packaging_environment(pkg_python, constraints_file)
    return pkg_python


def generate_redistribution_notices(pkg_python: Path) -> tuple[str, set[str]]:
    """Generates complete third-party redistribution notices from the locked packaging environment."""
    script = """
import importlib.metadata
import json
import pathlib
import sys

notices = []
covered_pkgs = set()

header = \"\"\"CAD Copilot Desktop Engine - Third-Party Redistribution Notices
================================================================

This distribution bundles Python 3.14 (Python Software Foundation License)
and the following locked third-party dependencies:
\"\"\"
notices.append(header)

# 1. Python 3.14 runtime license
python_license_text = None
for cand in [
    pathlib.Path(sys.base_prefix) / "LICENSE.txt",
    pathlib.Path(sys.base_prefix) / "LICENSE",
    pathlib.Path(sys.prefix) / "LICENSE.txt",
    pathlib.Path(sys.prefix) / "LICENSE",
]:
    if cand.is_file():
        try:
            txt = cand.read_text(encoding="utf-8", errors="replace").strip()
            if len(txt) > 100:
                python_license_text = txt
                break
        except Exception:
            pass

if not python_license_text:
    raise RuntimeError("Failed to resolve Python 3.14 runtime license text from interpreter base prefix.")

python_entry = f\"\"\"================================================================
Package: Python (version {sys.version.split()[0]})
License: Python Software Foundation License (PSFL)
================================================================
{python_license_text}
\"\"\"
notices.append(python_entry)

for dist in sorted(importlib.metadata.distributions(), key=lambda d: d.metadata.get('Name', '').lower()):
    name = dist.metadata.get('Name', '')
    if not name or name.lower() == 'cad-copilot':
        continue

    covered_pkgs.add(name.lower().replace('-', '_'))
    covered_pkgs.add(name.lower().replace('_', '-'))
    ver = dist.version

    license_chunks = []
    seen_texts = set()
    for f in (dist.files or []):
        fn = str(f).replace('\\\\', '/').lower()
        parts = fn.split('/')
        if '.dist-info' in parts[0] and any(token in parts[-1] for token in ['license', 'licence', 'copying', 'notice']) and not fn.endswith('.py') and not fn.endswith('.pyc'):
            try:
                p = dist.locate_file(f)
                if p.is_file():
                    txt = p.read_text(encoding='utf-8', errors='replace').strip()
                    if len(txt) > 20 and txt not in seen_texts:
                        seen_texts.add(txt)
                        license_chunks.append((str(f), txt))
            except Exception:
                pass

    if not license_chunks:
        for f in (dist.files or []):
            fn = str(f).replace('\\\\', '/').lower()
            if '_vendor' in fn or fn.endswith('.py') or fn.endswith('.pyc'):
                continue
            parts = fn.split('/')
            if any(token in parts[-1] for token in ['license', 'licence', 'copying', 'notice']):
                try:
                    p = dist.locate_file(f)
                    if p.is_file():
                        txt = p.read_text(encoding='utf-8', errors='replace').strip()
                        if len(txt) > 20 and txt not in seen_texts:
                            seen_texts.add(txt)
                            license_chunks.append((str(f), txt))
                except Exception:
                    pass

    if license_chunks:
        if len(license_chunks) == 1:
            license_content = license_chunks[0][1]
        else:
            formatted_chunks = []
            for fname, ftxt in license_chunks:
                formatted_chunks.append(f"[{fname}]\\n{ftxt}")
            license_content = "\\n\\n".join(formatted_chunks)
    else:
        lic_expr = (dist.metadata.get('License-Expression') or '').strip()
        lic_field = (dist.metadata.get('License') or '').strip()
        classifiers = [c.split('::')[-1].strip() for c in (dist.metadata.get_all('Classifier') or []) if 'License' in c]

        specific_license = None
        if lic_expr:
            specific_license = lic_expr
        elif lic_field and lic_field.upper() not in ('UNKNOWN', 'NONE', 'CUSTOM'):
            specific_license = lic_field
        elif classifiers:
            specific_license = ', '.join(classifiers)

        if not specific_license or 'standard open source' in specific_license.lower():
            raise RuntimeError(f"Could not determine valid license or extract license text for package '{name}'")
        license_content = f"License: {specific_license}"

    entry = f\"\"\"----------------------------------------------------------------
Package: {name} (version {ver})
----------------------------------------------------------------
{license_content}
\"\"\"
    notices.append(entry)

result = {
    "text": "\\n".join(notices),
    "covered": sorted(list(covered_pkgs)),
}
print(json.dumps(result))
"""
    res = subprocess.check_output([str(pkg_python), "-c", script], text=True)
    data = json.loads(res)
    return data["text"], set(data["covered"])


def find_file_in_payload(payload_dir: Path, filename: str) -> Path | None:
    """Searches for a filename in payload root and _internal subdirectories."""
    candidate = payload_dir / filename
    if candidate.is_file():
        return candidate
    internal_candidate = payload_dir / "_internal" / filename
    if internal_candidate.is_file():
        return internal_candidate
    for match in payload_dir.rglob(filename):
        if match.is_file():
            return match
    return None


def find_dir_in_payload(payload_dir: Path, rel_dir: str) -> Path | None:
    """Searches for a relative directory path in payload root or _internal."""
    candidate = payload_dir / rel_dir
    if candidate.is_dir():
        return candidate
    internal_candidate = payload_dir / "_internal" / rel_dir
    if internal_candidate.is_dir():
        return internal_candidate
    for match in payload_dir.rglob(Path(rel_dir).name):
        if match.is_dir() and str(match).replace("\\", "/").endswith(rel_dir.replace("\\", "/")):
            return match
    return None


def main() -> int:
    """Executes the engine packaging pipeline."""
    ver = sys.version_info
    if (ver.major, ver.minor, ver.micro) != (3, 14, 3):
        sys.stderr.write(
            f"ERROR: Expected Python 3.14.3 for desktop packaging, found {ver.major}.{ver.minor}.{ver.micro}\n"
        )
        return 1

    engine_root = Path(__file__).resolve().parents[1]
    packaging_dir = engine_root / "packaging"
    constraints_file = packaging_dir / "constraints.txt"
    if not constraints_file.is_file():
        sys.stderr.write(f"ERROR: Packaging constraints file missing: {constraints_file}\n")
        return 1

    spec_path = packaging_dir / "cad-copilot-engine.spec"
    if not spec_path.is_file():
        sys.stderr.write(f"ERROR: PyInstaller spec not found at {spec_path}\n")
        return 1

    dist_dir = engine_root / "dist"
    final_output_dir = dist_dir / "cad-copilot-engine"
    temp_staging_dir = dist_dir / ".cad-copilot-engine.staging"
    temp_build_dir = dist_dir / ".cad-copilot-engine.build"

    # 1. Clean previous temporary staging and build directories
    if temp_staging_dir.exists():
        shutil.rmtree(temp_staging_dir, ignore_errors=True)
    if temp_build_dir.exists():
        shutil.rmtree(temp_build_dir, ignore_errors=True)

    dist_dir.mkdir(parents=True, exist_ok=True)
    temp_staging_dir.mkdir(parents=True, exist_ok=True)
    temp_build_dir.mkdir(parents=True, exist_ok=True)

    # 2. Setup fresh isolated packaging environment with pinned binary constraints
    pkg_env_dir = dist_dir / ".cad-copilot-engine.pkg-env"
    pkg_python = setup_isolated_packaging_environment(
        pkg_env_dir,
        constraints_file,
    )

    # 3. Build project wheel inside the clean packaging environment using pinned tooling
    temp_wheel_dir = temp_build_dir / "wheel"
    temp_wheel_dir.mkdir(parents=True, exist_ok=True)
    print("Building cad-copilot wheel with pinned tooling inside packaging environment...")
    wheel_cmd = [
        str(pkg_python),
        "-m",
        "pip",
        "wheel",
        "--no-deps",
        "--no-build-isolation",
        "-w",
        str(temp_wheel_dir),
        str(engine_root),
    ]
    res_wheel = subprocess.run(wheel_cmd, cwd=str(engine_root))
    if res_wheel.returncode != 0:
        sys.stderr.write(f"ERROR: Failed to build wheel inside packaging environment: {res_wheel.returncode}\n")
        return res_wheel.returncode

    built_wheels = list(temp_wheel_dir.glob("cad_copilot-*.whl"))
    if not built_wheels:
        sys.stderr.write(f"ERROR: No wheel file built in {temp_wheel_dir}\n")
        return 1
    wheel_path = built_wheels[0]
    wheel_sha256 = compute_sha256(wheel_path)
    print(f"Built wheel: {wheel_path.name} (SHA-256: {wheel_sha256})")

    # 4. Install wheel into packaging environment and purge direct_url.json
    print(f"Installing {wheel_path.name} into packaging environment...")
    res_install = subprocess.run(
        [
            str(pkg_python),
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--force-reinstall",
            str(wheel_path),
        ]
    )
    if res_install.returncode != 0:
        sys.stderr.write(f"ERROR: Failed to install built wheel: {res_install.returncode}\n")
        return res_install.returncode

    site_packages = pkg_env_dir / "Lib" / "site-packages"
    if site_packages.is_dir():
        for dinfo in site_packages.glob("cad_copilot-*.dist-info"):
            (dinfo / "direct_url.json").unlink(missing_ok=True)

    # 5. Generate redistribution notices covering all bundled libraries
    print("Generating third-party redistribution notices from locked environment...")
    notices_text, covered_packages = generate_redistribution_notices(pkg_python)

    # 6. Invoke PyInstaller using pkg_python
    cmd = [
        str(pkg_python),
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(temp_staging_dir),
        "--workpath",
        str(temp_build_dir),
        str(spec_path),
    ]

    print(f"Executing: {' '.join(cmd)}")
    res = subprocess.run(cmd, cwd=str(engine_root))
    if res.returncode != 0:
        sys.stderr.write(f"ERROR: PyInstaller failed with exit code {res.returncode}\n")
        return res.returncode

    staged_payload = temp_staging_dir / "cad-copilot-engine"
    if not staged_payload.is_dir():
        sys.stderr.write(f"ERROR: Expected payload directory missing: {staged_payload}\n")
        return 1

    # 7. Validate executable
    exe_file = staged_payload / "cad-copilot-engine.exe"
    if not exe_file.is_file():
        sys.stderr.write(f"ERROR: Expected executable missing: {exe_file}\n")
        return 1

    # 8. Validate pywin32 system DLLs
    for dll_name in ["pythoncom314.dll", "pywintypes314.dll"]:
        found = find_file_in_payload(staged_payload, dll_name)
        if not found:
            sys.stderr.write(f"ERROR: Required pywin32 DLL missing from payload: {dll_name}\n")
            return 1
        root_dll = staged_payload / dll_name
        if not root_dll.exists():
            shutil.copy2(found, root_dll)

    # 9. Validate all 5 schema directories
    required_schemas = [
        "manifests/schemas",
        "example_catalog/resources",
        "plan_providers/schemas",
        "ipc/schemas",
        "batch/schemas",
    ]
    for schema_rel in required_schemas:
        found_dir = find_dir_in_payload(staged_payload, schema_rel)
        if not found_dir:
            sys.stderr.write(f"ERROR: Required resource directory missing from payload: {schema_rel}\n")
            return 1
        json_files = list(found_dir.glob("*.json"))
        if not json_files:
            sys.stderr.write(f"ERROR: Resource directory contains no JSON files: {schema_rel}\n")
            return 1

    # 10. Validate distribution metadata & sanitize local path leakage
    found_metadata = find_dir_in_payload(staged_payload, "cad_copilot-0.1.0.dist-info")
    if not found_metadata:
        sys.stderr.write("ERROR: Distribution metadata missing from payload: cad_copilot-0.1.0.dist-info\n")
        return 1

    direct_url = found_metadata / "direct_url.json"
    if direct_url.is_file():
        direct_url.unlink()

    for meta_file in found_metadata.glob("*"):
        if meta_file.is_file():
            try:
                content = meta_file.read_text(encoding="utf-8", errors="ignore")
                if "file:///" in content or str(engine_root) in content:
                    sys.stderr.write(f"ERROR: Local path leakage detected in metadata file: {meta_file.name}\n")
                    return 1
            except Exception:
                pass

    # 11. Validate notice coverage for all bundled distributions
    internal_dir = staged_payload / "_internal"
    if internal_dir.is_dir():
        for dinfo_dir in internal_dir.glob("*.dist-info"):
            dist_stem = dinfo_dir.name.split("-")[0].lower()
            if dist_stem != "cad_copilot" and dist_stem not in covered_packages:
                sys.stderr.write(f"ERROR: Bundled distribution lacks license notice coverage: {dist_stem}\n")
                return 1

    # 12. Write notices and inventory
    notices_path = staged_payload / "THIRD_PARTY_LICENSES.txt"
    notices_path.write_text(notices_text, encoding="utf-8")

    inventory: dict[str, dict[str, str | int]] = {}
    total_bytes = 0
    file_count = 0
    for root, _, files in os.walk(staged_payload):
        for f in files:
            full_path = Path(root) / f
            rel_path = full_path.relative_to(staged_payload).as_posix()
            size = full_path.stat().st_size
            total_bytes += size
            file_count += 1
            inventory[rel_path] = {
                "size": size,
                "sha256": compute_sha256(full_path),
            }

    inventory_path = staged_payload / "PACKAGE_INVENTORY.json"
    inventory_record = {
        "packageName": "cad-copilot-engine",
        "targetArch": "win_amd64",
        "pythonVersion": "3.14.3",
        "wheelSha256": wheel_sha256,
        "payloadFilesCount": file_count,
        "totalFilesCount": file_count + 1,
        "payloadSizeBytes": total_bytes,
        "manifestSelfExcluded": True,
        "files": inventory,
    }
    inventory_path.write_text(json.dumps(inventory_record, indent=2), encoding="utf-8")

    # 13. Checked rollback-safe replacement of final output directory
    print(f"Staging validated ({file_count} payload files). Replacing {final_output_dir}...")
    backup_dir = dist_dir / ".cad-copilot-engine.backup"
    if backup_dir.exists():
        shutil.rmtree(backup_dir, ignore_errors=True)

    has_existing = final_output_dir.exists()
    if has_existing:
        final_output_dir.rename(backup_dir)

    try:
        shutil.move(str(staged_payload), str(final_output_dir))
        if backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)
    except Exception as exc:
        sys.stderr.write(f"ERROR: Failed to move staged payload to final location: {exc}\n")
        if has_existing and backup_dir.exists():
            backup_dir.rename(final_output_dir)
        return 1

    # Clean temporary build and staging directories
    shutil.rmtree(temp_staging_dir, ignore_errors=True)
    shutil.rmtree(temp_build_dir, ignore_errors=True)

    print("Desktop engine packaging completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
