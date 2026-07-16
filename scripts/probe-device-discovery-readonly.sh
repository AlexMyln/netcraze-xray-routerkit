#!/bin/sh
# Read-only RouterKit device-discovery probe packet.
#
# This script intentionally executes no Netcraze/Keenetic discovery command by
# default because #21 did not prove an official local client-enumeration
# command or schema. During the hardware window, enable only commands that are
# documented in docs/hardware/device-discovery-probe.md and confirmed for the
# exact firmware/model under test.

set -eu
umask 077

case "${1:-}" in
    --print-contract-pending)
        printf '%s\n' "SOFTWARE_CORE_READY_HARDWARE_CONTRACT_PENDING"
        printf '%s\n' "No router command is enabled by default."
        exit 0
        ;;
    --help|-h)
        printf '%s\n' "Usage: sh scripts/probe-device-discovery-readonly.sh --print-contract-pending"
        printf '%s\n' "The hardware command allowlist is intentionally empty until official evidence is confirmed."
        exit 0
        ;;
    "")
        printf '%s\n' "routerkit: hardware discovery contract is pending; no commands executed." >&2
        exit 2
        ;;
    *)
        printf '%s\n' "routerkit: unsupported probe option." >&2
        exit 2
        ;;
esac
