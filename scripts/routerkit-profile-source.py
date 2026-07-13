#!/usr/bin/env python3
"""Offline CLI for parsing and selecting RouterKit profile-source nodes."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Optional, Sequence

from routerkit_profile_source import (
    MAX_PAYLOAD_BYTES,
    NodeRecord,
    OutputExistsError,
    PayloadValidationError,
    ProfileSourceError,
    SelectionError,
    SelectedNodes,
    build_profiles_document,
    parse_compatible_nodes,
    safe_node_summary,
    select_nodes,
    validate_env_name,
    write_private_json,
)


HTTPS_MESSAGE = "HTTPS source resolution is not implemented in this release; tracked in #23."
UNSUPPORTED_URL_MESSAGE = "This source scheme is not supported."
_URI_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


class CliConfigurationError(Exception):
    pass


class UserCancelled(Exception):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Parse VLESS profile payloads and select compatible nodes offline."
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--source-env", metavar="ENV_NAME")
    source.add_argument("--source-file", metavar="PATH")
    parser.add_argument("--output", default="profiles.json")
    parser.add_argument("--list", action="store_true", help="List compatible nodes without writing.")
    parser.add_argument("--json", action="store_true", help="Use secret-safe JSON with --list.")
    parser.add_argument("--primary-index", type=int)
    parser.add_argument("--fallback-index", type=int, action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--yes", action="store_true", help="Skip only the final write confirmation.")
    parser.add_argument("--force", action="store_true", help="Allow replacing an existing output file.")
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if args.json and not args.list:
        raise CliConfigurationError("--json is valid only with --list.")
    if args.fallback_index and args.primary_index is None:
        raise CliConfigurationError("--fallback-index requires --primary-index.")
    if len(args.fallback_index) > 2:
        raise CliConfigurationError("At most two --fallback-index values are allowed.")
    indexes = ([] if args.primary_index is None else [args.primary_index]) + args.fallback_index
    if len(indexes) != len(set(indexes)):
        raise CliConfigurationError("Primary and fallback indexes must be distinct.")
    if args.source_env:
        try:
            validate_env_name(args.source_env)
        except PayloadValidationError as exc:
            raise CliConfigurationError(str(exc)) from None


def _validate_source_file_metadata(metadata: os.stat_result) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise CliConfigurationError("Source file must be a regular, non-symlink file.")
    if os.name == "posix" and stat.S_IMODE(metadata.st_mode) & 0o077:
        raise CliConfigurationError("Source file permissions must be owner-only on POSIX.")
    if metadata.st_size > MAX_PAYLOAD_BYTES:
        raise PayloadValidationError("Payload is too large.")


def _read_source_file(path_text: str) -> str:
    path = Path(path_text)
    fd = -1
    try:
        path_metadata = path.lstat()
        if stat.S_ISLNK(path_metadata.st_mode):
            raise CliConfigurationError("Source file must be a regular, non-symlink file.")
        _validate_source_file_metadata(path_metadata)

        flags = os.O_RDONLY
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        opened_metadata = os.fstat(fd)
        _validate_source_file_metadata(opened_metadata)
        if (path_metadata.st_dev, path_metadata.st_ino) != (
            opened_metadata.st_dev,
            opened_metadata.st_ino,
        ):
            raise CliConfigurationError("Source file changed before it could be read safely.")

        data = bytearray()
        while len(data) <= MAX_PAYLOAD_BYTES:
            chunk = os.read(fd, min(65536, MAX_PAYLOAD_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
    except (CliConfigurationError, PayloadValidationError):
        raise
    except OSError:
        raise CliConfigurationError("Could not read the source file.") from None
    finally:
        if fd >= 0:
            os.close(fd)
    if len(data) > MAX_PAYLOAD_BYTES:
        raise PayloadValidationError("Payload is too large.")
    try:
        return bytes(data).decode("utf-8")
    except UnicodeDecodeError:
        raise PayloadValidationError("Source file must contain UTF-8 text.") from None


def read_payload(args: argparse.Namespace) -> str:
    if args.source_env:
        value = os.environ.get(args.source_env)
        if value is None:
            raise CliConfigurationError("Source environment variable is not set.")
        return value
    if args.source_file:
        return _read_source_file(args.source_file)
    try:
        return getpass.getpass("Paste profile source (input hidden): ")
    except (EOFError, KeyboardInterrupt):
        raise UserCancelled from None


def reject_network_source(payload: str) -> None:
    value = payload.strip()
    match = _URI_SCHEME_RE.match(value)
    if match is None:
        return
    scheme = match.group(0)[:-1].lower()
    if scheme == "vless":
        return
    if scheme == "https":
        raise CliConfigurationError(HTTPS_MESSAGE)
    raise CliConfigurationError(UNSUPPORTED_URL_MESSAGE)


def render_text_list(nodes: Sequence[NodeRecord]) -> None:
    print("Compatible nodes:")
    for index, node in enumerate(nodes, start=1):
        summary = safe_node_summary(node, index)
        flow = "vision" if summary["flow"] == "xtls-rprx-vision" else "no flow"
        print(
            f"{index}. {summary['label']} — reality/tcp — {flow} — port {summary['port']}"
        )


def render_json_list(nodes: Sequence[NodeRecord]) -> None:
    summaries = [safe_node_summary(node, index) for index, node in enumerate(nodes, start=1)]
    print(json.dumps({"compatible_nodes": summaries}, ensure_ascii=False, indent=2))


def _prompt(prompt: str) -> str:
    try:
        value = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        raise UserCancelled from None
    if value.lower() in {"q", "quit", "cancel"}:
        raise UserCancelled
    return value


def interactive_selection(nodes: Sequence[NodeRecord]) -> SelectedNodes:
    render_text_list(nodes)
    while True:
        value = _prompt("Primary node number (or q to cancel): ")
        try:
            primary = int(value)
            if not 1 <= primary <= len(nodes):
                raise ValueError
            break
        except ValueError:
            print("Enter a valid node number.", file=sys.stderr)
    while True:
        value = _prompt("Fallback node numbers, comma-separated (blank for none): ")
        try:
            fallbacks = [] if not value else [int(item.strip()) for item in value.split(",")]
            return select_nodes(nodes, primary, fallbacks)
        except (ValueError, SelectionError) as exc:
            message = str(exc) if isinstance(exc, SelectionError) else "Enter valid node numbers."
            print(message, file=sys.stderr)


def render_selection(selection: SelectedNodes, nodes: Sequence[NodeRecord]) -> None:
    by_identity = {node.canonical_identity: index for index, node in enumerate(nodes, start=1)}
    print("Selection:")
    selected = (("primary", selection.primary),) + tuple(
        (f"fallback-{index}", node) for index, node in enumerate(selection.fallbacks, start=1)
    )
    for role, node in selected:
        index = by_identity[node.canonical_identity]
        summary = safe_node_summary(node, index)
        print(f"- {role}: {summary['label']} — reality/tcp — port {summary['port']}")


def confirm_write() -> bool:
    return _prompt("Write private profiles.json? [y/N]: ").lower() in {"y", "yes"}


def run(args: argparse.Namespace) -> int:
    validate_args(args)
    payload = read_payload(args)
    reject_network_source(payload)
    nodes = parse_compatible_nodes(payload)

    if args.list:
        if args.json:
            render_json_list(nodes)
        else:
            render_text_list(nodes)
        return 0

    if args.primary_index is None:
        selection = interactive_selection(nodes)
    else:
        selection = select_nodes(nodes, args.primary_index, args.fallback_index)
        render_selection(selection, nodes)

    if args.dry_run:
        print("Dry run complete; no profiles file was written.")
        return 0

    output = Path(args.output)
    if output.exists() and not args.force:
        if args.yes or not sys.stdin.isatty():
            raise OutputExistsError("Output file already exists; use --force to replace it.")
        if _prompt("Output exists. Replace it? [y/N]: ").lower() not in {"y", "yes"}:
            raise UserCancelled
        overwrite = True
    else:
        overwrite = args.force

    if not args.yes and not confirm_write():
        raise UserCancelled
    document = build_profiles_document(selection)
    write_private_json(output, document, overwrite=overwrite)
    print("Private profiles file written with restrictive permissions.")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        return run(args)
    except CliConfigurationError as exc:
        print(f"routerkit-profile-source: {exc}", file=sys.stderr)
        return 2
    except UserCancelled:
        print("Cancelled; no profiles file was written.", file=sys.stderr)
        return 1
    except OutputExistsError as exc:
        print(f"routerkit-profile-source: {exc}", file=sys.stderr)
        return 2
    except (ProfileSourceError, SelectionError) as exc:
        print(f"routerkit-profile-source: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
