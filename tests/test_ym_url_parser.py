"""Тесты для парсера ссылок Яндекс.Музыки."""

import sys
import os

# Добавляем src в path для импорта
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from utils.ym_url_parser import parse_ym_url, YMParsedLink


def test_playlist_uuid() -> None:
    url = "https://music.yandex.ru/playlists/5cf5a1e0-c9b5-533e-8ae2-62aaed618bc4?utm_source=desktop&utm_medium=copy_link"
    result = parse_ym_url(url)
    assert result is not None
    assert result.type == "playlist_uuid"
    assert result.playlist_uuid == "5cf5a1e0-c9b5-533e-8ae2-62aaed618bc4"


def test_playlist_uuid_no_params() -> None:
    url = "https://music.yandex.ru/playlists/5cf5a1e0-c9b5-533e-8ae2-62aaed618bc4"
    result = parse_ym_url(url)
    assert result is not None
    assert result.type == "playlist_uuid"
    assert result.playlist_uuid == "5cf5a1e0-c9b5-533e-8ae2-62aaed618bc4"


def test_playlist_legacy() -> None:
    url = "https://music.yandex.ru/users/12345678/playlists/3"
    result = parse_ym_url(url)
    assert result is not None
    assert result.type == "playlist_legacy"
    assert result.uid == 12345678
    assert result.kind == 3


def test_album() -> None:
    url = "https://music.yandex.ru/album/24701386?utm_source=desktop&utm_medium=copy_link"
    result = parse_ym_url(url)
    assert result is not None
    assert result.type == "album"
    assert result.album_id == 24701386


def test_album_trailing_slash() -> None:
    url = "https://music.yandex.ru/album/24701386/"
    result = parse_ym_url(url)
    assert result is not None
    assert result.type == "album"
    assert result.album_id == 24701386


def test_track_in_album() -> None:
    url = "https://music.yandex.ru/album/24701386/track/987654"
    result = parse_ym_url(url)
    assert result is not None
    assert result.type == "track"
    assert result.album_id == 24701386
    assert result.track_id == 987654


def test_artist() -> None:
    url = "https://music.yandex.ru/artist/41126"
    result = parse_ym_url(url)
    assert result is not None
    assert result.type == "artist"
    assert result.artist_id == 41126


def test_not_a_link() -> None:
    result = parse_ym_url("Кино - Группа крови")
    assert result is None


def test_random_url() -> None:
    result = parse_ym_url("https://google.com/search?q=test")
    assert result is None


def test_empty_string() -> None:
    result = parse_ym_url("")
    assert result is None


def test_playlist_legacy_username() -> None:
    url = "https://music.yandex.ru/users/some_username/playlists/3"
    result = parse_ym_url(url)
    assert result is not None
    assert result.type == "playlist_legacy"
    assert result.uid is None
    assert result.kind == 3
    assert result.playlist_uuid == "some_username"


if __name__ == "__main__":
    test_playlist_uuid()
    test_playlist_uuid_no_params()
    test_playlist_legacy()
    test_playlist_legacy_username()
    test_album()
    test_album_trailing_slash()
    test_track_in_album()
    test_artist()
    test_not_a_link()
    test_random_url()
    test_empty_string()
    print("OK: All parser tests passed!")

