"""Tests for cortex.ascii_video — BOLD-to-ASCII video renderer."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helper: minimal BOLD array
# ---------------------------------------------------------------------------

def _fake_bold(T: int = 5, N: int = 20484) -> np.ndarray:
    rng = np.random.default_rng(42)
    return rng.standard_normal((T, N)).astype(np.float32)


# ---------------------------------------------------------------------------
# Color / char helpers
# ---------------------------------------------------------------------------

class TestColorHelpers:
    def test_z2rgb_returns_tuple_of_3(self):
        from cortex.ascii_video import _z2rgb
        r, g, b = _z2rgb(0.0, 1.0)
        assert isinstance(r, int) and isinstance(g, int) and isinstance(b, int)

    def test_z2rgb_range_0_255(self):
        from cortex.ascii_video import _z2rgb
        for z in [-2.0, -1.0, 0.0, 1.0, 2.0]:
            r, g, b = _z2rgb(z, 2.0)
            assert 0 <= r <= 255
            assert 0 <= g <= 255
            assert 0 <= b <= 255

    def test_z2rgb_zero_z_scale_fallback(self):
        from cortex.ascii_video import _z2rgb
        r, g, b = _z2rgb(1.0, 0.0)
        assert 0 <= r <= 255

    def test_z2rgb_positive_z_is_warm(self):
        from cortex.ascii_video import _z2rgb
        # Positive z should have higher red component than negative
        r_pos, g_pos, _ = _z2rgb(2.0, 2.0)
        r_neg, g_neg, _ = _z2rgb(-2.0, 2.0)
        assert r_pos >= r_neg

    def test_z2char_returns_palette_char(self):
        from cortex.ascii_video import _z2char, PALETTE
        char = _z2char(0.5, 1.0)
        assert char in PALETTE

    def test_z2char_zero_z_returns_first_char(self):
        from cortex.ascii_video import _z2char, PALETTE
        char = _z2char(0.0, 1.0)
        assert char == PALETTE[0]

    def test_z2char_large_z_returns_last_char(self):
        from cortex.ascii_video import _z2char, PALETTE
        char = _z2char(100.0, 1.0)
        assert char == PALETTE[-1]

    def test_ansi_rgb_returns_escape_sequence(self):
        from cortex.ascii_video import _ansi_rgb
        s = _ansi_rgb(255, 128, 0, "X")
        assert "\x1b[" in s
        assert "X" in s


# ---------------------------------------------------------------------------
# Shader chain
# ---------------------------------------------------------------------------

class TestShaders:
    def test_shader_vignette_returns_uint8(self):
        from cortex.ascii_video import _shader_vignette
        frame = np.ones((64, 64, 3), dtype=np.uint8) * 200
        out = _shader_vignette(frame)
        assert out.dtype == np.uint8
        assert out.shape == frame.shape

    def test_shader_vignette_darkens_edges(self):
        from cortex.ascii_video import _shader_vignette
        frame = np.ones((100, 100, 3), dtype=np.uint8) * 255
        out = _shader_vignette(frame, strength=0.8)
        # Center should be brighter than edge
        center = int(out[50, 50, 0])
        edge = int(out[0, 0, 0])
        assert center > edge

    def test_shader_bloom_returns_uint8(self):
        from cortex.ascii_video import _shader_bloom
        frame = np.ones((32, 32, 3), dtype=np.uint8) * 100
        out = _shader_bloom(frame)
        assert out.dtype == np.uint8
        assert out.shape == frame.shape

    def test_tonemap_returns_uint8(self):
        from cortex.ascii_video import _tonemap
        frame = np.ones((32, 32, 3), dtype=np.uint8) * 128
        out = _tonemap(frame)
        assert out.dtype == np.uint8
        assert out.shape == frame.shape

    def test_tonemap_gamma_one_passthrough(self):
        from cortex.ascii_video import _tonemap
        frame = np.full((10, 10, 3), 128, dtype=np.uint8)
        out = _tonemap(frame, gamma=1.0)
        np.testing.assert_allclose(out.astype(float), frame.astype(float), atol=1)


# ---------------------------------------------------------------------------
# Frame rendering
# ---------------------------------------------------------------------------

class TestFrameRendering:
    def test_render_frame_rgb_shape(self):
        from cortex.ascii_video import _render_frame_rgb
        row = _fake_bold(1)[0]
        frame = _render_frame_rgb(row, z_scale=2.0, canvas_w=160, canvas_h=80,
                                  font_w=8, font_h=8)
        assert frame.shape == (80, 160, 3)
        assert frame.dtype == np.uint8

    def test_render_block_fallback_shape(self):
        from cortex.ascii_video import _render_block_fallback
        row = _fake_bold(1)[0]
        grid = row[:143 * 143].reshape(143, 143).astype(np.float32)
        canvas = np.zeros((80, 160, 3), dtype=np.uint8)
        _render_block_fallback(canvas, grid, z_scale=2.0,
                               canvas_w=160, canvas_h=80,
                               cols=20, rows=10)
        assert canvas.shape == (80, 160, 3)


# ---------------------------------------------------------------------------
# Main render function (mocked ffmpeg)
# ---------------------------------------------------------------------------

class TestRenderBoldAsciiVideo:
    def test_wrong_n_vertices_raises(self, tmp_path):
        from cortex.ascii_video import render_bold_ascii_video
        bold = np.zeros((5, 1000), dtype=np.float32)  # wrong N
        with pytest.raises(ValueError, match="20484"):
            render_bold_ascii_video(bold, tmp_path / "out.mp4")

    def test_ffmpeg_not_found_raises_runtime_error(self, tmp_path):
        from cortex.ascii_video import render_bold_ascii_video
        bold = _fake_bold(T=3)
        with patch("subprocess.Popen", side_effect=FileNotFoundError):
            with pytest.raises(RuntimeError, match="ffmpeg not found"):
                render_bold_ascii_video(bold, tmp_path / "out.mp4")

    def test_render_calls_ffmpeg(self, tmp_path):
        from cortex.ascii_video import render_bold_ascii_video
        bold = _fake_bold(T=3)

        fake_proc = MagicMock()
        fake_proc.stdin = MagicMock()
        fake_proc.stdin.write = MagicMock()
        fake_proc.stdin.close = MagicMock()
        fake_proc.wait = MagicMock()

        with patch("subprocess.Popen", return_value=fake_proc):
            result = render_bold_ascii_video(
                bold, tmp_path / "out.mp4",
                fps=2.0, resolution="480p", font_size=32,
                vignette=False, bloom=False,
            )
        assert fake_proc.stdin.close.called
        assert fake_proc.wait.called
        assert result == tmp_path / "out.mp4"

    def test_render_with_peak_t(self, tmp_path):
        from cortex.ascii_video import render_bold_ascii_video
        bold = _fake_bold(T=6)

        fake_proc = MagicMock()
        fake_proc.stdin = MagicMock()
        fake_proc.stdin.write = MagicMock()
        fake_proc.stdin.close = MagicMock()
        fake_proc.wait = MagicMock()

        with patch("subprocess.Popen", return_value=fake_proc):
            render_bold_ascii_video(
                bold, tmp_path / "out2.mp4",
                fps=2.0, resolution="480p", font_size=64,
                peak_t=3,
            )
        assert fake_proc.wait.called

    def test_render_all_resolutions(self, tmp_path):
        from cortex.ascii_video import render_bold_ascii_video
        bold = _fake_bold(T=2)

        for res in ["480p", "720p", "1080p"]:
            fake_proc = MagicMock()
            fake_proc.stdin = MagicMock()
            fake_proc.stdin.write = MagicMock()
            fake_proc.stdin.close = MagicMock()
            fake_proc.wait = MagicMock()
            with patch("subprocess.Popen", return_value=fake_proc):
                render_bold_ascii_video(
                    bold, tmp_path / f"out_{res}.mp4",
                    fps=2.0, resolution=res, font_size=64,
                )


# ---------------------------------------------------------------------------
# Async wrapper
# ---------------------------------------------------------------------------

class TestGenerateForScan:
    def test_missing_bold_path_returns_none(self):
        from cortex.ascii_video import generate_for_scan

        async def _run():
            return await generate_for_scan(
                scan_id="test123",
                bold_path="/nonexistent/bold.npy",
            )
        result = asyncio.run(_run())
        assert result is None

    def test_returns_mp4_path_on_success(self, tmp_path):
        from cortex.ascii_video import generate_for_scan
        bold = _fake_bold(T=3)
        bold_path = tmp_path / "bold.npy"
        np.save(bold_path, bold)

        fake_proc = MagicMock()
        fake_proc.stdin = MagicMock()
        fake_proc.stdin.write = MagicMock()
        fake_proc.stdin.close = MagicMock()
        fake_proc.wait = MagicMock()

        async def _run():
            with patch("subprocess.Popen", return_value=fake_proc):
                return await generate_for_scan(
                    scan_id="test123",
                    bold_path=bold_path,
                    output_dir=str(tmp_path),
                    resolution="480p",
                )
        result = asyncio.run(_run())
        assert result is not None
        assert str(result).endswith(".mp4")

    def test_render_failure_returns_none(self, tmp_path):
        from cortex.ascii_video import generate_for_scan
        bold = _fake_bold(T=3)
        bold_path = tmp_path / "bold.npy"
        np.save(bold_path, bold)

        async def _run():
            with patch("subprocess.Popen", side_effect=RuntimeError("ffmpeg error")):
                return await generate_for_scan(
                    scan_id="test123",
                    bold_path=bold_path,
                    output_dir=str(tmp_path),
                )
        result = asyncio.run(_run())
        assert result is None
