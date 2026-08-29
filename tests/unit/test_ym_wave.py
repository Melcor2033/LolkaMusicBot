import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
import lolka as discord
from cogs.yandex_music import YandexMusic
from utils.queue_manager import QueueManager

@pytest.mark.asyncio
async def test_refill_wave_logic(mocker):
    # Setup mocks
    bot = mocker.Mock()
    cog = YandexMusic(bot)
    guild_id = 123
    
    state = cog.get_state(guild_id)
    state["station_id"] = "user:onyourwave"
    state["source"] = "wave"
    
    # Pre-populate tracks to test queue_param extraction
    state["tracks"] = [
        {"id": "track_1", "title": "Track 1", "file": "some_path"},
        {"id": "track_2", "title": "Track 2", "file": "some_path"}
    ]
    
    # Mock yandex music client
    mock_client = mocker.AsyncMock()
    mocker.patch.object(cog, "get_ym_client", return_value=mock_client)
    
    # Mock rotor_station_tracks result
    mock_result = mocker.MagicMock()
    mock_result.batch_id = "batch_123"
    
    # Prepare sequence of 3 tracks:
    # 1. track_2 (already in queue, but let's test if played_ids filters it if added to played_ids)
    # 2. track_3 (new)
    # 3. track_4 (new)
    seq_1 = mocker.MagicMock()
    seq_1.track = mocker.MagicMock(id="track_2")
    seq_2 = mocker.MagicMock()
    seq_2.track = mocker.MagicMock(id="track_3")
    seq_3 = mocker.MagicMock()
    seq_3.track = mocker.MagicMock(id="track_4")
    
    mock_result.sequence = [seq_1, seq_2, seq_3]
    mock_client.rotor_station_tracks.return_value = mock_result
    
    # Add track_2 to played_ids to trigger duplicate filter
    state["played_ids"] = {"track_1", "track_2"}
    
    # Mock queue_track
    mock_queue_track = mocker.patch.object(cog, "queue_track", new_callable=AsyncMock, return_value=True)
    
    # Run refill_wave
    res = await cog.refill_wave(guild_id)
    await asyncio.sleep(0.01)

    # Verify
    assert res is True
    # Should call rotor_station_tracks with last track ID ("track_2") as queue
    mock_client.rotor_station_tracks.assert_called_once_with("user:onyourwave", queue="track_2")
    
    # queue_track should only be called for track_3 and track_4 (since track_2 is in played_ids)
    assert mock_queue_track.call_count == 2
    mock_queue_track.assert_any_call(guild_id, seq_2.track, "wave", "user:onyourwave", "batch_123")
    mock_queue_track.assert_any_call(guild_id, seq_3.track, "wave", "user:onyourwave", "batch_123")
    
    # played_ids should be updated
    assert "track_3" in state["played_ids"]
    assert "track_4" in state["played_ids"]

@pytest.mark.asyncio
async def test_refill_wave_played_ids_trimming(mocker):
    bot = mocker.Mock()
    cog = YandexMusic(bot)
    guild_id = 123
    
    state = cog.get_state(guild_id)
    state["station_id"] = "user:onyourwave"
    state["source"] = "wave"
    state["tracks"] = [{"id": "track_current", "title": "Current", "file": "path"}]
    
    # Fill played_ids to exceed 200
    state["played_ids"] = {f"old_track_{i}" for i in range(250)}
    
    mock_client = mocker.AsyncMock()
    mocker.patch.object(cog, "get_ym_client", return_value=mock_client)
    
    mock_result = mocker.MagicMock()
    mock_result.batch_id = "batch_123"
    seq = mocker.MagicMock()
    seq.track = mocker.MagicMock(id="new_track")
    mock_result.sequence = [seq]
    mock_client.rotor_station_tracks.return_value = mock_result
    
    mocker.patch.object(cog, "queue_track", new_callable=AsyncMock, return_value=True)
    
    # Run
    await cog.refill_wave(guild_id)
    
    # verify trimming: played_ids should be trimmed to active track ids (track_current)
    # because queue_track is mocked and doesn't actually append to state["tracks"]
    assert len(state["played_ids"]) == 1
    assert "track_current" in state["played_ids"]

@pytest.mark.asyncio
async def test_start_wave_sends_radio_started_feedback(mocker):
    bot = mocker.Mock()
    cog = YandexMusic(bot)
    
    # Mock Interaction
    interaction = mocker.MagicMock(spec=discord.Interaction)
    interaction.guild_id = 123
    interaction.channel = mocker.MagicMock()
    interaction.channel.send = mocker.AsyncMock()
    interaction.original_response = mocker.AsyncMock()
    
    # Mock response
    interaction.response = mocker.MagicMock()
    interaction.response.is_done = mocker.Mock(return_value=True)
    interaction.response.defer = mocker.AsyncMock()
    
    # Mock connection, client, refill_wave, and permissions check
    mocker.patch.object(cog, "_check_interaction_permissions", return_value=True)
    mocker.patch.object(cog, "ensure_connection", new_callable=AsyncMock)
    mock_client = mocker.AsyncMock()
    mocker.patch.object(cog, "get_ym_client", return_value=mock_client)
    mocker.patch.object(cog, "refill_wave", new_callable=AsyncMock, return_value=True)
    mocker.patch.object(cog, "play_track", new_callable=AsyncMock)
    
    # Setup state
    state = cog.get_state(123)
    state["tracks"] = [{"id": "track_1"}]
    
    # Run
    await cog.start_wave(interaction)
    
    # Verify radio started feedback is called
    mock_client.rotor_station_feedback_radio_started.assert_called_once_with(
        station="user:onyourwave",
        from_="web-radio-user-onyourwave"
    )


@pytest.mark.asyncio
async def test_ym_wave_command_defers_immediately(mocker):
    bot = mocker.Mock()
    cog = YandexMusic(bot)

    interaction = mocker.MagicMock(spec=discord.Interaction)
    interaction.guild_id = 123
    interaction.response = mocker.MagicMock()
    interaction.response.is_done = mocker.Mock(return_value=False)
    interaction.response.defer = mocker.AsyncMock()

    mocker.patch.object(cog, "_check_interaction_permissions", return_value=True)
    mocker.patch.object(cog, "start_wave", new_callable=AsyncMock)

    await cog.ym_wave.callback(cog, interaction)

    interaction.response.defer.assert_called_once()
    cog.start_wave.assert_called_once_with(interaction)


