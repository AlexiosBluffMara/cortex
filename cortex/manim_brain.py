"""Manim brain activation explainer for Cortex.

Uses the Hermes Agent Manim skill technique to generate a 3Blue1Brown-style
animated explainer video about TRIBE v2 BOLD predictions.

Generates two scene types:
  1. BoldTimeseries — animated chart of per-network z-scores over time
  2. BrainNetworkDiagram — Yeo-7 network activation ring with labels

Requires: manim (pip install manim) or manim community edition
Run:
    manim -pql cortex/manim_brain.py BoldTimeseries
    manim -pql cortex/manim_brain.py BrainNetworkDiagram

Or from Python:
    from cortex.manim_brain import render_bold_explainer
    render_bold_explainer(bold_npy="scans/abc123.npy", peak_t=11)
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Yeo-7 network metadata (matches main.js YEO7)
# ---------------------------------------------------------------------------
YEO7 = {
    "Vis":         {"label": "Visual",            "color": "#7B5EA7", "idx": 0},
    "SomMot":      {"label": "Somatomotor",        "color": "#5584C2", "idx": 1},
    "DorsAttn":    {"label": "Dorsal Attention",   "color": "#3CB371", "idx": 2},
    "SalVentAttn": {"label": "Salience",           "color": "#E08E45", "idx": 3},
    "Limbic":      {"label": "Limbic",             "color": "#C2C25A", "idx": 4},
    "Cont":        {"label": "Control",            "color": "#D4814E", "idx": 5},
    "Default":     {"label": "Default Mode",       "color": "#CC4455", "idx": 6},
}

YEO7_FUNC = {
    "Vis":         "Processing visual input — what you see",
    "SomMot":      "Sensory & motor — touch, movement",
    "DorsAttn":    "Spatial attention — tracking objects",
    "SalVentAttn": "Salience — unexpected events, reorienting",
    "Limbic":      "Memory & emotion — feeling + recall",
    "Cont":        "Cognitive control — planning & reasoning",
    "Default":     "Rest, self-referential — mind-wandering",
}

# ---------------------------------------------------------------------------
# BOLD → per-network summary
# ---------------------------------------------------------------------------

def bold_to_network_traces(
    bold: np.ndarray,       # (T, 20484) or (T, 400) float32
    n_regions: int = 400,
) -> dict[str, np.ndarray]:
    """Compute per-network mean z-score traces from BOLD array.

    If bold has 20484 vertices, downsample to 400 Schaefer regions first.
    Returns dict {network_key: ndarray(T,)}.
    """
    n_v = bold.shape[1]

    # Schaefer-400 → Yeo-7 assignment (simplified: divide 400 ROIs into 7 groups)
    # Real mapping would use the Schaefer parcellation labels.
    # Here we use the known distribution: Vis~50, SomMot~60, DorsAttn~50,
    # SalVentAttn~50, Limbic~30, Cont~60, Default~100.
    network_slices = {
        "Vis":         (0,   50),
        "SomMot":      (50,  110),
        "DorsAttn":    (110, 160),
        "SalVentAttn": (160, 210),
        "Limbic":      (210, 240),
        "Cont":        (240, 300),
        "Default":     (300, 400),
    }

    if n_v == 20484:
        # Project 20484 vertices → 400 regions (uniform block mean)
        block = max(1, n_v // 400)
        reduced = np.array([bold[:, i*block:(i+1)*block].mean(axis=1) for i in range(400)]).T
    elif n_v >= 400:
        reduced = bold[:, :400]
    else:
        reduced = bold

    traces = {}
    for key, (lo, hi) in network_slices.items():
        hi = min(hi, reduced.shape[1])
        traces[key] = reduced[:, lo:hi].mean(axis=1)
    return traces


# ---------------------------------------------------------------------------
# Manim scene definitions
# ---------------------------------------------------------------------------

MANIM_SCENES = '''
from manim import *
import numpy as np

# Yeo-7 network colors and labels
YEO7_INFO = {
    "Vis":         {"label": "Visual",          "color": "#7B5EA7"},
    "SomMot":      {"label": "Somatomotor",      "color": "#5584C2"},
    "DorsAttn":    {"label": "Dorsal Attn",      "color": "#3CB371"},
    "SalVentAttn": {"label": "Salience",         "color": "#E08E45"},
    "Limbic":      {"label": "Limbic",           "color": "#C2C25A"},
    "Cont":        {"label": "Control",          "color": "#D4814E"},
    "Default":     {"label": "Default Mode",     "color": "#CC4455"},
}

ISU_RED    = "#CC0000"
ISU_YELLOW = "#F6A917"
ISU_BLUE   = "#56758f"
BG_COLOR   = "#0b0d12"

config.background_color = BG_COLOR


class BoldTimeseries(Scene):
    """Animated chart of Yeo-7 network BOLD z-scores over the scan duration.

    Resembles a 3Blue1Brown-style math explainer: clean axes, smooth curves,
    animated labels, ISU color palette.
    """

    NETWORK_TRACES = {NET_TRACES_PLACEHOLDER}
    TR_SECONDS = 0.5
    PEAK_T = PEAK_T_PLACEHOLDER

    def construct(self):
        # --- Title ---
        title = Text("Cortical BOLD Response", font="Open Sans",
                     color=WHITE, weight=BOLD).scale(0.7)
        subtitle = Text("TRIBE v2 · fsaverage5 · Yeo-7 Networks",
                        font="Open Sans", color=GRAY).scale(0.35)
        title_group = VGroup(title, subtitle).arrange(DOWN, buff=0.1)
        title_group.to_edge(UP, buff=0.3)
        self.play(Write(title), run_time=0.8)
        self.play(FadeIn(subtitle), run_time=0.4)

        # --- Axes ---
        traces = self.NETWORK_TRACES
        T = max(len(v) for v in traces.values())
        t_end = T * self.TR_SECONDS

        axes = Axes(
            x_range=[0, t_end, max(1, t_end // 8)],
            y_range=[-2, 2, 0.5],
            axis_config={"color": GRAY_B, "stroke_width": 1.5},
            x_length=9, y_length=5,
        )
        axes.shift(DOWN * 0.3)

        x_label = axes.get_x_axis_label("Time (s)", direction=DOWN)
        y_label = axes.get_y_axis_label("BOLD z-score", direction=LEFT)
        x_label.scale(0.5).set_color(GRAY_B)
        y_label.scale(0.5).set_color(GRAY_B)

        self.play(Create(axes), FadeIn(x_label), FadeIn(y_label), run_time=1.0)

        # Zero line
        zero_line = axes.get_horizontal_line(axes.c2p(0, 0),
                                              color=GRAY_D, stroke_width=1)
        self.play(Create(zero_line), run_time=0.3)

        # --- Draw curves ---
        curves = []
        labels = []
        for key, net in YEO7_INFO.items():
            z = np.array(traces.get(key, [0.0] * T), dtype=float)
            # Smooth slightly
            kernel = np.array([0.1, 0.2, 0.4, 0.2, 0.1])
            if len(z) >= 5:
                z = np.convolve(z, kernel, mode="same")
            t_vals = np.linspace(0, t_end, len(z))

            points = [axes.c2p(t, float(v)) for t, v in zip(t_vals, z)]
            curve = VMobject(color=net["color"], stroke_width=2.2)
            curve.set_points_smoothly(points)
            curves.append(curve)

            # Label at the end of the curve
            end_z = float(z[-1]) if len(z) else 0
            lbl = Text(net["label"], font="Open Sans",
                       color=net["color"]).scale(0.22)
            lbl.move_to(axes.c2p(t_end + 0.3, end_z))
            labels.append(lbl)

        self.play(*[Create(c) for c in curves], run_time=2.5)
        self.play(*[FadeIn(l) for l in labels], run_time=0.5)

        # --- Highlight peak ---
        if self.PEAK_T is not None and 0 <= self.PEAK_T < T:
            peak_s = self.PEAK_T * self.TR_SECONDS
            peak_line = axes.get_vertical_line(
                axes.c2p(peak_s, 2.2), color=ISU_RED, stroke_width=2
            )
            peak_lbl = Text(f"Peak t={peak_s:.1f}s", font="Open Sans",
                            color=ISU_RED).scale(0.28)
            peak_lbl.next_to(axes.c2p(peak_s, 2.2), UP, buff=0.1)
            self.play(Create(peak_line), Write(peak_lbl), run_time=0.6)

        # --- ISU color key strip ---
        strip_label = Text("Activation color key", font="Open Sans",
                           color=GRAY_B).scale(0.28)
        strip_label.to_corner(DL, buff=0.4)
        key_items = VGroup()
        for key, net in YEO7_INFO.items():
            dot  = Dot(color=net["color"], radius=0.07)
            name = Text(net["label"], font="Open Sans",
                        color=net["color"]).scale(0.22)
            item = VGroup(dot, name).arrange(RIGHT, buff=0.08)
            key_items.add(item)
        key_items.arrange(RIGHT, buff=0.22)
        key_items.next_to(strip_label, RIGHT, buff=0.2)
        self.play(FadeIn(strip_label), FadeIn(key_items), run_time=0.5)

        self.wait(2)

        # --- Fade attribution ---
        attr = Text("Cortex · TRIBE v2 + Gemma 4 + Nous Hermes",
                    font="Open Sans", color=GRAY_D).scale(0.22)
        attr.to_corner(DR, buff=0.3)
        self.play(FadeIn(attr), run_time=0.4)
        self.wait(1)


class BrainNetworkDiagram(Scene):
    """Animated Yeo-7 network ring showing relative activation at peak_t.

    Inspired by the Hermes Manim skill — clean, labeled, smooth transitions.
    """

    PEAK_ACTIVATIONS = {PEAK_ACT_PLACEHOLDER}

    def construct(self):
        # Title
        title = Text("Brain Network Activation", font="Open Sans",
                     color=WHITE, weight=BOLD).scale(0.65)
        subtitle = Text("Peak frame · Yeo-7 parcellation",
                        font="Open Sans", color=GRAY).scale(0.32)
        VGroup(title, subtitle).arrange(DOWN, 0.1).to_edge(UP, buff=0.35)
        self.play(Write(title), FadeIn(subtitle), run_time=0.7)

        # Ring segments
        nets = list(YEO7_INFO.items())
        n    = len(nets)
        cx, cy = 0, -0.3
        outer_r = 2.5
        inner_r = 1.2
        gap   = 0.04

        activations = self.PEAK_ACTIVATIONS
        max_act = max(abs(v) for v in activations.values()) or 1.0

        arcs = []
        net_labels = []
        for i, (key, net) in enumerate(nets):
            act  = activations.get(key, 0.0)
            norm = act / max_act
            boost = max(0, norm) * 0.35
            seg_outer = outer_r * (1 + boost)
            alpha_frac = max(0.25, min(1.0, 0.3 + abs(norm) * 0.7))

            start_angle = i * TAU / n + gap / 2
            end_angle   = (i + 1) * TAU / n - gap / 2

            # Outer arc
            arc = AnnularSector(
                inner_radius=inner_r, outer_radius=seg_outer,
                angle=end_angle - start_angle,
                start_angle=start_angle,
                color=net["color"], fill_opacity=alpha_frac,
                stroke_width=0.8, stroke_color=BG_COLOR,
            ).shift(RIGHT * cx + UP * cy)
            arcs.append(arc)

            # Label outside the ring
            mid_angle = (start_angle + end_angle) / 2
            label_r = seg_outer + 0.45
            lx = label_r * np.cos(mid_angle) + cx
            ly = label_r * np.sin(mid_angle) + cy
            lbl = Text(net["label"], font="Open Sans",
                       color=net["color"]).scale(0.28)
            lbl.move_to(RIGHT * lx + UP * ly)
            net_labels.append(lbl)

        # Animate
        self.play(*[FadeIn(a) for a in arcs], run_time=1.5)
        self.play(*[Write(l) for l in net_labels], run_time=0.8)

        # Center text
        peak_net_key = max(activations, key=lambda k: activations[k])
        peak_net_lbl = YEO7_INFO[peak_net_key]["label"]
        center_text = VGroup(
            Text("Peak network", font="Open Sans", color=GRAY_B).scale(0.28),
            Text(peak_net_lbl, font="Open Sans",
                 color=YEO7_INFO[peak_net_key]["color"],
                 weight=BOLD).scale(0.38),
        ).arrange(DOWN, 0.05).move_to(RIGHT * cx + UP * cy)
        self.play(FadeIn(center_text), run_time=0.5)

        # Attribution
        attr = Text("Cortex · TRIBE v2 + Gemma 4 + Nous Hermes",
                    font="Open Sans", color=GRAY_D).scale(0.22)
        attr.to_corner(DR, buff=0.3)
        self.play(FadeIn(attr), run_time=0.3)
        self.wait(2)
'''

# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------

def _build_manim_script(
    bold: np.ndarray,
    peak_t: int | None,
    out_dir: Path,
) -> Path:
    """Inject real BOLD data into the Manim template and write a .py file."""
    traces = bold_to_network_traces(bold)

    # Convert traces to Python literal dict
    trace_str = "{\n"
    for key, arr in traces.items():
        vals = ", ".join(f"{v:.4f}" for v in arr.tolist())
        trace_str += f'        "{key}": [{vals}],\n'
    trace_str += "    }"

    # Peak-frame per-network activations
    if peak_t is not None and 0 <= peak_t < bold.shape[0]:
        peak_traces = {k: float(v[peak_t]) for k, v in traces.items()}
    else:
        peak_traces = {k: float(v.max()) for k, v in traces.items()}
    peak_str = "{\n"
    for key, val in peak_traces.items():
        peak_str += f'        "{key}": {val:.4f},\n'
    peak_str += "    }"

    script = MANIM_SCENES
    script = script.replace("{NET_TRACES_PLACEHOLDER}", trace_str)
    script = script.replace("{PEAK_T_PLACEHOLDER}", str(peak_t) if peak_t is not None else "None")
    script = script.replace("{PEAK_ACT_PLACEHOLDER}", peak_str)

    script_path = out_dir / "manim_bold_scene.py"
    script_path.write_text(script, encoding="utf-8")
    return script_path


def render_bold_explainer(
    bold_npy: str | Path,
    output_dir: str | Path = "D:/cortex/scans/manim",
    peak_t: int | None = None,
    scene: str = "BoldTimeseries",
    quality: str = "l",       # "l"=low (480p fast), "m"=medium (720p), "h"=high (1080p)
) -> Path | None:
    """Render a Manim brain activation explainer video.

    Args:
        bold_npy:   Path to BOLD .npy file (T x 20484 float32)
        output_dir: Directory for output video
        peak_t:     Peak activation frame index
        scene:      "BoldTimeseries" or "BrainNetworkDiagram"
        quality:    "l" | "m" | "h"

    Returns:
        Path to rendered .mp4, or None if manim is not installed.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        bold = np.load(bold_npy)
    except Exception:
        return None

    # Write Manim script to temp location
    script_path = _build_manim_script(bold, peak_t, output_dir)

    # Run manim
    cmd = [
        sys.executable, "-m", "manim",
        f"-p{quality}",              # quality flag
        "-o", f"{scene}.mp4",
        "--media_dir", str(output_dir),
        str(script_path),
        scene,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            import logging
            logging.getLogger("cortex-manim").warning(
                "Manim render failed: %s", result.stderr[-500:]
            )
            return None
    except FileNotFoundError:
        return None  # manim not installed
    except subprocess.TimeoutExpired:
        return None

    # Find output file
    for ext in ["mp4", "gif"]:
        candidate = output_dir / "videos" / "manim_bold_scene" / f"{quality}480p15" / f"{scene}.{ext}"
        if candidate.exists():
            return candidate

    return None
