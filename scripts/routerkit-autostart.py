#!/usr/bin/env python3
"""Hyphenated CLI entrypoint for routerkit_autostart.

The public CLI installs the bounded hardware-validated FD ownership scanner
before entering the transaction module.  This keeps all existing process,
command-line, listener, and rollback checks unchanged while avoiding the
legacy 256-descriptor false negative observed on NC-3812.
"""

from __future__ import annotations

import routerkit_autostart as autostart
from routerkit_autostart_fd import socket_inodes_for_pid


# Public RouterKit autostart flows enter through this wrapper (including the
# unified ``routerkit.py`` command).  Inject only the ownership enumeration
# primitive; every other strict verifier invariant stays in the reviewed core.
autostart._socket_inodes_for_pid = socket_inodes_for_pid
main = autostart.main


if __name__ == "__main__":
    raise SystemExit(main())
