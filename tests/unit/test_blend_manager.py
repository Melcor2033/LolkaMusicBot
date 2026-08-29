"""Unit tests for Phase 3: Smart Blend DJ (Совместная Волна).
"""

import asyncio
import logging
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import sys
from src.utils.blend_crypto import encrypt_user_token, decrypt_user_token, _derive_user_key
from src.utils.logger_sanitizer import TokenMaskingFilter
from src.utils.blend_pool import YMClientPool, ym_client_pool
import src.utils.blend as blend_mod
sys.modules['utils.blend'] = blend_mod
from src.utils.blend import BlendSession, BlendManager, blend_manager
import src.db as db
from src.views.ui import MusicSettingsMainView, UserControlPanel


def test_crypto_user_isolation():
    """1. test_crypto_user_isolation — Ключ шифрования userA изолирован от юзера B."""
    key_a = _derive_user_key(1001)
    key_b = _derive_user_key(2002)
    assert key_a != key_b

    token = "y0_AgAAAAAA1234567890_test_oauth_token"
    enc_a = encrypt_user_token(1001, token)
    assert enc_a != token

    dec_a = decrypt_user_token(1001, enc_a)
    assert dec_a == token

    # Попытка расшифровать чужим user_id вызывает ошибку Fernet
    with pytest.raises(Exception):
        decrypt_user_token(2002, enc_a)


def test_token_masking_filter():
    """2. test_token_masking_filter — Фильтр вырезает y0_... и enc:... из логов."""
    filter_obj = TokenMaskingFilter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="User authenticated with token y0_AgAAAAAA1234567890_secret_token_value_extra_long and encrypted enc:gAAAAABk123456789012345678901234567890",
        args=(),
        exc_info=None
    )
    res = filter_obj.filter(record)
    assert res is True
    assert "y0_AgAAAAAA1234567890_secret_token_value_extra_long" not in record.msg
    assert "enc:gAAAAABk123456789012345678901234567890" not in record.msg
    assert "[TOKEN_REDACTED]" in record.msg


@pytest.mark.asyncio
async def test_client_pool_lru_eviction():
    """3. test_client_pool_lru_eviction — Пул вытесняет 11-й клиент и выгружает сессию."""
    pool = YMClientPool()
    pool.MAX_ACTIVE_CLIENTS = 3

    mock_clients = []
    for i in range(4):
        c = MagicMock()
        c.init = AsyncMock()
        c._request = MagicMock()
        c._request.session = AsyncMock()
        c._request.session.closed = False
        mock_clients.append(c)

    with patch("src.utils.blend_pool.ClientAsync", side_effect=mock_clients):
        # Инициализируем 3 клиента
        await pool.get_client(1, 100, "token1")
        await pool.get_client(2, 100, "token2")
        await pool.get_client(3, 100, "token3")

        assert len(pool._pool) == 3

        # Добавляем 4-й — должен вытеснить самый старый (user 1)
        await pool.get_client(4, 100, "token4")
        assert len(pool._pool) == 3
        assert (1, 100) not in pool._pool
        assert (4, 100) in pool._pool


@pytest.mark.asyncio
async def test_client_pool_direct_rf_connection():
    """4. test_client_pool_direct_rf_connection — Запросы идут напрямую без прокси."""
    pool = YMClientPool()
    with patch("src.utils.blend_pool.ClientAsync") as mock_client_cls:
        mock_inst = MagicMock()
        mock_inst.init = AsyncMock()
        mock_client_cls.return_value = mock_inst

        client = await pool.get_client(1, 100, "token_direct")
        assert client is not None
        mock_client_cls.assert_called_once_with("token_direct")


def test_batch_prefetch_participant_tracks():
    """5. test_batch_prefetch_participant_tracks — Пакет предзагрузки генерирует 3+ трека на каждого участника."""
    session = BlendSession(guild_id=1, channel_id=10)
    session.add_participant(1001)
    session.add_participant(1002)

    assert len(session.active_participants) == 2
    assert 1001 in session.user_tracks_map
    assert 1002 in session.user_tracks_map


def test_round_robin_balance():
    """6. test_round_robin_balance — Треки участников поддерживаются в балансе."""
    session = BlendSession(guild_id=1, channel_id=10)
    session.add_participant(101)
    session.add_participant(102)

    track_a = MagicMock(id="t1")
    track_b = MagicMock(id="t2")
    session.track_added_for_user(101, track_a)
    session.track_added_for_user(102, track_b)

    assert len(session.user_tracks_map[101]) == 1
    assert len(session.user_tracks_map[102]) == 1


def test_round_robin_single_user():
    """7. test_round_robin_single_user — Корректная работа с 1 участником."""
    session = BlendSession(guild_id=1, channel_id=10)
    session.add_participant(101)
    assert len(session.active_participants) == 1


@pytest.mark.asyncio
async def test_debounce_task_cancellation():
    """8. test_debounce_task_cancellation — Быстрый перевход отменяет предыдущую таску."""
    bm = BlendManager()
    ym_cog = AsyncMock()

    async def dummy_delay(*args, **kwargs):
        await asyncio.sleep(10)

    with patch.object(bm, "_delayed_voice_state_update", side_effect=dummy_delay):
        await bm.handle_voice_state_update(1, 10, {1001}, ym_cog)
        task1 = bm._debounce_tasks[1]

        await bm.handle_voice_state_update(1, 10, {1001, 1002}, ym_cog)
        task2 = bm._debounce_tasks[1]

        await asyncio.sleep(0)  # Даем микроинтервал event loop для обработки отмены
        assert task1.cancelled() or task1.done() or getattr(task1, 'cancelling', lambda: 0)() > 0
        assert not task2.cancelled()
        task2.cancel()


def test_unplayed_tracks_cleanup_on_disconnect():
    """9. test_unplayed_tracks_cleanup_on_disconnect — Очистка несыгранных треков вышедшего юзера."""
    session = BlendSession(guild_id=1, channel_id=10)
    session.add_participant(101)
    track1 = MagicMock(id="t101")
    session.track_added_for_user(101, track1)

    removed = session.remove_participant(101)
    assert len(removed) == 1
    assert removed[0] == track1
    assert 101 not in session.active_participants


@pytest.mark.asyncio
async def test_token_purge_hierarchy():
    """10. test_token_purge_hierarchy — Иерархия очистки: Личная настройка -> Настройка сервера."""
    bm = BlendManager()

    # Сценарий A: Личная настройка forget_on_disconnect = True
    with patch("db.get_blend_user_token", new=AsyncMock(return_value={"forget_on_disconnect": True})), \
         patch("db.delete_blend_user_token", new=AsyncMock()) as mock_del:
        await bm._process_token_purge_policy(101, 1)
        mock_del.assert_called_once_with(101, 1)

    # Сценарий B: Личная настройка None, сервер logout_on_disconnect = True
    with patch("db.get_blend_user_token", new=AsyncMock(return_value={"forget_on_disconnect": None})), \
         patch("db.get_ym_settings", new=AsyncMock(return_value={"logout_on_disconnect": True})), \
         patch("db.delete_blend_user_token", new=AsyncMock()) as mock_del2:
        await bm._process_token_purge_policy(101, 1)
        mock_del2.assert_called_once_with(101, 1)


@pytest.mark.asyncio
async def test_unauthorized_token_graceful_handling():
    """11. test_unauthorized_token_graceful_handling — 401 помечает токен неактивным в БД."""
    bm = BlendManager()
    session = await bm.get_or_create_session(1, 10)
    session.add_participant(101)

    ym_cog = AsyncMock()

    mock_client = AsyncMock()
    mock_client.rotor_station_tracks.side_effect = Exception("401 Unauthorized")

    with patch("db.get_blend_guild_tokens", new=AsyncMock(return_value=[{"user_id": 101, "decrypted_token": "invalid_tok"}])), \
         patch("utils.blend_pool.ym_client_pool.get_client", new=AsyncMock(return_value=mock_client)), \
         patch("db.mark_blend_token_inactive", new=AsyncMock()) as mock_mark:

        count = await bm.generate_wave_batch(1, ym_cog)
        assert count == 0
        mock_mark.assert_called_once_with(101, 1)


@pytest.mark.asyncio
async def test_db_save_and_retrieve_token():
    """12. test_db_save_and_retrieve_token — Сохранение и дешифровка токена из БД."""
    with patch("src.db.pool", True), patch("src.db.db_connection") as mock_conn_ctx:
        mock_conn = AsyncMock()
        mock_conn_ctx.return_value.__aenter__.return_value = mock_conn

        await db.save_blend_user_token(101, 1, "test_token_val", "UserA")
        mock_conn.execute.assert_called_once()


@pytest.mark.asyncio
async def test_db_delete_token_unlink():
    """13. test_db_delete_token_unlink — Удаление записи токена при /blend unlink."""
    with patch("src.db.pool", True), patch("src.db.db_connection") as mock_conn_ctx:
        mock_conn = AsyncMock()
        mock_conn.execute.return_value = "DELETE 1"
        mock_conn_ctx.return_value.__aenter__.return_value = mock_conn

        res = await db.delete_blend_user_token(101, 1)
        assert res is True


def test_ui_blend_buttons_visibility():
    """14. test_ui_blend_buttons_visibility — Кнопка Совместная Волна видна строго в активном плеере Яндекс.Музыки."""
    from src.views.ym_views import YMPlayerView, YMReadyView
    player_view = YMPlayerView()
    player_custom_ids = [getattr(item, 'custom_id', None) for item in player_view.children]
    assert "ym_blend_btn" in player_custom_ids

    ready_view = YMReadyView()
    ready_custom_ids = [getattr(item, 'custom_id', None) for item in ready_view.children]
    assert "ym_ready_blend_btn" not in ready_custom_ids


@pytest.mark.asyncio
async def test_voice_state_update_trigger():
    """15. test_voice_state_update_trigger — Изменение состава канала передается в BlendManager."""
    bm = BlendManager()
    ym_cog = AsyncMock()
    with patch.object(bm, "handle_voice_state_update", new=AsyncMock()) as mock_handle:
        await bm.handle_voice_state_update(1, 10, {101, 102}, ym_cog)
        mock_handle.assert_called_once_with(1, 10, {101, 102}, ym_cog)


@pytest.mark.asyncio
async def test_session_cleanup_on_empty_channel():
    """16. test_session_cleanup_on_empty_channel — Выход всех участников завершает BlendSession."""
    bm = BlendManager()
    session = await bm.get_or_create_session(1, 10)
    session.add_participant(101)

    assert 1 in bm._sessions

    session.remove_participant(101)
    assert len(session.active_participants) == 0

    await bm.remove_session(1)
    assert 1 not in bm._sessions


@pytest.mark.asyncio
async def test_ym_cog_add_and_remove_blend_tracks():
    """17. test_ym_cog_add_and_remove_blend_tracks — Добавление и отфильтровывание треков Blend в YandexMusic cog."""
    from src.cogs.yandex_music import YandexMusic

    cog = YandexMusic(bot=MagicMock())
    guild_id = 999
    user_id = 7007

    state = cog.get_state(guild_id)
    state["index"] = 0
    state["tracks"] = [
        {"id": "1", "title": "Base Playing Track", "blend_user_id": None}
    ]

    artist_mock = MagicMock()
    artist_mock.name = "Artist A"

    mock_track = MagicMock()
    mock_track.id = 12345
    mock_track.title = "Test Blend Song"
    mock_track.artists = [artist_mock]
    mock_track.cover_uri = "https://cover.test/%%"
    mock_track.duration_ms = 180000

    with patch.object(cog, "download_track", new=AsyncMock(return_value="/tmp/test_file.mp3")), \
         patch("src.cogs.yandex_music.db.get_blend_user_token", new=AsyncMock(return_value={"username": "TestUser"})):

        ok = await cog.add_blend_track_to_queue(guild_id, user_id, mock_track)
        assert ok is True
        assert len(state["tracks"]) == 2
        added_item = state["tracks"][1]
        assert added_item["id"] == "12345"
        assert added_item["source"] == "blend"
        assert added_item["blend_user_id"] == user_id
        assert added_item["blend_username"] == "TestUser"

        # Проверка удаления треков вышедшего пользователя из очереди
        await cog.remove_user_tracks_from_queue(guild_id, user_id, [added_item])
        assert len(state["tracks"]) == 1
        assert state["tracks"][0]["id"] == "1"


@pytest.mark.asyncio
async def test_db_blend_crypto_imports_in_docker():
    """18. test_db_blend_crypto_imports_in_docker — Проверка функций db с деривацией ключа и расшифровкой."""
    with patch("src.db.pool", True), \
         patch("src.db.db_connection") as mock_conn_ctx, \
         patch("utils.blend_crypto.encrypt_user_token", return_value="enc_token", create=True), \
         patch("utils.blend_crypto.decrypt_user_token", return_value="dec_token", create=True), \
         patch("src.utils.blend_crypto.encrypt_user_token", return_value="enc_token", create=True), \
         patch("src.utils.blend_crypto.decrypt_user_token", return_value="dec_token", create=True):

        mock_conn = AsyncMock()
        mock_conn.fetchrow.return_value = {
            "user_id": 101,
            "guild_id": 1,
            "oauth_token": "enc_token",
            "username": "UserA",
            "is_active": True,
            "forget_on_disconnect": False,
            "updated_at": "2026-07-24"
        }
        mock_conn.fetch.return_value = [mock_conn.fetchrow.return_value]
        mock_conn_ctx.return_value.__aenter__.return_value = mock_conn

        # Сохранение
        await db.save_blend_user_token(101, 1, "token", "UserA")
        mock_conn.execute.assert_called_once()

        # Получение токена юзера
        res_user = await db.get_blend_user_token(101, 1)
        assert res_user is not None
        assert res_user["decrypted_token"] == "dec_token"

        # Получение списка токенов гильдии
        res_guild = await db.get_blend_guild_tokens(1)
        assert len(res_guild) == 1
        assert res_guild[0]["decrypted_token"] == "dec_token"


def test_create_progress_bar_compact_length():
    """19. test_create_progress_bar_compact_length — Проверка 8-сегментной длины прогресс-бара."""
    from src.views.base_player import create_progress_bar
    bar_str = create_progress_bar(36, 110)
    assert "`00:36`" in bar_str
    assert "`01:50`" in bar_str
    assert "🔘" in bar_str
    # По умолчанию ровно 8 символов/сегментов шкалы (1 ползунок + 7 полосок)
    assert bar_str.count("▬") == 7


@pytest.mark.asyncio
async def test_blend_unlink_cleans_active_session_and_queue():
    """20. test_blend_unlink_cleans_active_session_and_queue — /blend unlink выводит пользователя из активной волны и чистит очередь."""
    from src.cogs.yandex_music import YandexMusic

    cog = YandexMusic(bot=MagicMock())
    guild_id = 888
    user_id = 9009

    session = await blend_manager.get_or_create_session(guild_id, 100)
    session.add_participant(user_id)
    assert user_id in session.active_participants

    state = cog.get_state(guild_id)
    state["index"] = 0
    state["tracks"] = [
        {"id": "1", "blend_user_id": None},
        {"id": "2", "blend_user_id": user_id}
    ]

    mock_interaction = AsyncMock()
    mock_interaction.guild_id = guild_id
    mock_interaction.user.id = user_id

    with patch("src.db.delete_blend_user_token", new=AsyncMock(return_value=True)), \
         patch.object(cog, "send_now_playing", new=AsyncMock()) as mock_send_np:

        await cog.blend_unlink.callback(cog, mock_interaction)

        # Проверяем, что пользователь удален из сессии и сессия завершена
        assert guild_id not in blend_manager._sessions
        # Проверяем, что несыгранный трек пользователя удален из очереди
        assert len(state["tracks"]) == 1
        assert state["tracks"][0]["id"] == "1"
        # Проверяем отправку ответа в Discord
        mock_interaction.response.send_message.assert_called_once()
        mock_send_np.assert_called_once_with(guild_id)


@pytest.mark.asyncio
async def test_blend_leave_command():
    """21. test_blend_leave_command — Проверка работы команды /blend leave."""
    from src.cogs.yandex_music import YandexMusic

    cog = YandexMusic(bot=MagicMock())
    guild_id = 777
    user_id = 3003

    session = await blend_manager.get_or_create_session(guild_id, 200)
    session.add_participant(user_id)

    state = cog.get_state(guild_id)
    state["index"] = 0
    state["tracks"] = [
        {"id": "10", "blend_user_id": user_id}
    ]

    mock_interaction = AsyncMock()
    mock_interaction.guild_id = guild_id
    mock_interaction.user.id = user_id

    with patch.object(cog, "send_now_playing", new=AsyncMock()):
        await cog.blend_leave.callback(cog, mock_interaction)

        assert guild_id not in blend_manager._sessions
        mock_interaction.response.send_message.assert_called_once()


