"""BOLD-to-ASCII video renderer for Cortex.

Implements the ascii-video technique from Nous Research's Hermes Agent ascii-video skill.
Adapted for cortical BOLD timeseries: maps fsaverage5 z-scores onto a colored ASCII canvas
and encodes to MP4 via ffmpeg.

Input:  BOLD array, shape (T, 20484) float32   — per-vertex z-scores from TRIBE v2
Output: MP4 (1080p or 480p)                    — colored ASCII art showing cortical activation

Colormap (ISU-branded diverging):
  z < 0  → ISU Blue  rgb(86,117,143)   suppressed blood flow
  z ≈ 0  → dark neutral rgb(43,43,58)  baseline
  z > 0  → ISU Gold rgb(246,169,23) → ISU Red rgb(204,0,0)  activated

Skill-doc source: Nous Research Hermes Agent ascii-video skill (April 2026)
"""
from __future__ import annotations
import io
import os
import math
import subprocess
import sys
from pathlib import Path
from typing import Sequence

import numpy as np

# ---------------------------------------------------------------------------
# Character palettes (from Hermes ascii-video skill documentation)
# ---------------------------------------------------------------------------
DENSITY_RAMP  = " .,:;+x%#@"          # 10 levels: sparse → dense
BRAILLE_DARK  = "⠀⠄⠆⠇⠿⣿"             # braille: 6 levels (unicode, looks great on dark bg)
BLOCK_RAMP    = " ░▒▓█"               # block chars: 5 levels
BOLD_RAMP     = " ·:=+*#%@"           # 9 levels, good for brain data

PALETTE = BOLD_RAMP                    # default palette for BOLD activation

# ---------------------------------------------------------------------------
# ISU-branded diverging colormap (matches main.js zToRGB exactly)
# ---------------------------------------------------------------------------

def _z2rgb(z: float, z_scale: float) -> tuple[int, int, int]:
    t = max(-1.0, min(1.0, z / max(z_scale, 1e-4)))
    m = abs(t) ** 0.65
    BR, BG, BB = 0.17, 0.17, 0.23
    if t >= 0:
        if m < 0.55:
            p = m / 0.55
            r = BR + p * (0.965 - BR)
            g = BG + p * (0.663 - BG)
            b = BB + p * (0.090 - BB)
        else:
            p = (m - 0.55) / 0.45
            r = 0.965 - p * (0.965 - 0.80)
            g = 0.663 - p * 0.663
            b = 0.090 - p * 0.090
    else:
        r = BR - m * (BR - 0.337)
        g = BG - m * (BG - 0.459)
        b = BB + m * (0.561 - BB)
    return (int(r * 255), int(g * 255), int(b * 255))


def _z2char(z: float, z_scale: float) -> str:
    """Map z-score → ASCII character via brightness (abs activation intensity)."""
    intensity = abs(z) / max(z_scale, 1e-4)
    intensity = min(1.0, intensity ** 0.55)   # gamma lift — mild activations still show
    idx = int(intensity * (len(PALETTE) - 1))
    return PALETTE[idx]


# ---------------------------------------------------------------------------
# ANSI escape helpers for terminal preview
# ---------------------------------------------------------------------------

def _ansi_rgb(r: int, g: int, b: int, char: str) -> str:
    return f"\x1b[38;2;{r};{g};{b}m{char}\x1b[0m"


# ---------------------------------------------------------------------------
# Grid projection: 20484 fsaverage5 vertices → 2D ASCII grid
#
# Strategy: flat snake-order (vertex i → row i//cols, col i%cols).
# This is the same projection used in the thumbnail generator.  It is not
# anatomically accurate (that would require a cortical flatmap) but it
# preserves spatial locality within each hemisphere and looks compelling.
#
# We use a 143×143 grid (143*143=20449 ≈ 20484). The 35 missing vertex slots
# are padded with baseline color at the end of the grid.
# ---------------------------------------------------------------------------

GRID_COLS = 143
GRID_ROWS = 143   # 143*143 = 20449; we render 143*144=20592 cells and clip

# Display scaling: render a smaller ASCII grid for the video frame
# 1080p canvas: font ~10px → ~192 cols × 108 rows (HD density)
# We'll interpolate the 143×143 source grid to the display density.

def _render_frame_rgb(
    row: np.ndarray,     # (20484,) float32 z-scores
    z_scale: float,
    canvas_w: int,
    canvas_h: int,
    font_w: int,
    font_h: int,
) -> np.ndarray:
    """Return an RGB uint8 array (canvas_h, canvas_w, 3) for one BOLD frame.

    Implements the multi-layer approach from the Hermes ascii-video skill:
    - xs grid (fine, 8px font): atmospheric background haze
    - md grid (16px): main cortical activation
    - lg grid (24px): peak activation highlights
    """
    canvas = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)

    cols_md = canvas_w  // font_w
    rows_md = canvas_h  // font_h

    # Interpolate source 143×143 grid to display grid via simple nearest-neighbor
    src_cols = GRID_COLS
    src_rows = GRID_ROWS

    n_v = len(row)
    src_grid_z  = np.zeros((src_rows, src_cols), dtype=np.float32)
    for i in range(min(n_v, src_rows * src_cols)):
        r, c = divmod(i, src_cols)
        src_grid_z[r, c] = row[i]

    # Try to import PIL for fast text rendering; fallback to block-color rectangles
    try:
        from PIL import Image, ImageDraw, ImageFont
        _render_with_pil(canvas, src_grid_z, z_scale, canvas_w, canvas_h,
                         font_w, font_h, cols_md, rows_md)
    except ImportError:
        _render_block_fallback(canvas, src_grid_z, z_scale, canvas_w, canvas_h,
                               cols_md, rows_md)

    return canvas


def _render_with_pil(
    canvas: np.ndarray,
    src_grid: np.ndarray,
    z_scale: float,
    canvas_w: int, canvas_h: int,
    font_w: int, font_h: int,
    cols: int, rows: int,
) -> None:
    from PIL import Image, ImageDraw, ImageFont

    img  = Image.fromarray(canvas)
    draw = ImageDraw.Draw(img)

    # Dark background gradient: midnight blue at edges → deep dark in center
    for row_i in range(rows):
        for col_i in range(cols):
            # Map display cell → source grid
            src_r = int(row_i / rows * (src_grid.shape[0] - 1))
            src_c = int(col_i / cols * (src_grid.shape[1] - 1))
            z     = float(src_grid[src_r, src_c])

            char  = _z2char(z, z_scale)
            rgb   = _z2rgb(z, z_scale)

            x = col_i * font_w
            y = row_i * font_h

            # Background cell: use attenuated color for depth
            bg_r = max(0, int(rgb[0] * 0.08))
            bg_g = max(0, int(rgb[1] * 0.08))
            bg_b = max(0, int(rgb[2] * 0.12 + 10))
            draw.rectangle([x, y, x + font_w, y + font_h],
                           fill=(bg_r, bg_g, bg_b))

            if char.strip():   # don't draw spaces
                try:
                    draw.text((x, y), char, fill=rgb)
                except Exception:
                    draw.rectangle([x + 1, y + 1, x + font_w - 1, y + font_h - 1],
                                   fill=rgb)

    canvas[:] = np.array(img)


def _render_block_fallback(
    canvas: np.ndarray,
    src_grid: np.ndarray,
    z_scale: float,
    canvas_w: int, canvas_h: int,
    cols: int, rows: int,
) -> None:
    cell_w = canvas_w // cols
    cell_h = canvas_h // rows
    for row_i in range(rows):
        for col_i in range(cols):
            src_r = int(row_i / rows * (src_grid.shape[0] - 1))
            src_c = int(col_i / cols * (src_grid.shape[1] - 1))
            z     = float(src_grid[src_r, src_c])
            rgb   = _z2rgb(z, z_scale)
            intensity = abs(z) / max(z_scale, 1e-4)
            # Dim the background cells proportionally
            alpha = min(1.0, 0.12 + intensity * 0.88)
            r, g, b = int(rgb[0]*alpha), int(rgb[1]*alpha), int(rgb[2]*alpha)
            y0, y1 = row_i * cell_h, (row_i + 1) * cell_h
            x0, x1 = col_i * cell_w, (col_i + 1) * cell_w
            canvas[y0:y1, x0:x1] = [r, g, b]


# ---------------------------------------------------------------------------
# Post-processing shaders (from Hermes ascii-video skill shader chain)
# ---------------------------------------------------------------------------

def _shader_vignette(frame: np.ndarray, strength: float = 0.45) -> np.ndarray:
    """Darken edges — keeps focus on active brain regions in the center."""
    h, w = frame.shape[:2]
    cx, cy = w / 2, h / 2
    Y, X = np.mgrid[0:h, 0:w]
    dist = np.sqrt(((X - cx) / cx) ** 2 + ((Y - cy) / cy) ** 2)
    mask = np.clip(1.0 - strength * dist, 0, 1)[..., np.newaxis]
    return (frame * mask).astype(np.uint8)


def _shader_bloom(frame: np.ndarray, radius: int = 3, strength: float = 0.22) -> np.ndarray:
    """Soft bloom on bright activated regions."""
    try:
        from scipy.ndimage import uniform_filter
        bright = frame.astype(np.float32)
        blurred = uniform_filter(bright, size=[radius, radius, 1])
        out = np.clip(bright + blurred * strength, 0, 255)
        return out.astype(np.uint8)
    except ImportError:
        return frame


def _tonemap(frame: np.ndarray, gamma: float = 0.82) -> np.ndarray:
    """Gamma lift to prevent clipped highlights and lift midtones."""
    f = frame.astype(np.float32) / 255.0
    f = np.power(np.clip(f, 0, 1), gamma)
    return (f * 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_bold_ascii_video(
    bold: np.ndarray,               # (T, 20484) float32 z-scores
    output_path: str | Path,
    fps: float = 4.0,               # 2 Hz TRIBE × 2 for smoother video
    resolution: str = "480p",       # "480p" | "720p" | "1080p"
    font_size: int = 12,
    vignette: bool = True,
    bloom: bool = True,
    title: str = "",
    peak_t: int | None = None,
) -> Path:
    """Render BOLD activation timeseries as ASCII art video.

    Args:
        bold:        TRIBE v2 output, shape (T, 20484) float32 z-scores
        output_path: destination MP4 path
        fps:         output frames-per-second (4 Hz = 2× TRIBE sample rate)
        resolution:  "480p" | "720p" | "1080p"
        font_size:   ASCII cell size in pixels (smaller = more detail)
        vignette:    apply edge darkening
        bloom:       apply glow on active regions
        title:       scan title / filename to overlay
        peak_t:      if set, start video at peak activation frame

    Returns:
        Path to the rendered MP4.
    """
    res_map = {"480p": (854, 480), "720p": (1280, 720), "1080p": (1920, 1080)}
    W, H = res_map.get(resolution, (854, 480))
    font_w = font_h = font_size

    T, N = bold.shape
    if N != 20484:
        raise ValueError(f"Expected 20484 vertices, got {N}")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Per-frame z_scale: exponential moving average of abs-max
    z_scale = float(np.abs(bold).max()) or 1.0

    # Frame order: start at peak if provided, then cycle through all frames
    if peak_t is not None:
        order = list(range(peak_t, T)) + list(range(0, peak_t))
    else:
        order = list(range(T))

    # Duplicate frames for smoother playback (TRIBE 2 Hz → output fps)
    frames_per_tribe = max(1, round(fps / 2.0))
    expanded_order = []
    for t in order:
        expanded_order.extend([t] * frames_per_tribe)

    # Launch ffmpeg process to receive raw RGB frames on stdin
    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-pix_fmt", "rgb24",
        "-s", f"{W}x{H}",
        "-r", str(fps),
        "-i", "-",
        "-an",
        "-vcodec", "libx264",
        "-pix_fmt", "yuv420p",
        "-crf", "22",
        "-preset", "fast",
        str(output_path),
    ]
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except FileNotFoundError:
        raise RuntimeError("ffmpeg not found — install ffmpeg to generate ASCII videos")

    try:
        smooth_scale = z_scale
        for t in expanded_order:
            row = bold[t]
            # Smooth z_scale
            row_max = float(np.abs(row).max())
            smooth_scale = smooth_scale * 0.7 + max(row_max, 0.1) * 0.3

            frame = _render_frame_rgb(row, smooth_scale, W, H, font_w, font_h)

            if bloom:
                frame = _shader_bloom(frame)
            if vignette:
                frame = _shader_vignette(frame)
            frame = _tonemap(frame)

            proc.stdin.write(frame.tobytes())
    finally:
        proc.stdin.close()
        proc.wait()

    return output_path


# ---------------------------------------------------------------------------
# Server integration helper
# ---------------------------------------------------------------------------

async def generate_for_scan(
    scan_id: str,
    bold_path: str | Path,
    output_dir: str | Path = "D:/cortex/scans/ascii",
    peak_t: int | None = None,
    resolution: str = "480p",
) -> Path | None:
    """Async-safe wrapper: runs render in a thread pool executor.

    Called by webapp/server.py after a scan completes.
    Returns the output .mp4 path, or None if generation failed.
    """
    import asyncio
    bold_path = Path(bold_path)
    if not bold_path.exists():
        return None

    out_path = Path(output_dir) / f"{scan_id}_ascii.mp4"

    try:
        bold = np.load(bold_path)
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: render_bold_ascii_video(
                bold=bold,
                output_path=out_path,
                fps=4.0,
                resolution=resolution,
                peak_t=peak_t,
            )
        )
        return result
    except Exception as e:
        import logging
        logging.getLogger("cortex-ascii").warning("ASCII video generation failed: %s", e)
        return None


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate ASCII video from BOLD .npy file")
    parser.add_argument("npy", help="Path to BOLD .npy file (T x 20484 float32)")
    parser.add_argument("-o", "--output", default="bold_ascii.mp4")
    parser.add_argument("--fps", type=float, default=4.0)
    parser.add_argument("--res", default="480p", choices=["480p", "720p", "1080p"])
    parser.add_argument("--font-size", type=int, default=12)
    parser.add_argument("--peak-t", type=int, default=None)
    args = parser.parse_args()

    bold = np.load(args.npy)
    print(f"Loaded BOLD: {bold.shape}, z-range [{bold.min():.3f}, {bold.max():.3f}]")
    out = render_bold_ascii_video(
        bold=bold,
        output_path=args.output,
        fps=args.fps,
        resolution=args.res,
        font_size=args.font_size,
        peak_t=args.peak_t,
    )
    print(f"Saved: {out}")
