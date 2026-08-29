"""Конфигурация радиостанций для модуля Lofi Radio.

Каждая станция описана как frozen-датакласс с прямым HTTP-стримом.
Используются только публичные icecast/shoutcast потоки —
они стабильнее YouTube и не требуют yt-dlp.
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class LofiStation:
    """Описание одной радиостанции."""

    name: str
    url: str
    emoji: str
    genre: str


# ──────────────────────────────────────────────
# Список станций (публичные HTTP-стримы)
# ──────────────────────────────────────────────
STATIONS: list[LofiStation] = [
    LofiStation(
        name="Lofi Girl",
        url="https://play.streamafrica.net/lofiradio",
        emoji="🎧",
        genre="Lo-Fi Hip Hop",
    ),
    LofiStation(
        name="ChillHop",
        url="https://streams.fluxfm.de/Chillhop/mp3-128/streams.fluxfm.de/",
        emoji="☕",
        genre="Chillhop",
    ),

    LofiStation(
        name="Plaza One",
        url="https://radio.plaza.one/mp3",
        emoji="🏬",
        genre="Vaporwave",
    ),
    LofiStation(
        name="SmoothChill",
        url="https://media-ssl.musicradio.com/SmoothChill",
        emoji="🎷",
        genre="Smooth Jazz / Chill",
    ),
    LofiStation(
        name="Lofi 24/7",
        url="http://usa9.fastcast4u.com/proxy/jamz?mp=/1",
        emoji="🎶",
        genre="Lo-Fi",
    ),
    LofiStation(
        name="SomaFM Groove Salad",
        url="http://ice1.somafm.com/groovesalad-128-mp3",
        emoji="🥗",
        genre="Ambient / Chill",
    ),
    LofiStation(
        name="SomaFM Secret Agent",
        url="http://ice1.somafm.com/secretagent-128-mp3",
        emoji="🕵️",
        genre="Downtempo",
    ),
    LofiStation(
        name="SomaFM Drone Zone",
        url="http://ice1.somafm.com/dronezone-128-mp3",
        emoji="🌌",
        genre="Ambient Space",
    ),
    LofiStation(
        name="Radio Paradise Rock",
        url="http://stream.radioparadise.com/rock-128",
        emoji="🎸",
        genre="Rock / Alternative",
    ),
    LofiStation(
        name="SomaFM Indie Pop Rocks",
        url="http://ice1.somafm.com/indiepop-128-mp3",
        emoji="🤘",
        genre="Indie Rock",
    ),
]

DEFAULT_STATION: LofiStation = STATIONS[0]


def get_station_by_name(name: str) -> LofiStation | None:
    """Найти станцию по имени (регистронезависимо)."""
    name_lower = name.lower()
    for station in STATIONS:
        if station.name.lower() == name_lower:
            return station
    return None


def get_random_station(exclude: LofiStation | None = None) -> LofiStation:
    """Вернуть случайную станцию, отличную от текущей."""
    candidates = [s for s in STATIONS if s != exclude] if exclude else STATIONS
    return random.choice(candidates) if candidates else STATIONS[0]
