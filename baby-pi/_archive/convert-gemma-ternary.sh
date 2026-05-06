#!/usr/bin/env bash
# convert-gemma-ternary.sh
#
# Take Gemma 4 e2b, push it through llama.cpp's increasingly aggressive
# quantization steps to land near BitNet b1.58 ternary (1.5 bits/weight),
# then stage the result for deployment to Baby Pi via Ollama.
#
# Why IQ1_S/IQ1_M instead of "true" ternary:
#   - True BitNet b1.58 weights are {-1, 0, +1} (1.58 bits/weight) and
#     require quantization-aware training from scratch. Microsoft's
#     released models work; converting an off-the-shelf Gemma checkpoint
#     post-hoc loses too much quality unless you do QAT (~$$$).
#   - llama.cpp's IQ1_S sits at ~1.56 bits/weight and IQ1_M at ~1.75. They
#     use lookup-table importance weighting (imatrix) so quality survives
#     much better than naive 1-bit. Effective bit budget = BitNet, but the
#     weights aren't strictly ternary — they're learned codebooks.
#
# Outputs (all under exports/):
#   gemma-4-e2b-f16.gguf            ~5.0 GB   baseline
#   gemma-4-e2b-Q4_K_M.gguf         ~1.6 GB   reference (Pi default)
#   gemma-4-e2b-Q2_K.gguf           ~0.85 GB  2-bit
#   gemma-4-e2b-IQ1_S.gguf          ~0.47 GB  ~1.56 bit (ternary territory)
#   gemma-4-e2b-IQ1_M.gguf          ~0.55 GB  ~1.75 bit (more quality)
#
# Plus a perplexity comparison run on wikitext-2-raw to score quality drop.

set -euo pipefail
ROOT="/mnt/d/cortex/baby-pi"
EXPORTS="$ROOT/exports"
# WORK + llama.cpp on native WSL2 ext4 — DrvFs (/mnt/d) chokes on large
# multi-GB writes during convert/imatrix.
WORK="$HOME/cortex-baby-pi-work"
LLAMA="$WORK/llama.cpp"
HF_REPO="unsloth/gemma-4-E2B-it"
MODEL_NAME="gemma-4-e2b"
mkdir -p "$EXPORTS" "$WORK"

LOG="$ROOT/logs/convert-$(date +%Y%m%d-%H%M).log"
mkdir -p "$ROOT/logs" "$WORK" "$EXPORTS"
exec > >(tee -a "$LOG") 2>&1
echo "=== convert-gemma-ternary $(date) ==="

# Always use the venv python directly — `source activate` doesn't always
# survive setsid+nohup wrappers, so prefer the absolute path.
PY="$HOME/unsloth-env/.venv/bin/python"
export PATH="$HOME/unsloth-env/.venv/bin:$PATH"
export HF_HUB_ENABLE_HF_TRANSFER=1
echo "py: $PY"
[[ -x "$PY" ]] || { echo "ERROR: venv python missing at $PY"; exit 1; }

# ---------------------------------------------------------------
# 1. llama.cpp toolchain
# ---------------------------------------------------------------
if [[ ! -d "$LLAMA" ]]; then
    git clone --depth 1 https://github.com/ggerganov/llama.cpp.git "$LLAMA"
fi
cd "$LLAMA"
git pull --ff-only || true
if [[ ! -f "$LLAMA/build/bin/llama-quantize" ]]; then
    rm -rf build
    # Try CUDA, fall back to CPU. Quantize/imatrix are CPU-bound anyway.
    if ! cmake -B build -DGGML_CUDA=ON -DCMAKE_BUILD_TYPE=Release 2>/dev/null; then
        echo "CUDA not available in WSL2 — building CPU-only"
        rm -rf build
        cmake -B build -DCMAKE_BUILD_TYPE=Release
    fi
    cmake --build build --config Release -j --target llama-quantize llama-cli llama-perplexity llama-imatrix
fi
QUANTIZE="$LLAMA/build/bin/llama-quantize"
PERPLEXITY="$LLAMA/build/bin/llama-perplexity"
IMATRIX="$LLAMA/build/bin/llama-imatrix"
# Newer llama.cpp builds shared libs in build/bin/ — binaries can't find them
# without LD_LIBRARY_PATH (no rpath). Add it for every native call below.
export LD_LIBRARY_PATH="$LLAMA/build/bin:${LD_LIBRARY_PATH:-}"

# ---------------------------------------------------------------
# 2. Pull HF model
# ---------------------------------------------------------------
HF_LOCAL="$WORK/$MODEL_NAME-hf"
mkdir -p "$HF_LOCAL"
# Re-download until at least one .safetensors lands
if ! ls "$HF_LOCAL"/*.safetensors >/dev/null 2>&1; then
    "$PY" - <<PY
import os
os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "1"
from huggingface_hub import snapshot_download
snapshot_download(repo_id="$HF_REPO", local_dir="$HF_LOCAL",
                  allow_patterns=["*.json","*.safetensors","tokenizer*","*.txt","*.model"])
PY
fi
ls -lh "$HF_LOCAL"/*.safetensors 2>/dev/null | head -3

# ---------------------------------------------------------------
# 3. Convert HF -> GGUF f16
# ---------------------------------------------------------------
# Big intermediate file lives on WSL ext4 to avoid DrvFs Bus error
F16="$WORK/${MODEL_NAME}-f16.gguf"
if [[ -f "$F16" && $(stat -c %s "$F16" 2>/dev/null) -lt 8000000000 ]]; then
    echo "deleting corrupt partial f16 ($(stat -c %s "$F16") bytes)"
    rm -f "$F16"
fi
if [[ ! -f "$F16" ]]; then
    cd "$LLAMA"
    "$PY" convert_hf_to_gguf.py "$HF_LOCAL" --outtype f16 --outfile "$F16"
fi
ls -lh "$F16"

# ---------------------------------------------------------------
# 4. Build importance matrix (imatrix) on a calibration corpus.
#    Critical for IQ-quants — without imatrix the quant is much worse.
# ---------------------------------------------------------------
IMATRIX_FILE="$WORK/${MODEL_NAME}-imatrix.dat"
CALIB="$WORK/calibration.txt"
if [[ ! -f "$CALIB" ]]; then
    # Tiny English+code+reasoning calibration set
    curl -sL https://raw.githubusercontent.com/ggerganov/llama.cpp/master/examples/imatrix/imatrix.cpp \
        > "$WORK/_calib_seed.txt" || true
    cat > "$CALIB" <<'EOF'
The quick brown fox jumps over the lazy dog. In a hole in the ground there lived a hobbit.
def fibonacci(n): return n if n < 2 else fibonacci(n-1) + fibonacci(n-2)
Quantum entanglement describes correlations between particles separated by arbitrary distances.
Reduce the recipe by half: 2 cups flour, 1 tsp salt, 4 eggs, 1 cup milk, mix gently.
Question: What is the capital of France? Answer: Paris.
EOF
    cat "$WORK/_calib_seed.txt" >> "$CALIB" 2>/dev/null || true
fi
if [[ ! -f "$IMATRIX_FILE" ]]; then
    "$IMATRIX" -m "$F16" -f "$CALIB" -o "$IMATRIX_FILE" --chunks 8
fi

# ---------------------------------------------------------------
# 5. Quantize at every level we care about
# ---------------------------------------------------------------
mkdir -p "$EXPORTS"
for LEVEL in Q4_K_M Q2_K IQ1_M IQ1_S; do
    # Quantize to WORK first, then move to DrvFs (single seq write of <2 GB is fine)
    TMP_OUT="$WORK/${MODEL_NAME}-${LEVEL}.gguf"
    OUT="$EXPORTS/${MODEL_NAME}-${LEVEL}.gguf"
    if [[ -f "$OUT" ]]; then
        echo "skip $LEVEL (already exists $(du -h "$OUT" | cut -f1))"
        continue
    fi
    if [[ "$LEVEL" == IQ1_* || "$LEVEL" == IQ2_* ]]; then
        if ! "$QUANTIZE" --imatrix "$IMATRIX_FILE" "$F16" "$TMP_OUT" "$LEVEL" 2>&1; then
            echo "WARN: $LEVEL failed (likely insufficient imatrix coverage); skipping"
            rm -f "$TMP_OUT"
            continue
        fi
    else
        if ! "$QUANTIZE" "$F16" "$TMP_OUT" "$LEVEL" 2>&1; then
            echo "WARN: $LEVEL failed; skipping"
            rm -f "$TMP_OUT"
            continue
        fi
    fi
    mv "$TMP_OUT" "$OUT"
    du -h "$OUT"
done

# ---------------------------------------------------------------
# 6. Quick perplexity on a tiny eval set (lower = better)
# ---------------------------------------------------------------
EVAL="$WORK/wikitext-tiny.txt"
if [[ ! -f "$EVAL" ]]; then
    curl -sL https://raw.githubusercontent.com/ggerganov/llama.cpp/master/tests/quantize-stats/test.txt \
        > "$EVAL" 2>/dev/null || head -1000 "$CALIB" > "$EVAL"
fi
echo ""
echo "=== perplexity (lower better) ==="
for LEVEL in Q4_K_M Q2_K IQ1_M IQ1_S; do
    OUT="$EXPORTS/${MODEL_NAME}-${LEVEL}.gguf"
    [[ -f "$OUT" ]] || continue
    PPL=$("$PERPLEXITY" -m "$OUT" -f "$EVAL" --chunks 4 -c 512 2>&1 \
            | tail -3 | grep -oE '[0-9]+\.[0-9]+' | head -1)
    SIZE=$(du -h "$OUT" | cut -f1)
    printf "  %-8s %8s  ppl=%s\n" "$LEVEL" "$SIZE" "${PPL:-?}"
done

echo "=== convert-gemma-ternary done $(date) ==="
echo "Stage with: bash $ROOT/deploy-pi-models.sh"
