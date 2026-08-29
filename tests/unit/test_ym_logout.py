import pytest
from unittest.mock import AsyncMock, MagicMock
import lolka as discord
from cogs.yandex_music import YandexMusic
import db

@pytest.mark.asyncio
async def test_logout_on_disconnect_24_7_empty_channel(mocker):
    # Setup mocks
    bot = mocker.Mock()
    cog = YandexMusic(bot)
    
    guild_id = 12345
    channel_id = 67890
    
    # Mock voice client
    mock_vc = mocker.Mock(spec=discord.VoiceClient)
    mock_vc.channel = mocker.Mock(spec=discord.VoiceChannel)
    mock_vc.channel.id = channel_id
    mock_vc.channel.members = [mocker.Mock(bot=True)]  # Only bot left
    mock_vc.is_connected = mocker.Mock(return_value=True)
    
    cog._voice_clients[guild_id] = mock_vc
    
    # Mock db settings
    mocker.patch("db.get_ym_settings", return_value={
        "keep_alive": True,
        "logout_on_disconnect": True
    })
    mock_delete_ym_config = mocker.patch("db.delete_ym_config", new_callable=AsyncMock)
    
    # Mock cog methods
    mock_reset_state = mocker.patch.object(cog, "reset_state")
    mock_stop_and_cleanup = mocker.patch.object(cog, "_stop_and_cleanup", new_callable=AsyncMock)
    
    # Mock members and voice state
    member = mocker.Mock(spec=discord.Member)
    member.guild = mocker.Mock(spec=discord.Guild)
    member.guild.id = guild_id
    member.id = 99999  # Not the bot
    
    before = mocker.Mock(spec=discord.VoiceState)
    before.channel = mock_vc.channel
    
    after = mocker.Mock(spec=discord.VoiceState)
    after.channel = None
    
    # Execute
    await cog.on_voice_state_update(member, before, after)
    
    # Verify
    mock_delete_ym_config.assert_called_once_with(guild_id)
    mock_reset_state.assert_called_once_with(guild_id)
    mock_stop_and_cleanup.assert_called_once_with(guild_id, None, disconnect=False)

@pytest.mark.asyncio
async def test_logout_on_disconnect_24_7_not_empty(mocker):
    # Setup mocks
    bot = mocker.Mock()
    cog = YandexMusic(bot)
    
    guild_id = 12345
    channel_id = 67890
    
    # Mock voice client
    mock_vc = mocker.Mock(spec=discord.VoiceClient)
    mock_vc.channel = mocker.Mock(spec=discord.VoiceChannel)
    mock_vc.channel.id = channel_id
    # One bot and one human still in the channel
    mock_vc.channel.members = [mocker.Mock(bot=True), mocker.Mock(bot=False)]
    mock_vc.is_connected = mocker.Mock(return_value=True)
    
    cog._voice_clients[guild_id] = mock_vc
    
    # Mock db settings
    mocker.patch("db.get_ym_settings", return_value={
        "keep_alive": True,
        "logout_on_disconnect": True
    })
    mock_delete_ym_config = mocker.patch("db.delete_ym_config", new_callable=AsyncMock)
    mock_reset_state = mocker.patch.object(cog, "reset_state")
    
    # Mock members and voice state
    member = mocker.Mock(spec=discord.Member)
    member.guild = mocker.Mock(spec=discord.Guild)
    member.guild.id = guild_id
    member.id = 99999  # Not the bot
    
    before = mocker.Mock(spec=discord.VoiceState)
    before.channel = mock_vc.channel
    
    after = mocker.Mock(spec=discord.VoiceState)
    after.channel = None
    
    # Execute
    await cog.on_voice_state_update(member, before, after)
    
    # Verify: config is NOT deleted because channel is not empty of humans
    mock_delete_ym_config.assert_not_called()
    mock_reset_state.assert_not_called()

@pytest.mark.asyncio
async def test_logout_on_disconnect_disabled(mocker):
    # Setup mocks
    bot = mocker.Mock()
    cog = YandexMusic(bot)
    
    guild_id = 12345
    channel_id = 67890
    
    # Mock voice client
    mock_vc = mocker.Mock(spec=discord.VoiceClient)
    mock_vc.channel = mocker.Mock(spec=discord.VoiceChannel)
    mock_vc.channel.id = channel_id
    mock_vc.channel.members = [mocker.Mock(bot=True)]  # Only bot left
    mock_vc.is_connected = mocker.Mock(return_value=True)
    
    cog._voice_clients[guild_id] = mock_vc
    
    # Mock db settings
    mocker.patch("db.get_ym_settings", return_value={
        "keep_alive": True,
        "logout_on_disconnect": False
    })
    mock_delete_ym_config = mocker.patch("db.delete_ym_config", new_callable=AsyncMock)
    mock_reset_state = mocker.patch.object(cog, "reset_state")
    mock_stop_and_cleanup = mocker.patch.object(cog, "_stop_and_cleanup", new_callable=AsyncMock)
    
    # Mock members and voice state
    member = mocker.Mock(spec=discord.Member)
    member.guild = mocker.Mock(spec=discord.Guild)
    member.guild.id = guild_id
    member.id = 99999  # Not the bot
    
    before = mocker.Mock(spec=discord.VoiceState)
    before.channel = mock_vc.channel
    
    after = mocker.Mock(spec=discord.VoiceState)
    after.channel = None
    
    # Execute
    await cog.on_voice_state_update(member, before, after)
    
    # Verify
    mock_delete_ym_config.assert_not_called()
    mock_reset_state.assert_not_called()
    mock_stop_and_cleanup.assert_called_once_with(guild_id, None, disconnect=False)
