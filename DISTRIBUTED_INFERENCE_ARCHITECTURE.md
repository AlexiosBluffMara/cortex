# Distributed Inference + Storage Architecture
**Red Team Kitchen / Cortex** — 2026-05-01

## Hardware inventory (verified via Tailscale)

| Node | Tailscale | Role | Hardware | Notes |
| --- | --- | --- | --- | --- |
| `seratonin` (this PC) | `100.98.19.87` | Tier-1 GPU + primary storage | RTX 5090 (32 GB GDDR7), 64 GB RAM, Windows 11, D:\ for projects | Always-on; cloudflared tunnel host |
| `big-apple` (M4 Max) | `100.93.240.52` | Tier-2 inference + warm replica | M4 Max, **48 GB unified RAM**, macOS, Parsec hosting | Always-on; bedroom |
| `baby-pi` (RPi 5) | (after setup) | Tier-4 ternary LLM + dashboard host | RPi 5 8 GB, 4× Cortex-A76 @ 2.4 GHz, NVMe SSD, 4K HDMI out | **Living room**; runs BitNet b1.58 2B-4T at ~8-11 tok/s; drives 4K monitor as live demo dashboard. **See RASPBERRY_PI_5_SETUP.md** |
| `miniapple` | `100.75.223.113` | (reserved) | Mac mini class | Online; not in current plan |
| `dreamer` | offline 36 d | — | Windows | Skip |

## What "distributing Gemma + TRIBE" actually means (and doesn't)

**It does NOT mean tensor parallelism.** Splitting one inference call across two machines requires sub-millisecond interconnect (NVLink, PCIe 5, or RDMA). Tailscale at LAN speeds (~94 ms ping between seratonin and big-apple) is **5,000× too slow** to make tensor-parallel sharding faster than running on one node. Don't try.

**It DOES mean:**

1. **Request-level parallelism** — multiple concurrent demo submissions route round-robin. **2× throughput.** Single-request latency unchanged.
2. **Model-by-model placement** — different models live on different nodes by VRAM fit:
   - **5090 (32 GB GDDR7)**: TRIBE v2 (~3 GB) + Gemma 4 26B Q4 (~16 GB) loaded simultaneously. Headroom for activations.
   - **M4 Max (48 GB unified)**: Gemma 4 26B Q4 (~16 GB) **OR** Gemma 4 31B Dense Q4 (~18 GB) **OR** Gemma 4 E4B (~5 GB) + multiple secondary models. Unified memory means no host↔GPU copy penalty.
3. **Tier-aware routing** — fast/short narration → M4 (lower latency on small batch); deep/long narration with citations → 5090 (raw FLOPs win at long context).
4. **Failover** — if 5090 is mid-TRIBE-inference, narration requests don't queue behind it; they route to M4 immediately.

**Realistic speedup for the public website**: 2× peak throughput, ~30 % p50 latency reduction (because narration calls don't wait for TRIBE), no improvement on cold-start single-user latency.

---

## Inference cluster — request flow

```
                     ┌─────────────────────────────────────┐
                     │        Cloudflare DNS + Pages       │
                     │   redteamkitchen.com (static UI)    │
                     └─────────────┬───────────────────────┘
                                   │ POST /api/scan
                                   ▼
         ┌──────────────────────────────────────────────┐
         │     Cloud Run: cortex-relay (us-central1)    │
         │           orchestrator + queue head          │
         └────────────────┬───────────────┬─────────────┘
                          │               │
          INFERENCE_URL=  │               │  TRIBE_URL=
          inference.rtk   │               │  tribe.rtk
                          ▼               ▼
            ┌──────────────────────────────────────┐
            │   inference-router (D:\cortex)       │
            │   FastAPI on seratonin:8765 OR Pages │
            │   Function. Picks backend by:        │
            │     - model name → node affinity     │
            │     - queue depth on each node       │
            │     - health pings every 5 s         │
            └──────────┬─────────────────┬─────────┘
                       │                 │
                  Tier-1 try         Tier-2 fallback
                       │                 │
       ┌───────────────┘                 └────────────────┐
       ▼                                                  ▼
 ┌──────────────────┐                         ┌─────────────────────┐
 │  seratonin (RTX  │                         │  big-apple (M4 Max) │
 │  5090, 32 GB)    │                         │  48 GB unified      │
 │  Ollama :11434   │                         │  Ollama :11434 over │
 │  -- TRIBE v2     │                         │  Tailscale          │
 │  -- gemma4:26b   │                         │  -- gemma4:26b      │
 │  -- gemma4:e4b   │                         │  -- gemma4:e4b      │
 │  -- emb-gemma    │                         │  -- whisper-mlx     │
 └──────────────────┘                         └─────────────────────┘
                                                  ▲
                                                  │  optional: Parsec for visual
                                                  │  monitoring; SSH for ops
```

### Routing rules (encoded in inference-router config)

| Model class | Primary | Fallback chain | Why |
| --- | --- | --- | --- |
| **TRIBE v2** | `seratonin` only | none | Single-machine, GPU-pinned |
| **Heavy narration** (gemma4:26b, gemma4:31b) | round-robin seratonin/big-apple | the other → Workers AI | Both can hold the model |
| **Fast narration** (gemma4:e4b, gemma4:e2b) | `big-apple` | `seratonin` → `baby-pi` (BitNet) → Workers AI | Mac unified memory wins on small models; RPi catches overflow |
| **Vision gate** (gemma vision) | `big-apple` | `seratonin` | Mac MLX is fast on vision tokens |
| **Embeddings** (embeddinggemma:300m) | round-robin big-apple/seratonin | the other | Cheap, parallelize |
| **Whisper / audio** | `big-apple` | none | MLX whisper is faster than CUDA on small clips |
| **Intent classification / safety gate** | `baby-pi` (BitNet) | `big-apple` → fallthrough | ~600 ms on the Pi for 50-token classifications, free, always-on |
| **"is this video safe to process?"** | `baby-pi` | `big-apple` | Runs even when desktop+Mac are down |

Health endpoint on each node: `http://{node}:11434/api/tags`. If 3 consecutive checks fail, mark node **degraded** and route everything to the survivor.

---

## Storage architecture — local DB + backup using both machines

### Layer 1 — Live file sync (Syncthing)

Bidirectional sync of the Cortex demo assets between both machines:
- `D:\cortex\demo\` ↔ `~/Cortex/demo/` (Mac)
- `D:\cortex\generated\` ↔ `~/Cortex/generated/` (videos, PNGs, MP4s)
- `~/.ollama/models\` (5090) ↔ `~/.ollama/models` (Mac) — **share pulled model weights**, save bandwidth & disk on both ends

Syncthing chosen over rsync: live, encrypted (Tailscale-only), automatic conflict handling, no cron.

### Layer 2 — SQLite + Litestream (replication)

Cortex tracks demo submissions, narrations, vision-gate decisions in a SQLite DB. Litestream continuously streams the WAL to:
- Primary: `seratonin` (write side, low-latency for the relay)
- Replica: `big-apple` (read-only mirror, queryable from Mac when remote)
- Snapshot target: Cloudflare R2 (every 1 h)

This gives:
- 1 h RPO if both machines die simultaneously
- < 1 s lag for read replication to the Mac
- Read-from-Mac is useful when Soumit is using Parsec — no round-trip to seratonin

### Layer 3 — Restic encrypted backups to R2

Daily snapshots of:
- Whole `D:\cortex\` (excluding `~/.ollama/models` which Syncthing handles)
- Mac `~/Cortex/` and `~/.mercury/`
- Both machines' SQLite databases (point-in-time on top of Litestream)

Restic's deduplication means full daily snapshots only cost the delta. Encrypted with a key stored in 1Password / printed in the safe (not on either machine).

### Combined disk math

| Asset | Size | Storage strategy |
| --- | --- | --- |
| Ollama model weights | ~80 GB (gemma4:26b + e4b + emb + whisper) | **Shared via Syncthing** between both machines (saves ~80 GB on second pull) |
| TRIBE v2 weights | ~3 GB | seratonin only (GPU-pinned, no need on Mac) |
| Demo videos (input) | ~500 MB / submission | Syncthing (both sides) |
| Generated PNGs / MP4s | ~50 MB / submission | Syncthing (both sides) |
| SQLite DB | < 100 MB | Litestream replication |
| Daily Restic backups | ~50 MB delta | R2 (encrypted) |

---

## Always-on configuration

### Mac (`big-apple`, macOS)

```bash
# Prevent display sleep AND system sleep when the lid is closed (clamshell mode requires power+display+keyboard;
# alternative is `caffeinate` for indefinite no-sleep on AC)
sudo pmset -a sleep 0 disablesleep 1 displaysleep 0 disksleep 0
sudo pmset -a hibernatemode 0
sudo pmset -a powernap 0
# Allow lid-closed running on AC even without external display
sudo pmset -a tcpkeepalive 1

# Auto-restart Ollama if it crashes (launchd plist)
mkdir -p ~/Library/LaunchAgents
cat > ~/Library/LaunchAgents/ai.ollama.serve.plist <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>ai.ollama.serve</string>
  <key>ProgramArguments</key>
  <array>
    <string>/opt/homebrew/bin/ollama</string>
    <string>serve</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>EnvironmentVariables</key>
  <dict>
    <key>OLLAMA_HOST</key><string>0.0.0.0:11434</string>
    <key>OLLAMA_KEEP_ALIVE</key><string>24h</string>
    <key>OLLAMA_MAX_LOADED_MODELS</key><string>3</string>
  </dict>
  <key>StandardOutPath</key><string>/tmp/ollama.log</string>
  <key>StandardErrorPath</key><string>/tmp/ollama.err</string>
</dict>
</plist>
EOF
launchctl load -w ~/Library/LaunchAgents/ai.ollama.serve.plist
```

`OLLAMA_HOST=0.0.0.0:11434` makes the Mac's Ollama reachable on the Tailscale interface (and only the Tailscale interface, since macOS firewall + Tailscale ACLs lock external access).

### Windows (`seratonin`, this PC) — already always-on

Already running. Add to inference-router boot via existing Task Scheduler entry.

---

## Why the Mac going-away helps the website specifically

Today, the public flow is:
```
Browser → Cloud Run (cortex-relay) → seratonin/Ollama → narration → response
```
Single bottleneck. If seratonin is mid-TRIBE-inference (~30 s on a heavy clip), every other request stalls.

After this change:
```
Browser → Cloud Run → inference-router → {seratonin, big-apple} → response
```
TRIBE still pins to seratonin. But narration calls (the slow LLM step, ~5–15 s each) split round-robin. Two simultaneous demo submissions: one waits 0 s for narration, the other gets routed to big-apple's Ollama and runs in parallel. **Wall-clock time per submission unchanged for the first user; second concurrent user now sees no queueing.**

Caveat: M4 Max gemma4:26b at Q4 is ~30 % slower per-token than the 5090. So the routing prefers M4 for SHORT generations (which gemma's Metal backend handles with low overhead) and 5090 for LONG generations. The router knows.

---

## Setup sequence — what runs when

### Phase 1 — Mac preparation (Soumit, ~10 min via Parsec or local keyboard)
1. Enable SSH:  System Settings → General → Sharing → Remote Login = ON, restrict to admin user
2. Run on the Mac:
   ```bash
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   brew install ollama syncthing restic litestream
   ```
3. Confirm SSH works: `ssh soumit@100.93.240.52` from this Windows PC

### Phase 2 — Ollama on Mac (5 min, automated once SSH is up)
Once SSH is on, I'll run from this Windows PC:
- Push the launchd plist
- Pull the same Gemma models that are on seratonin
- Verify `curl http://100.93.240.52:11434/api/tags` returns the expected models

### Phase 3 — Update inference-router (10 min, automated)
- Add `OLLAMA_BACKENDS="http://localhost:11434,http://100.93.240.52:11434"` env var
- Push routing rules per the table above
- Restart router (parked on 8766 for now — moved off 8765 to avoid the NSSM webapp conflict)

### Phase 4 — Storage cluster (20 min, automated once Mac SSH is up)
- Start Syncthing on both machines, pair via web UI (one-time)
- Litestream config on seratonin (primary), Mac (replica), R2 (snapshot)
- Restic init repo at R2; nightly cron on each machine

### Phase 5 — Verification (5 min)
- Trigger 4 simultaneous Cortex demo submissions, watch them split
- Pull power on seratonin, verify Mac handles all requests (failover)
- Verify Litestream lag < 1 s
- Force Restic restore from R2 to a temp dir, verify integrity

---

## Open questions for Soumit

1. **Mac model name confirmation**: 48 GB M4 Max — is this MacBook Pro 14" or 16"? (affects sustained-load thermals; 14" throttles after ~10 min sustained)
2. **R2 access**: do you have R2 enabled on the Cloudflare account? (Was disabled-by-default last time I checked. If not, one click in dash → R2 Object Storage → Purchase R2.)
3. **Mac admin password**: I'll need it (or a passwordless sudoers rule for `pmset` and `launchctl`) to fully automate Phase 2. Or you can run a single `sudo` command from the script I'll generate.
4. **Parsec or SSH for ops**: do you want me to drive the Mac via SSH (faster for me) or do you prefer to run scripts I hand you via Parsec (more visibility for you)?

---

*Next: I'll write the actual setup script (`scripts/setup-mac-node.sh`) that handles Phases 1–4 once SSH is on. It's idempotent and re-runnable.*
