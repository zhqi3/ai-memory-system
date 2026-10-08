"""Synthetic encoded-source and redaction-stable assembly regressions."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_context_library as fixtures
import adapters
import auto_sync
import context_library as lib
import source_verification


class RedactionReplayTests(unittest.TestCase):
    setUp = fixtures.ArchiveTests.setUp
    tearDown = fixtures.ArchiveTests.tearDown
    source = fixtures.ArchiveTests.source
    meta = fixtures.ArchiveTests.meta
    event = fixtures.ArchiveTests.event

    def document(self, content, version="2.0"):
        return {"schema_version": "ai-context-transcript/" + version, "provider": "example",
                "conversation_id": "chat-1", "title": "Synthetic",
                "records": [{"id": "m1", "role": "user", "content": content}]}

    def bundle(self, contents):
        return {"schema_version": "ai-context-read-thread/1.0", "pages": [{
            "schemaVersion": 1, "thread": {"kind": "chatgpt", "id": "chat-1", "title": "Synthetic"},
            "page": {"order": "oldest_first", "hasMore": False, "nextCursor": None},
            "turns": [{"id": "turn-1", "status": "completed", "items": [
                {"type": "agentMessage", "id": "m1", "text": content} for content in contents]}]}]}

    def parse(self, document, raw=None):
        path = self.base / "fixture.json"
        path.write_text(raw if raw is not None else json.dumps(document, ensure_ascii=False, indent=2))
        if document["schema_version"] == "ai-context-read-thread/1.0":
            return adapters.parse_read_thread(path)
        return adapters.parse_transcript(path, "example", "chat-1", "Synthetic")

    def publish_verified(self, payload):
        original = copy.deepcopy(payload)
        result = lib.publish(self.root, "alpha", payload)
        path = Path(result["path"])
        manifest, records = lib.validate_revision(path, "alpha")
        artifact = lib.read_json(path / "source-artifact.json")
        self.assertEqual(source_verification.verify_source(path, "alpha")["status"], "verified-source-replay")
        self.assertEqual(payload, original)
        return manifest, records, artifact

    def test_unicode_escaped_credentials_removed_from_all_json_formats(self):
        secret = "sk-" + "a" * 32
        for doc in (self.document(secret, "1.0"), self.document(secret), self.bundle([secret])):
            with self.subTest(format=doc["schema_version"]):
                raw = json.dumps(doc).replace("sk-", r"\u0073\u006b\u002d")
                payload = self.parse(doc, raw)
                _, records, artifact = self.publish_verified(payload)
                self.assertNotIn(secret, json.dumps(records))
                self.assertNotIn(secret, json.dumps(json.loads(artifact["content"])))
                self.assertIn("[REDACTED:openai-key]", artifact["content"])
                self.assertEqual((self.base / "fixture.json").read_text(), raw)

    def test_json_keys_metadata_and_escaped_pem_are_redacted(self):
        secret = "sk-" + "b" * 32
        pem = "-----BEGIN " + "PRIVATE KEY-----\nsynthetic\n-----END " + "PRIVATE KEY-----"
        for doc in (self.document({secret: pem}), self.bundle([pem])):
            doc["visible_extension"] = {secret: {"nested": [secret, pem]}}
            raw = json.dumps(doc).replace("sk-", r"\u0073k-")
            _, records, artifact = self.publish_verified(self.parse(doc, raw))
            decoded = json.dumps(json.loads(artifact["content"]))
            self.assertNotIn(secret, decoded)
            self.assertNotIn("BEGIN " + "PRIVATE KEY", decoded)
            self.assertIn("[REDACTED:private-key]", json.dumps(records))

    def test_only_changed_json_string_lexemes_are_replaced(self):
        secret = "sk-" + "c" * 32
        doc = self.document({"safe": "α", "credential": secret})
        raw = json.dumps(doc, ensure_ascii=True, indent="\t") + "\n"
        raw = raw.replace('"id":', '"\\u0069d" :').replace("sk-", r"\u0073k-")
        _, _, artifact = self.publish_verified(self.parse(doc, raw))
        encoded_secret = json.dumps(secret).replace("sk-", r"\u0073k-")
        self.assertEqual(artifact["content"], raw.replace(encoded_secret, '"[REDACTED:openai-key]"'))

    def test_json_media_survives_with_and_without_other_redactions(self):
        media = "aaaa" + "AKIA" + "A" * 16 + "aaaa"
        self.assertTrue(lib.valid_base64(media))
        for secret in ("ordinary caption", "sk-" + "d" * 32):
            for use_uri in (False, True):
                payload = "data:image/png;base64," + media if use_uri else media
                content = {"type": "image", "data": payload, "caption": secret}
                docs = [self.document(content)]
                bundle = self.bundle(["visible response"])
                bundle["pages"][0]["turns"][0]["items"].insert(0, {
                    "type": "userMessage", "id": "u1", "content": [content]})
                docs.append(bundle)
                for doc in docs:
                    with self.subTest(format=doc["schema_version"], uri=use_uri, redacted=secret != "ordinary caption"):
                        _, records, artifact = self.publish_verified(self.parse(doc))
                        decoded = json.loads(artifact["content"])
                        source_media = (decoded["records"][0]["content"] if "records" in decoded
                                        else decoded["pages"][0]["turns"][0]["items"][0]["content"][0])
                        stored_media = records[0]["content"] if "records" in decoded else records[0]["content"][0]
                        self.assertEqual(source_media["data"], payload)
                        self.assertEqual(stored_media["data"], payload)

    def test_redacted_json_key_collisions_fail_before_publication(self):
        a, b = "sk-" + "a" * 32, "sk-" + "b" * 32
        doc = self.document("ordinary")
        doc["extra"] = {a: 1, b: 2}
        payload = self.parse(doc)
        with self.assertRaises(lib.LibraryError):
            lib.publish(self.root, "alpha", payload)
        self.assertEqual(lib.enumerate_revisions(self.root, "alpha"), [])

    def test_invalid_serialized_source_fails_before_publication(self):
        for content in ('{"a": 1, "a": 2}', '{"a": NaN}', '[]', '{"incomplete":'):
            payload = self.parse(self.document("ordinary"))
            payload["source_artifact"]["content"] = content
            with self.subTest(content=content), self.assertRaises(lib.LibraryError):
                lib.publish(self.root, "alpha", payload)
        self.assertEqual(lib.enumerate_revisions(self.root, "alpha"), [])

    def codex(self, texts, mirror=None):
        rows = [self.meta()]
        rows += [self.event("AgentMessage", "m1", [{"type": "Text", "text": text}], "final_answer") for text in texts]
        if mirror is not None:
            rows.append({"type": "response_item", "payload": {"type": "message", "id": "m1",
                "role": "assistant", "phase": "final_answer", "content": [{"type": "output_text", "text": mirror}]}})
        return adapters.parse_codex(self.source(rows))

    def test_codex_redacted_edits_preserve_ids_duplicates_and_source_refs(self):
        a, b = "sk-" + "a" * 32, "sk-" + "b" * 32
        for texts in (("ordinary", a), (a, b), (a, b, b)):
            payload = self.codex(texts, mirror=b if texts[0] == a else a)
            _, records, _ = self.publish_verified(payload)
            self.assertEqual(len(records), 2)
            self.assertEqual([x["id"] for x in records], [x["id"] for x in payload["records"]])
            self.assertEqual([x["source_refs"] for x in records], [x["source_refs"] for x in payload["records"]])

    def test_codex_redacted_cross_chain_supplements_are_not_collapsed(self):
        payload = self.codex(["sk-" + "a" * 32], mirror="sk-" + "b" * 32)
        self.assertEqual(len(payload["records"][0]["content"]["supplemental_content"]), 1)
        _, records, _ = self.publish_verified(payload)
        self.assertEqual(len(records), 1)
        self.assertEqual(len(records[0]["content"]["supplemental_content"]), 1)

    def test_codex_redacted_tool_presentations_preserve_distinct_events(self):
        rows = [self.meta(), {"type": "response_item", "payload": {
            "type": "function_call_output", "call_id": "call-1", "output": "base"}}]
        for secret in ("sk-" + "a" * 32, "sk-" + "b" * 32):
            rows.append(self.event("CommandExecution", "call-1", []))
            rows[-1]["payload"]["item"].update(aggregated_output=secret, exit_code=0)
        payload = adapters.parse_codex(self.source(rows))
        _, records, _ = self.publish_verified(payload)
        self.assertEqual(len(records[0]["content"]["completed_events"]), 2)

    def test_codex_redacted_supplemental_metadata_keeps_original_contributions(self):
        first = self.event("AgentMessage", "m1", [{"type": "Text", "text": "same text"}], "final_answer")
        first["payload"]["item"]["memory_citation"] = "sk-" + "a" * 32
        second = {"type": "response_item", "payload": {"type": "message", "id": "m1", "role": "assistant",
                  "phase": "final_answer", "content": [{"type": "output_text", "text": "same text"}],
                  "memory_citation": "sk-" + "b" * 32, "attachments": [{"type": "image", "url": "https://example.invalid/a"}]}}
        payload = adapters.parse_codex(self.source([self.meta(), first, second]))
        _, records, _ = self.publish_verified(payload)
        self.assertEqual(len(records[0]["content"]["supplemental_metadata"]), 1)
        self.assertEqual(records[0]["content"]["attachments"], second["payload"]["attachments"])

    def test_read_thread_redacted_edits_preserve_ids_duplicates_and_metadata(self):
        a, b = "sk-" + "a" * 32, "sk-" + "b" * 32
        for contents in (("ordinary", a), (a, b), (a, b, b)):
            payload = self.parse(self.bundle(contents))
            _, records, _ = self.publish_verified(payload)
            self.assertEqual(len(records), 2)
            self.assertEqual([x["id"] for x in records], [x["id"] for x in payload["records"]])
            self.assertEqual([x["source_refs"] for x in records], [x["source_refs"] for x in payload["records"]])

    def test_replay_plan_rejects_missing_duplicate_unused_or_bad_operations(self):
        for payload in (self.codex(["ordinary", "edited"]), self.parse(self.bundle(["ordinary", "edited"]))):
            original = payload["source_artifact"]
            corruptions = []
            missing = copy.deepcopy(original); missing["replay_plan"]["steps"].pop(); corruptions.append(missing)
            repeated = copy.deepcopy(original); repeated["replay_plan"]["steps"].append(repeated["replay_plan"]["steps"][0]); corruptions.append(repeated)
            unused = copy.deepcopy(original); unused["replay_plan"]["steps"][0]["locator"][0] = 999; corruptions.append(unused)
            invalid = copy.deepcopy(original); invalid["replay_plan"]["steps"][0]["action"] = "execute"; corruptions.append(invalid)
            null = copy.deepcopy(original); null["replay_plan"] = None; corruptions.append(null)
            alias = copy.deepcopy(original); alias["replay_plan"]["steps"][1]["record_id"] = alias["replay_plan"]["steps"][0]["record_id"]; corruptions.append(alias)
            for artifact in corruptions:
                with self.assertRaises(adapters.AdapterError):
                    adapters.replay_source_artifact(artifact, payload["provider"], "chat-1", "Synthetic")

    def test_replay_plan_cannot_omit_distinct_parts_or_metadata(self):
        payload = self.codex(["first visible text"], mirror="distinct supplemental text")
        part = copy.deepcopy(payload)
        part["records"][0]["content"].pop("supplemental_content")
        part["source_artifact"]["replay_plan"]["steps"][-1]["parts"] = []
        first = self.event("AgentMessage", "m1", [{"type": "Text", "text": "same"}], "final_answer")
        second = {"type": "response_item", "payload": {"type": "message", "id": "m1", "role": "assistant",
                  "phase": "final_answer", "content": [{"type": "output_text", "text": "same"}],
                  "attachments": [{"type": "image", "url": "https://example.invalid/lost.png"}]}}
        metadata = adapters.parse_codex(self.source([self.meta(), first, second]))
        metadata["records"][0]["content"].pop("attachments")
        metadata["source_artifact"]["replay_plan"]["steps"][-1]["added_keys"] = []
        for altered in (part, metadata):
            with self.subTest(kind="part" if altered is part else "metadata"):
                stored = lib.publish(self.root, "alpha", altered)
                with self.assertRaises(lib.LibraryError):
                    source_verification.verify_source(Path(stored["path"]))

    def test_replay_plan_cannot_invent_source_identities(self):
        for payload in (self.codex(["ordinary"]), self.parse(self.bundle(["ordinary"]))):
            artifact = copy.deepcopy(payload["source_artifact"])
            for identity in ("invented-platform-id", "m1:revision:" + "0" * 16, "m1:source-version:" + "0" * 16):
                artifact["replay_plan"]["steps"][0]["record_id"] = identity
                with self.assertRaises(adapters.AdapterError):
                    adapters.replay_source_artifact(artifact, payload["provider"], "chat-1", "Synthetic")

    def test_replay_plan_cannot_merge_or_deduplicate_unrelated_source_ids(self):
        for text in ("same", "different"):
            rows = [self.meta(), self.event("AgentMessage", "m1", [{"type": "Text", "text": "same"}], "final_answer"),
                    self.event("AgentMessage", "m2", [{"type": "Text", "text": text}], "final_answer")]
            payload = adapters.parse_codex(self.source(rows))
            for action in ("duplicate", "merge-message"):
                artifact = copy.deepcopy(payload["source_artifact"])
                step = artifact["replay_plan"]["steps"][1]
                step.update(action=action, record_id="m1")
                if action == "merge-message":
                    step.update(parts=[0], added_keys=[], supplemental_keys=[])
                with self.assertRaises(adapters.AdapterError):
                    adapters.replay_source_artifact(artifact, "codex", "chat-1", "Synthetic")

    def test_replay_plan_cannot_merge_unrelated_tool_calls(self):
        rows = [self.meta(), {"type": "response_item", "payload": {
            "type": "function_call_output", "id": "r1", "call_id": "call-1", "output": "base"}},
            self.event("CommandExecution", "call-2", [])]
        rows[-1]["payload"]["item"].update(aggregated_output="unrelated", exit_code=0)
        payload = adapters.parse_codex(self.source(rows))
        artifact = copy.deepcopy(payload["source_artifact"])
        artifact["replay_plan"]["steps"][-1].update(record_id="r1", action="merge-tool", append_event=True)
        with self.assertRaises(adapters.AdapterError):
            adapters.replay_source_artifact(artifact, "codex", "chat-1", "Synthetic")

    def test_legacy_unmapped_safe_sources_replay_without_rewriting(self):
        for payload in (self.codex(["ordinary", "edited"]), self.parse(self.bundle(["ordinary", "edited"]))):
            payload["source_artifact"].pop("replay_plan")
            result = lib.publish(self.root, "alpha", payload)
            path = Path(result["path"])
            before = {file.name: file.read_bytes() for file in path.iterdir()}
            self.assertEqual(source_verification.verify_source(path)["status"], "verified-source-replay")
            self.assertEqual(before, {file.name: file.read_bytes() for file in path.iterdir()})

    def test_legacy_unmapped_redacted_edits_are_not_falsely_verified(self):
        for payload in (self.codex(["ordinary", "sk-" + "a" * 32]),
                        self.parse(self.bundle(["ordinary", "sk-" + "a" * 32]))):
            payload["source_artifact"].pop("replay_plan")
            result = lib.publish(self.root, "alpha", payload)
            path = Path(result["path"])
            before = {file.name: file.read_bytes() for file in path.iterdir()}
            with self.assertRaises(lib.LibraryError):
                source_verification.verify_source(path)
            lib.validate_revision(path)
            self.assertEqual(before, {file.name: file.read_bytes() for file in path.iterdir()})

    def test_auto_sync_checkpoints_redacted_edits_and_then_skips_unchanged(self):
        payload = self.codex(["sk-" + "a" * 32, "sk-" + "b" * 32])
        source = {"kind": "codex", "provider": "codex", "conversation_id": "chat-1", "path": payload["source"]["path"],
                  "title": "Synthetic", "projects": ["alpha"], "enabled": True}
        auto_sync.atomic_json(self.root / "sync-config.json", {"schema_version": "ai-context-auto-sync/1.0", "enabled": True,
                              "sources": [source], "codex_discovery_roots": [], "workspace_routes": []})
        first = auto_sync.sync_files(self.root)
        self.assertEqual(first["sources"][0]["status"], "imported")
        self.assertTrue(first["sources"][0]["source_verified"])
        self.assertTrue(auto_sync.read_state(self.root / "sync")["sources"])
        self.assertEqual(auto_sync.sync_files(self.root)["sources"][0]["status"], "unchanged-file")

    def test_auto_sync_recovers_from_unreplayable_legacy_snapshot_without_rewriting(self):
        payload = self.codex(["ordinary", "sk-" + "a" * 32])
        legacy = copy.deepcopy(payload)
        legacy["source_artifact"].pop("replay_plan")
        old = Path(lib.publish(self.root, "alpha", legacy, "Synthetic")["path"])
        before = {file.name: file.read_bytes() for file in old.iterdir()}
        with self.assertRaises(lib.LibraryError):
            source_verification.verify_source(old)
        settings = {"schema_version": "ai-context-auto-sync/1.0", "enabled": True, "codex_discovery_roots": [], "workspace_routes": [],
                    "sources": [{"kind": "codex", "provider": "codex", "conversation_id": "chat-1", "path": payload["source"]["path"],
                                 "title": "Synthetic", "projects": ["alpha"], "enabled": True}]}
        auto_sync.atomic_json(self.root / "sync-config.json", settings)
        result = auto_sync.sync_files(self.root)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["sources"][0]["status"], "imported")
        self.assertTrue(result["sources"][0]["source_verified"])
        self.assertEqual(len(lib.enumerate_revisions(self.root, "alpha")), 2)
        self.assertEqual(before, {file.name: file.read_bytes() for file in old.iterdir()})

    def test_auto_sync_does_not_mask_corrupt_storage_as_legacy_replay_failure(self):
        payload = self.codex(["ordinary", "edited"])
        old = Path(lib.publish(self.root, "alpha", payload, "Synthetic")["path"])
        (old / "messages.jsonl").write_text("corrupted synthetic bytes")
        source = {"kind": "codex", "provider": "codex", "conversation_id": "chat-1", "path": payload["source"]["path"],
                  "title": "Synthetic", "projects": ["alpha"], "enabled": True}
        auto_sync.atomic_json(self.root / "sync-config.json", {"schema_version": "ai-context-auto-sync/1.0", "enabled": True,
                              "sources": [source], "codex_discovery_roots": [], "workspace_routes": []})
        self.assertEqual(auto_sync.sync_files(self.root)["status"], "errors")
        self.assertEqual(auto_sync.read_state(self.root / "sync")["sources"], {})
        self.assertEqual(len(lib.enumerate_revisions(self.root, "alpha")), 1)

    def test_auto_sync_rechecks_unchanged_files_after_redaction_policy_upgrade(self):
        payload = self.codex(["ordinary"])
        with patch.object(lib, "REDACTION_POLICY_VERSION", "2"):
            old = lib.publish(self.root, "alpha", payload, "Synthetic")
        source = {"kind": "codex", "provider": "codex", "conversation_id": "chat-1", "path": payload["source"]["path"],
                  "title": "Synthetic", "projects": ["alpha"], "enabled": True}
        auto_sync.atomic_json(self.root / "sync-config.json", {"schema_version": "ai-context-auto-sync/1.0", "enabled": True,
                              "sources": [source], "codex_discovery_roots": [], "workspace_routes": []})
        folder = self.root / "sync"
        folder.mkdir(mode=0o700)
        key = auto_sync.key_for("codex", "chat-1")
        auto_sync.atomic_json(folder / "state.json", {"candidates": {}, "sources": {key: {
            "revision": old["revision"], "file_signature": auto_sync.stat_signature(Path(source["path"])),
            "capture_policy": "adapter-4/redaction-2"}}})
        self.assertEqual(auto_sync.sync_files(self.root)["sources"][0]["status"], "imported")
        self.assertEqual(len(lib.enumerate_revisions(self.root, "alpha")), 2)
        self.assertEqual(auto_sync.read_state(folder)["sources"][key]["capture_policy"], auto_sync.CAPTURE_POLICY)


if __name__ == "__main__":
    unittest.main()
