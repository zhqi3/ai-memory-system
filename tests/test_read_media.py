"""Read-only media projection checks using synthetic, private temporary data."""

import base64
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import context_library as lib


class ReadMediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="context-read-media-", dir=Path(tempfile.gettempdir()).resolve())
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "library"
        self.root.mkdir(mode=0o700)
        memory = base / "memory"
        project = memory / "projects" / "alpha"
        project.mkdir(parents=True)
        (project / "project-manifest.json").write_text(json.dumps({
            "schema_version": "2.0", "canonical_name": "alpha"}), encoding="utf-8")
        config = self.root / "library.json"
        config.write_text(json.dumps({"memory_root": str(memory)}), encoding="utf-8")
        config.chmod(0o600)
        self.encoded = base64.b64encode(bytes(range(256)) * 8).decode("ascii")
        self.uri = "data:image/png;base64," + self.encoded
        self.prose = "原始工具正文，不省略。\n" * 2000 + "URI 示例：" + self.uri + " 后面的文字。"
        self.tool_content = {"image": {"type": "image", "mimeType": "image/png", "data": self.encoded},
                             "text": self.prose, "uri": self.uri}
        self.payload = {
            "provider": "synthetic", "conversation_id": "media-chat", "title": "媒体读取测试",
            "records": [
                {"id": "user-1", "role": "user", "channel": "message", "timestamp": None,
                 "content": self.prose, "source_refs": [{"path": "/synthetic/source", "line": 1}]},
                {"id": "tool-2", "role": "tool", "channel": "tool", "timestamp": None,
                 "content": self.tool_content, "source_refs": [{"path": "/synthetic/source", "line": 2}]},
            ],
            "source": {"path": "/synthetic/source", "snapshot_sha256": "a" * 64},
            "completeness": {"scope": "synthetic-snapshot"},
        }
        self.imported = lib.publish(self.root, "alpha", self.payload)
        self.revision_path = Path(self.imported["path"])
        self.before = {path.name: path.read_bytes() for path in self.revision_path.iterdir()}

    def read(self, *extra):
        args = lib.build_parser().parse_args([
            "--root", str(self.root), "read", "--project", "alpha",
            "--conversation", "media-chat", "--revision", self.imported["revision"], *extra])
        return lib.run(args)

    def assert_storage_unchanged(self):
        self.assertEqual(self.before, {path.name: path.read_bytes() for path in self.revision_path.iterdir()})
        _, records = lib.validate_revision(self.revision_path, "alpha")
        self.assertEqual(records[1]["content"], self.tool_content)

    def test_default_read_projects_media_but_preserves_all_prose(self):
        result = self.read()
        self.assertEqual(result["media_view"], "display-projection")
        self.assertEqual(result["records"][0]["content"], self.prose)
        content = result["records"][1]["content"]
        self.assertEqual(content["text"], self.prose)
        self.assertEqual(content["image"]["data"]["preserved_in"], "messages.jsonl")
        self.assertEqual(content["image"]["data"]["sha256"], lib.digest(self.encoded.encode("utf-8")))
        self.assertEqual(content["uri"]["length"], len(self.uri))
        self.assertEqual(result["records"][1]["source_id"], "tool-2")
        self.assert_storage_unchanged()

    def test_explicit_include_media_returns_original_payloads(self):
        result = self.read("--include-media", "--start", "2", "--end", "2")
        self.assertEqual(result["media_view"], "original-inline-payloads")
        self.assertEqual(len(result["records"]), 1)
        self.assertEqual(result["records"][0]["ordinal"], 2)
        self.assertEqual(result["records"][0]["content"], self.tool_content)
        self.assertEqual(result["revision"], self.imported["revision"])
        self.assert_storage_unchanged()

    def test_base64_shaped_credential_cannot_use_media_redaction_exception(self):
        credential = "AKIA" + "A" * 16
        wrapped = "data:image/png;base64," + credential
        for value in (credential, wrapped):
            with self.subTest(shape="uri" if value == wrapped else "typed-data"):
                content = {"type": "image", "data": value}
                redactor = lib.Redactor()
                safe = redactor.value(content)
                self.assertNotIn(credential, safe["data"])
                self.assertIn("[REDACTED:aws-access-key]", safe["data"])
                self.assertEqual(redactor.inline_media_preserved, 0)
                self.assertEqual(redactor.counts["aws-access-key"], 1)
        # A coincidental credential-shaped sequence inside a larger valid
        # binary payload must not corrupt the original media string.
        larger_payload = "AAAA" + credential + "AAAA"
        content = {"type": "image", "data": larger_payload}
        redactor = lib.Redactor()
        self.assertEqual(redactor.value(content), content)
        self.assertEqual(redactor.inline_media_preserved, 1)


if __name__ == "__main__":
    unittest.main()
