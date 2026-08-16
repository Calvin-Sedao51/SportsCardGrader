"""End-to-end golden test: POST /api/scan with real provider parsing and a real
EbayClient, both served canned bytes via httpx.MockTransport.

No scan-module internals are faked: the real Anthropic response parsing, the
real vision-JSON parsing, the real eBay item filtering, comps bucketing, and
verdict math all run. Only the network is replaced.

Fixture arithmetic (ebay_luka_search.json):
  - 10 items; one has no "price" and one has a negative price -> both skipped -> 8 listings
  - raw prices (5):   45.00, 55.00, 62.50, 74.99, 89.00
      -> robust low 55.0 (trimmed floor: second-lowest for 4-7 comps), median 62.5
  - graded prices (3): 150.00, 210.00, 400.00 -> low 150.0 (min for <=3), median 210.0
  - verdict range: value_low 55.0, value_high 62.5
  - undervalued threshold: 0.7 * 55.0 = 38.50  (ask 30 -> undervalued)
  - overpriced threshold:  1.2 * 62.5 = 75.00  (ask 60 -> fair, ask 80 -> overpriced)
"""
import io
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import scan as scan_module
from app.config import Settings, get_settings
from app.ebay import EbayClient
from app.main import app
from app.pricing import EbayActiveSource
from app.schemas import CompsSummary, Condition
from app.verdict import decide
from app.vision.providers import analyze_card

FIXTURES = Path(__file__).parent / "fixtures"
VISION_TEXT = (FIXTURES / "vision_luka_good.json").read_text()
EBAY_BODY = json.loads((FIXTURES / "ebay_luka_search.json").read_text())

# Hand-computed goldens from the fixture (see module docstring).
EXPECTED_RAW_COUNT = 5
EXPECTED_GRADED_COUNT = 3
EXPECTED_RAW_LOW = 55.0  # robust low: second-lowest of the 5 raw comps
EXPECTED_RAW_MEDIAN = 62.5
EXPECTED_GRADED_LOW = 150.0
EXPECTED_GRADED_MEDIAN = 210.0


def _anthropic_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.anthropic.com"
        assert request.headers["x-api-key"] == "test-key"
        return httpx.Response(
            200, json={"content": [{"type": "text", "text": VISION_TEXT}]})
    return httpx.MockTransport(handler)


def _ebay_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/identity/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 7200})
        if request.url.path == "/buy/browse/v1/item_summary/search":
            assert request.headers["Authorization"] == "Bearer tok"
            assert request.url.params["q"] == "2018 Panini Prizm Luka Doncic #280"
            return httpx.Response(200, json=EBAY_BODY)
        return httpx.Response(404)
    return httpx.MockTransport(handler)


@pytest.fixture
def client(monkeypatch):
    """Wire /api/scan end-to-end over mock transports; leak no module state."""
    async def vision_via_mock_transport(front, front_type, back, provider, api_key, model):
        # The REAL analyze_card: prompt build, Anthropic request/response
        # parsing, and vision-JSON validation all run against the mock wire.
        return await analyze_card(front, front_type, back, provider=provider,
                                  api_key=api_key, model=model,
                                  transport=_anthropic_transport())

    monkeypatch.setattr(scan_module, "run_vision", vision_via_mock_transport)
    monkeypatch.setattr(
        scan_module, "get_settings",
        lambda: Settings(_env_file=None, ebay_client_id="x", ebay_client_secret="y"))
    # Real EbayActiveSource adapter; only its HTTP client rides the mock wire.
    source = EbayActiveSource(Settings(_env_file=None, ebay_client_id="x",
                                       ebay_client_secret="y"))
    source._client = EbayClient("x", "y", "production", transport=_ebay_transport())
    scan_module._pricing_source = source
    try:
        yield TestClient(app)
    finally:
        scan_module._pricing_source = None
        get_settings.cache_clear()


def post_scan(client, **form):
    return client.post(
        "/api/scan",
        files={"front": ("card.jpg", io.BytesIO(b"fake-jpeg-bytes"), "image/jpeg")},
        data=form,
        headers={"X-AI-Provider": "anthropic", "X-AI-Key": "test-key"})


def test_scan_end_to_end_undervalued(client):
    resp = post_scan(client, asking_price="30")
    assert resp.status_code == 200
    body = resp.json()

    identity = body["vision"]["identity"]
    assert identity["subject"] == "Luka Doncic"
    assert identity["year"] == "2018"
    assert identity["set_name"] == "Panini Prizm"
    assert identity["card_number"] == "280"
    assert identity["confidence"] == 0.92
    assert body["vision"]["condition"]["grade_low"] == 6
    assert body["vision"]["condition"]["grade_high"] == 8
    assert body["vision"]["authenticity"]["risk"] == "low"
    assert body["comps_error"] is None

    comps = body["comps"]
    assert comps["source"] == "active_listings"
    assert comps["raw_count"] == EXPECTED_RAW_COUNT          # malformed items skipped
    assert comps["graded_count"] == EXPECTED_GRADED_COUNT
    assert comps["raw_low"] == EXPECTED_RAW_LOW
    assert comps["raw_median"] == EXPECTED_RAW_MEDIAN
    assert comps["graded_low"] == EXPECTED_GRADED_LOW
    assert comps["graded_median"] == EXPECTED_GRADED_MEDIAN

    verdict = body["verdict"]
    assert verdict["verdict"] == "undervalued"               # 30 <= 0.7 * 55 = 38.5
    assert verdict["value_low"] == EXPECTED_RAW_LOW
    assert verdict["value_high"] == EXPECTED_RAW_MEDIAN
    assert verdict["reasoning"]


@pytest.mark.parametrize("asking_price,expected", [
    ("60", "fair"),        # 38.5 < 60 < 75.0
    ("80", "overpriced"),  # 80 >= 1.2 * 62.5 = 75.0
])
def test_scan_end_to_end_fair_and_overpriced(client, asking_price, expected):
    body = post_scan(client, asking_price=asking_price).json()
    assert body["verdict"]["verdict"] == expected
    assert body["verdict"]["value_low"] == EXPECTED_RAW_LOW
    assert body["verdict"]["value_high"] == EXPECTED_RAW_MEDIAN


def test_verdict_threshold_goldens():
    """Pin the 0.7 / 1.2 constants: exact-threshold asks must keep their labels."""
    comps = CompsSummary(source="active_listings", raw_count=3,
                         raw_low=100.0, raw_median=200.0, graded_count=0)
    condition = Condition(observations=[], grade_low=6, grade_high=8)

    at_low = decide(comps, condition, 70.0, 0.9)     # ask == 0.7 * 100 (inclusive)
    assert at_low.verdict == "undervalued"

    just_above = decide(comps, condition, 70.01, 0.9)
    assert just_above.verdict == "fair"

    at_high = decide(comps, condition, 240.0, 0.9)   # ask == 1.2 * 200 (inclusive)
    assert at_high.verdict == "overpriced"
