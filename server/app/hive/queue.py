"""File-backed publish queue: one JSON file per job, single async worker.

Why a file queue and not a DB: restarts must not lose accepted payloads, and
the chain itself serializes throughput (one root post per ~5 min per
account), so a single-process worker IS the ceiling — running multiple
server replicas is unsupported (documented in DEPLOY.md). The deterministic
permlink + on-chain existence check is the backstop that turns any race or
crash-retry into an edit instead of a duplicate post.

The iron broadcast rule lives here: broadcast_ops is called AT MOST ONCE per
attempt. If it throws, we wait ~3 blocks and check the chain — a dropped
socket does not mean the transaction didn't land.
"""
import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Callable, Literal, Optional

from pydantic import BaseModel

from app.hive.post_builder import build_post
from app.hive.record import CardRecord

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
BACKOFF_SECONDS = [60.0, 300.0, 1800.0]
IDLE_DELAY_SECONDS = 30.0
RC_PARK_SECONDS = 600.0
BLOCK_SECONDS = 3.0


class PublishJob(BaseModel):
    job_id: str  # == record.record_id
    seq: int  # FIFO tie-breaker (enqueue order)
    kind: Literal["create", "update"] = "create"
    record: CardRecord
    permlink: str
    user_id: Optional[str] = None  # signed-in publisher; None = anonymous
    status: Literal["queued", "publishing", "confirmed", "failed"] = "queued"
    attempts: int = 0
    last_error: Optional[str] = None
    enqueued_at: float
    confirmed_at: Optional[float] = None
    hive_url: Optional[str] = None


class PublishQueue:
    def __init__(self, queue_dir: Path | str, client, community: str, *,
                 root_interval: float = 305.0, update_interval: float = 15.0,
                 min_rc_percent: float = 5.0,
                 clock: Callable[[], float] = time.time,
                 sleeper: Callable = asyncio.sleep,
                 on_confirmed: Optional[Callable[[PublishJob], None]] = None):
        self.dir = Path(queue_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.client = client
        self.community = community
        self.root_interval = root_interval
        self.update_interval = update_interval
        self.min_rc_percent = min_rc_percent
        self.clock = clock
        self.sleeper = sleeper
        self.on_confirmed = on_confirmed
        self._jobs: dict[str, PublishJob] = {}
        self._last_update_at: Optional[float] = None
        self._load()

    # -- persistence ----------------------------------------------------------

    def _load(self) -> None:
        for path in self.dir.glob("*.json"):
            try:
                job = PublishJob.model_validate_json(path.read_text())
            except ValueError:
                logger.warning("skipping unreadable queue file %s", path)
                continue
            if job.status == "publishing":
                # Crashed mid-publish: the pre-broadcast chain check decides
                # whether it actually landed. Never assume it didn't.
                job.status = "queued"
            self._jobs[job.job_id] = job

    def _persist(self, job: PublishJob) -> None:
        path = self.dir / f"{job.job_id}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(job.model_dump_json())
        os.rename(tmp, path)

    # -- public API -----------------------------------------------------------

    def enqueue(self, record: CardRecord,
                kind: Literal["create", "update"] = "create", *,
                user_id: Optional[str] = None) -> PublishJob:
        from app.hive.record import card_permlink
        existing = self._jobs.get(record.record_id)
        if existing is not None and not (kind == "update" and existing.status == "confirmed"):
            return existing  # idempotent: re-submits return the live job
        seq = max((j.seq for j in self._jobs.values()), default=0) + 1
        job = PublishJob(job_id=record.record_id, seq=seq, kind=kind, record=record,
                         permlink=card_permlink(record), enqueued_at=self.clock(),
                         user_id=user_id)
        if existing is not None:  # re-publishing a confirmed record = edit
            job.kind = "update"
        self._jobs[job.job_id] = job
        self._persist(job)
        return job

    def get_job(self, job_id: str) -> Optional[PublishJob]:
        return self._jobs.get(job_id)

    def _queued(self) -> list[PublishJob]:
        return sorted((j for j in self._jobs.values() if j.status in ("queued", "publishing")),
                      key=lambda j: j.seq)

    def position(self, job_id: str) -> int:
        for i, job in enumerate(self._queued(), start=1):
            if job.job_id == job_id:
                return i
        return 0

    def eta_for(self, job_id: str) -> float:
        pos = self.position(job_id)
        if pos == 0:
            return 0.0
        return self._cooldown("create") + (pos - 1) * self.root_interval

    def status(self) -> dict:
        depth = len(self._queued())
        return {
            "depth": depth,
            "eta_seconds": self._cooldown("create") + max(0, depth - 1) * self.root_interval
            if depth else 0.0,
            "last_published_at": self._last_create_at(),
        }

    # -- spacing --------------------------------------------------------------

    def _last_create_at(self) -> Optional[float]:
        stamps = [j.confirmed_at for j in self._jobs.values()
                  if j.kind == "create" and j.confirmed_at is not None]
        return max(stamps) if stamps else None

    def _cooldown(self, kind: str) -> float:
        if kind == "create":
            last = self._last_create_at()
            interval = self.root_interval
        else:
            last = self._last_update_at
            interval = self.update_interval
        if last is None:
            return 0.0
        return max(0.0, interval - (self.clock() - last))

    # -- worker ---------------------------------------------------------------

    def _matches(self, post: Optional[dict], record_id: str) -> bool:
        if not post:
            return False
        meta = post.get("json_metadata") or {}
        return isinstance(meta, dict) and meta.get("card", {}).get("record_id") == record_id

    def _confirm(self, job: PublishJob) -> None:
        job.status = "confirmed"
        job.confirmed_at = self.clock()
        job.last_error = None
        job.hive_url = f"https://peakd.com/@{self.client.account}/{job.permlink}"
        if job.kind == "update":
            self._last_update_at = job.confirmed_at
        self._persist(job)
        if self.on_confirmed:
            self.on_confirmed(job)

    async def tick(self) -> float:
        """Process at most one job; return seconds until the next tick."""
        queued = self._queued()
        if not queued:
            return IDLE_DELAY_SECONDS
        job = queued[0]
        wait = self._cooldown(job.kind)
        if wait > 0:
            return wait

        job.status = "publishing"
        self._persist(job)

        # Idempotency check: a crash after broadcast, or a duplicate submit,
        # means the post may already be on chain — then this attempt is a no-op.
        existing = await self.client.get_post(self.client.account, job.permlink)
        if self._matches(existing, job.job_id):
            self._confirm(job)
            return 1.0

        rc = await self.client.get_rc_percent()
        if rc < self.min_rc_percent:
            job.status = "queued"
            job.last_error = (f"Low Resource Credits ({rc:.1f}%) — the app account "
                              "needs more Hive Power.")
            self._persist(job)
            return RC_PARK_SECONDS

        ops = build_post(job.record, community=self.community,
                         account=self.client.account, permlink=job.permlink)
        try:
            await self.client.broadcast_ops(ops)
        except Exception as exc:  # noqa: BLE001 — any failure gets the verify path
            await self.sleeper(BLOCK_SECONDS * 3)
            landed = await self.client.get_post(self.client.account, job.permlink)
            if self._matches(landed, job.job_id):
                self._confirm(job)  # it DID land; re-broadcasting would duplicate
                return 1.0
            job.attempts += 1
            job.last_error = str(exc)
            job.status = "failed" if job.attempts >= MAX_ATTEMPTS else "queued"
            self._persist(job)
            logger.warning("publish attempt %d for %s failed: %s",
                           job.attempts, job.permlink, exc)
            return BACKOFF_SECONDS[min(job.attempts - 1, len(BACKOFF_SECONDS) - 1)]

        # Broadcast accepted: wait for Hivemind to index before confirming.
        for _ in range(4):
            await self.sleeper(BLOCK_SECONDS)
            if await self.client.get_post(self.client.account, job.permlink):
                break
        self._confirm(job)
        return 1.0

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                delay = await self.tick()
            except Exception:  # noqa: BLE001 — worker must survive anything
                logger.exception("publish queue tick crashed")
                delay = IDLE_DELAY_SECONDS
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
