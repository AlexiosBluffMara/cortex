"""Fine-tune Gemma 4 E4B with Unsloth → `cortex-gemma-4-e4b` (sprint Day 11).

Default hyperparameters come from Daniel Han-Chen's verified Gemma 4 31B
notebook (`Gemma4_(31B)-Vision.ipynb`), with E4B-appropriate deltas. See
`docs/unsloth.md` for the per-parameter rationale and source pinning.

Usage::

    # Dry run — print the resolved config and exit, no GPU work
    python -m scripts.train_cortex --dry-run

    # Real run on local 5090 against our synthetic dataset
    python -m scripts.train_cortex \\
        --dataset data/cortex_train.jsonl \\
        --output-dir outputs/cortex-training \\
        --epochs 3

    # Full pipeline: train → merge → GGUF → Modelfile
    python -m scripts.train_cortex \\
        --dataset data/cortex_train.jsonl \\
        --output-dir outputs/cortex-training \\
        --epochs 3 --merge --gguf q4_k_m --modelfile

Hard invariants (enforced below):
  - Gemma 4 only — `--base-model` must contain "gemma-4" (no Gemma 3 fallback).
  - The fine-tuned tag is `RedTeamKitchen/cortex-gemma-4-e4b`. Per Google's
    Gemma naming guidelines, "Gemma" appears only in the path, not in the
    product name.
  - The Modelfile system prompt cites `"Gemma is a trademark of Google LLC."`
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Verified config (defaults — see docs/unsloth.md for sourcing)
# ---------------------------------------------------------------------------

DEFAULT_BASE_MODEL = "unsloth/gemma-4-E4B-it"
DEFAULT_FINETUNED_REPO = "RedTeamKitchen/cortex-gemma-4-e4b"
DEFAULT_DATASET_PATH = Path("data/cortex_train.jsonl")
DEFAULT_OUTPUT_DIR = Path("outputs/cortex-training")
DEFAULT_CHAT_TEMPLATE = "gemma-4-thinking"

# Daniel Han-Chen's 31B values, verified from Gemma4_(31B)-Vision.ipynb on
# raw.githubusercontent.com/unslothai/notebooks/main/. Adjustments for E4B
# noted inline.
DEFAULT_LORA_R = 32                 # Daniel: 32 (vs our prior plan of 16)
DEFAULT_LORA_ALPHA = 32             # Daniel: 32 (alpha == r recommended)
DEFAULT_LORA_DROPOUT = 0.0          # Daniel: 0
DEFAULT_TARGET_MODULES = "all-linear"
DEFAULT_BIAS = "none"
DEFAULT_RANDOM_STATE = 3407         # Unsloth signature seed
DEFAULT_USE_RSLORA = False
DEFAULT_LOFTQ_CONFIG: Any = None
DEFAULT_USE_GRADIENT_CHECKPOINTING = "unsloth"  # long-context aware

# Training — E4B can take a larger per-device batch than 31B
DEFAULT_PER_DEVICE_TRAIN_BATCH_SIZE = 4   # Daniel's 31B used 1 (A100-40GB)
DEFAULT_GRADIENT_ACCUMULATION_STEPS = 4   # effective batch 16
DEFAULT_MAX_GRAD_NORM = 0.3               # Daniel: 0.3 — load-bearing
DEFAULT_WARMUP_RATIO = 0.03
DEFAULT_NUM_TRAIN_EPOCHS = 3
DEFAULT_LEARNING_RATE = 2e-4
DEFAULT_WEIGHT_DECAY = 0.001              # Daniel: 0.001
DEFAULT_LR_SCHEDULER_TYPE = "cosine"
DEFAULT_OPTIM = "adamw_8bit"
DEFAULT_LOGGING_STEPS = 10
DEFAULT_SAVE_STRATEGY = "epoch"
DEFAULT_REPORT_TO = "none"

DEFAULT_MAX_SEQ_LENGTH = 8192             # E4B; Daniel's 31B vision used 2048
DEFAULT_LOAD_IN_4BIT = True               # QLoRA — 4-bit base, BF16 LoRA adapters

# Ollama Modelfile parameters
DEFAULT_TEMPERATURE = 0.4
DEFAULT_NUM_CTX = 8192
DEFAULT_TOP_P = 0.9
DEFAULT_NUM_PREDICT = 512


# ---------------------------------------------------------------------------
# Cortex system prompt embedded into the Modelfile
# ---------------------------------------------------------------------------

CORTEX_SYSTEM_PROMPT = (
    "You are Cortex, an AI neuroscience assistant created by Alexios Bluff Mara LLC. "
    "You specialize in interpreting TRIBE v2 brain activation data, generating "
    "Three.js visualization code, and assisting with agentic tool-based workflows. "
    "Always be scientifically accurate; never invent BOLD numbers the model did not "
    "produce; never offer medical diagnoses; treat TRIBE v2 outputs as "
    "population-averaged predictions, not personal scans.\n\n"
    "Gemma is a trademark of Google LLC. Cortex is not endorsed by Google."
)


@dataclass
class TrainConfig:
    """Fine-tune configuration. Defaults pinned to docs/unsloth.md.

    Most callers will only override `dataset_path`, `output_dir`, and
    `num_train_epochs`. The LoRA / training hyperparameters are intentionally
    locked to the verified Daniel Han-Chen values; override only with reason.
    """

    base_model: str = DEFAULT_BASE_MODEL
    finetuned_repo: str = DEFAULT_FINETUNED_REPO
    dataset_path: Path = DEFAULT_DATASET_PATH
    output_dir: Path = DEFAULT_OUTPUT_DIR
    chat_template: str = DEFAULT_CHAT_TEMPLATE

    # LoRA
    lora_r: int = DEFAULT_LORA_R
    lora_alpha: int = DEFAULT_LORA_ALPHA
    lora_dropout: float = DEFAULT_LORA_DROPOUT
    target_modules: str = DEFAULT_TARGET_MODULES
    bias: str = DEFAULT_BIAS
    random_state: int = DEFAULT_RANDOM_STATE
    use_rslora: bool = DEFAULT_USE_RSLORA
    use_gradient_checkpointing: str = DEFAULT_USE_GRADIENT_CHECKPOINTING

    # Training
    per_device_train_batch_size: int = DEFAULT_PER_DEVICE_TRAIN_BATCH_SIZE
    gradient_accumulation_steps: int = DEFAULT_GRADIENT_ACCUMULATION_STEPS
    max_grad_norm: float = DEFAULT_MAX_GRAD_NORM
    warmup_ratio: float = DEFAULT_WARMUP_RATIO
    num_train_epochs: int = DEFAULT_NUM_TRAIN_EPOCHS
    learning_rate: float = DEFAULT_LEARNING_RATE
    weight_decay: float = DEFAULT_WEIGHT_DECAY
    lr_scheduler_type: str = DEFAULT_LR_SCHEDULER_TYPE
    optim: str = DEFAULT_OPTIM
    logging_steps: int = DEFAULT_LOGGING_STEPS
    save_strategy: str = DEFAULT_SAVE_STRATEGY
    report_to: str = DEFAULT_REPORT_TO
    max_seq_length: int = DEFAULT_MAX_SEQ_LENGTH
    load_in_4bit: bool = DEFAULT_LOAD_IN_4BIT

    # Pipeline switches
    dry_run: bool = False
    merge_to_bf16: bool = False
    gguf_quantization: str | None = None      # "q4_k_m", "q5_k_m", "bf16", or None
    write_modelfile: bool = False
    push_to_hub: bool = False

    def __post_init__(self) -> None:
        # Hard invariant: Gemma 4 only.
        if "gemma-4" not in self.base_model.lower() and "gemma_4" not in self.base_model.lower():
            raise ValueError(
                f"base_model must be a Gemma 4 variant; got {self.base_model!r}. "
                "Cortex CLAUDE.md forbids Gemma 3."
            )
        # Hard invariant: alpha >= r is the Unsloth recommendation.
        if self.lora_alpha < self.lora_r:
            raise ValueError(
                f"lora_alpha ({self.lora_alpha}) should be >= lora_r ({self.lora_r}); "
                "see Unsloth Gemma 4 docs."
            )

    def as_summary_dict(self) -> dict[str, Any]:
        """JSON-serializable view of the resolved config (paths → strings)."""
        d = asdict(self)
        d["dataset_path"] = str(self.dataset_path)
        d["output_dir"] = str(self.output_dir)
        return d


# ---------------------------------------------------------------------------
# Dataset loading + chat-template formatting
# ---------------------------------------------------------------------------

def load_sharegpt_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load a ShareGPT-format JSONL file produced by generate_neuro_dataset.py.

    Each line is `{"id", "conversations": [{from, value}...], "metadata": {...}}`.
    Returns the parsed list. Raises if the file is empty or malformed.
    """
    if not path.exists():
        raise FileNotFoundError(f"Dataset not found: {path}")

    examples: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line_num, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_num} malformed JSON: {exc}") from exc
            if "conversations" not in obj or not obj["conversations"]:
                raise ValueError(f"{path}:{line_num} missing 'conversations'")
            examples.append(obj)

    if not examples:
        raise ValueError(f"{path}: no examples found")
    return examples


def to_gemma_chat_format(example: dict[str, Any]) -> list[dict[str, str]]:
    """Convert a ShareGPT-format example to Gemma chat messages.

    Gemma's chat template expects messages with `role` and `content`. The
    `unsloth.get_chat_template("gemma-4-thinking")` helper handles the
    `assistant` → `model` role mapping internally; we keep `assistant` here
    so the format is portable.
    """
    role_map = {"system": "system", "user": "user", "assistant": "assistant", "model": "assistant"}
    out: list[dict[str, str]] = []
    for turn in example["conversations"]:
        role = role_map.get(turn["from"], turn["from"])
        out.append({"role": role, "content": turn["value"]})
    return out


# ---------------------------------------------------------------------------
# Ollama Modelfile generation
# ---------------------------------------------------------------------------

def render_modelfile(
    *,
    gguf_filename: str,
    system_prompt: str = CORTEX_SYSTEM_PROMPT,
    temperature: float = DEFAULT_TEMPERATURE,
    num_ctx: int = DEFAULT_NUM_CTX,
    top_p: float = DEFAULT_TOP_P,
    num_predict: int = DEFAULT_NUM_PREDICT,
) -> str:
    """Render an Ollama Modelfile for the cortex-gemma-4-e4b GGUF artifact.

    The resulting file imports the GGUF, sets sampling defaults that match the
    cortex/ollama_client.py production defaults, and bakes the Cortex persona
    into the system prompt.
    """
    # Triple-quoted SYSTEM block in Ollama Modelfiles requires no escaping —
    # but newlines must be embedded directly.
    return (
        f"FROM ./{gguf_filename}\n"
        f"\n"
        f"PARAMETER temperature {temperature}\n"
        f"PARAMETER num_ctx {num_ctx}\n"
        f"PARAMETER top_p {top_p}\n"
        f"PARAMETER num_predict {num_predict}\n"
        f"\n"
        f'SYSTEM """{system_prompt}"""\n'
    )


# ---------------------------------------------------------------------------
# Top-level pipeline
# ---------------------------------------------------------------------------

def run(config: TrainConfig) -> dict[str, Any]:
    """Execute the configured pipeline.

    Returns a result dict so callers (and tests) can introspect what happened.
    On --dry-run the dict carries `{"dry_run": True, "config": {...},
    "n_examples": int}` and no model code is touched.
    """
    config.output_dir.mkdir(parents=True, exist_ok=True)

    # Always load the dataset — that's cheap, a sanity check, and useful
    # context to print.
    examples = load_sharegpt_jsonl(config.dataset_path)
    summary = {
        "dry_run": config.dry_run,
        "config": config.as_summary_dict(),
        "n_examples": len(examples),
        "dataset_path": str(config.dataset_path),
        "output_dir": str(config.output_dir),
    }

    if config.dry_run:
        print(json.dumps(summary, indent=2), file=sys.stderr)
        return summary

    # --- Real training (heavy imports deferred so --dry-run stays light) ---
    from unsloth import FastModel  # noqa: I001 — lazy import
    from unsloth.chat_templates import get_chat_template
    from datasets import Dataset
    from trl import SFTConfig, SFTTrainer
    import torch

    # 1. Load base model in 4-bit (QLoRA) — Daniel Han-Chen's setup
    model, tokenizer = FastModel.from_pretrained(
        model_name=config.base_model,
        max_seq_length=config.max_seq_length,
        dtype=torch.bfloat16,
        load_in_4bit=config.load_in_4bit,
    )
    tokenizer = get_chat_template(tokenizer, chat_template=config.chat_template)

    # 2. Wrap with LoRA adapters
    model = FastModel.get_peft_model(
        model,
        r=config.lora_r,
        lora_alpha=config.lora_alpha,
        lora_dropout=config.lora_dropout,
        target_modules=config.target_modules,
        bias=config.bias,
        use_gradient_checkpointing=config.use_gradient_checkpointing,
        random_state=config.random_state,
        use_rslora=config.use_rslora,
    )

    # 3. Build the dataset by mapping each ShareGPT example through the chat
    # template
    def _format(ex: dict[str, Any]) -> dict[str, str]:
        messages = to_gemma_chat_format(ex)
        text = tokenizer.apply_chat_template(messages, tokenize=False)
        return {"text": text}

    dataset = Dataset.from_list(examples).map(_format)

    # 4. Train via TRL
    sft_config = SFTConfig(
        per_device_train_batch_size=config.per_device_train_batch_size,
        gradient_accumulation_steps=config.gradient_accumulation_steps,
        max_grad_norm=config.max_grad_norm,
        warmup_ratio=config.warmup_ratio,
        num_train_epochs=config.num_train_epochs,
        learning_rate=config.learning_rate,
        weight_decay=config.weight_decay,
        lr_scheduler_type=config.lr_scheduler_type,
        optim=config.optim,
        logging_steps=config.logging_steps,
        save_strategy=config.save_strategy,
        report_to=config.report_to,
        seed=config.random_state,
        bf16=True,
        output_dir=str(config.output_dir),
        dataset_text_field="text",
        max_seq_length=config.max_seq_length,
    )
    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        args=sft_config,
    )
    train_result = trainer.train()
    summary["train_loss"] = float(train_result.training_loss)

    # 5. Save LoRA adapters
    adapter_dir = config.output_dir / "lora"
    model.save_pretrained(str(adapter_dir))
    tokenizer.save_pretrained(str(adapter_dir))
    summary["adapter_dir"] = str(adapter_dir)

    # 6. Optional: merge to BF16 (vLLM / direct HF inference)
    if config.merge_to_bf16:
        merged_dir = config.output_dir / "merged_bf16"
        model.save_pretrained_merged(str(merged_dir), tokenizer)
        summary["merged_dir"] = str(merged_dir)

    # 7. Optional: GGUF export (Ollama-ready)
    if config.gguf_quantization:
        gguf_dir = config.output_dir / "gguf"
        model.save_pretrained_gguf(
            str(gguf_dir), tokenizer, quantization_method=config.gguf_quantization
        )
        summary["gguf_dir"] = str(gguf_dir)

        # 8. Optional: write Modelfile next to the GGUF
        if config.write_modelfile:
            gguf_file = next(gguf_dir.glob("*.gguf"), None)
            if gguf_file is not None:
                modelfile = render_modelfile(gguf_filename=gguf_file.name)
                (gguf_dir / "Modelfile").write_text(modelfile, encoding="utf-8")
                summary["modelfile"] = str(gguf_dir / "Modelfile")

    # 9. Optional: push to HF Hub
    if config.push_to_hub:
        model.push_to_hub(config.finetuned_repo)
        summary["pushed_to"] = config.finetuned_repo

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Fine-tune Gemma 4 E4B with Unsloth → cortex-gemma-4-e4b.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--base-model", default=DEFAULT_BASE_MODEL)
    p.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH)
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)

    # LoRA — defaults locked; override only with reason
    p.add_argument("--lora-r", type=int, default=DEFAULT_LORA_R)
    p.add_argument("--lora-alpha", type=int, default=DEFAULT_LORA_ALPHA)
    p.add_argument("--lora-dropout", type=float, default=DEFAULT_LORA_DROPOUT)

    # Training
    p.add_argument("--epochs", dest="num_train_epochs", type=int, default=DEFAULT_NUM_TRAIN_EPOCHS)
    p.add_argument(
        "--batch-size",
        dest="per_device_train_batch_size",
        type=int,
        default=DEFAULT_PER_DEVICE_TRAIN_BATCH_SIZE,
    )
    p.add_argument("--lr", dest="learning_rate", type=float, default=DEFAULT_LEARNING_RATE)
    p.add_argument("--max-seq-length", type=int, default=DEFAULT_MAX_SEQ_LENGTH)

    # Pipeline switches
    p.add_argument("--dry-run", action="store_true", help="Print resolved config and exit")
    p.add_argument("--merge", dest="merge_to_bf16", action="store_true")
    p.add_argument(
        "--gguf",
        dest="gguf_quantization",
        choices=["q4_k_m", "q5_k_m", "q8_0", "bf16"],
        default=None,
        help="Export GGUF after training (recommended: q4_k_m for Ollama)",
    )
    p.add_argument("--modelfile", dest="write_modelfile", action="store_true")
    p.add_argument("--push", dest="push_to_hub", action="store_true")

    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    config = TrainConfig(
        base_model=args.base_model,
        dataset_path=args.dataset,
        output_dir=args.output_dir,
        lora_r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        num_train_epochs=args.num_train_epochs,
        per_device_train_batch_size=args.per_device_train_batch_size,
        learning_rate=args.learning_rate,
        max_seq_length=args.max_seq_length,
        dry_run=args.dry_run,
        merge_to_bf16=args.merge_to_bf16,
        gguf_quantization=args.gguf_quantization,
        write_modelfile=args.write_modelfile,
        push_to_hub=args.push_to_hub,
    )
    run(config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
