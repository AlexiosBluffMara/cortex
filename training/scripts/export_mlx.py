"""export_mlx.py — merge LoRA + convert to MLX 4-bit for Big Apple.

Uses mlx_lm.convert under the hood. Produces an MLX model directory that
mlx_lm.server (running on Big Apple) can load via --model.

Run this on the Mac (Apple Silicon) OR on this Windows box (mlx package
installs even without an MPS device for the convert step). For deployment
the artifact must be copied to ~/.cache/huggingface/hub/ on Big Apple.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ckpt", required=True, help="LoRA checkpoint dir")
    p.add_argument("--config", default="configs/mercury-gemma4-e4b-lora.yaml")
    p.add_argument("--bits", type=int, default=None, help="Override quant bits (default 4)")
    p.add_argument("--merge-only", action="store_true",
                   help="Just produce a merged HF checkpoint, skip MLX conversion")
    args = p.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    bits = args.bits or cfg["export"]["mlx"]["quant_bits"]
    target_dir = Path(cfg["export"]["mlx"]["target"])
    target_dir.mkdir(parents=True, exist_ok=True)
    date = datetime.now().strftime("%Y%m%d")
    base = "mercury-gemma4-e4b"
    merged_dir = target_dir / f"{base}-merged-{date}"
    mlx_dir = target_dir / f"{base}-{date}-mlx"

    # Step 1. Merge LoRA into a full HF checkpoint (Unsloth helper)
    from unsloth import FastLanguageModel
    print(f"=== loading + merging {args.ckpt} ===")
    model, tokenizer = FastLanguageModel.from_pretrained(args.ckpt, load_in_4bit=False)
    print(f"=== writing merged HF -> {merged_dir} ===")
    model.save_pretrained_merged(str(merged_dir), tokenizer, save_method="merged_16bit")

    if args.merge_only:
        print(f"OK merged={merged_dir}")
        return 0

    # Step 2. Convert to MLX 4-bit. Requires the `mlx_lm` package.
    print(f"=== mlx_lm.convert -> {mlx_dir} ===")
    rc = subprocess.call([
        sys.executable, "-m", "mlx_lm.convert",
        "--hf-path", str(merged_dir),
        "--mlx-path", str(mlx_dir),
        "-q", "--q-bits", str(bits),
    ])
    if rc != 0:
        print(f"ERROR: mlx_lm.convert exited {rc}", file=sys.stderr)
        return rc

    print(f"OK mlx={mlx_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
