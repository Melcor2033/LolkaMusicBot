"""Unit tests for Phase 2: Soundscapes (Фоновые Атмосферы).
"""

import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.utils.soundscapes import (
    SOUNDSCAPE_PRESETS,
    get_soundscape_path,
    build_soundscape_ffmpeg_args,
)
import src.db as db
from src.views.ui import SoundscapeSelect, MusicSettingsMainView


def test_soundscape_presets_definition():
    """Проверка определения пресетов атмосфер."""
    expected_keys = {"rain", "fireplace", "ocean", "bonfire", "drops"}
    assert set(SOUNDSCAPE_PRESETS.keys()) == expected_keys
    for key, preset in SOUNDSCAPE_PRESETS.items():
        assert "name" in preset
        assert "emoji" in preset
        assert "filename" in preset


def test_get_soundscape_path():
    """Проверка получения пути к файлу атмосферы."""
    # Существующий ключ
    path = get_soundscape_path("rain")
    assert path is not None
    assert os.path.exists(path)

    # Несуществующий ключ или None
    assert get_soundscape_path("non_existent_preset") is None
    assert get_soundscape_path(None) is None


def test_build_soundscape_ffmpeg_args_standalone():
    """Сценарий 1: Фоновая Атмосфера (Закольцованный автономный поток)."""
    src, before_opts, opts = build_soundscape_ffmpeg_args(
        music_source=None,
        soundscape_key="rain",
        soundscape_enabled=True,
        volume_scape=0.15,
    )
    assert src is not None and "rain" in src
    assert before_opts == "-stream_loop -1"
    assert "amix" not in opts
    assert "volume=0.15" in opts
    assert "-threads 1" in opts
    assert "-map \"[out]\"" in opts


def test_build_soundscape_ffmpeg_args_soundscape_only():
    """Сценарий 2: Только Атмосфера (Без музыки, зацикленный автономный поток)."""
    src, before_opts, opts = build_soundscape_ffmpeg_args(
        music_source=None,
        soundscape_key="fireplace",
        soundscape_enabled=True,
        volume_scape=0.4,
    )
    assert src is not None and "fireplace" in src
    assert before_opts == "-stream_loop -1"
    assert "amix" not in opts
    assert "volume=0.40" in opts
    assert "-threads 1" in opts


def test_build_soundscape_ffmpeg_args_music_only():
    """Сценарий 3: Только Музыка (Атмосфера выключена)."""
    src, before_opts, opts = build_soundscape_ffmpeg_args(
        music_source="http://example.com/song.mp3",
        soundscape_key="rain",
        soundscape_enabled=False,
    )
    assert src == "http://example.com/song.mp3"
    assert before_opts == ""
    assert "amix" not in opts
    assert "-threads 1" in opts


def test_build_soundscape_ffmpeg_args_silence_fallback():
    """Сценарий 4: Ничего не воспроизводится (Тишина)."""
    src, before_opts, opts = build_soundscape_ffmpeg_args(
        music_source=None,
        soundscape_key=None,
        soundscape_enabled=True,
    )
    assert "anullsrc" in src
    assert before_opts == "-f lavfi"
    assert "-threads 1" in opts


@pytest.mark.asyncio
async def test_db_soundscapes_config_fallback():
    """Тестирование функции get_soundscapes_config при отсутствии подключения к БД."""
    with patch.object(db, "pool", None):
        cfg = await db.get_soundscapes_config(12345)
        assert cfg == {"soundscapes_enabled": True}


@pytest.mark.asyncio
async def test_db_soundscapes_config_get_and_update():
    """Тестирование get_soundscapes_config и update_soundscapes_config с мок-подключением БД."""
    mock_conn = AsyncMock()
    mock_conn.fetchrow.return_value = {"soundscapes_enabled": False}

    mock_pool = MagicMock()
    
    class AsyncContextManager:
        async def __aenter__(self):
            return mock_conn
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    mock_pool.acquire.return_value = AsyncContextManager()

    with patch.object(db, "pool", mock_pool):
        cfg = await db.get_soundscapes_config(12345)
        assert cfg["soundscapes_enabled"] is False

        await db.update_soundscapes_config(12345, enabled=True)
        mock_conn.execute.assert_called_once()
        args = mock_conn.execute.call_args[0]
        assert "UPDATE voice_config" in args[0]
        assert args[1] == 12345
        assert args[2] is True


def test_soundscape_select_ui_initialization():
    """Тестирование инициализации UI Dropdown меню SoundscapeSelect."""
    select_default = SoundscapeSelect(current_soundscape=None)
    assert len(select_default.options) == 6
    assert select_default.options[0].value == "off"
    assert select_default.options[0].default is True

    select_rain = SoundscapeSelect(current_soundscape="rain")
    assert select_rain.options[1].value == "rain"
    assert select_rain.options[1].default is True


@pytest.mark.asyncio
async def test_soundscape_select_ui_callback():
    """Тестирование срабатывания каллбэка выпадающего списка SoundscapeSelect."""
    select = SoundscapeSelect(current_soundscape=None)
    select._values = ["fireplace"]

    mock_vc = MagicMock()
    mock_vc._is_music_playing = False
    mock_vc.is_playing.return_value = False
    mock_conn = MagicMock()
    mock_vc._conn = mock_conn

    mock_guild = MagicMock()
    mock_guild.voice_client = mock_vc

    mock_interaction = AsyncMock()
    mock_interaction.guild = mock_guild

    await select.callback(mock_interaction)

    assert mock_conn._current_soundscape == "fireplace"
    mock_interaction.response.send_message.assert_called_once()
    msg = mock_interaction.response.send_message.call_args[0][0]
    assert "Уютный камин" in msg


@pytest.mark.asyncio
async def test_soundscape_select_ui_callback_music_playing():
    """Уведомление при попытке включить атмосферу при играющей музыке."""
    select = SoundscapeSelect(current_soundscape=None)
    select._values = ["rain"]

    mock_vc = MagicMock()
    mock_vc._is_music_playing = True
    mock_guild = MagicMock()
    mock_guild.voice_client = mock_vc

    mock_interaction = AsyncMock()
    mock_interaction.guild = mock_guild

    await select.callback(mock_interaction)

    mock_interaction.response.send_message.assert_called_once()
    msg = mock_interaction.response.send_message.call_args[0][0]
    assert "Фоновые атмосферы работают автономно" in msg


def test_multiserver_soundscape_isolation():
    """Тестирование мультисерверной изоляции настроек атмосфер."""
    from src.patches._voice_impl import VoiceConnection

    vc_server_1 = VoiceConnection(endpoint="ws://localhost", token="t1", guild_id=101)
    vc_server_2 = VoiceConnection(endpoint="ws://localhost", token="t2", guild_id=102)

    vc_server_1._soundscapes_enabled = True
    vc_server_1._current_soundscape = "rain"

    vc_server_2._soundscapes_enabled = False
    vc_server_2._current_soundscape = "ocean"

    assert vc_server_1._soundscapes_enabled is True
    assert vc_server_1._current_soundscape == "rain"

    assert vc_server_2._soundscapes_enabled is False
    assert vc_server_2._current_soundscape == "ocean"
