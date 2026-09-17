"""Google sign-in -> app JWT.

Identity model (advisor-approved, 2026-09-16): Google ID is the user's
identity; the app issues its own HS256 JWT (30 days) that the web client sends
as a Bearer token. The AI key flow stays bring-your-own and is untouched.

No JWT/crypto library is available in this environment, so HS256 is done with
the stdlib (hmac + sha256). Google ID tokens are RS256-signed; their
signature is checked by a pluggable GoogleVerifier:

  * TokeninfoVerifier — production. Google's tokeninfo endpoint validates the
    signature server-side and echoes the claims. We still enforce iss/aud/exp
    ourselves (never trust a verifier to do policy).
  * Hs256Verifier — offline double for tests/dev: a Google-shaped JWT signed
    with a local secret. Never configure this in production.
"""
import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Optional, Protocol

import httpx

GOOGLE_ISSUERS = frozenset({"accounts.google.com", "https://accounts.google.com"})
GOOGLE_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"
APP_TOKEN_TTL_SECONDS = 30 * 24 * 3600
APP_TOKEN_ISSUER = "cardscanner"


class AuthError(Exception):
    """Any reason a credential is not acceptable. Routes map it to 401."""


# -- HS256 JWT (stdlib) -------------------------------------------------------

def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    padded = text + "=" * (-len(text) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise AuthError("malformed token") from exc


def _sign(signing_input: bytes, secret: str) -> bytes:
    return hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()


def encode_hs256(claims: dict, secret: str, *, header: Optional[dict] = None) -> str:
    header = header or {"alg": "HS256", "typ": "JWT"}
    segments = [_b64url_encode(json.dumps(header, separators=(",", ":")).encode()),
                _b64url_encode(json.dumps(claims, separators=(",", ":")).encode())]
    signing_input = ".".join(segments).encode("ascii")
    segments.append(_b64url_encode(_sign(signing_input, secret)))
    return ".".join(segments)


def decode_hs256(token: str, secret: str, *, now: Optional[float] = None) -> dict:
    """Verify signature + expiry and return the claims, else raise AuthError."""
    parts = token.split(".") if token else []
    if len(parts) != 3:
        raise AuthError("malformed token")
    header_b64, payload_b64, sig_b64 = parts
    try:
        header = json.loads(_b64url_decode(header_b64))
        claims = json.loads(_b64url_decode(payload_b64))
    except (ValueError, UnicodeDecodeError) as exc:
        raise AuthError("malformed token") from exc
    if not isinstance(header, dict) or header.get("alg") != "HS256" or not isinstance(claims, dict):
        raise AuthError("unsupported token")
    expected = _sign(f"{header_b64}.{payload_b64}".encode("ascii"), secret)
    if not hmac.compare_digest(expected, _b64url_decode(sig_b64)):
        raise AuthError("bad signature")
    _check_exp(claims.get("exp"), now)
    return claims


def _check_exp(exp, now: Optional[float]) -> None:
    if exp is None:
        raise AuthError("token has no expiry")
    try:
        exp_value = float(exp)
    except (TypeError, ValueError) as exc:
        raise AuthError("bad expiry") from exc
    if exp_value <= (time.time() if now is None else now):
        raise AuthError("token expired")


# -- Google ID token ----------------------------------------------------------

@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: Optional[str]
    name: Optional[str]


class GoogleVerifier(Protocol):
    async def verify(self, credential: str) -> dict:
        """Return the token's claims iff its signature is genuine; else AuthError."""


class TokeninfoVerifier:
    """Production verifier: Google checks the RS256 signature for us."""

    def __init__(self, http: httpx.AsyncClient, url: str = GOOGLE_TOKENINFO_URL):
        self.http = http
        self.url = url

    async def verify(self, credential: str) -> dict:
        try:
            resp = await self.http.get(self.url, params={"id_token": credential})
        except httpx.HTTPError as exc:
            raise AuthError("could not reach Google to verify sign-in") from exc
        if resp.status_code != 200:
            raise AuthError("Google rejected the sign-in credential")
        claims = resp.json()
        if not isinstance(claims, dict):
            raise AuthError("unexpected tokeninfo response")
        return claims


class Hs256Verifier:
    """Offline double: a Google-shaped JWT signed with a shared local secret."""

    def __init__(self, secret: str):
        self.secret = secret

    async def verify(self, credential: str) -> dict:
        # Expiry is re-checked by check_google_claims; here only the signature matters.
        return decode_hs256(credential, self.secret, now=0.0)


def check_google_claims(claims: dict, *, client_id: str,
                        now: Optional[float] = None) -> GoogleIdentity:
    """Policy on an already signature-verified Google ID token."""
    if claims.get("iss") not in GOOGLE_ISSUERS:
        raise AuthError("not a Google-issued token")
    if not client_id or claims.get("aud") != client_id:
        raise AuthError("token was issued for a different app")
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise AuthError("token has no subject")
    _check_exp(claims.get("exp"), now)
    email = claims.get("email")
    name = claims.get("name")
    return GoogleIdentity(sub=sub, email=email if isinstance(email, str) else None,
                          name=name if isinstance(name, str) and name.strip() else None)


# -- App token ----------------------------------------------------------------

@dataclass(frozen=True)
class AppClaims:
    sub: str  # Google sub
    user_id: str
    email: Optional[str]


def issue_app_token(*, secret: str, user_id: str, google_sub: str, email: Optional[str],
                    now: Optional[float] = None) -> str:
    issued = int(time.time() if now is None else now)
    return encode_hs256({"iss": APP_TOKEN_ISSUER, "sub": google_sub, "uid": user_id,
                         "email": email, "iat": issued,
                         "exp": issued + APP_TOKEN_TTL_SECONDS}, secret)


def read_app_token(token: str, *, secret: str, now: Optional[float] = None) -> AppClaims:
    claims = decode_hs256(token, secret, now=now)
    if claims.get("iss") != APP_TOKEN_ISSUER or not claims.get("uid") or not claims.get("sub"):
        raise AuthError("not an app token")
    email = claims.get("email")
    return AppClaims(sub=claims["sub"], user_id=claims["uid"],
                     email=email if isinstance(email, str) else None)
