from functools import lru_cache

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    ebay_client_id: str = ""
    ebay_client_secret: str = ""
    ebay_env: str = "production"  # or "sandbox"
    pricing_source: str = "ebay_active"  # registry key in app/pricing.py
    anthropic_default_model: str = "claude-sonnet-5"
    openai_default_model: str = "gpt-5.1"

    # Hive publishing ("The Binder" community). Posting key only — never the
    # active/owner key; a leak means spam risk, not fund risk. Never logged.
    hive_account: str = ""
    hive_posting_key: str = ""
    hive_community: str = "hive-192941"
    hive_nodes: str = ""  # comma-separated override; empty = DEFAULT_NODES
    hive_dry_run: bool = False  # log ops instead of broadcasting
    hive_queue_dir: str = "data/publish_queue"
    hive_root_post_interval_seconds: int = 305  # chain allows 1 root post / 5 min
    hive_min_rc_percent: float = 5.0
    images_3speak_token: str = ""  # optional images.hive.blog fallback
    # Feature flag for the dormant per-user Hive account shim (app/hive/identity.py).
    # Only "shared" is implemented; anything else refuses to start.
    hive_account_mode: str = "shared"

    # Google sign-in -> app JWT (app/auth.py). Both unset = anonymous-only app.
    auth_google_client_id: str = ""  # OAuth client ID the web app signs in with
    auth_jwt_secret: str = ""  # HS256 secret for app tokens; never logged
    auth_users_file: str = "data/users.json"

    # Per-user publish budget (app/publish_routes.py): caps how many jobs one
    # signed-in account can enqueue per rolling hour, so a compromised/bot
    # account can't burn the whole shared Hive posting chain.
    publish_rate_budget: int = 50

    model_config = {"env_file": ".env"}

    @property
    def ebay_configured(self) -> bool:
        return bool(self.ebay_client_id and self.ebay_client_secret)

    @property
    def auth_configured(self) -> bool:
        return bool(self.auth_google_client_id and self.auth_jwt_secret)

    @property
    def hive_configured(self) -> bool:
        return bool(self.hive_account and self.hive_posting_key)

    @property
    def hive_node_list(self) -> list[str]:
        from app.hive.client import DEFAULT_NODES
        if not self.hive_nodes.strip():
            return list(DEFAULT_NODES)
        return [n.strip() for n in self.hive_nodes.split(",") if n.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
