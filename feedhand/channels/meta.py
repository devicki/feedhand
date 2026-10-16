"""Threads + Instagram via Meta Graph APIs. UNTESTED against live accounts.

Flow (both): create media container(s) -> poll container status until FINISHED -> publish.
Media must be public HTTPS URLs (Meta fetches them). Long-lived tokens last ~60 days; refresh() extends them.

Threads env: THREADS_ACCESS_TOKEN, THREADS_USER_ID   (graph.threads.net/v1.0)
Instagram env: IG_ACCESS_TOKEN, IG_USER_ID           (graph.facebook.com/v21.0, professional account)
"""
from __future__ import annotations

import os
import time

import requests

from feedhand.post import kind


class MetaError(RuntimeError):
    pass


class _Base:
    BASE = ""
    ENV = ("", "")
    TEXT_KEY = "text"
    FINISHED = "FINISHED"

    def __init__(self, token=None, user_id=None, session=None, sleep=time.sleep):
        self.token = token or os.environ.get(self.ENV[0], "")
        self.uid = user_id or os.environ.get(self.ENV[1], "")
        if not self.token or not self.uid:
            raise MetaError(f"missing secrets: {', '.join(k for k in self.ENV if not os.environ.get(k))}")
        self.s = session or requests.Session()
        self.sleep = sleep

    @classmethod
    def missing_env(cls):
        return [k for k in cls.ENV if not os.environ.get(k)]

    def _req(self, method, path, **params):
        params["access_token"] = self.token
        r = self.s.request(method, f"{self.BASE}/{path}", params=params, timeout=60)
        if r.status_code >= 400:
            raise MetaError(f"{method} {path} -> {r.status_code}: {r.text[:500]}")
        return r.json()

    def wait(self, cid, tries=30):
        for _ in range(tries):
            d = self._req("GET", cid, fields="status_code,status")
            st = d.get("status_code")
            if st in (self.FINISHED, "PUBLISHED"):
                return
            if st in ("ERROR", "EXPIRED"):
                raise MetaError(f"container {cid} {st}: {d.get('status')}")
            self.sleep(5)
        raise MetaError(f"container {cid} not ready")

    def _item(self, url, carousel=False, text=None):
        p = {"is_carousel_item": "true"} if carousel else {}
        if text is not None:
            p[self.TEXT_KEY] = text
        p.update(self._media_params(url))
        return self._req("POST", f"{self.uid}/{self.MEDIA_EDGE}", **p)["id"]

    def publish(self, text, media=None) -> dict:
        media = media or []
        for m in media:
            if not m.startswith("https://"):
                raise MetaError(f"media must be a public HTTPS URL: {m}")
        if len(media) > 1:
            kids = [self._item(m, carousel=True) for m in media]
            for k in kids:
                self.wait(k)
            cid = self._req("POST", f"{self.uid}/{self.MEDIA_EDGE}", media_type="CAROUSEL",
                            children=",".join(kids), **{self.TEXT_KEY: text})["id"]
        elif media:
            cid = self._item(media[0], text=text)
        else:
            cid = self._text_only(text)
        self.wait(cid)
        pid = self._req("POST", f"{self.uid}/{self.PUBLISH_EDGE}", creation_id=cid)["id"]
        link = self._req("GET", pid, fields="permalink").get("permalink")
        return {"id": pid, "url": link}


class Threads(_Base):
    BASE = "https://graph.threads.net/v1.0"
    ENV = ("THREADS_ACCESS_TOKEN", "THREADS_USER_ID")
    MEDIA_EDGE = "threads"
    PUBLISH_EDGE = "threads_publish"
    TEXT_KEY = "text"

    def _media_params(self, url):
        return {"media_type": "VIDEO", "video_url": url} if kind(url) == "video" else {"media_type": "IMAGE", "image_url": url}

    def _text_only(self, text):
        return self._req("POST", f"{self.uid}/threads", media_type="TEXT", text=text)["id"]

    def refresh(self) -> dict:
        r = self.s.get("https://graph.threads.net/refresh_access_token",
                       params={"grant_type": "th_refresh_token", "access_token": self.token}, timeout=60)
        if r.status_code >= 400:
            raise MetaError(f"refresh -> {r.status_code}: {r.text[:300]}")
        return r.json()  # {access_token, token_type, expires_in}; caller stores the new token as a secret


class Instagram(_Base):
    BASE = "https://graph.facebook.com/v21.0"
    ENV = ("IG_ACCESS_TOKEN", "IG_USER_ID")
    MEDIA_EDGE = "media"
    PUBLISH_EDGE = "media_publish"
    TEXT_KEY = "caption"

    def _media_params(self, url):
        return {"media_type": "REELS", "video_url": url} if kind(url) == "video" else {"image_url": url}

    def _text_only(self, text):
        raise MetaError("Instagram needs media")

    def refresh(self) -> dict:
        # Instagram API with Instagram Login long-lived token refresh; Facebook-Login tokens use fb_exchange_token instead.
        r = self.s.get("https://graph.instagram.com/refresh_access_token",
                       params={"grant_type": "ig_refresh_token", "access_token": self.token}, timeout=60)
        if r.status_code >= 400:
            raise MetaError(f"refresh -> {r.status_code}: {r.text[:300]}")
        return r.json()
