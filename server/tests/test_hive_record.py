import json
import re
from pathlib import Path

from app.hive.record import (
    Attribution,
    CardImages,
    CardRecord,
    build_tags,
    card_permlink,
    from_scan_response,
    slugify,
)
from app.schemas import CompListing, CompsSummary, ScanResponse, VisionResult

FIXTURES = Path(__file__).parent / "fixtures"
COMMUNITY = "hive-192941"


def luka_scan() -> ScanResponse:
    vision = VisionResult(**json.loads((FIXTURES / "vision_luka_good.json").read_text()))
    comps = CompsSummary(source="active_listings", raw_count=12, raw_low=30.0,
                         raw_median=47.0, graded_count=5, graded_low=90.0, graded_median=140.0)
    return ScanResponse(vision=vision, comps=comps)


def make_record(**overrides) -> CardRecord:
    listings = [CompListing(title=f"listing {i} " + "x" * 200, price=10.0 + i, graded=False)
                for i in range(25)]
    base = from_scan_response(
        luka_scan(),
        record_id="4fef9db2-9f3a-4c5e-8f6d-0123456789ab",
        images=CardImages(front="https://images.hive.blog/DQm/front.jpg"),
        attribution=Attribution(client_id="c1"),
        scanned_at="2026-08-16T12:00:00Z",
        asking_price=55.0,
        top_sales=listings,
    )
    return base.model_copy(update=overrides)


def test_from_scan_response_maps_fields():
    record = make_record()
    assert record.v == 1 and record.kind == "card"
    assert record.identity.subject == "Luka Doncic"
    assert record.condition.grade_low == 6
    assert record.comps.summary.raw_median == 47.0
    assert record.comps.as_of  # stamped
    assert record.asking_price == 55.0


def test_top_sales_capped_and_titles_truncated():
    record = make_record()
    assert len(record.comps.top_sales) == 10
    assert all(len(item.title) <= 80 for item in record.comps.top_sales)


def test_slugify():
    assert slugify("Luka Dončić!!", 32) == "luka-doncic"
    assert slugify("  Panini   Prizm  ", 10) == "panini-pri"
    assert slugify("###", 10) == ""


def test_build_tags_community_first_and_valid():
    tags = build_tags(make_record().identity, COMMUNITY)
    assert tags[0] == COMMUNITY
    assert "sportscards" in tags and "cardscanner" in tags
    assert "sc-luka-doncic" in tags and "sc-2018" in tags and "sc-panini-prizm" in tags
    assert len(tags) <= 8
    for tag in tags:
        assert re.fullmatch(r"[a-z][a-z0-9-]*", tag), tag


def test_permlink_deterministic_and_safe():
    a, b = make_record(), make_record()
    assert card_permlink(a) == card_permlink(b)
    assert card_permlink(a).startswith("card-luka-doncic-2018-")
    assert re.fullmatch(r"[a-z0-9-]+", card_permlink(a))
    assert card_permlink(make_record(record_id="different")) != card_permlink(a)


def test_json_metadata_stays_small():
    # The whole embedded record — with maximal top_sales — must stay well
    # under the 8 KB envelope budget enforced by the post builder.
    payload = make_record().model_dump_json()
    assert len(payload) < 7000
