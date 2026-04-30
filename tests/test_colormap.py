"""Tests for colormap math — no GPU required."""
import math


def z_to_rgb(z: float, z_scale: float = 1.0):
    """Python port of the JS zToRGB function."""
    raw = z / max(0.0001, z_scale)
    t = max(-1.0, min(1.0, raw))
    sign = 1 if t >= 0 else -1
    m = abs(t) ** 0.6
    if sign >= 0:
        return (1.0, 0.84 - m * 0.62, 0.85 - m * 0.71)
    return (0.90 - m * 0.78, 0.84 - m * 0.48, 0.85 + m * 0.15)


def test_zero_z_returns_neutral():
    r, g, b = z_to_rgb(0.0)
    assert abs(r - 1.0) < 0.01
    assert abs(g - 0.84) < 0.01


def test_positive_z_goes_warm():
    r, g, b = z_to_rgb(2.0)
    assert r > 0.9
    assert b < 0.5  # warm orange, low blue


def test_negative_z_goes_cool():
    r, g, b = z_to_rgb(-2.0)
    assert r < 0.5  # cool blue, low red
    assert b > 0.8


def test_z_scale_clamps():
    # z = 10, scale = 1.0 should clamp to t=1 (saturated)
    rgb_high = z_to_rgb(10.0, z_scale=1.0)
    rgb_capped = z_to_rgb(2.0, z_scale=1.0)
    # Both should produce the same saturated warm color
    for a, b in zip(rgb_high, rgb_capped):
        assert abs(a - b) < 0.001
