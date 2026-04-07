"""
Admin API — full CRUD for the platform registry.
This is the ONLY way platforms enter the system.
No hard-coded lists. No config files. Database is the source of truth.

Mount: app.include_router(admin_router, prefix="/admin")
"""

from typing import Optional, List
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field

from models import Platform, Offer, ScraperRun, get_db

admin_router = APIRouter(tags=["admin"])


# ── Schemas ──────────────────────────────────────────────

class PlatformCreate(BaseModel):
    name: str
    slug: str
    url: str
    logo_url: Optional[str] = None
    description: Optional[str] = None
    scraper_module: Optional[str] = None
    scraper_config: Optional[dict] = None
    scrape_interval_minutes: int = 60
    category: str = "general"
    avg_payout: Optional[float] = None
    currency: str = "USD"
    min_cashout: Optional[float] = None
    cashout_methods: Optional[list] = None


class PlatformUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    logo_url: Optional[str] = None
    description: Optional[str] = None
    scraper_module: Optional[str] = None
    scraper_config: Optional[dict] = None
    scrape_interval_minutes: Optional[int] = None
    category: Optional[str] = None
    avg_payout: Optional[float] = None
    currency: Optional[str] = None
    min_cashout: Optional[float] = None
    cashout_methods: Optional[list] = None
    is_active: Optional[bool] = None
    is_verified: Optional[bool] = None


class PlatformOut(BaseModel):
    id: str
    name: str
    slug: str
    url: str
    logo_url: Optional[str]
    description: Optional[str]
    scraper_module: Optional[str]
    scrape_interval_minutes: int
    category: str
    avg_payout: Optional[float]
    currency: str
    min_cashout: Optional[float]
    cashout_methods: Optional[list]
    is_active: bool
    is_verified: bool
    auto_discovered: bool
    created_at: datetime
    updated_at: datetime
    last_scraped: Optional[datetime]
    offer_count: int = 0

    class Config:
        from_attributes = True


class SeedPayload(BaseModel):
    """Bulk-seed platforms via admin API (replaces any config files)."""
    platforms: List[PlatformCreate]


class StatsOut(BaseModel):
    total_platforms: int
    active_platforms: int
    verified_platforms: int
    auto_discovered: int
    total_offers: int
    hot_offers: int
    total_scraper_runs: int
    last_scrape: Optional[datetime]


# ── Endpoints ────────────────────────────────────────────

@admin_router.get("/platforms", response_model=List[PlatformOut])
async def list_platforms(
    active_only: bool = Query(False),
    category: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    q = select(Platform)
    if active_only:
        q = q.where(Platform.is_active == True)
    if category:
        q = q.where(Platform.category == category)
    q = q.order_by(Platform.name)
    result = await db.execute(q)
    platforms = result.scalars().all()

    # attach offer counts
    out = []
    for p in platforms:
        count_q = select(func.count()).where(Offer.platform_id == p.id)
        count_result = await db.execute(count_q)
        count = count_result.scalar() or 0
        data = PlatformOut.model_validate(p)
        data.offer_count = count
        out.append(data)
    return out


@admin_router.get("/platforms/{slug}", response_model=PlatformOut)
async def get_platform(slug: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Platform).where(Platform.slug == slug))
    p = result.scalar_one_or_none()
    if not p:
        raise HTTPException(404, f"Platform '{slug}' not found")
    return PlatformOut.model_validate(p)


@admin_router.post("/platforms", response_model=PlatformOut, status_code=201)
async def create_platform(
    payload: PlatformCreate,
    db: AsyncSession = Depends(get_db),
):
    # check duplicate
    existing = await db.execute(
        select(Platform).where(Platform.slug == payload.slug)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, f"Platform '{payload.slug}' already exists")

    platform = Platform(**payload.model_dump())
    db.add(platform)
    await db.commit()
    await db.refresh(platform)
    return PlatformOut.model_validate(platform)


@admin_router.put("/platforms/{slug}", response_model=PlatformOut)
async def update_platform(
    slug: str,
    payload: PlatformUpdate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Platform).where(Platform.slug == slug))
    platform = result.scalar_one_or_none()
    if not platform:
        raise HTTPException(404, f"Platform '{slug}' not found")

    update_data = payload.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(platform, field, value)
    platform.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(platform)
    return PlatformOut.model_validate(platform)


@admin_router.delete("/platforms/{slug}")
async def delete_platform(slug: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Platform).where(Platform.slug == slug))
    platform = result.scalar_one_or_none()
    if not platform:
        raise HTTPException(404, f"Platform '{slug}' not found")
    await db.delete(platform)
    await db.commit()
    return {"deleted": slug}


@admin_router.post("/platforms/seed", status_code=201)
async def seed_platforms(
    payload: SeedPayload,
    db: AsyncSession = Depends(get_db),
):
    """
    Bulk-seed platforms into the DB.
    Skips duplicates (by slug). This replaces config files entirely.
    """
    created = []
    skipped = []
    for p in payload.platforms:
        existing = await db.execute(
            select(Platform).where(Platform.slug == p.slug)
        )
        if existing.scalar_one_or_none():
            skipped.append(p.slug)
            continue
        platform = Platform(**p.model_dump())
        db.add(platform)
        created.append(p.slug)
    await db.commit()
    return {"created": created, "skipped": skipped}


@admin_router.get("/stats", response_model=StatsOut)
async def dashboard_stats(db: AsyncSession = Depends(get_db)):
    """Quick stats for admin dashboard."""
    total_p = (await db.execute(select(func.count(Platform.id)))).scalar() or 0
    active_p = (await db.execute(
        select(func.count(Platform.id)).where(Platform.is_active == True)
    )).scalar() or 0
    verified_p = (await db.execute(
        select(func.count(Platform.id)).where(Platform.is_verified == True)
    )).scalar() or 0
    auto_d = (await db.execute(
        select(func.count(Platform.id)).where(Platform.auto_discovered == True)
    )).scalar() or 0
    total_o = (await db.execute(select(func.count(Offer.id)))).scalar() or 0
    hot_o = (await db.execute(
        select(func.count(Offer.id)).where(Offer.is_hot == True)
    )).scalar() or 0
    total_sr = (await db.execute(select(func.count(ScraperRun.id)))).scalar() or 0

    last_run = (await db.execute(
        select(ScraperRun.finished_at)
        .where(ScraperRun.status == "success")
        .order_by(ScraperRun.finished_at.desc())
        .limit(1)
    )).scalar()

    return StatsOut(
        total_platforms=total_p,
        active_platforms=active_p,
        verified_platforms=verified_p,
        auto_discovered=auto_d,
        total_offers=total_o,
        hot_offers=hot_o,
        total_scraper_runs=total_sr,
        last_scrape=last_run,
    )
