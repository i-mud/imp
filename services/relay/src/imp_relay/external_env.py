"""Environment for external system binaries launched from the frozen server runtime."""

from __future__ import annotations

import os
import sys


def external_env() -> dict[str, str]:
    # The PyInstaller bootloader prepends its bundle dir to LD_LIBRARY_PATH; system binaries must not load it.
    env = os.environ.copy()
    if getattr(sys, "frozen", False):
        original = env.pop("LD_LIBRARY_PATH_ORIG", None)
        if original is None:
            env.pop("LD_LIBRARY_PATH", None)
        else:
            env["LD_LIBRARY_PATH"] = original
    return env
