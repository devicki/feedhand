"""Fetch server media over a saved SSH alias (scp) into ~/.feedhand/media/<request>/.

Only aliases in prefs `media_hosts` are allowed. Remote paths must be absolute, without `..`.
The remote size is read first (`stat -c %s`, run as one plain command so any login shell works)
and checked against the current channel limits before copying. Key material is never read or printed.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path, PurePosixPath

from feedhand import media as M, prefs as P

SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]
_SAFE = re.compile(r"^[A-Za-z0-9._/+@=,:-][A-Za-z0-9 ._/+@=,:~()\[\]-]*$")
_ALIAS = re.compile(r"^[A-Za-z0-9._-]+$")


def media_dir() -> Path:
    return P.home() / "media"


def parse(spec) -> dict:
    """'alias:/abs/path' string or {'host','path','note'} -> {'host','path','note'}."""
    if isinstance(spec, dict):
        host, path, note = spec.get("host"), spec.get("path"), spec.get("note")
    else:
        if ":" not in spec:
            raise ValueError(f"expected HOST:/abs/path, got {spec!r}")
        host, path = spec.split(":", 1)
        note = None
    if not host or not _ALIAS.match(str(host)):
        raise ValueError(f"bad host alias: {host!r}")
    if not path or not str(path).startswith("/"):
        raise ValueError(f"remote path must be absolute: {path!r}")
    if ".." in PurePosixPath(path).parts:
        raise ValueError(f"'..' not allowed in remote path: {path!r}")
    if not _SAFE.match(path) or "'" in path:
        raise ValueError(f"unsupported characters in remote path: {path!r}")
    return {"host": host, "path": path, "note": note}


def limit_for(path: str, channel: str, prefs: dict) -> int | None:
    lim = M.limits(channel, prefs)
    ext = PurePosixPath(path).suffix.lower()
    if ext in M.VIDEO_EXT:
        return lim["video_max_bytes"]
    if ext == ".gif":
        return lim.get("gif_max_bytes") or lim["video_max_bytes"]
    return lim["image_max_bytes"]


def _q(path: str) -> str:
    return "'" + path + "'"


def remote_size(host: str, path: str) -> int:
    r = subprocess.run(["ssh", *SSH_OPTS, host, "stat -c %s " + _q(path)],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"{host}: cannot stat {path}: {(r.stderr or '').strip()[-300:]}")
    try:
        return int(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise RuntimeError(f"{host}: unexpected stat output for {path}")


def fetch(specs, channel="x", request_id=None, prefs=None) -> list[dict]:
    prefs = P.load() if prefs is None else prefs
    allowed = prefs.get("media_hosts") or []
    items = [parse(s) for s in specs]
    for it in items:
        if it["host"] not in allowed:
            raise PermissionError(f"host {it['host']!r} not in prefs media_hosts {allowed}")
    rid = request_id or time.strftime("%Y%m%d-%H%M%S")
    if not _ALIAS.match(rid):
        raise ValueError(f"bad request id: {rid!r}")
    dest = media_dir() / rid
    dest.mkdir(parents=True, exist_ok=True, mode=0o700)
    out = []
    for it in items:
        size = remote_size(it["host"], it["path"])
        cap = limit_for(it["path"], channel, prefs)
        if cap and size > cap:
            raise ValueError(f"{it['path']}: {size} bytes > {channel} limit {cap} bytes; not copied")
        local = dest / PurePosixPath(it["path"]).name
        r = subprocess.run(["scp", "-q", "-p", *SSH_OPTS, f"{it['host']}:{it['path']}", str(local)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"scp failed for {it['host']}:{it['path']}: {(r.stderr or '').strip()[-300:]}")
        got = local.stat().st_size if local.exists() else -1
        if got != size:
            local.unlink(missing_ok=True)
            raise RuntimeError(f"size mismatch for {it['path']}: remote {size}, local {got}")
        res = {"host": it["host"], "remote_path": it["path"], "local_path": str(local), "bytes": got,
               "note": it["note"], "duration": None}
        if shutil.which("ffprobe") and PurePosixPath(it["path"]).suffix.lower() in M.VIDEO_EXT | {".gif"}:
            try:
                res["duration"] = M.probe(local)["duration"]
            except Exception:  # noqa: BLE001
                pass
        out.append(res)
    return out


def is_server(m) -> bool:
    return isinstance(m, dict)


def localize(post: dict, channel="x", request_id=None, prefs=None) -> tuple[dict, list[dict]]:
    """Fetch every {host,path} media entry in a request and return (post with local paths, results)."""
    post = json.loads(json.dumps(post))
    blocks = [post] + list((post.get("per_channel") or {}).values())
    specs = [m for b in blocks for m in (b.get("media") or []) if is_server(m)]
    uniq = {(s["host"], s["path"]): s for s in specs}
    res = fetch(list(uniq.values()), channel, request_id, prefs) if uniq else []
    by = {(r["host"], r["remote_path"]): r["local_path"] for r in res}
    for b in blocks:
        if b.get("media"):
            b["media"] = [by[(m["host"], m["path"])] if is_server(m) else m for m in b["media"]]
    return post, res
