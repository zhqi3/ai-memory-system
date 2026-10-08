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
