"""Quality check for a generated synthetic neuroscience QA dataset.

Reads a ShareGPT-format JSONL produced by `generate_neuro_dataset.py` and
emits a Markdown report flagging:

  - Per-region and per-family example counts (uneven distribution = a region
    or family the LLM kept failing on)
  - Answer-length stats (too-short answers usually mean the model truncated;
    too-long ones often run off-topic)
  - Region-mention rate — the LLM should reference the region by name or
    abbreviation in every answer; if it doesn't, the answer is probably
    generic/off-topic
  - Network-mention rate — answers should reference the region's Yeo network
  - Suspicious patterns: empty answers, common refusal phrases, repeated
    phrases verbatim across examples (template collapse)

Usage::

    python -m scripts.validate_dataset --input data/cortex_train.jsonl

    # Custom output path + threshold tweaks
    python -m scripts.validate_dataset \\
        --input data/cortex_train.jsonl \\
        --report data/quality_report.md \\
        --min-answer-tokens 80
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Common LLM refusal / non-answer phrases. If we see these, the LLM dodged.
_REFUSAL_PHRASES = (
    "i'm sorry",
    "i cannot",
    "i can't",
    "as an ai",
    "as a language model",
    "i don't have",
    "i am unable",
)


@dataclass
class DatasetStats:
    n_total: int = 0
    n_per_region: Counter = field(default_factory=Counter)
    n_per_family: Counter = field(default_factory=Counter)
    n_per_region_family: dict[tuple[str, str], int] = field(default_factory=dict)
    answer_lengths_chars: list[int] = field(default_factory=list)
    answer_lengths_words: list[int] = field(default_factory=list)
    region_mention_hits: int = 0
    network_mention_hits: int = 0
    refusal_hits: int = 0
    empty_answers: int = 0
    too_short: int = 0
    too_long: int = 0
    duplicates: int = 0
    seen_id: set[str] = field(default_factory=set)
    duplicate_ids: list[str] = field(default_factory=list)

    @property
    def region_mention_rate(self) -> float:
        return self.region_mention_hits / self.n_total if self.n_total else 0.0

    @property
    def network_mention_rate(self) -> float:
        return self.network_mention_hits / self.n_total if self.n_total else 0.0

    @property
    def refusal_rate(self) -> float:
        return self.refusal_hits / self.n_total if self.n_total else 0.0


def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def analyze(
    path: Path,
    *,
    min_answer_words: int = 50,
    max_answer_words: int = 800,
) -> DatasetStats:
    """Walk the JSONL and accumulate stats."""
    if not path.exists():
        raise FileNotFoundError(f"dataset not found: {path}")

    stats = DatasetStats()

    for line_num, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue

        try:
            ex = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"WARN: {path}:{line_num} malformed JSON: {exc}", file=sys.stderr)
            continue

        ex_id = ex.get("id", f"unknown_{line_num}")
        if ex_id in stats.seen_id:
            stats.duplicates += 1
            stats.duplicate_ids.append(ex_id)
            continue
        stats.seen_id.add(ex_id)
        stats.n_total += 1

        meta = ex.get("metadata", {})
        region = meta.get("region", "?")
        family = meta.get("family", "?")
        network = meta.get("network", "?")
        stats.n_per_region[region] += 1
        stats.n_per_family[family] += 1
        stats.n_per_region_family[(region, family)] = (
            stats.n_per_region_family.get((region, family), 0) + 1
        )

        # Find the assistant turn
        answer = ""
        for turn in ex.get("conversations", []):
            if turn.get("from") == "assistant":
                answer = turn.get("value", "")
                break

        n_chars = len(answer)
        n_words = _word_count(answer)
        stats.answer_lengths_chars.append(n_chars)
        stats.answer_lengths_words.append(n_words)

        if n_chars == 0:
            stats.empty_answers += 1
        elif n_words < min_answer_words:
            stats.too_short += 1
        elif n_words > max_answer_words:
            stats.too_long += 1

        lower = answer.lower()

        # Region mention: by full name, by abbreviation (look at user prompt)
        region_lower = region.lower() if region else ""
        # Heuristic: extract the abbreviation in parentheses from the user prompt
        user_prompt = ""
        for turn in ex.get("conversations", []):
            if turn.get("from") == "user":
                user_prompt = turn.get("value", "")
                break
        abbr_match = re.search(r"\(([A-Za-z0-9/]{1,10})\)", user_prompt)
        abbr = abbr_match.group(1).lower() if abbr_match else ""

        mentions_region = (
            (region_lower and region_lower in lower)
            or (abbr and re.search(rf"\b{re.escape(abbr)}\b", lower) is not None)
        )
        if mentions_region:
            stats.region_mention_hits += 1

        # Network mention
        network_lower = (network or "").replace("_", " ").lower()
        if network_lower and (network_lower in lower or _network_alias_in(lower, network)):
            stats.network_mention_hits += 1

        # Refusal phrase
        if any(phrase in lower for phrase in _REFUSAL_PHRASES):
            stats.refusal_hits += 1

    return stats


def _network_alias_in(text: str, network: str) -> bool:
    """Allow common aliases for network names to count as mentions."""
    aliases = {
        "default_mode": ["dmn", "default-mode", "default mode network"],
        "frontoparietal": ["frontoparietal control", "central executive"],
        "ventral_attention": ["salience", "salience network", "van"],
        "dorsal_attention": ["dorsal attention network", "dan"],
        "somatomotor": ["motor cortex", "sensorimotor"],
    }.get(network, [])
    return any(a in text for a in aliases)


def render_report(stats: DatasetStats, *, source_path: Path, min_answer_words: int, max_answer_words: int) -> str:
    """Render the stats as a Markdown report."""
    if stats.n_total == 0:
        return f"# Dataset quality report\n\n**No examples found at `{source_path}`.**\n"

    lines: list[str] = []
    lines.append("# Dataset quality report")
    lines.append("")
    lines.append(f"**Source:** `{source_path}`")
    lines.append(f"**Total examples:** {stats.n_total}")
    lines.append(f"**Duplicates skipped:** {stats.duplicates}")
    lines.append("")

    # Region distribution
    lines.append("## Examples per region")
    lines.append("")
    lines.append("| Region | Count |")
    lines.append("| --- | ---: |")
    for region, count in stats.n_per_region.most_common():
        lines.append(f"| {region} | {count} |")
    lines.append("")

    # Family distribution
    lines.append("## Examples per template family")
    lines.append("")
    lines.append("| Family | Count |")
    lines.append("| --- | ---: |")
    for family, count in stats.n_per_family.most_common():
        lines.append(f"| `{family}` | {count} |")
    lines.append("")

    # Answer length
    if stats.answer_lengths_words:
        lengths = stats.answer_lengths_words
        lines.append("## Answer length (words)")
        lines.append("")
        lines.append(f"- Min: {min(lengths)}")
        lines.append(f"- Median: {statistics.median(lengths):.0f}")
        lines.append(f"- Mean: {statistics.mean(lengths):.0f}")
        lines.append(f"- Max: {max(lengths)}")
        if len(lengths) > 1:
            lines.append(f"- Stdev: {statistics.stdev(lengths):.0f}")
        lines.append("")

    # Quality flags
    lines.append("## Quality flags")
    lines.append("")
    lines.append("| Metric | Count | Rate |")
    lines.append("| --- | ---: | ---: |")
    lines.append(f"| Empty answers | {stats.empty_answers} | {stats.empty_answers / stats.n_total:.1%} |")
    lines.append(f"| Too short (< {min_answer_words} words) | {stats.too_short} | {stats.too_short / stats.n_total:.1%} |")
    lines.append(f"| Too long (> {max_answer_words} words) | {stats.too_long} | {stats.too_long / stats.n_total:.1%} |")
    lines.append(f"| Mentions target region | {stats.region_mention_hits} | {stats.region_mention_rate:.1%} |")
    lines.append(f"| Mentions Yeo network | {stats.network_mention_hits} | {stats.network_mention_rate:.1%} |")
    lines.append(f"| Refusal phrases | {stats.refusal_hits} | {stats.refusal_rate:.1%} |")
    lines.append("")

    # Diagnostic verdicts
    lines.append("## Verdict")
    lines.append("")
    issues: list[str] = []
    if stats.empty_answers > 0:
        issues.append(f"⚠️ {stats.empty_answers} empty answer(s) — re-run with `--retries-per-example 4`")
    if stats.region_mention_rate < 0.85:
        issues.append(
            f"⚠️ Region mention rate is {stats.region_mention_rate:.0%} (target ≥85%). "
            "Suggests answers are going off-topic; consider stricter system prompt."
        )
    if stats.refusal_rate > 0.02:
        issues.append(
            f"⚠️ Refusal rate is {stats.refusal_rate:.0%} (target ≤2%). "
            "Some answers are LLM dodges; review and regenerate flagged examples."
        )
    if stats.too_short / stats.n_total > 0.10:
        issues.append(
            f"⚠️ {stats.too_short / stats.n_total:.0%} of answers are below {min_answer_words} words. "
            "Increase `num_predict` in the backend config."
        )
    if stats.duplicates > 0:
        issues.append(
            f"⚠️ {stats.duplicates} duplicate ID(s) in the JSONL — clean before training."
        )

    if not issues:
        lines.append("✅ No critical issues. Dataset looks fit for fine-tuning.")
    else:
        lines.extend(issues)

    lines.append("")
    return "\n".join(lines)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Quality-check a generated neuro-QA dataset.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--input",
        type=Path,
        default=Path("data/cortex_train.jsonl"),
        help="Path to the JSONL dataset",
    )
    p.add_argument(
        "--report",
        type=Path,
        default=Path("data/dataset_quality_report.md"),
        help="Where to write the Markdown report",
    )
    p.add_argument("--min-answer-words", type=int, default=50)
    p.add_argument("--max-answer-words", type=int, default=800)
    p.add_argument("--print", action="store_true", help="Also print report to stdout")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    stats = analyze(
        args.input,
        min_answer_words=args.min_answer_words,
        max_answer_words=args.max_answer_words,
    )
    report = render_report(
        stats,
        source_path=args.input,
        min_answer_words=args.min_answer_words,
        max_answer_words=args.max_answer_words,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report, encoding="utf-8")
    print(f"[validate_dataset] wrote report to {args.report} ({stats.n_total} examples)", file=sys.stderr)
    if args.print:
        print(report)
    # Exit code: 1 if there are critical issues, 0 otherwise
    return 1 if (
        stats.empty_answers > 0
        or stats.region_mention_rate < 0.85
        or stats.refusal_rate > 0.02
    ) else 0


if __name__ == "__main__":
    sys.exit(main())


# Re-export for tests
__all__ = ["DatasetStats", "analyze", "main", "render_report"]


def _make_example(
    *,
    id: str,
    region: str,
    family: str,
    network: str,
    abbr: str,
    answer: str,
) -> dict[str, Any]:
    """Test-only helper. Used by tests/unit/test_validate_dataset.py."""
    return {
        "id": id,
        "conversations": [
            {"from": "system", "value": "..."},
            {"from": "user", "value": f"What does ({abbr}) do?"},
            {"from": "assistant", "value": answer},
        ],
        "metadata": {"region": region, "family": family, "network": network},
    }
