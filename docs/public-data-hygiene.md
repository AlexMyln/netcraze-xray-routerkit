# Publishing without private data

Keep credentials, dotenv files, SSH keys, certificate/key bundles, subscriptions,
generated Xray configurations and router backups outside Git. `.gitignore` covers
these inputs; it does not protect files already tracked or added with `git add -f`.
Do not use real values in examples, tests, commit messages or filenames.

Use your own verified GitHub noreply identity before committing:

```sh
git config --local user.email 'YOUR_GITHUB_NOREPLY_ADDRESS'
git config --local user.useConfigOnly true
```

Copy the exact noreply address from GitHub **Settings → Emails**. Enable email
privacy and the setting that blocks command-line pushes exposing your personal
email. Use noreply in author, committer, coauthor trailers and annotated tags.
The repository owner's noreply must not be used to impersonate another contributor.

After staging a change, run:

```sh
python3 scripts/check_public_data.py
python3 -m unittest tests.test_check_public_data -v
```

The offline guard checks all reachable commits and tags, every historical tree
path, file blobs, commit/tag messages, identities, the index and tracked working
files. It rejects shallow history, missing objects, submodules, oversized blobs,
private filenames and archives (including common archive signatures under another
extension). Binary blobs and UTF-16 exports are checked too. Ignored, untracked
private inputs are intentionally outside the publication set.

Rules detect private keys, common provider tokens, authorization values, JWTs,
VLESS profiles, Reality query values, UUIDs, long credential assignments and
non-example email addresses. Tests and examples have no directory exemption.
Only GitHub noreply addresses are accepted in commit/tag identity fields.
Diagnostics print an object ID, rule and line number, without matched bytes or
filenames. Inspect the indicated object locally; do not paste raw findings into
public issues or CI logs. Pattern checks are an additional barrier, not proof
that arbitrary private information or encoded credentials are absent.

CI fetches complete branch/tag history, runs the guard before other checks, uses
read-only repository permissions and removes checkout credentials. Guard regression
tests run on Python 3.8 and the current Python version. Keep GitHub secret scanning
and push protection enabled as independent publication controls.

## History email cleanup (2026-10-17)

Personal email references are replaced with the owner's verified GitHub noreply
in author/committer fields and coauthor trailers across public branches and tags.
Historical file trees, authors' names, timestamps and merge topology are preserved.
Commit IDs and descendant IDs change; old commit signatures cannot remain valid.
Release tags retain their names and file contents. Recorded historical baseline
IDs in acceptance documents remain records of the original runs, not new hardware
acceptance claims.

Use a fresh clone after the rewrite. Do not merge or push old branches/tags from
an existing clone: doing so can restore the original email-bearing history. Keep
any recovery bundle private and outside the repository. Existing forks, local
clones, GitHub pull-request refs and cached commit pages can retain original
objects; branch/tag rewriting cannot remove those copies. Check those locations
separately and use GitHub's support process where necessary.

Русская версия: [public-data-hygiene.ru.md](public-data-hygiene.ru.md).
