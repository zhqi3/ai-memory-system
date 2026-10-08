"""Synthetic publication checks; no real secret or personal path is used."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import audit_public_tree as audit


class PublicAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "PUBLIC_FILES.txt").write_text("PUBLIC_FILES.txt\nexample.txt\n")
        (self.root / "example.txt").write_text("Only synthetic public text.\n")

    def tearDown(self):
        self.temp.cleanup()

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


if __name__ == "__main__":
    unittest.main()
