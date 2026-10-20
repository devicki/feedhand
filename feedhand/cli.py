"""feedhand CLI. Every command prints one JSON object to stdout.

Exit codes: 0 ok, 2 error, 3 needs user confirmation (no approval given / validation failed before publish).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from feedhand import __version__, article as A, fetch as F, log as L, media as M, post as PO, prefs as P, schedule as S
from feedhand.channels import manual, meta, x as X

X_COST_USER_READ = 0.01   # User: Read (pricing table)
X_COST_POST_READ = 0.005  # Posts: Read (pricing table)
SECRETS = {"x": X.ENV, "threads": list(meta.Threads.ENV), "instagram": list(meta.Instagram.ENV)}


def out(obj, code=0):
    print(json.dumps(obj, ensure_ascii=False, indent=1))
    sys.exit(code)


REWARDS_RISK = ("X is set to manual mode in prefs: posts made through the API may not count for X's Original "
                "Content Rewards (help.x.com excludes content 'created or posted using automated means'; whether "
                "API posts count is not defined). Use the manual bundle instead, or re-run with --force-api if "
                "the user explicitly wants the API for this one.")


def x_manual(prefs) -> bool:
    return PO.default_mode("x", prefs) == "manual"


def refuse_manual(a, prefs, what):
    """Exit 3 when X is in manual mode and --force-api was not given."""
    if x_manual(prefs) and not getattr(a, "force_api", False):
        out({"ok": False, "needs_override": "--force-api", "refused": what, "message": REWARDS_RISK}, 3)


def forced_api_channels(post, prefs) -> list[str]:
    """X channels whose post.json explicitly asks for mode api while prefs keep X manual."""
    if not x_manual(prefs):
        return []
    pcs = post.get("per_channel") or {}
    return [c for c in post.get("channels") or [] if c in ("x", "x_article")
            and (pcs.get(c) or {}).get("mode") == "api"]


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
    forced = forced_api_channels(post, prefs)
    if forced and not a.force_api:
        out({"ok": False, "needs_override": "--force-api", "channels": forced, "message": REWARDS_RISK}, 3)
    if a.dry_run:
        out({"ok": True, "dry_run": True, "approver": approver, "preview": v})
    results, failed = publish_post(post, v, approver, prefs)
    out({"ok": not failed, "approver": approver, "results": results}, 2 if failed else 0)


def publish_post(post, v, approver, prefs, note_prefix=""):
    results, failed = [], False
    for c in v["channels"]:
        ch = c["channel"]
        r = PO.resolve(post, ch, prefs)
        rec = {"requester": post.get("requester"), "approver": approver, "channel": ch, "mode": r["mode"]}
        try:
            if r["mode"] == "manual":
                b = manual.bundle(post, ch, prefs["out_dir"])
                res = {"channel": ch, "mode": "manual", "status": "bundle_ready", **b}
                L.add(**rec, status="bundle_ready", note=note_prefix + b["dir"], est_cost_usd=0)
            elif ch == "x_article":
                res = publish_article(post, r, rec, note_prefix)
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


def create_article_draft(cli, title, md, cover=None) -> dict:
    conv = A.to_content_state(md)
    if conv["errors"] or A.check_images([i["path"] for i in conv["images"]] + ([cover] if cover else [])):
        raise ValueError("article does not validate; run `feedhand draft validate`")
    cs = A.fill_media(conv, cli.upload_media)
    cm = None
    if cover:
        cm = {"media_id": cli.upload_media(cover), "media_category": X.media_category(cover)[1]}
    return cli.article_draft(title, cs, cm)


ARTICLE_COST_NOTE = "cost unknown (Articles are not in the X pricing table)"


def publish_article(post, r, rec, note_prefix=""):
    cli = X.XClient()
    d = create_article_draft(cli, r["title"], r["text"], r["cover"])
    try:
        pub = cli.article_publish(d["id"])
    except Exception as e:  # noqa: BLE001
        L.add(**rec, status="article_draft", post_id=d["id"], est_cost_usd=None,
              note=f"{note_prefix}draft created, publish failed: {e}; {ARTICLE_COST_NOTE}"[:500])
        raise RuntimeError(f"article draft {d['id']} created but publish failed: {e}; "
                           f"retry with `feedhand article publish {d['id']} --approved-by ...`") from e
    url = f"https://x.com/i/web/status/{pub['post_id']}"
    L.add(**rec, status="published", post_id=pub["post_id"], url=url, est_cost_usd=None,
          note=f"{note_prefix}article {d['id']}; {ARTICLE_COST_NOTE}")
    return {"channel": "x_article", "status": "published", "article_id": d["id"], "post_id": pub["post_id"],
            "url": url, "est_cost_usd": None, "cost_unknown": True}


def cmd_sched_add(a):
    post = PO.load(a.post)
    v = PO.validate(post)
    if not v["ok"]:
        out({"ok": False, "stage": "validate", **v}, 2)
    prefs = P.load()
    forced = forced_api_channels(post, prefs)
    if forced and not a.force_api:
        out({"ok": False, "needs_override": "--force-api", "channels": forced, "message": REWARDS_RISK}, 3)
    approver = _approver(post, a, prefs)
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
    out({"ok": True, **M.check(a.file, a.channel)})  # X limits follow prefs x_premium


def cmd_media_fetch(a):
    try:
        if a.request:
            post = json.loads(Path(a.request).read_text())
            new, res = F.localize(post, a.channel, a.id)
            outp = a.out or str(Path(a.request).with_suffix(".local.json"))
            Path(outp).write_text(json.dumps(new, ensure_ascii=False, indent=1))
            out({"ok": True, "fetched": res, "request": outp})
        if not a.specs:
            out({"ok": False, "error": "give HOST:/abs/path ... or --request FILE"}, 2)
        out({"ok": True, "fetched": F.fetch(a.specs, a.channel, a.id)})
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        out({"ok": False, "error": str(e)}, 2)


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


def cmd_x_check(a):
    try:
        u = X.XClient().me("subscription_type,verified_type")
    except Exception as e:  # noqa: BLE001
        out({"ok": False, "error": str(e)}, 2)
    L.add(requester="user", channel="x", mode="api", status="x_check", post_id=u.get("id"),
          est_cost_usd=X_COST_USER_READ, note="GET /2/users/me (User: Read, estimated)")
    sub, vt = u.get("subscription_type"), u.get("verified_type")
    known = sub is not None
    premium = sub in ("Basic", "Premium", "PremiumPlus") if known else None
    res = {"ok": True, "username": u.get("username"), "subscription_type": sub, "verified_type": vt,
           "x_premium_detected": premium, "articles_tier": sub == "PremiumPlus" if known else None,
           "est_cost_usd": X_COST_USER_READ, "applied": False}
    if not known:
        res["message"] = "X did not return subscription_type; ask the user and set x_premium by hand"
    elif a.apply:
        P.set_many([f"x_premium={'true' if premium else 'false'}", f"x_subscription_type={sub}"])
        res.update(applied=True, prefs={"x_premium": premium, "x_subscription_type": sub})
    out(res)


def cmd_edit(a):
    prefs = P.load()
    refuse_manual(a, prefs, "edit")
    if prefs.get("x_premium") is not True:
        out({"ok": False, "error": "editing via the API needs X Premium on the posting account; "
                                   "run `feedhand x-check --apply` first"}, 2)
    text = a.text
    n, limit = PO.x_weighted_len(text), PO.x_limit(prefs)
    if not text.strip() or n > limit:
        out({"ok": False, "error": f"edit text must be 1..{limit} weighted chars, got {n}"}, 2)
    edit_cost = PO.X_COST_POST_LINK if PO.links(text) else PO.X_COST_POST
    est = round(edit_cost + (0 if a.no_check else X_COST_POST_READ), 4)
    preview = {"post_id": a.id, "text": text, "weighted_chars": n, "est_cost_usd": est,
               "est_cost_note": "estimated: edit billed like Post: Create"
                                + ("" if a.no_check else f" + ${X_COST_POST_READ} read of edit_controls")}
    if not a.approved_by:
        out({"ok": False, "needs_confirmation": True, "message": f"confirm editing {a.id}", "preview": preview}, 3)
    rec = {"requester": "user", "approver": a.approved_by, "channel": "x", "mode": "api"}
    cli = X.XClient()
    spent = 0.0
    try:
        if not a.no_check:
            info = cli.get_post(a.id)
            spent = X_COST_POST_READ
            ec = info.get("edit_controls") or {}
            why = None
            if ec and not ec.get("is_edit_eligible", True):
                why = "X says this post is not edit-eligible"
            elif ec.get("edits_remaining") == 0:
                why = "no edits remaining"
            elif ec.get("editable_until") and ec["editable_until"] < time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()):
                why = f"edit window closed at {ec['editable_until']}"
            elif (info.get("attachments") or {}).get("media_keys") and not a.force:
                why = ("the post has media; an API edit sends text only and may drop it (unverified). "
                       "Re-run with --force to edit anyway")
            if why:
                L.add(**rec, status="edit_refused", post_id=a.id, est_cost_usd=spent,
                      note=f"{why}; estimated read cost")
                out({"ok": False, "error": why, "edit_controls": ec, "est_cost_usd": spent}, 2)
        d = cli.edit(a.id, text)
    except Exception as e:  # noqa: BLE001
        L.add(**rec, status="edit_failed", post_id=a.id, est_cost_usd=spent, note=str(e)[:500])
        out({"ok": False, "error": str(e), "est_cost_usd": spent}, 2)
    url = f"https://x.com/i/web/status/{d['id']}"
    total = round(spent + edit_cost, 4)
    L.add(**rec, status="edited", post_id=d["id"], url=url, est_cost_usd=total,
          note=f"edit of {a.id}; estimated: edit ${edit_cost}" + (f" + read ${spent}" if spent else ""))
    out({"ok": True, "edited": a.id, "new_post_id": d["id"], "url": url, "est_cost_usd": total,
         "edit_history_post_ids": d.get("edit_history_tweet_ids") or d.get("edit_history_post_ids")})


def _article_post(path):
    post = PO.load(path)
    if "x_article" not in (post.get("channels") or []):
        out({"ok": False, "error": "post.json must list channel x_article"}, 2)
    return post


def cmd_article_render(a):
    post = _article_post(a.post)
    r = PO.resolve(post, "x_article")
    conv = A.to_content_state(r["text"])
    out({"ok": not conv["errors"], "title": r["title"], "cover": r["cover"], **conv},
        0 if not conv["errors"] else 2)


def cmd_article_bundle(a):
    post = _article_post(a.post)
    b = manual.article_bundle(post, P.load()["out_dir"])
    L.add(requester=post.get("requester"), channel="x_article", mode="manual", status="bundle_ready",
          note=b["dir"], est_cost_usd=0)
    out({"ok": True, "status": "bundle_ready", **b})


def cmd_article_draft(a):
    prefs = P.load()
    refuse_manual(a, prefs, "article draft")
    post = _article_post(a.post)
    v = PO.validate({**post, "channels": ["x_article"]}, prefs)
    if not v["ok"]:
        out({"ok": False, "stage": "validate", **v}, 2)
    r = PO.resolve(post, "x_article", prefs)
    rec = {"requester": post.get("requester"), "channel": "x_article", "mode": "api"}
    try:
        d = create_article_draft(X.XClient(), r["title"], r["text"], r["cover"])
    except Exception as e:  # noqa: BLE001
        L.add(**rec, status="error", note=f"article draft: {e}"[:500])
        out({"ok": False, "error": str(e)}, 2)
    L.add(**rec, status="article_draft", post_id=d["id"], est_cost_usd=None, note=ARTICLE_COST_NOTE)
    out({"ok": True, "article_id": d["id"], "title": d.get("title"), "published": False,
         "next": f"feedhand article publish {d['id']} --approved-by NAME", "cost_unknown": True})


def cmd_article_publish(a):
    prefs = P.load()
    refuse_manual(a, prefs, "article publish")
    if not a.approved_by:
        out({"ok": False, "needs_confirmation": True, "message": f"confirm publishing article {a.id}"}, 3)
    rec = {"requester": "user", "approver": a.approved_by, "channel": "x_article", "mode": "api"}
    try:
        pub = X.XClient().article_publish(a.id)
    except Exception as e:  # noqa: BLE001
        L.add(**rec, status="error", post_id=a.id, note=f"article publish: {e}"[:500])
        out({"ok": False, "error": str(e)}, 2)
    url = f"https://x.com/i/web/status/{pub['post_id']}"
    L.add(**rec, status="published", post_id=pub["post_id"], url=url, est_cost_usd=None,
          note=f"article {a.id}; {ARTICLE_COST_NOTE}")
    out({"ok": True, "article_id": a.id, "post_id": pub["post_id"], "url": url, "cost_unknown": True})


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
    pb.add_argument("--force-api", action="store_true", help="allow mode api for X while prefs keep X manual")
    pb.set_defaults(f=cmd_publish)
    xc = sp.add_parser("x-check", help="read the X account's subscription_type ($0.01)")
    xc.add_argument("--apply", action="store_true", help="set x_premium / x_subscription_type from the result")
    xc.set_defaults(f=cmd_x_check)
    ed = sp.add_parser("edit", help="edit an own recent X post (X Premium)")
    ed.add_argument("id")
    ed.add_argument("--text", required=True)
    ed.add_argument("--approved-by")
    ed.add_argument("--no-check", action="store_true", help="skip the edit_controls read ($0.005)")
    ed.add_argument("--force", action="store_true", help="edit even if the post has media")
    ed.add_argument("--force-api", action="store_true")
    ed.set_defaults(f=cmd_edit)
    ar = sp.add_parser("article", help="X Articles")
    asp = ar.add_subparsers(dest="sub", required=True)
    for name, fn in (("render", cmd_article_render), ("bundle", cmd_article_bundle), ("draft", cmd_article_draft)):
        x_ = asp.add_parser(name)
        x_.add_argument("post")
        if name == "draft":
            x_.add_argument("--force-api", action="store_true")
        x_.set_defaults(f=fn)
    ap_ = asp.add_parser("publish")
    ap_.add_argument("id")
    ap_.add_argument("--approved-by")
    ap_.add_argument("--force-api", action="store_true")
    ap_.set_defaults(f=cmd_article_publish)
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
    sa.add_argument("--force-api", action="store_true")
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
    mf = msp.add_parser("fetch", help="copy server media (HOST:/abs/path) over SSH into ~/.feedhand/media/<id>/")
    mf.add_argument("specs", nargs="*", metavar="HOST:/abs/path")
    mf.add_argument("--request", help="post JSON; fetch its {host,path} media and write FILE.local.json")
    mf.add_argument("--out", help="output path for the rewritten request")
    mf.add_argument("--id", help="per-request folder name (default: timestamp)")
    mf.add_argument("--channel", choices=["x", "threads", "instagram"], default="x")
    mf.set_defaults(f=cmd_media_fetch)
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
