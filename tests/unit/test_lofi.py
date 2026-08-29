import pytest
import aiohttp
import asyncio
from unittest.mock import AsyncMock, MagicMock
from cogs.lofi import validate_stream_url, _build_player_embed, is_bot_busy_in_other_channel
from lofi_streams import STATIONS
import lolka as discord

@pytest.fixture
def mock_session(mocker):
    mock_sess = AsyncMock()
    mock_sess.__aenter__.return_value = mock_sess
    
    # get must be a synchronous mock returning a context manager
    mock_sess.get = MagicMock()
    
    mocker.patch("aiohttp.ClientSession", return_value=mock_sess)
    return mock_sess

@pytest.mark.asyncio
async def test_validate_stream_url_invalid_url():
    res, err = await validate_stream_url("not_a_url")
    assert res is False
    assert "URL должен начинаться с" in err

    res, err = await validate_stream_url("")
    assert res is False
    assert "URL должен начинаться с" in err

@pytest.mark.asyncio
async def test_validate_stream_url_success(mock_session):
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.headers = {"Content-Type": "audio/mpeg"}
    mock_resp.close = MagicMock()
    
    mock_get = MagicMock()
    mock_get.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_session.get.return_value = mock_get
    
    res, err = await validate_stream_url("http://test.com/stream")
    assert res is True
    assert err == ""

@pytest.mark.asyncio
async def test_validate_stream_url_status_error(mock_session):
    mock_resp = MagicMock()
    mock_resp.status = 404
    mock_resp.headers = {}
    mock_resp.close = MagicMock()
    
    mock_get = MagicMock()
    mock_get.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_session.get.return_value = mock_get
    
    res, err = await validate_stream_url("http://test.com/stream")
    assert res is False
    assert "Сервер вернул статус 404" in err

@pytest.mark.asyncio
async def test_validate_stream_url_html_error(mock_session):
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.headers = {"Content-Type": "text/html"}
    mock_resp.close = MagicMock()
    
    mock_get = MagicMock()
    mock_get.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_session.get.return_value = mock_get
    
    res, err = await validate_stream_url("http://test.com/stream")
    assert res is False
    assert "Сервер вернул HTML-страницу" in err

@pytest.mark.asyncio
async def test_validate_stream_url_timeout(mock_session):
    mock_session.get.side_effect = asyncio.TimeoutError()
    
    res, err = await validate_stream_url("http://test.com/stream")
    assert res is False
    assert "Сервер не ответил" in err

@pytest.mark.asyncio
async def test_validate_stream_url_client_error(mock_session):
    mock_session.get.side_effect = aiohttp.ClientError("connection refused")
    
    res, err = await validate_stream_url("http://test.com/stream")
    assert res is False
    assert "Ошибка подключения" in err

@pytest.mark.asyncio
async def test_validate_stream_url_generic_exception(mock_session):
    mock_session.get.side_effect = Exception("something bad")
    
    res, err = await validate_stream_url("http://test.com/stream")
    assert res is False
    assert "Неизвестная ошибка" in err

def test_build_player_embed():
    station = STATIONS[0]
    embed = _build_player_embed(station, 0.5, connected=True)
    assert embed.title == f"🎵 Lofi Radio — {station.name}"
    assert embed.fields[0].name == "📻 Статус"
    assert embed.fields[1].value == station.genre
    assert embed.fields[2].value == "50%"

    embed2 = _build_player_embed(station, 0.25, connected=False)
    assert embed2.fields[0].value == "🔴 Оффлайн"
    assert embed2.fields[2].value == "25%"

def test_is_bot_busy_in_other_channel(mocker):
    # Case 1: no voice client
    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.guild.voice_client = None
    assert is_bot_busy_in_other_channel(interaction) is False

    # Case 2: voice client present but not in channel
    vc = MagicMock()
    vc.channel = None
    interaction.guild.voice_client = vc
    assert is_bot_busy_in_other_channel(interaction) is False
