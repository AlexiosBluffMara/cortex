"""Resume the train_cortex pipeline at the merge stage.

Use case: training succeeded and the LoRA adapter is saved at
``outputs/cortex-training/lora/``, but the merge step failed (e.g. Windows
``WinError 5: Access is denied`` from ``os.replace`` over a stale
``model.safetensors``). Re-running the full ``train_cortex`` would burn
~50 min retraining for nothing — this script picks up from step 6
(merge → GGUF → Modelfile) without retraining.

Usage::

    python -m scripts.finalize_export \
        --output-dir outputs/cortex-training \
        --gguf q4_k_m

Outputs:
  <output-dir>/merged_bf16/   full BF16 base + LoRA, HF format
  <output-dir>/gguf/          GGUF + Modelfile for ``ollama create``
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-dir", type=Path, default=Path("outputs/cortex-training"))
    p.add_argument("--base-model", default="unsloth/gemma-4-E4B-it")
    p.add_argument("--max-seq-length", type=int, default=8192)
    p.add_argument(
        "--gguf",
        choices=["q4_k_m", "q5_k_m", "q8_0", "bf16"],
        default="q4_k_m",
    )
    p.add_argument("--skip-merge", action="store_true",
                   help="Skip BF16 merge step; only GGUF.")
    p.add_argument("--skip-gguf", action="store_true",
                   help="Skip GGUF export; only BF16 merge.")
    args = p.parse_args()

    out = args.output_dir.resolve()
    adapter_dir = out / "lora"
    merged_dir = out / "merged_bf16"
    gguf_dir = out / "gguf"

    if not (adapter_dir / "adapter_model.safetensors").exists():
        print(f"[finalize] FAIL: no adapter at {adapter_dir}/adapter_model.safetensors")
        return 2

    # Mirror train_cortex.py import order: env → torch → unsloth → everything else.
    os.environ.setdefault("UNSLOTH_RETURN_LOGITS", "1")
    import torch  # noqa: F401
    from unsloth import FastLanguageModel
    from unsloth.chat_templates import get_chat_template

    # Pre-clean the merge dir to avoid Windows os.replace failures over a stale
    # model.safetensors that has Links > 1 (hardlinked from a previous attempt).
    # Unsloth's writer uses tempfile + os.replace; replace fails when the
    # destination has more than one hardlink under some Windows configs.
    if not args.skip_merge and merged_dir.exists():
        import shutil
        print(f"[finalize] cleaning stale {merged_dir}")
        shutil.rmtree(merged_dir, ignore_errors=True)

    # Load the LoRA adapter on top of the base model. Pointing
    # FastLanguageModel.from_pretrained at the adapter directory makes
    # unsloth load the base from peft's adapter_config.json automatically.
    print(f"[finalize] loading adapter from {adapter_dir}")
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=str(adapter_dir),
        max_seq_length=args.max_seq_length,
        dtype=torch.bfloat16,
        load_in_4bit=True,
    )
    tokenizer = get_chat_template(tokenizer, chat_template="gemma-4-thinking")
    print(f"[finalize] adapter loaded; tokenizer={type(tokenizer).__name__}")

    if not args.skip_merge:
        print(f"[finalize] merging to BF16 -> {merged_dir}")
        model.save_pretrained_merged(str(merged_dir), tokenizer)
        print(f"[finalize] BF16 merge OK")

    if not args.skip_gguf:
        print(f"[finalize] exporting GGUF ({args.gguf}) -> {gguf_dir}")
        model.save_pretrained_gguf(
            str(gguf_dir), tokenizer, quantization_method=args.gguf,
        )
        gguf_file = next(gguf_dir.glob("*.gguf"), None)
        if gguf_file is None:
            print("[finalize] FAIL: no .gguf file produced")
            return 3
        print(f"[finalize] GGUF OK: {gguf_file} ({gguf_file.stat().st_size/1024**3:.2f} GB)")

        # Reuse train_cortex.render_modelfile so Modelfile invariants stay
        # in lock-step with the production trainer.
        from scripts.train_cortex import render_modelfile
        modelfile = render_modelfile(gguf_filename=gguf_file.name)
        (gguf_dir / "Modelfile").write_text(modelfile, encoding="utf-8")
        print(f"[finalize] Modelfile OK: {gguf_dir / 'Modelfile'}")

    print("[finalize] done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
