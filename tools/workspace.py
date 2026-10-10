#!/usr/bin/env python3
"""Create an explicit private workspace or read its compact project memory.

No provider discovery, network calls, global configuration, or archive body
reads occur here. Existing destinations are never overwritten.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

import context_library as lib

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = REPO_ROOT / "templates"
SCHEMA = "ai-memory-workspace/1.0"
PROJECT_FILES = (
    "project-manifest.json", "memory-index.md", "handoff.md",
    "working-agreement.md", "project-brief.md", "decisions.md",
    "findings.md", "session-log.md", "ai-self-evaluation.md",
)
STARTUP_FILES = (
    "project-manifest.json", "memory-index.md", "handoff.md",
    "working-agreement.md",
)


def project_name(value: str) -> str:
    if (not isinstance(value, str) or value != value.strip()
            or not re.fullmatch(r"[\w][\w .-]{0,95}", value)
            or value == "_template"):
        raise lib.LibraryError("Use a project name of 1..96 letters, digits, spaces, dots, underscores, or hyphens; _template is reserved.")
    return value


def explicit_workspace(path: Path, *, exists: bool) -> Path:
    if not path.is_absolute():
        raise lib.LibraryError("--workspace must be an absolute path.")
    path = lib.checked_path(path, must_exist=exists)
    if path.is_relative_to(REPO_ROOT):
        raise lib.LibraryError("Private data must live outside the public source repository.")
    # Avoid accidentally initializing private records in another Git checkout.
    if not exists and any((p / ".git").exists() for p in (path, *path.parents)):
        raise lib.LibraryError("Choose a private data directory outside a Git checkout.")
    return path


def render(relative: str, project: str = "") -> bytes:
    source = lib.regular_file(TEMPLATES / relative).read_text(encoding="utf-8")
    values = {"project": project, "date": datetime.now(timezone.utc).date().isoformat()}
    for key, value in values.items():
        source = source.replace("{{" + key + "}}", value)
    if "{{" in source:
        raise lib.LibraryError("An unresolved template field remains.")
    return source.encode("utf-8")


def project_payload(project: str) -> dict[str, bytes]:
    project_name(project)
    files = {name: render("memory/project/" + name, project) for name in PROJECT_FILES}
    manifest = json.loads(files["project-manifest.json"])
    if manifest["canonical_name"] != project or manifest["schema_version"] != "2.0":
        raise lib.LibraryError("Invalid project template identity.")
    return files


def write_files(root: Path, files: dict[str, bytes]) -> None:
    for name, data in files.items():
        destination = root / name
        missing = []
        parent = destination.parent
        while parent != root:
            missing.append(parent)
            parent = parent.parent
        for folder in reversed(missing):
            lib.private_directory(folder, create=True)
        lib.write_private(destination, data)


def init_workspace(path: Path, project: str) -> dict:
    path = explicit_workspace(path, exists=False)
    if path.exists():
        raise lib.LibraryError("Workspace already exists; initialization never overwrites it.")
    if not path.parent.is_dir():
        raise lib.LibraryError("Create the workspace parent directory first.")
    project_files = project_payload(project)
    memory = path / "AI-Memory"
    context = path / "AI-Context"
    files = {
        "AI-Memory/AI_MEMORY_CONTRACT.md": render("memory/AI_MEMORY_CONTRACT.md"),
        "AI-Memory/global/startup-core.md": render("memory/global/startup-core.md"),
    }
    files.update({f"AI-Memory/projects/{project}/{name}": data for name, data in project_files.items()})
    library = json.loads(render("context/library.json"))
    library["memory_root"] = str(memory)
    files["AI-Context/library.json"] = lib.canonical_bytes(library) + b"\n"
    files["AI-Context/sync-config.json"] = render("context/sync-config.json")
    files["AI-Context/AGENTS.md"] = render("context/AGENTS.md")
    files["AI-Memory/context-library.json"] = lib.canonical_bytes({
        "schema_version": "1.0", "library_root": str(context),
        "load_conversation_bodies_on_startup": False,
    }) + b"\n"
    # Exclusive creation claims an absent destination. If a write fails, the
    # partial directory is left visible for diagnosis; it is never overwritten.
    path.mkdir(mode=0o700)
    write_files(path, files)
    lib.write_private(path / "workspace.json", lib.canonical_bytes({
        "schema_version": SCHEMA, "memory_root": str(memory),
        "context_root": str(context),
    }) + b"\n")
    startup(path, project)
    return {"status": "created", "workspace": str(path), "project": project,
            "memory_root": str(memory), "context_root": str(context),
            "automatic_sync_enabled": False}


def load_workspace(path: Path) -> tuple[Path, Path, Path]:
    path = explicit_workspace(path, exists=True)
    lib.private_directory(path, create=False)
    marker = path / "workspace.json"
    lib.check_private_file(marker)
    data = lib.read_json(marker)
    memory, context = path / "AI-Memory", path / "AI-Context"
    if data != {"schema_version": SCHEMA, "memory_root": str(memory), "context_root": str(context)}:
        raise lib.LibraryError("Workspace metadata does not match this explicit location.")
    lib.private_directory(memory, create=False)
    return path, memory, context


def add_project(path: Path, project: str) -> dict:
    path, memory, _ = load_workspace(path)
    files = project_payload(project)
    parent = lib.private_directory(memory / "projects", create=False)
    destination = lib.checked_path(parent / project, must_exist=False)
    if destination.exists():
        raise lib.LibraryError("Project already exists; existing memory was preserved.")
    destination.mkdir(mode=0o700)
    write_files(destination, files)
    startup(path, project)
    return {"status": "created", "project": project}


def startup(path: Path, project: str) -> dict:
    path, memory, _ = load_workspace(path)
    project_name(project)
    folder = memory / "projects" / project
    lib.private_directory(folder, create=False)
    navigation = (memory / "global/navigation/moss.json", folder / "navigation.json")
    if any(source.exists() or source.is_symlink() for source in navigation):
        import moss_startup
        result = moss_startup.resolve(path / "workspace.json", project=project, bootstrap=True)
        if result.get("status") != "loaded":
            raise lib.LibraryError(json.dumps(result, ensure_ascii=False))
        contents = result.pop("contents")
        files = [{"path": name if name.startswith("global/") else f"projects/{project}/{name}",
                  "content": body} for name, body in contents.items()]
        return {**result, "project": project, "files": files, "file_count": len(files),
                "archive_bodies_loaded": False,
                "deferred_details": ["decisions.md", "findings.md", "session-log.md", "ai-self-evaluation.md"]}
    entries = [("global/startup-core.md", memory / "global/startup-core.md")]
    entries += [(f"projects/{project}/{name}", folder / name) for name in STARTUP_FILES]
    loaded = []
    for relative, source in entries:
        lib.check_private_file(source)
        loaded.append({"path": relative, "content": source.read_text(encoding="utf-8")})
    manifest = json.loads(loaded[1]["content"])
    if (manifest.get("schema_version") != "2.0"
            or manifest.get("canonical_name") != project
            or manifest.get("index_file") != "memory-index.md"):
        raise lib.LibraryError("Compact memory manifest schema or identity is invalid.")
    return {"status": "loaded", "project": project, "files": loaded,
            "file_count": len(loaded), "archive_bodies_loaded": False,
            "deferred_details": [x for x in PROJECT_FILES if x not in STARTUP_FILES]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "add-project", "startup"):
        command = commands.add_parser(name)
        command.add_argument("--workspace", required=True, type=Path)
        command.add_argument("--project", required=True)
    args = parser.parse_args(argv)
    try:
        action = {"init": init_workspace, "add-project": add_project, "startup": startup}[args.command]
        print(json.dumps(action(args.workspace, args.project), ensure_ascii=False))
        return 0
    except lib.LibraryError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
    except Exception as exc:
        print(json.dumps({"status": "error", "error_type": type(exc).__name__,
                          "detail": "Operation failed; existing data was not overwritten. Inspect any partial new directory before retrying."}), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    raise SystemExit(main())
