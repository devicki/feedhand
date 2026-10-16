"""Scheduled-post queue in SQLite (~/.feedhand/history.db, table queue). Media copied to ~/.feedhand/queue/<id>/."""
from __future__ import annotations

import json
import shutil
import time
from datetime import datetime
from pathlib import Path

from feedhand import log as L, prefs as P

SCHEMA = """CREATE TABLE IF NOT EXISTS queue(
 id INTEGER PRIMARY KEY, created TEXT, due_at TEXT, due_epoch REAL, requester TEXT, approver TEXT,
 status TEXT, post_json TEXT, result TEXT, updated TEXT)"""


def db():
    c = L.db()
    c.execute(SCHEMA)
    return c


def parse_at(s: str) -> datetime:
    t = datetime.fromisoformat(s)
    if t.tzinfo is None:
        t = t.astimezone()  # interpret as box-local time
    return t


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _copy_media(post: dict, dest: Path) -> dict:
    post = json.loads(json.dumps(post))
    mapping = {}

    def cp(m):
        if not isinstance(m, str) or m.startswith("http"):
            return m
        src = Path(m).expanduser()
        if m not in mapping:
            dest.mkdir(parents=True, exist_ok=True, mode=0o700)
            tgt = dest / f"{len(mapping)}_{src.name}"
            shutil.copy2(src, tgt)
            mapping[m] = str(tgt)
        return mapping[m]

    post["media"] = [cp(m) for m in post.get("media") or []]
    for pc in (post.get("per_channel") or {}).values():
        if "media" in pc:
            pc["media"] = [cp(m) for m in pc["media"] or []]
        if "alt" in pc:
            pc["alt"] = {mapping.get(k, k): v for k, v in pc["alt"].items()}
    return post


def add(post: dict, at: datetime, approver: str) -> dict:
    with db() as c:
        cur = c.execute("INSERT INTO queue(created,due_at,due_epoch,requester,approver,status,post_json,updated)"
                        " VALUES(?,?,?,?,?,'pending','{}',?)",
                        (_now(), at.isoformat(), at.timestamp(), post.get("requester"), approver, _now()))
        qid = cur.lastrowid
    try:
        stored = _copy_media(post, P.home() / "queue" / str(qid))
    except Exception:
        with db() as c:
            c.execute("DELETE FROM queue WHERE id=?", (qid,))
        raise
    with db() as c:
        c.execute("UPDATE queue SET post_json=? WHERE id=?", (json.dumps(stored, ensure_ascii=False), qid))
    return get(qid)


def _row(r):
    keys = ["id", "created", "due_at", "requester", "approver", "status", "post_json", "result", "updated"]
    d = dict(zip(keys, r))
    d["post"] = json.loads(d.pop("post_json") or "{}")
    d["result"] = json.loads(d["result"]) if d["result"] else None
    return d


SEL = "SELECT id,created,due_at,requester,approver,status,post_json,result,updated FROM queue"


def get(qid):
    with db() as c:
        r = c.execute(SEL + " WHERE id=?", (qid,)).fetchone()
    return _row(r) if r else None


def list_(status=None):
    q, args = SEL, []
    if status:
        q += " WHERE status=?"
        args.append(status)
    with db() as c:
        return [_row(r) for r in c.execute(q + " ORDER BY due_epoch", args)]


def cancel(qid) -> bool:
    with db() as c:
        n = c.execute("UPDATE queue SET status='cancelled',updated=? WHERE id=? AND status='pending'",
                      (_now(), qid)).rowcount
    if n:
        shutil.rmtree(P.home() / "queue" / str(qid), ignore_errors=True)
    return bool(n)


def claim_due(now_epoch: float) -> list[dict]:
    """Atomically move due pending items to 'publishing' so a second runner never double-posts."""
    with db() as c:
        ids = [r[0] for r in c.execute("SELECT id FROM queue WHERE status='pending' AND due_epoch<=? "
                                       "ORDER BY due_epoch", (now_epoch,))]
        got = [i for i in ids if c.execute("UPDATE queue SET status='publishing',updated=? WHERE id=? "
                                           "AND status='pending'", (_now(), i)).rowcount]
    return [get(i) for i in got]


def finish(qid, ok: bool, result):
    with db() as c:
        c.execute("UPDATE queue SET status=?,result=?,updated=? WHERE id=?",
                  ("done" if ok else "failed", json.dumps(result, ensure_ascii=False), _now(), qid))


def pending_count() -> int:
    with db() as c:
        return c.execute("SELECT COUNT(*) FROM queue WHERE status='pending'").fetchone()[0]
