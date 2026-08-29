"""Модуль утилит Soundscapes (Фоновые Атмосферы).

Обеспечивает формирование параметров FFmpeg для микширования основной музыки
и фоновой зацикленной атмосферы (дождь, камин, прибой, костёр, капли).
"""

import os
from typing import Optional, Tuple

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOUNDSCAPES_DIR = os.path.join(BASE_DIR, "assets", "soundscapes")

SOUNDSCAPE_PRESETS = {
    "rain": {
        "name": "Дождь за окном",
        "emoji": "🌧️",
        "filename": "rain.mp3",
    },
    "fireplace": {
        "name": "Уютный камин",
        "emoji": "🔥",
        "filename": "fireplace.mp3",
    },
    "ocean": {
        "name": "Шум прибоя",
        "emoji": "🌊",
        "filename": "ocean.mp3",
    },
    "bonfire": {
        "name": "Ночной костёр",
        "emoji": "🏕️",
        "filename": "bonfire.mp3",
    },
    "drops": {
        "name": "Лесные капли",
        "emoji": "💧",
        "filename": "drops.mp3",
    },
}


def get_soundscape_path(key: Optional[str]) -> Optional[str]:
    """Возвращает абсолютный путь к файлу атмосферы или None, если пресет не найден."""
    if not key or key not in SOUNDSCAPE_PRESETS:
        return None
    
    preset = SOUNDSCAPE_PRESETS[key]
    path = os.path.join(SOUNDSCAPES_DIR, preset["filename"])
    if os.path.exists(path):
        return path
    
    alt_exts = [".ogg", ".mp3", ".wav"]
    base, _ = os.path.splitext(path)
    for ext in alt_exts:
        alt_path = base + ext
        if os.path.exists(alt_path):
            return alt_path
            
    return None


def build_soundscape_ffmpeg_args(
    music_source: Optional[str] = None,
    soundscape_key: Optional[str] = None,
    soundscape_enabled: bool = True,
    volume_music: float = 1.0,
    volume_scape: float = 0.15,
) -> Tuple[str, str, str]:
    """Генерирует кортеж (source, before_options, options) для discord.FFmpegPCMAudio.

    :param music_source: Путь к файлу или URL основного музыкального трека.
    :param soundscape_key: Ключ выбранного пресета атмосферы ('rain', 'fireplace' etc.).
    :param soundscape_enabled: Глобальный тумблер гильдии в БД.
    :param volume_music: Громкость музыки (0.0 .. 1.0).
    :param volume_scape: Громкость фоновой атмосферы (0.0 .. 1.0).
    :return: (source_path, before_options, options)
    """
    scape_path = get_soundscape_path(soundscape_key) if soundscape_enabled else None
    
    # Безопасная граница громкости (0.0 .. 1.0)
    v_music = max(0.0, min(1.0, float(volume_music)))
    v_scape = max(0.0, min(1.0, float(volume_scape)))

    # Сценарий 1: Фоновая Атмосфера (Закольцованный фоновый поток)
    if soundscape_key and scape_path:
        before_options = "-stream_loop -1"
        filter_complex = f"[0:a]volume={v_scape:.2f}[out]"
        options = f"-vn -sn -dn -threads 1 -filter_complex \"{filter_complex}\" -map \"[out]\""
        return scape_path, before_options, options

    # Сценарий 2: Музыкальный трек
    if music_source:
        before_options = ""
        options = "-vn -sn -dn -threads 1"
        return music_source, before_options, options

    # Сценарий 3: Ничего не воспроизводится (Тишина)
    before_options = "-f lavfi"
    options = "-vn -sn -dn -threads 1"
    return "anullsrc=channel_layout=stereo:sample_rate=48000", before_options, options
