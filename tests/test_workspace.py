"""Public setup and isolation regressions using only synthetic temporary files."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import context_library as lib
import workspace
import demo


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.base = Path(self.temp.name)
        self.root = self.base / "data"

    def tearDown(self):
        self.temp.cleanup()

    def initialize(self):
        return workspace.init_workspace(self.root, "demo")

    def snapshot(self):
        return {str(p.relative_to(self.root)): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                for p in self.root.rglob("*") if p.is_file()}

    def test_init_private_modes_and_sync_disabled(self):
        result = self.initialize()
        self.assertFalse(result["automatic_sync_enabled"])
        for path in [self.root, *self.root.rglob("*")]:
            self.assertEqual(path.stat().st_mode & 0o777, 0o700 if path.is_dir() else 0o600)
        settings = json.loads((self.root / "AI-Context/sync-config.json").read_text())
        self.assertFalse(settings["enabled"])
        self.assertEqual(settings["sources"], [])
        self.assertEqual(settings["codex_discovery_roots"], [])

    def test_startup_reads_only_five_compact_files_and_is_read_only(self):
        self.initialize()
        before = self.snapshot()
        # Even an inaccessible/missing archive cannot cause body reads at startup.
        (self.root / "AI-Context").rename(self.root / "archive-unavailable")
        actual_read = Path.read_text
        accessed = []
        def observed(path, *args, **kwargs):
            accessed.append(path.relative_to(self.root).as_posix())
            return actual_read(path, *args, **kwargs)
        with patch.object(Path, "read_text", observed):
            result = workspace.startup(self.root, "demo")
        self.assertEqual(result["file_count"], 5)
        self.assertFalse(result["archive_bodies_loaded"])
        self.assertEqual(len(accessed), 6)  # workspace metadata plus five memory files
        self.assertFalse(any(x.startswith("AI-Context/") or x.startswith("archive-unavailable/") for x in accessed))
        (self.root / "archive-unavailable").rename(self.root / "AI-Context")
        self.assertEqual(self.snapshot(), before)

    def test_existing_destination_is_unchanged(self):
        self.initialize()
        before = self.snapshot()
        with self.assertRaises(lib.LibraryError):
            self.initialize()
        self.assertEqual(before, self.snapshot())

    def test_project_addition_preserves_first_project(self):
        self.initialize()
        before = self.snapshot()
        workspace.add_project(self.root, "second project")
        after = self.snapshot()
        self.assertTrue(all(after[key] == value for key, value in before.items()))
        self.assertEqual(workspace.startup(self.root, "second project")["project"], "second project")
        with self.assertRaises(lib.LibraryError):
            workspace.add_project(self.root, "second project")

    def test_invalid_project_cannot_escape_or_partially_initialize(self):
        for name in ("../escape", "_template", "", "a/b", "trailing ", 'quote"', "line\nbreak"):
            with self.subTest(name=name), self.assertRaises(lib.LibraryError):
                workspace.init_workspace(self.root, name)
            self.assertFalse(self.root.exists())

    def test_symlink_parent_is_rejected(self):
        alias = self.base / "alias"
        alias.symlink_to(self.base, target_is_directory=True)
        with self.assertRaises(lib.LibraryError):
            workspace.init_workspace(alias / "new", "demo")

    def test_symlink_memory_file_is_rejected(self):
        self.initialize()
        handoff = self.root / "AI-Memory/projects/demo/handoff.md"
        outside = self.base / "unrelated.md"
        outside.write_text("synthetic unrelated data")
        handoff.unlink()
        handoff.symlink_to(outside)
        with self.assertRaises(lib.LibraryError):
            workspace.startup(self.root, "demo")

    def test_repo_and_relative_destinations_rejected(self):
        for destination in (Path("relative-data"), workspace.REPO_ROOT / "private-test-data"):
            with self.assertRaises(lib.LibraryError):
                workspace.init_workspace(destination, "demo")
        checkout = self.base / "another-repository"
        (checkout / ".git").mkdir(parents=True)
        with self.assertRaises(lib.LibraryError):
            workspace.init_workspace(checkout / "data", "demo")

    def test_manifest_identity_and_workspace_redirection_rejected(self):
        self.initialize()
        manifest = self.root / "AI-Memory/projects/demo/project-manifest.json"
        manifest.write_text(json.dumps({"schema_version": "2.0", "canonical_name": "wrong", "index_file": "memory-index.md"}))
        with self.assertRaises(lib.LibraryError):
            workspace.startup(self.root, "demo")
        marker = self.root / "workspace.json"
        data = json.loads(marker.read_text())
        data["memory_root"] = str(self.base)
        marker.write_text(json.dumps(data))
        with self.assertRaises(lib.LibraryError):
            workspace.startup(self.root, "demo")

    def test_cli_works_from_unrelated_cwd_with_empty_home(self):
        home = self.base / "empty-home"
        home.mkdir()
        env = dict(os.environ, HOME=str(home), PYTHONDONTWRITEBYTECODE="1")
        # No global config or provider session directory exists in this HOME.
        command = [sys.executable, "-B", str(workspace.REPO_ROOT / "tools/workspace.py")]
        for operation in ("init", "startup"):
            result = subprocess.run([*command, operation, "--workspace", str(self.root), "--project", "demo"],
                                    cwd=self.base, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(json.loads(result.stdout)["status"], {"created", "loaded"})
        self.assertEqual(list(home.iterdir()), [])

    def test_archive_cli_requires_explicit_root(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            lib.build_parser().parse_args(["list"])
        self.assertEqual(error.exception.code, 2)

    def test_demo_uses_synthetic_source_and_never_acknowledges(self):
        result = demo.run_demo()
        self.assertEqual(result["source_replay"], "verified-source-replay")
        self.assertFalse(result["reading_acknowledged"])
        self.assertTrue(result["temporary_data_removed"])


if __name__ == "__main__":
    unittest.main()
