#!/usr/bin/env python3
"""Standalone entrypoint for the offline hardware-canary packet validator."""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from routerkit_hardware_canary import run_cli


if __name__ == "__main__":
    raise SystemExit(run_cli())
