"""A user's "Hive presence" — how their identity shows up inside the app's
Hive space.

v1 (advisor-approved 2026-09-16): ONE shared app posting account publishes
every card; a user is identified inside that space by a stable public
`display_key` carried in each post's attribution block. Real per-user Hive
accounts (creation, wallets, resource-credit management) were explicitly
ruled out for v1 — one root post per 5 minutes per account plus RC needs per
account would throttle a 100-user community to uselessness.

`HIVE_ACCOUNT_MODE` is the dormant feature flag; `hive_presence` is the ONE
integration point where per-user accounts would plug in later.
"""
from dataclasses import dataclass

from app.users import User

HIVE_ACCOUNT_MODES = ("shared", "per_user")


@dataclass(frozen=True)
class HivePresence:
    mode: str  # "shared" | "per_user"
    posting_account: str  # the Hive account that signs this user's posts
    display_key: str  # public collector handle in post attribution


def hive_presence(user: User, *, mode: str, shared_account: str) -> HivePresence:
    if mode == "shared":
        return HivePresence(mode="shared", posting_account=shared_account,
                            display_key=user.hive_display_key)
    if mode == "per_user":
        # === DORMANT SHIM — INTEGRATION POINT FOR PER-USER HIVE ACCOUNTS ===
        # A future implementation would look up / create the user's own Hive
        # account here and return posting_account=<their account>. The publish
        # queue would then need a per-account posting key + per-account
        # root-post spacing + RC checks. Not built in v1 by decision.
        raise NotImplementedError("per-user Hive accounts are not implemented (v1 is shared)")
    raise ValueError(f"unknown HIVE_ACCOUNT_MODE {mode!r}; expected one of {HIVE_ACCOUNT_MODES}")
