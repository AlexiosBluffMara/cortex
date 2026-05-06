"""extract_trajectories.py
Gather Mercury ShareGPT trajectories from one or more source dirs into a single
deduped JSONL training file. Filters to completed=true by default.

Mercury writes trajectory_samples.jsonl (completed) and failed_trajectories.jsonl
(incomplete) per agent/trajectory.py. Looks for both filenames recursively.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Iterable


def iter_jsonl(path: Path) -> Iterable[dict]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def fingerprint(entry: dict) -> str:
    convs = entry.get("conversations") or []
    blob = json.dumps(convs, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--src", nargs="+", required=True, help="One or more dirs to scan recursively")
    p.add_argument("--out", required=True, help="Output JSONL path")
    p.add_argument("--include-failed", action="store_true",
                   help="Also include failed_trajectories.jsonl entries")
    p.add_argument("--min-turns", type=int, default=4,
                   help="Drop trajectories with fewer than N conversation turns")
    args = p.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    target_names = {"trajectory_samples.jsonl"}
    if args.include_failed:
        target_names.add("failed_trajectories.jsonl")

    seen: set[str] = set()
    kept = 0
    skipped_dup = 0
    skipped_short = 0

    with out_path.open("w", encoding="utf-8") as out:
        for src in args.src:
            root = Path(src).expanduser()
            if not root.exists():
                print(f"WARN: source missing: {root}", file=sys.stderr)
                continue
            for path in root.rglob("*.jsonl"):
                if path.name not in target_names:
                    continue
                for entry in iter_jsonl(path):
                    convs = entry.get("conversations") or []
                    if len(convs) < args.min_turns:
                        skipped_short += 1
                        continue
                    fp = fingerprint(entry)
                    if fp in seen:
                        skipped_dup += 1
                        continue
                    seen.add(fp)
                    out.write(json.dumps({"conversations": convs},
                                         ensure_ascii=False) + "\n")
                    kept += 1

    # Refresh "LATEST" symlink for trainer convenience
    latest = out_path.parent / "mercury-LATEST.jsonl"
    try:
        if latest.exists() or latest.is_symlink():
            latest.unlink()
        os.symlink(out_path.name, latest)
    except OSError as exc:
        print(f"WARN: could not create LATEST symlink: {exc}", file=sys.stderr)

    print(f"kept={kept}  dup={skipped_dup}  too-short={skipped_short}  -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
