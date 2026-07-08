# Security Policy

This project is a toolkit for router-side Xray configuration. It can handle highly sensitive data.

## Never Publish

Do not publish or commit:

- subscription URLs;
- real VLESS links;
- UUIDs from real subscription links;
- Reality `pbk`, `sid`, `spx`;
- local `profiles.json` files that contain URLs;
- real router `startup-config` files;
- `/opt/etc/xray` configs from a production router;
- Entware/Xray backup archives.

If a secret scan reports a real value, stop the release. Rotate the exposed credential/link before making the repository public.

## Recommended Practice

- Use environment variables or local ignored `profiles.json`.
- Use private transfer for backups and generated configs.
- Bind SOCKS listeners to `127.0.0.1` only.
- Do not expose Entware SSH/dropbear to WAN.
- Change the default Entware root password after installation.
- Review `git status --short` before every commit.
- Keep release assets secret-free; do not attach router backups or generated config archives.

## Reporting

Open a private security report or contact the maintainer before posting suspected secret exposure publicly.
