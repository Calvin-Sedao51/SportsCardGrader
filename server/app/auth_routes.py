"""Sign-in routes. All auth machinery hangs off app.state.auth (an AuthState);
when AUTH_GOOGLE_CLIENT_ID / AUTH_JWT_SECRET are unset the app still runs
fully as the anonymous "just scan" app — these endpoints answer 503 and every
other endpoint behaves exactly as before (auth is additive).
"""
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.auth import AuthError, GoogleVerifier, check_google_claims, issue_app_token, read_app_token
from app.hive.identity import hive_presence
from app.users import User, UserStore

router = APIRouter()


@dataclass
class AuthState:
    verifier: GoogleVerifier
    client_id: str
    secret: str = field(repr=False)  # never in logs/repr
    users: UserStore
    shared_hive_account: str = ""
    hive_account_mode: str = "shared"
    clock: Callable[[], float] = time.time


def _auth_state(request: Request) -> AuthState:
    state = getattr(request.app.state, "auth", None)
    if state is None:
        raise HTTPException(503, "Sign-in is not configured on this server.")
    return state


def _public_user(user: User) -> dict:
    # google_sub stays server-side; email only goes back to its owner.
    return {"id": user.id, "email": user.email, "display_name": user.display_name,
            "hive_display_key": user.hive_display_key, "created_at": user.created_at}


def optional_user(request: Request) -> Optional[User]:
    """The signed-in user for a Bearer token, None when no header is sent.

    A header that IS sent but is bad raises 401: silently downgrading to an
    anonymous publish would strip attribution the user believes they have.
    """
    header = request.headers.get("authorization", "")
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(401, "Malformed Authorization header.")
    state = _auth_state(request)
    try:
        claims = read_app_token(token.strip(), secret=state.secret, now=state.clock())
    except AuthError:
        raise HTTPException(401, "Your sign-in has expired. Sign in again.")
    user = state.users.get(claims.user_id)
    if user is None:
        # Token outlived the file store (fresh disk): self-heal from the claims.
        user, _ = state.users.get_or_create(claims.sub, email=claims.email, display_name=None)
    return user


def require_user(request: Request) -> User:
    user = optional_user(request)
    if user is None:
        raise HTTPException(401, "Sign in to do that.")
    return user


class GoogleLogin(BaseModel):
    credential: str  # Google ID token (GIS) or the Google credential from Firebase's Google provider


@router.post("/api/auth/google")
async def login_with_google(request: Request, body: GoogleLogin):
    state = _auth_state(request)
    try:
        claims = await state.verifier.verify(body.credential)
        identity = check_google_claims(claims, client_id=state.client_id, now=state.clock())
    except AuthError as exc:
        raise HTTPException(401, f"Google sign-in was rejected: {exc}")
    user, created = state.users.get_or_create(identity.sub, email=identity.email,
                                              display_name=identity.name)
    token = issue_app_token(secret=state.secret, user_id=user.id, google_sub=user.google_sub,
                            email=user.email, now=state.clock())
    return {"token": token, "user": _public_user(user), "created": created}


@router.post("/api/auth/hive")
async def provision_hive(request: Request):
    """Auto-provision on first login; idempotent. Returns the Hive presence."""
    state = _auth_state(request)
    user = require_user(request)
    presence = hive_presence(user, mode=state.hive_account_mode,
                             shared_account=state.shared_hive_account)
    return {"user_id": user.id, "display_name": user.display_name,
            "hive_display_key": user.hive_display_key,
            "hive": {"mode": presence.mode, "posting_account": presence.posting_account,
                     "display_key": presence.display_key}}
