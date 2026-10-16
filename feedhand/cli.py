"""feedhand CLI. Every command prints one JSON object to stdout.

Exit codes: 0 ok, 2 error, 3 needs user confirmation (no approval given / validation failed before publish).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from feedhand import __version__, log as L, post as PO, prefs as P
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
    results, failed = [], False
    for c in v["channels"]:
        ch = c["channel"]
        r = PO.resolve(post, ch)
        rec = {"requester": post.get("requester"), "approver": approver, "channel": ch, "mode": r["mode"]}
        try:
            if r["mode"] == "manual":
                b = manual.bundle(post, ch, prefs["out_dir"])
                res = {"channel": ch, "mode": "manual", "status": "bundle_ready", **b}
                L.add(**rec, status="bundle_ready", note=b["dir"], est_cost_usd=0)
            elif ch == "x":
                posts = X.XClient().publish_thread(c["planned_posts"], r["media"], r["alt"])
                res = {"channel": ch, "status": "published", "posts": posts, "url": posts[0]["url"],
                       "est_cost_usd": c["est_cost_usd"]}
                L.add(**rec, status="published", post_id=",".join(p["id"] for p in posts),
                      url=posts[0]["url"], est_cost_usd=c["est_cost_usd"])
            else:
                cli = meta.Threads() if ch == "threads" else meta.Instagram()
                d = cli.publish(r["text"], r["media"])
                res = {"channel": ch, "status": "published", **d, "est_cost_usd": 0}
                L.add(**rec, status="published", post_id=d["id"], url=d.get("url"), est_cost_usd=0)
        except Exception as e:  # noqa: BLE001
            failed = True
            res = {"channel": ch, "status": "error", "error": str(e)}
            L.add(**rec, status="error", note=str(e)[:500])
        results.append(res)
    out({"ok": not failed, "approver": approver, "results": results}, 2 if failed else 0)


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
