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
    region_filter: tuple[str, ...] = ()  # empty = all
    on_progress: Any = None              # optional callable(idx, total, current_id)


def generate(config: GeneratorConfig) -> int:
    """Run the generator. Returns the number of examples written."""
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

    # Per-region family counters, so IDs are stable across reruns at the same seed
    family_counter: dict[tuple[str, str], int] = {}

    n_total = len(regions) * config.n_per_family * 5  # 5 template families
    written = 0
    t0 = time.time()

    with config.output_path.open("w", encoding="utf-8") as fh:
        for region in regions:
            instances = list(
                per_region_examples(
                    region,
                    n_per_family=config.n_per_family,
                    rng=rng,
                    partner_pool=REGIONS,
                )
            )
            for instance in instances:
                key = (instance.region_abbr, instance.family.value)
                idx = family_counter.get(key, 0)
                family_counter[key] = idx + 1

                answer = config.backend.generate(SYSTEM_PROMPT, instance.user_prompt)
                example = build_example(instance, answer, family_index=idx)
                fh.write(example.to_jsonl() + "\n")
                written += 1
                if config.on_progress is not None:
                    config.on_progress(written, n_total, example.id)

    elapsed = time.time() - t0
    rate = written / elapsed if elapsed > 0 else 0
    print(
        f"Wrote {written} examples to {config.output_path} "
        f"in {elapsed:.1f}s ({rate:.1f} ex/s) "
        f"using backend={config.backend.name}",
        file=sys.stderr,
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
    return p.parse_args(argv)


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
    config = GeneratorConfig(
        backend=backend,
        n_per_family=args.n_per_family,
        seed=args.seed,
        output_path=args.output,
        region_filter=tuple(args.regions),
        on_progress=_make_progress_printer(args.quiet),
    )
    generate(config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
