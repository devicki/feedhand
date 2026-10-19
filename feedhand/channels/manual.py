"""Manual channels (Naver Blog, Reddit, or any channel in mode 'manual'): build a copy-paste bundle."""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from feedhand.post import resolve


def _copy(src, d, name):
    src = Path(src).expanduser()
    if src.exists():
        shutil.copy2(src, d / name)
        return name
    return None


def article_bundle(post: dict, out_root: str = "~/feedhand-out") -> dict:
    """X Article for pasting into the X Articles composer by hand: title.txt, body.md, body.txt, cover, images."""
    from feedhand import article as A
    r = resolve(post, "x_article")
    slug = re.sub(r"[^\w-]+", "-", r["title"] or "article").strip("-")[:40] or "article"
    d = Path(out_root).expanduser() / f"{time.strftime('%Y%m%d-%H%M%S')}-x_article-{slug}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "title.txt").write_text((r["title"] or "") + "\n", encoding="utf-8")
    (d / "body.md").write_text(r["text"].rstrip() + "\n", encoding="utf-8")
    (d / "body.txt").write_text(A.plain_text(r["text"]), encoding="utf-8")
    cover = _copy(r["cover"], d, "cover" + Path(r["cover"]).suffix.lower()) if r["cover"] else None
    imgs = []
    for i, im in enumerate(A.to_content_state(r["text"])["images"], 1):
        n = _copy(im["path"], d, f"{i:02d}-{Path(im['path']).name}")
        imgs.append({"file": n, "source": im["path"], "caption": im["caption"]})
    (d / "meta.json").write_text(json.dumps({"channel": "x_article", "requester": post.get("requester"),
                                             "title": r["title"], "cover": cover, "images": imgs,
                                             "how_to": "x.com > Articles > Write: paste title.txt and body.txt "
                                                       "(or body.md), add cover and images in order, publish"},
                                            ensure_ascii=False, indent=1))
    return {"dir": str(d), "files": sorted(p.name for p in d.iterdir())}


def bundle(post: dict, ch: str, out_root: str = "~/feedhand-out") -> dict:
    if ch == "x_article":
        return article_bundle(post, out_root)
    r = resolve(post, ch)
    slug = re.sub(r"[^\w-]+", "-", (r["title"] or r["text"][:30] or "post")).strip("-")[:40] or "post"
    d = Path(out_root).expanduser() / f"{time.strftime('%Y%m%d-%H%M%S')}-{ch}-{slug}"
    d.mkdir(parents=True, exist_ok=True)
    parts = []
    if r["title"]:
        parts.append(f"TITLE:\n{r['title']}\n")
    if r["subreddit"]:
        parts.append(f"SUBREDDIT: r/{r['subreddit'].removeprefix('r/')}\n")
    parts.append(r["text"])
    for t in r["thread"]:
        parts.append("\n---\n" + t)
    (d / "post.txt").write_text("\n".join(parts) + "\n", encoding="utf-8")
    copied, links = [], []
    for i, m in enumerate(r["media"], 1):
        if m.startswith("http"):
            links.append(m)
            continue
        src = Path(m).expanduser()
        if src.exists():
            dst = d / f"{i:02d}-{src.name}"
            shutil.copy2(src, dst)
            copied.append(dst.name)
    if links:
        (d / "media-links.txt").write_text("\n".join(links) + "\n")
    (d / "meta.json").write_text(json.dumps({"channel": ch, "requester": post.get("requester"),
                                             "media": copied, "media_links": links}, ensure_ascii=False, indent=1))
    return {"dir": str(d), "files": sorted(p.name for p in d.iterdir())}
