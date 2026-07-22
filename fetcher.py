import csv
import re
import os
import json
import time
import tempfile
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone, timedelta
from google.oauth2 import service_account
from googleapiclient.discovery import build

# Bangkok timezone
BANGKOK_TZ = timezone(timedelta(hours=7))

# --- Config (from environment variables) ---
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "1_lHNg3BbGKdyN4SfHhymjsAHNZ2BW8e36zVw1U0e3SM")
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


# ============================================================================
# HTTP HELPERS
# ============================================================================

def urlopen_with_retry(req, timeout=15, retries=2, backoff=1.5):
    """Open a urllib Request with retries on transient failures.

    Retries on network-level errors (timeouts, connection issues) and on
    HTTP 429/5xx responses, with a short backoff between attempts. Any
    other HTTPError (e.g. 400/401/404) is raised immediately since retrying
    won't help. Returns the raw response bytes.
    """
    last_exc = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                last_exc = e
            else:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_exc = e

        if attempt < retries:
            time.sleep(backoff * (attempt + 1))

    raise last_exc


# ============================================================================
# AUTH
# ============================================================================

def get_service_account_file():
    """Get service account file path from environment variables.

    Supports two modes:
    1. GOOGLE_SERVICE_ACCOUNT_JSON - Full JSON content as env var (for cloud)
    2. GOOGLE_SERVICE_ACCOUNT_FILE - File path as env var (for local dev)
    """
    sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if sa_json:
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        tmp.write(sa_json)
        tmp.close()
        return tmp.name

    sa_file = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
    if sa_file:
        return sa_file

    raise ValueError(
        "No service account credentials found. "
        "Set GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_FILE env var."
    )


# ============================================================================
# SHEET READING
# ============================================================================

def get_sheet_metadata(sheets_service, progress_callback=None):
    """Get sheet metadata to find tab names."""
    metadata = sheets_service.spreadsheets().get(
        spreadsheetId=SPREADSHEET_ID
    ).execute()
    sheets = metadata.get("sheets", [])
    for sheet in sheets:
        props = sheet.get("properties", {})
        if progress_callback:
            progress_callback(f"  Tab: '{props.get('title', 'Unknown')}' (gid={props.get('sheetId', '?')})")
    return metadata


def read_api_keys(sheets_service, api_tab_name, progress_callback=None):
    """Get the YouTube and ScrapeCreators API keys.

    Environment variables (YOUTUBE_API_KEY, SCRAPE_CREATORS_API_KEY) take
    priority. The API tab in the Sheet (C2=YouTube, C6=ScrapeCreators) is
    only used as a fallback for whichever key isn't set via env var, and is
    skipped entirely if no API tab was found (e.g. it's been deleted).
    """
    yt_key = os.environ.get("YOUTUBE_API_KEY", "")
    sc_key = os.environ.get("SCRAPE_CREATORS_API_KEY", "")

    if (not yt_key or not sc_key) and api_tab_name:
        range_str = f"'{api_tab_name}'!C2:C6"
        result = sheets_service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=range_str
        ).execute()
        rows = result.get("values", [])

        if not yt_key:
            yt_key = rows[0][0].strip() if len(rows) > 0 and len(rows[0]) > 0 else ""
        # C3-C5 are legacy keys (omkar.cloud TikTok, RapidAPI Facebook/Instagram) — no longer used
        if not sc_key:
            sc_key = rows[4][0].strip() if len(rows) > 4 and len(rows[4]) > 0 else ""

    if progress_callback:
        progress_callback(f"  YouTube Key (Official): {'Found' if yt_key else 'NOT FOUND'}")
        progress_callback(f"  ScrapeCreators Key: {'Found' if sc_key else 'NOT FOUND'}")
    return yt_key, sc_key


def extract_channel_id(raw_id, platform):
    """Extract channel ID/handle from a URL or raw value.

    Supports:
    - TikTok: https://www.tiktok.com/@handle → handle, or just @handle → handle
    - YouTube: https://www.youtube.com/channel/UCxxxx → UCxxxx,
               https://www.youtube.com/@handle → handle,
               https://www.youtube.com/c/CustomName → CustomName
    - Facebook: https://www.facebook.com/PageName → PageName
    - Instagram: https://www.instagram.com/username → username
    - Already a plain ID: returned as-is
    """
    raw_id = raw_id.strip()
    if not raw_id:
        return raw_id

    # If not a URL, return as-is (already a plain handle/ID)
    if not raw_id.startswith("http"):
        # Remove leading @ for TikTok/Instagram
        if raw_id.startswith("@") and platform in ("tiktok", "instagram"):
            return raw_id[1:]
        return raw_id

    platform_lower = platform.lower()

    if platform_lower == "tiktok":
        # https://www.tiktok.com/@handle
        match = re.search(r'tiktok\.com/@([a-zA-Z0-9_.]+)', raw_id)
        if match:
            return match.group(1)
        # https://www.tiktok.com/@handle/live or /video/xxx
        match = re.search(r'tiktok\.com/@([a-zA-Z0-9_.]+)(?:/|$)', raw_id)
        if match:
            return match.group(1)

    elif platform_lower == "youtube":
        # https://www.youtube.com/channel/UCxxxx
        match = re.search(r'youtube\.com/channel/([a-zA-Z0-9_-]+)', raw_id)
        if match:
            return match.group(1)
        # https://www.youtube.com/@handle
        match = re.search(r'youtube\.com/@([a-zA-Z0-9_.-]+)', raw_id)
        if match:
            return match.group(1)
        # https://www.youtube.com/c/CustomName
        match = re.search(r'youtube\.com/c/([a-zA-Z0-9_.-]+)', raw_id)
        if match:
            return match.group(1)
        # https://www.youtube.com/CustomName (old format)
        match = re.search(r'youtube\.com/([a-zA-Z0-9_.-]+)', raw_id)
        if match:
            return match.group(1)

    elif platform_lower == "facebook":
        # https://www.facebook.com/PageName
        match = re.search(r'facebook\.com/([a-zA-Z0-9_.]+)', raw_id)
        if match:
            return match.group(1)

    elif platform_lower == "instagram":
        # https://www.instagram.com/username
        match = re.search(r'instagram\.com/([a-zA-Z0-9_.]+)', raw_id)
        if match:
            return match.group(1)

    # If URL but can't parse, return the raw value and let caller handle it
    return raw_id


def read_channel_list(sheets_service, channel_tab_name, progress_callback=None):
    """Read channel list from 'Channel KOLs' tab.
    A=Channel Name, B=Social Media, C=Channel ID/@handle
    Supports both plain IDs/handles and full URLs in column C.
    Skips header row (first row).
    """
    range_str = f"'{channel_tab_name}'!A1:C"
    result = sheets_service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=range_str
    ).execute()
    rows = result.get("values", [])

    channels = []
    for i, row in enumerate(rows):
        channel_name = row[0] if len(row) > 0 else ""
        platform = row[1] if len(row) > 1 else ""
        channel_id_raw = row[2] if len(row) > 2 else ""

        # Skip header row
        if i == 0 and channel_name.lower().strip() in ("channel name", "channel", "name", "ชื่อ"):
            continue

        # Skip empty rows
        if not channel_id_raw.strip() and not channel_name.strip():
            continue

        # Skip if channel_id looks like a header
        if channel_id_raw.lower().strip() in ("channel id", "id", "code"):
            continue

        platform_lower = platform.lower().strip()
        if platform_lower in ("youtube", "yt"):
            platform_key = "youtube"
        elif platform_lower in ("tiktok", "tt"):
            platform_key = "tiktok"
        elif platform_lower in ("facebook", "fb"):
            platform_key = "facebook"
        elif platform_lower in ("instagram", "ig"):
            platform_key = "instagram"
        else:
            platform_key = platform_lower

        # Extract handle/ID from URL if needed
        channel_id = extract_channel_id(channel_id_raw, platform_key)

        channels.append({
            "channel_name": channel_name.strip(),
            "platform": platform_key,
            "channel_id": channel_id.strip(),
        })

    if progress_callback:
        progress_callback(f"  Read {len(channels)} channels from '{channel_tab_name}'")
        for ch in channels:
            progress_callback(f"    {ch['channel_name']} | {ch['platform']} | {ch['channel_id']}")

    return channels


def read_search_config(sheets_service, result_tab_name, progress_callback=None):
    """Read search configuration from 'Result' tab.
    A2 = start date, C2 = end date, E2 = keywords
    Date format can be: DD/M/YYYY, DD/MM/YYYY, YYYY-MM-DD, or "15 May 2026"
    Keywords split by / or ,
    """
    range_str = f"'{result_tab_name}'!A1:G2"
    result = sheets_service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=range_str
    ).execute()
    rows = result.get("values", [])

    if len(rows) < 2:
        if progress_callback:
            progress_callback("  WARNING: Result tab has no config in row 2")
        return None, None, []

    row2 = rows[1]

    # Parse start date: A2 (single cell, e.g. "22/5/2026" or "15 May 2026")
    start_raw = row2[0] if len(row2) > 0 else ""
    # Parse end date: C2
    end_raw = row2[2] if len(row2) > 2 else ""
    # Parse keywords: E2 (single cell, e.g. "#POE2/PathofExile2")
    kw_str = row2[4] if len(row2) > 4 else ""

    # Also check if A2 is day+B2 is month (legacy format)
    b_val = row2[1] if len(row2) > 1 else ""
    d_val = row2[3] if len(row2) > 3 else ""

    # Parse dates
    start_date = parse_date_flexible(start_raw, b_val)
    end_date = parse_date_flexible(end_raw, d_val)

    # Parse keywords: split by / or ,
    keywords = []
    if kw_str.strip():
        keywords = [k.strip() for k in re.split(r'[/,]', kw_str) if k.strip()]

    if progress_callback:
        progress_callback(f"  Start date: {start_date}")
        progress_callback(f"  End date: {end_date}")
        progress_callback(f"  Keywords: {keywords}")

    return start_date, end_date, keywords


def parse_ddmmyy(date_str):
    """Parse dd/mm/yy string to datetime.date for proper comparison.
    Returns None if parsing fails.
    """
    if not date_str:
        return None
    try:
        dt = datetime.strptime(date_str, "%d/%m/%y")
        return dt.date()
    except (ValueError, TypeError):
        pass
    # Try other formats
    try:
        dt = datetime.strptime(date_str, "%d/%m/%Y")
        return dt.date()
    except (ValueError, TypeError):
        pass
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.date()
    except (ValueError, TypeError):
        pass
    return None


def parse_date_flexible(date_str, fallback_month=""):
    """Parse date from various formats.
    Supports: DD/M/YYYY, DD/MM/YYYY, YYYY-MM-DD, "15 May 2026", "15" + "May 2026"
    """
    if not date_str or not date_str.strip():
        return None

    date_str = date_str.strip()

    # Try YYYY-MM-DD first
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%d/%m/%y")
    except ValueError:
        pass

    # Try DD/M/YYYY or DD/MM/YYYY
    try:
        dt = datetime.strptime(date_str, "%d/%m/%Y")
        return dt.strftime("%d/%m/%y")
    except ValueError:
        pass

    # Try DD/M/YY
    try:
        dt = datetime.strptime(date_str, "%d/%m/%y")
        return dt.strftime("%d/%m/%y")
    except ValueError:
        pass

    # Try "15 May 2026" format
    try:
        dt = datetime.strptime(date_str, "%d %B %Y")
        return dt.strftime("%d/%m/%y")
    except ValueError:
        pass

    # Try "15" + fallback_month "May 2026"
    if fallback_month and fallback_month.strip():
        try:
            day = int(date_str)
            combined = f"{day} {fallback_month.strip()}"
            dt = datetime.strptime(combined, "%d %B %Y")
            return dt.strftime("%d/%m/%y")
        except (ValueError, AttributeError):
            pass

    return None


# ============================================================================
# YOUTUBE - Fetch all videos from a channel, then filter
# ============================================================================

def resolve_youtube_channel_id(channel_id, api_key, progress_callback=None, usage=None):
    """Resolve a YouTube channel identifier to a channel ID.
    Handles: @handle, UC... channel ID, full URL
    """
    channel_id = channel_id.strip()

    # Already a channel ID (starts with UC)
    if channel_id.startswith("UC") and len(channel_id) == 24:
        return channel_id

    # Full URL
    if "youtube.com" in channel_id:
        # Extract @handle from URL
        handle_match = re.search(r'youtube\.com/@([a-zA-Z0-9_.-]+)', channel_id)
        if handle_match:
            channel_id = "@" + handle_match.group(1)
        else:
            # Extract /channel/UC... from URL
            ch_match = re.search(r'youtube\.com/channel/(UC[a-zA-Z0-9_-]+)', channel_id)
            if ch_match:
                return ch_match.group(1)

    # @handle -> resolve via API
    if channel_id.startswith("@"):
        url = (
            f"https://www.googleapis.com/youtube/v3/search"
            f"?part=snippet&type=channel&q={urllib.parse.quote(channel_id, safe='')}"
            f"&maxResults=1&key={api_key}"
        )
        req = urllib.request.Request(url)
        try:
            data = json.loads(urlopen_with_retry(req, timeout=15).decode("utf-8"))
            if usage is not None:
                usage["yt_units"] = usage.get("yt_units", 0) + 100  # search.list costs 100 units
            items = data.get("items", [])
            if items:
                ch_id = items[0].get("snippet", {}).get("channelId", "")
                if ch_id:
                    if progress_callback:
                        progress_callback(f"    Resolved {channel_id} -> {ch_id}")
                    return ch_id
        except Exception as e:
            if progress_callback:
                progress_callback(f"    Failed to resolve {channel_id}: {e}")

    # Fallback: treat as channel ID directly
    return channel_id


def fetch_youtube_channel_videos(channel_id, api_key, start_date=None, end_date=None, keywords=None, progress_callback=None, usage=None):
    """Fetch all videos from a YouTube channel, filter by date range and keywords.

    Returns list of dicts with video data.
    """
    if keywords is None:
        keywords = []

    # Step 1: Get uploads playlist ID
    url = (
        f"https://www.googleapis.com/youtube/v3/channels"
        f"?part=contentDetails,statistics,snippet"
        f"&id={channel_id}"
        f"&key={api_key}"
    )
    req = urllib.request.Request(url)
    try:
        data = json.loads(urlopen_with_retry(req, timeout=15).decode("utf-8"))
        if usage is not None:
            usage["yt_units"] = usage.get("yt_units", 0) + 1  # channels.list costs 1 unit
    except Exception as e:
        if progress_callback:
            progress_callback(f"    Failed to get channel info for {channel_id}: {e}")
        return [], {}

    items = data.get("items", [])
    if not items:
        if progress_callback:
            progress_callback(f"    Channel not found: {channel_id}")
        return [], {}

    channel_info = items[0]
    channel_title = channel_info.get("snippet", {}).get("title", "")
    subscriber_count = channel_info.get("statistics", {}).get("subscriberCount", "0")
    uploads_playlist = channel_info.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads", "")

    if not uploads_playlist:
        if progress_callback:
            progress_callback(f"    No uploads playlist for {channel_id}")
        return [], {"channel_name": channel_title, "subscribers": subscriber_count}

    channel_meta = {"channel_name": channel_title, "subscribers": subscriber_count}

    # Step 2: Collect video IDs from uploads playlist (paginated)
    # Step 3 will fetch details in batches — we collect IDs first,
    # but stop early if we can detect videos are too old.
    # Note: playlistItems snippet.publishedAt = when added to playlist,
    #        not the video's own publish date. Still useful as approximate cutoff.
    video_ids = []
    next_page_token = ""
    page_count = 0
    reached_old = False  # flag to stop collecting

    while not reached_old:
        playlist_url = (
            f"https://www.googleapis.com/youtube/v3/playlistItems"
            f"?part=snippet&playlistId={uploads_playlist}&maxResults=50"
            f"&key={api_key}"
        )
        if next_page_token:
            playlist_url += f"&pageToken={next_page_token}"

        req = urllib.request.Request(playlist_url)
        try:
            pl_data = json.loads(urlopen_with_retry(req, timeout=15).decode("utf-8"))
            if usage is not None:
                usage["yt_units"] = usage.get("yt_units", 0) + 1  # playlistItems.list costs 1 unit
        except Exception as e:
            if progress_callback:
                progress_callback(f"    Error fetching playlist: {e}")
            break

        for item in pl_data.get("items", []):
            vid_id = item.get("snippet", {}).get("resourceId", {}).get("videoId", "")
            if vid_id:
                video_ids.append(vid_id)
                # Use playlist item date as approximate cutoff
                # If the item was added before start_date, the video is likely too old
                if start_date:
                    item_date_str = item.get("snippet", {}).get("publishedAt", "")
                    if item_date_str:
                        try:
                            item_date = datetime.fromisoformat(
                                item_date_str.replace("Z", "+00:00")
                            ).astimezone(BANGKOK_TZ).strftime("%d/%m/%y")
                            if item_date < str(start_date):
                                reached_old = True
                                break
                        except (ValueError, TypeError):
                            pass

        next_page_token = pl_data.get("nextPageToken", "")
        page_count += 1
        if not next_page_token or page_count >= 20:  # Limit to ~1000 videos
            break

    if progress_callback:
        progress_callback(f"    Found {len(video_ids)} videos in channel")

    # Step 3: Fetch video details in batches, filter by date & keywords
    matched_videos = []
    batch_size = 50
    all_too_old = False  # stop fetching details if entire batch is before start_date

    for i in range(0, len(video_ids), batch_size):
        if all_too_old:
            break

        batch = video_ids[i:i + batch_size]
        ids_str = ",".join(batch)

        vid_url = (
            f"https://www.googleapis.com/youtube/v3/videos"
            f"?part=snippet,statistics,liveStreamingDetails,contentDetails"
            f"&id={ids_str}"
            f"&key={api_key}"
        )

        req = urllib.request.Request(vid_url)
        try:
            vid_data = json.loads(urlopen_with_retry(req, timeout=30).decode("utf-8"))
            if usage is not None:
                usage["yt_units"] = usage.get("yt_units", 0) + 1  # videos.list costs 1 unit
        except Exception as e:
            if progress_callback:
                progress_callback(f"    Error fetching video batch: {e}")
            continue

        batch_old_count = 0
        for item in vid_data.get("items", []):
            vid_id = item["id"]
            snippet = item.get("snippet", {})
            stats = item.get("statistics", {})
            live_details = item.get("liveStreamingDetails", None)
            content_details = item.get("contentDetails", {})
            duration = content_details.get("duration", "")

            # Determine content type
            if live_details:
                content_type = "Live"
            else:
                dur_seconds = 0
                dur_match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration)
                if dur_match:
                    h = int(dur_match.group(1) or 0)
                    m = int(dur_match.group(2) or 0)
                    s = int(dur_match.group(3) or 0)
                    dur_seconds = h * 3600 + m * 60 + s
                # The Data API doesn't expose an explicit "is this a Short" flag,
                # so duration is the best available signal. YouTube Shorts can be
                # up to 3 minutes (180s) as of the current platform rules.
                if dur_seconds > 0 and dur_seconds <= 180:
                    content_type = "Short"
                else:
                    content_type = "VOD"

            published_at = snippet.get("publishedAt", "")
            title = snippet.get("title", "")
            published_date = format_published_date(published_at)

            # Date filter (proper datetime comparison, not string)
            published_dt = parse_ddmmyy(published_date)
            start_dt = parse_ddmmyy(start_date) if start_date else None
            end_dt = parse_ddmmyy(end_date) if end_date else None
            if start_dt and published_dt and published_dt < start_dt:
                batch_old_count += 1
                continue
            if end_dt and published_dt and published_dt > end_dt:
                continue

            # Keyword filter (case-insensitive, match any keyword in title only)
            if keywords and not matches_keywords(title, keywords):
                continue

            duration_formatted = format_duration(duration)

            # For live streams: if duration is "Live" (P0D), calculate from actualStartTime
            if duration_formatted == "Live" and live_details:
                actual_start = live_details.get("actualStartTime", "")
                actual_end = live_details.get("actualEndTime", "")
                if actual_start and actual_end:
                    # Stream ended but duration still P0D — calculate from times
                    try:
                        start_dt = datetime.fromisoformat(actual_start.replace("Z", "+00:00"))
                        end_dt = datetime.fromisoformat(actual_end.replace("Z", "+00:00"))
                        delta_min = int((end_dt - start_dt).total_seconds() / 60) + 1
                        duration_formatted = str(delta_min)
                    except (ValueError, TypeError):
                        pass
                elif actual_start:
                    # Stream still live — calculate from start to now
                    try:
                        start_dt = datetime.fromisoformat(actual_start.replace("Z", "+00:00"))
                        now_dt = datetime.now(timezone.utc)
                        delta_min = int((now_dt - start_dt).total_seconds() / 60) + 1
                        duration_formatted = f"{delta_min}"
                    except (ValueError, TypeError):
                        pass

            video_link = f"https://www.youtube.com/watch?v={vid_id}"
            # Override link for shorts
            if content_type == "Short":
                video_link = f"https://www.youtube.com/shorts/{vid_id}"

            matched_videos.append({
                "channel_name": channel_title,
                "subscribers": subscriber_count,
                "platform": "youtube",
                "link": video_link,
                "content_type": content_type,
                "published_date": published_date,
                "views": stats.get("viewCount", "0"),
                "likes": stats.get("likeCount", "0"),
                "comments": stats.get("commentCount", "0"),
                "shares": "",
                "saves": "",
                "duration": duration_formatted,
                "caption": title,
            })

        # If ALL videos in this batch are before start_date, stop fetching more
        if start_date and batch_old_count == len(vid_data.get("items", [])) and batch_old_count > 0:
            all_too_old = True
            if progress_callback:
                progress_callback(f"    All videos in batch are older than {start_date}, stopping early")

        if progress_callback:
            progress_callback(f"    Processed batch {i // batch_size + 1}")

    if progress_callback:
        progress_callback(f"    Matched {len(matched_videos)} videos from {channel_title}")

    return matched_videos, channel_meta


# ============================================================================
# TIKTOK - Fetch all videos from a user, then filter
# ============================================================================

# --- ScrapeCreators API ---
SC_API_BASE = "https://api.scrapecreators.com"


# ============================================================================
# TIKTOK - ScrapeCreators API
# ============================================================================

def fetch_tiktok_channel_videos(username, api_key, start_date=None, end_date=None, keywords=None, progress_callback=None):
    """Fetch videos from a TikTok user via ScrapeCreators API, filter by date and keywords.

    Uses ScrapeCreators API:
    - User Profile: GET /v1/tiktok/profile?handle=username
    - User Videos:  GET /v3/tiktok/profile/videos?handle=username&max_cursor=xxx

    API key passed via "x-api-key" header.

    Returns list of dicts with video data.
    """
    if keywords is None:
        keywords = []

    # Extract username from URL or plain handle
    username = username.strip()
    if "tiktok.com/@" in username:
        match = re.search(r'tiktok\.com/@([^\s/?&#]+)', username)
        if match:
            username = match.group(1)
    username = username.lstrip("@")

    sc_headers = {"x-api-key": api_key}

    # Get user info for subscriber count
    subscriber_count = "0"
    channel_name = username
    profile_url = f"{SC_API_BASE}/v1/tiktok/profile?handle={urllib.parse.quote(username, safe='')}"
    req = urllib.request.Request(profile_url, headers=sc_headers, method="GET")
    try:
        data = json.loads(urlopen_with_retry(req, timeout=15).decode("utf-8"))
        user_data = data.get("user", {})
        user_stats = data.get("stats", {})
        subscriber_count = str(user_stats.get("followerCount", "0"))
        channel_name = user_data.get("nickname", user_data.get("uniqueId", username))
        if progress_callback:
            progress_callback(f"    TikTok user: @{username} ({channel_name}), followers: {subscriber_count}")
    except Exception as e:
        if progress_callback:
            progress_callback(f"    Failed to get TikTok user info for @{username}: {e}")

    time.sleep(0.5)

    # Get user's videos with pagination (cursor-based)
    matched_videos = []
    max_cursor = ""
    page_num = 0
    max_pages = 3  # limit pages to conserve credits

    while page_num < max_pages:
        params = f"handle={urllib.parse.quote(username, safe='')}"
        if max_cursor:
            params += f"&max_cursor={max_cursor}"
        videos_url = f"{SC_API_BASE}/v3/tiktok/profile/videos?{params}"
        req = urllib.request.Request(videos_url, headers=sc_headers, method="GET")

        try:
            data = json.loads(urlopen_with_retry(req, timeout=30).decode("utf-8"))
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else ""
            if progress_callback:
                progress_callback(f"    TikTok HTTP Error {e.code}: {error_body[:200]}")
            break
        except Exception as e:
            if progress_callback:
                progress_callback(f"    TikTok Error fetching videos for @{username}: {e}")
            break

        videos = data.get("aweme_list", [])
        if not isinstance(videos, list) or not videos:
            break

        if progress_callback:
            progress_callback(f"    Page {page_num + 1}: Found {len(videos)} videos from @{username}")

        all_before_start = True

        for video in videos:
            caption = video.get("desc", "")
            create_time = video.get("create_time", "")

            # Parse date (create_time is Unix timestamp)
            published_date = ""
            if create_time:
                try:
                    ts = int(create_time)
                    dt = datetime.fromtimestamp(ts, tz=BANGKOK_TZ)
                    published_date = dt.strftime("%d/%m/%y")
                except (ValueError, TypeError):
                    published_date = str(create_time)

            # Date filter
            published_dt = parse_ddmmyy(published_date)
            start_dt = parse_ddmmyy(start_date) if start_date else None
            end_dt = parse_ddmmyy(end_date) if end_date else None
            if start_dt and published_dt and published_dt < start_dt:
                continue
            if end_dt and published_dt and published_dt > end_dt:
                all_before_start = False
                continue

            all_before_start = False

            # Keyword filter
            if keywords and not matches_keywords(caption, keywords):
                continue

            stats = video.get("statistics", {})
            vid_id = video.get("aweme_id", "")

            # Duration (video.duration is in milliseconds)
            video_obj = video.get("video", {})
            duration_ms = video_obj.get("duration", "")
            if isinstance(duration_ms, (int, float)) and duration_ms:
                total_minutes = int(duration_ms) // 60000
                if int(duration_ms) % 60000 > 0:
                    total_minutes += 1
                duration_formatted = str(total_minutes)
            else:
                duration_formatted = ""

            video_link = f"https://www.tiktok.com/@{username}/video/{vid_id}" if vid_id else ""

            shares = str(stats.get("share_count", ""))
            saves = str(stats.get("collect_count", ""))

            matched_videos.append({
                "channel_name": channel_name,
                "subscribers": subscriber_count,
                "platform": "tiktok",
                "link": video_link,
                "content_type": "Short",
                "published_date": published_date,
                "views": str(stats.get("play_count", "0")),
                "likes": str(stats.get("digg_count", "0")),
                "comments": str(stats.get("comment_count", "0")),
                "shares": shares if shares and shares not in ("None", "0") else "",
                "saves": saves if saves and saves not in ("None", "0") else "",
                "duration": duration_formatted,
                "caption": caption,
            })

        # Early termination: if ALL videos in this page are before start_date
        if start_date and all_before_start:
            if progress_callback:
                progress_callback(f"    All videos in page {page_num + 1} are older than {start_date}, stopping early")
            break

        # Check for next page (ScrapeCreators: has_more=1 means more available)
        has_more = data.get("has_more", 0)
        next_cursor = str(data.get("max_cursor", ""))
        if not has_more or not next_cursor:
            break
        max_cursor = next_cursor
        page_num += 1
        time.sleep(0.5)

    if progress_callback:
        progress_callback(f"    Matched {len(matched_videos)} videos from @{username}")

    channel_meta = {"channel_name": channel_name, "subscribers": subscriber_count}
    return matched_videos, channel_meta


# ============================================================================
# FACEBOOK - ScrapeCreators API
# ============================================================================

def fetch_facebook_channel_posts(page_id_or_slug, api_key, start_date=None, end_date=None, keywords=None, progress_callback=None):
    """Fetch posts from a Facebook page via ScrapeCreators API, filter by date and keywords.

    Uses ScrapeCreators API:
    - Profile: GET /v1/facebook/profile?url=xxx
    - Posts:   GET /v1/facebook/profile/posts?pageId=xxx&cursor=xxx

    Returns list of dicts with post data.
    """
    if keywords is None:
        keywords = []

    sc_headers = {"x-api-key": api_key}

    page_slug = page_id_or_slug.strip()
    subscriber_count = "0"
    channel_name = page_slug
    page_id = ""

    # Get profile info (name, follower count, page ID)
    # Accept slug or URL
    if "facebook.com" not in page_slug:
        fb_url = f"https://www.facebook.com/{page_slug}"
    else:
        fb_url = page_slug

    profile_url = f"{SC_API_BASE}/v1/facebook/profile?url={urllib.parse.quote(fb_url, safe='')}"
    req = urllib.request.Request(profile_url, headers=sc_headers, method="GET")
    try:
        data = json.loads(urlopen_with_retry(req, timeout=15).decode("utf-8"))
        page_id = data.get("id", "")
        channel_name = data.get("name", page_slug)
        follower_count = data.get("followerCount", 0)
        if follower_count:
            subscriber_count = str(follower_count)
        if progress_callback:
            progress_callback(f"    Facebook page: {channel_name} (id={page_id}), followers: {subscriber_count}")
    except Exception as e:
        if progress_callback:
            progress_callback(f"    Failed to get Facebook profile for {page_slug}: {e}")

    time.sleep(0.5)

    # Fetch posts with cursor pagination (returns ~3 posts per request)
    matched_posts = []
    cursor = ""
    page_num = 0
    max_pages = 10  # ~30 posts max per channel (3 posts/page × 10 pages)

    while page_num < max_pages:
        params = []
        if page_id:
            params.append(f"pageId={urllib.parse.quote(page_id, safe='')}")
        else:
            params.append(f"url={urllib.parse.quote(fb_url, safe='')}")
        if cursor:
            params.append(f"cursor={urllib.parse.quote(cursor, safe='')}")
        posts_url = f"{SC_API_BASE}/v1/facebook/profile/posts?{'&'.join(params)}"
        req = urllib.request.Request(posts_url, headers=sc_headers, method="GET")

        try:
            data = json.loads(urlopen_with_retry(req, timeout=30).decode("utf-8"))
        except urllib.error.HTTPError as e:
            if progress_callback:
                progress_callback(f"    Facebook HTTP Error {e.code}")
            break
        except Exception as e:
            if progress_callback:
                progress_callback(f"    Facebook Error for {page_slug}: {e}")
            break

        posts = data.get("posts", [])
        if not isinstance(posts, list) or not posts:
            break

        if progress_callback:
            progress_callback(f"    Page {page_num + 1}: Found {len(posts)} posts from {channel_name}")

        all_before_start = True

        for post in posts:
            message = post.get("text", "")
            publish_time = post.get("publishTime", "")
            post_url = post.get("url", post.get("permalink", ""))

            # Parse date (publishTime is Unix timestamp)
            published_date = ""
            if publish_time:
                try:
                    dt = datetime.fromtimestamp(int(publish_time), tz=BANGKOK_TZ)
                    published_date = dt.strftime("%d/%m/%y")
                except (ValueError, TypeError):
                    published_date = str(publish_time)

            # Date filter
            published_dt = parse_ddmmyy(published_date)
            start_dt = parse_ddmmyy(start_date) if start_date else None
            end_dt = parse_ddmmyy(end_date) if end_date else None
            if start_dt and published_dt and published_dt < start_dt:
                continue
            if end_dt and published_dt and published_dt > end_dt:
                all_before_start = False
                continue

            all_before_start = False

            # Keyword filter
            if keywords and not matches_keywords(message, keywords):
                continue

            reaction_count = str(post.get("reactionCount", "0"))
            comment_count = str(post.get("commentCount", "0"))
            video_view_count = post.get("videoViewCount")
            video_details = post.get("videoDetails", {})

            # Determine content type and views
            has_video = bool(video_details) or video_view_count is not None
            fb_content_type = "VOD" if has_video else "Post"

            views_str = ""
            if video_view_count is not None and str(video_view_count) != "0":
                views_str = str(video_view_count)

            matched_posts.append({
                "channel_name": channel_name,
                "subscribers": subscriber_count,
                "platform": "facebook",
                "link": post_url,
                "content_type": fb_content_type,
                "published_date": published_date,
                "views": views_str,
                "likes": reaction_count,
                "comments": comment_count,
                "shares": "",      # not available from ScrapeCreators
                "saves": "",       # not available from ScrapeCreators
                "duration": "",    # not available from ScrapeCreators
                "caption": message,
            })

        # Early termination: if ALL posts in this page are before start_date
        if start_date and all_before_start:
            if progress_callback:
                progress_callback(f"    All posts in page {page_num + 1} are older than {start_date}, stopping early")
            break

        # Check for next cursor
        next_cursor = data.get("cursor", "")
        if not next_cursor:
            break
        cursor = next_cursor
        page_num += 1
        time.sleep(0.5)

    if progress_callback:
        progress_callback(f"    Matched {len(matched_posts)} posts from {channel_name}")

    channel_meta = {"channel_name": channel_name, "subscribers": subscriber_count}
    return matched_posts, channel_meta


# ============================================================================
# INSTAGRAM - ScrapeCreators API
# ============================================================================

def fetch_instagram_channel_posts(username, api_key, start_date=None, end_date=None, keywords=None, progress_callback=None):
    """Fetch posts/reels from an Instagram user via ScrapeCreators API, filter by date and keywords.

    Uses ScrapeCreators API:
    - Profile: GET /v1/instagram/profile?handle=username
    - Posts:   GET /v2/instagram/user/posts?handle=username&next_max_id=xxx
    - Reels:   GET /v1/instagram/user/reels?handle=username&max_id=xxx

    Returns list of dicts with post data.
    """
    if keywords is None:
        keywords = []

    username = username.strip().lstrip("@")

    sc_headers = {"x-api-key": api_key}

    # Get user info for subscriber count
    subscriber_count = "0"
    channel_name = username
    profile_url = f"{SC_API_BASE}/v1/instagram/profile?handle={urllib.parse.quote(username, safe='')}"
    req = urllib.request.Request(profile_url, headers=sc_headers, method="GET")
    try:
        data = json.loads(urlopen_with_retry(req, timeout=15).decode("utf-8"))
        user_data = data.get("user", data)
        subscriber_count = str(user_data.get("follower_count", user_data.get("followers", "0")))
        channel_name = user_data.get("full_name", user_data.get("username", username))
        if progress_callback:
            progress_callback(f"    IG user: @{username} ({channel_name}), followers: {subscriber_count}")
    except Exception as e:
        if progress_callback:
            progress_callback(f"    Failed to get IG user info for @{username}: {e}")

    time.sleep(0.5)

    matched_posts = []
    seen_codes = set()  # deduplicate posts vs reels

    # ---- Fetch Posts ----
    next_max_id = ""
    page_num = 0
    max_pages = 3

    while page_num < max_pages:
        params = f"handle={urllib.parse.quote(username, safe='')}"
        if next_max_id:
            params += f"&next_max_id={urllib.parse.quote(next_max_id, safe='')}"
        posts_url = f"{SC_API_BASE}/v2/instagram/user/posts?{params}"
        req = urllib.request.Request(posts_url, headers=sc_headers, method="GET")

        try:
            data = json.loads(urlopen_with_retry(req, timeout=30).decode("utf-8"))
        except Exception as e:
            if progress_callback:
                progress_callback(f"    Failed to fetch IG posts: {e}")
            break

        items = data.get("items", [])
        if not isinstance(items, list) or not items:
            break

        if progress_callback:
            progress_callback(f"    Posts page {page_num + 1}: Found {len(items)} items from @{username}")

        for item in items:
            code = item.get("code", "")
            if code in seen_codes:
                continue
            seen_codes.add(code)

            # Parse caption
            caption_obj = item.get("caption")
            if isinstance(caption_obj, dict):
                caption = caption_obj.get("text", "")
            elif isinstance(caption_obj, str):
                caption = caption_obj
            else:
                caption = ""

            taken_at = item.get("taken_at", "")
            published_date = ""
            if taken_at:
                try:
                    dt = datetime.fromtimestamp(int(taken_at), tz=BANGKOK_TZ)
                    published_date = dt.strftime("%d/%m/%y")
                except (ValueError, TypeError):
                    published_date = str(taken_at)

            # Date filter
            published_dt = parse_ddmmyy(published_date)
            start_dt = parse_ddmmyy(start_date) if start_date else None
            end_dt = parse_ddmmyy(end_date) if end_date else None
            if start_dt and published_dt and published_dt < start_dt:
                continue
            if end_dt and published_dt and published_dt > end_dt:
                continue

            # Keyword filter
            if keywords and not matches_keywords(caption, keywords):
                continue

            # Content type
            media_type = item.get("media_type", 0)
            product_type = item.get("product_type", "")
            if product_type == "clips":
                content_type = "Short"  # Reel
            elif media_type == 2:
                content_type = "VOD"
            else:
                content_type = "Post"

            # Link
            if product_type == "clips":
                link = f"https://www.instagram.com/reel/{code}/" if code else ""
            else:
                link = f"https://www.instagram.com/p/{code}/" if code else ""

            like_count = str(item.get("like_count", "0"))
            comment_count = str(item.get("comment_count", "0"))

            play_count = item.get("play_count")
            ig_play_count = item.get("ig_play_count")
            views = ""
            if play_count is not None and str(play_count) != "0":
                views = str(play_count)
            elif ig_play_count is not None and str(ig_play_count) != "0":
                views = str(ig_play_count)

            # Duration
            video_duration = item.get("video_duration")
            duration_str = ""
            if isinstance(video_duration, (int, float)) and video_duration:
                total_minutes = int(video_duration) // 60
                if int(video_duration) % 60 > 0:
                    total_minutes += 1
                duration_str = str(total_minutes)

            matched_posts.append({
                "channel_name": channel_name,
                "subscribers": subscriber_count,
                "platform": "instagram",
                "link": link,
                "content_type": content_type,
                "published_date": published_date,
                "views": views,
                "likes": like_count,
                "comments": comment_count,
                "shares": "",
                "saves": "",
                "duration": duration_str,
                "caption": caption,
            })

        # Pagination
        more = data.get("more_available", False)
        next_id = data.get("next_max_id", "")
        if not more or not next_id:
            break
        next_max_id = next_id
        page_num += 1
        time.sleep(0.5)

    # ---- Fetch Reels (separate tab) ----
    reels_max_id = ""
    page_num = 0
    max_reel_pages = 3

    while page_num < max_reel_pages:
        params = f"handle={urllib.parse.quote(username, safe='')}"
        if reels_max_id:
            params += f"&max_id={urllib.parse.quote(reels_max_id, safe='')}"
        reels_url = f"{SC_API_BASE}/v1/instagram/user/reels?{params}"
        req = urllib.request.Request(reels_url, headers=sc_headers, method="GET")

        try:
            data = json.loads(urlopen_with_retry(req, timeout=30).decode("utf-8"))
        except Exception as e:
            if progress_callback:
                progress_callback(f"    Failed to fetch IG reels: {e}")
            break

        reel_items = data.get("items", [])
        if not isinstance(reel_items, list) or not reel_items:
            break

        if progress_callback:
            progress_callback(f"    Reels page {page_num + 1}: Found {len(reel_items)} reels from @{username}")

        for reel_item in reel_items:
            media = reel_item.get("media", reel_item)
            code = media.get("code", "")
            if code in seen_codes:
                continue
            seen_codes.add(code)

            # Caption is often null on reels endpoint
            caption_obj = media.get("caption")
            if isinstance(caption_obj, dict):
                caption = caption_obj.get("text", "")
            elif isinstance(caption_obj, str):
                caption = caption_obj
            else:
                caption = ""

            taken_at = media.get("taken_at", "")
            published_date = ""
            if taken_at:
                try:
                    dt = datetime.fromtimestamp(int(taken_at), tz=BANGKOK_TZ)
                    published_date = dt.strftime("%d/%m/%y")
                except (ValueError, TypeError):
                    published_date = str(taken_at)

            # Date filter
            published_dt = parse_ddmmyy(published_date)
            start_dt = parse_ddmmyy(start_date) if start_date else None
            end_dt = parse_ddmmyy(end_date) if end_date else None
            if start_dt and published_dt and published_dt < start_dt:
                continue
            if end_dt and published_dt and published_dt > end_dt:
                continue

            # Keyword filter
            if keywords and not matches_keywords(caption, keywords):
                continue

            link = f"https://www.instagram.com/reel/{code}/" if code else ""

            like_count = str(media.get("like_count", "0"))
            comment_count = str(media.get("comment_count", "0"))

            play_count = media.get("play_count")
            ig_play_count = media.get("ig_play_count")
            views = ""
            if play_count is not None and str(play_count) != "0":
                views = str(play_count)
            elif ig_play_count is not None and str(ig_play_count) != "0":
                views = str(ig_play_count)

            # Duration
            video_duration = media.get("video_duration")
            duration_str = ""
            if isinstance(video_duration, (int, float)) and video_duration:
                total_minutes = int(video_duration) // 60
                if int(video_duration) % 60 > 0:
                    total_minutes += 1
                duration_str = str(total_minutes)

            matched_posts.append({
                "channel_name": channel_name,
                "subscribers": subscriber_count,
                "platform": "instagram",
                "link": link,
                "content_type": "Short",
                "published_date": published_date,
                "views": views,
                "likes": like_count,
                "comments": comment_count,
                "shares": "",
                "saves": "",
                "duration": duration_str,
                "caption": caption,
            })

        # Pagination
        paging = data.get("paging_info", {})
        more = paging.get("more_available", False)
        next_id = paging.get("max_id", "")
        if not more or not next_id:
            break
        reels_max_id = next_id
        page_num += 1
        time.sleep(0.5)

    if progress_callback:
        progress_callback(f"    Matched {len(matched_posts)} items from @{username}")

    channel_meta = {"channel_name": channel_name, "subscribers": subscriber_count}
    return matched_posts, channel_meta


# ============================================================================
# ACCOUNT STATUS
# ============================================================================

def get_scrapecreators_credit_balance(api_key, progress_callback=None):
    """Get remaining ScrapeCreators API credits for this account.

    Returns the credit count (int), or None if the check failed.
    """
    url = f"{SC_API_BASE}/v1/account/credit-balance"
    req = urllib.request.Request(url, headers={"x-api-key": api_key})
    try:
        data = json.loads(urlopen_with_retry(req, timeout=15).decode("utf-8"))
        return data.get("creditCount")
    except Exception as e:
        if progress_callback:
            progress_callback(f"  Failed to get ScrapeCreators credit balance: {e}")
        return None


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def matches_keywords(text, keywords):
    """Check if text contains any of the keywords as a whole word, case-insensitive.

    Uses word-boundary matching (not plain substring) so a short keyword like
    "AI" won't match inside an unrelated word like "MAIN". A leading '#' on a
    keyword is also stripped before matching, so "#POE2" matches "POE2" or
    "[POE2]" in the text too.
    """
    if not keywords:
        return True
    text_lower = (text or "").lower()
    for kw in keywords:
        for candidate in {kw.lower(), kw.lower().lstrip('#')}:
            if candidate and re.search(r'\b' + re.escape(candidate) + r'\b', text_lower):
                return True
    return False


def format_duration(iso_duration):
    """Format ISO 8601 duration (PT1H2M3S) to total minutes.
    P0D (ongoing live) returns 'Live'.
    """
    if not iso_duration:
        return ""
    if iso_duration == "P0D":
        return "Live"
    dur_match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', iso_duration)
    if not dur_match:
        return iso_duration
    h = int(dur_match.group(1) or 0)
    m = int(dur_match.group(2) or 0)
    s = int(dur_match.group(3) or 0)
    total_minutes = h * 60 + m + (1 if s > 0 else 0)
    return str(total_minutes)


def format_published_date(published):
    """Format ISO date string to dd/mm/yy."""
    if not published:
        return ""
    if len(published) == 10 and published.count("-") == 2:
        return published
    try:
        dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
        return dt.strftime("%d/%m/%y")
    except:
        return published[:10] if len(published) >= 10 else published


# ============================================================================
# WRITE RESULTS TO SHEET
# ============================================================================

def write_results_to_sheet(sheets_service, result_tab_name, results, progress_callback=None):
    """Write matched results to the 'Result' tab starting from row 4.
    A=Channel Name, B=Subscribe, C=Social Media, D=Link, E=Content Type,
    F=Date, G=Views, H=Like, I=Comment, J=Share, K=Save, L=Duration(min), M=ER%, N=Caption
    """
    # Clear existing data from row 4 onwards (columns A-N)
    clear_range = f"'{result_tab_name}'!A4:N"
    sheets_service.spreadsheets().values().clear(
        spreadsheetId=SPREADSHEET_ID,
        range=clear_range
    ).execute()

    if not results:
        if progress_callback:
            progress_callback("  No results to write")
        return

    values = []
    for r in results:
        # Column J = Share, Column K = Save
        share_val = r.get("shares", "")
        save_val = r.get("saves", "")
        share_count = 0
        try:
            share_count = int(str(share_val).replace(",", "")) if share_val and share_val not in ("None", "") else 0
        except (ValueError, TypeError):
            pass
        save_count = 0
        try:
            save_count = int(str(save_val).replace(",", "")) if save_val and save_val not in ("None", "") else 0
        except (ValueError, TypeError):
            pass

        # Calculate ER% (Engagement Rate)
        # ER = (Likes + Comments + Shares + Saves) / Views * 100
        er_pct = ""
        try:
            views_num = int(str(r.get("views", "0")).replace(",", ""))
            likes_num = int(str(r.get("likes", "0")).replace(",", ""))
            comments_num = int(str(r.get("comments", "0")).replace(",", ""))
            if views_num > 0:
                er_val = (likes_num + comments_num + share_count + save_count) / views_num * 100
                er_pct = f"{er_val:.2f}"
        except (ValueError, TypeError):
            pass

        # Store er_pct back into result dict for web UI display
        r["er_pct"] = er_pct

        values.append([
            r["channel_name"],          # A
            r["subscribers"],            # B
            r["platform"],               # C
            r["link"],                   # D
            r["content_type"],           # E
            r["published_date"],         # F
            r["views"],                  # G
            r["likes"],                  # H
            r["comments"],               # I
            share_val,                   # J = Share
            save_val,                    # K = Save
            r["duration"],               # L = Duration (minute)
            er_pct,                      # M = ER%
            r["caption"],                # N
        ])

    range_str = f"'{result_tab_name}'!A4"
    body = {"values": values}

    result = sheets_service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=range_str,
        valueInputOption="USER_ENTERED",
        body=body
    ).execute()

    if progress_callback:
        updated = result.get("updatedCells", 0)
        progress_callback(f"  Written {len(values)} rows ({updated} cells) to '{result_tab_name}'")

    return result


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def run_fetcher(progress_callback=None, progress_percent_callback=None):
    """Main entry point.

    Flow:
    1. Auth with Google Sheets
    2. Read config from 'Result' tab (date range, keywords)
    3. Read channel list from 'Channel KOLs' tab
    4. Read API keys from 'API' tab
    5. For each channel, fetch all content and filter by date + keywords
    6. Write matched results to 'Result' tab starting row 4

    Returns:
        dict with keys: success, total_rows, results, error, yt_units_used,
        sc_credits_remaining
    """
    def log(msg):
        if progress_callback:
            progress_callback(msg)

    completed_channels = 0
    total_channels_holder = {"total": 0}

    def channel_done():
        nonlocal completed_channels
        completed_channels += 1
        if progress_percent_callback:
            progress_percent_callback(completed_channels, total_channels_holder["total"])

    log("Au Date Result -> Channel Content Fetcher")
    log("=" * 55)

    # Authenticate
    log("\nAuthenticating with Google Sheets API...")
    sa_file = None
    try:
        sa_file = get_service_account_file()
        creds = service_account.Credentials.from_service_account_file(
            sa_file, scopes=SCOPES
        )
        sheets_service = build("sheets", "v4", credentials=creds)
        log("  Authenticated successfully")
    except Exception as e:
        log(f"  Authentication failed: {e}")
        return {"success": False, "total_rows": 0, "results": [], "error": str(e)}
    finally:
        # Clean up temp file if we created one (even on auth failure)
        if sa_file and os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"):
            try:
                os.unlink(sa_file)
            except:
                pass

    # Discover sheet tabs
    log("\nDiscovering sheet tabs...")
    metadata = get_sheet_metadata(sheets_service, progress_callback=log)
    sheets = metadata.get("sheets", [])

    api_tab_name = None
    channel_tab_name = None
    result_tab_name = None

    # Log all discovered tab names for debugging
    log(f"  Discovered {len(sheets)} tabs:")
    for idx, sheet in enumerate(sheets):
        title = sheet["properties"]["title"]
        log(f"    #{idx + 1} '{title}'")

    # Step 1: Identify tabs by name (exact + fuzzy matching)
    for sheet in sheets:
        title = sheet["properties"]["title"]
        title_lower = title.lower().strip()
        if title_lower in ("api",):
            api_tab_name = title
        elif title_lower in ("result", "results"):
            result_tab_name = title
        elif "kol" in title_lower or "channel" in title_lower or title_lower == "date":
            channel_tab_name = title

    # Step 2: Fallback by tab order if name matching missed any.
    # The API tab is optional (API keys can come from environment variables
    # instead), so it deliberately has no position-based fallback - guessing
    # a position would misfire once that tab is removed and shift everyone
    # else's index.
    if not result_tab_name and len(sheets) >= 2:
        result_tab_name = sheets[1]["properties"]["title"]
        log(f"  Fallback: Result tab → #{2} '{result_tab_name}'")
    if not channel_tab_name and len(sheets) >= 3:
        channel_tab_name = sheets[2]["properties"]["title"]
        log(f"  Fallback: Channel tab → #{3} '{channel_tab_name}'")

    log(f"  API tab: '{api_tab_name}'" if api_tab_name else "  API tab: not found (using environment variables for API keys)")
    log(f"  Channel KOLs tab: '{channel_tab_name}'")
    log(f"  Result tab: '{result_tab_name}'")

    if not channel_tab_name:
        log("  ERROR: Could not find Channel KOLs tab!")
        return {"success": False, "total_rows": 0, "results": [], "error": "Channel KOLs tab not found"}
    if not result_tab_name:
        log("  ERROR: Could not find Result tab!")
        return {"success": False, "total_rows": 0, "results": [], "error": "Result tab not found"}

    # Read search config from Result tab
    log("\nReading search configuration from Result tab...")
    start_date, end_date, keywords = read_search_config(sheets_service, result_tab_name, progress_callback=log)

    if not start_date or not end_date:
        log("  ERROR: Date range not configured in Result tab (A2:D2)!")
        return {"success": False, "total_rows": 0, "results": [], "error": "Date range not configured"}

    # Swap dates if start > end
    if start_date > end_date:
        start_date, end_date = end_date, start_date
        log(f"  Note: Swapped dates so start <= end: {start_date} to {end_date}")

    if not keywords:
        log("  WARNING: No keywords configured - will fetch ALL content in date range")

    # Read API keys
    log("\nReading API keys...")
    yt_api_key, sc_api_key = read_api_keys(sheets_service, api_tab_name, progress_callback=log)

    # Read channel list
    log("\nReading channel list...")
    channels = read_channel_list(sheets_service, channel_tab_name, progress_callback=log)

    if not channels:
        log("  No channels found. Exiting.")
        return {"success": False, "total_rows": 0, "results": [], "error": "No channels found"}

    total_channels_holder["total"] = len(channels)
    yt_usage = {"yt_units": 0}

    yt_channels = [ch for ch in channels if ch["platform"] == "youtube"]
    tt_channels = [ch for ch in channels if ch["platform"] == "tiktok"]
    fb_channels = [ch for ch in channels if ch["platform"] == "facebook"]
    ig_channels = [ch for ch in channels if ch["platform"] == "instagram"]

    log(f"\n  Total channels: {len(channels)}")
    log(f"    YouTube: {len(yt_channels)}")
    log(f"    TikTok: {len(tt_channels)}")
    log(f"    Facebook: {len(fb_channels)}")
    log(f"    Instagram: {len(ig_channels)}")

    all_results = []

    # ---- Fetch YouTube channels ----
    if yt_channels and yt_api_key:
        log(f"\n--- Fetching YouTube channels ({len(yt_channels)}) ---")
        for ch in yt_channels:
            try:
                log(f"\n  Channel: {ch['channel_name']} ({ch['channel_id']})")
                resolved_id = resolve_youtube_channel_id(ch["channel_id"], yt_api_key, progress_callback=log, usage=yt_usage)
                videos, _ = fetch_youtube_channel_videos(
                    resolved_id, yt_api_key,
                    start_date=start_date, end_date=end_date, keywords=keywords,
                    progress_callback=log, usage=yt_usage
                )
                all_results.extend(videos)
                time.sleep(1)
            except Exception as e:
                log(f"    ERROR processing {ch['channel_name']}: {e}")
                import traceback
                log(f"    {traceback.format_exc()[:200]}")
            finally:
                channel_done()
    elif yt_channels and not yt_api_key:
        log("\n  YouTube channels found but no API key. Skipping.")

    # ---- Fetch TikTok channels ----
    if tt_channels and sc_api_key:
        log(f"\n--- Fetching TikTok channels ({len(tt_channels)}) ---")
        for ch in tt_channels:
            try:
                log(f"\n  Channel: {ch['channel_name']} (@{ch['channel_id']})")
                videos, _ = fetch_tiktok_channel_videos(
                    ch["channel_id"], sc_api_key,
                    start_date=start_date, end_date=end_date, keywords=keywords,
                    progress_callback=log
                )
                all_results.extend(videos)
                time.sleep(0.5)
            except Exception as e:
                log(f"    ERROR processing {ch['channel_name']}: {e}")
            finally:
                channel_done()
    elif tt_channels and not sc_api_key:
        log("\n  TikTok channels found but no ScrapeCreators API key. Skipping.")

    # ---- Fetch Facebook channels ----
    if fb_channels and sc_api_key:
        log(f"\n--- Fetching Facebook pages ({len(fb_channels)}) ---")
        for ch in fb_channels:
            try:
                log(f"\n  Page: {ch['channel_name']} ({ch['channel_id']})")
                posts, _ = fetch_facebook_channel_posts(
                    ch["channel_id"], sc_api_key,
                    start_date=start_date, end_date=end_date, keywords=keywords,
                    progress_callback=log
                )
                all_results.extend(posts)
                time.sleep(0.5)
            except Exception as e:
                log(f"    ERROR processing {ch['channel_name']}: {e}")
            finally:
                channel_done()
    elif fb_channels and not sc_api_key:
        log("\n  Facebook channels found but no ScrapeCreators API key. Skipping.")

    # ---- Fetch Instagram channels ----
    if ig_channels and sc_api_key:
        log(f"\n--- Fetching Instagram accounts ({len(ig_channels)}) ---")
        for ch in ig_channels:
            try:
                log(f"\n  Account: {ch['channel_name']} (@{ch['channel_id']})")
                posts, _ = fetch_instagram_channel_posts(
                    ch["channel_id"], sc_api_key,
                    start_date=start_date, end_date=end_date, keywords=keywords,
                    progress_callback=log
                )
                all_results.extend(posts)
                time.sleep(0.5)
            except Exception as e:
                log(f"    ERROR processing {ch['channel_name']}: {e}")
            finally:
                channel_done()
    elif ig_channels and not sc_api_key:
        log("\n  Instagram channels found but no ScrapeCreators API key. Skipping.")

    # Write results
    log(f"\nWriting {len(all_results)} results to Google Sheet...")
    try:
        write_results_to_sheet(sheets_service, result_tab_name, all_results, progress_callback=log)
    except Exception as e:
        log(f"  Failed to write results: {e}")
        return {"success": False, "total_rows": len(all_results), "results": all_results, "error": str(e)}

    # Save CSV backup
    csv_path = os.path.join(tempfile.gettempdir(), "au_date_result_output.csv")
    try:
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "channel_name", "subscribers", "platform", "link", "content_type",
                "published_date", "views", "likes", "comments", "shares", "saves",
                "duration", "er_pct", "caption"
            ])
            writer.writeheader()
            writer.writerows(all_results)
        log("  CSV backup saved")
    except Exception as e:
        log(f"  Could not save CSV backup: {e}")

    # Check remaining ScrapeCreators credits
    sc_credits_remaining = None
    if sc_api_key:
        sc_credits_remaining = get_scrapecreators_credit_balance(sc_api_key, progress_callback=log)

    # Summary
    log("\n" + "=" * 55)
    log("SUMMARY")
    log("=" * 55)
    log(f"  Date range: {start_date} to {end_date}")
    log(f"  Keywords: {keywords}")
    log(f"  Total matched: {len(all_results)} items")

    platform_counts = {}
    for r in all_results:
        p = r["platform"]
        platform_counts[p] = platform_counts.get(p, 0) + 1
    for p, c in platform_counts.items():
        icon = {"youtube": "YT", "tiktok": "TT", "facebook": "FB", "instagram": "IG"}.get(p, "??")
        log(f"    {icon} {p}: {c}")

    if yt_usage["yt_units"]:
        log(f"  YouTube units used this run: ~{yt_usage['yt_units']} (Google doesn't expose remaining daily quota via API key - check Google Cloud Console)")
    if sc_credits_remaining is not None:
        log(f"  ScrapeCreators credits remaining: {sc_credits_remaining}")

    log(f"\nDone! Check the Result tab in your Google Sheet.")

    return {
        "success": True,
        "total_rows": len(all_results),
        "results": all_results,
        "error": None,
        "yt_units_used": yt_usage["yt_units"],
        "sc_credits_remaining": sc_credits_remaining,
    }
