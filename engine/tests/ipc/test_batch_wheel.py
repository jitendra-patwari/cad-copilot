"""Packaging and isolated-install verification tests for batch wheel distribution.

Proves:
1. Wheel build packages all three batch schemas byte-identical to canonical contracts.
2. Wheel entry_points.txt contains both cad-copilot-batch and cad-copilot-generate.
3. Wheel contains no tests, temporary files, or untracked repository artifacts.
4. Installed wheel loads and validates schemas in an isolated virtual environment without repository checkout.
5. Installed cad-copilot-batch console script functions in the isolated environment.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

from ipc.batch_progress import BATCH_FATAL_DIAGNOSTIC_BYTES, BATCH_FATAL_DIAGNOSTIC_MESSAGE

SCHEMA_FILENAMES = (
    "batch-request.schema.json",
    "batch-response.schema.json",
    "batch-manifest-v1.schema.json",
)


def _build_offline_wheel(engine_root: Path, wheel_dir: Path) -> Path:
    """Build cad-copilot wheel offline without build isolation."""
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "-w",
            str(wheel_dir),
            str(engine_root),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    wheels = list(wheel_dir.glob("*.whl"))
    assert len(wheels) == 1, f"Expected 1 wheel, found {wheels}"
    return wheels[0]


def test_batch_wheel_packaging_and_schema_inventory(tmp_path: Path) -> None:
    """Proves built wheel contains all batch schemas byte-identical to canonical contracts and clean entrypoints."""
    engine_root = Path(__file__).resolve().parents[2]
    repo_root = engine_root.parent
    canonical_schemas_dir = repo_root / "contracts" / "schemas" / "batch"
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()

    wheel_path = _build_offline_wheel(engine_root, wheel_dir)

    with zipfile.ZipFile(wheel_path) as z:
        names = z.namelist()

        # 1. Verify all 3 batch schemas are packaged and byte-identical to canonical contracts
        for schema_name in SCHEMA_FILENAMES:
            packaged_path = f"batch/schemas/{schema_name}"
            assert packaged_path in names, f"Missing {packaged_path} in wheel archive: {names}"

            wheel_bytes = z.read(packaged_path)
            canonical_bytes = (canonical_schemas_dir / schema_name).read_bytes()
            assert wheel_bytes == canonical_bytes, (
                f"Wheel schema {packaged_path} differs from canonical contract {schema_name}"
            )

        # 2. Verify entry_points.txt exposes both generate and batch CLI entry points
        ep_files = [n for n in names if n.endswith("entry_points.txt")]
        assert len(ep_files) == 1, f"Missing entry_points.txt in wheel: {names}"
        ep_text = z.read(ep_files[0]).decode("utf-8")
        assert "cad-copilot-batch = ipc.batch_stdio:main" in ep_text
        assert "cad-copilot-generate = ipc.stdio:main" in ep_text

        # 3. Verify clean package inventory and privacy protections
        forbidden_path_substrings = (
            "tests/",
            ".handoff",
            "fixtures",
            ".env",
            "__pycache__",
        )
        forbidden_extensions = (
            ".pyc",
            ".cmd",
            ".bat",
            ".ps1",
            ".dll",
            ".so",
            ".dylib",
            ".exe",
            ".key",
            ".pem",
            ".secret",
            ".pfx",
            ".tlb",
            ".pdb",
            ".pwd",
            ".chm",
            # CAD outputs
            ".par",
            ".psm",
            ".asm",
            ".dft",
            ".step",
            ".stp",
            ".stl",
            ".x_t",
            ".pdf",
            ".dxf",
        )

        for n in names:
            lower_name = n.lower()
            for sub in forbidden_path_substrings:
                assert sub not in lower_name, f"Forbidden path segment '{sub}' found in wheel member: {n}"
            for ext in forbidden_extensions:
                assert not lower_name.endswith(ext), f"Forbidden file extension '{ext}' found in wheel member: {n}"

        # 4. Content privacy scan: ensure text members contain no workstation paths or secret patterns
        text_extensions = (".py", ".json", ".txt", ".cfg", ".md")
        workstation_markers = (
            "c:\\users\\",
            "c:/users/",
            "e:\\14. cad copilot",
            "e:/14. cad copilot",
            "/home/",
            "/users/",
        )
        secret_markers = (
            "AIzaSy",
            "sk-ant-",
            "sk-proj-",
        )

        for n in names:
            if any(n.endswith(ext) for ext in text_extensions):
                content = z.read(n).decode("utf-8", errors="replace")
                lower_content = content.lower()
                for marker in workstation_markers:
                    assert marker not in lower_content, f"Workstation path marker '{marker}' leaked in wheel file {n}"
                for secret in secret_markers:
                    assert secret not in content, f"Secret pattern '{secret}' leaked in wheel file {n}"

        # 5. Verify all non-metadata packaged files map directly to git-tracked files under src/
        proc_git = subprocess.run(
            ["git", "ls-files", "src"],
            cwd=str(engine_root),
            capture_output=True,
            text=True,
            check=True,
        )
        tracked_src_files = {
            line.strip().replace("\\", "/").removeprefix("src/")
            for line in proc_git.stdout.splitlines()
            if line.strip().startswith("src/")
        }

        packaged_src_files = [n for n in names if ".dist-info" not in n]
        for n in packaged_src_files:
            assert n in tracked_src_files, f"Packaged file {n} is not tracked in git"
        assert len(packaged_src_files) == len(tracked_src_files), (
            f"Packaged source count ({len(packaged_src_files)}) does not match tracked source count ({len(tracked_src_files)})"
        )


def test_batch_wheel_isolated_installation_and_execution(tmp_path: Path) -> None:
    """Proves installed wheel loads schemas, executes batch API, and runs console script in isolated venv."""
    engine_root = Path(__file__).resolve().parents[2]
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()

    wheel_path = _build_offline_wheel(engine_root, wheel_dir)

    # 1. Create disposable isolated virtual environment
    venv_dir = tmp_path / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--system-site-packages", str(venv_dir)],
        capture_output=True,
        text=True,
        check=True,
    )
    venv_python = venv_dir / "Scripts" / "python.exe" if sys.platform == "win32" else venv_dir / "bin" / "python"

    # 2. Install wheel offline without dependencies or index access
    subprocess.run(
        [
            str(venv_python),
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-index",
            str(wheel_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    site_packages_matches = list(venv_dir.glob("**/site-packages"))
    assert len(site_packages_matches) == 1, f"Expected 1 site-packages directory, got {site_packages_matches}"
    site_dir = site_packages_matches[0]

    # 3. Verify isolated Python import and schema resolution outside repo
    verify_script = """
import sys
from pathlib import Path

site_dir = sys.argv[1]

# Verify imports originate strictly from the isolated site-packages location
import batch.schemas
import ipc.batch_contracts
assert str(Path(batch.schemas.__file__).resolve()).startswith(str(Path(site_dir).resolve()))
assert str(Path(ipc.batch_contracts.__file__).resolve()).startswith(str(Path(site_dir).resolve()))

# Verify all 3 schema validators load and operate in isolated install
req_val = batch.schemas.get_batch_request_validator()
resp_val = batch.schemas.get_batch_response_validator()
manifest_val = batch.schemas.get_batch_manifest_validator()
assert req_val is not None
assert resp_val is not None
assert manifest_val is not None

# Verify contracts decode, validate, and serialize
raw_req = b'{"contract_version":"1.0","request_id":"req-iso-01","kind":"batch_operation","operation":{"type":"export_3d","formats":["step"]},"input":{"root":"C:/in","files":["part.par"]},"output_root":"C:/out"}'
parsed, req_id = ipc.batch_contracts.decode_and_parse_request_json(raw_req)
assert req_id == "req-iso-01"
typed_req = ipc.batch_contracts.build_typed_batch_request(parsed)
assert typed_req.request_id == "req-iso-01"

resp = ipc.batch_contracts.build_rejected_batch_response("INVALID_SCHEMA", request_id=req_id)
ipc.batch_contracts.validate_response_payload(resp)
serialized = ipc.batch_contracts.serialize_response(resp)
assert b'"rejected"' in serialized

# Verify zero optional AI dependencies loaded
assert "google.genai" not in sys.modules
assert "openai" not in sys.modules
assert "anthropic" not in sys.modules

print("ISOLATED_BATCH_WHEEL_VERIFIED")
"""

    env = os.environ.copy()
    proc = subprocess.run(
        [str(venv_python), "-c", verify_script, str(site_dir)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0, f"Isolated verification failed:\nStdout: {proc.stdout}\nStderr: {proc.stderr}"
    assert "ISOLATED_BATCH_WHEEL_VERIFIED" in proc.stdout

    # 4. Verify installed cad-copilot-batch console entrypoint script
    entrypoint_candidates = [
        f for f in venv_dir.glob("**/cad-copilot-batch*") if f.is_file() and not f.name.endswith(".py")
    ]
    assert len(entrypoint_candidates) >= 1, f"Installed cad-copilot-batch entrypoint not found in {venv_dir}"
    installed_exe = entrypoint_candidates[0]

    # A) Bad arguments -> exit 1 with fatal diagnostic
    proc_fatal = subprocess.run(
        [str(installed_exe), "--bad-arg"],
        capture_output=True,
        cwd=str(tmp_path),
        env=env,
    )
    assert proc_fatal.returncode == 1
    assert proc_fatal.stdout == b""
    assert proc_fatal.stderr == BATCH_FATAL_DIAGNOSTIC_BYTES

    diag = json.loads(proc_fatal.stderr.decode("ascii").strip())
    assert diag == {
        "type": "diagnostic",
        "phase": "fatal",
        "message": BATCH_FATAL_DIAGNOSTIC_MESSAGE,
    }

    # B) Empty input -> rejected response with exit 0
    proc_empty = subprocess.run(
        [str(installed_exe)],
        input=b"",
        capture_output=True,
        cwd=str(tmp_path),
        env=env,
    )
    assert proc_empty.returncode == 0
    assert proc_empty.stdout.endswith(b"\n")
    resp_empty = json.loads(proc_empty.stdout.decode("utf-8"))
    assert resp_empty["status"] == "rejected"
    assert resp_empty["errors"][0]["code"] == "INVALID_SCHEMA"
