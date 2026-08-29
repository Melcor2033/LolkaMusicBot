"""Unit tests for DynamicVoice cog (dynamic voice channel creation and welcome messages).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import lolka as discord

from src.cogs.voice import DynamicVoice, render_template


def test_render_template():
    member = MagicMock(spec=discord.Member)
    member.display_name = "Alice"
    member.mention = "<@123>"
    member.guild.name = "Awesome Server"

    result = render_template("Welcome {user_mention} to {server} ({user})!", member, mention=True)
    assert result == "Welcome <@123> to Awesome Server (Alice)!"


@pytest.mark.asyncio
async def test_create_temp_channel_sends_welcome_embed():
    bot = MagicMock()
    cog = DynamicVoice(bot)

    guild = MagicMock()
    guild.id = 12345
    guild.name = "Test Guild"

    member = MagicMock(spec=discord.Member)
    member.id = 999
    member.display_name = "TestUser"
    member.mention = "<@999>"
    member.guild = guild

    category = MagicMock(spec=discord.CategoryChannel)
    category.id = 100
    category.create_voice_channel = AsyncMock()
    guild.get_channel.return_value = category

    new_channel = AsyncMock(spec=discord.VoiceChannel)
    new_channel.id = 202
    category.create_voice_channel.return_value = new_channel

    master_cfg = {
        'category_id': 100,
        'channel_name_template': "📞 │ {user}",
        'embed_title': "Управление комнатой",
        'embed_description': "Привет, {user_mention}!",
        'embed_color': 0xFFD700,
        'mention_user': True,
        'send_welcome': True,
    }

    with patch("src.cogs.voice.db.add_dynamic_channel", new=AsyncMock()), \
         patch("src.cogs.voice.db.get_soundscapes_config", new=AsyncMock(return_value={"soundscapes_enabled": True})):
        
        async def mock_wait_for(event, check, timeout):
            return True

        bot.wait_for = AsyncMock(side_effect=mock_wait_for)

        await cog.create_temp_channel(member, master_id=1, master_cfg=master_cfg)

        new_channel.send.assert_called_once()
        call_kwargs = new_channel.send.call_args.kwargs
        assert "embed" in call_kwargs
        sent_embed = call_kwargs["embed"]
        assert sent_embed.title == "Управление комнатой"
        assert sent_embed.description == "Привет, <@999>!"
