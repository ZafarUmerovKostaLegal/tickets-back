"""Print an invoice preview (self-contained HTML) to PDF with headless Chromium.

The page is rendered exactly as the browser preview: same HTML, same CSS, fonts embedded as
data URIs. A Content-Security-Policy forbids scripts and every network request is blocked, so
the HTML cannot reach internal services.
"""

from __future__ import annotations

import asyncio
import re

MAX_HTML_BYTES = 40 * 1024 * 1024
_LIMIT = asyncio.Semaphore(2)
_CSP = "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src data: 'unsafe-inline'; script-src 'none'\">"
_HEAD = re.compile(r"<head[^>]*>", re.IGNORECASE)


class HtmlPdfUnavailable(RuntimeError):
    pass


def sanitize_html(html: str) -> str:
    """Put the CSP before anything else in <head>."""
    match = _HEAD.search(html)
    if match:
        return html[: match.end()] + _CSP + html[match.end():]
    return f"<!doctype html><html><head>{_CSP}</head><body>{html}</body></html>"


async def _block_network(route) -> None:
    if route.request.url.startswith("data:"):
        await route.continue_()
    else:
        await route.abort()


async def render_html_to_pdf(html: str) -> bytes:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise HtmlPdfUnavailable("Chromium недоступен на сервере") from exc
    async with _LIMIT:
        async with async_playwright() as runner:
            browser = await runner.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage", "--font-render-hinting=none"])
            try:
                context = await browser.new_context(viewport={"width": 794, "height": 1123})
                page = await context.new_page()
                await page.route("**/*", _block_network)
                await page.emulate_media(media="screen")
                await page.set_content(sanitize_html(html), wait_until="load", timeout=60_000)
                await page.evaluate("() => document.fonts.ready.then(() => true)")
                return await page.pdf(
                    format="A4",
                    print_background=True,
                    prefer_css_page_size=True,
                    margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
                )
            finally:
                await browser.close()
