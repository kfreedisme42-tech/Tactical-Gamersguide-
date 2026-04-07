"""
Hybrid Map — orchestrates scraping strategy per platform.

Priority chain:
  1. Vision Scraper (universal, AI-powered — primary for all platforms)
  2. Traditional plugin scraper (if a platform has a hand-tuned scraper_module)
  3. Auto-discovery: when Vision finds offers from unknown platforms,
     register them in the DB automatically.

Zero hard-coded platform data. The Hybrid Map reads strategy from the DB
and decides which scraper path to take at runtime.

The discovery loop:
  Admin seeds a platform via API → Hybrid Map runs Vision Scraper →
  Vision may detect cross-links to NEW platforms → auto-register them →
  next cycle picks them up. Self-improving.
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Platform, async_session
from scrapers.base_scraper import BaseScraper, RawOffer
from scrapers.vision_scraper import VisionScraper
from scrapers.plugin_loader import load_scraper_class, get_due_platforms

logger = logging.getLogger("caddie.hybrid")


class HybridScrapeResult:
    """Result container for a single platform scrape."""
    __slots__ = ("platform_slug", "method", "offers_found", "success", "error")

    def __init__(self, slug: str, method: str, found: int, success: bool, error: str = ""):
        self.platform_slug = slug
        self.method = method
        self.offers_found = found
        self.success = success
        self.error = error

    def to_dict(self) -> dict:
        return {
            "platform": self.platform_slug,
            "method": self.method,
            "offers_found": self.offers_found,
            "success": self.success,
            "error": self.error,
        }


async def run_hybrid_scrape(platform: Platform) -> HybridScrapeResult:
    """
    Run the best available scraper for a platform.

    Strategy:
      - If platform has a traditional scraper_module AND it's not the vision scraper,
        try traditional first. If it fails or returns 0 offers, fall back to vision.
      - If platform has no scraper_module or uses vision, go straight to vision.
      - Vision is always the safety net.
    """
    traditional_module = platform.scraper_module
    is_vision_module = (
        traditional_module is None
        or "vision_scraper" in (traditional_module or "")
    )

    # ── Path 1: Traditional scraper available → try it first ──
    if not is_vision_module and traditional_module:
        logger.info(f"[{platform.slug}] Hybrid: trying traditional scraper '{traditional_module}'")
        try:
            scraper_cls = load_scraper_class(traditional_module)
            scraper = scraper_cls(platform)
            run_result = await scraper.run()

            if run_result.status == "success" and (run_result.offers_found or 0) > 0:
                logger.info(
                    f"[{platform.slug}] Traditional scraper succeeded: "
                    f"{run_result.offers_found} offers"
                )
                return HybridScrapeResult(
                    slug=platform.slug,
                    method="traditional",
                    found=run_result.offers_found or 0,
                    success=True,
                )
            else:
                logger.warning(
                    f"[{platform.slug}] Traditional scraper returned 0 offers — "
                    f"falling back to vision"
                )
        except Exception as e:
            logger.warning(
                f"[{platform.slug}] Traditional scraper failed: {e} — "
                f"falling back to vision"
            )

    # ── Path 2: Vision scraper (universal fallback / primary) ──
    logger.info(f"[{platform.slug}] Hybrid: running Vision Scraper")
    try:
        vision = VisionScraper(platform)
        run_result = await vision.run()

        if run_result.status == "success":
            return HybridScrapeResult(
                slug=platform.slug,
                method="vision",
                found=run_result.offers_found or 0,
                success=True,
            )
        else:
            return HybridScrapeResult(
                slug=platform.slug,
                method="vision",
                found=0,
                success=False,
                error=run_result.error_message or "Vision scraper returned failure",
            )

    except Exception as e:
        logger.error(f"[{platform.slug}] Vision scraper failed: {e}")
        return HybridScrapeResult(
            slug=platform.slug,
            method="vision",
            found=0,
            success=False,
            error=str(e),
        )


async def run_all_hybrid(max_concurrent: int = 5) -> list[dict]:
    """
    Run hybrid scrape for all due platforms.
    Respects concurrency limits to avoid hammering proxy providers.
    """
    async with async_session() as db:
        due = await get_due_platforms(db)

    if not due:
        logger.info("No platforms due for scraping.")
        return []

    logger.info(f"Hybrid Map: {len(due)} platform(s) due")

    semaphore = asyncio.Semaphore(max_concurrent)

    async def _scrape_with_limit(platform: Platform) -> HybridScrapeResult:
        async with semaphore:
            return await run_hybrid_scrape(platform)

    results = await asyncio.gather(
        *[_scrape_with_limit(p) for p in due],
        return_exceptions=True,
    )

    output = []
    for r in results:
        if isinstance(r, Exception):
            output.append({"error": str(r)})
        else:
            output.append(r.to_dict())

    successes = sum(1 for r in output if r.get("success"))
    failures = len(output) - successes
    logger.info(f"Hybrid Map cycle complete: {successes} succeeded, {failures} failed")

    return output


async def auto_discover_platform(
    name: str,
    url: str,
    discovered_from: Optional[str] = None,
) -> Optional[Platform]:
    """
    Auto-register a newly discovered platform.
    Called when the Vision Scraper spots cross-links to platforms
    not yet in the database.

    Sets auto_discovered=True so admin can review.
    Assigns the vision scraper by default.
    """
    slug = name.lower().strip().replace(" ", "-").replace(".", "")

    async with async_session() as db:
        # Check if already exists
        existing = await db.execute(
            select(Platform).where(Platform.slug == slug)
        )
        if existing.scalar_one_or_none():
            logger.debug(f"[auto-discover] Platform '{slug}' already exists, skipping")
            return None

        platform = Platform(
            name=name,
            slug=slug,
            url=url,
            description=f"Auto-discovered from {discovered_from or 'vision scan'}",
            scraper_module="scrapers.vision_scraper",
            scrape_interval_minutes=120,  # conservative default
            category="general",
            is_active=True,
            is_verified=False,
            auto_discovered=True,
        )
        db.add(platform)
        await db.commit()
        await db.refresh(platform)

        logger.info(
            f"[auto-discover] Registered new platform: {name} ({url}) "
            f"discovered from {discovered_from or 'unknown'}"
        )
        return platform
