import pytest
import os
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock

from cogs.spotify import (
    SpotifyMusic,
    extract_youtube_playlist_id,
    normalize_youtube_playlist_entry,
    YOUTUBE_PLAYLIST_MAX_TRACKS,
)


def test_extract_youtube_playlist_id_urls():
    # Watch URL with list param
    assert extract_youtube_playlist_id("https://www.youtube.com/watch?v=abc&list=PL1234567890abcdef") == "PL1234567890abcdef"
    
    # Standard playlist URL (user provided example)
    assert extract_youtube_playlist_id("https://youtube.com/playlist?list=PLDZI9QxVsZbI") == "PLDZI9QxVsZbI"
    assert extract_youtube_playlist_id("https://www.youtube.com/playlist?list=PLDZI9QxVsZbI") == "PLDZI9QxVsZbI"

    # YouTube Music URL
    assert extract_youtube_playlist_id("https://music.youtube.com/playlist?list=OLAK5uy_xyz12345") == "OLAK5uy_xyz12345"

    # YouTu.be short link with list
    assert extract_youtube_playlist_id("https://youtu.be/vid123?list=RDxyz1234567890") == "RDxyz1234567890"

    # Bare ID
    assert extract_youtube_playlist_id("PL1234567890abcdef") == "PL1234567890abcdef"

    # Invalid / Normal watch URL without list
    assert extract_youtube_playlist_id("https://www.youtube.com/watch?v=abc123xyz") is None
    assert extract_youtube_playlist_id("Просто название песни") is None
    assert extract_youtube_playlist_id("") is None


def test_normalize_youtube_playlist_entry_valid():
    entry = {
        "id": "vid123",
        "title": "Test Track Title",
        "url": "https://www.youtube.com/watch?v=vid123",
        "uploader": "Test Channel",
        "duration": 210,
        "thumbnail": "https://i.ytimg.com/vi/vid123/hqdefault.jpg"
    }
    normalized = normalize_youtube_playlist_entry(entry, fallback_uploader="Fallback")
    assert normalized is not None
    assert normalized["id"] == "search:https://www.youtube.com/watch?v=vid123"
    assert normalized["title"] == "Test Track Title"
    assert normalized["artists"] == "Test Channel"
    assert normalized["duration"] == 210
    assert normalized["thumbnail_url"] == "https://i.ytimg.com/vi/vid123/hqdefault.jpg"


def test_normalize_youtube_playlist_entry_filters():
    # Private video
    assert normalize_youtube_playlist_entry({"id": "p1", "title": "[Private video]"}, None) is None

    # Deleted video
    assert normalize_youtube_playlist_entry({"id": "d1", "title": "[Deleted video]"}, None) is None

    # Live stream
    assert normalize_youtube_playlist_entry({"id": "l1", "title": "Live Stream", "is_live": True}, None) is None
    assert normalize_youtube_playlist_entry({"id": "l2", "title": "Upcoming", "live_status": "is_upcoming"}, None) is None

    # Missing both URL and ID
    assert normalize_youtube_playlist_entry({"title": "No media"}, None) is None
    assert normalize_youtube_playlist_entry({}, None) is None


@pytest.mark.asyncio
async def test_resolve_youtube_playlist_success(mocker):
    mock_ydl = MagicMock()
    mock_entries = [
        {"id": f"vid_{i}", "title": f"Track {i}", "url": f"https://www.youtube.com/watch?v=vid_{i}", "uploader": "Artist", "duration": 180}
        for i in range(5)
    ]
    # Add a private entry that should be filtered out
    mock_entries.append({"id": "priv", "title": "[Private video]"})

    mock_ydl.extract_info.return_value = {
        "_type": "playlist",
        "title": "My Test Playlist",
        "uploader": "Playlist Creator",
        "entries": mock_entries
    }
    mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
    mock_ydl.__exit__ = MagicMock(return_value=False)

    mocker.patch("yt_dlp.YoutubeDL", return_value=mock_ydl)

    bot = AsyncMock()
    spotify_cog = SpotifyMusic(bot)

    tracks = await spotify_cog.resolve_youtube_playlist("https://youtube.com/playlist?list=PLDZI9QxVsZbI")

    assert len(tracks) == 5
    assert tracks[0]["title"] == "Track 0"
    assert tracks[0]["artists"] == "Artist"
    assert tracks[0]["search_query"] == "https://www.youtube.com/watch?v=vid_0"


@pytest.mark.asyncio
async def test_resolve_youtube_playlist_max_tracks_limit(mocker):
    mock_ydl = MagicMock()
    mock_entries = [
        {"id": f"vid_{i}", "title": f"Track {i}", "url": f"https://www.youtube.com/watch?v=vid_{i}", "uploader": "Artist", "duration": 180}
        for i in range(150)
    ]

    mock_ydl.extract_info.return_value = {
        "_type": "playlist",
        "title": "Huge Playlist",
        "entries": mock_entries
    }
    mock_ydl.__enter__ = MagicMock(return_value=mock_ydl)
    mock_ydl.__exit__ = MagicMock(return_value=False)

    mocker.patch("yt_dlp.YoutubeDL", return_value=mock_ydl)

    bot = AsyncMock()
    spotify_cog = SpotifyMusic(bot)

    tracks = await spotify_cog.resolve_youtube_playlist("https://youtube.com/playlist?list=PLDZI9QxVsZbI")

    assert len(tracks) == YOUTUBE_PLAYLIST_MAX_TRACKS
