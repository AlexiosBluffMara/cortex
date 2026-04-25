"""Extract every possible insight from TRIBE v2's 20,484 x 100 preds array.

Atlas hierarchy (richest -> fastest):
  Brainnetome-246    246 ROIs (210 cortical + 36 subcortical) + BrainMap cognitive ontology
  Harvard-Oxford     48 cortical + 21 subcortical -- anatomical landmarks, thalamus, amygdala
  Juelich            52 probabilistic cytoarchitectonic regions
  Schaefer-400       Functional connectivity parcellation, Yeo-7 network labels
  Schaefer-1000      High-resolution functional, optional slow path

Additional metrics from all atlases:
  - Yeo-7 network aggregation with LH/RH laterality index per network
  - Temporal shape: peak TR, rise time, half-max duration, decay slope
  - Activation volume: vertex count above 1 sigma and 2 sigma thresholds
  - Global signal statistics: mean, std, kurtosis, skewness

Returns a rich `BrainAnalysis` dataclass. Atlases are cached after first load.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .pipeline import InferenceResult

_atlas_cache: dict[str, Any] = {}


# -- Generic vol-to-surf projection --------------------------------------------

def _project_volumetric_atlas(
    preds: np.ndarray,
    atlas_maps,
    labels: list[str],
    cache_key: str,
) -> tuple[pd.DataFrame, list[str]]:
    """Shared projection path for any nilearn volumetric atlas -> fsaverage5 vertex labels."""
    from nilearn.datasets import fetch_surf_fsaverage
    from nilearn.surface import vol_to_surf

    if cache_key not in _atlas_cache:
        fsavg5 = _atlas_cache.setdefault("fsavg5", fetch_surf_fsaverage("fsaverage5"))
        lh = vol_to_surf(atlas_maps, fsavg5.pial_left,
                         interpolation="nearest_most_frequent").astype(int)
        rh = vol_to_surf(atlas_maps, fsavg5.pial_right,
                         interpolation="nearest_most_frequent").astype(int)
        _atlas_cache[cache_key] = {
            "labels": labels,
            "vertex_labels": np.concatenate([lh, rh]),
        }

    lbl   = _atlas_cache[cache_key]["labels"]
    verts = _atlas_cache[cache_key]["vertex_labels"]
    T = preds.shape[0]
    roi_ts = np.zeros((T, len(lbl)))
    for i in range(1, len(lbl) + 1):
        mask = verts == i
        if mask.any():
            roi_ts[:, i - 1] = preds[:, mask].mean(axis=1)
    df = pd.DataFrame(roi_ts, columns=lbl)
    df = df.loc[:, df.abs().sum(axis=0) > 0]
    top = df.abs().mean().sort_values(ascending=False).head(12).index.tolist()
    return df, top


# -- Atlas-specific loaders ----------------------------------------------------

def _schaefer_roi_means(preds: np.ndarray, n_rois: int = 400) -> tuple[pd.DataFrame, list[str]]:
    """Schaefer-N atlas (functional connectivity, Yeo-7 network labels)."""
    from nilearn.datasets import fetch_atlas_schaefer_2018
    atlas = fetch_atlas_schaefer_2018(n_rois=n_rois, yeo_networks=7, resolution_mm=2)
    labels = [n.decode() if isinstance(n, bytes) else n for n in atlas.labels]
    return _project_volumetric_atlas(preds, atlas.maps, labels, f"schaefer_{n_rois}")


def _harvard_oxford_roi_means(preds: np.ndarray) -> tuple[pd.DataFrame, list[str]]:
    """Harvard-Oxford atlas -- 48 cortical + 21 subcortical, anatomical landmarks.

    Crucial for identifying amygdala, thalamus, hippocampus, putamen, caudate.
    """
    from nilearn.datasets import fetch_atlas_harvard_oxford
    # Cortical (max-probability, 25% threshold, 2mm)
    cort = fetch_atlas_harvard_oxford("cort-maxprob-thr25-2mm")
    subc = fetch_atlas_harvard_oxford("sub-maxprob-thr25-2mm")
    c_labels = [f"HO-cort: {n}" for n in cort.labels[1:]]   # skip Background
    s_labels = [f"HO-sub:  {n}" for n in subc.labels[1:]]
    all_labels = c_labels + s_labels

    cort_df, _ = _project_volumetric_atlas(preds, cort.maps, c_labels, "ho_cort")
    subc_df, _ = _project_volumetric_atlas(preds, subc.maps, s_labels, "ho_sub")
    df = pd.concat([cort_df, subc_df], axis=1)
    df = df.loc[:, ~df.columns.duplicated()]
    top = df.abs().mean().sort_values(ascending=False).head(12).index.tolist()
    return df, top


def _juelich_roi_means(preds: np.ndarray) -> tuple[pd.DataFrame, list[str]]:
    """Juelich cytoarchitectonic atlas -- 52 probabilistic regions.

    Maps to Brodmann areas and cytoarchitectonic fields. Useful for cortical
    layers (V1=BA17, motor=BA4, Broca=BA44/45, Wernicke=BA22).
    """
    from nilearn.datasets import fetch_atlas_juelich
    atlas = fetch_atlas_juelich("maxprob-thr25-2mm")
    labels = [f"Juelich: {n}" for n in atlas.labels[1:]]    # skip Background
    return _project_volumetric_atlas(preds, atlas.maps, labels, "juelich")


def _brainnetome_roi_means(
    preds: np.ndarray, cache_dir: Path | None = None
) -> tuple[pd.DataFrame, list[str]]:
    """Brainnetome atlas -- 246 ROIs (210 cortical + 36 subcortical).

    The gold standard for linking anatomy to cognitive function via BrainMap.
    Auto-downloads from atlas.brainnetome.org on first call (~50 MB).
    Requires: requests (already in requirements.txt)
    """
    from pathlib import Path

    from . import config as _cfg
    cache_dir = cache_dir or _cfg.CACHE_DIR / "atlases"
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    nii_path = cache_dir / "BN_Atlas_246_2mm.nii.gz"
    labels_path = cache_dir / "BN_Atlas_246_LUT.txt"

    if not nii_path.exists():
        import requests
        _URL_NII = (
            "https://atlas.brainnetome.org/downloads/BN_Atlas_246_2mm.nii.gz"
        )
        _URL_LUT = (
            "https://atlas.brainnetome.org/downloads/BN_Atlas_246_LUT.txt"
        )
        print("[analysis] Downloading Brainnetome atlas (~50 MB)...")
        for url, path in ((_URL_NII, nii_path), (_URL_LUT, labels_path)):
            r = requests.get(url, timeout=120, stream=True)
            r.raise_for_status()
            with open(path, "wb") as fh:
                for chunk in r.iter_content(65536):
                    fh.write(chunk)
        print("[analysis] Brainnetome atlas downloaded.")

    # Parse LUT: columns are index, label, R, G, B, A
    if labels_path.exists():
        raw_labels = []
        for line in labels_path.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if parts and parts[0].isdigit() and int(parts[0]) > 0:
                raw_labels.append(f"BNA: {' '.join(parts[1:2])}")
    else:
        raw_labels = [f"BNA-{i+1}" for i in range(246)]

    import nibabel as nib
    atlas_img = nib.load(str(nii_path))
    return _project_volumetric_atlas(preds, atlas_img, raw_labels, "brainnetome")


def _yeo7_network_code(roi_name: str) -> str:
    """'7Networks_LH_Vis_1' -> 'Vis'"""
    parts = roi_name.split("_")
    return parts[2] if len(parts) >= 3 else "Unknown"


_YEO7_FULL = {
    "Vis":         "Visual",
    "SomMot":      "Somatomotor",
    "DorsAttn":    "Dorsal Attention",
    "SalVentAttn": "Salience / Ventral Attention",
    "Limbic":      "Limbic",
    "Cont":        "Frontoparietal Control",
    "Default":     "Default Mode",
}


# -- Temporal dynamics ---------------------------------------------------------

def _temporal_dynamics(global_ts: np.ndarray, peak_t: int) -> dict[str, float]:
    """Compute temporal shape metrics from the global mean |z| time series."""
    T = len(global_ts)
    ts = np.abs(global_ts)
    peak_val = float(ts[peak_t])
    half_max = peak_val * 0.5

    # Rise time: first TR where signal crosses 50% of peak
    rise_t = int(peak_t)
    for t in range(peak_t, -1, -1):
        if ts[t] < half_max:
            rise_t = t + 1
            break

    # Duration above half-max
    above = np.where(ts >= half_max)[0]
    duration_half_max = len(above)

    # Decay slope: linear fit from peak to end (TRs per unit z)
    if peak_t < T - 2:
        x = np.arange(T - peak_t, dtype=float)
        y = ts[peak_t:]
        slope = float(np.polyfit(x, y, 1)[0])
    else:
        slope = 0.0

    return {
        "peak_tr":           peak_t,
        "peak_s":            round(peak_t / 2.0, 2),
        "peak_z":            round(peak_val, 4),
        "rise_tr":           rise_t,
        "rise_s":            round(rise_t / 2.0, 2),
        "duration_above_half_max_tr": duration_half_max,
        "duration_above_half_max_s":  round(duration_half_max / 2.0, 2),
        "decay_slope_per_tr": round(slope, 6),
    }


# -- Main analysis function ----------------------------------------------------

@dataclass
class BrainAnalysis:
    # Core prediction metadata
    preds_shape: tuple[int, int]
    duration_s:  float
    hemo_lag_s:  float = 5.0   # fixed 5s lag baked into TRIBE v2

    # Schaefer-400 (fast path, Yeo-7 network labels)
    s400_roi_df:   pd.DataFrame = field(repr=False, default_factory=pd.DataFrame)
    s400_top_rois: list[str] = field(default_factory=list)

    # Schaefer-1000 (high resolution functional, optional)
    s1000_roi_df:   pd.DataFrame = field(repr=False, default_factory=pd.DataFrame)
    s1000_top_rois: list[str] = field(default_factory=list)

    # Harvard-Oxford (anatomical landmarks: amygdala, thalamus, hippocampus, etc.)
    ho_roi_df:   pd.DataFrame = field(repr=False, default_factory=pd.DataFrame)
    ho_top_rois: list[str] = field(default_factory=list)

    # Juelich cytoarchitectonic (Brodmann areas, cortical layers)
    juelich_roi_df:   pd.DataFrame = field(repr=False, default_factory=pd.DataFrame)
    juelich_top_rois: list[str] = field(default_factory=list)

    # Brainnetome (246 ROIs + BrainMap cognitive ontology, optional)
    bna_roi_df:   pd.DataFrame = field(repr=False, default_factory=pd.DataFrame)
    bna_top_rois: list[str] = field(default_factory=list)

    # Yeo-7 network summary
    network_means: dict[str, float] = field(default_factory=dict)
    network_laterality: dict[str, float] = field(default_factory=dict)
    dominant_network: str = ""

    # Temporal dynamics
    temporal: dict[str, float] = field(default_factory=dict)

    # Activation volume
    vertices_above_1sd: int = 0
    vertices_above_2sd: int = 0
    activation_fraction_1sd: float = 0.0
    activation_fraction_2sd: float = 0.0

    # Global signal stats
    global_mean_z: float = 0.0
    global_std_z:  float = 0.0
    global_max_z:  float = 0.0
    global_min_z:  float = 0.0
    global_kurtosis: float = 0.0

    # Hemisphere dominant network list
    lh_dominant_networks: list[str] = field(default_factory=list)
    rh_dominant_networks: list[str] = field(default_factory=list)

    # Processing time
    analysis_seconds: float = 0.0

    def _top_roi_list(self, df: pd.DataFrame, rois: list[str], n: int = 8) -> list[dict]:
        if df.empty or not rois:
            return []
        return [
            {"roi": r, "mean_abs_z": round(float(df[r].abs().mean()), 4)}
            for r in rois[:n] if r in df.columns
        ]

    def to_dict(self) -> dict:
        """Flatten to a JSON-serializable dict for Gemma injection."""
        nets_sorted = sorted(self.network_means.items(), key=lambda kv: kv[1], reverse=True)
        networks_ranked = [
            {
                "network":           k,
                "full_name":         _YEO7_FULL.get(k, k),
                "mean_abs_z":        round(v, 4),
                "laterality_index":  round(self.network_laterality.get(k, 0.0), 3),
                "laterality_side": (
                    "left"      if self.network_laterality.get(k, 0) > 0.05 else
                    "right"     if self.network_laterality.get(k, 0) < -0.05 else
                    "bilateral"
                ),
            }
            for k, v in nets_sorted
        ]
        return {
            "duration_s":               self.duration_s,
            "hemodynamic_lag_s":        self.hemo_lag_s,
            "peak_s":                   self.temporal.get("peak_s", 0),
            "peak_z":                   self.temporal.get("peak_z", 0),
            "rise_s":                   self.temporal.get("rise_s", 0),
            "duration_above_half_max_s": self.temporal.get("duration_above_half_max_s", 0),
            "decay_slope":              self.temporal.get("decay_slope_per_tr", 0),
            "dominant_network":         _YEO7_FULL.get(self.dominant_network, self.dominant_network),
            "networks_ranked":          networks_ranked,
            "top_rois_schaefer400":     self._top_roi_list(self.s400_roi_df, self.s400_top_rois),
            "top_rois_schaefer1000":    self._top_roi_list(self.s1000_roi_df, self.s1000_top_rois, 12),
            "top_rois_harvard_oxford":  self._top_roi_list(self.ho_roi_df, self.ho_top_rois),
            "top_rois_juelich":         self._top_roi_list(self.juelich_roi_df, self.juelich_top_rois),
            "top_rois_brainnetome":     self._top_roi_list(self.bna_roi_df, self.bna_top_rois, 12),
            "vertices_above_1sd":       self.vertices_above_1sd,
            "vertices_above_2sd":       self.vertices_above_2sd,
            "activation_fraction_1sd":  round(self.activation_fraction_1sd, 4),
            "global_max_z":             round(self.global_max_z, 4),
            "global_min_z":             round(self.global_min_z, 4),
            "lh_dominant_networks":     self.lh_dominant_networks,
            "rh_dominant_networks":     self.rh_dominant_networks,
        }

    def gemma_context(self) -> str:
        """Compact text block to inject into any Gemma tier prompt."""
        d = self.to_dict()
        nets = "\n".join(
            f"  - {n['full_name']:30s}  mean|z|={n['mean_abs_z']:.3f}  "
            f"({n['laterality_side']})"
            for n in d["networks_ranked"]
        )
        s400 = "\n".join(
            f"  - {r['roi'][:55]:55s}  {r['mean_abs_z']:.3f}"
            for r in d["top_rois_schaefer400"]
        )
        ho = "\n".join(
            f"  - {r['roi'][:55]:55s}  {r['mean_abs_z']:.3f}"
            for r in d["top_rois_harvard_oxford"]
        ) if d["top_rois_harvard_oxford"] else "  (not computed)"
        bna = "\n".join(
            f"  - {r['roi'][:55]:55s}  {r['mean_abs_z']:.3f}"
            for r in d["top_rois_brainnetome"]
        ) if d["top_rois_brainnetome"] else "  (not computed -- add --brainnetome flag)"
        juelich = "\n".join(
            f"  - {r['roi'][:55]:55s}  {r['mean_abs_z']:.3f}"
            for r in d["top_rois_juelich"]
        ) if d["top_rois_juelich"] else "  (not computed)"
        return (
            f"Duration: {d['duration_s']:.1f}s  |  "
            f"Peak at: {d['peak_s']:.1f}s  (z={d['peak_z']:.3f})  |  "
            f"Rise: {d['rise_s']:.1f}s  |  "
            f"Above half-max: {d['duration_above_half_max_s']:.1f}s\n"
            f"Dominant network: {d['dominant_network']}\n"
            f"LH-dominant networks: {', '.join(d['lh_dominant_networks']) or 'none'}\n"
            f"RH-dominant networks: {', '.join(d['rh_dominant_networks']) or 'none'}\n"
            f"Activated vertices: {d['vertices_above_1sd']} / 20484 above 1\u03c3 "
            f"({d['activation_fraction_1sd']*100:.1f}%)\n\n"
            f"Yeo-7 network ranking:\n{nets}\n\n"
            f"Top Schaefer-400 ROIs (functional):\n{s400}\n\n"
            f"Top Harvard-Oxford ROIs (anatomical, incl. subcortical):\n{ho}\n\n"
            f"Top Juelich cytoarchitectonic regions (Brodmann areas):\n{juelich}\n\n"
            f"Top Brainnetome ROIs (cognitive ontology):\n{bna}"
        )


def analyse(
    result: InferenceResult,
    *,
    high_res: bool = False,
    harvard_oxford: bool = True,
    juelich: bool = True,
    brainnetome: bool = False,
) -> BrainAnalysis:
    """Run full analysis on an InferenceResult.

    Args:
        result:         InferenceResult from pipeline.run_inference()
        high_res:       Also compute Schaefer-1000 (~30s extra)
        harvard_oxford: Compute Harvard-Oxford anatomical atlas (~20s extra, ON by default)
        juelich:        Compute Juelich cytoarchitectonic atlas (~20s extra, ON by default)
        brainnetome:    Compute Brainnetome-246 (~30s + 50MB download on first use, OFF by default)
    """
    t0 = time.time()
    preds = result.preds                    # (T, 20484) float32
    T, V = preds.shape
    duration_s = T / 2.0

    # -- Global signal stats ---------------------------------------------------
    flat = preds.ravel()
    global_mean  = float(flat.mean())
    global_std   = float(flat.std())
    global_max   = float(flat.max())
    global_min   = float(flat.min())
    try:
        from scipy import stats as _stats
        kurtosis = float(_stats.kurtosis(flat))
    except Exception:
        kurtosis = 0.0

    # -- Activation volume (at peak frame) ------------------------------------
    peak_frame = preds[result.peak_t]
    threshold_1 = float(np.abs(preds).mean()) + float(np.abs(preds).std())
    threshold_2 = float(np.abs(preds).mean()) + 2 * float(np.abs(preds).std())
    above_1 = int((np.abs(peak_frame) > threshold_1).sum())
    above_2 = int((np.abs(peak_frame) > threshold_2).sum())

    # -- Schaefer-400 (reuse result.roi_df if already computed) ---------------
    if hasattr(result, "roi_df") and not result.roi_df.empty:
        s400_df   = result.roi_df
        s400_top  = result.top_rois
    else:
        s400_df, s400_top = _schaefer_roi_means(preds, 400)

    # -- Schaefer-1000 (optional high-res) ------------------------------------
    s1000_df: pd.DataFrame = pd.DataFrame()
    s1000_top: list[str] = []
    if high_res:
        s1000_df, s1000_top = _schaefer_roi_means(preds, 1000)

    # -- Harvard-Oxford (anatomical + subcortical, default ON) ----------------
    ho_df: pd.DataFrame = pd.DataFrame()
    ho_top: list[str] = []
    if harvard_oxford:
        try:
            ho_df, ho_top = _harvard_oxford_roi_means(preds)
        except Exception as exc:
            print(f"[analysis] Harvard-Oxford skipped: {exc}")

    # -- Juelich cytoarchitectonic (default ON) --------------------------------
    juelich_df: pd.DataFrame = pd.DataFrame()
    juelich_top: list[str] = []
    if juelich:
        try:
            juelich_df, juelich_top = _juelich_roi_means(preds)
        except Exception as exc:
            print(f"[analysis] Juelich skipped: {exc}")

    # -- Brainnetome (optional, downloads ~50MB first run) ---------------------
    bna_df: pd.DataFrame = pd.DataFrame()
    bna_top: list[str] = []
    if brainnetome:
        try:
            bna_df, bna_top = _brainnetome_roi_means(preds)
        except Exception as exc:
            print(f"[analysis] Brainnetome skipped: {exc}")

    # -- Yeo-7 network aggregation + laterality --------------------------------
    lh_preds = preds[:, :10242]             # left hemisphere vertices
    rh_preds = preds[:, 10242:]             # right hemisphere vertices

    net_lh_vals: dict[str, list[float]] = {}
    net_rh_vals: dict[str, list[float]] = {}
    net_all_vals: dict[str, list[float]] = {}

    for col in s400_df.columns:
        net = _yeo7_network_code(col)
        ts = s400_df[col].abs()
        mean_val = float(ts.mean())
        net_all_vals.setdefault(net, []).append(mean_val)
        # Approximate hemisphere from ROI name prefix ("LH" / "RH")
        parts = col.split("_")
        hemi_tag = parts[1] if len(parts) > 1 else "LH"
        if hemi_tag == "LH":
            net_lh_vals.setdefault(net, []).append(mean_val)
        else:
            net_rh_vals.setdefault(net, []).append(mean_val)

    network_means: dict[str, float] = {
        k: float(np.mean(v)) for k, v in net_all_vals.items() if v
    }
    network_laterality: dict[str, float] = {}
    for net in network_means:
        lh = float(np.mean(net_lh_vals.get(net, [0])))
        rh = float(np.mean(net_rh_vals.get(net, [0])))
        total = lh + rh
        network_laterality[net] = (lh - rh) / total if total > 1e-9 else 0.0

    dominant_net = max(network_means, key=network_means.get) if network_means else ""
    lh_dominant = [_YEO7_FULL.get(k, k) for k, li in network_laterality.items() if li > 0.1]
    rh_dominant = [_YEO7_FULL.get(k, k) for k, li in network_laterality.items() if li < -0.1]

    # -- Temporal dynamics -----------------------------------------------------
    global_ts = np.abs(preds).mean(axis=1)
    temporal = _temporal_dynamics(global_ts, result.peak_t)

    return BrainAnalysis(
        preds_shape          = (T, V),
        duration_s           = duration_s,
        s400_roi_df          = s400_df,
        s400_top_rois        = s400_top,
        s1000_roi_df         = s1000_df,
        s1000_top_rois       = s1000_top,
        ho_roi_df            = ho_df,
        ho_top_rois          = ho_top,
        juelich_roi_df       = juelich_df,
        juelich_top_rois     = juelich_top,
        bna_roi_df           = bna_df,
        bna_top_rois         = bna_top,
        network_means        = network_means,
        network_laterality   = network_laterality,
        dominant_network     = dominant_net,
        temporal             = temporal,
        vertices_above_1sd   = above_1,
        vertices_above_2sd   = above_2,
        activation_fraction_1sd = above_1 / V,
        activation_fraction_2sd = above_2 / V,
        global_mean_z        = global_mean,
        global_std_z         = global_std,
        global_max_z         = global_max,
        global_min_z         = global_min,
        global_kurtosis      = kurtosis,
        lh_dominant_networks = lh_dominant,
        rh_dominant_networks = rh_dominant,
        analysis_seconds     = round(time.time() - t0, 2),
    )
