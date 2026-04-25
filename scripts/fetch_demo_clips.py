"""Fetch curated demo clips from official Google YouTube channels.

Reads `scripts/demo_clips.yaml` for the clip list, downloads each one with
yt-dlp, trims to the per-clip `max_seconds` cap (≤50 s — TRIBE v2's hard
limit), probes the trimmed file with `cortex.media_processor.probe` to verify
it's playable, and writes a JSON manifest next to the clips.

Usage::

    # Dry run — print what would be fetched, no downloads
    python -m scripts.fetch_demo_clips --dry-run

    # Real fetch into assets/demo/
    python -m scripts.fetch_demo_clips --output-dir assets/demo

    # Fetch a single clip by id
    python -m scripts.fetch_demo_clips --only deepmind-gemma-launch

The downloader is abstracted via a `Downloader` protocol so tests substitute
a `StubDownloader` and never touch YouTube. Same pattern as
`scripts/backends.py`.

Manifest format (`<output_dir>/manifest.json`)::

    {
      "schema_version": 1,
      "fetched_at": "2026-04-25T12:34:56+00:00",
      "clips": [
        {"id": "...", "label": "...", "url": "...", "track": "...",
         "path": "<output_dir>/<id>.mp4",
         "duration_s": 47.9, "size_mb": 12.3,
         "probe": {"width": 1280, "height": 720, "fps": 30.0,
                   "has_audio": true, "codec": "h264"}}
      ]
    }
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

# ---------------------------------------------------------------------------
# Downloader abstraction
# ---------------------------------------------------------------------------

class Downloader(Protocol):
    """Anything that pulls a YouTube URL down to an MP4 path. Lets tests
    substitute a fake without hitting the network."""

    name: str

    def download(self, url: str, dest: Path) -> Path: ...


class YtDlpDownloader:
    """Wraps the `yt-dlp` CLI. Selects 720p+audio MP4 when available, else
    falls back to whatever yt-dlp auto-picks."""

    name = "yt-dlp"

    FORMAT = "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best"

    def download(self, url: str, dest: Path) -> Path:
        if shutil.which("yt-dlp") is None:
            raise RuntimeError(
                "yt-dlp not on PATH. Install with: pip install yt-dlp  "
                "or pipx install yt-dlp"
            )
        dest.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "yt-dlp",
            "--quiet",
            "--no-warnings",
            "--no-progress",
            "--format", self.FORMAT,
            "--merge-output-format", "mp4",
            "--output", str(dest.with_suffix(".%(ext)s")),
            url,
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        # yt-dlp writes to dest.mp4; return the actual path
        produced = dest.with_suffix(".mp4")
        if not produced.exists():
            raise RuntimeError(f"yt-dlp produced no file at {produced}")
        return produced


class StubDownloader:
    """Test downloader: writes a tiny pre-baked MP4 stub at the destination."""

    name = "stub"

    # Smallest valid-ish MP4 header — enough to trick file-system + size checks.
    # Tests that need real probing should mock probe() too.
    _STUB_BYTES = b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isom\x00\x00\x00\x00"

    def __init__(self, payload: bytes | None = None) -> None:
        self._payload = payload or self._STUB_BYTES

    def download(self, url: str, dest: Path) -> Path:
        path = dest.with_suffix(".mp4")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self._payload)
        return path


def make_downloader(spec: str) -> Downloader:
    if spec == "yt-dlp":
        return YtDlpDownloader()
    if spec == "stub":
        return StubDownloader()
    raise ValueError(f"Unknown downloader: {spec!r}. Valid: yt-dlp, stub.")


# ---------------------------------------------------------------------------
# Trim helper
# ---------------------------------------------------------------------------

class TrimError(Exception):
    pass


def trim_to_seconds(input_path: Path, output_path: Path, seconds: float) -> Path:
    """Trim `input_path` to the first `seconds` seconds via FFmpeg copy.

    Uses stream-copy (no re-encode) for speed. If the source's audio/video
    streams aren't keyframe-aligned at second 0, the trim may overshoot by a
    fraction of a second — acceptable since TRIBE's 50 s cap has a 2 s buffer.
    """
    if shutil.which("ffmpeg") is None:
        raise TrimError("ffmpeg not on PATH; install via winget install FFmpeg")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-t", f"{seconds:.3f}",
        "-c", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
    except subprocess.CalledProcessError as exc:
        raise TrimError(
            f"ffmpeg trim failed for {input_path}: "
            f"{exc.stderr.decode('utf-8', errors='replace')[:400]}"
        ) from exc
    return output_path


# ---------------------------------------------------------------------------
# Spec + manifest
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ClipSpec:
    id: str
    url: str
    label: str
    track: str
    max_seconds: float

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ClipSpec:
        required = {"id", "url", "label", "track", "max_seconds"}
        missing = required - d.keys()
        if missing:
            raise ValueError(f"clip missing fields: {sorted(missing)}; got {d!r}")
        return cls(
            id=str(d["id"]),
            url=str(d["url"]),
            label=str(d["label"]),
            track=str(d["track"]),
            max_seconds=float(d["max_seconds"]),
        )


def load_clip_specs(path: Path) -> list[ClipSpec]:
    """Load + validate the clip list from YAML."""
    if not path.exists():
        raise FileNotFoundError(f"clip list not found: {path}")
    import yaml
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    clips_raw = raw.get("clips") or []
    if not clips_raw:
        raise ValueError(f"{path}: no `clips:` entries")

    specs = [ClipSpec.from_dict(c) for c in clips_raw]
    ids = [s.id for s in specs]
    if len(ids) != len(set(ids)):
        dupes = {x for x in ids if ids.count(x) > 1}
        raise ValueError(f"duplicate clip ids in {path}: {sorted(dupes)}")
    for s in specs:
        if s.max_seconds <= 0 or s.max_seconds > 50:
            raise ValueError(
                f"clip {s.id}: max_seconds must be in (0, 50]; got {s.max_seconds}"
            )
    return specs


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

@dataclass
class FetchConfig:
    output_dir: Path
    clip_list_path: Path
    downloader: Downloader
    only: tuple[str, ...] = ()
    dry_run: bool = False


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def fetch_one(spec: ClipSpec, config: FetchConfig) -> dict[str, Any]:
    """Fetch + trim + probe a single clip. Returns its manifest entry."""
    # Lazy probe import — keeps the script importable in environments without
    # the full cortex media stack (tests substitute a fake via monkeypatch).
    from cortex.media_processor import probe

    out_dir = config.output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    raw_path = out_dir / "_raw" / spec.id
    final_path = out_dir / f"{spec.id}.mp4"

    # 1. Download
    downloaded = config.downloader.download(spec.url, raw_path)

    # 2. Trim
    trim_to_seconds(downloaded, final_path, spec.max_seconds)

    # 3. Clean up raw download to keep the asset dir tidy
    try:
        if downloaded.exists():
            downloaded.unlink()
    except OSError:
        pass

    # 4. Probe to verify
    info = probe(final_path)

    return {
        "id": spec.id,
        "label": spec.label,
        "track": spec.track,
        "url": spec.url,
        "path": str(final_path),
        "max_seconds_requested": spec.max_seconds,
        "duration_s": info.duration_s,
        "size_mb": round(final_path.stat().st_size / (1024 * 1024), 2),
        "probe": {
            "width": info.width,
            "height": info.height,
            "fps": info.fps,
            "has_audio": info.has_audio,
            "codec": info.codec,
            "audio_codec": info.audio_codec,
            "audio_sample_rate": info.audio_sample_rate,
        },
    }


def fetch_all(config: FetchConfig) -> dict[str, Any]:
    """Top-level pipeline. Returns the manifest dict (also written to disk)."""
    specs = load_clip_specs(config.clip_list_path)
    if config.only:
        wanted = set(config.only)
        specs = [s for s in specs if s.id in wanted]
        missing = wanted - {s.id for s in specs}
        if missing:
            raise ValueError(
                f"--only filter referenced unknown ids: {sorted(missing)}; "
                f"available: {[s.id for s in load_clip_specs(config.clip_list_path)]}"
            )

    if config.dry_run:
        return {
            "dry_run": True,
            "clip_list": str(config.clip_list_path),
            "output_dir": str(config.output_dir),
            "downloader": config.downloader.name,
            "n_clips": len(specs),
            "clips": [
                {
                    "id": s.id,
                    "label": s.label,
                    "track": s.track,
                    "url": s.url,
                    "max_seconds": s.max_seconds,
                }
                for s in specs
            ],
        }

    config.output_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for spec in specs:
        try:
            entries.append(fetch_one(spec, config))
            print(f"[fetch_demo_clips] ✓ {spec.id} ({spec.label})", file=sys.stderr)
        except Exception as exc:
            failures.append({
                "id": spec.id,
                "url": spec.url,
                "error": str(exc),
                "error_class": exc.__class__.__name__,
            })
            print(f"[fetch_demo_clips] ✗ {spec.id}: {exc}", file=sys.stderr)

    manifest = {
        "schema_version": 1,
        "fetched_at": _utc_iso(),
        "downloader": config.downloader.name,
        "n_clips": len(entries),
        "n_failures": len(failures),
        "clips": entries,
        "failures": failures,
    }

    manifest_path = config.output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(
        f"[fetch_demo_clips] wrote manifest with {len(entries)} clips "
        f"({len(failures)} failures) to {manifest_path}",
        file=sys.stderr,
    )
    return manifest


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Fetch curated YouTube demo clips for Cortex.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--clip-list",
        type=Path,
        default=Path(__file__).parent / "demo_clips.yaml",
        help="Path to the YAML clip list (default: scripts/demo_clips.yaml)",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=Path("assets/demo"),
        help="Where to put the trimmed clips + manifest.json",
    )
    p.add_argument(
        "--downloader",
        default="yt-dlp",
        choices=["yt-dlp", "stub"],
        help="Downloader implementation",
    )
    p.add_argument(
        "--only",
        nargs="+",
        default=[],
        help="Only fetch these clip ids (matches the YAML `id:` field)",
    )
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    config = FetchConfig(
        output_dir=args.output_dir,
        clip_list_path=args.clip_list,
        downloader=make_downloader(args.downloader),
        only=tuple(args.only),
        dry_run=args.dry_run,
    )
    manifest = fetch_all(config)
    if config.dry_run:
        print(json.dumps(manifest, indent=2))
    return 0 if manifest.get("n_failures", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
