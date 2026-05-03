"""mercury_skills.browser — stealth browser automation tools for Mercury.

Default engine: Patchright (patched Playwright Chromium) — handles ~90% of
modern anti-bot fingerprinting (navigator.webdriver, plugin enumeration,
Chrome runtime, devtools heuristics).

Fallback engine: Camoufox (custom Firefox build with rotating realistic
fingerprints) — slower to spin up but defeats Chromium-specific detection.

Tools exported here are designed to be exposed to Mercury's tool-use loop
through the MCP / skill registration layer.
"""
from .session import BrowserSession, get_or_create_session, close_session
from .tools import (
    screenshot_url,
    extract_text,
    extract_html,
    fill_form,
    click_selector,
    wait_for_selector,
    download_pdf,
)

__all__ = [
    "BrowserSession",
    "get_or_create_session",
    "close_session",
    "screenshot_url",
    "extract_text",
    "extract_html",
    "fill_form",
    "click_selector",
    "wait_for_selector",
    "download_pdf",
]
