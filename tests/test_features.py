import json
import shutil
import subprocess
from datetime import datetime, timedelta

import pytest

from feedhand import log as L, media as M, prefs as P
from feedhand.channels import x as X
from test_cli import run, write

FF = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg missing")


class FakeX:
    calls = []

    def publish_thread(self, texts, media, alt):
        FakeX.calls.append((texts, media))
        return [{"id": str(len(FakeX.calls)), "url": f"https://x.com/i/web/status/{len(FakeX.calls)}"}]


@pytest.fixture
def fx(monkeypatch):
    FakeX.calls = []
    monkeypatch.setattr(X, "XClient", FakeX)
    return FakeX


def future(minutes=30):
    return (datetime.now() + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S")


def test_schedule_requires_approval_and_future(capsys, tmp_path):
    p = write(tmp_path, {"requester": "user", "channels": ["x"], "text": "hi"})
    assert run(capsys, "schedule", "add", p, "--at", future())[0] == 3
    assert run(capsys, "schedule", "add", p, "--at", "2020-01-01T00:00", "--approved-by", "me")[0] == 2
    assert run(capsys, "schedule", "add", p, "--at", "notatime", "--approved-by", "me")[0] == 2


def test_schedule_flow_idempotent(capsys, tmp_path, fx):
    img = tmp_path / "a.jpg"
    img.write_bytes(b"x")
    p = write(tmp_path, {"requester": "user", "channels": ["x"], "text": "hi", "media": [str(img)]})
    code, d = run(capsys, "schedule", "add", p, "--at", future(), "--approved-by", "devicki")
    assert code == 0 and d["pending"] == 1
    qid = d["scheduled"]["id"]
    img.unlink()  # original gone; queue copy must be used
    code, lst = run(capsys, "schedule", "list")
    assert lst["items"][0]["status"] == "pending"
    code, r = run(capsys, "schedule", "run-due")
    assert r["ran"] == [] and not fx.calls
    later = (datetime.now() + timedelta(hours=1)).isoformat()
    code, r = run(capsys, "schedule", "run-due", "--now", later)
    assert code == 0 and r["ran"][0]["status"] == "done" and r["stop_routine"]
    assert "/queue/%d/" % qid in fx.calls[0][1][0]
    code, r = run(capsys, "schedule", "run-due", "--now", later)
    assert r["ran"] == [] and len(fx.calls) == 1
    assert L.rows()[0]["note"].startswith(f"schedule#{qid}")


def test_schedule_cancel_and_failure(capsys, tmp_path, monkeypatch):
    p = write(tmp_path, {"requester": "user", "channels": ["x"], "text": "hi"})
    _, a = run(capsys, "schedule", "add", p, "--at", future(), "--approved-by", "me")
    _, b = run(capsys, "schedule", "add", p, "--at", future(40), "--approved-by", "me")
    assert run(capsys, "schedule", "cancel", str(a["scheduled"]["id"]))[0] == 0
    assert run(capsys, "schedule", "cancel", str(a["scheduled"]["id"]))[0] == 2

    class Boom:
        def publish_thread(self, *a):
            raise RuntimeError("401")
    monkeypatch.setattr(X, "XClient", Boom)
    code, r = run(capsys, "schedule", "run-due", "--now", (datetime.now() + timedelta(hours=2)).isoformat())
    assert code == 2 and [x["status"] for x in r["ran"]] == ["failed"]
    _, lst = run(capsys, "schedule", "list", "--status", "failed")
    assert lst["items"][0]["id"] == b["scheduled"]["id"]


def test_cost_summary(capsys):
    L.add(channel="x", status="published", post_id="1,2", est_cost_usd=0.215)
    L.add(channel="x", status="deleted", post_id="1", est_cost_usd=0.01)
    L.add(channel="naver", status="bundle_ready", est_cost_usd=0)
    L.add(ts="2020-01-01T00:00:00+0900", channel="x", status="published", post_id="9", est_cost_usd=1)
    _, d = run(capsys, "cost", "--period", "month")
    assert d["per_channel"]["x"] == {"est_cost_usd": 0.225, "posts": 2, "deletes": 1, "bundles": 0, "errors": 0}
    assert d["posts"] == 2 and d["deletes"] == 1
    _, d = run(capsys, "cost", "--period", "all")
    assert d["total_est_cost_usd"] == 1.225
    _, d = run(capsys, "cost", "--period", "week")
    assert d["total_est_cost_usd"] == 0.225


def mk(tmp_path, name, args):
    f = tmp_path / name
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args, str(f)], check=True)
    return f


@FF
def test_media_ok_untouched(capsys, tmp_path):
    f = mk(tmp_path, "ok.mp4", ["-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=1",
                                 "-f", "lavfi", "-i", "sine=duration=1", "-c:v", "libx264", "-c:a", "aac",
                                 "-shortest"])
    P.set_many([f"out_dir={tmp_path / 'out'}"])
    code, d = run(capsys, "media", "prep", str(f), "--thumb", "0.5")
    assert code == 0 and not d["prepared"] and d["output"] == str(f)
    assert (tmp_path / "out" / "prepared" / "ok.thumb.jpg").exists()


@FF
def test_media_prep_transcode_downscale_and_validate_warns(capsys, tmp_path, monkeypatch):
    f = mk(tmp_path, "big.webm", ["-f", "lavfi", "-i", "testsrc=size=2560x1440:rate=30:duration=1",
                                  "-c:v", "libvpx", "-b:v", "200k"])
    p = write(tmp_path, {"requester": "user", "channels": ["x"], "text": "hi", "media": [str(f)]})
    _, v = run(capsys, "draft", "validate", p)
    assert any("media needs prep" in w for w in v["channels"][0]["warnings"])
    P.set_many([f"out_dir={tmp_path / 'out'}"])
    code, d = run(capsys, "media", "prep", str(f), "--channel", "x")
    assert code == 0 and d["prepared"] and d["output"].endswith("big.x.mp4")
    info = M.probe(d["output"])
    assert info["vcodec"] == "h264" and info["width"] <= 1920 and info["height"] <= 1200


@FF
def test_media_trim_long(tmp_path, monkeypatch):
    monkeypatch.setitem(M.LIMITS["x"], "video_max_s", 2)
    f = mk(tmp_path, "long.mp4", ["-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=4",
                                  "-c:v", "libx264"])
    r = M.prep(f, "x", tmp_path / "out")
    assert r["trimmed"] and M.probe(r["output"])["duration"] <= 2


def test_gif_limit(tmp_path, monkeypatch):
    g = tmp_path / "a.gif"
    g.write_bytes(b"0" * 100)
    monkeypatch.setitem(M.LIMITS["x"], "gif_max_bytes", 10)
    assert M.check(g, "x")["needs_prep"]
    monkeypatch.setitem(M.LIMITS["x"], "gif_max_bytes", 1000)
    assert not M.check(g, "x")["needs_prep"]
