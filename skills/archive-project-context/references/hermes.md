# Hermes source capture

## Select and export

Use the actual running profile/instance and a full session ID. Reuse host-provided
session metadata or a user-selected export; do not enumerate unrelated histories.
Check that the installed version supports the command before relying on it:

```sh
hermes sessions export --help
hermes --profile director sessions export "$EXPORT_FILE" --session-id "$SESSION_ID"
```

The second command is an official Hermes operation, executed in the authorized
Hermes environment; this skill never implements SQLite access. Prefer having the
host or user export the session if the local archive policy forbids even
provider-mediated store access. Do not run a frozen desktop binary's maintenance
commands merely to discover its behavior. Desktop forks and older versions need
their own verified export action.

Create a private staging directory (0700) outside every repository before export,
use restrictive umask 077, choose a new file, and avoid stdout for raw content.
Never use an unfiltered bulk export, `--upload`, or `--delete-after-verified`.
The exporter may include system prompts and internal reasoning; do not load it
wholesale into a model context or copy it into the archive as plain text.

Supported helper input is one JSON object, with optional trailing newline:
`{"id":"native-session-id","messages":[...]}`. It is the single-session JSONL
shape produced by the documented exporter. Every retained message needs its
persistent `id`, a supported `role` and visible `content` or `tool_calls`.
Other `/save` shapes must be validated first; filenames alone prove nothing.

```sh
python3 -B "$SKILL_DIR/scripts/capture.py" \
  --repo "$ARCHIVE_REPO" --root "$CONTEXT_ROOT" --project demo \
  --format hermes-session-json --source "$EXPORT_FILE" \
  --session-id "$SESSION_ID" --agent-id director --title "Design discussion"
```

Keep `--agent-id` stable across exports. Different machines/instances containing
independent `director` profiles need distinct IDs chosen during registration.
The conversation ID is a JSON-encoded `[profile_id, native_session_id]`; the full
archive key is returned in the receipt. Native parent-session IDs, visible tool
calls/results and available active/compacted flags are retained. Source order is
not a claim that archived or rewound messages form the active conversation.
System/developer roles, reasoning fields, model settings and unrelated session
metadata are not copied. Unknown roles/content blocks fail rather than disappear.

Compression can create a parent/child session lineage. Export only authorized
segments individually and retain their relationship. Do not replace missing
segments with a compression summary or silently fetch every ancestor.

## Multiple agents and future automatic capture

Workers without filesystem/export tools cannot run this skill. The coordinator
must capture their host-produced transcript or explicit input/output artifacts.
Keep the existing worker permissions. A worker's final report alone is partial
evidence, not its full conversation or tool history.

An approved coordinator can export each known session after its host has persisted
it, then call the helper once per profile/session/project. Export again before
capturing later additions: watching a static old export cannot discover new text.
No Hermes scheduler, profile configuration, tool permission or live source is
changed by installing or invoking this package.

## References and verification boundary

Reviewed 2026-10-08: [Hermes sessions](https://hermes-agent.nousresearch.com/docs/user-guide/sessions/),
[skills](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills/),
and the upstream `SessionPortabilityMixin.export_session` source shape in
[hermes_state_portability.py](https://github.com/NousResearch/hermes-agent/blob/main/hermes_state_portability.py).
The projection is locally tested with synthetic exports; this is not a live
acceptance test of a particular installed Hermes fork.
