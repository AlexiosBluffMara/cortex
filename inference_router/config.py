"""Configuration for the 3-tier inference router.

Loads provider credentials from environment variables, falling back to the
on-disk credential files Soumit keeps under his home directory:

  ~/.cloudflare/credentials   — INI/key=value with CLOUDFLARE_API_TOKEN and
                                CLOUDFLARE_ACCOUNT_ID
  ~/.huggingface/token        — single-line file containing the HF token

No Gemini / Google AI SDK is touched here on purpose — the whole point of this
router is to keep paid frontier-model APIs out of the hot path.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _read_token_file(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def _read_kv_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


@dataclass(frozen=True)
class RouterConfig:
    cloudflare_api_token: str | None
    cloudflare_account_id: str | None
    hf_token: str | None
    # Single-backend (legacy, kept for back-compat with tests + simple deploys)
    ollama_url: str
    # Multi-backend list, ordered. First entry is preferred. Each is "http://host:port".
    # Sourced from OLLAMA_BACKENDS env var (comma-separated) or falls back to [ollama_url].
    ollama_backends: tuple[str, ...]
    # BitNet (RPi 5 ternary node). None = disabled.
    bitnet_url: str | None
    ollama_model_preference: str
    openrouter_api_key: str | None

    @property
    def has_cloudflare(self) -> bool:
        return bool(self.cloudflare_api_token and self.cloudflare_account_id)

    @property
    def has_hf(self) -> bool:
        return bool(self.hf_token)

    @property
    def has_bitnet(self) -> bool:
        return bool(self.bitnet_url)

    @property
    def has_openrouter(self) -> bool:
        return bool(self.openrouter_api_key)


def load_config() -> RouterConfig:
    cf_token = os.environ.get("CLOUDFLARE_API_TOKEN")
    cf_account = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    if not (cf_token and cf_account):
        cf_path = Path.home() / ".cloudflare" / "credentials"
        kv = _read_kv_file(cf_path)
        cf_token = cf_token or kv.get("CLOUDFLARE_API_TOKEN") or kv.get("api_token")
        cf_account = cf_account or kv.get("CLOUDFLARE_ACCOUNT_ID") or kv.get("account_id")

    hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    if not hf_token:
        hf_token = _read_token_file(Path.home() / ".huggingface" / "token")

    # OpenRouter — free Gemma 4 26B/31B fallback (200 req/day, $0)
    or_key = os.environ.get("OPENROUTER_API_KEY")
    if not or_key:
        or_key = _read_token_file(Path.home() / ".openrouter" / "key")
    if not or_key:
        # Check Mercury/Hermes env file
        hermes_env = Path.home() / ".hermes" / ".env"
        if hermes_env.exists():
            for line in hermes_env.read_text().splitlines():
                if line.startswith("OPENROUTER_API_KEY="):
                    or_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break

    ollama_url = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
    ollama_pref = os.environ.get("OLLAMA_MODEL_PREFERENCE", "gemma4:e4b")

    # OLLAMA_BACKENDS = "http://localhost:11434,http://big-apple:11434"
    raw_backends = os.environ.get("OLLAMA_BACKENDS", "").strip()
    if raw_backends:
        backends = tuple(b.strip().rstrip("/") for b in raw_backends.split(",") if b.strip())
    else:
        backends = (ollama_url,)

    bitnet_url = os.environ.get("BITNET_URL", "").strip().rstrip("/") or None

    return RouterConfig(
        cloudflare_api_token=cf_token,
        cloudflare_account_id=cf_account,
        hf_token=hf_token,
        openrouter_api_key=or_key,
        ollama_url=ollama_url,
        ollama_backends=backends,
        bitnet_url=bitnet_url,
        ollama_model_preference=ollama_pref,
    )
