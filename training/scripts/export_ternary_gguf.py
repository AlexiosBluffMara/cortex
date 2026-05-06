"""export_ternary_gguf.py
Take a QAT-trained checkpoint (BitLinear modules + FP teacher heads) and emit
a bitnet.cpp-compatible I2_S GGUF for inference on the Pi 5.

Process:
  1. Load HF checkpoint (BitLinear modules)
  2. For each BitLinear, snap weight to {-1,0,+1} via absmean and store the
     scale separately
  3. Pack 4 ternary weights per byte (2 bits each, so 8 weights/byte using
     a 3-state lookup; bitnet.cpp's I2_S layout uses 4 weights per byte
     with sign+magnitude)
  4. Write GGUF with bitnet.cpp's expected metadata + tensor types
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from bitnetize import BitLinear, quantise_weight_ternary


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--ckpt", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--bitnet-repo",
                   default=str(Path.home() / "BitNet"),
                   help="Local clone of microsoft/BitNet (uses its convert script)")
    args = p.parse_args()

    # Strategy: defer the GGUF write to bitnet.cpp's official conversion
    # pipeline. We just snap weights to ternary and save a clean HF
    # checkpoint that BitNet/utils/convert-helper-bitnet.py can process.

    from transformers import AutoModelForCausalLM, AutoTokenizer

    print(f"=== loading {args.ckpt} ===")
    model = AutoModelForCausalLM.from_pretrained(args.ckpt, torch_dtype=torch.bfloat16)
    tok = AutoTokenizer.from_pretrained(args.ckpt)

    # Snap each BitLinear to its hard ternary value + scale
    n = 0
    with torch.no_grad():
        for name, m in model.named_modules():
            if isinstance(m, BitLinear):
                w = m.weight.data
                w_q, scale = quantise_weight_ternary(w)
                # Replace stored weight with the rounded ternary, retain scale
                # as a separate scalar buffer (bitnet.cpp expects per-tensor scale).
                m.weight.data.copy_((w_q * scale).to(w.dtype))
                m.register_buffer("ternary_scale", scale.detach().clone(), persistent=True)
                n += 1
    print(f"snapped {n} BitLinear modules to hard ternary")

    snap_dir = Path(args.output).with_suffix(".snap")
    snap_dir.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(snap_dir, safe_serialization=True)
    tok.save_pretrained(snap_dir)
    print(f"snapped checkpoint -> {snap_dir}")

    # Hand off to bitnet.cpp's converter
    repo = Path(args.bitnet_repo)
    conv = repo / "utils" / "convert-helper-bitnet.py"
    if conv.exists():
        import subprocess
        cmd = [sys.executable, str(conv), str(snap_dir)]
        print(f"--- {cmd} ---")
        rc = subprocess.call(cmd)
        if rc != 0:
            print("bitnet.cpp converter failed; manual GGUF write skipped.")
            return rc
        # The helper writes ggml-model-i2_s.gguf next to the input; move it.
        produced = next(snap_dir.glob("*i2_s*.gguf"), None)
        if produced:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            os.rename(produced, args.output)
            print(f"OK -> {args.output}")
    else:
        print(f"WARN: bitnet.cpp helper not at {conv}.")
        print("    Clone microsoft/BitNet and re-run, or copy {snap_dir} to the Pi")
        print("    and run the conversion there.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
