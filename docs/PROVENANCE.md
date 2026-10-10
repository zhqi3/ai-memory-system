# Code provenance and publication scope

This repository is a clean extraction prepared on 2026-10-08 from a locally
developed project-memory and context-archive implementation, with the owner's
authorization. It does not include the operational data store or its history.

## Included by explicit file selection

- Seven archive modules: `adapters`, `auto_sync`, `context_library`,
  `conversation_catalog`, `reading_reviews`, `revision_selection`, and
  `source_verification`.
- Ten existing synthetic test modules, retained with portable temporary-directory
  selection instead of a host-specific temporary path.
- Newly authored workspace initialization, compact startup, demonstration,
  publication checks, templates, integration guides and corresponding tests.
- A newly authored portable `archive-project-context` skill and visible-export
  normalization helper for Hermes and Claude Code, plus a ChatGPT bundle route
  reusing the existing read-thread adapter, with synthetic regressions.
  Upstream documentation and public schema code were inspected as references;
  no provider implementation or real provider transcript is bundled.

The public extraction requires an explicit `--root` for archive and sync CLI
commands. No private runtime configuration is used as a default. Templates start
with no source registrations and with automatic capture disabled.

## Excluded

Live compact memory, real transcripts, conversation IDs, personal project links,
provider exports, source artifacts, credentials, runtime receipts, schedules,
private configurations, prior snapshots, local backups and internal agent memory
are excluded. External skill repositories and their source code are not bundled.
General memory templates and agent guidance were written for this repository.

No third-party Python implementation or vendored dependency is included. Runtime
imports use the Python standard library and repository modules. License text uses
the standard [MIT form](https://choosealicense.com/licenses/mit/), selected by the
project owner. The copyright attribution uses the public project contributor name.
Imported conversations retain their own ownership and privacy requirements; the
software license does not authorize distribution of user archives.

`PUBLIC_FILES.txt` enumerates the intended public file set. The publication audit
checks this allowlist and common accidental data/secret signatures, but cannot
establish legal ownership or prove absence of all possible personal information.

## Extraction probe

Decision: whether the archive implementation can become a standalone public
toolkit without including its operational data or private host configuration.
Bedrock: the seven source modules, ten synthetic test modules, their imports and
CLI defaults, plus the tests and clean source-copy demonstration.

| Claim | Observation and method | Status |
| --- | --- | --- |
| Archive code needs private workspace data to run | Explicit temporary configuration and synthetic sources passed the retained tests | DISPROVEN |
| Existing CLI defaults are suitable for a public checkout | Original defaults selected a directory beside the code; public entry points now require `--root` | MODIFIED |
| Existing tests use portable temporary paths | Three test modules used a host-specific path; they now resolve the standard temporary directory | MODIFIED |
| Public files work independently of the original installation | Clean source copy and empty-HOME demo passed | PROVEN within tested workflow |
| Other operating systems and native platform exports work | Not exercised or implemented for all platforms | UNKNOWN; no general support claim |
| Private data was included in the public file set | Explicit 49-file selection, identifier/signature checks and index inspection found none | No inclusion observed; not an exhaustive privacy proof |

Consequences: keep data roots explicit, ship empty disabled sync configuration,
use synthetic fixtures, document host-only integrations, and limit platform
claims to the [validation evidence](VALIDATION.md).

## MOSS candidate extraction — 2026-10-10

The owner authorized adding the locally authored navigation implementation to
this repository. The navigation reader, switch logic and export helper were
extracted from that owned shared implementation; the startup adapter was changed
to accept only this repository's explicit workspace marker. No host settings,
project adoption records, private protocols with source links, logs or actual
project snapshots were included.

The public candidate protocol is a newly identified portable version based on
the owned navigation rules, without mandatory communication style or
private source-review links. It is adopted only by the user's explicit install
action. Generic installation, skill wrapper, docs, tests and demo were authored
for this candidate. The existing MIT software license continues to apply;
imported user conversations remain outside this software distribution.
