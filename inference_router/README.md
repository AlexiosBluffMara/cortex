# inference_router

A 3-tier inference router built for Cortex after a $2K Gemini API bill. The
router preferentially sends every request to the local Ollama instance running
on the RTX 5090 (free, no per-token cost). When Ollama is unhealthy, requests
fall through to Cloudflare Workers AI, then to the HuggingFace Inference API.
There are no retries within a single provider — a failure at one tier moves on
to the next, and only when all three fail does the router raise. Gemini (and
the Google AI SDK in general) is intentionally not in the chain. Start the
FastAPI server with `python -m inference_router.server` (binds 127.0.0.1:8765),
or call `await inference_router.router.generate(prompt, model="gemma3:12b")`
directly from Python. The CLI exposes `POST /v1/generate` for prompts and
`GET /healthz` for per-provider health booleans.

Credentials are read from environment variables first, then from on-disk
fallback files: `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID` (or
`~/.cloudflare/credentials`), `HF_TOKEN` (or `~/.huggingface/token`), and
`OLLAMA_URL` (defaults to `http://localhost:11434`). The default model is
`gemma3:12b`; internal canonical names (`gemma3:12b`, `gemma3:27b`,
`gemma3:e4b`, etc.) are mapped to the right Workers AI ids
(`@cf/google/gemma-3-12b-it` style) and HuggingFace repo ids
(`google/gemma-3-12b-it` style) automatically. All Ollama calls are serialized
through a single global `asyncio.Lock` so we don't thrash the 5090's KV cache;
Workers AI and HF calls bypass that queue and run concurrently since they're
rate-limited externally.
