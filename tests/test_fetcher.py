import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fetcher import (
    detect_content_link,
    extract_channel_id,
    format_duration,
    format_published_date,
    matches_keywords,
    parse_ddmmyy,
    parse_date_flexible,
    resolve_tiktok_short_url,
    swap_dates_if_needed,
)


# ---- parse_ddmmyy ----

def test_parse_ddmmyy_two_digit_year():
    assert parse_ddmmyy("22/05/26") == datetime.date(2026, 5, 22)


def test_parse_ddmmyy_four_digit_year():
    assert parse_ddmmyy("22/05/2026") == datetime.date(2026, 5, 22)


def test_parse_ddmmyy_iso_format():
    assert parse_ddmmyy("2026-05-22") == datetime.date(2026, 5, 22)


def test_parse_ddmmyy_invalid_returns_none():
    assert parse_ddmmyy("not a date") is None


def test_parse_ddmmyy_empty_returns_none():
    assert parse_ddmmyy("") is None
    assert parse_ddmmyy(None) is None


# ---- parse_date_flexible ----

def test_parse_date_flexible_iso():
    assert parse_date_flexible("2026-05-22") == "22/05/26"


def test_parse_date_flexible_ddmmyyyy():
    assert parse_date_flexible("22/5/2026") == "22/05/26"


def test_parse_date_flexible_ddmmyy():
    assert parse_date_flexible("22/5/26") == "22/05/26"


def test_parse_date_flexible_day_month_year_text():
    assert parse_date_flexible("15 May 2026") == "15/05/26"


def test_parse_date_flexible_day_plus_fallback_month():
    assert parse_date_flexible("15", "May 2026") == "15/05/26"


def test_parse_date_flexible_empty_returns_none():
    assert parse_date_flexible("") is None
    assert parse_date_flexible("   ") is None


def test_parse_date_flexible_garbage_returns_none():
    assert parse_date_flexible("not a date") is None


# ---- extract_channel_id ----

def test_extract_channel_id_plain_handle_passthrough():
    assert extract_channel_id("somechannel", "youtube") == "somechannel"


def test_extract_channel_id_strips_leading_at_for_tiktok():
    assert extract_channel_id("@handle", "tiktok") == "handle"


def test_extract_channel_id_strips_leading_at_for_instagram():
    assert extract_channel_id("@handle", "instagram") == "handle"


def test_extract_channel_id_keeps_at_for_youtube_plain_value():
    # YouTube plain (non-URL) values are returned as-is, @ included
    assert extract_channel_id("@handle", "youtube") == "@handle"


def test_extract_channel_id_tiktok_url():
    assert extract_channel_id("https://www.tiktok.com/@somehandle", "tiktok") == "somehandle"


def test_extract_channel_id_youtube_channel_url():
    assert extract_channel_id(
        "https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv", "youtube"
    ) == "UCabcdefghijklmnopqrstuv"


def test_extract_channel_id_youtube_handle_url():
    assert extract_channel_id("https://www.youtube.com/@somehandle", "youtube") == "somehandle"


def test_extract_channel_id_facebook_url():
    assert extract_channel_id("https://www.facebook.com/SomePage", "facebook") == "SomePage"


def test_extract_channel_id_instagram_url():
    assert extract_channel_id("https://www.instagram.com/someuser", "instagram") == "someuser"


def test_extract_channel_id_kick_channel_url():
    assert extract_channel_id("https://kick.com/xqc", "kick") == "xqc"


def test_extract_channel_id_kick_clip_url():
    assert extract_channel_id("https://kick.com/xqc/clips/clip_01JGJHB6CEVFCQRYTVPM8DW892", "kick") == "xqc"


def test_extract_channel_id_empty_returns_empty():
    assert extract_channel_id("", "youtube") == ""


# ---- matches_keywords ----

def test_matches_keywords_no_keywords_matches_everything():
    assert matches_keywords("anything at all", []) is True


def test_matches_keywords_word_boundary_match():
    assert matches_keywords("check out this AI tool", ["AI"]) is True


def test_matches_keywords_no_false_positive_substring():
    # "AI" must not match inside "MAIN" - the bug this test guards against
    assert matches_keywords("this is the MAIN feature", ["AI"]) is False


def test_matches_keywords_case_insensitive():
    assert matches_keywords("PATHOFEXILE2 news", ["pathofexile2"]) is True
    assert matches_keywords("pathofexile2 news", ["PathOfExile2"]) is True


def test_matches_keywords_hashtag_prefix_matches_plain_word():
    assert matches_keywords("hype for POE2 this week", ["#POE2"]) is True


def test_matches_keywords_no_match():
    assert matches_keywords("totally unrelated content", ["POE2"]) is False


def test_matches_keywords_none_text():
    assert matches_keywords(None, ["POE2"]) is False
    assert matches_keywords(None, []) is True


def test_matches_keywords_concatenated_keyword_matches_spaced_text():
    # Campaign keyword written as one word, but the caption spells it with spaces
    assert matches_keywords("Just Like GTA: Gangstar Mirage City", ["gangstarmiragecity"]) is True


def test_matches_keywords_short_keyword_still_requires_word_boundary():
    # The concatenated-match fallback must not reintroduce false positives for short keywords
    assert matches_keywords("this is the MAIN feature", ["AI"]) is False


# ---- format_duration ----

def test_format_duration_minutes_and_seconds_round_up():
    assert format_duration("PT1M2S") == "2"  # rounds up on any leftover seconds


def test_format_duration_exact_minutes_no_round_up():
    assert format_duration("PT2M") == "2"


def test_format_duration_hours_minutes():
    assert format_duration("PT1H2M3S") == "63"


def test_format_duration_live_marker():
    assert format_duration("P0D") == "Live"


def test_format_duration_empty():
    assert format_duration("") == ""


# ---- format_published_date ----

def test_format_published_date_iso_datetime():
    assert format_published_date("2026-05-22T10:00:00Z") == "22/05/26"


def test_format_published_date_converts_utc_to_bangkok_across_day_boundary():
    # 18:30 UTC is 01:30 the *next* day in Bangkok (UTC+7) - must roll the date over
    assert format_published_date("2026-05-22T18:30:00Z") == "23/05/26"


def test_format_published_date_already_date_only():
    assert format_published_date("2026-05-22") == "2026-05-22"


def test_format_published_date_empty():
    assert format_published_date("") == ""


# ---- swap_dates_if_needed ----

def test_swap_dates_if_needed_already_ascending_no_swap():
    assert swap_dates_if_needed("05/08/26", "10/08/26") == ("05/08/26", "10/08/26", False)


def test_swap_dates_if_needed_reversed_input_gets_swapped():
    assert swap_dates_if_needed("10/08/26", "05/08/26") == ("05/08/26", "10/08/26", True)


def test_swap_dates_if_needed_month_boundary_not_misordered():
    # The bug this guards against: lexicographic string comparison would
    # think "28/07/26" > "05/08/26" (day digit '2' > '0') and wrongly swap
    # an already-correct ascending range spanning a month boundary.
    assert swap_dates_if_needed("28/07/26", "05/08/26") == ("28/07/26", "05/08/26", False)


def test_swap_dates_if_needed_unparsable_returns_unchanged():
    assert swap_dates_if_needed("not a date", "05/08/26") == ("not a date", "05/08/26", False)


# ---- detect_content_link ----

def test_detect_content_link_youtube_watch_url():
    assert detect_content_link("https://www.youtube.com/watch?v=6kQDTuK5uUo", "youtube") == {"video_id": "6kQDTuK5uUo"}


def test_detect_content_link_youtube_shorts_url():
    assert detect_content_link("https://www.youtube.com/shorts/6kQDTuK5uUo", "youtube") == {"video_id": "6kQDTuK5uUo"}


def test_detect_content_link_youtube_channel_url_is_not_content():
    assert detect_content_link("https://www.youtube.com/@BaVinciPlay", "youtube") is None


def test_detect_content_link_tiktok_video_url():
    result = detect_content_link("https://www.tiktok.com/@someuser/video/7123456789012345678", "tiktok")
    assert result == {"username": "someuser", "video_id": "7123456789012345678"}


def test_detect_content_link_tiktok_profile_url_is_not_content():
    assert detect_content_link("https://www.tiktok.com/@someuser", "tiktok") is None


def test_detect_content_link_facebook_post_url():
    result = detect_content_link("https://www.facebook.com/somepage/posts/123456789012345", "facebook")
    assert result == {"post_id": "123456789012345"}


def test_detect_content_link_facebook_videos_url():
    result = detect_content_link("https://www.facebook.com/somepage/videos/987654321098765", "facebook")
    assert result == {"post_id": "987654321098765"}


def test_detect_content_link_facebook_reel_url_flagged_unsupported():
    # facebook.com/reel/{id} has no page name in the path at all - unlike
    # /PageName/posts/{id}, there's no profile to search from the URL alone
    result = detect_content_link("https://www.facebook.com/reel/1682717543474921", "facebook")
    assert result == {"post_id": "1682717543474921", "unsupported": True}


def test_detect_content_link_facebook_watch_url_flagged_unsupported():
    result = detect_content_link("https://www.facebook.com/watch/?v=1234567890123", "facebook")
    assert result == {"post_id": "1234567890123", "unsupported": True}


def test_detect_content_link_facebook_profile_url_is_not_content():
    assert detect_content_link("https://www.facebook.com/somepage", "facebook") is None


def test_detect_content_link_instagram_reel_url_flagged_unsupported():
    result = detect_content_link("https://www.instagram.com/reel/ABC123/", "instagram")
    assert result == {"code": "ABC123", "unsupported": True}


def test_detect_content_link_kick_clip_url():
    url = "https://kick.com/xqc/clips/clip_01JGJHB6CEVFCQRYTVPM8DW892"
    assert detect_content_link(url, "kick") == {"clip_url": url}


def test_detect_content_link_kick_video_url_flagged_unsupported():
    # kick.com/channel/videos/{uuid} is a VOD, not a "clip" - confirmed via
    # a live API test that ScrapeCreators 500s on these every time
    url = "https://kick.com/huahed/videos/01a00053-e020-72db-96dc-a1eb847a98fa"
    assert detect_content_link(url, "kick") == {"clip_url": url, "unsupported": True}


def test_detect_content_link_kick_channel_url_is_not_content():
    # No channel-listing endpoint exists for Kick - a bare channel URL has nothing to fetch
    assert detect_content_link("https://kick.com/xqc", "kick") is None


def test_detect_content_link_plain_handle_is_not_content():
    assert detect_content_link("UCQ_S28L6WmCl6LWnnv90C9g", "youtube") is None


# ---- resolve_tiktok_short_url ----

def test_resolve_tiktok_short_url_passthrough_for_normal_url():
    # Not a vt./vm.tiktok.com short-link - must return unchanged, no network call
    url = "https://www.tiktok.com/@someuser/video/1234567890"
    assert resolve_tiktok_short_url(url) == url


def test_resolve_tiktok_short_url_passthrough_for_non_tiktok_url():
    url = "https://www.youtube.com/watch?v=abc123"
    assert resolve_tiktok_short_url(url) == url
