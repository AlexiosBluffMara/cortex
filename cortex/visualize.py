"""Render TRIBE v2 predictions as publication-quality charts.

Functions:
  render_peak_cortex       — inflated cortex PNG at peak activity timestep
  render_roi_stream        — animated ROI bar chart + running mean (MP4/GIF)
  render_network_summary   — Yeo-7 network mean |z| bar chart (PNG)
  render_timeseries_panel  — top-6 ROI line plots, 2x3 grid (PNG)
  render_roi_heatmap       — time x ROI heatmap with peak marker (PNG)
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np

from . import config
from .pipeline import InferenceResult

# -- Yeo-7 network palette ----------------------------------------------------

_YEO7_COLOR = {
    "Vis":         "#781286",
    "SomMot":      "#4682B4",
    "DorsAttn":    "#00760E",
    "SalVentAttn": "#C43AFA",
    "Limbic":      "#DCF8A4",
    "Cont":        "#E69422",
    "Default":     "#CD3E4E",
}

_YEO7_LABEL = {
    "Vis":         "Visual",
    "SomMot":      "Somatomotor",
    "DorsAttn":    "Dorsal Attention",
    "SalVentAttn": "Salience / Ventral Attn",
    "Limbic":      "Limbic",
    "Cont":        "Frontoparietal Control",
    "Default":     "Default Mode",
}


def _roi_network(roi_name: str) -> str:
    """Extract Yeo-7 network code from a Schaefer-400 ROI label.

    Label format: '7Networks_LH_Vis_1'  ->  'Vis'
    """
    parts = roi_name.split("_")
    return parts[2] if len(parts) >= 3 else "Unknown"


def _roi_color(roi_name: str) -> str:
    return _YEO7_COLOR.get(_roi_network(roi_name), "#888888")


def _fig_footer(label: str) -> str:
    suffix = f" — {label}" if label else ""
    return f"TRIBE v2 group-averaged prediction · Schaefer-400 · Cortex{suffix}"


# -- 1. Peak cortex map --------------------------------------------------------

def render_peak_cortex(result: InferenceResult,
                       label: str = "",
                       out_path: Path | None = None) -> Path:
    """Static inflated cortex (LH + RH) at peak predicted activity timestep."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from nilearn import datasets, plotting

    fsavg5 = datasets.fetch_surf_fsaverage("fsaverage5")
    frame = result.preds[result.peak_t]
    lh, rh = frame[:10242], frame[10242:]

    fig = plt.figure(figsize=(14, 5), facecolor="#1a1a2e")
    ax1 = fig.add_subplot(1, 2, 1, projection="3d")
    plotting.plot_surf_stat_map(
        fsavg5.infl_left, lh, hemi="left", view="lateral",
        bg_map=fsavg5.sulc_left, cmap="cold_hot", axes=ax1,
        title=f"LH @ {result.peak_t / 2:.1f}s", colorbar=False,
    )
    ax2 = fig.add_subplot(1, 2, 2, projection="3d")
    plotting.plot_surf_stat_map(
        fsavg5.infl_right, rh, hemi="right", view="lateral",
        bg_map=fsavg5.sulc_right, cmap="cold_hot", axes=ax2,
        title=f"RH @ {result.peak_t / 2:.1f}s", colorbar=True,
    )
    title = f"TRIBE v2 — peak BOLD @ {result.peak_t / 2:.1f}s"
    if label:
        title += f"\n{label}"
    fig.suptitle(title, fontsize=11, color="white")
    fig.tight_layout()
    out_path = out_path or config.OUT_DIR / "brain_peak.png"
    fig.savefig(out_path, dpi=160, facecolor=fig.get_facecolor())
    plt.close(fig)
    return out_path


# -- 2. Streaming ROI animation ------------------------------------------------

def render_roi_stream(result: InferenceResult, out_path: Path | None = None) -> Path:
    """Animated panel: top-12 ROI bar chart + running mean line (MP4 or GIF fallback)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.animation import FFMpegWriter, FuncAnimation

    df = result.roi_df[result.top_rois]
    T = df.shape[0]
    colors = [_roi_color(r) for r in result.top_rois]

    fig = plt.figure(figsize=(14, 7), constrained_layout=True, facecolor="#1a1a2e")
    gs = fig.add_gridspec(2, 1, height_ratios=[2.2, 1.2])
    ax_bar = fig.add_subplot(gs[0, 0], facecolor="#0d0d1a")
    ax_ts = fig.add_subplot(gs[1, 0], facecolor="#0d0d1a")

    for ax in (ax_bar, ax_ts):
        ax.tick_params(colors="white")
        for spine in ax.spines.values():
            spine.set_edgecolor("#444")

    bars = ax_bar.barh(range(len(result.top_rois)), np.zeros(len(result.top_rois)), color=colors)
    ax_bar.set_yticks(range(len(result.top_rois)))
    ax_bar.set_yticklabels([r[:45] for r in result.top_rois], fontsize=7, color="white")
    ax_bar.set_xlim(float(df.min().min()) * 1.1, float(df.max().max()) * 1.1)
    ax_bar.invert_yaxis()
    ax_bar.set_title("Top-12 Schaefer-400 ROI activations", color="white", fontsize=10)
    ax_bar.axvline(0, color="#555", lw=0.8)

    (line,) = ax_ts.plot([], [], lw=1.5, color="#57F287")
    ax_ts.set_xlim(0, T / 2)
    mean_ts = df.mean(axis=1)
    ax_ts.set_ylim(mean_ts.min() * 1.1, mean_ts.max() * 1.1)
    ax_ts.set_xlabel("time (s)", color="white", fontsize=9)
    ax_ts.set_ylabel("mean top-12 ROI z", color="white", fontsize=9)
    ax_ts.axvline(result.peak_t / 2, color="#FEE75C", lw=1.2, ls="--", alpha=0.7,
                  label=f"peak {result.peak_t / 2:.1f}s")
    ax_ts.legend(fontsize=8, framealpha=0.3, labelcolor="white")

    fig.text(0.01, 0.01, _fig_footer(""), fontsize=6, color="#666")

    def update(t: int):
        vals = df.iloc[t].values
        for b, v in zip(bars, vals):
            b.set_width(float(v))
        line.set_data(np.arange(t + 1) / 2, mean_ts.iloc[: t + 1].values)
        return [*bars, line]

    anim = FuncAnimation(fig, update, frames=T, interval=500, blit=False)
    out_path = out_path or config.OUT_DIR / "tribev2_stream.mp4"
    try:
        anim.save(str(out_path), writer=FFMpegWriter(fps=2, bitrate=2400))
    except Exception as e:
        print(f"[visualize] FFmpeg unavailable ({e}); falling back to GIF.")
        out_path = out_path.with_suffix(".gif")
        anim.save(str(out_path), writer="pillow", fps=2)
    plt.close(fig)
    return out_path


# -- 3. Yeo-7 network summary bar chart ----------------------------------------

def render_network_summary(result: InferenceResult,
                           label: str = "",
                           out_path: Path | None = None) -> Path:
    """Horizontal bar chart: mean |z| per Yeo-7 network, aggregated from all ROIs."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import pandas as pd

    # Aggregate all ROIs (not just top_rois) by network
    network_means: dict[str, list[float]] = {k: [] for k in _YEO7_LABEL}
    network_means["Unknown"] = []
    for col in result.roi_df.columns:
        net = _roi_network(col)
        if net not in network_means:
            network_means[net] = []
        network_means[net].append(float(result.roi_df[col].abs().mean()))

    net_df = pd.Series({
        _YEO7_LABEL.get(k, k): (np.mean(v) if v else 0.0)
        for k, v in network_means.items()
        if v
    }).sort_values()

    fig, ax = plt.subplots(figsize=(10, 5), facecolor="#1a1a2e")
    ax.set_facecolor("#0d0d1a")
    bar_colors = [
        _YEO7_COLOR.get(
            next((k for k, v in _YEO7_LABEL.items() if v == name), ""), "#888888"
        )
        for name in net_df.index
    ]
    bars = ax.barh(net_df.index, net_df.values, color=bar_colors, edgecolor="#333", linewidth=0.5)
    ax.bar_label(bars, fmt="%.3f", padding=4, color="white", fontsize=8)
    ax.set_xlabel("mean |z-score| across all network ROIs", color="white", fontsize=9)
    ax.set_title(
        f"TRIBE v2 — Yeo-7 network activation summary\n{label}" if label
        else "TRIBE v2 — Yeo-7 network activation summary",
        color="white", fontsize=11,
    )
    ax.tick_params(colors="white", labelsize=9)
    for spine in ax.spines.values():
        spine.set_edgecolor("#444")
    ax.axvline(0, color="#555", lw=0.8)
    fig.text(0.01, 0.01, _fig_footer(label), fontsize=6, color="#555")
    fig.tight_layout()

    out_path = out_path or config.OUT_DIR / "network_summary.png"
    fig.savefig(out_path, dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)
    return out_path


# -- 4. Top-6 ROI time-series panel -------------------------------------------

def render_timeseries_panel(result: InferenceResult,
                            label: str = "",
                            scene_markers: Sequence[tuple[float, str]] = (),
                            out_path: Path | None = None) -> Path:
    """2x3 grid of line plots — top-6 ROIs, each color-coded by Yeo-7 network.

    scene_markers: list of (time_s, label) to mark on every subplot.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    top6 = result.top_rois[:6]
    time_axis = np.arange(len(result.roi_df)) / 2.0
    peak_s = result.peak_t / 2.0

    fig, axes = plt.subplots(2, 3, figsize=(15, 7), facecolor="#1a1a2e",
                             sharex=True, sharey=False)
    fig.subplots_adjust(hspace=0.45, wspace=0.35)

    for i, roi in enumerate(top6):
        ax = axes[i // 3][i % 3]
        ax.set_facecolor("#0d0d1a")
        color = _roi_color(roi)
        ts = result.roi_df[roi].values
        ax.plot(time_axis, ts, lw=1.2, color=color, alpha=0.9)
        ax.fill_between(time_axis, 0, ts, alpha=0.12, color=color)
        ax.axvline(peak_s, color="#FEE75C", lw=1.1, ls="--", alpha=0.75)
        for mk_t, mk_label in scene_markers:
            ax.axvline(mk_t, color="#aaa", lw=0.8, ls=":", alpha=0.6)
            ax.text(mk_t + 0.3, ax.get_ylim()[1] * 0.88, mk_label,
                    fontsize=5, color="#aaa", rotation=90)
        ax.axhline(0, color="#444", lw=0.6)
        ax.set_xlabel("time (s)", color="white", fontsize=7)
        ax.set_ylabel("z-score", color="white", fontsize=7)
        net = _YEO7_LABEL.get(_roi_network(roi), _roi_network(roi))
        ax.set_title(f"{roi[:35]}\n[{net}]", fontsize=7, color=color)
        ax.tick_params(colors="white", labelsize=7)
        for spine in ax.spines.values():
            spine.set_edgecolor("#444")

    # Hide unused panels if < 6 ROIs
    for j in range(len(top6), 6):
        axes[j // 3][j % 3].set_visible(False)

    suptitle = "TRIBE v2 — top-6 ROI time-series"
    if label:
        suptitle += f"\n{label}"
    fig.suptitle(suptitle, color="white", fontsize=11)
    fig.text(0.01, 0.005, _fig_footer(label), fontsize=6, color="#555")

    out_path = out_path or config.OUT_DIR / "roi_timeseries.png"
    fig.savefig(out_path, dpi=150, facecolor=fig.get_facecolor())
    plt.close(fig)
    return out_path


# -- 5. ROI heatmap ------------------------------------------------------------

def render_roi_heatmap(result: InferenceResult,
                       label: str = "",
                       n_rois: int = 16,
                       out_path: Path | None = None) -> Path:
    """Seaborn heatmap: top-N ROIs (rows) x time (columns), z-score color.

    Rows are ordered by mean |z|; columns downsampled to ~1s resolution.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import seaborn as sns

    top_n = result.top_rois[:n_rois]
    df = result.roi_df[top_n].T   # shape: (n_rois, T)

    # Downsample time axis to ~1s resolution (every 2 TRs @ 2 Hz)
    df_ds = df.iloc[:, ::2]
    col_labels = [f"{c / 2:.0f}s" for c in df_ds.columns]
    df_ds.columns = col_labels

    row_labels = [f"{r[:38]}  [{_YEO7_LABEL.get(_roi_network(r), '?')[:12]}]"
                  for r in top_n]
    df_ds.index = row_labels

    row_colors = [_roi_color(r) for r in top_n]

    fig, ax = plt.subplots(figsize=(16, max(6, n_rois * 0.45 + 1.5)), facecolor="#1a1a2e")
    ax.set_facecolor("#0d0d1a")

    sns.heatmap(
        df_ds.astype(float),
        ax=ax,
        cmap="RdBu_r",
        center=0,
        linewidths=0.2,
        linecolor="#222",
        cbar_kws={"shrink": 0.7, "label": "z-score"},
        xticklabels=True,
        yticklabels=True,
    )

    # Color-code y-axis tick labels by network
    for ytick, color in zip(ax.get_yticklabels(), row_colors):
        ytick.set_color(color)
        ytick.set_fontsize(7)

    ax.set_xticklabels(ax.get_xticklabels(), rotation=45, ha="right",
                       fontsize=7, color="white")

    # Mark peak column
    peak_col_idx = result.peak_t // 2
    if peak_col_idx < len(col_labels):
        ax.axvline(peak_col_idx + 0.5, color="#FEE75C", lw=1.5, ls="--", alpha=0.85)
        ax.text(peak_col_idx + 0.7, -0.8, f"peak\n{result.peak_t / 2:.0f}s",
                fontsize=7, color="#FEE75C", va="top")

    # Style colorbar
    cbar = ax.collections[0].colorbar
    if cbar:
        cbar.ax.yaxis.label.set_color("white")
        cbar.ax.tick_params(colors="white")

    title = f"TRIBE v2 — ROI activation heatmap (top {n_rois})"
    if label:
        title += f"\n{label}"
    ax.set_title(title, color="white", fontsize=11, pad=12)
    fig.text(0.01, 0.002, _fig_footer(label), fontsize=6, color="#555")
    fig.tight_layout()

    out_path = out_path or config.OUT_DIR / "roi_heatmap.png"
    fig.savefig(out_path, dpi=150, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    return out_path
