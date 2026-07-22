# Au Result Date

A Flask web app that fetches YouTube, TikTok, Facebook, and Instagram videos/posts
for a list of channels defined in a Google Sheet, filters them by date range and
keywords, and writes the results back out as a CSV. Runs can be triggered from
a Discord bot panel or from the web UI.

## How it works

- Channel list and search config (date range, keywords) are read from a
  Google Sheet via the Sheets API (see `fetcher.py`). API keys are read
  from environment variables first; the Sheet's API tab is only used as a
  fallback and is entirely optional (safe to hide or delete it, e.g. to
  keep the keys out of a Sheet that gets shared around).
- YouTube uses the official YouTube Data API. TikTok, Facebook, and Instagram
  use the [ScrapeCreators](https://scrapecreators.com/) API.
- `run_manager.py` holds the shared run state used by both trigger points
  below, so only one fetch can run at a time. A run is only reclaimed as
  "stuck" after it stops reporting progress for 2 minutes (not just because
  it's been running a while - fetching many channels can legitimately take
  longer than that).
- All external API calls retry automatically on timeouts/network errors and
  on 429/5xx responses (see `urlopen_with_retry` in `fetcher.py`).
- **Discord bot** (`discord_bot.py`): run `/panel` in your server to post a
  message with a "เริ่มดึงข้อมูล" button (starts a run, edits the message with
  progress and the final result) and a "เปิด Google Sheet" link button.
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
