# Contributing

Contributions are welcome, but keep the project safe by default.

## Rules

- Do not add firewall/TPROXY behavior to the default flow.
- Do not make Xray listen on LAN/WAN by default.
- Do not commit real router configs, generated Xray configs, subscription URLs, or backup archives.
- Keep examples generic and secret-free.
- Keep Web UI policy operations documented and explicit.
- Prefer dry-run and read-only checks before writes.

## Local Checks

Before opening a PR:

```sh
sh -n scripts/install-xray-direct.sh
sh -n scripts/healthcheck.sh
sh -n scripts/backup.sh
sh -n templates/S23xray-direct
python3 -m py_compile scripts/generate-xray-profiles.py
```

Also run a secret scan before publishing any branch from a real router workspace. If a scan finds a real subscription link, UUID, Reality key, backup archive, or `/opt/etc/xray` config, stop and clean the workspace before committing.
