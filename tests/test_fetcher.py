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


def test_matches_keywords_thai_keyword_ending_in_combining_vowel():
    # \b treats Thai combining vowels/tone marks (e.g. the ี in ของดี) as
    # non-word characters, so a plain \b...\b regex never finds a boundary
    # here even with spaces on both sides - this must fall back to substring
    # matching for Thai instead of silently missing a real match.
    assert matches_keywords("ซื้อ ของดี วันนี้", ["ของดี"]) is True


def test_matches_keywords_thai_keyword_embedded_no_spaces():
    # Thai doesn't put spaces between words at all, so the keyword can be
    # directly adjacent to other Thai text with no delimiter whatsoever.
    assert matches_keywords("ซื้อของดีวันนี้เลยด่วน", ["ของดี"]) is True


def test_matches_keywords_thai_keyword_not_present():
    assert matches_keywords("วันนี้อากาศดี", ["ของดี"]) is False


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
    url = "https://www.facebook.com/somepage/posts/123456789012345"
    assert detect_content_link(url, "facebook") == {
        "post_url": url, "post_id": "123456789012345", "page_slug": "somepage"}


def test_detect_content_link_facebook_videos_url():
    url = "https://www.facebook.com/somepage/videos/987654321098765"
    assert detect_content_link(url, "facebook") == {
        "post_url": url, "post_id": "987654321098765", "page_slug": "somepage"}


def test_detect_content_link_facebook_pfbid_post_url():
    # Newer post URLs use an opaque pfbid token instead of a numeric id - it
    # used to fall through as a page link and fetch the whole page
    url = "https://www.facebook.com/somepage/posts/pfbid02AbCdEfGhIjKlMnOpQrStUvWxYz"
    result = detect_content_link(url, "facebook")
    assert result == {"post_url": url, "post_id": "", "page_slug": ""}


def test_detect_content_link_facebook_reel_url():
    url = "https://www.facebook.com/reel/1682717543474921"
    assert detect_content_link(url, "facebook") == {
        "post_url": url, "post_id": "1682717543474921", "page_slug": ""}


def test_detect_content_link_facebook_watch_url():
    url = "https://www.facebook.com/watch/?v=1234567890123"
    assert detect_content_link(url, "facebook") == {
        "post_url": url, "post_id": "1234567890123", "page_slug": ""}


def test_detect_content_link_facebook_share_url():
    url = "https://www.facebook.com/share/p/1AbCdEfGhI/"
    assert detect_content_link(url, "facebook") == {"post_url": url, "post_id": "", "page_slug": ""}


def test_detect_content_link_facebook_permalink_url():
    url = "https://www.facebook.com/permalink.php?story_fbid=123456789012&id=99887766"
    assert detect_content_link(url, "facebook") == {
        "post_url": url, "post_id": "123456789012", "page_slug": ""}


def test_detect_content_link_facebook_profile_url_is_not_content():
    assert detect_content_link("https://www.facebook.com/somepage", "facebook") is None
    assert detect_content_link("https://www.facebook.com/profile.php?id=100063669491743", "facebook") is None
    assert detect_content_link("https://www.facebook.com/somepage/videos/", "facebook") is None


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


# ---- make_error_row ----

def test_make_error_row_has_sheet_name_error_in_link_and_original_link_in_caption():
    from fetcher import make_error_row
    ch = {"channel_name": "JJamx", "platform": "facebook", "channel_id": "x",
          "raw_link": "https://www.facebook.com/share/p/abc/", "order": 4}
    row = make_error_row(ch, "ดึงข้อมูลไม่สำเร็จ")
    assert row["channel_name"] == "JJamx"
    assert row["platform"] == "facebook"
    assert row["link"] == "Error"
    assert "https://www.facebook.com/share/p/abc/" in row["caption"]
    assert "ดึงข้อมูลไม่สำเร็จ" in row["caption"]
    assert row["order"] == 4


def test_make_error_row_falls_back_to_channel_id_without_raw_link():
    from fetcher import make_error_row
    row = make_error_row({"channel_name": "A", "platform": "youtube", "channel_id": "someid"})
    assert "someid" in row["caption"]
    assert row["order"] == 0


# ---- fetch_facebook_content_link ----

def _fb_row(details, url="https://www.facebook.com/permalink.php?story_fbid=pfbid0abc&id=1"):
    from unittest import mock
    import fetcher
    link = fetcher.detect_content_link(url, "facebook")
    with mock.patch.object(fetcher, "fetch_facebook_post_details", return_value=details):
        posts, meta = fetcher.fetch_facebook_content_link(link, "KEY")
    return posts, meta


def test_facebook_content_link_photo_post_is_not_labelled_vod():
    details = {"success": True, "like_count": 10, "comment_count": 2, "share_count": 3,
               "view_count": 0, "video": {"id": None, "sd_url": None}, "description": "photo",
               "creation_time": "2026-10-28T03:00:00.000Z", "author": {"name": "Someone"}}
    posts, meta = _fb_row(details)
    assert posts[0]["content_type"] == "Post"
    assert posts[0]["views"] == ""
    assert (posts[0]["likes"], posts[0]["comments"], posts[0]["shares"]) == ("10", "2", "3")
    assert meta["had_error"] is False


def test_facebook_content_link_video_post_is_vod_with_views():
    details = {"success": True, "like_count": 10, "comment_count": 2, "share_count": 3,
               "view_count": 5000, "video": {"id": "1", "length_in_second": 125.0},
               "creation_time": "2026-10-28T03:00:00.000Z", "author": {"name": "Someone"}}
    posts, _ = _fb_row(details, "https://www.facebook.com/somepage/videos/123456789012")
    assert posts[0]["content_type"] == "VOD"
    assert posts[0]["views"] == "5000"
    assert posts[0]["duration"] == "3"


def test_facebook_content_link_reel_is_short():
    details = {"success": True, "like_count": 1, "comment_count": 1, "share_count": 1,
               "view_count": 99, "video": {"id": "1"}, "author": {"name": "Someone"}}
    posts, _ = _fb_row(details, "https://www.facebook.com/reel/1464406115597705")
    assert posts[0]["content_type"] == "Short"


# ---- resolve_tiktok_short_url ----

def test_resolve_tiktok_short_url_passthrough_for_normal_url():
    # Not a vt./vm.tiktok.com short-link - must return unchanged, no network call
    url = "https://www.tiktok.com/@someuser/video/1234567890"
    assert resolve_tiktok_short_url(url) == url


def test_resolve_tiktok_short_url_passthrough_for_non_tiktok_url():
    url = "https://www.youtube.com/watch?v=abc123"
    assert resolve_tiktok_short_url(url) == url
