"""Synthetic publication checks; no real secret or personal path is used."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import audit_public_tree as audit


class PublicAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "PUBLIC_FILES.txt").write_text("PUBLIC_FILES.txt\nexample.txt\n")
        (self.root / "example.txt").write_text("Only synthetic public text.\n")
        # Keep all Git operations inside the synthetic repository, with no
        # inherited alternate index, worktree, hooks or personal Git settings.
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("GIT_")}
        environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        git_environment = patch.dict(os.environ, environment, clear=True)
        git_environment.start()
        self.addCleanup(git_environment.stop)

    def tearDown(self):
        self.temp.cleanup()

    def git(self, *args, input=None):
        return subprocess.run(["git", "-C", str(self.root), *args], input=input,
                              check=True, capture_output=True).stdout

    def stage_tree(self):
        self.git("init", "--quiet")
        self.git("add", "--", "PUBLIC_FILES.txt", "example.txt")

    def blob(self, content):
        return self.git("hash-object", "-w", "--stdin", input=content).decode().strip()

    def test_clean_tree_passes(self):
        self.assertEqual(audit.audit(self.root)["status"], "passed")

    def test_unlisted_data_is_rejected(self):
        (self.root / "private.json").write_text('{"synthetic":true}')
        self.assertEqual(audit.audit(self.root)["findings"][0]["kind"], "unexpected-file")

    def test_personal_path_and_credential_signature_report_no_contents(self):
        path = "/" + "Users" + "/" + "synthetic-person" + "/" + "private.txt"
        secret = "sk-" + "a" * 32
        (self.root / "example.txt").write_text(path + "\n" + secret)
        report = audit.audit(self.root)
        self.assertEqual({x["kind"] for x in report["findings"]}, {"personal-home-path", "openai-key"})
        self.assertNotIn(secret, str(report))
        self.assertNotIn(path, str(report))

    def test_symlink_is_rejected(self):
        (self.root / "example.txt").unlink()
        (self.root / "example.txt").symlink_to(self.root / "PUBLIC_FILES.txt")
        self.assertEqual(audit.audit(self.root)["findings"][0]["kind"], "non-regular-file")

    def test_clean_index_passes_with_existing_report_metadata(self):
        self.stage_tree()
        report = audit.audit(self.root, tracked=True)
        self.assertEqual(report, {
            "status": "passed", "mode": "tracked", "allowed_files": 2,
            "checked_files": 2, "findings": [],
            "scope": "allowlist and common accidental exposure signatures; not a full privacy guarantee",
        })

    def test_staged_secret_is_detected_after_working_copy_is_cleaned(self):
        secret = "sk-" + "a" * 32
        (self.root / "example.txt").write_text("Synthetic first line.\n" + secret)
        self.stage_tree()
        (self.root / "example.txt").write_text("Clean working copy.\n")
        report = audit.audit(self.root, tracked=True)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["findings"], [
            {"kind": "openai-key", "path": "example.txt", "line": 2}])
        self.assertNotIn(secret, str(report))
        self.assertEqual(audit.audit(self.root)["status"], "passed")

    def test_unstaged_secret_does_not_change_clean_index_report(self):
        self.stage_tree()
        (self.root / "example.txt").write_text("sk-" + "b" * 32)
        self.assertEqual(audit.audit(self.root, tracked=True)["status"], "passed")
        self.assertEqual(audit.audit(self.root)["status"], "failed")

    def test_staged_allowlist_omission_is_not_repaired_by_working_copy(self):
        (self.root / "PUBLIC_FILES.txt").write_text("PUBLIC_FILES.txt\n")
        self.stage_tree()
        (self.root / "PUBLIC_FILES.txt").write_text("PUBLIC_FILES.txt\nexample.txt\n")
        report = audit.audit(self.root, tracked=True)
        self.assertEqual(report["allowed_files"], 1)
        self.assertEqual(report["findings"], [
            {"kind": "unexpected-file", "path": "example.txt"}])
        self.assertEqual(audit.audit(self.root)["status"], "passed")

    def test_unstaged_allowlist_omission_does_not_change_index_report(self):
        self.stage_tree()
        (self.root / "PUBLIC_FILES.txt").write_text("PUBLIC_FILES.txt\n")
        self.assertEqual(audit.audit(self.root, tracked=True)["status"], "passed")
        self.assertEqual(audit.audit(self.root)["status"], "failed")

    def test_deleted_working_files_and_allowlist_do_not_change_index_report(self):
        self.stage_tree()
        (self.root / "example.txt").unlink()
        (self.root / "PUBLIC_FILES.txt").unlink()
        self.assertEqual(audit.audit(self.root, tracked=True)["status"], "passed")

    def test_working_symlinks_do_not_replace_staged_blobs(self):
        self.stage_tree()
        for name in ("example.txt", "PUBLIC_FILES.txt"):
            (self.root / name).unlink()
            (self.root / name).symlink_to("missing-synthetic-target")
        self.assertEqual(audit.audit(self.root, tracked=True)["status"], "passed")

    def test_staged_symlink_is_rejected_despite_regular_working_copy(self):
        (self.root / "example.txt").unlink()
        (self.root / "example.txt").symlink_to("PUBLIC_FILES.txt")
        self.stage_tree()
        (self.root / "example.txt").unlink()
        (self.root / "example.txt").write_text("Clean working copy.\n")
        self.assertEqual(audit.audit(self.root, tracked=True)["findings"], [
            {"kind": "non-regular-file", "path": "example.txt"}])

    def test_staged_allowlist_symlink_is_rejected(self):
        self.stage_tree()
        oid = self.blob(b"example.txt")
        self.git("update-index", "--cacheinfo", "120000", oid, "PUBLIC_FILES.txt")
        with self.assertRaises(ValueError):
            audit.audit(self.root, tracked=True)

    def test_missing_staged_allowlist_is_rejected(self):
        self.stage_tree()
        self.git("update-index", "--force-remove", "PUBLIC_FILES.txt")
        with self.assertRaises(ValueError):
            audit.audit(self.root, tracked=True)

    def test_staged_gitlink_is_rejected(self):
        self.stage_tree()
        oid = self.blob(b"Synthetic object used only for the index mode test.\n")
        self.git("update-index", "--cacheinfo", "160000", oid, "example.txt")
        self.assertEqual(audit.audit(self.root, tracked=True)["findings"], [
            {"kind": "non-regular-file", "path": "example.txt"}])

    def test_staged_executable_regular_file_is_allowed(self):
        self.stage_tree()
        self.git("update-index", "--chmod=+x", "example.txt")
        self.assertEqual(audit.audit(self.root, tracked=True)["status"], "passed")

    def test_regular_index_mode_requires_an_actual_blob(self):
        self.stage_tree()
        target = self.blob(b"Synthetic public blob.\n")
        tag = (f"object {target}\ntype blob\ntag synthetic\n"
               "tagger Synthetic <synthetic@example.invalid> 0 +0000\n\nSynthetic tag.\n")
        oid = self.git("mktag", input=tag.encode()).decode().strip()
        self.git("update-index", "--cacheinfo", "100644", oid, "example.txt")
        with self.assertRaises(ValueError):
            audit.audit(self.root, tracked=True)

    def test_unmerged_index_is_rejected_despite_clean_working_files(self):
        self.stage_tree()
        oid = self.blob(b"Synthetic conflict content.\n")
        self.git("update-index", "--force-remove", "example.txt")
        records = "".join(f"100644 {oid} {stage}\texample.txt\n" for stage in (1, 2, 3))
        self.git("update-index", "--index-info", input=records.encode())
        with self.assertRaises(ValueError):
            audit.audit(self.root, tracked=True)

    def test_invalid_staged_allowlist_cannot_be_hidden_by_working_copy(self):
        for invalid in ("../synthetic.txt", "/synthetic.txt", "example.txt\nexample.txt"):
            with self.subTest(invalid=invalid):
                (self.root / "PUBLIC_FILES.txt").write_text("PUBLIC_FILES.txt\n" + invalid + "\n")
                self.stage_tree()
                (self.root / "PUBLIC_FILES.txt").write_text("PUBLIC_FILES.txt\nexample.txt\n")
                with self.assertRaises(ValueError):
                    audit.audit(self.root, tracked=True)

    def test_staged_non_utf8_is_rejected_despite_clean_working_copy(self):
        (self.root / "example.txt").write_bytes(b"\xff\xfe")
        self.stage_tree()
        (self.root / "example.txt").write_text("Clean working copy.\n")
        self.assertEqual(audit.audit(self.root, tracked=True)["findings"], [
            {"kind": "non-utf8-file", "path": "example.txt"}])

    def test_repository_subdirectory_cannot_limit_tracked_audit_scope(self):
        self.stage_tree()
        nested = self.root / "nested"
        nested.mkdir()
        (nested / "PUBLIC_FILES.txt").write_text("PUBLIC_FILES.txt\n")
        with self.assertRaises(ValueError):
            audit.audit(nested, tracked=True)

    def test_replacement_refs_cannot_hide_staged_secret(self):
        secret = "sk-" + "c" * 32
        (self.root / "example.txt").write_text(secret)
        self.stage_tree()
        original = self.blob(secret.encode())
        replacement = self.blob(b"Clean replacement.\n")
        self.git("replace", original, replacement)
        self.assertEqual(audit.audit(self.root, tracked=True)["findings"], [
            {"kind": "openai-key", "path": "example.txt", "line": 1}])

    def test_blob_reads_use_captured_index_object_ids(self):
        secret = "sk-" + "d" * 32
        (self.root / "example.txt").write_text(secret)
        self.stage_tree()
        real_git_bytes = audit.git_bytes
        changed = False

        def change_index_after_snapshot(root, *args, **kwargs):
            nonlocal changed
            if args[0] == "cat-file" and not changed:
                changed = True
                (self.root / "example.txt").write_text("Clean next snapshot.\n")
                (self.root / "PUBLIC_FILES.txt").write_text("PUBLIC_FILES.txt\n")
                self.git("add", "--", "example.txt", "PUBLIC_FILES.txt")
            return real_git_bytes(root, *args, **kwargs)

        with patch.object(audit, "git_bytes", side_effect=change_index_after_snapshot):
            report = audit.audit(self.root, tracked=True)
        self.assertEqual(report["allowed_files"], 2)
        self.assertEqual(report["findings"], [
            {"kind": "openai-key", "path": "example.txt", "line": 1}])


if __name__ == "__main__":
    unittest.main()
