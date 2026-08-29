"""
Unit tests for on_guild_join event handler: auto-setting bot nickname on new servers.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import lolka as discord

import config


@pytest.mark.asyncio
async def test_on_guild_join_sets_nickname():
    """Verify that on_guild_join changes nickname on a newly joined guild."""
    from bot import DynamicVoiceBot

    bot = DynamicVoiceBot()

    # Mock guild and me member
    mock_guild = MagicMock(spec=discord.Guild)
    mock_guild.id = 12345
    mock_guild.name = "Test New Server"

    mock_me = AsyncMock()
    mock_guild.me = mock_me

    # Trigger event
    await bot.on_guild_join(mock_guild)

    # Check edit call
    expected_nick = getattr(config, "DEFAULT_BOT_NICKNAME", "Dynamic Voice")
    mock_me.edit.assert_called_once_with(nick=expected_nick)


@pytest.mark.asyncio
async def test_on_guild_join_handles_forbidden():
    """Verify that on_guild_join gracefully handles Forbidden error without raising."""
    from bot import DynamicVoiceBot

    bot = DynamicVoiceBot()

    mock_guild = MagicMock(spec=discord.Guild)
    mock_guild.id = 67890
    mock_guild.name = "No Permission Server"

    mock_me = AsyncMock()
    mock_me.edit.side_effect = discord.Forbidden(MagicMock(), "Cannot edit nick")
    mock_guild.me = mock_me

    # Should not raise exception
    await bot.on_guild_join(mock_guild)
    mock_me.edit.assert_called_once()
