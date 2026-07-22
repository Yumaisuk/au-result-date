# Au Result Date

A Flask web app that fetches YouTube, TikTok, Facebook, and Instagram videos/posts
for a list of channels defined in a Google Sheet, filters them by date range and
keywords, and writes the results back out as a CSV. Runs can be triggered from
a Discord bot panel or from the web UI.

## How it works

- Channel list, search config (date range, keywords), and API keys are read from
  a Google Sheet via the Sheets API (see `fetcher.py`).
- YouTube uses the official YouTube Data API. TikTok, Facebook, and Instagram
  use the [ScrapeCreators](https://scrapecreators.com/) API.
- `run_manager.py` holds the shared run state (with stale-run detection) used
  by both trigger points below, so only one fetch can run at a time.
- **Discord bot** (`discord_bot.py`): run `/panel` in your server to post a
  message with a "เริ่มดึงข้อมูล" button (starts a run, edits the message with
  progress and the final result) and a "เปิด Google Sheet" link button.
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
   - `SCRAPE_CREATORS_API_KEY` (optional fallback if the key isn't in the sheet's API tab)
   - `DISCORD_BOT_TOKEN` (Discord bot token — omit to run without the Discord bot)
   - `DISCORD_GUILD_ID` (optional — your server's ID, makes the `/panel` slash
     command sync instantly instead of waiting up to an hour for a global sync)
3. Run locally:
   ```
   python app.py
   ```
   The app listens on `http://localhost:5000` (or `$PORT` if set).
4. In Discord, invite the bot with the `applications.commands` and `bot`
   scopes (Send Messages, Embed Links permissions), then run `/panel` in the
   channel where you want the control panel to appear.

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
