from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock
import pytest
import lolka as discord

from utils.voice_utils import get_user_voice_channel


@pytest.mark.asyncio
async def test_get_user_voice_channel_none_source():
    assert await get_user_voice_channel(None) is None


@pytest.mark.asyncio
async def test_get_user_voice_channel_none_guild():
    user = MagicMock(spec=discord.User)
    user.id = 123
    user.guild = None
    assert await get_user_voice_channel(user, guild=None) is None


@pytest.mark.asyncio
async def test_get_user_voice_channel_interaction_source():
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.id = 123
    interaction.guild = MagicMock(spec=discord.Guild)

    channel = MagicMock(spec=discord.VoiceChannel)
    interaction.user.voice = MagicMock()
    interaction.user.voice.channel = channel

    res = await get_user_voice_channel(interaction)
    assert res == channel


@pytest.mark.asyncio
async def test_get_user_voice_channel_tier1_member_voice():
    member = MagicMock(spec=discord.Member)
    member.id = 123
    guild = MagicMock(spec=discord.Guild)
    member.guild = guild

    channel = MagicMock(spec=discord.VoiceChannel)
    member.voice = MagicMock()
    member.voice.channel = channel

    res = await get_user_voice_channel(member)
    assert res == channel


@pytest.mark.asyncio
async def test_get_user_voice_channel_user_non_member_fetch():
    user = MagicMock(spec=discord.User)
    user.id = 123
    user.guild = None

    guild = MagicMock(spec=discord.Guild)
    fetched_member = MagicMock(spec=discord.Member)
    channel = MagicMock(spec=discord.VoiceChannel)
    fetched_member.voice = MagicMock()
    fetched_member.voice.channel = channel
    guild.get_member.return_value = fetched_member

    res = await get_user_voice_channel(user, guild=guild)
    assert res == channel


@pytest.mark.asyncio
async def test_get_user_voice_channel_tier2_voice_state_for():
    user = MagicMock(spec=discord.Member)
    user.id = 123
    user.voice = None
    guild = MagicMock(spec=discord.Guild)
    user.guild = guild

    channel = MagicMock(spec=discord.VoiceChannel)
    vs = MagicMock()
    vs.channel = channel
    guild._voice_state_for.return_value = vs

    res = await get_user_voice_channel(user)
    assert res == channel


@pytest.mark.asyncio
async def test_get_user_voice_channel_tier2_exception():
    user = MagicMock(spec=discord.Member)
    user.id = 123
    user.voice = None
    guild = MagicMock(spec=discord.Guild)
    user.guild = guild
    guild._voice_state_for.side_effect = Exception("Internal voice state error")
    guild.voice_channels = []

    res = await get_user_voice_channel(user)
    assert res is None


@pytest.mark.asyncio
async def test_get_user_voice_channel_tier3_voice_states_scan():
    user = MagicMock(spec=discord.Member)
    user.id = 123
    user.voice = None
    guild = MagicMock(spec=discord.Guild)
    user.guild = guild
    guild._voice_state_for.return_value = None

    ch1 = MagicMock(spec=discord.VoiceChannel)
    ch1.voice_states = {999: MagicMock()}
    ch1.members = []

    ch2 = MagicMock(spec=discord.VoiceChannel)
    ch2.voice_states = {123: MagicMock()}

    guild.voice_channels = [ch1, ch2]

    res = await get_user_voice_channel(user)
    assert res == ch2


@pytest.mark.asyncio
async def test_get_user_voice_channel_tier3_members_scan():
    user = MagicMock(spec=discord.Member)
    user.id = 123
    user.voice = None
    guild = MagicMock(spec=discord.Guild)
    user.guild = guild
    guild._voice_state_for.return_value = None

    m_other = MagicMock()
    m_other.id = 999

    m_target = MagicMock()
    m_target.id = 123

    ch1 = MagicMock(spec=discord.VoiceChannel)
    del ch1.voice_states  # no voice_states attribute
    ch1.members = [m_other]

    ch2 = MagicMock(spec=discord.VoiceChannel)
    del ch2.voice_states
    ch2.members = [m_target]

    guild.voice_channels = [ch1, ch2]

    res = await get_user_voice_channel(user)
    assert res == ch2


@pytest.mark.asyncio
async def test_get_user_voice_channel_tier3_exception_during_scan():
    user = MagicMock(spec=discord.Member)
    user.id = 123
    user.voice = None
    guild = MagicMock(spec=discord.Guild)
    user.guild = guild
    guild._voice_state_for.return_value = None

    type(guild).voice_channels = PropertyMock(side_effect=Exception("Scan failure"))

    res = await get_user_voice_channel(user)
    assert res is None


@pytest.mark.asyncio
async def test_safe_defer_success():
    from utils.voice_utils import safe_defer
    interaction = MagicMock(spec=discord.Interaction)
    response = MagicMock()
    response.is_done.return_value = False
    
    async def async_defer(**kwargs):
        pass
    
    response.defer = MagicMock(side_effect=async_defer)
    interaction.response = response

    res = await safe_defer(interaction, ephemeral=True)
    assert res is True
    response.defer.assert_called_once_with(ephemeral=True)


@pytest.mark.asyncio
async def test_safe_defer_already_done():
    from utils.voice_utils import safe_defer
    interaction = MagicMock(spec=discord.Interaction)
    response = MagicMock()
    response.is_done.return_value = True
    interaction.response = response

    res = await safe_defer(interaction)
    assert res is False


@pytest.mark.asyncio
async def test_safe_defer_catches_http_exception():
    from utils.voice_utils import safe_defer
    interaction = MagicMock(spec=discord.Interaction)
    response = MagicMock()
    response.is_done.return_value = False
    
    async def failing_defer(**kwargs):
        raise discord.errors.HTTPException(MagicMock(), "Interaction has already been acknowledged")
    
    response.defer = MagicMock(side_effect=failing_defer)
    interaction.response = response

    res = await safe_defer(interaction)
    assert res is False

