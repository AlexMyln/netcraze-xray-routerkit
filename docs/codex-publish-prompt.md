# Codex prompt: publish to GitHub

Use this prompt after creating an empty GitHub repository.

```text
Goal: publish this local starter kit as a public GitHub repository.

Repository name: netcraze-xray-routerkit

Rules:
- Do not commit real subscription URLs.
- Do not commit VLESS links.
- Do not commit router startup-config backups.
- Do not commit Entware/Xray backup archives.
- Do not commit generated/ or real 04_outbounds.json.
- Verify `.gitignore` protects secrets.
- Run a secret scan before first commit:
  - grep for real VLESS links
  - grep for private subscription hostnames
  - grep for Reality key query parameters
  - grep for UUID-like strings
- If any real secret is found, stop.

Steps:
1. Inspect files.
2. Run shell syntax checks:
   `sh -n scripts/install-xray-direct.sh`
   `sh -n scripts/healthcheck.sh`
   `sh -n scripts/backup.sh`
   `sh -n templates/S23xray-direct`
3. Run Python syntax check:
   `python3 -m py_compile scripts/generate-xray-profiles.py`
4. Initialize git if needed.
5. Create first commit.
6. Push to GitHub.
7. Report repository URL.
```
