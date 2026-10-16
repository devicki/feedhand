"""X (Twitter) API v2 channel. OAuth 1.0a user context.

Env: X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET.
Endpoints: POST /2/tweets, DELETE /2/tweets/:id, POST /2/media/upload (simple),
/2/media/upload/initialize|{id}/append|{id}/finalize + GET status (chunked), /2/media/metadata (alt).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import mimetypes
import os
import secrets
import time
import urllib.parse
from pathlib import Path

import requests

API = "https://api.x.com"
ENV = ["X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET"]
CHUNK = 4 * 1024 * 1024
SIMPLE_MAX = 5 * 1024 * 1024


class XError(RuntimeError):
    pass


def missing_env() -> list[str]:
    return [k for k in ENV if not os.environ.get(k)]


def _pe(s) -> str:
    return urllib.parse.quote(str(s), safe="~-._")


def oauth_header(method, url, creds, params=None, nonce=None, ts=None) -> str:
    """RFC 5849 HMAC-SHA1. `params` = query/form params to sign (JSON & multipart bodies are not signed)."""
    o = {
        "oauth_consumer_key": creds["X_API_KEY"],
        "oauth_nonce": nonce or secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(ts or int(time.time())),
        "oauth_token": creds["X_ACCESS_TOKEN"],
        "oauth_version": "1.0",
    }
    allp = {**(params or {}), **o}
    norm = "&".join(f"{k}={v}" for k, v in sorted((_pe(k), _pe(v)) for k, v in allp.items()))
    base = "&".join([method.upper(), _pe(url.split("?")[0]), _pe(norm)])
    key = f"{_pe(creds['X_API_SECRET'])}&{_pe(creds['X_ACCESS_SECRET'])}"
    o["oauth_signature"] = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
    return "OAuth " + ", ".join(f'{_pe(k)}="{_pe(v)}"' for k, v in sorted(o.items()))


class XClient:
    def __init__(self, creds=None, session=None, sleep=time.sleep):
        self.creds = creds or {k: os.environ.get(k, "") for k in ENV}
        miss = [k for k in ENV if not self.creds.get(k)]
        if miss:
            raise XError(f"missing secrets: {', '.join(miss)}")
        self.s = session or requests.Session()
        self.sleep = sleep

    def _req(self, method, path, params=None, json=None, files=None, data=None):
        url = API + path
        h = {"Authorization": oauth_header(method, url, self.creds, params)}
        r = self.s.request(method, url, params=params, json=json, files=files, data=data, headers=h, timeout=120)
        if r.status_code >= 400:
            raise XError(f"X API {method} {path} -> {r.status_code}: {r.text[:500]}")
        return r.json() if r.content else {}

    # --- media ---
    def upload_media(self, path: str, alt: str | None = None) -> str:
        p = Path(path).expanduser()
        mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        size = p.stat().st_size
        is_video = mime.startswith("video/")
        is_gif = mime == "image/gif"
        cat = "tweet_video" if is_video else ("tweet_gif" if is_gif else "tweet_image")
        if not is_video and not is_gif and size <= SIMPLE_MAX:
            with open(p, "rb") as f:
                d = self._req("POST", "/2/media/upload", files={"media": (p.name, f, mime)},
                              data={"media_category": cat})
            mid = d["data"]["id"]
        else:
            mid = self._chunked(p, mime, size, cat)
        if alt:
            self._req("POST", "/2/media/metadata", json={"id": mid, "metadata": {"alt_text": {"text": alt[:1000]}}})
        return mid

    def _chunked(self, p, mime, size, cat) -> str:
        d = self._req("POST", "/2/media/upload/initialize",
                      json={"media_type": mime, "total_bytes": size, "media_category": cat})
        mid = d["data"]["id"]
        with open(p, "rb") as f:
            i = 0
            while chunk := f.read(CHUNK):
                self._req("POST", f"/2/media/upload/{mid}/append",
                          files={"media": ("chunk", chunk, "application/octet-stream")},
                          data={"segment_index": str(i)})
                i += 1
        d = self._req("POST", f"/2/media/upload/{mid}/finalize")
        info = (d.get("data") or {}).get("processing_info")
        for _ in range(60):
            if not info or info.get("state") == "succeeded":
                return mid
            if info.get("state") == "failed":
                raise XError(f"media processing failed: {info}")
            self.sleep(int(info.get("check_after_secs", 2)))
            d = self._req("GET", "/2/media/upload", params={"command": "STATUS", "media_id": mid})
            info = (d.get("data") or {}).get("processing_info")
        raise XError("media processing timed out")

    # --- posts ---
    def create(self, text, media_ids=None, reply_to=None) -> dict:
        body = {"text": text}
        if media_ids:
            body["media"] = {"media_ids": media_ids}
        if reply_to:
            body["reply"] = {"in_reply_to_tweet_id": reply_to}
        return self._req("POST", "/2/tweets", json=body)["data"]

    def delete(self, tweet_id) -> bool:
        return bool(self._req("DELETE", f"/2/tweets/{tweet_id}").get("data", {}).get("deleted"))

    def me(self) -> dict:
        return self._req("GET", "/2/users/me")["data"]

    def publish_thread(self, texts, media=None, alt=None) -> list[dict]:
        alt = alt or {}
        mids = [self.upload_media(m, alt.get(m)) for m in (media or [])]
        out, prev = [], None
        for i, t in enumerate(texts):
            d = self.create(t, mids if i == 0 else None, prev)
            prev = d["id"]
            out.append({"id": d["id"], "url": f"https://x.com/i/web/status/{d['id']}"})
        return out
