"""Storage: API cache, listings, evaluations and the shared deal log.

Uses SQLite by default. Set DATABASE_URL (e.g. a free Neon or Supabase Postgres)
so you and your partner share one persistent pipeline.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

from sqlalchemy import (Column, Float, Integer, MetaData, String, Table, Text,
                        create_engine, select)
from sqlalchemy.pool import NullPool

metadata = MetaData()

api_cache = Table(
    "api_cache", metadata,
    Column("key", String(500), primary_key=True),
    Column("fetched_at", Float, nullable=False),
    Column("body", Text, nullable=False),
)

listings = Table(
    "listings", metadata,
    Column("id", String(64), primary_key=True),
    Column("first_seen", String(32)),
    Column("last_seen", String(32)),
    Column("area", String(100)),
    Column("data", Text),          # normalised listing JSON
    Column("evaluation", Text),    # latest evaluation JSON
)

deal_log = Table(
    "deal_log", metadata,
    Column("id", String(64), primary_key=True),
    Column("status", String(32), default="New"),
    Column("reason", String(200), default=""),
    Column("notes", Text, default=""),
    Column("overrides", Text, default="{}"),  # sqft, band, renov_psf, asis_psf
    Column("updated_by", String(100), default=""),
    Column("updated_at", String(32), default=""),
)

settings = Table(
    "settings", metadata,
    Column("key", String(64), primary_key=True),
    Column("value", Text),
    Column("updated_by", String(100)),
    Column("updated_at", String(32)),
)

scan_runs = Table(
    "scan_runs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("at", String(32)),
    Column("mode", String(16)),
    Column("credits_used", Integer),
    Column("listings", Integer),
    Column("notes", Text),
)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


class Store:
    def __init__(self, url: str | None = None):
        url = url or os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or ""
        self.persistent = bool(url)
        if not url:
            # Local default. On Vercel only /tmp is writable and it doesn't last.
            url = "sqlite:////tmp/dealhunter.db" if os.environ.get("VERCEL") else "sqlite:///dealhunter.db"
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                url = "postgresql+psycopg://" + url[len(prefix):]
        if url.startswith("sqlite"):
            self.persistent = self.persistent and True
            self.engine = create_engine(url, future=True, connect_args={"check_same_thread": False})
        else:
            self.engine = create_engine(url, future=True, poolclass=NullPool)  # serverless-friendly
        metadata.create_all(self.engine)

    # ---- shared settings
    def get_setting(self, key: str, default=None):
        with self.engine.connect() as c:
            r = c.execute(select(settings).where(settings.c.key == key)).first()
        return json.loads(r.value) if r else default

    def put_setting(self, key: str, value, who: str = "") -> None:
        with self.engine.begin() as c:
            c.execute(settings.delete().where(settings.c.key == key))
            c.execute(settings.insert().values(key=key, value=json.dumps(value), updated_by=who, updated_at=now_iso()))

    # ---- API cache
    def cache_get(self, key: str, max_age_days: float):
        with self.engine.connect() as c:
            row = c.execute(select(api_cache).where(api_cache.c.key == key)).first()
        if row and time.time() - row.fetched_at < max_age_days * 86400:
            return json.loads(row.body)
        return None

    def cache_put(self, key: str, body) -> None:
        with self.engine.begin() as c:
            c.execute(api_cache.delete().where(api_cache.c.key == key))
            c.execute(api_cache.insert().values(key=key, fetched_at=time.time(), body=json.dumps(body)))

    # ---- listings
    def upsert_listing(self, lid: str, area: str, data: dict, evaluation: dict) -> None:
        ts = now_iso()
        with self.engine.begin() as c:
            row = c.execute(select(listings.c.first_seen).where(listings.c.id == lid)).first()
            if row:
                c.execute(listings.update().where(listings.c.id == lid).values(
                    last_seen=ts, area=area, data=json.dumps(data), evaluation=json.dumps(evaluation)))
            else:
                c.execute(listings.insert().values(
                    id=lid, first_seen=ts, last_seen=ts, area=area,
                    data=json.dumps(data), evaluation=json.dumps(evaluation)))

    def all_listings(self) -> list[dict]:
        with self.engine.connect() as c:
            rows = c.execute(select(listings)).all()
        return [{"id": r.id, "first_seen": r.first_seen, "last_seen": r.last_seen, "area": r.area,
                 "data": json.loads(r.data), "evaluation": json.loads(r.evaluation or "{}")} for r in rows]

    def clear_listings(self) -> None:
        with self.engine.begin() as c:
            c.execute(listings.delete())

    # ---- deal log
    def get_log(self, lid: str) -> dict:
        with self.engine.connect() as c:
            r = c.execute(select(deal_log).where(deal_log.c.id == lid)).first()
        if not r:
            return {"status": "New", "reason": "", "notes": "", "overrides": {}, "updated_by": "", "updated_at": ""}
        return {"status": r.status, "reason": r.reason or "", "notes": r.notes or "",
                "overrides": json.loads(r.overrides or "{}"), "updated_by": r.updated_by or "",
                "updated_at": r.updated_at or ""}

    def all_logs(self) -> dict[str, dict]:
        with self.engine.connect() as c:
            rows = c.execute(select(deal_log)).all()
        return {r.id: {"status": r.status, "reason": r.reason or "", "notes": r.notes or "",
                       "overrides": json.loads(r.overrides or "{}"), "updated_by": r.updated_by or "",
                       "updated_at": r.updated_at or ""} for r in rows}

    def save_log(self, lid: str, status: str, reason: str, notes: str, overrides: dict, who: str) -> None:
        vals = dict(status=status, reason=reason, notes=notes, overrides=json.dumps(overrides),
                    updated_by=who, updated_at=now_iso())
        with self.engine.begin() as c:
            if c.execute(select(deal_log.c.id).where(deal_log.c.id == lid)).first():
                c.execute(deal_log.update().where(deal_log.c.id == lid).values(**vals))
            else:
                c.execute(deal_log.insert().values(id=lid, **vals))

    # ---- scan runs
    def log_scan(self, mode: str, credits: int, n: int, notes: str = "") -> None:
        with self.engine.begin() as c:
            c.execute(scan_runs.insert().values(at=now_iso(), mode=mode, credits_used=credits, listings=n, notes=notes))

    def last_scans(self, n: int = 10) -> list[dict]:
        with self.engine.connect() as c:
            rows = c.execute(select(scan_runs).order_by(scan_runs.c.id.desc()).limit(n)).all()
        return [dict(r._mapping) for r in rows]
