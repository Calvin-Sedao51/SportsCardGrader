import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Optional

import httpx
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from app import scan as scan_module
from app.auth import TokeninfoVerifier
from app.auth_routes import AuthState, router as auth_router
from app.config import get_settings
from app.hive.client import HiveClient
from app.hive.queue import PublishQueue
from app.publish_routes import HiveState, PublishRateLimiter, router as publish_router
from app.hive.identity import HIVE_ACCOUNT_MODES
from app.schemas import ScanResponse
from app.users import UserStore
from app.vision.prompt import VisionParseError
from app.vision.providers import ProviderAuthError, ProviderRateLimited


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.hive_account_mode != "shared":
        # "per_user" is a dormant shim (app/hive/identity.py) — refuse loudly
        # rather than run with a mode the publish path cannot honor.
        raise RuntimeError(f"HIVE_ACCOUNT_MODE={settings.hive_account_mode!r} is not implemented; "
                           f"known modes: {HIVE_ACCOUNT_MODES}, only 'shared' runs in v1")
    stop = asyncio.Event()
    worker: Optional[asyncio.Task] = None
    auth_http: Optional[httpx.AsyncClient] = None
    if settings.auth_configured:
        auth_http = httpx.AsyncClient(timeout=10.0)
        app.state.auth = AuthState(
            verifier=TokeninfoVerifier(auth_http), client_id=settings.auth_google_client_id,
            secret=settings.auth_jwt_secret, users=UserStore(settings.auth_users_file),
            shared_hive_account=settings.hive_account,
            hive_account_mode=settings.hive_account_mode)
    if settings.hive_configured:
        client = HiveClient(settings.hive_node_list, account=settings.hive_account,
                            posting_key=settings.hive_posting_key,
                            dry_run=settings.hive_dry_run)
        queue = PublishQueue(
            Path(settings.hive_queue_dir), client, settings.hive_community,
            root_interval=settings.hive_root_post_interval_seconds,
            min_rc_percent=settings.hive_min_rc_percent)
        app.state.hive = HiveState(
            client=client, queue=queue, community=settings.hive_community,
            http=httpx.AsyncClient(timeout=30.0),
            posting_key=settings.hive_posting_key,
            fallback_token=settings.images_3speak_token,
            dry_run=settings.hive_dry_run,
            rate_limiter=PublishRateLimiter(max_per_window=settings.publish_rate_budget))
        # A freshly confirmed card must show on the next Binder refresh.
        queue.on_confirmed = lambda _job: app.state.hive.feed_cache.clear()
        worker = asyncio.create_task(queue.run_forever(stop))
    yield
    if worker is not None:
        stop.set()
        await worker
        await app.state.hive.http.aclose()
    if auth_http is not None:
        await auth_http.aclose()


app = FastAPI(title="Card Scanner API", version="0.1.0", lifespan=lifespan)
app.state.hive = None  # set by lifespan when HIVE_ACCOUNT/HIVE_POSTING_KEY exist
app.state.auth = None  # set by lifespan when AUTH_GOOGLE_CLIENT_ID/AUTH_JWT_SECRET exist
app.include_router(publish_router)
app.include_router(auth_router)

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # public endpoint; phone photos are well under this

# Hard budget for the whole scan pipeline (vision + comps). Kept under 60s so
# we time out cleanly before typical platform/LB request timeouts cut the
# connection mid-flight — the LB window must exceed this (see DEPLOY.md).
SCAN_TIMEOUT_SECONDS = 55.0

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # public, keyless API surface; the AI key is the user's own
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/scan", response_model=ScanResponse)
async def scan(
    front: Annotated[UploadFile, File()],
    x_ai_provider: Annotated[str, Header()],
    x_ai_key: Annotated[str, Header()],
    x_ai_model: Annotated[Optional[str], Header()] = None,
    back: Annotated[Optional[UploadFile], File()] = None,
    asking_price: Annotated[Optional[float], Form()] = None,
):
    settings = get_settings()
    if x_ai_provider not in ("anthropic", "openai"):
        raise HTTPException(422, "X-AI-Provider must be 'anthropic' or 'openai'")
    if asking_price is not None and asking_price < 0:
        raise HTTPException(422, "asking_price must be >= 0")
    model = x_ai_model or (settings.anthropic_default_model if x_ai_provider == "anthropic"
                           else settings.openai_default_model)
    front_bytes = await front.read()
    if len(front_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Image too large (20MB max)")
    back_tuple = None
    if back is not None:
        back_bytes = await back.read()
        if len(back_bytes) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Image too large (20MB max)")
        back_tuple = (back_bytes, back.content_type or "image/jpeg")
    # Only vision-path errors surface here; eBay failures become a partial
    # result (comps_error) inside perform_scan.
    try:
        async with asyncio.timeout(SCAN_TIMEOUT_SECONDS):
            return await scan_module.perform_scan(
                front_bytes, front.content_type or "image/jpeg", back_tuple,
                provider=x_ai_provider, api_key=x_ai_key, model=model,
                asking_price=asking_price)
    # TimeoutError first: it must never be swallowed by a broader handler
    # (asyncio.timeout raises the builtin TimeoutError on Python 3.11+).
    except TimeoutError:
        raise HTTPException(504, "Scan timed out. Try again — a clearer photo often helps.")
    except ProviderAuthError:
        raise HTTPException(401, "Your AI provider rejected the API key. Check it in Settings.")
    except ProviderRateLimited:
        raise HTTPException(429, "Your AI provider is rate limiting. Wait a moment and retry.")
    except VisionParseError:
        raise HTTPException(502, "The AI returned an unreadable analysis. Try scanning again.")
    except httpx.HTTPStatusError:
        raise HTTPException(502, "Your AI provider returned an error. Try again.")
    except httpx.TransportError:
        raise HTTPException(502, "Could not reach your AI provider. Check your connection.")
