"""Central config for Cortex — TRIBE v2 local pipeline.

All paths resolved relative to project root.
Secrets come from environment variables (loaded from .env by the startup script).

Model tier architecture:
  FAST   -> gemma4:e4b         (always warm, gate/classify/quick narration)
  DEEP   -> gemma4:26b         (MoE, loaded on demand, tiers 0-4 analysis)
  EXPERT -> gemma4:31b         (dense, maximum quality, tiers 5-6)
"""
from __future__ import annotations

import os
import pathlib
import sys
from pathlib import Path

if sys.platform == "win32":
    pathlib.PosixPath = pathlib.WindowsPath

# -- Paths -------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEIGHTS_DIR  = PROJECT_ROOT / "tribev2_weights"
SOURCE_DIR   = PROJECT_ROOT / "tribev2_src"
CACHE_DIR    = PROJECT_ROOT / "tribev2_cache"
OUT_DIR      = PROJECT_ROOT / "outputs"
ASSETS_DIR   = PROJECT_ROOT / "assets"
UPLOAD_DIR   = PROJECT_ROOT / "uploads"
LOGS_DIR     = PROJECT_ROOT / "logs"

for _d in (CACHE_DIR, OUT_DIR, ASSETS_DIR, UPLOAD_DIR, LOGS_DIR):
    _d.mkdir(exist_ok=True)

# -- Hugging Face ------------------------------------------------------------

HF_TOKEN = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
if not HF_TOKEN:
    _tok = PROJECT_ROOT / ".hf_token"
    if _tok.exists():
        HF_TOKEN = _tok.read_text().strip()

os.environ["HUGGING_FACE_HUB_TOKEN"]       = HF_TOKEN or ""
os.environ["HF_TOKEN"]                      = HF_TOKEN or ""
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

# -- Ollama / Model config ---------------------------------------------------

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")

# Four-tier model split — assigned per node by the inference router:
#   FAST    = E4B (~10 GB)  -- always warm; quick narration; runs anywhere
#   DEEP    = 26B MoE (~18 GB) -- standard narration (Seratonin RTX 5090)
#   EXPERT  = 31B dense (~20 GB) -- max-quality narration
#   VISION  = 31B dense  -- detailed image/video description
OLLAMA_MODEL_FAST   = os.environ.get("MODEL_FAST",   "gemma4:e4b")
OLLAMA_MODEL_DEEP   = os.environ.get("MODEL_DEEP",   "gemma4:26b")
OLLAMA_MODEL_EXPERT = os.environ.get("MODEL_EXPERT", "gemma4:31b")
OLLAMA_MODEL_VISION = os.environ.get("MODEL_VISION", "gemma4:31b")

# Back-compat aliases
OLLAMA_MODEL_QUALITY = OLLAMA_MODEL_DEEP    # used by tiers.py
OLLAMA_MODEL         = OLLAMA_MODEL_QUALITY

# Flash attention in Ollama (reduces VRAM at long contexts on Blackwell)
OLLAMA_FLASH_ATTENTION = os.environ.get("OLLAMA_FLASH_ATTENTION", "1")
os.environ["OLLAMA_FLASH_ATTENTION"] = OLLAMA_FLASH_ATTENTION

# KV cache quantization (q8_0 halves KV cache VRAM at minimal quality cost)
OLLAMA_KV_CACHE_TYPE = os.environ.get("OLLAMA_KV_CACHE_TYPE", "q8_0")
os.environ["OLLAMA_KV_CACHE_TYPE"] = OLLAMA_KV_CACHE_TYPE

# -- TRIBE v2 ----------------------------------------------------------------

# duration_trs must match model's keep-mask length (100 TRs = 50 seconds at 2 Hz).
# CRITICAL: do not lower this -- breaks index into segment keep-mask.
TRIBE_CONFIG_UPDATE = {
    "data.duration_trs": int(os.environ.get("TRIBE_DURATION_TRS", "100")),
}

# -- Pipeline limits ----------------------------------------------------------

MAX_UPLOAD_MB  = int(os.environ.get("MAX_UPLOAD_MB", "50"))
TRIBE_MAX_SECS = float(os.environ.get("TRIBE_MAX_SECS", "50"))   # TRIBE v2 hard limit

# -- Misc ---------------------------------------------------------------------

DEMO_VIDEO = ASSETS_DIR / "demo_clip_20s.mp4"
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "3"))
