"""Posting history in SQLite (~/.feedhand/history.db)."""
from __future__ import annotations

import sqlite3
import time

from feedhand import prefs as P

SCHEMA = """CREATE TABLE IF NOT EXISTS posts(
 id INTEGER PRIMARY KEY, ts TEXT, requester TEXT, approver TEXT, channel TEXT,
 mode TEXT, status TEXT, post_id TEXT, url TEXT, est_cost_usd REAL, note TEXT)"""


def db():
    P.home().mkdir(parents=True, exist_ok=True, mode=0o700)
    c = sqlite3.connect(P.home() / "history.db")
    c.execute(SCHEMA)
    return c


def add(**kw):
    kw.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    cols = ["ts", "requester", "approver", "channel", "mode", "status", "post_id", "url", "est_cost_usd", "note"]
    with db() as c:
        c.execute(f"INSERT INTO posts({','.join(cols)}) VALUES({','.join('?'*len(cols))})",
                  [kw.get(k) for k in cols])


def rows(limit=50, channel=None):
    q = "SELECT ts,requester,approver,channel,mode,status,post_id,url,est_cost_usd,note FROM posts"
    args = []
    if channel:
        q += " WHERE channel=?"
        args.append(channel)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    keys = ["ts", "requester", "approver", "channel", "mode", "status", "post_id", "url", "est_cost_usd", "note"]
    with db() as c:
        return [dict(zip(keys, r)) for r in c.execute(q, args)]


def cost(period="all", now=None):
    """Sum est_cost_usd per channel; week = since Monday 00:00 local, month = since the 1st."""
    from datetime import datetime, timedelta
    now = now or datetime.now().astimezone()
    start = None
    if period == "week":
        start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    per, total = {}, 0.0
    with db() as c:
        for ts, ch, status, est, pid in c.execute("SELECT ts,channel,status,est_cost_usd,post_id FROM posts"):
            if start is not None:
                try:
                    t = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S%z")
                except (TypeError, ValueError):
                    continue
                if t < start:
                    continue
            d = per.setdefault(ch, {"est_cost_usd": 0.0, "posts": 0, "deletes": 0, "bundles": 0, "errors": 0})
            d["est_cost_usd"] += est or 0
            total += est or 0
            if status == "published":
                d["posts"] += len((pid or "").split(",")) if pid else 1
            elif status == "deleted":
                d["deletes"] += 1
            elif status == "bundle_ready":
                d["bundles"] += 1
            elif status in ("error", "delete_failed"):
                d["errors"] += 1
    for d in per.values():
        d["est_cost_usd"] = round(d["est_cost_usd"], 4)
    return {"period": period, "since": start.isoformat() if start else None,
            "per_channel": per, "total_est_cost_usd": round(total, 4),
            "posts": sum(d["posts"] for d in per.values()), "deletes": sum(d["deletes"] for d in per.values())}
