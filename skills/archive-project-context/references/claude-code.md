# Claude Code source capture

## Select the source and identity

Prefer the host's exact session ID and transcript path. Do not derive a project
directory slug or choose the most recently modified JSONL file.

- Main conversation: host hook `session_id` and `transcript_path`; omit
  `--agent-id`.
- Child agent: `SubagentStop` supplies `agent_id` and `agent_transcript_path`.
  Its `transcript_path` still points to the parent; using it as the child's source
  would misattribute the history. Not all such events represent user-spawned
  workers, so an automatic hook also needs an explicit agent-type/routing policy.

```sh
python3 -B "$SKILL_DIR/scripts/capture.py" \
  --repo "$ARCHIVE_REPO" --root "$CONTEXT_ROOT" --project demo \
  --format claude-code-jsonl --source "$TRANSCRIPT_FILE" \
  --session-id "$SESSION_ID" --title "Implementation discussion"
```

For a selected child, add `--agent-id "$AGENT_ID"` and use its own source file.
The archive conversation ID is JSON-encoded `[native_session_id, agent_id]`, where
the main agent's ID is null. Capture each child separately under the same project.

The observed input subset has `type: user|assistant`, `sessionId`, `uuid`,
`parentUuid`, `timestamp`, and `message: {role, content}`. Source UUID, parent
relationship, source order and available sidechain/compaction flags are retained.
The helper keeps visible text, supported media references and tool content;
thinking blocks and known runtime-only events are excluded. Unknown event/block
types, duplicate UUIDs and mixed identities require adapter review. An unfinished
final JSONL line causes a retryable failure, not a successful partial capture.

## Plain-text fallback

Claude Code's native `/export` can save a readable conversation. If structured
logs are unavailable, a user-provided visible text export can be imported with
the existing `context_library.py import-transcript` command. Serialize this with
other library writers. State that speakers, branches and child histories are
not structurally verified in text mode. Do not rename raw runtime JSONL to TXT
to bypass private-content filtering. An AI-created conversation recap is not an
export. Missing logs require a real export, not changing persistence settings
without an explicit request.

## Installation and optional automation

The skill folder can be copied to a chosen project or personal Claude Code skill
directory, normally `.claude/skills/archive-project-context/`. Its helper still
needs `--repo` pointing to a trusted checkout with the archive tools. Do not
overwrite an existing installation or edit `CLAUDE.md`/settings merely to import.

For separately authorized automatic capture, the host can call the helper from
`Stop` and selected `SubagentStop` events, with `SessionEnd` as a catch-up point.
Capture only once the corresponding transcript has been flushed. A production
hook must bind explicit project routes, pass argv without shell-interpolating
hook JSON, retain failure receipts for retries, and avoid returning archive
bodies or a blocking decision to the model. This package does not install a hook
or guarantee that a final in-progress response is already in the file. The next
capture can add persisted content under the same identity as a new revision.

## References and verification boundary

Reviewed 2026-10-08: [hooks](https://code.claude.com/docs/en/hooks),
[skills](https://code.claude.com/docs/en/skills), and
[/export](https://support.claude.com/en/articles/14553413-claude-code-cheatsheet).
Hook field semantics are documented; the JSONL message projection is an observed
subset, tested with synthetic fixtures, not a guarantee across Claude versions.
Validate one authorized actual export in the destination environment before
claiming live integration.
