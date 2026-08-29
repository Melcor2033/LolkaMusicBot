import pytest
from unittest.mock import AsyncMock, Mock
import asyncio
import lolka as discord

@pytest.fixture
def mock_interaction(mocker):
    interaction = mocker.AsyncMock(spec=discord.Interaction)
    
    # User mock
    interaction.user = mocker.Mock(spec=discord.Member)
    interaction.user.id = 12345
    interaction.user.name = "TestUser"
    
    # Guild mock
    interaction.guild = mocker.Mock(spec=discord.Guild)
    interaction.guild.id = 67890
    interaction.guild.name = "TestGuild"
    
    # Channel mock
    interaction.channel = mocker.Mock(spec=discord.TextChannel)
    interaction.channel.id = 11111
    
    # Response and Followup mocks
    interaction.response = mocker.AsyncMock()
    interaction.followup = mocker.AsyncMock()
    
    # Guild ID property shortcut
    interaction.guild_id = 67890
    
    # Simulate not being deferred by default
    interaction.response.is_done.return_value = False
    
    return interaction


@pytest.fixture
def mock_voice_client(mocker):
    client = mocker.AsyncMock(spec=discord.VoiceClient)
    client.is_connected.return_value = True
    client.is_playing.return_value = False
    client.is_paused.return_value = False
    client.channel = mocker.Mock(spec=discord.VoiceChannel)
    client.channel.id = 22222
    client.channel.name = "TestVoiceChannel"
    client.channel.members = []
    return client


@pytest.fixture
def mock_guild(mocker):
    guild = mocker.Mock(spec=discord.Guild)
    guild.id = 67890
    guild.name = "TestGuild"
    guild.voice_client = None
    return guild


@pytest.fixture(autouse=True)
def disable_discord_webhook_requests(monkeypatch, request):
    """Отключает отправку реальных логов в Discord Webhook во время прогона unit-тестов."""
    if "test_logger_webhook.py" in request.node.fspath.strpath:
        return
    from utils.logger_webhook import DiscordWebhookHandler
    monkeypatch.setattr(DiscordWebhookHandler, "_send_webhook", lambda self, levelname, logger_name, message: None)


