"""Shared test hygiene: pricing/catalog-source and settings state must never leak
between test files (the scan module holds a lazy process-wide singleton)."""

import pytest

from app import scan as scan_module
from app.config import get_settings


@pytest.fixture(autouse=True)
def _reset_pricing_state(monkeypatch, tmp_path):
    # Tests never reach the real card databases; catalog tests opt back in by
    # stubbing scan_module.lookup_catalog or passing a MockTransport.
    monkeypatch.setenv("CATALOG_ENABLED", "false")
    # A synced server/data/sportsdb.sqlite on this machine must not change
    # which sports catalog source the registry picks.
    monkeypatch.setenv("SPORTSDB_PATH", str(tmp_path / "no-sportsdb.sqlite"))
    scan_module._pricing_source = None
    scan_module._catalog_sources.clear()
    get_settings.cache_clear()
    yield
    scan_module._pricing_source = None
    scan_module._catalog_sources.clear()
    get_settings.cache_clear()
