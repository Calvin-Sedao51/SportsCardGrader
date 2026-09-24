"""App JWT primitives and Google claim checks — pure functions, no network."""
import time

import pytest

from app.auth import (
    APP_TOKEN_TTL_SECONDS,
    AuthError,
    Hs256Verifier,
    check_google_claims,
    decode_hs256,
    encode_hs256,
    issue_app_token,
    read_app_token,
)

SECRET = "test-secret"
CLIENT_ID = "123-abc.apps.googleusercontent.com"
NOW = 1_800_000_000.0


def google_claims(**overrides) -> dict:
    claims = {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": "10769150350006150715113082367",
              "email": "calvin@example.com", "name": "Calvin", "exp": int(NOW) + 3600,
              "iat": int(NOW)}
    claims.update(overrides)
    return {k: v for k, v in claims.items() if v is not None}


def test_hs256_round_trip():
    token = encode_hs256({"sub": "x", "exp": int(NOW) + 60}, SECRET)
    assert token.count(".") == 2
    assert decode_hs256(token, SECRET, now=NOW) == {"sub": "x", "exp": int(NOW) + 60}


def test_hs256_rejects_bad_signature():
    token = encode_hs256({"sub": "x", "exp": int(NOW) + 60}, SECRET)
    with pytest.raises(AuthError):
        decode_hs256(token, "other-secret", now=NOW)


def test_hs256_rejects_tampered_payload():
    header, payload, sig = encode_hs256({"sub": "x", "exp": int(NOW) + 60}, SECRET).split(".")
    forged = encode_hs256({"sub": "y", "exp": int(NOW) + 60}, "whatever").split(".")[1]
    with pytest.raises(AuthError):
        decode_hs256(f"{header}.{forged}.{sig}", SECRET, now=NOW)


def test_hs256_rejects_expired_and_garbage():
    token = encode_hs256({"sub": "x", "exp": int(NOW) - 1}, SECRET)
    with pytest.raises(AuthError):
        decode_hs256(token, SECRET, now=NOW)
    for garbage in ("", "abc", "a.b", "a.b.c", "not a token at all"):
        with pytest.raises(AuthError):
            decode_hs256(garbage, SECRET, now=NOW)


def test_hs256_rejects_non_hs256_header():
    # alg confusion: an RS256/none header must never be accepted by the HS path.
    token = encode_hs256({"sub": "x", "exp": int(NOW) + 60}, SECRET, header={"alg": "none", "typ": "JWT"})
    with pytest.raises(AuthError):
        decode_hs256(token, SECRET, now=NOW)


def test_google_claims_happy_path():
    identity = check_google_claims(google_claims(), client_id=CLIENT_ID, now=NOW)
    assert identity.sub == "10769150350006150715113082367"
    assert identity.email == "calvin@example.com"
    assert identity.name == "Calvin"


def test_google_claims_accepts_both_issuer_spellings():
    for iss in ("accounts.google.com", "https://accounts.google.com"):
        check_google_claims(google_claims(iss=iss), client_id=CLIENT_ID, now=NOW)


def test_google_claims_rejects_bad_aud():
    with pytest.raises(AuthError):
        check_google_claims(google_claims(aud="someone-else"), client_id=CLIENT_ID, now=NOW)


def test_google_claims_rejects_missing_sub():
    with pytest.raises(AuthError):
        check_google_claims(google_claims(sub=None), client_id=CLIENT_ID, now=NOW)


def test_google_claims_rejects_bad_issuer_and_expiry():
    with pytest.raises(AuthError):
        check_google_claims(google_claims(iss="https://evil.example"), client_id=CLIENT_ID, now=NOW)
    with pytest.raises(AuthError):
        check_google_claims(google_claims(exp=int(NOW) - 10), client_id=CLIENT_ID, now=NOW)


def test_google_claims_tolerates_string_exp():
    # Google's tokeninfo endpoint returns every claim as a string.
    check_google_claims(google_claims(exp=str(int(NOW) + 60)), client_id=CLIENT_ID, now=NOW)


async def test_hs256_verifier_is_the_offline_google_double():
    token = encode_hs256(google_claims(), "google-fake-secret")
    claims = await Hs256Verifier("google-fake-secret").verify(token)
    assert claims["sub"] == google_claims()["sub"]
    with pytest.raises(AuthError):
        await Hs256Verifier("wrong").verify(token)


def test_app_token_round_trip_and_30_day_expiry():
    token = issue_app_token(secret=SECRET, user_id="u1", google_sub="g1",
                            email="calvin@example.com", now=NOW)
    claims = read_app_token(token, secret=SECRET, now=NOW + 60)
    assert claims.user_id == "u1" and claims.sub == "g1" and claims.email == "calvin@example.com"
    assert APP_TOKEN_TTL_SECONDS == 30 * 24 * 3600
    with pytest.raises(AuthError):
        read_app_token(token, secret=SECRET, now=NOW + APP_TOKEN_TTL_SECONDS + 1)


def test_app_token_default_clock_is_wall_time():
    token = issue_app_token(secret=SECRET, user_id="u1", google_sub="g1", email="e")
    assert read_app_token(token, secret=SECRET).user_id == "u1"
    assert abs(decode_hs256(token, SECRET)["iat"] - time.time()) < 5


def tokeninfo_client(handler):
    import httpx
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_tokeninfo_verifier_returns_google_claims_on_200():
    import httpx
    from app.auth import TokeninfoVerifier

    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"iss": "accounts.google.com", "sub": "1", "aud": CLIENT_ID,
                                         "exp": str(int(NOW) + 60)})

    claims = await TokeninfoVerifier(tokeninfo_client(handler)).verify("id.token.here")
    assert claims["sub"] == "1"
    assert seen["url"].startswith("https://oauth2.googleapis.com/tokeninfo?id_token=id.token.here")


async def test_tokeninfo_verifier_rejects_non_200_and_transport_errors():
    import httpx
    from app.auth import TokeninfoVerifier

    with pytest.raises(AuthError):
        await TokeninfoVerifier(tokeninfo_client(
            lambda r: httpx.Response(400, json={"error": "invalid_token"}))).verify("x")

    def boom(request):
        raise httpx.ConnectError("offline")

    with pytest.raises(AuthError):
        await TokeninfoVerifier(tokeninfo_client(boom)).verify("x")
