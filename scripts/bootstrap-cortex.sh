#!/usr/bin/env bash
# bootstrap-cortex.sh — one-shot setup for Cortex on a single host.
#
# Cortex is a multimodal brain-response analysis system: TRIBE v2 (Meta) +
# Gemma 4 (Google), wrapped in a webapp that renders predicted BOLD activity
# on a 3D cortical surface ("Brain Cinema").
#
# Project: Alexios Bluff Mara LLC (dba Red Team Kitchen) × Illinois State University
# Repo:    https://github.com/AlexiosBluffMara/cortex
# Page:    https://alexiosbluffmara.github.io/cortex/
# License: Apache-2.0 (code) — TRIBE v2 weights are CC-BY-NC 4.0 (Meta),
#          Gemma 4 weights are under the Gemma Terms of Use (Google).
#          Both sets of weights are pulled by this script, never bundled.
#
# Targets: Linux, macOS, Windows (Git Bash / WSL).
# Honors: HF_TOKEN, CORTEX_OFFLINE, HF_HUB_OFFLINE, CORTEX_CACHE_DIR,
#         CORTEX_TAG, CORTEX_PORT, CORTEX_GPU_INDEX, CORTEX_CONTAINER_NAME.

set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# Defaults
# ─────────────────────────────────────────────────────────────────────────────
# __CORTEX_DEFAULT_TAG__ is rewritten by the release workflow to the actual
# release tag. When run directly from a checkout it stays as "latest".
DEFAULT_TAG="__CORTEX_DEFAULT_TAG__"
if [[ "${DEFAULT_TAG}" == "__CORTEX_DEFAULT_TAG__" ]]; then
  DEFAULT_TAG="latest"
fi

CORTEX_TAG="${CORTEX_TAG:-${DEFAULT_TAG}}"
CORTEX_PORT="${CORTEX_PORT:-8765}"
CORTEX_GPU_INDEX="${CORTEX_GPU_INDEX:-0}"
CORTEX_CONTAINER_NAME="${CORTEX_CONTAINER_NAME:-cortex-webapp}"
CORTEX_CACHE_DIR="${CORTEX_CACHE_DIR:-${HOME}/.cortex/cache}"
CORTEX_OFFLINE="${CORTEX_OFFLINE:-0}"
CORTEX_IMAGE="ghcr.io/alexiosbluffmara/cortex-webapp:${CORTEX_TAG}"

GEMMA_FAST_MODEL="${GEMMA_FAST_MODEL:-gemma4:e4b}"
TRIBE_HF_REPO="${TRIBE_HF_REPO:-facebook/tribe-v2}"
HEALTH_TIMEOUT_S="${HEALTH_TIMEOUT_S:-180}"

# ─────────────────────────────────────────────────────────────────────────────
# Pretty output
# ─────────────────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'; C_RED=$'\033[31m'
  C_GRN=$'\033[32m'; C_YLW=$'\033[33m'; C_BLU=$'\033[34m'; C_RST=$'\033[0m'
else
  C_BOLD=""; C_DIM=""; C_RED=""; C_GRN=""; C_YLW=""; C_BLU=""; C_RST=""
fi
log()  { printf "%s[cortex]%s %s\n" "${C_BLU}" "${C_RST}" "$*"; }
ok()   { printf "%s[cortex]%s %s\n" "${C_GRN}" "${C_RST}" "$*"; }
warn() { printf "%s[cortex]%s %s\n" "${C_YLW}" "${C_RST}" "$*" >&2; }
fail() { printf "%s[cortex]%s %s\n" "${C_RED}" "${C_RST}" "$*" >&2; exit 1; }

banner() {
  printf "\n%s┌─ Cortex bootstrap ─────────────────────────────────────────┐%s\n" "${C_BOLD}" "${C_RST}"
  printf "%s│ Brain Cinema · TRIBE v2 + Gemma 4 · Alexios Bluff Mara LLC │%s\n" "${C_BOLD}" "${C_RST}"
  printf "%s│ Research with Illinois State University                    │%s\n" "${C_BOLD}" "${C_RST}"
  printf "%s└────────────────────────────────────────────────────────────┘%s\n\n" "${C_BOLD}" "${C_RST}"
}

# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — Detect OS + required tooling
# ─────────────────────────────────────────────────────────────────────────────
detect_os() {
  case "$(uname -s)" in
    Linux*)             OS="linux" ;;
    Darwin*)            OS="macos" ;;
    MINGW*|MSYS*|CYGWIN*) OS="windows-bash" ;;
    *)                  OS="unknown" ;;
  esac
  log "Host OS detected: ${OS}"
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1. Install it and re-run."
}

# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — GPU detection (NVIDIA only; CPU-only is not supported)
# ─────────────────────────────────────────────────────────────────────────────
# Returns the chosen execution mode in CORTEX_MODE: "swap" or "both-resident".
# Errors out if VRAM < 24 GB.
detect_gpu() {
  if ! command -v nvidia-smi >/dev/null 2>&1; then
    fail "nvidia-smi not found. Cortex requires an NVIDIA GPU with ≥24 GB VRAM. \
See https://alexiosbluffmara.github.io/cortex/ for the supported hardware list."
  fi

  # nvidia-smi --query-gpu=memory.total returns "32607 MiB" by default.
  local raw name mib
  if ! raw="$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits -i "${CORTEX_GPU_INDEX}" 2>/dev/null)"; then
    fail "nvidia-smi failed to query GPU index ${CORTEX_GPU_INDEX}. Is the driver installed?"
  fi
  name="$(echo "$raw" | cut -d',' -f1 | sed 's/^ *//;s/ *$//')"
  mib="$(echo "$raw" | cut -d',' -f2 | sed 's/^ *//;s/ *$//' | tr -d '[:space:]')"
  CORTEX_GPU_NAME="$name"
  CORTEX_GPU_VRAM_MIB="$mib"

  log "GPU: ${name} (${mib} MiB VRAM)"

  if [[ "$mib" -lt 24000 ]]; then
    fail "GPU has only ${mib} MiB VRAM. Cortex needs ≥24 GB. \
See https://alexiosbluffmara.github.io/cortex/#hardware for supported tiers."
  elif [[ "$mib" -lt 48000 ]]; then
    CORTEX_MODE="swap"
    log "Selected execution mode: ${C_BOLD}swap${C_RST} (TRIBE ↔ Gemma 4 sequential, ~10 s per swap)"
  else
    CORTEX_MODE="both-resident"
    log "Selected execution mode: ${C_BOLD}both-resident${C_RST} (no swap)"
  fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Step 3 — Docker + nvidia-container-toolkit
# ─────────────────────────────────────────────────────────────────────────────
check_docker() {
  require_cmd docker
  if ! docker info >/dev/null 2>&1; then
    fail "Docker is installed but the daemon is not reachable. Start Docker (or Docker Desktop) and re-run."
  fi

  # Probe for nvidia runtime support. We don't fail hard here — the actual
  # `docker run --gpus all` will give a clear error if the toolkit is missing.
  if ! docker info 2>/dev/null | grep -qi 'nvidia\|Runtimes:.*nvidia'; then
    warn "Docker may not have the NVIDIA Container Toolkit configured. If 'docker run --gpus all' fails below, install it: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html"
  fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Step 4 — Pull image
# ─────────────────────────────────────────────────────────────────────────────
pull_image() {
  if [[ "$CORTEX_OFFLINE" == "1" ]]; then
    log "CORTEX_OFFLINE=1 — skipping image pull. Verifying local image exists…"
    if ! docker image inspect "$CORTEX_IMAGE" >/dev/null 2>&1; then
      fail "Image ${CORTEX_IMAGE} not present locally and CORTEX_OFFLINE=1. \
Either unset CORTEX_OFFLINE or 'docker load' the saved tarball first."
    fi
    ok "Local image present: ${CORTEX_IMAGE}"
    return
  fi

  log "Pulling ${CORTEX_IMAGE} from GHCR (public, no login required)…"
  if ! docker pull "$CORTEX_IMAGE"; then
    fail "Failed to pull ${CORTEX_IMAGE}. Check tag with: gh release list --repo AlexiosBluffMara/cortex"
  fi
  ok "Image pulled."
}

# ─────────────────────────────────────────────────────────────────────────────
# Step 5 — TRIBE v2 weights via HuggingFace
# ─────────────────────────────────────────────────────────────────────────────
fetch_tribe_weights() {
  local tribe_dir="${CORTEX_CACHE_DIR}/tribev2"
  mkdir -p "$tribe_dir"

  if [[ "${HF_HUB_OFFLINE:-0}" == "1" ]]; then
    if [[ -z "$(ls -A "$tribe_dir" 2>/dev/null)" ]]; then
      warn "HF_HUB_OFFLINE=1 but ${tribe_dir} is empty. Cortex will fail at first scan. \
Pre-populate the cache from a connected host first."
    else
      ok "HF_HUB_OFFLINE=1, using existing TRIBE cache at ${tribe_dir}"
    fi
    return
  fi

  if [[ "$CORTEX_OFFLINE" == "1" ]]; then
    log "CORTEX_OFFLINE=1 — skipping TRIBE v2 download. Using cache at ${tribe_dir}"
    if [[ -z "$(ls -A "$tribe_dir" 2>/dev/null)" ]]; then
      warn "Cache directory is empty. First scan will fail."
    fi
    return
  fi

  if [[ -z "${HF_TOKEN:-}" ]]; then
    warn "HF_TOKEN not set. TRIBE v2 is gated (CC-BY-NC 4.0, Meta). \
Get a token at https://huggingface.co/settings/tokens and accept the model terms at https://huggingface.co/${TRIBE_HF_REPO}"
    read -r -p "Paste your HF token now (or Ctrl-C to abort): " HF_TOKEN
    if [[ -z "$HF_TOKEN" ]]; then
      fail "No token provided."
    fi
    export HF_TOKEN
  fi

  log "Fetching TRIBE v2 weights from ${TRIBE_HF_REPO} into ${tribe_dir}…"
  # Prefer the huggingface CLI if present, fall back to a tiny python snippet.
  if command -v huggingface-cli >/dev/null 2>&1; then
    HF_TOKEN="$HF_TOKEN" huggingface-cli download "$TRIBE_HF_REPO" \
      --local-dir "$tribe_dir" --local-dir-use-symlinks False \
      || fail "huggingface-cli download failed."
  elif command -v python3 >/dev/null 2>&1; then
    python3 - <<PY || fail "HF download failed via python."
import os
try:
    from huggingface_hub import snapshot_download
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet", "huggingface_hub>=0.24"])
    from huggingface_hub import snapshot_download
snapshot_download(
    repo_id="${TRIBE_HF_REPO}",
    local_dir="${tribe_dir}",
    local_dir_use_symlinks=False,
    token=os.environ["HF_TOKEN"],
)
PY
  else
    fail "Neither huggingface-cli nor python3 is available to download TRIBE weights."
  fi
  ok "TRIBE v2 weights cached at ${tribe_dir}"
}

# ─────────────────────────────────────────────────────────────────────────────
# Step 6 — Gemma 4 via Ollama
# ─────────────────────────────────────────────────────────────────────────────
ensure_gemma() {
  if [[ "$CORTEX_OFFLINE" == "1" ]]; then
    log "CORTEX_OFFLINE=1 — skipping Ollama pull."
    return
  fi
  if ! command -v ollama >/dev/null 2>&1; then
    warn "Ollama not found. Install it (one line):"
    case "$OS" in
      linux)         warn "  curl -fsSL https://ollama.com/install.sh | sh" ;;
      macos)         warn "  brew install ollama   # or download https://ollama.com/download/mac" ;;
      windows-bash)  warn "  Download https://ollama.com/download/windows and re-run this script after install." ;;
      *)             warn "  See https://ollama.com/download" ;;
    esac
    warn "Skipping Gemma 4 pull. Cortex will fail at narrate-time without it."
    return
  fi
  if ! curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    warn "Ollama daemon is not running on :11434. Start it (e.g. 'ollama serve' or via the system tray) and re-run."
    return
  fi
  log "Pulling Ollama model ${GEMMA_FAST_MODEL}…"
  ollama pull "$GEMMA_FAST_MODEL" || warn "Ollama pull failed; continuing — you can retry after launch."
}

# ─────────────────────────────────────────────────────────────────────────────
# Step 7 — Run the container
# ─────────────────────────────────────────────────────────────────────────────
launch_container() {
  # Stop & remove any previous instance with the same name (idempotent).
  if docker ps -a --format '{{.Names}}' | grep -qx "$CORTEX_CONTAINER_NAME"; then
    log "Removing existing container ${CORTEX_CONTAINER_NAME}…"
    docker rm -f "$CORTEX_CONTAINER_NAME" >/dev/null
  fi

  # Host network on Linux makes Ollama-on-host reachable as 127.0.0.1.
  # On macOS / Docker Desktop / Windows we use host.docker.internal.
  local ollama_url="http://host.docker.internal:11434"
  local net_args=()
  if [[ "$OS" == "linux" ]]; then
    ollama_url="http://127.0.0.1:11434"
    net_args=(--network host)
  else
    net_args=(-p "${CORTEX_PORT}:${CORTEX_PORT}" --add-host=host.docker.internal:host-gateway)
  fi

  log "Launching ${CORTEX_CONTAINER_NAME} on :${CORTEX_PORT} (mode=${CORTEX_MODE})…"
  docker run -d \
    --name "$CORTEX_CONTAINER_NAME" \
    --gpus "device=${CORTEX_GPU_INDEX}" \
    --restart unless-stopped \
    -v "${CORTEX_CACHE_DIR}:/root/.cortex/cache" \
    -e "CORTEX_MODE=${CORTEX_MODE}" \
    -e "CORTEX_CACHE_DIR=/root/.cortex/cache" \
    -e "OLLAMA_URL=${ollama_url}" \
    -e "GEMMA_FAST_MODEL=${GEMMA_FAST_MODEL}" \
    -e "WEBAPP_HOST=0.0.0.0" \
    -e "WEBAPP_PORT=${CORTEX_PORT}" \
    -e "HF_HUB_OFFLINE=${HF_HUB_OFFLINE:-0}" \
    -e "CORTEX_OFFLINE=${CORTEX_OFFLINE}" \
    "${net_args[@]}" \
    "$CORTEX_IMAGE" >/dev/null
  ok "Container started."
}

# ─────────────────────────────────────────────────────────────────────────────
# Step 8 — Wait for /api/health
# ─────────────────────────────────────────────────────────────────────────────
wait_for_health() {
  local url="http://127.0.0.1:${CORTEX_PORT}/api/health"
  log "Waiting for ${url} (up to ${HEALTH_TIMEOUT_S}s)…"
  local start now
  start="$(date +%s)"
  while true; do
    if curl -sf "$url" >/dev/null 2>&1; then
      ok "Cortex is healthy."
      break
    fi
    now="$(date +%s)"
    if (( now - start > HEALTH_TIMEOUT_S )); then
      docker logs --tail=80 "$CORTEX_CONTAINER_NAME" || true
      fail "Health check did not pass within ${HEALTH_TIMEOUT_S}s. See container logs above."
    fi
    sleep 3
  done
}

# ─────────────────────────────────────────────────────────────────────────────
# Step 9 — Print run summary
# ─────────────────────────────────────────────────────────────────────────────
summary() {
  cat <<EOF

${C_GRN}${C_BOLD}Cortex is up.${C_RST}

  Open:        ${C_BOLD}http://localhost:${CORTEX_PORT}/${C_RST}
  Health:      http://localhost:${CORTEX_PORT}/api/health
  Container:   ${CORTEX_CONTAINER_NAME}   (image: ${CORTEX_IMAGE})
  GPU:         ${CORTEX_GPU_NAME} (${CORTEX_GPU_VRAM_MIB} MiB) — mode=${CORTEX_MODE}
  Cache:       ${CORTEX_CACHE_DIR}

  Stop:        docker stop ${CORTEX_CONTAINER_NAME}
  Logs:        docker logs -f ${CORTEX_CONTAINER_NAME}

${C_DIM}Brain Cinema project page: https://alexiosbluffmara.github.io/cortex/${C_RST}
${C_DIM}Alexios Bluff Mara LLC × Illinois State University${C_RST}

EOF
}

# ─────────────────────────────────────────────────────────────────────────────
main() {
  banner
  detect_os
  require_cmd curl
  detect_gpu
  check_docker
  pull_image
  fetch_tribe_weights
  ensure_gemma
  launch_container
  wait_for_health
  summary
}

main "$@"
