import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fetcher import (
    compute_er_pct,
    detect_content_link,
    extract_channel_id,
    format_duration,
    format_published_date,
    matches_keywords,
    parse_ddmmyy,
    parse_date_flexible,
    resolve_tiktok_short_url,
    sheet_text,
    swap_dates_if_needed,
    write_results_to_sheet,
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


# ---- YouTube channel ID / URL handling ----

def test_extract_channel_id_youtube_handle_url_with_trailing_path_and_query():
    assert extract_channel_id("https://www.youtube.com/@KamiReiV/videos?si=abc", "youtube") == "KamiReiV"


def test_extract_channel_id_youtube_handle_url_without_scheme():
    assert extract_channel_id("youtube.com/@KamiReiV", "youtube") == "KamiReiV"


def test_extract_channel_id_youtube_percent_encoded_thai_handle():
    assert extract_channel_id("https://www.youtube.com/@%E0%B9%84%E0%B8%97%E0%B8%A2", "youtube") == "ไทย"


def test_extract_channel_id_youtube_channel_url_with_trailing_path():
    assert extract_channel_id(
        "https://www.youtube.com/channel/UCVzwh-Nav8BxYbm-Vt6xQYg/videos", "youtube"
    ) == "UCVzwh-Nav8BxYbm-Vt6xQYg"


def test_extract_channel_id_youtube_user_and_c_urls():
    assert extract_channel_id("https://www.youtube.com/user/SomeLegacyName", "youtube") == "SomeLegacyName"
    assert extract_channel_id("https://www.youtube.com/c/SomeCustom", "youtube") == "SomeCustom"


def test_extract_channel_id_youtube_plain_channel_id_passthrough():
    assert extract_channel_id("UCVzwh-Nav8BxYbm-Vt6xQYg", "youtube") == "UCVzwh-Nav8BxYbm-Vt6xQYg"


def _fake_youtube_api(monkeypatch, responses):
    """Patch urlopen_with_retry; `responses` maps a URL substring to a JSON-able
    reply (or an Exception to raise). Returns the list of URLs requested."""
    import json
    import fetcher
    calls = []

    def fake(req, timeout=15, retries=2, backoff=1.5):
        url = req.full_url if hasattr(req, "full_url") else req
        calls.append(url)
        for needle, reply in responses.items():
            if needle in url:
                if isinstance(reply, Exception):
                    raise reply
                return json.dumps(reply).encode("utf-8")
        return json.dumps({"items": []}).encode("utf-8")

    monkeypatch.setattr(fetcher, "urlopen_with_retry", fake)
    return calls


def test_resolve_youtube_channel_id_uc_id_needs_no_api_call(monkeypatch):
    from fetcher import resolve_youtube_channel_id
    calls = _fake_youtube_api(monkeypatch, {})
    assert resolve_youtube_channel_id("UCVzwh-Nav8BxYbm-Vt6xQYg", "KEY") == "UCVzwh-Nav8BxYbm-Vt6xQYg"
    assert calls == []


def test_resolve_youtube_channel_id_full_channel_url_needs_no_api_call(monkeypatch):
    from fetcher import resolve_youtube_channel_id
    calls = _fake_youtube_api(monkeypatch, {})
    url = "https://www.youtube.com/channel/UCVzwh-Nav8BxYbm-Vt6xQYg"
    assert resolve_youtube_channel_id(url, "KEY") == "UCVzwh-Nav8BxYbm-Vt6xQYg"
    assert calls == []


def test_resolve_youtube_channel_id_handle_uses_exact_for_handle_lookup(monkeypatch):
    from fetcher import resolve_youtube_channel_id
    calls = _fake_youtube_api(monkeypatch, {"forHandle=%40KamiReiV": {"items": [{"id": "UCVzwh-Nav8BxYbm-Vt6xQYg"}]}})
    usage = {}
    assert resolve_youtube_channel_id("KamiReiV", "KEY", usage=usage) == "UCVzwh-Nav8BxYbm-Vt6xQYg"
    assert len(calls) == 1 and "/search" not in calls[0]
    assert usage["yt_units"] == 1


def test_resolve_youtube_channel_id_full_handle_url_and_at_prefix(monkeypatch):
    from fetcher import resolve_youtube_channel_id
    _fake_youtube_api(monkeypatch, {"forHandle=%40KamiReiV": {"items": [{"id": "UCVzwh-Nav8BxYbm-Vt6xQYg"}]}})
    assert resolve_youtube_channel_id("https://www.youtube.com/@KamiReiV", "KEY") == "UCVzwh-Nav8BxYbm-Vt6xQYg"
    assert resolve_youtube_channel_id("@KamiReiV", "KEY") == "UCVzwh-Nav8BxYbm-Vt6xQYg"


def test_resolve_youtube_channel_id_falls_back_to_search_when_no_exact_match(monkeypatch):
    from fetcher import resolve_youtube_channel_id
    calls = _fake_youtube_api(monkeypatch, {"/search": {"items": [{"snippet": {"channelId": "UCfromsearch000000000000"}}]}})
    assert resolve_youtube_channel_id("SomeCustom", "KEY") == "UCfromsearch000000000000"
    assert any("/search" in c for c in calls)


# ---- date parsing: abbreviated and Thai months ----


def test_parse_date_flexible_abbreviated_english_month():
    assert parse_date_flexible("22 Oct 2026") == "22/10/26"
    assert parse_date_flexible("5 Sept 2026") == "05/09/26"


def test_parse_date_flexible_thai_full_month_buddhist_year():
    assert parse_date_flexible("22 ตุลาคม 2569") == "22/10/26"


def test_parse_date_flexible_thai_abbreviated_month():
    assert parse_date_flexible("22 ต.ค. 2569") == "22/10/26"
    assert parse_date_flexible("1 พ.ค. 2026") == "01/05/26"


def test_parse_date_flexible_thai_fallback_month():
    assert parse_date_flexible("15", "พฤษภาคม 2569") == "15/05/26"


def test_parse_date_flexible_invalid_day_or_month_returns_none():
    assert parse_date_flexible("31 Feb 2026") is None
    assert parse_date_flexible("15 Foo 2026") is None


# ---- ER% ----


def test_compute_er_pct_basic():
    row = {"views": "1000", "likes": "50", "comments": "10", "shares": "", "saves": ""}
    assert compute_er_pct(row) == "6.00"


def test_compute_er_pct_blank_comments_counts_as_zero():
    # Kick clips never have comments; this used to blank the whole ER%.
    row = {"views": "1000", "likes": "50", "comments": "", "shares": "", "saves": ""}
    assert compute_er_pct(row) == "5.00"


def test_compute_er_pct_handles_commas_none_and_shares_saves():
    row = {"views": "1,000", "likes": "None", "comments": "100", "shares": "50", "saves": "50"}
    assert compute_er_pct(row) == "20.00"


def test_compute_er_pct_no_views_is_blank():
    assert compute_er_pct({"views": "", "likes": "5"}) == ""
    assert compute_er_pct({"views": "0", "likes": "5"}) == ""


# ---- sheet_text (formula-injection guard) ----


def test_sheet_text_escapes_formula_prefixes():
    for text in ('=HYPERLINK("http://x","y")', "+1234", "-cmd", "@SUM(A1)"):
        assert sheet_text(text) == "'" + text


def test_sheet_text_escapes_numeric_and_date_lookalikes():
    assert sheet_text("1/2") == "'1/2"
    assert sheet_text("2024") == "'2024"


def test_sheet_text_leaves_normal_text_alone():
    assert sheet_text("Normal caption #POE2") == "Normal caption #POE2"
    assert sheet_text("https://youtu.be/abc") == "https://youtu.be/abc"
    assert sheet_text("") == ""
    assert sheet_text(None) is None


# ---- write_results_to_sheet ----


class _FakeValues:
    def __init__(self):
        self.cleared = []
        self.written = None

    def clear(self, spreadsheetId, range):
        self.cleared.append(range)
        return self

    def update(self, spreadsheetId, range, valueInputOption, body):
        self.written = body["values"]
        return self

    def execute(self):
        return {"updatedCells": 0}


class _FakeSheets:
    def __init__(self):
        self.vals = _FakeValues()

    def spreadsheets(self):
        return self

    def values(self):
        return self.vals


def _row(**overrides):
    row = {
        "channel_name": "KOL", "subscribers": "10", "platform": "tiktok", "link": "https://x",
        "content_type": "Short", "published_date": "01/01/26", "views": "100", "likes": "5",
        "comments": "", "shares": "", "saves": "", "duration": "1", "caption": "hello",
    }
    row.update(overrides)
    return row


def test_write_results_empty_does_not_clear_existing_data():
    svc = _FakeSheets()
    write_results_to_sheet(svc, "Result", [])
    assert svc.vals.cleared == []
    assert svc.vals.written is None


def test_write_results_clears_then_writes_when_there_are_results():
    svc = _FakeSheets()
    write_results_to_sheet(svc, "Result", [_row()])
    assert svc.vals.cleared == ["'Result'!A4:O"]
    assert len(svc.vals.written) == 1


def test_write_results_escapes_formula_caption_and_computes_er_with_blank_comments():
    svc = _FakeSheets()
    rows = [_row(caption='=IMPORTXML("http://evil","//a")', channel_name="+KOL")]
    write_results_to_sheet(svc, "Result", rows)
    written = svc.vals.written[0]
    assert written[0] == "'+KOL"
    assert written[13] == "'=IMPORTXML(\"http://evil\",\"//a\")"
    assert written[12] == "5.00"
    assert rows[0]["er_pct"] == "5.00"


# ---- YouTube keyword matching: title or #hashtag in description ----

def test_extract_hashtags_returns_tags_without_hash():
    from fetcher import extract_hashtags
    assert extract_hashtags("Join us #NIKKE #นิกเกะ\n#Airi, thanks") == ["NIKKE", "นิกเกะ", "Airi,"]
    assert extract_hashtags("") == []
    assert extract_hashtags(None) == []


def test_matches_keywords_in_hashtags_matches_whole_hashtag_only():
    from fetcher import matches_keywords_in_hashtags
    assert matches_keywords_in_hashtags("blah #NIKKE blah", ["NIKKE"])
    assert matches_keywords_in_hashtags("blah #nikke", ["#NIKKE"])
    assert matches_keywords_in_hashtags("#นิกเกะ", ["นิกเกะ"])
    # plain word in the description is not a hashtag
    assert not matches_keywords_in_hashtags("playing NIKKE today", ["NIKKE"])
    # a longer hashtag that merely starts with the keyword is not a match
    assert not matches_keywords_in_hashtags("#NIKKE4thAnniversary", ["NIKKE"])
    # neighbouring hashtags do not combine into a match
    assert not matches_keywords_in_hashtags("#ni #kke", ["NIKKE"])


def _yt_item(vid, title, description, published="2026-11-13T10:00:00Z"):
    return {"id": vid, "snippet": {"title": title, "description": description, "publishedAt": published},
            "statistics": {"viewCount": "10", "likeCount": "1", "commentCount": "0"},
            "contentDetails": {"duration": "PT10M"}}


def test_youtube_channel_fetch_keeps_title_and_hashtag_matches_but_not_plain_description(monkeypatch):
    from fetcher import fetch_youtube_channel_videos
    _fake_youtube_api(monkeypatch, {
        "/channels?": {"items": [{"snippet": {"title": "Airi"}, "statistics": {"subscriberCount": "100"},
                                  "contentDetails": {"relatedPlaylists": {"uploads": "UUx"}}}]},
        "/playlistItems": {"items": [
            {"snippet": {"resourceId": {"videoId": v}, "publishedAt": "2026-11-13T10:00:00Z"}}
            for v in ("title_hit", "hashtag_hit", "plain_desc_only", "no_hit")]},
        "/videos?": {"items": [
            _yt_item("title_hit", "NIKKE day one", "nothing"),
            _yt_item("hashtag_hit", "เรียกพี่ว่า ราชา", "Join membership\n#NIKKE #Airi"),
            _yt_item("plain_desc_only", "Other game", "sponsor of NIKKE and more"),
            _yt_item("no_hit", "Other game", "#minecraft"),
        ]},
    })
    logs = []
    videos, meta = fetch_youtube_channel_videos(
        "UCVzwh-Nav8BxYbm-Vt6xQYg", "KEY", start_date="29/10/26", end_date="18/11/26",
        keywords=["NIKKE"], progress_callback=logs.append)
    assert [v["link"].split("=")[-1] for v in videos] == ["title_hit", "hashtag_hit"]
    assert any("1 matched only via a #hashtag" in line for line in logs)


# ---- "Matched by" column (O) ----

def test_keyword_match_source_no_keywords_is_blank():
    from fetcher import keyword_match_source
    assert keyword_match_source("anything #tag", []) == ""


def test_keyword_match_source_caption_vs_hashtag():
    from fetcher import keyword_match_source
    assert keyword_match_source("Playing NIKKE tonight", ["NIKKE"]) == "Caption"
    assert keyword_match_source("Playing NIKKE tonight", ["NIKKE"], "Title") == "Title"
    assert keyword_match_source("Fun stream #NIKKE #game", ["NIKKE"]) == "Hashtag"
    assert keyword_match_source("NIKKE review #NIKKE", ["NIKKE"]) == "Caption"
    assert keyword_match_source("#nikke only", ["#NIKKE"]) == "Hashtag"


def test_make_error_row_has_blank_match_source():
    from fetcher import make_error_row
    row = make_error_row({"channel_name": "X", "platform": "youtube", "channel_id": "x"}, "reason")
    assert row["match_source"] == ""


def test_write_results_writes_match_source_to_column_o():
    svc = _FakeSheets()
    write_results_to_sheet(svc, "Result", [_row(match_source="Hashtag")])
    written = svc.vals.written[0]
    assert len(written) == 15
    assert written[14] == "Hashtag"


def test_write_results_match_source_defaults_to_blank():
    svc = _FakeSheets()
    write_results_to_sheet(svc, "Result", [_row()])
    assert svc.vals.written[0][14] == ""


# ---- Instagram: profile parsing and hidden like counts ----

def test_instagram_profile_fields_reads_nested_data_user_and_edge_followed_by():
    from fetcher import _instagram_profile_fields
    data = {"success": True, "data": {"user": {
        "full_name": "K Ning", "username": "k4niing.3", "edge_followed_by": {"count": 1234}}}}
    assert _instagram_profile_fields(data, "fallback") == ("k4niing.3", "1234")


def test_instagram_profile_fields_accepts_older_flat_shapes():
    from fetcher import _instagram_profile_fields
    assert _instagram_profile_fields({"user": {"full_name": "A", "username": "a_user", "follower_count": 7}}, "x") == ("a_user", "7")
    assert _instagram_profile_fields({"username": "bob", "followers": 9}, "x") == ("bob", "9")


def test_instagram_profile_fields_falls_back_when_nothing_usable():
    from fetcher import _instagram_profile_fields
    assert _instagram_profile_fields({}, "somename") == ("somename", "0")


def test_instagram_like_count_hidden_flag_gives_hidden():
    from fetcher import _instagram_like_count
    assert _instagram_like_count({"like_count": 3, "like_and_view_counts_disabled": True}) == "Hidden"
    assert _instagram_like_count({"like_count": 62, "like_and_view_counts_disabled": False}) == "62"
    assert _instagram_like_count({"like_count": 62}) == "62"
    assert _instagram_like_count({}) == "0"


def test_compute_er_pct_is_blank_when_likes_are_hidden():
    from fetcher import compute_er_pct
    assert compute_er_pct({"views": "100", "likes": "Hidden", "comments": "6"}) == ""


def test_instagram_fetch_uses_profile_name_followers_and_marks_hidden_likes(monkeypatch):
    import datetime as _dt
    import fetcher
    from fetcher import fetch_instagram_channel_posts
    monkeypatch.setattr(fetcher.time, "sleep", lambda s: None)
    ts = int(_dt.datetime(2026, 11, 10, 5, 0, tzinfo=_dt.timezone.utc).timestamp())

    def post(code, likes, hidden):
        return {"code": code, "caption": {"text": "hello"}, "taken_at": ts, "media_type": 1,
                "product_type": "feed", "like_count": likes, "comment_count": 6,
                "like_and_view_counts_disabled": hidden}

    _fake_youtube_api(monkeypatch, {
        "/v1/instagram/profile": {"data": {"user": {"full_name": "K Ning", "username": "k4niing.3", "edge_followed_by": {"count": 1234}}}},
        "/v2/instagram/user/posts": {"items": [post("HID", 3, True), post("VIS", 156, False)], "more_available": False},
        "/v1/instagram/user/reels": {"items": []},
    })
    logs = []
    posts, meta = fetch_instagram_channel_posts(
        "k4niing.3", "KEY", start_date="29/10/26", end_date="18/11/26", progress_callback=logs.append)
    by_code = {p["link"].rstrip("/").split("/")[-1]: p for p in posts}
    assert by_code["HID"]["likes"] == "Hidden" and by_code["HID"]["comments"] == "6"
    assert by_code["VIS"]["likes"] == "156"
    assert all(p["channel_name"] == "k4niing.3" and p["subscribers"] == "1234" for p in posts)
    assert meta["subscribers"] == "1234"
    assert any("1 item(s) hide their like count" in line for line in logs)


def test_instagram_profile_fields_uses_username_not_display_name():
    from fetcher import _instagram_profile_fields
    data = {"data": {"user": {"full_name": "Chanon Udomwetayanan", "username": "lxlamountylxl",
                              "edge_followed_by": {"count": 5}}}}
    assert _instagram_profile_fields(data, "typed_in_sheet")[0] == "lxlamountylxl"
    # no username in the response -> the handle taken from the sheet, never the display name
    data_no_username = {"data": {"user": {"full_name": "Chanon Udomwetayanan"}}}
    assert _instagram_profile_fields(data_no_username, "typed_in_sheet")[0] == "typed_in_sheet"
