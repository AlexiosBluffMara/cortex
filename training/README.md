# Mercury → Cortex training pipeline

End-to-end LoRA fine-tuning loop:

```
Mercury runs (agentic_opd / hermes_swe / web_research / terminal_test envs)
  └─ writes trajectory_samples.jsonl  (ShareGPT format, see mercury/agent/trajectory.py)
       │
       ▼
  scripts/extract_trajectories.py   # gather + dedupe + filter completed=true
       │
       ▼
  datasets/mercury-<DATE>.jsonl     # consolidated ShareGPT dataset
       │
       ▼
  scripts/train_lora.py             # Unsloth on RTX 5090 (sm_120 Blackwell)
       │   reads configs/mercury-gemma4-e4b-lora.yaml
       │   ↓
  checkpoints/mercury-gemma4-e4b-<DATE>/  # adapter weights + tokenizer
       │
       ├── scripts/export_gguf.py   # merge LoRA, convert to GGUF Q4_K_M
       │      ↓
       │   exports/mercury-gemma4-e4b-<DATE>.gguf
       │      ↓
       │   ollama create mercury:e4b -f Modelfile  (Seratonin Ollama)
       │
       └── scripts/export_mlx.py    # merge LoRA, convert to MLX 4-bit
              ↓
           exports/mercury-gemma4-e4b-<DATE>-mlx/
              ↓
           scp → Seratonin ~/.cache/huggingface/hub/
              ↓
           launchctl reload ai.mlx.server  (Seratonin MLX :8090)
```

The inference router (`inference_router/router.py`) hot-picks whichever model
the request asks for. Mercury can target `mercury:e4b` to hit its own
fine-tune, or `gemma4:e4b` to hit the base.

## Hardware

- **RTX 5090, 32 GB GDDR7, sm_120 Blackwell** — runs `unsloth` LoRA training
- **CUDA driver 596.21, runtime 13.2** — exposed via WSL2
- **PyTorch nightly cu128** — required for sm_120
- WSL2 Ubuntu 24.04, Python 3.12, uv venv at `~/unsloth-env`

## Quick start

```bash
# 1. Extract latest trajectories from Mercury runs
python scripts/extract_trajectories.py \
    --src ~/.mercury/sessions /mnt/d/mercury/runs \
    --out datasets/mercury-$(date +%Y%m%d).jsonl

# 2. Train (writes checkpoints/)
python scripts/train_lora.py --config configs/mercury-gemma4-e4b-lora.yaml

# 3. Export both formats
python scripts/export_gguf.py --ckpt checkpoints/mercury-gemma4-e4b-LATEST
python scripts/export_mlx.py  --ckpt checkpoints/mercury-gemma4-e4b-LATEST

# 4. Deploy
bash scripts/deploy.sh
```

Or run the whole loop as a cron job (configured via `mercury cron create`).
