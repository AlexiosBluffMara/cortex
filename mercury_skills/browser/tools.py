"""High-level tool functions Mercury exposes to its LLM tool-use loop.

These wrap BrowserSession in single-shot async calls returning serializable
results (str / bytes / dict). They reuse the per-conversation persistent
context so cookies and login flow naturally across calls.
"""
from __future__ import annotations

import base64
import logging
from typing import Any

from .session import get_or_create_session, BrowserSession

log = logging.getLogger(__name__)


async def screenshot_url(
    url: str,
    *,
    conversation_id: str = "default",
    full_page: bool = True,
    wait_for_selector: str | None = None,
) -> dict[str, Any]:
    """Screenshot a URL. Returns {png_b64, byte_size, url}.

    Reuses the conversation's persistent browser session.
    """
    sess = await get_or_create_session(conversation_id)
    png = await sess.screenshot(url, full_page=full_page, wait_for_selector=wait_for_selector)
    return {
        "url": url,
        "byte_size": len(png),
        "png_b64": base64.b64encode(png).decode(),
    }


async def extract_text(
    url: str,
    *,
    selector: str | None = None,
    conversation_id: str = "default",
    max_chars: int = 20_000,
) -> dict[str, Any]:
    sess = await get_or_create_session(conversation_id)
    text = await sess.text(url, selector=selector)
    truncated = len(text) > max_chars
    return {
        "url": url,
        "selector": selector,
        "text": text[:max_chars],
        "char_count": len(text),
        "truncated": truncated,
    }


async def extract_html(
    url: str,
    *,
    conversation_id: str = "default",
    max_chars: int = 100_000,
) -> dict[str, Any]:
    sess = await get_or_create_session(conversation_id)
    html = await sess.html(url)
    return {
        "url": url,
        "html": html[:max_chars],
        "char_count": len(html),
        "truncated": len(html) > max_chars,
    }


async def click_selector(
    url: str,
    selector: str,
    *,
    conversation_id: str = "default",
) -> dict[str, Any]:
    sess = await get_or_create_session(conversation_id)
    page = await sess.goto(url)
    el = await page.query_selector(selector)
    if el is None:
        return {"ok": False, "error": "selector not found", "selector": selector, "url": url}
    await el.click()
    return {"ok": True, "url": page.url, "title": await page.title()}


async def fill_form(
    url: str,
    fields: dict[str, str],
    *,
    submit_selector: str | None = None,
    conversation_id: str = "default",
) -> dict[str, Any]:
    """Fill a form. `fields` is a dict of CSS selector -> value.

    If submit_selector is given, clicks it after filling.
    """
    sess = await get_or_create_session(conversation_id)
    page = await sess.goto(url)
    for sel, val in fields.items():
        try:
            await page.fill(sel, val)
        except Exception as exc:
            return {"ok": False, "error": f"fill failed for {sel}: {exc}"}
    if submit_selector:
        try:
            await page.click(submit_selector)
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception as exc:
            return {"ok": False, "error": f"submit failed: {exc}"}
    return {"ok": True, "url": page.url, "title": await page.title()}


async def wait_for_selector(
    url: str,
    selector: str,
    *,
    timeout: float = 15.0,
    conversation_id: str = "default",
) -> dict[str, Any]:
    sess = await get_or_create_session(conversation_id)
    page = await sess.goto(url)
    try:
        await page.wait_for_selector(selector, timeout=int(timeout * 1000))
        return {"ok": True, "selector": selector}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


async def download_pdf(
    url: str,
    *,
    save_to: str,
    conversation_id: str = "default",
) -> dict[str, Any]:
    """Navigate to a URL and save the response as PDF (Chromium print)."""
    sess = await get_or_create_session(conversation_id)
    page = await sess.goto(url, wait_until="networkidle")
    await page.pdf(path=save_to, format="A4")
    return {"ok": True, "saved_to": save_to, "url": page.url}
