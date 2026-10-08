#!/usr/bin/env python3
"""Run a synthetic lifecycle in an automatically removed temporary workspace."""
import json
from pathlib import Path
import sys
import tempfile

import adapters
import context_library as lib
import reading_reviews as reviews
import source_verification
import workspace


def run_demo():
    with tempfile.TemporaryDirectory(prefix="ai-memory-demo-", dir=Path(tempfile.gettempdir()).resolve()) as temporary:
        target = Path(temporary) / "private-workspace"
        workspace.init_workspace(target, "demo")
        memory = workspace.startup(target, "demo")
        root = target / "AI-Context"
        payload = adapters.parse_transcript(workspace.REPO_ROOT / "examples/synthetic-conversation.json",
                                            "example", "synthetic-design-001", "Synthetic notebook design")
        imported = lib.publish(root, "demo", payload)
        verification = source_verification.verify_source(Path(imported["path"]), "demo")
        if verification["status"] != "verified-source-replay":
            raise lib.LibraryError("Synthetic source replay failed.")
        plan = reviews.create_review(root, "demo", "full")
        reviews.fetch(root, plan["review_id"], "synthetic-design-001", 1, 3)
        coverage = reviews.report(root, plan["review_id"])
        if coverage["plan_body_read_complete"]:
            raise lib.LibraryError("Fetching must not attest to actual reading.")
        return {"status": "passed", "fixture": "synthetic only",
                "startup_file_count": memory["file_count"],
                "archive_bodies_loaded_on_startup": memory["archive_bodies_loaded"],
                "source_replay": verification["status"], "record_count": 3,
                "reading_acknowledged": False, "temporary_data_removed": True}


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    print(json.dumps(run_demo(), ensure_ascii=False))
