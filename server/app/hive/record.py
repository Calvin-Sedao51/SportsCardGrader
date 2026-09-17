"""Canonical card record embedded in a Binder post's json_metadata.card.

Mirrored field-for-field in web/src/binderTypes.ts — keep them in sync.
Reuses the scan schemas verbatim so a published record round-trips into the
same UI components that render a live scan.
"""
import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel

from app.schemas import (
    Authenticity,
    CompListing,
    CompsSummary,
    Condition,
    Identity,
    ScanResponse,
    Slab,
    Verdict,
)

TOP_SALES_CAP = 10
TITLE_CAP = 80

GAME_TAG = {"sports": "sportscards", "pokemon": "pokemon", "yugioh": "yugioh",
            "magic": "mtg", "onepiece": "onepiece",
            "other_tcg": "tradingcards", "other": "tradingcards"}


class CardComps(BaseModel):
    summary: CompsSummary
    top_sales: list[CompListing] = []
    as_of: str  # ISO timestamp of the comps fetch


class CardImages(BaseModel):
    front: str  # images.hive.blog URL
    back: Optional[str] = None


class Attribution(BaseModel):
    """Who scanned the card. v1 identity model: one shared posting account,
    so the user is identified by this block, not by the post author.

    The server stamps user_id/hive_display_key/display_name from the
    signed-in user (publish_routes) and strips them on anonymous publishes —
    a client can never claim an identity. Email and the raw Google sub never
    appear here (they'd be on chain forever).
    """
    client_id: str  # anonymous per-install UUID; never an identity claim
    display_name: Optional[str] = None
    user_id: Optional[str] = None  # app user id; None = anonymous scan
    hive_display_key: Optional[str] = None  # public collector handle 'binder-<8 hex>'


ATTRIBUTION_IDENTITY_FIELDS = ("user_id", "hive_display_key", "display_name")


class CardRecordDraft(BaseModel):
    """What the client submits: a record before images are uploaded."""
    v: Literal[1] = 1
    kind: Literal["card"] = "card"
    record_id: str  # client UUID — idempotency key for the publish queue
    identity: Identity
    condition: Optional[Condition] = None
    slab: Optional[Slab] = None
    authenticity: Optional[Authenticity] = None
    verdict: Optional[Verdict] = None
    comps: Optional[CardComps] = None
    asking_price: Optional[float] = None
    attribution: Attribution
    scanned_at: str


class CardRecord(CardRecordDraft):
    images: CardImages


def attribution_from_post(post: dict) -> Optional[Attribution]:
    """Extract attribution from a bridge post; None when it isn't an app card.

    Reads the top-level json_metadata.attribution block (what My Collection
    filters on) and falls back to card.attribution for posts published before
    that block existed, so older cards still resolve to their scanner.
    """
    meta = post.get("json_metadata") if isinstance(post, dict) else None
    if not isinstance(meta, dict) or not isinstance(meta.get("card"), dict):
        return None
    base = meta["card"].get("attribution")
    if not isinstance(base, dict):
        return None
    merged = dict(base)
    block = meta.get("attribution")
    if isinstance(block, dict):
        merged.update({k: block.get(k) for k in ATTRIBUTION_IDENTITY_FIELDS if k in block})
    try:
        return Attribution.model_validate(merged)
    except ValueError:
        return None


def slugify(value: str, max_len: int) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value.lower()).strip("-")
    return slug[:max_len].strip("-")


def build_tags(identity: Identity, community: str) -> list[str]:
    """First tag MUST be the community (that's what assigns the post to it).

    Derived tags are sc- prefixed so they always start with a letter and never
    collide with organic tags. Hive tags must match [a-z][a-z0-9-]*.
    """
    tags = [community, GAME_TAG[identity.category], "cardscanner"]
    for raw, cap in ((identity.subject, 32), (identity.year, 12), (identity.set_name, 24)):
        slug = slugify(raw or "", cap)
        if slug:
            tag = f"sc-{slug}"
            if tag not in tags:
                tags.append(tag)
    return tags[:8]


def card_permlink(record: CardRecordDraft) -> str:
    """Deterministic for a record_id: an accidental second publish attempt
    targets the same permlink (an edit) instead of creating a duplicate post."""
    subject = slugify(record.identity.subject, 32) or "unknown"
    year = slugify(record.identity.year, 12) or "na"
    digest = hashlib.sha256(record.record_id.encode()).hexdigest()[:8]
    return f"card-{subject}-{year}-{digest}"


def from_scan_response(scan: ScanResponse, *, record_id: str, images: CardImages,
                       attribution: Attribution, scanned_at: str,
                       asking_price: Optional[float] = None,
                       top_sales: Optional[list[CompListing]] = None,
                       comps_as_of: Optional[str] = None) -> CardRecord:
    comps = None
    if scan.comps is not None:
        capped = [
            listing.model_copy(update={"title": listing.title[:TITLE_CAP]})
            for listing in (top_sales or [])[:TOP_SALES_CAP]
        ]
        comps = CardComps(
            summary=scan.comps, top_sales=capped,
            as_of=comps_as_of or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
    return CardRecord(
        record_id=record_id,
        identity=scan.vision.identity,
        condition=scan.vision.condition,
        slab=scan.vision.slab,
        authenticity=scan.vision.authenticity,
        verdict=scan.verdict,
        comps=comps,
        images=images,
        asking_price=asking_price,
        attribution=attribution,
        scanned_at=scanned_at,
    )
