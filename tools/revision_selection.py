"""Select a demonstrably non-regressing visible conversation snapshot.

The caller groups candidates by conversation and handles explicit --revision
filtering. Selection reads and validates stored revisions; it never reads the
original provider source, writes files, or treats import time as chronology.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import importlib
from pathlib import Path
import re
from typing import Any, NamedTuple, Sequence


class SelectionError(Exception):
    """No unambiguous, integrity-checked default revision can be selected."""


class _Media(NamedTuple):
    sha256: str
    length: int
    payload_present: bool


class _Candidate(NamedTuple):
    path: Path
    manifest: dict[str, Any]
    records: list[dict[str, Any]]


def _library() -> Any:
    # Lazy import permits context_library to import this module while preserving
    # one integrity implementation for legacy and conversation-scoped layouts.
    return importlib.import_module("context_library")


def _media_marker(value: dict[str, Any]) -> _Media | None:
    if not (value.get("omitted_inline_media") is True or value.get("inline_media_display_omitted") is True):
        return None
    checksum, length = value.get("sha256"), value.get("length")
    if (isinstance(checksum, str) and re.fullmatch(r"[0-9a-f]{64}", checksum)
            and isinstance(length, int) and not isinstance(length, bool) and length >= 0):
        return _Media(checksum, length, False)
    return None


def _media_payload(value: str) -> _Media:
    return _Media(hashlib.sha256(value.encode("utf-8")).hexdigest(), len(value), True)


def _content(value: Any, lib: Any) -> Any:
    """Compare stored media with its older digest without discarding fidelity."""
    if isinstance(value, str):
        return _media_payload(value) if lib.full_media_uri(value) else value
    if isinstance(value, list):
        return [_content(item, lib) for item in value]
    if isinstance(value, dict):
        marker = _media_marker(value)
        if marker is not None:
            return marker
        media = lib.binary_media_object(value)
        return {key: _media_payload(item)
                if media and key in lib.MEDIA_PAYLOAD_KEYS and isinstance(item, str) and lib.binary_payload(item)
                else _content(item, lib) for key, item in value.items()}
    return value


def _content_covers(more: Any, less: Any) -> bool:
    """Information containment, never substring matching or length ranking."""
    if isinstance(less, _Media):
        return (isinstance(more, _Media)
                and more.sha256 == less.sha256 and more.length == less.length
                and (more.payload_present or not less.payload_present))
    if isinstance(more, _Media):
        return False
    if isinstance(less, dict):
        return isinstance(more, dict) and all(
            key in more and _content_covers(more[key], value) for key, value in less.items())
    if isinstance(less, list):
        # Only the outer record stream permits inserted entries. An insertion
        # inside a command-argument or question-option list can change meaning,
        # so nested lists require the same positions and multiplicity.
        return (isinstance(more, list) and len(more) == len(less)
                and all(_content_covers(a, b) for a, b in zip(more, less)))
    # In JSON, False and 0 are different values despite Python equality.
    return type(more) is type(less) and more == less


def _record_covers(more: dict[str, Any], less: dict[str, Any]) -> bool:
    # New export formats may add parent_id, branch_id, edit_of, or unknown
    # public extensions. Their values are part of record identity/meaning;
    # equal visible prose must never erase a conflict in those fields.
    return _content_covers(
        {key: value for key, value in more.items() if key not in {"id", "source_refs"}},
        {key: value for key, value in less.items() if key not in {"id", "source_refs"}})


def _records_cover(more: list[dict[str, Any]], less: list[dict[str, Any]]) -> bool:
    positions = {record["id"]: index for index, record in enumerate(more)}
    cursor = 0
    for wanted in less:
        same_id = positions.get(wanted["id"])
        if same_id is not None:
            # A changed stable ID cannot be hidden by an equal message elsewhere.
            if same_id < cursor or not _record_covers(more[same_id], wanted):
                return False
            cursor = same_id + 1
            continue
        # Re-exports may assign different IDs; matching requires complete visible
        # contents and structural fields, in order, with one-to-one multiplicity.
        while cursor < len(more) and not _record_covers(more[cursor], wanted):
            cursor += 1
        if cursor == len(more):
            return False
        cursor += 1
    return True


def _size(source: dict[str, Any], key: str) -> int | None:
    value = source.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise SelectionError("Source size metadata is inconsistent; specify --revision explicitly.")
    return value


def _time(source: dict[str, Any], key: str) -> datetime | None:
    value = source.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise SelectionError("Source cutoff metadata is not comparable; specify --revision explicitly.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise SelectionError("Source cutoff metadata is not comparable; specify --revision explicitly.") from None
    if parsed.tzinfo is None:
        raise SelectionError("Source cutoff metadata lacks a timezone; specify --revision explicitly.")
    return parsed


def _source_allows(more: dict[str, Any], less: dict[str, Any]) -> bool:
    """Source metadata may contradict containment; it cannot establish it."""
    for field in ("conversation_metadata", "source_declared_completeness"):
        # Legacy absence means no captured extension fields, not a guessed
        # branch or completeness claim. Explicit nulls and conflicting values
        # remain distinct; filling a recorded null requires an explicit choice.
        if not _content_covers(more.get(field, {}), less.get(field, {})):
            return False
    a, b = more.get("source", {}), less.get("source", {})
    if not isinstance(a, dict) or not isinstance(b, dict):
        raise SelectionError("Source metadata is malformed; specify --revision explicitly.")
    a_end, b_end = _time(a, "last_timestamp"), _time(b, "last_timestamp")
    if a_end is not None and b_end is not None and a_end < b_end:
        return False
    a_start, b_start = _time(a, "first_timestamp"), _time(b, "first_timestamp")
    if a_start is not None and b_start is not None and a_start > b_start:
        return False
    a_size, b_size = _size(a, "snapshot_bytes"), _size(b, "snapshot_bytes")
    a_hash, b_hash = a.get("snapshot_sha256"), b.get("snapshot_sha256")
    valid_hashes = all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                       for value in (a_hash, b_hash))
    if valid_hashes and a_hash == b_hash and a_size is not None and b_size is not None and a_size != b_size:
        return False
    same_path = isinstance(a.get("path"), str) and bool(a["path"]) and a["path"] == b.get("path")
    if same_path:
        # File growth alone is insufficient: the caller also requires complete
        # visible-record containment. prefix_stability_checked only describes one
        # capture and is deliberately not treated as cross-snapshot prefix proof.
        if a_size is not None and b_size is not None:
            if a_size < b_size:
                return False
            if a_size == b_size and valid_hashes and a_hash != b_hash:
                return False
        a_parsed, b_parsed = _size(a, "parsed_bytes"), _size(b, "parsed_bytes")
        if a_parsed is not None and b_parsed is not None and a_parsed < b_parsed:
            return False
    # exported_at and captured_at are not conversation content boundaries.
    return True


def select_revision(candidates: Sequence[tuple[Path, dict[str, Any]]]) -> tuple[Path, dict[str, Any]]:
    """Return a revision proven to cover every candidate, or raise SelectionError.

    Identical re-exports choose a deterministic representative by revision/path,
    not import time. Branches, edits, reordered stable IDs, or contradictory
    source cutoffs require explicit --revision. All integrity checks remain in
    force when the caller supplies just one explicitly selected candidate.
    """
    if not candidates:
        raise SelectionError("No conversation revisions are available.")
    lib = _library()
    verified = []
    identity = None
    seen = set()
    for path, supplied in candidates:
        path = Path(path)
        try:
            manifest, records = lib.validate_revision(path, supplied.get("project"))
        except Exception as exc:
            raise SelectionError("Revision integrity verification failed; source contents were not printed.") from exc
        for key in ("schema_version", "project", "provider", "conversation_id", "conversation_key", "revision"):
            if supplied.get(key) != manifest.get(key):
                raise SelectionError("Candidate metadata changed during revision selection.")
        current_identity = (manifest.get("provider"), manifest.get("conversation_id"), manifest.get("conversation_key"))
        if identity is None:
            identity = current_identity
        elif current_identity != identity:
            raise SelectionError("Revision selection requires candidates from one conversation.")
        location = (str(path), manifest.get("revision"))
        if location in seen:
            continue
        seen.add(location)
        if len({record.get("id") for record in records}) != len(records):
            raise SelectionError("Stored record identity is ambiguous.")
        verified.append(_Candidate(path, manifest, records))
    if len(verified) == 1:
        return verified[0].path, verified[0].manifest
    normalized = [
        _Candidate(candidate.path, candidate.manifest,
                   [{key: value if key in {"id", "source_refs"} else _content(value, lib)
                     for key, value in record.items()} for record in candidate.records])
        for candidate in verified
    ]
    winners = []
    for index, candidate in enumerate(normalized):
        if all(index == other_index or (
                _source_allows(candidate.manifest, other.manifest)
                and _records_cover(candidate.records, other.records))
               for other_index, other in enumerate(normalized)):
            winners.append(candidate)
    if not winners:
        raise SelectionError("Conversation revisions contain edits, branches, or conflicting source cutoffs; specify --revision explicitly.")
    selected = min(winners, key=lambda candidate: (str(candidate.manifest.get("revision", "")), str(candidate.path)))
    return selected.path, selected.manifest
