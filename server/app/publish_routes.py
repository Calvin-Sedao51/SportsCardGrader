"""Publish cards to The Binder community and read them back.

All Hive machinery hangs off app.state.hive (a HiveState); when Hive is not
configured the app still runs fully — these endpoints just answer 503.
"""
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Annotated, Optional

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app import scan as scan_module
from app.auth_routes import optional_user
from app.hive.client import HiveClient
from app.hive.images import ImageInvalid, ImageUploadError, upload_image
from app.hive.queue import PublishJob, PublishQueue
from app.hive.record import (
    TITLE_CAP,
    TOP_SALES_CAP,
    CardComps,
    CardImages,
    CardRecord,
    CardRecordDraft,
    attribution_from_post,
)
from app.schemas import VisionResult

logger = logging.getLogger(__name__)

router = APIRouter()


FEED_CACHE_TTL_SECONDS = 60.0


@dataclass
class HiveState:
    client: HiveClient
    queue: PublishQueue
    community: str
    http: httpx.AsyncClient
    posting_key: str = field(repr=False, default="")  # never in logs/repr
    fallback_token: str = field(repr=False, default="")
    dry_run: bool = False
    # Feed cache: cursor key -> (expires_monotonic, payload). Cleared when the
    # queue confirms a publish so a fresh card shows on the next refresh.
    feed_cache: dict = field(default_factory=dict)


def _state(request: Request) -> HiveState:
    state = getattr(request.app.state, "hive", None)
    if state is None:
        raise HTTPException(503, "Publishing is not configured on this server.")
    return state


def _job_payload(job: PublishJob, queue: PublishQueue) -> dict:
    return {
        "job_id": job.job_id,
        "permlink": job.permlink,
        "status": job.status,
        "position": queue.position(job.job_id),
        "eta_seconds": queue.eta_for(job.job_id),
        "hive_url": job.hive_url,
        "last_error": job.last_error,
    }


@router.post("/api/publish", status_code=202)
async def publish(
    request: Request,
    record: Annotated[str, Form()],
    front: Annotated[UploadFile, File()],
    back: Annotated[Optional[UploadFile], File()] = None,
):
    state = _state(request)
    user = optional_user(request)  # 401 on a bad token; None when signed out
    try:
        draft = CardRecordDraft.model_validate_json(record)
    except ValidationError as exc:
        raise HTTPException(422, f"Invalid card record: {exc.errors()[0]['msg']}")
    # Identity comes from the token, never from the client's draft.
    draft.attribution = draft.attribution.model_copy(update={
        "user_id": user.id if user else None,
        "hive_display_key": user.hive_display_key if user else None,
        "display_name": user.display_name if user else None,
    })

    existing = state.queue.get_job(draft.record_id)
    if existing is not None:
        # Idempotent re-submit (offline retry, double tap): same job back.
        return JSONResponse(status_code=200,
                            content=_job_payload(existing, state.queue))

    async def _upload(upload: UploadFile) -> str:
        if state.dry_run:
            # Rehearsal mode: uploads are permanent, so never touch the CDN.
            return f"dry-run://{upload.filename or 'card.jpg'}"
        try:
            return await upload_image(
                await upload.read(), upload.filename or "card.jpg",
                account=state.client.account, posting_key=state.posting_key,
                http=state.http, fallback_token=state.fallback_token)
        except ImageInvalid as exc:
            raise HTTPException(422, str(exc))
        except ImageUploadError as exc:
            raise HTTPException(502, str(exc))

    images = CardImages(front=await _upload(front),
                        back=await _upload(back) if back is not None else None)
    full = CardRecord(**draft.model_dump(), images=images)
    job = state.queue.enqueue(full, user_id=user.id if user else None)
    return _job_payload(job, state.queue)


@router.get("/api/publish/{job_id}")
async def publish_status(request: Request, job_id: str):
    state = _state(request)
    job = state.queue.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Unknown publish job.")
    return _job_payload(job, state.queue)


def _parse_card_post(post: dict) -> Optional[dict]:
    """A bridge post -> feed entry, or None when it isn't a valid app card.

    The community is open — humans can post in The Binder too — so anything
    that doesn't carry a valid v1 card record is silently skipped.
    """
    meta = post.get("json_metadata") or {}
    if not isinstance(meta, dict) or not isinstance(meta.get("card"), dict):
        return None
    try:
        card = CardRecord.model_validate(meta["card"])
    except ValidationError:
        return None
    if card.v != 1:
        return None
    attribution = attribution_from_post(post) or card.attribution
    return {"permlink": post["permlink"], "author": post["author"],
            "created": post.get("created"), "card": card.model_dump(mode="json"),
            "attribution": attribution.model_dump(mode="json")}


@router.get("/api/cards")
async def list_cards(request: Request, limit: int = 20, start_author: str = "",
                     start_permlink: str = "", all_authors: bool = False,
                     owner: str = ""):
    """The community feed; `owner=<hive_display_key>` narrows it to one
    collector's cards ("My Collection") — same feed, filtered server-side so
    paging over a busy community doesn't hide a user's older cards."""
    state = _state(request)
    key = (limit, start_author, start_permlink, all_authors, owner)
    cached = state.feed_cache.get(key)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    posts = await state.client.get_ranked_posts(
        sort="created", tag=state.community, limit=limit,
        start_author=start_author, start_permlink=start_permlink)
    cards = []
    for post in posts:
        entry = _parse_card_post(post)
        if entry is None:
            continue
        if not all_authors and entry["author"] != state.client.account:
            continue
        if owner and entry["attribution"].get("hive_display_key") != owner:
            continue
        cards.append(entry)
    # Cursor from the last RAW post: filtering must never skip a page.
    next_cursor = None
    if len(posts) >= limit and posts:
        next_cursor = {"start_author": posts[-1]["author"],
                       "start_permlink": posts[-1]["permlink"]}
    payload = {"cards": cards, "next": next_cursor}
    state.feed_cache[key] = (time.monotonic() + FEED_CACHE_TTL_SECONDS, payload)
    return payload


@router.get("/api/cards/{permlink}")
async def card_detail(request: Request, permlink: str):
    state = _state(request)
    post = await state.client.get_post(state.client.account, permlink)
    entry = _parse_card_post(post) if post else None
    if entry is None:
        raise HTTPException(404, "No such card in The Binder.")
    entry["hive_url"] = f"https://peakd.com/@{state.client.account}/{permlink}"
    return entry


@router.post("/api/cards/{permlink}/refresh-comps", status_code=202)
async def refresh_card_comps(request: Request, permlink: str):
    """Re-run comps + verdict for a published card and queue a post edit."""
    state = _state(request)
    post = await state.client.get_post(state.client.account, permlink)
    entry = _parse_card_post(post) if post else None
    if entry is None:
        raise HTTPException(404, "No such card in The Binder.")
    record = CardRecord.model_validate(entry["card"])
    vision = VisionResult(photo_ok=True, identity=record.identity,
                          condition=record.condition, slab=record.slab,
                          authenticity=record.authenticity)
    comps, verdict, listings, comps_error = await scan_module.price_vision(
        vision, record.asking_price)
    if comps_error is not None or comps is None:
        raise HTTPException(502, f"Comps refresh failed: {comps_error or 'no data'}")
    updated = record.model_copy(update={
        "comps": CardComps(
            summary=comps,
            top_sales=[item.model_copy(update={"title": item.title[:TITLE_CAP]})
                       for item in listings[:TOP_SALES_CAP]],
            as_of=datetime.now(timezone.utc).isoformat(timespec="seconds")),
        "verdict": verdict,
    })
    job = state.queue.enqueue(updated, kind="update")
    return _job_payload(job, state.queue)


@router.get("/api/hive/status")
async def hive_status(request: Request):
    state = getattr(request.app.state, "hive", None)
    if state is None:
        return {"configured": False}
    try:
        rc_percent = await state.client.get_rc_percent()
    except Exception:  # noqa: BLE001 — status must not 500 on node flakiness
        rc_percent = None
    queue_status = state.queue.status()
    return {
        "configured": True,
        "account": state.client.account,
        "community": state.community,
        "dry_run": state.dry_run,
        "rc_percent": rc_percent,
        "queue_depth": queue_status["depth"],
        "eta_seconds": queue_status["eta_seconds"],
    }
