# Ascended Base — operating policy

**Owner:** Alexios Bluff Mara LLC (dba Red Team Kitchen), Chicago, IL
**Cluster name:** Ascended Base
**Operating principle:** [Be Googley](~/.claude/projects/D--RedTeamKitchenGoogleMetaISU/memory/reference_googleyness.md) — focus on the user, 10× over 10%, prefer Google primitives where free + better, intellectual humility about what doesn't work yet.

### Nodes (4 desktop-class + 1 future)

| Node | Hardware | Role | Tailscale |
| --- | --- | --- | --- |
| **Seratonin** | Windows 11, RTX 5090 32 GB GDDR7, 64 GB RAM | Tier-1 GPU + tunnel host | `100.98.19.87` |
| **Seratonin** | macOS, M4 Max, 48 GB unified | Tier-2 LLM + warm replica | `127.0.0.1` |
| **Baby Pi** | RPi 5, 8 GB, BitNet b1.58 ternary | Tier-4 fallback + 4K kiosk | (after firstrun) |
| **Pixel 9 Pro Fold** | Tensor G4, 16 GB RAM, 8" inner display, USB-C DisplayPort, Pixel Buds Pro 2 | Mobile control + portable workstation via [Native Desktop Mode](PIXEL_FOLD_CONTROL_PANEL.md) | `100.102.198.9` |
| *future:* PS5 hack | TBD | Possibly a 4th GPU tier | n/a |

The LLC owns all the hardware. Subscriptions migrate to the LLC business debit card to build credit history.

---

## Two operating profiles

The cluster runs in one of two profiles at any time. Switching is a one-line script; no manual fiddling.

### Profile A — "Always on, all up" (default)

| Node | State | What it serves |
| --- | --- | --- |
| **Seratonin** | Always-on | tunnel host, TRIBE v2 GPU pin, Gemma 4 26B+, Mercury dashboard :8080, inference router :8765 |
| **Seratonin** | Always-on (lid-closed-on-AC) | Tier-2 Ollama, Whisper-MLX, warm replica of SQLite + model weights via Syncthing, Quick Share + Pixel Buds bridge |
| **Baby Pi** | Always-on | BitNet b1.58 ternary on :8000, kiosk dashboard on the 4K monitor, intent-classification gate |
| **Pixel Fold** | With Soumit | Tailscale always-on; Termux:Widget shortcuts; Native Desktop Mode when docked |

Steady-state continuous draw: ~115 W (5090 idle ~80 W, M4 ~5 W idle, Pi ~3 W idle). Pixel battery, not wall power.

### Profile B — "Gaming on Seratonin" (toggle on while playing)

| Node | State | What changes |
| --- | --- | --- |
| Seratonin | **Gaming foreground** | Inference router demoted to `BelowNormal` priority; Ollama capped at 1 GPU stream; Mercury dashboard suspended. TRIBE GPU pin freed. |
| Seratonin | **Promoted to Tier-1** | Inference router on Seratonin redirects narration calls to `http://seratonin:11434`. Mac picks up 100% of LLM load. |
| Baby Pi | **Unchanged** | Continues handling intent gate + dashboard. |

Trigger: detect a known game process (Steam, Epic, Riot, etc.) starts → flip a Cloudflared `originRequest.preference` for `inference.redteamkitchen.com` to point at Seratonin. When the game exits → flip back. Implemented via `D:\cortex\scripts\gaming-mode.ps1` (writes a `~/.cortex/inference.env` overlay with `OLLAMA_BACKENDS="http://seratonin:11434"` first).

### Profile C — "Soumit traveling to Bloomington" (manual)

When Soumit takes Seratonin to Bloomington:
- Seratonin **stays on, headless** — accessible via Parsec from the Mac
- Seratonin is a roaming Tier-1 (still on Tailscale, can hit Seratonin's tunnel even on cellular)
- Baby Pi: unchanged, lives in the apartment
- **Pixel Fold travels too** — its Native Desktop Mode + a portable USB-C monitor in the bag = a real workstation if Seratonin is busy training

No flip needed; just Seratonin's + Pixel's Tailscale IPs follow them.

### Profile D — "Pixel-only field op" (anywhere there's Wi-Fi or 5G)

Soumit + Pixel + Pixel Buds Pro 2 + a USB-C dock + any monitor = a complete dev environment:
- **Pixel Native Desktop Mode** on the external monitor (Chrome, Termux, VS Code Web)
- **Tailscale on the Pixel** routes him back to Ascended Base
- **Pixel Buds Pro 2 multipoint** keeps him on calls without breaking the dev flow
- **NFC tag on the laptop bag** auto-launches the dev tools the moment he docks

Don't try to run inference on the Pixel. Pin all LLM work to Seratonin/Seratonin/Baby Pi and route via the tunnel.

---

## Public surface (what the internet sees)

| URL | Owner | Performance target |
| --- | --- | --- |
| `https://redteamkitchen.com/` | Cloudflare Pages (apex) | < 100 ms p50 globally |
| `https://www.redteamkitchen.com/` | Pages CNAME (after add) | < 100 ms p50 |
| `https://cortex.redteamkitchen.com/` | Pages site (post-migration) → Worker → tunnel → router | < 30 s for narration p95 |
| `https://mercury.redteamkitchen.com/` | tunnel → Mercury dashboard :8080 | private, Cloudflare Access soon |
| `https://ollama.redteamkitchen.com/` | tunnel → Ollama :11434 | private, internal only |
| `https://inference.redteamkitchen.com/` | tunnel → router :8765 | internal; Worker calls only |
| `https://seratonin.scylla-betta.ts.net/` | Tailscale Funnel → router :8443 | backup public path; if Cloudflare goes down |

**Two redundant public paths**: Cloudflare (preferred, proxied + WAF + Pages CDN) and Tailscale Funnel (free, runs even if Cloudflare token expires). Worth keeping both.

---

## Ports + services on each node

### Seratonin (Windows 11)

```
:8080   Mercury dashboard          — D:\mercury\.venv\Scripts\python.exe -m mercury_cli.main dashboard
:8765   Inference router           — C:\Users\soumi\cortex\.venv\Scripts\python.exe -m uvicorn inference_router.server:app
:11434  Ollama                     — C:\Users\soumi\AppData\Local\Programs\Ollama\ollama.exe serve
:8443   Tailscale Funnel listener  — built-in
```

NSSM services that auto-start on boot:
- `rtk-cloudflared` — the tunnel. Runs as SYSTEM.
- `rtk-cortex-webapp` — disabled now (Manual start). Was conflicting with the inference router on 8765.
- `rtk-mercury-gateway` — currently Paused; the dashboard process is running on 8080 manually.

Logs:
- `C:\Users\soumi\.cortex\logs\` — inference router, daily-cap watcher
- `C:\Users\soumi\.mercury\logs\` — Mercury dashboard
- `C:\Users\soumi\.cloudflared\logs\` — tunnel + foreground replicas

### Seratonin (macOS)

After `setup-mac-node.sh`:
```
:11434  Ollama (launchd ai.ollama.serve)
:8384   Syncthing GUI (launchd io.syncthing.app)
:22     SSH (after `sudo tailscale up --ssh`)
```

### Baby Pi (Raspberry Pi OS)

After `setup-baby-pi.sh`:
```
:8000   llama-server (BitNet b1.58 2B-4T)   — systemd bitnet-server.service
:22     SSH                                  — Tailscale SSH
```
Plus a Chromium kiosk on the connected 4K monitor pointed at `https://cortex.redteamkitchen.com/dashboard`.

---

## Pixel Fold as control panel

`pixel-9-pro-fold` is currently OFFLINE on Tailscale (last seen 11 days ago). To bring it online + use it as a roaming control surface:

### One-time setup
1. Wake the device, connect to WiFi
2. Open Tailscale app → log in with `soumitlahiri@philanthropytraders.com` → confirm node name `pixel-9-pro-fold`
3. (Optional) Enable Tailscale "Always on" and "Allow Tailscale on metered connections"
4. Install Termux from F-Droid (NOT Play Store version — Play Store one is abandoned)
5. In Termux: `pkg install openssh curl jq` then `ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519`
6. Copy the public key into `~/.ssh/authorized_keys` on Seratonin (via `tailscale ssh soumit@seratonin "cat >> ~/.ssh/authorized_keys" < ~/.ssh/id_ed25519.pub`)
7. Add a Termux widget shortcut to the Pixel home screen for the most common commands

### Useful one-tap shortcuts (Termux:Widget)
Save these as `~/.shortcuts/<name>` on the Pixel:

```
~/.shortcuts/health       # public health probe
~/.shortcuts/restart-router  # SSH to seratonin and restart inference router
~/.shortcuts/seratonin-tail  # SSH to Seratonin and tail Ollama log
~/.shortcuts/tunnel-status   # CF API call, jq for status
```

The Pixel becomes a 1-tap dashboard for the cluster wherever Soumit is. Battery cost: negligible (Tailscale uses < 1% / hr idle).

---

## Storage policy

| Asset | Primary | Replica | Backup |
| --- | --- | --- | --- |
| Cortex source code | `D:\cortex` (Seratonin) | git origin (GitHub public) | Restic to `H:\restic-cortex` (8 TB SanDisk) |
| Mercury source | `D:\mercury` | git origin | same |
| Ollama model weights | `~\.ollama\models` (Seratonin) | `~/.ollama/models` (Seratonin) via Syncthing | not backed up — re-pullable |
| TRIBE v2 weights | `D:\cortex\models\` | none | Restic |
| Cortex SQLite DB | `D:\cortex\cortex.db` | Seratonin via Litestream | R2 hourly snapshot |
| Demo videos in/out | `D:\cortex\demo\` | Seratonin via Syncthing | Restic to H: + R2 weekly |
| LLC docs | (TBD: 1Password vault) | n/a | encrypted Restic to H: AND R2 |

Encryption: Restic uses an encryption key NOT stored on either machine. The key is in 1Password + a printed paper copy in a safe. Lose both → backups become unrecoverable. Don't lose both.

---

## Costs (current)

| Line | $/mo | Notes |
| --- | --- | --- |
| Cloudflare (Free) | $0 | Pages, Tunnel, Workers free tier, R2 once enabled |
| Tailscale (Free, < 100 devices) | $0 | Funnel included |
| Mercury bank | $0 | LLC checking + debit, no monthly fee |
| Domain renewal | ~$0.83 | $10/yr after Cloudflare's at-cost pricing |
| Electricity (115 W × 24/7) | ~$10 | At Chicago ComEd ~$0.12/kWh |
| Workspace (philanthropytraders.com) | $7 | Soumit's existing personal-domain Workspace; Red Team Kitchen email forwards through it |
| Claude Max | $100 | Building this, so it's R&D |
| GitHub Copilot Pro+ | ~$39 | Build velocity |
| **GCP** | **TBD: $50/mo soft cap** | Kept ONLY for kill switch + billing-export. Will trend to $0 after migration. |
| **Total run-rate goal** | **~$160/mo** | Most of which is Claude Max |

The $2K Gemini incident on April 26-27 is the one that matters; everything else is small.

---

## Decisions: deferred

- **PS5 GPU hack**: yes, but only after Sony's anti-tamper allows a custom OS path on PS5 Pro. Don't cut into other deadlines. Track the homebrew scene.
- **Mac mini (`miniapple`)**: reserved Tailscale node, not in current plan. Could become a Litestream replica if Seratonin goes traveling.
- **`dreamer` (offline Windows, 36 d)**: probably defunct. Remove from Tailscale unless there's a reason to keep.

---

## When something breaks

Always check in this order:
1. `Get-Service rtk-cloudflared` — Running?
2. `tailscale status` — all expected nodes online?
3. The 5-line public-health probe (in `rtk-fleet` skill, section B.1)
4. Tail the relevant log under `C:\Users\soumi\.cortex\logs\` or `~/.cloudflared/logs/`

Don't restart cloudflared without first confirming it's actually the problem. The hosts file pin we added means even if cloudflared dies, Cloudflare Pages still serves the apex correctly.

---

*This file is the single source of truth for how Ascended Base operates. Keep it short. If you find yourself adding more than 30 lines, move the new content into a topic-specific doc and link from here.*
