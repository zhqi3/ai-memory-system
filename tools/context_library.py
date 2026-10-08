#!/usr/bin/env python3
"""Immutable, project-scoped storage for explicitly requested visible transcripts.

This module never opens Codex databases, credentials, or native export archives.
Adapters own source parsing. The library is separate from automatic startup,
dream consolidation, and research search; ``read`` is an explicit operation.
"""

from __future__ import annotations

import argparse
import base64
import binascii
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any

sys.modules.setdefault("context_library", sys.modules[__name__])


SCHEMA_VERSION = "2.0"
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
PROVIDER = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}\Z")
FORBIDDEN = {"analysis", "system", "developer"}
VISIBLE_ROLES = {"user", "assistant", "tool", "function"}
LOCAL_PATH_KEYS = {"path", "source_path", "local_path", "file_path", "absolute_path"}
BINARY_MEDIA_TYPES = {
    "image", "input_image", "output_image", "local_image", "localimage",
    "audio", "input_audio", "output_audio", "video",
}
MEDIA_PAYLOAD_KEYS = {"data", "blob", "bytes", "base64"}
BASE64_MEDIA_URI = re.compile(
    r"data:(?:image|audio|video)/[^,\s]*;base64,(?P<payload>[A-Za-z0-9+/=\r\n]*)\Z", re.I)
REDACTION_PATTERNS = (
    ("private-key", re.compile(
        r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"
        r"[\s\S]*?-----END (?:RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----")),
    ("openai-key", re.compile(r"(?<![A-Za-z0-9_-])sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])")),
    ("github-token", re.compile(r"(?<![A-Za-z0-9_])(?:gh[opusr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})(?![A-Za-z0-9_])")),
    ("gitlab-token", re.compile(r"(?<![A-Za-z0-9_-])glpat-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])")),
    ("aws-access-key", re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])")),
    ("slack-token", re.compile(r"(?<![A-Za-z0-9-])xox[baprs]-[A-Za-z0-9-]{24,}(?![A-Za-z0-9-])")),
    ("jwt-token", re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{16,}(?![A-Za-z0-9_-])")),
)


class LibraryError(Exception):
    """An actionable error that never embeds untrusted source content."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def binary_media_object(value: dict[str, Any]) -> bool:
    kind = str(value.get("type", "")).strip().lower()
    mime = value.get("mimeType", value.get("mime_type"))
    return kind in BINARY_MEDIA_TYPES or (
        isinstance(mime, str) and mime.lower().split("/", 1)[0] in {"image", "audio", "video"})


def valid_base64(value: str) -> bool:
    # Wrapped base64 is allowed, but prose, URL-safe token strings, and malformed
    # payloads must not acquire the binary redaction exception from a type label.
    normalized = value.replace("\r", "").replace("\n", "")
    if not normalized:
        return False
    # Some credential alphabets are also valid base64 (for example AWS access
    # key IDs). A whole-payload credential is text to redact, not a binary
    # exception. Substring coincidences inside real encoded media stay intact.
    if any(pattern.fullmatch(normalized) for _, pattern in REDACTION_PATTERNS):
        return False
    try:
        base64.b64decode(normalized, validate=True)
    except (ValueError, binascii.Error):
        return False
    return True


def full_media_uri(value: str) -> bool:
    # Whole-value boundaries keep ordinary prose and tool output out of this
    # binary exception, including text that merely mentions a data URI.
    match = BASE64_MEDIA_URI.fullmatch(value)
    return bool(match and valid_base64(match.group("payload")))


def binary_payload(value: str) -> bool:
    return full_media_uri(value) or valid_base64(value)


def media_display_projection(value: Any) -> Any:
    """Abbreviate binary media only in the generated Markdown display view."""
    def marker(payload: str) -> dict[str, Any]:
        return {"inline_media_display_omitted": True, "preserved_in": "messages.jsonl",
                "length": len(payload), "sha256": digest(payload.encode("utf-8"))}

    if isinstance(value, str):
        return marker(value) if full_media_uri(value) else value
    if isinstance(value, list):
        return [media_display_projection(item) for item in value]
    if isinstance(value, dict):
        media = binary_media_object(value)
        return {key: marker(item) if media and key in MEDIA_PAYLOAD_KEYS and isinstance(item, str) and binary_payload(item)
                else media_display_projection(item) for key, item in value.items()}
    return value


def checked_path(path: Path, *, must_exist: bool = True) -> Path:
    """Reject traversal and symlinks, including symlinked ancestor directories."""
    if ".." in path.parts:
        raise LibraryError("Path traversal is not allowed.")
    path = path.absolute()
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            if must_exist:
                raise LibraryError("A required path does not exist.") from None
            continue
        if stat.S_ISLNK(mode):
            raise LibraryError("Symbolic links are not allowed in library or memory paths.")
    return path


def regular_file(path: Path) -> Path:
    checked_path(path)
    if not stat.S_ISREG(path.lstat().st_mode):
        raise LibraryError("Expected a regular file.")
    return path


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(regular_file(path).read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise LibraryError("A metadata file is not valid UTF-8 JSON.") from None
    if not isinstance(value, dict):
        raise LibraryError("Metadata must be a JSON object.")
    return value


def private_directory(path: Path, *, create: bool) -> Path:
    checked_path(path, must_exist=not create)
    if create:
        path.mkdir(mode=0o700, parents=False, exist_ok=True)
    if not path.is_dir():
        raise LibraryError("Expected a directory.")
    # Existing data must already be private. Read-only commands never chmod.
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise LibraryError("Library directories must have mode 0700; permission repair is separate.")
    return path


def check_private_file(path: Path) -> None:
    regular_file(path)
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise LibraryError("Library data files must have mode 0600.")


def project_slug(canonical_name: str) -> str:
    readable = re.sub(r"[^a-z0-9]+", "-", canonical_name.lower()).strip("-")[:32] or "project"
    return readable + "-" + digest(canonical_name.encode("utf-8"))


def resolve_project(root: Path, project: str) -> tuple[Path, dict[str, Any]]:
    root = checked_path(root)
    private_directory(root, create=False)
    config = read_json(root / "library.json")
    memory_root = config.get("memory_root")
    if not isinstance(memory_root, str) or not Path(memory_root).is_absolute():
        raise LibraryError("library.json must contain an absolute memory_root.")
    if not project or project in {".", "..", "_template"} or any(c in project for c in "/\\\x00"):
        raise LibraryError("--project must exactly name an existing canonical project.")
    memory = checked_path(Path(memory_root))
    manifest_path = memory / "projects" / project / "project-manifest.json"
    manifest = read_json(manifest_path)
    if manifest.get("schema_version") != "2.0" or manifest.get("canonical_name") != project:
        raise LibraryError("Project manifest identity or schema does not match --project.")
    return root / "projects" / project_slug(project), manifest


class Redactor:
    """Narrow credential signatures; ordinary words such as 'token' do not match."""

    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()
        self.inline_media_preserved = 0

    def text(self, value: str) -> str:
        if full_media_uri(value):
            self.inline_media_preserved += 1
            return value
        for kind, pattern in REDACTION_PATTERNS:
            value, count = pattern.subn(f"[REDACTED:{kind}]", value)
            self.counts[kind] += count
        return value

    def value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.value(item) for item in value]
        if isinstance(value, dict):
            result = {}
            media = binary_media_object(value)
            for key, item in value.items():
                if not isinstance(key, str):
                    raise LibraryError("Structured content keys must be strings.")
                replacement = self.text(key)
                if replacement in result:
                    raise LibraryError("Redaction would collapse structured content keys; import was not published.")
                if media and key in MEDIA_PAYLOAD_KEYS and isinstance(item, str) and binary_payload(item):
                    # Credential regexes inside encoded media could corrupt the
                    # original bytes. Metadata and all ordinary text still pass
                    # through the normal high-confidence redaction rules.
                    result[replacement] = item
                    self.inline_media_preserved += 1
                else:
                    result[replacement] = self.value(item)
            return result
        if value is None or isinstance(value, (bool, int, float)):
            return value
        raise LibraryError("Adapter returned non-JSON content.")


def strip_local_paths(value: Any) -> Any:
    """Local provenance location is not part of semantic revision identity."""
    if isinstance(value, dict):
        return {key: strip_local_paths(item) for key, item in value.items()
                if key not in LOCAL_PATH_KEYS}
    if isinstance(value, list):
        return [strip_local_paths(item) for item in value]
    return value


def revision_payload(manifest: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_records = []
    for record in records:
        normalized = dict(record)
        # Content may legitimately contain a path; only provenance is normalized.
        normalized["source_refs"] = strip_local_paths(normalized.get("source_refs", []))
        normalized_records.append(normalized)
    value = {
        "schema_version": manifest.get("schema_version", "1.0"),
        "provider": manifest["provider"],
        "conversation_id": manifest["conversation_id"],
        "title": manifest.get("title"),
        "records": normalized_records,
        "completeness": strip_local_paths(manifest["completeness"]),
        "redaction": manifest["redaction"],
        "excluded_records": manifest["excluded_records"],
    }
    if manifest.get("schema_version", "1.0") == "1.0":
        value["project"] = manifest["project"]
    else:
        for key in ("conversation_metadata", "exported_at", "source_declared_completeness", "source_artifact"):
            value[key] = manifest.get(key)
    return value


def prepare_payload(payload: dict[str, Any], project: str | None, title: str | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(payload, dict):
        raise LibraryError("Adapter must return an object.")
    provider, conversation_id = payload.get("provider"), payload.get("conversation_id")
    if not isinstance(provider, str) or not PROVIDER.fullmatch(provider):
        raise LibraryError("Adapter provider identifier is invalid.")
    if not isinstance(conversation_id, str) or not conversation_id.strip():
        raise LibraryError("Adapter conversation_id is missing.")
    raw_records = payload.get("records")
    if not isinstance(raw_records, list):
        raise LibraryError("Adapter records must be a list.")
    if not isinstance(payload.get("source", {}), dict) or not isinstance(payload.get("completeness", {}), dict):
        raise LibraryError("Adapter provenance and completeness must be objects.")
    chosen_title = title if title is not None else payload.get("title")
    if chosen_title is not None and not isinstance(chosen_title, str):
        raise LibraryError("Conversation title must be text.")
    records, excluded = [], Counter()
    ids: set[str] = set()
    for raw in raw_records:
        if not isinstance(raw, dict):
            raise LibraryError("Each adapter record must be an object.")
        role = raw.get("role")
        channel = raw.get("channel")
        if not isinstance(role, str) or (channel is not None and not isinstance(channel, str)):
            raise LibraryError("Record role or channel is invalid.")
        role = role.strip().lower()
        normalized_channel = channel.strip().lower() if channel else None
        if role in FORBIDDEN or normalized_channel in FORBIDDEN:
            excluded["forbidden-role-or-channel"] += 1
            continue
        if role not in VISIBLE_ROLES:
            raise LibraryError("Adapter returned an unsupported visible role.")
        record_id = raw.get("id")
        if not isinstance(record_id, str) or not record_id or record_id in ids:
            raise LibraryError("Visible record IDs must be nonempty and unique.")
        ids.add(record_id)
        content = raw.get("content")
        if not isinstance(content, (str, dict, list)):
            raise LibraryError("Visible content must be text or structured JSON.")
        refs = raw.get("source_refs", [])
        if not isinstance(refs, list):
            raise LibraryError("source_refs must be a list.")
        timestamp = raw.get("timestamp")
        if timestamp is not None and not isinstance(timestamp, (str, int, float)):
            raise LibraryError("Record timestamp is invalid.")
        records.append({**raw, "id": record_id, "role": role, "channel": channel,
                        "timestamp": timestamp, "content": content, "source_refs": refs})
    if not records:
        raise LibraryError("No permitted visible records were found; no revision was published.")
    redactor = Redactor()
    safe = redactor.value({"provider": provider, "conversation_id": conversation_id,
                           "title": chosen_title, "records": records,
                           "source": payload.get("source", {}),
                           "completeness": payload.get("completeness", {}),
                           "conversation_metadata": payload.get("conversation_metadata", {}),
                           "exported_at": payload.get("exported_at"),
                           "source_declared_completeness": payload.get("source_declared_completeness", {}),
                           "source_artifact": payload.get("source_artifact")})
    records = safe.pop("records")
    artifact = safe.pop("source_artifact")
    artifact_description = None
    if artifact is not None:
        if not isinstance(artifact, dict) or not isinstance(artifact.get("format"), str) or "content" not in artifact:
            raise LibraryError("Source artifact needs a format and content.")
        data = canonical_bytes(artifact) + b"\n"
        artifact_description = {"filename": "source-artifact.json", "format": artifact["format"],
                                "sha256": digest(data), "bytes": len(data),
                                "scope": "permitted-visible-source; explicit redactions may differ from original",
                                "raw_runtime_log": False}
    if len({record["id"] for record in records}) != len(records):
        raise LibraryError("Redaction would collapse visible record IDs; import was not published.")
    completeness = {
        "source_assessment": safe.pop("completeness"),
        "storage_scope": "visible-records-inline-media-and-external-attachment-references",
        "inline_media_preserved": redactor.inline_media_preserved,
        "external_attachments_copied": False,
        "overall_complete": False,
        "limitations": ["Inline media already present in source records is preserved in messages.jsonl; external attachments are not copied or downloaded, and whole-conversation completeness is not established."],
    }
    redaction = {"policy_version": "2", "count": sum(redactor.counts.values()),
                 "by_type": {key: value for key, value in sorted(redactor.counts.items()) if value},
                 "scope": "visible-records-and-provenance-metadata",
                 "text_fidelity": "Visible text is preserved except explicitly marked redactions; generated framing is not source text.",
                 "limitations": "High-confidence text signatures only; recognized binary media payloads are preserved without text scanning. This is not proof that all sensitive material is absent."}
    manifest = {
        "schema_version": SCHEMA_VERSION, "project": None,
        "project_slug": None, **safe, "source_artifact": artifact_description,
        "raw_preservation": "visible-source-artifact" if artifact_description else "external-reference-only",
        "conversation_key": digest((provider + "\x00" + conversation_id).encode("utf-8")),
        "captured_at_iso": datetime.now(timezone.utc).isoformat(),
        "record_count": len(records), "completeness": completeness,
        "redaction": redaction, "redaction_count": redaction["count"],
        "excluded_records": dict(excluded),
        "snapshot_cutoff": {"last_record_id": records[-1]["id"],
                            "last_record_timestamp": records[-1]["timestamp"],
                            "scope": "last-stored-visible-record; not proof of conversation end"},
    }
    manifest["revision"] = digest(canonical_bytes(revision_payload(manifest, records)))
    return manifest, records


def transcript_bytes(manifest: dict[str, Any], records: list[dict[str, Any]]) -> bytes:
    has_preserved_inline_media = "inline_media_preserved" in manifest.get("completeness", {})
    description = (
        "Generated view of messages.jsonl. Inline media is preserved there; external attachments are not copied.\n"
        if has_preserved_inline_media else
        "Generated view of messages.jsonl. Original attachments are not copied.\n")
    chunks = ["# Visible conversation snapshot\n",
              description,
              f"Project: {manifest['project']}\nProvider: {manifest['provider']}\n",
              f"Conversation key: {manifest['conversation_key']}\nRevision: {manifest['revision']}\n",
              f"Explicit redactions: {manifest['redaction_count']}\n"]
    if manifest.get("schema_version") == "2.0":
        chunks.append("Source record order is shown. Retained branch and edit relationships do not imply one continuous dialogue. Project associations live in the separate link catalog.\n")
    for index, record in enumerate(records, 1):
        chunks.append(f"\n## {index} · {record['role']}\n")
        metadata = {"source_id": record["id"], "timestamp": record["timestamp"],
                    "channel": record["channel"], "source_refs": record["source_refs"]}
        if manifest.get("schema_version") == "2.0":
            metadata.update({key: value for key, value in record.items()
                             if key not in {"id", "role", "content", "timestamp", "channel", "source_refs"}})
        chunks.append(json.dumps(metadata, ensure_ascii=False, sort_keys=True) + "\n\n")
        content = (media_display_projection(record["content"])
                   if has_preserved_inline_media else record["content"])
        if isinstance(content, str):
            chunks.append(content)
        else:
            rendered = json.dumps(content, ensure_ascii=False, sort_keys=True,
                                  indent=2, allow_nan=False)
            fence = "`" * max(3, max((len(x) for x in re.findall(r"`+", rendered)), default=0) + 1)
            chunks.append(f"{fence}json\n{rendered}\n{fence}")
        chunks.append("\n")
    return "\n".join(chunks).encode("utf-8")


def manifest_digest(manifest: dict[str, Any]) -> str:
    return digest(canonical_bytes({key: value for key, value in manifest.items() if key != "manifest_sha256"}))


def metadata_view(manifest: dict[str, Any], path: Path) -> dict[str, Any]:
    return {"project": manifest["project"], "provider": manifest["provider"],
            "conversation_id": manifest["conversation_id"], "conversation_key": manifest["conversation_key"],
            "title": manifest.get("title"), "revision": manifest["revision"],
            "captured_at_iso": manifest["captured_at_iso"], "record_count": manifest["record_count"],
            "redaction_count": manifest["redaction_count"], "completeness": manifest["completeness"],
            "path": str(path)}


def load_manifest(path: Path, project: str | None) -> dict[str, Any]:
    check_private_file(path)
    manifest = read_json(path)
    required = ("provider", "conversation_id", "conversation_key", "revision", "captured_at_iso",
                "record_count", "redaction", "redaction_count", "completeness", "files", "excluded_records")
    legacy = manifest.get("schema_version") == "1.0"
    if (manifest.get("schema_version") not in {"1.0", "2.0"}
            or (legacy and project is not None and manifest.get("project") != project)
            or any(key not in manifest for key in required)):
        raise LibraryError("Stored revision metadata identity or schema is invalid.")
    if legacy and manifest.get("project_slug") != project_slug(manifest["project"]):
        raise LibraryError("Stored project slug does not match canonical identity.")
    if not legacy and (manifest.get("project") is not None or manifest.get("project_slug") is not None):
        raise LibraryError("Source-centric revisions must not embed a project assignment.")
    if not HEX64.fullmatch(str(manifest["revision"])) or not HEX64.fullmatch(str(manifest["conversation_key"])):
        raise LibraryError("Stored conversation or revision hash is invalid.")
    if path.parent.name != manifest["revision"] or path.parent.parent.parent.name != manifest["conversation_key"]:
        raise LibraryError("Stored revision metadata does not match its directory.")
    if manifest.get("manifest_sha256") != manifest_digest(manifest):
        raise LibraryError("Manifest hash verification failed.")
    if not isinstance(manifest["record_count"], int) or manifest["record_count"] < 1:
        raise LibraryError("Stored record count is invalid.")
    try:
        when = datetime.fromisoformat(manifest["captured_at_iso"])
    except (TypeError, ValueError):
        raise LibraryError("Stored capture timestamp is invalid.") from None
    if when.tzinfo is None:
        raise LibraryError("Stored capture timestamp must include a timezone.")
    return manifest


def read_records(path: Path) -> list[dict[str, Any]]:
    records = []
    try:
        with regular_file(path).open(encoding="utf-8") as stream:
            for line in stream:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise LibraryError("Stored message records must be JSON objects.")
                records.append(record)
    except (UnicodeError, json.JSONDecodeError):
        raise LibraryError("Stored messages are not valid UTF-8 JSONL.") from None
    return records


def validate_revision(path: Path, project: str | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    private_directory(path, create=False)
    manifest = load_manifest(path / "manifest.json", project)
    names = {"messages.jsonl", "transcript.md"}
    if manifest.get("source_artifact"):
        names.add("source-artifact.json")
    if set(manifest["files"]) != names:
        raise LibraryError("Stored file inventory is invalid.")
    if {child.name for child in path.iterdir()} != names | {"manifest.json"}:
        raise LibraryError("Revision contains unlisted or missing files.")
    for filename, expected in manifest["files"].items():
        file_path = path / filename
        check_private_file(file_path)
        data = file_path.read_bytes()
        if not isinstance(expected, dict) or expected.get("sha256") != digest(data) or expected.get("bytes") != len(data):
            raise LibraryError("Stored file hash or length verification failed.")
    records = read_records(path / "messages.jsonl")
    if manifest.get("source_artifact"):
        item = manifest["source_artifact"]
        if item.get("filename") != "source-artifact.json" or any(item.get(key) != manifest["files"]["source-artifact.json"][key] for key in ("sha256", "bytes")):
            raise LibraryError("Source artifact descriptor does not match its stored file.")
    if len(records) != manifest["record_count"]:
        raise LibraryError("Stored message count does not match the manifest.")
    ids = set()
    for record in records:
        if (not isinstance(record.get("id"), str) or record["id"] in ids
                or record.get("role") not in VISIBLE_ROLES
                or str(record.get("channel", "")).strip().lower() in FORBIDDEN
                or not isinstance(record.get("content"), (str, dict, list))
                or not isinstance(record.get("source_refs"), list)):
            raise LibraryError("Stored visible record schema or role is invalid.")
        ids.add(record["id"])
    if digest(canonical_bytes(revision_payload(manifest, records))) != manifest["revision"]:
        raise LibraryError("Semantic revision hash verification failed.")
    if transcript_bytes(manifest, records) != (path / "transcript.md").read_bytes():
        raise LibraryError("Transcript does not match canonical message records.")
    return manifest, records


def write_private(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        # fdopen owns the descriptor after creation.
        raise


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def publish(root: Path, project: str | None, payload: dict[str, Any], title: str | None = None) -> dict[str, Any]:
    import conversation_catalog as catalog
    catalog.validate_root(root)
    if project is not None:
        resolve_project(root, project)
    manifest, records = prepare_payload(payload, project, title)
    revisions = catalog.ensure_path(root, "conversations", manifest["conversation_key"], "revisions")
    destination = revisions / manifest["revision"]

    def existing_result() -> dict[str, Any]:
        existing, existing_records = validate_revision(destination, project)
        if revision_payload(existing, existing_records) != revision_payload(manifest, records):
            raise LibraryError("Revision hash collision; existing data was not changed.")
        if project is not None:
            catalog.link_project(root, project, manifest["conversation_key"])
        return {"status": "deduplicated", **metadata_view(existing, destination),
                "assignment": project or "pending"}

    if destination.exists() or destination.is_symlink():
        return existing_result()
    messages = b"".join(canonical_bytes(record) + b"\n" for record in records)
    transcript = transcript_bytes(manifest, records)
    artifacts = {"messages.jsonl": messages, "transcript.md": transcript}
    if manifest.get("source_artifact"):
        artifacts["source-artifact.json"] = canonical_bytes(Redactor().value(payload["source_artifact"])) + b"\n"
    manifest["files"] = {name: {"sha256": digest(data), "bytes": len(data)}
                         for name, data in artifacts.items()}
    manifest["manifest_sha256"] = manifest_digest(manifest)
    staging = Path(tempfile.mkdtemp(prefix=".incoming-", dir=revisions))
    os.chmod(staging, 0o700)
    published = False
    try:
        for name, data in artifacts.items():
            write_private(staging / name, data)
        write_private(staging / "manifest.json", json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                                                           indent=2, allow_nan=False).encode("utf-8") + b"\n")
        # Check bytes before publication, without relying only on successful writes.
        for filename, data in artifacts.items():
            if (staging / filename).read_bytes() != data:
                raise LibraryError("Staged artifact readback failed.")
        if read_json(staging / "manifest.json") != manifest:
            raise LibraryError("Staged manifest readback failed.")
        # Canonical JSONL sorts object keys. Rebuild from the bytes actually
        # stored so structured-content rendering cannot fail only after publish.
        stored_records = read_records(staging / "messages.jsonl")
        if transcript_bytes(manifest, stored_records) != (staging / "transcript.md").read_bytes():
            raise LibraryError("Staged transcript does not match canonical message records.")
        fsync_directory(staging)
        if destination.exists() or destination.is_symlink():
            return existing_result()
        try:
            os.rename(staging, destination)
            published = True
        except OSError:
            if destination.exists() or destination.is_symlink():
                return existing_result()
            raise
        fsync_directory(revisions)
        verified, _ = validate_revision(destination, project)
        if project is not None:
            catalog.link_project(root, project, manifest["conversation_key"])
        return {"status": "imported", **metadata_view(verified, destination),
                "assignment": project or "pending"}
    finally:
        if not published and staging.exists():
            # Only this private staging directory and these three owned files.
            checked_path(staging)
            for filename in (*artifacts, "manifest.json"):
                child = staging / filename
                if child.exists():
                    regular_file(child).unlink()
            staging.rmdir()


def _legacy_revisions(root: Path, project: str) -> list[tuple[Path, dict[str, Any]]]:
    project_path, _ = resolve_project(root, project)
    if not project_path.exists():
        checked_path(project_path, must_exist=False)
        return []
    private_directory(root / "projects", create=False)
    private_directory(project_path, create=False)
    result = []
    for conversation in sorted(project_path.iterdir()):
        private_directory(conversation, create=False)
        if not HEX64.fullmatch(conversation.name):
            raise LibraryError("Unexpected directory in project conversation catalog.")
        revisions = private_directory(conversation / "revisions", create=False)
        for revision in sorted(revisions.iterdir()):
            private_directory(revision, create=False)
            if revision.name.startswith(".incoming-"):
                continue
            if not HEX64.fullmatch(revision.name):
                raise LibraryError("Unexpected revision directory.")
            result.append((revision, load_manifest(revision / "manifest.json", project)))
    return sorted(result, key=lambda pair: (pair[1]["captured_at_iso"], pair[1]["conversation_key"], pair[1]["revision"]))


def enumerate_revisions(root: Path, project: str | None = None) -> list[tuple[Path, dict[str, Any]]]:
    import conversation_catalog as catalog
    result = _legacy_revisions(root, project) if project is not None else []
    result.extend(catalog.enumerate_catalog(root, project))
    unique = {str(path): (path, manifest) for path, manifest in result}
    return sorted(unique.values(), key=lambda pair: (pair[1]["captured_at_iso"], pair[1]["conversation_key"], pair[1]["revision"]))


def load_adapters() -> Any:
    path = regular_file(Path(__file__).absolute().parent / "adapters.py")
    spec = importlib.util.spec_from_file_location("context_library_adapters", path)
    if spec is None or spec.loader is None:
        raise LibraryError("The transcript adapters are unavailable.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def parse_source(args: argparse.Namespace) -> dict[str, Any]:
    # Project resolution is intentionally performed by the caller before this.
    adapter = load_adapters()
    try:
        if args.command == "import-codex":
            return adapter.parse_codex(Path(args.source))
        if args.command == "import-read-thread":
            return adapter.parse_read_thread(Path(args.source))
        source = Path(args.source)
        if source.suffix.lower() not in {".md", ".txt", ".html", ".htm", ".json"}:
            raise LibraryError("import-transcript accepts one UTF-8 text conversation or explicit ai-context-transcript/1.0 JSON, not a native export archive.")
        return adapter.parse_transcript(source, args.provider, args.conversation_id, args.title)
    except LibraryError:
        raise
    except Exception as exc:
        raise LibraryError(f"Source adapter failed ({type(exc).__name__}); source contents were not printed.") from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True,
                        help="Explicit private library root containing library.json.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("import-codex", "import-transcript", "import-read-thread", "list", "verify", "read", "register-legacy", "link", "verify-source"):
        command = commands.add_parser(name)
        command.add_argument("--project", required=name == "link", help="Exact canonical project; omitted imports are saved pending assignment.")
        if name == "list":
            command.add_argument("--unassigned", action="store_true", help="List only conversations with no project links.")
        if name.startswith("import-"):
            command.add_argument("--source", required=True)
            command.add_argument("--title", required=name == "import-transcript")
        if name == "import-transcript":
            command.add_argument("--provider", required=True)
            command.add_argument("--conversation-id", required=True)
        if name in {"read", "link", "verify-source"}:
            command.add_argument("--conversation", required=True, help="Exact stored conversation ID or full conversation_key.")
        if name in {"read", "verify-source"}:
            command.add_argument("--revision", help="Exact revision hash; otherwise select by verified content coverage, failing on ambiguity.")
            command.add_argument("--start", type=int, default=1, help="First visible record, 1-based inclusive.")
            command.add_argument("--end", type=int, help="Last visible record, 1-based inclusive.")
            command.add_argument("--include-media", action="store_true",
                                 help="Return original stored inline media payloads; default returns display markers.")
    for name in ("review-create", "review-fetch", "review-ack", "review-report"):
        command = commands.add_parser(name)
        if name == "review-create":
            command.add_argument("--project", required=True)
        else:
            command.set_defaults(project=None)
        if name == "review-create":
            command.add_argument("--mode", choices=("full", "topic"), required=True)
            command.add_argument("--conversation")
            command.add_argument("--revision")
            command.add_argument("--topic")
            command.add_argument("--versions", type=Path, help="Path to a JSON file mapping conversation_key to explicit revision.")
        else:
            command.add_argument("--review", required=True)
        if name in {"review-fetch", "review-ack"}:
            command.add_argument("--conversation", required=True)
            command.add_argument("--start", type=int, required=True)
            command.add_argument("--end", type=int, required=True)
        if name == "review-fetch":
            command.add_argument("--include-media", action="store_true")
        if name == "review-ack":
            command.add_argument("--note", help="Caller attestation after actually reading the retrieved text.")
    return parser


def run(args: argparse.Namespace) -> Any:
    import conversation_catalog as catalog
    root = checked_path(args.root)
    catalog.validate_root(root)
    if args.project is not None:
        resolve_project(root, args.project)
    if args.command.startswith("review-"):
        import reading_reviews as reviews
        if args.command == "review-create":
            return reviews.create_review(root, args.project, args.mode, args.conversation,
                                         args.revision, args.topic,
                                         read_json(args.versions) if args.versions else None)
        if args.command == "review-fetch":
            return reviews.fetch(root, args.review, args.conversation, args.start, args.end, args.include_media)
        if args.command == "review-ack":
            return reviews.acknowledge(root, args.review, args.conversation, args.start, args.end, args.note)
        return reviews.report(root, args.review)
    if args.command == "register-legacy":
        return catalog.register_legacy(root, args.project)
    if args.command.startswith("import-"):
        return publish(root, args.project, parse_source(args), args.title)
    revisions = enumerate_revisions(root, args.project)
    if args.command == "list":
        if args.unassigned:
            if args.project:
                raise LibraryError("Use either --project or --unassigned.")
            revisions = catalog.enumerate_catalog(root, unassigned=True)
        return {"project": args.project, "revision_count": len(revisions),
                "revisions": [metadata_view(manifest, path) for path, manifest in revisions]}
    if args.command == "verify":
        for path, _ in revisions:
            validate_revision(path, args.project)
        return {"project": args.project, "status": "verified", "revision_count": len(revisions)}
    if args.command == "link":
        revisions = enumerate_revisions(root, None)
    matches = [(path, manifest) for path, manifest in revisions
               if args.conversation in {manifest["conversation_id"], manifest["conversation_key"]}]
    if len({manifest["conversation_key"] for _, manifest in matches}) > 1:
        raise LibraryError("Conversation ID occurs under multiple providers; use its full conversation_key.")
    if args.command == "link":
        if not matches:
            raise LibraryError("No source conversation exists for linking.")
        return catalog.link_project(root, args.project, matches[0][1]["conversation_key"])
    if args.revision:
        if not HEX64.fullmatch(args.revision):
            raise LibraryError("--revision must be a complete SHA-256 hash.")
        matches = [(path, manifest) for path, manifest in matches if manifest["revision"] == args.revision]
    if not matches:
        raise LibraryError("No matching conversation revision was found in this project.")
    from revision_selection import select_revision, SelectionError
    try:
        path, _ = select_revision(matches)
    except SelectionError as exc:
        raise LibraryError(str(exc)) from None
    if args.command == "verify-source":
        import source_verification
        return source_verification.verify_source(path, args.project)
    manifest, records = validate_revision(path, args.project)
    end = args.end if args.end is not None else len(records)
    if args.start < 1 or end < args.start or end > len(records):
        raise LibraryError("Read range must be within 1..record_count, inclusive.")
    include_media = getattr(args, "include_media", False)
    return {"project": args.project, "conversation_key": manifest["conversation_key"],
            "revision": manifest["revision"], "start": args.start, "end": end,
            "media_view": "original-inline-payloads" if include_media else "display-projection",
            "records": [{"ordinal": index, "source_id": record["id"], **record,
                         "content": record["content"] if include_media else media_display_projection(record["content"])}
                        for index, record in enumerate(records, 1) if args.start <= index <= end]}


def main(argv: list[str] | None = None) -> int:
    # Prevent cache files beside either script during ordinary CLI operation.
    sys.dont_write_bytecode = True
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
        return 0
    except LibraryError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
    except Exception as exc:
        # JSON parse errors and adapter exceptions can include source fragments.
        print(json.dumps({"status": "error", "error": f"Operation failed ({type(exc).__name__}); no source content was printed."}), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
