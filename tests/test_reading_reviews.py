"""Synthetic reading coverage checks; never read private conversation archives."""
from pathlib import Path
import json
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import context_library as lib
import reading_reviews as reviews


class ReadingReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="reading-review-test-", dir=Path(tempfile.gettempdir()).resolve())
        self.base = Path(self.temp.name)
        self.root = self.base / "library"
        self.root.mkdir(mode=0o700)
        self.memory = self.base / "memory"
        for project in ("alpha", "beta"):
            path = self.memory / "projects" / project
            path.mkdir(parents=True)
            (path / "project-manifest.json").write_text(json.dumps({"schema_version": "2.0", "canonical_name": project}))
        (self.root / "library.json").write_text(json.dumps({"memory_root": str(self.memory)}))

    def tearDown(self):
        self.temp.cleanup()

    def archive(self, conversation="one", count=5, project="alpha", provider="fixture", change=""):
        payload = {"provider": provider, "conversation_id": conversation, "title": "Synthetic",
                   "records": [{"id": f"m{i}", "role": "user" if i % 2 else "assistant",
                                "channel": "message", "timestamp": None,
                                "content": f"Synthetic visible record {i}{change}", "source_refs": []}
                               for i in range(1, count + 1)],
                   "source": {}, "completeness": {"complete": False}}
        return lib.publish(self.root, project, payload)

    def plan(self, mode="full", **kwargs):
        return reviews.create_review(self.root, "alpha", mode, **kwargs)

    def test_full_plan_pins_all_conversations_and_never_auto_acknowledges(self):
        first, second = self.archive(), self.archive("two", 3)
        plan = self.plan()
        self.assertEqual(len(plan["conversations"]), 2)
        before = reviews.report(self.root, plan["review_id"])
        self.assertEqual(before["record_count"], 8)
        self.assertEqual(before["unread_count"], 8)
        self.assertEqual(before["retrieved_count"], 0)
        reviews.fetch(self.root, plan["review_id"], first["conversation_key"], 1, 5)
        retrieved = reviews.report(self.root, plan["review_id"])
        self.assertEqual(retrieved["retrieved_count"], 5)
        self.assertEqual(retrieved["acknowledged_count"], 0)
        self.assertEqual(retrieved["unread_count"], 8)
        reviews.acknowledge(self.root, plan["review_id"], "one", 1, 5)
        reviews.fetch(self.root, plan["review_id"], "two", 1, 3)
        self.assertEqual(reviews.report(self.root, plan["review_id"])["status"], "in-progress")
        reviews.acknowledge(self.root, plan["review_id"], second["conversation_key"], 1, 3)
        complete = reviews.report(self.root, plan["review_id"])
        self.assertEqual(complete["status"], "complete")
        self.assertTrue(complete["full_project_snapshot_read_complete"])
        self.assertFalse(complete["whole_project_history_complete"])
        self.assertEqual(complete["attachments"], {"status": "unknown", "complete": False})

    def test_ack_requires_retrieval_and_rejects_holes(self):
        self.archive()
        plan = self.plan()
        rid = plan["review_id"]
        with self.assertRaises(lib.LibraryError):
            reviews.acknowledge(self.root, rid, "one", 1, 1)
        reviews.fetch(self.root, rid, "one", 1, 2)
        reviews.fetch(self.root, rid, "one", 4, 5)
        with self.assertRaises(lib.LibraryError):
            reviews.acknowledge(self.root, rid, "one", 1, 5)
        reviews.acknowledge(self.root, rid, "one", 1, 2)
        report = reviews.report(self.root, rid)
        self.assertEqual(report["conversations"][0]["unread"], [[3, 5]])
        reviews.fetch(self.root, rid, "one", 3, 3)
        reviews.acknowledge(self.root, rid, "one", 1, 5)
        reviews.acknowledge(self.root, rid, "one", 2, 4)
        final = reviews.report(self.root, rid)
        self.assertEqual(final["retrieved_count"], 5)
        self.assertEqual(final["acknowledged_count"], 5)
        self.assertEqual(final["unread_count"], 0)

    def test_topic_remains_scoped_even_if_all_pinned_text_read(self):
        self.archive(count=2)
        plan = self.plan("topic", topic="Synthetic decision trace")
        reviews.fetch(self.root, plan["review_id"], "one", 1, 2)
        reviews.acknowledge(self.root, plan["review_id"], "one", 1, 2)
        report = reviews.report(self.root, plan["review_id"])
        self.assertEqual(report["status"], "scoped")
        self.assertTrue(report["plan_body_read_complete"])
        self.assertFalse(report["full_project_snapshot_read_complete"])

    def test_selected_conversation_is_not_project_full_coverage(self):
        self.archive(count=1)
        self.archive("two", 1)
        plan = self.plan(conversation="one")
        reviews.fetch(self.root, plan["review_id"], "one", 1, 1)
        reviews.acknowledge(self.root, plan["review_id"], "one", 1, 1)
        report = reviews.report(self.root, plan["review_id"])
        self.assertTrue(report["plan_body_read_complete"])
        self.assertFalse(report["full_project_snapshot_read_complete"])
        self.assertEqual(report["coverage_scope"], "selected-conversation")

    def test_explicit_versions_pin_snapshot_after_future_import(self):
        first = self.archive(count=2)
        self.archive(count=3, change="different source branch")
        plan = self.plan(versions={first["conversation_key"]: first["revision"]})
        self.assertEqual(plan["conversations"][0]["revision"], first["revision"])
        self.archive(count=4, change="later capture")
        result = reviews.fetch(self.root, plan["review_id"], "one", 1, 2)
        self.assertEqual(result["record_count"], 2)
        self.assertEqual(result["revision"], first["revision"])
        with self.assertRaises(lib.LibraryError):
            reviews.fetch(self.root, plan["review_id"], "one", 1, 4)

    def test_ambiguous_selection_is_not_replaced_by_latest(self):
        self.archive(count=2)
        self.archive(count=2, change="alternate branch")
        with self.assertRaises(lib.LibraryError):
            self.plan()
        self.assertFalse((self.root / "reviews").exists())

    def test_unknown_project_conversation_or_version_fails_closed(self):
        first = self.archive()
        foreign = self.archive("foreign", project="beta")
        with self.assertRaises(lib.LibraryError):
            reviews.create_review(self.root, "missing", "full")
        with self.assertRaises(lib.LibraryError):
            self.plan(conversation=foreign["conversation_key"])
        with self.assertRaises(lib.LibraryError):
            self.plan(versions={foreign["conversation_key"]: foreign["revision"]})
        with self.assertRaises(lib.LibraryError):
            self.plan(revision="f" * 64)
        plan = self.plan()
        for fn in (reviews.fetch, reviews.acknowledge):
            with self.assertRaises(lib.LibraryError):
                fn(self.root, plan["review_id"], foreign["conversation_key"], 1, 1)
        self.assertEqual(plan["conversations"][0]["conversation_key"], first["conversation_key"])

    def test_provider_collision_requires_full_conversation_key(self):
        first = self.archive(provider="fixture")
        self.archive(provider="another")
        with self.assertRaises(lib.LibraryError):
            self.plan(conversation="one")
        plan = self.plan()
        with self.assertRaises(lib.LibraryError):
            reviews.fetch(self.root, plan["review_id"], "one", 1, 1)
        result = reviews.fetch(self.root, plan["review_id"], first["conversation_key"], 1, 1)
        self.assertEqual(result["conversation_key"], first["conversation_key"])

    def test_permissions_and_archive_immutability(self):
        archive = self.archive()
        path = Path(archive["path"])
        before = {p.name: p.read_bytes() for p in path.iterdir()}
        plan = self.plan()
        reviews.fetch(self.root, plan["review_id"], "one", 1, 2)
        reviews.acknowledge(self.root, plan["review_id"], "one", 1, 2, note="Actual synthetic text read.")
        reviews.report(self.root, plan["review_id"])
        self.assertEqual(before, {p.name: p.read_bytes() for p in path.iterdir()})
        for p in (self.root / "reviews").rglob("*"):
            self.assertEqual(p.stat().st_mode & 0o777, 0o700 if p.is_dir() else 0o600)

    def test_invalid_ids_paths_and_symlink_roots(self):
        self.archive()
        plan = self.plan()
        for bad in ("../other", "", "A" * 32, "0" * 32):
            with self.assertRaises(lib.LibraryError):
                reviews.report(self.root, bad)
        linked = self.base / "linked"
        linked.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(lib.LibraryError):
            reviews.report(linked, plan["review_id"])
        event_dir = self.root / "reviews" / plan["review_id"] / "events"
        (event_dir / "000000000001.json").symlink_to(self.root / "library.json")
        with self.assertRaises(lib.LibraryError):
            reviews.review(self.root, plan["review_id"])

    def test_plan_and_event_tampering_rejected(self):
        self.archive()
        plan = self.plan()
        reviews.fetch(self.root, plan["review_id"], "one", 1, 1)
        event = self.root / "reviews" / plan["review_id"] / "events" / "000000000001.json"
        content = json.loads(event.read_text())
        content["end"] = 5
        event.write_text(json.dumps(content))
        with self.assertRaises(lib.LibraryError):
            reviews.report(self.root, plan["review_id"])
        new_plan = self.plan()
        path = self.root / "reviews" / new_plan["review_id"] / "plan.json"
        content = json.loads(path.read_text())
        content["project"] = "beta"
        path.write_text(json.dumps(content))
        with self.assertRaises(lib.LibraryError):
            reviews.review(self.root, new_plan["review_id"])

    def test_rehashed_cross_project_path_still_fails_scope_check(self):
        self.archive()
        foreign = self.archive("foreign", project="beta")
        plan = self.plan()
        path = self.root / "reviews" / plan["review_id"] / "plan.json"
        value = json.loads(path.read_text())
        value["conversations"][0]["revision_path"] = Path(foreign["path"]).relative_to(self.root).as_posix()
        value.pop("plan_sha256")
        path.write_bytes(lib.canonical_bytes(reviews._signed(value, "plan_sha256")))
        with self.assertRaises(lib.LibraryError):
            reviews.review(self.root, plan["review_id"])

    def test_snapshot_tampering_prevents_fetch_receipt(self):
        result = self.archive()
        plan = self.plan()
        path = Path(result["path"]) / "messages.jsonl"
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaises(lib.LibraryError):
            reviews.fetch(self.root, plan["review_id"], "one", 1, 1)
        events = self.root / "reviews" / plan["review_id"] / "events"
        self.assertEqual(list(events.iterdir()), [])

    def test_media_fetch_view_does_not_assert_attachment_inspection(self):
        media = {"type": "image", "mimeType": "image/png", "data": "aGVsbG8="}
        payload = {"provider": "fixture", "conversation_id": "media", "records": [
            {"id": "m", "role": "user", "content": media}]}
        lib.publish(self.root, "alpha", payload)
        plan = self.plan()
        display = reviews.fetch(self.root, plan["review_id"], "media", 1, 1)
        self.assertTrue(display["records"][0]["content"]["data"]["inline_media_display_omitted"])
        original = reviews.fetch(self.root, plan["review_id"], "media", 1, 1, include_media=True)
        self.assertEqual(original["records"][0]["content"], media)
        reviews.acknowledge(self.root, plan["review_id"], "media", 1, 1)
        self.assertEqual(reviews.report(self.root, plan["review_id"])["attachments"]["status"], "unknown")

    def test_empty_scope_and_invalid_arguments_do_not_create_plan(self):
        for project in (None, ""):
            with self.assertRaises(lib.LibraryError):
                reviews.create_review(self.root, project, "full")
        with self.assertRaises(lib.LibraryError):
            self.plan()
        self.archive()
        for kwargs in ({"mode": "unexpected"}, {"mode": "topic"}, {"mode": "topic", "topic": " "}, {"revision": "../bad"}):
            with self.assertRaises(lib.LibraryError):
                reviews.create_review(self.root, "alpha", kwargs.pop("mode", "full"), **kwargs)
        self.assertFalse((self.root / "reviews").exists())
        plan = self.plan()
        for start, end in ((0, 1), (2, 1), (1, 6), (True, 1), (1, 1.0)):
            with self.assertRaises(lib.LibraryError):
                reviews.fetch(self.root, plan["review_id"], "one", start, end)


if __name__ == "__main__":
    unittest.main()
