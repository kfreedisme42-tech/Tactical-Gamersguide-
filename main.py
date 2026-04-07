"""
The Caddie — main FastAPI application entry point.
Zero hard-coded platform data. Database is the single source of truth.

Run:  uvicorn main:app --reload
Seed: POST /admin/platforms/seed  (send platform JSON via API at runtime)
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from models import init_db
from api import admin_router, scan_router
from api.offers import offers_router
from api.scraper_api import scraper_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create tables on startup."""
    await init_db()
    yield


app = FastAPI(
    title="The Caddie",
    description="Tactical P2E optimizer — zero hard-coded platforms, database-driven everything.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # lock down in prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ───────────────────────────────────────────────
app.include_router(admin_router, prefix="/admin")
app.include_router(offers_router, prefix="/api")
app.include_router(scraper_router, prefix="/admin/scrapers")
app.include_router(scan_router, prefix="/api")  # POST /api/scan


@app.get("/health")
async def health():
    return {"status": "ok", "service": "the-caddie"}
