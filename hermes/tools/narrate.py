"""
Hermes Agent tool: Narrate

Generate multi-tier text narrations of TRIBE v2 brain scan results.
Runs entirely on Gemma (no TRIBE v2 needed — works on cached results).

Supports all 7 expertise tiers:
  0 = toddler (3-5 years)
  1 = grandparent (no science background)
  2 = curious adult (general public)
  3 = high school student
  4 = college-educated adult
  5 = clinician (neurologist, psychiatrist)
  6 = researcher (neuroscience / ML scientist)
"""
from __future__ import annotations

TOOL_SCHEMA = {
    "name": "narrate",
    "description": (
        "Generate a text narration of a brain scan result at any expertise level. "
        "Can narrate a single tier or all 7 tiers at once. Does not require "
        "TRIBE v2 — uses cached brain scan data."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "scan_data": {
                "type": "object",
                "description": "Brain scan result dict from brain_scan tool (must include top_rois, peak_frame)",
            },
            "tier": {
                "type": "integer",
                "description": "Single tier to narrate (0-6). If omitted, narrates tiers 2, 5, 6.",
                "minimum": 0,
                "maximum": 6,
            },
            "all_tiers": {
                "type": "boolean",
                "description": "Generate all 7 tiers (overrides single tier)",
                "default": False,
            },
            "label": {
                "type": "string",
                "description": "Short description of the stimulus (e.g. 'educational video about volcanoes')",
                "default": "video stimulus",
            },
        },
        "required": ["scan_data"],
    },
}


async def execute(
    scan_data: dict,
    tier: int | None = None,
    all_tiers: bool = False,
    label: str = "video stimulus",
) -> dict:
    """Generate narrations from cached scan data."""
    import asyncio

    from cortex.gpu_scheduler import get_scheduler
    from cortex.logger import log

    # Validate scan data
    if not scan_data.get("ok"):
        return {"ok": False, "error": "invalid_scan", "message": "Scan data indicates a failed scan"}

    top_rois = scan_data.get("top_rois", [])
    peak_frame = scan_data.get("peak_frame", 0)
    num_timepoints = scan_data.get("num_timepoints", 100)

    if not top_rois:
        return {"ok": False, "error": "no_rois", "message": "Scan data has no ROI information"}

    # Ensure Gemma is loaded
    scheduler = get_scheduler()
    await scheduler.ensure_gemma()

    # Build a synthetic InferenceResult-like context for the narration prompts
    brain_context = _build_brain_context(top_rois, peak_frame, num_timepoints)

    loop = asyncio.get_event_loop()

    if all_tiers:
        # Generate all 7 tiers
        narrations = {}
        for t in range(7):
            try:
                narration = await loop.run_in_executor(
                    None,
                    lambda t=t: _narrate_from_context(brain_context, label, t),
                )
                narrations[t] = narration
            except Exception as exc:
                log.warning("[narrate] Tier %d failed: %s", t, exc)
                narrations[t] = f"(Tier {t} unavailable: {exc})"

        return {"ok": True, "narrations": narrations, "tiers_generated": list(narrations.keys())}

    else:
        # Single tier (default: tier 2 = curious adult)
        t = tier if tier is not None else 2
        try:
            narration = await loop.run_in_executor(
                None,
                lambda: _narrate_from_context(brain_context, label, t),
            )
            return {"ok": True, "narration": narration, "tier": t}
        except Exception as exc:
            return {"ok": False, "error": "narration_failed", "message": str(exc)}


def _build_brain_context(top_rois: list[str], peak_frame: int, num_timepoints: int) -> str:
    """Build a brain context string from cached scan data for the prompts."""
    peak_s = peak_frame / 2.0
    duration_s = num_timepoints / 2.0

    lines = [
        f"Duration: {duration_s:.1f}s. Peak activity at t={peak_s:.1f}s.",
        "Top Schaefer-400 ROIs (by activation strength):",
    ]
    for i, roi in enumerate(top_rois[:8], 1):
        lines.append(f"  {i}. {roi}")

    return "\n".join(lines)


def _narrate_from_context(brain_context: str, label: str, tier: int) -> str:
    """Generate a single narration using the prompts and ollama_client."""
    from cortex import config, ollama_client, prompts

    tier = max(0, min(6, tier))

    # Tier → model mapping
    tier_model = {
        0: config.OLLAMA_MODEL_FAST,
        1: config.OLLAMA_MODEL_FAST,
        2: config.OLLAMA_MODEL_DEEP,
        3: config.OLLAMA_MODEL_DEEP,
        4: config.OLLAMA_MODEL_DEEP,
        5: config.OLLAMA_MODEL_EXPERT,
        6: config.OLLAMA_MODEL_EXPERT,
    }

    tier_temp = [0.55, 0.5, 0.4, 0.4, 0.38, 0.32, 0.28]
    tier_predict = [120, 180, 300, 350, 420, 550, 700]

    system_prompt = prompts.ALL_TIER_SYSTEMS[tier]
    user_prompt = (
        f"Stimulus: {label}\n\n"
        f"Brain response data:\n{brain_context}\n\n"
        "Write the narration for your assigned audience. "
        "Stick strictly to your tier's register and rules."
    )

    return ollama_client.generate(
        prompt=user_prompt,
        system=system_prompt,
        model=tier_model[tier],
        temperature=tier_temp[tier],
        num_predict=tier_predict[tier],
        think=False,
    )
