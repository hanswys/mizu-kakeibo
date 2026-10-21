"""Record which commit an installed copy was built from, for the update hint."""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class BuildInfoHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict) -> None:
        if self.target_name != "wheel" or version != "standard":
            return
        try:
            commit = subprocess.run(
                ["git", "-C", self.root, "rev-parse", "HEAD"],
                capture_output=True,
                check=True,
                text=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return
        info = Path(tempfile.mkdtemp()) / "build_info.json"
        info.write_text(json.dumps({"source": self.root, "commit": commit}), encoding="utf-8")
        build_data["force_include"][str(info)] = "mizu_kakeibo/build_info.json"
