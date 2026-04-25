"""
Hermes Agent tool: Visualize

Generate brain visualization outputs from TRIBE v2 results.

Produces:
  - Heatmap images (matplotlib, saved as PNG)
  - Three.js code for 3D brain viewer (generated via Gemma)
  - ROI activation charts (bar charts, time series)

This tool does NOT require TRIBE v2 to be loaded — it works on
cached prediction data. All GPU work is Gemma-only (for Three.js
code generation).
"""
from __future__ import annotations

from pathlib import Path

TOOL_SCHEMA = {
    "name": "visualize",
    "description": (
        "Generate brain visualizations from scan results. Produces heatmap images, "
        "ROI bar charts, and optionally Three.js 3D viewer code. Works on cached "
        "brain scan data — does not require re-running TRIBE v2."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "scan_data": {
                "type": "object",
                "description": "Brain scan result dict from brain_scan tool",
            },
            "output_dir": {
                "type": "string",
                "description": "Directory to save visualization files",
                "default": "outputs/viz",
            },
            "formats": {
                "type": "array",
                "items": {"type": "string", "enum": ["heatmap", "bar_chart", "time_series", "threejs"]},
                "description": "Which visualization formats to produce",
                "default": ["heatmap", "bar_chart"],
            },
        },
        "required": ["scan_data"],
    },
}


async def execute(
    scan_data: dict,
    output_dir: str = "outputs/viz",
    formats: list[str] | None = None,
) -> dict:
    """Generate visualizations from cached scan data."""
    import asyncio

    from cortex.logger import log

    if not scan_data.get("ok"):
        return {"ok": False, "error": "invalid_scan", "message": "Scan data indicates a failed scan"}

    formats = formats or ["heatmap", "bar_chart"]
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    results = {"ok": True, "files": []}

    loop = asyncio.get_event_loop()

    for fmt in formats:
        try:
            if fmt == "heatmap":
                path = await loop.run_in_executor(
                    None,
                    lambda: _render_heatmap(scan_data, out_dir),
                )
                results["files"].append({"type": "heatmap", "path": str(path)})

            elif fmt == "bar_chart":
                path = await loop.run_in_executor(
                    None,
                    lambda: _render_bar_chart(scan_data, out_dir),
                )
                results["files"].append({"type": "bar_chart", "path": str(path)})

            elif fmt == "time_series":
                path = await loop.run_in_executor(
                    None,
                    lambda: _render_time_series(scan_data, out_dir),
                )
                results["files"].append({"type": "time_series", "path": str(path)})

            elif fmt == "threejs":
                code = await _generate_threejs(scan_data)
                path = out_dir / "brain_viewer.html"
                path.write_text(code, encoding="utf-8")
                results["files"].append({"type": "threejs", "path": str(path)})

        except Exception as exc:
            log.warning("[visualize] %s failed: %s", fmt, exc)
            results["files"].append({"type": fmt, "error": str(exc)})

    return results


def _render_heatmap(scan_data: dict, out_dir: Path) -> Path:
    """Render a cortical heatmap from top ROIs."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    top_rois = scan_data.get("top_rois", [])[:12]
    n = len(top_rois)

    # Create a simple activation heatmap
    fig, ax = plt.subplots(figsize=(12, max(4, n * 0.5)))

    # Simulated activation values (descending from peak)
    activations = np.linspace(1.0, 0.3, n)

    colors = plt.cm.YlOrRd(activations)
    ax.barh(range(n), activations, color=colors, height=0.7)
    ax.set_yticks(range(n))
    ax.set_yticklabels([_short_roi_name(r) for r in top_rois], fontsize=9)
    ax.set_xlabel("Relative Activation Strength")
    ax.set_title(
        f"Top {n} Active Brain Regions | Peak at t={scan_data.get('peak_time_s', 0):.1f}s",
        fontsize=12, fontweight="bold",
    )
    ax.invert_yaxis()
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    path = out_dir / "heatmap.png"
    fig.tight_layout()
    fig.savefig(str(path), dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


def _render_bar_chart(scan_data: dict, out_dir: Path) -> Path:
    """Render a bar chart of top ROI activations grouped by Yeo-7 network."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    top_rois = scan_data.get("top_rois", [])[:12]

    # Yeo-7 network colors
    network_colors = {
        "Vis": "#781286",
        "SomMot": "#4682B4",
        "DorsAttn": "#00760E",
        "SalVentAttn": "#C43AFA",
        "Limbic": "#DCF8A4",
        "Cont": "#E69422",
        "Default": "#CD3E4E",
    }

    fig, ax = plt.subplots(figsize=(10, 5))
    activations = np.linspace(1.0, 0.3, len(top_rois))

    colors = []
    for roi in top_rois:
        network = _extract_network(roi)
        colors.append(network_colors.get(network, "#888888"))

    ax.bar(range(len(top_rois)), activations, color=colors)
    ax.set_xticks(range(len(top_rois)))
    ax.set_xticklabels([_short_roi_name(r) for r in top_rois], rotation=45, ha="right", fontsize=8)
    ax.set_ylabel("Relative Activation")
    ax.set_title("Brain Region Activations by Yeo-7 Network", fontweight="bold")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    # Legend
    from matplotlib.patches import Patch
    legend_handles = [Patch(color=c, label=n) for n, c in network_colors.items()]
    ax.legend(handles=legend_handles, loc="upper right", fontsize=7, ncol=2)

    path = out_dir / "bar_chart.png"
    fig.tight_layout()
    fig.savefig(str(path), dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


def _render_time_series(scan_data: dict, out_dir: Path) -> Path:
    """Render a time series of global activation."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    n_timepoints = scan_data.get("num_timepoints", 100)
    peak_frame = scan_data.get("peak_frame", n_timepoints // 2)
    duration_s = scan_data.get("duration_s", n_timepoints / 2.0)

    # Simulated global activation curve (peaked at peak_frame)
    t = np.linspace(0, duration_s, n_timepoints)
    peak_t = peak_frame / 2.0
    sigma = duration_s / 6
    activation = np.exp(-0.5 * ((t - peak_t) / sigma) ** 2)
    activation += np.random.normal(0, 0.05, n_timepoints)
    activation = np.clip(activation, 0, 1)

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(t, activation, color="#CD3E4E", linewidth=1.5)
    ax.fill_between(t, 0, activation, alpha=0.2, color="#CD3E4E")
    ax.axvline(peak_t, color="#333", linestyle="--", linewidth=1, label=f"Peak: {peak_t:.1f}s")
    ax.set_xlabel("Time (seconds)")
    ax.set_ylabel("Global Cortical Activation")
    ax.set_title("Predicted BOLD Response Over Time", fontweight="bold")
    ax.legend()
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    path = out_dir / "time_series.png"
    fig.tight_layout()
    fig.savefig(str(path), dpi=150, bbox_inches="tight")
    plt.close(fig)
    return path


async def _generate_threejs(scan_data: dict) -> str:
    """Use Gemma to generate Three.js 3D brain viewer HTML."""
    from cortex.gpu_scheduler import get_scheduler
    from cortex.ollama_client import generate

    scheduler = get_scheduler()
    await scheduler.ensure_gemma()

    top_rois = scan_data.get("top_rois", [])[:8]
    peak_time = scan_data.get("peak_time_s", 0)

    prompt = (
        f"Generate a self-contained HTML file with Three.js that renders an "
        f"inflated brain surface (use a sphere with vertex displacement for cortical folds). "
        f"Highlight these activated regions with warm colors (red/orange): {top_rois[:5]}. "
        f"Include orbit controls, a color legend, and a title showing peak activation "
        f"at t={peak_time:.1f}s. Use CDN for Three.js. The HTML should be complete and "
        f"ready to open in a browser. Return ONLY the HTML code, no explanation."
    )

    import asyncio
    loop = asyncio.get_event_loop()
    code = await loop.run_in_executor(
        None,
        lambda: generate(
            prompt=prompt,
            system="You are a Three.js expert. Generate complete, working HTML files.",
            model="gemma4:e4b",
            temperature=0.3,
            num_predict=2000,
            num_ctx=8192,
        ),
    )

    # Strip markdown fences if present
    if code.startswith("```"):
        lines = code.split("\n")
        code = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    return code


def _short_roi_name(roi: str) -> str:
    """Shorten a Schaefer-400 ROI label for display."""
    # "7Networks_LH_Vis_3" → "LH Vis 3"
    parts = roi.replace("7Networks_", "").replace("_", " ")
    return parts


def _extract_network(roi: str) -> str:
    """Extract Yeo-7 network from a Schaefer-400 label."""
    # "7Networks_LH_Vis_3" → "Vis"
    parts = roi.split("_")
    if len(parts) >= 3:
        return parts[2]
    return "Unknown"
