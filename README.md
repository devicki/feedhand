# Feedhand

> Engine for the Feedhand Grok Bot template.

Feedhand turns one approved post request into channel-ready posts: it validates each channel's hard rules, publishes through official APIs where they exist (X; Threads and Instagram via Meta), builds copy-paste bundles for channels without a posting API (Naver Blog, Reddit), and logs every post with requester, approver and estimated cost.

Nothing is published without approval: `publish` exits `3` unless `--approved-by` is given (or the requesting bot is in `auto_approve_bots`).

## Install

```bash
git clone https://github.com/devicki/feedhand ~/feedhand
cd ~/feedhand && python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/feedhand doctor
```

## CLI

Every command prints one JSON object. Exit codes: `0` ok, `2` error, `3` needs confirmation.

| Command | What it does |
|---|---|
| `feedhand doctor` | versions, which channel secrets are missing (names only, never values), prefs file mode |
| `feedhand channels` | API-ready vs manual channels |
| `feedhand draft validate post.json` | per-channel hard-rule check + estimated X cost |
| `feedhand publish post.json --approved-by NAME [--dry-run]` | validate, then publish / bundle each channel, log results |
| `feedhand delete x TWEET_ID --approved-by NAME` | delete an X post (estimated $0.01, logged) |
| `feedhand refresh-token threads\|instagram` | extend a 60-day Meta token (new value withheld unless `--show`) |
| `feedhand schedule add post.json --at 2026-10-20T09:00 --approved-by NAME` | validate + approve now, queue for later (no offset = box-local time); local media copied to `~/.feedhand/queue/<id>/` |
| `feedhand schedule list [--status pending\|done\|failed\|cancelled] [--full]` | queued items |
| `feedhand schedule cancel ID` | cancel a pending item (removes its media copy) |
| `feedhand schedule run-due` | publish due items once each (pending → publishing → done/failed), logged with `schedule#ID`; prints `pending` and `stop_routine` |
| `feedhand media check FILE [--channel x\|threads\|instagram]` | probe a video/GIF/image against channel limits |
| `feedhand media prep FILE [--channel x] [--thumb SECONDS]` | transcode/trim/downscale only when needed → `~/feedhand-out/prepared/`; optional thumbnail JPEG |
| `feedhand cost [--period week\|month\|all]` | estimated X API spend per channel from the log + post/delete counts (week = since Monday, month = since the 1st; default month) |
| `feedhand log [--channel x] [--limit N]` | posting history (SQLite, `~/.feedhand/history.db`) |
| `feedhand prefs [--set k=v ...]` | `~/.feedhand/prefs.json`, mode 600 (e.g. `--set x_premium=true`) |

## post.json

See [`post.example.json`](post.example.json). Top-level `text`/`media` are defaults; `per_channel.<ch>` overrides `text`, `media`, `mode` (`api`/`manual`), `thread` (X replies), `alt` (X alt text by media path), `title`/`subreddit` (manual). `requester` is `user` or a bot name. `reply_link_instead: true` moves links on X out of the main post into a self-reply.

## Channel rules checked before posting

- **X**: 280 weighted chars per post (URLs = 23, CJK/emoji = 2), or 25,000 with `x_premium` (see below); max 4 images or 1 video; link warning (a post with a link is billed $0.20 vs $0.015).
- **Threads**: 500 chars, ≤5 links, carousel 2–20, API media must be public HTTPS URLs.
- **Instagram** (professional account): media required, caption ≤2200, ≤30 hashtags, carousel ≤10, public HTTPS media.
- **Naver Blog / Reddit**: no posting API used; a bundle (`post.txt`, numbered images, `meta.json`) is written to `~/feedhand-out/`.

## X Premium long posts

`feedhand prefs --set x_premium=true` (default `false`) raises the X limit from 280 to 25,000 weighted chars per post, for an X account with any X Premium tier. Set it only if the posting account really has Premium: a standard account cannot post over 280. Notes:

- A long post is one post, not a thread. Nothing is split automatically: `per_channel.x.thread` is still posted as replies if you set it, and each post in it gets the same limit.
- Counting uses the same weights as the 280 rule (URLs = 23, CJK/emoji = 2). X's [counting doc](https://docs.x.com/fundamentals/counting-characters) states those weights for the 280 limit only; the 25,000 figure is from X's Premium help page. Korean text therefore fits about 12,500 characters.
- Over 280, `draft validate` warns that timelines show about the first 280 before "Show more" and that @mentions after that point do not notify.
- Cost: X's [pricing table](https://docs.x.com/x-api/getting-started/pricing) bills `Post: Create` per request ($0.015, $0.20 with a URL) with no separate rate for long posts, so the estimate is unchanged. Not yet checked against a billed long post in the console.

## Scheduling

Approval happens when the item is added, not when it runs. Run `feedhand schedule run-due` from a routine every 10–15 minutes while `pending > 0`; stop the routine when `stop_routine` is true. Items stuck in `publishing` (runner crashed mid-post) are never retried automatically, to avoid double posts — check the channel and cancel/re-add by hand. Due items are re-validated before posting; a failure marks the item `failed` with the error in `result`.

## Media prep (approximate limits)

`draft validate` warns `media needs prep` when a local video/GIF exceeds a channel limit. `media prep` re-encodes to MP4 (H.264 + AAC, yuv420p, faststart) only in that case and reports `trimmed: true` if it cut the end — tell the user before posting a trimmed video. Limits used (platforms change these; treat as approximate):

| | X (standard account) | Threads | Instagram |
|---|---|---|---|
| Video | MP4/MOV, H.264/AAC, ≤512 MB, ≤140 s, ≤1920×1200, ≤60 fps | MP4/MOV, H.264/HEVC, ≤1 GB, ≤5 min, ≤1920 px | MP4/MOV, H.264/HEVC, ≤300 MB, ≤90 s, ≤1920 px |
| GIF | ≤15 MB | — | — |
| Image | ≤5 MB (not auto-resized) | ~8 MB | ~8 MB |

Requires `ffmpeg`/`ffprobe` on PATH.

## Secrets (environment only)

| Channel | Variables | Where |
|---|---|---|
| X | `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET` | console.x.com → your app → set user authentication permissions to **Read and Write** → then (re)generate the Access Token & Secret |
| Threads | `THREADS_ACCESS_TOKEN`, `THREADS_USER_ID` | Meta developer app with the Threads use case (`threads_basic`, `threads_content_publish`) |
| Instagram | `IG_ACCESS_TOKEN`, `IG_USER_ID` | Meta developer app, Instagram professional account (`instagram_content_publish`) |

## Status

- X module: live posting (text and video) and deleting verified (post $0.015, delete $0.01 per the X console). Long posts (`x_premium`) are validated locally but not yet posted live.
- Threads / Instagram modules: skeletons following Meta docs (container → status poll → publish); **untested live**.

## License

MIT

## Roadmap

- Post-performance check (views/likes/replies a day after posting, ~$0.005 per read, opt-in) — planned for when multiple channels and replies need managing.
