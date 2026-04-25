"""Cortex CLI — batch TRIBE v2 inference + Gemma narration from the terminal.

Usage:
    python -m cli.main infer video.mp4
    python -m cli.main infer video.mp4 --tier 6 --brainnetome
    python -m cli.main text "A documentary about ocean life"
    python -m cli.main narrate video.mp4 --all-tiers
    python -m cli.main vram
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import typer

app = typer.Typer(
    name="cortex",
    help="Cortex — TRIBE v2 brain prediction + Gemma 4 narration (RTX 5090 local pipeline)",
    no_args_is_help=True,
)


@app.command()
def infer(
    media: Path = typer.Argument(..., help="Path to video/audio/text file"),
    output: Path | None = typer.Option(None, "--output", "-o", help="Save predictions as .npy"),
    json_out: Path | None = typer.Option(None, "--json", "-j", help="Save analysis as JSON"),
    tier: int = typer.Option(2, "--tier", "-t", help="Narration tier (0=toddler, 6=researcher)"),
    all_tiers: bool = typer.Option(False, "--all-tiers", help="Generate all 7 narration tiers"),
    high_res: bool = typer.Option(False, "--high-res", help="Also compute Schaefer-1000"),
    brainnetome: bool = typer.Option(False, "--brainnetome", help="Include Brainnetome-246 atlas"),
    no_narrate: bool = typer.Option(False, "--no-narrate", help="Skip Gemma narration"),
    label: str = typer.Option("", "--label", "-l", help="Stimulus label for narration"),
):
    """Run TRIBE v2 inference on a media file, then optionally narrate with Gemma."""
    import numpy as np

    from cortex.analysis import analyse
    from cortex.pipeline import run_inference

    if not media.exists():
        typer.echo(f"File not found: {media}", err=True)
        raise typer.Exit(1)

    typer.echo(f"[cortex] Running inference on {media.name}...")
    result = run_inference(media)
    typer.echo(f"[cortex] Inference complete: {result.preds.shape} in {result.seconds_elapsed:.1f}s")

    # Analysis
    typer.echo("[cortex] Running multi-atlas analysis...")
    brain = analyse(result, high_res=high_res, brainnetome=brainnetome)
    typer.echo(f"[cortex] Analysis complete in {brain.analysis_seconds:.1f}s")
    typer.echo(f"[cortex] Dominant network: {brain.dominant_network}")
    typer.echo(f"[cortex] Peak at {brain.temporal.get('peak_s', 0):.1f}s "
               f"(z={brain.temporal.get('peak_z', 0):.3f})")

    # Save outputs
    if output:
        np.save(str(output), result.preds)
        typer.echo(f"[cortex] Predictions saved to {output}")

    if json_out:
        with open(json_out, "w") as f:
            json.dump(brain.to_dict(), f, indent=2, default=str)
        typer.echo(f"[cortex] Analysis JSON saved to {json_out}")

    # Narration
    if not no_narrate:
        from cortex.tiers import narrate_all_tiers, narrate_tier

        stim_label = label or media.stem
        ctx = brain.gemma_context()

        if all_tiers:
            typer.echo("[cortex] Generating all 7 narration tiers...")
            narrations = narrate_all_tiers(result, stim_label, brain_context=ctx)
            from cortex.prompts import EXPERTISE_LEVELS
            for t, text in narrations.items():
                typer.echo(f"\n{'='*60}")
                typer.echo(f"TIER {t}: {EXPERTISE_LEVELS[t]}")
                typer.echo(f"{'='*60}")
                typer.echo(text)
        else:
            typer.echo(f"[cortex] Generating tier {tier} narration...")
            text = narrate_tier(result, stim_label, tier, brain_context=ctx)
            typer.echo(f"\n{text}")


@app.command()
def text(
    description: str = typer.Argument(..., help="Text description to analyze"),
    tier: int = typer.Option(2, "--tier", "-t", help="Narration tier (0=toddler, 6=researcher)"),
    all_tiers: bool = typer.Option(False, "--all-tiers", help="Generate all 7 narration tiers"),
):
    """Run text-only TRIBE v2 inference (fast path, language networks only)."""
    from cortex.pipeline import run_inference_text_only
    from cortex.tiers import narrate_quick

    typer.echo(f"[cortex] Text-only inference: {description[:60]}...")
    result = run_inference_text_only(description)
    typer.echo(f"[cortex] Done: {result.preds.shape} in {result.seconds_elapsed:.1f}s")

    quick = narrate_quick(result, description)
    typer.echo(f"\n{quick}")

    if all_tiers:
        from cortex.analysis import analyse
        from cortex.prompts import EXPERTISE_LEVELS
        from cortex.tiers import narrate_all_tiers

        brain = analyse(result)
        ctx = brain.gemma_context()
        narrations = narrate_all_tiers(result, description[:50], brain_context=ctx)
        for t, txt in narrations.items():
            typer.echo(f"\n{'='*60}")
            typer.echo(f"TIER {t}: {EXPERTISE_LEVELS[t]}")
            typer.echo(f"{'='*60}")
            typer.echo(txt)


@app.command()
def vram():
    """Show current GPU VRAM usage."""
    from cortex.pipeline import vram_report
    report = vram_report()
    if not report:
        typer.echo("No CUDA GPU detected.")
        raise typer.Exit(1)
    typer.echo(f"GPU:       {report['gpu']}")
    typer.echo(f"Allocated: {report['allocated']:.2f} GB")
    typer.echo(f"Reserved:  {report['reserved']:.2f} GB")
    typer.echo(f"Total:     {report['total']:.2f} GB")
    typer.echo(f"Free:      {report['free']:.2f} GB")


@app.command()
def warmup():
    """Load the TRIBE v2 model and warm the CUDA graph."""
    from cortex.pipeline import load_model
    typer.echo("[cortex] Loading TRIBE v2 model...")
    t0 = time.time()
    load_model()
    typer.echo(f"[cortex] Model ready in {time.time() - t0:.1f}s")


@app.command()
def models():
    """Show Ollama model status and warm the fast model."""
    from cortex.model_manager import get_manager
    mm = get_manager()
    typer.echo("[cortex] Warming fast model (E4B)...")
    mm.warm_fast_model()
    typer.echo(mm.status_report())


@app.command()
def render(
    media: Path = typer.Argument(..., help="Path to video/audio/text file"),
    output_dir: Path | None = typer.Option(None, "--out-dir", "-d", help="Output directory"),
    label: str = typer.Option("", "--label", "-l", help="Stimulus label"),
):
    """Run inference and generate all visualization renders."""
    from cortex import config
    from cortex.pipeline import run_inference
    from cortex.visualize import (
        render_network_summary,
        render_peak_cortex,
        render_roi_heatmap,
        render_timeseries_panel,
    )

    out = output_dir or config.OUT_DIR
    out.mkdir(parents=True, exist_ok=True)

    typer.echo(f"[cortex] Running inference on {media.name}...")
    result = run_inference(media)

    typer.echo("[cortex] Generating renders...")
    p1 = render_peak_cortex(result, label=label, out_path=out / "brain_peak.png")
    typer.echo(f"  -> {p1}")
    p2 = render_network_summary(result, label=label, out_path=out / "network_summary.png")
    typer.echo(f"  -> {p2}")
    p3 = render_timeseries_panel(result, label=label, out_path=out / "roi_timeseries.png")
    typer.echo(f"  -> {p3}")
    p4 = render_roi_heatmap(result, label=label, out_path=out / "roi_heatmap.png")
    typer.echo(f"  -> {p4}")
    typer.echo("[cortex] All renders complete.")


if __name__ == "__main__":
    app()
