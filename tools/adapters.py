"""Read-only, snapshot-bounded conversation importers.

These adapters export public conversation records, not model context. They never
claim whole-account or whole-platform coverage. ``source_refs`` use absolute
``path`` plus one-based ``line`` and byte offsets; paths are provenance, not
conversation identity. No import timestamp is generated here.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections import Counter
from pathlib import Path
from typing import Any


class AdapterError(ValueError):
    """An unsupported, malformed, unsafe, or changing input source."""


ADAPTER_VERSION = 5


_PRIVATE = {"analysis", "reasoning", "internal", "system", "developer", "justify", "confidence"}
_MEDIA_TYPES = {
    "image", "input_image", "output_image", "local_image", "localimage",
    "audio", "input_audio", "output_audio", "video", "file", "attachment",
}
_BINARY_MEDIA_TYPES = _MEDIA_TYPES - {"file", "attachment"}
_INJECTED_START = re.compile(
    r"^\s*<(?:environment_context|skill|external_codex_apps_open_page|"
    r"user_instructions|developer_instructions|system_instructions|"
    r"permissions\b|collaboration_mode|app-context)(?:\s|>)", re.I
)
_TRUNCATED = re.compile(
    r"warning:\s*truncated output|\b\d+\s+tokens truncated\b|"
    r"\[\.{3}\s*truncated|\boutput (?:was )?truncated\b", re.I
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _typename(value: Any) -> str:
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", value) else "<unknown>"


def _reject_symlinks(path: Path) -> None:
    for component in (path, *path.parents):
        try:
            if stat.S_ISLNK(component.lstat().st_mode):
                raise AdapterError("Source path must not contain symbolic links.")
        except OSError as exc:
            raise AdapterError("Source path is unavailable.") from None


def _snapshot(path: str | os.PathLike[str]) -> tuple[bytes, dict[str, Any]]:
    """Read exactly the initial stat size, then independently recheck its hash."""
    source = Path(os.path.abspath(os.path.expanduser(os.fspath(path))))
    _reject_symlinks(source)
    try:
        before = source.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise AdapterError("Source must be an ordinary file.")
        fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise AdapterError("Source identity changed while opening.")
            size = opened.st_size

            def read_prefix() -> bytes:
                os.lseek(fd, 0, os.SEEK_SET)
                parts: list[bytes] = []
                remaining = size
                while remaining:
                    part = os.read(fd, min(1024 * 1024, remaining))
                    if not part:
                        raise AdapterError("Source was truncated during snapshot capture.")
                    parts.append(part)
                    remaining -= len(part)
                return b"".join(parts)

            data = read_prefix()
            if _sha(read_prefix()) != _sha(data):
                raise AdapterError("Source prefix changed during snapshot capture.")
            after = source.lstat()
            if (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino) or not stat.S_ISREG(after.st_mode):
                raise AdapterError("Source identity changed during snapshot capture.")
            if after.st_size < size:
                raise AdapterError("Source was truncated during snapshot capture.")
        finally:
            os.close(fd)
        _reject_symlinks(source)
    except AdapterError:
        raise
    except OSError:
        raise AdapterError("Unable to capture source snapshot.") from None
    return data, {
        "path": str(source), "snapshot_sha256": _sha(data),
        "snapshot_bytes": len(data), "mtime_ns": opened.st_mtime_ns,
        "parsed_bytes": len(data), "trailing_bytes_ignored": 0,
        "first_timestamp": None, "last_timestamp": None,
        "prefix_stability_checked": True, "adapter_version": ADAPTER_VERSION,
    }


def _json(text: str, label: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise AdapterError(f"Duplicate JSON key in {label}.")
            result[key] = value
        return result

    def invalid_constant(_: str) -> None:
        raise AdapterError(f"Non-finite JSON value in {label}.")

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except AdapterError:
        raise
    except (ValueError, RecursionError):
        raise AdapterError(f"Invalid JSON in {label}.") from None


def _utf8(data: bytes, label: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeError:
        raise AdapterError(f"Invalid UTF-8 in {label}.") from None


def _private(obj: dict[str, Any]) -> bool:
    return any(isinstance(obj.get(key), str) and obj[key].strip().lower() in _PRIVATE for key in ("role", "channel", "phase"))


def _validate_extensions(record: dict[str, Any], label: str) -> None:
    """Validate declared relationship/time fields without inventing a graph."""
    singular = {"parent", "parent_id", "branch_id", "edit_of", "edited_from", "regenerated_from", "regeneration_of", "replaces"}
    plural = {"children", "children_ids", "parent_ids", "branch_ids", "edited_message_ids", "regenerated_message_ids"}
    for key in singular:
        if key in record and record[key] is not None and not isinstance(record[key], str):
            raise AdapterError(f"Invalid relationship field in {label}.")
    for key in plural:
        if key in record and record[key] is not None and (not isinstance(record[key], list) or any(not isinstance(item, str) for item in record[key])):
            raise AdapterError(f"Invalid relationship list in {label}.")
    for key in ("relationships", "relations"):
        if key in record and record[key] is not None:
            if not isinstance(record[key], dict):
                raise AdapterError(f"Invalid relationship object in {label}.")
            _validate_extensions(record[key], label)
    for key in ("timestamp", "created_at", "updated_at", "message_created_at", "source_recorded_at", "exported_at"):
        if key in record and record[key] is not None and (isinstance(record[key], bool) or not isinstance(record[key], (str, int, float))):
            raise AdapterError(f"Invalid time field in {label}.")


def _message_created_at(obj: dict[str, Any]) -> Any:
    for key in ("message_created_at", "created_at", "create_time"):
        if obj.get(key) is not None:
            return obj[key]
    metadata = obj.get("internal_chat_message_metadata_passthrough")
    return metadata.get("create_time") if isinstance(metadata, dict) else None


def _public(value: Any, counters: Counter[str]) -> Any:
    """Preserve visible data, including recoverable embedded media, verbatim.

    Privacy filtering belongs at the source message/engine-record boundary.
    Nested objects may be legitimate reports or API examples, so names such as
    ``analysis``, ``summary_text`` or ``role: system`` are ordinary visible data.
    The renderer may replace large media with previews or metadata; the archival
    record must retain the original payload. External references are never read.
    """
    if isinstance(value, dict):
        kind = str(value.get("type", "")).lower()
        mime = value.get("mimeType", value.get("mime_type"))
        binary_media = kind in _BINARY_MEDIA_TYPES or isinstance(mime, str) and mime.split("/", 1)[0] in {"image", "audio", "video"}
        media = kind in _MEDIA_TYPES or binary_media
        if media:
            counters["attachment_references"] += 1
        result = {}
        for key, item in value.items():
            if binary_media and key in {"data", "blob", "bytes", "base64"} and isinstance(item, str):
                result[key] = item
                if item:
                    counters["inline_media_preserved"] += 1
                    counters["inline_payloads_preserved"] += 1
                else:
                    counters["unavailable_inline_payloads"] += 1
                continue
            result[key] = _public(item, counters)
        return result
    if isinstance(value, list):
        return [_public(item, counters) for item in value]
    if isinstance(value, str) and re.match(r"^data:(?:image|audio|video)/[^,]*,", value, re.I):
        if value.partition(",")[2]:
            counters["inline_media_preserved"] += 1
            counters["inline_payloads_preserved"] += 1
        else:
            counters["unavailable_inline_payloads"] += 1
        return value
    return value


def _channel(obj: dict[str, Any], default: str = "message") -> str | None:
    if _private(obj):
        return None
    phase = obj.get("phase", obj.get("channel", default))
    if not isinstance(phase, str) or not phase:
        return default
    return "final" if phase == "final_answer" else phase


def _texts(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for child in value:
            yield from _texts(child)
    elif isinstance(value, dict):
        for child in value.values():
            yield from _texts(child)


def _message_parts(content: Any) -> list[Any]:
    if isinstance(content, dict):
        value = content.get("content", content.get("message", []))
    else:
        value = content
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [{"type": "text", "text": value}]
    return [value] if value is not None else []


def _message_text(content: Any) -> str:
    return "\n".join(part["text"] for part in _message_parts(content)
                     if isinstance(part, dict) and isinstance(part.get("text"), str)
                     and str(part.get("type", "text")).lower() in {"text", "input_text", "output_text"})


def _part_signature(part: Any) -> str:
    if isinstance(part, dict) and isinstance(part.get("text"), str) and str(part.get("type", "text")).lower() in {"text", "input_text", "output_text"}:
        part = {key: value for key, value in part.items() if key != "type" and not (key == "text_elements" and value == [])}
        part = {"type": "text", **part}
    return _sha(json.dumps(part, sort_keys=True, ensure_ascii=False).encode())


def _message_context(row: dict[str, Any], obj: dict[str, Any]) -> tuple[set[str], str | None, bool]:
    payload = row.get("payload", {})
    metadata = row.get("metadata", {})
    metadata = metadata if isinstance(metadata, dict) else {}
    retained = metadata.get("retained_source", {})
    retained = retained if isinstance(retained, dict) else {}
    retained_id = retained.get("id", {})
    retained_id = retained_id if isinstance(retained_id, dict) else {}
    passthrough = obj.get("internal_chat_message_metadata_passthrough", {})
    passthrough = passthrough if isinstance(passthrough, dict) else {}
    identities = {value for value in (obj.get("id"), obj.get("call_id"), retained_id.get("message_id")) if isinstance(value, str) and value}
    turn = next((value for value in (payload.get("turn_id"), retained_id.get("turn_id"), passthrough.get("turn_id")) if isinstance(value, str) and value), None)
    authored = metadata.get("client_authored") is True or retained_id.get("role") == obj.get("role") and bool(retained_id.get("message_id"))
    return identities, turn, authored


def _completeness(source: dict[str, Any], mode: str, counters: Counter[str], unknown: Counter[str]) -> dict[str, Any]:
    return {
        "adapter_version": ADAPTER_VERSION,
        "status": "observed-schema" if mode == "event_msg.item_completed" else "pending-validation",
        "complete": False, "scope": "single-conversation-file-snapshot",
        "message_source": mode, "counts": dict(sorted(counters.items())),
        "message_reconciliation": "per-record across completed, legacy, and response chains",
        "unknown_types": dict(sorted(unknown.items())),
        "truncation_marker_count": counters["tool_output_truncation_markers"],
        "compaction_event_count": counters["compaction_events"],
        "attachments": {"external_files_copied": False, "links_may_expire": True,
                        "recognized_reference_count": counters["attachment_references"],
                        "inline_payloads_preserved": counters["inline_payloads_preserved"],
                        "embedded_payloads_preserved": counters["inline_payloads_preserved"] > 0,
                        "unavailable_inline_payloads": counters["unavailable_inline_payloads"],
                        "external_reference_availability": "not-checked",
                        "visible_image_events_reference_only": counters["visible_image_events_reference_only"],
                        "imageview_payload_status": "reference preserved; payload availability unverified" if counters["visible_image_events_reference_only"] else "no ImageView events observed"},
        "snapshot_cutoff": {"source_bytes": source["snapshot_bytes"], "parsed_bytes": source["parsed_bytes"],
                            "trailing_bytes_ignored": source["trailing_bytes_ignored"],
                            "last_timestamp": source["last_timestamp"]},
        "limitations": ["Only this source snapshot was inspected; whole-platform and whole-history completeness are unverified.",
                        "Appends after the captured file size are outside this snapshot.",
                        "Unmatched public presentations are preserved; potential duplicates are not silently discarded.",
                        "Unknown content or attachment schemas may need a separately validated adapter."],
    }


def parse_codex(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Parse public messages and tool records from one local Codex JSONL file."""
    data, source = _snapshot(path)
    return _parse_codex_snapshot(data, source)


def _parse_codex_snapshot(data: bytes, source: dict[str, Any], original_refs: dict[int, dict[str, Any]] | None = None,
                          replay_steps: dict[tuple[int, ...], dict[str, Any]] | None = None) -> dict[str, Any]:
    cutoff = data.rfind(b"\n") + 1
    source.update(parsed_bytes=cutoff, trailing_bytes_ignored=len(data) - cutoff)
    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    offset = 0
    for line_no, part in enumerate(data[:cutoff].split(b"\n")[:-1], 1):
        line = part + b"\n"
        row = _json(_utf8(line, f"complete line {line_no}"), f"complete line {line_no}")
        if not isinstance(row, dict):
            raise AdapterError(f"JSON object required at complete line {line_no}.")
        ref = {"path": source["path"], "line": line_no, "byte_start": offset, "byte_end": offset + len(line)}
        if isinstance(row.get("ordinal"), int):
            ref["ordinal"] = row["ordinal"]
        if original_refs and line_no in original_refs:
            ref = dict(original_refs[line_no])
        rows.append((row, ref))
        offset += len(line)
    timestamps = [row["timestamp"] for row, _ in rows if isinstance(row.get("timestamp"), str)]
    if timestamps:
        source.update(first_timestamp=timestamps[0], last_timestamp=timestamps[-1])
    identities = set()
    has_modern = False
    has_legacy = False
    for row, _ in rows:
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        if row.get("type") == "session_meta":
            identity = payload.get("id") or payload.get("session_id")
            if isinstance(identity, str) and identity:
                identities.add(identity)
        if row.get("type") == "event_msg":
            has_modern |= payload.get("type") == "item_completed"
            has_legacy |= isinstance(payload.get("type"), str) and payload["type"] in {"user_message", "agent_message"}
    if len(identities) > 1:
        raise AdapterError("Source contains conflicting conversation identities.")
    conversation_id = next(iter(identities), None)
    completed_threads = {row["payload"]["thread_id"] for row, _ in rows if isinstance(row.get("payload"), dict) and row["payload"].get("type") == "item_completed" and isinstance(row["payload"].get("thread_id"), str) and row["payload"]["thread_id"]}
    if conversation_id is None:
        if len(completed_threads) == 1:
            conversation_id = next(iter(completed_threads))
        else:
            raise AdapterError("A unique conversation identity is required.")
    if any(identity != conversation_id for identity in completed_threads):
        raise AdapterError("Completed events conflict with the source conversation identity.")
    mode = "event_msg.item_completed" if has_modern else "legacy-event-msg" if has_legacy else "response-item-fallback"
    counters: Counter[str] = Counter()
    unknown: Counter[str] = Counter()
    records: list[dict[str, Any]] = []
    seen: dict[str, dict[str, Any]] = {}
    messages: list[dict[str, Any]] = []
    event_tools: list[dict[str, Any]] = []
    response_tools: list[dict[str, Any]] = []
    evidence_gaps: list[dict[str, Any]] = []
    visible_source: dict[int, dict[str, Any]] = {}
    assembly: dict[tuple[int, ...], dict[str, Any]] = {}

    def locator(ref):
        return (ref["line"], ref["byte_start"])

    def planned(ref):
        if replay_steps is None:
            return None
        step = replay_steps.get(locator(ref))
        if step is None:
            raise AdapterError("A retained source row is missing its replay step.")
        return step

    def remember(ref, record, action, **extra):
        key = locator(ref)
        if key in assembly:
            raise AdapterError("A source row has multiple replay operations.")
        step = {"locator": list(key), "record_id": record["id"], "action": action, **extra}
        if replay_steps is not None and step != planned(ref):
            raise AdapterError("A replay operation disagrees with its source mapping.")
        assembly[key] = step

    def add(row: dict[str, Any], ref: dict[str, Any], obj: dict[str, Any], role: str, content: Any, channel: str | None, fallback_id: str) -> dict[str, Any] | None:
        if channel is None or _private(obj):
            counters["private_records_omitted"] += 1
            return
        cleaned = _public(content, counters)
        identity = obj.get("id") or fallback_id
        if not isinstance(identity, str):
            identity = fallback_id
        record = {"id": identity, "role": role, "channel": channel,
                  "timestamp": row.get("timestamp") if isinstance(row.get("timestamp"), str) else None,
                  "source_recorded_at": row.get("timestamp"), "message_created_at": _message_created_at(obj),
                  "content": cleaned, "source_refs": [ref]}
        step = planned(ref)
        if step is not None:
            if step["record_id"] != identity and (identity not in seen or not re.fullmatch(
                    re.escape(identity) + r":revision:[0-9a-f]{16}", step["record_id"])):
                raise AdapterError("Replay target is not a version of this source identity.")
            identity = record["id"] = step["record_id"]
            if step["action"] == "duplicate":
                existing = seen.get(identity)
                if existing is None or any(existing[k] != record[k] for k in ("role", "channel", "content")):
                    raise AdapterError("A duplicate replay row disagrees with its target.")
                existing["source_refs"].append(ref)
                counters["duplicate_records_coalesced"] += 1
                remember(ref, existing, "duplicate")
                return existing
            if step["action"] != "new" or identity in seen:
                raise AdapterError("A new replay row has an invalid target or action.")
        existing = seen.get(identity)
        if existing is not None:
            if all(existing[k] == record[k] for k in ("role", "channel", "content")):
                existing["source_refs"].append(ref)
                counters["duplicate_records_coalesced"] += 1
                remember(ref, existing, "duplicate")
                return existing
            signature = json.dumps({k: record[k] for k in ("role", "channel", "content")}, sort_keys=True, ensure_ascii=False).encode()
            record["id"] = identity + ":revision:" + _sha(signature)[:16]
            revision = seen.get(record["id"])
            if revision is not None:
                if all(revision[k] == record[k] for k in ("role", "channel", "content")):
                    revision["source_refs"].append(ref)
                    counters["duplicate_records_coalesced"] += 1
                    remember(ref, revision, "duplicate")
                    return revision
                raise AdapterError("A record revision identity collision was detected.")
        seen[record["id"]] = record
        records.append(record)
        counters["exported_" + role] += 1
        remember(ref, record, "new")
        return record

    def candidate(row: dict[str, Any], ref: dict[str, Any], obj: dict[str, Any], role: str, content: Any, chain: str) -> dict[str, Any] | None:
        if _private(obj):
            counters["private_records_omitted"] += 1
            return None
        identities, turn, authored = _message_context(row, obj)
        source_ref = {**ref, "source_kind": chain}
        if isinstance(row.get("timestamp"), str):
            source_ref["timestamp"] = row["timestamp"]
        return {"row": row, "ref": source_ref, "obj": obj, "role": role, "content": content,
                "chain": chain, "ids": identities, "turn": turn, "authored": authored,
                "channel": "tool" if role == "tool" else _channel(obj), "text": _message_text(content)}

    def gap(reason: str, ref: dict[str, Any]) -> None:
        evidence_gaps.append({"reason": reason, "source_refs": [ref]})

    def capture_visible(entry: dict[str, Any]) -> None:
        """Keep only the already-approved public engine boundary, not raw logs."""
        row, obj, chain = entry["row"], entry["obj"], entry["chain"]
        public_obj = {key: obj[key] for key in ("type", "id", "role", "phase", "channel", "created_at", "create_time", "message_created_at") if key in obj}
        public_obj.update(entry["content"])
        passthrough = obj.get("internal_chat_message_metadata_passthrough")
        if isinstance(passthrough, dict):
            public_obj["internal_chat_message_metadata_passthrough"] = {key: passthrough[key] for key in ("create_time", "turn_id") if key in passthrough}
        public_row = {key: row[key] for key in ("timestamp", "ordinal", "type") if key in row}
        if chain in {"completed", "completed-tool"}:
            envelope = row["payload"]
            public_row["payload"] = {key: envelope[key] for key in ("type", "thread_id", "turn_id", "started_at_ms", "completed_at_ms") if key in envelope}
            public_row["payload"]["item"] = public_obj
        else:
            public_row["payload"] = public_obj
            if chain == "legacy" and isinstance(row["payload"].get("turn_id"), str):
                public_row["payload"]["turn_id"] = row["payload"]["turn_id"]
        metadata = row.get("metadata")
        if isinstance(metadata, dict):
            public_row["metadata"] = {key: metadata[key] for key in ("client_authored", "retained_source", "user_input_order") if key in metadata}
        visible_source[entry["ref"]["line"]] = {"source_ref": entry["ref"], "row": public_row}

    known_top = {"session_meta", "event_msg", "response_item", "world_state", "turn_context", "token_usage_record", "inter_agent_communication_metadata", "compacted"}
    known_event = {"task_started", "task_complete", "token_count", "thread_settings_applied", "item_completed", "user_message", "agent_message", "agent_reasoning", "agent_reasoning_raw_content", "context_compacted"}
    known_items = {"UserMessage", "AgentMessage", "CommandExecution", "Reasoning", "SubAgentActivity", "FileChange", "McpToolCall", "Extension", "ContextCompaction"}
    tool_types = {"function_call", "custom_tool_call", "function_call_output", "custom_tool_call_output"}
    event_tool_fields = {
        "CommandExecution": ("type", "command", "cwd", "process_id", "status", "exit_code", "stdout", "stderr", "aggregated_output", "formatted_output", "duration", "parsed_cmd", "source"),
        "FileChange": ("type", "status", "changes", "stdout", "stderr"),
        "McpToolCall": ("type", "server", "tool", "arguments", "result", "status", "duration", "pluginId"),
        "Extension": ("type", "kind", "query", "results", "action", "status"),
        "ImageView": ("type", "path", "url", "image", "images", "content", "source", "description", "mimeType", "data"),
    }
    for row, ref in rows:
        top = _typename(row.get("type"))
        payload = row.get("payload")
        if not isinstance(payload, dict):
            unknown["non-object-payload"] += 1
            continue
        kind = _typename(payload.get("type"))
        counters["source_records"] += 1
        record_compacted = "compac" in str(top).lower() or "compac" in str(kind).lower()
        if record_compacted:
            counters["compaction_events"] += 1
        fallback_id = "codex-" + str(row.get("ordinal", ref["line"]))
        if top not in known_top:
            unknown["record:" + _typename(top)] += 1
            gap("unknown-record-type", ref)
        if top == "event_msg":
            if kind not in known_event:
                unknown["event:" + _typename(kind)] += 1
                gap("unknown-event-type", ref)
            if kind == "item_completed":
                item = payload.get("item")
                if not isinstance(item, dict):
                    unknown["item:malformed"] += 1
                    continue
                item_kind = _typename(item.get("type"))
                if "compac" in str(item_kind).lower() and not record_compacted:
                    counters["compaction_events"] += 1
                if item_kind == "ImageView":
                    counters["visible_image_events_reference_only"] += 1
                if item_kind not in known_items:
                    unknown["item:" + _typename(item_kind)] += 1
                    gap("unknown-or-not-fully-decoded-completed-item", ref)
                if item_kind in {"UserMessage", "AgentMessage"}:
                    content = {key: item[key] for key in ("content", "questions", "delivery", "memory_citation", "attachments") if key in item}
                    entry = candidate(row, ref, item, "user" if item_kind == "UserMessage" else "assistant", content, "completed")
                    if entry is not None:
                        messages.append(entry)
                elif item_kind in event_tool_fields:
                    content = {key: item[key] for key in event_tool_fields[item_kind] if key in item}
                    entry = candidate(row, ref, item, "tool", content, "completed-tool")
                    if entry is not None:
                        event_tools.append(entry)
            elif kind in {"user_message", "agent_message"}:
                content = {key: payload[key] for key in ("message", "images", "local_images", "attachments", "questions", "delivery", "memory_citation") if key in payload}
                entry = candidate(row, ref, payload, "user" if kind == "user_message" else "assistant", content, "legacy")
                if entry is not None:
                    messages.append(entry)
        elif top == "response_item":
            if kind in tool_types:
                if _private(payload):
                    counters["private_records_omitted"] += 1
                    continue
                content = {key: payload[key] for key in ("type", "call_id", "name", "namespace", "arguments", "input", "output", "status") if key in payload}
                if kind.endswith("_output") and any(_TRUNCATED.search(t) for t in _texts(payload.get("output"))):
                    counters["tool_output_truncation_markers"] += 1
                entry = candidate(row, ref, payload, "tool", content, "response-tool")
                if entry is not None:
                    record = add(row, entry["ref"], payload, "tool", content, "tool", fallback_id)
                    if record is not None:
                        response_tools.append({**entry, "record": record})
                        capture_visible(entry)
            elif kind == "message":
                role = payload.get("role")
                if not isinstance(role, str) or role not in {"user", "assistant"} or _private(payload):
                    counters["private_records_omitted"] += 1
                    continue
                content = {key: payload[key] for key in ("content", "questions", "delivery", "memory_citation", "attachments") if key in payload}
                entry = candidate(row, ref, payload, role, content, "response")
                if entry is not None:
                    messages.append(entry)
            elif kind not in {"reasoning", "agent_message", "compaction"}:
                unknown["response:" + _typename(kind)] += 1
                gap("unknown-response-type", ref)

    # Completed UI messages retain their richer metadata. Other chains may add
    # unique visible content; source-wide format selection never discards them.
    selected: list[dict[str, Any]] = []
    entries_by_record: dict[str, dict[str, Any]] = {}

    def compatible(left: dict[str, Any], right: dict[str, Any]) -> bool:
        return (left["role"] == right["role"]
                and not (left["turn"] and right["turn"] and left["turn"] != right["turn"])
                and (left["channel"] == right["channel"] or "message" in {left["channel"], right["channel"]}))

    for entry in sorted(messages, key=lambda x: ({"completed": 0, "legacy": 1, "response": 2}[x["chain"]], x["ref"]["line"])):
        eligible = [old for old in selected if compatible(old, entry)]
        # A replay on a secondary chain can still join its established ID link;
        # same-chain revisions continue through add() and retain distinct text.
        matches = [old for old in eligible if entry["chain"] != old["chain"] and entry["ids"] & old["ids"]]
        match_kind = "id"
        if len(matches) > 1 and entry["text"]:
            exact = [old for old in matches if old["text"] == entry["text"]]
            if len(exact) == 1:
                matches = exact
        if not matches and entry["text"]:
            matches = [old for old in eligible if entry["chain"] not in old["chains"] and old["text"] == entry["text"]]
            match_kind = "unique-text"
        target = matches[0] if len(matches) == 1 else None
        step = planned(entry["ref"]) if replay_steps is not None else None
        if step is not None:
            # Redaction can make distinct messages textually equal. The source
            # assembly plan preserves original grouping, never message content.
            matches = [old for old in eligible if old["record"]["id"] == step["record_id"]]
            target = matches[0] if step["action"] == "merge-message" and len(matches) == 1 else None
            if step["action"] == "merge-message" and target is None:
                raise AdapterError("A message replay merge has no compatible target.")
            if target is not None:
                linked_id = entry["chain"] != target["chain"] and bool(entry["ids"] & target["ids"])
                linked_text = (bool(entry["text"]) and entry["chain"] not in target["chains"]
                               and entry["text"] == target["text"])
                if not linked_id and not linked_text:
                    raise AdapterError("A message replay merge has no source link.")
        if len(matches) > 1:
            counters["ambiguous_message_chain_matches"] += 1
            gap("ambiguous-message-chain-match-preserved-separately", entry["ref"])
        if entry["chain"] == "response" and entry["role"] == "user" and target is None:
            if _INJECTED_START.match(entry["text"]) or not entry["authored"]:
                counters["unverified_or_injected_response_users_omitted"] += 1
                gap("unverified-or-injected-response-user-not-promoted-to-dialogue", entry["ref"])
                continue
        capture_visible(entry)
        if target is not None:
            record = target["record"]
            # Normalize text container spelling only for comparison; all added
            # parts retain their original structure and embedded media bytes.
            existing_parts = _message_parts(record["content"]) + record["content"].get("supplemental_content", [])
            signatures = {_part_signature(part) for part in existing_parts}
            additions, added_parts = [], []
            parts = _message_parts(entry["content"])
            if step is not None and (not isinstance(step.get("parts"), list)
                    or any(type(i) is not int or i < 0 or i >= len(parts) for i in step["parts"])
                    or step["parts"] != sorted(set(step["parts"]))):
                raise AdapterError("Invalid supplemental part positions in replay plan.")
            for index, part in enumerate(parts):
                signature = _part_signature(part)
                include_part = index in step["parts"] if step is not None else signature not in signatures
                if include_part:
                    additions.append(_public(part, counters))
                    added_parts.append(index)
                    signatures.add(signature)
                elif signature not in signatures:
                    raise AdapterError("A replay plan omits distinct visible source content.")
            if additions:
                record["content"].setdefault("supplemental_content", []).extend(additions)
                counters["supplemental_message_parts_preserved"] += len(additions)
            added_keys, supplemental_keys = [], []
            metadata_keys = set(entry["content"]) - {"content", "message"}
            if step is not None:
                for field in ("added_keys", "supplemental_keys"):
                    values = step.get(field)
                    if (not isinstance(values, list) or any(not isinstance(k, str) or k not in metadata_keys for k in values)
                            or len(values) != len(set(values))):
                        raise AdapterError("Invalid replay metadata keys.")
                if set(step["added_keys"]) & set(step["supplemental_keys"]):
                    raise AdapterError("Replay metadata operations overlap.")
            for key, value in entry["content"].items():
                if key in {"content", "message"}:
                    continue
                add_key = key in step["added_keys"] if step is not None else key not in record["content"]
                supplement = key in step["supplemental_keys"] if step is not None else key in record["content"] and record["content"][key] != value
                if add_key:
                    if key in record["content"]:
                        raise AdapterError("Replay metadata addition would overwrite a value.")
                    record["content"][key] = _public(value, counters)
                    added_keys.append(key)
                elif supplement:
                    if key not in record["content"]:
                        raise AdapterError("Replay metadata supplement has no original value.")
                    record["content"].setdefault("supplemental_metadata", []).append({key: _public(value, counters)})
                    supplemental_keys.append(key)
                elif key not in record["content"] or record["content"][key] != value:
                    raise AdapterError("A replay plan omits distinct source metadata.")
            record["source_refs"].append(entry["ref"])
            target["chains"].add(entry["chain"])
            target["ids"].update(entry["ids"])
            counters["message_chain_matches_by_" + match_kind] += 1
            remember(entry["ref"], record, "merge-message", parts=added_parts,
                     added_keys=added_keys, supplemental_keys=supplemental_keys)
            continue
        record = add(entry["row"], entry["ref"], entry["obj"], entry["role"], entry["content"], entry["channel"], "codex-" + str(entry["ref"]["line"]))
        if record is None:
            continue
        if entry["chain"] == "response":
            counters["unmatched_public_response_messages_preserved"] += 1
        old = entries_by_record.get(record["id"])
        if old is None:
            selected_entry = {**entry, "record": record, "chains": {entry["chain"]}}
            selected.append(selected_entry)
            entries_by_record[record["id"]] = selected_entry
        else:
            old["chains"].add(entry["chain"])
            old["ids"].update(entry["ids"])

    for entry in event_tools:
        capture_visible(entry)
        matches = [old for old in response_tools if entry["ids"] & old["ids"] and not (entry["turn"] and old["turn"] and entry["turn"] != old["turn"])]
        outputs = [old for old in matches if str(old["obj"].get("type", "")).endswith("_output")]
        if outputs:
            matches = outputs
        unique = {old["record"]["id"]: old for old in matches}
        step = planned(entry["ref"]) if replay_steps is not None else None
        if step is not None:
            unique = {key: old for key, old in unique.items()
                      if key == step["record_id"]} if step["action"] == "merge-tool" else {}
            if step["action"] == "merge-tool" and len(unique) != 1:
                raise AdapterError("A tool replay merge has no target.")
        if len(unique) == 1:
            target = next(iter(unique.values()))["record"]
            visible = _public(entry["content"], counters)
            presentations = target["content"].setdefault("completed_events", [])
            append_event = step.get("append_event") if step is not None else visible not in presentations
            if type(append_event) is not bool:
                raise AdapterError("Invalid replay tool presentation decision.")
            if append_event:
                presentations.append(visible)
            elif visible not in presentations:
                raise AdapterError("A duplicate tool presentation has no matching value.")
            target["source_refs"].append(entry["ref"])
            counters["tool_event_chain_matches_by_id"] += 1
            remember(entry["ref"], target, "merge-tool", append_event=append_event)
        else:
            add(entry["row"], entry["ref"], entry["obj"], "tool", entry["content"], "tool", "codex-" + str(entry["ref"]["line"]))
            counters["unmatched_public_tool_events_preserved"] += 1
            if response_tools:
                counters["potential_duplicate_tool_presentations"] += 1
                gap("tool-event-preserved-without-verified-response-link", entry["ref"])
    records.sort(key=lambda record: min(ref.get("line", 0) for ref in record["source_refs"]))
    completeness = _completeness(source, mode, counters, unknown)
    completeness["source_evidence_gaps"] = evidence_gaps
    if replay_steps is not None and set(assembly) != set(replay_steps):
        raise AdapterError("Replay plan has missing or unused source rows.")
    return {"provider": "codex", "conversation_id": conversation_id, "title": "", "exported_at": None, "records": records,
            "source_artifact": {"format": "ai-context-codex-visible-source/1.0",
                                "content": {"conversation_id": conversation_id, "source_rows": [visible_source[key] for key in sorted(visible_source)]},
                                "replay_plan": {"schema_version": "codex-assembly/1.0", "steps": list(assembly.values())}},
            "source": source, "completeness": completeness}


def parse_transcript(path: str | os.PathLike[str], provider: str, conversation_id: str, title: str) -> dict[str, Any]:
    """Import a selected single-conversation transcript or our explicit JSON schema.

    Native ChatGPT/other-platform JSON is intentionally unsupported until its
    actual export schema has been validated. Plain HTML is retained as source
    text; it is neither executed nor treated as a verified native conversation.
    """
    if not isinstance(provider, str) or not provider or not isinstance(conversation_id, str) or not conversation_id:
        raise AdapterError("Provider and conversation identity are required.")
    if not isinstance(title, str):
        raise AdapterError("Title must be a string.")
    suffix = Path(path).suffix.lower()
    if suffix not in {".txt", ".md", ".html", ".htm", ".json"}:
        raise AdapterError("Unsupported transcript format; use TXT, MD, HTML/HTM, or the explicit transcript JSON schema.")
    data, source = _snapshot(path)
    return _parse_transcript_snapshot(data, source, suffix, provider, conversation_id, title)


def _parse_transcript_snapshot(data: bytes, source: dict[str, Any], suffix: str, provider: str, conversation_id: str, title: str) -> dict[str, Any]:
    text = _utf8(data, "transcript")
    counters: Counter[str] = Counter()
    unknown: Counter[str] = Counter()
    ref = {"path": source["path"], "byte_start": 0, "byte_end": len(data)}
    if suffix != ".json":
        records = [{"id": "transcript-" + _sha(data), "role": "user", "channel": "source-transcript",
                    "timestamp": None, "message_created_at": None, "source_recorded_at": None, "content": {"format": suffix[1:], "text": text,
                    "attribution": "Imported source document; embedded speakers are not structurally parsed."},
                    "source_refs": [ref]}]
        completeness = _completeness(source, "selected-single-conversation-text", counters, unknown)
        completeness["native_platform_structure_validated"] = False
        completeness["speaker_attribution"] = "unparsed-source-document"
        return {"provider": provider, "conversation_id": conversation_id, "title": title, "exported_at": None,
                "source_artifact": {"format": "utf8-transcript/" + suffix[1:], "content": text,
                                    "derived_identity": {"record_id": records[0]["id"],
                                                         "source_snapshot_sha256": _sha(data),
                                                         "kind": "adapter-generated-document-id-not-platform-message-id"}},
                "records": records, "source": source, "completeness": completeness}
    document = _json(text, "transcript document")
    if not isinstance(document, dict) or document.get("schema_version") not in {"ai-context-transcript/1.0", "ai-context-transcript/2.0"}:
        raise AdapterError("Unsupported JSON schema; native platform exports require a validated adapter.")
    if document.get("provider") != provider or document.get("conversation_id") != conversation_id:
        raise AdapterError("Transcript provider or conversation identity does not match the requested import.")
    if not isinstance(document.get("title"), str) or not isinstance(document.get("records"), list):
        raise AdapterError("Transcript title and records have invalid types.")
    if "completeness" in document and not isinstance(document["completeness"], dict):
        raise AdapterError("Transcript completeness must be an object.")
    _validate_extensions({"exported_at": document.get("exported_at")}, "transcript export metadata")
    records = []
    identities = set()
    for index, item in enumerate(document["records"]):
        label = f"transcript record {index + 1}"
        if not isinstance(item, dict) or not isinstance(item.get("role"), str) or item["role"] not in {"user", "assistant", "tool"}:
            raise AdapterError(f"Invalid public role in {label}.")
        if not isinstance(item.get("id"), str) or not item["id"] or item["id"] in identities:
            raise AdapterError(f"Missing or duplicate identity in {label}.")
        identities.add(item["id"])
        if "content" not in item or _private(item):
            raise AdapterError(f"Missing content or private channel in {label}.")
        if "channel" in item and not isinstance(item["channel"], str):
            raise AdapterError(f"Invalid channel in {label}.")
        _validate_extensions(item, label)
        references = item.get("source_refs", [])
        if not isinstance(references, list) or any(not isinstance(x, dict) for x in references):
            raise AdapterError(f"Invalid source references in {label}.")
        public_item = _public(item, counters)
        content = public_item["content"]
        clean_refs = public_item.get("source_refs", [])
        # The normalized exchange format is explicitly public. Keep unknown
        # extensions as data, rather than projecting away future graph fields.
        records.append({**public_item, "id": item["id"], "role": item["role"], "channel": _channel(item, "tool" if item["role"] == "tool" else "message"),
                        "timestamp": item.get("timestamp"), "content": content,
                        "message_created_at": item.get("message_created_at", item.get("created_at", item.get("timestamp"))),
                        "source_recorded_at": item.get("source_recorded_at"),
                        "source_refs": clean_refs + [{**ref, "record_index": index}]})
        counters["exported_" + item["role"]] += 1
    timestamps = [x["timestamp"] for x in records if x["timestamp"] is not None]
    if timestamps:
        source.update(first_timestamp=timestamps[0], last_timestamp=timestamps[-1])
    completeness = _completeness(source, document["schema_version"], counters, unknown)
    completeness["schema_validated"] = True
    completeness["native_platform_structure_validated"] = False
    # Do not accept an imported claim of whole-platform completeness as authority.
    completeness["source_declared_complete"] = document.get("completeness", {}).get("complete") is True
    known_top = {"schema_version", "provider", "conversation_id", "title", "records", "completeness", "exported_at"}
    return {"provider": provider, "conversation_id": conversation_id, "title": title or document["title"],
            "exported_at": document.get("exported_at"),
            "conversation_metadata": {key: value for key, value in document.items() if key not in known_top},
            "source_declared_completeness": document.get("completeness", {}),
            "source_artifact": {"format": document["schema_version"], "content": text},
            "records": records, "source": source, "completeness": completeness}


def parse_read_thread(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Read saved public read_thread pages; previews never become messages."""
    data, source = _snapshot(path)
    return _parse_read_thread_snapshot(data, source)


def _parse_read_thread_snapshot(data: bytes, source: dict[str, Any],
                                replay_steps: dict[tuple[int, ...], dict[str, Any]] | None = None) -> dict[str, Any]:
    text = _utf8(data, "read_thread bundle")
    bundle = _json(text, "read_thread bundle")
    if not isinstance(bundle, dict) or bundle.get("schema_version") != "ai-context-read-thread/1.0":
        raise AdapterError("Unsupported read_thread bundle schema.")
    pages = bundle.get("pages")
    if not isinstance(pages, list) or not pages:
        raise AdapterError("read_thread bundle requires nonempty pages.")
    ids: set[str] = set()
    orders: set[str] = set()
    counters: Counter[str] = Counter()
    unknown: Counter[str] = Counter()
    for index, page in enumerate(pages):
        if not isinstance(page, dict) or page.get("schemaVersion") != 1:
            raise AdapterError("Only observed read_thread schemaVersion 1 is supported.")
        thread = page.get("thread")
        if not isinstance(thread, dict) or thread.get("kind") != "chatgpt" or not isinstance(thread.get("id"), str) or not thread["id"]:
            raise AdapterError("A read_thread page must identify one ChatGPT conversation.")
        ids.add(thread["id"])
        if not isinstance(page.get("turns"), list) or not isinstance(page.get("page"), dict):
            raise AdapterError("read_thread page requires turn and pagination structures.")
        order = page["page"].get("order")
        if order not in {"newest_first", "oldest_first"}:
            raise AdapterError("Unsupported read_thread page ordering.")
        orders.add(order)
        if "attachments" in page and not isinstance(page["attachments"], list):
            raise AdapterError("read_thread attachments must be a list.")
        for turn in page["turns"]:
            if not isinstance(turn, dict) or not isinstance(turn.get("id"), str) or not isinstance(turn.get("items"), list):
                raise AdapterError("Malformed read_thread turn.")
            for item in turn["items"]:
                if not isinstance(item, dict):
                    raise AdapterError("Malformed read_thread item.")
                kind = str(item.get("type", "")).lower()
                if _private(item) or any(word in kind for word in ("reasoning", "systemmessage", "developermessage")):
                    raise AdapterError("read_thread bundle contains nonpublic engine items.")
    if len(ids) != 1 or len(orders) != 1:
        raise AdapterError("read_thread pages disagree on conversation identity or ordering.")
    conversation_id = next(iter(ids))
    if bundle.get("conversation_id", conversation_id) != conversation_id or bundle.get("provider", "chatgpt") != "chatgpt":
        raise AdapterError("read_thread bundle identity does not match its pages.")
    _validate_extensions({"exported_at": bundle.get("exported_at")}, "read_thread export metadata")
    if "completeness" in bundle and not isinstance(bundle["completeness"], dict):
        raise AdapterError("read_thread declared completeness must be an object.")
    newest_first = next(iter(orders)) == "newest_first"
    page_indices = list(range(len(pages)))
    if newest_first:
        page_indices.reverse()
    records: list[dict[str, Any]] = []
    seen: dict[str, dict[str, Any]] = {}
    gaps: list[dict[str, Any]] = []
    assembly = {}

    def remember(location, record, action):
        step = {"locator": list(location), "record_id": record["id"], "action": action}
        if replay_steps is not None and step != replay_steps.get(location):
            raise AdapterError("API replay operation disagrees with its source mapping.")
        assembly[location] = step
    for page_index in page_indices:
        page = pages[page_index]
        turn_indices = list(range(len(page["turns"])))
        if newest_first:
            turn_indices.reverse()
        for turn_index in turn_indices:
            turn = page["turns"][turn_index]
            for item_index, item in enumerate(turn["items"]):
                kind = item.get("type")
                ref = {"path": source["path"], "page_index": page_index, "turn_index": turn_index,
                       "item_index": item_index, "turn_id": turn["id"], "byte_start": 0, "byte_end": len(data)}
                if kind not in {"userMessage", "agentMessage"}:
                    unknown["read-thread-item:" + _typename(kind)] += 1
                    gaps.append({"reason": "unparsed-public-api-item", "source_refs": [ref]})
                    continue
                identity = item.get("id")
                if not isinstance(identity, str) or not identity:
                    raise AdapterError("A read_thread message has no stable identity.")
                if kind == "userMessage":
                    if not isinstance(item.get("content"), list):
                        raise AdapterError("read_thread userMessage requires actual content.")
                    content = item["content"]
                else:
                    if not isinstance(item.get("text"), str):
                        raise AdapterError("read_thread agentMessage requires actual text, not preview.")
                    content = item["text"]
                created = next((item[key] for key in ("timestamp", "createdAt", "created_at", "create_time") if item.get(key) is not None), None)
                _validate_extensions({"message_created_at": created}, "read_thread message")
                record = {**{key: value for key, value in item.items() if key not in {"type", "id", "text", "content"}},
                          "id": identity, "source_message_id": identity,
                          "role": "user" if kind == "userMessage" else "assistant", "channel": "message",
                          "timestamp": created, "message_created_at": created, "source_recorded_at": None,
                          "turn_id": turn["id"], "turn_started_at": turn.get("startedAt"),
                          "turn_completed_at": turn.get("completedAt"), "turn_status": turn.get("status"),
                          "source_item_type": kind, "content": _public(content, counters), "source_refs": [ref]}
                comparable = lambda value: {key: val for key, val in value.items() if key not in {"id", "source_refs"}}
                location = (page_index, turn_index, item_index)
                if replay_steps is not None:
                    step = replay_steps.get(location)
                    if step is None:
                        raise AdapterError("An API source message has no replay step.")
                    if step["record_id"] != identity and (identity not in seen or not re.fullmatch(
                            re.escape(identity) + r":source-version:[0-9a-f]{16}", step["record_id"])):
                        raise AdapterError("API replay target is not a version of this source identity.")
                    record["id"] = step["record_id"]
                    existing = seen.get(record["id"])
                    if step["action"] == "duplicate":
                        if existing is None or comparable(existing) != comparable(record):
                            raise AdapterError("Duplicate API replay message disagrees with its target.")
                        existing["source_refs"].append(ref)
                        counters["duplicate_api_messages_coalesced"] += 1
                        remember(location, existing, "duplicate")
                        continue
                    if step["action"] != "new" or existing is not None:
                        raise AdapterError("API replay message has an invalid target or action.")
                else:
                    existing = seen.get(identity)
                if existing is not None:
                    if comparable(existing) == comparable(record):
                        existing["source_refs"].append(ref)
                        counters["duplicate_api_messages_coalesced"] += 1
                        remember(location, existing, "duplicate")
                        continue
                    record["id"] = identity + ":source-version:" + _sha(json.dumps(comparable(record), sort_keys=True, ensure_ascii=False).encode())[:16]
                    prior = seen.get(record["id"])
                    if prior is not None:
                        if comparable(prior) != comparable(record):
                            raise AdapterError("read_thread source-version identity collision.")
                        prior["source_refs"].append(ref)
                        remember(location, prior, "duplicate")
                        continue
                records.append(record)
                seen[record["id"]] = record
                remember(location, record, "new")
                counters["exported_" + record["role"]] += 1
    metadata = {key: value for key, value in bundle.items() if key not in {"schema_version", "pages", "provider", "conversation_id", "title", "exported_at", "completeness"}}
    metadata.update(thread={key: value for key, value in pages[0]["thread"].items() if key != "preview"},
                    page_metadata=[page["page"] for page in pages],
                    page_attachments=[{"page_index": index, "attachments": _public(page.get("attachments", []), counters)} for index, page in enumerate(pages)],
                    thread_preview_present=any("preview" in page["thread"] for page in pages))
    last_pagination = pages[-1]["page"]
    exhausted = last_pagination.get("hasMore") is False and not last_pagination.get("nextCursor")
    completeness = _completeness(source, "read_thread.schemaVersion=1", counters, unknown)
    completeness.update(schema_validated=True, text_status="api-pagination-exhausted" if exhausted else "partial",
                        pagination_exhausted=exhausted, pagination_chain_verified=False,
                        page_count=len(pages), source_evidence_gaps=gaps,
                        structure_status="branch-relationships-not-established-by-this-api",
                        preview_used_as_body=False)
    completeness["limitations"].append("Page exhaustion describes supplied pagination metadata; missing branches, deleted messages, and omitted pages remain unverified.")
    if replay_steps is not None and set(assembly) != set(replay_steps):
        raise AdapterError("API replay plan has missing or unused source items.")
    return {"provider": "chatgpt", "conversation_id": conversation_id,
            "title": bundle.get("title", pages[0]["thread"].get("title", "")),
            "exported_at": bundle.get("exported_at"), "conversation_metadata": metadata,
            "source_declared_completeness": bundle.get("completeness", {}), "records": records,
            "source": source, "completeness": completeness,
            "source_artifact": {"format": "ai-context-read-thread/1.0", "content": text,
                                "replay_plan": {"schema_version": "read-thread-assembly/1.0", "steps": list(assembly.values())}}}


def _replay_steps(artifact, schema, width):
    """Validate adapter-generated assembly decisions; never accept message copies.

    Old artifacts without a plan keep the original parser behavior. New plans
    preserve source identities and equality decisions that redaction can erase.
    Each entry is later consumed and checked against its actual source location.
    """
    if "replay_plan" not in artifact:
        return None
    plan = artifact["replay_plan"]
    if (not isinstance(plan, dict) or set(plan) != {"schema_version", "steps"}
            or plan["schema_version"] != schema or not isinstance(plan["steps"], list)):
        raise AdapterError("Unsupported source replay plan.")
    fields = {"new": set(), "duplicate": set()}
    if width == 2:
        fields.update({"merge-message": {"parts", "added_keys", "supplemental_keys"}, "merge-tool": {"append_event"}})
    result = {}
    for step in plan["steps"]:
        if (not isinstance(step, dict) or not isinstance(step.get("action"), str)
                or step["action"] not in fields
                or set(step) != {"locator", "record_id", "action"} | fields[step["action"]]
                or not isinstance(step["record_id"], str) or not step["record_id"]
                or not isinstance(step["locator"], list) or len(step["locator"]) != width
                or any(type(index) is not int or index < 0 for index in step["locator"])):
            raise AdapterError("Invalid source replay step.")
        key = tuple(step["locator"])
        if key in result or (width == 2 and key[0] < 1):
            raise AdapterError("Duplicate or invalid source replay location.")
        result[key] = step
    return result


def replay_source_artifact(artifact: dict[str, Any], provider: str, conversation_id: str, title: str) -> dict[str, Any]:
    """Reparse a self-contained public artifact without touching original paths.

    Consumers compare public records/relationships, excluding source_refs and
    capture provenance: redaction can change byte lengths and filesystem paths.
    """
    if not isinstance(artifact, dict) or not isinstance(artifact.get("format"), str):
        raise AdapterError("Malformed source artifact.")
    format_name, content = artifact["format"], artifact.get("content")

    def source_for(data: bytes) -> dict[str, Any]:
        return {"path": "/source-artifact", "snapshot_sha256": _sha(data), "snapshot_bytes": len(data),
                "parsed_bytes": len(data), "trailing_bytes_ignored": 0, "mtime_ns": None,
                "first_timestamp": None, "last_timestamp": None, "adapter_version": ADAPTER_VERSION,
                "prefix_stability_checked": False, "replayed_from_artifact": True}

    if format_name == "ai-context-codex-visible-source/1.0":
        if provider != "codex" or not isinstance(content, dict) or content.get("conversation_id") != conversation_id or not isinstance(content.get("source_rows"), list):
            raise AdapterError("Codex source artifact identity or structure is invalid.")
        rows = [{"type": "session_meta", "payload": {"id": conversation_id}}]
        refs = {}
        for position, entry in enumerate(content["source_rows"], 2):
            if not isinstance(entry, dict) or not isinstance(entry.get("row"), dict) or not isinstance(entry.get("source_ref"), dict):
                raise AdapterError("Malformed public Codex source mapping.")
            rows.append(entry["row"])
            refs[position] = entry["source_ref"]
        data = b"".join(json.dumps(row, ensure_ascii=False).encode() + b"\n" for row in rows)
        steps = _replay_steps(artifact, "codex-assembly/1.0", 2)
        if steps is not None and set(steps) != {(ref.get("line"), ref.get("byte_start")) for ref in refs.values()}:
            raise AdapterError("Codex replay plan does not cover the retained source rows.")
        parsed = _parse_codex_snapshot(data, source_for(data), refs, steps)
    elif isinstance(content, str):
        data = content.encode("utf-8")
        if format_name in {"ai-context-transcript/1.0", "ai-context-transcript/2.0"}:
            parsed = _parse_transcript_snapshot(data, source_for(data), ".json", provider, conversation_id, title)
        elif format_name.startswith("utf8-transcript/") and format_name.split("/", 1)[1] in {"txt", "md", "html", "htm"}:
            parsed = _parse_transcript_snapshot(data, source_for(data), "." + format_name.split("/", 1)[1], provider, conversation_id, title)
            identity = artifact.get("derived_identity")
            if identity is not None:
                if not isinstance(identity, dict) or not isinstance(identity.get("source_snapshot_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", identity["source_snapshot_sha256"]) or identity.get("record_id") != "transcript-" + identity["source_snapshot_sha256"]:
                    raise AdapterError("Invalid derived transcript identity in source artifact.")
                parsed["records"][0]["id"] = identity["record_id"]
                parsed["source_artifact"] = artifact
        elif format_name == "ai-context-read-thread/1.0":
            parsed = _parse_read_thread_snapshot(data, source_for(data), _replay_steps(artifact, "read-thread-assembly/1.0", 3))
        else:
            raise AdapterError("Unsupported source artifact format.")
    else:
        raise AdapterError("Source artifact requires its preserved UTF-8 text.")
    if parsed["provider"] != provider or parsed["conversation_id"] != conversation_id:
        raise AdapterError("Replayed source identity does not match the archive.")
    return parsed
