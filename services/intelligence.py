"""
Offer Intelligence — the brain of The Caddie.

Calculates EV/hour, composite Caddie Score, and flags hot offers.
Runs after every scraper cycle to re-score all active offers.
No hard-coded thresholds — all configurable via env or DB.

The sniper spotter: silently calculates all variables, delivers
the pre-calibrated recommendation. User never sees the math.
"""

import os
import logging
from datetime import datetime
from typing import Optional
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models import Offer, Platform, async_session

logger = logging.getLogger("caddie.intelligence")

# ── Configurable thresholds ──────────────────────────────
HOT_THRESHOLD = float(os.getenv("CADDIE_HOT_THRESHOLD", "15.0"))       # $/hr
DEFAULT_EST_MINUTES = float(os.getenv("CADDIE_DEFAULT_EST_MIN", "30")) # fallback estimate
TOP_N = int(os.getenv("CADDIE_TOP_N", "3"))                            # top offers to surface


# ── Scoring weights (sum to 1.0) ─────────────────────────
W_EV         = 0.50   # EV/hour dominates
W_PAYOUT     = 0.20   # raw payout amount
W_DIFFICULTY = 0.15   # easier = better (inverted)
W_FRESHNESS  = 0.10   # newer = better
W_VERIFIED   = 0.05   # dual-stamp bonus


def calc_ev_per_hour(
    payout: float,
    est_minutes: Optional[float],
) -> float:
    """
    Expected value per hour.
    If no time estimate, use conservative default.
    """
    minutes = est_minutes if est_minutes and est_minutes > 0 else DEFAULT_EST_MINUTES
    return (payout / minutes) * 60


def calc_caddie_score(
    ev_per_hour: float,
    payout: float,
    difficulty: Optional[float],
    first_seen: datetime,
    player_verified: bool,
    platform_verified: bool,
) -> float:
    """
    Composite score 0-100. Weighted blend of:
    - EV/hour (normalized against hot threshold)
    - Raw payout (log-scaled)
    - Difficulty (inverted, lower = better)
    - Freshness (hours since first seen, decays)
    - Verification bonus (dual stamp)
    """
    import math

    # Normalize EV: $15/hr = 50 points, $30/hr = 100 points (capped)
    ev_norm = min((ev_per_hour / HOT_THRESHOLD) * 50, 100)

    # Payout: log scale, $1 = ~0, $10 = ~50, $100 = ~100
    payout_norm = min(math.log10(max(payout, 0.01) + 1) * 50, 100)

    # Difficulty: 0 (easy) = 100, 1 (hard) = 0
    diff = difficulty if difficulty is not None else 0.5
    diff_norm = (1 - diff) * 100

    # Freshness: full score if < 1hr old, decays over 72hrs
    hours_old = (datetime.utcnow() - first_seen).total_seconds() / 3600
    fresh_norm = max(0, 100 - (hours_old / 72) * 100)

    # Verification: each stamp worth 50
    verify_norm = 0
    if player_verified:
        verify_norm += 50
    if platform_verified:
        verify_norm += 50

    score = (
        W_EV * ev_norm
        + W_PAYOUT * payout_norm
        + W_DIFFICULTY * diff_norm
        + W_FRESHNESS * fresh_norm
        + W_VERIFIED * verify_norm
    )

    return round(min(max(score, 0), 100), 1)


async def score_all_offers(db: Optional[AsyncSession] = None):
    """
    Re-score every active offer in the database.
    Call after each scraper cycle.
    """
    close_session = False
    if db is None:
        db = async_session()
        close_session = True

    try:
        result = await db.execute(
            select(Offer).where(Offer.is_active == True)
        )
        offers = result.scalars().all()

        scored = 0
        hot_count = 0

        for offer in offers:
            ev = calc_ev_per_hour(offer.payout_amount, offer.estimated_minutes)
            score = calc_caddie_score(
                ev_per_hour=ev,
                payout=offer.payout_amount,
                difficulty=offer.difficulty_score,
                first_seen=offer.first_seen,
                player_verified=offer.player_verified,
                platform_verified=offer.platform_verified,
            )
            is_hot = ev >= HOT_THRESHOLD

            offer.ev_per_hour = round(ev, 2)
            offer.caddie_score = score
            offer.is_hot = is_hot

            scored += 1
            if is_hot:
                hot_count += 1

        await db.commit()
        logger.info(f"Scored {scored} offers. {hot_count} flagged hot (>${HOT_THRESHOLD}/hr)")
        return {"scored": scored, "hot": hot_count}

    finally:
        if close_session:
            await db.close()


async def get_top_offers(
    limit: int = TOP_N,
    category: Optional[str] = None,
    platform_slug: Optional[str] = None,
) -> list[dict]:
    """
    Return top-ranked active offers — what The Caddie surfaces to the user.
    Sorted by caddie_score descending.
    """
    async with async_session() as db:
        q = (
            select(Offer, Platform.name.label("platform_name"), Platform.slug.label("platform_slug"))
            .join(Platform, Offer.platform_id == Platform.id)
            .where(Offer.is_active == True)
        )

        if category:
            q = q.where(Platform.category == category)
        if platform_slug:
            q = q.where(Platform.slug == platform_slug)

        q = q.order_by(Offer.caddie_score.desc()).limit(limit)

        result = await db.execute(q)
        rows = result.all()

        return [
            {
                "title": offer.title,
                "platform": platform_name,
                "platform_slug": platform_slug_val,
                "payout": f"${offer.payout_amount:.2f}",
                "ev_per_hour": f"${offer.ev_per_hour:.2f}/hr" if offer.ev_per_hour else "N/A",
                "caddie_score": offer.caddie_score,
                "is_hot": offer.is_hot,
                "offer_type": offer.offer_type,
                "est_minutes": offer.estimated_minutes,
                "url": offer.url,
                "verified": {
                    "player": offer.player_verified,
                    "platform": offer.platform_verified,
                },
            }
            for offer, platform_name, platform_slug_val in rows
        ]


async def get_hot_offers() -> list[dict]:
    """Shortcut: only hot offers (>$15/hr EV)."""
    async with async_session() as db:
        q = (
            select(Offer, Platform.name.label("platform_name"))
            .join(Platform, Offer.platform_id == Platform.id)
            .where(Offer.is_active == True, Offer.is_hot == True)
            .order_by(Offer.ev_per_hour.desc())
            .limit(10)
        )
        result = await db.execute(q)
        rows = result.all()

        return [
            {
                "title": offer.title,
                "platform": platform_name,
                "payout": f"${offer.payout_amount:.2f}",
                "ev_per_hour": f"${offer.ev_per_hour:.2f}/hr",
                "caddie_score": offer.caddie_score,
                "url": offer.url,
            }
            for offer, platform_name in rows
        ]
