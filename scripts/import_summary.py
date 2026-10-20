"""Print counts from an existing importer JSON report; never echo payloads."""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
        creates, updates, conflicts = report["creates"], report["updates"], report["conflicts"]
        if not isinstance(creates, dict) or not isinstance(updates, dict) or not isinstance(conflicts, list):
            raise ValueError("Invalid report structure")
        counts = [*creates.values(), *updates.values(), report["source_snapshots"]]
        if any(type(value) is not int or value < 0 for value in counts):
            raise ValueError("Invalid report counts")
        if report["mode"] not in {"apply", "dry-run"}:
            raise ValueError("Invalid report mode")
        digest = report["manifest_sha256"]
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("Invalid manifest digest")
    except (OSError, ValueError, KeyError, TypeError):
        parser.exit(2, "Cannot summarize report: unreadable file or invalid importer JSON.\n")
    print(json.dumps({
        "mode": report["mode"], "create_count": sum(creates.values()),
        "update_count": sum(updates.values()), "conflict_count": len(conflicts),
        "source_snapshots": report["source_snapshots"], "manifest_sha256": digest,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
