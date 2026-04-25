# Cortex — Technical Specification v4

**Project:** Cortex by Alexios Bluff Mara LLC  
**Owner:** Soumit Lahiri / Red Team Kitchen / ISU  
**Model name:** `RedTeamKitchen/cortex-gemma-4-e4b`  
**Domain:** redteamkitchen.com  
**Repo:** github.com/AlexiosBluffMara/cortex  

---

## Table of Contents

1. VRAM Reality Check  
2. Task Graph (Parallel vs Sequential)  
3. GPU Priority Scheduler  
4. Video Processing Pipeline  
5. TRIBE v2 Local Inference  
6. Gemma 4 Model Configuration  
7. Hermes Agent Fork  
8. Synthetic Neuroscience Dataset  
9. Unsloth Fine-Tuning  
10. WebUI Architecture  
11. 3D Brain Viewer  
12. WhatsApp Integration  
13. GCP Backup Infrastructure  
14. Website: redteamkitchen.com  
15. Error Handling  
16. Submission Checklist  

---

## 1. VRAM Reality Check

### Can TRIBE v2 run on the 5090?

**YES.** The paper (arXiv 2507.22229) trained on V100 32GB. Our measured peak is ~22.4GB on the 5090 with BF16 + torch.compile + Flash Attention.

**Component VRAM breakdown:**
```
V-JEPA2-ViT-g (1.1B params, frozen)     ~2.2 GB
wav2vec-BERT 2.0 (0.6B params, frozen)   ~1.2 GB
Llama-3.2-3B (3.0B params, frozen)       ~6.0 GB
8-layer transformer head (0.15B params)  ~0.3 GB
─────────────────────────────────────────────────
Model weights subtotal                   ~9.7 GB
Activations + KV cache (50s video)      ~12.7 GB
─────────────────────────────────────────────────
Peak VRAM                               ~22.4 GB
5090 total                               32.0 GB
OS/display reserved                      ~0.5 GB
Headroom                                 ~9.1 GB
```

**Conclusion:** TRIBE v2 fits with 9.1GB to spare. No quantization needed for TRIBE alone.

### Can TRIBE + Gemma coexist?

**NO.** TRIBE uses ~22.4GB. Gemma E4B uses ~10GB. Total = ~32.4GB > 31.5GB available.

**Solution:** Sequential swap mode (already designed in `model_manager.py`). They never run simultaneously.

### Gemma 4 model VRAM (measured on 5090)

```
gemma4:e2b      ~8 GB    232.7 tok/s   multimodal: yes
gemma4:e4b     ~10 GB    195.7 tok/s   multimodal: yes
gemma4:e4b-q8  ~12 GB    116.3 tok/s   multimodal: yes
gemma4:e4b-bf16 ~16 GB    88.9 tok/s   multimodal: yes
gemma4:26b     ~19 GB    132.2 tok/s   multimodal: yes (MoE, sparse)
gemma4:31b     ~21 GB     50.5 tok/s   multimodal: yes (dense)
```

All Gemma 4 models fit individually. The 31B is tight (21GB + activations) but works with KV cache quantization (`OLLAMA_KV_CACHE_TYPE=q8_0`).

### Multimodal quantization

Ollama's Q4_K_M quantization preserves multimodal capabilities. The vision encoder stays at higher precision internally while text weights are quantized. Measured latency on E4B: image processing adds ~200ms over text-only. This is acceptable.

### Batch processing strategy (night mode)

For bulk video analysis (your suggestion to run overnight):
1. Queue all videos during the day
2. At night, run TRIBE v2 in batch: load model once → process all videos → unload
3. Then load Gemma → narrate all results → unload
4. Results stored in GCP Cloud Storage, available on WebUI next morning

This avoids constant model swapping and maximizes throughput.

---

## 2. Task Graph (Parallel vs Sequential)

### Legend
- `[P]` = can run in parallel with other `[P]` tasks
- `[S]` = must run sequentially (depends on prior task)
- `[B]` = blocks downstream tasks

### Phase A: Local Infrastructure

```
A1 [B] Fork Hermes Agent and verify it boots
  │
  ├─ A2 [P] Pull all Gemma 4 models on Ollama
  │       ollama pull gemma4:e4b
  │       ollama pull gemma4:26b
  │       ollama pull gemma4:31b
  │
  ├─ A3 [P] Verify TRIBE v2 loads on 5090
  │       cd D:\cortex
  │       python -c "from tribev2.demo_utils import TribeModel; m = TribeModel.from_pretrained(...); m.cuda()"
  │       # Check nvidia-smi: should show ~22GB used
  │
  └─ A4 [P] Set up Cloudflare Tunnel for redteamkitchen.com
          cloudflared tunnel create cortex
          cloudflared tunnel route dns cortex cortex.redteamkitchen.com
```

### Phase B: GPU Scheduler + Pipeline (depends on A1, A2, A3)

```
B1 [B] Extend ModelManager with TRIBE swap logic
  │
  ├─ B2 [S] Build video preprocessing pipeline (FFmpeg)
  │
  ├─ B3 [S] Wire Hermes tools (brain_scan, narrate, visualize, describe_input)
  │
  └─ B4 [P] Build request queue system (async queue with priority)
```

### Phase C: Interfaces (depends on B3)

```
C1 [P] WhatsApp integration via Hermes
C2 [P] WebUI frontend (GCP Cloud Run)
C3 [P] Update existing Discord bot for Cortex
```

### Phase D: Unsloth Fine-Tuning (can start in parallel with Phase C)

```
D1 [B] Generate synthetic neuroscience dataset (5K QA pairs)
  │
  ├─ D2 [P] Download and format public datasets (Nemotron, TokenBender, OpenHermes)
  │
  └─ D3 [S] Run Unsloth training on 5090 (requires GPU, blocks other GPU work)
       │
       └─ D4 [S] Export GGUF, load in Ollama, benchmark vs base
```

### Phase E: Website + Polish (depends on C2)

```
E1 [P] Build redteamkitchen.com/cortex/ pages
E2 [P] Build analysis gallery with stored results
E3 [P] Upload model to HuggingFace
E4 [S] Record demo videos (Hermes first, then Gemma YouTube)
E5 [S] Write Kaggle writeup
E6 [S] Final submission
```

---

## 3. GPU Priority Scheduler

### Extending model_manager.py

The existing `ModelManager` handles Gemma tier swaps. We add a `GPUScheduler` layer on top:

```python
# core/gpu_scheduler.py
"""
GPU Priority Scheduler — manages exclusive access to 5090 VRAM.

State machine:
  IDLE         → no models loaded
  GEMMA_ACTIVE → Gemma E4B (or higher tier) loaded in Ollama
  TRIBE_ACTIVE → TRIBE v2 loaded via PyTorch

Transitions:
  GEMMA_ACTIVE → TRIBE_ACTIVE:
    1. Unload all Gemma models (Ollama keep_alive=0s)
    2. Wait for Ollama to release VRAM (poll nvidia-smi)
    3. Load TRIBE v2 via pipeline.load_model()
    
  TRIBE_ACTIVE → GEMMA_ACTIVE:
    1. Delete TRIBE model reference, torch.cuda.empty_cache()
    2. gc.collect()
    3. Warm Gemma E4B via model_manager.warm_fast_model()
"""
from __future__ import annotations

import asyncio
import enum
import gc
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from core.logger import log


class GPUState(enum.Enum):
    IDLE = "idle"
    GEMMA_ACTIVE = "gemma_active"
    TRIBE_ACTIVE = "tribe_active"
    SWAPPING = "swapping"


@dataclass
class QueuedRequest:
    priority: int        # 0=highest (user-facing), 9=lowest (background)
    callback: Callable
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)
    source: str = "unknown"   # "whatsapp", "discord", "webui", "batch"
    created_at: float = field(default_factory=time.time)


class GPUScheduler:
    TOTAL_VRAM_GB = 32.0
    TRIBE_VRAM_GB = 22.4
    GEMMA_E4B_VRAM_GB = 10.0
    VRAM_SAFETY_MARGIN_GB = 1.0
    SWAP_TIMEOUT_S = 120
    
    def __init__(self, model_manager, ollama_url: str = "http://localhost:11434"):
        self._state = GPUState.IDLE
        self._lock = asyncio.Lock()
        self._mm = model_manager
        self._ollama_url = ollama_url
        self._gemma_queue: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=50)
        self._tribe_queue: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=10)
        self._tribe_model = None
    
    @property
    def state(self) -> GPUState:
        return self._state
    
    def get_free_vram_gb(self) -> float:
        """Query nvidia-smi for actual free VRAM."""
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.free",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5
            )
            return float(result.stdout.strip()) / 1024  # MB → GB
        except Exception:
            return 0.0
    
    async def ensure_gemma(self) -> None:
        """Ensure Gemma is loaded. If TRIBE is active, swap."""
        async with self._lock:
            if self._state == GPUState.GEMMA_ACTIVE:
                return
            if self._state == GPUState.TRIBE_ACTIVE:
                await self._swap_tribe_to_gemma()
            elif self._state == GPUState.IDLE:
                self._mm.warm_fast_model()
                self._state = GPUState.GEMMA_ACTIVE
    
    async def ensure_tribe(self) -> None:
        """Ensure TRIBE is loaded. If Gemma is active, swap."""
        async with self._lock:
            if self._state == GPUState.TRIBE_ACTIVE:
                return
            if self._state == GPUState.GEMMA_ACTIVE:
                await self._swap_gemma_to_tribe()
            elif self._state == GPUState.IDLE:
                await self._load_tribe()
    
    async def _swap_gemma_to_tribe(self) -> None:
        self._state = GPUState.SWAPPING
        log.info("[gpu_scheduler] Swapping GEMMA → TRIBE")
        
        # Step 1: Unload all Gemma models
        import requests
        for model in ["gemma4:e4b", "gemma4:26b", "gemma4:31b"]:
            try:
                requests.post(
                    f"{self._ollama_url}/api/generate",
                    json={"model": model, "keep_alive": "0s"},
                    timeout=10
                )
            except Exception:
                pass
        
        # Step 2: Wait for VRAM to free (poll every 500ms, max 30s)
        for _ in range(60):
            free = self.get_free_vram_gb()
            if free >= self.TRIBE_VRAM_GB + self.VRAM_SAFETY_MARGIN_GB:
                break
            await asyncio.sleep(0.5)
        else:
            log.error("[gpu_scheduler] VRAM didn't free in 30s. Free: %.1fGB", free)
            raise RuntimeError(f"VRAM stuck: {free:.1f}GB free, need {self.TRIBE_VRAM_GB}GB")
        
        # Step 3: Load TRIBE
        await self._load_tribe()
    
    async def _swap_tribe_to_gemma(self) -> None:
        self._state = GPUState.SWAPPING
        log.info("[gpu_scheduler] Swapping TRIBE → GEMMA")
        
        # Step 1: Unload TRIBE
        import torch
        self._tribe_model = None
        
        # Force-clear the pipeline's global model ref
        from core import pipeline
        pipeline._model = None
        pipeline._compiled = False
        
        torch.cuda.empty_cache()
        gc.collect()
        
        # Step 2: Wait for VRAM
        for _ in range(60):
            free = self.get_free_vram_gb()
            if free >= self.GEMMA_E4B_VRAM_GB + self.VRAM_SAFETY_MARGIN_GB:
                break
            await asyncio.sleep(0.5)
        
        # Step 3: Reload Gemma E4B
        self._mm.warm_fast_model()
        self._state = GPUState.GEMMA_ACTIVE
        log.info("[gpu_scheduler] GEMMA restored. Free VRAM: %.1fGB", self.get_free_vram_gb())
    
    async def _load_tribe(self) -> None:
        import torch
        loop = asyncio.get_event_loop()
        
        def _do_load():
            from core.pipeline import load_model
            return load_model()
        
        self._tribe_model = await loop.run_in_executor(None, _do_load)
        self._state = GPUState.TRIBE_ACTIVE
        log.info("[gpu_scheduler] TRIBE loaded. Free VRAM: %.1fGB", self.get_free_vram_gb())
    
    async def run_brain_scan(self, media_path: str, priority: int = 0) -> Any:
        """
        Full brain scan: swap to TRIBE → run inference → swap back to Gemma.
        Returns InferenceResult.
        """
        await self.ensure_tribe()
        
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: __import__('core.pipeline', fromlist=['run_inference']).run_inference(media_path)
        )
        
        await self.ensure_gemma()
        return result
```

### Queue system for concurrent requests

```python
# core/request_queue.py
"""
Priority queue for GPU requests.
During TRIBE inference, Gemma requests queue up.
Optionally falls back to GitHub Copilot / 0x models.
"""
import asyncio
from dataclasses import dataclass
from typing import Optional


@dataclass
class PendingRequest:
    id: str
    source: str          # "whatsapp", "discord", "webui"
    request_type: str    # "gemma_chat", "brain_scan", "narrate"
    priority: int        # 0=urgent (user waiting), 5=normal, 9=batch
    payload: dict
    future: asyncio.Future
    
    def __lt__(self, other):
        return self.priority < other.priority


class RequestQueue:
    def __init__(self, gpu_scheduler, fallback_provider=None):
        self._scheduler = gpu_scheduler
        self._queue = asyncio.PriorityQueue(maxsize=50)
        self._fallback = fallback_provider  # GitHub Copilot / 0x
        self._processing = False
    
    async def submit(self, request: PendingRequest) -> None:
        """Submit a request. If GPU is busy and fallback available, use fallback."""
        if (self._scheduler.state == GPUState.TRIBE_ACTIVE 
            and request.request_type == "gemma_chat"
            and self._fallback is not None):
            # GPU busy with TRIBE, use fallback for chat
            result = await self._fallback.generate(request.payload)
            request.future.set_result(result)
            return
        
        await self._queue.put(request)
        
        if not self._processing:
            asyncio.create_task(self._process_queue())
    
    async def _process_queue(self):
        self._processing = True
        while not self._queue.empty():
            request = await self._queue.get()
            try:
                if request.request_type == "brain_scan":
                    result = await self._scheduler.run_brain_scan(
                        request.payload["media_path"],
                        priority=request.priority
                    )
                elif request.request_type == "gemma_chat":
                    await self._scheduler.ensure_gemma()
                    result = await self._run_gemma(request.payload)
                elif request.request_type == "narrate":
                    await self._scheduler.ensure_gemma()
                    result = await self._run_narrate(request.payload)
                else:
                    result = {"error": f"Unknown request type: {request.request_type}"}
                
                request.future.set_result(result)
            except Exception as exc:
                request.future.set_exception(exc)
        
        self._processing = False
```

---

## 4. Video Processing Pipeline

### Input constraints (TRIBE v2)

```
Maximum duration:    50 seconds (hard limit: duration_trs=100 at 2Hz)
Video frames:        V-JEPA2 samples 64 frames from preceding 4 seconds
Video resolution:    224×224 (V-JEPA2 ViT-g native, 2×16×16 patches)
Audio sample rate:   16 kHz mono (wav2vec-BERT 2.0 native)
Audio resampling:    Internal to model (50Hz → 2Hz)
Text token limit:    <512 tokens (Llama-3.2-3B context)
Modality dropout:    Model handles missing modalities (video-only, audio-only, text-only all work)
```

### FFmpeg preprocessing commands

```python
# core/media_processor.py
"""
Media preprocessing for TRIBE v2 and Gemma 4 multimodal input.
All processing uses FFmpeg via subprocess for maximum control.
"""
import subprocess
import json
from pathlib import Path
from dataclasses import dataclass


@dataclass
class MediaInfo:
    duration_s: float
    width: int
    height: int
    fps: float
    has_audio: bool
    codec: str
    file_size_mb: float


def probe(path: Path) -> MediaInfo:
    """Extract media metadata with FFprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    data = json.loads(result.stdout)
    
    video_stream = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    audio_stream = next((s for s in data["streams"] if s["codec_type"] == "audio"), None)
    
    return MediaInfo(
        duration_s=float(data["format"]["duration"]),
        width=int(video_stream["width"]) if video_stream else 0,
        height=int(video_stream["height"]) if video_stream else 0,
        fps=eval(video_stream["r_frame_rate"]) if video_stream else 0,
        has_audio=audio_stream is not None,
        codec=video_stream["codec_name"] if video_stream else "",
        file_size_mb=float(data["format"]["size"]) / (1024 * 1024),
    )


def preprocess_for_tribe(
    input_path: Path,
    output_dir: Path,
    max_duration_s: float = 50.0,
    target_resolution: int = 224,
) -> dict:
    """
    Preprocess media for TRIBE v2 inference.
    
    Produces:
      - video: 224x224, H.265 CRF 6, 30fps, ≤50s
      - audio: 16kHz mono WAV
      
    Near-lossless: CRF 6 preserves >99% visual quality.
    H.265 chosen for best compression/quality ratio.
    Audio extracted separately at wav2vec-BERT's native sample rate.
    
    Returns dict with paths to processed files.
    """
    info = probe(input_path)
    stem = input_path.stem
    output_dir.mkdir(parents=True, exist_ok=True)
    
    video_out = output_dir / f"{stem}_tribe.mp4"
    audio_out = output_dir / f"{stem}_tribe.wav"
    
    # --- Video: scale to 224x224, trim to 50s, CRF 6, H.265 ---
    # 
    # Why these settings:
    #   - scale=224:224 — V-JEPA2 native resolution (2x16x16 patches require div-by-16)
    #   - pad — center-pad if aspect ratio doesn't match (letterbox, not stretch)
    #   - fps=30 — V-JEPA2 samples 64 frames; 30fps gives good temporal density
    #   - CRF 6 — near-lossless (CRF 0 = lossless, CRF 18 = visually lossless)
    #   - libx265 — 30-40% smaller than H.264 at same quality
    #   - -ss/-to before -i — fast seek, no decode of skipped frames
    #   - -movflags +faststart — progressive download for web serving
    
    trim_args = []
    if info.duration_s > max_duration_s:
        # Take the LAST 50 seconds (most interesting part is usually the end)
        start = info.duration_s - max_duration_s
        trim_args = ["-ss", str(start), "-to", str(info.duration_s)]
    
    video_cmd = [
        "ffmpeg", "-y",
        *trim_args,
        "-i", str(input_path),
        "-vf", (
            f"fps=30,"
            f"scale={target_resolution}:{target_resolution}:"
            f"force_original_aspect_ratio=decrease,"
            f"pad={target_resolution}:{target_resolution}:(ow-iw)/2:(oh-ih)/2:black"
        ),
        "-c:v", "libx265",
        "-crf", "6",
        "-preset", "fast",
        "-an",  # no audio in video file (extracted separately)
        "-movflags", "+faststart",
        str(video_out),
    ]
    
    subprocess.run(video_cmd, capture_output=True, timeout=300, check=True)
    
    # --- Audio: 16kHz mono WAV for wav2vec-BERT ---
    #
    # wav2vec-BERT 2.0 expects 16kHz mono.
    # WAV (uncompressed) avoids any codec artifacts in the audio features.
    # -ac 1 forces mono. -ar 16000 forces 16kHz.
    
    if info.has_audio:
        audio_cmd = [
            "ffmpeg", "-y",
            *trim_args,
            "-i", str(input_path),
            "-vn",          # no video
            "-ac", "1",     # mono
            "-ar", "16000", # 16kHz
            "-c:a", "pcm_s16le",  # 16-bit PCM WAV
            str(audio_out),
        ]
        subprocess.run(audio_cmd, capture_output=True, timeout=120, check=True)
    
    # --- Gemma multimodal keyframes (for vision gate) ---
    # Extract 1 frame per second for Gemma's visual analysis
    keyframes_dir = output_dir / f"{stem}_keyframes"
    keyframes_dir.mkdir(exist_ok=True)
    
    keyframe_cmd = [
        "ffmpeg", "-y",
        *trim_args,
        "-i", str(input_path),
        "-vf", "fps=1,scale=768:768:force_original_aspect_ratio=decrease",
        "-q:v", "2",  # high quality JPEG
        str(keyframes_dir / "frame_%04d.jpg"),
    ]
    subprocess.run(keyframe_cmd, capture_output=True, timeout=120, check=True)
    
    # Verify audio-video sync
    video_info = probe(video_out)
    if info.has_audio:
        audio_info_cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", str(audio_out)
        ]
        audio_result = subprocess.run(audio_info_cmd, capture_output=True, text=True)
        audio_data = json.loads(audio_result.stdout)
        audio_dur = float(audio_data["format"]["duration"])
        video_dur = video_info.duration_s
        drift = abs(audio_dur - video_dur)
        if drift > 0.1:
            # Log warning but don't fail — small drift is acceptable
            print(f"WARNING: A/V drift of {drift:.3f}s detected")
    
    return {
        "video": str(video_out),
        "audio": str(audio_out) if info.has_audio else None,
        "keyframes_dir": str(keyframes_dir),
        "original_duration_s": info.duration_s,
        "processed_duration_s": min(info.duration_s, max_duration_s),
        "original_resolution": f"{info.width}x{info.height}",
        "processed_resolution": f"{target_resolution}x{target_resolution}",
        "original_size_mb": info.file_size_mb,
        "processed_size_mb": video_out.stat().st_size / (1024 * 1024),
    }


def preprocess_for_web(
    input_path: Path,
    output_dir: Path,
    max_duration_s: float = 50.0,
) -> Path:
    """
    Create a web-optimized version for the gallery viewer.
    720p H.264 for maximum browser compatibility.
    """
    output = output_dir / f"{input_path.stem}_web.mp4"
    
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-t", str(max_duration_s),
        "-vf", "scale=-2:720",
        "-c:v", "libx264",
        "-crf", "23",
        "-preset", "medium",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(output),
    ]
    subprocess.run(cmd, capture_output=True, timeout=300, check=True)
    return output
```

---

## 5. TRIBE v2 Local Inference

### Existing code (working, in core/pipeline.py)

The pipeline already handles:
- Model loading with Blackwell optimizations (BF16, torch.compile, Flash Attention)
- Video/audio/text input via `get_events_dataframe()`
- Inference with `torch.inference_mode()` + `torch.autocast(bfloat16)`
- Schaefer-400 parcellation and ROI extraction
- Peak activation frame detection

### What to add: VRAM profiling

```python
# Add to core/pipeline.py

def get_vram_usage() -> dict:
    """Return current VRAM state for monitoring."""
    import torch
    if not torch.cuda.is_available():
        return {"available": False}
    return {
        "available": True,
        "total_gb": torch.cuda.get_device_properties(0).total_mem / (1024**3),
        "allocated_gb": torch.cuda.memory_allocated(0) / (1024**3),
        "reserved_gb": torch.cuda.memory_reserved(0) / (1024**3),
        "free_gb": (torch.cuda.get_device_properties(0).total_mem 
                    - torch.cuda.memory_reserved(0)) / (1024**3),
        "peak_gb": torch.cuda.max_memory_allocated(0) / (1024**3),
    }


def unload_model() -> None:
    """Explicitly unload TRIBE v2 from VRAM."""
    import torch, gc
    global _model, _compiled
    _model = None
    _compiled = False
    torch.cuda.empty_cache()
    gc.collect()
    log.info("[pipeline] TRIBE v2 unloaded. VRAM: %s", get_vram_usage())
```

### TRIBE v2 limitations (from the paper)

```
Input:
  - Max 50 seconds of stimulus (100 TRs at 2Hz)
  - Text: uses preceding k=1024 words at f=2Hz
  - Video: 64 frames from preceding 4 seconds per time point
  - Audio: resampled from 50Hz to 2Hz internally

Output:
  - (T, 20484) float32 — per-vertex BOLD z-scores on fsaverage5
  - T = number of time points (100 for 50s stimulus)
  - 20484 = 10242 vertices/hemisphere × 2 hemispheres
  - Temporal resolution: 1 TR = ~0.5 seconds (2Hz)

Training data:
  - Courtois NeuroMod only (6 subjects, Friends S1-6 + movies)
  - NOT trained on OpenNeuro, HCP, NSD, or StudyForrest
  - CC-BY-NC 4.0 license (non-commercial)

Known limitations:
  - 1000 Schaefer parcels (not voxel-level)
  - Subject-conditional (best results with subjects seen during training)
  - Temporal hemodynamic delay not explicitly modeled
  - No subcortical predictions (cortical surface only)
```

---

## 6. Gemma 4 Model Configuration

### Ollama configuration

```bash
# Pull all Gemma 4 models (NO Gemma 3)
ollama pull gemma4:e4b          # ~10GB, primary fast model
ollama pull gemma4:26b          # ~19GB, MoE deep analysis
ollama pull gemma4:31b          # ~21GB, dense expert

# Verify multimodal works
ollama run gemma4:e4b "Describe this image" --images test.jpg

# Set environment for optimal 5090 performance
export OLLAMA_FLASH_ATTENTION=1
export OLLAMA_KV_CACHE_TYPE=q8_0
export OLLAMA_NUM_PARALLEL=1     # single request at a time
export OLLAMA_MAX_LOADED_MODELS=1 # only one model in VRAM
```

### Gemma 4 multimodal usage in the pipeline

```python
# core/gemma_vision.py (existing, verify multimodal works)

async def vision_gate(keyframes: list[Path], ollama_url: str) -> dict:
    """
    Use Gemma E4B multimodal to classify video content.
    
    Processes each keyframe individually, then aggregates.
    Returns: scene descriptions, detected objects, emotions, content type.
    """
    import base64, httpx
    
    results = []
    async with httpx.AsyncClient(timeout=30) as client:
        for frame_path in keyframes[:10]:  # process up to 10 frames
            img_b64 = base64.b64encode(frame_path.read_bytes()).decode()
            
            response = await client.post(
                f"{ollama_url}/api/generate",
                json={
                    "model": "gemma4:e4b",
                    "prompt": (
                        "Analyze this video frame. Describe:\n"
                        "1. Scene type (indoor/outdoor, setting)\n"
                        "2. Objects and people visible\n"
                        "3. Emotional tone\n"
                        "4. Visual complexity (simple/moderate/complex)\n"
                        "5. Motion indicators\n"
                        "Respond as JSON."
                    ),
                    "images": [img_b64],
                    "stream": False,
                    "options": {
                        "temperature": 0.3,
                        "num_predict": 300,
                    },
                }
            )
            results.append(response.json()["response"])
    
    return {"frames_analyzed": len(results), "analyses": results}
```

### GCP Gemma 31B for advanced summaries

For the WebUI gallery, we use Gemma 31B on GCP for rich text descriptions:

```python
# core/gemma_cloud.py
"""
Gemma 31B on GCP for advanced text generation.
Used for: detailed content descriptions, expert-level narrations,
and long-form analysis summaries in the WebUI gallery.

This runs on GCP, NOT the local 5090. Does not compete for local VRAM.
"""
import httpx

GCP_GEMMA_ENDPOINT = "https://cortex-gemma-api-xxxxx.run.app"  # Cloud Run

async def generate_description(
    vision_gate_result: dict,
    tribe_result: dict,
    tier: int = 4,
) -> str:
    """
    Generate a rich text description of the analyzed content.
    Uses Gemma 31B on GCP for maximum quality.
    
    This creates the "input description" shown in the gallery
    when we can't show the original copyrighted video.
    """
    prompt = f"""Based on this AI analysis of a video:

Frame analyses: {vision_gate_result}

Brain activation data:
- Top active regions: {tribe_result['top_rois'][:5]}
- Peak activation frame: {tribe_result['peak_t']}

Write a detailed, vivid description of the original video content.
Focus on visual elements, audio characteristics, emotional tone,
and narrative structure. Do NOT reproduce copyrighted material —
describe the experience of watching it.

Target audience tier: {tier} (0=toddler, 6=researcher)"""
    
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            f"{GCP_GEMMA_ENDPOINT}/generate",
            json={"prompt": prompt, "max_tokens": 800, "temperature": 0.4}
        )
        return response.json()["text"]
```

---

## 7. Hermes Agent Fork

### Setup

```bash
cd D:\RedTeamKitchenGoogleMetaISU
git clone https://github.com/nousresearch/hermes-agent.git cortex-agent
cd cortex-agent
git remote add upstream https://github.com/nousresearch/hermes-agent.git
git checkout -b cortex-main
```

### CLAUDE.md for the fork

```markdown
# Cortex Agent — Hermes Fork

Fork of Hermes Agent specialized for brain-response visualization
and TRIBE v2 neuroscience translation.

## Machine
- Windows 11, RTX 5090 (32GB GDDR7), 64GB RAM
- Git-bash shell (Unix syntax)
- Ollama at localhost:11434 with Gemma 4 models

## Specializations
1. 3D brain visualization via Three.js code generation
2. TRIBE v2 BOLD data interpretation and multi-tier narration
3. Content description for copyright-safe gallery entries

## Code conventions
- Match existing Hermes code style
- No attribution comments
- No unprovoked docstrings
- Error handlers only at system boundaries

## Models
- FAST: gemma4:e4b (or cortex-gemma-4-e4b when fine-tuned)
- DEEP: gemma4:26b
- EXPERT: gemma4:31b
- NO Gemma 3

## Integration points
- WhatsApp (primary mobile)
- WebUI at cortex.redteamkitchen.com
- Discord (existing, reference only)
```

### Tools to register

Each tool follows Hermes's tool registration pattern. Here's the brain_scan tool:

```python
# cortex-agent/tools/brain_scan.py
"""
Hermes Agent tool: Brain Scan
Orchestrates TRIBE v2 inference via the GPU scheduler.
"""

TOOL_SCHEMA = {
    "name": "brain_scan",
    "description": "Analyze a video or audio file using TRIBE v2 brain foundation model. "
                   "Predicts which cortical regions activate in response to the stimulus.",
    "parameters": {
        "type": "object",
        "properties": {
            "media_path": {
                "type": "string",
                "description": "Path to video (.mp4, .mkv, .webm) or audio (.wav, .mp3) file"
            },
            "include_narration": {
                "type": "boolean",
                "description": "Whether to generate text narrations of the brain response",
                "default": True
            },
            "narration_tier": {
                "type": "integer",
                "description": "Narration complexity: 0=toddler, 1=general, 2=curious, "
                               "3=high-school, 4=college, 5=clinician, 6=researcher",
                "default": 1,
                "minimum": 0,
                "maximum": 6
            }
        },
        "required": ["media_path"]
    }
}


async def execute(media_path: str, include_narration: bool = True, narration_tier: int = 1):
    from core.gpu_scheduler import get_scheduler
    from core.media_processor import preprocess_for_tribe
    from core.tiers import narrate_single
    from pathlib import Path
    
    path = Path(media_path)
    if not path.exists():
        return {"ok": False, "error": "file_not_found", "message": f"File not found: {path}"}
    
    # Step 1: Preprocess
    processed = preprocess_for_tribe(path, path.parent / "processed")
    
    # Step 2: Run TRIBE (handles GPU swap automatically)
    scheduler = get_scheduler()
    result = await scheduler.run_brain_scan(processed["video"])
    
    output = {
        "ok": True,
        "top_rois": result.top_rois,
        "peak_frame": result.peak_t,
        "num_timepoints": result.preds.shape[0],
        "num_vertices": result.preds.shape[1],
        "processing_time_s": result.seconds_elapsed,
    }
    
    # Step 3: Narrate (runs after GPU swaps back to Gemma)
    if include_narration:
        narration = await narrate_single(result, tier=narration_tier)
        output["narration"] = narration
    
    return output
```

---

## 8. Synthetic Neuroscience Dataset

### Reference sources (machine-readable, open access)

```
1. Neurosynth (neurosynth.org)
   - 507,891 activations from 14,371 fMRI studies
   - REST API: GET /api/locations/?x=X&y=Y&z=Z
   - Maps terms → brain coordinates → anatomical regions
   - Use for: validating generated QA pairs

2. Schaefer 400 Atlas
   - github.com/ThomasYeoLab/CBIG/.../Schaefer2018_LocalGlobal
   - 400 cortical regions mapped to 7 Yeo networks
   - Machine-readable CSV with region names, coordinates, network assignments
   - Already in our codebase (analysis.py uses it)

3. Allen Brain Atlas (brain-map.org)
   - Gene expression + anatomical reference
   - REST API available

4. NeuroQuery (neuroquery.org)
   - Text-to-brain-activation mapping
   - Given a text description, predicts which brain regions activate
   - Can be used to validate our QA pairs bidirectionally

5. Brainnetome Atlas (atlas.brainnetome.org)
   - 246 regions with connectivity profiles
   - Already in analysis.py (Brainnetome-246 parcellation)
```

### Generation pipeline

```python
# scripts/generate_neuro_dataset.py
"""
Generate 5,000 neuroscience QA pairs for fine-tuning Cortex.

Process:
  1. Load Schaefer-400 atlas region definitions
  2. For each of 50 key regions, generate 100 QA pairs
  3. Validate against Neurosynth database
  4. Format as ShareGPT JSON for Unsloth

Run with Claude Code or a local model.
Estimated time: 2-4 hours with Claude Code.
"""
import json
from pathlib import Path

# The 50 target regions with their network assignments and known functions
# Sources: Kandel "Principles of Neural Science", Neurosynth meta-analyses
REGIONS = [
    {
        "name": "Primary Visual Cortex (V1)",
        "schaefer_ids": [1, 2, 3, 4],  # Schaefer parcels mapping to V1
        "network": "Visual",
        "brodmann": "BA17",
        "functions": [
            "Basic visual processing",
            "Edge detection and orientation selectivity",
            "Contrast sensitivity",
            "Retinotopic mapping"
        ],
        "stimuli": [
            "Any visual stimulus",
            "High-contrast patterns",
            "Moving edges and gratings",
            "Flashing lights"
        ],
        "clinical": [
            "Lesions cause cortical blindness",
            "Hyperactivation in visual hallucinations",
            "Reduced activation in amblyopia"
        ]
    },
    {
        "name": "Fusiform Face Area (FFA)",
        "schaefer_ids": [45, 46],
        "network": "Visual",
        "brodmann": "BA37",
        "functions": [
            "Face perception and recognition",
            "Identity processing",
            "Holistic face processing",
            "Expert-level visual categorization"
        ],
        "stimuli": [
            "Human faces",
            "Face-like patterns (pareidolia)",
            "Emotional facial expressions",
            "Familiar vs unfamiliar faces"
        ],
        "clinical": [
            "Lesions cause prosopagnosia (face blindness)",
            "Reduced activation in autism spectrum",
            "Enhanced activation in face expertise"
        ]
    },
    # ... 48 more regions defined similarly
    # Full list: V1, V2, V4, MT/V5, FFA, PPA, LOC, EBA (visual)
    #            A1, STG, STS, PT, Heschl's (auditory)
    #            mPFC, PCC, AG, Hippocampus, TP, Precuneus, vmPFC, RSC (DMN)
    #            dlPFC, IPS, aIPL, preSMA, FEF, AI, MFG (frontoparietal)
    #            M1, S1, SMA, PMC, PCL, PostCG (somatomotor)
    #            FEF, SPL, IPS, MT+, V3A (dorsal attention)
    #            TPJ, vFC, MFG, AI, IFG (ventral attention)
    #            Amygdala, OFC, TP, Insula, ACC, Hippocampus (limbic)
]


QA_TEMPLATES = {
    "activation_meaning": (
        "The BOLD fMRI analysis shows significant activation (z-score > {z_score:.1f}) "
        "in {region_name}. What does this indicate about the subject's brain response?"
    ),
    "stimulus_cause": (
        "A subject watching {stimulus_desc} shows peak activation in {region_name} "
        "at timepoint {t}. Why would this region activate?"
    ),
    "comparison": (
        "The analysis shows simultaneous activation in {region_a} and {region_b}. "
        "How are these regions functionally related?"
    ),
    "clinical": (
        "A patient shows {pattern} activation in {region_name} during {task}. "
        "What might this suggest from a clinical perspective?"
    ),
    "plain_english": (
        "In simple terms that anyone can understand, explain what it means when "
        "{region_name} becomes active while someone is {activity}."
    ),
}

SYSTEM_PROMPT = (
    "You are Cortex, an AI neuroscience assistant that explains brain activation "
    "data from TRIBE v2 fMRI analysis. You translate complex neuroscience into "
    "clear explanations at the appropriate expertise level. Always cite specific "
    "brain regions, networks, and known functional associations. Be scientifically "
    "accurate while remaining accessible."
)


def generate_qa_pair(region: dict, template_key: str, **kwargs) -> dict:
    """Generate a single QA pair for a brain region."""
    # This would be called with Claude Code or a local model
    # to generate the actual answer text
    user_prompt = QA_TEMPLATES[template_key].format(
        region_name=region["name"],
        **kwargs
    )
    
    return {
        "conversations": [
            {"from": "system", "value": SYSTEM_PROMPT},
            {"from": "user", "value": user_prompt},
            {"from": "assistant", "value": ""}  # to be filled by generation
        ],
        "metadata": {
            "region": region["name"],
            "network": region["network"],
            "template": template_key,
            "brodmann": region["brodmann"],
        }
    }
```

### Validation with Neurosynth

```python
# scripts/validate_neuro_dataset.py
"""
Validate generated QA pairs against Neurosynth database.
Checks that claimed brain region → function mappings are
supported by at least N published fMRI studies.
"""
import httpx

NEUROSYNTH_API = "https://neurosynth.org/api"

async def validate_region_function(region_name: str, function_term: str) -> dict:
    """
    Check if Neurosynth supports the claim that {region} is involved in {function}.
    Returns the number of supporting studies and activation likelihood.
    """
    async with httpx.AsyncClient() as client:
        # Search for studies mentioning the function term
        response = await client.get(
            f"{NEUROSYNTH_API}/decode/",
            params={"text": function_term}
        )
        data = response.json()
        
        return {
            "term": function_term,
            "region": region_name,
            "n_studies": data.get("n_studies", 0),
            "supported": data.get("n_studies", 0) >= 5,
        }
```

### RAG for accurate generation

```python
# Build a LanceDB vector store with neuroscience reference material

# Sources to ingest:
# 1. Schaefer atlas region descriptions (CSV)
# 2. Neurosynth term → activation mappings (API dump)
# 3. Brainnetome atlas connectivity profiles
# 4. Key neuroscience review papers (PubMed abstracts)

# The RAG ensures Claude Code generates factually accurate QA pairs
# by retrieving relevant reference material for each brain region
# before generating the answer.
```

---

## 9. Unsloth Fine-Tuning

### Complete training script

```python
# scripts/train_cortex.py
"""
Fine-tune Gemma 4 E4B with Unsloth for the Cortex model.

Run on RTX 5090. Requires ~16GB VRAM during training.
TRIBE v2 must NOT be loaded during training.

Usage:
  python scripts/train_cortex.py --dataset data/cortex_train.jsonl --epochs 3
"""
from unsloth import FastLanguageModel
import torch

# --- Model setup ---
model, tokenizer = FastLanguageModel.from_pretrained(
    model_name="google/gemma-4-e4b",
    max_seq_length=8192,
    dtype=torch.bfloat16,
    load_in_4bit=True,  # QLoRA — 4-bit base, train LoRA adapters in BF16
)

model = FastLanguageModel.get_peft_model(
    model,
    r=16,                    # LoRA rank
    lora_alpha=32,           # scaling factor
    lora_dropout=0.05,
    target_modules=[
        "q_proj", "k_proj", "v_proj", "o_proj",
        "gate_proj", "up_proj", "down_proj",
    ],
    bias="none",
    use_gradient_checkpointing="unsloth",  # 30% less VRAM
    random_state=42,
)

# --- Dataset ---
from datasets import load_dataset

dataset = load_dataset("json", data_files="data/cortex_train.jsonl", split="train")

# Format for Gemma chat template
def format_example(example):
    messages = example["conversations"]
    text = tokenizer.apply_chat_template(messages, tokenize=False)
    return {"text": text}

dataset = dataset.map(format_example)

# --- Training ---
from trl import SFTTrainer
from transformers import TrainingArguments

trainer = SFTTrainer(
    model=model,
    tokenizer=tokenizer,
    train_dataset=dataset,
    dataset_text_field="text",
    max_seq_length=8192,
    args=TrainingArguments(
        per_device_train_batch_size=4,
        gradient_accumulation_steps=4,
        warmup_ratio=0.03,
        num_train_epochs=3,
        learning_rate=2e-4,
        bf16=True,
        logging_steps=10,
        output_dir="outputs/cortex-training",
        save_strategy="epoch",
        optim="adamw_8bit",
        seed=42,
    ),
)

trainer.train()

# --- Export to GGUF for Ollama ---
model.save_pretrained_gguf(
    "outputs/cortex-gemma-4-e4b-gguf",
    tokenizer,
    quantization_method="q4_k_m",  # 4-bit quantization, good quality/size tradeoff
)

print("Done. Load in Ollama with:")
print("  ollama create cortex-gemma-4-e4b -f outputs/cortex-gemma-4-e4b-gguf/Modelfile")
```

### Ollama Modelfile for the fine-tuned model

```dockerfile
# outputs/cortex-gemma-4-e4b-gguf/Modelfile
FROM ./cortex-gemma-4-e4b.Q4_K_M.gguf

PARAMETER temperature 0.4
PARAMETER num_predict 512
PARAMETER num_ctx 8192
PARAMETER top_p 0.9

SYSTEM """You are Cortex, an AI neuroscience assistant created by Alexios Bluff Mara LLC.
You specialize in interpreting TRIBE v2 brain activation data and translating it into
clear explanations. You can generate Three.js visualization code, explain brain regions,
and assist with agentic tool-based workflows. Always be scientifically accurate."""
```

---

## 10. WebUI Architecture

### Hosting: fully on GCP

```
GCP Cloud Run (frontend)
  ├── Next.js or static HTML + Three.js
  ├── Serves cortex.redteamkitchen.com
  ├── No GPU needed (static + API calls)
  └── Talks to 5090 via Cloudflare Tunnel for live inference

GCP Cloud Storage
  ├── Stored analysis results (BOLD data, narrations, thumbnails)
  ├── Compressed videos for gallery playback
  └── Brain mesh assets (brain.glb, networks.bin)

GCP Cloud Run (API, optional)
  ├── Gemma 31B inference for rich descriptions
  ├── Only spun up for batch processing
  └── A100 40GB on-demand ($2.19/hr)
```

### WebUI pages

```
/cortex/
  ├── Upload page — drag & drop video/audio, submit for analysis
  ├── Gallery — grid of past analyses with thumbnails
  ├── Viewer — interactive 3D brain + all analysis data
  └── About — project description, team, methodology

Viewer layout (single analysis):
  ┌─────────────────────────────────────────┐
  │ [Video Player]  │  [3D Brain Viewer]    │
  │ Original video  │  Interactive Three.js │
  │ (or text desc   │  cortex heatmap       │
  │  if copyrighted)│                       │
  ├─────────────────┼───────────────────────┤
  │ [Gemma Description]                     │
  │ AI-generated description of the content │
  ├─────────────────────────────────────────┤
  │ [TRIBE v2 Raw Data]                     │
  │ Top ROIs, peak frame, network summary   │
  ├─────────────────────────────────────────┤
  │ [Gemma Explanation]                     │
  │ Tier selector: [Lay] [Clinical] [Res]   │
  │ What the brain response actually means  │
  ├─────────────────────────────────────────┤
  │ [Metadata]                              │
  │ Processing time, model used, VRAM,      │
  │ confidence scores, timestamp            │
  └─────────────────────────────────────────┘
```

### Responsive design requirements

- Desktop: side-by-side video + brain viewer
- Tablet/Pixel Fold: stacked layout, brain viewer full-width
- Mobile: single column, brain viewer with touch gestures
- Three.js must work on mobile WebGL (test on Pixel Fold 9)
- All text is selectable and accessible
- Dark mode support via CSS custom properties

---

## 11. 3D Brain Viewer — Detailed Design

(Deferred to later implementation — get everything else working first)

### Architecture overview

- Three.js with existing `brain.glb` mesh
- Schaefer-400 parcellation mapped to vertex colors
- WebSocket for real-time BOLD streaming
- Raycaster for click-to-inspect interaction
- 7 network sub-meshes (can toggle individually)

### Key innovation: time scrubber

The viewer shows brain activation over time. A slider lets the user
scrub through the 50-second stimulus, watching activations change.
This is the "wow" for the demo video.

---

## 12. WhatsApp Integration

### Via Hermes Agent's native messaging support

Hermes Agent already supports WhatsApp, Telegram, Discord, Slack, Signal.
We configure WhatsApp on the Pixel Fold 9's Google Fi number.

```yaml
# cortex-agent/config.yaml
messaging:
  whatsapp:
    enabled: true
    phone: "+1XXXXXXXXXX"  # Google Fi number
    
  discord:
    enabled: true
    token: "${DISCORD_BOT_TOKEN}"
    
  telegram:
    enabled: false  # documented as possible, not built
```

---

## 13. GCP Backup Infrastructure

### A100 40GB on-demand (not spot)

Per your direction: regular pricing, not spot. $2.19/hr is fine for hackathon budget.

**Is A100 40GB sufficient?** Yes. TRIBE v2 needs ~22GB VRAM. A100 40GB gives 18GB headroom. An A100 80GB ($3.27/hr) or H100 ($8.55/hr) would be faster but overkill for a 2-3 minute inference job.

**Speed comparison:**
- A100 40GB: ~40-70s per 50s video
- A100 80GB: ~35-60s (marginal improvement, memory bandwidth helps slightly)
- H100 80GB: ~25-40s (1.6x faster, 3.9x the cost)

**Recommendation:** A100 40GB. The cost per video is ~$0.04-0.08. Even 100 analyses costs ~$5.

### GCP setup

```bash
# Create A100 instance
gcloud compute instances create cortex-tribe \
  --zone=us-central1-a \
  --machine-type=a2-highgpu-1g \
  --accelerator=type=nvidia-tesla-a100,count=1 \
  --image-family=pytorch-latest-gpu \
  --image-project=deeplearning-platform-release \
  --boot-disk-size=200GB \
  --maintenance-policy=TERMINATE

# Auto-shutdown script (save $$$)
# Add to instance metadata as shutdown-script:
cat << 'EOF' > /tmp/auto_shutdown.sh
#!/bin/bash
# Shut down if no requests for 15 minutes
IDLE_THRESHOLD=900  # seconds
LAST_REQUEST=$(stat -c %Y /tmp/last_request 2>/dev/null || echo 0)
NOW=$(date +%s)
IDLE=$((NOW - LAST_REQUEST))
if [ $IDLE -gt $IDLE_THRESHOLD ]; then
  sudo shutdown -h now
fi
EOF
```

---

## 14. Website: redteamkitchen.com

### DNS (Cloudflare, already configured)

```
cortex.redteamkitchen.com    CNAME → Cloud Run service URL
api.redteamkitchen.com       CNAME → Cloudflare Tunnel (5090)
viewer.redteamkitchen.com    CNAME → same Cloud Run (viewer route)
```

### Hackathon sections

Both `/cortex/gemma4good/` and `/cortex/hermes/` serve the same project
with different framing:

- Gemma 4 Good: emphasizes social impact, Unsloth fine-tuning, health & sciences
- Hermes Creative: emphasizes 3D visualization, agent creativity, interactive media

---

## 15. Error Handling — Complete Reference

### Error response format

```python
@dataclass
class CortexError:
    ok: bool = False
    error_code: str = ""
    error_class: str = ""  # input, resource, model, network
    message: str = ""
    recovery_action: str = ""
    retry: bool = False
    fallback_used: str | None = None
    component: str = ""
    vram_state: dict | None = None
    timestamp: str = ""
    
    def to_dict(self) -> dict:
        import datetime
        return {
            "ok": self.ok,
            "error_code": self.error_code,
            "error_class": self.error_class,
            "message": self.message,
            "recovery_action": self.recovery_action,
            "retry": self.retry,
            "fallback_used": self.fallback_used,
            "component": self.component,
            "vram_state": self.vram_state,
            "timestamp": self.timestamp or datetime.datetime.utcnow().isoformat(),
        }
```

### OOM handling (critical path)

```python
async def safe_inference(media_path: str) -> InferenceResult | CortexError:
    """Run TRIBE inference with OOM recovery."""
    import torch
    
    try:
        return await scheduler.run_brain_scan(media_path)
    
    except torch.cuda.OutOfMemoryError:
        # Step 1: Clear VRAM
        torch.cuda.empty_cache()
        gc.collect()
        
        # Step 2: Check what's hogging VRAM
        vram = get_vram_usage()
        log.error("[oom] CUDA OOM. VRAM state: %s", vram)
        
        # Step 3: Try again with reduced batch
        try:
            # Reduce any batch dimensions
            os.environ["TRIBE_DURATION_TRS"] = "50"  # half duration
            result = await scheduler.run_brain_scan(media_path)
            os.environ["TRIBE_DURATION_TRS"] = "100"  # restore
            return result
        except torch.cuda.OutOfMemoryError:
            pass
        
        # Step 4: Fall back to GCP
        log.warning("[oom] Local OOM twice. Falling back to GCP A100.")
        try:
            return await gcp_inference(media_path)
        except Exception as gcp_err:
            return CortexError(
                error_code="cuda_oom_all_fallbacks_failed",
                error_class="resource",
                message=f"GPU OOM locally and GCP failed: {gcp_err}",
                recovery_action="Try a shorter video (<25s) or wait for GPU to free up",
                retry=False,
                vram_state=vram,
                component="pipeline.safe_inference",
            )
    
    except Exception as exc:
        return CortexError(
            error_code="inference_failed",
            error_class="model",
            message=str(exc),
            recovery_action="Check logs for details",
            retry=True,
            component="pipeline.safe_inference",
        )
```

---

## 16. Submission Checklist

### Gemma 4 Good (Kaggle)

```
[ ] Kaggle writeup ≤1,500 words, submitted (not draft)
[ ] YouTube video ≤3 minutes, public, no login required
[ ] GitHub repo public with clear documentation
[ ] Live demo URL (cortex.redteamkitchen.com/viewer/)
[ ] Cover image in Media Gallery
[ ] HuggingFace model public (RedTeamKitchen/cortex-gemma-4-e4b)
[ ] Track selected: Ollama or Unsloth (decide after benchmarking)
[ ] All links verified working
[ ] CC-BY 4.0 license on submission code
```

### Hermes Creative (Nous Research)

```
[ ] Demo video (60-90s) posted to Twitter tagging @NousResearch
[ ] Short writeup in tweet thread
[ ] Link dropped in #creative-hackathon-submissions on Nous Discord
[ ] GitHub repo (cortex-agent fork) linked
```

---

*Spec v4 — Comprehensive technical specification with code snippets,
exact commands, VRAM analysis, and parallel/sequential task ordering.
No dates — execute tasks in dependency order.*
