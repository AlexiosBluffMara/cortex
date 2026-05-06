"""register_cron.py — register the nightly_retrain.sh job with Mercury's
cron scheduler (mercury/cron/jobs.py). Picks one of three modes per --mode.

Usage:
    python scripts/register_cron.py --mode nightly         # 0 3 * * *
    python scripts/register_cron.py --mode threshold       # check every hour, retrain when >=100 new trajectories
    python scripts/register_cron.py --mode manual          # remove auto job; only Studio UI runs it

Requires Mercury's cron module on PYTHONPATH. Easiest way: run inside a
shell where mercury's repo root is the cwd (or PYTHONPATH includes it):

    PYTHONPATH=/mnt/d/mercury python /mnt/d/cortex/training/scripts/register_cron.py --mode nightly
"""
from __future__ import annotations

import argparse
import sys

JOB_NAME = "mercury-nightly-retrain"
JOB_PROMPT = (
    "Mercury nightly retrain job ran. Read the captured stdout above and "
    "summarize for the operator: how many new trajectories were extracted, "
    "whether training completed, what tok/s the smoke probes returned, and "
    "any errors. Keep it under 5 lines."
)
SCRIPT = "/mnt/d/cortex/training/scripts/nightly_retrain.sh"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", required=True, choices=["nightly", "threshold", "manual"])
    args = p.parse_args()

    try:
        from cron.jobs import create_job, list_jobs, remove_job
    except ImportError:
        print("ERROR: cron.jobs not importable. Run with PYTHONPATH=/mnt/d/mercury",
              file=sys.stderr)
        return 1

    # Remove any existing job by the same name first
    for job in list_jobs():
        if job.get("name") == JOB_NAME:
            print(f"removing existing job {job['id']}")
            remove_job(job["id"])

    if args.mode == "manual":
        print("manual mode: no cron job registered. Use studio.py or run nightly_retrain.sh by hand.")
        return 0

    if args.mode == "nightly":
        schedule = "0 3 * * *"
        descr = "every night at 03:00"
    else:  # threshold mode
        schedule = "0 * * * *"  # hourly check, the script's MIN_NEW gate decides
        descr = "hourly check, training fires when >= 100 new trajectories"

    job = create_job(
        prompt=JOB_PROMPT,
        schedule=schedule,
        name=JOB_NAME,
        repeat=None,
        deliver="local",
        script=SCRIPT,
    )
    print(f"registered job {job['id']} mode={args.mode} ({descr})")
    print(f"  script: {SCRIPT}")
    print(f"  schedule: {schedule}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
