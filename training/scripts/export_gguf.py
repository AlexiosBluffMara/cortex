"""export_gguf.py — merge a trained LoRA + convert to GGUF for Ollama.

Uses Unsloth's built-in `save_pretrained_gguf` which wraps llama.cpp convert +
quantize. After GGUF lands, it writes a Modelfile and (optionally) registers
the model with the local Ollama daemon.
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
    p.add_argument("--quant", default=None, help="Override quant from config (e.g. q4_k_m)")
    p.add_argument("--register", action="store_true",
                   help="Run `ollama create` against the local daemon after export")
    p.add_argument("--name", default="mercury:e4b",
                   help="Ollama model name when registering")
    args = p.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    quant = args.quant or cfg["export"]["gguf"]["quant"]
    target_dir = Path(cfg["export"]["gguf"]["target"])
    target_dir.mkdir(parents=True, exist_ok=True)

    date = datetime.now().strftime("%Y%m%d")
    base = "mercury-gemma4-e4b"
    gguf_path = target_dir / f"{base}-{date}.gguf"

    # Lazy import — same reason as in train_lora.py
    from unsloth import FastLanguageModel
    print(f"=== loading {args.ckpt} ===")
    model, tokenizer = FastLanguageModel.from_pretrained(args.ckpt, load_in_4bit=False)

    print(f"=== writing GGUF ({quant}) -> {gguf_path} ===")
    model.save_pretrained_gguf(
        str(target_dir),
        tokenizer,
        quantization_method=quant,
    )

    # Unsloth writes <model_name>-unsloth.<quant>.gguf — rename to our naming
    candidates = list(target_dir.glob(f"*-unsloth.{quant.upper()}.gguf"))
    if candidates:
        produced = candidates[0]
        shutil.move(str(produced), str(gguf_path))
    else:
        # Fallback: pick the most recent .gguf in target_dir
        ggufs = sorted(target_dir.glob("*.gguf"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        if ggufs:
            shutil.move(str(ggufs[0]), str(gguf_path))

    # Modelfile
    tmpl = cfg["export"]["gguf"]["ollama_modelfile"].replace("{date}", date)
    mf_path = target_dir / f"Modelfile.{base}-{date}"
    mf_path.write_text(tmpl, encoding="utf-8")
    print(f"=== Modelfile -> {mf_path} ===")

    if args.register:
        print(f"=== ollama create {args.name} ===")
        rc = subprocess.call(["ollama", "create", args.name, "-f", str(mf_path)])
        if rc != 0:
            print(f"WARN: ollama create exited {rc}", file=sys.stderr)
            return rc

    print(f"OK gguf={gguf_path} modelfile={mf_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
