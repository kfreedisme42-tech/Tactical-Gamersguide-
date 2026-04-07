"""
Offer model — individual money-making opportunities pulled by scrapers.
Each offer belongs to a Platform (FK). Scoring is calculated, never hard-coded.
"""

import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Float, Boolean, DateTime, Text, JSON, Integer, ForeignKey
)
from sqlalchemy.orm import relationship
from .database import Base


def _uuid():
    return str(uuid.uuid4())


class Offer(Base):
    __tablename__ = "offers"

    id              = Column(String, primary_key=True, default=_uuid)
    platform_id     = Column(String, ForeignKey("platforms.id"), nullable=False, index=True)
    platform        = relationship("Platform", backref="offers")

    # --- offer details ---
    title           = Column(String, nullable=False)
    description     = Column(Text, nullable=True)
    url             = Column(String, nullable=True)
    offer_type      = Column(String, default="task")     # task, survey, game, cashback, signup
    external_id     = Column(String, nullable=True)       # platform's own offer ID

    # --- payout ---
    payout_amount   = Column(Float, nullable=False)
    payout_currency = Column(String, default="USD")
    payout_type     = Column(String, default="fixed")     # fixed, range, percentage

    # --- time / difficulty ---
    estimated_minutes = Column(Float, nullable=True)      # community or scraped estimate
    difficulty_score  = Column(Float, nullable=True)       # 0-1 scale
    requirements      = Column(JSON, nullable=True)        # ["level_30", "new_user_only"]

    # --- calculated intelligence ---
    ev_per_hour     = Column(Float, nullable=True)         # $ expected value per hour
    caddie_score    = Column(Float, nullable=True)         # composite ranking 0-100
    is_hot          = Column(Boolean, default=False)       # flagged as >$15/hr

    # --- lifecycle ---
    is_active       = Column(Boolean, default=True)
    expires_at      = Column(DateTime, nullable=True)
    first_seen      = Column(DateTime, default=datetime.utcnow)
    last_seen       = Column(DateTime, default=datetime.utcnow)
    times_seen      = Column(Integer, default=1)

    # --- dual stamp (player + platform verification) ---
    player_verified   = Column(Boolean, default=False)
    platform_verified = Column(Boolean, default=False)

    def __repr__(self):
        return f"<Offer '{self.title}' ${self.payout_amount} ev/hr={self.ev_per_hour}>"


class ScraperRun(Base):
    """Log of every scraper execution for debugging + self-improvement."""
    __tablename__ = "scraper_runs"

    id            = Column(String, primary_key=True, default=_uuid)
    platform_id   = Column(String, ForeignKey("platforms.id"), nullable=False, index=True)
    platform      = relationship("Platform")

    started_at    = Column(DateTime, default=datetime.utcnow)
    finished_at   = Column(DateTime, nullable=True)
    status        = Column(String, default="running")     # running, success, failed, partial
    offers_found  = Column(Integer, default=0)
    offers_new    = Column(Integer, default=0)
    offers_updated = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    run_metadata  = Column(JSON, nullable=True)           # timing, retries, etc.
