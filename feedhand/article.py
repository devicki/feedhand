"""X Articles: Markdown -> DraftJS content_state for POST /2/articles/draft.

Schema per api.x.com/2/openapi.json (ArticleCreateDraft*), checked 2026-10-19:
- block types: unstyled, header-one/two/three, unordered-list-item, ordered-list-item, blockquote, atomic
- inline styles: bold, italic, strikethrough
- entities [{key, value: {type, mutability, data}}]; entity_ranges.key = index into entities
- entity types used here: link (data.url), image (data.media_items), post (data.post_id),
  divider ({}), markdown (data.markdown: fenced code / GFM tables; <=10,000 weighted per article)
- atomic blocks carry a single space as text, with one entity range over it.
Offsets/lengths are UTF-16 code units (DraftJS runs in JavaScript) - inferred, not stated in the spec.

Markdown supported: # / ## / ### headings (#### and deeper -> header-three), paragraphs (single newlines
kept as soft breaks), -/*/+ and 1. lists, > quotes, **bold** / __bold__, *italic* / _italic_, ~~strike~~,
[text](url), bare http(s) URLs, ![caption](local-image) on its own line, a post URL on its own line
(embedded post), --- divider, ``` fenced code and | tables (as markdown entities).
"""
from __future__ import annotations

import re
from pathlib import Path

MARKDOWN_ENTITY_MAX = 10_000
POST_URL_RE = re.compile(r"^https?://(?:www\.|mobile\.)?(?:x|twitter)\.com/(?:[A-Za-z0-9_]+|i/web|i)/status/(\d{1,19})\S*$")
IMG_RE = re.compile(r'^!\[([^\]]*)\]\(\s*<?([^)>\s]+)>?(?:\s+"([^"]*)")?\s*\)$')
INLINE_IMG_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
HEAD_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
UL_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
OL_RE = re.compile(r"^\s*\d+[.)]\s+(.*)$")
QUOTE_RE = re.compile(r"^\s*>\s?(.*)$")
HR_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
HEAD_TYPES = {1: "header-one", 2: "header-two"}

# inline tokens, tried in order at each position
TOKENS = [
    ("link", re.compile(r"\[([^\]]+)\]\(\s*<?([^)\s>]+)>?\s*\)")),
    ("bold", re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")),
    ("bold", re.compile(r"(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)")),
    ("strikethrough", re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")),
    ("italic", re.compile(r"\*(?=[^\s*])(.+?)(?<=[^\s*])\*")),
    ("italic", re.compile(r"(?<!\w)_(?=[^\s_])(.+?)(?<=[^\s_])_(?!\w)")),
    ("url", re.compile(r"https?://[^\s<>()\[\]]+[^\s<>()\[\].,;:!?'\"]")),
]
ESC_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!~|>])")


def u16(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


class _Builder:
    def __init__(self):
        self.blocks, self.entities, self.images, self.markdown_len = [], [], [], 0

    def entity(self, typ, mut, data) -> int:
        self.entities.append({"key": str(len(self.entities)), "value": {"type": typ, "mutability": mut, "data": data}})
        return len(self.entities) - 1

    def atomic(self, typ, mut, data) -> int:
        k = self.entity(typ, mut, data)
        self.blocks.append({"text": " ", "type": "atomic", "entity_ranges": [{"key": k, "offset": 0, "length": 1}]})
        return k

    def text_block(self, typ, md):
        text, styles, ents = self.inline(md)
        b = {"text": text, "type": typ}
        if styles:
            b["inline_style_ranges"] = styles
        if ents:
            b["entity_ranges"] = ents
        self.blocks.append(b)

    def inline(self, md: str):
        """Return (plain text, inline_style_ranges, entity_ranges) for one block of inline Markdown."""
        out, styles, ents = [], [], []
        pos_u16 = 0

        def emit(s):
            nonlocal pos_u16
            s = ESC_RE.sub(r"\1", s)
            out.append(s)
            pos_u16 += u16(s)

        i = 0
        while i < len(md):
            best = None
            for name, rx in TOKENS:
                m = rx.search(md, i)
                if m and (best is None or m.start() < best[1].start()):
                    best = (name, m)
            if best is None or (best[1].start() > 0 and md[best[1].start() - 1] == "\\"):
                if best is None:
                    emit(md[i:])
                    break
                emit(md[i:best[1].start() + 1])
                i = best[1].start() + 1
                continue
            name, m = best
            emit(md[i:m.start()])
            start = pos_u16
            if name == "url":
                emit(m.group(0))
                ents.append({"key": self.entity("link", "mutable", {"url": m.group(0)}), "offset": start,
                             "length": pos_u16 - start})
            else:
                t, st, en = self.inline(m.group(1))
                out.append(t)
                pos_u16 += u16(t)
                styles += [{**r, "offset": r["offset"] + start} for r in st]
                ents += [{**r, "offset": r["offset"] + start} for r in en]
                if name == "link":
                    ents.append({"key": self.entity("link", "mutable", {"url": m.group(2)}), "offset": start,
                                 "length": pos_u16 - start})
                else:
                    styles.append({"offset": start, "length": pos_u16 - start, "style": name})
            i = m.end()
        return "".join(out), styles, ents


def to_content_state(md: str) -> dict:
    """Return {'content_state', 'images': [{'entity': idx, 'path', 'caption'}], 'errors', 'warnings', counts}."""
    b = _Builder()
    errs, warns = [], []
    lines = (md or "").replace("\r\n", "\n").split("\n")
    para: list[str] = []

    def flush():
        if para:
            b.text_block("unstyled", "\n".join(para))
            para.clear()

    def md_entity(src):
        b.markdown_len += len(src)
        b.atomic("markdown", "mutable", {"markdown": src})

    i = 0
    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        if FENCE_RE.match(ln):
            flush()
            fence = FENCE_RE.match(ln).group(1)
            j = i + 1
            while j < len(lines) and not lines[j].strip().startswith(fence):
                j += 1
            md_entity("\n".join(lines[i:j + 1]) if j < len(lines) else "\n".join(lines[i:]) + "\n" + fence)
            i = j + 1
            continue
        if s.startswith("|"):
            flush()
            j = i
            while j < len(lines) and lines[j].strip().startswith("|"):
                j += 1
            md_entity("\n".join(x.strip() for x in lines[i:j]))
            i = j
            continue
        if not s:
            flush()
        elif HR_RE.match(ln) and not para:
            b.atomic("divider", "immutable", {})
        elif (m := IMG_RE.match(s)):
            flush()
            cap = m.group(3) or ""
            k = b.atomic("image", "immutable", {"media_items": [{"media_category": "tweet_image", "media_id": ""}],
                                                **({"caption": cap} if cap else {})})
            b.images.append({"entity": k, "path": m.group(2), "caption": cap})
        elif (m := POST_URL_RE.match(s)):
            flush()
            b.atomic("post", "immutable", {"post_id": m.group(1)})
        elif (m := HEAD_RE.match(s)):
            flush()
            b.text_block(HEAD_TYPES.get(len(m.group(1)), "header-three"), m.group(2))
        elif (m := UL_RE.match(ln)) and not HR_RE.match(ln):
            flush()
            b.text_block("unordered-list-item", m.group(1))
        elif (m := OL_RE.match(ln)):
            flush()
            b.text_block("ordered-list-item", m.group(1))
        elif (m := QUOTE_RE.match(ln)):
            flush()
            b.text_block("blockquote", m.group(1))
        else:
            if INLINE_IMG_RE.search(s):
                errs.append(f"images must be on their own line: {s[:60]}")
            para.append(ln.rstrip())
        i += 1
    flush()
    if not b.blocks:
        errs.append("article body is empty")
    if b.markdown_len > MARKDOWN_ENTITY_MAX:
        errs.append(f"code blocks/tables total {b.markdown_len} chars > {MARKDOWN_ENTITY_MAX} per article")
    counts = {"block_count": len(b.blocks), "image_count": len(b.images),
              "embedded_posts": sum(e["value"]["type"] == "post" for e in b.entities),
              "links": sum(e["value"]["type"] == "link" for e in b.entities)}
    return {"content_state": {"blocks": b.blocks, "entities": b.entities}, "images": b.images,
            "errors": errs, "warnings": warns, **counts}


def check_images(paths, base: Path | None = None) -> list[str]:
    from feedhand.channels.x import media_category
    errs = []
    for p in paths:
        if p.startswith("http"):
            errs.append(f"article image must be a local file (upload needs bytes): {p}")
            continue
        lp = Path(p).expanduser()
        if not lp.exists():
            errs.append(f"article image not found: {p}")
            continue
        mime, cat = media_category(lp)
        if cat == "tweet_video":
            errs.append(f"article images must be images, got video: {p}")
        elif lp.stat().st_size > (15 if cat == "tweet_gif" else 5) * 1024**2:
            errs.append(f"article image too large for X ({lp.stat().st_size / 1024**2:.1f}MB): {p}")
    return errs


def fill_media(conv: dict, upload) -> dict:
    """Upload body images with upload(path) -> media_id and put the ids into the content_state."""
    from feedhand.channels.x import media_category
    cs = conv["content_state"]
    for im in conv["images"]:
        item = cs["entities"][im["entity"]]["value"]["data"]["media_items"][0]
        item["media_id"] = upload(im["path"])
        item["media_category"] = media_category(im["path"])[1]
    return cs


def plain_text(md: str) -> str:
    """Markdown -> readable plain text for pasting into the X Articles composer by hand."""
    conv = to_content_state(md)
    lines, n = [], 0
    ents = conv["content_state"]["entities"]
    img_path = {im["entity"]: im["path"] for im in conv["images"]}
    for blk in conv["content_state"]["blocks"]:
        t = blk["type"]
        if t == "atomic":
            k = blk["entity_ranges"][0]["key"]
            e = ents[k]["value"]
            d = e["data"]
            lines.append({"divider": "---", "post": f"[embed post {d.get('post_id')}]",
                          "markdown": d.get("markdown", ""),
                          "image": f"[image: {img_path.get(k, '')}]"}
                         .get(e["type"], ""))
        elif t == "ordered-list-item":
            n += 1
            lines.append(f"{n}. {blk['text']}")
            continue
        elif t == "unordered-list-item":
            lines.append(f"• {blk['text']}")
        elif t == "blockquote":
            lines.append(f"“{blk['text']}”")
        else:
            lines.append(blk["text"])
        n = 0
    return "\n\n".join(lines) + "\n"
