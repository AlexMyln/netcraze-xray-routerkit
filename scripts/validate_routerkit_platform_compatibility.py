#!/usr/bin/env python3
"""Validate RouterKit's fail-closed public platform compatibility manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse


SCHEMA = "routerkit.platform-compatibility.v1"
REVIEW_DATE = "2026-08-23"
PACKET_V1_SHA256 = "eccc110602db87be8fe2580d49716908e86b5cb1e77c7e07e9dd34a747b06743"
CLASSIFICATIONS = {
    "primary_canary_target",
    "strong_candidate",
    "hardware_equivalent_candidate",
    "research_candidate",
    "unsupported_current_routerkit_path",
    "insufficient_evidence",
}
VALIDATION_STATES = {
    "not_hardware_tested",
    "hardware_canary_passed",
    "hardware_canary_failed",
}
FIRMWARE_STATES = {
    "vendor_main",
    "vendor_preview",
    "historical_requested_point",
    "source_reviewed_unverified",
    "hardware_validated",
    "unsupported",
}
STRONG = {"primary_canary_target", "strong_candidate", "hardware_equivalent_candidate"}
TOP_LEVEL_FIELDS = {
    "schema",
    "reviewed_at",
    "review_scope",
    "source_policy",
    "catalog_coverage",
    "classification_enum",
    "validation_state_enum",
    "firmware_state_enum",
    "routerkit_requirements",
    "firmware_policy",
    "packet_decision",
    "alias_assessments",
    "models",
    "sources",
    "disclaimers",
}
MODEL_REQUIRED_FIELDS = {
    "id",
    "vendor",
    "product_family",
    "product_name",
    "model_index",
    "universe_pass",
    "catalog_status",
    "lifecycle_status",
    "soc",
    "cpu_cores",
    "cpu_frequency_mhz",
    "architecture",
    "bitness",
    "architecture_evidence",
    "ram_mb",
    "flash_mb",
    "usb_ports",
    "usb_storage",
    "ext4",
    "opkg",
    "entware_opt",
    "cli",
    "ssh",
    "rci_api",
    "backup_recovery",
    "vendor_main",
    "vendor_preview",
    "firmware_family",
    "xray_artifact_match",
    "storage_prerequisite",
    "management_prerequisite",
    "ram_assessment",
    "classification",
    "blocking_reasons",
    "hardware_validation_state",
    "official_sources",
    "retrieval_date",
}


class CompatibilityValidationError(ValueError):
    """Manifest validation failed without authorizing any live action."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CompatibilityValidationError(message)


def _https_vendor_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return (
        parsed.scheme == "https"
        and parsed.hostname
        in {
            "netcraze.ru",
            "support.netcraze.ru",
            "keenetic.com",
            "support.keenetic.com",
            "docs.help.keenetic.com",
        }
        and parsed.username is None
        and parsed.password is None
    )


def validate_manifest(data: Any, *, repository_root: Path | None = None) -> None:
    _require(isinstance(data, dict), "manifest must be an object")
    _require(set(data) == TOP_LEVEL_FIELDS, "top-level fields mismatch")
    _require(data.get("schema") == SCHEMA, "schema must be exact v1")
    _require(data.get("reviewed_at") == REVIEW_DATE, "review date must be exact")

    review_scope = data.get("review_scope")
    _require(isinstance(review_scope, dict), "review_scope must be an object")
    _require(
        set(review_scope)
        == {
            "baseline",
            "baseline_commit",
            "current_netcraze_router_count",
            "current_keenetic_detailed_count",
            "recent_legacy_count",
            "hardware_validated",
            "live_contract_confirmed",
        },
        "review_scope fields mismatch",
    )
    _require(review_scope.get("baseline") == "v0.2.0-alpha.17", "baseline tag mismatch")
    _require(
        review_scope.get("baseline_commit") == "a72fcacd412da9f2724675c0ff815f4698f0b16e",
        "baseline commit mismatch",
    )
    _require(review_scope.get("hardware_validated") is False, "hardware validation must remain false")
    _require(review_scope.get("live_contract_confirmed") is False, "live contract must remain false")

    source_policy = data.get("source_policy")
    _require(isinstance(source_policy, dict), "source_policy must be an object")
    _require(
        set(source_policy) == {"primary_only_for_classification", "retrieval_date", "unknowns_fail_closed", "catalogs"},
        "source_policy fields mismatch",
    )
    _require(source_policy["primary_only_for_classification"] is True, "classification sources must remain primary")
    _require(source_policy["unknowns_fail_closed"] is True, "unknown source facts must fail closed")
    _require(source_policy["retrieval_date"] == REVIEW_DATE, "source policy date mismatch")
    _require(
        isinstance(source_policy["catalogs"], list)
        and len(source_policy["catalogs"]) == len(set(source_policy["catalogs"]))
        and all(_https_vendor_url(url) for url in source_policy["catalogs"]),
        "catalog sources invalid",
    )

    coverage = data.get("catalog_coverage")
    _require(isinstance(coverage, dict), "catalog_coverage must be an object")
    _require(set(coverage) == {"netcraze", "keenetic", "legacy_cutoff"}, "catalog_coverage fields mismatch")
    _require(
        isinstance(coverage["netcraze"], dict)
        and set(coverage["netcraze"])
        == {"catalog_product_entries", "routers_inventoried", "non_router_entries_excluded", "detailed_usb_or_high_resource_entries"},
        "Netcraze coverage fields mismatch",
    )
    _require(
        coverage["netcraze"]
        == {"catalog_product_entries": 33, "routers_inventoried": 26, "non_router_entries_excluded": 7, "detailed_usb_or_high_resource_entries": 26},
        "Netcraze coverage reconciliation mismatch",
    )
    _require(
        isinstance(coverage["keenetic"], dict)
        and set(coverage["keenetic"]) == {"catalog_scope", "detailed_entries", "non_usb_current_routers_excluded_at_catalog_filter"},
        "Keenetic coverage fields mismatch",
    )
    _require(coverage["keenetic"]["detailed_entries"] == 13, "Keenetic detailed count mismatch")
    _require(isinstance(coverage["legacy_cutoff"], str) and coverage["legacy_cutoff"], "legacy cutoff missing")

    _require(set(data.get("classification_enum", [])) == CLASSIFICATIONS, "classification enum mismatch")
    _require(set(data.get("validation_state_enum", [])) == VALIDATION_STATES, "validation enum mismatch")
    _require(set(data.get("firmware_state_enum", [])) == FIRMWARE_STATES, "firmware state enum mismatch")
    requirements = data.get("routerkit_requirements")
    _require(isinstance(requirements, dict), "routerkit_requirements must be an object")
    _require(
        set(requirements)
        == {"mandatory", "preferred", "hardware_to_confirm", "usb_3_required", "usb_2_policy", "xray_bootstrap", "ram_policy"},
        "routerkit_requirements fields mismatch",
    )
    _require(requirements.get("usb_3_required") is False, "USB 3 must not be mandatory")
    for field in ("mandatory", "preferred", "hardware_to_confirm"):
        _require(
            isinstance(requirements[field], list)
            and requirements[field]
            and all(isinstance(item, str) and item for item in requirements[field]),
            f"routerkit_requirements.{field} invalid",
        )
    bootstrap = requirements.get("xray_bootstrap")
    _require(isinstance(bootstrap, dict), "xray_bootstrap must be an object")
    _require(
        set(bootstrap) == {"manifest", "architecture_token", "upstream_artifact", "uname_machines", "other_architectures_supported"},
        "xray_bootstrap fields mismatch",
    )
    _require(bootstrap.get("manifest") == "manifests/xray-artifacts.json", "Xray manifest path mismatch")
    _require(bootstrap.get("upstream_artifact") == "Xray-linux-arm64-v8a.zip", "Xray artifact mismatch")
    _require(bootstrap.get("architecture_token") == "linux-arm64", "architecture token must stay strict")
    _require(set(bootstrap.get("uname_machines", [])) == {"aarch64", "arm64"}, "uname map mismatch")
    _require(bootstrap.get("other_architectures_supported") is False, "unsupported architectures must fail closed")
    _require(
        isinstance(requirements["ram_policy"], dict)
        and set(requirements["ram_policy"]) == {"1024", "512", "256", "128"},
        "RAM policy mismatch",
    )

    firmware = data.get("firmware_policy")
    _require(isinstance(firmware, dict), "firmware_policy must be an object")
    _require(
        set(firmware)
        == {
            "record_exact_observed_firmware_at_preflight",
            "first_canary_channel",
            "preview_requires_deliberate_opt_in",
            "different_from_last_source_reviewed_requires_p4_review",
            "source_reviewed_is_not_live_contract",
            "runtime_schemas_require_hardware",
            "historical_packet_display_version",
            "historical_packet_exact_build_string",
            "nc_2312_vendor_main",
            "nc_2312_vendor_preview",
            "historical_requested_5_1_3",
            "source_level_verdict_5_1_3",
            "live_contract_confirmed",
        },
        "firmware_policy fields mismatch",
    )
    _require(firmware.get("source_level_verdict_5_1_3") == "NO_SOURCE_LEVEL_BLOCKER", "5.1.3 verdict mismatch")
    _require(firmware.get("live_contract_confirmed") is False, "firmware policy overclaims live contract")
    for field in (
        "record_exact_observed_firmware_at_preflight",
        "preview_requires_deliberate_opt_in",
        "different_from_last_source_reviewed_requires_p4_review",
        "source_reviewed_is_not_live_contract",
        "runtime_schemas_require_hardware",
    ):
        _require(firmware[field] is True, f"firmware policy {field} must remain true")
    _require(firmware["first_canary_channel"] == "vendor_main", "first canary channel mismatch")
    main = firmware.get("nc_2312_vendor_main")
    preview = firmware.get("nc_2312_vendor_preview")
    _require(isinstance(main, dict) and main.get("state") == "vendor_main", "Main channel missing")
    _require(isinstance(preview, dict) and preview.get("state") == "vendor_preview", "Preview channel missing")
    _require(main.get("display_version") == "5.1.3", "NC-2312 Main must be 5.1.3 at review date")
    _require(preview.get("display_version") == "5.1.4", "NC-2312 Preview must be 5.1.4 at review date")
    _require(main.get("exact_build_string") is None, "unproved Main build string must stay null")
    _require(preview.get("exact_build_string") is None, "unproved Preview build string must stay null")

    packet = data.get("packet_decision")
    _require(isinstance(packet, dict), "packet_decision must be an object")
    _require(set(packet) == {"decision", "packet", "sha256", "packet_v2_created", "reason"}, "packet_decision fields mismatch")
    _require(packet.get("decision") == "preserve_packet_v1_unchanged", "packet v1 decision mismatch")
    _require(packet.get("packet") == "hardware/netcraze-canary-packet.v1.json", "packet path mismatch")
    _require(packet.get("sha256") == PACKET_V1_SHA256, "packet v1 recorded hash mismatch")
    _require(packet.get("packet_v2_created") is False, "packet v2 must not be implied")

    models = data.get("models")
    _require(isinstance(models, list) and models, "models must be a non-empty array")
    ids: set[str] = set()
    indexes: set[tuple[str, str, str]] = set()
    counts = {"current_netcraze": 0, "current_keenetic": 0, "recent_legacy": 0}
    primary = 0
    usb2_only_pass = False
    for position, model in enumerate(models):
        label = f"models[{position}]"
        _require(isinstance(model, dict), f"{label} must be an object")
        _require(set(model) == MODEL_REQUIRED_FIELDS, f"{label} fields mismatch")
        model_id = model["id"]
        _require(isinstance(model_id, str) and re.fullmatch(r"[a-z0-9-]+", model_id), f"{label}.id invalid")
        _require(model_id not in ids, f"duplicate model id {model_id}")
        ids.add(model_id)
        key = (model["vendor"], model["model_index"], model["universe_pass"])
        _require(key not in indexes, f"duplicate model identity {key}")
        indexes.add(key)
        universe_pass = model["universe_pass"]
        _require(universe_pass in counts, f"{label}.universe_pass invalid")
        counts[universe_pass] += 1
        classification = model["classification"]
        _require(classification in CLASSIFICATIONS, f"{label}.classification invalid")
        _require(model["hardware_validation_state"] in VALIDATION_STATES, f"{label}.validation invalid")
        _require(model["hardware_validation_state"] == "not_hardware_tested", f"{label} overclaims hardware")
        _require(model["retrieval_date"] == REVIEW_DATE, f"{label}.retrieval_date invalid")
        sources = model["official_sources"]
        _require(isinstance(sources, list) and sources, f"{label}.official_sources missing")
        _require(all(_https_vendor_url(url) for url in sources), f"{label}.official_sources invalid")
        _require(len(sources) == len(set(sources)), f"{label}.official_sources duplicate")
        _require(isinstance(model["blocking_reasons"], list), f"{label}.blocking_reasons invalid")
        if classification == "primary_canary_target":
            primary += 1
        if classification in STRONG:
            _require(model["architecture"] == "aarch64", f"{label} strong without aarch64 proof")
            _require(model["architecture_evidence"] == "official_package_matrix", f"{label} strong without official architecture evidence")
            _require(model["bitness"] == 64, f"{label} strong without 64-bit proof")
            _require(model["xray_artifact_match"] is True, f"{label} strong without Xray artifact match")
            _require(model["usb_storage"] is True, f"{label} strong without USB storage")
            _require(model["ext4"] is True, f"{label} strong without EXT4")
            _require(model["opkg"] is True and model["entware_opt"] is True, f"{label} strong without Entware")
            _require(model["vendor_main"] is not None and model["vendor_preview"] is not None, f"{label} strong without channel separation")
        for field, state, source_token in (
            ("vendor_main", "vendor_main", "latest-main-release"),
            ("vendor_preview", "vendor_preview", "latest-preview-release"),
        ):
            channel = model[field]
            if channel is None:
                continue
            _require(isinstance(channel, dict), f"{label}.{field} must be an object or null")
            _require(set(channel) == {"display_version", "exact_build_string", "state"}, f"{label}.{field} fields mismatch")
            _require(channel["state"] == state, f"{label}.{field} state mismatch")
            _require(channel["exact_build_string"] is None, f"{label}.{field} unproved exact build must stay null")
            if channel["display_version"] is not None:
                _require(
                    isinstance(channel["display_version"], str) and re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", channel["display_version"]),
                    f"{label}.{field} display version invalid",
                )
                _require(
                    any(source_token in url and model["model_index"].lower() in url.lower() for url in sources),
                    f"{label}.{field} lacks model-specific release source",
                )
        if model["architecture"] is None:
            _require(classification not in STRONG, f"{label} unknown architecture cannot be strong")
        if model["usb_storage"] is False:
            _require(classification == "unsupported_current_routerkit_path", f"{label} no-storage model must be unsupported")
            _require(model["storage_prerequisite"] == "fail", f"{label} no-storage prerequisite must fail")
        if model["usb_ports"] == ["USB 2.0"] and model["usb_storage"] is True:
            usb2_only_pass = True

    _require(counts == {"current_netcraze": 26, "current_keenetic": 13, "recent_legacy": 8}, "catalog coverage counts mismatch")
    _require(primary == 1, "exactly one primary canary target required")
    _require(usb2_only_pass, "USB 2.0 storage must not be rejected solely for bus version")

    aliases = data.get("alias_assessments")
    _require(isinstance(aliases, list) and aliases, "alias assessments must be non-empty")
    for position, alias in enumerate(aliases):
        _require(isinstance(alias, dict), f"alias_assessments[{position}] invalid")
        _require(set(alias) == {"left", "right", "classification", "reason"}, f"alias_assessments[{position}] fields mismatch")
        _require(alias["left"] in ids and alias["right"] in ids, f"alias_assessments[{position}] model missing")
        _require(alias["classification"] == "insufficient_evidence", f"alias_assessments[{position}] overclaims equivalence")

    sources = data.get("sources")
    _require(isinstance(sources, list) and sources, "bibliography must be non-empty")
    bibliography_urls: set[str] = set()
    for position, source in enumerate(sources):
        _require(isinstance(source, Mapping), f"sources[{position}] invalid")
        _require(set(source) == {"title", "url", "vendor", "fact_proven", "retrieval_date"}, f"sources[{position}] fields mismatch")
        _require(_https_vendor_url(source.get("url")), f"sources[{position}].url invalid")
        _require(source.get("retrieval_date") == REVIEW_DATE, f"sources[{position}].retrieval_date invalid")
        _require(isinstance(source.get("fact_proven"), str) and source["fact_proven"], f"sources[{position}].fact_proven missing")
        _require(source["url"] not in bibliography_urls, f"sources[{position}].url duplicate")
        bibliography_urls.add(source["url"])

    disclaimers = data.get("disclaimers")
    _require(isinstance(disclaimers, list) and all(isinstance(item, str) and item for item in disclaimers), "disclaimers invalid")
    _require("hardware_validated=false" in disclaimers, "hardware disclaimer missing")
    _require("live_contract_confirmed=false" in disclaimers, "live-contract disclaimer missing")

    if repository_root is not None:
        packet_path = repository_root / packet["packet"]
        try:
            packet_bytes = packet_path.read_bytes()
        except OSError as exc:
            raise CompatibilityValidationError(f"could not read historical packet v1: {exc}") from exc
        digest = hashlib.sha256(packet_bytes).hexdigest()
        _require(digest == PACKET_V1_SHA256, "historical packet v1 changed")


def load_and_validate(path: Path, *, repository_root: Path | None = None) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CompatibilityValidationError(f"could not read compatibility manifest: {exc}") from exc
    validate_manifest(data, repository_root=repository_root)
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate RouterKit platform compatibility data without live access.")
    parser.add_argument("manifest", type=Path, nargs="?", default=Path("hardware/routerkit-platform-compatibility.v1.json"))
    args = parser.parse_args()
    repository_root = Path(__file__).resolve().parents[1]
    try:
        load_and_validate(args.manifest, repository_root=repository_root)
    except CompatibilityValidationError as exc:
        print(f"routerkit platform compatibility: {exc}")
        return 1
    print("routerkit platform compatibility: valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
