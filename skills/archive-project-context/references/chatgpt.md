# ChatGPT source capture

## Capabilities and selected scope

This branch accepts actual ChatGPT `read_thread` response bundles using the
existing archive adapter. It is not a ChatGPT login client, a public account
export API, or a promise that every ChatGPT conversation has `read_thread`.
The skill may be executed by any agent with the package, trusted repository,
Python and authorized local file access. The host that fetches the source can be
a different agent from the one importing it.

Reuse the user-selected conversation ID/link and exact canonical project. If
the current conversation ID is not exposed, request its link/ID; never infer it
from a title or choose the latest chat. Do not use an AI-reconstructed transcript,
chat preview, compressed context or turn summary as original message text.

## Fetch real pages when the host provides read_thread

1. Call the available host `read_thread` tool for that conversation ID. In the
   Codex host interface, use `threadId`, `turnLimit=10`, `includeOutputs=true`,
   and `maxOutputCharsPerItem=20000`. Other hosts must use their actual schema.
2. Keep the returned schema-version-1 JSON response object in tool orchestration
   or a private temporary file. Do not print full bodies into the active chat or
   ask the model to reproduce them as JSON. If a transport wraps the response in
   text/structured content, decode the actual JSON payload mechanically; do not
   pass the transport envelope as a page or rebuild fields from its prose.
3. Follow each returned `page.nextCursor` using the tool's `cursor` parameter
   until `page.hasMore` is false and there is no next cursor. Keep pages in fetch
   order. Preserve each page, item, identifier, timestamp, attachment reference
   and pagination object without editorial changes. Stop on repeated cursors,
   identity/order changes, errors, unavailable pages or reported truncation.
4. Save a UTF-8 JSON object with exactly this outer schema and the actual page
   objects: `{"schema_version":"ai-context-read-thread/1.0","pages":[...]}`.
   The ellipsis is notation, not a valid page. Use a new file in a private
   directory (0700), mode 0600, outside every repository. Do not relabel account
   export JSON, a share page, ZIP contents, or a summary as this bundle format.

Pagination exhaustion covers only supplied metadata. A missing page cannot
always be detected when the API does not echo request cursors; hidden branches,
deleted messages and unavailable attachments remain unverified. The helper
checks cursor consistency where available but does not claim full-chain proof.

If output is truncated, request an untruncated response through the available
tool controls or obtain an actual export with a separately verified adapter.
Do not concatenate overlapping summaries to fill the gap, delete truncation
flags, fabricate a terminal page, or pass a partial bundle off as a complete
capture. Keep incomplete inputs privately for retry; the strict skill importer
rejects them. A text export can use the library's existing text import route,
with its documented lack of structured speaker/branch verification; this helper
does not implement a universal ChatGPT native-export parser.

## Import the bundle

After setting the explicit paths and replacing the example project/title:

```sh
python3 -B "$SKILL_DIR/scripts/capture.py" \
  --repo "$ARCHIVE_REPO" --root "$CONTEXT_ROOT" --project demo \
  --format chatgpt-read-thread --source "$BUNDLE_FILE" \
  --session-id "$CONVERSATION_ID" --title "Project design discussion"
```

`--session-id` is the ChatGPT conversation ID, which must match every page.
Omit `--agent-id`; a separate ChatGPT conversation gets its own ID. Provider
`chatgpt` plus the native ID reuses the archive's existing identity scheme, so
the same bundle already imported by `import-read-thread` is deduplicated.

The helper snapshots the source once and passes those bytes to the existing
read-thread adapter, without first normalizing or redacting the messages. The
archive applies credential redaction together with its source assembly mapping,
preserving distinct edited messages even if their redacted text becomes equal.
The actual response bundle is retained with marked redactions where needed;
without redactions, the saved source content keeps the original input bytes.

The metadata-only receipt under `sync/agent-capture-runs/` reports the revision,
message count, page count, redactions, and unparsed public-item count. Unknown
public tool items remain in the source bundle and are reported as extraction
gaps; they are not invented as normal message records. An unknown schema or a
private engine item fails. Neither successful import nor source replay proves
the entire platform history exists, that attachments were inspected, or that an
agent has read the archived conversation.

The helper does not edit/delete the supplied bundle. After verified success,
remove only private temporary inputs created by this capture when appropriate;
retain failed inputs with restricted permissions for diagnosis.

## Continued updates and local access

Fetch fresh pages for later changes; re-importing a static bundle cannot obtain
new conversation text. Existing authorized `kind=read_thread` registrations can
continue through `auto_sync.py import-read-thread`. The new skill does not add
registrations, enable a schedule, change account permissions, or update compact
memory. Independent child chats require explicit project association and capture.

ChatGPT's ability to load a skill and its ability to obtain originals or write a
local directory are separate. If local execution is unavailable, produce/retain
the real source bundle only when an actual export tool exists, then use an
authorized local importer. Do not claim cloud execution wrote the user's local
archive. See [OpenAI skill documentation](https://learn.chatgpt.com/docs/build-skills)
and [local access requirements](https://learn.chatgpt.com/docs/enterprise/chatgpt-work-local-security).
Tool parameter examples above describe a host integration, not a general ChatGPT
API endpoint. Source-shape validation is defined by this repository's
`tools/adapters.py` and synthetic regression tests.
