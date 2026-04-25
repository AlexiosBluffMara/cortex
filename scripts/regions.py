"""Brain region definitions for the synthetic neuroscience QA dataset.

The Cortex fine-tune (`cortex-gemma-4-e4b`) is trained to interpret TRIBE v2
BOLD activation in terms of canonical brain regions. This module defines the
target regions, their networks (per the Yeo 7-network parcellation), Brodmann
areas, known functions, common stimuli, and clinical notes.

Sources used to ground each entry:
  - Kandel, Schwartz, Jessell — *Principles of Neural Science* (5th ed.)
  - Schaefer 400 atlas (Schaefer et al., 2018, Cerebral Cortex)
  - Neurosynth meta-analyses (neurosynth.org) for stimulus-region mappings
  - Yeo et al. (2011) for network assignments

The full target set is 50 regions per SPEC §8. This module starts with the
core 15 spanning all 8 network families; the remaining 35 are tracked as
follow-on work and added incrementally as Neurosynth-validated entries.

Each `Region` is keyed by a stable canonical name. The script generator
attaches a `region_id` deterministically by hashing the name, so adding new
regions never reshuffles existing example IDs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Network(str, Enum):
    VISUAL = "visual"
    AUDITORY = "auditory"
    DEFAULT_MODE = "default_mode"
    FRONTOPARIETAL = "frontoparietal"
    SOMATOMOTOR = "somatomotor"
    DORSAL_ATTENTION = "dorsal_attention"
    VENTRAL_ATTENTION = "ventral_attention"
    LIMBIC = "limbic"


@dataclass(frozen=True)
class Region:
    name: str                       # canonical, human-readable
    abbreviation: str               # short tag (V1, FFA, mPFC)
    network: Network
    brodmann: str                   # e.g. "BA17" or "BA17/18"
    functions: tuple[str, ...]      # 3-5 attested functional roles
    stimuli: tuple[str, ...]        # 3-5 stimuli that reliably activate it
    clinical: tuple[str, ...] = field(default_factory=tuple)  # 1-3 clinical notes
    schaefer_ids: tuple[int, ...] = field(default_factory=tuple)  # parcel IDs (filled later)

    def slug(self) -> str:
        """Stable filesystem-safe identifier used in dataset IDs and filenames."""
        return self.abbreviation.lower().replace(" ", "_").replace("/", "_")


REGIONS: tuple[Region, ...] = (
    # ---------------------------------------------------------------------
    # VISUAL NETWORK
    # ---------------------------------------------------------------------
    Region(
        name="Primary Visual Cortex",
        abbreviation="V1",
        network=Network.VISUAL,
        brodmann="BA17",
        functions=(
            "Edge detection and orientation selectivity",
            "Contrast sensitivity at all spatial frequencies",
            "Retinotopic mapping of the visual field",
            "First cortical relay for nearly all visual information",
        ),
        stimuli=(
            "Any visible stimulus enters here first",
            "High-contrast gratings and sinusoidal patterns",
            "Moving edges and oriented lines",
            "Flashing or flickering lights",
        ),
        clinical=(
            "Lesions cause cortical blindness (sometimes preserving 'blindsight')",
            "Hyperactivation in visual hallucinations and migraine aura",
        ),
    ),
    Region(
        name="Extrastriate Visual Cortex (V2)",
        abbreviation="V2",
        network=Network.VISUAL,
        brodmann="BA18",
        functions=(
            "Integrates V1 outputs into illusory contours and figure-ground",
            "Encodes complex shapes and texture boundaries",
            "Supports binocular disparity for depth",
        ),
        stimuli=(
            "Illusory contours (e.g. Kanizsa triangle)",
            "Random-dot stereograms",
            "Textured patterns and natural images",
        ),
        clinical=("Reduced activation in visual agnosia",),
    ),
    Region(
        name="Color-selective Cortex (V4)",
        abbreviation="V4",
        network=Network.VISUAL,
        brodmann="BA19",
        functions=(
            "Wavelength-selective color processing",
            "Color constancy under changing illumination",
            "Form processing for curved shapes",
        ),
        stimuli=(
            "Color-rich Mondrian patterns",
            "Color-changing or chromatic stimuli",
            "Saturated colored objects",
        ),
        clinical=("Lesions cause achromatopsia (cortical color blindness)",),
    ),
    Region(
        name="Middle Temporal area (MT/V5)",
        abbreviation="MT",
        network=Network.VISUAL,
        brodmann="BA19/37",
        functions=(
            "Detects coherent motion direction and speed",
            "Optic flow and self-motion estimation",
            "Smooth-pursuit eye movement guidance",
        ),
        stimuli=(
            "Random-dot kinematograms",
            "Moving objects across the visual field",
            "Optic flow during locomotion footage",
        ),
        clinical=("Lesions cause akinetopsia (inability to perceive motion)",),
    ),
    Region(
        name="Fusiform Face Area",
        abbreviation="FFA",
        network=Network.VISUAL,
        brodmann="BA37",
        functions=(
            "Face perception and identity recognition",
            "Holistic processing of facial configuration",
            "Expert-level visual category discrimination",
        ),
        stimuli=(
            "Human faces, especially close-ups",
            "Face-like patterns (pareidolia)",
            "Familiar versus unfamiliar face contrasts",
        ),
        clinical=(
            "Lesions cause prosopagnosia (face blindness)",
            "Reduced activation reported in autism-spectrum populations",
        ),
    ),
    Region(
        name="Parahippocampal Place Area",
        abbreviation="PPA",
        network=Network.VISUAL,
        brodmann="BA36",
        functions=(
            "Encodes spatial layouts and scene geometry",
            "Recognition of indoor and outdoor environments",
            "Stable place encoding across viewpoints",
        ),
        stimuli=(
            "Photographs of buildings and rooms",
            "Outdoor landscapes",
            "Topographical scene transitions",
        ),
        clinical=("Lesions impair spatial navigation memory",),
    ),
    # ---------------------------------------------------------------------
    # AUDITORY NETWORK
    # ---------------------------------------------------------------------
    Region(
        name="Primary Auditory Cortex (A1)",
        abbreviation="A1",
        network=Network.AUDITORY,
        brodmann="BA41",
        functions=(
            "Tonotopic frequency analysis of incoming sound",
            "Initial cortical processing of pitch and amplitude",
            "Temporal envelope tracking",
        ),
        stimuli=(
            "Pure tones across the audible spectrum",
            "Speech and music onsets",
            "Click trains and amplitude-modulated noise",
        ),
        clinical=("Lesions cause cortical deafness or auditory agnosia",),
    ),
    Region(
        name="Superior Temporal Gyrus",
        abbreviation="STG",
        network=Network.AUDITORY,
        brodmann="BA22",
        functions=(
            "Phonological processing of speech sounds",
            "Voice and timbre recognition",
            "Auditory scene analysis",
        ),
        stimuli=(
            "Spoken speech (especially native language)",
            "Familiar voices",
            "Complex auditory scenes with multiple sources",
        ),
        clinical=("Anterior STG lesions disrupt speech comprehension (Wernicke-type)",),
    ),
    # ---------------------------------------------------------------------
    # DEFAULT MODE NETWORK
    # ---------------------------------------------------------------------
    Region(
        name="Medial Prefrontal Cortex",
        abbreviation="mPFC",
        network=Network.DEFAULT_MODE,
        brodmann="BA10/32",
        functions=(
            "Self-referential processing and trait judgments",
            "Theory of mind and social inference",
            "Value-based decision making",
        ),
        stimuli=(
            "Tasks requiring introspection or self-judgments",
            "Watching socially complex narratives",
            "Reading mental-state language",
        ),
        clinical=(
            "Hyperactivation associated with rumination in depression",
            "Hypoactivation reported in autism-spectrum populations",
        ),
    ),
    Region(
        name="Posterior Cingulate Cortex",
        abbreviation="PCC",
        network=Network.DEFAULT_MODE,
        brodmann="BA23/31",
        functions=(
            "Hub of the default mode network",
            "Autobiographical memory retrieval",
            "Internally directed attention and mind-wandering",
        ),
        stimuli=(
            "Resting-state baseline activity",
            "Recall of personal memories",
            "Watching emotionally familiar content",
        ),
        clinical=("Reduced PCC connectivity is an early marker in Alzheimer's disease",),
    ),
    Region(
        name="Hippocampus",
        abbreviation="Hipp",
        network=Network.DEFAULT_MODE,
        brodmann="BA28/35/36",
        functions=(
            "Episodic memory encoding and retrieval",
            "Spatial navigation via place cells and grid cells",
            "Pattern separation and completion",
        ),
        stimuli=(
            "Novel scenes triggering encoding",
            "Cued recall tasks",
            "Spatial-navigation footage",
        ),
        clinical=(
            "Bilateral lesions cause anterograde amnesia (e.g. patient H.M.)",
            "Atrophy precedes cognitive decline in Alzheimer's disease",
        ),
    ),
    # ---------------------------------------------------------------------
    # FRONTOPARIETAL CONTROL NETWORK
    # ---------------------------------------------------------------------
    Region(
        name="Dorsolateral Prefrontal Cortex",
        abbreviation="dlPFC",
        network=Network.FRONTOPARIETAL,
        brodmann="BA9/46",
        functions=(
            "Working memory maintenance and manipulation",
            "Cognitive control and rule-switching",
            "Goal-directed decision making",
        ),
        stimuli=(
            "N-back and span working-memory tasks",
            "Complex problem solving",
            "Tasks requiring sustained attention switches",
        ),
        clinical=(
            "Hypoactivation in schizophrenia working-memory deficits",
            "Targeted by rTMS for treatment-resistant depression",
        ),
    ),
    Region(
        name="Intraparietal Sulcus",
        abbreviation="IPS",
        network=Network.FRONTOPARIETAL,
        brodmann="BA7/40",
        functions=(
            "Number representation and arithmetic",
            "Spatial attention and saccade planning",
            "Cross-modal integration of sensory information",
        ),
        stimuli=(
            "Numerical comparison and arithmetic tasks",
            "Visuospatial attention shifts",
            "Eye-movement saccade tasks",
        ),
        clinical=("Lesions impair calculation (acalculia) and spatial neglect",),
    ),
    # ---------------------------------------------------------------------
    # SOMATOMOTOR NETWORK
    # ---------------------------------------------------------------------
    Region(
        name="Primary Motor Cortex",
        abbreviation="M1",
        network=Network.SOMATOMOTOR,
        brodmann="BA4",
        functions=(
            "Direct corticospinal control of voluntary movement",
            "Somatotopic motor map (the homunculus)",
            "Force and direction of muscle contractions",
        ),
        stimuli=(
            "Voluntary movement of any body part",
            "Motor imagery tasks (weaker activation)",
            "Observation of skilled actions (mirror activation)",
        ),
        clinical=(
            "Lesions cause contralateral weakness or paralysis",
            "Hyperactivation in tic disorders",
        ),
    ),
    Region(
        name="Primary Somatosensory Cortex",
        abbreviation="S1",
        network=Network.SOMATOMOTOR,
        brodmann="BA1/2/3",
        functions=(
            "Tactile, pressure, and proprioceptive processing",
            "Somatotopic sensory map (the homunculus)",
            "Stereognosis (object recognition by touch)",
        ),
        stimuli=(
            "Tactile stimulation of the skin",
            "Proprioceptive feedback during movement",
            "Vibration and pressure stimuli",
        ),
        clinical=("Lesions cause contralateral somatosensory deficits",),
    ),
    # ---------------------------------------------------------------------
    # DORSAL ATTENTION NETWORK
    # ---------------------------------------------------------------------
    Region(
        name="Superior Parietal Lobule",
        abbreviation="SPL",
        network=Network.DORSAL_ATTENTION,
        brodmann="BA5/7",
        functions=(
            "Top-down spatial attention orienting",
            "Visuospatial transformations and mental rotation",
            "Eye-hand coordination and reaching",
        ),
        stimuli=(
            "Cued spatial attention tasks",
            "Mental rotation of 3D objects",
            "Reaching toward visual targets",
        ),
        clinical=("Lesions cause optic ataxia and contralateral neglect",),
    ),
    # ---------------------------------------------------------------------
    # VENTRAL ATTENTION / SALIENCE NETWORK
    # ---------------------------------------------------------------------
    Region(
        name="Anterior Insula",
        abbreviation="AI",
        network=Network.VENTRAL_ATTENTION,
        brodmann="BA13",
        functions=(
            "Interoceptive awareness of bodily states",
            "Salience detection and switching attention",
            "Affective and pain processing",
        ),
        stimuli=(
            "Painful or aversive stimuli",
            "Surprising or salient events in a sequence",
            "Disgust-evoking images",
        ),
        clinical=(
            "Hyperactivation in anxiety disorders",
            "Dysregulation observed in addiction craving",
        ),
    ),
    Region(
        name="Anterior Cingulate Cortex",
        abbreviation="ACC",
        network=Network.VENTRAL_ATTENTION,
        brodmann="BA24/32",
        functions=(
            "Conflict monitoring and error detection",
            "Effort allocation and cost-benefit analysis",
            "Emotional regulation",
        ),
        stimuli=(
            "Stroop, Flanker, and other conflict tasks",
            "Error feedback in performance tasks",
            "Anticipation of pain or aversive outcomes",
        ),
        clinical=(
            "Hyperactivation in OCD",
            "Hypoactivation in adult ADHD",
        ),
    ),
    Region(
        name="Temporoparietal Junction",
        abbreviation="TPJ",
        network=Network.VENTRAL_ATTENTION,
        brodmann="BA39/40",
        functions=(
            "Reorienting attention to unexpected stimuli",
            "Theory of mind and perspective taking",
            "Multisensory integration of body and self",
        ),
        stimuli=(
            "Surprising stimulus onsets",
            "Reading false-belief stories",
            "Observation of others' actions and intentions",
        ),
        clinical=("Lesions disrupt spatial attention and self-other distinction",),
    ),
    # ---------------------------------------------------------------------
    # LIMBIC
    # ---------------------------------------------------------------------
    Region(
        name="Amygdala",
        abbreviation="Amyg",
        network=Network.LIMBIC,
        brodmann="(subcortical)",
        functions=(
            "Rapid evaluation of emotional salience, especially threat",
            "Fear conditioning and memory consolidation",
            "Modulation of attention via emotional content",
        ),
        stimuli=(
            "Fearful or threatening faces",
            "Conditioned stimuli paired with aversive outcomes",
            "Emotionally arousing music or imagery",
        ),
        clinical=(
            "Hyperactivation across anxiety and PTSD",
            "Hypoactivation in psychopathy",
        ),
    ),
)


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

def by_abbreviation(abbr: str) -> Region:
    """Return the region with the given abbreviation (case-insensitive)."""
    needle = abbr.casefold()
    for r in REGIONS:
        if r.abbreviation.casefold() == needle:
            return r
    raise KeyError(f"No region with abbreviation: {abbr!r}")


def by_network(network: Network) -> tuple[Region, ...]:
    """All regions in the given network."""
    return tuple(r for r in REGIONS if r.network is network)


def all_networks_covered() -> bool:
    """Sanity check: every Network must have at least one region."""
    covered = {r.network for r in REGIONS}
    return covered == set(Network)


__all__ = [
    "REGIONS",
    "Network",
    "Region",
    "all_networks_covered",
    "by_abbreviation",
    "by_network",
]
