"""Manual channels (Naver Blog, Reddit, or any channel in mode 'manual'): build a copy-paste bundle."""
from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from feedhand.post import resolve


def bundle(post: dict, ch: str, out_root: str = "~/feedhand-out") -> dict:
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
