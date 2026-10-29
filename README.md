# Au Result Date

A Flask web app that fetches YouTube, TikTok, Facebook, Instagram, and Kick
videos/posts/clips for a list of channels defined in a Google Sheet, filters
them by date range and keywords, and writes the results back out as a CSV.
Runs can be triggered from a Discord bot panel or from the web UI.

## How it works

- Channel list and search config (date range, keywords) are read from a
  Google Sheet via the Sheets API (see `fetcher.py`). API keys are read
  from environment variables first; the Sheet's API tab is only used as a
  fallback and is entirely optional (safe to hide or delete it, e.g. to
  keep the keys out of a Sheet that gets shared around).
- A row in "Channel KOLs" can also be a direct link to one specific video/
  post (e.g. a `youtube.com/watch?v=...`, `youtube.com/shorts/...`, or
  `tiktok.com/@user/video/...` URL) instead of a channel/profile link -
  detected automatically from the URL shape (see `detect_content_link` in
  `fetcher.py`), no extra column needed. That exact item is fetched and
  included in the results regardless of the configured date range or
  keywords. Facebook post/video/reel links of any shape (`/PageName/posts/...`
  including the newer `pfbid...` ones, `/videos/...`, `/reel/...`,
  `/watch/?v=...`, `/share/p|v|r/...`, `permalink.php?story_fbid=...`) are
  looked up directly with ScrapeCreators' single-post endpoint
  (`/v1/facebook/post`), which returns likes, comments, **shares** and
  views; if that lookup fails and the link is the classic
  `/PageName/posts|videos/{id}` shape, it falls back to scanning that page's
  recent posts (no shares there). A plain page link fetches the page's posts
  in the date range, then looks each matched post up once more to get its
  share count (one extra API call per matched post; set
  `FACEBOOK_FETCH_SHARES=0` to turn that off if runs get too slow). Not
  supported for Instagram post/reel links, since those don't expose the
  owning profile name in the URL. Unsupported rows are reported as failed
  instead of silently skipped or searching the wrong profile.
- TikTok share short-links (`vt.tiktok.com/...`, `vm.tiktok.com/...` - what
  you get from the app's "Share" button) are automatically resolved to
  their canonical `tiktok.com/@user/video/...` URL first (see
  `resolve_tiktok_short_url`), since the short-link itself doesn't contain
  a username or video ID for the parsing above to work with.
- **Kick only supports content links to clips** (`kick.com/channel/clips/clip_xxx`)
  - ScrapeCreators currently offers a single-clip lookup for Kick and
  nothing else, no channel-wide clip listing endpoint exists yet, so
  there's no whole-channel/date-range fetch mode for Kick the way there is
  for the other platforms. A bare `kick.com/channel` row has nothing to
  fetch and is reported as failed. `kick.com/channel/videos/{uuid}` links
  (full VODs, a different content type from a "clip") are also not
  supported - confirmed by testing against the real API, which returns an
  HTTP 500 for every VOD URL - so these are flagged and reported as failed
  up front rather than wasting an API call on a guaranteed error.
- YouTube uses the official YouTube Data API. TikTok, Facebook, Instagram,
  and Kick use the [ScrapeCreators](https://scrapecreators.com/) API.
- `run_manager.py` holds the shared run state used by both trigger points
  below, so only one fetch can run at a time. A run is only reclaimed as
  "stuck" after it stops reporting progress for 2 minutes (not just because
  it's been running a while - fetching many channels can legitimately take
  longer than that).
- All external API calls retry automatically on timeouts/network errors and
  on 429/5xx responses (see `urlopen_with_retry` in `fetcher.py`).
- **Discord bot** (`discord_bot.py`): run `/panel` in your server to post a
  message with a "เริ่มดึงข้อมูล" button (starts a run, edits the message with
  progress and the final result) and a "เปิด Google Sheet" link button. Every
  time a run is started, the panel is reposted at the bottom of the channel
  so it's always the most recent message. Finding the old panel to remove
  is done by scanning the channel's own recent history (any bot message
  with buttons) rather than in-memory tracking, so it stays correct even
  across a bot restart/redeploy instead of leaving a stray duplicate behind.
- To keep the channel from filling up with old run status messages, the bot
  automatically purges its own status messages older than 7 days once a day.
  It never deletes pinned messages or the `/panel` control panel message
  itself (identified by having buttons), and never touches messages from
  other users. Target channel is `DISCORD_CLEANUP_CHANNEL_ID` (defaults to
  the main status channel already configured). Requires the bot to have the
  "Manage Messages" permission in that channel.
- **Web UI** (`templates/fetch-date.html`): triggers a run via `/run`, which
  streams progress over Server-Sent Events; the result CSV can be downloaded
  from `/download-csv`.

## Setup

1. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
2. Set the required environment variables:
   - `GOOGLE_SERVICE_ACCOUNT_JSON` (full service account JSON, for cloud deploys)
     or `GOOGLE_SERVICE_ACCOUNT_FILE` (path to a service account key file, for local dev)
   - `SPREADSHEET_ID` (optional — defaults to the configured sheet)
   - `YOUTUBE_API_KEY` and `SCRAPE_CREATORS_API_KEY` (API keys - if unset,
     falls back to reading the Sheet's API tab, which is otherwise optional)
   - `DISCORD_BOT_TOKEN` (Discord bot token — omit to run without the Discord bot)
   - `DISCORD_GUILD_ID` (optional — your server's ID, makes the `/panel` slash
     command sync instantly instead of waiting up to an hour for a global sync)
   - `DISCORD_CLEANUP_CHANNEL_ID` (optional — channel to auto-purge old bot
     status messages from every 24h; defaults to the main status channel)
3. Run locally:
   ```
   python app.py
   ```
   The app listens on `http://localhost:5000` (or `$PORT` if set).
4. In Discord, invite the bot with the `applications.commands` and `bot`
   scopes (Send Messages, Embed Links permissions), then run `/panel` in the
   channel where you want the control panel to appear.

## Testing

Unit tests cover the pure/deterministic helpers in `fetcher.py` (date
parsing, channel ID extraction, keyword matching, duration formatting) -
no network or Google Sheets access required.

```
pip install -r requirements-dev.txt
pytest
```

## Deployment

The included `Procfile` runs the app with Gunicorn:
```
web: gunicorn app:app --timeout 600 --workers 1 --threads 4
```

## Routes

| Route | Description |
|---|---|
| `/` , `/fetch-date` | Main UI page |
| `/run` | Starts a fetch run, streams progress (SSE) |
| `/results` | Current run state as JSON |
| `/download-csv` | Download the latest result CSV |
| `/reset` | Force-reset a stuck "running" state |
| `/status` | Health check |
