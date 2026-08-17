"""Pricing-source adapter: registry resolution, misconfiguration errors, and a
fake sold-price source wired through perform_scan — the exact path a
self-hoster with a paid sold-data provider would use."""
import asyncio

import pytest

from app import scan as scan_module
from app.config import Settings, get_settings
from app.pricing import EbayActiveSource, get_pricing_source
from app.schemas import (Authenticity, CompListing, Condition, Identity,
                         VisionResult)

GOOD_VISION = VisionResult(
    photo_ok=True,
    identity=Identity(subject="Luka Doncic", year="2018", set_name="Panini Prizm",
                      card_number="280", search_string="2018 Panini Prizm Luka Doncic #280",
                      confidence=0.92),
    condition=Condition(observations=[], grade_low=6, grade_high=8),
    authenticity=Authenticity(red_flags=[], risk="low"),
)


@pytest.fixture(autouse=True)
def clean_state():
    """No singleton/cache leakage in either direction (mirrors test_integration)."""
    scan_module._pricing_source = None
    get_settings.cache_clear()
    yield
    scan_module._pricing_source = None
    get_settings.cache_clear()


def test_default_settings_resolve_ebay_active_source():
    source = get_pricing_source(Settings(_env_file=None))
    assert isinstance(source, EbayActiveSource)
    assert source.source_type == "active_listings"


def test_unknown_pricing_source_raises_with_valid_names():
    with pytest.raises(RuntimeError) as exc:
        get_pricing_source(Settings(_env_file=None, pricing_source="pricecharting"))
    assert "pricecharting" in str(exc.value)
    assert "ebay_active" in str(exc.value)  # tells the operator what IS valid


def test_ebay_source_without_credentials_raises_not_configured():
    source = get_pricing_source(Settings(_env_file=None))
    with pytest.raises(RuntimeError, match="not configured"):
        asyncio.run(source.search("luka"))


def test_sold_source_flows_through_scan_to_verdict(monkeypatch):
    """A custom sold source must surface as comps.source == 'sold' and produce
    verdict reasoning worded around sold prices — no pipeline edits needed."""
    class FakeSoldSource:
        source_type = "sold"

        async def search(self, query: str, category="sports") -> list[CompListing]:
            return [CompListing(title=f"Sold Luka {i}", price=40.0 + i, graded=False)
                    for i in range(3)]

    async def fake_vision(*a, **k):
        return GOOD_VISION

    monkeypatch.setattr(scan_module, "run_vision", fake_vision)
    monkeypatch.setattr(scan_module, "_pricing_source", FakeSoldSource())

    resp = asyncio.run(scan_module.perform_scan(
        b"fake", "image/jpeg", None, "anthropic", "k", "gpt", asking_price=None))

    assert resp.comps_error is None
    assert resp.comps is not None and resp.comps.source == "sold"
    assert resp.comps.raw_count == 3
    assert resp.verdict is not None
    assert "sold prices" in resp.verdict.reasoning
    assert "asking" not in resp.verdict.reasoning


def test_unknown_source_surfaces_as_comps_error_not_crash(monkeypatch):
    """Misconfigured PRICING_SOURCE degrades to a self-diagnosing comps_error
    (vision results still returned), not a 500."""
    async def fake_vision(*a, **k):
        return GOOD_VISION

    monkeypatch.setattr(scan_module, "run_vision", fake_vision)
    monkeypatch.setattr(
        scan_module, "get_settings",
        lambda: Settings(_env_file=None, pricing_source="typo_source"))

    resp = asyncio.run(scan_module.perform_scan(
        b"fake", "image/jpeg", None, "anthropic", "k", "gpt", asking_price=None))

    assert resp.vision.identity is not None
    assert resp.comps is None and resp.verdict is None
    assert "typo_source" in resp.comps_error and "ebay_active" in resp.comps_error
