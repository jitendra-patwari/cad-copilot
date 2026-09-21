"""Unit tests for fixed desktop engine entrypoint dispatcher."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from desktop_entrypoint import configure_win32com_cache, main


def test_main_missing_args_returns_one(capsys: pytest.CaptureFixture[str]) -> None:
    code = main([])
    assert code == 1
    captured = capsys.readouterr()
    assert "Usage: cad-copilot-engine" in captured.err


def test_main_unknown_subcommand_returns_one(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["invalid_subcommand"])
    assert code == 1
    captured = capsys.readouterr()
    assert "Unknown subcommand: invalid_subcommand" in captured.err


def test_main_dispatches_generate() -> None:
    with patch("ipc.stdio.main", return_value=0) as mock_gen:
        code = main(["generate"])
        assert code == 0
        mock_gen.assert_called_once_with(argv=[])


def test_main_frozen_ai_bootstrap_failure_returns_one(capfd: pytest.CaptureFixture[str]) -> None:
    import builtins

    orig_import = builtins.__import__

    def mock_import(name: str, *args: object, **kwargs: object) -> object:
        if name.startswith("google.genai") or name == "google":
            raise ImportError("mocked import failure")
        return orig_import(name, *args, **kwargs)

    with patch("sys.frozen", True, create=True), patch("builtins.__import__", side_effect=mock_import):
        code = main(["generate"])
        assert code == 1
        captured = capfd.readouterr()
        assert '"phase":"fatal"' in captured.err or '"phase": "fatal"' in captured.err


def test_main_dispatches_batch() -> None:
    with patch("ipc.batch_stdio.main", return_value=0) as mock_batch:
        code = main(["batch"])
        assert code == 0
        mock_batch.assert_called_once_with(argv=[])


def test_configure_win32com_cache_first_run_creation(tmp_path: Path) -> None:
    test_local_appdata = tmp_path / "LocalAppData"
    with patch.dict("os.environ", {"LOCALAPPDATA": str(test_local_appdata)}):
        assert configure_win32com_cache() is True
        target = test_local_appdata / "io.github.jitendra-patwari.cad-copilot" / "gen_py"
        assert target.is_dir()
        if sys.platform == "win32":
            import win32com

            assert win32com.__gen_path__ == str(target)
            assert win32com.gen_py.__path__ == [str(target)]


def test_configure_win32com_cache_gencache_synchronization_and_install_tree_protection(
    tmp_path: Path,
) -> None:
    """Verifies that gencache uses application-configured path without modifying install tree."""
    test_local_appdata = tmp_path / "LocalAppData"
    with patch.dict("os.environ", {"LOCALAPPDATA": str(test_local_appdata)}):
        assert configure_win32com_cache() is True
        target = test_local_appdata / "io.github.jitendra-patwari.cad-copilot" / "gen_py"
        assert target.is_dir()

        if sys.platform == "win32":
            import win32com
            import win32com.client.gencache as gencache

            assert win32com.__gen_path__ == str(target)
            assert win32com.gen_py.__path__ == [str(target)]

            gen_path = gencache.GetGeneratePath()
            assert gen_path == str(target)

            # First-run creation proof outside package
            init_file = target / "__init__.py"
            assert init_file.is_file()

            # Install-tree immutability proof: packaged dist directory is untouched
            pkg_dist = Path(__file__).resolve().parent.parent.parent / "dist" / "cad-copilot-engine"
            if pkg_dist.is_dir():
                pkg_gen_py = pkg_dist / "gen_py"
                assert not pkg_gen_py.exists(), "gen_py directory must not be created inside packaged install tree"


def test_configure_win32com_cache_unwritable_fails_closed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Point candidate paths to non-creatable file paths
    fake_file = tmp_path / "blocker_file"
    fake_file.write_text("not a directory")
    with patch.dict("os.environ", {"LOCALAPPDATA": str(fake_file), "TEMP": str(fake_file), "TMP": str(fake_file)}):
        if sys.platform == "win32":
            result = configure_win32com_cache()
            assert result is False
            captured = capsys.readouterr()
            assert "Failed to initialize application COM cache in user profile" in captured.err

            code = main(["generate"])
            assert code == 1


def test_configure_win32com_cache_gencache_mismatch_fails_closed(tmp_path: Path) -> None:
    test_local_appdata = tmp_path / "LocalAppData"
    with patch.dict("os.environ", {"LOCALAPPDATA": str(test_local_appdata), "TEMP": "", "TMP": ""}):
        if sys.platform == "win32":
            with patch("win32com.client.gencache.GetGeneratePath", return_value="C:\\unexpected\\gen_py"):
                assert configure_win32com_cache() is False
