import pytest

from conftest import FakeSession, Resp
from feedhand.channels import x as X

CREDS = {"X_API_KEY": "k", "X_API_SECRET": "s", "X_ACCESS_TOKEN": "t", "X_ACCESS_SECRET": "ts"}


def test_oauth_signature_known_vector():
    # Vector from X "Creating a signature" docs; cross-checked against oauthlib.
    creds = {"X_API_KEY": "xvz1evFS4wEEPTGEFPHBog",
             "X_API_SECRET": "kAcSOqF21Fu85e7zjz7ZN2U4ZRhfV3WpwPAoE3Z7kBw",
             "X_ACCESS_TOKEN": "370773112-GmHxMAgYyLbNEtIKZeRNFsMKPR9EyMZeS9weJAEb",
             "X_ACCESS_SECRET": "LswwdoUaIvS8ltyTt5jkRh4J50vUPVVHtR2YPi5kE"}
    h = X.oauth_header("POST", "https://api.twitter.com/1.1/statuses/update.json", creds,
                       {"include_entities": "true", "status": "Hello Ladies + Gentlemen, a signed OAuth request!"},
                       nonce="kYjzVBB8Y0ZFabxSWbWovY3uYSQ2pTgmZeNu2VS4cg", ts=1318622958)
    assert 'oauth_signature="hCtSmYh%2BiHYCEqBWrE7C7hYmtUk%3D"' in h


def test_missing_creds():
    with pytest.raises(X.XError, match="X_API_KEY"):
        X.XClient()


def test_thread_with_image(tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"png")
    s = FakeSession({
        ("POST", "/2/media/upload"): Resp(200, {"data": {"id": "m1"}}),
        ("POST", "/2/media/metadata"): Resp(200, {"data": {}}),
        ("POST", "/2/tweets"): [Resp(201, {"data": {"id": "1"}}), Resp(201, {"data": {"id": "2"}})],
    })
    out = X.XClient(CREDS, s).publish_thread(["a", "b"], [str(img)], {str(img): "alt"})
    assert [o["id"] for o in out] == ["1", "2"]
    tweets = [c for c in s.calls if c[1].endswith("/2/tweets")]
    assert tweets[0][2]["json"] == {"text": "a", "media": {"media_ids": ["m1"]}}
    assert tweets[1][2]["json"] == {"text": "b", "reply": {"in_reply_to_tweet_id": "1"}}
    assert all(c[2]["headers"]["Authorization"].startswith("OAuth ") for c in s.calls)


def test_chunked_video(tmp_path, monkeypatch):
    monkeypatch.setattr(X, "CHUNK", 4)
    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"0123456789")  # 3 chunks
    s = FakeSession({
        ("POST", "/2/media/upload/initialize"): Resp(200, {"data": {"id": "v1"}}),
        ("POST", "/2/media/upload/v1/append"): Resp(204, None),
        ("POST", "/2/media/upload/v1/finalize"): Resp(200, {"data": {"id": "v1", "processing_info": {"state": "pending", "check_after_secs": 1}}}),
        ("GET", "/2/media/upload"): [Resp(200, {"data": {"processing_info": {"state": "in_progress"}}}),
                                     Resp(200, {"data": {"processing_info": {"state": "succeeded"}}})],
    })
    sleeps = []
    assert X.XClient(CREDS, s, sleep=sleeps.append).upload_media(str(vid)) == "v1"
    appends = [c for c in s.calls if c[1].endswith("/append")]
    assert [c[2]["data"]["segment_index"] for c in appends] == ["0", "1", "2"]
    assert len(sleeps) == 2
    init = s.calls[0][2]["json"]
    assert init == {"media_type": "video/mp4", "total_bytes": 10, "media_category": "tweet_video"}


def test_processing_failed(tmp_path):
    vid = tmp_path / "v.mp4"
    vid.write_bytes(b"0")
    s = FakeSession({
        ("POST", "/2/media/upload/initialize"): Resp(200, {"data": {"id": "v1"}}),
        ("POST", "/2/media/upload/v1/append"): Resp(204, None),
        ("POST", "/2/media/upload/v1/finalize"): Resp(200, {"data": {"processing_info": {"state": "failed"}}}),
    })
    with pytest.raises(X.XError, match="failed"):
        X.XClient(CREDS, s, sleep=lambda _: None).upload_media(str(vid))


def test_delete_and_error():
    s = FakeSession({("DELETE", "/2/tweets/9"): Resp(200, {"data": {"deleted": True}}),
                     ("POST", "/2/tweets"): Resp(403, {"title": "Forbidden"})})
    c = X.XClient(CREDS, s)
    assert c.delete("9")
    with pytest.raises(X.XError, match="403"):
        c.create("x")
