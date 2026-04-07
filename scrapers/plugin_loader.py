"""
Dynamic plugin loader — imports scraper modules at runtime from the
`scraper_module` field on each Platform row. No hard-coded registry.

The loop:
  1. Query all active platforms with a scraper_module set
  2. Dynamically import each module
  3. Instantiate the scraper class
  4. Run it

New platforms added via Admin API are automatically picked up
on the next cycle. Zero code changes required.
"""

import importlib
import asyncio
import logging
from datetime import datetime, timedelta
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Platform, async_session
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger("caddie.loader")


def load_scraper_class(module_path: str) -> type[BaseScraper]:
    """
    Dynamically import a scraper module and return its Scraper class.
    Convention: each plugin module exposes a `Scraper` class.

    Example:
        Platform.scraper_module = "scrapers.plugins.some_platform"
        → imports scrapers.plugins.some_platform.Scraper
    """
    try:
        module = importlib.import_module(module_path)
        scraper_cls = getattr(module, "Scraper")
        if not issubclass(scraper_cls, BaseScraper):
            raise TypeError(
                f"{module_path}.Scraper must subclass BaseScraper"
            )
        return scraper_cls
    except (ImportError, AttributeError) as e:
        logger.error(f"Failed to load scraper module '{module_path}': {e}")
        raise


async def get_due_platforms(db: AsyncSession) -> list[Platform]:
    """
    Return active platforms whose scrape interval has elapsed
    (or that have never been scraped).
    """
    result = await db.execute(
        select(Platform).where(
            Platform.is_active == True,
            Platform.scraper_module.isnot(None),
        )
    )
    platforms = result.scalars().all()

    due = []
    now = datetime.utcnow()
    for p in platforms:
        if p.last_scraped is None:
            due.append(p)
        elif now - p.last_scraped >= timedelta(minutes=p.scrape_interval_minutes):
            due.append(p)
    return due


async def run_all_due_scrapers():
    """
    Main scraper loop entry point. Call this on a schedule (cron, APScheduler, etc).
    Finds all platforms due for a scrape, loads their plugin, runs them.
    """
    async with async_session() as db:
        due = await get_due_platforms(db)
        if not due:
            logger.info("No platforms due for scraping.")
            return

        logger.info(f"{len(due)} platform(s) due for scraping.")

        tasks = []
        for platform in due:
            try:
                scraper_cls = load_scraper_class(platform.scraper_module)
                scraper = scraper_cls(platform)
                tasks.append(scraper.run())
            except Exception as e:
                logger.error(f"Skipping {platform.slug}: {e}")

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            successes = sum(1 for r in results if not isinstance(r, Exception))
            failures = sum(1 for r in results if isinstance(r, Exception))
            logger.info(
                f"Scraper cycle complete: {successes} succeeded, {failures} failed"
            )


async def run_single_platform(slug: str):
    """Run scraper for a single platform by slug. Useful for admin triggers."""
    async with async_session() as db:
        result = await db.execute(
            select(Platform).where(Platform.slug == slug)
        )
        platform = result.scalar_one_or_none()
        if not platform:
            raise ValueError(f"Platform '{slug}' not found")
        if not platform.scraper_module:
            raise ValueError(f"Platform '{slug}' has no scraper_module configured")

        scraper_cls = load_scraper_class(platform.scraper_module)
        scraper = scraper_cls(platform)
        return await scraper.run()
