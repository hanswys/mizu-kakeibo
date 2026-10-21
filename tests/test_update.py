from __future__ import annotations

import subprocess
from pathlib import Path

from mizu_kakeibo.update import update_commands


def git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments], capture_output=True, check=True, text=True
    ).stdout.strip()


def commit(repository: Path, message: str) -> str:
    git(
        repository,
        "-c",
        "user.name=t",
        "-c",
        "user.email=t@t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        message,
    )
    return git(repository, "rev-parse", "HEAD")


def checkout(tmp_path: Path) -> tuple[Path, str, str]:
    repository = tmp_path / "mizu-kakeibo"
    repository.mkdir()
    git(repository, "init", "-q")
    installed = commit(repository, "installed")
    merged = commit(repository, "merged")
    git(repository, "update-ref", "refs/remotes/origin/main", merged)
    return repository, installed, merged


def test_update_commands_when_main_has_moved_on(tmp_path) -> None:
    repository, installed, _ = checkout(tmp_path)
    assert update_commands({"source": str(repository), "commit": installed}) == [
        f"git -C {repository} pull --ff-only",
        f"uv tool install --reinstall {repository}",
    ]


def test_no_update_commands_when_current_or_unknown(tmp_path) -> None:
    repository, installed, merged = checkout(tmp_path)
    assert update_commands({"source": str(repository), "commit": merged}) is None
    git(repository, "update-ref", "refs/remotes/origin/main", installed)
    assert update_commands({"source": str(repository), "commit": merged}) is None
    assert update_commands({"source": str(repository), "commit": "0" * 40}) is None
    assert update_commands({"source": str(tmp_path / "missing"), "commit": installed}) is None


def test_no_update_commands_without_build_information() -> None:
    assert update_commands() is None
