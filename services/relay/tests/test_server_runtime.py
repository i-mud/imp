from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import pytest

from imp_relay import direct_edge, server_runtime
from imp_relay.external_env import external_env


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["/r/bin/imp-server"],
        ["/r/bin/imp-server", "nope"],
        ["/r/bin/other", "relay"],
        ["/r/bin/imp-bundle-check", "/x"],
        ["/r/bin/imp-server", "bundle-check"],
        ["/r/bin/imp-server", "runtime-check", "1", "2"],
    ],
)
def test_unknown_invocations_are_rejected(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        server_runtime.main({}, argv)
    assert exc.value.code != 0


@pytest.mark.parametrize(
    "argv", [["/r/bin/imp-action-consumer", "--help"], ["/r/bin/imp-server", "action-consumer", "--help"]]
)
def test_dispatch_preserves_role_arguments(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["test"])
    invoked: list[list[str]] = []

    def consume() -> None:
        invoked.append(sys.argv.copy())

    with pytest.raises(SystemExit) as exc:
        server_runtime.main({"action-consumer": consume}, argv)
    assert exc.value.code is None
    assert invoked == [["imp-action-consumer", "--help"]]


@pytest.mark.parametrize(
    "argv", [["/r/bin/imp-feed-watch", "--help"], ["/r/bin/imp-server", "feed-watch", "--help"]]
)
def test_feed_watch_dispatch_preserves_arguments(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["test"])
    invoked: list[list[str]] = []

    def watch() -> None:
        invoked.append(sys.argv.copy())

    with pytest.raises(SystemExit) as exc:
        server_runtime.main({"feed-watch": watch}, argv)
    assert exc.value.code is None
    assert invoked == [["imp-feed-watch", "--help"]]


def _bundle(root: Path) -> None:
    (root / "bin").mkdir(parents=True)
    names = ["VERSION", "bin/imp-server"]
    for name in names:
        (root / name).write_text(name)
    manifest = [f"{hashlib.sha256(name.encode()).hexdigest()}  {name}" for name in names]
    (root / "SHA256SUMS").write_text("\n".join(manifest) + "\n")


def test_bundle_check_rejects_unlisted_missing_and_symlink(tmp_path: Path) -> None:
    _bundle(tmp_path)
    server_runtime.bundle_check(tmp_path)  # control: the exact inventory passes
    (tmp_path / "bin" / "extra").write_text("x")
    with pytest.raises(SystemExit, match="extra=\\['bin/extra'\\]"):
        server_runtime.bundle_check(tmp_path)
    (tmp_path / "bin" / "extra").unlink()
    (tmp_path / "VERSION").unlink()
    with pytest.raises(SystemExit, match="missing=\\['VERSION'\\]"):
        server_runtime.bundle_check(tmp_path)
    (tmp_path / "VERSION").symlink_to("bin/imp-server")
    with pytest.raises(SystemExit, match="symlink: VERSION"):
        server_runtime.bundle_check(tmp_path)


@pytest.mark.parametrize("name", ["../x", "/abs", "SHA256SUMS"])
def test_bundle_check_rejects_unsafe_manifest_names(tmp_path: Path, name: str) -> None:
    _bundle(tmp_path)
    (tmp_path / "SHA256SUMS").write_text(f"{'0' * 64}  {name}\n")
    with pytest.raises(SystemExit, match="invalid SHA256SUMS entry"):
        server_runtime.bundle_check(tmp_path)


def test_bundle_check_rejects_duplicates(tmp_path: Path) -> None:
    _bundle(tmp_path)
    (tmp_path / "SHA256SUMS").write_text(f"{'0' * 64}  VERSION\n" * 2)
    with pytest.raises(SystemExit, match="duplicate"):
        server_runtime.bundle_check(tmp_path)


def _release(bin_dir: Path) -> None:
    (bin_dir / server_runtime.RUNTIME_DIR).mkdir(parents=True)
    for name in (server_runtime.ENTRYPOINT, *server_runtime.ALIASES):
        (bin_dir / name).write_text("x")
        (bin_dir / name).chmod(0o755)


GOOD = {"imp-relay": "1.2.3", "imp-adapter": "1.2.3", "imp-tinyfugue": "1.2.3", "websockets": "16.0"}


@pytest.mark.parametrize(
    "installed",
    [GOOD | {"imp-adapter": "1.2.4"}, GOOD | {"websockets": "17.0"}],
)
def test_runtime_check_rejects_wrong_versions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, installed: dict[str, str]
) -> None:
    _release(tmp_path)
    monkeypatch.setattr(server_runtime, "version", GOOD.__getitem__)
    server_runtime.runtime_check("1.2.3", tmp_path)  # control: the good release passes
    monkeypatch.setattr(server_runtime, "version", installed.__getitem__)
    with pytest.raises(SystemExit, match="expected"):
        server_runtime.runtime_check("1.2.3", tmp_path)


@pytest.mark.parametrize("name", ["imp-feed", "imp-feed-watch"])
def test_runtime_check_rejects_missing_or_symlinked_entrypoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    _release(tmp_path)
    monkeypatch.setattr(server_runtime, "version", GOOD.__getitem__)
    alias = tmp_path / name
    alias.unlink()
    with pytest.raises(SystemExit, match=name):
        server_runtime.runtime_check("1.2.3", tmp_path)
    alias.symlink_to("imp-server")
    with pytest.raises(SystemExit, match=name):
        server_runtime.runtime_check("1.2.3", tmp_path)


@pytest.mark.parametrize("invoked", ["imp-server", "imp-direct-edge"])
def test_frozen_sudo_reexecs_absolute_imp_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, invoked: str
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "bin" / invoked))
    assert direct_edge._sudo_command() == [str(tmp_path / "bin" / "imp-server"), "direct-edge"]


def test_external_env_restores_loader_path_only_when_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LD_LIBRARY_PATH", "/bundle")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/host")
    assert external_env()["LD_LIBRARY_PATH"] == "/bundle"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert external_env()["LD_LIBRARY_PATH"] == "/host"
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG")
    assert "LD_LIBRARY_PATH" not in external_env()
    assert os.environ["LD_LIBRARY_PATH"] == "/bundle"
