# Mac as TRIBE + Gemma primary — architecture rebuild

The April 26 Gemini bill ($2K) and the gaming-on-Seratonin requirement push us to a new arrangement: **Big Apple becomes the steady-state Tier-1 inference provider.** Seratonin is freed for gaming and heavy-batch jobs. This doc is the rebuild plan.

## What changes vs. what we had

| Layer | Before | After |
| --- | --- | --- |
| TRIBE v2 host | Seratonin (5090, GPU-pinned) | **Big Apple (M4 Max, MLX-optimized)** |
| Gemma narration host | Seratonin Ollama | **Big Apple Ollama** primary, Seratonin secondary |
| Inference router | on Seratonin :8765 | runs on **both**; Cloudflare Worker picks healthy one |
| Cloudflared tunnel | on Seratonin only | also on Big Apple as warm standby |
| Gaming policy | manual flip | **default**: Big Apple is primary, Seratonin gaming = no inference cost |
| Power draw 24/7 | ~115 W (5090 idle) | **~30 W** (M4 idle ~5 W + Pi ~3 W + 5090 sleep) |

## Why this is googley

- **Right tool for the job**: M4 Max's 38-TOPS NPU + unified memory makes inference cheap; the 5090's 1.4-PFLOPS makes training cheap. Use each for what it's best at.
- **Energy efficient**: 5090 idles at ~80 W when "not gaming." Big Apple idles at ~5 W. 24/7 inference on the Mac saves **~$60/year in electricity** alone.
- **Failure-mode improvement**: if Mac dies, Seratonin spins up; if Seratonin dies, Mac keeps running. No single point of failure.
- **Mac is portable**: Big Apple goes to Bloomington with Soumit. Inference travels with him. Tailscale routes back to Cortex demo automatically.

## Hardware capabilities recap

### Big Apple (M4 Max, 48 GB unified)
- **Gemma 4 26B Q4** (16 GB) ✓ comfortable
- **Gemma 4 31B dense Q4** (18 GB) ✓ comfortable
- **Gemma 4 e4b** (5 GB) + **gemma 4 e2b** (3 GB) simultaneous ✓
- **TRIBE v2** (~3 GB) ✓ via MLX or PyTorch-MPS
- **Whisper Large v3 Turbo** (1.5 GB) via MLX ✓ — actually faster than CUDA on small clips
- Power: ~5 W idle, peaks at ~80 W under sustained load

### Seratonin (5090, 32 GB GDDR7, 64 GB system)
- Same models all fit; faster on long-context generation
- **Reserved for**: training runs, large-batch sweeps, gaming
- Power: ~80 W idle, peaks at ~600 W

### Baby Pi (Tier-4, fallback)
- BitNet b1.58 2B-4T (700 MB) ✓
- Always-on, dashboard host
- Power: ~3-12 W

## The new request flow

```
Browser → Cloudflare DNS + Pages
                │
                ▼
         Cloudflare Worker (cortex-api)
                │ checks healthy endpoints
                ▼
        ┌───────┴────────┐
        │                │
   TIER 1: Big Apple   TIER 2: Seratonin (only when M4 saturated)
   Tailscale            Tailscale
   :11434 Ollama        :11434 Ollama
   :8765  router        :8765  router
   :tribe (MLX)         :tribe (CUDA)
        │                │
        └────┬───────────┘
             │ both fail
             ▼
        TIER 3: Workers AI (Cloudflare)
        TIER 4: Baby Pi BitNet ternary
```

## Implementation: Big Apple side

### 1. Install Ollama + MLX + TRIBE port

On Big Apple, run via Tailscale SSH from Seratonin:

```bash
# Ollama
brew install ollama
brew services start ollama
launchctl setenv OLLAMA_HOST 0.0.0.0:11434
launchctl setenv OLLAMA_KEEP_ALIVE 24h
launchctl setenv OLLAMA_MAX_LOADED_MODELS 3

# Pull the same models that are on Seratonin
ollama pull gemma4:26b
ollama pull gemma4:e4b
ollama pull gemma4:e2b
ollama pull embeddinggemma:300m

# MLX for native Apple Silicon inference (faster than Ollama for some workloads)
python3 -m venv ~/cortex/mlx-venv
source ~/cortex/mlx-venv/bin/activate
pip install mlx mlx-lm mlx-whisper

# Whisper Large v3 Turbo via MLX (replaces Whisper-CUDA on Seratonin)
python3 -c "from mlx_whisper import load_models; load_models('large-v3-turbo')"
```

### 2. TRIBE v2 on Big Apple via PyTorch-MPS or MLX port

TRIBE v2 is a research model from Meta AI (Brain Encoding Foundation Model). The reference impl uses CUDA; for the Mac we use the MPS (Metal Performance Shaders) backend:

```bash
cd ~/cortex
git clone https://github.com/[tribe-repo] tribe-v2  # if mirrored to git
python3 -m venv .venv
source .venv/bin/activate
pip install torch torchvision  # PyTorch with MPS backend ships in stable since 2022
# Verify MPS works:
python3 -c "import torch; print(torch.backends.mps.is_available())"  # → True
```

For PyTorch on Mac:
- `torch.device("mps")` instead of `torch.device("cuda")`
- Most TRIBE ops port cleanly; rare `aten::*` ops need CPU fallback (`PYTORCH_ENABLE_MPS_FALLBACK=1`)
- Expected throughput: ~70-80% of CUDA RTX 5090 on this model size, but with **0 W draw when idle**

If TRIBE turns out to be too custom for MPS, fall back to running it on Seratonin via Tailscale and only routing Gemma to Big Apple. We can mix.

### 3. Cloudflared on Big Apple as standby tunnel

```bash
brew install cloudflared

# Use the same tunnel cert.pem (export from Seratonin via:
#   tailscale ssh soumit@big-apple "mkdir -p ~/.cloudflared"
#   scp ~/.cloudflared/cert.pem soumit@big-apple:~/.cloudflared/
# )

# Launch tunnel using the existing rtk-5090 tunnel ID. cloudflared supports
# multiple replicas for the same tunnel — Cloudflare load-balances.
cloudflared tunnel --config ~/.cloudflared/config.yml run rtk-5090
```

Now both Seratonin AND Big Apple maintain QUIC connections to Cloudflare's edge. If Seratonin's tunnel dies, Big Apple keeps serving. Free, automatic redundancy.

### 4. Inference router on Big Apple

Clone the router from Seratonin:

```bash
git clone https://github.com/AlexiosBluffMara/cortex.git ~/cortex
cd ~/cortex/inference_router
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# Run with multi-backend config that prefers local Ollama
OLLAMA_BACKENDS="http://localhost:11434,http://seratonin:11434" \
  uvicorn inference_router.server:app --host 127.0.0.1 --port 8765
```

Make it a launchd daemon so it survives reboots:

```bash
cat > ~/Library/LaunchAgents/com.redteamkitchen.inference.plist <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.redteamkitchen.inference</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/soumit/cortex/.venv/bin/uvicorn</string>
    <string>inference_router.server:app</string>
    <string>--host</string><string>127.0.0.1</string>
    <string>--port</string><string>8765</string>
  </array>
  <key>WorkingDirectory</key><string>/Users/soumit/cortex</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>OLLAMA_BACKENDS</key><string>http://localhost:11434,http://seratonin:11434</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/inference-router.log</string>
  <key>StandardErrorPath</key><string>/tmp/inference-router.err</string>
</dict>
</plist>
EOF
launchctl load -w ~/Library/LaunchAgents/com.redteamkitchen.inference.plist
```

### 5. Update Cloudflare Worker to route by health

The `cortex-api` Worker already exists at `D:\cortex\workers\cortex-api`. We update its routing to:

1. Try `https://big-apple.<your-tailscale-domain>.ts.net:8765/healthz` (via Tailscale Funnel) first
2. Fall back to `https://seratonin.scylla-betta.ts.net:8765/healthz`
3. If both unhealthy, return Workers AI fallback

Edit `workers/cortex-api/src/index.ts`, change the `INFERENCE_BACKENDS` constant to the dual-funnel config. Redeploy when Cloudflare token gets write scope.

## Implementation: Seratonin side

### 1. Demote inference router to "warm standby"

The router stays running on Seratonin :8765, but the Worker calls it only when Big Apple's healthz fails 3 times in a row. Seratonin's GPU is freed up for gaming whenever the user wants.

### 2. Gaming auto-detect (optional polish)

When a known-game `.exe` (steam, epic, riot client, etc.) launches, a PowerShell hook downgrades the inference router to `BelowNormal` priority and tells Ollama to release the GPU:

```powershell
# D:\cortex\scripts\gaming-mode-on.ps1
Get-Process python | Where-Object { $_.CommandLine -match "inference_router" } | ForEach-Object { $_.PriorityClass = "BelowNormal" }
$env:OLLAMA_KEEP_ALIVE = "0"
Restart-Service ollama -Force
```

### 3. Tunnel stays running, but Big Apple takes the lead

Both replicas register; Cloudflare picks whichever responds fastest. Cloudflared on Big Apple should advertise lower latency to ord11 / ord14 / ord15 (closer DERPs) since the M4 has less networking overhead.

## Tailscale + Parsec verification

After the above is up:

```powershell
# From Seratonin, test Big Apple as inference origin
$ip = "100.93.240.52"     # big-apple Tailscale IP
Invoke-WebRequest "http://${ip}:11434/api/tags"  # should list Gemma models
Invoke-WebRequest "http://${ip}:8765/healthz"    # should be 200, JSON

# Parsec test: open Parsec → host = big-apple
# If hosting works, you can KVM to Big Apple from this PC anytime
```

```bash
# From Big Apple, test back to Seratonin
tailscale ping seratonin                   # < 5 ms LAN, < 50 ms over internet
curl http://seratonin:11434/api/tags       # falls back across nodes
```

## What stays the same

- The Cortex demo URL (`https://cortex.redteamkitchen.com`) — still proxied by Cloudflare Pages + Worker
- The Mercury dashboard (`https://mercury.redteamkitchen.com`) — still on Seratonin :8080 (Mercury is light, Seratonin handles it fine even while gaming)
- Baby Pi as Tier-4 BitNet ternary — unchanged
- Pixel Fold control panel — unchanged
- Cloudflare Funnel + Tailscale Funnel public paths — unchanged

## Cost / power outcome

| Metric | Before | After |
| --- | --- | --- |
| Daily inference electricity (24/7) | $0.23 (5090 idle 80 W) | $0.06 (M4 idle 5 W + Pi 3 W) |
| Annual electricity for inference idle | ~$84 | ~$22 |
| Cortex demo cold-start latency | ~3 s (5090 Gemma cold load) | ~1.5 s (M4 unified memory load) |
| Gaming inference cost | inference router fights game for GPU | **zero** — gaming has 5090 to itself |
| Bloomington-trip behavior | Mac must Tailscale back to Seratonin | Mac IS the inference, no roundtrip |

## Sources
- [Apple MLX framework docs](https://ml-explore.github.io/mlx/build/html/index.html)
- [MLX whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper)
- [PyTorch MPS backend](https://pytorch.org/docs/stable/notes/mps.html)
- [Ollama on macOS docs](https://github.com/ollama/ollama/blob/main/docs/macos.md)
- [Cloudflare Tunnel HA / multi-replica setup](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/configure-tunnels/local-management/multiple-replicas/)
