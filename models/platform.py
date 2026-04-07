"""
Platform model — the single source of truth.
Zero hard-coded platform data in the codebase. Everything lives here.
"""

import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Float, Boolean, DateTime, Text, JSON, Integer
)
from .database import Base


def _uuid():
    return str(uuid.uuid4())


class Platform(Base):
    __tablename__ = "platforms"

    id            = Column(String, primary_key=True, default=_uuid)
    name          = Column(String, nullable=False, unique=True, index=True)
    slug          = Column(String, nullable=False, unique=True, index=True)
    url           = Column(String, nullable=False)
    logo_url      = Column(String, nullable=True)
    description   = Column(Text, nullable=True)

    # --- scraper config (plugin-based) ---
    scraper_module = Column(String, nullable=True)      # e.g. "scrapers.plugins.some_platform"
    scraper_config = Column(JSON, nullable=True)         # per-platform scraper params
    scrape_interval_minutes = Column(Integer, default=60)

    # --- platform metadata ---
    category      = Column(String, default="general")    # gaming, surveys, cashback, crypto
    avg_payout    = Column(Float, nullable=True)
    currency      = Column(String, default="USD")
    min_cashout   = Column(Float, nullable=True)
    cashout_methods = Column(JSON, nullable=True)        # ["paypal", "gift_card", "crypto"]

    # --- status ---
    is_active     = Column(Boolean, default=True)
    is_verified   = Column(Boolean, default=False)       # admin-verified platform
    auto_discovered = Column(Boolean, default=False)     # found by AI discovery

    # --- timestamps ---
    created_at    = Column(DateTime, default=datetime.utcnow)
    updated_at    = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_scraped  = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<Platform {self.slug} active={self.is_active}>"
