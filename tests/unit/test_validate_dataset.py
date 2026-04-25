"""Tests for scripts/validate_dataset.py — dataset quality checker."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.validate_dataset import (
    DatasetStats,
    _make_example,
    analyze,
    render_report,
)

pytestmark = pytest.mark.unit


def _write_dataset(path: Path, examples: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for ex in examples:
            fh.write(json.dumps(ex) + "\n")


# ---------------------------------------------------------------------------
# analyze()
# ---------------------------------------------------------------------------

class TestAnalyzeShape:
    def test_empty_file_returns_zero_total(self, tmp_path: Path):
        path = tmp_path / "empty.jsonl"
        path.write_text("", encoding="utf-8")
        stats = analyze(path)
        assert stats.n_total == 0

    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            analyze(tmp_path / "nope.jsonl")

    def test_per_region_counts(self, tmp_path: Path):
        path = tmp_path / "ds.jsonl"
        _write_dataset(path, [
            _make_example(
                id=f"v1_activation_meaning_{i:04d}",
                region="Primary Visual Cortex",
                family="activation_meaning",
                network="visual",
                abbr="V1",
                answer=" ".join(["x"] * 100),  # 100 words
            )
            for i in range(3)
        ] + [
            _make_example(
                id="ffa_clinical_0001",
                region="Fusiform Face Area",
                family="clinical",
                network="visual",
                abbr="FFA",
                answer=" ".join(["x"] * 100),
            )
        ])
        stats = analyze(path)
        assert stats.n_total == 4
        assert stats.n_per_region["Primary Visual Cortex"] == 3
        assert stats.n_per_region["Fusiform Face Area"] == 1
        assert stats.n_per_family["activation_meaning"] == 3
        assert stats.n_per_family["clinical"] == 1


class TestAnalyzeQualityFlags:
    def _example(self, *, answer: str, abbr: str = "V1") -> dict:
        return _make_example(
            id=f"v1_test_{hash(answer) % 10000:04d}",
            region="Primary Visual Cortex",
            family="activation_meaning",
            network="visual",
            abbr=abbr,
            answer=answer,
        )

    def test_empty_answer_flagged(self, tmp_path: Path):
        path = tmp_path / "ds.jsonl"
        _write_dataset(path, [self._example(answer="")])
        stats = analyze(path)
        assert stats.empty_answers == 1
        assert stats.region_mention_hits == 0

    def test_too_short_flagged(self, tmp_path: Path):
        path = tmp_path / "ds.jsonl"
        _write_dataset(path, [self._example(answer="V1 processes vision.")])  # 3 words
        stats = analyze(path, min_answer_words=10)
        assert stats.too_short == 1
        assert stats.empty_answers == 0

    def test_too_long_flagged(self, tmp_path: Path):
        path = tmp_path / "ds.jsonl"
        _write_dataset(path, [self._example(answer="V1 word " * 1000)])  # ~2000 words
        stats = analyze(path, max_answer_words=500)
        assert stats.too_long == 1

    def test_region_mention_by_full_name(self, tmp_path: Path):
        path = tmp_path / "ds.jsonl"
        _write_dataset(path, [self._example(answer="The primary visual cortex is responsible for " + "x " * 60)])
        stats = analyze(path)
        assert stats.region_mention_hits == 1

    def test_region_mention_by_abbreviation(self, tmp_path: Path):
        path = tmp_path / "ds.jsonl"
        _write_dataset(path, [self._example(answer="V1 activation indicates " + "x " * 60, abbr="V1")])
        stats = analyze(path)
        assert stats.region_mention_hits == 1

    def test_region_not_mentioned(self, tmp_path: Path):
        path = tmp_path / "ds.jsonl"
        _write_dataset(path, [self._example(
            answer="This region does generic things " + "x " * 60,
            abbr="V1",
        )])
        stats = analyze(path)
        # Neither "Primary Visual Cortex" nor "v1" appears in the generic answer
        assert stats.region_mention_hits == 0

    def test_refusal_phrase_flagged(self, tmp_path: Path):
        path = tmp_path / "ds.jsonl"
        _write_dataset(path, [self._example(
            answer="I'm sorry, I cannot help with that. " + "x " * 60,
        )])
        stats = analyze(path)
        assert stats.refusal_hits == 1

    def test_network_mention_via_alias(self, tmp_path: Path):
        path = tmp_path / "ds.jsonl"
        _write_dataset(path, [_make_example(
            id="x_y_0001",
            region="Posterior Cingulate Cortex",
            family="activation_meaning",
            network="default_mode",
            abbr="PCC",
            answer="The DMN includes " + "x " * 60,
        )])
        stats = analyze(path)
        assert stats.network_mention_hits == 1

    def test_duplicates_counted_separately(self, tmp_path: Path):
        path = tmp_path / "ds.jsonl"
        ex = _make_example(
            id="dup_0001",
            region="V1",
            family="x",
            network="visual",
            abbr="V1",
            answer="ok " * 100,
        )
        _write_dataset(path, [ex, ex, ex])
        stats = analyze(path)
        assert stats.n_total == 1   # only the first counts
        assert stats.duplicates == 2
        assert stats.duplicate_ids.count("dup_0001") == 2


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------

class TestStatsRates:
    def test_rates_handle_zero(self):
        s = DatasetStats()
        assert s.region_mention_rate == 0.0
        assert s.network_mention_rate == 0.0
        assert s.refusal_rate == 0.0


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

class TestReport:
    def _good_dataset(self, tmp_path: Path) -> Path:
        path = tmp_path / "good.jsonl"
        examples = []
        for i in range(20):
            examples.append(_make_example(
                id=f"v1_activation_meaning_{i:04d}",
                region="Primary Visual Cortex",
                family="activation_meaning",
                network="visual",
                abbr="V1",
                # Mentions V1 + visual network, ~100 words
                answer="The primary visual cortex (V1) is the entry point for the visual network. " * 12,
            ))
        _write_dataset(path, examples)
        return path

    def test_report_has_headers(self, tmp_path: Path):
        path = self._good_dataset(tmp_path)
        stats = analyze(path)
        report = render_report(stats, source_path=path, min_answer_words=50, max_answer_words=800)
        assert "# Dataset quality report" in report
        assert "## Examples per region" in report
        assert "## Examples per template family" in report
        assert "## Answer length (words)" in report
        assert "## Quality flags" in report
        assert "## Verdict" in report

    def test_clean_dataset_passes_verdict(self, tmp_path: Path):
        path = self._good_dataset(tmp_path)
        stats = analyze(path)
        report = render_report(stats, source_path=path, min_answer_words=50, max_answer_words=800)
        assert "✅ No critical issues" in report

    def test_dirty_dataset_flags_issues(self, tmp_path: Path):
        # 10 examples, half are empty/refusal/no-mention
        path = tmp_path / "dirty.jsonl"
        examples = []
        for i in range(5):
            examples.append(_make_example(
                id=f"v1_x_{i:04d}", region="V1", family="x", network="visual", abbr="V1",
                answer="V1 normal answer " * 30,
            ))
        for i in range(5):
            examples.append(_make_example(
                id=f"v1_y_{i:04d}", region="V1", family="x", network="visual", abbr="V1",
                answer="I'm sorry, I cannot help.",  # refusal AND too short
            ))
        _write_dataset(path, examples)
        stats = analyze(path)
        report = render_report(stats, source_path=path, min_answer_words=50, max_answer_words=800)
        assert "⚠️" in report
        assert "Refusal rate" in report or "refusal" in report.lower()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestCLI:
    def test_main_writes_report_and_returns_zero_for_clean_dataset(self, tmp_path: Path):
        from scripts.validate_dataset import main
        ds = tmp_path / "ds.jsonl"
        _write_dataset(ds, [
            _make_example(
                id=f"v1_act_{i:04d}",
                region="Primary Visual Cortex",
                family="activation_meaning",
                network="visual",
                abbr="V1",
                answer="Primary visual cortex (V1) is the visual network entry. " * 12,
            )
            for i in range(20)
        ])
        report_path = tmp_path / "rep.md"
        rc = main(["--input", str(ds), "--report", str(report_path)])
        assert rc == 0
        assert report_path.exists()
        assert "# Dataset quality report" in report_path.read_text(encoding="utf-8")

    def test_main_returns_one_when_dataset_fails(self, tmp_path: Path):
        from scripts.validate_dataset import main
        ds = tmp_path / "bad.jsonl"
        _write_dataset(ds, [
            _make_example(id="x_y_0001", region="V1", family="x",
                          network="visual", abbr="V1", answer="")
        ])
        rc = main(["--input", str(ds), "--report", str(tmp_path / "rep.md")])
        assert rc == 1
