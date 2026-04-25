"""Tests for scripts/fetch_demo_clips.py — YouTube demo clip fetcher.

We never hit YouTube here — the `Downloader` abstraction lets us substitute
a `StubDownloader` that writes a placeholder MP4 to disk. FFmpeg trim is also
mocked at the function level. `cortex.media_processor.probe` is monkey-patched
to return a fixed `MediaInfo`.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# ClipSpec / clip-list loader
# ---------------------------------------------------------------------------

class TestClipSpec:
    def test_from_dict_round_trip(self):
        from scripts.fetch_demo_clips import ClipSpec
        s = ClipSpec.from_dict({
            "id": "x", "url": "https://y.example/v=A",
            "label": "Test clip", "track": "education", "max_seconds": 30,
        })
        assert s.id == "x"
        assert s.max_seconds == 30.0

    def test_missing_field_raises(self):
        from scripts.fetch_demo_clips import ClipSpec
        with pytest.raises(ValueError, match="missing fields"):
            ClipSpec.from_dict({"id": "x"})


class TestLoadClipSpecs:
    def _write_yaml(self, path: Path, body: str) -> None:
        path.write_text(body, encoding="utf-8")

    def test_loads_valid_yaml(self, tmp_path: Path):
        from scripts.fetch_demo_clips import load_clip_specs
        path = tmp_path / "clips.yaml"
        self._write_yaml(path, """
clips:
  - id: a
    url: https://example.com/a
    label: Clip A
    track: education
    max_seconds: 30
""")
        specs = load_clip_specs(path)
        assert len(specs) == 1
        assert specs[0].id == "a"

    def test_missing_file_raises(self, tmp_path: Path):
        from scripts.fetch_demo_clips import load_clip_specs
        with pytest.raises(FileNotFoundError):
            load_clip_specs(tmp_path / "nope.yaml")

    def test_empty_clips_raises(self, tmp_path: Path):
        from scripts.fetch_demo_clips import load_clip_specs
        path = tmp_path / "empty.yaml"
        path.write_text("clips: []", encoding="utf-8")
        with pytest.raises(ValueError, match="no `clips:`"):
            load_clip_specs(path)

    def test_duplicate_ids_raises(self, tmp_path: Path):
        from scripts.fetch_demo_clips import load_clip_specs
        path = tmp_path / "dup.yaml"
        path.write_text("""
clips:
  - {id: a, url: u, label: l, track: t, max_seconds: 10}
  - {id: a, url: u2, label: l2, track: t, max_seconds: 10}
""", encoding="utf-8")
        with pytest.raises(ValueError, match="duplicate clip ids"):
            load_clip_specs(path)

    def test_max_seconds_capped_at_50(self, tmp_path: Path):
        from scripts.fetch_demo_clips import load_clip_specs
        path = tmp_path / "long.yaml"
        path.write_text("""
clips:
  - {id: a, url: u, label: l, track: t, max_seconds: 60}
""", encoding="utf-8")
        with pytest.raises(ValueError, match="max_seconds must be in"):
            load_clip_specs(path)

    def test_negative_max_seconds_rejected(self, tmp_path: Path):
        from scripts.fetch_demo_clips import load_clip_specs
        path = tmp_path / "neg.yaml"
        path.write_text("""
clips:
  - {id: a, url: u, label: l, track: t, max_seconds: -1}
""", encoding="utf-8")
        with pytest.raises(ValueError):
            load_clip_specs(path)

    def test_real_demo_clips_yaml_is_valid(self):
        # The shipped curated clip list must parse + validate.
        from scripts.fetch_demo_clips import load_clip_specs
        specs = load_clip_specs(
            Path(__file__).resolve().parents[2] / "scripts" / "demo_clips.yaml"
        )
        assert len(specs) >= 4
        ids = [s.id for s in specs]
        assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# Downloaders
# ---------------------------------------------------------------------------

class TestStubDownloader:
    def test_writes_a_file(self, tmp_path: Path):
        from scripts.fetch_demo_clips import StubDownloader
        out = StubDownloader().download("https://x.example/abc", tmp_path / "demo")
        assert out.exists()
        assert out.suffix == ".mp4"
        assert out.stat().st_size > 0


class TestFactory:
    def test_dispatch_yt_dlp(self):
        from scripts.fetch_demo_clips import YtDlpDownloader, make_downloader
        assert isinstance(make_downloader("yt-dlp"), YtDlpDownloader)

    def test_dispatch_stub(self):
        from scripts.fetch_demo_clips import StubDownloader, make_downloader
        assert isinstance(make_downloader("stub"), StubDownloader)

    def test_unknown_raises(self):
        from scripts.fetch_demo_clips import make_downloader
        with pytest.raises(ValueError, match="Unknown downloader"):
            make_downloader("magic")


# ---------------------------------------------------------------------------
# fetch_one — full per-clip pipeline (downloader + trim + probe all mocked)
# ---------------------------------------------------------------------------

class TestFetchOne:
    def _make_config(self, tmp_path: Path):
        from scripts.fetch_demo_clips import FetchConfig, StubDownloader
        return FetchConfig(
            output_dir=tmp_path / "demo",
            clip_list_path=tmp_path / "clips.yaml",
            downloader=StubDownloader(),
        )

    def _spec(self, **overrides):
        from scripts.fetch_demo_clips import ClipSpec
        defaults = {
            "id": "test_clip",
            "url": "https://example.com/v",
            "label": "Test",
            "track": "education",
            "max_seconds": 20.0,
        }
        defaults.update(overrides)
        return ClipSpec(**defaults)

    def _patch_trim_and_probe(self, monkeypatch, tmp_path: Path):
        """Stub trim_to_seconds and probe so we don't shell out to ffmpeg."""
        from cortex.media_processor import MediaInfo
        from scripts import fetch_demo_clips

        def _fake_trim(input_path, output_path, seconds):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"\x00" * 4096)
            return output_path

        def _fake_probe(path):
            return MediaInfo(
                duration_s=20.0,
                width=1280,
                height=720,
                fps=30.0,
                has_audio=True,
                codec="h264",
                file_size_mb=0.004,
                audio_codec="aac",
                audio_sample_rate=48000,
            )

        monkeypatch.setattr(fetch_demo_clips, "trim_to_seconds", _fake_trim)
        monkeypatch.setattr("cortex.media_processor.probe", _fake_probe)

    def test_emits_manifest_entry(self, tmp_path: Path, monkeypatch):
        from scripts.fetch_demo_clips import fetch_one
        config = self._make_config(tmp_path)
        self._patch_trim_and_probe(monkeypatch, tmp_path)

        entry = fetch_one(self._spec(), config)
        assert entry["id"] == "test_clip"
        assert entry["track"] == "education"
        assert entry["max_seconds_requested"] == 20.0
        assert entry["duration_s"] == 20.0
        assert entry["probe"]["width"] == 1280
        assert entry["probe"]["has_audio"] is True
        # The final file lives at output_dir/<id>.mp4
        assert entry["path"].endswith("test_clip.mp4")
        assert Path(entry["path"]).exists()


# ---------------------------------------------------------------------------
# fetch_all — top-level orchestration
# ---------------------------------------------------------------------------

class TestFetchAll:
    def _setup(self, tmp_path: Path, monkeypatch, n_clips: int = 3):
        """Build a tmp config + clip list with `n_clips` synthetic clips."""
        from cortex.media_processor import MediaInfo
        from scripts import fetch_demo_clips

        clip_list = tmp_path / "clips.yaml"
        body_lines = ["clips:"]
        for i in range(n_clips):
            body_lines.append(
                f"  - {{id: c{i}, url: 'https://e.example/{i}', "
                f"label: 'L{i}', track: education, max_seconds: 25}}"
            )
        clip_list.write_text("\n".join(body_lines), encoding="utf-8")

        def _fake_trim(input_path, output_path, seconds):
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"\x00" * 4096)
            return output_path

        def _fake_probe(path):
            return MediaInfo(
                duration_s=25.0, width=1280, height=720, fps=30.0,
                has_audio=True, codec="h264", file_size_mb=0.004,
                audio_codec="aac", audio_sample_rate=48000,
            )

        monkeypatch.setattr(fetch_demo_clips, "trim_to_seconds", _fake_trim)
        monkeypatch.setattr("cortex.media_processor.probe", _fake_probe)

        from scripts.fetch_demo_clips import FetchConfig, StubDownloader
        return FetchConfig(
            output_dir=tmp_path / "demo",
            clip_list_path=clip_list,
            downloader=StubDownloader(),
        )

    def test_writes_manifest_with_all_clips(self, tmp_path: Path, monkeypatch):
        from scripts.fetch_demo_clips import fetch_all
        config = self._setup(tmp_path, monkeypatch, n_clips=3)
        manifest = fetch_all(config)

        assert manifest["schema_version"] == 1
        assert manifest["n_clips"] == 3
        assert manifest["n_failures"] == 0
        assert len(manifest["clips"]) == 3

        manifest_path = config.output_dir / "manifest.json"
        assert manifest_path.exists()
        loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert loaded["n_clips"] == 3

    def test_failures_recorded_in_manifest(self, tmp_path: Path, monkeypatch):
        """If one clip fails, the others still succeed and the failure is logged."""
        from scripts import fetch_demo_clips
        from scripts.fetch_demo_clips import fetch_all

        config = self._setup(tmp_path, monkeypatch, n_clips=3)

        # Make trim fail for c1
        original_trim = fetch_demo_clips.trim_to_seconds
        def _selective_trim(input_path, output_path, seconds):
            if "c1" in str(output_path):
                raise fetch_demo_clips.TrimError("simulated ffmpeg failure")
            return original_trim(input_path, output_path, seconds)
        monkeypatch.setattr(fetch_demo_clips, "trim_to_seconds", _selective_trim)

        manifest = fetch_all(config)
        assert manifest["n_clips"] == 2
        assert manifest["n_failures"] == 1
        assert manifest["failures"][0]["id"] == "c1"
        assert "simulated ffmpeg failure" in manifest["failures"][0]["error"]

    def test_only_filter_restricts_output(self, tmp_path: Path, monkeypatch):
        from scripts.fetch_demo_clips import fetch_all
        config = self._setup(tmp_path, monkeypatch, n_clips=3)
        config.only = ("c1",)
        manifest = fetch_all(config)
        assert manifest["n_clips"] == 1
        assert manifest["clips"][0]["id"] == "c1"

    def test_only_with_unknown_id_raises(self, tmp_path: Path, monkeypatch):
        from scripts.fetch_demo_clips import fetch_all
        config = self._setup(tmp_path, monkeypatch, n_clips=2)
        config.only = ("does_not_exist",)
        with pytest.raises(ValueError, match="unknown ids"):
            fetch_all(config)

    def test_dry_run_does_not_call_downloader(self, tmp_path: Path, monkeypatch):
        from scripts.fetch_demo_clips import fetch_all
        config = self._setup(tmp_path, monkeypatch, n_clips=2)
        # Replace downloader with one that explodes if called
        downloader = MagicMock()
        downloader.name = "should-not-fire"
        downloader.download.side_effect = AssertionError("dry-run must not download")
        config.downloader = downloader
        config.dry_run = True

        result = fetch_all(config)
        assert result["dry_run"] is True
        assert result["n_clips"] == 2
        downloader.download.assert_not_called()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestCLI:
    def test_dry_run_against_real_clip_list(self):
        from scripts.fetch_demo_clips import main
        rc = main(["--dry-run", "--downloader", "stub"])
        # Real clip list ships with placeholder URLs; dry-run shouldn't fail
        # since we never hit the network.
        assert rc == 0

    def test_unknown_flag_errors(self):
        from scripts.fetch_demo_clips import main
        with pytest.raises(SystemExit):
            main(["--definitely-not-a-flag"])

    def test_invalid_downloader_errors(self):
        from scripts.fetch_demo_clips import main
        with pytest.raises(SystemExit):
            main(["--downloader", "magic", "--dry-run"])
