"""Synthetic fidelity fixtures; no real conversation data is read."""
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import adapters


class AdapterFidelityTests(unittest.TestCase):
    def snapshot(self, text):
        data = text.encode("utf-8")
        return data, {"path": "/virtual/fixture.json", "snapshot_sha256": hashlib.sha256(data).hexdigest(),
                      "snapshot_bytes": len(data), "parsed_bytes": len(data), "trailing_bytes_ignored": 0,
                      "first_timestamp": None, "last_timestamp": None, "mtime_ns": 0,
                      "adapter_version": adapters.ADAPTER_VERSION, "prefix_stability_checked": True}

    def document(self, version="2.0"):
        return {"schema_version": "ai-context-transcript/" + version, "provider": "synthetic",
                "conversation_id": "fixture", "title": "Fixture", "records": [
                    {"id": "m1", "role": "user", "content": "Synthetic visible content"}]}

    def parse_document(self, document):
        text = json.dumps(document, ensure_ascii=False, indent=2)
        with patch.object(adapters, "_snapshot", return_value=self.snapshot(text)):
            return adapters.parse_transcript("/virtual/fixture.json", "synthetic", "fixture", "Fixture")

    def test_both_schemas_preserve_relationships_and_unknown_extensions(self):
        for version in ("1.0", "2.0"):
            with self.subTest(version=version):
                doc = self.document(version)
                extra = {"parent_id": "p1", "children": ["c1"], "branch_id": "b1", "edit_of": "old1",
                         "regenerated_from": None, "relationships": {"parent": "p1", "custom": {"evidence": 1}},
                         "custom_extension": {"analysis": "public report", "role": "system"}}
                doc["records"][0].update(extra)
                parsed = self.parse_document(doc)
                for key, value in extra.items():
                    self.assertEqual(parsed["records"][0][key], value)

    def test_conversation_extensions_and_declared_gaps_are_not_discarded(self):
        doc = self.document()
        doc.update(branch_selection={"leaf_id": "m1"}, export_origin={"application": "synthetic"},
                   completeness={"complete": False, "text": "partial", "structure": "branched",
                                 "attachments": "missing", "notes": ["synthetic caveat"]})
        parsed = self.parse_document(doc)
        self.assertEqual(parsed["conversation_metadata"]["branch_selection"], doc["branch_selection"])
        self.assertEqual(parsed["source_declared_completeness"], doc["completeness"])
        self.assertFalse(parsed["completeness"]["complete"])

    def test_message_source_and_export_times_are_separate(self):
        doc = self.document()
        doc["exported_at"] = "2026-01-03T00:00:00Z"
        doc["records"][0].update(timestamp="2026-01-01T00:00:00Z", updated_at="2026-01-02T00:00:00Z",
                                  source_recorded_at="2026-01-01T00:00:01Z")
        parsed = self.parse_document(doc)
        self.assertEqual(parsed["records"][0]["message_created_at"], doc["records"][0]["timestamp"])
        self.assertEqual(parsed["records"][0]["source_recorded_at"], doc["records"][0]["source_recorded_at"])
        self.assertEqual(parsed["exported_at"], doc["exported_at"])
        unknown = self.parse_document(self.document())
        self.assertIsNone(unknown["records"][0]["message_created_at"])
        self.assertIsNone(unknown["exported_at"])

    def test_malformed_supported_relationship_fields_fail(self):
        for key, value in (("parent_id", ["p1"]), ("children", "c1"), ("branch_id", 3),
                           ("relationships", {"children_ids": [3]})):
            with self.subTest(key=key):
                doc = self.document()
                doc["records"][0][key] = value
                with self.assertRaises(adapters.AdapterError):
                    self.parse_document(doc)

    def public_records(self, payload):
        return [{key: value for key, value in record.items() if key != "source_refs"} for record in payload["records"]]

    def test_standard_source_artifact_preserves_utf8_and_replays(self):
        doc = self.document()
        doc["records"][0].update(parent_id="p1", branch_id="b1", created_at=123)
        raw = json.dumps(doc, ensure_ascii=False, indent=2)
        parsed = self.parse_document(doc)
        self.assertEqual(parsed["source_artifact"]["content"], raw)
        replayed = adapters.replay_source_artifact(parsed["source_artifact"], "synthetic", "fixture", "Fixture")
        self.assertEqual(self.public_records(parsed), self.public_records(replayed))

    def test_plaintext_source_artifact_is_exact_and_self_contained(self):
        text = "Synthetic α transcript\n\nSecond paragraph.\n"
        with patch.object(adapters, "_snapshot", return_value=self.snapshot(text)):
            parsed = adapters.parse_transcript("/virtual/deleted-source.md", "synthetic", "fixture", "Fixture")
        self.assertEqual(parsed["source_artifact"]["content"], text)
        replayed = adapters.replay_source_artifact(parsed["source_artifact"], "synthetic", "fixture", "Fixture")
        self.assertEqual(self.public_records(parsed), self.public_records(replayed))

    def test_plaintext_derived_identity_survives_artifact_redaction(self):
        text = "Synthetic original marker"
        with patch.object(adapters, "_snapshot", return_value=self.snapshot(text)):
            parsed = adapters.parse_transcript("/virtual/deleted-source.txt", "synthetic", "fixture", "Fixture")
        artifact = {**parsed["source_artifact"], "content": "Synthetic [REDACTED] marker"}
        replayed = adapters.replay_source_artifact(artifact, "synthetic", "fixture", "Fixture")
        self.assertEqual(replayed["records"][0]["id"], parsed["records"][0]["id"])
        self.assertEqual(replayed["records"][0]["content"]["text"], artifact["content"])

    def test_codex_public_evidence_replays_without_engine_private_data(self):
        rows = [
            {"type": "session_meta", "payload": {"id": "fixture", "base_instructions": "PRIVATE_SYSTEM_MARKER"}},
            {"type": "response_item", "payload": {"type": "reasoning", "summary": "PRIVATE_REASONING_MARKER"}},
            {"type": "event_msg", "timestamp": "2026-01-01T00:00:01Z", "payload": {"type": "item_completed", "thread_id": "fixture", "item": {
                "type": "UserMessage", "id": "u1", "content": [{"type": "text", "text": "Synthetic question"}]}}},
            {"type": "response_item", "timestamp": "2026-01-01T00:00:02Z", "payload": {"type": "message", "id": "a1", "role": "assistant", "phase": "final_answer",
                "content": [{"type": "output_text", "text": "Synthetic answer"}],
                "internal_chat_message_metadata_passthrough": {"create_time": 123, "turn_id": "t1", "private_context": "PRIVATE_CONTEXT_MARKER"}}},
            {"type": "event_msg", "payload": {"type": "item_completed", "thread_id": "fixture", "item": {
                "type": "CommandExecution", "id": "tool1", "aggregated_output": "Synthetic tool result", "exit_code": 0}}},
        ]
        raw = "".join(json.dumps(row) + "\n" for row in rows)
        with patch.object(adapters, "_snapshot", return_value=self.snapshot(raw)):
            parsed = adapters.parse_codex("/virtual/deleted-rollout.jsonl")
        artifact_text = json.dumps(parsed["source_artifact"])
        for marker in ("PRIVATE_SYSTEM_MARKER", "PRIVATE_REASONING_MARKER", "PRIVATE_CONTEXT_MARKER"):
            self.assertNotIn(marker, artifact_text)
        replayed = adapters.replay_source_artifact(parsed["source_artifact"], "codex", "fixture", "Fixture")
        self.assertEqual(self.public_records(parsed), self.public_records(replayed))

    def api_page(self, turn_id, has_more):
        return {"schemaVersion": 1, "thread": {"id": "fixture", "kind": "chatgpt", "title": "Fixture", "preview": "PREVIEW_NOT_BODY"},
                "page": {"order": "newest_first", "hasMore": has_more, "nextCursor": "cursor" if has_more else None, "limit": 1},
                "turns": [{"id": turn_id, "status": "completed", "startedAt": 100, "completedAt": 200, "items": [
                    {"type": "userMessage", "id": turn_id + "-u", "content": [{"type": "text", "text": "Synthetic question " + turn_id}]},
                    {"type": "agentMessage", "id": turn_id + "-a", "text": "Synthetic response " + turn_id}]}], "attachments": []}

    def parse_bundle(self, bundle):
        raw = json.dumps(bundle, ensure_ascii=False, indent=2)
        with patch.object(adapters, "_snapshot", return_value=self.snapshot(raw)):
            return adapters.parse_read_thread("/virtual/fixture.json")

    def test_read_thread_public_pages_have_ids_times_attachments_and_no_preview_body(self):
        pages = [self.api_page("t2", True), self.api_page("t1", False)]
        attachment = {"type": "image", "url": "https://example.invalid/synthetic.png", "label": "Synthetic attachment"}
        pages[0]["attachments"] = [attachment]
        bundle = {"schema_version": "ai-context-read-thread/1.0", "pages": pages}
        parsed = self.parse_bundle(bundle)
        self.assertEqual([record["id"] for record in parsed["records"]], ["t1-u", "t1-a", "t2-u", "t2-a"])
        self.assertNotIn("PREVIEW_NOT_BODY", json.dumps(parsed["records"]))
        self.assertIsNone(parsed["records"][0]["message_created_at"])
        self.assertEqual(parsed["records"][0]["turn_started_at"], 100)
        self.assertEqual(parsed["conversation_metadata"]["page_attachments"][0]["attachments"], [attachment])
        self.assertTrue(parsed["completeness"]["pagination_exhausted"])
        self.assertFalse(parsed["completeness"]["complete"])
        replayed = adapters.replay_source_artifact(parsed["source_artifact"], "chatgpt", "fixture", "Fixture")
        self.assertEqual(self.public_records(parsed), self.public_records(replayed))

    def test_read_thread_incomplete_pages_are_partial(self):
        parsed = self.parse_bundle({"schema_version": "ai-context-read-thread/1.0", "pages": [self.api_page("t2", True)]})
        self.assertEqual(parsed["completeness"]["text_status"], "partial")
        self.assertFalse(parsed["completeness"]["pagination_exhausted"])

    def test_read_thread_preview_cannot_replace_missing_agent_body(self):
        page = self.api_page("t1", False)
        del page["turns"][0]["items"][1]["text"]
        with self.assertRaises(adapters.AdapterError):
            self.parse_bundle({"schema_version": "ai-context-read-thread/1.0", "pages": [page]})

    def test_read_thread_private_items_cannot_enter_source_artifact(self):
        page = self.api_page("t1", False)
        page["turns"][0]["items"].append({"type": "reasoning", "text": "PRIVATE_MARKER"})
        with self.assertRaises(adapters.AdapterError):
            self.parse_bundle({"schema_version": "ai-context-read-thread/1.0", "pages": [page]})


if __name__ == "__main__":
    unittest.main()
