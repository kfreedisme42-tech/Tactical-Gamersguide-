"""
Vision Scraper — Universal AI-powered offer extraction.

Uses Playwright stealth browser to screenshot any offer wall,
then sends the image to GPT-4o Vision for structured extraction.
Works on ANY platform without hard-coded selectors.
"""

import asyncio
import base64
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

import aiohttp
from playwright.async_api import async_playwright, Page, Browser

logger = logging.getLogger(__name__)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_VISION_MODEL", "gpt-4o")
OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"

DEFAULT_VIEWPORT = {"width": 1280, "height": 900}
DEFAULT_WAIT_MS = 3000
DEFAULT_SCROLL_PAGES = 2

EXTRACTION_PROMPT = """You are an expert data-extraction assistant.
Analyse the screenshot of a Play-to-Earn / GPT / offer-wall page.

Return a JSON array of objects, one per offer visible on screen.
Each object MUST have these fields (use null if unknown):

{
  "title": "string — short name of the offer or game",
  "platform": "string — the site or app hosting the offer",
  "payout_usd": number or null,
  "currency_type": "string — USD, coins, points, crypto, etc.",
  "estimated_minutes": number or null,
  "difficulty": "string — easy / medium / hard / unknown",
  "category": "string — survey, game, signup, video, download, casino, shopping, other",
  "requirements": "string — brief description of completion requirements",
  "url": "string or null — deeplink if visible",
  "confidence": number 0-100
}

Rules:
- Extract EVERY visible offer; do not summarise or skip.
- Use the EXACT payout number shown; do not convert currencies.
- If a field is ambiguous, set confidence lower.
- Return ONLY the JSON array, no commentary.
"""


@dataclass
class VisionOffer:
    """Single offer extracted by vision."""

    title: str
    platform: str | None = None
    payout_usd: float | None = None
    currency_type: str | None = None
    estimated_minutes: float | None = None
    difficulty: str | None = None
    category: str | None = None
    requirements: str | None = None
    url: str | None = None
    confidence: int = 50


# ---------------------------------------------------------------------------
# Browser helpers
# ---------------------------------------------------------------------------

async def _launch_browser() -> Browser:
    """Launch a headless Chromium with stealth flags."""
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    )
    return browser


async def _apply_stealth(page: Page) -> None:
    """Inject minimal stealth patches."""
    await page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => false});
        Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
        Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
        window.chrome = { runtime: {} };
    """)


async def _scroll_and_capture(
    page: Page,
    scroll_pages: int = DEFAULT_SCROLL_PAGES,
    wait_ms: int = DEFAULT_WAIT_MS,
) -> list[bytes]:
    """Scroll through the page and capture screenshots of each viewport."""
    screenshots: list[bytes] = []

    # Capture initial viewport
    await page.wait_for_timeout(wait_ms)
    screenshots.append(await page.screenshot(type="png"))

    for _ in range(scroll_pages):
        await page.evaluate("window.scrollBy(0, window.innerHeight)")
        await page.wait_for_timeout(1500)
        screenshots.append(await page.screenshot(type="png"))

    return screenshots


async def _execute_pre_actions(page: Page, actions: list[dict]) -> None:
    """Run optional pre-capture actions (click, type, wait)."""
    for action in actions:
        kind = action.get("action", "")
        selector = action.get("selector", "")
        value = action.get("value", "")

        if kind == "click" and selector:
            try:
                await page.click(selector, timeout=5000)
            except Exception as exc:
                logger.warning("Pre-action click failed: %s", exc)

        elif kind == "type" and selector:
            try:
                await page.fill(selector, value, timeout=5000)
            except Exception as exc:
                logger.warning("Pre-action type failed: %s", exc)

        elif kind == "wait":
            ms = int(value) if value else 1000
            await page.wait_for_timeout(ms)

        elif kind == "select" and selector:
            try:
                await page.select_option(selector, value, timeout=5000)
            except Exception as exc:
                logger.warning("Pre-action select failed: %s", exc)


# ---------------------------------------------------------------------------
# OpenAI Vision call
# ---------------------------------------------------------------------------

async def _call_vision(
    screenshots: list[bytes],
    prompt: str = EXTRACTION_PROMPT,
) -> list[dict[str, Any]]:
    """Send screenshots to GPT-4o Vision and parse the JSON response."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set — cannot run vision extraction.")

    image_parts = []
    for img_bytes in screenshots:
        b64 = base64.b64encode(img_bytes).decode("ascii")
        image_parts.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "high"},
            }
        )

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                *image_parts,
            ],
        }
    ]

    payload = {
        "model": OPENAI_MODEL,
        "messages": messages,
        "max_tokens": 4096,
        "temperature": 0.1,
    }

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(
            OPENAI_API_URL, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=120)
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"OpenAI API error {resp.status}: {body[:500]}")
            data = await resp.json()

    raw_text = data["choices"][0]["message"]["content"]
    return _parse_vision_response(raw_text)


def _parse_vision_response(raw: str) -> list[dict[str, Any]]:
    """Parse LLM response, handling markdown fencing and partial JSON."""
    text = raw.strip()

    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]  # remove opening fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # Try direct parse
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return [result]
    except json.JSONDecodeError:
        pass

    # Try to find JSON array in text
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # Try to find JSON object
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            obj = json.loads(match.group())
            return [obj]
        except json.JSONDecodeError:
            pass

    logger.error("Failed to parse vision response: %s", text[:200])
    return []


def _raw_to_vision_offers(raw_offers: list[dict]) -> list[VisionOffer]:
    """Convert raw dicts from the LLM into VisionOffer dataclasses."""
    results = []
    for item in raw_offers:
        try:
            vo = VisionOffer(
                title=str(item.get("title", "Unknown")),
                platform=item.get("platform"),
                payout_usd=_safe_float(item.get("payout_usd")),
                currency_type=item.get("currency_type"),
                estimated_minutes=_safe_float(item.get("estimated_minutes")),
                difficulty=item.get("difficulty", "unknown"),
                category=item.get("category", "other"),
                requirements=item.get("requirements"),
                url=item.get("url"),
                confidence=int(item.get("confidence", 50)),
            )
            results.append(vo)
        except Exception as exc:
            logger.warning("Skipping malformed vision offer: %s — %s", item, exc)
    return results


def _safe_float(val: Any) -> float | None:
    """Safely convert a value to float."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def scan_url(
    url: str,
    *,
    viewport: dict | None = None,
    wait_ms: int = DEFAULT_WAIT_MS,
    scroll_pages: int = DEFAULT_SCROLL_PAGES,
    offer_selector: str | None = None,
    pre_actions: list[dict] | None = None,
    custom_prompt: str | None = None,
) -> list[VisionOffer]:
    """
    Scan a URL and extract offers using AI vision.

    Args:
        url: The page to scan.
        viewport: Browser viewport dimensions.
        wait_ms: Milliseconds to wait after page load.
        scroll_pages: Number of viewport-heights to scroll.
        offer_selector: Optional CSS selector to narrow capture area.
        pre_actions: Optional actions to perform before capture.
        custom_prompt: Override the default extraction prompt.

    Returns:
        List of VisionOffer objects extracted from the page.
    """
    vp = viewport or DEFAULT_VIEWPORT
    browser = await _launch_browser()

    try:
        context = await browser.new_context(
            viewport=vp,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/121.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        await _apply_stealth(page)

        logger.info("Vision scraper navigating to: %s", url)
        await page.goto(url, wait_until="networkidle", timeout=30000)

        # Execute pre-actions if provided
        if pre_actions:
            await _execute_pre_actions(page, pre_actions)

        # If a specific selector is provided, scroll within it
        if offer_selector:
            try:
                element = await page.query_selector(offer_selector)
                if element:
                    await element.scroll_into_view_if_needed()
                    await page.wait_for_timeout(1000)
            except Exception as exc:
                logger.warning("Offer selector failed, using full page: %s", exc)

        screenshots = await _scroll_and_capture(page, scroll_pages, wait_ms)
        logger.info("Captured %d screenshots from %s", len(screenshots), url)

    finally:
        await browser.close()

    prompt = custom_prompt or EXTRACTION_PROMPT
    raw_offers = await _call_vision(screenshots, prompt)
    logger.info("Vision extracted %d raw offers from %s", len(raw_offers), url)

    return _raw_to_vision_offers(raw_offers)


async def scan_url_from_config(
    url: str,
    config: dict | None = None,
) -> list[VisionOffer]:
    """
    Scan using a platform's scraper_config dictionary.

    Config keys (all optional):
        offers_url: Override URL to scan.
        wait_ms: Wait time after load.
        viewport: {width, height} dict.
        scroll_pages: Number of scrolls.
        offer_selector: CSS selector for offer area.
        pre_actions: List of action dicts.
        custom_prompt: Override extraction prompt.
    """
    cfg = config or {}
    target_url = cfg.get("offers_url", url)

    return await scan_url(
        target_url,
        viewport=cfg.get("viewport"),
        wait_ms=cfg.get("wait_ms", DEFAULT_WAIT_MS),
        scroll_pages=cfg.get("scroll_pages", DEFAULT_SCROLL_PAGES),
        offer_selector=cfg.get("offer_selector"),
        pre_actions=cfg.get("pre_actions"),
        custom_prompt=cfg.get("custom_prompt"),
    )
