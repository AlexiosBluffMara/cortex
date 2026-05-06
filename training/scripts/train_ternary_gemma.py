"""train_ternary_gemma.py
Quantization-Aware-Training (QAT) loop that takes Gemma 4 e2b, swaps its
linear layers for BitLinear (ternary weights, 8-bit activations), and
distills from the frozen FP16 teacher on a high-quality text mix.

Run on the 5090 (32 GB GDDR7 / sm_120 / Blackwell):
    cd /mnt/d/cortex/training
    source ~/unsloth-env/.venv/bin/activate
    python scripts/train_ternary_gemma.py --config configs/ternary-gemma-e2b.yaml

Memory plan on the 5090:
    teacher (bf16)               5.0 GB
    student (bf16 shadow)        5.0 GB
    optimizer state (8-bit)      ~1.5 GB
    activations + KV (bs 4 x 2K) ~6 GB
    total                        ~17 GB / 32 GB
"""
from __future__ import annotations

import argparse
import os
import random
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader

from bitnetize import (
    replace_linear_with_bitlinear,
    count_ternary_params,
    quantise_weight_ternary,
)


def load_cfg(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_dataset(cfg: dict, tokenizer):
    """Stream + interleave the configured sources, tokenize to fixed seq length."""
    from datasets import load_dataset, interleave_datasets

    streams = []
    weights = []
    max_seq = cfg["dataset"]["max_seq_length"]

    for src in cfg["dataset"]["sources"]:
        kwargs: dict = {}
        if "subset" in src:
            kwargs["name"] = src["subset"]
        try:
            ds = load_dataset(src["name"], split="train", streaming=True, **kwargs)
        except Exception as exc:
            print(f"WARN: could not load {src['name']}: {exc}", file=sys.stderr)
            continue
        if "max_examples" in src:
            ds = ds.take(src["max_examples"])
        # Pick the right text column — most common is "text", fall back to first str field
        def pick_text(ex):
            for k in ("text", "content", "instruction", "prompt"):
                if k in ex and isinstance(ex[k], str):
                    return ex[k]
            for v in ex.values():
                if isinstance(v, str):
                    return v
            return ""
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
        # Force non-None values; only keep keys we want to feed downstream
        keep = {}
        for k in ("input_ids", "attention_mask"):
            v = out.get(k)
            if v is None:
                v = [0] * max_seq
            keep[k] = v
        keep["labels"] = list(keep["input_ids"])
        return keep

    mixed = mixed.map(tok, batched=False)
    return mixed


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--max-steps", type=int, default=None,
                   help="Override config max_steps for a smoke run")
    args = p.parse_args()

    cfg = load_cfg(args.config)
    if args.max_steps is not None:
        cfg["training"]["max_steps"] = args.max_steps

    from transformers import (
        AutoModelForCausalLM, AutoTokenizer, get_cosine_schedule_with_warmup
    )
    from torch.optim import AdamW
    try:
        from bitsandbytes.optim import AdamW8bit
        Optim = AdamW8bit
    except Exception:
        Optim = AdamW

    device = torch.device("cuda:0")
    torch.manual_seed(cfg["training"]["seed"])
    random.seed(cfg["training"]["seed"])

    teacher = None
    distill_enabled = cfg.get("distill", {}).get("enabled", True)
    if distill_enabled:
        print(f"=== Loading teacher: {cfg['teacher']} (GPU, frozen, NF4) ===")
        from transformers import BitsAndBytesConfig
        bnb = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
        )
        teacher = AutoModelForCausalLM.from_pretrained(
            cfg["teacher"], quantization_config=bnb, device_map={"": 0},
        )
        teacher.eval()
        for pp in teacher.parameters():
            pp.requires_grad_(False)
    else:
        print("=== Distillation disabled: pure CE on next-token (no teacher) ===")

    tokenizer = AutoTokenizer.from_pretrained(cfg["teacher"])
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    print(f"=== Building student from: {cfg['student_init']} ===")
    student = AutoModelForCausalLM.from_pretrained(
        cfg["student_init"], torch_dtype=torch.bfloat16
    ).to(device)

    # Replace Linear -> BitLinear in matching modules
    n_swapped = replace_linear_with_bitlinear(
        student,
        include=cfg["ternarise"]["patterns"],
        exclude=cfg["ternarise"]["exclude"],
    )
    t, a = count_ternary_params(student)
    print(f"BitLinear-swapped {n_swapped} modules, ternary params {t/1e6:.1f} M / {a/1e6:.1f} M total")

    # Freeze everything except BitLinear weights — keeps embeddings, lm_head,
    # norms, etc fixed, halves optimizer state, prevents backprop through the
    # huge per_layer_token_embd. Only ~1.85 B params get gradient.
    from bitnetize import BitLinear
    bitlinear_param_ids = set()
    for m in student.modules():
        if isinstance(m, BitLinear):
            bitlinear_param_ids.add(id(m.weight))
            if m.bias is not None:
                bitlinear_param_ids.add(id(m.bias))
    n_train = 0
    for p in student.parameters():
        if id(p) in bitlinear_param_ids:
            p.requires_grad_(True)
            n_train += p.numel()
        else:
            p.requires_grad_(False)
    print(f"Trainable params: {n_train/1e6:.1f} M ({100*n_train/a:.1f}% of model)")

    student.gradient_checkpointing_enable()

    # ------------------------------------------------------------
    # Data
    # ------------------------------------------------------------
    print("=== Building dataset ===")
    ds = build_dataset(cfg, tokenizer)
    bs = cfg["training"]["per_device_train_batch_size"]
    accum = cfg["training"]["gradient_accumulation_steps"]

    def _collate(batch):
        # Strip any non-list/dict junk; force input_ids/attention_mask/labels
        # to int64 tensors of shape [bs, max_seq].
        keys = ("input_ids", "attention_mask", "labels")
        out = {}
        for k in keys:
            seqs = [ex[k] for ex in batch if isinstance(ex.get(k), (list, tuple))]
            if not seqs:
                return None
            out[k] = torch.tensor(seqs, dtype=torch.long)
        return out

    dl = DataLoader(ds, batch_size=bs, num_workers=0, pin_memory=True,
                    collate_fn=_collate)

    # ------------------------------------------------------------
    # Optimizer + scheduler
    # ------------------------------------------------------------
    max_steps = int(cfg["training"]["max_steps"])
    lr = float(cfg["training"]["learning_rate"])
    opt = Optim((p for p in student.parameters() if p.requires_grad),
                lr=lr, betas=(0.9, 0.95), weight_decay=cfg["training"]["weight_decay"])
    sched = get_cosine_schedule_with_warmup(
        opt, cfg["training"]["warmup_steps"], max_steps
    )

    out_dir = Path(cfg["training"]["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------
    # Training loop
    # ------------------------------------------------------------
    alpha_kl = cfg["distill"]["alpha_kl"]
    alpha_ce = cfg["distill"]["alpha_ce"]
    T = cfg["distill"]["temperature"]

    student.train()
    step = 0
    accum_loss = 0.0
    accum_kl = 0.0
    accum_ce = 0.0
    print(f"=== Training for {max_steps} steps (bs {bs} x accum {accum}) ===")

    micro = 0
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
            kl = F.kl_div(
                F.log_softmax(s_logits / T, dim=-1),
                F.softmax(t_logits / T, dim=-1),
                reduction="batchmean",
            ) * (T * T)
        else:
            s_logits = student(input_ids=ids).logits
            kl = torch.zeros((), device=device)

        # Cross-entropy on the ground-truth tokens (shift)
        shift_logits = s_logits[..., :-1, :].contiguous()
        shift_labels = lbl[..., 1:].contiguous()
        ce = F.cross_entropy(
            shift_logits.view(-1, shift_logits.size(-1)),
            shift_labels.view(-1),
            ignore_index=tokenizer.pad_token_id,
        )

        loss = (alpha_kl * kl + alpha_ce * ce) / accum
        loss.backward()

        accum_loss += loss.item() * accum
        accum_kl += kl.item()
        accum_ce += ce.item()
        micro += 1

        if micro % accum == 0:
            torch.nn.utils.clip_grad_norm_(student.parameters(), 1.0)
            opt.step()
            sched.step()
            opt.zero_grad(set_to_none=True)
            step += 1

            if step % cfg["training"]["logging_steps"] == 0:
                lr_now = sched.get_last_lr()[0]
                print(f"step {step:>6} | loss {accum_loss/accum:.4f} "
                      f"| kl {accum_kl/accum:.4f} | ce {accum_ce/accum:.4f} "
                      f"| lr {lr_now:.2e}")
                accum_loss = accum_kl = accum_ce = 0.0

            if step % cfg["training"]["save_steps"] == 0:
                ck = out_dir / f"step-{step}"
                ck.mkdir(exist_ok=True)
                student.save_pretrained(ck, safe_serialization=True)
                tokenizer.save_pretrained(ck)
                print(f"  saved checkpoint -> {ck}")

    # Final save
    student.save_pretrained(out_dir / "final", safe_serialization=True)
    tokenizer.save_pretrained(out_dir / "final")
    print(f"=== done. final -> {out_dir / 'final'} ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
