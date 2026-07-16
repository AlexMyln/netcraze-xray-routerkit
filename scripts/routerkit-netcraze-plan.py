#!/usr/bin/env python3
"""Standalone entrypoint for the offline Netcraze change-plan engine."""

from routerkit_devices import DeviceDiscoveryError, load_result_from_inventory_file, select_device
from routerkit_netcraze_plan import NetcrazePlanError, run_cli


def load_explicit_device_selection(path, choice):
    try:
        return select_device(load_result_from_inventory_file(path), choice)
    except DeviceDiscoveryError as exc:
        raise NetcrazePlanError(str(exc)) from None


if __name__ == "__main__":
    raise SystemExit(run_cli(device_selection_loader=load_explicit_device_selection))
