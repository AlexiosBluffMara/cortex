"""train_ternary_gemma_mlx.py
QAT ternary fine-tune of Gemma 4 E2B on Apple Silicon (M4 Max, 48 GB unified)
using Apple's MLX framework.

Why MLX here: the 5090's 32 GB GDDR7 doesn't hold a 5.5 B Gemma + bf16 student
shadow + optimizer state for QAT. The M4 Max's 48 GB unified memory does.

Pipeline:
  1. Load Gemma 4 E2B with mlx_lm.load() (teacher copy, frozen)
  2. Load a second copy as student
  3. In the student: replace mlx.nn.Linear in attn + ffn projections with
     BitLinear (absmean ternary weights + per-token int8 activations, STE).
  4. Freeze all non-BitLinear params (embeddings + lm_head + per_layer_*)
  5. Distill: alpha_kl * KL(student || teacher) + alpha_ce * CE(targets)
  6. Save checkpoint; export to HF format; run bitnet.cpp convert helper.

Run on Seratonin:
  scp this file + ternary-gemma-e2b-mlx.yaml from Seratonin
  ~/cortex-mlx/bin/python train_ternary_gemma_mlx.py --config <yaml>
"""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Iterator

import mlx.core as mx
import mlx.nn as nn
import mlx.optimizers as optim
import yaml
from mlx_lm import load


# ---------------------------------------------------------------------------
# BitLinear (MLX)
# ---------------------------------------------------------------------------
def _abs_mean(t: mx.array, eps: float = 1e-5) -> mx.array:
    return mx.maximum(mx.mean(mx.abs(t)), mx.array(eps))


def _round_ste(x: mx.array) -> mx.array:
    """Straight-through estimator for round() — gradient passes through."""
    return x + mx.stop_gradient(mx.round(x) - x)


def _quantise_weight_ternary(w: mx.array) -> tuple[mx.array, mx.array]:
    scale = _abs_mean(w)
    w_q = mx.clip(_round_ste(w / scale), -1.0, 1.0)
    return w_q, scale


def _quantise_activation_int8(x: mx.array) -> tuple[mx.array, mx.array]:
    # Per-token absmax → int8 symmetric. Last dim is features.
    amax = mx.max(mx.abs(x), axis=-1, keepdims=True)
    scale = mx.maximum(amax, mx.array(1e-5)) / 127.0
    x_q = mx.clip(_round_ste(x / scale), -128.0, 127.0)
    return x_q, scale


class BitLinear(nn.Module):
    """Drop-in replacement for nn.Linear with BitNet b1.58 quantisation.
    Pre-RMSNorm before quant.
    """

    def __init__(self, in_features: int, out_features: int, bias: bool = True):
        super().__init__()
        self.in_features = in_features
        self.out_features = out_features
        # weight stored in fp16 during training (STE); snapped to ternary in forward
        self.weight = mx.random.uniform(low=-0.01, high=0.01,
                                        shape=(out_features, in_features),
                                        dtype=mx.bfloat16)
        if bias:
            self.bias = mx.zeros(out_features, dtype=mx.bfloat16)
        else:
            self.bias = None
        # Pre-norm — small RMSNorm
        self.norm = nn.RMSNorm(in_features, eps=1e-6)

    def __call__(self, x: mx.array) -> mx.array:
        x = self.norm(x)
        w_q, w_scale = _quantise_weight_ternary(self.weight)
        x_q, a_scale = _quantise_activation_int8(x)
        y = (x_q @ w_q.T) * a_scale * w_scale
        if self.bias is not None:
            y = y + self.bias
        return y

    @classmethod
    def from_linear(cls, linear: nn.Linear) -> "BitLinear":
        out_features, in_features = linear.weight.shape
        bl = cls(in_features, out_features, bias=("bias" in linear))
        bl.weight = linear.weight
        if "bias" in linear:
            bl.bias = linear.bias
        return bl


# ---------------------------------------------------------------------------
# Module surgery
# ---------------------------------------------------------------------------
TARGET_NAMES = ("q_proj", "k_proj", "v_proj", "o_proj",
                "gate_proj", "up_proj", "down_proj")


def replace_linears(model: nn.Module, target_names: tuple[str, ...] = TARGET_NAMES) -> int:
    """In-place: swap nn.Linear children whose attribute name matches target_names
    with BitLinear. Returns count.
    """
    n = 0
    for module in _iter_modules(model):
        for attr_name in list(vars(module).get("_modules", {}).keys()) + list(vars(module).keys()):
            if attr_name not in target_names:
                continue
            child = getattr(module, attr_name, None)
            if isinstance(child, nn.Linear):
                setattr(module, attr_name, BitLinear.from_linear(child))
                n += 1
    return n


def _iter_modules(m) -> Iterator:
    yield m
    if hasattr(m, "children"):
        for c in m.children().values():
            if isinstance(c, list):
                for x in c:
                    yield from _iter_modules(x)
            else:
                yield from _iter_modules(c)


# ---------------------------------------------------------------------------
# Dataset (very simple streaming text)
# ---------------------------------------------------------------------------
def text_iter(sources: list[dict], tokenizer, seq_len: int) -> Iterator[mx.array]:
    """Stream tokenized fixed-length sequences from a mix of HF datasets."""
    from datasets import load_dataset, interleave_datasets

    streams, weights = [], []
    for src in sources:
        try:
            ds_kwargs = {}
            if "subset" in src:
                ds_kwargs["name"] = src["subset"]
            ds = load_dataset(src["name"], split="train", streaming=True, **ds_kwargs)
        except Exception as exc:
            print(f"WARN: skipping {src['name']}: {exc}")
            continue
        if "max_examples" in src:
            ds = ds.take(src["max_examples"])
        streams.append(ds)
        weights.append(src.get("weight", 1.0))

    if not streams:
        raise RuntimeError("no datasets loaded")

    s = sum(weights)
    weights = [w / s for w in weights]
    mixed = interleave_datasets(streams, probabilities=weights, seed=42,
                                stopping_strategy="all_exhausted")

    buf: list[int] = []
    for ex in mixed:
        text = ""
        for k in ("text", "content", "instruction", "prompt"):
            v = ex.get(k)
            if isinstance(v, str) and v:
                text = v
                break
        if not text:
            for v in ex.values():
                if isinstance(v, str) and v:
                    text = v
                    break
        if not text:
            continue
        ids = tokenizer.encode(text)
        buf.extend(ids)
        while len(buf) >= seq_len + 1:
            chunk = buf[: seq_len + 1]
            buf = buf[seq_len:]
            yield mx.array(chunk[:-1], dtype=mx.int32), mx.array(chunk[1:], dtype=mx.int32)


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------
def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--max-steps", type=int, default=None)
    args = p.parse_args()

    cfg = yaml.safe_load(open(args.config, "r", encoding="utf-8"))
    if args.max_steps is not None:
        cfg["training"]["max_steps"] = args.max_steps
    out_dir = Path(cfg["training"]["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"=== Loading teacher: {cfg['teacher']} ===")
    teacher, teacher_tok = load(cfg["teacher"])
    teacher.freeze()
    teacher.eval()

    print(f"=== Loading student: {cfg['student_init']} ===")
    student, _ = load(cfg["student_init"])
    n = replace_linears(student)
    print(f"BitLinear-swapped {n} modules in student")

    # Freeze all non-BitLinear params
    student.freeze()
    bitlinear_params = []
    for module in _iter_modules(student):
        if isinstance(module, BitLinear):
            for k in ("weight", "bias", "norm"):
                if hasattr(module, k):
                    pass
            module.unfreeze()
            for p in module.trainable_parameters().values():
                bitlinear_params.append(p)
    n_train = sum(p.size for p in bitlinear_params)
    print(f"Trainable BitLinear params: {n_train/1e6:.1f} M")

    seq = cfg["dataset"]["max_seq_length"]
    bs = cfg["training"]["per_device_train_batch_size"]
    accum = cfg["training"]["gradient_accumulation_steps"]
    max_steps = int(cfg["training"]["max_steps"])
    lr = float(cfg["training"]["learning_rate"])
    warmup = cfg["training"]["warmup_steps"]
    alpha_kl = cfg["distill"]["alpha_kl"]
    alpha_ce = cfg["distill"]["alpha_ce"]
    T = cfg["distill"]["temperature"]

    opt = optim.AdamW(learning_rate=lr, weight_decay=cfg["training"]["weight_decay"])

    def loss_fn(student, ids, targets):
        s_logits = student(ids).astype(mx.float32)
        if alpha_kl > 0:
            t_logits = mx.stop_gradient(teacher(ids).astype(mx.float32))
            log_p_s = mx.log(mx.softmax(s_logits / T, axis=-1) + 1e-9)
            p_t = mx.softmax(t_logits / T, axis=-1)
            kl = -mx.sum(p_t * log_p_s, axis=-1).mean() * (T * T)
        else:
            kl = mx.array(0.0)
        ce = nn.losses.cross_entropy(s_logits, targets, reduction="mean")
        return alpha_kl * kl + alpha_ce * ce, (kl, ce)

    state = [student.state, opt.state]

    @mx.compile(inputs=state, outputs=state)
    def step_fn(ids, targets):
        (loss, aux), grads = nn.value_and_grad(student, loss_fn)(student, ids, targets)
        opt.update(student, grads)
        return loss, aux

    # Build dataset iterator
    print("=== streaming dataset ===")
    it = text_iter(cfg["dataset"]["sources"], teacher_tok, seq)

    print(f"=== training {max_steps} steps (bs {bs}, accum {accum}, seq {seq}) ===")
    step = 0
    t0 = time.time()
    while step < max_steps:
        try:
            ids, tgt = next(it)
        except StopIteration:
            break
        ids = ids.reshape(1, -1)
        tgt = tgt.reshape(1, -1)
        loss, (kl, ce) = step_fn(ids, tgt)
        mx.eval(state)
        step += 1
        if step % cfg["training"]["logging_steps"] == 0:
            dt = time.time() - t0
            print(f"step {step:>6}  loss {float(loss):.4f}  kl {float(kl):.3f}  ce {float(ce):.3f}  "
                  f"({step/dt:.2f} step/s, {dt/60:.1f} min elapsed)")
        if step % cfg["training"]["save_steps"] == 0:
            ck = out_dir / f"step-{step}"
            ck.mkdir(exist_ok=True, parents=True)
            student.save_weights(str(ck / "weights.safetensors"))
            print(f"  saved {ck}")

    final = out_dir / "final"
    final.mkdir(exist_ok=True, parents=True)
    student.save_weights(str(final / "weights.safetensors"))
    teacher_tok.save_pretrained(str(final))
    print(f"=== done. final -> {final} ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
