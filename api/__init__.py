from .admin import admin_router
from .offers import offers_router
from .scraper_api import scraper_router
from .scan import scan_router

__all__ = ["admin_router", "offers_router", "scraper_router", "scan_router"]
