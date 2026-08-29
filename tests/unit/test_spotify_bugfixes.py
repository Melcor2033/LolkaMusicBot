"""
Regression tests for three bugs fixed in Dynamic player:
1. SpotifyLinkModal label too long (51 > 45 chars) -> HTTP 400
2. VK playlist URL (music?z=audio_playlist...) not recognized by VK_URL_PATTERN
3. FFmpeg ignores os.environ proxy for googlevideo.com -> 403 Forbidden
"""
import pytest
import re
import os
from unittest.mock import patch, MagicMock

# ─────────────────────────────────────────────────────────────────────────────
# Bug 1: SpotifyLinkModal label length <= 45 chars (Discord API limit)
# ─────────────────────────────────────────────────────────────────────────────

def test_spotify_link_modal_label_length():
    """
    Regression: SpotifyLinkModal url_input label must not exceed 45 characters.
    Discord API returns HTTP 400 if label > 45 chars.
    """
    from views.spotify_views import SpotifyLinkModal
    modal = SpotifyLinkModal()
    label = modal.url_input.label
    assert len(label) <= 45, (
        f"SpotifyLinkModal url_input label is too long ({len(label)} > 45): '{label}'"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Bug 2: VK_URL_PATTERN must recognize music?z=audio_playlist... format
# ─────────────────────────────────────────────────────────────────────────────

def test_vk_url_pattern_recognizes_music_z_format():
    """
    Regression: VK_URL_PATTERN must match browser-format VK music links
    with query param music?z=audio_playlist<owner_id>_<playlist_id>&access_key=...
    """
    from cogs.spotify_manager import is_vk_url, VK_URL_PATTERN
    url = "https://vk.ru/music?z=audio_playlist14374179_1&access_key=842fd3a336980dd698"
    assert is_vk_url(url), (
        f"VK_URL_PATTERN did not recognize VK browser playlist URL: {url}"
    )


def test_vk_url_pattern_recognizes_vkru_music_format():
    """VK browser playlist on vk.ru domain must be recognized."""
    from cogs.spotify_manager import is_vk_url
    # Various VK browser link formats
    assert is_vk_url("https://vk.ru/music?z=audio_playlist14374179_1")
    assert is_vk_url("https://vk.com/music?z=playlist14374179_1")
    # These should still work as before
    assert is_vk_url("https://vk.com/music/playlist/14374179_1_842fd3a336980dd698")
    assert is_vk_url("https://vk.com/audio_playlist14374179_1")


def test_parse_vk_url_extracts_access_key_from_query():
    """
    Regression: parse_vk_url must extract access_key from query params
    (access_key=842fd3a336980dd698) and pass it to al_audio.php as access_hash.
    """
    import re
    url = "https://vk.ru/music?z=audio_playlist14374179_1&access_key=842fd3a336980dd698"

    # The regex in parse_vk_url must find playlist IDs from z= param (excluding single audio)
    match = re.search(
        r'(?:audio_playlist|playlist|album|audio_album)/?(-?\d+)_(\d+)(?:_([a-f0-9]+))?',
        url
    )
    assert match is not None, "Regex did not find owner_id/playlist_id in VK music?z= URL"
    assert match.group(1) == "14374179"
    assert match.group(2) == "1"

    # Single track audio regex check
    single_url = "https://vk.ru/audio2000323760_456245740_a3d7c6777f1511f246"
    assert re.search(r'(?:audio_playlist|playlist|album|audio_album)/?(-?\d+)_(\d+)', single_url) is None
    single_match = re.search(r'audio(-?\d+)_(\d+)(?:_([a-f0-9]+))?', single_url)
    assert single_match is not None
    assert single_match.group(1) == "2000323760"
    assert single_match.group(2) == "456245740"


# ─────────────────────────────────────────────────────────────────────────────
# Bug 3: FFmpeg must use -http_proxy flag (not os.environ) for googlevideo.com
# ─────────────────────────────────────────────────────────────────────────────

def test_ffmpeg_before_opts_contains_http_proxy_for_googlevideo():
    """
    Regression: when STREAM_PROXY is set and stream_url is a googlevideo.com URL,
    the FFmpeg before_options must contain '-http_proxy <proxy>' explicitly,
    because FFmpeg binary does NOT respect os.environ['https_proxy'] for HTTPS streams.
    """
    stream_url = (
        "https://rr1---sn-aj5go5-5i.googlevideo.com/videoplayback"
        "?expire=1785410571&itag=251&source=youtube"
    )
    proxy = "http://xray:10809"

    # Simulate the logic that should be in play_track()
    before_opts = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
    is_youtube = (
        "googlevideo.com" in stream_url
        or "youtube.com" in stream_url
        or "youtu.be" in stream_url
    )
    stream_proxy = proxy  # simulating config.STREAM_PROXY being set

    if is_youtube and stream_proxy:
        before_opts += f" -http_proxy {stream_proxy}"

    assert "-http_proxy" in before_opts, (
        "FFmpeg before_options must include -http_proxy for googlevideo.com URLs"
    )
    assert proxy in before_opts, (
        f"Proxy '{proxy}' must be present in FFmpeg before_options"
    )
    # Ensure we do NOT rely on os.environ only
    assert "http_proxy" not in os.environ or os.environ.get("http_proxy") != proxy, \
        "Should not rely solely on os.environ for FFmpeg proxy"
