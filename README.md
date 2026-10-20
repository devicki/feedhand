# Feedhand

> Engine for the Feedhand Grok Bot template.

Feedhand turns one approved post request into channel-ready posts: it validates each channel's hard rules, publishes through official APIs where they exist (X; Threads and Instagram via Meta), builds copy-paste bundles for channels without a posting API (Naver Blog, Reddit), and logs every post with requester, approver and estimated cost.

Nothing is published without approval: `publish`, `edit`, `delete` and `article publish` exit `3` unless `--approved-by` is given (`publish` also accepts a requesting bot listed in `auto_approve_bots`).

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
| `feedhand publish post.json --approved-by NAME [--dry-run] [--force-api]` | validate, then publish / bundle each channel, log results |
| `feedhand delete x TWEET_ID --approved-by NAME` | delete an X post (estimated $0.01, logged) |
| `feedhand edit POST_ID --text "..." --approved-by NAME [--no-check] [--force] [--force-api]` | edit an own recent X post (X Premium; see [Editing](#editing-x-premium)) |
| `feedhand x-check [--apply]` | read the X account's `subscription_type` / `verified_type` (User: Read, $0.01); `--apply` sets `x_premium` and `x_subscription_type` |
| `feedhand article render post.json` | Markdown → the DraftJS `content_state` that would be sent (offline, no cost) |
| `feedhand article bundle post.json` | ready-to-paste Article bundle (`title.txt`, `body.md`, `body.txt`, cover, images) |
| `feedhand article draft post.json [--force-api]` | create a private X Article draft (uploads images; cost unknown) |
| `feedhand article publish ARTICLE_ID --approved-by NAME [--force-api]` | publish a draft (cost unknown) |
| `feedhand refresh-token threads\|instagram` | extend a 60-day Meta token (new value withheld unless `--show`) |
| `feedhand schedule add post.json --at 2026-10-20T09:00 --approved-by NAME` | validate + approve now, queue for later (no offset = box-local time); local media copied to `~/.feedhand/queue/<id>/` |
| `feedhand schedule list [--status pending\|done\|failed\|cancelled] [--full]` | queued items |
| `feedhand schedule cancel ID` | cancel a pending item (removes its media copy) |
| `feedhand schedule run-due` | publish due items once each (pending → publishing → done/failed), logged with `schedule#ID`; prints `pending` and `stop_routine` |
| `feedhand media check FILE [--channel x\|threads\|instagram]` | probe a video/GIF/image against channel limits |
| `feedhand media prep FILE [--channel x] [--thumb SECONDS]` | transcode/trim/downscale only when needed → `~/feedhand-out/prepared/`; optional thumbnail JPEG |
| `feedhand media fetch HOST:/abs/path [...] [--id ID] [--channel x]` / `feedhand media fetch --request post.json [--id ID]` | copy server media over a saved SSH alias (`scp`) into `~/.feedhand/media/<id>/`; only aliases in prefs `media_hosts` (default `["srv-dev","srv-bot"]`), absolute paths, no `..`; remote size checked against channel limits before copying, local size verified; `--request` rewrites `{host,path}` media to local paths in `post.local.json` |
| `feedhand cost [--period week\|month\|all]` | estimated X API spend per channel from the log + post/delete counts (week = since Monday, month = since the 1st; default month) |
| `feedhand log [--channel x] [--limit N]` | posting history (SQLite, `~/.feedhand/history.db`) |
| `feedhand prefs [--set k=v ...]` | `~/.feedhand/prefs.json`, mode 600 (e.g. `--set x_premium=true`) |

## post.json

See [`post.example.json`](post.example.json). Top-level `text`/`media` are defaults; `per_channel.<ch>` overrides `text`, `media`, `mode` (`api`/`manual`), `thread` (X replies), `alt` (X alt text by media path), `title`/`subreddit` (manual). `requester` is `user` or a bot name. A `media` entry may also be a server file `{"host": "<ssh-alias>", "path": "/abs/path", "note": "..."}`; `draft validate` refuses those until `feedhand media fetch --request` has replaced them with local paths. `reply_link_instead: true` moves links on X out of the main post into a self-reply.

Channel `x_article` (an X Article) uses `per_channel.x_article.title`, `.text` (Markdown body) and `.cover` (optional local image); `media` is ignored there, so put images in the body as `![caption](local/path.png)`. Add `"x_article"` to `channels` to use it.

## Channel mode (api / manual)

When post.json gives no `mode`, the prefs decide: `per_channel_mode` (e.g. `{"x": "manual"}`), then `default_mode_<channel>`, then `api`. `x_article` follows X's setting unless it has its own entry. In manual mode `publish` writes a copy-paste bundle instead of calling the API.

While X is manual in prefs, every X API write is refused with exit `3` and `needs_override: --force-api`: `publish`/`schedule add` of a post.json that sets `mode: api` for `x`/`x_article`, `edit`, `article draft` and `article publish`. Reason: X's Original Content Rewards exclude content "created or posted using automated means", and X does not say whether API posts count, so they may not. Pass `--force-api` only when the user explicitly wants the API for that item.

## Channel rules checked before posting

- **X**: 280 weighted chars per post (URLs = 23, CJK/emoji = 2), or 25,000 with `x_premium` (see below); max 4 images or 1 video; link warning (a post with a link is billed $0.20 vs $0.015).
- **X Article** (`x_article`): title required, non-empty body, images/cover must be local images (≤5 MB, GIF ≤15 MB) and images on their own line, code blocks + tables ≤10,000 chars per article; warns that Articles are a Premium+ feature in the app and that the API cost is unknown.
- **Threads**: 500 chars, ≤5 links, carousel 2–20, API media must be public HTTPS URLs.
- **Instagram** (professional account): media required, caption ≤2200, ≤30 hashtags, carousel ≤10, public HTTPS media.
- **Naver Blog / Reddit**: no posting API used; a bundle (`post.txt`, numbered images, `meta.json`) is written to `~/feedhand-out/`.

## X Premium

Detect the tier with `feedhand x-check` (reads `subscription_type`: `None`, `Basic`, `Premium`, `PremiumPlus`; costs one User: Read, $0.01, logged). `--apply` sets `x_premium` (true for any paid tier) and `x_subscription_type`. If X omits the field nothing is changed; ask the user and set `x_premium` by hand.

What `x_premium` changes in Feedhand: long posts (below), post video up to 125 min / 16 GB instead of 20 min / 8 GB (see [Media prep](#media-prep-approximate-limits)), and `edit` is allowed. Premium does not change X API prices or rate limits ([pricing](https://docs.x.com/x-api/getting-started/pricing), [rate limits](https://docs.x.com/x-api/fundamentals/rate-limits)).

### Long posts

`feedhand prefs --set x_premium=true` (default `false`) raises the X limit from 280 to 25,000 weighted chars per post, for an X account with any X Premium tier. Set it only if the posting account really has Premium: a standard account cannot post over 280. Notes:

- A long post is one post, not a thread. Nothing is split automatically: `per_channel.x.thread` is still posted as replies if you set it, and each post in it gets the same limit.
- Counting uses the same weights as the 280 rule (URLs = 23, CJK/emoji = 2). X's [counting doc](https://docs.x.com/fundamentals/counting-characters) states those weights for the 280 limit only; the 25,000 figure is from X's Premium help page. Korean text therefore fits about 12,500 characters.
- Over 280, `draft validate` warns that timelines show about the first 280 before "Show more" and that @mentions after that point do not notify.
- Cost: X's [pricing table](https://docs.x.com/x-api/getting-started/pricing) bills `Post: Create` per request ($0.015, $0.20 with a URL) with no separate rate for long posts, so the estimate is unchanged. Not yet checked against a billed long post in the console.

## Editing (X Premium)

`feedhand edit POST_ID --text "new text" --approved-by NAME` sends `POST /2/tweets` with `edit_options.previous_post_id` ([changelog, Oct 2025](https://docs.x.com/changelog)). X requires Premium on the posting account, your own post, and a recent post; each edit gets a **new post id** (logged). By default Feedhand first reads `edit_controls` (`is_edit_eligible`, `editable_until`, `edits_remaining`) and stops clearly if the post cannot be edited; `--no-check` skips that read. If the post has media it stops unless `--force`, because the edit sends text only and might drop the media (unverified). Polls, replies to others, reposts and community posts are not editable.

Cost (estimated, marked so in the log): the edit is assumed to bill like Post: Create ($0.015, or $0.20 if the new text has a link) plus $0.005 for the `edit_controls` read.

Unverified: the edit window. X's changelog says posts created "within the last hour"; the [edit-posts page](https://docs.x.com/x-api/fundamentals/edit-posts) says 30 minutes and up to 5 edits. Trust `edit_controls`. Also unverified: the edit price, and whether media survives a text-only edit.

## X Articles

Write the body in Markdown; Feedhand converts it to the DraftJS `content_state` that `POST /2/articles/draft` expects (schema from [`openapi.json`](https://api.x.com/2/openapi.json), [Articles intro](https://docs.x.com/x-api/articles/introduction)):

| Markdown | Article |
|---|---|
| `#`, `##`, `###` (deeper → `###`) | header-one / two / three |
| paragraphs (single newlines kept) | unstyled |
| `- item`, `1. item`, `> quote` | unordered / ordered list item, blockquote |
| `**b**`, `*i*`, `~~s~~` | bold, italic, strikethrough |
| `[text](url)`, bare `https://` URLs | link entities |
| `![caption](local.png "optional caption")` on its own line | uploaded image (atomic block) |
| a post URL (`https://x.com/u/status/ID`) on its own line | embedded post |
| `---` | divider |
| ```` ``` ```` fenced code, `|` tables | markdown entity (≤10,000 per article) |

Inline code, LaTeX, emoji entities and mention/hashtag metadata are not generated (plain text is kept). Offsets are UTF-16 units, as DraftJS uses (inferred).

Flow: `feedhand article render` (offline preview) → `feedhand article draft` (uploads images and cover, creates a private draft) → `feedhand article publish ID --approved-by NAME`. `feedhand publish post.json --approved-by NAME` with `x_article` does draft + publish in one go; if publishing fails after the draft exists, the draft id is logged and returned. In manual mode (or with `feedhand article bundle`) you get `title.txt`, `body.md`, `body.txt` (plain text for the composer), the cover and numbered images, to paste into x.com → Articles by hand.

Unverified: Article pricing (not in the pricing table, logged with unknown cost and counted under `unknown_cost_items` in `feedhand cost`); which tier the API requires (the X app lists Articles under Premium+); scheduled items do not copy images referenced in an article body, so keep those files in place until it runs.

## Original Content Rewards

X's Original Content Rewards exclude content "created or posted using automated means" and do not say whether API posts count. Treat API posts as possibly ineligible; that is why the manual mode guard exists.

## Scheduling

Approval happens when the item is added, not when it runs. Run `feedhand schedule run-due` from a routine every 10–15 minutes while `pending > 0`; stop the routine when `stop_routine` is true. Items stuck in `publishing` (runner crashed mid-post) are never retried automatically, to avoid double posts — check the channel and cancel/re-add by hand. Due items are re-validated before posting; a failure marks the item `failed` with the error in `result`.

## Media prep (approximate limits)

`draft validate` warns `media needs prep` when a local video/GIF exceeds a channel limit (for X, the limit follows `x_premium`). `media prep` re-encodes to MP4 (H.264 + AAC, yuv420p, faststart) only in that case and reports `trimmed: true` if it cut the end — tell the user before posting a trimmed video. Limits used (platforms change these; treat as approximate):

| | X | Threads | Instagram |
|---|---|---|---|
| Video | MP4/MOV, H.264/AAC, ≤20 min and ≤8 GB (with `x_premium`: ≤125 min, ≤16 GB), ≤1920×1200, ≤60 fps | MP4/MOV, H.264/HEVC, ≤1 GB, ≤5 min, ≤1920 px | MP4/MOV, H.264/HEVC, ≤300 MB, ≤90 s, ≤1920 px |
| GIF | ≤15 MB | — | — |
| Image | ≤5 MB (not auto-resized) | ~8 MB | ~8 MB |

Requires `ffmpeg`/`ffprobe` on PATH.

X video limits are from [docs.x.com media](https://docs.x.com/x-api/media/introduction) (Sep 2026): they follow the posting account's Premium status, not the API plan, and are checked again when the post is created. If X refuses with `403 ... not allowed to post a video longer than N minutes`, Feedhand reports that cap and suggests `media prep` or `x-check --apply`. (140 s / 512 MB is X's DM-video limit.) The wait for X's video processing scales with file size and duration (5 min base, up to 4 h) instead of a fixed number of polls; chunk size grows so uploads stay within 10,000 segments.

## Secrets (environment only)

| Channel | Variables | Where |
|---|---|---|
| X | `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET` | console.x.com → your app → set user authentication permissions to **Read and Write** → then (re)generate the Access Token & Secret |
| Threads | `THREADS_ACCESS_TOKEN`, `THREADS_USER_ID` | Meta developer app with the Threads use case (`threads_basic`, `threads_content_publish`) |
| Instagram | `IG_ACCESS_TOKEN`, `IG_USER_ID` | Meta developer app, Instagram professional account (`instagram_content_publish`) |

## Status

- X module: live posting (text and video) and deleting verified (post $0.015, delete $0.01 per the X console). Long posts (`x_premium`), long video, `edit`, `x-check` and Articles are built and unit-tested against the documented API but **not yet tried live**.
- Threads / Instagram modules: skeletons following Meta docs (container → status poll → publish); **untested live**.

## License

MIT

## Roadmap

- Post-performance check (views/likes/replies a day after posting, ~$0.005 per read, opt-in) — planned for when multiple channels and replies need managing.
