"""Shared test hygiene: pricing/catalog-source and settings state must never leak
between test files (the scan module holds a lazy process-wide singleton)."""

import pytest

from app import scan as scan_module
from app.config import get_settings


@pytest.fixture(autouse=True)
def _reset_pricing_state(monkeypatch):
    # Tests never reach the real card databases; catalog tests opt back in by
    # stubbing scan_module.lookup_catalog or passing a MockTransport.
    monkeypatch.setenv("CATALOG_ENABLED", "false")
    scan_module._pricing_source = None
    scan_module._catalog_sources.clear()
    get_settings.cache_clear()
    yield
    scan_module._pricing_source = None
    scan_module._catalog_sources.clear()
    get_settings.cache_clear()
