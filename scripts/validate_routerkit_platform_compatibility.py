#!/usr/bin/env python3
"""Validate RouterKit's fail-closed public platform compatibility manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse


SCHEMA = "routerkit.platform-compatibility.v1"
REVIEW_DATE = "2026-08-23"
LEGACY_EVIDENCE_CUTOFF = "2023-08-23"
PACKET_V1_SHA256 = "eccc110602db87be8fe2580d49716908e86b5cb1e77c7e07e9dd34a747b06743"
CLASSIFICATIONS = {
    "primary_canary_target",
    "strong_candidate",
    "hardware_equivalent_candidate",
    "research_candidate",
    "unsupported_current_routerkit_path",
    "insufficient_evidence",
}
VALIDATION_STATES = {"not_hardware_tested", "hardware_canary_passed", "hardware_canary_failed"}
FIRMWARE_STATES = {
    "vendor_main",
    "vendor_preview",
    "historical_requested_point",
    "source_reviewed_unverified",
    "hardware_validated",
    "unsupported",
}
STRONG = {"primary_canary_target", "strong_candidate", "hardware_equivalent_candidate"}
SOURCE_FACTS = {
    "catalog_boundary",
    "identity",
    "hardware",
    "storage",
    "architecture",
    "package_support",
    "firmware_main",
    "firmware_preview",
    "lifecycle",
    "management",
}
EVIDENCE_CATEGORIES = SOURCE_FACTS - {"catalog_boundary"}
SOURCE_KINDS = {
    "catalog",
    "product_page",
    "package_architecture_matrix",
    "firmware_release_notes",
    "lifecycle_policy",
    "download_center",
    "cli_reference",
}
ARCHITECTURE_SOURCE_KINDS = {"package_architecture_matrix"}
USB_USAGE = {"general_storage", "modem_only", "none", "unknown"}
REQUIRED_STRONG_EVIDENCE = {
    "identity",
    "hardware",
    "storage",
    "architecture",
    "package_support",
    "lifecycle",
    "management",
}
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
    "usb_usage",
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
    "evidence",
    "retrieval_date",
}
SOURCE_FIELDS = {
    "id",
    "vendor",
    "title",
    "url",
    "retrieval_date",
    "kind",
    "model_ids",
    "facts",
    "catalog_boundary_only",
    "firmware",
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


def _valid_date(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def static_eligibility_failures(model: Mapping[str, Any], evidence: Mapping[str, Any]) -> list[str]:
    """Return the single canonical set of mandatory static eligibility failures."""
    failures = []
    checks = (
        (model.get("usb_usage") == "general_storage", "storage.general_usb"),
        (model.get("usb_storage") is True, "storage.usb_storage"),
        (model.get("ext4") is True, "storage.ext4"),
        (model.get("opkg") is True, "storage.opkg"),
        (model.get("entware_opt") is True, "storage.entware_opt"),
        (model.get("storage_prerequisite") in {"pass", "pass_static"}, "storage.prerequisite"),
        (model.get("architecture") == "aarch64", "architecture.aarch64"),
        (model.get("bitness") == 64, "architecture.bitness"),
        (model.get("xray_artifact_match") is True, "architecture.xray_artifact"),
        (model.get("cli") is True, "management.cli"),
        (model.get("ssh") is True, "management.ssh"),
        (model.get("management_prerequisite") == "pass_static", "management.prerequisite"),
        (model.get("backup_recovery") is True, "management.backup_recovery"),
        (model.get("hardware_validation_state") == "not_hardware_tested", "validation.source_only"),
        (model.get("ram_mb") in {512, 1024}, "resources.ram"),
        (model.get("ram_assessment") == "comfortable", "resources.ram_assessment"),
        (
            not str(model.get("lifecycle_status", "")).lower().startswith("unsupported"),
            "lifecycle.supported",
        ),
    )
    failures.extend(code for passed, code in checks if not passed)
    failures.extend(
        f"evidence.{category}"
        for category in sorted(REQUIRED_STRONG_EVIDENCE)
        if not evidence.get(category)
    )
    return failures


def _validate_source_registry(data: Mapping[str, Any], model_ids: set[str]) -> dict[str, Mapping[str, Any]]:
    sources = data.get("sources")
    _require(isinstance(sources, list) and sources, "source registry must be non-empty")
    by_id = {}
    urls = set()
    for position, source in enumerate(sources):
        label = f"sources[{position}]"
        _require(isinstance(source, Mapping), f"{label} invalid")
        _require(set(source) == SOURCE_FIELDS, f"{label} fields mismatch")
        source_id = source.get("id")
        _require(isinstance(source_id, str) and re.fullmatch(r"src-[a-z0-9-]+", source_id), f"{label}.id invalid")
        _require(source_id not in by_id, f"duplicate source id {source_id}")
        _require(_https_vendor_url(source.get("url")), f"{label}.url invalid")
        _require(source["url"] not in urls, f"duplicate source URL {source['url']}")
        _require(source.get("retrieval_date") == REVIEW_DATE, f"{label}.retrieval_date invalid")
        _require(source.get("kind") in SOURCE_KINDS, f"{label}.kind invalid")
        _require(isinstance(source.get("vendor"), str) and source["vendor"], f"{label}.vendor invalid")
        _require(isinstance(source.get("title"), str) and source["title"], f"{label}.title invalid")
        scoped_models = source.get("model_ids")
        _require(
            isinstance(scoped_models, list)
            and len(scoped_models) == len(set(scoped_models))
            and all(model_id in model_ids for model_id in scoped_models),
            f"{label}.model_ids invalid",
        )
        facts = source.get("facts")
        _require(
            isinstance(facts, list)
            and facts
            and len(facts) == len(set(facts))
            and set(facts) <= SOURCE_FACTS,
            f"{label}.facts invalid",
        )
        catalog_only = source.get("catalog_boundary_only")
        _require(isinstance(catalog_only, bool), f"{label}.catalog_boundary_only invalid")
        if source["kind"] == "catalog":
            _require(catalog_only is True, f"{label} catalog must be boundary-only")
            _require(scoped_models == [] and facts == ["catalog_boundary"], f"{label} catalog proof scope invalid")
        else:
            _require(catalog_only is False, f"{label} authoritative source cannot be boundary-only")
            _require(scoped_models, f"{label} authoritative source needs exact model scope")
            _require("catalog_boundary" not in facts, f"{label} mixes catalog and authoritative facts")
        firmware = source.get("firmware")
        if source["kind"] == "firmware_release_notes":
            _require(isinstance(firmware, Mapping), f"{label}.firmware missing")
            _require(
                set(firmware) == {"model_id", "channel", "display_version", "released_at"},
                f"{label}.firmware fields mismatch",
            )
            channel = firmware.get("channel")
            _require(channel in {"main", "preview"}, f"{label}.firmware channel invalid")
            _require(firmware.get("model_id") in scoped_models, f"{label}.firmware model scope mismatch")
            _require(facts == [f"firmware_{channel}"], f"{label}.firmware fact mismatch")
            version = firmware.get("display_version")
            _require(
                version is None or (isinstance(version, str) and re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", version)),
                f"{label}.firmware display version invalid",
            )
            _require(firmware.get("released_at") is None or _valid_date(firmware["released_at"]), f"{label}.firmware date invalid")
        else:
            _require(firmware is None, f"{label}.firmware only allowed on release notes")
        by_id[source_id] = source
        urls.add(source["url"])
    return by_id


def _validate_firmware_binding(
    model: Mapping[str, Any],
    field: str,
    channel: str,
    evidence: Mapping[str, Any],
    sources: Mapping[str, Mapping[str, Any]],
    label: str,
) -> None:
    value = model[field]
    if value is None:
        return
    _require(isinstance(value, Mapping), f"{label}.{field} must be an object or null")
    _require(
        set(value) == {"display_version", "exact_build_string", "state", "source_id"},
        f"{label}.{field} fields mismatch",
    )
    _require(value["state"] == f"vendor_{channel}", f"{label}.{field} state mismatch")
    _require(value["exact_build_string"] is None, f"{label}.{field} unproved exact build must stay null")
    version = value["display_version"]
    _require(
        version is None or (isinstance(version, str) and re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", version)),
        f"{label}.{field} display version invalid",
    )
    source_id = value["source_id"]
    if version is None and source_id is None:
        return
    _require(isinstance(source_id, str) and source_id in sources, f"{label}.{field} source missing")
    _require(source_id in evidence.get(f"firmware_{channel}", []), f"{label}.{field} source not bound")
    source_firmware = sources[source_id]["firmware"]
    _require(source_firmware["model_id"] == model["id"], f"{label}.{field} source model mismatch")
    _require(source_firmware["channel"] == channel, f"{label}.{field} source channel mismatch")
    _require(source_firmware["display_version"] == version, f"{label}.{field} source version mismatch")


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
        set(source_policy)
        == {
            "primary_only_for_classification",
            "retrieval_date",
            "unknowns_fail_closed",
            "catalog_source_ids",
            "fact_types",
            "evidence_categories",
        },
        "source_policy fields mismatch",
    )
    _require(source_policy["primary_only_for_classification"] is True, "classification sources must remain primary")
    _require(source_policy["unknowns_fail_closed"] is True, "unknown source facts must fail closed")
    _require(source_policy["retrieval_date"] == REVIEW_DATE, "source policy date mismatch")
    _require(set(source_policy["fact_types"]) == SOURCE_FACTS, "source fact enum mismatch")
    _require(set(source_policy["evidence_categories"]) == EVIDENCE_CATEGORIES, "evidence category enum mismatch")

    coverage = data.get("catalog_coverage")
    _require(isinstance(coverage, dict), "catalog_coverage must be an object")
    _require(set(coverage) == {"netcraze", "keenetic", "legacy_cutoff"}, "catalog_coverage fields mismatch")
    _require(
        coverage.get("netcraze")
        == {
            "catalog_product_entries": 33,
            "routers_inventoried": 26,
            "non_router_entries_excluded": 7,
            "detailed_usb_or_high_resource_entries": 26,
        },
        "Netcraze coverage reconciliation mismatch",
    )
    keenetic = coverage.get("keenetic")
    _require(
        isinstance(keenetic, dict)
        and set(keenetic) == {"catalog_scope", "detailed_entries", "non_usb_current_routers_excluded_at_catalog_filter"}
        and keenetic["detailed_entries"] == 13,
        "Keenetic coverage reconciliation mismatch",
    )
    legacy = coverage.get("legacy_cutoff")
    _require(isinstance(legacy, dict), "legacy cutoff policy missing")
    _require(
        set(legacy)
        == {
            "policy_version",
            "review_date",
            "lookback_years",
            "evidence_retrieval_cutoff_date",
            "required_conditions",
            "scope_limit",
        },
        "legacy cutoff fields mismatch",
    )
    _require(legacy["policy_version"] == "routerkit-recent-legacy-v1", "legacy policy version mismatch")
    _require(legacy["review_date"] == REVIEW_DATE and legacy["lookback_years"] == 3, "legacy policy date mismatch")
    _require(legacy["evidence_retrieval_cutoff_date"] == LEGACY_EVIDENCE_CUTOFF, "legacy cutoff date mismatch")
    _require(
        legacy["required_conditions"]
        == [
            "not_in_current_catalog",
            "exact_model_in_first_party_package_matrix",
            "general_usb_storage",
            "opkg_relevance",
            "current_generation_os_family",
            "evidence_retrieved_within_lookback",
        ],
        "legacy selection predicates mismatch",
    )
    _require("not a claim of complete historical model coverage" in legacy["scope_limit"], "legacy scope overclaim guard missing")

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
        requirements.get("ram_policy")
        == {
            "1024": "comfortable",
            "512": "comfortable",
            "256": "borderline_requires_hardware_measurement",
            "128": "insufficient unless separately justified and hardware measured",
        },
        "RAM policy mismatch",
    )

    packet = data.get("packet_decision")
    _require(isinstance(packet, dict), "packet_decision must be an object")
    _require(set(packet) == {"decision", "packet", "sha256", "packet_v2_created", "reason"}, "packet_decision fields mismatch")
    _require(packet.get("decision") == "preserve_packet_v1_unchanged", "packet v1 decision mismatch")
    _require(packet.get("packet") == "hardware/netcraze-canary-packet.v1.json", "packet path mismatch")
    _require(packet.get("sha256") == PACKET_V1_SHA256, "packet v1 recorded hash mismatch")
    _require(packet.get("packet_v2_created") is False, "packet v2 must not be implied")

    models = data.get("models")
    _require(isinstance(models, list) and models, "models must be a non-empty array")
    ids = set()
    indexes = set()
    for position, model in enumerate(models):
        label = f"models[{position}]"
        _require(isinstance(model, dict), f"{label} must be an object")
        _require(set(model) == MODEL_REQUIRED_FIELDS, f"{label} fields mismatch")
        model_id = model.get("id")
        _require(isinstance(model_id, str) and re.fullmatch(r"[a-z0-9-]+", model_id), f"{label}.id invalid")
        _require(model_id not in ids, f"duplicate model id {model_id}")
        ids.add(model_id)
        key = (model.get("vendor"), model.get("model_index"), model.get("universe_pass"))
        _require(key not in indexes, f"duplicate model identity {key}")
        indexes.add(key)

    sources = _validate_source_registry(data, ids)
    catalog_source_ids = source_policy.get("catalog_source_ids")
    _require(
        isinstance(catalog_source_ids, list)
        and len(catalog_source_ids) == len(set(catalog_source_ids))
        and set(catalog_source_ids) == {source_id for source_id, source in sources.items() if source["kind"] == "catalog"},
        "catalog source registry mismatch",
    )

    counts = {"current_netcraze": 0, "current_keenetic": 0, "recent_legacy": 0}
    primary = 0
    usb2_only_pass = False
    evidence_by_model = {}
    referenced_sources = set()
    for position, model in enumerate(models):
        label = f"models[{position}]"
        model_id = model["id"]
        universe_pass = model["universe_pass"]
        _require(universe_pass in counts, f"{label}.universe_pass invalid")
        counts[universe_pass] += 1
        classification = model["classification"]
        _require(classification in CLASSIFICATIONS, f"{label}.classification invalid")
        _require(model["hardware_validation_state"] == "not_hardware_tested", f"{label} overclaims hardware")
        _require(model["retrieval_date"] == REVIEW_DATE, f"{label}.retrieval_date invalid")
        _require(model["architecture"] in {None, "aarch64", "mips", "mipsel"}, f"{label}.architecture invalid")
        _require(model["usb_usage"] in USB_USAGE, f"{label}.usb_usage invalid")
        _require(isinstance(model["usb_storage"], bool), f"{label}.usb_storage invalid")
        _require(isinstance(model["blocking_reasons"], list), f"{label}.blocking_reasons invalid")
        _require(
            all(isinstance(reason, str) and reason for reason in model["blocking_reasons"]),
            f"{label}.blocking_reasons entries invalid",
        )
        evidence = model["evidence"]
        _require(isinstance(evidence, dict) and set(evidence) <= EVIDENCE_CATEGORIES, f"{label}.evidence invalid")
        for category, source_ids in evidence.items():
            _require(
                isinstance(source_ids, list) and source_ids and len(source_ids) == len(set(source_ids)),
                f"{label}.evidence.{category} invalid",
            )
            for source_id in source_ids:
                _require(source_id in sources, f"{label}.evidence.{category} source missing")
                source = sources[source_id]
                _require(model_id in source["model_ids"], f"{label}.evidence.{category} source model mismatch")
                _require(category in source["facts"], f"{label}.evidence.{category} source fact mismatch")
                _require(source["vendor"] == model["vendor"], f"{label}.evidence.{category} source vendor mismatch")
                referenced_sources.add(source_id)
        evidence_by_model[model_id] = evidence

        if model["architecture_evidence"] == "official_package_matrix":
            architecture_sources = evidence.get("architecture", [])
            _require(architecture_sources, f"{label} architecture source binding missing")
            _require(
                all(sources[source_id]["kind"] in ARCHITECTURE_SOURCE_KINDS for source_id in architecture_sources),
                f"{label} architecture proof kind invalid",
            )
        if model["architecture"] is None:
            _require(model["bitness"] is None and model["xray_artifact_match"] is False, f"{label} unknown architecture state incoherent")
            _require(classification not in STRONG, f"{label} unknown architecture cannot be strong")
        elif model["architecture"] == "aarch64":
            _require(model["bitness"] == 64 and model["xray_artifact_match"] is True, f"{label} AArch64 mapping incoherent")
        else:
            _require(model["bitness"] == 32 and model["xray_artifact_match"] is False, f"{label} unsupported ISA mapping incoherent")

        expected_ram = {
            1024: "comfortable",
            512: "comfortable",
            256: "borderline_requires_hardware_measurement",
            128: "insufficient",
            None: "unknown",
        }
        _require(model["ram_mb"] in expected_ram, f"{label}.ram_mb outside documented policy")
        _require(model["ram_assessment"] == expected_ram[model["ram_mb"]], f"{label}.ram_assessment contradicts policy")

        usage = model["usb_usage"]
        if usage == "general_storage":
            _require(model["usb_storage"] is True, f"{label} general storage must set usb_storage")
        else:
            _require(model["usb_storage"] is False, f"{label} non-storage USB cannot set usb_storage")
            _require(model["storage_prerequisite"] == "fail", f"{label} non-storage prerequisite must fail")
            _require(model["ext4"] is False and model["opkg"] is False and model["entware_opt"] is False, f"{label} non-storage pass state incoherent")
        if usage == "none":
            _require(model["usb_ports"] == [], f"{label} no-USB state contradicts listed ports")
        if usage == "modem_only":
            _require(bool(model["usb_ports"]), f"{label} modem-only state needs a USB port")

        _validate_firmware_binding(model, "vendor_main", "main", evidence, sources, label)
        _validate_firmware_binding(model, "vendor_preview", "preview", evidence, sources, label)
        failures = static_eligibility_failures(model, evidence)
        if classification == "primary_canary_target":
            primary += 1
        if classification in STRONG:
            _require(not failures, f"{label} strong eligibility failures: {', '.join(failures)}")
            _require(not model["blocking_reasons"], f"{label} strong classification has unresolved blocking reason")
        elif classification == "unsupported_current_routerkit_path":
            known_failures = [failure for failure in failures if not failure.startswith("evidence.")]
            _require(known_failures, f"{label} unsupported classification lacks a failing prerequisite")
            _require(model["blocking_reasons"], f"{label} unsupported classification needs blocking reasons")
        elif classification == "research_candidate":
            _require(failures and model["blocking_reasons"], f"{label} research classification lacks a material uncertainty")
        elif classification == "insufficient_evidence":
            evidence_gap = any(failure.startswith("evidence.") for failure in failures)
            unknown_static = model["architecture"] is None or model["ram_mb"] is None
            _require((evidence_gap or unknown_static) and model["blocking_reasons"], f"{label} insufficient-evidence classification lacks an evidence gap")

        if model["usb_ports"] == ["USB 2.0"] and model["usb_usage"] == "general_storage":
            usb2_only_pass = True
        if universe_pass == "recent_legacy":
            _require(model["catalog_status"] == "recent_legacy", f"{label} legacy model must be outside current catalog")
            _require(model["usb_usage"] == "general_storage" and model["opkg"] is True, f"{label} legacy selection storage/package mismatch")
            _require(str(model["firmware_family"]).startswith("KeeneticOS"), f"{label} legacy OS-family boundary mismatch")
            matrix_sources = evidence.get("architecture", [])
            _require(
                matrix_sources
                and all(sources[source_id]["kind"] == "package_architecture_matrix" for source_id in matrix_sources),
                f"{label} legacy exact-model package-matrix proof missing",
            )
            _require(
                all(sources[source_id]["retrieval_date"] >= LEGACY_EVIDENCE_CUTOFF for source_id in matrix_sources),
                f"{label} legacy evidence predates cutoff",
            )

    _require(counts == {"current_netcraze": 26, "current_keenetic": 13, "recent_legacy": 8}, "catalog coverage counts mismatch")
    _require(primary == 1, "exactly one primary canary target required")
    _require(usb2_only_pass, "USB 2.0 storage must not be rejected solely for bus version")
    for source_id, source in sources.items():
        if source["catalog_boundary_only"]:
            continue
        _require(source_id in referenced_sources, f"orphan authoritative source {source_id}")
        for model_id in source["model_ids"]:
            for fact in source["facts"]:
                _require(
                    source_id in evidence_by_model[model_id].get(fact, []),
                    f"decorative source claim {source_id}:{model_id}:{fact}",
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
    canonical = {
        "nc_2312_vendor_main": {
            "model_id": "netcraze-nc-2312",
            "channel": "main",
            "display_version": "5.1.3",
            "released_at": "2026-08-10",
            "state": "vendor_main",
            "source_id": "src-netcraze-nc-2312-main-2026-08-23",
        },
        "nc_2312_vendor_preview": {
            "model_id": "netcraze-nc-2312",
            "channel": "preview",
            "display_version": "5.1.4",
            "released_at": "2026-08-18",
            "state": "vendor_preview",
            "source_id": "src-netcraze-nc-2312-preview-2026-08-23",
        },
    }
    canonical_source_ids = []
    nc_2312 = next(model for model in models if model["id"] == "netcraze-nc-2312")
    for field, expected in canonical.items():
        value = firmware.get(field)
        _require(isinstance(value, dict), f"firmware_policy.{field} missing")
        _require(
            set(value)
            == {
                "display_version",
                "exact_build_string",
                "exact_build_status",
                "state",
                "released_at",
                "model_id",
                "channel",
                "source_id",
            },
            f"firmware_policy.{field} fields mismatch",
        )
        for key, expected_value in expected.items():
            _require(value[key] == expected_value, f"firmware_policy.{field}.{key} mismatch")
        _require(value["exact_build_string"] is None and value["exact_build_status"] == "unknown", f"firmware_policy.{field} exact build overclaim")
        source_id = value["source_id"]
        _require(source_id in sources, f"firmware_policy.{field} source missing")
        source_firmware = sources[source_id]["firmware"]
        _require(
            source_firmware
            == {
                "model_id": expected["model_id"],
                "channel": expected["channel"],
                "display_version": expected["display_version"],
                "released_at": expected["released_at"],
            },
            f"firmware_policy.{field} source tuple mismatch",
        )
        model_field = "vendor_main" if expected["channel"] == "main" else "vendor_preview"
        _require(nc_2312[model_field]["source_id"] == source_id, f"firmware_policy.{field} model binding mismatch")
        canonical_source_ids.append(source_id)
    _require(len(set(canonical_source_ids)) == 2, "Main and Preview must use distinct sources")

    aliases = data.get("alias_assessments")
    _require(isinstance(aliases, list) and aliases, "alias assessments must be non-empty")
    for position, alias in enumerate(aliases):
        _require(isinstance(alias, dict), f"alias_assessments[{position}] invalid")
        _require(set(alias) == {"left", "right", "classification", "reason"}, f"alias_assessments[{position}] fields mismatch")
        _require(alias["left"] in ids and alias["right"] in ids, f"alias_assessments[{position}] model missing")
        _require(alias["classification"] == "insufficient_evidence", f"alias_assessments[{position}] overclaims equivalence")

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
