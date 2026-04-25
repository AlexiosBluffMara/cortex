"""Generate the synthetic neuroscience QA dataset for the Cortex fine-tune.

Output: ShareGPT-style JSONL, one example per line.

  {"id": "v1_activation_meaning_0007",
   "conversations": [
     {"from": "system",    "value": "..."},
     {"from": "user",      "value": "..."},
     {"from": "assistant", "value": "..."}
   ],
   "metadata": {"region": "Primary Visual Cortex", "network": "visual", ...}}

Usage::

    # Quick smoke run (5 per family per region, stub answers — finishes in ~1s)
    python -m scripts.generate_neuro_dataset \\
        --backend stub --n-per-family 5 --output data/neuro_smoke.jsonl

    # Real run on local Ollama (gemma4:31b)
    python -m scripts.generate_neuro_dataset \\
        --backend ollama:gemma4:31b --n-per-family 20 \\
        --output data/cortex_train.jsonl

    # Real run on Anthropic Claude (requires ANTHROPIC_API_KEY)
    python -m scripts.generate_neuro_dataset \\
        --backend anthropic:claude-sonnet-4-7 --n-per-family 20 \\
        --output data/cortex_train.jsonl

The full sprint target is `--n-per-family 20` × 5 families × 50 regions =
5,000 examples. With the current 20 regions in `regions.py` that's 2,000
examples; extend `regions.py` to reach the full 50.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.backends import LLMBackend, make_backend
from scripts.regions import REGIONS, all_networks_covered
from scripts.templates import SYSTEM_PROMPT, TemplateInstance, per_region_examples

# ---------------------------------------------------------------------------
# Output format
# ---------------------------------------------------------------------------

@dataclass
class DatasetExample:
    id: str
    conversations: list[dict[str, str]]
    metadata: dict[str, Any]

    def to_jsonl(self) -> str:
        return json.dumps(
            {
                "id": self.id,
                "conversations": self.conversations,
                "metadata": self.metadata,
            },
            ensure_ascii=False,
        )


def build_example(
    instance: TemplateInstance, answer: str, *, family_index: int
) -> DatasetExample:
    family_value = instance.family.value
    region_slug = instance.region_abbr.lower().replace(" ", "_").replace("/", "_")
    return DatasetExample(
        id=f"{region_slug}_{family_value}_{family_index:04d}",
        conversations=[
            {"from": "system", "value": SYSTEM_PROMPT},
            {"from": "user", "value": instance.user_prompt},
            {"from": "assistant", "value": answer},
        ],
        metadata=dict(instance.metadata) | {"family": family_value},
    )


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

@dataclass
class GeneratorConfig:
    backend: LLMBackend
    n_per_family: int
    seed: int
    output_path: Path
    region_filter: tuple[str, ...] = ()    # empty = all
    on_progress: Any = None                # optional callable(idx, total, current_id)
    # ---- Supervisor mode (used by `--supervised` for unattended runs) ----
    resume: bool = False                   # skip examples whose IDs already exist
    max_runtime_s: float | None = None     # stop cleanly after this many seconds
    retries_per_example: int = 1           # >1 enables exponential-backoff retry
    log_path: Path | None = None           # append a structured per-example log
    health_check: Any = None               # callable() -> bool, polled before each region


def _read_existing_ids(path: Path) -> set[str]:
    """Return the set of IDs already present in `path` (empty if missing)."""
    if not path.exists():
        return set()
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            seen.add(json.loads(line)["id"])
        except (json.JSONDecodeError, KeyError):
            continue
    return seen


def _generate_with_retry(
    backend: LLMBackend,
    system: str,
    user: str,
    *,
    max_attempts: int,
    log: _Logger,
) -> str | None:
    """Call backend.generate with exponential-backoff retry. Returns None on
    final failure so the supervisor can skip the example without aborting."""
    delay = 0.5
    last_err: str = ""
    for attempt in range(1, max_attempts + 1):
        try:
            answer = backend.generate(system, user)
            if answer and answer.strip():
                return answer
            last_err = "empty response"
        except Exception as exc:
            last_err = f"{exc.__class__.__name__}: {exc}"
        log.warn(f"attempt {attempt}/{max_attempts}: {last_err}")
        if attempt < max_attempts:
            time.sleep(min(delay, 30.0))
            delay *= 2
    return None


class _Logger:
    """Tiny structured logger that writes JSONL events to disk + stderr."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    def _emit(self, level: str, msg: str, **fields: Any) -> None:
        record = {"t": time.time(), "level": level, "msg": msg, **fields}
        line = json.dumps(record, ensure_ascii=False)
        if self.path is not None:
            try:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except OSError:
                pass
        print(f"[neuro_supervisor] {level} {msg}", file=sys.stderr)

    def info(self, msg: str, **fields: Any) -> None: self._emit("INFO", msg, **fields)
    def warn(self, msg: str, **fields: Any) -> None: self._emit("WARN", msg, **fields)
    def error(self, msg: str, **fields: Any) -> None: self._emit("ERROR", msg, **fields)


def generate(config: GeneratorConfig) -> int:
    """Run the generator. Returns the number of examples written.

    In default mode (no resume, no retry, no deadline) this is the original
    SPEC §8 behavior: full overwrite, single-shot LLM call per example.

    With `resume=True`, existing IDs in `output_path` are loaded and skipped;
    the file is opened in append mode. With `retries_per_example > 1`, each
    LLM call gets exponential-backoff retries. With `max_runtime_s`, the run
    exits cleanly when the budget is exhausted.
    """
    if not all_networks_covered():
        print("WARNING: not every Yeo network has a region defined.", file=sys.stderr)

    if config.region_filter:
        wanted = {a.casefold() for a in config.region_filter}
        regions = tuple(r for r in REGIONS if r.abbreviation.casefold() in wanted)
        if not regions:
            raise ValueError(
                f"No regions matched filter {config.region_filter!r}. "
                f"Available: {[r.abbreviation for r in REGIONS]}"
            )
    else:
        regions = REGIONS

    rng = random.Random(config.seed)
    config.output_path.parent.mkdir(parents=True, exist_ok=True)
    log = _Logger(config.log_path)

    existing_ids = _read_existing_ids(config.output_path) if config.resume else set()
    if existing_ids:
        log.info(
            f"resume: loaded {len(existing_ids)} existing IDs from {config.output_path}",
            existing=len(existing_ids),
        )

    family_counter: dict[tuple[str, str], int] = {}

    n_total = len(regions) * config.n_per_family * 5
    written = 0
    skipped_existing = 0
    skipped_failed = 0
    t0 = time.time()
    deadline = (t0 + config.max_runtime_s) if config.max_runtime_s else None

    file_mode = "a" if config.resume else "w"
    with config.output_path.open(file_mode, encoding="utf-8") as fh:
        for region in regions:
            if deadline is not None and time.time() > deadline:
                log.info(f"deadline hit after {region.abbreviation}; stopping cleanly")
                break
            if config.health_check is not None and not config.health_check():
                log.warn(f"health check failed before region {region.abbreviation}; sleeping 30s")
                time.sleep(30)
                if config.health_check is not None and not config.health_check():
                    log.error("health check still failing after 30s; aborting")
                    break

            instances = list(
                per_region_examples(
                    region,
                    n_per_family=config.n_per_family,
                    rng=rng,
                    partner_pool=REGIONS,
                )
            )
            for instance in instances:
                if deadline is not None and time.time() > deadline:
                    break
                key = (instance.region_abbr, instance.family.value)
                idx = family_counter.get(key, 0)
                family_counter[key] = idx + 1

                # Synthesize the would-be ID and skip if already present
                provisional = build_example(instance, "", family_index=idx)
                if provisional.id in existing_ids:
                    skipped_existing += 1
                    continue

                answer = _generate_with_retry(
                    config.backend,
                    SYSTEM_PROMPT,
                    instance.user_prompt,
                    max_attempts=max(1, config.retries_per_example),
                    log=log,
                )
                if answer is None:
                    skipped_failed += 1
                    log.error(f"giving up on {provisional.id} after retries", id=provisional.id)
                    continue

                example = build_example(instance, answer, family_index=idx)
                fh.write(example.to_jsonl() + "\n")
                fh.flush()  # durability — supervised runs may be killed mid-write
                written += 1
                if config.on_progress is not None:
                    config.on_progress(written, n_total, example.id)

    elapsed = time.time() - t0
    rate = written / elapsed if elapsed > 0 else 0
    summary = (
        f"Wrote {written} examples to {config.output_path} "
        f"in {elapsed:.1f}s ({rate:.1f} ex/s) "
        f"using backend={config.backend.name} "
        f"(skipped {skipped_existing} existing, {skipped_failed} failed)"
    )
    print(summary, file=sys.stderr)
    log.info(
        "generation finished",
        written=written, skipped_existing=skipped_existing,
        skipped_failed=skipped_failed, elapsed_s=round(elapsed, 1),
    )
    return written


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate the synthetic neuroscience QA dataset for Cortex.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--backend",
        default="stub",
        help="Backend spec: stub | ollama[:model] | anthropic[:model]",
    )
    p.add_argument(
        "--n-per-family",
        type=int,
        default=20,
        help="Examples per template family per region (default 20 = SPEC §8 target)",
    )
    p.add_argument("--seed", type=int, default=42)
    p.add_argument(
        "--output",
        type=Path,
        default=Path("data/cortex_train.jsonl"),
        help="Output JSONL path",
    )
    p.add_argument(
        "--regions",
        nargs="+",
        default=[],
        help="Restrict to specific region abbreviations (e.g. V1 FFA M1)",
    )
    p.add_argument("--quiet", action="store_true", help="Suppress per-example progress")
    # ---- Supervisor flags (unattended runs) ----
    p.add_argument(
        "--resume",
        action="store_true",
        help="Skip examples whose IDs already exist in --output (append mode)",
    )
    p.add_argument(
        "--max-runtime-min",
        type=float,
        default=None,
        help="Stop cleanly after this many minutes",
    )
    p.add_argument(
        "--retries-per-example",
        type=int,
        default=1,
        help="Retry failed LLM calls up to N times with exponential backoff (default 1 = no retry)",
    )
    p.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="Append per-event JSONL log to this path",
    )
    p.add_argument(
        "--supervised",
        action="store_true",
        help="Sensible defaults for unattended runs: --resume, "
             "--retries-per-example=4, log to data/supervisor.log, "
             "fail-loud on Ollama health check.",
    )
    return p.parse_args(argv)


def _ollama_health_check_factory(backend_spec: str) -> Any:
    """Return a `() -> bool` health-checker for the given backend.

    For Ollama we hit /api/version. For the stub or Anthropic backends, we
    skip the check (always returns True).
    """
    if not backend_spec.startswith("ollama"):
        return None

    import os

    import requests
    url = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")

    def _check() -> bool:
        try:
            r = requests.get(f"{url}/api/version", timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    return _check


def _make_progress_printer(quiet: bool):
    if quiet:
        return None
    last_print = [0.0]

    def _on(written: int, total: int, current_id: str) -> None:
        now = time.time()
        if now - last_print[0] < 0.5 and written < total:
            return
        last_print[0] = now
        pct = 100 * written / total if total else 0
        print(
            f"\r[neuro_dataset] {written}/{total} ({pct:.1f}%) {current_id}",
            end="",
            file=sys.stderr,
            flush=True,
        )
        if written >= total:
            print(file=sys.stderr)

    return _on


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    backend = make_backend(args.backend)

    # Supervised mode: opt into all the unattended-friendly defaults.
    resume = args.resume or args.supervised
    retries = max(args.retries_per_example, 4 if args.supervised else 1)
    log_file = args.log_file or (Path("data/supervisor.log") if args.supervised else None)
    health_check = _ollama_health_check_factory(args.backend) if args.supervised else None

    config = GeneratorConfig(
        backend=backend,
        n_per_family=args.n_per_family,
        seed=args.seed,
        output_path=args.output,
        region_filter=tuple(args.regions),
        on_progress=_make_progress_printer(args.quiet),
        resume=resume,
        max_runtime_s=(args.max_runtime_min * 60.0) if args.max_runtime_min else None,
        retries_per_example=retries,
        log_path=log_file,
        health_check=health_check,
    )
    generate(config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
