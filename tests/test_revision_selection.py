"""Synthetic revision containment and ambiguity checks; no provider data."""

import base64
import hashlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import context_library as lib
import revision_selection as selection


class RevisionSelectionTests(unittest.TestCase):
    def setUp(self):
        self.records = {}
        self.manifests = {}
        self.calls = []
        self.addCleanup(patch.stopall)
        patch.object(lib, "validate_revision", side_effect=self.validate).start()

    def validate(self, path, project):
        self.calls.append((path, project))
        return self.manifests[path], self.records[path]

    def candidate(self, letter, contents, *, imported="2026-10-08T01:00:00Z",
                  end=None, size=None, source_path="/synthetic/source", ids=None,
                  schema="1.0", project="alpha"):
        path = Path("/synthetic/revisions") / (letter * 64)
        source = {"path": source_path}
        if end is not None:
            source["last_timestamp"] = end
        if size is not None:
            source["snapshot_bytes"] = size
            source["parsed_bytes"] = size
        manifest = {"schema_version": schema, "project": project, "provider": "synthetic",
                    "conversation_id": "conversation", "conversation_key": "c" * 64,
                    "revision": letter * 64, "record_count": len(contents),
                    "captured_at_iso": imported, "source": source}
        records = [{"id": ids[index] if ids else f"m{index + 1}", "role": "user", "channel": "message",
                    "timestamp": None, "content": value, "source_refs": []}
                   for index, value in enumerate(contents)]
        self.records[path], self.manifests[path] = records, manifest
        return path, manifest

    def test_older_short_snapshot_imported_later_does_not_regress(self):
        complete = self.candidate("a", list("12345"), imported="2026-10-08T01:00:00Z",
                                  end="2026-10-07T05:00:00Z", size=500)
        stale = self.candidate("b", list("123"), imported="2026-10-08T02:00:00Z",
                               end="2026-10-07T03:00:00Z", size=300)
        self.assertEqual(selection.select_revision([complete, stale])[0], complete[0])

    def test_branch_requires_explicit_revision_even_when_longer(self):
        a = self.candidate("a", ["question", "answer A"])
        b = self.candidate("b", ["question", "answer B", "extra response"])
        with self.assertRaisesRegex(selection.SelectionError, "--revision"):
            selection.select_revision([a, b])

    def test_identical_reexport_ignores_import_order_and_reassigned_ids(self):
        a = self.candidate("a", ["same", "same"], source_path="/synthetic/export-a",
                           ids=["first", "second"], imported="2026-10-08T02:00:00Z")
        b = self.candidate("b", ["same", "same"], source_path="/synthetic/export-b",
                           ids=["new-first", "new-second"], imported="2026-10-08T01:00:00Z")
        self.assertEqual(selection.select_revision([b, a])[0], a[0])
        self.assertEqual(selection.select_revision([a, b])[0], a[0])

    def test_single_revision_validates_and_supports_projectless_v2(self):
        a = self.candidate("a", ["only"], schema="2.0", project=None)
        self.assertEqual(selection.select_revision([a]), a)
        self.assertEqual(self.calls, [(a[0], None)])

    def test_source_cutoff_conflicts_with_record_containment(self):
        a = self.candidate("a", list("12345"), end="2026-10-07T03:00:00Z", size=500)
        b = self.candidate("b", list("123"), end="2026-10-07T05:00:00Z", size=300)
        with self.assertRaisesRegex(selection.SelectionError, "--revision"):
            selection.select_revision([a, b])

    def test_source_growth_alone_never_establishes_record_coverage(self):
        a = self.candidate("a", ["old text"], end="2026-10-07T03:00:00Z", size=100)
        b = self.candidate("b", ["replacement text"], end="2026-10-07T05:00:00Z", size=1000)
        with self.assertRaises(selection.SelectionError):
            selection.select_revision([a, b])

    def test_same_size_different_source_hash_requires_explicit_choice(self):
        a = self.candidate("a", ["same"], size=100)
        b = self.candidate("b", ["same"], size=100)
        a[1]["source"]["snapshot_sha256"] = "a" * 64
        b[1]["source"]["snapshot_sha256"] = "b" * 64
        with self.assertRaises(selection.SelectionError):
            selection.select_revision([a, b])

    def test_adapter_enrichment_restores_media_and_adds_fields(self):
        encoded = base64.b64encode(bytes(range(256))).decode("ascii")
        marker = {"omitted_inline_media": True, "length": len(encoded),
                  "sha256": hashlib.sha256(encoded.encode()).hexdigest()}
        a = self.candidate("a", [{"type": "image", "data": marker}])
        b = self.candidate("b", [{"type": "image", "data": encoded, "detail": "original"}])
        self.assertEqual(selection.select_revision([a, b])[0], b[0])

    def test_structure_change_with_equal_text_is_not_equivalent(self):
        a = self.candidate("a", ["same text"])
        b = self.candidate("b", ["same text"])
        self.records[b[0]][0]["role"] = "assistant"
        with self.assertRaises(selection.SelectionError):
            selection.select_revision([a, b])

    def test_reordered_stable_ids_do_not_cover_history(self):
        a = self.candidate("a", ["first", "second"])
        b = self.candidate("b", ["second", "first", "third"], ids=["m2", "m1", "m3"])
        with self.assertRaises(selection.SelectionError):
            selection.select_revision([a, b])

    def test_nested_argument_insertion_is_an_edit_not_more_records(self):
        a = self.candidate("a", [{"arguments": ["command", "target"]}])
        b = self.candidate("b", [{"arguments": ["command", "new-option", "target"]}, "extra"])
        with self.assertRaises(selection.SelectionError):
            selection.select_revision([a, b])

    def test_equal_text_with_conflicting_top_level_branch_extensions_is_ambiguous(self):
        for field in ("branch_id", "parent_id", "edit_of", "unknown_public_extension"):
            with self.subTest(field=field):
                a = self.candidate("a", ["same text"], imported="2026-10-08T01:00:00Z")
                b = self.candidate("b", ["same text"], imported="2026-10-08T02:00:00Z")
                self.records[a[0]][0][field] = "branch-A"
                self.records[b[0]][0][field] = "branch-B"
                with self.assertRaisesRegex(selection.SelectionError, "--revision"):
                    selection.select_revision([a, b])

    def test_manifest_selected_branch_conflict_prevents_equivalence(self):
        a = self.candidate("a", ["same text"])
        b = self.candidate("b", ["same text"])
        a[1]["conversation_metadata"] = {"selected_branch": "branch-A"}
        b[1]["conversation_metadata"] = {"selected_branch": "branch-B"}
        with self.assertRaisesRegex(selection.SelectionError, "--revision"):
            selection.select_revision([a, b])

    def test_declared_completeness_null_fill_requires_explicit_choice(self):
        a = self.candidate("a", ["same text"])
        b = self.candidate("b", ["same text", "extra record"])
        a[1]["source_declared_completeness"] = {"complete": None}
        b[1]["source_declared_completeness"] = {"complete": True}
        with self.assertRaisesRegex(selection.SelectionError, "--revision"):
            selection.select_revision([a, b])

    def test_integrity_error_is_not_bypassed_by_single_candidate(self):
        a = self.candidate("a", ["only"])
        with patch.object(lib, "validate_revision", side_effect=ValueError("SENSITIVE_SENTINEL")):
            with self.assertRaises(selection.SelectionError) as error:
                selection.select_revision([a])
        self.assertNotIn("SENSITIVE_SENTINEL", str(error.exception))


if __name__ == "__main__":
    unittest.main()
