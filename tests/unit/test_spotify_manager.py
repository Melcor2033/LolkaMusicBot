import pytest
import aiohttp
import asyncio
import json
from unittest.mock import MagicMock, patch, AsyncMock
from cogs.spotify_manager import SpotifyManager
import config

@pytest.fixture
def mock_session(mocker):
    mock_sess = AsyncMock()
    mock_sess.__aenter__.return_value = mock_sess
    mock_sess.get = MagicMock()
    mocker.patch("aiohttp.ClientSession", return_value=mock_sess)
    return mock_sess

@pytest.mark.asyncio
async def test_parse_spotify_url_track(mock_session):
    # Prepare HTML containing track json state
    track_data = {
        "props": {
            "pageProps": {
                "state": {
                    "data": {
                        "entity": {
                            "type": "track",
                            "id": "track_123",
                            "title": "Track Title",
                            "duration": 240000,
                            "artists": [{"name": "Artist 1"}, {"name": "Artist 2"}],
                            "visualIdentity": {
                                "image": [{"url": "http://cover.com/img.jpg"}]
                            }
                        }
                    }
                }
            }
        }
    }
    html = f'<html><body><script id="__NEXT_DATA__" type="application/json">{json.dumps(track_data)}</script></body></html>'
    
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.text = AsyncMock(return_value=html)
    mock_resp.close = MagicMock()
    
    mock_get = MagicMock()
    mock_get.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_session.get.return_value = mock_get
    
    sm = SpotifyManager()
    tracks = await sm.parse_spotify_url("https://open.spotify.com/track/track_123")
    
    assert len(tracks) == 1
    assert tracks[0]["id"] == "track_123"
    assert tracks[0]["title"] == "Track Title"
    assert tracks[0]["artist"] == "Artist 1, Artist 2"
    assert tracks[0]["duration"] == 240
    assert tracks[0]["cover"] == "http://cover.com/img.jpg"
    assert tracks[0]["search_query"] == "Artist 1, Artist 2 - Track Title"

@pytest.mark.asyncio
async def test_parse_spotify_url_playlist(mock_session):
    # Prepare HTML containing playlist trackList
    playlist_data = {
        "props": {
            "pageProps": {
                "state": {
                    "data": {
                        "entity": {
                            "type": "playlist",
                            "title": "Playlist Title",
                            "trackList": [
                                {
                                    "title": "Track 1",
                                    "subtitle": "Artist 1",
                                    "uri": "spotify:track:456",
                                    "duration": 180000
                                }
                            ]
                        }
                    }
                }
            }
        }
    }
    html = f'<html><body><script id="__NEXT_DATA__" type="application/json">{json.dumps(playlist_data)}</script></body></html>'
    
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.text = AsyncMock(return_value=html)
    mock_resp.close = MagicMock()
    
    mock_get = MagicMock()
    mock_get.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_session.get.return_value = mock_get
    
    sm = SpotifyManager()
    tracks = await sm.parse_spotify_url("https://open.spotify.com/playlist/playlist_123")
    
    assert len(tracks) == 1
    assert tracks[0]["id"] == "456"
    assert tracks[0]["title"] == "Track 1"
    assert tracks[0]["artist"] == "Artist 1"
    assert tracks[0]["duration"] == 180

@pytest.mark.asyncio
async def test_parse_spotify_url_errors(mock_session):
    # 404 status
    mock_resp = MagicMock()
    mock_resp.status = 404
    mock_get = MagicMock()
    mock_get.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_session.get.return_value = mock_get
    
    sm = SpotifyManager()
    assert await sm.parse_spotify_url("https://open.spotify.com/track/123") == []
    
    # Missing json script
    mock_resp.status = 200
    mock_resp.text = AsyncMock(return_value="<html></html>")
    assert await sm.parse_spotify_url("https://open.spotify.com/track/123") == []

def test_sync_get_audio_url_direct_link(mocker):
    # Mock yt_dlp
    mock_ydl = MagicMock()
    mock_ydl.__enter__.return_value = mock_ydl
    mock_info = {
        "entries": [{
            "url": "http://audio.stream/direct.mp3",
            "webpage_url": "http://youtube.com/watch",
            "duration": 200,
            "title": "Direct Stream"
        }]
    }
    mock_ydl.extract_info.return_value = mock_info
    
    mocker.patch("yt_dlp.YoutubeDL", return_value=mock_ydl)
    
    sm = SpotifyManager()
    res = sm._sync_get_audio_url("https://youtube.com/watch?v=123")
    assert res is not None
    assert res["url"] == "http://audio.stream/direct.mp3"
    assert res["title"] == "Direct Stream"

def test_sync_get_audio_url_search(mocker):
    mock_ydl = MagicMock()
    mock_ydl.__enter__.return_value = mock_ydl
    mock_info = {
        "entries": [{
            "url": "http://audio.stream/search.mp3",
            "webpage_url": "http://youtube.com/watch",
            "duration": 150,
            "title": "Search Result"
        }]
    }
    mock_ydl.extract_info.return_value = mock_info
    mocker.patch("yt_dlp.YoutubeDL", return_value=mock_ydl)
    
    sm = SpotifyManager()
    res = sm._sync_get_audio_url("Some Artist - Song")
    assert res is not None
    assert res["url"] == "http://audio.stream/search.mp3"
    assert res["title"] == "Search Result"

@pytest.mark.asyncio
async def test_get_audio_url_fallback(mocker, mock_session):
    sm = SpotifyManager()
    
    # Mock thread target to return None first, then return result on fallback query
    mocker.patch.object(sm, "_sync_get_audio_url", side_effect=[None, {"title": "Fallback Song", "url": "http://fallback.com"}])
    
    # Mock oEmbed response
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value={"title": "oEmbed Video Title", "author_name": "Artist"})
    
    mock_get = MagicMock()
    mock_get.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_session.get.return_value = mock_get
    
    res = await sm.get_audio_url("https://youtube.com/watch?v=fallback")
    assert res is not None
    assert res["title"] == "Fallback Song"
    # Should call _sync_get_audio_url second time with fallback query
    sm._sync_get_audio_url.assert_any_call("Artist - oEmbed Video Title")


@pytest.mark.asyncio
async def test_parse_spotify_url_missing_fields(mock_session):
    # state missing
    data1 = {"props": {"pageProps": {}}}
    html1 = f'<html><body><script id="__NEXT_DATA__" type="application/json">{json.dumps(data1)}</script></body></html>'
    
    # entity missing
    data2 = {"props": {"pageProps": {"state": {"data": {}}}}}
    html2 = f'<html><body><script id="__NEXT_DATA__" type="application/json">{json.dumps(data2)}</script></body></html>'
    
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.text = AsyncMock(side_effect=[html1, html2])
    
    mock_get = MagicMock()
    mock_get.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_session.get.return_value = mock_get
    
    sm = SpotifyManager()
    assert len(await sm.parse_spotify_url("https://open.spotify.com/track/1")) == 0
    assert len(await sm.parse_spotify_url("https://open.spotify.com/track/2")) == 0


@pytest.mark.asyncio
async def test_parse_spotify_url_exception(mocker):
    # Cause exception during replace
    sm = SpotifyManager()
    # url as None will raise AttributeError on .replace
    tracks = await sm.parse_spotify_url(None)
    assert tracks == []


def test_sync_get_audio_url_direct_link_no_entries(mocker):
    mock_ydl = MagicMock()
    mock_ydl.__enter__.return_value = mock_ydl
    mock_info = {
        "url": "http://audio.stream/direct.mp3",
        "webpage_url": "http://youtube.com/watch",
        "duration": 200,
        "title": "Direct Stream"
    }
    # Return info directly (no entries)
    mock_ydl.extract_info.return_value = mock_info
    mocker.patch("yt_dlp.YoutubeDL", return_value=mock_ydl)
    
    sm = SpotifyManager()
    res = sm._sync_get_audio_url("https://youtube.com/watch?v=123")
    assert res is not None
    assert res["url"] == "http://audio.stream/direct.mp3"


def test_sync_get_audio_url_direct_link_exception(mocker):
    mock_ydl = MagicMock()
    mock_ydl.__enter__.return_value = mock_ydl
    mock_ydl.extract_info.side_effect = Exception("Extract failed")
    mocker.patch("yt_dlp.YoutubeDL", return_value=mock_ydl)
    
    sm = SpotifyManager()
    res = sm._sync_get_audio_url("https://youtube.com/watch?v=123")
    assert res is None


def test_sync_get_audio_url_search_providers_fallback(mocker):
    mock_ydl = MagicMock()
    mock_ydl.__enter__.return_value = mock_ydl
    
    # Soundcloud fails (Exception), Youtube succeeds
    mock_info = {
        "entries": [{
            "url": "http://youtube.stream/search.mp3",
            "webpage_url": "http://youtube.com/watch",
            "duration": 150,
            "title": "Youtube Result"
        }]
    }
    mock_ydl.extract_info.side_effect = [Exception("Soundcloud error"), mock_info]
    mocker.patch("yt_dlp.YoutubeDL", return_value=mock_ydl)
    
    # Set search providers to soundcloud, youtube
    mocker.patch.object(config, "SPOTIFY_SEARCH_PROVIDERS", ["soundcloud", "youtube"])
    
    sm = SpotifyManager()
    res = sm._sync_get_audio_url("Test Query")
    assert res is not None
    assert res["url"] == "http://youtube.stream/search.mp3"


@pytest.mark.asyncio
async def test_get_audio_url_fallback_proxy_only(mocker, mock_session):
    sm = SpotifyManager()
    mocker.patch.object(sm, "_sync_get_audio_url", side_effect=[None, {"title": "Fallback Song", "url": "http://fallback.com"}])
    
    # Direct oEmbed fails, proxy oEmbed succeeds
    mock_resp_direct = MagicMock()
    mock_resp_direct.status = 500
    
    mock_resp_proxy = MagicMock()
    mock_resp_proxy.status = 200
    mock_resp_proxy.json = AsyncMock(return_value={"title": "Proxy oEmbed Title", "author_name": "Artist"})
    
    mock_get_direct = MagicMock()
    mock_get_direct.__aenter__ = AsyncMock(return_value=mock_resp_direct)
    
    mock_get_proxy = MagicMock()
    mock_get_proxy.__aenter__ = AsyncMock(return_value=mock_resp_proxy)
    
    mock_session.get.side_effect = [mock_get_direct, mock_get_proxy]
    
    res = await sm.get_audio_url("https://youtube.com/watch?v=proxy")
    assert res is not None
    assert res["title"] == "Fallback Song"
    sm._sync_get_audio_url.assert_any_call("Artist - Proxy oEmbed Title")


@pytest.mark.asyncio
async def test_get_audio_url_fallback_failed(mocker, mock_session):
    sm = SpotifyManager()
    mocker.patch.object(sm, "_sync_get_audio_url", return_value=None)
    
    # All oEmbed fail
    mock_resp = MagicMock()
    mock_resp.status = 404
    mock_get = MagicMock()
    mock_get.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_session.get.return_value = mock_get
    
    res = await sm.get_audio_url("https://youtube.com/watch?v=failed")
    assert res is None


@pytest.mark.asyncio
async def test_parse_spotify_url_cover_exception(mock_session):
    playlist_data = {
        "props": {
            "pageProps": {
                "state": {
                    "data": {
                        "entity": {
                            "type": "track",
                            "id": "123",
                            "title": "Track",
                            "artists": [],
                            "visualIdentity": {
                                "image": [None] # will raise exception when accessing .get on None
                            }
                        }
                    }
                }
            }
        }
    }
    html = f'<html><body><script id="__NEXT_DATA__" type="application/json">{json.dumps(playlist_data)}</script></body></html>'
    
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.text = AsyncMock(return_value=html)
    
    mock_get = MagicMock()
    mock_get.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_session.get.return_value = mock_get
    
    sm = SpotifyManager()
    tracks = await sm.parse_spotify_url("https://open.spotify.com/track/123")
    assert len(tracks) == 1
    assert tracks[0]["cover"] is None


def test_sync_get_audio_url_direct_link_empty_entries(mocker):
    mock_ydl = MagicMock()
    mock_ydl.__enter__.return_value = mock_ydl
    mock_ydl.extract_info.return_value = {"entries": []}
    mocker.patch("yt_dlp.YoutubeDL", return_value=mock_ydl)
    
    sm = SpotifyManager()
    assert sm._sync_get_audio_url("https://youtube.com/watch?v=empty") is None


def test_sync_get_audio_url_empty_providers(mocker):
    mock_ydl = MagicMock()
    mock_ydl.__enter__.return_value = mock_ydl
    mock_ydl.extract_info.return_value = {"url": "http://audio.stream"}
    mocker.patch("yt_dlp.YoutubeDL", return_value=mock_ydl)
    
    mocker.patch.object(config, "SPOTIFY_SEARCH_PROVIDERS", [])
    
    sm = SpotifyManager()
    res = sm._sync_get_audio_url("Search Query")
    assert res is not None


def test_sync_get_audio_url_custom_provider(mocker):
    mock_ydl = MagicMock()
    mock_ydl.__enter__.return_value = mock_ydl
    mock_ydl.extract_info.return_value = {"url": "http://audio.stream"}
    mocker.patch("yt_dlp.YoutubeDL", return_value=mock_ydl)
    
    mocker.patch.object(config, "SPOTIFY_SEARCH_PROVIDERS", ["vimeo"])
    
    sm = SpotifyManager()
    res = sm._sync_get_audio_url("Search Query")
    mock_ydl.extract_info.assert_called_once_with("vimeosearch1:Search Query", download=False)


def test_sync_get_audio_url_search_no_entries_but_url(mocker):
    mock_ydl = MagicMock()
    mock_ydl.__enter__.return_value = mock_ydl
    mock_ydl.extract_info.return_value = {"url": "http://search.stream"}
    mocker.patch("yt_dlp.YoutubeDL", return_value=mock_ydl)
    
    sm = SpotifyManager()
    res = sm._sync_get_audio_url("Search Query")
    assert res["url"] == "http://search.stream"


def test_sync_get_audio_url_all_providers_fail(mocker):
    mock_ydl = MagicMock()
    mock_ydl.__enter__.return_value = mock_ydl
    mock_ydl.extract_info.side_effect = Exception("Extract failed")
    mocker.patch("yt_dlp.YoutubeDL", return_value=mock_ydl)
    
    sm = SpotifyManager()
    assert sm._sync_get_audio_url("Search Query") is None


@pytest.mark.asyncio
async def test_get_audio_url_oembed_proxy_exception(mocker, mock_session):
    sm = SpotifyManager()
    mocker.patch.object(sm, "_sync_get_audio_url", return_value=None)
    
    # direct get throws exception, proxy get throws exception
    mock_session.get.side_effect = Exception("HTTP error")
    
    res = await sm.get_audio_url("https://youtube.com/watch?v=123")
    assert res is None


@pytest.mark.asyncio
async def test_get_audio_url_success_direct(mocker):
    sm = SpotifyManager()
    mock_res = {"url": "http://audio", "title": "Direct Audio"}
    mocker.patch.object(sm, "_sync_get_audio_url", return_value=mock_res)
    
    res = await sm.get_audio_url("Some Song")
    assert res == mock_res


def test_is_vk_url():
    from cogs.spotify_manager import is_vk_url
    assert is_vk_url("https://vk.com/audio-2001123456_123456") is True
    assert is_vk_url("https://vk.com/audio_playlist284343620_121") is True
    assert is_vk_url("https://vk.com/music/album/-2000123456_123456") is True
    assert is_vk_url("https://vk.com/playlist/-2000123456_123456") is True
    assert is_vk_url("https://vk.com/wall-123456_789") is True
    assert is_vk_url("https://vk.com/audios123456789") is True
    assert is_vk_url("https://vk.ru/audios134861724?section=all") is True
    assert is_vk_url("https://vk.com/artist/artist_name") is True
    assert is_vk_url("https://open.spotify.com/track/12345") is False
    assert is_vk_url("https://youtube.com/watch?v=12345") is False


@pytest.mark.asyncio
async def test_parse_vk_url_success(mocker):
    sm = SpotifyManager()
    mock_ydl = MagicMock()
    mock_ydl.__enter__.return_value = mock_ydl
    mock_ydl.extract_info.return_value = {
        'entries': [
            {
                'id': '101',
                'title': 'Test Track 1',
                'artist': 'Test Artist',
                'duration': 180,
                'thumbnail': 'http://cover.png',
                'url': 'https://vk.com/audio101'
            }
        ]
    }
    mocker.patch("yt_dlp.YoutubeDL", return_value=mock_ydl)
    
    tracks = await sm.parse_vk_url("https://vk.com/playlist/123")
    assert len(tracks) == 1
    assert tracks[0]['title'] == 'Test Track 1'
    assert tracks[0]['artist'] == 'Test Artist'
    assert tracks[0]['search_query'] == 'Test Artist - Test Track 1'




