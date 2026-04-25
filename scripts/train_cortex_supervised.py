"""Stage-gated, monitored Unsloth fine-tune supervisor.

Pipeline:
  1. preflight  — check imports, GPU, dataset, disk
  2. install    — pip install heavy deps (gated by --install-deps; opt-in)
  3. smoke      — 50 examples, 1 epoch, batch 2, max_seq 2048 (~3-5 min)
  4. production — full dataset, configured epochs, with live metrics
  5. validate   — load merged model, smoke an inference call
  6. ollama     — pull the GGUF into Ollama and smoke a generate

Each stage runs as a subprocess of `train_cortex.py` so the supervisor itself
stays light (no torch/unsloth import). Failure in any stage halts the
pipeline with a structured diagnostic written to
`data/training_supervisor.log`.

Live monitoring: the `production` stage spawns the trainer in the background
and the supervisor tails the metrics JSONL on a polling loop, printing a
compact one-line status every 10 seconds: step, loss, vram, ETA.

Usage::

    # Full pipeline (no install — assumes deps already in place)
    python -m scripts.train_cortex_supervised

    # Full pipeline with auto-install
    python -m scripts.train_cortex_supervised --install-deps

    # Stop after smoke test (good for incremental verification)
    python -m scripts.train_cortex_supervised --stage smoke

    # Re-run only validate + ollama (after a previous training succeeded)
    python -m scripts.train_cortex_supervised --stage validate --skip-up-to validate
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = REPO_ROOT / "outputs" / "cortex-training"
DEFAULT_DATASET = REPO_ROOT / "data" / "cortex_train.jsonl"
DEFAULT_LOG = REPO_ROOT / "data" / "training_supervisor.log"
DEFAULT_METRICS = REPO_ROOT / "data" / "training_metrics.jsonl"

ALL_STAGES = ("preflight", "install", "smoke", "production", "validate", "ollama")


# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------

class _Logger:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def _emit(self, level: str, msg: str, **fields: Any) -> None:
        rec = {"t": time.time(), "level": level, "msg": msg, **fields}
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")
        except OSError:
            pass
        print(f"[train_supervisor] {level} {msg}", file=sys.stderr, flush=True)

    def info(self, msg: str, **fields: Any) -> None: self._emit("INFO", msg, **fields)
    def warn(self, msg: str, **fields: Any) -> None: self._emit("WARN", msg, **fields)
    def error(self, msg: str, **fields: Any) -> None: self._emit("ERROR", msg, **fields)


# ---------------------------------------------------------------------------
# Stage 1: preflight
# ---------------------------------------------------------------------------

REQUIRED_PACKAGES = (
    "torch", "unsloth", "trl", "datasets", "bitsandbytes",
    "peft", "accelerate", "transformers",
)


def stage_preflight(*, dataset: Path, log: _Logger) -> dict[str, Any]:
    """Check imports + GPU + dataset + disk. Returns a structured report."""
    report: dict[str, Any] = {"stage": "preflight", "ok": True, "checks": {}}

    def _record(name: str, ok: bool, **extra: Any) -> None:
        report["checks"][name] = {"ok": ok, **extra}
        if not ok:
            report["ok"] = False

    # 1. nvidia-smi
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.free,memory.total",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        _record("nvidia-smi", r.returncode == 0, output=r.stdout.strip())
    except (subprocess.TimeoutExpired, OSError) as exc:
        _record("nvidia-smi", False, error=str(exc))

    # 2. each Python package
    for pkg in REQUIRED_PACKAGES:
        r = subprocess.run(
            [sys.executable, "-c", f"import {pkg}; print({pkg}.__version__)"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if r.returncode == 0:
            _record(pkg, True, version=r.stdout.strip())
        else:
            _record(pkg, False, error=r.stderr.strip().splitlines()[-1] if r.stderr else "")

    # 3. torch + CUDA + Blackwell sanity
    if report["checks"].get("torch", {}).get("ok"):
        r = subprocess.run(
            [sys.executable, "-c",
             "import torch, json; "
             "info = {'cuda_available': torch.cuda.is_available(), "
             "'cuda_version': torch.version.cuda, "
             "'device': torch.cuda.get_device_name(0) if torch.cuda.is_available() else None, "
             "'compute_cap': list(torch.cuda.get_device_capability(0)) if torch.cuda.is_available() else None}; "
             "free, total = (torch.cuda.mem_get_info(0) if torch.cuda.is_available() else (0, 0)); "
             "info['vram_free_gb'] = round(free / 1024**3, 1); "
             "info['vram_total_gb'] = round(total / 1024**3, 1); "
             "print(json.dumps(info))"],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if r.returncode == 0:
            try:
                gpu_info = json.loads(r.stdout.strip())
                _record("cuda", gpu_info["cuda_available"], **gpu_info)
            except json.JSONDecodeError:
                _record("cuda", False, error=f"could not parse: {r.stdout!r}")
        else:
            _record("cuda", False, error=r.stderr.strip())

    # 4. dataset
    if dataset.exists():
        try:
            n_lines = sum(1 for _ in dataset.open(encoding="utf-8"))
            _record("dataset", n_lines >= 100,
                    path=str(dataset), n_examples=n_lines)
        except OSError as exc:
            _record("dataset", False, error=str(exc))
    else:
        _record("dataset", False, error=f"{dataset} not found")

    # 5. disk free
    _total, _used, free = shutil.disk_usage(REPO_ROOT)
    free_gb = round(free / 1024**3, 1)
    _record("disk_free", free_gb >= 20, free_gb=free_gb)

    log.info("preflight complete", ok=report["ok"], n_checks=len(report["checks"]))
    return report


# ---------------------------------------------------------------------------
# Stage 2: install (only if --install-deps)
# ---------------------------------------------------------------------------

PIP_TORCH = ["pip", "install", "--index-url",
             "https://download.pytorch.org/whl/cu128",
             "torch", "torchvision"]
PIP_REST = ["pip", "install", "--upgrade",
            "unsloth", "trl", "datasets", "bitsandbytes",
            "peft", "accelerate", "transformers"]


def stage_install(*, log: _Logger, retries: int = 2) -> bool:
    """Install heavy deps in two stages with retries."""
    for label, cmd in (("torch", PIP_TORCH), ("unsloth-stack", PIP_REST)):
        for attempt in range(1, retries + 1):
            log.info(f"install: {label} attempt {attempt}/{retries}")
            r = subprocess.run(
                [sys.executable, "-m", *cmd],
                capture_output=False, check=False,
            )
            if r.returncode == 0:
                break
            log.warn(f"install {label} failed (rc={r.returncode}); retrying")
            time.sleep(min(2 ** attempt, 30))
        else:
            log.error(f"install {label} failed after {retries} attempts")
            return False
    return True


# ---------------------------------------------------------------------------
# Stage 3: smoke test
# ---------------------------------------------------------------------------

@dataclass
class SmokeResult:
    ok: bool
    elapsed_s: float
    stdout_tail: str = ""
    stderr_tail: str = ""


def stage_smoke(*, dataset: Path, output_dir: Path, log: _Logger) -> SmokeResult:
    """Tiny end-to-end run: 50 examples, 1 epoch, batch 2.

    The whole point: catch "the pipeline is broken" failures (bad import,
    OOM at load, NaN loss, save failures) in 5 minutes instead of 45.
    """
    smoke_dir = output_dir.parent / f"{output_dir.name}-smoke"
    log.info(f"smoke: 50 examples → {smoke_dir}")
    t0 = time.time()
    cmd = [
        sys.executable, "-m", "scripts.train_cortex",
        "--smoke-test",
        "--dataset", str(dataset),
        "--output-dir", str(smoke_dir),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900, check=False)
    except subprocess.TimeoutExpired:
        log.error("smoke timed out after 15 minutes")
        return SmokeResult(ok=False, elapsed_s=time.time() - t0, stderr_tail="TIMEOUT")

    elapsed = time.time() - t0
    ok = r.returncode == 0
    if ok:
        log.info(f"smoke OK in {elapsed:.0f}s")
    else:
        log.error(f"smoke FAILED in {elapsed:.0f}s rc={r.returncode}")
    return SmokeResult(
        ok=ok,
        elapsed_s=elapsed,
        stdout_tail=r.stdout[-2000:],
        stderr_tail=r.stderr[-2000:],
    )


# ---------------------------------------------------------------------------
# Stage 4: production fine-tune with live monitoring
# ---------------------------------------------------------------------------

def _tail_metrics(metrics_file: Path, pid_proc: subprocess.Popen, log: _Logger,
                  poll_every_s: float = 10.0) -> None:
    """Tail the metrics JSONL until the training process exits.

    Prints a one-line status every poll_every_s with current step / loss / vram.
    """
    last_size = 0
    last_step = -1
    last_log_t = 0.0

    while pid_proc.poll() is None:
        time.sleep(poll_every_s)
        if not metrics_file.exists():
            continue
        size = metrics_file.stat().st_size
        if size == last_size:
            continue
        last_size = size

        # Read all lines, find the latest record
        try:
            lines = metrics_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        if not lines:
            continue

        latest: dict[str, Any] | None = None
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                latest = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
        if latest is None:
            continue

        step = int(latest.get("step", -1))
        if step == last_step:
            continue
        last_step = step

        # Build a one-line status
        loss = latest.get("loss") or latest.get("train_loss")
        lr = latest.get("learning_rate")
        epoch = latest.get("epoch", 0.0)
        vram = latest.get("vram_alloc_gb")
        bits = [f"step={step}"]
        if epoch is not None:
            bits.append(f"epoch={epoch:.2f}")
        if loss is not None:
            bits.append(f"loss={loss:.4f}")
        if lr is not None:
            bits.append(f"lr={lr:.2e}")
        if vram is not None:
            bits.append(f"vram={vram:.1f}GB")

        now = time.time()
        if now - last_log_t >= poll_every_s - 0.1:  # rate-limit
            log.info("training: " + " ".join(bits))
            last_log_t = now

        # NaN guard
        if isinstance(loss, float) and (loss != loss):  # NaN
            log.error("NaN loss detected; killing trainer")
            pid_proc.kill()
            break


def stage_production(
    *,
    dataset: Path,
    output_dir: Path,
    epochs: int,
    metrics_file: Path,
    log: _Logger,
    timeout_s: int = 4 * 60 * 60,  # 4 hours
) -> bool:
    """Run the full fine-tune with live metrics tailing."""
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics_file.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, "-m", "scripts.train_cortex",
        "--dataset", str(dataset),
        "--output-dir", str(output_dir),
        "--epochs", str(epochs),
        "--metrics-file", str(metrics_file),
        "--merge",
        "--gguf", "q4_k_m",
        "--modelfile",
    ]
    log.info("production: spawning trainer", cmd=" ".join(cmd))

    proc = subprocess.Popen(
        cmd, stdout=sys.stderr, stderr=sys.stderr,
        cwd=str(REPO_ROOT),
    )
    deadline = time.time() + timeout_s
    try:
        _tail_metrics(metrics_file, proc, log)
    except KeyboardInterrupt:
        log.warn("interrupted by user; killing trainer")
        proc.kill()
        return False

    # Make sure proc is done
    while proc.poll() is None:
        if time.time() > deadline:
            log.error("production exceeded timeout; killing")
            proc.kill()
            return False
        time.sleep(2)

    rc = proc.returncode
    if rc == 0:
        log.info("production OK")
        return True
    log.error(f"production FAILED rc={rc}")
    return False


# ---------------------------------------------------------------------------
# Stage 5+6: validate + ollama smoke
# ---------------------------------------------------------------------------

def stage_validate(*, output_dir: Path, log: _Logger) -> bool:
    """Load the GGUF and run one inference call to verify the artifact."""
    gguf_dir = output_dir / "gguf"
    gguf_files = list(gguf_dir.glob("*.gguf")) if gguf_dir.exists() else []
    modelfile = gguf_dir / "Modelfile" if gguf_dir.exists() else None

    if not gguf_files:
        log.error(f"validate: no GGUF found at {gguf_dir}")
        return False
    if modelfile is None or not modelfile.exists():
        log.error(f"validate: no Modelfile at {modelfile}")
        return False

    log.info(f"validate: GGUF present at {gguf_files[0]} ({gguf_files[0].stat().st_size / 1024**3:.1f} GB)")
    log.info(f"validate: Modelfile present at {modelfile}")
    return True


def stage_ollama(*, output_dir: Path, model_name: str, log: _Logger) -> bool:
    """Pull the GGUF into Ollama via `ollama create` and smoke a generate."""
    if shutil.which("ollama") is None:
        log.warn("ollama: CLI not on PATH; skipping")
        return True  # not a hard failure

    gguf_dir = output_dir / "gguf"
    modelfile = gguf_dir / "Modelfile"
    if not modelfile.exists():
        log.error("ollama: no Modelfile to import")
        return False

    log.info(f"ollama: ollama create {model_name} -f {modelfile}")
    r = subprocess.run(
        ["ollama", "create", model_name, "-f", str(modelfile)],
        cwd=str(gguf_dir), capture_output=True, text=True, timeout=300, check=False,
    )
    if r.returncode != 0:
        log.error(f"ollama create failed: {r.stderr.strip()[:500]}")
        return False

    log.info(f"ollama: smoke generate against {model_name}")
    r = subprocess.run(
        ["ollama", "run", model_name,
         "What does activation in V1 indicate? Answer in one sentence."],
        capture_output=True, text=True, timeout=120, check=False,
    )
    if r.returncode != 0:
        log.error(f"ollama generate failed: {r.stderr.strip()[:500]}")
        return False

    log.info("ollama smoke output", text=r.stdout.strip()[:300])
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Stage-gated, monitored Unsloth fine-tune for cortex-gemma-4-e4b.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--metrics-file", type=Path, default=DEFAULT_METRICS)
    p.add_argument("--log-file", type=Path, default=DEFAULT_LOG)
    p.add_argument("--epochs", type=int, default=3)
    p.add_argument("--model-name", default="cortex-gemma-4-e4b",
                   help="Local Ollama tag for the fine-tune")

    p.add_argument("--install-deps", action="store_true",
                   help="Run the heavy pip install before training")
    p.add_argument(
        "--stage",
        nargs="+",
        choices=ALL_STAGES,
        default=list(ALL_STAGES),
        help="Run only these stages",
    )
    p.add_argument(
        "--skip-failed-preflight",
        action="store_true",
        help="Continue past a failing preflight (use only if you know why)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    log = _Logger(args.log_file)
    stages = set(args.stage)

    log.info("supervisor start",
             dataset=str(args.dataset), output_dir=str(args.output_dir),
             stages=sorted(stages))

    # Preflight first
    if "preflight" in stages:
        report = stage_preflight(dataset=args.dataset, log=log)
        print(json.dumps(report, indent=2), file=sys.stderr)
        if not report["ok"] and not args.skip_failed_preflight:
            log.error("preflight failed; aborting (use --skip-failed-preflight to override)")
            return 1

    # Install
    if "install" in stages and args.install_deps:
        if not stage_install(log=log):
            log.error("install failed; aborting")
            return 2

    # Smoke
    if "smoke" in stages:
        result = stage_smoke(
            dataset=args.dataset, output_dir=args.output_dir, log=log,
        )
        if not result.ok:
            log.error("smoke failed", stderr=result.stderr_tail[-500:])
            print("--- stdout tail ---", file=sys.stderr)
            print(result.stdout_tail, file=sys.stderr)
            print("--- stderr tail ---", file=sys.stderr)
            print(result.stderr_tail, file=sys.stderr)
            return 3

    # Production
    if "production" in stages:
        if not stage_production(
            dataset=args.dataset,
            output_dir=args.output_dir,
            epochs=args.epochs,
            metrics_file=args.metrics_file,
            log=log,
        ):
            return 4

    # Validate
    if "validate" in stages:
        if not stage_validate(output_dir=args.output_dir, log=log):
            return 5

    # Ollama smoke
    if "ollama" in stages:
        if not stage_ollama(output_dir=args.output_dir, model_name=args.model_name, log=log):
            return 6

    log.info("supervisor done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
