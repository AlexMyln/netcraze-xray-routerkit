#!/usr/bin/env python3
"""Bounded /proc FD ownership scan used by the public autostart CLI.

The first NC-3812 hardware run observed a healthy Xray process with roughly
729 file descriptors.  The legacy verifier stopped after 256 entries and
therefore returned a false negative.  This helper keeps the scan bounded while
allowing realistic Xray workloads.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Set


MAX_PID_FD_ENTRIES = 4096


def socket_inodes_for_pid(proc_root: Path, pid: int) -> Optional[Set[str]]:
    """Return socket inodes owned by *pid*, or None when proof is unavailable.

    The scan is deliberately fail-closed.  Any unreadable entry or a process
    exceeding the bounded descriptor budget produces ``None`` rather than a
    partial ownership set.
    """

    fd_dir = Path(proc_root) / str(pid) / "fd"
    inodes: Set[str] = set()
    try:
        scanner = os.scandir(str(fd_dir))
    except OSError:
        return None
    with scanner:
        for index, entry in enumerate(scanner, start=1):
            if index > MAX_PID_FD_ENTRIES:
                return None
            try:
                target = os.readlink(entry.path)
            except OSError:
                return None
            if target.startswith("socket:[") and target.endswith("]"):
                inodes.add(target[len("socket:[") : -1])
    return inodes
