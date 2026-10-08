# Local validation

Date: 2026-10-08. Environment: macOS, Python 3.12.14, Git 2.54.0.

This is a local source preparation checkpoint, not a published release or a
claim of complete cross-platform support.

## Checks

- 123 synthetic unit/integration tests passed: 107 retained archive tests,
  12 workspace and isolation tests, and 4 publication-audit tests.
- The synthetic demo completed initialization, compact startup, import,
  source replay and reading retrieval in a temporary directory. It reported
  five compact startup files, three synthetic records, no startup archive-body
  reads, and no automatic reading acknowledgement.
- CLI initialization and startup succeeded from an unrelated working directory
  with an empty temporary HOME and no global AI configuration.
- A fresh copy of only the 49 allowlisted source files ran the demo from an
  unrelated directory with an empty temporary HOME. No private sibling directory
  or original source tree was required by that run.
- Existing destinations, duplicate projects, identity mismatches, repository
  data destinations and symlink substitutions are rejected by setup tests.
- All 49 allowlisted files passed working-tree and Git-index publication checks.
  No common credential signature or personal-home-path match was reported.
  All 22 local Markdown links resolved; Git's staged whitespace check passed.
- Hash comparison confirmed that the 17 extracted source/test files remained
  unchanged in the original local implementation.

## Reproduce

```sh
python3 -B -m unittest discover -s tests -q
python3 -B tools/demo.py
python3 -B tools/audit_public_tree.py
git diff --check
```

After staging the intended public files, also run:

```sh
python3 -B tools/audit_public_tree.py --tracked
git diff --cached --check
```

## Limits

The tests do not access a real provider account, import a private transcript,
enable a scheduler, publish to GitHub, or prove support for future provider
schemas. No clean operating-system installation, Linux, Windows, or every
supported Python version was exercised. A file allowlist and signature scan
reduce accidental exposure but are not exhaustive privacy or security audits.

The expected Python minimum is 3.10 based on the language features used; only
the Python version above was executed for this checkpoint. Automatic sync was
tested against synthetic files; no scheduler is shipped or installed here.
