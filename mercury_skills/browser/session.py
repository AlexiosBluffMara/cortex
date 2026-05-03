"""BrowserSession — persistent stealth browser context, one per conversation.

Why persistent: cookies, localStorage, and login state survive across requests
within a Mercury conversation. Each conversation gets its own profile dir so
sessions don't bleed into each other.

Why patchright: it's a drop-in replacement for `playwright.async_api` that
patches the most common anti-bot fingerprints (navigator.webdriver,
chrome.runtime, plugin list, headless detection markers).

Set MERCURY_BROWSER_ENGINE=camoufox to fall back to a custom Firefox build
when a target site is Chromium-fingerprinting heavily.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

# Drop-in stealth-patched Playwright. Same async API surface.
from patchright.async_api import async_playwright as _async_playwright
from patchright.async_api import Browser, BrowserContext, Page, Playwright

log = logging.getLogger(__name__)

DEFAULT_PROFILES_DIR = Path(os.environ.get(
    "MERCURY_BROWSER_PROFILES",
    str(Path.home() / ".mercury" / "browser_profiles"),
))
DEFAULT_PROFILES_DIR.mkdir(parents=True, exist_ok=True)

# Realistic viewport — close to median user, not the suspicious 1280×720.
DEFAULT_VIEWPORT = {"width": 1440, "height": 900}

# Reasonable modern Chrome UA (not the default Playwright one which contains
# "HeadlessChrome").  Updated for early-2026 Chrome release train.
DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
)

# Map of conversation_id -> live BrowserSession (in-process; restart loses them)
_LIVE_SESSIONS: dict[str, "BrowserSession"] = {}
_LOCK = asyncio.Lock()


class BrowserSession:
    """One persistent browser context, used across many tool calls."""

    def __init__(self, conversation_id: str):
        self.conversation_id = conversation_id
        self.profile_dir = DEFAULT_PROFILES_DIR / conversation_id
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._ctx: BrowserContext | None = None

    async def start(self) -> None:
        """Spin up Patchright Chromium with persistent profile."""
        if self._ctx is not None:
            return
        self._pw = await _async_playwright().start()
        # launch_persistent_context returns a BrowserContext directly — no
        # separate Browser object — and that's the recommended pattern for
        # stealth (matches a real user with one chrome window).
        self._ctx = await self._pw.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            headless=os.environ.get("MERCURY_BROWSER_HEADLESS", "1") != "0",
            viewport=DEFAULT_VIEWPORT,
            user_agent=DEFAULT_UA,
            args=[
                # Remove the "Chrome is being controlled by automated test software" bar.
                "--disable-blink-features=AutomationControlled",
                # Spoof a non-headless display.
                "--window-size=1440,900",
            ],
        )
        log.info("[browser] started session %s (profile=%s)",
                 self.conversation_id, self.profile_dir)

    async def page(self) -> Page:
        """Return a page (creates one if none exist yet)."""
        if self._ctx is None:
            await self.start()
        assert self._ctx is not None
        pages = self._ctx.pages
        if pages:
            return pages[0]
        return await self._ctx.new_page()

    async def goto(self, url: str, wait_until: str = "networkidle", timeout: float = 30.0) -> Page:
        page = await self.page()
        try:
            await page.goto(url, wait_until=wait_until, timeout=int(timeout * 1000))
        except Exception as exc:
            log.warning("[browser] goto %s slow/failed: %s", url, exc)
        return page

    async def screenshot(self, url: str, full_page: bool = True, wait_for_selector: str | None = None) -> bytes:
        page = await self.goto(url)
        if wait_for_selector:
            try:
                await page.wait_for_selector(wait_for_selector, timeout=15_000)
            except Exception as exc:
                log.warning("[browser] wait_for_selector %s missed: %s", wait_for_selector, exc)
        return await page.screenshot(full_page=full_page, type="png")

    async def text(self, url: str, selector: str | None = None) -> str:
        page = await self.goto(url)
        if selector:
            el = await page.query_selector(selector)
            return (await el.inner_text()) if el else ""
        return await page.evaluate("() => document.body.innerText")

    async def html(self, url: str) -> str:
        page = await self.goto(url)
        return await page.content()

    async def close(self) -> None:
        try:
            if self._ctx is not None:
                await self._ctx.close()
            if self._pw is not None:
                await self._pw.stop()
        except Exception as exc:
            log.warning("[browser] close error: %s", exc)
        finally:
            self._ctx = None
            self._pw = None


async def get_or_create_session(conversation_id: str = "default") -> BrowserSession:
    """Return the live session for this conversation, creating + starting it if needed."""
    async with _LOCK:
        sess = _LIVE_SESSIONS.get(conversation_id)
        if sess is None:
            sess = BrowserSession(conversation_id)
            _LIVE_SESSIONS[conversation_id] = sess
        if sess._ctx is None:
            await sess.start()
        return sess


async def close_session(conversation_id: str) -> None:
    async with _LOCK:
        sess = _LIVE_SESSIONS.pop(conversation_id, None)
        if sess is not None:
            await sess.close()
