#!/usr/bin/env python3
"""Run the native forwarding experiment against a disposable Linux sshd (Docker)."""

import argparse
import asyncio
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path


# No project directory, host .ssh, Docker socket or SSH agent enters the image.
IMAGE = """FROM python:3.12.14-bookworm
RUN apt-get -qq update && apt-get -qq install -y --no-install-recommends openssh-server \\
    && rm -rf /var/lib/apt/lists/* /etc/ssh/ssh_host_* \\
    && mkdir -p /run/sshd && useradd -m impfixture && passwd -d impfixture
COPY relay /tmp/relay
ARG WEBSOCKETS
RUN pip -q install --no-cache-dir /tmp/relay "websockets==$WEBSOCKETS" && rm -rf /tmp/relay
"""
SERVER = """ListenAddress 0.0.0.0
Port 2222
HostKey /run/imp-host
AuthorizedKeysFile /run/imp-authorized
AllowUsers impfixture
PubkeyAuthentication yes
AuthenticationMethods publickey
PasswordAuthentication no
KbdInteractiveAuthentication no
UsePAM no
AllowTcpForwarding local
PermitOpen 127.0.0.1:8787
AllowAgentForwarding no
X11Forwarding no
MaxSessions 0
"""


def docker(*args, data=None, timeout=60):
    return (
        subprocess.run(
            ["docker", *args],
            input=data,
            stdout=subprocess.PIPE,
            check=True,
            timeout=timeout,
        )
        .stdout.decode()
        .strip()
    )


def build_image(project):
    with (project / "uv.lock").open("rb") as file:
        version = next(
            p["version"]
            for p in tomllib.load(file)["package"]
            if p["name"] == "websockets"
        )
    with tempfile.TemporaryDirectory(prefix="imp-image-") as directory:
        # Stage only public package inputs: never send a checkout or a client's key to Docker.
        context = Path(directory)
        package = context / "relay"
        shutil.copytree(
            project / "src",
            package / "src",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        for name in ("pyproject.toml", "README.md"):
            shutil.copy(project / name, package)
        (context / "Dockerfile").write_text(IMAGE)
        return docker(
            "build",
            "-q",
            "--build-arg",
            f"WEBSOCKETS={version}",
            str(context),
            timeout=300,
        )


async def real_relay(name, client, method):
    backend = await asyncio.create_subprocess_exec(
        "docker",
        "exec",
        "-i",
        name,
        "python3",
        "-u",
        "/run/test-guided-relay.py",
        "backend",
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(60):
            if (await backend.stdout.readline()).strip() != b"READY 8787":
                raise RuntimeError("fixture relay did not become ready")
            await asyncio.to_thread(
                subprocess.run,
                [*client, "--protocol-only", method],
                check=True,
                timeout=45,
            )
            output, _ = await backend.communicate(b"stop\n")
            if backend.returncode or b"PASS" not in output:
                raise RuntimeError("fixture producer did not confirm delivery")
            print(output.decode().strip(), flush=True)
    finally:
        if backend.returncode is None:
            backend.kill()
            await backend.wait()


def published_port(name, container_port):
    host, number = docker("port", name, f"{container_port}/tcp").split(":")
    if host != "127.0.0.1":
        raise RuntimeError("fixture port not restricted to loopback")
    return int(number)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "harness", type=Path, help="native runtime-ownership-harness executable"
    )
    args = parser.parse_args()
    harness = args.harness.resolve(strict=True)
    driver = Path(__file__).with_name("test-guided-forward.py").resolve(strict=True)
    name = "imp-guided-" + secrets.token_hex(8)
    image = build_image(driver.parents[1] / "services" / "relay")
    # Only SSH and the synthetic test controller are published, both loopback-only.
    docker(
        "run",
        "--rm",
        "--init",
        "--detach",
        "--name",
        name,
        "-p",
        "127.0.0.1::2222",
        "-p",
        "127.0.0.1::8790",
        image,
        "sleep",
        "600",
    )
    try:
        with tempfile.TemporaryDirectory(prefix="imp-guided-") as temp:
            root = Path(temp)
            keygen = (
                Path(os.environ["SystemRoot"]) / "System32/OpenSSH/ssh-keygen.exe"
                if os.name == "nt"
                else shutil.which("ssh-keygen")
            )
            if not keygen:
                raise RuntimeError("OpenSSH ssh-keygen required")
            identities = [root / label for label in ("identity", "denied")]
            for path in identities:
                subprocess.run(
                    [keygen, "-q", "-t", "ed25519", "-N", "", "-f", path],
                    check=True,
                    timeout=15,
                )
            authorized = "".join(
                policy + " " + key.with_suffix(".pub").read_text().strip() + "\n"
                for key, policy in zip(
                    identities,
                    (
                        'restrict,port-forwarding,permitopen="127.0.0.1:8787"',
                        "restrict",
                    ),
                )
            )
            docker(
                "exec",
                "-i",
                name,
                "sh",
                "-ec",
                "cat >/run/imp-authorized",
                data=authorized.encode(),
            )
            docker(
                "exec",
                "-i",
                name,
                "sh",
                "-ec",
                "cat >/run/imp-sshd.conf",
                data=SERVER.encode(),
            )
            docker(
                "exec",
                name,
                "ssh-keygen",
                "-q",
                "-t",
                "ed25519",
                "-N",
                "",
                "-f",
                "/run/imp-host",
            )
            host_key = root / "host.pub"
            # Pin the generated server key through the local container control plane, not the network.
            host_key.write_text(docker("exec", name, "cat", "/run/imp-host.pub") + "\n")
            docker("cp", str(driver), name + ":/run/test-guided-forward.py")
            docker(
                "cp",
                str(driver.with_name("test-guided-relay.py")),
                name + ":/run/test-guided-relay.py",
            )
            docker("exec", name, "/usr/sbin/sshd", "-f", "/run/imp-sshd.conf")
            ssh_port = published_port(name, 2222)
            client = [
                sys.executable,
                driver,
                "--harness",
                harness,
                "--ssh-host",
                "127.0.0.1",
                "--ssh-user",
                "impfixture",
                "--ssh-port",
                str(ssh_port),
                "--identity",
                identities[0],
                "--host-key",
                host_key,
            ]
            for method in ("warm", "guided"):
                asyncio.run(real_relay(name, client, method))
            docker(
                "exec",
                "-d",
                name,
                "python3",
                "/run/test-guided-forward.py",
                "relay",
                "--control-port",
                "8790",
            )
            control_port = published_port(name, 8790)
            deadline = time.monotonic() + 10
            while True:
                try:
                    with socket.create_connection(
                        ("127.0.0.1", control_port), timeout=1
                    ) as control:
                        control.sendall(b"stats\n")
                        with control.makefile("rb") as reply:
                            if json.loads(reply.readline(1024)).get("active") != 0:
                                raise RuntimeError("unexpected relay fixture state")
                    break
                except (OSError, ValueError):
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.05)
            result = subprocess.run(
                [
                    *client,
                    "--denied-identity",
                    identities[1],
                    "--relay-control",
                    f"127.0.0.1:{control_port}",
                ],
                check=False,
                timeout=360,
            )
            raise SystemExit(result.returncode)
    finally:
        docker("rm", "-f", name)


if __name__ == "__main__":
    main()
