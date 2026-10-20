import json
import subprocess

import pytest

from feedhand import fetch as F
from test_cli import run, write


class FakeRun:
    def __init__(self, size=1234, scp_bytes=None, stat_rc=0):
        self.size, self.scp_bytes, self.stat_rc, self.calls = size, scp_bytes, stat_rc, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if cmd[0] == "ssh":
            return subprocess.CompletedProcess(cmd, self.stat_rc, f"{self.size}\n", "no such file")
        if cmd[0] == "scp":
            n = self.size if self.scp_bytes is None else self.scp_bytes
            with open(cmd[-1], "wb") as f:
                f.write(b"\0" * n)
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(cmd)


@pytest.fixture
def fr(monkeypatch):
    f = FakeRun()
    monkeypatch.setattr(F.subprocess, "run", f)
    monkeypatch.setattr(F.shutil, "which", lambda _: None)
    return f


def test_fetch_ok(capsys, fr):
    code, d = run(capsys, "media", "fetch", "srv-dev:/tmp/a.png", "--id", "r1")
    assert code == 0
    r = d["fetched"][0]
    assert r["bytes"] == 1234 and r["local_path"].endswith("media/r1/a.png")
    assert fr.calls[0][-1] == "stat -c %s '/tmp/a.png'"


@pytest.mark.parametrize("spec", ["evil:/tmp/a.png", "srv-dev:tmp/a.png", "srv-dev:/tmp/../etc/x.png",
                                  "srv-dev:/tmp/a'b.png", "nocolon"])
def test_fetch_refused(capsys, fr, spec):
    code, d = run(capsys, "media", "fetch", spec)
    assert code == 2 and not d["ok"]
    assert not any(c[0] == "scp" for c in fr.calls)


def test_fetch_over_limit_not_copied(capsys, fr):
    fr.size = 6 * 1024**2  # > X image limit 5MB
    code, d = run(capsys, "media", "fetch", "srv-bot:/tmp/big.jpg")
    assert code == 2 and "limit" in d["error"]
    assert not any(c[0] == "scp" for c in fr.calls)


def test_fetch_premium_video_limit(capsys, fr, monkeypatch):
    fr.size = 9 * 1000**3
    assert run(capsys, "media", "fetch", "srv-dev:/tmp/v.mp4")[0] == 2
    from feedhand import prefs as P
    P.set_many(["x_premium=true"])
    assert run(capsys, "media", "fetch", "srv-dev:/tmp/v.mp4")[0] == 0


def test_fetch_size_mismatch(capsys, fr):
    fr.scp_bytes = 10
    code, d = run(capsys, "media", "fetch", "srv-dev:/tmp/a.png", "--id", "r2")
    assert code == 2 and "mismatch" in d["error"]


def test_fetch_missing_remote(capsys, fr):
    fr.stat_rc = 1
    code, d = run(capsys, "media", "fetch", "srv-dev:/tmp/none.png")
    assert code == 2 and "cannot stat" in d["error"]


def test_request_localize_and_validate(capsys, fr, tmp_path):
    post = {"requester": "user", "channels": ["x"], "text": "hi",
            "media": [{"host": "srv-dev", "path": "/tmp/a.png", "note": "shot"}]}
    p = write(tmp_path, post)
    code, d = run(capsys, "draft", "validate", p)
    assert code != 0 and "not fetched" in json.dumps(d)
    code, d = run(capsys, "media", "fetch", "--request", p, "--id", "r3")
    assert code == 0
    new = json.loads(open(d["request"]).read())
    assert new["media"] == [d["fetched"][0]["local_path"]]
    assert run(capsys, "draft", "validate", d["request"])[0] == 0
