from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "test-installer-connections.py"


def load_harness() -> ModuleType:
    spec = importlib.util.spec_from_file_location("installer_connections", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


harness = load_harness()


def bundle(root: Path, version: str) -> Path:
    path = root / version
    path.mkdir()
    (path / "VERSION").write_text(version + "\n")
    return path


def test_legacy_version_is_lowest_free_and_avoids_reserved(tmp_path: Path) -> None:
    assert harness.legacy_version() == "0.3.2+legacy.fixture.0"
    a, b = bundle(tmp_path, "0.0.0+dev.a"), bundle(tmp_path, "0.3.2")
    assert harness.legacy_version(a, b) == "0.3.2+legacy.fixture.0"
    taken = [bundle(tmp_path, f"0.3.2+legacy.fixture.{n}") for n in (0, 2)]
    assert harness.legacy_version(a, b, *taken) == "0.3.2+legacy.fixture.1"


class Double:
    def __init__(self, tmp_path: Path) -> None:
        self.units = tmp_path / "units"
        self.units.mkdir()
        self.script = tmp_path / "systemctl"
        self.script.write_text(harness.SYSTEMCTL)
        self.script.chmod(0o755)

    def run(self, *args: str, **env: str) -> int:
        full = {**os.environ, "IMP_TEST_UNITS": str(self.units), **env}
        return subprocess.run([str(self.script), *args], env=full, capture_output=True).returncode

    def has(self, kind: str, unit: str) -> bool:
        return (self.units / f"{kind}-{unit}").exists()


def test_systemctl_double_state_transitions(tmp_path: Path) -> None:
    double = Double(tmp_path)
    unit = "imp-relay.service"
    assert double.run("--user", "is-active", "--quiet", unit) == 3
    assert double.run("--user", "enable", "--now", unit) == 0
    assert double.has("enabled", unit) and double.has("active", unit)
    assert double.run("--user", "is-active", "--quiet", unit) == 0
    assert double.run("--user", "stop", unit) == 0
    assert not double.has("active", unit) and double.run("is-enabled", unit) == 0
    assert double.run("--user", "disable", unit) == 0
    assert double.run("is-enabled", unit) == 1
    assert (double.units / "calls.log").read_text().splitlines()[:2] == [
        "is-active --quiet imp-relay.service",
        "enable --now imp-relay.service",
    ]


def test_systemctl_double_failed_restart_clears_active_once(tmp_path: Path) -> None:
    double = Double(tmp_path)
    unit = "imp-relay.service"
    double.run("enable", "--now", unit)
    assert double.run("restart", unit, IMP_TEST_FAIL_RESTART=unit) == 1
    assert not double.has("active", unit)
    assert (double.units / "failure-record").read_text() == f"{unit} inactive\n"
    assert double.run("restart", unit, IMP_TEST_FAIL_RESTART=unit) == 0
    assert double.has("active", unit)


def test_systemctl_double_signals_its_parent(tmp_path: Path) -> None:
    double = Double(tmp_path)
    env = {
        **os.environ,
        "IMP_TEST_UNITS": str(double.units),
        "IMP_TEST_SIGNAL_PHASE": "feed",
        "IMP_TEST_SIGNAL": "SIGINT",
    }
    script = 'trap "exit 42" INT; "$0" restart imp-feed.service; sleep 1'
    done = subprocess.run(["sh", "-c", script, str(double.script)], env=env, capture_output=True)
    assert done.returncode == 42
    assert double.has("active", "imp-feed.service")


@pytest.mark.skipif(os.getuid() == 0, reason="installer must run as the target non-root user")
def test_tinyfugue_startup_onboarding(tmp_path: Path) -> None:
    """Exercise real install.sh without building the frozen runtime."""
    root = tmp_path / "bundle"
    root.mkdir()
    shutil.copyfile(SCRIPT.parents[1] / "deploy/install.sh", root / "install.sh")
    (root / "install.sh").chmod(0o755)
    (root / "VERSION").write_text("0.0.0+startup.fixture\n")
    (root / "capture.tf").write_text("; capture payload fixture\n")
    (root / "bin").mkdir()
    runtime = root / "bin/imp-server"
    runtime.write_text("#!/bin/sh\nexit 0\n")
    runtime.chmod(0o755)
    (root / "systemd").mkdir()
    for name in ("relay", "feed", "gateway"):
        (root / f"systemd/imp-{name}.service").write_text("[Service]\nExecStart=/bin/true\n")
    (root / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(root)}\n"
            for path in sorted(root.rglob("*"))
            if path.is_file()
        )
    )
    harness.installer_shims(tmp_path)
    harness.startup_cases(tmp_path, root, guarded=False)
