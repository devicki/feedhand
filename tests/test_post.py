import json

from feedhand import post as PO


def v(post, ch):
    return PO.validate_channel(post, ch)


def test_x_weighted_len():
    assert PO.x_weighted_len("hello") == 5
    assert PO.x_weighted_len("안녕") == 4
    assert PO.x_weighted_len("こんにちは") == 10
    assert PO.x_weighted_len("see https://example.com/very/long/path/abc") == 4 + 23


def test_x_limits():
    assert v({"text": "a" * 280}, "x")["ok"]
    r = v({"text": "a" * 281}, "x")
    assert not r["ok"] and "281" in r["errors"][0]
    assert not v({"text": "가" * 141}, "x")["ok"]
    assert v({"text": "가" * 140}, "x")["ok"]


def test_x_media(tmp_path):
    imgs = []
    for i in range(5):
        p = tmp_path / f"{i}.jpg"
        p.write_bytes(b"x")
        imgs.append(str(p))
    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"x")
    assert v({"text": "t", "media": imgs[:4]}, "x")["ok"]
    assert not v({"text": "t", "media": imgs}, "x")["ok"]
    assert v({"text": "t", "media": [str(vid)]}, "x")["ok"]
    assert not v({"text": "t", "media": [str(vid), imgs[0]]}, "x")["ok"]
    assert not v({"text": "t", "media": [str(tmp_path / "missing.png")]}, "x")["ok"]


def test_x_link_cost():
    r = v({"text": "read https://example.com"}, "x")
    assert r["est_cost_usd"] == 0.2 and r["warnings"]
    r = v({"text": "plain"}, "x")
    assert r["est_cost_usd"] == 0.015 and not r["warnings"]


def test_reply_link_instead():
    r = v({"text": "new post https://example.com", "reply_link_instead": True}, "x")
    assert r["planned_posts"] == ["new post", "https://example.com"]
    assert r["est_cost_usd"] == round(0.015 + 0.2, 4)


def test_thread_chain():
    r = v({"per_channel": {"x": {"text": "1/", "thread": ["2/", "3/"]}}}, "x")
    assert r["planned_posts"] == ["1/", "2/", "3/"] and r["est_cost_usd"] == 0.045


def test_threads_rules():
    assert v({"text": "a" * 500}, "threads")["ok"]
    assert not v({"text": "a" * 501}, "threads")["ok"]
    six = " ".join(f"https://e{i}.com" for i in range(6))
    assert not v({"text": six}, "threads")["ok"]
    urls = [f"https://cdn.example.com/{i}.jpg" for i in range(21)]
    assert v({"text": "c", "media": urls[:2]}, "threads")["ok"]
    assert v({"text": "c", "media": urls[:1]}, "threads")["ok"]
    assert not v({"text": "c", "media": urls}, "threads")["ok"]


def test_meta_needs_https(tmp_path):
    p = tmp_path / "a.jpg"
    p.write_bytes(b"x")
    r = v({"text": "c", "media": [str(p)]}, "threads")
    assert not r["ok"] and "HTTPS" in r["errors"][0]
    assert v({"text": "c", "media": [str(p)], "per_channel": {"threads": {"mode": "manual"}}}, "threads")["ok"]


def test_instagram_rules():
    m = ["https://cdn.example.com/a.jpg"]
    assert not v({"text": "no media"}, "instagram")["ok"]
    assert v({"text": "c", "media": m}, "instagram")["ok"]
    assert not v({"text": "a" * 2201, "media": m}, "instagram")["ok"]
    tags = " ".join(f"#t{i}" for i in range(31))
    assert not v({"text": tags, "media": m}, "instagram")["ok"]
    assert v({"text": " ".join(f"#t{i}" for i in range(30)), "media": m}, "instagram")["ok"]
    assert len(PO.HASHTAG_RE.findall("#서울 #맛집 #cafe")) == 3


def test_manual_channels():
    assert v({"text": "x"}, "naver")["ok"]
    assert not v({"text": "x", "per_channel": {"naver": {"mode": "api"}}}, "naver")["ok"]
    assert v({"text": "x"}, "reddit")["warnings"]


def test_validate_top():
    r = PO.validate({"channels": []})
    assert not r["ok"] and len(r["errors"]) == 2
    r = PO.validate({"requester": "user", "channels": ["x", "naver"], "text": "hi"})
    assert r["ok"] and r["est_cost_usd"] == 0.015
    assert not PO.validate({"requester": "u", "channels": ["myspace"], "text": "a"})["ok"]
