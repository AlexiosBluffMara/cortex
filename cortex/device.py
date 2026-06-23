"""device.py — single source of truth for GPU/accelerator selection.

Abstracts CUDA (NVIDIA, e.g. RTX 5090) vs MPS (Apple Silicon)
vs CPU. Every other module in the package should import `DEVICE`,
`DEVICE_KIND`, and the `free_vram_gb()` / `used_vram_gb()` / `empty_cache()`
helpers from here instead of touching `torch.cuda` or `nvidia-smi` directly.

DEVICE_KIND values:
    "cuda"  — NVIDIA GPU available; dedicated VRAM model.
    "mps"   — Apple Silicon Metal; unified memory model (no separate VRAM pool).
    "cpu"   — fallback; everything runs slow.

Memory accounting differs per kind:
    cuda: free = (total_vram - reserved); queried via nvidia-smi or torch.
    mps:  unified memory. We report the system's free RAM as the budget.
    cpu:  same as mps, just the system memory.

The TRIBE inference code path treats DEVICE as the only knob.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import Literal

import torch

# CREATE_NO_WINDOW. Every `nvidia-smi` subprocess call MUST pass this on
# Windows. Without it, nvidia-smi.exe (a console app) allocates a console
# (conhost.exe) on each invocation — a black window that flashes on screen
# and steals keyboard focus. The webapp polls free/used VRAM every ~2s, so
# this flashed ~2x/2s relentlessly. This was THE flashing-window bug
# (traced 2026-05-15 via process-spawn capture). 0 on non-Windows = no-op.
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

DeviceKind = Literal["cuda", "mps", "cpu"]


def _detect_kind() -> DeviceKind:
    # Allow CORTEX_DEVICE override for testing (= "cuda" / "mps" / "cpu")
    forced = os.environ.get("CORTEX_DEVICE", "").lower()
    if forced in ("cuda", "mps", "cpu"):
        return forced  # type: ignore[return-value]
    if torch.cuda.is_available():
        return "cuda"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


DEVICE_KIND: DeviceKind = _detect_kind()
DEVICE: torch.device = torch.device(DEVICE_KIND if DEVICE_KIND != "mps" else "mps")

# Apple Silicon supports bf16 natively on M3+; older MPS may not.
# CUDA bf16 supported on Ampere (sm_80) and newer.
def _supports_bf16() -> bool:
    if DEVICE_KIND == "cuda":
        try:
            major, _ = torch.cuda.get_device_capability(0)
            return major >= 8
        except Exception:
            return False
    if DEVICE_KIND == "mps":
        # Apple Silicon: M3+ has full bf16; M1/M2 have partial. Conservative on.
        return True
    return False


SUPPORTS_BF16: bool = _supports_bf16()


def device_name() -> str:
    """Human-readable identifier for logging / health endpoints."""
    if DEVICE_KIND == "cuda":
        try:
            return torch.cuda.get_device_name(0)
        except Exception:
            return "CUDA (unknown)"
    if DEVICE_KIND == "mps":
        # macOS doesn't expose chip name through PyTorch. Try sysctl.
        try:
            r = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True, timeout=2,
            )
            return r.stdout.strip() or "Apple Silicon (MPS)"
        except Exception:
            return "Apple Silicon (MPS)"
    return "CPU"


def free_vram_gb() -> float:
    """Free memory available for compute, in GB.

    cuda: nvidia-smi reading (most accurate including external apps' usage).
    mps:  system free memory (unified pool).
    cpu:  system free memory.
    """
    if DEVICE_KIND == "cuda":
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.free",
                 "--format=csv,nounits,noheader", "--id=0"],
                capture_output=True, text=True, timeout=2, check=True,
                creationflags=_NO_WINDOW,
            )
            return float(r.stdout.strip()) / 1024.0
        except Exception:
            # Fall back to torch's view (less accurate — doesn't see other procs)
            try:
                props = torch.cuda.get_device_properties(0)
                free = (props.total_memory - torch.cuda.memory_reserved(0)) / 1e9
                return float(free)
            except Exception:
                return 0.0
    # mps/cpu: report system free RAM
    try:
        import psutil
        return psutil.virtual_memory().available / 1e9
    except ImportError:
        # If psutil isn't installed, give a generous-but-finite default
        return 16.0


def used_vram_gb() -> float:
    """Memory currently in use by GPU compute (or system, on Mac)."""
    if DEVICE_KIND == "cuda":
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used",
                 "--format=csv,nounits,noheader", "--id=0"],
                capture_output=True, text=True, timeout=2, check=True,
                creationflags=_NO_WINDOW,
            )
            return float(r.stdout.strip()) / 1024.0
        except Exception:
            try:
                return torch.cuda.memory_allocated(0) / 1e9
            except Exception:
                return 0.0
    if DEVICE_KIND == "mps":
        # PyTorch tracks MPS allocations
        try:
            return torch.mps.current_allocated_memory() / 1e9
        except (AttributeError, RuntimeError):
            return 0.0
    return 0.0


def total_vram_gb() -> float:
    """Total accessible memory budget for compute on this device."""
    if DEVICE_KIND == "cuda":
        try:
            return torch.cuda.get_device_properties(0).total_memory / 1e9
        except Exception:
            return 0.0
    # MPS / CPU = system RAM (unified pool on Apple Silicon)
    try:
        import psutil
        return psutil.virtual_memory().total / 1e9
    except ImportError:
        return 32.0


def empty_cache() -> None:
    """Release any free memory the framework is holding back to the OS/driver."""
    if DEVICE_KIND == "cuda":
        try:
            torch.cuda.empty_cache()
            torch.cuda.synchronize()
            torch.cuda.empty_cache()
        except Exception:
            pass
    elif DEVICE_KIND == "mps":
        try:
            torch.mps.empty_cache()
            torch.mps.synchronize()
        except (AttributeError, RuntimeError):
            pass
    # cpu: no-op


def reset_peak_stats() -> None:
    if DEVICE_KIND == "cuda":
        try:
            torch.cuda.reset_peak_memory_stats()
        except Exception:
            pass
    # mps/cpu: no-op (mps doesn't expose peak stats consistently)


def is_available() -> bool:
    """True if we have an accelerator (cuda or mps), False on cpu-only."""
    return DEVICE_KIND in ("cuda", "mps")


def autocast_dtype() -> torch.dtype:
    """Best dtype for inference on this device."""
    if SUPPORTS_BF16:
        return torch.bfloat16
    if DEVICE_KIND == "cuda":
        return torch.float16
    return torch.float32


def has_nvidia_smi() -> bool:
    return shutil.which("nvidia-smi") is not None
