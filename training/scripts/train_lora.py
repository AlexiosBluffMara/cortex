"""train_lora.py — Unsloth LoRA fine-tune driver for Mercury → Gemma 4.

Reads a YAML config (see configs/mercury-gemma4-e4b-lora.yaml). Loads a
ShareGPT-format JSONL dataset, applies the Gemma 4 chat template, runs the
trainer on a single GPU. Designed for an RTX 5090 (Blackwell sm_120).
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

import yaml


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", required=True, help="YAML training config")
    p.add_argument("--dataset", help="Override dataset path from config")
    p.add_argument("--output-suffix", default=None,
                   help="Append a suffix to output dir (default: YYYYMMDD-HHMM)")
    args = p.parse_args()

    cfg = load_config(args.config)
    suffix = args.output_suffix or datetime.now().strftime("%Y%m%d-%H%M")
    out_dir = f"{cfg['training']['output_dir']}-{suffix}"

    # Imports are lazy: unsloth wants to be the FIRST thing torch sees so it can
    # patch attention etc. Keep these imports after argparse so --help works on
    # a machine without GPU.
    from unsloth import FastLanguageModel, is_bfloat16_supported  # noqa: E402
    from unsloth.chat_templates import get_chat_template  # noqa: E402
    from datasets import load_dataset  # noqa: E402
    from trl import SFTTrainer  # noqa: E402
    from transformers import TrainingArguments  # noqa: E402

    print(f"=== loading base: {cfg['base_model']} ===")
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=cfg["base_model"],
        max_seq_length=cfg["max_seq_length"],
        dtype=None,
        load_in_4bit=cfg.get("load_in_4bit", True),
    )

    tokenizer = get_chat_template(tokenizer, chat_template=cfg["dataset"]["chat_template"])

    lora_cfg = cfg["lora"]
    model = FastLanguageModel.get_peft_model(
        model,
        r=lora_cfg["r"],
        lora_alpha=lora_cfg["alpha"],
        lora_dropout=lora_cfg["dropout"],
        bias=lora_cfg["bias"],
        target_modules=lora_cfg["target_modules"],
        use_gradient_checkpointing=lora_cfg["use_gradient_checkpointing"],
        random_state=lora_cfg["random_state"],
    )

    ds_path = args.dataset or cfg["dataset"]["path"]
    print(f"=== loading dataset: {ds_path} ===")
    raw = load_dataset("json", data_files=ds_path, split="train")

    def to_text(example):
        msgs = []
        for turn in example["conversations"]:
            role_map = {"human": "user", "user": "user",
                        "gpt": "assistant", "assistant": "assistant",
                        "system": "system", "tool": "tool"}
            role = role_map.get(turn.get("from", turn.get("role", "")), "user")
            msgs.append({"role": role, "content": turn.get("value", turn.get("content", ""))})
        text = tokenizer.apply_chat_template(msgs, tokenize=False,
                                             add_generation_prompt=False)
        return {"text": text}

    raw = raw.map(to_text, remove_columns=raw.column_names)
    print(f"  examples: {len(raw)}")

    tr = cfg["training"]
    bf16 = tr.get("bf16", True) and is_bfloat16_supported()
    args_t = TrainingArguments(
        per_device_train_batch_size=tr["per_device_train_batch_size"],
        gradient_accumulation_steps=tr["gradient_accumulation_steps"],
        warmup_steps=tr["warmup_steps"],
        num_train_epochs=tr["num_train_epochs"],
        learning_rate=tr["learning_rate"],
        fp16=tr.get("fp16", False) and not bf16,
        bf16=bf16,
        logging_steps=tr["logging_steps"],
        optim=tr["optim"],
        weight_decay=tr["weight_decay"],
        lr_scheduler_type=tr["lr_scheduler_type"],
        seed=tr["seed"],
        save_strategy=tr["save_strategy"],
        report_to=tr["report_to"],
        output_dir=out_dir,
    )

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=raw,
        dataset_text_field="text",
        max_seq_length=cfg["max_seq_length"],
        args=args_t,
        packing=False,
    )

    print(f"=== training -> {out_dir} ===")
    trainer.train()

    print(f"=== saving adapter to {out_dir} ===")
    model.save_pretrained(out_dir)
    tokenizer.save_pretrained(out_dir)

    # Update LATEST symlink for downstream export scripts
    latest = Path(cfg["training"]["output_dir"] + "-LATEST")
    try:
        if latest.exists() or latest.is_symlink():
            latest.unlink()
        os.symlink(Path(out_dir).name, latest)
        print(f"=== LATEST -> {latest} -> {Path(out_dir).name} ===")
    except OSError as exc:
        print(f"WARN: could not write LATEST symlink: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
