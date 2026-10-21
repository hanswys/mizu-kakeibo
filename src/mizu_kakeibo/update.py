"""Tell the user when the installed copy is behind the main branch it was built from."""

from __future__ import annotations

import json
import subprocess
from importlib import resources
from pathlib import Path

BUILD_INFO = "build_info.json"


def update_commands(info: dict[str, str] | None = None) -> list[str] | None:
    """Return the update commands when origin/main has commits the installed copy lacks.

    An editable or checkout copy has no build information, and anything that cannot be
    checked quickly and locally, such as a missing checkout, says nothing.
    """
    if info is None:
        try:
            info = json.loads(resources.files("mizu_kakeibo").joinpath(BUILD_INFO).read_text())
        except (OSError, ValueError):
            return None
    source, commit = info["source"], info["commit"]

    def git(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", source, *arguments], capture_output=True, text=True, timeout=5
        )

    try:
        main = git("rev-parse", "--verify", "--quiet", "origin/main^{commit}").stdout.strip()
        if not main or main == commit:
            return None
        if git("merge-base", "--is-ancestor", commit, main).returncode != 0:
            return None
    except (OSError, subprocess.SubprocessError):
        return None
    home = str(Path.home())
    checkout = "~" + source[len(home) :] if source.startswith(home + "/") else source
    return [
        f"git -C {checkout} pull --ff-only",
        f"uv tool install --reinstall {checkout}",
    ]
