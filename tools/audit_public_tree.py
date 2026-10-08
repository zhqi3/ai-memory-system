#!/usr/bin/env python3
"""Check the explicit public file allowlist without printing file contents.

This is an accidental-publication guard, not comprehensive secret detection or
an anonymizer. Review changed content and Git history separately before release.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import context_library as lib

ROOT = Path(__file__).resolve().parents[1]
IGNORED = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".DS_Store"}
HOME_PATH = re.compile(r"(?:/Users/|/home/)[A-Za-z0-9_.-]+/|[A-Za-z]:\\Users\\[A-Za-z0-9_.-]+\\")
REGULAR_MODES = {"100644", "100755"}


def git_bytes(root: Path, *args: str, input: bytes | None = None) -> bytes:
    # Object IDs captured from the index must refer to their actual bytes, even
    # when the repository has local replacement refs.
    return subprocess.run(["git", "--no-replace-objects", "-C", str(root), *args],
                          input=input, check=True, capture_output=True).stdout


def index_blob(root: Path, oid: str) -> bytes:
    output = git_bytes(root, "cat-file", "--batch", input=(oid + "\n").encode("ascii"))
    header, _, content = output.partition(b"\n")
    fields = header.split()
    # Batch mode reports the object's real type without peeling a tag to its
    # target. An index entry claiming a regular mode must reference a blob.
    if (len(fields) != 3 or fields[0] != oid.encode("ascii") or fields[1] != b"blob"
            or not fields[2].isdigit() or len(content) != int(fields[2]) + 1
            or not content.endswith(b"\n")):
        raise ValueError("Invalid blob in Git index")
    return content[:-1]


def index_snapshot(root: Path) -> dict[str, tuple[str, str]]:
    root = root.resolve()
    if git_bytes(root, "rev-parse", "--show-toplevel") != os.fsencode(root) + b"\n":
        raise ValueError("Tracked audit requires the repository root")
    entries = {}
    for record in git_bytes(root, "ls-files", "--stage", "--full-name", "-z").split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, oid, stage = metadata.decode("ascii").split()
        relative = raw_path.decode("utf-8")
        if (Path(relative).is_absolute() or relative in {"", "."}
                or any(part in {"", ".", ".."} for part in relative.split("/"))):
            raise ValueError("Invalid Git index path")
        if stage != "0" or relative in entries:
            raise ValueError("Unmerged Git index")
        entries[relative] = (mode, oid)
    return entries


def audit(root: Path, *, tracked: bool = False) -> dict:
    manifest = root / "PUBLIC_FILES.txt"
    entries = index_snapshot(root) if tracked else None
    if entries is not None:
        manifest_entry = entries.get("PUBLIC_FILES.txt")
        if manifest_entry is None or manifest_entry[0] not in REGULAR_MODES:
            raise ValueError("Missing or non-regular public file allowlist in Git index")
        manifest_text = index_blob(root, manifest_entry[1]).decode("utf-8")
    else:
        manifest_text = manifest.read_text(encoding="utf-8")
    lines = [line.strip() for line in manifest_text.splitlines()
             if line.strip() and not line.startswith("#")]
    if (len(set(lines)) != len(lines)
            or any(Path(p).is_absolute() or ".." in Path(p).parts for p in lines)):
        raise ValueError("Invalid public file allowlist")
    expected = set(lines)
    if entries is not None:
        actual = set(entries)
    else:
        actual = set()
        for folder, dirs, files in os.walk(root, followlinks=False):
            for name in dirs:
                path = Path(folder) / name
                if name not in IGNORED and path.is_symlink():
                    actual.add(path.relative_to(root).as_posix())
            dirs[:] = [name for name in dirs if name not in IGNORED]
            actual.update((Path(folder) / name).relative_to(root).as_posix()
                          for name in files if name not in IGNORED)
    findings = [{"kind": "unexpected-file", "path": p} for p in sorted(actual - expected)]
    findings += [{"kind": "missing-file", "path": p} for p in sorted(expected - actual)]
    for relative in sorted(actual & expected):
        path = root / relative
        if ((entries is not None and entries[relative][0] not in REGULAR_MODES)
                or (entries is None and (path.is_symlink() or not path.is_file()))):
            findings.append({"kind": "non-regular-file", "path": relative})
            continue
        try:
            content = (index_blob(root, entries[relative][1]).decode("utf-8")
                       if entries is not None else path.read_text(encoding="utf-8"))
        except UnicodeError:
            findings.append({"kind": "non-utf8-file", "path": relative})
            continue
        for kind, pattern in (("personal-home-path", HOME_PATH), *lib.REDACTION_PATTERNS):
            for match in pattern.finditer(content):
                findings.append({"kind": kind, "path": relative,
                                 "line": content.count("\n", 0, match.start()) + 1})
    return {"status": "passed" if not findings else "failed", "mode": "tracked" if tracked else "working-tree",
            "allowed_files": len(expected), "checked_files": len(actual & expected), "findings": findings,
            "scope": "allowlist and common accidental exposure signatures; not a full privacy guarantee"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracked", action="store_true",
                        help="Check the staged allowlist, file modes and blob contents from the Git index.")
    args = parser.parse_args()
    try:
        report = audit(ROOT, tracked=args.tracked)
        print(json.dumps(report))
        sys.exit(0 if report["status"] == "passed" else 1)
    except Exception as exc:
        print(json.dumps({"status": "error", "error_type": type(exc).__name__}), file=sys.stderr)
        sys.exit(1)
