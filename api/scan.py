"""
Scan API — On-demand URL scanning via Vision Scraper.

POST /api/scan
Accepts a URL, optionally auto-registers the platform,
runs vision extraction, scores results, returns structured offers.
"""

import logging
import re
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.database import get_db
from models.offer import Offer, ScraperRun
from models.platform import Platform
from scrapers.vision_scraper import scan_url, scan_url_from_config, VisionOffer
from services.intelligence import calc_ev_per_hour, calc_caddie_score

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/scan", tags=["scan"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class ScanRequest(BaseModel):
    """Incoming scan request."""

    url: str = Field(..., description="URL of the offer wall or platform to scan")
    auto_register: bool = Field(
        default=True,
        description="Automatically register the platform if not already known",
    )
    viewport_width: int | None = Field(default=None, description="Browser viewport width")
    viewport_height: int | None = Field(default=None, description="Browser viewport height")
    wait_ms: int | None = Field(default=None, description="Wait time after page load (ms)")
    scroll_pages: int | None = Field(default=None, description="Number of viewport scrolls")
    offer_selector: str | None = Field(
        default=None, description="CSS selector to narrow capture to offer area"
    )


class ScanOfferOut(BaseModel):
    """Single offer in scan results."""

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
    ev_per_hour: float | None = None
    caddie_score: float | None = None
    is_hot: bool = False


class ScanResponse(BaseModel):
    """Full scan response."""

    scanned_url: str
    platform_slug: str | None = None
    platform_id: int | None = None
    auto_registered: bool = False
    offers_found: int = 0
    offers: list[ScanOfferOut] = []
    scraper_run_id: int | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slug_from_url(url: str) -> str:
    """Generate a platform slug from a URL's domain."""
    parsed = urlparse(url)
    domain = parsed.netloc or parsed.path
    # Remove www. prefix and port
    domain = re.sub(r"^www\.", "", domain)
    domain = domain.split(":")[0]
    # Convert to slug: replace dots and hyphens
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", domain).strip("-").lower()
    return slug


def _domain_from_url(url: str) -> str:
    """Extract clean domain from URL."""
    parsed = urlparse(url)
    domain = parsed.netloc or parsed.path
    domain = re.sub(r"^www\.", "", domain)
    return domain.split(":")[0]


def _score_vision_offer(vo: VisionOffer) -> ScanOfferOut:
    """Score a VisionOffer and return a ScanOfferOut."""
    ev = None
    score = None
    hot = False

    if vo.payout_usd is not None and vo.estimated_minutes is not None:
        if vo.estimated_minutes > 0:
            ev = calc_ev_per_hour(vo.payout_usd, vo.estimated_minutes)
            score = calc_caddie_score(
                payout=vo.payout_usd,
                ev_per_hour=ev,
                difficulty=vo.difficulty or "unknown",
                minutes_old=0,
                verified=False,
            )
            hot = ev >= 15.0

    return ScanOfferOut(
        title=vo.title,
        platform=vo.platform,
        payout_usd=vo.payout_usd,
        currency_type=vo.currency_type,
        estimated_minutes=vo.estimated_minutes,
        difficulty=vo.difficulty,
        category=vo.category,
        requirements=vo.requirements,
        url=vo.url,
        confidence=vo.confidence,
        ev_per_hour=round(ev, 2) if ev else None,
        caddie_score=round(score, 1) if score else None,
        is_hot=hot,
    )


async def _find_or_create_platform(
    db: AsyncSession,
    url: str,
    auto_register: bool,
) -> tuple[Platform | None, bool]:
    """Find existing platform by slug or optionally create one."""
    slug = _slug_from_url(url)
    domain = _domain_from_url(url)

    # Search by slug
    result = await db.execute(select(Platform).where(Platform.slug == slug))
    platform = result.scalar_one_or_none()

    if platform:
        return platform, False

    # Search by domain in base_url
    result = await db.execute(
        select(Platform).where(Platform.base_url.contains(domain))
    )
    platform = result.scalar_one_or_none()

    if platform:
        return platform, False

    if not auto_register:
        return None, False

    # Auto-register new platform
    platform = Platform(
        name=domain.replace(".", " ").title(),
        slug=slug,
        base_url=f"https://{domain}",
        category="unknown",
        auto_discovered=True,
        scraper_module=None,  # Vision-only platform
        scraper_config={"offers_url": url},
        is_active=True,
    )
    db.add(platform)
    await db.flush()
    logger.info("Auto-registered platform: %s (id=%d)", slug, platform.id)

    return platform, True


async def _save_offers_to_db(
    db: AsyncSession,
    platform: Platform,
    scored_offers: list[ScanOfferOut],
    scraper_run: ScraperRun,
) -> None:
    """Persist scanned offers into the database."""
    for so in scored_offers:
        offer = Offer(
            platform_id=platform.id,
            title=so.title,
            payout=so.payout_usd,
            currency_type=so.currency_type or "USD",
            estimated_minutes=so.estimated_minutes,
            difficulty=so.difficulty or "unknown",
            category=so.category or "other",
            requirements=so.requirements,
            url=so.url,
            ev_per_hour=so.ev_per_hour,
            caddie_score=so.caddie_score,
            is_hot=so.is_hot,
            is_active=True,
            source="vision",
            confidence=so.confidence,
        )
        db.add(offer)

    scraper_run.offers_found = len(scored_offers)
    scraper_run.status = "completed"
    await db.flush()


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("", response_model=ScanResponse)
@router.post("/", response_model=ScanResponse)
async def scan_endpoint(
    req: ScanRequest,
    db: AsyncSession = Depends(get_db),
) -> ScanResponse:
    """
    Scan a URL for offers using AI vision extraction.

    Workflow:
    1. Find or auto-register the platform
    2. Build vision scraper config from request params
    3. Run the vision scraper
    4. Score each extracted offer
    5. Optionally persist results to the database
    6. Return scored offers
    """
    # Validate URL
    parsed = urlparse(req.url)
    if not parsed.scheme or not parsed.netloc:
        raise HTTPException(status_code=400, detail="Invalid URL — must include scheme (https://)")

    # Find or create platform
    platform, auto_registered = await _find_or_create_platform(
        db, req.url, req.auto_register
    )

    # Create scraper run log
    scraper_run = ScraperRun(
        platform_id=platform.id if platform else None,
        scraper_type="vision_scan",
        status="running",
        trigger="api_scan",
    )
    db.add(scraper_run)
    await db.flush()

    # Build config
    config: dict = {}
    if platform and platform.scraper_config:
        config = dict(platform.scraper_config)

    # Override with request params
    config["offers_url"] = req.url
    if req.wait_ms is not None:
        config["wait_ms"] = req.wait_ms
    if req.scroll_pages is not None:
        config["scroll_pages"] = req.scroll_pages
    if req.offer_selector is not None:
        config["offer_selector"] = req.offer_selector
    if req.viewport_width and req.viewport_height:
        config["viewport"] = {"width": req.viewport_width, "height": req.viewport_height}

    # Run vision scan
    try:
        vision_offers: list[VisionOffer] = await scan_url_from_config(req.url, config)
    except Exception as exc:
        scraper_run.status = "failed"
        scraper_run.error_message = str(exc)[:500]
        await db.commit()
        logger.error("Vision scan failed for %s: %s", req.url, exc)
        raise HTTPException(status_code=502, detail=f"Vision scan failed: {exc}") from exc

    # Score offers
    scored: list[ScanOfferOut] = [_score_vision_offer(vo) for vo in vision_offers]

    # Sort by caddie_score descending (None last)
    scored.sort(key=lambda o: o.caddie_score or 0, reverse=True)

    # Persist if we have a platform
    if platform:
        try:
            await _save_offers_to_db(db, platform, scored, scraper_run)
            await db.commit()
        except Exception as exc:
            logger.error("Failed to save scan results: %s", exc)
            await db.rollback()
            scraper_run.status = "partial"
    else:
        scraper_run.status = "completed"
        scraper_run.offers_found = len(scored)
        await db.commit()

    return ScanResponse(
        scanned_url=req.url,
        platform_slug=platform.slug if platform else _slug_from_url(req.url),
        platform_id=platform.id if platform else None,
        auto_registered=auto_registered,
        offers_found=len(scored),
        offers=scored,
        scraper_run_id=scraper_run.id,
    )


@router.post("/batch", response_model=list[ScanResponse])
async def scan_batch_endpoint(
    urls: list[str],
    db: AsyncSession = Depends(get_db),
) -> list[ScanResponse]:
    """
    Scan multiple URLs sequentially.

    Accepts a list of URLs, scans each one, and returns
    an array of ScanResponse objects.
    """
    if len(urls) > 10:
        raise HTTPException(
            status_code=400,
            detail="Maximum 10 URLs per batch scan",
        )

    results: list[ScanResponse] = []

    for url in urls:
        try:
            req = ScanRequest(url=url)
            resp = await scan_endpoint(req, db)
            results.append(resp)
        except HTTPException:
            # Include failed scans with zero offers
            results.append(
                ScanResponse(
                    scanned_url=url,
                    platform_slug=_slug_from_url(url),
                    offers_found=0,
                    offers=[],
                )
            )
        except Exception as exc:
            logger.error("Batch scan error for %s: %s", url, exc)
            results.append(
                ScanResponse(
                    scanned_url=url,
                    platform_slug=_slug_from_url(url),
                    offers_found=0,
                    offers=[],
                )
            )

    return results
