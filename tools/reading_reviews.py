"""Explicit, project-scoped reading plans and append-only coverage receipts.

Retrieval is not acknowledgement. Only a separate caller action may confirm
that returned text was actually read; no operation executes archived content.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import os
from pathlib import Path
import re
import uuid

import context_library as lib


SCHEMA = "ai-context-reading-review/1.0"
REVIEW_ID = re.compile(r"[0-9a-f]{32}\Z")
EVENT_NAME = re.compile(r"[0-9]{12}\.json\Z")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _signed(value, field):
    return {**value, field: lib.digest(lib.canonical_bytes(value))}


def _verify_signature(value, field):
    if value.get(field) != lib.digest(lib.canonical_bytes({k: v for k, v in value.items() if k != field})):
        raise lib.LibraryError("Reading review integrity verification failed.")


def _read_json(path):
    lib.check_private_file(path)
    return lib.read_json(path)


def _root(root):
    root = lib.checked_path(Path(root))
    lib.private_directory(root, create=False)
    lib.read_json(root / "library.json")
    return root


def _directory(root, review_id):
    root = _root(root)
    if not isinstance(review_id, str) or not REVIEW_ID.fullmatch(review_id):
        raise lib.LibraryError("Review ID must be the complete generated identifier.")
    lib.private_directory(root / "reviews", create=False)
    directory = lib.private_directory(root / "reviews" / review_id, create=False)
    lib.private_directory(directory / "events", create=False)
    if {p.name for p in directory.iterdir()} != {"plan.json", "events", ".lock"}:
        raise lib.LibraryError("Reading review contains missing or unexpected files.")
    return root, directory


@contextmanager
def _locked(directory, *, exclusive=False):
    path = directory / ".lock"
    lib.check_private_file(path)
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def merge_intervals(intervals):
    result = []
    for start, end in sorted(intervals):
        if result and start <= result[-1][1] + 1:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return result


def _unread(intervals, count):
    result, cursor = [], 1
    for start, end in merge_intervals(intervals):
        if cursor < start:
            result.append([cursor, start - 1])
        cursor = end + 1
    if cursor <= count:
        result.append([cursor, count])
    return result


def _range(start, end, count):
    if (type(start) is not int or type(end) is not int
            or not 1 <= start <= end <= count):
        raise lib.LibraryError("Reading range must be within the pinned record count.")


def _covers(intervals, start, end):
    return any(a <= start and end <= b for a, b in merge_intervals(intervals))


def _resolve_conversation(plan, conversation):
    if not isinstance(conversation, str) or not conversation:
        raise lib.LibraryError("A planned conversation identifier is required.")
    matches = [item for item in plan["conversations"]
               if conversation in {item["conversation_key"], item["conversation_id"]}]
    if len(matches) != 1:
        raise lib.LibraryError("Conversation is absent from this plan or ambiguous; use its full key.")
    return matches[0]


def _load(root, directory):
    plan = _read_json(directory / "plan.json")
    _verify_signature(plan, "plan_sha256")
    if (plan.get("schema_version") != SCHEMA or plan.get("review_id") != directory.name
            or plan.get("mode") not in {"full", "topic"}
            or not isinstance(plan.get("project"), str)
            or not plan["project"]
            or plan.get("coverage_scope") not in {"selected-conversation", "project-at-plan-creation"}
            or not isinstance(plan.get("created_at"), str)
            or (plan.get("topic") is not None and not isinstance(plan["topic"], str))
            or not isinstance(plan.get("conversations"), list) or not plan["conversations"]):
        raise lib.LibraryError("Reading plan schema or identity is invalid.")
    # The authoritative project links are checked again, including for v2
    # project-neutral snapshots. Editing a plan cannot grant another scope.
    available = {}
    for path, manifest in lib.enumerate_revisions(root, plan["project"]):
        path = lib.checked_path(path)
        if not path.is_relative_to(root):
            raise lib.LibraryError("Revision path escapes the library.")
        available[(manifest["conversation_key"], manifest["revision"], path.relative_to(root).as_posix())] = manifest
    keys = set()
    for item in plan["conversations"]:
        if not isinstance(item, dict):
            raise lib.LibraryError("Planned conversation metadata is invalid.")
        relative = item.get("revision_path")
        if (not isinstance(relative, str) or Path(relative).is_absolute()
                or ".." in Path(relative).parts):
            raise lib.LibraryError("Planned revision path is invalid.")
        key, revision = item.get("conversation_key"), item.get("revision")
        if (not isinstance(key, str) or not lib.HEX64.fullmatch(key)
                or not isinstance(revision, str) or not lib.HEX64.fullmatch(revision)
                or key in keys):
            raise lib.LibraryError("Planned conversation identity is invalid or repeated.")
        keys.add(key)
        manifest = available.get((key, revision, relative))
        if manifest is None:
            raise lib.LibraryError("A pinned revision is no longer available in this project's scope.")
        for field in ("provider", "conversation_id", "record_count", "manifest_sha256"):
            if item.get(field) != manifest.get(field):
                raise lib.LibraryError("Pinned revision metadata changed.")
        if type(item["record_count"]) is not int or item["record_count"] < 1:
            raise lib.LibraryError("Pinned record count is invalid.")
    events, retrieved = [], defaultdict(list)
    for sequence, path in enumerate(sorted((directory / "events").iterdir()), 1):
        if not EVENT_NAME.fullmatch(path.name) or path.name != f"{sequence:012d}.json":
            raise lib.LibraryError("Reading event sequence is incomplete or contains unexpected files.")
        event = _read_json(path)
        _verify_signature(event, "event_sha256")
        if (event.get("schema_version") != SCHEMA or event.get("review_id") != plan["review_id"]
                or event.get("plan_sha256") != plan["plan_sha256"]
                or event.get("sequence") != sequence or event.get("type") not in {"retrieved", "acknowledged"}):
            raise lib.LibraryError("Reading event schema or plan binding is invalid.")
        item = _resolve_conversation(plan, event.get("conversation_key"))
        if event.get("revision") != item["revision"]:
            raise lib.LibraryError("Reading event refers to another revision.")
        start, end = event.get("start"), event.get("end")
        _range(start, end, item["record_count"])
        if event["type"] == "retrieved":
            retrieved[item["conversation_key"]].append([start, end])
        elif not _covers(retrieved[item["conversation_key"]], start, end):
            raise lib.LibraryError("Acknowledgement has no preceding retrieval coverage.")
        events.append(event)
    return plan, events


def _select(candidates):
    try:
        from revision_selection import select_revision, SelectionError
    except ImportError:
        # Safe while installing the independently maintained selector: a single
        # already explicit revision is unambiguous, multiple revisions are not.
        if len(candidates) == 1:
            return candidates[0]
        raise lib.LibraryError("Revision selection is unavailable; explicitly select a revision.") from None
    try:
        return select_revision(candidates)
    except SelectionError:
        raise lib.LibraryError("Revision choice is ambiguous; supply explicit versions.") from None


def create_review(root, project, mode, conversation=None, revision=None, topic=None, versions=None):
    """Pin a project's available conversations without acknowledging any text.

    ``versions`` maps full conversation keys to revision hashes. Supplying a
    conversation narrows the plan and is explicitly labelled in its scope.
    """
    root = _root(root)
    if not isinstance(project, str) or not project:
        raise lib.LibraryError("A reading plan requires an explicit canonical project.")
    if mode not in {"full", "topic"}:
        raise lib.LibraryError("Review mode must be full or topic.")
    if topic is not None and (not isinstance(topic, str) or not topic.strip()):
        raise lib.LibraryError("Topic must be nonempty text when supplied.")
    if mode == "topic" and topic is None:
        raise lib.LibraryError("A topic review must state its question or scope.")
    if revision is not None and (not isinstance(revision, str) or not lib.HEX64.fullmatch(revision)):
        raise lib.LibraryError("Explicit revision must be a complete hash.")
    versions = {} if versions is None else versions
    if not isinstance(versions, dict) or any(
            not isinstance(k, str) or not lib.HEX64.fullmatch(k)
            or not isinstance(v, str) or not lib.HEX64.fullmatch(v) for k, v in versions.items()):
        raise lib.LibraryError("Versions must map full conversation keys to revision hashes.")
    groups = defaultdict(list)
    for path, manifest in lib.enumerate_revisions(root, project):
        groups[manifest["conversation_key"]].append((path, manifest))
    if conversation is not None:
        matches = [key for key, values in groups.items()
                   if any(conversation in {m["conversation_key"], m["conversation_id"]} for _, m in values)]
        if len(matches) != 1:
            raise lib.LibraryError("Conversation is unavailable or ambiguous in the requested project.")
        groups = {matches[0]: groups[matches[0]]}
    if not groups:
        raise lib.LibraryError("No archived conversations are available for this reading plan.")
    if set(versions) - set(groups):
        raise lib.LibraryError("Explicit versions contain a conversation outside this plan.")
    if revision is not None and len(groups) != 1:
        raise lib.LibraryError("A single revision requires a single conversation; otherwise use versions.")
    items = []
    for key, candidates in sorted(groups.items()):
        explicit = versions.get(key, revision)
        if key in versions and revision is not None and explicit != revision:
            raise lib.LibraryError("Conflicting explicit revision selections.")
        if explicit is not None:
            candidates = [(p, m) for p, m in candidates if m["revision"] == explicit]
            if not candidates:
                raise lib.LibraryError("The selected revision is not available for this conversation.")
        path, _ = _select(candidates)
        path = lib.checked_path(path)
        if not path.is_relative_to(root):
            raise lib.LibraryError("Revision path escapes the library.")
        manifest, _ = lib.validate_revision(path, project)
        items.append({"conversation_key": key, "conversation_id": manifest["conversation_id"],
                      "provider": manifest["provider"], "revision": manifest["revision"],
                      "revision_path": path.relative_to(root).as_posix(),
                      "record_count": manifest["record_count"], "manifest_sha256": manifest["manifest_sha256"],
                      "source_completeness": manifest["completeness"]})
    redactor = lib.Redactor()
    plan = {"schema_version": SCHEMA, "review_id": uuid.uuid4().hex, "project": project,
            "mode": mode, "topic": redactor.text(topic) if topic is not None else None,
            "created_at": _now(), "coverage_scope": "selected-conversation" if conversation is not None else "project-at-plan-creation",
            "conversations": items, "attachments": {"status": "unknown", "complete": False},
            "whole_project_history_complete": False,
            "acknowledgement_policy": "Explicit caller confirmation after actually reading delivered text; never automatic."}
    plan = _signed(plan, "plan_sha256")
    reviews = lib.private_directory(root / "reviews", create=True)
    directory = reviews / plan["review_id"]
    # UUID plus mkdir without exist_ok prevents an existing plan being reused.
    directory.mkdir(mode=0o700)
    (directory / "events").mkdir(mode=0o700)
    lib.write_private(directory / ".lock", b"")
    lib.write_private(directory / "plan.json", lib.canonical_bytes(plan) + b"\n")
    lib.fsync_directory(directory)
    lib.fsync_directory(reviews)
    return plan


def review(root, review_id):
    root, directory = _directory(root, review_id)
    with _locked(directory):
        plan, events = _load(root, directory)
        return {"plan": plan, "events": events}


def _append(directory, plan, events, item, kind, start, end, **extra):
    event = _signed({"schema_version": SCHEMA, "review_id": plan["review_id"],
                     "plan_sha256": plan["plan_sha256"], "sequence": len(events) + 1,
                     "type": kind, "conversation_key": item["conversation_key"],
                     "revision": item["revision"], "start": start, "end": end,
                     "recorded_at": _now(), **extra}, "event_sha256")
    lib.write_private(directory / "events" / f"{event['sequence']:012d}.json", lib.canonical_bytes(event) + b"\n")
    lib.fsync_directory(directory / "events")
    return event


def fetch(root, review_id, conversation, start, end, include_media=False):
    root, directory = _directory(root, review_id)
    with _locked(directory, exclusive=True):
        plan, events = _load(root, directory)
        item = _resolve_conversation(plan, conversation)
        _range(start, end, item["record_count"])
        if type(include_media) is not bool:
            raise lib.LibraryError("include_media must be a boolean.")
        _, records = lib.validate_revision(root / item["revision_path"], plan["project"])
        result = [{"ordinal": i, **record,
                   "content": record["content"] if include_media else lib.media_display_projection(record["content"])}
                  for i, record in enumerate(records, 1) if start <= i <= end]
        event = _append(directory, plan, events, item, "retrieved", start, end,
                        media_view="original-inline-payloads" if include_media else "display-projection")
        return {"review_id": review_id, "project": plan["project"], "conversation_key": item["conversation_key"],
                "revision": item["revision"], "start": start, "end": end, "record_count": item["record_count"],
                "records": result, "receipt": event, "acknowledged": False,
                "attachments": {"status": "unknown", "complete": False}}


def acknowledge(root, review_id, conversation, start, end, note=None):
    root, directory = _directory(root, review_id)
    with _locked(directory, exclusive=True):
        plan, events = _load(root, directory)
        item = _resolve_conversation(plan, conversation)
        _range(start, end, item["record_count"])
        if note is not None and (not isinstance(note, str) or len(note) > 4096):
            raise lib.LibraryError("Acknowledgement note must be text of at most 4096 characters.")
        retrieved = [[e["start"], e["end"]] for e in events
                     if e["conversation_key"] == item["conversation_key"] and e["type"] == "retrieved"]
        if not _covers(retrieved, start, end):
            raise lib.LibraryError("Only previously retrieved ranges can be acknowledged.")
        lib.validate_revision(root / item["revision_path"], plan["project"])
        event = _append(directory, plan, events, item, "acknowledged", start, end,
                        note=lib.Redactor().text(note) if note is not None else None,
                        basis="explicit-caller-confirmation-of-actual-reading")
        return {"review_id": review_id, "receipt": event}


def report(root, review_id):
    state = review(root, review_id)
    plan, events = state["plan"], state["events"]
    items = []
    for item in plan["conversations"]:
        ranges = {kind: merge_intervals([[e["start"], e["end"]] for e in events
                  if e["type"] == kind and e["conversation_key"] == item["conversation_key"]])
                  for kind in ("retrieved", "acknowledged")}
        unread = _unread(ranges["acknowledged"], item["record_count"])
        items.append({**item, **ranges, "unread": unread,
                      "retrieved_count": sum(b - a + 1 for a, b in ranges["retrieved"]),
                      "acknowledged_count": sum(b - a + 1 for a, b in ranges["acknowledged"]),
                      "unread_count": sum(b - a + 1 for a, b in unread),
                      "body_read_complete": not unread})
    complete = all(item["body_read_complete"] for item in items)
    return {"schema_version": SCHEMA, "review_id": review_id, "project": plan["project"],
            "mode": plan["mode"], "topic": plan["topic"], "coverage_scope": plan["coverage_scope"],
            "planned_at": plan["created_at"], "conversation_count": len(items),
            "record_count": sum(i["record_count"] for i in items),
            "retrieved_count": sum(i["retrieved_count"] for i in items),
            "acknowledged_count": sum(i["acknowledged_count"] for i in items),
            "unread_count": sum(i["unread_count"] for i in items),
            "status": ("complete" if complete else "in-progress") if plan["mode"] == "full" else "scoped",
            "plan_body_read_complete": complete,
            "full_project_snapshot_read_complete": complete and plan["mode"] == "full" and plan["coverage_scope"] == "project-at-plan-creation",
            "whole_project_history_complete": False,
            "attachments": {"status": "unknown", "complete": False},
            "conversations": items,
            "limitations": ["Coverage applies only to the pinned snapshots available when this plan was created.",
                            "Retrieval receipts do not establish reading; acknowledgements are explicit caller assertions.",
                            "Reading text does not establish attachment inspection or complete platform history."]}
