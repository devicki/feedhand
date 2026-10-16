import pytest

from conftest import FakeSession, Resp
from feedhand.channels import meta


def test_threads_text_flow():
    s = FakeSession({
        ("POST", "/u1/threads"): Resp(200, {"id": "c1"}),
        ("GET", "/c1"): [Resp(200, {"status_code": "IN_PROGRESS"}), Resp(200, {"status_code": "FINISHED"})],
        ("POST", "/u1/threads_publish"): Resp(200, {"id": "p1"}),
        ("GET", "/p1"): Resp(200, {"permalink": "https://www.threads.net/@a/post/x"}),
    })
    d = meta.Threads("tok", "u1", s, sleep=lambda _: None).publish("hello")
    assert d == {"id": "p1", "url": "https://www.threads.net/@a/post/x"}
    assert s.calls[0][2]["params"]["media_type"] == "TEXT"


def test_ig_carousel():
    s = FakeSession({
        ("POST", "/u2/media"): [Resp(200, {"id": "k1"}), Resp(200, {"id": "k2"}), Resp(200, {"id": "car"})],
        ("GET", "/k1"): Resp(200, {"status_code": "FINISHED"}),
        ("GET", "/k2"): Resp(200, {"status_code": "FINISHED"}),
        ("GET", "/car"): Resp(200, {"status_code": "FINISHED"}),
        ("POST", "/u2/media_publish"): Resp(200, {"id": "p"}),
        ("GET", "/p"): Resp(200, {"permalink": "https://instagram.com/p/x"}),
    })
    d = meta.Instagram("tok", "u2", s, sleep=lambda _: None).publish("cap", ["https://c/a.jpg", "https://c/b.mp4"])
    assert d["id"] == "p"
    posts = [c[2]["params"] for c in s.calls if c[1].endswith("/u2/media")]
    assert posts[1]["media_type"] == "REELS" and posts[1]["is_carousel_item"] == "true"
    assert posts[2]["children"] == "k1,k2" and posts[2]["caption"] == "cap"


def test_meta_rejects_local_and_errors():
    c = meta.Instagram("tok", "u", FakeSession({}), sleep=lambda _: None)
    with pytest.raises(meta.MetaError, match="HTTPS"):
        c.publish("c", ["/tmp/a.jpg"])
    with pytest.raises(meta.MetaError, match="needs media"):
        c.publish("c")
    s = FakeSession({("POST", "/u/threads"): Resp(200, {"id": "c"}), ("GET", "/c"): Resp(200, {"status_code": "ERROR"})})
    with pytest.raises(meta.MetaError, match="ERROR"):
        meta.Threads("t", "u", s, sleep=lambda _: None).publish("x")


def test_missing_env():
    with pytest.raises(meta.MetaError, match="THREADS_ACCESS_TOKEN"):
        meta.Threads()
    assert meta.Instagram.missing_env() == ["IG_ACCESS_TOKEN", "IG_USER_ID"]


def test_refresh():
    s = FakeSession({("GET", "/refresh_access_token"): Resp(200, {"access_token": "new", "expires_in": 5184000})})
    assert meta.Threads("t", "u", s).refresh()["expires_in"] == 5184000
