"""bitnetize.py — replace nn.Linear modules with BitLinear (ternary weight,
8-bit activation) per BitNet b1.58 (Ma et al., 2024).

The math:
    weight quant:    w_q  = round(w / max(|w|.mean(), eps)).clip(-1, 1)   # ternary {-1,0,+1}
    activation quant a_q  = round(a * 127 / max(|a|.max(per-token), eps)) # per-token int8
    forward:         y    = a_q @ w_q.T  /  scale_a  *  scale_w + bias

Use the straight-through estimator for the round() so gradients flow.
"""
from __future__ import annotations

import fnmatch
import math
from typing import Iterable

import torch
import torch.nn as nn
import torch.nn.functional as F


def _abs_mean(t: torch.Tensor, eps: float = 1e-5) -> torch.Tensor:
    return t.abs().mean().clamp_min(eps)


class _RoundSTE(torch.autograd.Function):
    @staticmethod
    def forward(ctx, x):
        return x.round()

    @staticmethod
    def backward(ctx, g):
        return g


def round_ste(x: torch.Tensor) -> torch.Tensor:
    return _RoundSTE.apply(x)


def quantise_weight_ternary(w: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    """Return (w_quant_in_{-1,0,1}, scale)."""
    scale = _abs_mean(w)
    w_norm = w / scale
    w_q = round_ste(w_norm).clamp_(-1.0, 1.0)
    return w_q, scale


def quantise_activation_int8(x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    """Per-token int8 symmetric quant. Last dim of x = features."""
    scale = x.abs().amax(dim=-1, keepdim=True).clamp_min(1e-5) / 127.0
    x_q = round_ste(x / scale).clamp_(-128.0, 127.0)
    return x_q, scale


class BitLinear(nn.Module):
    """Drop-in replacement for nn.Linear with BitNet b1.58 quantisation.

    Stores weight in fp16/bf16 during training (the ternary projection is
    re-applied every forward — straight-through). At export time we cast
    each weight to int8 with values in {-1, 0, 1}.
    """

    def __init__(self, in_features: int, out_features: int,
                 bias: bool = True,
                 device=None, dtype=None) -> None:
        super().__init__()
        factory = {"device": device, "dtype": dtype}
        self.in_features = in_features
        self.out_features = out_features
        self.weight = nn.Parameter(torch.empty(out_features, in_features, **factory))
        if bias:
            self.bias = nn.Parameter(torch.empty(out_features, **factory))
        else:
            self.register_parameter("bias", None)
        # RMSNorm before quant — improves stability per BitNet paper
        self.norm = nn.RMSNorm(in_features, eps=1e-6, **factory)
        self.reset_parameters()

    def reset_parameters(self) -> None:
        nn.init.kaiming_uniform_(self.weight, a=math.sqrt(5))
        if self.bias is not None:
            fan_in, _ = nn.init._calculate_fan_in_and_fan_out(self.weight)
            bound = 1 / math.sqrt(fan_in) if fan_in > 0 else 0
            nn.init.uniform_(self.bias, -bound, bound)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # Pre-norm
        x = self.norm(x)
        # Quantise weight and activation
        w_q, w_scale = quantise_weight_ternary(self.weight)
        x_q, a_scale = quantise_activation_int8(x)
        # Matmul in float (for grad flow); int kernels are used at inference
        y = F.linear(x_q, w_q, None) * a_scale * w_scale
        if self.bias is not None:
            y = y + self.bias
        return y

    @classmethod
    def from_linear(cls, linear: nn.Linear) -> "BitLinear":
        bl = cls(linear.in_features, linear.out_features,
                 bias=linear.bias is not None,
                 device=linear.weight.device,
                 dtype=linear.weight.dtype)
        with torch.no_grad():
            bl.weight.copy_(linear.weight)
            if linear.bias is not None:
                bl.bias.copy_(linear.bias)
        return bl


def _matches(name: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(name, p) for p in patterns)


def replace_linear_with_bitlinear(model: nn.Module,
                                  include: list[str],
                                  exclude: list[str]) -> int:
    """In-place: swap matching nn.Linear modules for BitLinear.
    Returns number of modules replaced.
    """
    n = 0
    for parent_name, parent in list(model.named_modules()):
        for child_name, child in list(parent.named_children()):
            full = f"{parent_name}.{child_name}" if parent_name else child_name
            if not isinstance(child, nn.Linear):
                continue
            if _matches(full, exclude):
                continue
            if not _matches(full, include):
                continue
            setattr(parent, child_name, BitLinear.from_linear(child))
            n += 1
    return n


def count_ternary_params(model: nn.Module) -> tuple[int, int]:
    """Return (ternary_param_count, total_param_count)."""
    t, a = 0, 0
    for m in model.modules():
        if isinstance(m, BitLinear):
            t += m.weight.numel()
        for p in m.parameters(recurse=False):
            a += p.numel()
    return t, a


if __name__ == "__main__":
    # quick self-test
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="unsloth/gemma-4-E2B-it")
    args = p.parse_args()

    from transformers import AutoModelForCausalLM
    m = AutoModelForCausalLM.from_pretrained(args.model, torch_dtype=torch.bfloat16,
                                             device_map="cuda:0")
    n = replace_linear_with_bitlinear(
        m,
        include=["*.q_proj","*.k_proj","*.v_proj","*.o_proj",
                 "*.gate_proj","*.up_proj","*.down_proj"],
        exclude=["embed_tokens","lm_head"],
    )
    t, a = count_ternary_params(m)
    print(f"Replaced {n} Linear layers with BitLinear")
    print(f"Ternary params: {t/1e6:.1f} M")
    print(f"Total params:   {a/1e6:.1f} M")
    print(f"Effective bits/weight (overall): "
          f"{((a-t)*16 + t*1.58)/a:.2f}")
