# Contributing

Use Python 3.10+ and synthetic fixtures only. Keep changes focused and document
source format assumptions and platform limits. Do not attach real chats, provider
exports, credentials, private absolute paths, or runtime receipts to issues or PRs.

Before proposing a change, run:

```sh
python3 -B -m unittest discover -s tests -q
python3 -B tools/demo.py
python3 -B tools/audit_public_tree.py
git diff --check
```

When adding an intended public file, update `PUBLIC_FILES.txt` explicitly. Do not
use an automated broad folder copy to update the list. Keep private data outside
the repository; `.gitignore` is only a secondary guard and cannot protect data
already tracked by Git.

Adapter changes should include exact synthetic source records, expected visible
output, missing-data behavior and replay checks. Do not silently discard unknown
public structure, infer complete platform coverage, or flatten branches into a
false single conversation. Existing revisions must remain readable and unchanged.

Use GitHub's private vulnerability reporting if enabled, or contact the repository
maintainer privately for issues involving sensitive data. Do not publish private
logs as a reproduction. This repository currently has no hosted service or
guaranteed response SLA.

Before committing, stage the intended public changes and run
`python3 -B tools/audit_public_tree.py --tracked`. This mode reads the allowlist,
file modes and blob contents from the captured Git index, including when working
copies differ or are absent. A later staging operation needs a new audit. The
default audit checks working files; neither mode audits historical commits.

Contributions are made under the repository's MIT license. Include provenance and
license details before adding any third-party code; do not assume an accessible
skill, snippet or document is automatically redistributable.
