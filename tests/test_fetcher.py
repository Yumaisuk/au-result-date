import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fetcher import (
    extract_channel_id,
    format_duration,
    format_published_date,
    matches_keywords,
    parse_ddmmyy,
    parse_date_flexible,
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


def test_format_published_date_already_date_only():
    assert format_published_date("2026-05-22") == "2026-05-22"


def test_format_published_date_empty():
    assert format_published_date("") == ""
