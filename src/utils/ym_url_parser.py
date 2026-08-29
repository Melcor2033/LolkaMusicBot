"""Парсер ссылок Яндекс.Музыки.

Поддерживает форматы:
- https://music.yandex.ru/playlists/{uuid}         → playlist_uuid
- https://music.yandex.ru/users/{uid}/playlists/{kind} → playlist_legacy
- https://music.yandex.ru/album/{id}/track/{id}     → track
- https://music.yandex.ru/album/{id}                → album
- https://music.yandex.ru/artist/{id}               → artist
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True, slots=True)
class YMParsedLink:
    """Результат парсинга URL Яндекс.Музыки."""

    type: str  # "playlist_uuid" | "playlist_legacy" | "album" | "track" | "artist"
    playlist_uuid: Optional[str] = None
    uid: Optional[int] = None
    kind: Optional[int] = None
    album_id: Optional[int] = None
    track_id: Optional[int] = None
    artist_id: Optional[int] = None


# Порядок паттернов важен: более специфичные (track в альбоме) идут раньше менее специфичных (album).
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Плейлист нового формата (UUID)
    (re.compile(r"music\.yandex\.ru/playlists/([a-f0-9\-]{36})"), "playlist_uuid"),
    # Плейлист старого формата (uid/kind)
    (re.compile(r"music\.yandex\.ru/users/([^/]+)/playlists/(\d+)"), "playlist_legacy"),
    # Конкретный трек в альбоме
    (re.compile(r"music\.yandex\.ru/album/(\d+)/track/(\d+)"), "track"),
    # Альбом
    (re.compile(r"music\.yandex\.ru/album/(\d+)"), "album"),
    # Артист
    (re.compile(r"music\.yandex\.ru/artist/(\d+)"), "artist"),
]


def parse_ym_url(text: str) -> Optional[YMParsedLink]:
    """Парсит URL Яндекс.Музыки и возвращает структурированный результат.

    Если текст не содержит распознаваемой ссылки — возвращает None.
    Query-параметры (?utm_source=...) автоматически игнорируются.

    Args:
        text: Строка, потенциально содержащая URL Яндекс.Музыки.

    Returns:
        YMParsedLink с заполненными полями или None.
    """
    # Убираем query params и trailing slash для надёжного матча
    clean = text.split("?")[0].rstrip("/")

    for pattern, link_type in _PATTERNS:
        match = pattern.search(clean)
        if not match:
            continue

        if link_type == "playlist_uuid":
            return YMParsedLink(type="playlist_uuid", playlist_uuid=match.group(1))

        if link_type == "playlist_legacy":
            uid_str = match.group(1)
            kind = int(match.group(2))
            # uid может быть числовым или строковым (логин)
            try:
                uid = int(uid_str)
            except ValueError:
                uid = None
            return YMParsedLink(
                type="playlist_legacy",
                uid=uid,
                kind=kind,
                playlist_uuid=uid_str if uid is None else None,
            )

        if link_type == "track":
            return YMParsedLink(
                type="track",
                album_id=int(match.group(1)),
                track_id=int(match.group(2)),
            )

        if link_type == "album":
            return YMParsedLink(type="album", album_id=int(match.group(1)))

        if link_type == "artist":
            return YMParsedLink(type="artist", artist_id=int(match.group(1)))

    return None
