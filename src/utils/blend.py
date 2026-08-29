import asyncio
import logging
import time
from typing import Dict, List, Set, Optional, Any
from weakref import WeakValueDictionary

import db
from utils.blend_pool import ym_client_pool

logger = logging.getLogger(__name__)


class BlendSession:
    """Сессия Совместной Волны для конкретного голосового канала (Guild)."""

    __slots__ = ('guild_id', 'channel_id', 'active_participants', 'user_tracks_map', '_lock')

    def __init__(self, guild_id: int, channel_id: int):
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.active_participants: Set[int] = set()
        # user_id -> List of Track objects added to queue specifically for this user
        self.user_tracks_map: Dict[int, List[Any]] = {}
        self._lock = asyncio.Lock()

    def add_participant(self, user_id: int):
        self.active_participants.add(user_id)
        if user_id not in self.user_tracks_map:
            self.user_tracks_map[user_id] = []

    def remove_participant(self, user_id: int) -> List[Any]:
        """Удаляет участника и возвращает список несыгранных треков, сгенерированных для него."""
        if user_id in self.active_participants:
            self.active_participants.remove(user_id)
        return self.user_tracks_map.pop(user_id, [])

    def track_added_for_user(self, user_id: int, track: Any):
        if user_id in self.user_tracks_map:
            self.user_tracks_map[user_id].append(track)

    def track_played(self, track_id: str):
        """Удаляет трек из карт пользователей, когда он проигран."""
        for user_id, tracks in list(self.user_tracks_map.items()):
            self.user_tracks_map[user_id] = [
                t for t in tracks if getattr(t, 'id', str(t)) != str(track_id)
            ]


class BlendManager:
    """Глобальный менеджер Smart Blend DJ (Совместной Волны)."""

    def __init__(self):
        # guild_id -> BlendSession
        self._sessions: Dict[int, BlendSession] = {}
        # guild_id -> asyncio.Task for debounce
        self._debounce_tasks: Dict[int, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def get_or_create_session(self, guild_id: int, channel_id: int) -> BlendSession:
        async with self._lock:
            if guild_id not in self._sessions:
                self._sessions[guild_id] = BlendSession(guild_id, channel_id)
            return self._sessions[guild_id]

    async def get_session(self, guild_id: int) -> Optional[BlendSession]:
        return self._sessions.get(guild_id)

    def get_session_sync(self, guild_id: int) -> Optional[BlendSession]:
        return self._sessions.get(guild_id)

    async def remove_session(self, guild_id: int, ym_cog: Any = None):
        async with self._lock:
            if guild_id in self._sessions:
                del self._sessions[guild_id]
        if ym_cog and hasattr(ym_cog, 'send_now_playing'):
            try:
                await ym_cog.send_now_playing(guild_id)
            except Exception:
                pass

    async def handle_voice_state_update(self, guild_id: int, channel_id: int, member_ids: Set[int], ym_cog: Any):
        """Обработка изменения состава участников канала с Debounce 4 секунды."""
        if guild_id in self._debounce_tasks:
            self._debounce_tasks[guild_id].cancel()

        task = asyncio.create_task(
            self._delayed_voice_state_update(guild_id, channel_id, member_ids, ym_cog)
        )
        task.add_done_callback(lambda t: self._debounce_tasks.pop(guild_id, None))
        self._debounce_tasks[guild_id] = task

    async def _delayed_voice_state_update(self, guild_id: int, channel_id: int, member_ids: Set[int], ym_cog: Any):
        try:
            await asyncio.sleep(4.0)  # 4 секунды debounce
            
            # Проверяем включена ли фича на сервере
            cfg = await db.get_blend_config(guild_id)
            if not cfg.get("blend_enabled", True):
                return

            session = await self.get_session(guild_id)
            if not session:
                return

            current_participants = set(session.active_participants)
            new_participants = member_ids - current_participants
            left_participants = current_participants - member_ids

            # Обработка ушедших участников
            for user_id in left_participants:
                unplayed_tracks = session.remove_participant(user_id)
                # Удаляем их несыгранные треки из очереди плеера
                if unplayed_tracks and hasattr(ym_cog, 'remove_user_tracks_from_queue'):
                    await ym_cog.remove_user_tracks_from_queue(guild_id, user_id, unplayed_tracks)
                
                # Иерархия очистки токена при выходе:
                # 1. Личная настройка пользователя forget_on_disconnect
                # 2. Фолбэк на настройку сервера ym_settings.logout_on_disconnect
                await self._process_token_purge_policy(user_id, guild_id)

            # Обработка вошедших участников
            for user_id in new_participants:
                session.add_participant(user_id)

            # Если в комнате никого не осталось — завершаем сессию
            if not session.active_participants:
                await self.remove_session(guild_id)
                return

            # Если пришли новые участники — генерируем доп. треки
            if new_participants:
                await self.generate_wave_batch(guild_id, ym_cog, target_user_ids=new_participants)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Ошибка при выполнении debounce voice state update для %s: %s", guild_id, e)

    async def _process_token_purge_policy(self, user_id: int, guild_id: int):
        """Определяет необходимость авто-удаления токена пользователя при дисконнекте."""
        try:
            token_data = await db.get_blend_user_token(user_id, guild_id)
            if not token_data:
                return

            user_pref = token_data.get("forget_on_disconnect")
            should_delete = False

            if user_pref is not None:
                # 1. Приоритет: личная настройка пользователя
                should_delete = bool(user_pref)
            else:
                # 2. Приоритет: фолбэк на настройки текущего сервера в ym_settings
                ym_st = await db.get_ym_settings(guild_id)
                should_delete = bool(ym_st.get("logout_on_disconnect", False))

            if should_delete:
                logger.info("Авто-удаление токена пользователя %s при выходе из канала в гильдии %s", user_id, guild_id)
                await db.delete_blend_user_token(user_id, guild_id)
        except Exception as e:
            logger.error("Ошибка проверки политики очистки токена для user %s guild %s: %s", user_id, guild_id, e)

    async def generate_wave_batch(self, guild_id: int, ym_cog: Any, target_user_ids: Optional[Set[int]] = None) -> int:
        """
        Генерирует пакет треков: минимум по 3 трека от каждого присутствующего участника.
        Прямые асинхронные запросы к API Яндекс.Музыки без искусственных задержек.
        """
        session = await self.get_session(guild_id)
        if not session or not session.active_participants:
            return 0

        participants = target_user_ids if target_user_ids else set(session.active_participants)
        guild_tokens = await db.get_blend_guild_tokens(guild_id)
        token_map = {t["user_id"]: t for t in guild_tokens if t.get("decrypted_token")}

        added_count = 0

        for user_id in list(participants):
            token_info = token_map.get(user_id)
            if not token_info:
                continue

            decrypted_token = token_info["decrypted_token"]
            client = await ym_client_pool.get_client(user_id, guild_id, decrypted_token)
            if not client:
                continue

            try:
                # Запрашиваем радиостанции / персональные треки пользователя ("Моя Волна")
                # В Яндекс.Музыка API: client.rotor_station_tracks('user:onyourwave') или похожие треки
                rotor_tracks = await client.rotor_station_tracks('user:onyourwave')
                tracks_to_add = []
                if rotor_tracks and hasattr(rotor_tracks, 'sequence'):
                    tracks_to_add = [item.track for item in rotor_tracks.sequence[:3] if hasattr(item, 'track')]
                elif isinstance(rotor_tracks, list):
                    tracks_to_add = rotor_tracks[:3]

                for track in tracks_to_add:
                    if hasattr(ym_cog, 'add_blend_track_to_queue'):
                        success = await ym_cog.add_blend_track_to_queue(guild_id, user_id, track)
                        if success:
                            session.track_added_for_user(user_id, track)
                            added_count += 1

            except Exception as e:
                err_str = str(e)
                if "401" in err_str or "Unauthorized" in err_str:
                    logger.warning("Токен пользователя %s протух (401), помечаем неактивным", user_id)
                    await db.mark_blend_token_inactive(user_id, guild_id)
                else:
                    logger.error("Ошибка при получении треков волны для user %s в гильдии %s: %s", user_id, guild_id, e)

        return added_count


# Глобальный синглтон BlendManager
blend_manager = BlendManager()
