"""
Admin scraper control endpoints — trigger scrapes, view run history.

Uses the Hybrid Map for all scrape cycles:
  Vision Scraper (primary) → traditional plugin (fallback) → auto-discover.
Zero hard-coded platform data.
"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import ScraperRun, Platform, get_db
from scrapers.hybrid_map import run_all_hybrid, run_hybrid_scrape
from scrapers.plugin_loader import run_single_platform
from services.intelligence import score_all_offers

scraper_router = APIRouter(tags=["scrapers"])


@scraper_router.post("/run-all")
async def trigger_all_scrapers():
    """
    Trigger Hybrid Map scrape cycle for all due platforms, then re-score.
    Uses Vision Scraper as primary, traditional plugins as fallback.
    """
    results = await run_all_hybrid()
    stats = await score_all_offers()
    return {
        "message": "Hybrid scrape cycle complete",
        "platforms_scraped": len(results),
        "results": results,
        "scoring": stats,
    }


@scraper_router.post("/run/{slug}")
async def trigger_single_scraper(slug: str):
    """
    Trigger hybrid scrape for one platform by slug, then re-score.
    Tries traditional plugin first (if configured), falls back to vision.
    """
    from models import async_session
    async with async_session() as db:
        result = await db.execute(
            select(Platform).where(Platform.slug == slug)
        )
        platform = result.scalar_one_or_none()

    if not platform:
        raise HTTPException(404, f"Platform '{slug}' not found")

    hybrid_result = await run_hybrid_scrape(platform)
    stats = await score_all_offers()

    return {
        "platform": slug,
        "method": hybrid_result.method,
        "success": hybrid_result.success,
        "offers_found": hybrid_result.offers_found,
        "error": hybrid_result.error or None,
        "scoring": stats,
    }


@scraper_router.post("/score")
async def trigger_scoring():
    """Re-score all active offers without scraping."""
    stats = await score_all_offers()
    return stats


@scraper_router.get("/runs")
async def scraper_history(
    platform_slug: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """View scraper run history."""
    q = (
        select(ScraperRun, Platform.name.label("platform_name"))
        .join(Platform, ScraperRun.platform_id == Platform.id)
    )
    if platform_slug:
        q = q.where(Platform.slug == platform_slug)
    q = q.order_by(ScraperRun.started_at.desc()).limit(limit)

    result = await db.execute(q)
    rows = result.all()

    return [
        {
            "id": run.id,
            "platform": platform_name,
            "status": run.status,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "offers_found": run.offers_found,
            "offers_new": run.offers_new,
            "offers_updated": run.offers_updated,
            "error": run.error_message,
        }
        for run, platform_name in rows
    ]
