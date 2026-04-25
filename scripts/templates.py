"""QA template engine for the synthetic neuroscience dataset (per SPEC §8).

Five template families, each with multiple stimulus prompts:

  A. activation_meaning  — "What does activation here mean?"
  B. stimulus_cause      — "What stimulus would cause this?"
  C. comparison          — "How do these two regions interact?"
  D. clinical            — "What might this pattern suggest clinically?"
  E. plain_english       — "Explain in everyday language"

Each template renders to a `(system_prompt, user_prompt)` pair plus a small
metadata dict that the generator stamps onto the dataset entry.

The system prompt is the same across templates: the Cortex persona constrained
to neuroscience translation. Templates differ only in the user prompt.
"""
from __future__ import annotations

import random
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum

from .regions import Region

SYSTEM_PROMPT = (
    "You are Cortex, a neuroscience research assistant trained on TRIBE v2 brain "
    "predictions. You translate cortical activation patterns into clear, accurate "
    "explanations at the requested expertise level. You always:\n"
    "  - Cite the specific brain region and its Yeo network membership\n"
    "  - Tie functional roles to the stimulus when one is given\n"
    "  - Caveat that TRIBE v2 predictions are population-averaged, not personal scans\n"
    "  - Decline to offer medical diagnosis or treatment recommendations\n"
    "  - Refuse to invent numbers the model did not produce\n"
)


class TemplateFamily(str, Enum):
    ACTIVATION_MEANING = "activation_meaning"
    STIMULUS_CAUSE = "stimulus_cause"
    COMPARISON = "comparison"
    CLINICAL = "clinical"
    PLAIN_ENGLISH = "plain_english"


@dataclass(frozen=True)
class TemplateInstance:
    family: TemplateFamily
    region_abbr: str
    user_prompt: str
    metadata: dict[str, str | float | int]


# ---------------------------------------------------------------------------
# Template builders
# ---------------------------------------------------------------------------

def _activation_meaning(region: Region, *, z_score: float, rng: random.Random) -> TemplateInstance:
    user = (
        f"The BOLD analysis shows significant activation (z = {z_score:.1f}) in "
        f"{region.name} ({region.abbreviation}). What does this indicate about "
        f"the subject's cortical response, and which functional role is most "
        f"likely being recruited?"
    )
    return TemplateInstance(
        family=TemplateFamily.ACTIVATION_MEANING,
        region_abbr=region.abbreviation,
        user_prompt=user,
        metadata={
            "region": region.name,
            "network": region.network.value,
            "brodmann": region.brodmann,
            "z_score": z_score,
        },
    )


def _stimulus_cause(region: Region, *, t_seconds: float, rng: random.Random) -> TemplateInstance:
    stimulus = rng.choice(region.stimuli)
    user = (
        f"A subject is watching {stimulus.lower()}. At t = {t_seconds:.1f}s the "
        f"BOLD prediction peaks in {region.name} ({region.abbreviation}). Why "
        f"would this region activate, and how does its function map onto the "
        f"stimulus features?"
    )
    return TemplateInstance(
        family=TemplateFamily.STIMULUS_CAUSE,
        region_abbr=region.abbreviation,
        user_prompt=user,
        metadata={
            "region": region.name,
            "network": region.network.value,
            "stimulus": stimulus,
            "peak_t_s": t_seconds,
        },
    )


def _comparison(region: Region, partner: Region, rng: random.Random) -> TemplateInstance:
    user = (
        f"The analysis shows simultaneous activation in {region.name} "
        f"({region.abbreviation}) and {partner.name} ({partner.abbreviation}). "
        f"How are these two regions functionally related — through shared "
        f"network membership, hierarchical processing, or task co-recruitment?"
    )
    return TemplateInstance(
        family=TemplateFamily.COMPARISON,
        region_abbr=region.abbreviation,
        user_prompt=user,
        metadata={
            "region": region.name,
            "network": region.network.value,
            "partner_region": partner.name,
            "partner_network": partner.network.value,
        },
    )


def _clinical(region: Region, *, direction: str, task: str, rng: random.Random) -> TemplateInstance:
    user = (
        f"A patient shows unusual {direction}-activation in {region.name} "
        f"({region.abbreviation}) during {task}. What might this suggest from a "
        f"clinical research perspective? Be careful to caveat the limits of "
        f"TRIBE v2 predictions and avoid diagnostic claims."
    )
    return TemplateInstance(
        family=TemplateFamily.CLINICAL,
        region_abbr=region.abbreviation,
        user_prompt=user,
        metadata={
            "region": region.name,
            "network": region.network.value,
            "direction": direction,
            "task": task,
        },
    )


def _plain_english(region: Region, *, activity: str, rng: random.Random) -> TemplateInstance:
    user = (
        f"In simple language for someone with no neuroscience background, "
        f"explain what it means when the {region.name} ({region.abbreviation}) "
        f"becomes more active while a person is {activity}. Use one analogy and "
        f"avoid jargon."
    )
    return TemplateInstance(
        family=TemplateFamily.PLAIN_ENGLISH,
        region_abbr=region.abbreviation,
        user_prompt=user,
        metadata={
            "region": region.name,
            "network": region.network.value,
            "activity": activity,
        },
    )


# ---------------------------------------------------------------------------
# Per-region generation
# ---------------------------------------------------------------------------

_DIRECTIONS = ("hyper", "hypo")
_CLINICAL_TASKS = (
    "a working-memory task",
    "viewing emotional faces",
    "passive listening to speech",
    "a finger-tapping motor task",
    "resting-state baseline",
)
_PLAIN_ACTIVITIES = (
    "watching a movie scene",
    "listening to music",
    "remembering a childhood event",
    "solving an arithmetic problem",
    "feeling startled",
)


def per_region_examples(
    region: Region,
    *,
    n_per_family: int,
    rng: random.Random,
    partner_pool: tuple[Region, ...],
) -> Iterator[TemplateInstance]:
    """Yield `5 * n_per_family` TemplateInstances for one region.

    Spec target is 100 examples per region (20 per family). Lower counts are
    used in tests and dev runs; higher counts approach the upper bound of what
    a single region's metadata can support without repetition.
    """
    if n_per_family <= 0:
        return

    for _ in range(n_per_family):
        z = round(rng.uniform(2.0, 5.5), 1)
        yield _activation_meaning(region, z_score=z, rng=rng)

    for _ in range(n_per_family):
        t = round(rng.uniform(2.0, 48.0), 1)
        yield _stimulus_cause(region, t_seconds=t, rng=rng)

    # Comparison templates need a partner — prefer same-network for sane co-activation
    same_net = tuple(p for p in partner_pool if p.network is region.network and p is not region)
    cross_net = tuple(p for p in partner_pool if p.network is not region.network)
    for _ in range(n_per_family):
        if same_net and rng.random() < 0.7:
            partner = rng.choice(same_net)
        elif cross_net:
            partner = rng.choice(cross_net)
        else:
            continue  # only one region in pool — skip comparison family
        yield _comparison(region, partner, rng=rng)

    for _ in range(n_per_family):
        yield _clinical(
            region,
            direction=rng.choice(_DIRECTIONS),
            task=rng.choice(_CLINICAL_TASKS),
            rng=rng,
        )

    for _ in range(n_per_family):
        yield _plain_english(
            region,
            activity=rng.choice(_PLAIN_ACTIVITIES),
            rng=rng,
        )


__all__ = [
    "SYSTEM_PROMPT",
    "TemplateFamily",
    "TemplateInstance",
    "per_region_examples",
]
