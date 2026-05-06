"""smoke_test.py — sanity check that Unsloth loads Gemma 4 e4b in 4-bit + runs
a forward pass on the 5090. Does NOT do actual training, just exercises the
critical path.
"""
from __future__ import annotations

import time

import torch

print(f"torch {torch.__version__}  cuda={torch.version.cuda}  available={torch.cuda.is_available()}")
print(f"device {torch.cuda.get_device_name(0)}  capability={torch.cuda.get_device_capability(0)}")

t0 = time.perf_counter()
print("\n=== importing unsloth ===")
from unsloth import FastLanguageModel
print(f"unsloth import: {time.perf_counter()-t0:.1f}s")

t0 = time.perf_counter()
print("\n=== loading unsloth/gemma-4-E4B-it-unsloth-bnb-4bit ===")
model, tokenizer = FastLanguageModel.from_pretrained(
    model_name="unsloth/gemma-4-E4B-it-unsloth-bnb-4bit",
    max_seq_length=4096,
    dtype=None,
    load_in_4bit=True,
)
print(f"load: {time.perf_counter()-t0:.1f}s")
print(f"model device: {next(model.parameters()).device}")
print(f"VRAM allocated: {torch.cuda.memory_allocated()/1e9:.2f} GB")

print("\n=== applying LoRA r=16 ===")
model = FastLanguageModel.get_peft_model(
    model,
    r=16, lora_alpha=32, lora_dropout=0,
    bias="none",
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
    use_gradient_checkpointing="unsloth",
    random_state=3407,
)

print("\n=== generating ===")
FastLanguageModel.for_inference(model)
prompt = "Question: In one sentence, what is fine-tuning?\nAnswer:"
inputs = tokenizer(prompt, return_tensors="pt").to("cuda")
t0 = time.perf_counter()
out = model.generate(**inputs, max_new_tokens=40, temperature=0.7, do_sample=False)
dt = time.perf_counter() - t0
text = tokenizer.decode(out[0], skip_special_tokens=True)
n_tok = out.shape[1] - inputs["input_ids"].shape[1]
print(f"generated {n_tok} tokens in {dt:.2f}s ({n_tok/dt:.1f} t/s)")
print("---")
print(text)
print("---")
print(f"VRAM peak: {torch.cuda.max_memory_allocated()/1e9:.2f} GB")
print("\nOK")
