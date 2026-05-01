"""TRIBE v2 fine-tuning entry point (stub).

The real fine-tune loop lives downstream — this module is the dispatcher that:
  1. resolves a list of scan_ids,
  2. fetches signed-read URLs from the relay,
  3. downloads each scan into the local cache,
  4. prints the planned training run.

Replace `_run` with the actual training script when weights + dataloader land.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("cortex.train_tribe")

CACHE_DIR = Path(os.environ.get("CORTEX_TRIBE_CACHE", "tribev2_cache/fmri_uploads"))


def _plan(scan_ids: list[str]) -> dict:
    return {
        "model": "tribe-v2",
        "stage": "fine-tune",
        "scans": scan_ids,
        "n_scans": len(scan_ids),
        "cache_dir": str(CACHE_DIR),
        "lr": 1e-5,
        "epochs": 3,
        "batch_size": 1,
        "lora_r": 16,
        "fp_dtype": "bfloat16",
        "device": "cuda:0",
    }


def _run(scan_ids: list[str]) -> int:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    plan = _plan(scan_ids)
    print(json.dumps({"event": "training-plan", **plan}, indent=2))
    print(
        "(stub) real fine-tune runs once the dataloader and saved weights are wired in. "
        "No model state was modified."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TRIBE v2 fine-tune dispatcher")
    parser.add_argument("--scan-ids", nargs="+", required=True, help="Firestore scan ids")
    args = parser.parse_args(argv)
    return _run(args.scan_ids)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
