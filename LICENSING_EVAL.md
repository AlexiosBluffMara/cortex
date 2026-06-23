# Licensing eval — what to pay for vs what's free

Audit of Ascended Base recurring services. Recommendation column is what to *actually* do.

## Paid-or-free decisions

| Product | Free tier covers you? | Paid worth it? | Recommendation |
| --- | --- | --- | --- |
| **Tailscale** | Yes — 100 devices, 3 users, 1 admin, Funnel included. You have 6 nodes / 1 user. | Premium ($6/user/mo) only buys ACL-as-code + custom domain. | **Stay free.** |
| **Parsec** | Yes for personal use. Soumit uses Mac→Seratonin already. | Warp ($9.99/mo) gets 4K@60 HDR, USB passthrough. Teams ($30/user/mo) for shared hosts. | **Stay free** unless gaming-from-Bloomington over public internet starts feeling laggy. Then pay $9.99/mo for Warp temporarily. |
| **Sunshine + Moonlight** | 100% free, open source. ARM64 + x86 + macOS + iOS + Android clients. | n/a | **Use it.** Replaces Parsec for the Pi entirely. |
| **Cloudflare** | Pages, DNS, Tunnel, Workers, R2 (after enable), AI Gateway, Vectorize, KV, D1 — all free at your scale. | Pro ($25/mo per zone) adds image resizing, mobile redirects, custom error pages. Workers Paid ($5/mo) lifts request caps to 10M/mo, adds Cron Triggers, Logpush. | **Stay free** through demo. Add **Workers Paid** ($5/mo) if Cortex demo gets busy enough that 100k req/day isn't enough. |
| **Google Cloud (GCP)** | $300 / 90 days new account credit (already used). Always-free tier ≈ 0 usable for ML. | Vertex Gemini was the $2K nuke. | **Trend to $0.** Keep only the kill-switch + budget infra. Trim everything else. |
| **GCP VPC** | Free up to 1 GB egress / region / month. | Cloud NAT, peering, etc. all charge. | **Don't use.** Everything we'd put behind VPC, Tailscale already does for free with better UX. |
| **GitHub** | Free for public repos. | Pro ($4/mo) for private repos + Codespaces hours. Copilot Pro+ ($39/mo) you already have. | **Stay free for public repos**, keep Copilot Pro+. |
| **Mercury (bank)** | $0 — checking, debit, Treasury, Vault all free for LLCs. | Mercury IO (their bookkeeping) = $20-40/mo paid. Mercury IOPlus pricing varies. | **Free Mercury checking is plenty**; Mercury IO add-on only if your CPA can't sync transactions another way. |
| **HuggingFace Inference API** | 1k calls / month free on most public models. | $9/mo Pro lifts cap, gives early access to private endpoints. | **Stay free** — last-resort fallback, you'll never hit 1k/mo if Ollama + Workers AI are healthy. |
| **OpenRouter** | $0 to use, you pay per token. Soumit set OpenRouter default to Gemma 4 26B free tier (per his own commit). | n/a | **Free tier suffices** for the Mercury auxiliary models he routes there. |
| **Cloudflare Stream** | $5 / 1k minutes stored + $1 / 1k delivered. | n/a (small) | **Skip** until there's actually a video product. |
| **Stripe** | 2.9% + $0.30 / transaction (US cards). No monthly fee. | Stripe Connect for marketplace splits, Stripe Tax, Stripe Sigma. | **Standard pricing** is fine for donations + subscriptions. |
| **Anthropic Claude** | n/a (paid product) | Max $100/mo (you have it). | **Keep** — this is the build-velocity multiplier. |
| **Domain (Cloudflare Registrar)** | At-cost ~$10/yr | n/a | **Done** |

**Total recurring spend recommended:** Claude Max ($100) + Copilot Pro+ ($39) + Domain (~$1/mo) = **~$140/mo**. Everything else free or pay-as-you-grow.

## Specifically about VPC / "private networking"

Soumit asked about "GCP VPC version." That's Google's enterprise networking layer — VPCs, subnets, Cloud NAT, VPN tunnels, private Google access. Costs add up fast: $36/mo for a Cloud NAT alone before traffic, $15/tunnel-hour for VPN, etc.

**You don't need any of it.** What you actually need:
- **Encrypted mesh between your machines** → Tailscale (free, WireGuard).
- **Private DNS** → Tailscale's MagicDNS (free).
- **Identity-keyed access** → Cloudflare Access or private LAN-only admin routes.
- **Public ingress** → Cloudflare Tunnel (free).

If any project ever genuinely needs GCP VPC (e.g. you're being paid by an enterprise customer who requires it), revisit. Until then, the Cloudflare + Tailscale stack is strictly better and $360/mo cheaper.

## When to spend money (the trigger conditions)

| Trigger | Then enable |
| --- | --- |
| Cortex demo > 100k requests/day | Workers Paid ($5/mo) |
| Cortex stream > 1k video minutes/month | Cloudflare Stream ($5/mo + per-min) |
| Demo lag from Bloomington over public internet > 100 ms p95 | Parsec Warp ($9.99/mo, monthly cancellable) |
| First paid customer of Ascended Base inference | Stripe Atlas / Tax ($50 one-time) and Mercury IO ($20-40/mo) for proper bookkeeping |
| First professor at ISU joins as a Tailscale user | Still free until 4th user; if more, Tailscale Premium @ $6/user/mo |
| Cortex demo ever genuinely needs Vertex AI | **Don't.** Use Workers AI or self-host. |
