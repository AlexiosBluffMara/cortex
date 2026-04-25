"""
Seven-tier Gemma narration of a TRIBE v2 inference result.

Tier -> Model mapping (3-tier model architecture):
  Tiers 0-1 (toddler, general adult) -> FAST model (E4B, always warm)
  Tiers 2-4 (curious adult through college) -> DEEP model (26B MoE)
  Tiers 5-6 (clinician, researcher) -> EXPERT model (31B dense)

This ensures maximum quality where it matters (expert tiers) while keeping
the interactive experience fast for casual users (E4B at 197 tok/s).

Backwards-compatible: narrate_tiered() still returns TieredNarration.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import config, ollama_client, prompts
from .pipeline import InferenceResult

# -- Tier -> Model mapping -----------------------------------------------------

# Which tiers use which model tier
_TIER_MODEL_MAP: dict[int, str] = {
    0: config.OLLAMA_MODEL_FAST,    # Toddler: quick, simple
    1: config.OLLAMA_MODEL_FAST,    # General adult: quick
    2: config.OLLAMA_MODEL_DEEP,    # Curious adult: richer analysis
    3: config.OLLAMA_MODEL_DEEP,    # High school: moderate depth
    4: config.OLLAMA_MODEL_DEEP,    # College: good depth
    5: config.OLLAMA_MODEL_EXPERT,  # Clinician: maximum quality
    6: config.OLLAMA_MODEL_EXPERT,  # Researcher: maximum quality
}

# num_predict budget per tier (tokens to generate)
_TIER_NUM_PREDICT: list[int] = [120, 180, 300, 350, 420, 550, 700]

# Temperature per tier (lower = more factual, higher = more creative)
_TIER_TEMPERATURE: list[float] = [0.55, 0.5, 0.4, 0.4, 0.38, 0.32, 0.28]

# num_ctx per tier (context window -- larger models can handle more)
_TIER_NUM_CTX: list[int] = [
    4096,   # 0: simple, small context
    4096,   # 1: simple
    8192,   # 2: moderate
    8192,   # 3: moderate
    12288,  # 4: fuller context
    16384,  # 5: clinician -- full BrainAnalysis context
    32768,  # 6: researcher -- maximum context
]

# keep_alive per model -- FAST stays warm, DEEP/EXPERT maintained by model_manager
_TIER_KEEP_ALIVE: list[str] = [
    '60m', '60m',    # FAST always warm
    '10m', '10m', '10m',  # DEEP held for session
    '10m', '10m',    # EXPERT held for session
]


# -- Data types ----------------------------------------------------------------

@dataclass
class TieredNarration:
    """Three-tier narration for backwards compatibility."""
    layperson:  str
    clinician:  str
    researcher: str

    def as_dict(self) -> dict[str, str]:
        return {
            'layperson':  self.layperson,
            'clinician':  self.clinician,
            'researcher': self.researcher,
        }


# -- Prompt builders -----------------------------------------------------------

def _roi_lines(result: InferenceResult, n: int = 8) -> str:
    means = result.roi_df[result.top_rois].abs().mean()
    return '\n'.join(
        f'  - {roi}: mean |z| = {float(means[roi]):.3f}'
        for roi in result.top_rois[:n]
    )


def _build_user_prompt(label: str, brain_context: str) -> str:
    return prompts.TIER_USER_TEMPLATE.format(
        label=label,
        brain_context=brain_context,
    )


def _legacy_user_prompt(result: InferenceResult, label: str) -> str:
    return (
        f'Stimulus: {label}\n\n'
        f'Brain response data:\n'
        f'Duration: {result.preds.shape[0] / 2.0:.1f}s. '
        f'Peak activity at t={result.peak_t / 2.0:.1f}s.\n'
        f'Top Schaefer-400 ROIs (mean |z|):\n{_roi_lines(result)}\n\n'
        'Write the narration for your assigned audience. '
        'Stick strictly to your tier\'s register and rules.'
    )


# -- Core narration functions --------------------------------------------------

def narrate_tier(
    result: InferenceResult,
    label: str,
    tier: int,
    brain_context: str | None = None,
) -> str:
    """
    Generate narration for a single expertise tier (0-6).

    Automatically selects the appropriate Gemma model:
      Tiers 0-1 -> E4B (fast, always warm)
      Tiers 2-4 -> 26B MoE (standard analysis)
      Tiers 5-6 -> 31B dense (expert quality)
    """
    tier          = max(0, min(6, tier))
    system_prompt = prompts.ALL_TIER_SYSTEMS[tier]
    user_prompt   = (
        _build_user_prompt(label, brain_context)
        if brain_context else
        _legacy_user_prompt(result, label)
    )

    return ollama_client.generate(
        prompt=user_prompt,
        system=system_prompt,
        model=_TIER_MODEL_MAP[tier],
        temperature=_TIER_TEMPERATURE[tier],
        num_predict=_TIER_NUM_PREDICT[tier],
        num_ctx=_TIER_NUM_CTX[tier],
        keep_alive=_TIER_KEEP_ALIVE[tier],
        think=False,
    )


def narrate_tiered(
    result: InferenceResult,
    label: str,
    brain_context: str | None = None,
) -> TieredNarration:
    """
    Three-tier narration (tiers 2, 5, 6) for backwards compatibility.
    Uses 26B for layperson, 31B for clinician and researcher.
    """
    user = (
        _build_user_prompt(label, brain_context)
        if brain_context else
        _legacy_user_prompt(result, label)
    )

    def _call(system_prompt: str, tier: int) -> str:
        return ollama_client.generate(
            prompt=user,
            system=system_prompt,
            model=_TIER_MODEL_MAP[tier],
            temperature=_TIER_TEMPERATURE[tier],
            num_predict=_TIER_NUM_PREDICT[tier],
            num_ctx=_TIER_NUM_CTX[tier],
            keep_alive=_TIER_KEEP_ALIVE[tier],
            think=False,
        )

    return TieredNarration(
        layperson  = _call(prompts.TIER_LAYPERSON_SYSTEM,  2),
        clinician  = _call(prompts.TIER_CLINICIAN_SYSTEM,  5),
        researcher = _call(prompts.TIER_RESEARCHER_SYSTEM, 6),
    )


def narrate_quick(result: InferenceResult, description: str) -> str:
    """
    One-paragraph quick narration from the text-only fast path.
    Always uses FAST model (E4B, always warm).
    """
    user = prompts.QUICK_NARRATION_USER.format(
        description=description,
        roi_lines=_roi_lines(result, n=5),
        peak_s=result.peak_t / 2.0,
        duration_s=result.preds.shape[0] / 2.0,
    )
    return ollama_client.generate(
        prompt=user,
        system=prompts.QUICK_NARRATION_SYSTEM,
        model=config.OLLAMA_MODEL_FAST,
        temperature=0.4,
        num_predict=220,
        num_ctx=4096,
        keep_alive='60m',
        think=False,
    )


def narrate_all_tiers(
    result: InferenceResult,
    label: str,
    brain_context: str | None = None,
) -> dict[int, str]:
    """
    Generate all 7 tiers in sequence.
    Tiers 0-1 use E4B; 2-4 use 26B; 5-6 use 31B.
    Total time estimate: ~3-5 min on RTX 5090.
    """
    return {
        tier: narrate_tier(result, label, tier, brain_context)
        for tier in range(7)
    }
