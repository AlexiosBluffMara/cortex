"""Tiny diagnostic: load Gemma 4 E4B QLoRA + wrap + one forward/backward.

Prints whether LoRA adapter gradients are non-zero. Designed to run in
~60 seconds so we can iterate fast on the gradient-flow bug rather than
burning 30+ minutes per attempt with the full SFTTrainer pipeline.

Usage:
    python -m scripts.diagnose_lora_grad
    python -m scripts.diagnose_lora_grad --enable-input-grads
"""
from __future__ import annotations

import argparse
import os
import sys

# IMPORTANT: set this BEFORE the unsloth import or unsloth's compiled
# kernels return empty logits, which would mask the actual issue.
os.environ.setdefault("UNSLOTH_RETURN_LOGITS", "1")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--enable-input-grads",
        action="store_true",
        help="Call model.enable_input_require_grads() after from_pretrained, "
             "before get_peft_model. This is the standard QLoRA + gradient "
             "checkpointing fix.",
    )
    p.add_argument(
        "--prepare-kbit",
        action="store_true",
        help="Call peft.prepare_model_for_kbit_training before get_peft_model.",
    )
    p.add_argument("--max-seq-length", type=int, default=2048)
    args = p.parse_args()

    import torch
    from unsloth import FastLanguageModel
    from unsloth.chat_templates import get_chat_template

    print(f"[diag] torch={torch.__version__} cuda_avail={torch.cuda.is_available()}")
    print(f"[diag] enable_input_grads={args.enable_input_grads} "
          f"prepare_kbit={args.prepare_kbit}")

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name="unsloth/gemma-4-E4B-it",
        max_seq_length=args.max_seq_length,
        dtype=torch.bfloat16,
        load_in_4bit=True,
    )
    # CRITICAL: production calls get_chat_template, which returns a wrapper.
    # If we don't reproduce this here, the diagnostic doesn't reflect reality.
    tokenizer = get_chat_template(tokenizer, chat_template="gemma-4-thinking")
    print(f"[diag] tokenizer type after chat-template: {type(tokenizer).__name__}")

    if args.prepare_kbit:
        from peft import prepare_model_for_kbit_training
        model = prepare_model_for_kbit_training(
            model, use_gradient_checkpointing=True,
        )
        print("[diag] called prepare_model_for_kbit_training")

    if args.enable_input_grads:
        model.enable_input_require_grads()
        print("[diag] called model.enable_input_require_grads()")

    model = FastLanguageModel.get_peft_model(
        model,
        r=16,
        lora_alpha=16,
        lora_dropout=0.0,
        target_modules=[
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj",
        ],
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=3407,
    )

    # Sanity: count trainable
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    print(f"[diag] trainable={trainable:,} / total={total:,} "
          f"({100.0 * trainable / total:.4f}%)")
    if trainable == 0:
        print("[diag] FAIL: no trainable parameters — get_peft_model wrap broken")
        return 1

    # Single forward + backward — use a real ShareGPT-shaped chat to mirror
    # production's data path through apply_chat_template.
    underlying = getattr(tokenizer, "tokenizer", tokenizer)
    messages = [
        {"role": "system", "content": "You are Cortex, an AI neuroscience assistant."},
        {"role": "user", "content": "What does V1 do?"},
        {"role": "assistant", "content": "V1 is the primary visual cortex; it processes basic features like edges and motion."},
    ]
    chat_text = underlying.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=False,
    )
    enc = underlying(chat_text, return_tensors="pt", padding=False, truncation=True,
                     max_length=args.max_seq_length).to("cuda")
    enc["labels"] = enc["input_ids"].clone()
    print(f"[diag] sample sequence length: {enc['input_ids'].shape[1]} tokens")

    model.train()
    out = model(**enc)
    print(f"[diag] forward OK. loss={out.loss.item():.4f}")

    out.loss.backward()
    print("[diag] backward OK")

    # Inspect LoRA adapter grads
    n_lora_params = 0
    n_with_grad = 0
    n_zero_grad = 0
    nonzero_norms: list[float] = []
    sample: list[tuple[str, float]] = []
    for name, param in model.named_parameters():
        if "lora_A" not in name and "lora_B" not in name:
            continue
        if not param.requires_grad:
            continue
        n_lora_params += 1
        if param.grad is None:
            continue
        n_with_grad += 1
        gnorm = param.grad.norm().item()
        if gnorm == 0.0:
            n_zero_grad += 1
        else:
            nonzero_norms.append(gnorm)
            if len(sample) < 4:
                sample.append((name, gnorm))

    print(f"[diag] LoRA params: total={n_lora_params} with_grad={n_with_grad} "
          f"zero_grad={n_zero_grad} nonzero_grad={len(nonzero_norms)}")
    for name, g in sample:
        print(f"[diag]   sample: {name}  ||grad||={g:.6e}")

    if not nonzero_norms:
        print("[diag] FAIL: ALL LoRA gradients are zero — gradient graph severed.")
        print("[diag] Likely fix: enable input require_grads OR "
              "prepare_model_for_kbit_training before get_peft_model.")
        return 2

    mean_norm = sum(nonzero_norms) / len(nonzero_norms)
    print(f"[diag] OK (isolated): gradients flow. nonzero count={len(nonzero_norms)} "
          f"mean ||grad||={mean_norm:.6e}")

    # ---- Stage 2: now run ONE step through SFTTrainer to see if the
    # trainer pipeline preserves the gradients we just verified flow.
    # Reset gradients so we start clean.
    for param in model.parameters():
        param.grad = None

    print("[diag] --- stage 2: SFTTrainer one-step probe ---")
    from datasets import Dataset
    from transformers import DataCollatorForSeq2Seq
    from trl import SFTConfig, SFTTrainer

    rows = [{
        "input_ids": enc["input_ids"][0].tolist(),
        "attention_mask": enc["attention_mask"][0].tolist(),
        "labels": enc["input_ids"][0].tolist(),
    } for _ in range(8)]
    ds = Dataset.from_list(rows)
    collator = DataCollatorForSeq2Seq(
        tokenizer=underlying, padding=True, label_pad_token_id=-100,
        return_tensors="pt",
    )

    # Mirror train_cortex.py EXACTLY: use load_sharegpt_jsonl + Dataset.map
    # rather than building rows manually. This is the ONE remaining
    # difference between this diagnostic and production code.
    import sys as _sys
    _sys.path.insert(0, ".")
    from scripts.train_cortex import load_sharegpt_jsonl, to_gemma_chat_format
    from pathlib import Path as _Path

    real_path = _Path("data/cortex_train.jsonl")
    examples = load_sharegpt_jsonl(real_path)[:50]

    def _format(ex):
        messages = to_gemma_chat_format(ex)
        text = underlying.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=False,
        )
        encoded = underlying(
            text, truncation=True,
            max_length=args.max_seq_length, padding=False,
            return_attention_mask=True,
        )
        return {
            "input_ids": encoded["input_ids"],
            "attention_mask": encoded["attention_mask"],
            "labels": encoded["input_ids"][:],
        }

    ds = Dataset.from_list(examples).map(
        _format, remove_columns=list(examples[0].keys()),
    )
    print(f"[diag] loaded {len(ds)} examples via Dataset.map (production path)")
    print(f"[diag] columns: {ds.column_names}")

    # Match production hyperparameters exactly except num steps.
    scenarios = [
        # (label, batch_size, grad_accum, max_grad_norm, optim)
        ("smoke_b2_ga2", 2, 2, 0.3, "adamw_8bit"),  # exact smoke config
        ("prod_b1_ga4", 1, 4, 0.3, "adamw_8bit"),   # exact production config
        ("ga4_optimtorch", 1, 4, 0.3, "adamw_torch"),
    ]
    for label, bs, ga, mgn, optim_name in scenarios:
        for param in model.parameters():
            param.grad = None
        cfg = SFTConfig(
            per_device_train_batch_size=bs,
            gradient_accumulation_steps=ga,
            max_grad_norm=mgn,
            num_train_epochs=1,
            max_steps=4,
            learning_rate=2e-4,
            optim=optim_name,
            logging_steps=1,
            save_strategy="no",
            report_to="none",
            seed=3407,
            bf16=True,
            output_dir=f"/tmp/diag_{label}",
            remove_unused_columns=False,
            max_length=args.max_seq_length,
            warmup_ratio=0.03,
            weight_decay=0.001,
            lr_scheduler_type="cosine",  # match train_cortex.py exactly
        )
        # Mirror production: pass the metrics callback so we exercise the
        # same callback surface. Use a no-op stub that just collects logs.
        class _Probe(__import__("transformers").TrainerCallback):
            def __init__(self):
                self.logs = []
            def on_log(self, args, state, control, logs=None, **kwargs):
                if logs is not None:
                    self.logs.append(dict(logs))
        probe = _Probe()
        trainer = SFTTrainer(
            model=model, processing_class=tokenizer, train_dataset=ds,
            args=cfg, data_collator=collator, callbacks=[probe],
        )
        result = trainer.train()
        gnorms = [
            r.get("grad_norm") for r in trainer.state.log_history
            if "grad_norm" in r
        ]
        losses = [
            r.get("loss") for r in trainer.state.log_history
            if "loss" in r and "grad_norm" in r
        ]
        print(f"[diag] {label}: train_loss={result.training_loss:.4f} "
              f"step_losses={losses} grad_norms={gnorms}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
