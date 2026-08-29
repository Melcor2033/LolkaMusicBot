import pytest
from unittest.mock import AsyncMock, MagicMock
import lolka as discord

from views.base_player import (
    parse_time_to_seconds,
    format_player_status,
    run_timeline_updater_loop,
    BasePlayerState,
    stop_other_cogs,
    UniversalSeekModal,
    UniversalVolumeModal,
    BasePlayerView
)


def test_base_player_state():
    state = BasePlayerState(guild_id=123)
    assert state.guild_id == 123
    assert state.volume == 0.5
    assert state.is_paused is False
    assert state.get_current_time() == 0


def test_stop_other_cogs():
    bot = MagicMock()
    cog_ym = MagicMock()
    cog_rt = MagicMock()
    bot.get_cog.side_effect = lambda name: cog_ym if name == "YandexMusic" else (cog_rt if name == "RutubeMusic" else None)
    
    stop_other_cogs(bot, 123, "SpotifyMusic")
    cog_ym.reset_state.assert_called_once_with(123)
    cog_rt.reset_state.assert_called_once_with(123)


def test_format_player_status():
    assert format_player_status(is_paused=False, is_live=False) == "▶️ Играет"
    assert format_player_status(is_paused=True, is_live=False) == "⏸️ Пауза"
    assert format_player_status(is_paused=False, is_live=True) == "🔴 В эфире"


def test_parse_time_to_seconds():
    assert parse_time_to_seconds("90") == 90
    assert parse_time_to_seconds("1:30") == 90
    assert parse_time_to_seconds("01:30") == 90
    assert parse_time_to_seconds("1:00:00") == 3600
    assert parse_time_to_seconds("invalid") is None
    assert parse_time_to_seconds("") is None


@pytest.mark.asyncio
async def test_universal_seek_modal_submit_valid():
    cog = MagicMock()
    cog.seek_to = AsyncMock()
    
    modal = UniversalSeekModal(cog=cog)
    modal.time_input = MagicMock()
    modal.time_input.value = "1:30"
    
    interaction = AsyncMock(spec=discord.Interaction)
    await modal.on_submit(interaction)
    
    cog.seek_to.assert_called_once_with(interaction, 90)


@pytest.mark.asyncio
async def test_universal_seek_modal_submit_invalid():
    cog = MagicMock()
    cog.seek_to = AsyncMock()
    
    modal = UniversalSeekModal(cog=cog)
    modal.time_input = MagicMock()
    modal.time_input.value = "abc"
    
    interaction = AsyncMock(spec=discord.Interaction)
    interaction.response.send_message = AsyncMock()
    
    await modal.on_submit(interaction)
    
    cog.seek_to.assert_not_called()
    interaction.response.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_base_player_view_seek_relative():
    cog = MagicMock()
    cog.seek_relative = AsyncMock()
    
    view = BasePlayerView(cog=cog)
    interaction = AsyncMock(spec=discord.Interaction)
    
    await view.handle_seek_relative(interaction, -10)
    cog.seek_relative.assert_called_once_with(interaction, -10)
    
    cog.seek_relative.reset_mock()
    await view.handle_seek_relative(interaction, 10)
    cog.seek_relative.assert_called_once_with(interaction, 10)


@pytest.mark.asyncio
async def test_base_player_view_source_change():
    view = BasePlayerView()
    interaction = AsyncMock(spec=discord.Interaction)
    interaction.response.is_done.return_value = False
    interaction.response.send_message = AsyncMock()
    
    await view.handle_source_change(interaction)
    interaction.response.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_universal_volume_modal_submit_valid():
    cog = MagicMock()
    cog.change_volume = AsyncMock()
    
    modal = UniversalVolumeModal(cog=cog)
    modal.volume_input = MagicMock()
    modal.volume_input.value = "75"
    
    interaction = AsyncMock(spec=discord.Interaction)
    await modal.on_submit(interaction)
    
    cog.change_volume.assert_called_once_with(interaction, 75)


@pytest.mark.asyncio
async def test_run_timeline_updater_loop_cancellation():
    cog = MagicMock()
    cog.bot.wait_until_ready = AsyncMock()
    cog.bot.is_closed.return_value = True
    
    # Должен безопасно завершиться, когда bot.is_closed() -> True
    await run_timeline_updater_loop(cog, interval=0.01)
