"""CAD Copilot desktop release packager and payload auditor.

Orchestrates Tauri NSIS bundle creation with dynamic path prefix remapping
to prevent workspace and developer profile leakage, followed by post-build
privacy and integrity audits of the output binary and installer artifacts.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path


def build_remap_flags(
    repo_root: Path,
    user_profile: Path,
    cargo_home: Path,
    rustup_home: Path,
) -> list[str]:
    """Builds Cargo --remap-path-prefix flags for repository and toolchain roots."""
    remap_targets = [
        (repo_root, "/cad-copilot"),
        (cargo_home, "/cargo"),
        (rustup_home, "/rustup"),
        (user_profile, "/user"),
    ]

    remap_flags: list[str] = []
    for path_obj, mapped in remap_targets:
        if not path_obj or not str(path_obj):
            continue
        variants = {str(path_obj), path_obj.as_posix()}
        drive_variants: set[str] = set()
        for v in variants:
            if len(v) >= 2 and v[1] == ":":
                drive_variants.add(v[0].lower() + v[1:])
                drive_variants.add(v[0].upper() + v[1:])
        variants.update(drive_variants)

        for v in variants:
            remap_flags.append(f"--remap-path-prefix={v}={mapped}")
            if "\\" in v:
                remap_flags.append(f"--remap-path-prefix=\\\\?\\{v}={mapped}")
            if "/" in v:
                remap_flags.append(f"--remap-path-prefix=//?/{v}={mapped}")

    return remap_flags


def collect_forbidden_tokens(
    repo_root: Path,
    user_profile: Path,
    username: str | None = None,
) -> list[tuple[bytes, str]]:
    """Derives forbidden path and identifier tokens dynamically from local roots."""
    tokens_str: list[str] = []

    if repo_root and str(repo_root):
        tokens_str.append(str(repo_root))
        tokens_str.append(repo_root.as_posix())
        # Workspace directory name without hardcoding workstation literals
        if repo_root.name and len(repo_root.name) > 3:
            tokens_str.append(repo_root.name)

    if user_profile and str(user_profile):
        tokens_str.append(str(user_profile))
        tokens_str.append(user_profile.as_posix())

    if username and len(username) >= 1:
        tokens_str.append(f"Users\\{username}")
        tokens_str.append(f"Users/{username}")
        tokens_str.append(f"\\Users\\{username}")
        tokens_str.append(f"/Users/{username}")

    # Deduplicate while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for t in tokens_str:
        if t not in seen:
            seen.add(t)
            deduped.append(t)

    # Encode every token in both UTF-8 and UTF-16LE representations
    forbidden: list[tuple[bytes, str]] = []
    for t in deduped:
        forbidden.append((t.encode("utf-8"), f"UTF-8 {t!r}"))
        forbidden.append((t.encode("utf-16le"), f"UTF-16LE {t!r}"))

    return forbidden


def audit_binary_privacy(
    binary_data: bytes,
    binary_name: str,
    forbidden_tokens: list[tuple[bytes, str]],
) -> list[str]:
    """Scans compiled binary data for leaked workstation or user tokens."""
    violations: list[str] = []
    for token_bytes, token_desc in forbidden_tokens:
        count = binary_data.count(token_bytes)
        if count > 0:
            violations.append(
                f"Found {count} occurrence(s) of forbidden token ({token_desc}) in {binary_name}"
            )
    return violations


def audit_metadata_privacy(
    staged_engine: Path,
    forbidden_tokens: list[tuple[bytes, str]],
) -> list[str]:
    """Audits staged engine dist-info metadata files for direct workstation paths."""
    violations: list[str] = []
    if not staged_engine.is_dir():
        return violations

    for meta_file in staged_engine.glob("_internal/**/*.dist-info/*"):
        if not meta_file.is_file():
            continue
        # Scan METADATA (extensionless) and common metadata extensions
        if meta_file.name == "METADATA" or meta_file.suffix.lower() in (
            ".json",
            ".txt",
            ".dist-info",
            ".entry_points",
            ".cfg",
        ):
            try:
                content = meta_file.read_bytes()
            except OSError as exc:
                violations.append(
                    f"Failed to read metadata file {meta_file.relative_to(staged_engine)}: {exc}"
                )
                continue

            for token_bytes, token_desc in forbidden_tokens:
                if token_bytes in content:
                    violations.append(
                        f"Workstation token ({token_desc}) leaked in staged metadata {meta_file.relative_to(staged_engine)}"
                    )
    return violations


def verify_nsis_installers(
    nsis_dir: Path,
    forbidden_tokens: list[tuple[bytes, str]],
) -> tuple[list[str], list[tuple[str, int, str]]]:
    """Verifies generated NSIS installer artifacts and scans uncompressed sections."""
    violations: list[str] = []
    verified_installers: list[tuple[str, int, str]] = []

    installers = list(nsis_dir.glob("*.exe"))
    if not installers:
        violations.append(f"No NSIS installer found in {nsis_dir}")
        return violations, verified_installers

    for inst in installers:
        try:
            inst_bytes = inst.read_bytes()
        except OSError as exc:
            violations.append(f"Failed to read installer {inst.name}: {exc}")
            continue

        size = len(inst_bytes)
        if size < 10 * 1024 * 1024:
            violations.append(
                f"Installer {inst.name} is unexpectedly small: {size} bytes (< 10 MB)"
            )

        sha = hashlib.sha256(inst_bytes).hexdigest()
        verified_installers.append((inst.name, size, sha))

        # Check uncompressed installer stub / header for path leakage
        for token_bytes, token_desc in forbidden_tokens:
            if token_bytes in inst_bytes:
                violations.append(
                    f"Workstation token ({token_desc}) found uncompressed in installer {inst.name}"
                )

    return violations, verified_installers


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    user_profile = Path(os.environ.get("USERPROFILE", "")).resolve()
    cargo_home = Path(os.environ.get("CARGO_HOME", user_profile / ".cargo")).resolve()
    rustup_home = Path(
        os.environ.get("RUSTUP_HOME", user_profile / ".rustup")
    ).resolve()
    username = os.environ.get("USERNAME")

    print("=" * 60)
    print("CAD Copilot -- Desktop Release Packager")
    print(f"Repository Root: {repo_root}")
    print(f"User Profile:    {user_profile}")
    print("=" * 60)

    # 1. Prepare dynamic path remapping flags
    remap_flags = build_remap_flags(repo_root, user_profile, cargo_home, rustup_home)

    env = os.environ.copy()
    env.pop("RUSTFLAGS", None)
    # Cargo uses CARGO_ENCODED_RUSTFLAGS with \x1f separator to prevent whitespace splitting
    env["CARGO_ENCODED_RUSTFLAGS"] = "\x1f".join(remap_flags)

    # 2. Invoke Tauri build
    print("\n[1/3] Building Tauri desktop application with NSIS bundle...")
    pnpm_cmd = "pnpm.cmd" if sys.platform == "win32" else "pnpm"
    build_args = [
        pnpm_cmd,
        "--filter",
        "@cad-copilot/desktop",
        "tauri",
        "build",
        "--config",
        "src-tauri/tauri.packaged.conf.json",
        "--features",
        "packaged-engine",
        "--bundles",
        "nsis",
    ]

    res = subprocess.run(build_args, cwd=repo_root, env=env, check=False)
    if res.returncode != 0:
        print(
            f"\n[ERROR] Tauri build failed with exit code {res.returncode}",
            file=sys.stderr,
        )
        return res.returncode

    # 3. Post-build payload audit
    print("\n[2/3] Auditing release binary and staged payload for path privacy...")
    release_bin = (
        repo_root
        / "desktop"
        / "src-tauri"
        / "target"
        / "release"
        / "cad-copilot-desktop.exe"
    )
    if not release_bin.is_file():
        print(
            f"\n[ERROR] Expected release binary not found at {release_bin}",
            file=sys.stderr,
        )
        return 1

    forbidden_tokens = collect_forbidden_tokens(repo_root, user_profile, username)

    violations: list[str] = []
    bin_data = release_bin.read_bytes()
    violations.extend(
        audit_binary_privacy(bin_data, release_bin.name, forbidden_tokens)
    )

    staged_engine = repo_root / "engine" / "dist" / "cad-copilot-engine"
    violations.extend(audit_metadata_privacy(staged_engine, forbidden_tokens))

    if violations:
        print("\n[ERROR] Payload privacy audit FAILED:", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        return 1

    print(
        "  [OK] Release binary completely free of workstation and user profile paths."
    )

    # 4. Verify NSIS installer
    print("\n[3/3] Inspecting and auditing generated NSIS installer...")
    nsis_dir = (
        repo_root / "desktop" / "src-tauri" / "target" / "release" / "bundle" / "nsis"
    )
    installer_violations, verified_installers = verify_nsis_installers(
        nsis_dir, forbidden_tokens
    )

    if installer_violations:
        print("\n[ERROR] NSIS installer verification FAILED:", file=sys.stderr)
        for iv in installer_violations:
            print(f"  - {iv}", file=sys.stderr)
        return 1

    for name, size, sha in verified_installers:
        size_mb = size / (1024 * 1024)
        print(f"  [OK] Installer: {name} ({size_mb:.2f} MB, SHA-256: {sha})")

    print("\n[SUCCESS] Desktop release packaging and payload verification complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
