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
| `feedhand log [--channel x] [--limit N]` | posting history (SQLite, `~/.feedhand/history.db`) |
| `feedhand prefs [--set k=v ...]` | `~/.feedhand/prefs.json`, mode 600 |

## post.json

See [`post.example.json`](post.example.json). Top-level `text`/`media` are defaults; `per_channel.<ch>` overrides `text`, `media`, `mode` (`api`/`manual`), `thread` (X replies), `alt` (X alt text by media path), `title`/`subreddit` (manual). `requester` is `user` or a bot name. `reply_link_instead: true` moves links on X out of the main post into a self-reply.

## Channel rules checked before posting

- **X**: 280 weighted chars (URLs = 23, CJK/emoji = 2), max 4 images or 1 video, link warning (a post with a link is billed $0.20 vs $0.015).
- **Threads**: 500 chars, ≤5 links, carousel 2–20, API media must be public HTTPS URLs.
- **Instagram** (professional account): media required, caption ≤2200, ≤30 hashtags, carousel ≤10, public HTTPS media.
- **Naver Blog / Reddit**: no posting API used; a bundle (`post.txt`, numbered images, `meta.json`) is written to `~/feedhand-out/`.

## Secrets (environment only)

| Channel | Variables | Where |
|---|---|---|
| X | `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET` | console.x.com → your app → set user authentication permissions to **Read and Write** → then (re)generate the Access Token & Secret |
| Threads | `THREADS_ACCESS_TOKEN`, `THREADS_USER_ID` | Meta developer app with the Threads use case (`threads_basic`, `threads_content_publish`) |
| Instagram | `IG_ACCESS_TOKEN`, `IG_USER_ID` | Meta developer app, Instagram professional account (`instagram_content_publish`) |

## Status

- X module: live posting and deleting verified (post $0.015, delete $0.01 per the X console).
- Threads / Instagram modules: skeletons following Meta docs (container → status poll → publish); **untested live**.

## License

MIT
