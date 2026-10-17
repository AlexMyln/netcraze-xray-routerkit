#!/usr/bin/env python3
"""Offline publication guard: tracked files, index, and all reachable Git history.

Diagnostics contain object IDs, rule names and line numbers, never matched data
or filenames (a filename itself can contain a credential). No network requests.
"""

import argparse
import fnmatch
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


FORBIDDEN_NAMES = (
    ".env", ".env.*", "*.env", "*.env.*", "*.cfg", "*.backup", "*.bak",
    "*.secret", "*.secrets", "*.pem", "*.key", "*.p12", "*.pfx",
    "*.keystore", "*.jks", "*.kdbx", "*.ovpn", "id_rsa*", "id_dsa*",
    "id_ecdsa*", "id_ed25519*", "*.tar", "*.gz", "*.tgz", "*.zip",
    "*.bz2", "*.xz", "*.7z", "*.rar", "*.vless", "*.sub",
    "profiles.json", "profiles.yaml", "subscriptions.txt", "subscription*.txt",
    "sub*.txt", "03_inbounds.json", "04_outbounds.json", "05_routing.json",
    "startup-config*", "*.startup-config", "final-netcraze-xray-*",
    "netcraze-hopper4g-final-*",
)
FORBIDDEN_DIRS = {"secrets", ".ssh", ".aws", ".gnupg", "generated", ".routerkit-live-install"}
EMAIL = re.compile(rb"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
PATTERNS = (
    ("private-key", rb"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----"),
    ("github-token", rb"\b(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{50,255})\b"),
    ("aws-access-key", rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    ("provider-token", rb"\b(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{32,}|xox[baprs]-[A-Za-z0-9-]{20,})\b"),
    ("jwt", rb"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    ("authorization", rb"\b(?:Bearer|Basic)[ \t]+[A-Za-z0-9+/_.=-]{20,}"),
    ("vless-profile", rb"vless://[^\s\"'<>]*@"),
    ("reality-query", rb"(?:[?&#]|\b)(?:pbk|sid)=[A-Za-z0-9_-]{2,}"),
    ("uuid", rb"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"),
    ("private-subscription-host", rb"link" + rb"\.vertpath|frost" + rb"-api"),
)
PATTERNS = tuple((name, re.compile(pattern, re.I)) for name, pattern in PATTERNS)
ASSIGNMENT = re.compile(
    rb"\b(?:password|passwd|api[_-]?key|access[_-]?token|client[_-]?secret)"
    rb"[\"']?\s*[:=]\s*[\"']([^\s\"']{16,})[\"']", re.I
)
ARCHIVE_MAGIC = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08", b"\x1f\x8b", b"BZh", b"\xfd7zXZ\x00", b"7z\xbc\xaf\x27\x1c", b"Rar!\x1a\x07")
MAX_BLOB_BYTES = 16 * 1024 * 1024


class GuardError(Exception):
    pass


def git(root, *args, data=None):
    result = subprocess.run(["git", "-C", str(root), *args], input=data,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        # Git error messages can contain paths, URLs or identity data.
        raise GuardError("Git operation failed; inspect locally without publishing raw output")
    return result.stdout


def forbidden_path(name):
    path = PurePosixPath(name)
    parts = tuple(part.lower() for part in path.parts)
    return (any(part in FORBIDDEN_DIRS for part in parts[:-1])
            or "/opt/etc/xray/" in "/" + "/".join(parts)
            or any(fnmatch.fnmatchcase(parts[-1], pattern) for pattern in FORBIDDEN_NAMES))


def noreply(email):
    email = email.lower()
    return (email == b"noreply@github.com"
            or re.fullmatch(rb"[a-z0-9+_.-]+@users\.noreply\.github\.com", email) is not None)


def example_email(email):
    domain = email.lower().rsplit(b"@", 1)[-1]
    return any(domain == suffix or domain.endswith(b"." + suffix)
               for suffix in (b"example.com", b"example.org", b"example.net", b"example", b"invalid", b"test"))


def content_findings(data):
    """Yield only (rule, line); scan binary bytes and common UTF-16 exports too."""
    if data.startswith(ARCHIVE_MAGIC) or data[257:263] in (b"ustar\x00", b"ustar "):
        yield "archive-content", 1
    forms = [data]
    if b"\x00" in data:
        for encoding in ("utf-16-le", "utf-16-be"):
            forms.append(data.decode(encoding, errors="ignore").encode("utf-8"))
    findings = set()
    for text in forms:
        for rule, pattern in PATTERNS:
            for match in pattern.finditer(text):
                findings.add((rule, text.count(b"\n", 0, match.start()) + 1))
        for match in EMAIL.finditer(text):
            if not noreply(match.group()) and not example_email(match.group()):
                findings.add(("personal-email", text.count(b"\n", 0, match.start()) + 1))
        for match in ASSIGNMENT.finditer(text):
            value = match.group(1).lower()
            if not any(word in value for word in (b"example", b"placeholder", b"redacted", b"synthetic")):
                findings.add(("credential-assignment", text.count(b"\n", 0, match.start()) + 1))
    yield from sorted(findings)


def scan(root):
    root = Path(git(root, "rev-parse", "--show-toplevel").decode().strip())
    if git(root, "rev-parse", "--is-shallow-repository").strip() != b"false":
        raise GuardError("Full Git history required; fetch with depth 0 before scanning")
    commits = git(root, "rev-list", "--all").splitlines()
    if not commits:
        raise GuardError("No reachable commits to check")
    findings = set()
    blobs = set()
    trees = set()

    def add(rule, oid, line=1):
        findings.add((oid.decode("ascii"), line, rule))

    def entry(mode, oid, name):
        if forbidden_path(os.fsdecode(name)):
            add("forbidden-path", oid)
        if mode == b"160000":
            add("unreviewed-submodule", oid)
        else:
            blobs.add(oid)

    for oid in commits:
        raw = git(root, "cat-file", "commit", oid.decode("ascii"))
        headers, message = raw.split(b"\n\n", 1)
        for header in headers.splitlines():
            if header.startswith(b"tree "):
                trees.add(header.split()[1])
            elif header.startswith((b"author ", b"committer ")):
                match = re.search(rb"<([^<>]*)> [0-9]+ [+-][0-9]{4}$", header)
                if match is None or not noreply(match.group(1)):
                    add("commit-email-must-be-github-noreply", oid)
        for rule, line in content_findings(message):
            add(rule, oid, line)

    # Annotated tags have their own identity and message outside commit history.
    for record in git(root, "for-each-ref", "--format=%(objecttype) %(objectname)", "refs/tags").splitlines():
        kind, oid = record.split()
        if kind != b"tag":
            continue
        seen = set()
        while kind == b"tag" and oid not in seen:
            seen.add(oid)
            headers, message = git(root, "cat-file", "tag", oid.decode("ascii")).split(b"\n\n", 1)
            for header in headers.splitlines():
                if header.startswith(b"tagger "):
                    match = re.search(rb"<([^<>]*)> [0-9]+ [+-][0-9]{4}$", header)
                    if match is None or not noreply(match.group(1)):
                        add("tag-email-must-be-github-noreply", oid)
            for rule, line in content_findings(message):
                add(rule, oid, line)
            fields = dict(line.split(b" ", 1) for line in headers.splitlines() if not line.startswith(b" "))
            kind, oid = fields[b"type"], fields[b"object"]

    for tree in trees:
        for record in git(root, "ls-tree", "-r", "-z", tree.decode("ascii")).split(b"\x00"):
            if record:
                meta, name = record.split(b"\t", 1)
                mode, kind, oid = meta.split()
                entry(mode, oid, name)

    for record in git(root, "ls-files", "--stage", "-z").split(b"\x00"):
        if not record:
            continue
        meta, name = record.split(b"\t", 1)
        mode, oid, stage = meta.split()
        entry(mode, oid, name)
        if stage != b"0":
            add("unmerged-index", oid)
        path = root / os.fsdecode(name)
        if mode == b"160000" or not (path.exists() or path.is_symlink()):
            continue
        if path.is_symlink():
            data = os.fsencode(os.readlink(path))
        elif path.stat().st_size > MAX_BLOB_BYTES:
            add("oversized-working-file", oid)
            continue
        else:
            data = path.read_bytes()
        for rule, line in content_findings(data):
            add("working-" + rule, oid, line)

    # --batch avoids one process per blob. Read lengths exactly, even for binary.
    with subprocess.Popen(["git", "-C", str(root), "cat-file", "--batch"],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL) as process:
        for oid in sorted(blobs):
            process.stdin.write(oid + b"\n")
            process.stdin.flush()
            header = process.stdout.readline().split()
            if len(header) != 3 or header[1] != b"blob":
                raise GuardError("Missing blob; incomplete history cannot pass")
            size = int(header[2])
            remaining = size
            chunks = []
            while remaining:
                chunk = process.stdout.read(min(remaining, 1024 * 1024))
                if not chunk:
                    raise GuardError("Truncated Git object")
                remaining -= len(chunk)
                if size <= MAX_BLOB_BYTES:
                    chunks.append(chunk)
            if process.stdout.read(1) != b"\n":
                raise GuardError("Invalid Git object framing")
            if size > MAX_BLOB_BYTES:
                add("oversized-blob", oid)
            else:
                for rule, line in content_findings(b"".join(chunks)):
                    add(rule, oid, line)
        process.stdin.close()
        if process.wait() != 0:
            raise GuardError("Git object scan failed")
    return sorted(findings), len(commits), len(blobs)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    try:
        findings, commits, blobs = scan(args.repo)
    except (GuardError, OSError, ValueError):
        print("Public data guard could not complete; full readable Git history is required.", file=sys.stderr)
        return 2
    for oid, line, rule in findings:
        print("object {} line {}: {} [REDACTED]".format(oid, line, rule))
    if findings:
        print("Public data guard failed: {} finding(s).".format(len(findings)))
        return 1
    print("Public data guard passed: {} commits, {} blobs, index and tracked working files.".format(commits, blobs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
