#!/usr/bin/env python3
"""Compatibility layer for semantic reuse of pre-existing native objects.

The first NC-3812 installation created human-readable native names
(`XRAY-NL`, `VPN-NL`, etc.) before the reusable adapter existed.  Those
objects are safe to reuse when their full semantics match the RouterKit local
endpoint, even though their descriptions differ from the adapter's later
code-owned names.

This layer is intentionally strict: more than one semantic match is ambiguous,
a same-name RouterKit-owned object with different semantics is a conflict, and
verification distinguishes created objects (which must have the code-owned
name) from reused semantic equivalents.
"""

from __future__ import annotations

from typing import List, Optional

import routerkit_netcraze_live as core
from routerkit_devices import DeviceDiscoveryError, normalize_trusted_device_mac
from routerkit_netcraze_plan import LocalEndpointManifest, LocalProxyProfile, connection_name, policy_name


def proxy_semantic_exact(proxy: core.ProxyState, profile: LocalProxyProfile) -> bool:
    return (
        proxy.protocol == "socks5"
        and proxy.host == profile.host
        and proxy.port == profile.port
        and proxy.enabled == profile.enabled
        and not proxy.authentication_configured
    )


def proxy_owned_exact(proxy: core.ProxyState, profile: LocalProxyProfile) -> bool:
    return proxy.description == connection_name(profile) and proxy_semantic_exact(proxy, profile)


def policy_semantic_exact(policy: core.PolicyState, proxy_id: str) -> bool:
    return policy.global_interfaces == (proxy_id,)


def policy_owned_exact(policy: core.PolicyState, profile: LocalProxyProfile, proxy_id: str) -> bool:
    return policy.description == policy_name(profile) and policy_semantic_exact(policy, proxy_id)


def _one_semantic_match(items, predicate, *, kind: str):
    matches = [item for item in items if predicate(item)]
    if len(matches) > 1:
        raise core.LiveAdapterError("Multiple semantically equivalent native %s objects are ambiguous." % kind)
    return None if not matches else matches[0]


def build_live_plan(
    manifest: LocalEndpointManifest,
    state: core.LiveState,
    *,
    device_mac: Optional[str] = None,
    profile_slot: Optional[int] = None,
    allow_move: bool = False,
) -> core.LivePlan:
    if (device_mac is None) != (profile_slot is None):
        raise core.LiveAdapterError("Device assignment requires both a MAC and a profile slot.")
    normalized_mac = None
    if device_mac is not None:
        try:
            normalized_mac = normalize_trusted_device_mac(device_mac)
        except DeviceDiscoveryError:
            raise core.LiveAdapterError("Selected device MAC is invalid or unsafe.") from None
        if profile_slot not in [item.slot for item in manifest.profiles]:
            raise core.LiveAdapterError("Selected profile slot is not present in the endpoint manifest.")

    proxy_ids = [item.object_id for item in state.proxies]
    policy_ids = [item.object_id for item in state.policies]
    reserved_proxy_ids = set(proxy_ids)
    reserved_policy_ids = set(policy_ids)
    bindings: List[core.BindingPlan] = []

    for profile in manifest.profiles:
        desired_proxy_name = connection_name(profile)
        owned_proxy = core._unique_by_description(state.proxies, desired_proxy_name)
        if owned_proxy is not None and not proxy_semantic_exact(owned_proxy, profile):
            raise core.LiveAdapterError("A RouterKit proxy name exists with conflicting semantics.")

        semantic_proxy = _one_semantic_match(
            state.proxies,
            lambda item: proxy_semantic_exact(item, profile),
            kind="proxy",
        )
        if semantic_proxy is not None:
            proxy_id = semantic_proxy.object_id
            proxy_action = "reuse"
        else:
            proxy_id = core._next_free(reserved_proxy_ids, "Proxy", core.MAX_PROXY_INDEX)
            reserved_proxy_ids.add(proxy_id)
            proxy_action = "create"

        desired_policy_name = policy_name(profile)
        owned_policy = core._unique_by_description(state.policies, desired_policy_name)
        if owned_policy is not None and not policy_semantic_exact(owned_policy, proxy_id):
            raise core.LiveAdapterError("A RouterKit policy name exists with conflicting semantics.")

        semantic_policy = _one_semantic_match(
            state.policies,
            lambda item: policy_semantic_exact(item, proxy_id),
            kind="policy",
        )
        if semantic_policy is not None:
            policy_id = semantic_policy.object_id
            policy_action = "reuse"
        else:
            policy_id = core._next_free(reserved_policy_ids, "Policy", core.MAX_POLICY_INDEX)
            reserved_policy_ids.add(policy_id)
            policy_action = "create"

        bindings.append(
            core.BindingPlan(
                slot=profile.slot,
                port=profile.port,
                proxy_id=proxy_id,
                policy_id=policy_id,
                proxy_action=proxy_action,
                policy_action=policy_action,
            )
        )

    assignment_action = "none"
    if normalized_mac is not None and profile_slot is not None:
        target_policy = next(item.policy_id for item in bindings if item.slot == profile_slot)
        current = state.assignment_map.get(normalized_mac)
        if current == target_policy:
            assignment_action = "reuse"
        elif current is None:
            assignment_action = "assign"
        elif allow_move:
            assignment_action = "move"
        else:
            raise core.LiveAdapterError(
                "Selected device already has another policy; explicit move authorization is required."
            )

    return core.LivePlan(
        bindings=tuple(bindings),
        selected_device_present=normalized_mac is not None,
        selected_slot=profile_slot,
        assignment_action=assignment_action,
    )


def verify_plan_applied(
    manifest: LocalEndpointManifest,
    plan: core.LivePlan,
    state: core.LiveState,
    *,
    device_mac: Optional[str],
) -> None:
    proxies = {item.object_id: item for item in state.proxies}
    policies = {item.object_id: item for item in state.policies}
    profiles = {item.slot: item for item in manifest.profiles}
    for binding in plan.bindings:
        profile = profiles[binding.slot]
        proxy = proxies.get(binding.proxy_id)
        policy = policies.get(binding.policy_id)
        proxy_ok = (
            proxy is not None
            and (
                proxy_owned_exact(proxy, profile)
                if binding.proxy_action == "create"
                else proxy_semantic_exact(proxy, profile)
            )
        )
        if not proxy_ok:
            raise core.LiveAdapterError("Post-apply proxy verification failed.")
        policy_ok = (
            policy is not None
            and (
                policy_owned_exact(policy, profile, binding.proxy_id)
                if binding.policy_action == "create"
                else policy_semantic_exact(policy, binding.proxy_id)
            )
        )
        if not policy_ok:
            raise core.LiveAdapterError("Post-apply policy verification failed.")
    if device_mac is not None and plan.selected_slot is not None:
        mac = normalize_trusted_device_mac(device_mac)
        target = next(item.policy_id for item in plan.bindings if item.slot == plan.selected_slot)
        if state.assignment_map.get(mac) != target:
            raise core.LiveAdapterError("Post-apply device assignment verification failed.")
