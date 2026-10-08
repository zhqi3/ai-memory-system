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


def audit(root: Path, *, tracked: bool = False) -> dict:
    manifest = root / "PUBLIC_FILES.txt"
    lines = [line.strip() for line in manifest.read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.startswith("#")]
    if (len(set(lines)) != len(lines)
            or any(Path(p).is_absolute() or ".." in Path(p).parts for p in lines)):
        raise ValueError("Invalid public file allowlist")
    expected = set(lines)
    if tracked:
        output = subprocess.run(["git", "-C", str(root), "ls-files", "-z"],
                                check=True, capture_output=True).stdout
        actual = {p for p in output.decode("utf-8").split("\0") if p}
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
        if path.is_symlink() or not path.is_file():
            findings.append({"kind": "non-regular-file", "path": relative})
            continue
        try:
            content = path.read_text(encoding="utf-8")
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
    parser.add_argument("--tracked", action="store_true", help="Check files in the Git index after staging.")
    args = parser.parse_args()
    try:
        report = audit(ROOT, tracked=args.tracked)
        print(json.dumps(report))
        sys.exit(0 if report["status"] == "passed" else 1)
    except Exception as exc:
        print(json.dumps({"status": "error", "error_type": type(exc).__name__}), file=sys.stderr)
        sys.exit(1)
