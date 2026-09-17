"""JSON-file-backed user store (server/data/users.json).

Same file-backed, single-process pattern as the publish queue: one JSON
document, atomic tmp+rename writes. Advisor guidance: fine at this scale,
revisit at 100+ users.

Identifiers are derived from the Google `sub` so a user can never be
double-provisioned by a racing first login, and so neither the raw sub nor
the email ever appears on chain: `hive_display_key` is the only identifier
that leaves this server (it rides in every published post's attribution).
"""
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)

STORE_VERSION = 1


def hive_display_key(google_sub: str) -> str:
    """Public, stable collector handle: 'binder-' + 8 hex chars of the sub."""
    return "binder-" + hashlib.sha256(f"binder-display:{google_sub}".encode()).hexdigest()[:8]


def user_id_for(google_sub: str) -> str:
    return hashlib.sha256(f"binder-user:{google_sub}".encode()).hexdigest()[:16]


class User(BaseModel):
    id: str
    google_sub: str
    email: Optional[str] = None
    display_name: str
    hive_display_key: str
    created_at: str


class UserStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._users: dict[str, User] = {}
        self._load()

    # -- persistence ----------------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            doc = json.loads(self.path.read_text())
            for raw in doc.get("users", {}).values():
                user = User.model_validate(raw)
                self._users[user.id] = user
        except (ValueError, AttributeError):
            logger.warning("user store %s unreadable; starting empty", self.path)
            self._users = {}

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        doc = {"v": STORE_VERSION,
               "users": {uid: u.model_dump() for uid, u in self._users.items()}}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, indent=1))
        os.rename(tmp, self.path)

    # -- public API -----------------------------------------------------------

    def get_or_create(self, google_sub: str, *, email: Optional[str],
                      display_name: Optional[str]) -> tuple[User, bool]:
        """Idempotent: the same Google sub always maps to the same user."""
        uid = user_id_for(google_sub)
        existing = self._users.get(uid)
        if existing is not None:
            return existing, False
        key = hive_display_key(google_sub)
        user = User(id=uid, google_sub=google_sub, email=email,
                    display_name=(display_name or "").strip() or key,
                    hive_display_key=key,
                    created_at=datetime.now(timezone.utc).isoformat(timespec="seconds")
                    .replace("+00:00", "Z"))
        self._users[uid] = user
        self._persist()
        return user, True

    def get(self, user_id: str) -> Optional[User]:
        return self._users.get(user_id)

    def count(self) -> int:
        return len(self._users)
