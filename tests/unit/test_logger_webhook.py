from __future__ import annotations

import logging
import urllib.request
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from utils.logger_webhook import DiscordWebhookHandler


@pytest.fixture
def webhook_url():
    return "https://example.com/webhook/test-stub-00000000"


@pytest.fixture
def handler(webhook_url):
    h = DiscordWebhookHandler(webhook_url, cooldown_seconds=60.0)
    yield h
    h.close()


def test_init_with_empty_url():
    h = DiscordWebhookHandler("", cooldown_seconds=60.0)
    record = logging.LogRecord("test", logging.ERROR, "path.py", 1, "Msg", (), None)
    h.emit(record)
    h.close()


def test_sanitize_tokens(handler):
    assert handler._sanitize("") == ""
    text = "Secret y0_AgAAAAA1234567890abcdef and enc:FernetKey123456=="
    sanitized = handler._sanitize(text)
    assert "y0_AgAAAAA" not in sanitized
    assert "[TOKEN_REDACTED]" in sanitized


def test_rate_limiting(handler):
    key = ("test_logger", "Identical message")
    assert not handler._is_rate_limited(key)
    assert handler._is_rate_limited(key)


def test_rate_limiting_in_emit(handler):
    record1 = logging.LogRecord("test_logger", logging.ERROR, "file.py", 10, "Dup message", (), None)
    record2 = logging.LogRecord("test_logger", logging.ERROR, "file.py", 10, "Dup message", (), None)
    
    with patch.object(handler, "_send_webhook") as mock_send:
        handler.emit(record1)
        handler.emit(record2)
        assert mock_send.call_count == 1


def test_rate_limiting_expiration(handler):
    handler.cooldown_seconds = 0.01
    key = ("test_logger", "Expired message")
    assert not handler._is_rate_limited(key)
    import time
    time.sleep(0.02)
    assert not handler._is_rate_limited(key)


@patch("urllib.request.urlopen")
def test_send_short_message_success(mock_urlopen, handler):
    mock_resp = MagicMock()
    mock_resp.read.return_value = b""
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    record = logging.LogRecord("test_logger", logging.ERROR, "file.py", 10, "Short error msg", (), None)
    handler.emit(record)
    handler._executor.shutdown(wait=True)

    assert mock_urlopen.called
    req = mock_urlopen.call_args[0][0]
    assert req.full_url == handler.webhook_url


@patch("urllib.request.urlopen")
def test_send_short_message_exception(mock_urlopen, handler):
    mock_urlopen.side_effect = urllib.error.URLError("Network error")

    record = logging.LogRecord("test_logger", logging.ERROR, "file.py", 10, "Short error msg", (), None)
    handler.emit(record)
    handler._executor.shutdown(wait=True)


@patch("urllib.request.urlopen")
def test_send_long_message_success(mock_urlopen, handler):
    mock_resp = MagicMock()
    mock_resp.read.return_value = b""
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    long_msg = "X" * 2000
    record = logging.LogRecord("test_logger", logging.CRITICAL, "file.py", 10, long_msg, (), None)
    handler.emit(record)
    handler._executor.shutdown(wait=True)

    assert mock_urlopen.called
    req = mock_urlopen.call_args[0][0]
    assert "multipart/form-data" in req.headers.get("Content-type")


@patch("urllib.request.urlopen")
def test_send_long_message_exception(mock_urlopen, handler):
    mock_urlopen.side_effect = Exception("Multipart send failed")

    long_msg = "Y" * 2000
    record = logging.LogRecord("test_logger", logging.CRITICAL, "file.py", 10, long_msg, (), None)
    handler.emit(record)
    handler._executor.shutdown(wait=True)


@patch.object(DiscordWebhookHandler, "handleError")
def test_emit_handle_error_on_exception(mock_handle_error, handler):
    bad_record = logging.LogRecord("test", logging.ERROR, "file.py", 1, "Bad %s %s", (1,), None)
    handler.emit(bad_record)
    assert mock_handle_error.called
