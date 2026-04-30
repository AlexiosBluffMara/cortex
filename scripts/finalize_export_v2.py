"""Patched-Unsloth merge + GGUF export for Windows.

Why this exists: ``finalize_export.py`` and ``manual_merge.py`` both fail:
  - finalize_export hits unsloth's ``_merge_and_overwrite_lora`` which calls
    ``os.replace(tmp, model.safetensors)`` while the destination is still
    mapped/held — Windows returns ``[WinError 5] Access is denied`` and
    unsloth's retry loop only catches WinError 32 / 1224 plus *specific*
    WinError 5 messages ("user-mapped", "sharing violation"). Plain
    "Access is denied" falls through to a hard fail.
  - manual_merge fails earlier: peft 0.19.1 doesn't recognize Gemma 4's
    custom ``Gemma4ClippableLinear`` layer wrapper as a Linear target.

This script monkey-patches ``os.replace`` inside the unsloth saving path so
that on WinError 5 it falls back to ``os.unlink(dst)`` + ``os.rename(src, dst)``
— breaks any lingering hardlink / mmap reference on Windows. Otherwise it
behaves identically to the upstream call.

Usage::

    python -m scripts.finalize_export_v2 \
        --output-dir outputs/cortex-training \
        --gguf q4_k_m
"""
from __future__ import annotations

import argparse
import gc
import os
import shutil
import sys
import time
from pathlib import Path


def _install_os_replace_patch() -> None:
    """Wrap os.replace in unsloth_zoo.saving_utils so plain WinError 5 retries
    by unlinking the destination first.

    Only patches the binding inside that one module, so other os.replace
    callers are unaffected.
    """
    import unsloth_zoo.saving_utils as _su

    real_replace = os.replace

    def _patched_replace(src, dst):
        try:
            return real_replace(src, dst)
        except PermissionError as exc:
            if os.name != "nt":
                raise
            # WinError 5 = Access denied; the destination probably has a
            # lingering mmap or hardlink. Drop refs, sleep briefly, then
            # unlink+rename which always works onto a missing target.
            winerror = getattr(exc, "winerror", None)
            if winerror not in (5, 32, 1224):
                raise
            for attempt in range(5):
                try:
                    gc.collect()
                    time.sleep(0.5 * (attempt + 1))
                    if os.path.exists(dst):
                        try:
                            os.chmod(dst, 0o666)
                        except OSError:
                            pass
                        os.unlink(dst)
                    os.rename(src, dst)
                    print(f"[v2-patch] os.replace WinError {winerror} -> "
                          f"unlink+rename succeeded on attempt {attempt+1}",
                          flush=True)
                    return None
                except OSError as inner:
                    if attempt == 4:
                        raise
                    print(f"[v2-patch] retry {attempt+1}/5 after {inner!r}",
                          flush=True)

    _su.os.replace = _patched_replace
    print("[v2-patch] installed os.replace wrapper in unsloth_zoo.saving_utils",
          flush=True)


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
    p.add_argument("--skip-merge", action="store_true")
    p.add_argument("--skip-gguf", action="store_true")
    args = p.parse_args()

    out = args.output_dir.resolve()
    adapter_dir = out / "lora"
    merged_dir = out / "merged_bf16"
    gguf_dir = out / "gguf"

    if not (adapter_dir / "adapter_model.safetensors").exists():
        print(f"[v2] FAIL: no adapter at {adapter_dir}/adapter_model.safetensors")
        return 2

    # Pre-clean both potential targets — both have the same hardlink
    # problem when stale.
    for d in (merged_dir, gguf_dir):
        if d.exists():
            print(f"[v2] cleaning {d}")
            shutil.rmtree(d, ignore_errors=True)

    # Mirror train_cortex import order: env -> torch -> unsloth -> rest.
    os.environ.setdefault("UNSLOTH_RETURN_LOGITS", "1")
    os.environ.setdefault("PYTHONUTF8", "1")
    import torch  # noqa: F401
    # unsloth_zoo refuses to import until unsloth has loaded — so we import
    # unsloth first, THEN patch unsloth_zoo's saving_utils, THEN proceed.
    from unsloth import FastLanguageModel
    from unsloth.chat_templates import get_chat_template
    _install_os_replace_patch()

    print(f"[v2] loading adapter from {adapter_dir}")
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=str(adapter_dir),
        max_seq_length=args.max_seq_length,
        dtype=torch.bfloat16,
        load_in_4bit=True,
    )
    tokenizer = get_chat_template(tokenizer, chat_template="gemma-4-thinking")
    print(f"[v2] adapter loaded; tokenizer={type(tokenizer).__name__}")

    if not args.skip_merge:
        print(f"[v2] merging to BF16 -> {merged_dir}")
        model.save_pretrained_merged(str(merged_dir), tokenizer)
        print("[v2] BF16 merge OK")

    if not args.skip_gguf:
        print(f"[v2] exporting GGUF ({args.gguf}) -> {gguf_dir}")
        model.save_pretrained_gguf(
            str(gguf_dir), tokenizer, quantization_method=args.gguf,
        )
        gguf_files = list(gguf_dir.glob("*.gguf"))
        if not gguf_files:
            print("[v2] FAIL: no .gguf produced")
            return 3
        gguf_file = gguf_files[0]
        print(f"[v2] GGUF OK: {gguf_file} ({gguf_file.stat().st_size/1024**3:.2f} GB)")

        from scripts.train_cortex import render_modelfile
        modelfile = render_modelfile(gguf_filename=gguf_file.name)
        (gguf_dir / "Modelfile").write_text(modelfile, encoding="utf-8")
        print(f"[v2] Modelfile OK: {gguf_dir / 'Modelfile'}")

    print("[v2] done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
