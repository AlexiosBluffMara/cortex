"""train_ternary_gemma_mps.py
PyTorch QAT for Gemma 4 E2B → ternary, running on Apple Silicon (MPS).
Uses 48 GB unified memory on the M4 Max — fits teacher + student + optimizer
where the 5090's 32 GB GDDR7 doesn't.

Differences vs the CUDA variant:
  - device = "mps" instead of "cuda:0"
  - No bitsandbytes (no AdamW8bit on Mac); use torch.optim.AdamW
  - No NF4 teacher (no bnb on Mac); teacher is bf16 (fits in unified mem)

Run on Big Apple:
  ~/cortex-pytorch/bin/python train_ternary_gemma_mps.py \\
      --config /Users/soumitlahiri/cortex-training/configs/ternary-gemma-e2b-mps.yaml
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader

from bitnetize import (
    BitLinear,
    replace_linear_with_bitlinear,
    count_ternary_params,
)


def _device() -> torch.device:
    if torch.backends.mps.is_available():
        return torch.device("mps")
    if torch.cuda.is_available():
        return torch.device("cuda:0")
    return torch.device("cpu")


def build_dataset(cfg, tokenizer):
    from datasets import load_dataset, interleave_datasets

    streams, weights = [], []
    max_seq = cfg["dataset"]["max_seq_length"]
    for src in cfg["dataset"]["sources"]:
        kwargs = {}
        if "subset" in src:
            kwargs["name"] = src["subset"]
        try:
            ds = load_dataset(src["name"], split="train", streaming=True, **kwargs)
        except Exception as exc:
            print(f"WARN: skip {src['name']}: {exc}", file=sys.stderr)
            continue
        if "max_examples" in src:
            ds = ds.take(src["max_examples"])

        def pick_text(ex):
            for k in ("text", "content", "instruction", "prompt"):
                v = ex.get(k)
                if isinstance(v, str) and v:
                    return v
            for v in ex.values():
                if isinstance(v, str) and v:
                    return v
            return " "

        ds = ds.map(lambda ex: {"text": pick_text(ex)})
        streams.append(ds)
        weights.append(src.get("weight", 1.0))

    if not streams:
        raise RuntimeError("no datasets loaded")
    s = sum(weights)
    weights = [w / s for w in weights]
    mixed = interleave_datasets(streams, probabilities=weights, seed=42,
                                stopping_strategy="all_exhausted")

    def tok(ex):
        text = ex.get("text") or " "
        if not isinstance(text, str) or not text.strip():
            text = " "
        out = tokenizer(text, truncation=True, max_length=max_seq,
                        padding="max_length", return_tensors=None)
        keep = {}
        for k in ("input_ids", "attention_mask"):
            v = out.get(k)
            keep[k] = v if v is not None else [0] * max_seq
        keep["labels"] = list(keep["input_ids"])
        return keep

    return mixed.map(tok, batched=False)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--max-steps", type=int, default=None)
    args = p.parse_args()

    cfg = yaml.safe_load(open(args.config, "r", encoding="utf-8"))
    if args.max_steps is not None:
        cfg["training"]["max_steps"] = args.max_steps

    device = _device()
    print(f"=== device: {device} ===")
    if device.type == "mps":
        # Free up unified memory aggressively
        os.environ.setdefault("PYTORCH_MPS_HIGH_WATERMARK_RATIO", "0.0")

    from transformers import AutoModelForCausalLM, AutoTokenizer, get_cosine_schedule_with_warmup

    distill_enabled = cfg.get("distill", {}).get("enabled", True)
    teacher = None
    if distill_enabled:
        print(f"=== Loading teacher: {cfg['teacher']} ===")
        teacher = AutoModelForCausalLM.from_pretrained(
            cfg["teacher"], torch_dtype=torch.bfloat16
        ).to(device)
        teacher.eval()
        for pp in teacher.parameters():
            pp.requires_grad_(False)

    tokenizer = AutoTokenizer.from_pretrained(cfg["teacher"])
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    print(f"=== Loading student: {cfg['student_init']} ===")
    student = AutoModelForCausalLM.from_pretrained(
        cfg["student_init"], torch_dtype=torch.bfloat16
    ).to(device)

    n_swapped = replace_linear_with_bitlinear(
        student,
        include=cfg["ternarise"]["patterns"],
        exclude=cfg["ternarise"]["exclude"],
    )
    t, a = count_ternary_params(student)
    print(f"BitLinear-swapped {n_swapped} modules. Ternary {t/1e6:.1f} M / total {a/1e6:.1f} M")

    # Move newly-created BitLinear modules to device + dtype
    student = student.to(device).to(torch.bfloat16)

    # Freeze everything except BitLinear weights
    bl_ids = set()
    for m in student.modules():
        if isinstance(m, BitLinear):
            bl_ids.add(id(m.weight))
            if m.bias is not None:
                bl_ids.add(id(m.bias))
            # The pre-norm RMSNorm inside BitLinear is also tiny+trainable
            for p in m.norm.parameters():
                bl_ids.add(id(p))
    n_train = 0
    for p in student.parameters():
        if id(p) in bl_ids:
            p.requires_grad_(True)
            n_train += p.numel()
        else:
            p.requires_grad_(False)
    print(f"Trainable: {n_train/1e6:.1f} M ({100*n_train/a:.2f}% of model)")
    if cfg["training"].get("gradient_checkpointing", False):
        student.gradient_checkpointing_enable()
        print("gradient_checkpointing: enabled")
    else:
        print("gradient_checkpointing: disabled (faster, more memory)")

    print("=== building dataset ===")
    ds = build_dataset(cfg, tokenizer)
    bs = cfg["training"]["per_device_train_batch_size"]
    accum = cfg["training"]["gradient_accumulation_steps"]

    def _collate(batch):
        keys = ("input_ids", "attention_mask", "labels")
        out = {}
        for k in keys:
            seqs = [ex[k] for ex in batch if isinstance(ex.get(k), (list, tuple))]
            if not seqs:
                return None
            out[k] = torch.tensor(seqs, dtype=torch.long)
        return out

    dl = DataLoader(ds, batch_size=bs, num_workers=0, pin_memory=False,
                    collate_fn=_collate)

    max_steps = int(cfg["training"]["max_steps"])
    lr = float(cfg["training"]["learning_rate"])
    opt = torch.optim.AdamW(
        (p for p in student.parameters() if p.requires_grad),
        lr=lr, betas=(0.9, 0.95), weight_decay=cfg["training"]["weight_decay"],
    )
    sched = get_cosine_schedule_with_warmup(opt, cfg["training"]["warmup_steps"], max_steps)

    out_dir = Path(cfg["training"]["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    alpha_kl = cfg["distill"]["alpha_kl"]
    alpha_ce = cfg["distill"]["alpha_ce"]
    T = cfg["distill"]["temperature"]

    print(f"=== training {max_steps} steps (bs {bs} x accum {accum}) ===")
    student.train()
    step = 0
    micro = 0
    accum_l = accum_kl = accum_ce = 0.0
    t0 = time.time()

    for batch in dl:
        if step >= max_steps:
            break
        if batch is None:
            continue
        ids = batch["input_ids"].to(device, non_blocking=True)
        lbl = batch["labels"].to(device, non_blocking=True)

        if teacher is not None:
            with torch.no_grad():
                t_logits = teacher(input_ids=ids).logits
        s_logits = student(input_ids=ids).logits

        if teacher is not None:
            kl = F.kl_div(
                F.log_softmax(s_logits.float() / T, dim=-1),
                F.softmax(t_logits.float() / T, dim=-1),
                reduction="batchmean",
            ) * (T * T)
        else:
            kl = torch.zeros((), device=device)

        shift_logits = s_logits[..., :-1, :].contiguous()
        shift_labels = lbl[..., 1:].contiguous()
        ce = F.cross_entropy(
            shift_logits.view(-1, shift_logits.size(-1)).float(),
            shift_labels.view(-1),
            ignore_index=tokenizer.pad_token_id,
        )
        loss = (alpha_kl * kl + alpha_ce * ce) / accum
        loss.backward()

        accum_l += loss.item() * accum
        accum_kl += float(kl)
        accum_ce += float(ce)
        micro += 1

        if micro % accum == 0:
            torch.nn.utils.clip_grad_norm_(
                (p for p in student.parameters() if p.requires_grad), 1.0
            )
            opt.step()
            sched.step()
            opt.zero_grad(set_to_none=True)
            step += 1

            if step % cfg["training"]["logging_steps"] == 0:
                dt = time.time() - t0
                lr_now = sched.get_last_lr()[0]
                print(
                    f"step {step:>6}  loss {accum_l/accum:.4f}  "
                    f"kl {accum_kl/accum:.3f}  ce {accum_ce/accum:.3f}  "
                    f"lr {lr_now:.2e}  ({dt/60:.1f} min, {step/dt:.2f} step/s)"
                )
                accum_l = accum_kl = accum_ce = 0.0

            if step % cfg["training"]["save_steps"] == 0:
                ck = out_dir / f"step-{step}"
                ck.mkdir(exist_ok=True)
                student.save_pretrained(ck, safe_serialization=True)
                tokenizer.save_pretrained(ck)
                print(f"  saved {ck}")

    final = out_dir / "final"
    final.mkdir(exist_ok=True, parents=True)
    student.save_pretrained(final, safe_serialization=True)
    tokenizer.save_pretrained(final)
    print(f"=== done. final -> {final} ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
