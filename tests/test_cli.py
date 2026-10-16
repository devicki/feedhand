import json
import os
import stat

import pytest

from feedhand import cli, log as L, prefs as P
from feedhand.channels import x as X


def run(capsys, *argv):
    with pytest.raises(SystemExit) as e:
        cli.main(list(argv))
    return e.value.code, json.loads(capsys.readouterr().out)


def write(tmp_path, post):
    p = tmp_path / "post.json"
    p.write_text(json.dumps(post))
    return str(p)


def test_doctor_no_secret_values(capsys, monkeypatch):
    monkeypatch.setenv("X_API_KEY", "SUPERSECRET123")
    code, d = run(capsys, "doctor")
    assert code == 0
    assert "SUPERSECRET123" not in json.dumps(d)
    assert d["channels"]["x"]["missing_secrets"] == ["X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_SECRET"]


def test_channels(capsys, monkeypatch):
    for k in X.ENV:
        monkeypatch.setenv(k, "v")
    code, d = run(capsys, "channels")
    assert d["api_ready"] == ["x"] and "naver" in d["manual"]


def test_validate_exit(capsys, tmp_path):
    assert run(capsys, "draft", "validate", write(tmp_path, {"requester": "user", "channels": ["x"], "text": "hi"}))[0] == 0
    assert run(capsys, "draft", "validate", write(tmp_path, {"requester": "user", "channels": ["x"], "text": "a" * 300}))[0] == 2


def test_publish_requires_approval(capsys, tmp_path):
    p = write(tmp_path, {"requester": "user", "channels": ["x"], "text": "hi"})
    code, d = run(capsys, "publish", p)
    assert code == 3 and d["needs_confirmation"]
    assert L.rows() == []


def test_publish_x_and_manual(capsys, tmp_path, monkeypatch):
    calls = []

    class Fake:
        def publish_thread(self, texts, media, alt):
            calls.append(texts)
            return [{"id": "11", "url": "https://x.com/i/web/status/11"}]

    monkeypatch.setattr(X, "XClient", Fake)
    P.set_many([f"out_dir={tmp_path / 'out'}"])
    img = tmp_path / "a.jpg"
    img.write_bytes(b"x")
    p = write(tmp_path, {"requester": "newsbot", "channels": ["x", "naver"], "text": "hello", "media": [str(img)],
                         "per_channel": {"naver": {"title": "제목"}}})
    code, d = run(capsys, "publish", p, "--approved-by", "devicki")
    assert code == 0, d
    assert calls == [["hello"]]
    bundle = d["results"][1]
    assert bundle["status"] == "bundle_ready" and "post.txt" in bundle["files"] and "01-a.jpg" in bundle["files"]
    assert "제목" in open(os.path.join(bundle["dir"], "post.txt")).read()
    code, lg = run(capsys, "log")
    assert {r["channel"] for r in lg["rows"]} == {"x", "naver"}
    xr = [r for r in lg["rows"] if r["channel"] == "x"][0]
    assert xr["requester"] == "newsbot" and xr["approver"] == "devicki" and xr["est_cost_usd"] == 0.015


def test_auto_approve_bot(capsys, tmp_path, monkeypatch):
    P.set_many(['auto_approve_bots=["newsbot"]', f"out_dir={tmp_path}"])
    p = write(tmp_path, {"requester": "newsbot", "channels": ["naver"], "text": "x"})
    code, d = run(capsys, "publish", p)
    assert code == 0 and d["approver"] == "auto:newsbot"
    p2 = write(tmp_path, {"requester": "otherbot", "channels": ["naver"], "text": "x"})
    assert run(capsys, "publish", p2)[0] == 3


def test_publish_error_logged(capsys, tmp_path):
    p = write(tmp_path, {"requester": "user", "channels": ["x"], "text": "hi"})
    code, d = run(capsys, "publish", p, "--approved-by", "me")
    assert code == 2 and "missing secrets" in d["results"][0]["error"]
    assert L.rows()[0]["status"] == "error"


def test_dry_run(capsys, tmp_path):
    p = write(tmp_path, {"requester": "user", "channels": ["x"], "text": "hi"})
    code, d = run(capsys, "publish", p, "--approved-by", "me", "--dry-run")
    assert code == 0 and d["dry_run"] and L.rows() == []


def test_delete_needs_confirmation(capsys):
    assert run(capsys, "delete", "x", "123")[0] == 3


def test_prefs_mode(capsys):
    code, d = run(capsys, "prefs", "--set", "language=ko", "x_link_cost_warning=false")
    assert d["prefs"]["language"] == "ko" and d["prefs"]["x_link_cost_warning"] is False
    assert stat.S_IMODE(os.stat(P.path()).st_mode) == 0o600
    assert run(capsys, "prefs", "--set", "bad")[0] == 2


def test_delete_cost_logged(capsys, monkeypatch):
    class Fake:
        def delete(self, tid):
            return True

    monkeypatch.setattr(X, "XClient", Fake)
    code, d = run(capsys, "delete", "x", "123", "--approved-by", "me")
    assert code == 0 and d["est_cost_usd"] == 0.01
    r = L.rows()[0]
    assert r["status"] == "deleted" and r["est_cost_usd"] == 0.01
