#!/usr/bin/env python3
"""Capture one explicitly selected visible transcript, without calling a model.

Hermes/Claude inputs are normalized into the existing archive exchange format.
ChatGPT read_thread bundles use the existing adapter and retain its source pages.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import shutil
import sys
import tempfile
import uuid


class CaptureError(ValueError):
    pass


PRIVATE = {"thinking", "redacted_thinking", "reasoning", "analysis", "system", "developer"}
BLOCKS = {"text", "input_text", "output_text", "image", "image_url", "document",
          "tool_use", "tool_result", "server_tool_use"}
CLAUDE_EVENTS = {"system", "summary", "progress", "file-history-snapshot",
                 "queue-operation", "last-prompt", "custom-title", "agent-name",
                 "agent-color", "pr-link", "tag", "saved_hook_context"}


def nonempty(value, label):
    if not isinstance(value, str) or not value.strip():
        raise CaptureError(label + " is required.")
    return value


def visible_content(value, counts):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        raise CaptureError("Unsupported message content shape.")
    result = []
    for block in value:
        if not isinstance(block, dict):
            raise CaptureError("Unsupported content block.")
        kind = block.get("type")
        if kind in PRIVATE:
            counts["private_content_blocks"] += 1
            continue
        if kind not in BLOCKS:
            raise CaptureError("Unknown content block type; adapter review required.")
        item = dict(block)
        # A tool's input object is ordinary data, even when a key is 'thinking'.
        # Only typed message blocks are filtered, never arbitrary tool JSON.
        if kind == "tool_result" and isinstance(item.get("content"), list):
            item["content"] = visible_content(item["content"], counts)
        result.append(item)
    return result


def document(provider, identity, title, records, metadata, counts):
    if not records:
        raise CaptureError("No visible messages were found.")
    return {"schema_version": "ai-context-transcript/2.0", "provider": provider,
            "conversation_id": identity, "title": title, "records": records,
            "capture": {"adapter": "archive-project-context/1", **metadata},
            "completeness": {
                "complete": False,
                "scope": "Selected file snapshot; visible message projection only.",
                "excluded": dict(counts),
                "limitations": ["Native fields outside the documented projection are not retained.",
                                "Deleted or missing history and external attachments are not recovered.",
                                "Replay verification starts at the normalized projection, not the native source."]}}


def parse_hermes(data, session_id, title, agent_id, parse_json):
    # Accept a single /save object or one-session JSONL export, never a bulk file.
    try:
        value = parse_json(data.decode("utf-8"), "Hermes export")
    except (ValueError, UnicodeError):
        raise CaptureError("Expected one complete Hermes session JSON object; bulk exports are not accepted.") from None
    if not isinstance(value, dict) or value.get("id") != session_id or not isinstance(value.get("messages"), list):
        raise CaptureError("Hermes export shape or session identity does not match.")
    counts, records, ids = Counter(), [], set()
    for index, row in enumerate(value["messages"]):
        if not isinstance(row, dict):
            raise CaptureError("Invalid Hermes message.")
        if row.get("session_id", session_id) != session_id:
            raise CaptureError("Mixed Hermes session identities.")
        role = row.get("role")
        if role in PRIVATE or row.get("channel") in PRIVATE:
            counts["private_messages"] += 1
            continue
        if role not in {"user", "assistant", "tool"}:
            raise CaptureError("Unsupported Hermes message role.")
        raw_id = row.get("id")
        if isinstance(raw_id, bool) or not isinstance(raw_id, (str, int)) or str(raw_id) == "":
            raise CaptureError("Hermes export needs persistent message IDs.")
        identity = str(raw_id)
        if identity in ids:
            raise CaptureError("Duplicate Hermes message identity.")
        ids.add(identity)
        content = visible_content(row.get("content"), counts)
        if row.get("tool_calls"):
            content = {"message": content, "tool_calls": row["tool_calls"]}
        if content in ("", []):
            counts["empty_or_private_only_messages"] += 1
            continue
        record = {"id": identity, "role": role, "content": content,
                  "native_message_index": index, "timestamp": row.get("timestamp")}
        for key in ("tool_call_id", "tool_name", "active", "compacted", "message_id", "platform_message_id"):
            if key in row:
                record[key] = row[key]
        records.append(record)
    # Profiles are separate stores and can reuse native IDs. A stable, explicit
    # profile identity is therefore part of the archive key, not just its title.
    identity = json.dumps([agent_id, session_id], ensure_ascii=False, separators=(",", ":"))
    metadata = {"format": "hermes-session-json", "native_session_id": session_id,
                "agent_id": agent_id, "parent_native_session_id": value.get("parent_session_id"),
                "source_platform": value.get("source")}
    return document("hermes", identity, title, records, metadata, counts)


def parse_claude(data, session_id, title, agent_id, parse_json):
    counts, records, ids = Counter(), [], set()
    if not data.endswith(b"\n"):
        raise CaptureError("Claude JSONL has an incomplete final line; retry after the writer flushes.")
    for number, line in enumerate(data.splitlines(), 1):
        if not line.strip():
            continue
        row = parse_json(line.decode("utf-8"), "Claude transcript row")
        if not isinstance(row, dict):
            raise CaptureError("Invalid Claude transcript row.")
        kind = row.get("type")
        if kind in CLAUDE_EVENTS:
            counts["non_message_events"] += 1
            continue
        if kind not in {"user", "assistant"}:
            raise CaptureError("Unknown Claude event type; adapter review required.")
        if row.get("sessionId") != session_id:
            raise CaptureError("Claude session identity does not match.")
        observed_agent = row.get("agentId")
        if observed_agent is not None and observed_agent != agent_id:
            raise CaptureError("Claude agent identity does not match.")
        if row.get("isSidechain") is True and not agent_id:
            raise CaptureError("A Claude subagent requires its explicit agent ID.")
        if agent_id and observed_agent is None and row.get("isSidechain") is not True:
            raise CaptureError("Main-session rows cannot be attributed to a child agent.")
        if row.get("isMeta") is True:
            counts["injected_meta_messages"] += 1
            continue
        message = row.get("message")
        if not isinstance(message, dict) or message.get("role") != kind:
            raise CaptureError("Claude message role does not match its envelope.")
        identity = nonempty(row.get("uuid"), "Claude message UUID")
        if identity in ids:
            raise CaptureError("Duplicate Claude UUID; edits need an explicit adapter, not silent deduplication.")
        ids.add(identity)
        content = visible_content(message.get("content"), counts)
        if content in ("", []):
            counts["empty_or_private_only_messages"] += 1
            continue
        record = {"id": identity, "role": kind, "content": content,
                  "native_line": number, "timestamp": row.get("timestamp"),
                  "parent_id": row.get("parentUuid")}
        for key in ("isSidechain", "isMeta", "isCompactSummary", "agentId"):
            if key in row:
                record[key] = row[key]
        records.append(record)
    identity = json.dumps([session_id, agent_id], ensure_ascii=False, separators=(",", ":"))
    return document("claude-code", identity, title, records,
                    {"format": "claude-code-jsonl", "native_session_id": session_id,
                     "agent_id": agent_id, "parent_native_session_id": session_id if agent_id else None}, counts)


def chatgpt_payload(data, source, session_id, adapters):
    """Use the original page adapter, with strict capture-completion checks.

    No receipt can prove authenticity or missing pages when the API does not
    echo request cursors. These checks reject observed inconsistencies only.
    """
    payload = adapters._parse_read_thread_snapshot(data, source)
    if payload["conversation_id"] != session_id:
        raise CaptureError("ChatGPT conversation identity does not match --session-id.")
    bundle = adapters._json(data.decode("utf-8"), "read_thread bundle")
    pages = bundle["pages"]
    cursors = set()
    previous = None
    flags = ("truncated", "isTruncated", "textTruncated", "outputTruncated", "hasTruncatedOutput")

    def check_truncation(value):
        if any(value.get(key) is True for key in flags):
            raise CaptureError("ChatGPT source reports truncation; obtain untruncated responses before capture.")

    check_truncation(bundle)
    for index, page in enumerate(pages):
        paging = page["page"]
        has_more, next_cursor = paging.get("hasMore"), paging.get("nextCursor")
        if not isinstance(has_more, bool):
            raise CaptureError("ChatGPT page requires an explicit boolean hasMore.")
        if "cursor" in paging and paging["cursor"] != previous:
            raise CaptureError("ChatGPT response cursor disagrees with the preceding page.")
        if has_more:
            if not isinstance(next_cursor, str) or not next_cursor.strip() or next_cursor in cursors:
                raise CaptureError("ChatGPT nextCursor is missing, invalid or repeated.")
            cursors.add(next_cursor)
            if index == len(pages) - 1:
                raise CaptureError("ChatGPT pagination is unfinished; fetch the remaining pages before capture.")
        elif index != len(pages) - 1 or next_cursor is not None:
            raise CaptureError("ChatGPT terminal page is inconsistent with the supplied pagination.")
        previous = next_cursor
        check_truncation(page)
        check_truncation(paging)
        for turn in page["turns"]:
            check_truncation(turn)
            for item in turn["items"]:
                check_truncation(item)
                # Do not reinterpret arbitrary quoted user JSON as transport
                # flags. Assistant/output text markers need source review.
                texts = [item.get(key) for key in ("text", "output", "aggregatedOutput", "aggregated_output")]
                if item.get("type") != "userMessage" and any(
                        isinstance(text, str) and adapters._TRUNCATED.search(text) for text in texts):
                    raise CaptureError("ChatGPT output contains a truncation marker; review the source before capture.")
    return payload


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True, type=Path, help="Trusted ai-memory-system checkout.")
    p.add_argument("--root", required=True, type=Path, help="Existing private AI-Context root.")
    p.add_argument("--project", required=True, help="Exact existing canonical project.")
    p.add_argument("--source", required=True, type=Path)
    p.add_argument("--format", required=True,
                   choices=("hermes-session-json", "claude-code-jsonl", "chatgpt-read-thread"))
    p.add_argument("--session-id", required=True, help="Native session ID, or the ChatGPT conversation ID.")
    p.add_argument("--agent-id", help="Required Hermes profile ID; Claude child ID; omit for ChatGPT or Claude main.")
    p.add_argument("--title", required=True)
    return p


def capture(args):
    for path in (args.repo, args.root, args.source):
        if not path.is_absolute() or ".." in path.parts:
            raise CaptureError("Repository, library and source paths must be explicit absolute paths.")
    nonempty(args.session_id, "Session ID")
    if args.source.suffix.lower() not in {".json", ".jsonl"}:
        raise CaptureError("Select an explicit JSON/JSONL export or transcript, never a provider database.")
    if args.format == "hermes-session-json":
        nonempty(args.agent_id, "Hermes profile ID")
    elif args.format == "chatgpt-read-thread" and args.agent_id is not None:
        raise CaptureError("ChatGPT uses its conversation ID; --agent-id is not applicable.")
    elif args.agent_id is not None:
        nonempty(args.agent_id, "Agent ID")
    # The user chooses this trusted checkout. Do not look for global provider
    # configurations, discover sessions, or execute commands found in a source.
    tools_dir = args.repo / "tools"
    if not all((tools_dir / name).is_file() for name in
               ("context_library.py", "auto_sync.py", "adapters.py", "source_verification.py")):
        raise CaptureError("The selected checkout lacks required archive tools.")
    sys.path.insert(0, str(tools_dir))
    import adapters
    import auto_sync
    import context_library as lib
    import conversation_catalog as catalog
    from source_verification import verify_source

    lib.checked_path(args.repo)
    lib.resolve_project(args.root, args.project)
    data, native_source = adapters._snapshot(args.source)
    redactor = lib.Redactor()
    is_chatgpt = args.format == "chatgpt-read-thread"
    if is_chatgpt:
        # Preserve the captured bytes and the existing assembly plan. Do not
        # pre-redact or normalize pages: doing so could collapse edited messages.
        payload = chatgpt_payload(data, native_source, args.session_id, adapters)
        exclusions = {}
        scope = "preserved-read-thread-pages"
    else:
        converter = parse_hermes if args.format == "hermes-session-json" else parse_claude
        normalized = converter(data, args.session_id, args.title, args.agent_id, adapters._json)
        normalized = redactor.value(normalized)
        exclusions = dict(normalized["completeness"]["excluded"])
        scope = "normalized-visible-projection"
    # Cooperate with the existing sync-files lock. Contention returns busy; it
    # never bypasses the lock or loops inside an agent turn.
    with auto_sync.locked(args.root) as folder:
        staging = None
        if not is_chatgpt:
            staging = Path(tempfile.mkdtemp(prefix=".agent-capture-", dir=folder))
            source = staging / "visible.json"
            lib.write_private(source, lib.canonical_bytes(normalized) + b"\n")
            payload = adapters.parse_transcript(source, normalized["provider"], normalized["conversation_id"], args.title)
        result = lib.publish(args.root, args.project, payload, args.title)
        verification = verify_source(Path(result["path"]), args.project)
        if verification.get("status") != "verified-source-replay":
            raise CaptureError("Archive verification failed; source inputs were retained for diagnosis.")
        receipt = {"schema_version": "ai-context-agent-capture/1.0", "status": result["status"],
                   "project": args.project, "provider": result["provider"],
                   "conversation_id": result["conversation_id"], "conversation_key": result["conversation_key"],
                   "revision": result["revision"], "record_count": result["record_count"],
                   "source_snapshot_sha256": native_source["snapshot_sha256"],
                   "source_snapshot_bytes": native_source["snapshot_bytes"],
                   "source_format": args.format, "normalization_exclusions": exclusions,
                   "redaction_count": sum(redactor.counts.values()) + result["redaction_count"],
                   "verification": verification["status"], "verification_scope": scope,
                   "captured_at": auto_sync.now(), "reading_acknowledged": False,
                   "automatic_sync_enabled_by_capture": False}
        if is_chatgpt:
            assessment = payload["completeness"]
            receipt["source_assessment"] = {
                "page_count": assessment["page_count"], "pagination_exhausted": True,
                "pagination_metadata_checked": True, "pagination_chain_verified": False,
                "unparsed_public_item_count": len(assessment["source_evidence_gaps"]),
                "whole_history_complete": False, "external_attachments_copied": False}
        receipts = catalog.ensure_path(args.root, "sync", "agent-capture-runs")
        receipt_path = receipts / (str(uuid.uuid4()) + ".json")
        lib.write_private(receipt_path, lib.canonical_bytes(receipt) + b"\n")
        if staging is not None:
            shutil.rmtree(staging)
        return {**receipt, "receipt_path": str(receipt_path)}


def main(argv=None):
    sys.dont_write_bytecode = True
    args = parser().parse_args(argv)
    try:
        print(json.dumps(capture(args), ensure_ascii=False))
        return 0
    except BlockingIOError:
        print(json.dumps({"status": "busy", "error": "Archive writer is active; retry later with the same source."}), file=sys.stderr)
        return 3
    except Exception as exc:
        # Provider JSON and filesystem exceptions can contain private content.
        # Only our own bounded, constant diagnostics are emitted.
        message = str(exc) if isinstance(exc, CaptureError) else "Capture failed; source contents were not printed."
        print(json.dumps({"status": "error", "error_type": type(exc).__name__, "error": message}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
