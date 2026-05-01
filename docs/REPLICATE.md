# Replicate Cortex on your own GCP project

`scripts/replicate.sh` (and its PowerShell sibling `scripts/replicate.ps1`)
spin up a complete copy of Cortex's cloud infrastructure on a GCP project
you control — so anyone can fork the repo, run one command, and end up
with their own working stack.

This is aimed at:

- **Academic researchers** who want to host Cortex on a university-owned GCP project.
- **HPC operators / lab admins** at ACCESS-CI partner sites.
- **Independent ML engineers** who want a private deploy without messing with our LLC's project.

## What gets created

| Resource | Where | Why |
|---|---|---|
| GCP project | `${PROJECT_ID}` | Container for everything else (created if missing). |
| Enabled APIs | `iamcredentials`, `cloudresourcemanager`, `run`, `storage`, `secretmanager`, `cloudbuild`, `firestore`, `firebase`, `artifactregistry` | Required by the deploy workflow. |
| Workload Identity Pool + Provider | `github-actions-pool` / `github-provider` | Lets GitHub Actions authenticate via OIDC — no service-account keys (org policy may block keys anyway). |
| Service account | `github-actions-deploy@${PROJECT_ID}.iam.gserviceaccount.com` | The identity Cloud Run revisions and CI jobs run as. |
| IAM role grants | 8 roles on the SA above | `run.admin`, `storage.admin`, `secretmanager.secretAccessor`, `cloudbuild.builds.editor`, `iam.serviceAccountUser`, `datastore.user`, `firebase.admin`, `artifactregistry.admin`. |
| Artifact Registry repo | `cortex` in your region | Holds the webapp / worker / relay container images. |
| Secret Manager secrets | `gcp-inference-token`, `hf-token` | Runtime secrets (placeholders if you don't supply values). |
| GCS bucket | `cortex-public-scans` (preferred) or `cortex-public-scans-${PROJECT_ID}` (fallback) | Stores uploaded media + thumbnails. Names are global; the script falls back to a project-suffixed name if the preferred one is taken. |
| Firestore database | `(default)`, Native mode | Scan job records. |
| Firebase project link | `${PROJECT_ID}` | Required for Firebase Hosting deploys. |
| Firebase Hosting site | `${PROJECT_ID}` | Static frontend (`/`, rewrites to Cloud Run). |
| GitHub Actions secrets | `GCP_WIF_PROVIDER`, `GCP_SA_EMAIL`, optional `OLLAMA_URL`, `CLOUDFLARE_TUNNEL_URL`, `GEMINI_API_KEY` | Read by `.github/workflows/deploy.yml`. |

## Prerequisites

- A Google account with **Project Creator** or higher on a billing account (free tier is fine for most workloads).
- A **fork** of `AlexiosBluffMara/cortex` at `${YOUR_GH_USER}/cortex` — the WIF binding is repo-scoped to your fork.
- These CLIs installed on your machine:
  - `gcloud` — [Google Cloud SDK installer](https://cloud.google.com/sdk/docs/install)
  - `gh` — GitHub CLI (`winget install GitHub.cli`, `brew install gh`, etc.)
  - `firebase` — `npm install -g firebase-tools`
  - `cloudflared` — only needed if you want a custom domain via Cloudflare Tunnel; **optional**.

## Run it

### macOS / Linux / Windows (Git Bash, WSL)

```bash
git clone https://github.com/YOUR_GH_USER/cortex.git
cd cortex
bash scripts/replicate.sh
```

The script will prompt for the GCP project ID, your GitHub username, region
(default `us-central1`), and optionally a HuggingFace token. Pass them as
flags for an unattended run:

```bash
bash scripts/replicate.sh \
  --project my-cortex-prod \
  --gh-user my-github-handle \
  --region us-central1 \
  --hf-token hf_xxxxxxxxxxxxxxxxxxx
```

### Windows PowerShell

```powershell
.\scripts\replicate.ps1 -ProjectId my-cortex-prod -GhUser my-github-handle
```

Add `-NonInteractive` to skip prompts when scripting.

## Expected runtime

About **10–15 minutes** end-to-end on a fresh GCP project, dominated by:

- Project + API enablement: 1–2 min
- WIF + IAM bindings: 30 s
- Firestore database creation: 30–90 s (Google's side, sometimes longer)
- **The Firebase ToS pause** is unbounded — that's a manual step in
  [console.firebase.google.com](https://console.firebase.google.com/). Do
  the click-through, then hit Enter to continue. Expect ~1 min.
- After the script finishes, the actual deploy on `git push origin main`
  takes another ~12 min for the first run (cold cache for the worker
  image; subsequent pushes are 2–4 min thanks to layer caching).

## Manual steps the script can't automate

These need browser interaction or out-of-band approval:

1. **Linking billing.** If you create a brand-new GCP project, billing
   isn't attached. The script pauses and gives you the console URL —
   click through, then continue.
2. **Firebase Terms of Service.** Required once per project. The
   `firebase projects:addfirebase` call from CI fails with HTTP 404 on a
   project that hasn't accepted the Firebase ToS in the console. Visit
   [console.firebase.google.com](https://console.firebase.google.com/),
   click **Add project**, pick your existing GCP project, accept ToS,
   finish the wizard. The script does this idempotently from then on.
3. **Cloudflare Tunnel cert.** `cloudflared tunnel login` opens a
   browser to pick a Cloudflare zone. Skip this entirely if you're
   happy with the default `*.run.app` URL — the deploy still works,
   the relay just doesn't proxy through your tunnel. If you want a
   custom domain like `cortex.yourdomain.com`, run the three
   `cloudflared` commands the script prints, then set
   `CLOUDFLARE_TUNNEL_URL` in your GitHub secrets.
4. **HuggingFace token for TRIBE v2.** If you don't supply one, the
   `hf-token` Secret Manager entry is created with a placeholder and
   the optional cloud worker won't be able to download TRIBE v2
   weights. The webapp still runs. Add the real token any time:
   `echo -n hf_xxx | gcloud secrets versions add hf-token --data-file=- --project=YOUR_PROJECT`.

## After the script finishes

1. Make sure your local clone's `origin` points at *your* fork:
   ```bash
   git remote set-url origin https://github.com/YOUR_GH_USER/cortex.git
   ```
2. Push to `main`:
   ```bash
   git push origin main
   ```
3. Watch the deploy:
   ```bash
   gh run watch --repo YOUR_GH_USER/cortex
   ```
4. After the first deploy, the cloud worker (if it built — see note in
   `deploy.yml`) is at `https://cortex-tribe-worker-XXXX-uc.a.run.app`.
   Set its URL as the `GCP_INFERENCE_ENDPOINT` GitHub secret so the
   webapp can route to it:
   ```bash
   gh secret set GCP_INFERENCE_ENDPOINT --repo YOUR_GH_USER/cortex --body https://cortex-tribe-worker-XXXX-uc.a.run.app
   ```
5. Open `https://YOUR_PROJECT_ID.web.app` — that's your Firebase Hosting URL.

## Costs

GCP free tier covers most of this: Cloud Run scales to zero between
requests, Artifact Registry storage is small (a few GB), Firestore and
Secret Manager have generous free quotas, GCS is pennies for the scan
bucket. The expensive items only kick in if you actually use the cloud
GPU worker (L4 GPU, ~$0.70/hr while running). Local-5090-via-tunnel is
the primary path; the cloud worker is optional redundancy.

## Tearing it down

```bash
bash scripts/replicate-cleanup.sh --project my-cortex-prod
# or, also delete the GCP project itself:
bash scripts/replicate-cleanup.sh --project my-cortex-prod --delete-project
# also remove GitHub Actions secrets:
bash scripts/replicate-cleanup.sh --project my-cortex-prod --gh-repo YOUR_GH_USER/cortex
```

This deletes Cloud Run services, AR repo + images, secrets, the GCS
bucket, Firestore database (best-effort — Firestore deletion sometimes
needs a manual confirm in the console), Firebase Hosting site, the
service account, and the WIF pool/provider. WIF pools enter a 30-day
soft-delete; `gcloud iam workload-identity-pools undelete` restores them.

## Idempotency

The script is safe to re-run. Every step uses the
"`describe` → if-not-exists → create" pattern, so a second run no-ops
on existing resources and only fills in anything that's missing. That
means it's also safe to use as a **drift fixer** if a resource gets
deleted by hand.

## Where to ask for help

- File an issue: [github.com/AlexiosBluffMara/cortex/issues](https://github.com/AlexiosBluffMara/cortex/issues)
- The script's diagnostic output lives in `/tmp/cortex-replicate-*.log`
  (Linux/macOS) or `%TEMP%\cortex-replicate-*.log` (Windows).
- WIF + Firebase issues are the two most common stumbling points; the
  script's `[WARN]` messages link to the exact console pages where you
  can resolve them.
