"""Back-compat narrate() kept for legacy callers; new code should use cortex.tiers."""
from __future__ import annotations

from collections.abc import Iterable

from . import ollama_client
from .prompts import PERSONA


def narrate(top_rois: list[str], roi_means: dict[str, float], stimulus_label: str,
            duration_s: float, peak_time_s: float) -> str:
    roi_lines = "\n".join(
        f"  - {r}: mean |z| = {roi_means.get(r, 0.0):.3f}"
        for r in list(top_rois)[:8]
    )
    user = (
        f"Stimulus: {stimulus_label}\n"
        f"Duration: {duration_s:.1f} s, peak activity at t={peak_time_s:.1f}s.\n"
        f"Top Schaefer-400 regions by mean |z|:\n{roi_lines}\n\n"
        "In 3-5 sentences, explain what this activation pattern suggests the "
        "brain is doing, grouping regions into networks where possible."
    )
    system = (
        f"{PERSONA}\n\n"
        "You are writing a single clinician-facing paragraph. Be concise, "
        "avoid hype, no bullet lists. End with a one-clause reminder that "
        "this is a group-averaged TRIBE v2 prediction, not a diagnostic result."
    )
    return ollama_client.generate(user, system, num_predict=400)


def _roi_summary(top_rois: Iterable[str], roi_means: dict[str, float]) -> str:
    return "\n".join(
        f"  - {r}: mean |z| = {roi_means.get(r, 0.0):.3f}"
        for r in list(top_rois)[:8]
    )
