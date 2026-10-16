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
