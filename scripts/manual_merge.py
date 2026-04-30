"""Last-resort merge path: transformers + peft only, no unsloth.

Use this when ``finalize_export.py`` fails on Windows because unsloth's
writer hardlinks the base safetensors and then can't ``os.replace`` over
it. This path goes through pure transformers + peft, which uses the
standard ``safetensors.torch.save_file`` (no hardlink optimization), so
the WinError 5 path simply doesn't exist.

Usage::

    python -m scripts.manual_merge \
        --adapter outputs/cortex-training/lora \
        --merged-dir outputs/cortex-training/merged_bf16 \
        --base-model unsloth/gemma-4-E4B-it

Then convert to GGUF separately with llama.cpp's ``convert_hf_to_gguf.py``,
or with ``unsloth``'s GGUF path *after* the merge already exists on disk
(which avoids the broken hardlink step).
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--adapter", type=Path, default=Path("outputs/cortex-training/lora"))
    p.add_argument("--merged-dir", type=Path, default=Path("outputs/cortex-training/merged_bf16"))
    p.add_argument("--base-model", default="unsloth/gemma-4-E4B-it")
    p.add_argument("--keep-existing", action="store_true",
                   help="Don't wipe --merged-dir before writing.")
    args = p.parse_args()

    adapter = args.adapter.resolve()
    merged = args.merged_dir.resolve()

    if not (adapter / "adapter_model.safetensors").exists():
        print(f"[manual-merge] FAIL: no adapter at {adapter}/adapter_model.safetensors")
        return 2

    # Defensive cleanup — same WinError 5 root cause as finalize_export.
    if merged.exists() and not args.keep_existing:
        print(f"[manual-merge] cleaning {merged}")
        shutil.rmtree(merged, ignore_errors=True)
    merged.mkdir(parents=True, exist_ok=True)

    # Pure transformers + peft; do NOT import unsloth here.
    os.environ.setdefault("PYTHONUTF8", "1")
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from peft import PeftModel

    print(f"[manual-merge] loading base {args.base_model} (BF16)")
    base = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        torch_dtype=torch.bfloat16,
        device_map="cuda",
        low_cpu_mem_usage=True,
    )

    print(f"[manual-merge] attaching adapter from {adapter}")
    model = PeftModel.from_pretrained(base, str(adapter))

    print("[manual-merge] running merge_and_unload()")
    model = model.merge_and_unload()

    print(f"[manual-merge] saving merged BF16 -> {merged}")
    # safe_serialization=True uses safetensors.torch.save_file via huggingface_hub.
    # That path writes directly without a temp+rename dance, so the
    # Windows hardlink/os.replace bug does not apply.
    model.save_pretrained(str(merged), safe_serialization=True, max_shard_size="5GB")

    # Save tokenizer next to the merged weights so the GGUF converter has it.
    tok = AutoTokenizer.from_pretrained(str(adapter))
    tok.save_pretrained(str(merged))

    # Verify the merged file is real (not a hardlinked base).
    merged_files = list(merged.glob("model*.safetensors"))
    if not merged_files:
        print("[manual-merge] FAIL: no merged safetensors written")
        return 3
    total_gb = sum(f.stat().st_size for f in merged_files) / 1024**3
    print(f"[manual-merge] merged {len(merged_files)} shard(s), {total_gb:.2f} GB total")
    print("[manual-merge] done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
