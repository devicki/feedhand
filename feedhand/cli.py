"""feedhand CLI. Every command prints one JSON object to stdout.

Exit codes: 0 ok, 2 error, 3 needs user confirmation (no approval given / validation failed before publish).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from feedhand import __version__, log as L, media as M, post as PO, prefs as P, schedule as S
from feedhand.channels import manual, meta, x as X

SECRETS = {"x": X.ENV, "threads": list(meta.Threads.ENV), "instagram": list(meta.Instagram.ENV)}


def out(obj, code=0):
    print(json.dumps(obj, ensure_ascii=False, indent=1))
    sys.exit(code)


def channel_status():
    res = {}
    for ch, keys in SECRETS.items():
        miss = [k for k in keys if not os.environ.get(k)]
        res[ch] = {"api_ready": not miss, "missing_secrets": miss,
                   "tested_live": False}
    for ch in sorted(PO.MANUAL_CHANNELS):
        res[ch] = {"api_ready": False, "manual_bundle": True}
    return res


def cmd_doctor(a):
    st = channel_status()
    pp = P.path()
    mode = oct(pp.stat().st_mode & 0o777) if pp.exists() else None
    ok = any(v.get("api_ready") for v in st.values())
    out({"ok": True, "version": __version__, "python": sys.version.split()[0], "channels": st,
         "any_api_channel_ready": ok, "prefs_file": str(pp), "prefs_mode": mode,
         "out_dir": os.path.expanduser(P.load()["out_dir"]),
         "note": "secret values are never printed"})


def cmd_channels(a):
    st = channel_status()
    out({"ok": True, "api_ready": [c for c, v in st.items() if v.get("api_ready")],
         "manual": sorted(PO.MANUAL_CHANNELS), "detail": st})


def cmd_validate(a):
    v = PO.validate(PO.load(a.post))
    out(v, 0 if v["ok"] else 2)


def _approver(post, a, prefs):
    if a.approved_by:
        return a.approved_by
    req = post.get("requester")
    if req and req != "user" and req in (prefs.get("auto_approve_bots") or []):
        return f"auto:{req}"
    return None


def cmd_publish(a):
    post = PO.load(a.post)
    v = PO.validate(post)
    if not v["ok"]:
        out({"ok": False, "stage": "validate", **v}, 2)
    prefs = P.load()
    approver = _approver(post, a, prefs)
    if not approver:
        out({"ok": False, "needs_confirmation": True,
             "message": "show the preview to the user and re-run with --approved-by <who>",
             "preview": v}, 3)
    if a.dry_run:
        out({"ok": True, "dry_run": True, "approver": approver, "preview": v})
    results, failed = publish_post(post, v, approver, prefs)
    out({"ok": not failed, "approver": approver, "results": results}, 2 if failed else 0)


def publish_post(post, v, approver, prefs, note_prefix=""):
    results, failed = [], False
    for c in v["channels"]:
        ch = c["channel"]
        r = PO.resolve(post, ch)
        rec = {"requester": post.get("requester"), "approver": approver, "channel": ch, "mode": r["mode"]}
        try:
            if r["mode"] == "manual":
                b = manual.bundle(post, ch, prefs["out_dir"])
                res = {"channel": ch, "mode": "manual", "status": "bundle_ready", **b}
                L.add(**rec, status="bundle_ready", note=note_prefix + b["dir"], est_cost_usd=0)
            elif ch == "x":
                posts = X.XClient().publish_thread(c["planned_posts"], r["media"], r["alt"])
                res = {"channel": ch, "status": "published", "posts": posts, "url": posts[0]["url"],
                       "est_cost_usd": c["est_cost_usd"]}
                L.add(**rec, status="published", post_id=",".join(p["id"] for p in posts),
                      url=posts[0]["url"], est_cost_usd=c["est_cost_usd"], note=note_prefix or None)
            else:
                cli = meta.Threads() if ch == "threads" else meta.Instagram()
                d = cli.publish(r["text"], r["media"])
                res = {"channel": ch, "status": "published", **d, "est_cost_usd": 0}
                L.add(**rec, status="published", post_id=d["id"], url=d.get("url"), est_cost_usd=0,
                      note=note_prefix or None)
        except Exception as e:  # noqa: BLE001
            failed = True
            res = {"channel": ch, "status": "error", "error": str(e)}
            L.add(**rec, status="error", note=(note_prefix + str(e))[:500])
        results.append(res)
    return results, failed


def cmd_sched_add(a):
    post = PO.load(a.post)
    v = PO.validate(post)
    if not v["ok"]:
        out({"ok": False, "stage": "validate", **v}, 2)
    approver = _approver(post, a, P.load())
    if not approver:
        out({"ok": False, "needs_confirmation": True,
             "message": "scheduling needs approval now: show the preview and time, re-run with --approved-by",
             "preview": v}, 3)
    try:
        at = S.parse_at(a.at)
    except ValueError as e:
        out({"ok": False, "error": f"bad --at: {e}"}, 2)
    if at.timestamp() <= time.time():
        out({"ok": False, "error": f"--at {at.isoformat()} is in the past"}, 2)
    item = S.add(post, at, approver)
    out({"ok": True, "scheduled": {k: item[k] for k in ("id", "due_at", "approver", "status")},
         "est_cost_usd": v["est_cost_usd"], "pending": S.pending_count()})


def cmd_sched_list(a):
    items = S.list_(a.status)
    if not a.full:
        for i in items:
            p = i.pop("post")
            i["channels"] = p.get("channels")
            i["text_head"] = (p.get("text") or "")[:60]
    out({"ok": True, "items": items, "pending": S.pending_count()})


def cmd_sched_cancel(a):
    ok = S.cancel(a.id)
    out({"ok": ok, "cancelled": a.id if ok else None,
         **({} if ok else {"error": "not found or not pending"})}, 0 if ok else 2)


def cmd_sched_run(a):
    now = S.parse_at(a.now).timestamp() if a.now else time.time()
    prefs = P.load()
    ran, any_fail = [], False
    for it in S.claim_due(now):
        post = it["post"]
        v = PO.validate(post)
        if not v["ok"]:
            res, failed = [{"status": "error", "stage": "validate", "errors": v["errors"],
                            "channels": v["channels"]}], True
        else:
            res, failed = publish_post(post, v, it["approver"], prefs, note_prefix=f"schedule#{it['id']} ")
        S.finish(it["id"], not failed, res)
        any_fail |= failed
        ran.append({"id": it["id"], "status": "failed" if failed else "done", "results": res})
    out({"ok": not any_fail, "ran": ran, "pending": S.pending_count(),
         "stop_routine": S.pending_count() == 0}, 2 if any_fail else 0)


def cmd_media_prep(a):
    if not M.available():
        out({"ok": False, "error": "ffmpeg/ffprobe not installed"}, 2)
    try:
        r = M.prep(a.file, a.channel, P.load()["out_dir"], a.thumb)
    except Exception as e:  # noqa: BLE001
        out({"ok": False, "error": str(e)}, 2)
    ok = not r.get("remaining_issues")
    out({"ok": ok, **r, "note": "limits are approximate; see README"}, 0 if ok else 2)


def cmd_media_check(a):
    out({"ok": True, **M.check(a.file, a.channel)})


def cmd_cost(a):
    out({"ok": True, **L.cost(a.period), "note": "estimates from the log, not billed amounts"})


def cmd_delete(a):
    if a.channel != "x":
        out({"ok": False, "error": "delete is implemented for x only"}, 2)
    if not a.approved_by:
        out({"ok": False, "needs_confirmation": True, "message": f"confirm deleting {a.id}"}, 3)
    try:
        ok = X.XClient().delete(a.id)
    except Exception as e:  # noqa: BLE001
        out({"ok": False, "error": str(e)}, 2)
    L.add(requester="user", approver=a.approved_by, channel="x", mode="api",
          status="deleted" if ok else "delete_failed", post_id=a.id, est_cost_usd=PO.X_COST_DELETE if ok else 0)
    out({"ok": ok, "deleted": a.id, "est_cost_usd": PO.X_COST_DELETE if ok else 0}, 0 if ok else 2)


def cmd_refresh(a):
    try:
        cli = meta.Threads() if a.channel == "threads" else meta.Instagram()
        d = cli.refresh()
    except Exception as e:  # noqa: BLE001
        out({"ok": False, "error": str(e)}, 2)
    env = cli.ENV[0]
    out({"ok": True, "expires_in": d.get("expires_in"),
         "message": f"store the new token as secret {env}; value withheld from this output unless --show"
         , **({"access_token": d.get("access_token")} if a.show else {})})


def cmd_log(a):
    out({"ok": True, "rows": L.rows(a.limit, a.channel)})


def cmd_prefs(a):
    try:
        d = P.set_many(a.set) if a.set else P.load()
    except ValueError as e:
        out({"ok": False, "error": str(e)}, 2)
    out({"ok": True, "prefs": d, "path": str(P.path())})


def build():
    p = argparse.ArgumentParser(prog="feedhand")
    p.add_argument("--version", action="version", version=__version__)
    sp = p.add_subparsers(dest="cmd", required=True)
    sp.add_parser("doctor").set_defaults(f=cmd_doctor)
    sp.add_parser("channels").set_defaults(f=cmd_channels)
    d = sp.add_parser("draft")
    dsp = d.add_subparsers(dest="sub", required=True)
    v = dsp.add_parser("validate")
    v.add_argument("post")
    v.set_defaults(f=cmd_validate)
    pb = sp.add_parser("publish")
    pb.add_argument("post")
    pb.add_argument("--approved-by")
    pb.add_argument("--dry-run", action="store_true")
    pb.set_defaults(f=cmd_publish)
    de = sp.add_parser("delete")
    de.add_argument("channel", choices=["x"])
    de.add_argument("id")
    de.add_argument("--approved-by")
    de.set_defaults(f=cmd_delete)
    rf = sp.add_parser("refresh-token")
    rf.add_argument("channel", choices=["threads", "instagram"])
    rf.add_argument("--show", action="store_true")
    rf.set_defaults(f=cmd_refresh)
    lg = sp.add_parser("log")
    lg.add_argument("--limit", type=int, default=50)
    lg.add_argument("--channel")
    lg.set_defaults(f=cmd_log)
    sc = sp.add_parser("schedule")
    ssp = sc.add_subparsers(dest="sub", required=True)
    sa = ssp.add_parser("add")
    sa.add_argument("post")
    sa.add_argument("--at", required=True, help="ISO time; no offset = box-local")
    sa.add_argument("--approved-by")
    sa.set_defaults(f=cmd_sched_add)
    sl = ssp.add_parser("list")
    sl.add_argument("--status")
    sl.add_argument("--full", action="store_true")
    sl.set_defaults(f=cmd_sched_list)
    scn = ssp.add_parser("cancel")
    scn.add_argument("id", type=int)
    scn.set_defaults(f=cmd_sched_cancel)
    sr = ssp.add_parser("run-due")
    sr.add_argument("--now", help=argparse.SUPPRESS)
    sr.set_defaults(f=cmd_sched_run)
    md = sp.add_parser("media")
    msp = md.add_subparsers(dest="sub", required=True)
    for name, fn in (("prep", cmd_media_prep), ("check", cmd_media_check)):
        m = msp.add_parser(name)
        m.add_argument("file")
        m.add_argument("--channel", choices=["x", "threads", "instagram"], default="x")
        if name == "prep":
            m.add_argument("--thumb", type=float, metavar="SECONDS")
        m.set_defaults(f=fn)
    co = sp.add_parser("cost")
    co.add_argument("--period", choices=["week", "month", "all"], default="month")
    co.set_defaults(f=cmd_cost)
    pr = sp.add_parser("prefs")
    pr.add_argument("--set", nargs="*", default=[])
    pr.set_defaults(f=cmd_prefs)
    return p


def main(argv=None):
    a = build().parse_args(argv)
    try:
        a.f(a)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        out({"ok": False, "error": str(e)}, 2)


if __name__ == "__main__":
    main()
