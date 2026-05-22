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
    """Read API keys from the API tab.
    Expected: C2=YouTube, C3=TikTok, C4=Facebook, C5=Instagram
    """
    range_str = f"'{api_tab_name}'!A1:C10"
    result = sheets_service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=range_str
    ).execute()
    rows = result.get("values", [])

    if progress_callback:
        progress_callback(f"  API keys tab ({len(rows)} rows):")

    yt_key = ""
    tt_key = ""
    fb_key = ""
    ig_key = ""
    for row in rows:
        b_val = row[1] if len(row) > 1 else ""
        c_val = row[2] if len(row) > 2 else ""
        b_lower = b_val.lower().strip()
        if "youtube" in b_lower or "yt" in b_lower:
            yt_key = c_val.strip()
        elif "tiktok" in b_lower or "tt" in b_lower:
            tt_key = c_val.strip()
        elif "facebook" in b_lower or "fb" in b_lower:
            fb_key = c_val.strip()
        elif "instagram" in b_lower or "ig" in b_lower:
            ig_key = c_val.strip()

    if progress_callback:
        progress_callback(f"  YouTube Key: {'Found' if yt_key else 'NOT FOUND'}")
        progress_callback(f"  TikTok Key: {'Found' if tt_key else 'NOT FOUND'}")
        progress_callback(f"  Facebook Key: {'Found' if fb_key else 'NOT FOUND'}")
        progress_callback(f"  Instagram Key: {'Found' if ig_key else 'NOT FOUND'}")
    return yt_key, tt_key, fb_key, ig_key


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

def resolve_youtube_channel_id(channel_id, api_key, progress_callback=None):
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
            with urllib.request.urlopen(req, timeout=15) as response:
                data = json.loads(response.read().decode("utf-8"))
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


def fetch_youtube_channel_videos(channel_id, api_key, start_date=None, end_date=None, keywords=None, progress_callback=None):
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
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
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
            with urllib.request.urlopen(req, timeout=15) as response:
                pl_data = json.loads(response.read().decode("utf-8"))
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
            with urllib.request.urlopen(req, timeout=30) as response:
                vid_data = json.loads(response.read().decode("utf-8"))
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
                # Check if short by URL pattern (not available here) or duration
                if "/shorts/" in snippet.get("thumbnails", {}).get("default", {}).get("url", ""):
                    content_type = "Short"
                elif dur_seconds > 0 and dur_seconds <= 60:
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
            # Strip leading # from keywords so "#POE2" also matches "POE2" or "[POE2]"
            if keywords:
                title_lower = title.lower()
                # Search with original keyword AND keyword without leading #
                if not any(kw.lower().lstrip('#') in title_lower or kw.lower() in title_lower for kw in keywords):
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

def fetch_tiktok_channel_videos(username, api_key, start_date=None, end_date=None, keywords=None, progress_callback=None):
    """Fetch videos from a TikTok user via omkar.cloud API, filter by date and keywords.

    Uses tiktok-scraper.omkar.cloud API:
    - User Profile: GET /tiktok/users/profile?handle=username
    - User Videos:  GET /tiktok/users/videos?handle=username&max_results=30&page_cursor=0

    API key passed via "API-Key" header (not RapidAPI headers).

    Returns list of dicts with video data.
    """
    if keywords is None:
        keywords = []

    # Extract username from URL or plain handle
    # Supports: "https://www.tiktok.com/@username", "@username", "username"
    username = username.strip()
    if "tiktok.com/@" in username:
        # Extract handle from URL like https://www.tiktok.com/@poe2thfans
        match = re.search(r'tiktok\.com/@([^\s/?&#]+)', username)
        if match:
            username = match.group(1)
    username = username.lstrip("@")

    TT_API_BASE = "https://tiktok-scraper.omkar.cloud"
    headers = {
        "API-Key": api_key,
    }

    # Get user info for subscriber count
    subscriber_count = "0"
    channel_name = username
    user_url = f"{TT_API_BASE}/tiktok/users/profile?handle={urllib.parse.quote(username, safe='')}"
    req = urllib.request.Request(user_url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
        user_data = data.get("user", {})
        user_stats = data.get("stats", {})
        subscriber_count = str(user_stats.get("follower_count", "0"))
        channel_name = user_data.get("display_name", user_data.get("handle", username))
        if progress_callback:
            progress_callback(f"    TikTok user: @{username} ({channel_name}), followers: {subscriber_count}")
    except Exception as e:
        if progress_callback:
            progress_callback(f"    Failed to get TikTok user info for @{username}: {e}")

    time.sleep(1)

    # Get user's videos with pagination
    matched_videos = []
    page_cursor = "0"
    page_num = 0
    # Hard limit: max 2 pages (≈60 videos) per channel to conserve API quota
    # (omkar.cloud free plan = 100 requests/month; 1 profile + 2 video pages = 3 requests/channel)
    max_pages = 2

    while page_num < max_pages:
        videos_url = (
            f"{TT_API_BASE}/tiktok/users/videos"
            f"?handle={urllib.parse.quote(username, safe='')}"
            f"&max_results=30"
            f"&page_cursor={page_cursor}"
        )
        req = urllib.request.Request(videos_url, headers=headers, method="GET")

        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else ""
            if progress_callback:
                progress_callback(f"    TikTok HTTP Error {e.code}: {error_body[:200]}")
            break
        except Exception as e:
            if progress_callback:
                progress_callback(f"    TikTok Error fetching videos for @{username}: {e}")
            break

        videos = data.get("videos", [])
        if not isinstance(videos, list) or not videos:
            break

        if progress_callback:
            progress_callback(f"    Page {page_num + 1}: Found {len(videos)} videos from @{username}")

        all_before_start = True  # track if all videos in this page are before start_date

        for video in videos:
            caption = video.get("caption", "")
            created_at = video.get("created_at", "")

            # Parse date (created_at is a Unix timestamp)
            published_date = ""
            if created_at:
                try:
                    ts = int(created_at)
                    dt = datetime.fromtimestamp(ts, tz=BANGKOK_TZ)
                    published_date = dt.strftime("%d/%m/%y")
                except (ValueError, TypeError):
                    published_date = str(created_at)

            # Date filter (proper datetime comparison, not string)
            published_dt = parse_ddmmyy(published_date)
            start_dt = parse_ddmmyy(start_date) if start_date else None
            end_dt = parse_ddmmyy(end_date) if end_date else None
            if start_dt and published_dt and published_dt < start_dt:
                continue
            if end_dt and published_dt and published_dt > end_dt:
                # Video is after end_date — still in range chronologically, don't count as "too old"
                all_before_start = False
                continue

            # At least one video passed the date range check (not too old, not too new)
            all_before_start = False

            # Keyword filter (strip # so "#POE2" also matches "POE2")
            if keywords:
                caption_lower = caption.lower() if caption else ""
                if not any(kw.lower().lstrip('#') in caption_lower or kw.lower() in caption_lower for kw in keywords):
                    continue

            stats = video.get("stats", {})
            vid_id = video.get("video_id", "")
            author = video.get("author", {})

            # Duration (duration_seconds field from omkar.cloud)
            duration_seconds = video.get("duration_seconds", "")
            if isinstance(duration_seconds, (int, float)) and duration_seconds:
                total_minutes = int(duration_seconds) // 60
                if int(duration_seconds) % 60 > 0:
                    total_minutes += 1
                duration_formatted = str(total_minutes)
            else:
                duration_formatted = ""

            video_link = f"https://www.tiktok.com/@{username}/video/{vid_id}" if vid_id else ""

            shares = str(stats.get("shares", ""))
            saves = str(stats.get("saves", ""))

            matched_videos.append({
                "channel_name": channel_name,
                "subscribers": subscriber_count,
                "platform": "tiktok",
                "link": video_link,
                "content_type": "Short",
                "published_date": published_date,
                "views": str(stats.get("views", "0")),
                "likes": str(stats.get("likes", "0")),
                "comments": str(stats.get("comments", "0")),
                "shares": shares if shares and shares != "None" and shares != "0" else "",
                "saves": saves if saves and saves != "None" and saves != "0" else "",
                "duration": duration_formatted,
                "caption": caption,
            })

        # Early termination: if ALL videos in this page are before start_date, stop fetching
        if start_date and all_before_start:
            if progress_callback:
                progress_callback(f"    All videos in page {page_num + 1} are older than {start_date}, stopping early")
            break

        # Check for next page cursor (omkar.cloud uses "next_cursor" + "has_more")
        has_more = data.get("has_more", False)
        next_cursor = data.get("next_cursor", "")
        if not has_more or not next_cursor:
            break
        page_cursor = str(next_cursor)
        page_num += 1
        time.sleep(1)  # rate limit between pages

    if progress_callback:
        progress_callback(f"    Matched {len(matched_videos)} videos from @{username}")

    channel_meta = {"channel_name": channel_name, "subscribers": subscriber_count}
    return matched_videos, channel_meta


# ============================================================================
# FACEBOOK - Fetch all posts from a page, then filter
# ============================================================================

def fetch_facebook_channel_posts(page_id_or_slug, api_key, start_date=None, end_date=None, keywords=None, progress_callback=None):
    """Fetch posts from a Facebook page, filter by date and keywords.

    Returns list of dicts with post data.
    """
    if keywords is None:
        keywords = []

    FB_API_HOST = "facebook-scraper3.p.rapidapi.com"
    FB_API_BASE = f"https://{FB_API_HOST}"
    headers = {
        "x-rapidapi-host": FB_API_HOST,
        "x-rapidapi-key": api_key,
    }

    # Resolve page_id if slug/URL provided
    page_id = page_id_or_slug.strip()
    subscriber_count = "0"
    channel_name = page_id

    # If it looks like a slug (not numeric), get page_id first
    if not page_id.isdigit():
        page_url = f"https://www.facebook.com/{page_id}" if "facebook.com" not in page_id else page_id
        api_url = f"{FB_API_BASE}/page/page_id?url={urllib.parse.quote(page_url, safe='')}"
        req = urllib.request.Request(api_url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                data = json.loads(response.read().decode("utf-8"))
            resolved_id = data.get("page_id", "")
            if resolved_id:
                page_id = resolved_id
                if progress_callback:
                    progress_callback(f"    Resolved '{page_id_or_slug}' -> page_id={page_id}")
        except Exception as e:
            if progress_callback:
                progress_callback(f"    Failed to resolve Facebook page: {e}")
        time.sleep(1)

    # Get page info for subscriber count
    page_info_url = f"{FB_API_BASE}/page/info?page_id={page_id}"
    req = urllib.request.Request(page_info_url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
        subscriber_count = str(data.get("followers", data.get("follower_count", "0")))
        channel_name = data.get("name", data.get("page_name", page_id_or_slug))
    except Exception as e:
        if progress_callback:
            progress_callback(f"    Could not get page info: {e}")
    time.sleep(1)

    # Fetch posts from page
    posts_url = f"{FB_API_BASE}/page/posts?page_id={page_id}"
    req = urllib.request.Request(posts_url, headers=headers, method="GET")

    matched_posts = []
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))

        posts = data.get("results", [])
        if not isinstance(posts, list):
            posts = []

        if progress_callback:
            progress_callback(f"    Found {len(posts)} posts from {channel_name}")

        for post in posts:
            message = post.get("message", post.get("message_rich", ""))
            timestamp = post.get("timestamp", "")
            post_url = post.get("url", "")

            # Parse date
            published_date = ""
            if timestamp:
                try:
                    dt = datetime.fromtimestamp(int(timestamp), tz=BANGKOK_TZ)
                    published_date = dt.strftime("%d/%m/%y")
                except (ValueError, TypeError):
                    published_date = str(timestamp)

            # Date filter (proper datetime comparison, not string)
            published_dt = parse_ddmmyy(published_date)
            start_dt = parse_ddmmyy(start_date) if start_date else None
            end_dt = parse_ddmmyy(end_date) if end_date else None
            if start_dt and published_dt and published_dt < start_dt:
                continue
            if end_dt and published_dt and published_dt > end_dt:
                continue

            # Keyword filter (strip # so "#POE2" also matches "POE2")
            if keywords:
                msg_lower = message.lower() if message else ""
                if not any(kw.lower().lstrip('#') in msg_lower or kw.lower() in msg_lower for kw in keywords):
                    continue

            reactions_count = str(post.get("reactions_count", "0"))
            comments_count = str(post.get("comments_count", "0"))
            reshare_count = str(post.get("reshare_count", "0"))

            video_info = post.get("video", {})
            video_view_count = "0"
            has_video = False
            fb_duration = ""
            if isinstance(video_info, dict) and video_info:
                video_view_count = str(video_info.get("view_count", video_info.get("views", "0")))
                has_video = True
                fb_dur = video_info.get("duration", video_info.get("length", ""))
                if isinstance(fb_dur, (int, float)):
                    total_minutes = int(fb_dur) // 60
                    if int(fb_dur) % 60 > 0:
                        total_minutes += 1
                    fb_duration = str(total_minutes)
                elif isinstance(fb_dur, str) and fb_dur:
                    try:
                        total = int(float(fb_dur))
                        total_minutes = total // 60
                        if total % 60 > 0:
                            total_minutes += 1
                        fb_duration = str(total_minutes)
                    except:
                        fb_duration = ""

            fb_content_type = "VOD" if has_video else "Post"

            matched_posts.append({
                "channel_name": channel_name,
                "subscribers": subscriber_count,
                "platform": "facebook",
                "link": post_url,
                "content_type": fb_content_type,
                "published_date": published_date,
                "views": video_view_count if video_view_count != "0" else "",
                "likes": reactions_count,
                "comments": comments_count,
                "shares": reshare_count if reshare_count != "0" else "",
                "saves": "",
                "duration": fb_duration,
                "caption": message,
            })

    except urllib.error.HTTPError as e:
        if progress_callback:
            progress_callback(f"    Facebook HTTP Error {e.code}")
    except Exception as e:
        if progress_callback:
            progress_callback(f"    Facebook Error for {page_id_or_slug}: {e}")

    if progress_callback:
        progress_callback(f"    Matched {len(matched_posts)} posts from {channel_name}")

    channel_meta = {"channel_name": channel_name, "subscribers": subscriber_count}
    return matched_posts, channel_meta


# ============================================================================
# INSTAGRAM - Fetch all posts from a user, then filter
# ============================================================================

def fetch_instagram_channel_posts(username, api_key, start_date=None, end_date=None, keywords=None, progress_callback=None):
    """Fetch posts/reels from an Instagram user, filter by date and keywords.

    Returns list of dicts with post data.
    """
    if keywords is None:
        keywords = []

    username = username.strip().lstrip("@")

    IG_API_HOST = "instagram-scraper-stable-api.p.rapidapi.com"
    IG_API_BASE = f"https://{IG_API_HOST}"
    headers = {
        "x-rapidapi-host": IG_API_HOST,
        "x-rapidapi-key": api_key,
        "Content-Type": "application/x-www-form-urlencoded",
    }

    # Get user info
    subscriber_count = "0"
    channel_name = username
    body = urllib.parse.urlencode({"username_or_url": username}).encode()
    req = urllib.request.Request(f"{IG_API_BASE}/get_ig_user_info.php", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
        user_data = data.get("user", data)
        subscriber_count = str(user_data.get("follower_count", user_data.get("followers", "0")))
        channel_name = user_data.get("full_name", user_data.get("username", username))
    except Exception as e:
        if progress_callback:
            progress_callback(f"    Failed to get IG user info for @{username}: {e}")
    time.sleep(1)

    matched_posts = []
    all_items = []

    # Fetch posts
    body = urllib.parse.urlencode({"username_or_url": username}).encode()
    req = urllib.request.Request(f"{IG_API_BASE}/get_ig_user_posts.php", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
        posts = data.get("posts", [])
        if isinstance(posts, list):
            for post_item in posts:
                node = post_item.get("node", post_item) if isinstance(post_item, dict) else post_item
                node["_source"] = "post"
                all_items.append(node)
            if progress_callback:
                progress_callback(f"    Fetched {len(posts)} posts from @{username}")
    except Exception as e:
        if progress_callback:
            progress_callback(f"    Failed to fetch IG posts: {e}")
    time.sleep(1)

    # Fetch reels
    body = urllib.parse.urlencode({"username_or_url": username}).encode()
    req = urllib.request.Request(f"{IG_API_BASE}/get_ig_user_reels.php", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
        reels = data.get("reels", [])
        if isinstance(reels, list):
            for reel_item in reels:
                node = reel_item.get("node", reel_item) if isinstance(reel_item, dict) else reel_item
                media = node.get("media", node)
                media["_source"] = "reel"
                all_items.append(media)
            if progress_callback:
                progress_callback(f"    Fetched {len(reels)} reels from @{username}")
    except Exception as e:
        if progress_callback:
            progress_callback(f"    Failed to fetch IG reels: {e}")
    time.sleep(1)

    # Process all items
    for item in all_items:
        source = item.pop("_source", "post")

        caption_obj = item.get("caption", {})
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

        # Date filter (proper datetime comparison, not string)
        published_dt = parse_ddmmyy(published_date)
        start_dt = parse_ddmmyy(start_date) if start_date else None
        end_dt = parse_ddmmyy(end_date) if end_date else None
        if start_dt and published_dt and published_dt < start_dt:
            continue
        if end_dt and published_dt and published_dt > end_dt:
            continue

        # Keyword filter (strip # so "#POE2" also matches "POE2")
        if keywords:
            cap_lower = caption.lower() if caption else ""
            if not any(kw.lower().lstrip('#') in cap_lower or kw.lower() in cap_lower for kw in keywords):
                continue

        # Content type
        if source == "reel":
            content_type = "Short"
        else:
            media_type = item.get("media_type", 0)
            if media_type == 2:
                content_type = "VOD"
            elif media_type == 8:
                content_type = "Post"
            else:
                content_type = "Post"

        code = item.get("code", "")
        if source == "reel":
            link = f"https://www.instagram.com/reel/{code}/" if code else ""
        else:
            link = f"https://www.instagram.com/p/{code}/" if code else ""

        like_count = str(item.get("like_count", "0"))
        comment_count = str(item.get("comment_count", "0"))

        play_count = item.get("play_count", None)
        view_count = item.get("view_count", None)
        views = ""
        if play_count is not None:
            views = str(play_count)
        elif view_count is not None:
            views = str(view_count) if str(view_count) != "0" else ""

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
            "duration": "",
            "caption": caption,
        })

    if progress_callback:
        progress_callback(f"    Matched {len(matched_posts)} items from @{username}")

    channel_meta = {"channel_name": channel_name, "subscribers": subscriber_count}
    return matched_posts, channel_meta


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

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

def run_fetcher(progress_callback=None):
    """Main entry point.

    Flow:
    1. Auth with Google Sheets
    2. Read config from 'Result' tab (date range, keywords)
    3. Read channel list from 'Channel KOLs' tab
    4. Read API keys from 'API' tab
    5. For each channel, fetch all content and filter by date + keywords
    6. Write matched results to 'Result' tab starting row 4

    Returns:
        dict with keys: success, total_rows, results, error
    """
    def log(msg):
        if progress_callback:
            progress_callback(msg)

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

    # Step 2: Fallback by tab order if name matching missed any
    # Expected tab order: API → Result → Channel KOLs (Date)
    if not api_tab_name and len(sheets) >= 1:
        api_tab_name = sheets[0]["properties"]["title"]
        log(f"  Fallback: API tab → #{1} '{api_tab_name}'")
    if not result_tab_name and len(sheets) >= 2:
        result_tab_name = sheets[1]["properties"]["title"]
        log(f"  Fallback: Result tab → #{2} '{result_tab_name}'")
    if not channel_tab_name and len(sheets) >= 3:
        channel_tab_name = sheets[2]["properties"]["title"]
        log(f"  Fallback: Channel tab → #{3} '{channel_tab_name}'")

    log(f"  API tab: '{api_tab_name}'")
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
    yt_api_key, tt_api_key, fb_api_key, ig_api_key = read_api_keys(sheets_service, api_tab_name, progress_callback=log)

    # Read channel list
    log("\nReading channel list...")
    channels = read_channel_list(sheets_service, channel_tab_name, progress_callback=log)

    if not channels:
        log("  No channels found. Exiting.")
        return {"success": False, "total_rows": 0, "results": [], "error": "No channels found"}

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
                resolved_id = resolve_youtube_channel_id(ch["channel_id"], yt_api_key, progress_callback=log)
                videos, _ = fetch_youtube_channel_videos(
                    resolved_id, yt_api_key,
                    start_date=start_date, end_date=end_date, keywords=keywords,
                    progress_callback=log
                )
                all_results.extend(videos)
                time.sleep(1)
            except Exception as e:
                log(f"    ERROR processing {ch['channel_name']}: {e}")
                import traceback
                log(f"    {traceback.format_exc()[:200]}")
    elif yt_channels and not yt_api_key:
        log("\n  YouTube channels found but no API key. Skipping.")

    # ---- Fetch TikTok channels ----
    if tt_channels and tt_api_key:
        log(f"\n--- Fetching TikTok channels ({len(tt_channels)}) ---")
        for ch in tt_channels:
            try:
                log(f"\n  Channel: {ch['channel_name']} (@{ch['channel_id']})")
                videos, _ = fetch_tiktok_channel_videos(
                    ch["channel_id"], tt_api_key,
                    start_date=start_date, end_date=end_date, keywords=keywords,
                    progress_callback=log
                )
                all_results.extend(videos)
                time.sleep(1)
            except Exception as e:
                log(f"    ERROR processing {ch['channel_name']}: {e}")
    elif tt_channels and not tt_api_key:
        log("\n  TikTok channels found but no API key. Skipping.")

    # ---- Fetch Facebook channels ----
    if fb_channels and fb_api_key:
        log(f"\n--- Fetching Facebook pages ({len(fb_channels)}) ---")
        for ch in fb_channels:
            try:
                log(f"\n  Page: {ch['channel_name']} ({ch['channel_id']})")
                posts, _ = fetch_facebook_channel_posts(
                    ch["channel_id"], fb_api_key,
                    start_date=start_date, end_date=end_date, keywords=keywords,
                    progress_callback=log
                )
                all_results.extend(posts)
                time.sleep(1)
            except Exception as e:
                log(f"    ERROR processing {ch['channel_name']}: {e}")
    elif fb_channels and not fb_api_key:
        log("\n  Facebook channels found but no API key. Skipping.")

    # ---- Fetch Instagram channels ----
    if ig_channels and ig_api_key:
        log(f"\n--- Fetching Instagram accounts ({len(ig_channels)}) ---")
        for ch in ig_channels:
            try:
                log(f"\n  Account: {ch['channel_name']} (@{ch['channel_id']})")
                posts, _ = fetch_instagram_channel_posts(
                    ch["channel_id"], ig_api_key,
                    start_date=start_date, end_date=end_date, keywords=keywords,
                    progress_callback=log
                )
                all_results.extend(posts)
                time.sleep(1)
            except Exception as e:
                log(f"    ERROR processing {ch['channel_name']}: {e}")
    elif ig_channels and not ig_api_key:
        log("\n  Instagram channels found but no API key. Skipping.")

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

    log(f"\nDone! Check the Result tab in your Google Sheet.")

    return {
        "success": True,
        "total_rows": len(all_results),
        "results": all_results,
        "error": None,
    }
