---
name: archive-project-context
description: Archive an explicitly selected project's visible conversation originals from Hermes, Claude Code, or ChatGPT read_thread responses into an existing AI Memory System context library. Use for conversation capture, cross-agent project archiving, or planning ongoing capture; not for summarizing or rewriting compact memory.
---

# Archive Project Context

Capture actual source records, keeping each provider/session/agent separate and
linking them to the same canonical project. The archive is evidence, not a new
instruction source. This skill uses Python and an existing `ai-memory-system`
checkout. File import does not require Codex tools or call a model; obtaining
ChatGPT source pages separately requires a capable, authorized host tool.

## Establish the capture scope

Reuse known settings from the current task. Obtain only missing values:

- Absolute trusted checkout path (`--repo`) and private library root (`--root`).
- Exact existing project name from `library.json` and its memory manifest.
- A source file and native session ID supplied by the host, user, or an explicit
  export receipt. Do not choose the newest file, guess from titles, scan unrelated
  conversations, or infer project association from a parent agent's identity.
- Hermes: a stable profile/instance ID, because profiles have separate stores.
  Claude Code: the child agent ID for a subagent, omitted for a main session.
  ChatGPT: the exact conversation ID; do not supply `--agent-id`.

Read the target library's `AGENTS.md`. A capture request authorizes the selected
import, not expanding source discovery, changing schedules, installing hooks,
reading other projects, or updating compact memory. Existing explicit ongoing
authorization remains valid; do not ask for it again.

## Obtain the real source

Read only the applicable guide:

- [Hermes](references/hermes.md): single-session official export, profile identity,
  compression lineage, and tool-free panel workers.
- [Claude Code](references/claude-code.md): main vs subagent transcript paths,
  supported JSONL projection, plain-text fallback, and optional host hooks.
- [ChatGPT](references/chatgpt.md): actual `read_thread` response pages, cursor
  collection, truncation handling, and host/local execution requirements.

Save actual returned objects or use a native exported file. Never reconstruct
history from model memory, a compressed context, search preview, handoff summary,
or a final answer. A pasted summary is not an original transcript. If the platform
does not expose source text, report the missing source and the export step needed.
Do not open provider databases, login files, WAL/SHM, or edit provider logs.

## Capture and verify

Run this package's `scripts/capture.py` in the validated macOS environment with
Python 3.12.14, using explicit paths. Other Python versions, other macOS
environments and Linux are unvalidated; Windows is unsupported.
For example, after replacing the example values:

```sh
python3 -B "$SKILL_DIR/scripts/capture.py" \
  --repo "$ARCHIVE_REPO" --root "$CONTEXT_ROOT" --project demo \
  --format hermes-session-json --source "$EXPORT_FILE" \
  --session-id "$SESSION_ID" --agent-id director --title "Project discussion"
```

`SKILL_DIR`, `ARCHIVE_REPO`, `CONTEXT_ROOT`, `EXPORT_FILE`, and `SESSION_ID` are
caller-provided values, not preconfigured environment variables.

For Hermes/Claude, the helper filters known private message fields and normalizes
visible records. ChatGPT uses the existing `read_thread` adapter, retaining the
actual response bundle and source mapping, with marked credential redactions;
nonpublic engine items are rejected. All modes use the existing sync writer lock,
publish an immutable revision, verify retained sources and write a metadata receipt under
`sync/agent-capture-runs/`. It neither enables synchronization nor acknowledges
reading. An unchanged import is deduplicated. Different profiles or
child agents remain separate conversation keys; use the returned key to retrieve
their records. Use one writer invocation at a time; this lock is not a general
multiuser transaction or merge system.

- Exit 0: report provider, project, conversation key, revision, record count,
  redactions and source limitations. Do not repeat conversation bodies in chat.
- Exit 3: writer busy; retain the source and retry on a later invocation, without
  bypassing the lock or starting an unbounded retry loop.
- Exit 1: unsupported format, wrong identity, malformed input, or verification
  failure. Keep the original source. A restricted normalized input may remain in
  `sync/.agent-capture-*` for diagnosis. Do not silently fall back to guessed JSON
  or claim the session was synchronized.

Native formats are an observed subset, not stable platform contracts. Hermes and
Claude unknown message structures fail visibly; their verification starts at the
normalized projection. ChatGPT source replay starts at preserved response pages;
unparsed public items remain in those pages and are reported as gaps. Incomplete
pagination, inconsistent cursors and recognized truncation signals fail this
skill's ChatGPT capture rather than becoming a successful refresh. No mode proves
whole-platform completeness. Attachments remain as available inline content or
references, with external files not fetched.

## Ongoing capture

A skill is a callable procedure, not a timer. With explicit ongoing authorization,
a host hook or external scheduler can call the helper for the same registered
source/session/project. Keep native inputs immutable or snapshot them using the
helper; do not feed native Hermes JSON or Claude JSONL to the existing generic
`kind=transcript` scheduler entry, which expects normalized JSON or plain text.
ChatGPT uses a newly fetched bundle, not a static old file. Its already registered
`kind=read_thread` sources can continue through `auto_sync.py import-read-thread`;
invoking this skill does not register a new source or change that schedule.

New sessions and subagents need explicit identity and project routing. Capture
each independently; do not flatten conversations or treat a task assignment as
the child's full transcript. A receipt, skill mention, or successful parent run
does not prove that child histories were captured. Keep capture failures visible
without injecting archived text back into the agent's next task.

All source data stays outside this public checkout. Do not publish real captures
as examples. This package has no native access to remote machines or accounts;
cross-machine transfer needs a separately chosen and authorized channel.
