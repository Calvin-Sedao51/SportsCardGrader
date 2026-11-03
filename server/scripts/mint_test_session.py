"""Mint a signed-in web session for browser automation, without Google.

Issues an app token with the server's AUTH_JWT_SECRET, lets the running
server provision the user (POST /api/auth/hive), and prints the
`{token, user}` JSON the web app keeps in sessionStorage['cardscanner.auth'].

Local/test use only: anyone holding AUTH_JWT_SECRET can mint any user.

    AUTH_JWT_SECRET=... python scripts/mint_test_session.py [--sub e2e-123] [--api http://localhost:8000]
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

from app.auth import issue_app_token


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sub", default=f"e2e-{int(time.time())}",
                        help="fake Google subject; a new value models a first-time sign-up")
    parser.add_argument("--email", default=None)
    parser.add_argument("--api", default="http://localhost:8000")
    args = parser.parse_args()

    secret = os.environ.get("AUTH_JWT_SECRET", "")
    if not secret:
        print("AUTH_JWT_SECRET is not set (it must match the running server's).", file=sys.stderr)
        return 2
    email = args.email or f"{args.sub}@example.test"
    # uid is a placeholder: the server self-heals unknown users from the
    # token's sub, so the first authenticated call creates the record.
    token = issue_app_token(secret=secret, user_id=f"uid-{args.sub}",
                            google_sub=args.sub, email=email)

    req = urllib.request.Request(f"{args.api}/api/auth/hive", method="POST",
                                 headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            presence = json.load(resp)
    except urllib.error.HTTPError as exc:
        print(f"/api/auth/hive -> {exc.code}: {exc.read().decode()}", file=sys.stderr)
        return 1

    user = {"id": presence["user_id"], "email": email,
            "display_name": presence["display_name"],
            "hive_display_key": presence["hive_display_key"],
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    print(json.dumps({"token": token, "user": user}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
