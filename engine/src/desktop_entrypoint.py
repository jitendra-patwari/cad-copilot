"""CAD Copilot fixed desktop engine entrypoint.

Dispatches execution to either `generate` (ipc.stdio) or `batch` (ipc.batch_stdio)
based on the first command-line argument. Configures application-owned gen_py cache
paths for win32com before dispatch to protect read-only install trees.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def configure_win32com_cache() -> bool:
    """Configures win32com __gen_path__ to an application-owned writable directory.

    Guarantees gen_py is never generated beneath the read-only packaged install tree.
    Returns True if cache was successfully prepared or non-Windows; False on failure.
    """
    if sys.platform != "win32":
        return True

    candidates: list[Path] = []
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        candidates.append(Path(local_appdata) / "io.github.jitendra-patwari.cad-copilot" / "gen_py")

    temp_dir = os.environ.get("TEMP") or os.environ.get("TMP")
    if temp_dir:
        candidates.append(Path(temp_dir) / "io.github.jitendra-patwari.cad-copilot" / "gen_py")

    for target_dir in candidates:
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            probe_file = target_dir / f".write_probe_{os.getpid()}"
            probe_file.write_text("probe", encoding="utf-8")
            probe_file.unlink(missing_ok=True)

            import win32com
            import win32com.client.gencache as gencache

            target_str = str(target_dir)
            win32com.__gen_path__ = target_str
            gencache.__gen_path__ = target_str
            if hasattr(win32com, "gen_py"):
                win32com.gen_py.__path__ = [target_str]
            if "win32com.gen_py" in sys.modules:
                sys.modules["win32com.gen_py"].__path__ = [target_str]

            active_gen_path = gencache.GetGeneratePath()
            if not active_gen_path or Path(active_gen_path).resolve() != target_dir.resolve():
                continue

            return True
        except Exception:
            continue

    sys.stderr.write(
        '{"type":"diagnostic","phase":"fatal","message":"Failed to initialize application COM cache in user profile."}\n'
    )
    return False


def main(argv: list[str] | None = None) -> int:
    """Fixed entrypoint dispatching generate or batch workflows."""
    args = sys.argv[1:] if argv is None else argv

    if not args:
        sys.stderr.write("Usage: cad-copilot-engine <generate|batch>\n")
        return 1

    subcommand = args[0].lower().strip()
    sub_args = args[1:]

    if not configure_win32com_cache():
        return 1

    if subcommand == "generate":
        from ipc.stdio import main as generate_main

        return int(generate_main(argv=sub_args))
    elif subcommand == "batch":
        from ipc.batch_stdio import main as batch_main

        return int(batch_main(argv=sub_args))
    else:
        sys.stderr.write(f"Unknown subcommand: {subcommand}. Expected 'generate' or 'batch'.\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
