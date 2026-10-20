"""post.json schema + per-channel hard-rule validation.

post.json:
{
  "requester": "user" | "<bot name>",
  "channels": ["x", "threads", "instagram", "naver", "reddit"],
  "text": "default text for all channels (optional)",
  "media": ["path/or/https-url", ...],          # default media
  "reply_link_instead": false,                   # X: move links to a self-reply
  "per_channel": {
    "x": {"text": "...", "media": [...], "mode": "api"|"manual", "thread": ["2nd", "3rd"]},
    "threads": {...}, "instagram": {...},
    "reddit": {"title": "...", "subreddit": "..."}
  }
}
"""
from __future__ import annotations

import json
import re
from pathlib import Path

API_CHANNELS = {"x", "x_article", "threads", "instagram"}
MANUAL_CHANNELS = {"naver", "reddit"}
ALL_CHANNELS = API_CHANNELS | MANUAL_CHANNELS

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".webm"}

URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"]+|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|net|org|io|ai|kr|co|me|ly|gg|app|dev|xyz)(?:/[^\s]*)?\b", re.I)
HASHTAG_RE = re.compile(r"(?<!\w)#[\w\u3131-\uD79D]+", re.U)

X_COST_POST = 0.015
X_COST_POST_LINK = 0.20
X_COST_MEDIA_ALT = 0.005
X_COST_DELETE = 0.01  # confirmed in the X console usage view

# X weighted-length limits. Standard accounts: 280 (docs.x.com "Counting characters").
# X Premium (any tier) "longer posts": 25,000 per X's Premium help page. X documents the
# weighting (URL = 23, CJK/emoji = 2) only for the 280 case; we apply the same weighting to
# long posts. The X API pricing table bills Post: Create per request, with no separate
# line for long posts, so cost estimates do not change with length.
X_LIMIT_STANDARD = 280
X_LIMIT_PREMIUM = 25_000


def load(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def default_mode(ch: str, prefs: dict | None = None) -> str:
    """Mode when post.json does not set one: prefs per_channel_mode[ch], then default_mode_<ch>;
    x_article follows X's setting unless it has its own. Manual-only channels are always manual."""
    if ch in MANUAL_CHANNELS:
        return "manual"
    if prefs is None:
        from feedhand import prefs as P
        prefs = P.load()
    pcm = prefs.get("per_channel_mode") or {}
    for c in ([ch, "x"] if ch == "x_article" else [ch]):
        m = pcm.get(c) or prefs.get(f"default_mode_{c}")
        if m:
            return m
    return "api"


def resolve(post: dict, ch: str, prefs: dict | None = None) -> dict:
    pc = (post.get("per_channel") or {}).get(ch, {})
    return {
        "text": pc.get("text", post.get("text", "")) or "",
        "media": list(pc.get("media", post.get("media", [])) or []) if ch != "x_article" else [],
        "mode": pc.get("mode") or default_mode(ch, prefs),
        "cover": pc.get("cover"),
        "thread": list(pc.get("thread", []) or []),
        "alt": pc.get("alt", post.get("alt", {})) or {},
        "title": pc.get("title", post.get("title")),
        "subreddit": pc.get("subreddit"),
        "reply_link_instead": bool(pc.get("reply_link_instead", post.get("reply_link_instead", False))),
    }


def kind(m: str) -> str:
    ext = Path(m.split("?")[0]).suffix.lower()
    if ext in VIDEO_EXT:
        return "video"
    if ext in IMAGE_EXT:
        return "image"
    return "unknown"


def links(text: str) -> list[str]:
    return URL_RE.findall(text or "")


def _w(ch: str) -> int:
    o = ord(ch)
    if o <= 0x10FF or 0x2000 <= o <= 0x200D or 0x2010 <= o <= 0x201F or 0x2032 <= o <= 0x2037:
        return 1
    return 2


def x_weighted_len(text: str) -> int:
    """twitter-text v3 approximation: URLs count 23, CJK/emoji count 2."""
    n = 0
    pos = 0
    for m in URL_RE.finditer(text):
        n += sum(_w(c) for c in text[pos:m.start()]) + 23
        pos = m.end()
    n += sum(_w(c) for c in text[pos:])
    return n


def strip_links(text: str) -> tuple[str, list[str]]:
    ls = links(text)
    t = URL_RE.sub("", text)
    return re.sub(r"[ \t]{2,}", " ", t).strip(), ls


def x_limit(prefs: dict | None = None) -> int:
    return X_LIMIT_PREMIUM if (prefs or {}).get("x_premium") is True else X_LIMIT_STANDARD


def x_plan(r: dict) -> list[str]:
    """Return the list of tweet texts (main + replies)."""
    texts = [r["text"]] + r["thread"]
    if r["reply_link_instead"]:
        main, ls = strip_links(r["text"])
        if ls:
            texts = [main] + r["thread"] + [" ".join(ls)]
    return texts


def x_cost(texts: list[str], r: dict) -> float:
    c = sum(X_COST_POST_LINK if links(t) else X_COST_POST for t in texts)
    c += X_COST_MEDIA_ALT * len([k for k in r["alt"]])
    return round(c, 4)


def media_warnings(media, ch, prefs=None) -> list[str]:
    from feedhand import media as M
    out = []
    for m in media:
        lp = Path(m).expanduser()
        if m.startswith("http") or not lp.exists() or lp.suffix.lower() not in VIDEO_EXT | {".gif"}:
            continue
        if not M.available():
            out.append(f"{ch}: cannot check {m} (ffmpeg not installed)")
            continue
        try:
            c = M.check(lp, ch, prefs)
        except Exception as e:  # noqa: BLE001
            out.append(f"{ch}: could not probe {m}: {e}")
            continue
        if c["needs_prep"]:
            out.append(f"{ch}: media needs prep: {m}: {'; '.join(c['issues'])} "
                       f"-> run `feedhand media prep {m} --channel {ch}`")
    return out


def validate_channel(post: dict, ch: str, prefs: dict | None = None) -> dict:
    """prefs: user prefs (only x_premium is read here); None = load from disk."""
    if prefs is None:
        from feedhand import prefs as P
        prefs = P.load()
    errs, warns = [], []
    r = resolve(post, ch, prefs)
    media = r["media"]
    kinds = [kind(m) if isinstance(m, str) else "server" for m in media]
    est = 0.0
    if ch == "x_article" and r["media"] == [] and (post.get("per_channel") or {}).get(ch, {}).get("media"):
        warns.append("X Article: per_channel.x_article.media is ignored; put images in the Markdown body")
    if r["mode"] not in ("api", "manual"):
        errs.append(f"mode must be api or manual, got {r['mode']!r}")
    if ch in MANUAL_CHANNELS and r["mode"] == "api":
        errs.append(f"{ch} has no posting API; use mode manual")
    server = [m for m in media if isinstance(m, dict)]
    if server:
        errs.append(f"{ch}: {len(server)} server media entr(y/ies) not fetched yet; run "
                    "`feedhand media fetch --request FILE` first")
        media = [m for m in media if not isinstance(m, dict)]
        kinds = [kind(m) for m in media]
    for m, k in zip(media, kinds):
        if k == "unknown":
            errs.append(f"unsupported media type: {m}")
        if not m.startswith("http") and not Path(m).expanduser().exists():
            errs.append(f"media file not found: {m}")
    if ch in ("x", "threads", "instagram"):
        warns += media_warnings(media, ch, prefs)
    if ch == "x":
        texts = x_plan(r)
        limit = x_limit(prefs)
        for i, t in enumerate(texts):
            n = x_weighted_len(t)
            if n > limit:
                errs.append(f"post {i+1}: {n} weighted chars > {limit}")
            elif n > X_LIMIT_STANDARD:
                warns.append(f"X: post {i+1} is a long post ({n} weighted chars, x_premium); timelines show "
                             f"about the first {X_LIMIT_STANDARD} before 'Show more', and @mentions after that "
                             "point do not notify")
            if not t.strip() and not (i == 0 and media):
                errs.append(f"post {i+1}: empty text")
        if kinds.count("video") > 1 or ("video" in kinds and len(media) > 1):
            errs.append("X: one video only, and not mixed with images")
        if len(media) > 4:
            errs.append(f"X: max 4 images, got {len(media)}")
        if any(links(t) for t in texts):
            warns.append(f"X: a post contains a link; costs ${X_COST_POST_LINK} vs ${X_COST_POST} without "
                         "(set reply_link_instead to move it to a reply — the reply itself still costs the link rate)")
        est = x_cost(texts, r)
        r["planned_posts"] = texts
    elif ch == "x_article":
        from feedhand import article as A
        conv = A.to_content_state(r["text"])
        if not (r["title"] or "").strip():
            errs.append("X Article: title is required")
        errs += [f"X Article: {e}" for e in conv["errors"]]
        imgs = [i["path"] for i in conv["images"]] + ([r["cover"]] if r["cover"] else [])
        errs += [f"X Article: {e}" for e in A.check_images(imgs)]
        sub = prefs.get("x_subscription_type")
        if sub != "PremiumPlus":
            warns.append("X Article: Articles are listed as a Premium+ feature in the X app (help.x.com, not "
                         "verified for the API); " + (f"this account reports {sub}" if sub else
                                                       "run `feedhand x-check` to see the account's tier"))
        if r["mode"] == "api":
            warns.append("X Article: API cost is unknown (not in the X pricing table); logged as unknown")
        r["article"] = {"title": r["title"], "cover": r["cover"], "block_count": conv["block_count"],
                        "image_count": conv["image_count"], "embedded_posts": conv["embedded_posts"],
                        "links": conv["links"]}
    elif ch == "threads":
        t = r["text"]
        if len(t) > 500:
            errs.append(f"Threads: {len(t)} chars > 500")
        if len(set(links(t))) > 5:
            errs.append("Threads: more than 5 links")
        if len(media) > 1 and not 2 <= len(media) <= 20:
            errs.append("Threads carousel needs 2-20 items")
        if not t.strip() and not media:
            errs.append("Threads: empty post")
        if r["mode"] == "api" and any(not m.startswith("https://") for m in media):
            errs.append("Threads API needs public HTTPS media URLs (local files must be hosted first)")
    elif ch == "instagram":
        t = r["text"]
        if not media:
            errs.append("Instagram needs at least one image or video")
        if len(t) > 2200:
            errs.append(f"Instagram: caption {len(t)} chars > 2200")
        if len(HASHTAG_RE.findall(t)) > 30:
            errs.append("Instagram: more than 30 hashtags")
        if len(media) > 10:
            errs.append("Instagram carousel max 10 items")
        if r["mode"] == "api" and any(not m.startswith("https://") for m in media):
            errs.append("Instagram API needs public HTTPS media URLs (local files must be hosted first)")
        if links(t):
            warns.append("Instagram: links in captions are not clickable")
    elif ch == "reddit":
        if not r["title"]:
            warns.append("Reddit: no title set (bundle will leave it blank)")
    elif ch == "naver":
        pass
    else:
        errs.append(f"unknown channel {ch!r}")
    return {"channel": ch, "mode": r["mode"], "ok": not errs, "errors": errs, "warnings": warns,
            "est_cost_usd": est, **({"planned_posts": r["planned_posts"]} if "planned_posts" in r else {}),
            **({"article": r["article"], "cost_unknown": r["mode"] == "api"} if "article" in r else {}),
            **({"x_limit": x_limit(prefs)} if ch == "x" else {})}


def validate(post: dict, prefs: dict | None = None) -> dict:
    if prefs is None:
        from feedhand import prefs as P
        prefs = P.load()
    chs = post.get("channels") or []
    errs = []
    if not chs:
        errs.append("channels list is empty")
    if not post.get("requester"):
        errs.append("requester is required (user or bot name)")
    res = [validate_channel(post, c, prefs) for c in chs]
    ok = not errs and all(r["ok"] for r in res)
    unknown = [r["channel"] for r in res if r.get("cost_unknown")]
    return {"ok": ok, "errors": errs, "channels": res,
            "est_cost_usd": round(sum(r["est_cost_usd"] for r in res), 4),
            **({"cost_unknown_channels": unknown} if unknown else {})}
