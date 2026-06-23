"""studio.py — Gradio UI for the Mercury → Cortex training pipeline.

Lightweight stand-in for Unsloth's official Studio (which is still primarily
notebook-based as of mid-2026). Exposes the same pipeline that
nightly_retrain.sh runs: extract → train → export → deploy.

Launch:
    python scripts/studio.py --port 7860 --host 0.0.0.0

Then visit http://seratonin:7860/ from the Mac/Pixel/etc.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

import gradio as gr
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "configs" / "mercury-gemma4-e4b-lora.yaml"


def load_cfg() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def save_cfg(cfg: dict) -> None:
    CONFIG_PATH.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")


def list_datasets() -> list[str]:
    return sorted([str(p.relative_to(ROOT)) for p in (ROOT / "datasets").glob("*.jsonl")])


def list_checkpoints() -> list[str]:
    out = []
    for p in (ROOT / "checkpoints").iterdir() if (ROOT / "checkpoints").exists() else []:
        if p.is_dir():
            out.append(str(p.relative_to(ROOT)))
    return sorted(out)


def list_exports() -> list[str]:
    out = []
    for p in (ROOT / "exports").iterdir() if (ROOT / "exports").exists() else []:
        out.append(str(p.relative_to(ROOT)))
    return sorted(out)


def stream_subprocess(cmd: list[str], log_path: Path):
    """Run cmd, yield lines as they arrive."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as logf:
        proc = subprocess.Popen(
            cmd, cwd=str(ROOT), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, bufsize=1, text=True,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            logf.write(line)
            logf.flush()
            yield line.rstrip()
        proc.wait()
        yield f"\n[exit {proc.returncode}]"


def cmd_extract(src_dirs: str, include_failed: bool, min_turns: int):
    out = ROOT / "datasets" / f"mercury-{datetime.now().strftime('%Y%m%d-%H%M')}.jsonl"
    cmd = [sys.executable, "scripts/extract_trajectories.py",
           "--src", *src_dirs.split(),
           "--out", str(out),
           "--min-turns", str(int(min_turns))]
    if include_failed:
        cmd.append("--include-failed")
    log = ROOT / "logs" / f"extract-{datetime.now().strftime('%Y%m%d-%H%M')}.log"
    buf = []
    for line in stream_subprocess(cmd, log):
        buf.append(line)
        yield "\n".join(buf[-200:])


def cmd_train(dataset: str, epochs: float, lr: float, lora_r: int):
    cfg = load_cfg()
    cfg["training"]["num_train_epochs"] = float(epochs)
    cfg["training"]["learning_rate"] = float(lr)
    cfg["lora"]["r"] = int(lora_r)
    cfg["lora"]["alpha"] = int(lora_r) * 2
    save_cfg(cfg)
    cmd = [sys.executable, "scripts/train_lora.py",
           "--config", "configs/mercury-gemma4-e4b-lora.yaml"]
    if dataset:
        cmd.extend(["--dataset", dataset])
    log = ROOT / "logs" / f"train-{datetime.now().strftime('%Y%m%d-%H%M')}.log"
    buf = []
    for line in stream_subprocess(cmd, log):
        buf.append(line)
        yield "\n".join(buf[-300:])


def cmd_export_gguf(ckpt: str, quant: str, register: bool):
    cmd = [sys.executable, "scripts/export_gguf.py", "--ckpt", ckpt, "--quant", quant]
    if register:
        cmd.append("--register")
    log = ROOT / "logs" / f"gguf-{datetime.now().strftime('%Y%m%d-%H%M')}.log"
    buf = []
    for line in stream_subprocess(cmd, log):
        buf.append(line)
        yield "\n".join(buf[-200:])


def cmd_export_mlx(ckpt: str, bits: int):
    cmd = [sys.executable, "scripts/export_mlx.py", "--ckpt", ckpt, "--bits", str(int(bits))]
    log = ROOT / "logs" / f"mlx-{datetime.now().strftime('%Y%m%d-%H%M')}.log"
    buf = []
    for line in stream_subprocess(cmd, log):
        buf.append(line)
        yield "\n".join(buf[-200:])


def cmd_deploy():
    cmd = ["bash", "scripts/deploy.sh"]
    log = ROOT / "logs" / f"deploy-{datetime.now().strftime('%Y%m%d-%H%M')}.log"
    buf = []
    for line in stream_subprocess(cmd, log):
        buf.append(line)
        yield "\n".join(buf[-200:])


def gpu_status() -> str:
    try:
        out = subprocess.check_output(["nvidia-smi", "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu",
                                       "--format=csv,noheader"], text=True)
        return out.strip()
    except Exception as exc:
        return f"nvidia-smi failed: {exc}"


def build_ui():
    with gr.Blocks(title="Cortex × Mercury Training Studio") as app:
        gr.Markdown("# Cortex × Mercury Training Studio\nUnsloth-driven LoRA loop on RTX 5090 (sm_120 Blackwell)")

        with gr.Row():
            gpu = gr.Textbox(label="GPU", value=gpu_status, every=10, interactive=False)

        with gr.Tabs():
            with gr.Tab("1. Extract"):
                src = gr.Textbox(label="Source dirs (space-separated)",
                                 value="~/.mercury/sessions /mnt/d/mercury/runs ~/gemma4-pipeline/runs")
                inc_failed = gr.Checkbox(label="Include failed trajectories", value=False)
                min_turns = gr.Slider(2, 20, value=4, step=1, label="Min turns per trajectory")
                extract_log = gr.Textbox(label="log", lines=18, interactive=False)
                gr.Button("Extract", variant="primary").click(
                    cmd_extract, [src, inc_failed, min_turns], extract_log)

            with gr.Tab("2. Train"):
                ds = gr.Dropdown(choices=list_datasets, label="Dataset",
                                 allow_custom_value=True)
                epochs = gr.Number(value=3, label="Epochs", precision=1)
                lr = gr.Number(value=2.0e-4, label="Learning rate")
                lora_r = gr.Slider(8, 128, value=32, step=8, label="LoRA rank (alpha = 2× rank)")
                train_log = gr.Textbox(label="log", lines=22, interactive=False)
                gr.Button("Start training", variant="primary").click(
                    cmd_train, [ds, epochs, lr, lora_r], train_log)

            with gr.Tab("3. Export GGUF (Ollama)"):
                ckpt_g = gr.Dropdown(choices=list_checkpoints, label="Checkpoint",
                                     allow_custom_value=True)
                quant = gr.Dropdown(choices=["q4_k_m", "q5_k_m", "q8_0", "f16"],
                                    value="q4_k_m", label="Quantization")
                reg = gr.Checkbox(label="Register with local Ollama", value=True)
                gguf_log = gr.Textbox(label="log", lines=14, interactive=False)
                gr.Button("Export GGUF", variant="primary").click(
                    cmd_export_gguf, [ckpt_g, quant, reg], gguf_log)

            with gr.Tab("4. Export MLX (Seratonin)"):
                ckpt_m = gr.Dropdown(choices=list_checkpoints, label="Checkpoint",
                                     allow_custom_value=True)
                bits = gr.Dropdown(choices=[4, 8], value=4, label="Quant bits")
                mlx_log = gr.Textbox(label="log", lines=14, interactive=False)
                gr.Button("Export MLX", variant="primary").click(
                    cmd_export_mlx, [ckpt_m, bits], mlx_log)

            with gr.Tab("5. Deploy"):
                deploy_log = gr.Textbox(label="log", lines=18, interactive=False)
                gr.Button("Deploy to Seratonin Ollama + Seratonin MLX",
                          variant="primary").click(cmd_deploy, [], deploy_log)

            with gr.Tab("6. Artifacts"):
                with gr.Row():
                    with gr.Column():
                        gr.Markdown("### Datasets")
                        gr.JSON(value=list_datasets, every=5)
                    with gr.Column():
                        gr.Markdown("### Checkpoints")
                        gr.JSON(value=list_checkpoints, every=5)
                    with gr.Column():
                        gr.Markdown("### Exports")
                        gr.JSON(value=list_exports, every=5)

    return app


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=7860)
    p.add_argument("--share", action="store_true")
    args = p.parse_args()
    app = build_ui()
    app.launch(server_name=args.host, server_port=args.port,
               share=args.share, show_error=True)


if __name__ == "__main__":
    main()
