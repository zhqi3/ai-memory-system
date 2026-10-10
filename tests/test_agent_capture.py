"""Cross-agent capture contracts, using invented exports only."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import adapters
import auto_sync
import context_library as lib
import source_verification
import workspace

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "skills/archive-project-context/scripts/capture.py"
spec = importlib.util.spec_from_file_location("agent_capture_skill", SCRIPT)
skill = importlib.util.module_from_spec(spec)
spec.loader.exec_module(skill)


class AgentCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.base = Path(self.temp.name)
        workspace.init_workspace(self.base / "data", "demo")
        self.root = self.base / "data/AI-Context"
        self.source = self.base / "export.jsonl"
        self.before = {str(p.relative_to(self.base)): p.read_bytes()
                       for p in (self.base / "data/AI-Memory").rglob("*") if p.is_file()}
        self.settings = (self.root / "sync-config.json").read_bytes()

    def tearDown(self):
        self.temp.cleanup()

    def hermes(self):
        return {"id": "session-synthetic", "system_prompt": "PRIVATE_SYSTEM_SENTINEL",
                "parent_session_id": "parent-synthetic", "source": "cli",
                "messages": [
                    {"id": 1, "role": "system", "content": "PRIVATE_SYSTEM_SENTINEL"},
                    {"id": 2, "role": "user", "content": "Keep this exact: 中文\n<literal>"},
                    {"id": 3, "role": "assistant", "content": "Visible response",
                     "reasoning": "PRIVATE_REASONING_SENTINEL", "active": 1, "compacted": 0,
                     "tool_calls": [{"id": "t1", "function": {"name": "example", "arguments": "{}"}}]},
                    {"id": 4, "role": "tool", "tool_call_id": "t1", "content": "Synthetic result"}]}

    def claude(self, agent=None):
        base = {"sessionId": "session-synthetic", "isSidechain": bool(agent)}
        if agent:
            base["agentId"] = agent
        return [
            {"type": "system", "content": "PRIVATE_SYSTEM_SENTINEL"},
            {**base, "type": "user", "uuid": "u1", "parentUuid": None,
             "message": {"role": "user", "content": "Exact visible question"}},
            {**base, "type": "assistant", "uuid": "a1", "parentUuid": "u1",
             "message": {"role": "assistant", "content": [
                 {"type": "thinking", "thinking": "PRIVATE_REASONING_SENTINEL"},
                 {"type": "text", "text": "Visible response"},
                 {"type": "tool_use", "id": "t1", "name": "example", "input": {"thinking": "ordinary tool data"}}]}}]

    def chatgpt_page(self, turn="t1", more=False, cursor=None, next_cursor=None):
        return {"schemaVersion": 1,
                "thread": {"id": "session-synthetic", "kind": "chatgpt", "title": "Synthetic capture",
                           "preview": "PREVIEW_NOT_BODY"},
                "page": {"order": "newest_first", "hasMore": more,
                         "cursor": cursor, "nextCursor": next_cursor},
                "turns": [{"id": turn, "status": "completed", "items": [
                    {"type": "userMessage", "id": turn + "-u", "content": [
                        {"type": "text", "text": "Exact question 中文 " + turn}]},
                    {"type": "agentMessage", "id": turn + "-a", "text": "Exact response " + turn,
                     "parent_id": turn + "-u"}]}],
                "attachments": [{"type": "file", "url": "https://example.invalid/synthetic.txt"}]}

    def chatgpt(self):
        return {"schema_version": "ai-context-read-thread/1.0", "pages": [
            self.chatgpt_page("t2", more=True, next_cursor="older"),
            self.chatgpt_page("t1", cursor="older")]}

    def save(self, value):
        rows = value if isinstance(value, list) else [value]
        self.source.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))

    def args(self, format="hermes-session-json", agent="director"):
        args = ["--repo", str(REPO), "--root", str(self.root), "--project", "demo",
                "--source", str(self.source), "--session-id", "session-synthetic",
                "--title", "Synthetic capture", "--format", format]
        if agent is not None:
            args += ["--agent-id", agent]
        return args

    def run_capture(self, format="hermes-session-json", agent="director"):
        result = subprocess.run([sys.executable, "-B", str(SCRIPT), *self.args(format, agent)],
                                capture_output=True, text=True, cwd=self.base)
        return result, json.loads(result.stdout or result.stderr)

    def revisions(self):
        return list(lib.enumerate_revisions(self.root, "demo"))

    def test_hermes_exact_visible_capture_private_fields_and_source_unchanged(self):
        self.save(self.hermes())
        original = self.source.read_bytes()
        result, receipt = self.run_capture()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(receipt["record_count"], 3)
        self.assertEqual(receipt["source_snapshot_sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(self.source.read_bytes(), original)
        revision, manifest = self.revisions()[0]
        records = lib.read_records(revision / "messages.jsonl")
        self.assertEqual(records[0]["content"], "Keep this exact: 中文\n<literal>")
        self.assertEqual(manifest["conversation_metadata"]["capture"]["parent_native_session_id"], "parent-synthetic")
        for file in self.root.rglob("*"):
            if file.is_file():
                self.assertNotIn(b"PRIVATE_SYSTEM_SENTINEL", file.read_bytes())
                self.assertNotIn(b"PRIVATE_REASONING_SENTINEL", file.read_bytes())
        self.assertNotIn("Visible response", result.stdout)
        self.assertEqual(self.settings, (self.root / "sync-config.json").read_bytes())
        self.assertEqual(self.before, {str(p.relative_to(self.base)): p.read_bytes()
                         for p in (self.base / "data/AI-Memory").rglob("*") if p.is_file()})

    def test_same_export_deduplicates_and_append_preserves_old_revision(self):
        data = self.hermes()
        self.save(data)
        _, first = self.run_capture()
        _, second = self.run_capture()
        self.assertEqual(first["revision"], second["revision"])
        self.assertEqual(second["status"], "deduplicated")
        old_path, _ = self.revisions()[0]
        old = {p.name: p.read_bytes() for p in old_path.iterdir()}
        data["messages"].append({"id": 5, "role": "user", "content": "Next exact question"})
        self.save(data)
        result, third = self.run_capture()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(first["revision"], third["revision"])
        self.assertEqual(len(self.revisions()), 2)
        self.assertEqual(old, {p.name: p.read_bytes() for p in old_path.iterdir()})

    def test_providers_profiles_and_claude_children_have_separate_archive_keys(self):
        self.save(self.hermes())
        keys = []
        for profile in ("director", "reviewer"):
            result, receipt = self.run_capture(agent=profile)
            self.assertEqual(result.returncode, 0, result.stderr)
            keys.append(receipt["conversation_key"])
        for agent in (None, "child-a", "child-b"):
            self.save(self.claude(agent))
            result, receipt = self.run_capture("claude-code-jsonl", agent)
            self.assertEqual(result.returncode, 0, result.stderr)
            keys.append(receipt["conversation_key"])
        self.save(self.chatgpt())
        result, receipt = self.run_capture("chatgpt-read-thread", None)
        self.assertEqual(result.returncode, 0, result.stderr)
        keys.append(receipt["conversation_key"])
        self.assertEqual(len(set(keys)), 6)
        self.assertEqual(len(self.revisions()), 6)

    def test_claude_preserves_graph_and_tool_json_excludes_thinking_and_injection(self):
        rows = self.claude()
        rows.append({**rows[1], "uuid": "meta", "isMeta": True,
                     "message": {"role": "user", "content": "PRIVATE_SYSTEM_SENTINEL"}})
        self.save(rows)
        result, receipt = self.run_capture("claude-code-jsonl", None)
        self.assertEqual(result.returncode, 0, result.stderr)
        records = lib.read_records(self.revisions()[0][0] / "messages.jsonl")
        self.assertEqual(len(records), 2)
        self.assertEqual(records[1]["parent_id"], "u1")
        self.assertEqual(records[1]["content"][1]["input"]["thinking"], "ordinary tool data")
        self.assertEqual(receipt["normalization_exclusions"]["injected_meta_messages"], 1)
        for path in self.root.rglob("*.json*"):
            self.assertNotIn(b"PRIVATE_", path.read_bytes())

    def test_identity_and_schema_failures_publish_nothing(self):
        bad = self.hermes()
        bad["id"] = "different-session"
        self.save(bad)
        self.assertEqual(self.run_capture()[0].returncode, 1)
        bad = self.hermes()
        bad["messages"][1]["content"] = [{"type": "unrecognized", "text": "PRIVATE_SENTINEL"}]
        self.save(bad)
        result, _ = self.run_capture()
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("PRIVATE_SENTINEL", result.stderr)
        self.save([self.hermes(), self.hermes()])
        self.assertEqual(self.run_capture()[0].returncode, 1)
        self.assertEqual(self.revisions(), [])

    def test_child_cannot_be_mislabeled_as_main_or_other_child(self):
        for observed, requested in (("child-a", None), ("child-a", "child-b"), (None, "child-b")):
            with self.subTest(observed=observed, requested=requested):
                self.save(self.claude(observed))
                self.assertEqual(self.run_capture("claude-code-jsonl", requested)[0].returncode, 1)
        self.assertEqual(self.revisions(), [])

    def test_partial_jsonl_and_unknown_events_are_not_success(self):
        self.save(self.claude())
        self.source.write_bytes(self.source.read_bytes() + b'{"incomplete":')
        self.assertEqual(self.run_capture("claude-code-jsonl", None)[0].returncode, 1)
        self.save(self.claude() + [{"type": "new-runtime-shape", "payload": "PRIVATE_SENTINEL"}])
        result, _ = self.run_capture("claude-code-jsonl", None)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("PRIVATE_SENTINEL", result.stderr)
        self.assertEqual(self.revisions(), [])

    def test_existing_sync_writer_lock_is_respected(self):
        self.save(self.hermes())
        with auto_sync.locked(self.root):
            result, receipt = self.run_capture()
        self.assertEqual(result.returncode, 3)
        self.assertEqual(receipt["status"], "busy")
        self.assertEqual(self.revisions(), [])

    def test_credentials_redacted_before_staging_and_receipts_are_private(self):
        data = self.hermes()
        secret = "sk-" + "a" * 32
        data["messages"][1]["content"] = secret
        self.save(data)
        result, receipt = self.run_capture()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreater(receipt["redaction_count"], 0)
        for path in self.root.rglob("*"):
            if path.is_file():
                self.assertNotIn(secret.encode(), path.read_bytes())
        self.assertEqual(Path(receipt["receipt_path"]).stat().st_mode & 0o777, 0o600)
        self.assertFalse(receipt["reading_acknowledged"])

    def test_source_symlink_and_missing_project_are_rejected(self):
        self.save(self.hermes())
        actual = self.source.with_suffix(".json")
        self.source.rename(actual)
        self.source.symlink_to(actual)
        self.assertEqual(self.run_capture()[0].returncode, 1)
        self.source.unlink()
        actual.rename(self.source)
        args = skill.parser().parse_args(self.args())
        args.project = "missing"
        with self.assertRaises(lib.LibraryError):
            skill.capture(args)
        self.assertEqual(self.revisions(), [])

    def test_chatgpt_preserves_pages_exactly_and_reports_limited_coverage(self):
        self.save(self.chatgpt())
        original = self.source.read_bytes()
        result, receipt = self.run_capture("chatgpt-read-thread", None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(receipt["conversation_id"], "session-synthetic")
        self.assertEqual(receipt["provider"], "chatgpt")
        self.assertEqual(receipt["record_count"], 4)
        self.assertEqual(receipt["verification_scope"], "preserved-read-thread-pages")
        assessment = receipt["source_assessment"]
        self.assertEqual(assessment["page_count"], 2)
        self.assertTrue(assessment["pagination_exhausted"])
        self.assertFalse(assessment["pagination_chain_verified"])
        self.assertFalse(assessment["whole_history_complete"])
        self.assertFalse(assessment["external_attachments_copied"])
        revision, manifest = self.revisions()[0]
        artifact = lib.read_json(revision / "source-artifact.json")
        self.assertEqual(artifact["format"], "ai-context-read-thread/1.0")
        self.assertEqual(artifact["content"].encode(), original)
        records = lib.read_records(revision / "messages.jsonl")
        self.assertEqual([r["id"] for r in records], ["t1-u", "t1-a", "t2-u", "t2-a"])
        self.assertEqual(records[1]["parent_id"], "t1-u")
        self.assertNotIn("PREVIEW_NOT_BODY", json.dumps(records))
        self.assertNotIn("Exact response", result.stdout)
        self.assertEqual(self.source.read_bytes(), original)
        self.assertEqual(self.settings, (self.root / "sync-config.json").read_bytes())
        self.assertFalse(receipt["reading_acknowledged"])
        self.assertEqual(self.before, {str(p.relative_to(self.base)): p.read_bytes()
                         for p in (self.base / "data/AI-Memory").rglob("*") if p.is_file()})

    def test_chatgpt_deduplicates_existing_read_thread_import(self):
        self.save(self.chatgpt())
        existing = lib.publish(self.root, "demo", adapters.parse_read_thread(self.source), "Synthetic capture")
        result, receipt = self.run_capture("chatgpt-read-thread", None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(receipt["status"], "deduplicated")
        self.assertEqual(receipt["conversation_key"], existing["conversation_key"])
        self.assertEqual(receipt["revision"], existing["revision"])
        self.assertEqual(len(self.revisions()), 1)

    def test_chatgpt_append_and_edit_keep_old_snapshot_and_source_versions(self):
        bundle = self.chatgpt()
        self.save(bundle)
        result, first = self.run_capture("chatgpt-read-thread", None)
        self.assertEqual(result.returncode, 0, result.stderr)
        old_path, _ = self.revisions()[0]
        old = {p.name: p.read_bytes() for p in old_path.iterdir()}
        items = bundle["pages"][0]["turns"][0]["items"]
        items.append({"type": "agentMessage", "id": "t2-a", "parent_id": "t2-u", "text": "Edited reply"})
        self.save(bundle)
        result, second = self.run_capture("chatgpt-read-thread", None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(first["revision"], second["revision"])
        self.assertEqual(second["record_count"], 5)
        self.assertEqual(old, {p.name: p.read_bytes() for p in old_path.iterdir()})
        self.assertEqual(len(self.revisions()), 2)

    def test_chatgpt_rejects_incomplete_or_inconsistent_pagination(self):
        variants = []
        bundle = self.chatgpt(); bundle["pages"].pop(); variants.append(bundle)
        bundle = self.chatgpt(); bundle["pages"][0]["page"].pop("hasMore"); variants.append(bundle)
        bundle = self.chatgpt(); bundle["pages"][0]["page"]["hasMore"] = 1; variants.append(bundle)
        bundle = self.chatgpt(); bundle["pages"][0]["page"]["nextCursor"] = None; variants.append(bundle)
        bundle = self.chatgpt(); bundle["pages"][1]["page"]["nextCursor"] = "unexpected"; variants.append(bundle)
        bundle = self.chatgpt(); bundle["pages"][0]["page"].update(hasMore=False, nextCursor=None); variants.append(bundle)
        bundle = self.chatgpt(); bundle["pages"][1]["page"]["cursor"] = "wrong"; variants.append(bundle)
        bundle = self.chatgpt(); bundle["pages"][0]["page"]["cursor"] = "not-first-page"; variants.append(bundle)
        bundle = self.chatgpt(); bundle["pages"][1]["page"].update(hasMore=True, nextCursor="older")
        bundle["pages"].append(self.chatgpt_page("t0", cursor="older")); variants.append(bundle)
        for index, bundle in enumerate(variants):
            with self.subTest(case=index):
                self.save(bundle)
                result, _ = self.run_capture("chatgpt-read-thread", None)
                self.assertEqual(result.returncode, 1)
        self.assertEqual(self.revisions(), [])

    def test_chatgpt_identity_mismatch_and_agent_id_are_rejected(self):
        bundle = self.chatgpt()
        bundle["pages"][1]["thread"]["id"] = "other-conversation"
        self.save(bundle)
        self.assertEqual(self.run_capture("chatgpt-read-thread", None)[0].returncode, 1)
        bundle["pages"][0]["thread"]["id"] = "other-conversation"
        self.save(bundle)
        self.assertEqual(self.run_capture("chatgpt-read-thread", None)[0].returncode, 1)
        self.save(self.chatgpt())
        self.assertEqual(self.run_capture("chatgpt-read-thread", "director")[0].returncode, 1)
        self.assertEqual(self.revisions(), [])

    def test_chatgpt_preview_and_private_engine_items_are_not_accepted(self):
        bundle = self.chatgpt()
        item = bundle["pages"][0]["turns"][0]["items"][1]
        item["preview"] = item.pop("text")
        self.save(bundle)
        self.assertEqual(self.run_capture("chatgpt-read-thread", None)[0].returncode, 1)
        for kind in ("reasoning", "systemMessage", "developerMessage"):
            bundle = self.chatgpt()
            bundle["pages"][0]["turns"][0]["items"].append({"type": kind, "text": "PRIVATE_SENTINEL"})
            self.save(bundle)
            result, _ = self.run_capture("chatgpt-read-thread", None)
            self.assertEqual(result.returncode, 1)
            self.assertNotIn("PRIVATE_SENTINEL", result.stderr)
        self.assertEqual(self.revisions(), [])

    def test_chatgpt_known_truncation_flags_and_markers_fail_capture(self):
        for location in ("bundle", "page", "pagination", "turn", "item", "text"):
            bundle = self.chatgpt()
            page = bundle["pages"][0]
            targets = {"bundle": bundle, "page": page, "pagination": page["page"],
                       "turn": page["turns"][0], "item": page["turns"][0]["items"][1]}
            if location == "text":
                targets["item"]["text"] = "Warning: truncated output, PRIVATE_SENTINEL"
            else:
                targets[location]["truncated"] = True
            self.save(bundle)
            result, _ = self.run_capture("chatgpt-read-thread", None)
            self.assertEqual(result.returncode, 1, location)
            self.assertNotIn("PRIVATE_SENTINEL", result.stderr)
        self.assertEqual(self.revisions(), [])

    def test_chatgpt_keeps_quoted_user_flags_as_data(self):
        bundle = self.chatgpt()
        content = [{"type": "text", "text": "Example JSON: {\"truncated\": true}"}]
        bundle["pages"][0]["turns"][0]["items"][0]["content"] = content
        self.save(bundle)
        result, _ = self.run_capture("chatgpt-read-thread", None)
        self.assertEqual(result.returncode, 0, result.stderr)
        records = lib.read_records(self.revisions()[0][0] / "messages.jsonl")
        self.assertEqual(records[2]["content"], content)

    def test_chatgpt_unknown_public_item_preserved_with_explicit_gap(self):
        bundle = self.chatgpt()
        tool = {"type": "toolCall", "id": "tool-1", "output": "Exact tool response"}
        bundle["pages"][0]["turns"][0]["items"].append(tool)
        self.save(bundle)
        result, receipt = self.run_capture("chatgpt-read-thread", None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(receipt["source_assessment"]["unparsed_public_item_count"], 1)
        artifact = lib.read_json(self.revisions()[0][0] / "source-artifact.json")
        self.assertEqual(json.loads(artifact["content"])["pages"][0]["turns"][0]["items"][-1], tool)
        self.assertNotIn("Exact tool response", result.stdout)

    def test_chatgpt_redacted_edits_replay_without_identity_collapse(self):
        bundle = self.chatgpt()
        items = bundle["pages"][0]["turns"][0]["items"]
        one, two = "sk-" + "a" * 32, "sk-" + "b" * 32
        items[1]["text"] = one
        items.append({**items[1], "text": two})
        self.save(bundle)
        result, receipt = self.run_capture("chatgpt-read-thread", None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(receipt["record_count"], 5)
        self.assertGreater(receipt["redaction_count"], 0)
        revision = self.revisions()[0][0]
        self.assertEqual(source_verification.verify_source(revision, "demo")["status"], "verified-source-replay")
        records = lib.read_records(revision / "messages.jsonl")
        self.assertNotEqual(records[-1]["id"], records[-2]["id"])
        self.assertEqual(records[-1]["content"], records[-2]["content"])
        for path in self.root.rglob("*"):
            if path.is_file():
                self.assertNotIn(one.encode(), path.read_bytes())
                self.assertNotIn(two.encode(), path.read_bytes())

    def test_chatgpt_capture_respects_existing_writer_lock(self):
        self.save(self.chatgpt())
        with auto_sync.locked(self.root):
            result, receipt = self.run_capture("chatgpt-read-thread", None)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(receipt["status"], "busy")
        self.assertEqual(self.revisions(), [])

    def test_chatgpt_bundle_is_not_an_account_export_or_transport_wrapper(self):
        for value in ({"conversations": []}, {"schema_version": "ai-context-read-thread/1.0", "pages": []},
                      {"schema_version": "ai-context-read-thread/1.0", "pages": [{"content": [{"type": "text", "text": "preview"}]}]}):
            self.save(value)
            self.assertEqual(self.run_capture("chatgpt-read-thread", None)[0].returncode, 1)
        self.assertEqual(self.revisions(), [])


if __name__ == "__main__":
    unittest.main()
