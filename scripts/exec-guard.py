#!/usr/bin/env python3
"""Test-only launcher: deny host Python/pip execution to an installed Imp process tree.

Usage: exec-guard.py [--allow DIR]... -- COMMAND [ARGS...]
       exec-guard.py --abi

Linux x86_64 only (stdlib ctypes, no dependencies).  Installs a Landlock ruleset that
handles ONLY the EXECUTE right (reads, writes, network and everything else stay
unrestricted), sets no_new_privs, then execs COMMAND; the restriction is inherited by every
descendant and cannot be lifted.  Execution is allowed for

  * everything beneath each --allow DIR (frozen bundle, fixture HOME),
  * ELF files in the ordinary system program directories and the dynamic loader,
    EXCLUDING anything named python*/pip* (by link name or resolved target) and any inode
    shared with such a file.  System bin directories are never allowed wholesale.

Exit 126 = exec denied by the kernel (EACCES), 127 = other exec failure, 1 = guard
unavailable/unsupported (never a silent fallback).  The developer harness never runs
restricted: only this process, after it started, and its exec'd command.
"""

import ctypes
import errno
import os
import platform
import re
import sys
from pathlib import Path

SYSTEM_DIRS = ("/usr/bin", "/bin", "/usr/sbin", "/sbin", "/usr/local/bin", "/usr/local/sbin")
LOADERS = ("/lib64/ld-linux-x86-64.so.2", "/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2")
DENIED = re.compile(r"^(python|pip)")
SYS_CREATE, SYS_ADD, SYS_RESTRICT = 444, 445, 446  # x86_64 syscall numbers
ACCESS_EXECUTE = 1
RULE_PATH_BENEATH = 1
PR_SET_NO_NEW_PRIVS = 38


class PathBeneath(ctypes.Structure):
    _pack_ = 1
    _fields_ = (("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32))


def libc():
    if sys.platform != "linux" or platform.machine() != "x86_64":
        sys.exit("exec-guard: requires Linux x86_64")
    return ctypes.CDLL(None, use_errno=True)


def abi_version(lib):
    version = lib.syscall(SYS_CREATE, None, ctypes.c_size_t(0), ctypes.c_uint32(1))
    if version < 1:
        sys.exit(f"exec-guard: Landlock unavailable: {os.strerror(ctypes.get_errno())}")
    return version


def elf(path):
    try:
        with open(path, "rb") as handle:
            return handle.read(4) == b"\x7fELF"
    except OSError:
        return False


def system_programs():
    """Real paths of non-Python ELF programs in the system dirs (symlinks resolved)."""
    candidates = [
        os.path.join(directory, name)
        for directory in SYSTEM_DIRS
        if os.path.isdir(directory)
        for name in os.listdir(directory)
    ]
    real = {path: os.path.realpath(path) for path in candidates}
    candidates = [path for path in candidates if os.path.isfile(real[path])]
    denied = {
        (st.st_dev, st.st_ino)
        for path in candidates
        if DENIED.match(os.path.basename(path)) or DENIED.match(os.path.basename(real[path]))
        for st in [os.stat(real[path])]
    }
    allowed = {os.path.realpath(path) for path in LOADERS if os.path.exists(path)}
    for path in candidates:
        st = os.stat(real[path])
        if (st.st_dev, st.st_ino) not in denied and elf(real[path]):
            allowed.add(real[path])
    return sorted(allowed)


def restrict(lib, allow_dirs):
    fd = lib.syscall(
        SYS_CREATE, ctypes.byref(ctypes.c_uint64(ACCESS_EXECUTE)), ctypes.c_size_t(8), ctypes.c_uint32(0)
    )
    if fd < 0:
        sys.exit(f"exec-guard: landlock_create_ruleset: {os.strerror(ctypes.get_errno())}")
    for path in [*allow_dirs, *system_programs()]:
        parent = os.open(path, os.O_PATH | os.O_CLOEXEC)
        try:
            rule = PathBeneath(ACCESS_EXECUTE, parent)
            if lib.syscall(SYS_ADD, fd, RULE_PATH_BENEATH, ctypes.byref(rule), ctypes.c_uint32(0)) != 0:
                sys.exit(f"exec-guard: landlock_add_rule {path}: {os.strerror(ctypes.get_errno())}")
        finally:
            os.close(parent)
    if lib.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        sys.exit(f"exec-guard: no_new_privs: {os.strerror(ctypes.get_errno())}")
    if lib.syscall(SYS_RESTRICT, fd, ctypes.c_uint32(0)) != 0:
        sys.exit(f"exec-guard: landlock_restrict_self: {os.strerror(ctypes.get_errno())}")
    os.close(fd)


def main(argv):
    lib = libc()
    if argv == ["--abi"]:
        print(abi_version(lib))
        return
    allow = []
    while argv and argv[0] == "--allow":
        allow.append(str(Path(argv[1]).resolve()))
        argv = argv[2:]
    if not argv or argv[0] != "--" or len(argv) < 2:
        sys.exit(__doc__.split("\n\n")[0] + "\nusage: exec-guard.py [--allow DIR]... -- COMMAND [ARGS...]")
    command = argv[1:]
    abi_version(lib)
    restrict(lib, allow)
    try:
        os.execvp(command[0], command)
    except OSError as exc:
        print(f"exec-guard: exec {command[0]}: {exc.strerror}", file=sys.stderr)
        sys.exit(126 if exc.errno == errno.EACCES else 127)


if __name__ == "__main__":
    main(sys.argv[1:])
