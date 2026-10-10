# Local validation

Latest candidate check: 2026-10-10. Environment: macOS, Python 3.12.14.
Earlier extraction checkpoints below retain their original scope.

This is a local source preparation checkpoint, not a published release or a
claim of complete cross-platform support.

## MOSS 0.2.0-rc.2 candidate — 2026-10-10

- Removed mandatory communication style from the public protocol and memory
  templates, and the fixed Chinese language/character limit from diagnosis
  instructions. Evidence, authority, output schema and resource guards remain.
- Protocol identity and installation paths now use `ai-navigation/1.0.0-rc.2`.
  Existing adopted protocols are not replaced by this source update.
- All 243 synthetic tests passed with 0 failures and 0 errors. Both lifecycle
  demos and the 76-file publication audit passed.
- A source-only clean copy passed the same checks from an unrelated directory
  with an empty HOME, no global Git configuration and Python site packages
  disabled (`-S`). The two runs are the same 243 distinct test cases.
- The source/templates no longer contain the former STE or fixed-language
  style requirements. No private deployment, account or model call was used.

## MOSS 0.2.0-rc.1 candidate — 2026-10-10

- 243 synthetic tests passed, including 20 new MOSS tests, with 0 failures and
  0 errors. An identical source-only clean copy passed the same suite. These are
  243 distinct test cases, not 486 independent cases from two runs.
- The clean copy contained only 76 allowlisted public files. It ran from an
  unrelated directory with an empty HOME, without global Git configuration,
  with Python site packages disabled (`-S`) and without private host data.
- Both the original archive demo and the new MOSS demo passed. Active MOSS
  delivered nine actual bodies; disabled navigation returned five compact files.
  New project inheritance, unchanged original project files, private handoff
  delivery hashes and no startup archive-body reads were verified.
- Tests exercised stale status hashes, protocol/adoption conflicts, incomplete
  required records, lock timeout, recognized interrupted transactions even when
  navigation is off, unrelated/terminal journals, symlink rejection, ambiguous
  identities and non-overwriting installation/export.
- A copied MOSS skill outside the checkout invoked the explicitly selected clean
  checkout successfully. Both skill frontmatter validators passed. This verifies
  callable skills, not automatic discovery or activation in every client.
- Working-tree allowlist/credential/path audit: 76 files, 0 findings. All 35 local
  Markdown links resolved; 38 Python files parsed with Python 3.10 syntax.
  Python 3.10 runtime, Linux and Windows were not tested.
- The candidate was prepared locally. No live model calls, account access,
  private-history capture, host reconfiguration or GitHub publication was part
  of these tests. Standalone demonstrations use only the standard library.

New opt-in health/diagnosis tests use synthetic metadata and mocked execution;
no model usage or fixed diagnosis cost is asserted. The reader guards only
registered supported operation journals, not every external editor or writer.
Final staged-tree and package checks are recorded in the candidate preparation
receipt outside this public source tree.

## Cross-agent capture skill

### ChatGPT source extension

- 186 synthetic unit/integration tests passed, including 22 capture tests: the
  prior 10 Hermes/Claude Code tests and 12 new ChatGPT cases.
- Six independently keyed sources (two Hermes profiles, a Claude main session,
  two Claude children and one ChatGPT conversation) were linked to one project.
  ChatGPT reused the existing provider/conversation identity, so a bundle already
  imported through the original read-thread adapter deduplicated correctly.
- Captured response pages survived import exactly when no redaction was needed.
  Source replay verified edited messages and credential redaction without
  collapsing their identities. Older snapshots remained unchanged.
- Capture rejected inconsistent conversation IDs, incomplete or inconsistent
  pagination, known truncation flags/markers, private engine items, account
  exports and transport wrappers. Unknown public item types remained in the raw
  source and produced explicit coverage gaps. Reaching the last page did not
  assert whole-history completeness or independently verified retrieval.
- Writer locking, unchanged source files, compact memory and synchronization
  settings, and metadata-only receipts were checked. Skill validation, the
  synthetic lifecycle demo, the 57-file working-tree publication audit and
  whitespace checks passed.
- No live ChatGPT retrieval, platform installation, source registration,
  scheduling or publication was performed. ChatGPT replay covers preserved
  response pages and their source mappings; Hermes/Claude Code replay covers
  their retained normalized visible projections.

### Initial Hermes and Claude Code checkpoint

- 174 synthetic unit/integration tests passed: the prior 164 plus 10 capture
  tests for Hermes exports and Claude Code main/subagent JSONL projections.
- Five independently keyed sources (two Hermes profiles, a Claude main session,
  and two Claude children) were linked to one synthetic project without merging
  their identities. Exact text, available parent relationships and tool data
  survived normalization; known system/meta/thinking fields were excluded.
- Repeated exports deduplicated; appended content produced a new revision while
  the old one remained byte-for-byte unchanged. Source files, compact memory and
  synchronization settings stayed unchanged. The helper respected an active
  sync writer lock, rejected partial/unknown/mismatched sources, and returned no
  conversation bodies in receipts. Credential-signature redaction was exercised.
- Skill frontmatter validation, the synthetic lifecycle demo, the 56-file
  working-tree publication audit and whitespace checks passed. All new fixtures
  were invented; no private provider histories were used.
- No platform hook, skill discovery configuration or scheduler was installed.
  These are file-level synthetic checks, not live acceptance of an installed
  Hermes fork or Claude Code version. Replay verifies the retained normalized
  visible projection, not independently every field of the native source.

The helper's runtime still uses only the standard library and repository code.
The separate skill-authoring validator used an already installed PyYAML package;
PyYAML is not a runtime dependency of the skill.

## Publication review fixes

- 164 synthetic unit/integration tests passed after the fixes: the original 123,
  19 additional Git-index audit cases, and 22 redaction/replay cases.
- Serialized JSON sources are decoded before redaction. Escaped secrets and
  sensitive metadata are removed, while unchanged JSON lexemes and typed media
  payloads retain their original representation.
- Source assembly plans preserve edited message identities and distinct visible
  contributions after redaction. Replay rejects missing source rows, omitted
  distinct content, invented identity aliases and unsupported merge targets.
- Synchronization rechecks unchanged files after adapter/redaction policy
  upgrades. Intact legacy snapshots that cannot replay remain untouched while a
  fresh capture is verified; damaged stored files still stop synchronization.
- Git-index publication checks read the staged allowlist, modes and actual blobs,
  including when working files differ, are deleted, or replacement refs exist.
- The synthetic lifecycle demo and the 50-file working-tree publication check
  passed. Regression triggers failed against the initial implementation and
  passed after the fixes. Historical snapshots were not rewritten.

## Initial extraction checks

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
