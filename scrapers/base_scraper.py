"""
Base scraper — every platform scraper plugin inherits from this.
Handles the run lifecycle, DB writes, error logging. Plugin authors
only implement `fetch_offers()`.

Zero hard-coded platform data. The scraper reads its config from the
Platform row in the DB.
"""

import abc
import asyncio
import logging
from datetime import datetime
from typing import List, Optional
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Platform, Offer, ScraperRun, async_session

logger = logging.getLogger("caddie.scraper")


@dataclass
class RawOffer:
    """Normalized offer shape returned by plugin's fetch_offers()."""
    title: str
    payout_amount: float
    offer_type: str = "task"
    description: str = ""
    url: str = ""
    external_id: str = ""
    payout_currency: str = "USD"
    payout_type: str = "fixed"
    estimated_minutes: Optional[float] = None
    difficulty_score: Optional[float] = None
    requirements: Optional[list] = field(default_factory=list)
    expires_at: Optional[datetime] = None


class BaseScraper(abc.ABC):
    """
    Subclass this for each platform plugin.

    class MyPlatformScraper(BaseScraper):
        async def fetch_offers(self) -> List[RawOffer]:
            # hit platform API / scrape page
            return [RawOffer(title="...", payout_amount=5.0)]
    """

    def __init__(self, platform: Platform):
        self.platform = platform
        self.config: dict = platform.scraper_config or {}

    @abc.abstractmethod
    async def fetch_offers(self) -> List[RawOffer]:
        """Pull raw offers from the platform. Implement per plugin."""
        ...

    async def run(self) -> ScraperRun:
        """
        Full scraper lifecycle:
        1. Create ScraperRun log entry
        2. Call plugin's fetch_offers()
        3. Upsert offers into DB (dedup by external_id)
        4. Mark stale offers inactive
        5. Close ScraperRun with stats
        """
        async with async_session() as db:
            run = ScraperRun(platform_id=self.platform.id)
            db.add(run)
            await db.commit()

            try:
                raw_offers = await self.fetch_offers()
                new_count, updated_count = await self._upsert_offers(db, raw_offers)

                run.status = "success"
                run.offers_found = len(raw_offers)
                run.offers_new = new_count
                run.offers_updated = updated_count
                run.finished_at = datetime.utcnow()

                # update platform last_scraped
                self.platform.last_scraped = datetime.utcnow()
                db.add(self.platform)

                logger.info(
                    f"[{self.platform.slug}] Done: {len(raw_offers)} found, "
                    f"{new_count} new, {updated_count} updated"
                )

            except Exception as e:
                run.status = "failed"
                run.error_message = str(e)
                run.finished_at = datetime.utcnow()
                logger.error(f"[{self.platform.slug}] Scraper failed: {e}")

            await db.commit()
            return run

    async def _upsert_offers(
        self, db: AsyncSession, raw_offers: List[RawOffer]
    ) -> tuple[int, int]:
        """Upsert offers by external_id. Returns (new_count, updated_count)."""
        new_count = 0
        updated_count = 0
        seen_external_ids = set()

        for raw in raw_offers:
            ext_id = raw.external_id or raw.title  # fallback dedup key

            # check existing
            result = await db.execute(
                select(Offer).where(
                    Offer.platform_id == self.platform.id,
                    Offer.external_id == ext_id,
                )
            )
            existing = result.scalar_one_or_none()

            if existing:
                # update
                existing.payout_amount = raw.payout_amount
                existing.title = raw.title
                existing.description = raw.description
                existing.url = raw.url
                existing.estimated_minutes = raw.estimated_minutes
                existing.difficulty_score = raw.difficulty_score
                existing.requirements = raw.requirements
                existing.expires_at = raw.expires_at
                existing.last_seen = datetime.utcnow()
                existing.times_seen += 1
                existing.is_active = True
                updated_count += 1
            else:
                # new
                offer = Offer(
                    platform_id=self.platform.id,
                    title=raw.title,
                    description=raw.description,
                    url=raw.url,
                    offer_type=raw.offer_type,
                    external_id=ext_id,
                    payout_amount=raw.payout_amount,
                    payout_currency=raw.payout_currency,
                    payout_type=raw.payout_type,
                    estimated_minutes=raw.estimated_minutes,
                    difficulty_score=raw.difficulty_score,
                    requirements=raw.requirements,
                    expires_at=raw.expires_at,
                )
                db.add(offer)
                new_count += 1

            seen_external_ids.add(ext_id)

        await db.flush()
        return new_count, updated_count
