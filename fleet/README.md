# Ascended Base — fleet control scripts

Wind-up, shutdown, and failover scripts for the two production nodes:

| Node          | OS        | Tailscale name | Role                                              |
|---------------|-----------|----------------|---------------------------------------------------|
| **Seratonin** | Windows 11 | `seratonin`    | Primary — RTX 5090 (TRIBE v2 + Gemma + everything) |
| **Big Apple** | macOS     | `big-apple`    | Secondary — M4 Max 48 GB (narration overflow + standby for Mercury / Cortex narration paths) |

Both nodes are Tailscale-connected; either can SSH the other (SSH key `id_ed25519`).

## Wind-up / shutdown — single-machine

| Action  | Run on Seratonin (PowerShell)             | Run on Big Apple (zsh)               |
|---------|-------------------------------------------|--------------------------------------|
| Up      | `pwsh fleet/up-seratonin.ps1`             | `bash fleet/up-bigapple.sh`          |
| Down    | `pwsh fleet/down-seratonin.ps1`           | `bash fleet/down-bigapple.sh`        |
| Status  | `bash fleet/status.sh`                    | `bash fleet/status.sh`               |

## Cross-machine — bring the *other* one up/down via SSH

From either node, run:

| Action                         | Command                                        |
|--------------------------------|------------------------------------------------|
| Bring Seratonin up from Big Apple | `bash fleet/remote-up.sh seratonin`         |
| Bring Big Apple up from Seratonin | `bash fleet/remote-up.sh bigapple`          |
| Shut Seratonin down from Big Apple | `bash fleet/remote-down.sh seratonin`      |
| Shut Big Apple down from Seratonin | `bash fleet/remote-down.sh bigapple`       |

## Failover modes

### Active failover (default)
Both nodes serve narration; the inference router on Seratonin round-robins between them and fails over per-request to OpenRouter free tier on error. **No manual action needed** — this is the default state when both are up.

```bash
bash fleet/failover-active.sh   # ensure both nodes up; reset router config
```

### Passive failover to Big Apple (gaming mode)
Seratonin is gaming. Cortex backend stays on Seratonin (TRIBE needs CUDA), but **all narration is forced to Big Apple** and Mercury moves to Big Apple. If the user wants to fully migrate the public URL too, they flip the Tailscale Funnel manually (see `failover-funnel.md`).

```bash
bash fleet/failover-to-bigapple.sh
```

What this does, in order:
1. SSH to Big Apple, ensure Ollama is running with Gemma 4 E4B+26B+31B preloaded
2. SSH to Big Apple, start Mercury gateway + dashboard there (`mercury_remote_up.sh`)
3. On Seratonin, restart the inference router with Big Apple as PRIMARY backend (Seratonin Ollama removed from pool until gaming ends)
4. On Seratonin, stop Mercury locally to free CPU
5. Cortex backend stays on Seratonin (TRIBE needs the 5090)
6. Verify all four narrations still complete via end-to-end smoke test

### Passive failover back to Seratonin (gaming over)
```bash
bash fleet/failover-to-seratonin.sh
```

Reverses every step above.

## Critical secrets / bindings (NOT in scripts — must be present)
- `~/.hermes/.env` on each node (OpenRouter key, Discord token)
- `~/.cloudflare/credentials` on Seratonin only (Wrangler deploys)
- Tailscale auth on both nodes

## Tailscale Funnel — single-machine constraint
Tailscale Funnel can be enabled on **only one machine per Tailnet at a time** (the public URL `seratonin.scylla-betta.ts.net` is bound to Seratonin's identity). To physically move the public URL to Big Apple:
1. On Seratonin: `tailscale funnel reset`
2. On Big Apple: `tailscale funnel --bg 5173`
3. Public URL becomes `big-apple.scylla-betta.ts.net`

Update the marketing-site links if you do this — the URL changes.
