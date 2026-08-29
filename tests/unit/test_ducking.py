import asyncio
import pytest
import unittest
from unittest.mock import MagicMock

from src.patches._voice_impl import SourceAudioTrack, VoiceConnection, SILENCE, FRAME_BYTES


class TestSourceAudioTrackGain(unittest.TestCase):
    def test_instant_set_gain(self):
        track = SourceAudioTrack()
        track.set_gain(0.5, fade_ms=0)
        self.assertEqual(track._current_gain, 0.5)
        self.assertEqual(track._target_gain, 0.5)
        self.assertEqual(track._fade_frames_left, 0)

    def test_smooth_fade_gain(self):
        track = SourceAudioTrack()
        # 100 мс fade = 5 кадров по 20 мс
        track.set_gain(0.5, fade_ms=100)
        self.assertEqual(track._target_gain, 0.5)
        self.assertEqual(track._fade_frames_left, 5)
        self.assertAlmostEqual(track._fade_step, -0.1, places=3)

    def test_apply_gain_silence(self):
        data = SILENCE
        result = SourceAudioTrack._apply_gain(data, 0.5)
        self.assertEqual(result, SILENCE)

    def test_apply_gain_scaling(self):
        # 4 байта = 1 сэмпл stereo s16le (left=1000, right=-1000)
        import struct
        pcm = struct.pack('<hh', 1000, -1000)
        scaled = SourceAudioTrack._apply_gain(pcm, 0.5)
        left, right = struct.unpack('<hh', scaled)
        self.assertEqual(left, 500)
        self.assertEqual(right, -500)

    def test_apply_gain_zero(self):
        import struct
        pcm = struct.pack('<hh', 1000, -1000)
        scaled = SourceAudioTrack._apply_gain(pcm, 0.0)
        self.assertEqual(scaled, SILENCE)


class TestVoiceConnectionDucking(unittest.IsolatedAsyncioTestCase):
    async def test_speaking_event_triggers_ducking(self):
        vc = VoiceConnection(endpoint="ws://localhost", token="test_token")
        vc.out_track = SourceAudioTrack()
        
        # Речь начинается
        vc.handle_speaking_event(user_id=123, speaking=True)
        self.assertIn(123, vc._speaking_users)
        self.assertEqual(vc.out_track._target_gain, 0.35)
        self.assertIsNotNone(vc._watchdog_task)
        
        # Речь заканчивается -> запуск Hysteresis
        vc.handle_speaking_event(user_id=123, speaking=False)
        self.assertNotIn(123, vc._speaking_users)
        self.assertIsNotNone(vc._recovery_task)
        
        # Ждем выполнения Hysteresis recovery
        await asyncio.sleep(1.1)
        self.assertEqual(vc.out_track._target_gain, 1.0)
        
        await vc.close()

    async def test_watchdog_timeout_resets_ducking(self):
        vc = VoiceConnection(endpoint="ws://localhost", token="test_token")
        vc.out_track = SourceAudioTrack()
        
        # Искусственно ставим таймаут watchdog для быстрого теста
        vc.handle_speaking_event(user_id=456, speaking=True)
        self.assertEqual(vc.out_track._target_gain, 0.35)
        
        # Ждем срабатывания Watchdog (в реал коде 5 сек)
        # Симулируем таймаут вручную
        await vc._ducking_watchdog_loop()
        self.assertEqual(len(vc._speaking_users), 0)
        self.assertEqual(vc.out_track._target_gain, 1.0)
        
        await vc.close()

    async def test_multiple_speakers_ducking(self):
        vc = VoiceConnection(endpoint="ws://localhost", token="test_token")
        vc.out_track = SourceAudioTrack()
        
        # Говорит юзер 1
        vc.handle_speaking_event(user_id=1, speaking=True)
        self.assertEqual(len(vc._speaking_users), 1)
        
        # Говорит юзер 2
        vc.handle_speaking_event(user_id=2, speaking=True)
        self.assertEqual(len(vc._speaking_users), 2)
        
        # Юзер 1 замолчал (но юзер 2 всё ещё говорит)
        vc.handle_speaking_event(user_id=1, speaking=False)
        self.assertEqual(len(vc._speaking_users), 1)
        # Громкость должна всё ещё оставаться приглушённой!
        self.assertEqual(vc.out_track._target_gain, 0.35)
        
        # Юзер 2 замолчал
        vc.handle_speaking_event(user_id=2, speaking=False)
        self.assertEqual(len(vc._speaking_users), 0)
        
        await asyncio.sleep(1.1)
        self.assertEqual(vc.out_track._target_gain, 1.0)
        
        await vc.close()

    async def test_ducking_disabled_ignores_speaking(self):
        vc = VoiceConnection(endpoint="ws://localhost", token="test_token")
        vc.out_track = SourceAudioTrack()
        vc._ducking_enabled = False

        # Говорит юзер
        vc.handle_speaking_event(user_id=999, speaking=True)
        self.assertEqual(len(vc._speaking_users), 0)
        self.assertEqual(vc.out_track._target_gain, 1.0)

        await vc.close()

    async def test_on_notification_peer_talking(self):
        vc = VoiceConnection(endpoint="ws://localhost", token="test_token")
        vc.out_track = SourceAudioTrack()

        # Нотификация peerTalking
        await vc._on_notification('peerTalking', {'userId': 777})
        self.assertIn(777, vc._speaking_users)
        self.assertEqual(vc.out_track._target_gain, 0.35)

        # Нотификация peerStoppedTalking
        await vc._on_notification('peerStoppedTalking', {'userId': 777})
        self.assertNotIn(777, vc._speaking_users)

        await vc.close()


@pytest.mark.asyncio
async def test_db_ducking_config_methods(mocker):
    import db
    mock_conn = mocker.AsyncMock()
    mock_conn.fetchrow.return_value = {"ducking_enabled": False, "ducking_level": 0.35}
    mock_conn.execute = mocker.AsyncMock()
    
    # Mock async context manager for db_connection
    class MockConnectionContext:
        async def __aenter__(self):
            return mock_conn
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
            
    mocker.patch("db.pool", True)
    mocker.patch("db.db_connection", return_value=MockConnectionContext())
    
    cfg = await db.get_ducking_config(12345)
    assert cfg["ducking_enabled"] is False
    assert cfg["ducking_level"] == 0.35

    await db.update_ducking_config(12345, enabled=True, level=0.4)
    assert mock_conn.execute.called


async def test_multitenant_ducking_isolation():
    # Создаем 2 независимых подключения для Сервера A (111) и Сервера B (222)
    vc_server_a = VoiceConnection(endpoint="ws://localhost", token="token_a", guild_id=111)
    vc_server_a.out_track = SourceAudioTrack()
    vc_server_a._ducking_enabled = True

    vc_server_b = VoiceConnection(endpoint="ws://localhost", token="token_b", guild_id=222)
    vc_server_b.out_track = SourceAudioTrack()
    vc_server_b._ducking_enabled = False  # На сервере B выключено

    # Речь на Сервере A -> громкость A снижается до 35%
    vc_server_a.handle_speaking_event(user_id=101, speaking=True)
    assert vc_server_a.out_track._target_gain == 0.35

    # Речь на Сервере B -> громкость B остается 100%!
    vc_server_b.handle_speaking_event(user_id=202, speaking=True)
    assert vc_server_b.out_track._target_gain == 1.0

    # Проверяем, что списки говорящих изолированы
    assert 101 in vc_server_a._speaking_users
    assert 101 not in vc_server_b._speaking_users
    assert 202 not in vc_server_a._speaking_users
    assert 202 not in vc_server_b._speaking_users

    await vc_server_a.close()
    await vc_server_b.close()


if __name__ == "__main__":
    unittest.main()
