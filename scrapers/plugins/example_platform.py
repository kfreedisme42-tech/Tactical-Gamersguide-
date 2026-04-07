"""
Example scraper plugin — template for building new platform scrapers.
Drop a new file in scrapers/plugins/, set Platform.scraper_module in the DB,
and the loader picks it up automatically. No code changes anywhere else.

Usage:
    1. POST /admin/platforms with scraper_module="scrapers.plugins.example_platform"
    2. The plugin loader imports this module, finds the `Scraper` class, runs it.
"""

import aiohttp
import logging
from typing import List

from scrapers.base_scraper import BaseScraper, RawOffer

logger = logging.getLogger("caddie.scraper.example")


class Scraper(BaseScraper):
    """
    Replace this with real scraping logic for your target platform.
    self.platform — the Platform DB row (name, url, config, etc.)
    self.config   — shortcut to platform.scraper_config dict
    """

    async def fetch_offers(self) -> List[RawOffer]:
        """
        Hit the platform's offers page/API and return normalized RawOffers.
        The base class handles DB writes, dedup, logging, error handling.
        """
        target_url = self.config.get("offers_url", f"{self.platform.url}/offers")

        async with aiohttp.ClientSession() as session:
            async with session.get(target_url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status != 200:
                    logger.warning(f"[{self.platform.slug}] HTTP {resp.status}")
                    return []

                # --- ADAPT THIS SECTION per platform ---
                # If JSON API:
                data = await resp.json()
                offers = []
                for item in data.get("offers", []):
                    offers.append(RawOffer(
                        title=item["title"],
                        payout_amount=float(item.get("payout", 0)),
                        offer_type=item.get("type", "task"),
                        description=item.get("description", ""),
                        url=item.get("url", ""),
                        external_id=str(item.get("id", "")),
                        estimated_minutes=item.get("est_minutes"),
                        difficulty_score=item.get("difficulty"),
                        requirements=item.get("requirements", []),
                    ))
                return offers

                # If HTML scrape, swap to BeautifulSoup:
                # html = await resp.text()
                # soup = BeautifulSoup(html, "html.parser")
                # ... parse offers from soup ...
