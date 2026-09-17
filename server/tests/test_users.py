"""JSON-file-backed user store (same pattern as the publish queue)."""
import json

from app.users import UserStore, hive_display_key, user_id_for

SUB = "10769150350006150715113082367"


def test_hive_display_key_is_deterministic_slug():
    key = hive_display_key(SUB)
    assert key == hive_display_key(SUB)
    assert key.startswith("binder-") and len(key) == len("binder-") + 8
    assert key != hive_display_key("other-sub")
    assert SUB not in key  # never leaks the raw Google sub


def test_user_id_is_stable_and_not_the_google_sub():
    assert user_id_for(SUB) == user_id_for(SUB)
    assert user_id_for(SUB) != SUB and SUB not in user_id_for(SUB)
    assert user_id_for(SUB) != hive_display_key(SUB)


def test_get_or_create_is_idempotent(tmp_path):
    store = UserStore(tmp_path / "users.json")
    user, created = store.get_or_create(SUB, email="calvin@example.com", display_name="Calvin")
    again, created_again = store.get_or_create(SUB, email="calvin@example.com", display_name="Calvin")
    assert created is True and created_again is False
    assert user.id == again.id
    assert store.count() == 1
    assert user.google_sub == SUB and user.email == "calvin@example.com"
    assert user.display_name == "Calvin"
    assert user.hive_display_key == hive_display_key(SUB)
    assert user.created_at.endswith("Z") or "+" in user.created_at


def test_display_name_falls_back_to_display_key(tmp_path):
    store = UserStore(tmp_path / "users.json")
    user, _ = store.get_or_create(SUB, email="calvin@example.com", display_name=None)
    assert user.display_name == user.hive_display_key


def test_store_persists_and_reloads(tmp_path):
    path = tmp_path / "users.json"
    UserStore(path).get_or_create(SUB, email="calvin@example.com", display_name="Calvin")
    assert json.loads(path.read_text())["v"] == 1
    reloaded = UserStore(path)
    assert reloaded.count() == 1
    user = reloaded.get(user_id_for(SUB))
    assert user is not None and user.email == "calvin@example.com"
    assert reloaded.get("nope") is None
    assert not list(tmp_path.glob("*.tmp"))  # atomic write leaves no debris


def test_store_starts_empty_when_file_missing_or_unreadable(tmp_path):
    assert UserStore(tmp_path / "missing" / "users.json").count() == 0
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert UserStore(bad).count() == 0
