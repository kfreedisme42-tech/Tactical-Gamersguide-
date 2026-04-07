"""
Proxy Orchestrator — connection layer for stealth browsing.

Supports:
  - Bright Data Scraping Browser (WSS endpoint)
  - Browserless.io (WSS endpoint)
  - Direct/local Chromium (fallback, no proxy)

Config comes from env vars or per-platform scraper_config in the DB.
Zero hard-coded platform data. This is infrastructure, not platform logic.

Env vars:
  CADDIE_PROXY_PROVIDER   = "brightdata" | "browserless" | "none"
  CADDIE_PROXY_WSS        = wss://... endpoint
  CADDIE_PROXY_API_KEY    = API key for provider
  BRIGHTDATA_ZONE         = zone name (Bright Data specific)
  BROWSERLESS_TOKEN       = token (Browserless specific)
"""

import os
import logging
from typing import Optional

logger = logging.getLogger("caddie.proxy")

# ── Env config ───────────────────────────────────────────
PROXY_PROVIDER = os.getenv("CADDIE_PROXY_PROVIDER", "none")
PROXY_WSS = os.getenv("CADDIE_PROXY_WSS", "")
PROXY_API_KEY = os.getenv("CADDIE_PROXY_API_KEY", "")

# Provider-specific
BRIGHTDATA_ZONE = os.getenv("BRIGHTDATA_ZONE", "scraping_browser")
BROWSERLESS_TOKEN = os.getenv("BROWSERLESS_TOKEN", "")


def get_browser_connection(platform_config: Optional[dict] = None) -> dict:
    """
    Return browser connection params based on provider config.

    Returns dict with:
      - ws_endpoint: WSS URL for remote browser (None = use local)
      - user_agent: UA string override
      - proxy_provider: which provider is active

    Platform-level overrides in scraper_config take priority over env vars:
      {
        "proxy_provider": "brightdata",
        "proxy_wss": "wss://...",
        "proxy_api_key": "..."
      }
    """
    config = platform_config or {}

    provider = config.get("proxy_provider", PROXY_PROVIDER).lower()
    wss = config.get("proxy_wss", PROXY_WSS)
    api_key = config.get("proxy_api_key", PROXY_API_KEY)

    if provider == "brightdata":
        return _brightdata_connection(wss, api_key, config)
    elif provider == "browserless":
        return _browserless_connection(wss, api_key, config)
    elif provider == "custom" and wss:
        return _custom_connection(wss, config)
    else:
        return _local_connection()


def _brightdata_connection(wss: str, api_key: str, config: dict) -> dict:
    """
    Bright Data Scraping Browser connection.
    Uses their managed browser with residential proxy, CAPTCHA solving,
    and anti-detection built in.
    """
    zone = config.get("brightdata_zone", BRIGHTDATA_ZONE)

    if not wss:
        # Construct default Bright Data WSS endpoint
        if api_key:
            wss = f"wss://brd-customer-{api_key}@brd.superproxy.io:9222"
        else:
            logger.warning("Bright Data configured but no WSS or API key — falling back to local")
            return _local_connection()

    logger.info(f"Using Bright Data Scraping Browser (zone: {zone})")
    return {
        "ws_endpoint": wss,
        "proxy_provider": "brightdata",
        "user_agent": _stealth_ua(),
        "zone": zone,
    }


def _browserless_connection(wss: str, api_key: str, config: dict) -> dict:
    """
    Browserless.io connection.
    Managed headless Chrome with stealth and proxy support.
    """
    token = config.get("browserless_token", BROWSERLESS_TOKEN) or api_key

    if not wss:
        if token:
            wss = f"wss://chrome.browserless.io?token={token}&stealth=true"
        else:
            logger.warning("Browserless configured but no token — falling back to local")
            return _local_connection()

    logger.info("Using Browserless.io Scraping Browser")
    return {
        "ws_endpoint": wss,
        "proxy_provider": "browserless",
        "user_agent": _stealth_ua(),
    }


def _custom_connection(wss: str, config: dict) -> dict:
    """Custom WSS endpoint — any CDP-compatible remote browser."""
    logger.info(f"Using custom browser endpoint: {wss[:50]}...")
    return {
        "ws_endpoint": wss,
        "proxy_provider": "custom",
        "user_agent": config.get("user_agent", _stealth_ua()),
    }


def _local_connection() -> dict:
    """No proxy — local headless Chromium. Good for dev/testing."""
    logger.info("Using local Chromium (no proxy)")
    return {
        "ws_endpoint": None,
        "proxy_provider": "local",
        "user_agent": _stealth_ua(),
    }


def _stealth_ua() -> str:
    """Realistic Chrome UA string."""
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )


def get_provider_status() -> dict:
    """
    Return current proxy provider config status.
    Used by /admin/stats or health checks.
    """
    provider = PROXY_PROVIDER.lower()
    return {
        "provider": provider,
        "configured": provider != "none" and bool(PROXY_WSS or PROXY_API_KEY),
        "wss_set": bool(PROXY_WSS),
        "api_key_set": bool(PROXY_API_KEY),
    }
