"""
Public offers API — what the frontend hits.
Top offers, hot offers, filtered by platform/category.
"""

from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Offer, Platform, get_db
from services.intelligence import get_top_offers, get_hot_offers

offers_router = APIRouter(tags=["offers"])


@offers_router.get("/offers/top")
async def top_offers(
    limit: int = Query(3, ge=1, le=25),
    category: Optional[str] = Query(None),
    platform: Optional[str] = Query(None),
):
    """The Caddie's top-ranked offers. This is what the UI surfaces."""
    return await get_top_offers(limit=limit, category=category, platform_slug=platform)


@offers_router.get("/offers/hot")
async def hot_offers():
    """Only offers with EV > $15/hr."""
    return await get_hot_offers()


@offers_router.get("/offers/{offer_id}")
async def get_offer(offer_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Offer, Platform.name.label("platform_name"))
        .join(Platform, Offer.platform_id == Platform.id)
        .where(Offer.id == offer_id)
    )
    row = result.one_or_none()
    if not row:
        from fastapi import HTTPException
        raise HTTPException(404, "Offer not found")
    offer, platform_name = row
    return {
        "id": offer.id,
        "title": offer.title,
        "description": offer.description,
        "platform": platform_name,
        "payout": offer.payout_amount,
        "ev_per_hour": offer.ev_per_hour,
        "caddie_score": offer.caddie_score,
        "is_hot": offer.is_hot,
        "offer_type": offer.offer_type,
        "estimated_minutes": offer.estimated_minutes,
        "difficulty_score": offer.difficulty_score,
        "requirements": offer.requirements,
        "url": offer.url,
        "verified": {
            "player": offer.player_verified,
            "platform": offer.platform_verified,
        },
        "first_seen": offer.first_seen.isoformat() if offer.first_seen else None,
        "last_seen": offer.last_seen.isoformat() if offer.last_seen else None,
        "times_seen": offer.times_seen,
    }
