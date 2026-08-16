import asyncio
import io

import httpx
import pytest
from fastapi.testclient import TestClient

from app import scan as scan_module
from app.config import Settings
from app.main import app
from app.schemas import (Authenticity, CompListing, Condition, Identity,
                         Slab, VisionResult)
from app.vision.prompt import VisionParseError
from app.vision.providers import ProviderAuthError, ProviderRateLimited

GOOD_VISION = VisionResult(
    photo_ok=True,
    identity=Identity(subject="Luka Doncic", year="2018", set_name="Panini Prizm",
                      card_number="280", search_string="2018 Panini Prizm Luka Doncic #280",
                      confidence=0.92),
    condition=Condition(observations=[], grade_low=6, grade_high=8),
    authenticity=Authenticity(red_flags=[], risk="low"),
)
LISTINGS = [CompListing(title=f"Luka raw {i}", price=50.0 + i, graded=False) for i in range(4)]

SLAB_VISION = VisionResult(
    photo_ok=True,
    identity=Identity(subject="Luka Doncic", year="2018", set_name="Panini Prizm",
                      card_number="280",
                      search_string="2018 Panini Prizm Luka Doncic #280 PSA 9",
                      confidence=0.92),
    condition=None,  # the slab already graded it
    slab=Slab(company="PSA", grade="9"),
    authenticity=Authenticity(red_flags=[], risk="low"),
)
SLAB_LISTINGS = [CompListing(title=f"Luka Doncic Prizm PSA 9 {i}", price=100.0 + i,
                             graded=True) for i in range(4)]


def post_scan(client, **form):
    return client.post("/api/scan",
        files={"front": ("card.jpg", io.BytesIO(b"fake"), "image/jpeg")},
        data=form,
        headers={"X-AI-Provider": "anthropic", "X-AI-Key": "k"})


@pytest.fixture
def client():
    # No lifespan events on this app, so a non-context-managed TestClient is fine.
    return TestClient(app)


def test_full_scan_happy_path(client, monkeypatch):
    async def fake_vision(*a, **k): return GOOD_VISION
    async def fake_search(q): return LISTINGS
    monkeypatch.setattr(scan_module, "run_vision", fake_vision)
    monkeypatch.setattr(scan_module, "search_comps", fake_search)
    resp = post_scan(client, asking_price="30")
    assert resp.status_code == 200
    body = resp.json()
    assert body["vision"]["identity"]["subject"] == "Luka Doncic"
    assert body["comps"]["raw_count"] == 4
    assert body["verdict"]["verdict"] == "undervalued"


def test_high_authenticity_risk_vetoes_price_verdict(client, monkeypatch):
    # End-to-end trust check: a likely counterfeit with a bargain ask must
    # surface authenticity_risk, never "undervalued".
    risky = GOOD_VISION.model_copy(update={
        "authenticity": Authenticity(red_flags=["print pattern looks off"], risk="high")})
    async def fake_vision(*a, **k): return risky
    async def fake_search(q): return LISTINGS
    monkeypatch.setattr(scan_module, "run_vision", fake_vision)
    monkeypatch.setattr(scan_module, "search_comps", fake_search)
    body = post_scan(client, asking_price="30").json()
    assert body["verdict"]["verdict"] == "authenticity_risk"
    assert body["verdict"]["value_low"] is not None


def test_slabbed_scan_happy_path(client, monkeypatch):
    # A slabbed card has condition=null — the scan must not short-circuit,
    # and the verdict must price from same-grade graded comps.
    async def fake_vision(*a, **k): return SLAB_VISION
    async def fake_search(q): return SLAB_LISTINGS
    monkeypatch.setattr(scan_module, "run_vision", fake_vision)
    monkeypatch.setattr(scan_module, "search_comps", fake_search)
    resp = post_scan(client, asking_price="110")
    assert resp.status_code == 200
    body = resp.json()
    assert body["vision"]["condition"] is None
    assert body["vision"]["slab"] == {"company": "PSA", "grade": "9"}
    assert "PSA 9" in body["verdict"]["reasoning"]
    assert body["verdict"]["verdict"] == "fair"
    # The comps field carries the summary that priced the card: matching-grade,
    # so the raw side is empty and all four PSA 9 listings are in the bucket.
    assert body["comps"]["raw_count"] == 0
    assert body["comps"]["graded_count"] == 4


def test_slabbed_scan_falls_back_to_overall_graded_comps(client, monkeypatch):
    # Listings graded, but not the slab's grade: mixed-grade fallback with the
    # overall summary in the comps field.
    mixed = [CompListing(title=f"Luka Doncic Prizm PSA 10 {i}", price=400.0 + i,
                         graded=True) for i in range(4)]
    async def fake_vision(*a, **k): return SLAB_VISION
    async def fake_search(q): return mixed
    monkeypatch.setattr(scan_module, "run_vision", fake_vision)
    monkeypatch.setattr(scan_module, "search_comps", fake_search)
    body = post_scan(client).json()
    assert "mixed grades" in body["verdict"]["reasoning"]
    assert body["comps"]["graded_count"] == 4


def test_bad_photo_short_circuits(client, monkeypatch):
    async def fake_vision(*a, **k):
        return VisionResult(photo_ok=False, photo_issue="too much glare")
    monkeypatch.setattr(scan_module, "run_vision", fake_vision)
    body = post_scan(client).json()
    assert body["vision"]["photo_ok"] is False
    assert body["comps"] is None and body["verdict"] is None


def test_ebay_failure_returns_partial(client, monkeypatch):
    async def fake_vision(*a, **k): return GOOD_VISION
    async def broken_search(q): raise RuntimeError("ebay down")
    monkeypatch.setattr(scan_module, "run_vision", fake_vision)
    monkeypatch.setattr(scan_module, "search_comps", broken_search)
    body = post_scan(client, asking_price="30").json()
    assert body["vision"]["identity"] is not None
    assert body["comps"] is None and body["comps_error"]
    assert body["verdict"] is None


def test_ebay_not_configured_returns_partial(client, monkeypatch):
    async def fake_vision(*a, **k): return GOOD_VISION
    async def not_configured(q):
        raise RuntimeError("eBay credentials not configured on this server")
    monkeypatch.setattr(scan_module, "run_vision", fake_vision)
    monkeypatch.setattr(scan_module, "search_comps", not_configured)
    body = post_scan(client).json()
    assert body["comps"] is None
    assert "not configured" in body["comps_error"]
    assert body["verdict"] is None


def test_search_comps_gate_without_credentials(client, monkeypatch):
    # Exercise the real search_comps() gate: empty creds -> RuntimeError -> comps_error.
    async def fake_vision(*a, **k): return GOOD_VISION
    monkeypatch.setattr(scan_module, "run_vision", fake_vision)
    # Drop any singleton a previous test built, so the patched settings below
    # actually drive the source this test claims to exercise.
    monkeypatch.setattr(scan_module, "_pricing_source", None)
    monkeypatch.setattr(
        scan_module, "get_settings",
        lambda: Settings(_env_file=None, ebay_client_id="", ebay_client_secret=""))
    body = post_scan(client).json()
    assert body["comps"] is None
    assert "not configured" in body["comps_error"]


def test_scan_timeout_is_504(client, monkeypatch):
    # A pipeline that outlives the budget must come back as a 504, not hang.
    async def stuck_scan(*a, **k):
        await asyncio.sleep(5)
    monkeypatch.setattr(scan_module, "perform_scan", stuck_scan)
    monkeypatch.setattr("app.main.SCAN_TIMEOUT_SECONDS", 0.05)
    resp = post_scan(client)
    assert resp.status_code == 504
    assert "Scan timed out" in resp.json()["detail"]


def test_scan_within_timeout_budget_succeeds(client, monkeypatch):
    # The timeout wrapper must not disturb a scan that finishes in time,
    # even with a tight budget.
    async def fake_vision(*a, **k): return GOOD_VISION
    async def fake_search(q): return LISTINGS
    monkeypatch.setattr(scan_module, "run_vision", fake_vision)
    monkeypatch.setattr(scan_module, "search_comps", fake_search)
    monkeypatch.setattr("app.main.SCAN_TIMEOUT_SECONDS", 5.0)
    resp = post_scan(client, asking_price="30")
    assert resp.status_code == 200
    assert resp.json()["verdict"]["verdict"] == "undervalued"


def test_invalid_ai_key_is_401(client, monkeypatch):
    async def fake_vision(*a, **k): raise ProviderAuthError("bad key")
    monkeypatch.setattr(scan_module, "run_vision", fake_vision)
    assert post_scan(client).status_code == 401


def test_rate_limited_is_429(client, monkeypatch):
    async def fake_vision(*a, **k): raise ProviderRateLimited("slow down")
    monkeypatch.setattr(scan_module, "run_vision", fake_vision)
    assert post_scan(client).status_code == 429


def test_vision_parse_error_is_502(client, monkeypatch):
    async def fake_vision(*a, **k): raise VisionParseError("garbage from model")
    monkeypatch.setattr(scan_module, "run_vision", fake_vision)
    assert post_scan(client).status_code == 502


def test_vision_http_status_error_is_502(client, monkeypatch):
    request = httpx.Request("POST", "https://ai.example/v1")
    async def fake_vision(*a, **k):
        raise httpx.HTTPStatusError(
            "server error", request=request,
            response=httpx.Response(500, request=request))
    monkeypatch.setattr(scan_module, "run_vision", fake_vision)
    assert post_scan(client).status_code == 502


def test_vision_transport_error_is_502(client, monkeypatch):
    async def fake_vision(*a, **k): raise httpx.ConnectError("no route to host")
    monkeypatch.setattr(scan_module, "run_vision", fake_vision)
    assert post_scan(client).status_code == 502


def test_invalid_provider_is_422(client, monkeypatch):
    async def fake_vision(*a, **k): return GOOD_VISION
    monkeypatch.setattr(scan_module, "run_vision", fake_vision)
    resp = client.post("/api/scan",
        files={"front": ("card.jpg", io.BytesIO(b"fake"), "image/jpeg")},
        headers={"X-AI-Provider": "gemini", "X-AI-Key": "k"})
    assert resp.status_code == 422


def test_negative_asking_price_is_422(client, monkeypatch):
    async def fake_vision(*a, **k): return GOOD_VISION
    monkeypatch.setattr(scan_module, "run_vision", fake_vision)
    assert post_scan(client, asking_price="-5").status_code == 422


def test_missing_headers_is_422(client):
    resp = client.post("/api/scan",
        files={"front": ("card.jpg", io.BytesIO(b"fake"), "image/jpeg")})
    assert resp.status_code == 422


def test_oversized_upload_is_413(client, monkeypatch):
    from app.main import MAX_UPLOAD_BYTES

    monkeypatch.setattr("app.main.MAX_UPLOAD_BYTES", 100)
    resp = client.post(
        "/api/scan",
        files={"front": ("card.jpg", io.BytesIO(b"x" * 200), "image/jpeg")},
        headers={"X-AI-Provider": "anthropic", "X-AI-Key": "k"},
    )
    assert resp.status_code == 413
