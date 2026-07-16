#!/bin/sh
# Inert #15 hardware-contract packet. The executable allowlist is empty.

set -eu
umask 077

case "${1:-}" in
    --print-contract-pending)
        printf '%s\n' "SOFTWARE_PLAN_CORE_READY_HARDWARE_WRITE_CONTRACT_PENDING"
        printf '%s\n' "No live connection, policy, or assignment operation is enabled."
        exit 0
        ;;
    --help|-h)
        printf '%s\n' "Usage: sh scripts/probe-netcraze-policy-contract.sh --print-contract-pending"
        printf '%s\n' "This packet has no network path, credentials, output files, or hidden enable switch."
        exit 0
        ;;
    "")
        printf '%s\n' "routerkit: Netcraze write contract is pending; nothing executed." >&2
        exit 2
        ;;
    *)
        printf '%s\n' "routerkit: unsupported probe option." >&2
        exit 2
        ;;
esac
