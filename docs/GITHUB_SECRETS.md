# GitHub Secrets inventory

All secrets are set at **Settings → Secrets and variables → Actions → New repository secret**.
Never commit actual values. The WIF provider and SA email can be copied from the `setup-wif.sh` output.

---

## Cortex (`AlexiosBluffMara/cortex`)

### GCP authentication — via Workload Identity Federation (no long-lived keys)

| Secret | Value source | Used by |
|---|---|---|
| `GCP_WIF_PROVIDER` | Output of `gcp/setup-wif.sh` | All deploy jobs |
| `GCP_SA_EMAIL` | Output of `gcp/setup-wif.sh` | All deploy jobs |

### GCP Secret Manager secrets (set these in GCP, not GitHub)

These are injected **at runtime** by Cloud Run from Secret Manager — you don't put them in GitHub.

| Secret Manager name | What it is | Cloud Run service |
|---|---|---|
| `gcp-inference-token` | GCP identity token for tribe-worker auth | webapp + worker |
| `hf-token` | HuggingFace access token (TRIBE v2 weights) | tribe-worker |

Set them once:
```bash
echo -n "hf_xxxx" | gcloud secrets create hf-token --data-file=- --project=abm-isu
echo -n "hf_xxxx" | gcloud secrets create gcp-inference-token --data-file=- --project=abm-isu
```

### GitHub Secrets (injected as env vars at deploy time)

| Secret | Example value | Used in |
|---|---|---|
| `OLLAMA_URL` | `https://ollama.redteamkitchen.com` | webapp Cloud Run |
| `CLOUDFLARE_TUNNEL_URL` | `https://ollama.redteamkitchen.com` | relay Cloud Run |
| `GCP_INFERENCE_ENDPOINT` | `https://cortex-tribe-xxx-uc.a.run.app` | webapp → worker calls |
| `GEMINI_API_KEY` | `AIza...` | relay (narration fallback when 5090 is offline) |
| `FIREBASE_SA_JSON` | Full JSON of Firebase service account | Firebase Hosting deploy |

**Getting `FIREBASE_SA_JSON`:**
```bash
gcloud iam service-accounts keys create /tmp/firebase-sa.json \
  --iam-account=github-actions-deploy@abm-isu.iam.gserviceaccount.com
# Paste the contents of /tmp/firebase-sa.json as the secret value.
# Delete the key file after.
rm /tmp/firebase-sa.json
```

> NOTE: Firebase Hosting deploy uses a JSON service account key because
> `firebase-tools` doesn't support WIF yet. Use the same SA created by setup-wif.sh.

---

## Mercury (`AlexiosBluffMara/mercury`)

### GCP authentication (same pool, same SA — the deploy workflow uses ghcr.io, not GCR)

Mercury doesn't deploy to GCP Cloud Run, so it only needs the WIF provider if you add a GCP step.
Currently mercury images are pushed to **ghcr.io**, which uses `GITHUB_TOKEN` automatically.

### Self-hosted runner requirement

The `deploy` job in `deploy.yml` runs on `[self-hosted, mercury]`.

To register a runner on the machine that hosts Mercury:
```bash
# On the target machine (5090 workstation or GCP VM):
mkdir ~/actions-runner && cd ~/actions-runner
# Download the runner from:
# https://github.com/AlexiosBluffMara/mercury/settings/actions/runners/new
# Follow the instructions, then label it:
./config.sh --labels "self-hosted,mercury"
./run.sh   # or: sudo ./svc.sh install && sudo ./svc.sh start
```

### Mercury-specific GitHub Secrets

| Secret | Value | Used by |
|---|---|---|
| `MERCURY_COMPOSE_DIR` | Absolute path to mercury repo on host (e.g. `D:/mercury`) | deploy job |

Mercury's `.env` (API keys, NOUS_API_KEY, etc.) lives on the host at `~/.hermes/.env` and is **never** in GitHub Secrets or the repo. The docker-compose mounts `~/.hermes` as `/opt/data`, so the container reads keys from there.

---

## Secret rotation checklist

- `hf-token` → rotate at HuggingFace when a team member leaves
- `gcp-inference-token` → this is a short-lived GCP identity token; the relay generates it on-demand via `gcloud auth print-identity-token` at startup
- `GEMINI_API_KEY` → rotate at Google AI Studio
- `FIREBASE_SA_JSON` → this is a long-lived key; consider rotating every 90 days

---

## How to add a new secret

```bash
# GitHub CLI method (fastest):
gh secret set MY_SECRET_NAME --repo AlexiosBluffMara/cortex
# It will prompt for the value without echoing it.
```
