"""X Premium features: video limits, 403 mapping, processing wait, x-check, edit, Articles, manual-mode guard."""
import json
import os

import pytest

from conftest import FakeSession, Resp
from feedhand import article as A, cli, log as L, media as M, post as PO, prefs as P
from feedhand.channels import x as X
from test_cli import run, write

CREDS = {"X_API_KEY": "k", "X_API_SECRET": "s", "X_ACCESS_TOKEN": "t", "X_ACCESS_SECRET": "ts"}


# ---------- 1. video limits, 403, processing wait ----------

def test_x_video_limits_follow_premium():
    std = M.limits("x", {})
    assert std["video_max_s"] == 20 * 60 and std["video_max_bytes"] == 8 * 1000**3
    pre = M.limits("x", {"x_premium": True})
    assert pre["video_max_s"] == 125 * 60 and pre["video_max_bytes"] == 16 * 1000**3
    assert M.limits("threads", {"x_premium": True})["video_max_s"] == 300


def test_media_check_duration_by_tier(tmp_path, monkeypatch):
    f = tmp_path / "v.mp4"
    f.write_bytes(b"0")
    info = {"duration": 30 * 60, "size": 2 * 1000**3, "vcodec": "h264", "acodec": "aac",
            "width": 1280, "height": 720, "fps": 30}
    monkeypatch.setattr(M, "probe", lambda p: dict(info))
    c = M.check(f, "x", {})
    assert c["needs_prep"] and "1200s" in c["issues"][0]
    assert not M.check(f, "x", {"x_premium": True})["needs_prep"]
    info["duration"] = 300  # 5 min used to be trimmed at 140 s
    assert not M.check(f, "x", {})["needs_prep"]
    P.set_many(["x_premium=true"])
    info["duration"] = 3600
    assert not M.check(f, "x")["needs_prep"]  # prefs from disk


def test_video_too_long_403_is_friendly():
    body = {"title": "Forbidden", "detail": "This user is not allowed to post a video longer than 20 minutes."}
    s = FakeSession({("POST", "/2/tweets"): Resp(403, body)})
    with pytest.raises(X.XError) as e:
        X.XClient(CREDS, s).create("x", ["m1"])
    msg = str(e.value)
    assert "up to 20 minutes" in msg and "media prep" in msg and "x-check --apply" in msg


def test_processing_budget_scales():
    assert X.processing_budget(0) == 300
    assert X.processing_budget(2 * 1024**3, 3600) > X.processing_budget(2 * 1024**3) > 1000
    assert X.processing_budget(16 * 1000**3, 125 * 60) == 4 * 3600


def test_processing_times_out_by_budget(tmp_path, monkeypatch):
    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"0")
    monkeypatch.setattr(X, "processing_budget", lambda size, dur=None: 10)
    s = FakeSession({
        ("POST", "/2/media/upload/initialize"): Resp(200, {"data": {"id": "v1"}}),
        ("POST", "/2/media/upload/v1/append"): Resp(204, None),
        ("POST", "/2/media/upload/v1/finalize"): Resp(200, {"data": {"processing_info": {"state": "pending", "check_after_secs": 3}}}),
        ("GET", "/2/media/upload"): Resp(200, {"data": {"processing_info": {"state": "in_progress", "check_after_secs": 3, "progress_percent": 40}}}),
    })
    sleeps = []
    with pytest.raises(X.XError, match="timed out after 12 s"):
        X.XClient(CREDS, s, sleep=sleeps.append).upload_media(str(vid))
    assert sleeps == [3, 3, 3, 3]


def test_chunk_grows_to_fit_segment_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(X, "CHUNK", 4)
    monkeypatch.setattr(X, "MAX_SEGMENTS", 2)
    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"0123456789")
    s = FakeSession({
        ("POST", "/2/media/upload/initialize"): Resp(200, {"data": {"id": "v1"}}),
        ("POST", "/2/media/upload/v1/append"): Resp(204, None),
        ("POST", "/2/media/upload/v1/finalize"): Resp(200, {"data": {"id": "v1"}}),
    })
    X.XClient(CREDS, s).upload_media(str(vid))
    assert len([c for c in s.calls if c[1].endswith("/append")]) == 2


# ---------- 2. x-check ----------

def test_me_requests_subscription_fields():
    s = FakeSession({("GET", "/2/users/me"): Resp(200, {"data": {"id": "1", "subscription_type": "Premium"}})})
    X.XClient(CREDS, s).me("subscription_type,verified_type")
    assert s.calls[0][2]["params"] == {"user.fields": "subscription_type,verified_type"}


class FakeMe:
    data = {}

    def me(self, fields=None):
        return dict(FakeMe.data)


def test_x_check_apply(capsys, monkeypatch):
    monkeypatch.setattr(X, "XClient", FakeMe)
    monkeypatch.setenv("X_API_SECRET", "TOPSECRET999")
    FakeMe.data = {"id": "7", "username": "me", "subscription_type": "PremiumPlus", "verified_type": "blue"}
    code, d = run(capsys, "x-check")
    assert code == 0 and d["x_premium_detected"] is True and d["articles_tier"] is True and not d["applied"]
    assert "TOPSECRET999" not in json.dumps(d)
    assert P.load()["x_premium"] is False
    code, d = run(capsys, "x-check", "--apply")
    assert d["applied"] and P.load()["x_premium"] is True and P.load()["x_subscription_type"] == "PremiumPlus"
    rows = [r for r in L.rows() if r["status"] == "x_check"]
    assert len(rows) == 2 and rows[0]["est_cost_usd"] == 0.01
    FakeMe.data = {"id": "7", "username": "me", "subscription_type": "None"}
    code, d = run(capsys, "x-check", "--apply")
    assert d["x_premium_detected"] is False and P.load()["x_premium"] is False


def test_x_check_unknown_does_not_apply(capsys, monkeypatch):
    monkeypatch.setattr(X, "XClient", FakeMe)
    P.set_many(["x_premium=true"])
    FakeMe.data = {"id": "7", "username": "me"}
    code, d = run(capsys, "x-check", "--apply")
    assert code == 0 and d["applied"] is False and d["x_premium_detected"] is None
    assert P.load()["x_premium"] is True


# ---------- 3. edit ----------

def test_edit_request_body():
    s = FakeSession({("POST", "/2/tweets"): Resp(200, {"data": {"id": "2", "edit_history_tweet_ids": ["1", "2"]}}),
                     ("GET", "/2/tweets/1"): Resp(200, {"data": {"id": "1", "edit_controls": {}}})})
    c = X.XClient(CREDS, s)
    assert c.edit("1", "fixed")["id"] == "2"
    assert s.calls[0][2]["json"] == {"text": "fixed", "edit_options": {"previous_post_id": "1"}}
    c.get_post("1")
    assert s.calls[1][2]["params"] == {"tweet.fields": "edit_controls,attachments,created_at"}


class FakeEdit:
    post = {}
    edits = []

    def get_post(self, tid):
        return dict(FakeEdit.post)

    def edit(self, tid, text):
        FakeEdit.edits.append((tid, text))
        return {"id": "200", "edit_history_tweet_ids": [tid, "200"]}


@pytest.fixture
def fe(monkeypatch):
    FakeEdit.post = {"id": "100", "edit_controls": {"is_edit_eligible": True, "edits_remaining": 5,
                                                    "editable_until": "2999-01-01T00:00:00.000Z"}}
    FakeEdit.edits = []
    monkeypatch.setattr(X, "XClient", FakeEdit)
    return FakeEdit


def test_edit_guards(capsys, fe):
    code, d = run(capsys, "edit", "100", "--text", "fixed", "--approved-by", "me")
    assert code == 2 and "X Premium" in d["error"]
    P.set_many(["x_premium=true"])
    code, d = run(capsys, "edit", "100", "--text", "fixed")
    assert code == 3 and d["needs_confirmation"] and d["preview"]["est_cost_usd"] == 0.02
    code, d = run(capsys, "edit", "100", "--text", "see https://example.com", "--no-check")
    assert d["preview"]["est_cost_usd"] == 0.2
    assert fe.edits == [] and L.rows() == []


def test_edit_refused_in_manual_mode(capsys, fe):
    P.set_many(["x_premium=true", 'per_channel_mode={"x": "manual"}'])
    code, d = run(capsys, "edit", "100", "--text", "fixed", "--approved-by", "me")
    assert code == 3 and d["needs_override"] == "--force-api" and "Original Content Rewards" in d["message"]
    assert fe.edits == []
    code, d = run(capsys, "edit", "100", "--text", "fixed", "--approved-by", "me", "--force-api")
    assert code == 0 and fe.edits == [("100", "fixed")]


def test_edit_success_logged(capsys, fe):
    P.set_many(["x_premium=true"])
    code, d = run(capsys, "edit", "100", "--text", "fixed", "--approved-by", "me")
    assert code == 0 and d["new_post_id"] == "200" and d["est_cost_usd"] == 0.02
    r = L.rows()[0]
    assert r["status"] == "edited" and r["post_id"] == "200" and r["est_cost_usd"] == 0.02
    assert "edit of 100" in r["note"] and "estimated" in r["note"]
    _, c = run(capsys, "cost", "--period", "all")
    assert c["per_channel"]["x"]["edits"] == 1


def test_edit_not_editable(capsys, fe):
    P.set_many(["x_premium=true"])
    fe.post["edit_controls"] = {"is_edit_eligible": True, "edits_remaining": 0}
    code, d = run(capsys, "edit", "100", "--text", "fixed", "--approved-by", "me")
    assert code == 2 and "no edits remaining" in d["error"] and fe.edits == []
    r = L.rows()[0]
    assert r["status"] == "edit_refused" and r["est_cost_usd"] == 0.005
    fe.post["edit_controls"] = {"is_edit_eligible": True, "editable_until": "2000-01-01T00:00:00.000Z"}
    code, d = run(capsys, "edit", "100", "--text", "fixed", "--approved-by", "me")
    assert code == 2 and "window closed" in d["error"]


def test_edit_post_with_media_needs_force(capsys, fe):
    P.set_many(["x_premium=true"])
    fe.post["attachments"] = {"media_keys": ["3_1"]}
    code, d = run(capsys, "edit", "100", "--text", "fixed", "--approved-by", "me")
    assert code == 2 and "--force" in d["error"]
    code, d = run(capsys, "edit", "100", "--text", "fixed", "--approved-by", "me", "--force")
    assert code == 0


def test_edit_length_uses_x_limit(capsys, fe):
    P.set_many(["x_premium=true"])
    assert run(capsys, "edit", "100", "--text", "a" * 300, "--approved-by", "me")[0] == 0
    assert run(capsys, "edit", "100", "--text", "a" * 25001, "--approved-by", "me")[0] == 2


# ---------- 4. Articles: conversion ----------

def test_markdown_inline_styles_utf16_offsets():
    cs = A.to_content_state("😀 **굵게** *it* ~~del~~ [link](https://e.com) https://a.com/x.")["content_state"]
    b = cs["blocks"][0]
    assert b["text"] == "😀 굵게 it del link https://a.com/x."
    st = {(r["style"], r["offset"], r["length"]) for r in b["inline_style_ranges"]}
    assert st == {("bold", 3, 2), ("italic", 6, 2), ("strikethrough", 9, 3)}
    ents = cs["entities"]
    assert [e["value"]["data"]["url"] for e in ents] == ["https://e.com", "https://a.com/x"]
    assert b["entity_ranges"] == [{"key": 0, "offset": 13, "length": 4}, {"key": 1, "offset": 18, "length": 15}]
    assert ents[0] == {"key": "0", "value": {"type": "link", "mutability": "mutable", "data": {"url": "https://e.com"}}}


def test_markdown_nested_and_escaped():
    b = A.to_content_state("**bold [in](https://e.com)** \\*lit\\* snake_case_name")["content_state"]["blocks"][0]
    assert b["text"] == "bold in *lit* snake_case_name"
    assert b["inline_style_ranges"] == [{"offset": 0, "length": 7, "style": "bold"}]
    assert b["entity_ranges"][0]["offset"] == 5


def test_markdown_blocks(tmp_path):
    md = ("# H1\n## H2\n### H3\n#### H4\npara line1\nline2\n\n- a\n* b\n1. one\n> q\n\n"
          "![cap](img.png \"Caption\")\nhttps://x.com/someone/status/123456\n\n---\n"
          "```py\nprint(1)\n```\n| a | b |\n|---|---|\n")
    r = A.to_content_state(md)
    types = [b["type"] for b in r["content_state"]["blocks"]]
    assert types == ["header-one", "header-two", "header-three", "header-three", "unstyled",
                     "unordered-list-item", "unordered-list-item", "ordered-list-item", "blockquote",
                     "atomic", "atomic", "atomic", "atomic", "atomic"]
    assert r["content_state"]["blocks"][4]["text"] == "para line1\nline2"
    ents = [e["value"] for e in r["content_state"]["entities"]]
    assert [e["type"] for e in ents] == ["image", "post", "divider", "markdown", "markdown"]
    assert ents[0]["data"]["caption"] == "Caption" and ents[1]["data"] == {"post_id": "123456"}
    assert ents[3]["data"]["markdown"] == "```py\nprint(1)\n```" and ents[2]["mutability"] == "immutable"
    atom = r["content_state"]["blocks"][-1]
    assert atom["text"] == " " and atom["entity_ranges"] == [{"key": 4, "offset": 0, "length": 1}]
    assert r["images"] == [{"entity": 0, "path": "img.png", "caption": "Caption"}] and not r["errors"]


def test_markdown_errors():
    assert "empty" in A.to_content_state("  \n")["errors"][0]
    assert "own line" in A.to_content_state("text ![x](a.png) more")["errors"][0]
    big = "```\n" + "a" * 10_001 + "\n```"
    assert "10000" in A.to_content_state(big)["errors"][0]


def test_fill_media():
    conv = A.to_content_state("![](a.png)\n\n![](b.gif)")
    cs = A.fill_media(conv, lambda p: "id-" + p)
    items = [e["value"]["data"]["media_items"][0] for e in cs["entities"]]
    assert items == [{"media_category": "tweet_image", "media_id": "id-a.png"},
                     {"media_category": "tweet_gif", "media_id": "id-b.gif"}]


# ---------- 4. Articles: validate / publish / bundle ----------

def art_post(tmp_path, **pc):
    img = tmp_path / "pic.png"
    img.write_bytes(b"png")
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"jpg")
    base = {"title": "제목", "text": f"# Hi\n**본문**\n\n![c]({img})\n", "cover": str(cover)}
    base.update(pc)
    return write(tmp_path, {"requester": "user", "channels": ["x_article"], "per_channel": {"x_article": base}})


def test_validate_article(capsys, tmp_path):
    code, d = run(capsys, "draft", "validate", art_post(tmp_path))
    assert code == 0, d
    ch = d["channels"][0]
    assert ch["article"]["image_count"] == 1 and ch["cost_unknown"] and d["cost_unknown_channels"] == ["x_article"]
    assert any("Premium+" in w for w in ch["warnings"])
    code, d = run(capsys, "draft", "validate", art_post(tmp_path, title="", text="![x](/nope.png)"))
    errs = " ".join(d["channels"][0]["errors"])
    assert code == 2 and "title is required" in errs and "not found" in errs
    P.set_many(["x_subscription_type=PremiumPlus"])
    code, d = run(capsys, "draft", "validate", art_post(tmp_path))
    assert not any("Premium+" in w for w in d["channels"][0]["warnings"])


def test_article_client_calls():
    s = FakeSession({("POST", "/2/articles/draft"): Resp(201, {"data": {"id": "55", "title": "T"}}),
                     ("POST", "/2/articles/55/publish"): Resp(200, {"data": {"post_id": "99"}})})
    c = X.XClient(CREDS, s)
    cs = {"blocks": [{"text": "a", "type": "unstyled"}], "entities": []}
    assert c.article_draft("T", cs, {"media_id": "m", "media_category": "tweet_image"})["id"] == "55"
    assert s.calls[0][2]["json"] == {"title": "T", "content_state": cs,
                                     "cover_media": {"media_id": "m", "media_category": "tweet_image"}}
    assert c.article_publish("55") == {"post_id": "99"}


class FakeArt:
    calls = []

    def upload_media(self, path, alt=None):
        FakeArt.calls.append(("upload", os.path.basename(path)))
        return "m-" + os.path.basename(path)

    def article_draft(self, title, cs, cover=None):
        FakeArt.calls.append(("draft", title, cs, cover))
        return {"id": "55", "title": title}

    def article_publish(self, aid):
        FakeArt.calls.append(("publish", aid))
        return {"post_id": "99"}


@pytest.fixture
def fa(monkeypatch):
    FakeArt.calls = []
    monkeypatch.setattr(X, "XClient", FakeArt)
    return FakeArt


def test_publish_article_api(capsys, tmp_path, fa):
    p = art_post(tmp_path)
    assert run(capsys, "publish", p)[0] == 3 and fa.calls == []
    code, d = run(capsys, "publish", p, "--approved-by", "me")
    assert code == 0, d
    r = d["results"][0]
    assert r["post_id"] == "99" and r["article_id"] == "55" and r["cost_unknown"]
    draft = [c for c in fa.calls if c[0] == "draft"][0]
    assert draft[1] == "제목" and draft[3] == {"media_id": "m-cover.jpg", "media_category": "tweet_image"}
    img = [e for e in draft[2]["entities"] if e["value"]["type"] == "image"][0]
    assert img["value"]["data"]["media_items"][0]["media_id"] == "m-pic.png"
    row = L.rows()[0]
    assert row["channel"] == "x_article" and row["est_cost_usd"] is None and "cost unknown" in row["note"]
    _, c = run(capsys, "cost", "--period", "all")
    assert c["unknown_cost_items"] == 1


def test_article_draft_then_publish(capsys, tmp_path, fa):
    code, d = run(capsys, "article", "draft", art_post(tmp_path))
    assert code == 0 and d["article_id"] == "55" and not d["published"]
    assert not [c for c in fa.calls if c[0] == "publish"]
    assert run(capsys, "article", "publish", "55")[0] == 3
    code, d = run(capsys, "article", "publish", "55", "--approved-by", "me")
    assert code == 0 and d["post_id"] == "99"
    assert [r["status"] for r in L.rows()] == ["published", "article_draft"]


def test_article_api_refused_in_manual_mode(capsys, tmp_path, fa):
    P.set_many(["default_mode_x=manual"])
    code, d = run(capsys, "article", "draft", art_post(tmp_path))
    assert code == 3 and d["needs_override"] == "--force-api"
    code, d = run(capsys, "article", "publish", "55", "--approved-by", "me")
    assert code == 3 and "Original Content Rewards" in d["message"]
    assert fa.calls == []
    assert run(capsys, "article", "publish", "55", "--approved-by", "me", "--force-api")[0] == 0


def test_article_manual_bundle(capsys, tmp_path, fa):
    P.set_many(['per_channel_mode={"x": "manual"}', f"out_dir={tmp_path / 'out'}"])
    p = art_post(tmp_path)
    code, d = run(capsys, "publish", p, "--approved-by", "me")
    assert code == 0, d
    r = d["results"][0]
    assert r["mode"] == "manual" and r["status"] == "bundle_ready" and fa.calls == []
    assert {"title.txt", "body.md", "body.txt", "cover.jpg", "01-pic.png", "meta.json"} <= set(r["files"])
    body = open(os.path.join(r["dir"], "body.txt"), encoding="utf-8").read()
    assert "본문" in body and "**" not in body and "[image:" in body
    code, d = run(capsys, "article", "bundle", p)
    assert code == 0 and "title.txt" in d["files"]
    code, d = run(capsys, "article", "render", p)
    assert code == 0 and d["content_state"]["blocks"][0]["type"] == "header-one"


# ---------- manual mode for X posts ----------

def test_default_mode_from_prefs():
    assert PO.default_mode("x", {}) == "api"
    assert PO.default_mode("x", {"default_mode_x": "manual"}) == "manual"
    assert PO.default_mode("x", {"default_mode_x": "api", "per_channel_mode": {"x": "manual"}}) == "manual"
    assert PO.default_mode("x_article", {"per_channel_mode": {"x": "manual"}}) == "manual"
    assert PO.default_mode("x_article", {"per_channel_mode": {"x": "manual", "x_article": "api"}}) == "api"
    assert PO.default_mode("naver", {"default_mode_naver": "api"}) == "manual"


def test_x_manual_mode_publish_bundles(capsys, tmp_path, monkeypatch):
    calls = []

    class Fake:
        def publish_thread(self, *a):
            calls.append(a)
            return [{"id": "1", "url": "u"}]

    monkeypatch.setattr(X, "XClient", Fake)
    P.set_many(["default_mode_x=manual", f"out_dir={tmp_path / 'out'}"])
    p = write(tmp_path, {"requester": "user", "channels": ["x"], "text": "hi"})
    code, d = run(capsys, "publish", p, "--approved-by", "me")
    assert code == 0 and d["results"][0]["status"] == "bundle_ready" and calls == []
    p2 = write(tmp_path, {"requester": "user", "channels": ["x"], "text": "hi", "per_channel": {"x": {"mode": "api"}}})
    code, d = run(capsys, "publish", p2, "--approved-by", "me")
    assert code == 3 and d["needs_override"] == "--force-api" and calls == []
    code, d = run(capsys, "publish", p2, "--approved-by", "me", "--force-api")
    assert code == 0 and len(calls) == 1
