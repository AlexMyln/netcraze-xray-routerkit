#!/bin/sh
# Inert index for the consolidated Netcraze hardware-canary packet.

set -eu
umask 077

case "${1:-}" in
    --print-status)
        printf '%s\n' "HARDWARE_CANARY_PACKET_CONTRACT"
        printf '%s\n' "No hardware operation is enabled."
        exit 0
        ;;
    --print-phase-list)
        printf '%s\n' \
            "P0_OPERATOR_PREFLIGHT" \
            "P1_READ_ONLY_PLATFORM_INVENTORY" \
            "P2_READ_ONLY_DEVICE_DISCOVERY_CONTRACT" \
            "P3_READ_ONLY_POLICY_CONTRACT" \
            "P4_OFF_DEVICE_COMPATIBILITY_DECISION" \
            "P5_DISPOSABLE_CONNECTION_CANARY" \
            "P6_DISPOSABLE_POLICY_CANARY" \
            "P7_OPTIONAL_DISPOSABLE_ASSIGNMENT_CANARY" \
            "P8_FULL_ROUTERKIT_INSTALL_CANARY" \
            "P9_IDEMPOTENT_RERUN" \
            "P10_FAILURE_AND_ROLLBACK" \
            "P11_REBOOT_AND_RECOVERY" \
            "P12_FINAL_INVARIANT_AUDIT" \
            "P13_CLEANUP_AND_DEVICE_RETURN"
        exit 0
        ;;
    --print-readiness)
        printf '%s\n' "READY_FOR_HARDWARE_CANARY"
        printf '%s\n' "hardware_validated=false"
        printf '%s\n' "live_contract_confirmed=false"
        exit 0
        ;;
    --help|-h)
        printf '%s\n' "Usage: sh scripts/probe-netcraze-hardware-canary.sh OPTION"
        printf '%s\n' "Options: --print-status --print-phase-list --print-readiness"
        printf '%s\n' "This script has no network, hardware, file-output, or hidden execution path."
        exit 0
        ;;
    "")
        printf '%s\n' "routerkit: choose an inert print option; nothing executed." >&2
        exit 2
        ;;
    *)
        printf '%s\n' "routerkit: unsupported hardware-canary probe option." >&2
        exit 2
        ;;
esac
