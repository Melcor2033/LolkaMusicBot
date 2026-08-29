"""Yandex Music — Cog для воспроизведения Яндекс.Музыки в голосовых каналах.

Поддерживает индивидуальную авторизацию для серверов (в БД),
асинхронное фоновое сканирование кэша, "Мою волну",
радиостанции и лайки/дизлайки.
"""

from __future__ import annotations

import os
import gc
import time
import logging
import asyncio
import inspect
from typing import TYPE_CHECKING, Optional

import lolka as discord
from lolka import app_commands
from lolka.ext import commands
from yandex_music import ClientAsync
from yandex_music.exceptions import DeviceAuthError

import db
import config
from utils.voice_utils import get_user_voice_channel, safe_defer, safe_send, safe_voice_connect
from views.ym_views import YMPlayerView, YMAuthView, YMReadyView
from views.base_player import create_progress_bar, format_player_status, run_timeline_updater_loop, stop_other_cogs, ensure_voice_connection, has_listeners
from utils.ym_url_parser import parse_ym_url
from utils.queue_manager import QueueManager
from cryptography.fernet import Fernet

def encrypt_value(value: str | None) -> str | None:
    if not value:
        return None
    f = Fernet(config.YM_ENCRYPTION_KEY.encode())
    encrypted = f.encrypt(value.encode()).decode()
    return f"enc:{encrypted}"

def decrypt_value(value: str | None) -> str | None:
    if not value:
        return None
    if value.startswith("enc:"):
        actual_val = value[4:]
        f = Fernet(config.YM_ENCRYPTION_KEY.encode())
        return f.decrypt(actual_val.encode()).decode()
    return value

if TYPE_CHECKING:
    from bot import DynamicVoiceBot

logger = logging.getLogger(__name__)

# Папка кэша аудиофайлов
CACHE_DIR = os.path.join(os.getcwd(), "music", "_ym")
os.makedirs(CACHE_DIR, exist_ok=True)

# Лимиты кэша (500 МБ макс, очистка до 400 МБ)
MAX_CACHE_BYTES = 500 * 1024 * 1024
TARGET_CACHE_BYTES = 400 * 1024 * 1024

# Дефолтный лимит очереди
MAX_QUEUE = 500
def is_bot_busy_in_other_channel(interaction: discord.Interaction) -> bool:
    """Проверяет, занят ли бот воспроизведением для людей в другом канале."""
    vc = interaction.guild.voice_client if interaction.guild else None
    if not vc or not vc.channel:
        return False
        
    user = interaction.user
    voice_state = None
    if interaction.guild:
        voice_state = interaction.guild._voice_state_for(user.id)
    if not voice_state:
        voice_state = getattr(user, 'voice', None)
    if not voice_state and interaction.guild:
        member = interaction.guild.get_member(user.id)
        if member:
            voice_state = getattr(member, 'voice', None)
            
    user_channel = voice_state.channel if voice_state else None
    
    if vc.channel != user_channel:
        active_members = [m for m in vc.channel.members if not m.bot]
        if active_members:
            return True
    return False


def format_duration(ms: int | None) -> str:
    if not ms:
        return "?"
    s = int(ms / 1000)
    m = s // 60
    return f"{m}:{s % 60:02d}"


class YandexMusic(commands.Cog):
    """Модуль Яндекс.Музыки для DynamicVoiceBot."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Сессии клиентов (guild_id -> ClientAsync)
        self._ym_clients: dict[int, ClientAsync] = {}
        # Соединения с голосовыми каналами (guild_id -> VoiceClient)
        self._voice_clients: dict[int, discord.VoiceClient] = {}
        # Громкость по умолчанию (guild_id -> float)
        self._volume: dict[int, float] = {}
        # Состояния очередей (guild_id -> QueueManager)
        self._states: dict[int, QueueManager] = {}
        # Мьютексы для предотвращения гонок при скачивании
        self._download_locks: dict[str, asyncio.Lock] = {}
        # Фоновые таски
        self._bg_tasks = set()
        self._cleaner_task = None
        self._timeline_task = None

    async def cog_load(self) -> None:
        """Запускается при загрузке cog'а."""
        self._cleaner_task = asyncio.create_task(self._cache_cleaner_loop())
        self._timeline_task = asyncio.create_task(self._timeline_updater_loop())

    async def cog_unload(self) -> None:
        """Очистка ресурсов при выгрузке модуля."""
        if self._cleaner_task:
            self._cleaner_task.cancel()
        if self._timeline_task:
            self._timeline_task.cancel()

        for task in list(self._bg_tasks):
            task.cancel()

        for guild_id, vc in list(self._voice_clients.items()):
            try:
                if vc.is_connected():
                    await vc.disconnect(force=True)
            except Exception as exc:
                logger.warning("Ошибка отключения от гильдии %s: %s", guild_id, exc)

        self._ym_clients.clear()
        self._voice_clients.clear()
        self._states.clear()

    async def _safe_feedback(self, coro) -> None:
        """Безопасно выполняет фоновые запросы к API Яндекс.Музыки, ловя исключения."""
        try:
            await coro
        except Exception as e:
            logger.warning("Ошибка при отправке фонового фидбека в Яндекс.Музыку: %s", e)

    async def _check_interaction_permissions(self, interaction: discord.Interaction, mode: str = "control") -> bool:
        """Проверяет права пользователя на лайки или управление плеером Яндекс.Музыки."""
        async def send_msg(text: str, **kwargs) -> None:
            if interaction.response.is_done():
                await interaction.followup.send(text, **kwargs)
            else:
                await interaction.response.send_message(text, **kwargs)

        user = interaction.user
        guild_id = interaction.guild_id

        vc = self._voice_clients.get(guild_id) or interaction.guild.voice_client
        
        # Если бот не подключен к голосовому каналу
        if not vc or not vc.channel:
            if mode != "control":
                await send_msg("❌ Бот не подключен к голосовому каналу.", ephemeral=True)
                return False
                
            # Для запуска плеера проверяем, находится ли пользователь в голосовом канале
            if not user.voice or not user.voice.channel:
                await send_msg("❌ Сначала зайдите в голосовой канал!", ephemeral=True)
                return False
                
            voice_channel = user.voice.channel
            channel_permissions = voice_channel.permissions_for(user)
            room_owner_id = await db.get_dynamic_channel_owner(voice_channel.id)
            is_owner = (room_owner_id and user.id == room_owner_id) or channel_permissions.manage_channels
            
            if user.guild_permissions.administrator:
                return True
                
            settings = await db.get_ym_settings(guild_id)
            control_mode = settings.get("control_mode", "everyone")
            dj_roles = settings.get("dj_role_ids", [])
            
            if control_mode == "everyone":
                return True
            if control_mode == "owner_only":
                if is_owner:
                    return True
                await send_msg("❌ Запустить плеер может только владелец комнаты.", ephemeral=True)
                return False
            if control_mode == "dj_only":
                if not dj_roles:
                    if is_owner:
                        return True
                    await send_msg("❌ DJ-роли не настроены. Запустить плеер может только владелец комнаты.", ephemeral=True)
                    return False
                
                user_role_ids = {role.id for role in user.roles}
                has_dj_role = any(r_id in user_role_ids for r_id in dj_roles)
                if has_dj_role or is_owner:
                    return True
                await send_msg("❌ У вас нет роли DJ для запуска плеера.", ephemeral=True)
                return False
            return True

        # Если бот подключен к каналу
        if not user.voice or user.voice.channel != vc.channel:
            await send_msg("❌ Вы должны находиться в том же голосовом канале, что и бот, чтобы управлять им.", ephemeral=True)
            return False

        settings = await db.get_ym_settings(guild_id)
        is_initiator = False
        state = self._states.get(guild_id)
        if state:
            is_initiator = state.get("initiator_id") == user.id

        if mode == "like":
            like_mode = settings.get("like_mode", "owner_only")
            if like_mode == "off":
                await send_msg("❌ Лайки на этом сервере отключены администратором.", ephemeral=True)
                return False
            if like_mode == "owner_only":
                if is_initiator:
                    return True
                await send_msg("❌ Ставить лайки может только инициатор воспроизведения.", ephemeral=True)
                return False
            if like_mode == "everyone":
                return True

        if user.guild_permissions.administrator:
            return True

        channel_permissions = vc.channel.permissions_for(user)
        room_owner_id = await db.get_dynamic_channel_owner(vc.channel.id)
        if room_owner_id:
            is_owner = is_initiator or (user.id == room_owner_id) or channel_permissions.manage_channels
        else:
            is_owner = is_initiator or channel_permissions.manage_channels

        if mode == "control":
            control_mode = settings.get("control_mode", "everyone")
            dj_roles = settings.get("dj_role_ids", [])

            if control_mode == "everyone":
                return True
            if control_mode == "owner_only":
                if is_owner:
                    return True
                await send_msg("❌ Управлять плеером может только владелец комнаты или инициатор воспроизведения.", ephemeral=True)
                return False
            if control_mode == "dj_only":
                if not dj_roles:
                    if is_owner:
                        return True
                    await send_msg("❌ DJ-роли не настроены. Управлять плеером может только владелец комнаты.", ephemeral=True)
                    return False
                
                user_role_ids = {role.id for role in user.roles}
                has_dj_role = any(r_id in user_role_ids for r_id in dj_roles)
                if has_dj_role or is_owner:
                    return True
                await send_msg("❌ У вас нет роли DJ для управления плеером.", ephemeral=True)
                return False

        return True

    # ──────────────────────────────────────────
    # Жизненный цикл клиента и авторизация
    # ──────────────────────────────────────────

    async def get_ym_client(self, guild_id: int) -> ClientAsync | None:
        """Получить инициализированный асинхронный клиент Яндекс.Музыки для сервера."""
        if guild_id in self._ym_clients:
            return self._ym_clients[guild_id]

        cfg = await db.get_ym_config(guild_id)
        if not cfg or not cfg.get("token"):
            return None

        token_raw = cfg["token"]
        session_id_raw = cfg.get("session_id")
        session_id2_raw = cfg.get("session_id2")

        # Проверим, нужно ли шифровать старые открытые токены
        needs_re_encryption = False
        if token_raw and not token_raw.startswith("enc:"):
            needs_re_encryption = True
        if session_id_raw and not session_id_raw.startswith("enc:"):
            needs_re_encryption = True
        if session_id2_raw and not session_id2_raw.startswith("enc:"):
            needs_re_encryption = True

        token = decrypt_value(token_raw)
        session_id = decrypt_value(session_id_raw)
        session_id2 = decrypt_value(session_id2_raw)

        if needs_re_encryption:
            logger.info("Обнаружены незашифрованные данные Яндекс.Музыки для сервера %s, выполняем ленивое шифрование...", guild_id)
            await db.save_ym_config(
                guild_id=guild_id,
                token=encrypt_value(token),
                session_id=encrypt_value(session_id),
                session_id2=encrypt_value(session_id2),
                username=cfg.get("username", "unknown")
            )

        headers = {}
        if session_id:
            cookies = f"Session_id={session_id}"
            if session_id2:
                cookies += f"; sessionid2={session_id2}"
            headers["Cookie"] = cookies

        try:
            if headers:
                from yandex_music.utils.request_async import Request
                req = Request(headers=headers)
                client = ClientAsync(token=token, request=req, language="ru")
            else:
                client = ClientAsync(token=token, language="ru")
            await client.init()
            self._ym_clients[guild_id] = client
            logger.info("Успешно запущен клиент ЯМ на сервере %s (%s)", guild_id, cfg.get("username"))
            return client
        except Exception as e:
            logger.error("Ошибка инициализации клиента ЯМ на сервере %s: %s", guild_id, e, exc_info=True)
            return None

    def unload_ym_client(self, guild_id: int) -> None:
        """Выгрузить клиент ЯМ из оперативной памяти."""
        client = self._ym_clients.pop(guild_id, None)
        if client is not None:
            session = getattr(client, '_session', None) or getattr(client, 'session', None)
            if session and not session.closed:
                try:
                    asyncio.get_event_loop().create_task(session.close())
                except Exception as e:
                    logger.warning("Не удалось закрыть сессию aiohttp для клиента ЯМ: %s", e)
        logger.info("Клиент ЯМ выгружен из памяти для сервера %s", guild_id)

    async def start_auth_flow(self, interaction: discord.Interaction) -> None:
        """Запускает процесс OAuth Device Flow для сервера."""
        guild_id = interaction.guild_id
        if not interaction.response.is_done():
            await interaction.response.defer()

        client = ClientAsync(language="ru")

        # Коллбек показа кода
        async def on_code(device_code):
            embed = discord.Embed(
                title="🔑 Авторизация в Яндекс.Музыке",
                description=(
                    f"Для привязки этого сервера к Яндекс.Музыке выполните шаги:\n\n"
                    f"1. Перейдите по ссылке: **[Подтвердить вход]({device_code.verification_url})**\n"
                    f"2. Введите код устройства: **`{device_code.user_code}`**\n"
                    f"3. Разрешите доступ боту.\n\n"
                    f"⏱️ Ожидание подтверждения... Код действителен {device_code.expires_in} сек."
                ),
                color=discord.Color.yellow()
            )
            await interaction.edit_original_response(embed=embed, view=discord.ui.View())

        # Ограничиваем разрешения исключительно Яндекс.Музыкой (music:read music:write),
        # чтобы убрать пугающие права на Оплату, Умный дом и Диск.
        from yandex_music._client_async.device_auth import _rand_device_id, DeviceCode

        async def _scoped_request_device_code(device_id=None, device_name=None, client_id=None):
            data = {
                'client_id': client_id or config.YM_CLIENT_ID,
                'device_id': device_id or _rand_device_id(),
                'device_name': device_name or "DynamicVoiceBot",
                'scope': 'music:read music:write',
            }
            res = await client._request.post('https://oauth.yandex.ru/device/code', data)
            return DeviceCode.de_json(res, client)

        client.request_device_code = _scoped_request_device_code

        async def _auth_task():
            try:
                # Используем официальный TV/Web Client ID Яндекс.Музыки с минимальным набором прав
                token = await client.device_auth(on_code=on_code, client_id=config.YM_CLIENT_ID)
                await client.init()
                
                login = client.me.account.login if client.me and client.me.account else "unknown"
                
                # Сохраняем в БД (в зашифрованном виде)
                await db.save_ym_config(
                    guild_id=guild_id,
                    token=encrypt_value(token.access_token),
                    session_id=None,
                    session_id2=None,
                    username=login
                )
                self._ym_clients[guild_id] = client

                # Также сохраняем персональный токен пользователя для Совместной Волны (Blend)
                user_id = interaction.user.id
                await db.save_blend_user_token(
                    user_id=user_id,
                    guild_id=guild_id,
                    token=token.access_token,
                    username=login
                )

                vc = self._voice_clients.get(guild_id) or (interaction.guild.voice_client if interaction.guild else None)
                state = self.get_state(guild_id)
                is_currently_playing = vc and vc.is_connected() and (vc.is_playing() or vc.is_paused() or state.get("tracks"))

                if is_currently_playing and vc and vc.channel:
                    from utils.blend import blend_manager
                    session = await blend_manager.get_or_create_session(guild_id, vc.channel.id)
                    session.add_participant(user_id)
                    tracks_added = await blend_manager.generate_wave_batch(guild_id, self, target_user_ids={user_id})

                    success_embed = discord.Embed(
                        title="✅ Авторизация успешна!",
                        description=(
                            f"Бот успешно вошел под аккаунтом **{login}**.\n\n"
                            f"🔀 **Вы автоматически присоединились к Совместной Волне в {vc.channel.mention}!**\n"
                            f"🎵 В общий микс добавлено **{tracks_added}** ваших предпочтений."
                        ),
                        color=discord.Color.green()
                    )
                    await interaction.edit_original_response(embed=success_embed, view=discord.ui.View())
                else:
                    success_embed = discord.Embed(
                        title="✅ Авторизация успешна!",
                        description=f"Бот успешно вошел под аккаунтом **{login}** для этого сервера.",
                        color=discord.Color.green()
                    )
                    await interaction.edit_original_response(embed=success_embed, view=discord.ui.View())

                    channel = interaction.channel
                    if channel:
                        embed = discord.Embed(
                            title="📻 Яндекс.Музыка",
                            description=f"Бот успешно авторизован аккаунтом **{login}**!\nНажмите **Моя Волна** 🌊 ниже или воспользуйтесь кнопкой поиска для воспроизведения.",
                            color=discord.Color.from_rgb(255, 204, 0)
                        )
                        from views.ym_views import YMReadyView
                        await channel.send(embed=embed, view=YMReadyView())
            except Exception as e:
                logger.error("Ошибка авторизации ЯМ на сервере %s: %s", guild_id, e)
                err_embed = discord.Embed(
                    title="❌ Ошибка авторизации",
                    description=f"Произошла ошибка в процессе авторизации: {e}",
                    color=discord.Color.red()
                )
                try:
                    await interaction.edit_original_response(embed=err_embed, view=discord.ui.View())
                except Exception:
                    pass

        task = asyncio.create_task(_auth_task())
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    # ──────────────────────────────────────────
    # Управление состояниями очередей
    # ──────────────────────────────────────────

    def get_state(self, guild_id: int) -> QueueManager:
        """Получить или инициализировать состояние воспроизведения на сервере."""
        if guild_id not in self._states:
            self._states[guild_id] = QueueManager()
        return self._states[guild_id]

    def reset_state(self, guild_id: int) -> None:
        """Полностью сбросить состояние сессии и выгрузить клиент из памяти."""
        self._states.pop(guild_id, None)
        self.unload_ym_client(guild_id)
        logger.info("Сессия и очередь полностью сброшены для сервера %s", guild_id)

    # ──────────────────────────────────────────
    # Скачивание и кэширование
    # ──────────────────────────────────────────

    async def download_track(self, guild_id: int, track_id: str | int) -> str | None:
        """Скачивает трек в локальный кэш. Потокобезопасно."""
        client = await self.get_ym_client(guild_id)
        if not client:
            return None

        track_id_str = str(track_id)
        file_path = os.path.join(CACHE_DIR, f"{track_id_str}.mp3")

        # Если файл уже есть, возвращаем сразу
        if os.path.exists(file_path):
            return file_path

        # Избегаем параллельной закачки одного и того же трека
        if track_id_str not in self._download_locks:
            self._download_locks[track_id_str] = asyncio.Lock()

        async with self._download_locks[track_id_str]:
            # Повторно проверяем после снятия лока
            if os.path.exists(file_path):
                return file_path

            try:
                tracks = await client.tracks([track_id_str])
                if not tracks:
                    return None
                track = tracks[0]

                download_infos = await track.get_download_info_async(get_direct_links=True)
                if not download_infos:
                    return None

                # Выбираем максимальный битрейт
                download_infos.sort(key=lambda x: x.bitrate_in_kbps or 0, reverse=True)
                best_info = download_infos[0]

                temp_path = file_path + ".tmp"
                await best_info.download_async(temp_path)
                
                # Атомарное переименование
                os.rename(temp_path, file_path)
                return file_path
            except Exception as e:
                logger.error("Ошибка скачивания трека %s на сервере %s: %s", track_id_str, guild_id, e)
                return None
            finally:
                self._download_locks.pop(track_id_str, None)

    async def queue_track(
        self,
        guild_id: int,
        track,
        source: str,
        station_id: str | None,
        batch_id: str | None = None,
        blend_user_id: int | None = None,
        blend_username: str | None = None,
    ) -> bool:
        """Загружает метаданные и добавляет трек в очередь воспроизведения."""
        state = self.get_state(guild_id)
        try:
            file_path = await self.download_track(guild_id, track.id)
            if not file_path:
                return False

            artists = ", ".join(a.name for a in track.artists) if track.artists else "Неизвестный исполнитель"
            cover = None
            if track.cover_uri:
                cover = track.cover_uri.replace("%%", "200x200")

            track_item = {
                "id": str(track.id),
                "title": track.title or "Без названия",
                "artist": artists,
                "duration": track.duration_ms,
                "cover": cover,
                "file": file_path,
                "source": source,
                "station_id": station_id,
                "batch_id": batch_id,
            }
            if blend_user_id:
                track_item["blend_user_id"] = blend_user_id
            if blend_username:
                track_item["blend_username"] = blend_username

            state["tracks"].append(track_item)

            # Держим размер очереди в пределах лимита
            while len(state["tracks"]) > MAX_QUEUE:
                state["tracks"].pop(0)
                if state["index"] > 0:
                    state["index"] -= 1

            return True
        except Exception as e:
            logger.error("Ошибка добавления трека %s в очередь сервера %s: %s", getattr(track, 'id', 'unknown'), guild_id, e)
            return False

    async def add_blend_track_to_queue(self, guild_id: int, user_id: int, track) -> bool:
        """Добавляет трек участника Совместной Волны в очередь гильдии."""
        state = self.get_state(guild_id)
        source = "blend"
        station_id = state.get("station_id") or "user:onyourwave"

        username = None
        try:
            token_info = await db.get_blend_user_token(user_id, guild_id)
            if token_info:
                username = token_info.get("username")
        except Exception:
            pass

        return await self.queue_track(
            guild_id,
            track,
            source,
            station_id,
            blend_user_id=user_id,
            blend_username=username,
        )

    async def remove_user_tracks_from_queue(self, guild_id: int, user_id: int, unplayed_tracks: list) -> None:
        """Удаляет несыгранные треки вышедшего участника волны из очереди."""
        state = self.get_state(guild_id)
        if not state.get("tracks"):
            return

        curr_idx = state.get("index", 0)
        new_tracks = []
        for idx, t in enumerate(state["tracks"]):
            if idx <= curr_idx or t.get("blend_user_id") != user_id:
                new_tracks.append(t)

        state["tracks"] = new_tracks

    # ──────────────────────────────────────────
    # Логика воспроизведения
    # ──────────────────────────────────────────

    async def refill_wave(self, guild_id: int, background: bool = False) -> bool:
        """Запрашивает новые треки из бесконечного потока рекомендаций.

        Защищён asyncio.Lock для предотвращения гонки между фоновым
        prefetch (after_callback) и синхронным вызовом из play_track.
        """
        logger.info("refill_wave вызван для сервера %s (background=%s)", guild_id, background)
        state = self.get_state(guild_id)
        client = await self.get_ym_client(guild_id)
        if not client or not state["station_id"]:
            logger.warning("refill_wave прерван: нет клиента или station_id для сервера %s", guild_id)
            return False

        async with state._refill_wave_lock:
            logger.info("refill_wave захватил лок для сервера %s (background=%s)", guild_id, background)
            # Double-check: пока ждали лок, другой вызов мог уже пополнить буфер
            if len(state["tracks"]) - state["index"] > 2:
                logger.info("refill_wave double-check пройден (буфер уже полон: %s треков впереди)", len(state["tracks"]) - state["index"])
                return True

            try:
                queue_param = None
                if state["tracks"]:
                    queue_param = state["tracks"][-1]["id"]

                logger.info("Запрос rotor_station_tracks для сервера %s с queue=%s", guild_id, queue_param)
                result = await client.rotor_station_tracks(state["station_id"], queue=queue_param)
                if not result or not result.sequence:
                    return False

                state["batch_id"] = result.batch_id

                # Send radio_started if this is the first fetch in this session
                if not state.get("radio_started"):
                    try:
                        asyncio.run_coroutine_threadsafe(
                            self._safe_feedback(
                                client.rotor_station_feedback_radio_started(
                                    station=state["station_id"],
                                    from_="station:" + state["station_id"],
                                    batch_id=result.batch_id
                                )
                            ),
                            self.bot.loop
                        )
                        state["radio_started"] = True
                    except Exception:
                        pass

                played_ids = state.setdefault("played_ids", set())
                added = 0

                # 1. Сначала скачиваем только ПЕРВЫЙ трек для мгновенного запуска плеера
                first_seq = None
                remaining_seqs = []
                for seq in result.sequence:
                    if seq.track and str(seq.track.id) not in played_ids:
                        if first_seq is None:
                            first_seq = seq
                        else:
                            remaining_seqs.append(seq)

                if not first_seq:
                    return False

                # Загружаем 1-й трек синхронно для немедленного старта
                ok = await self.queue_track(guild_id, first_seq.track, state["source"], state["station_id"], result.batch_id)
                if ok:
                    added += 1
                    played_ids.add(str(first_seq.track.id))

                # 2. Оставшиеся треки батча качаем фоном в асинхронной задаче, пока играет 1-й трек
                if remaining_seqs:
                    current_version = state.version
                    async def load_remaining_wave_tracks():
                        for r_seq in remaining_seqs:
                            if state.version != current_version:
                                break
                            r_id = str(r_seq.track.id)
                            if r_id not in played_ids:
                                if await self.queue_track(guild_id, r_seq.track, state["source"], state["station_id"], result.batch_id):
                                    played_ids.add(r_id)

                    bg_task = asyncio.create_task(load_remaining_wave_tracks())
                    self._bg_tasks.add(bg_task)
                    bg_task.add_done_callback(self._bg_tasks.discard)

                if len(played_ids) > 200:
                    active_ids = {t["id"] for t in state["tracks"]}
                    state["played_ids"] = active_ids

                # Если на сервере активна Совместная Волна — подмешиваем треки участников
                try:
                    try:
                        from utils.blend import blend_manager
                    except ModuleNotFoundError:
                        from src.utils.blend import blend_manager

                    session = await blend_manager.get_session(guild_id)
                    if session and session.active_participants:
                        logger.info("Подмешивание треков участников Совместной Волны в refill_wave для сервера %s", guild_id)
                        await blend_manager.generate_wave_batch(guild_id, self)
                except Exception as be:
                    logger.error("Ошибка авто-пополнения треков Совместной Волны в refill_wave: %s", be)

                return added > 0
            except Exception as e:
                e_str = str(e)
                # HTTP 429 — Яндекс временно ограничивает запросы (лимит concurrency или rate limit)
                if "429" in e_str or "Concurrency limit" in e_str:
                    logger.warning("refill_wave: 429 от ЯМ для сервера %s, повторная попытка через 2 сек...", guild_id)
                    await asyncio.sleep(2.0)
                    try:
                        result2 = await client.rotor_station_tracks(state["station_id"], queue=queue_param)
                        if result2 and result2.sequence:
                            state["batch_id"] = result2.batch_id
                            played_ids = state.setdefault("played_ids", set())
                            added2 = 0
                            for seq in result2.sequence:
                                if seq.track and str(seq.track.id) not in played_ids:
                                    ok2 = await self.queue_track(guild_id, seq.track, state["source"], state["station_id"], result2.batch_id)
                                    if ok2:
                                        added2 += 1
                                        played_ids.add(str(seq.track.id))
                            return added2 > 0
                    except Exception as e2:
                        logger.error("refill_wave: повторная попытка тоже упала для сервера %s: %s", guild_id, e2)
                        # Уведомляем пользователя в текстовый канал волны
                        ch = state.get("channel")
                        if ch:
                            try:
                                await ch.send(
                                    "⚠️ Яндекс.Музыка временно не отдаёт рекомендации. "
                                    "Попробуйте позже или запустите волну заново."
                                )
                            except Exception:
                                pass
                        return False
                logger.error("Ошибка получения рекомендаций для сервера %s: %s", guild_id, e)
                return False

    def _play_next(self, guild_id: int) -> None:
        """Запускает следующий трек из asyncio event loop (коллбек)."""
        coro = self.play_track(guild_id)
        asyncio.run_coroutine_threadsafe(coro, self.bot.loop)

    async def play_track(self, guild_id: int, start_index: int | None = None) -> None:
        """Основной цикл воспроизведения."""
        state = self.get_state(guild_id)
        if start_index is not None:
            state["index"] = start_index

        vc = self._voice_clients.get(guild_id)
        if not vc or not vc.is_connected():
            self.reset_state(guild_id)
            return

        # Проверяем лимит очереди
        if state["index"] >= len(state["tracks"]):
            if state["source"] in ("wave", "radio"):
                msg = None
                if state["channel"]:
                    msg = await state["channel"].send("⏳ *Подгружаю рекомендации...*")
                ok = await self.refill_wave(guild_id, background=False)
                if msg:
                    try:
                        await msg.delete()
                    except Exception:
                        pass
                if ok:
                    # Очищаем отыгранное, сдвигая индекс
                    state["played_count"] += state["index"]
                    state["tracks"] = state["tracks"][state["index"]:]
                    state["index"] = 0
                else:
                    await self._stop_and_cleanup(guild_id, "⏹️ Поток рекомендаций иссяк.")
                    return
            elif state.get("pending_tracks") or state.get("is_refilling_pending"):
                msg = None
                if state["channel"]:
                    msg = await state["channel"].send("⏳ *Подгружаю следующую партию...*")
                
                # Если уже идет подгрузка в фоне, подождем ее окончания
                attempts = 0
                while state.get("is_refilling_pending") and attempts < 20:
                    await asyncio.sleep(0.5)
                    attempts += 1
                
                if state["index"] < len(state["tracks"]):
                    ok = True
                else:
                    ok = await self.refill_pending(guild_id)

                if msg:
                    try:
                        await msg.delete()
                    except Exception:
                        pass
                
                if not (ok and state["index"] < len(state["tracks"])):
                    await self._stop_and_cleanup(guild_id, "⏹️ Очередь воспроизведения завершена.", disconnect=False)
                    return
            elif state["loop"]:
                state["index"] = 0
            else:
                await self._stop_and_cleanup(guild_id, "⏹️ Очередь воспроизведения завершена.", disconnect=False)
                return

        track_data = state["tracks"][state["index"]]
        state["current_track_id"] = track_data["id"]

        if not os.path.exists(track_data["file"]):
            if state["channel"]:
                await state["channel"].send(f"⚠️ Файл для трека **{track_data['title']}** потерян. Пропускаю...")
            state["index"] += 1
            await self.play_track(guild_id)
            return

        # Настраиваем проигрывание
        if vc.is_playing():
            vc.stop()

        state["playback_start_time"] = time.time()
        state["playback_elapsed"] = 0

        volume = self._volume.get(guild_id)
        if volume is None:
            cfg = await db.get_ym_settings(guild_id)
            volume = cfg.get("volume", 0.5) if cfg else 0.5
            self._volume[guild_id] = volume

        ffmpeg_options = "-vn -sn -dn -nostdin -threads 1 -loglevel error"
        source = discord.FFmpegPCMAudio(track_data["file"], options=ffmpeg_options)
        transformed = discord.PCMVolumeTransformer(source, volume=volume * 0.50)

        # История для возврата назад
        if state["index"] > 0 and (not state["prev_history"] or state["prev_history"][-1] != state["index"] - 1):
            state["prev_history"].append(state["index"] - 1)
            if len(state["prev_history"]) > 50:
                state["prev_history"].pop(0)

        # Отправляем фидбек в Яндекс
        client = await self.get_ym_client(guild_id)
        if client and state["source"] in ("wave", "radio") and state["station_id"]:
            try:
                task = asyncio.create_task(
                    self._safe_feedback(
                        client.rotor_station_feedback_track_started(
                            station=state["station_id"],
                            track_id=track_data["id"],
                            batch_id=track_data["batch_id"]
                        )
                    )
                )
                self._bg_tasks.add(task)
                task.add_done_callback(self._bg_tasks.discard)
            except Exception:
                pass

        def after_callback(error: Exception | None) -> None:
            if error:
                logger.error("Ошибка в плеере на сервере %s: %s", guild_id, error)
            
            # Отправка фидбека о завершении трека
            if client and state["source"] in ("wave", "radio") and state["station_id"]:
                try:
                    played_sec = int(track_data["duration"] / 1000) if track_data.get("duration") else 120
                    asyncio.run_coroutine_threadsafe(
                        self._safe_feedback(
                            client.rotor_station_feedback_track_finished(
                                station=state["station_id"],
                                track_id=track_data["id"],
                                total_played_seconds=played_sec,
                                batch_id=track_data["batch_id"]
                            )
                        ),
                        self.bot.loop
                    )
                except Exception:
                    pass

            # Переход на следующий
            if state["loop"]:
                # Если включен повтор трека, индекс остается прежним
                pass
            elif state["shuffle"] and state["source"] not in ("wave", "radio") and len(state["tracks"]) > 1:
                import random
                state["index"] = random.randint(0, len(state["tracks"]) - 1)
            else:
                state["index"] += 1

            # Предзагрузка новых рекомендаций в фоне (порог 2 трека для бесшовности)
            if len(state["tracks"]) - state["index"] <= 2:
                if state["source"] in ("wave", "radio"):
                    asyncio.run_coroutine_threadsafe(self.refill_wave(guild_id, background=True), self.bot.loop)
                elif state.get("pending_tracks"):
                    asyncio.run_coroutine_threadsafe(self.refill_pending(guild_id), self.bot.loop)

            self._play_next(guild_id)

        if vc.is_playing() or vc.is_paused():
            try:
                vc.stop()
            except Exception:
                pass
        self._stop_other_cogs(guild_id)
        vc.play(transformed, after=after_callback)
        state["fail_count"] = 0

        # Показываем панель плеера
        await self.send_now_playing(guild_id)

    def _stop_other_cogs(self, guild_id: int) -> None:
        stop_other_cogs(self.bot, guild_id, "YandexMusic")

    async def refill_pending(self, guild_id: int) -> bool:
        """Подгружает следующую партию треков (по 5 штук) из pending_tracks."""
        state = self.get_state(guild_id)
        if not state.get("pending_tracks"):
            return False

        if state.get("is_refilling_pending"):
            return False
        state["is_refilling_pending"] = True

        try:
            batch = state["pending_tracks"][:5]
            state["pending_tracks"] = state["pending_tracks"][5:]
            
            added = 0
            for item in batch:
                if isinstance(item, dict) and "track" in item:
                    track = item["track"]
                    source = item.get("source") or state.get("pending_type") or "playlist"
                    station_id = item.get("station_id") or state.get("pending_source_id")
                else:
                    track = item
                    source = state.get("pending_type") or "playlist"
                    station_id = state.get("pending_source_id")

                if track:
                    if hasattr(track, "fetch_track_async") and not hasattr(track, "duration_ms"):
                        try:
                            track = await track.fetch_track_async()
                        except Exception:
                            pass
                    elif hasattr(track, "fetch_trackAsync") and not hasattr(track, "duration_ms"):
                        try:
                            track = await track.fetch_trackAsync()
                        except Exception:
                            pass

                    ok = await self.queue_track(guild_id, track, source, station_id)
                    if ok:
                        added += 1
                    
            if added > 0:
                await self.send_now_playing(guild_id)
            return True
        except Exception as e:
            logger.error("Ошибка при подгрузке pending_tracks: %s", e)
            return False
        finally:
            state["is_refilling_pending"] = False

    async def _stop_and_cleanup(self, guild_id: int, message: str | None = None, disconnect: bool = True) -> None:
        """Остановить проигрывание и опционально выйти из канала."""
        state = self.get_state(guild_id)
        
        if disconnect:
            if state.get("np_msg"):
                try:
                    await state["np_msg"].delete()
                except Exception:
                    pass
                state["np_msg"] = None
                
            if state["channel"] and message:
                await state["channel"].send(message)
                
            vc = self._voice_clients.pop(guild_id, None)
            if vc:
                try:
                    if vc.is_playing():
                        vc.stop()
                    await vc.disconnect(force=True)
                except Exception:
                    pass
            # Полностью удаляем состояние воспроизведения
            self.reset_state(guild_id)
        else:
            vc = self._voice_clients.get(guild_id)
            if vc and vc.is_playing():
                vc.stop()
                
            last_track_str = ""
            if state["tracks"] and state["index"] < len(state["tracks"]):
                t = state["tracks"][state["index"]]
                last_track_str = f"\n\nПоследний трек: **{t.get('title', 'Unknown')}** — *{t.get('artist', 'Unknown')}*"
            
            # Очищаем очередь, но оставляем голосового клиента
            state["tracks"] = []
            state["index"] = 0
            state["played_count"] = 0
            state["current_track_id"] = None
            state["source"] = None
            
            if state.get("np_msg"):
                try:
                    client = await self.get_ym_client(guild_id)
                    if not client:
                        embed = discord.Embed(
                            title="🔑 Вход в Яндекс",
                            description="Бот работает в режиме 24/7. Для запуска музыки авторизуйте бота в Яндекс.Музыке.",
                            color=discord.Color.red()
                        )
                        view = YMAuthView()
                    else:
                        embed = discord.Embed(
                            title="📻 Яндекс.Музыка",
                            description=f"⏹️ **Очередь воспроизведения завершена.**{last_track_str}\n\nБот ожидает новые треки. Нажмите **Моя Волна** 🌊 или воспользуйтесь `/ym play`.",
                            color=discord.Color.from_rgb(255, 204, 0)
                        )
                        view = YMReadyView()
                    await state["np_msg"].edit(embed=embed, view=view)
                except Exception:
                    state["np_msg"] = None
            else:
                if state["channel"] and message:
                    await state["channel"].send(message)

    # ──────────────────────────────────────────
    # Отправка UI сообщений
    # ──────────────────────────────────────────

    def get_current_time(self, guild_id: int) -> int:
        state = self.get_state(guild_id)
        if not state.get("playback_start_time"):
            return 0
        vc = self._voice_clients.get(guild_id)
        if vc and vc.is_paused():
            return int(state.get("playback_elapsed", 0))
        now = time.time()
        return int(state.get("playback_elapsed", 0) + (now - state["playback_start_time"]))

    def _build_now_playing_embed(self, guild_id: int) -> discord.Embed:
        state = self.get_state(guild_id)
        track = state["tracks"][state["index"]]
        pos = state.get("played_count", 0) + state["index"] + 1
        total = "∞" if state["source"] in ("wave", "radio") else len(state["tracks"]) + len(state.get("pending_tracks", []))

        elapsed = self.get_current_time(guild_id)
        duration_sec = int(track.get("duration", 0) / 1000) if track.get("duration") else None
        progress_bar = create_progress_bar(elapsed, duration_sec)

        embed = discord.Embed(
            title=f"{track['artist']} — {track['title']}",
            description=f"▶️ **Прогресс:**\n{progress_bar}",
            color=discord.Color.from_rgb(255, 204, 0)  # Желтый
        )

        embed.add_field(
            name="📻 Статус",
            value=format_player_status(is_paused=state.get("is_paused", False)),
            inline=True
        )
        embed.add_field(
            name="📋 Очередь",
            value=f"`{pos} / {total}`",
            inline=True
        )
        embed.add_field(
            name="🔊 Громкость",
            value=f"`{int(self._volume.get(guild_id, 0.5) * 100)}%`",
            inline=True
        )
        if track.get("user_id"):
            embed.add_field(
                name="👥 Подмешано от",
                value=f"<@{track['user_id']}>",
                inline=True
            )
        if track["cover"]:
            embed.set_thumbnail(url="https://" + track["cover"])

        from utils.blend import blend_manager
        blend_session = blend_manager.get_session_sync(guild_id)

        footer = "Источник: "
        if blend_session and blend_session.active_participants:
            footer += f"Совместная Волна 🔀 (👥 {len(blend_session.active_participants)} участников)"
        else:
            track_source = track.get("source") or state["source"]
            if track_source in ("wave", "blend"):
                footer += "Моя волна 🌊"
            elif track_source == "radio":
                footer += "Радиостанция 📻"
            elif track_source == "search":
                footer += "Поиск 🔍"
            elif track_source == "album":
                footer += "Альбом 💿"
            elif track_source == "artist":
                footer += "Артист 👤"
            elif track_source == "playlist":
                footer += "Плейлист 📋"
            else:
                footer += "Очередь 📋"

        if state["loop"]:
            footer += " • 🔁 Повтор"
        if state["shuffle"]:
            footer += " • 🔀 Шаффл"

        embed.set_footer(text=footer)
        return embed

    async def send_now_playing(self, guild_id: int) -> None:
        """Отправляет или обновляет текущее сообщение Now Playing."""
        state = self.get_state(guild_id)
        if not state["channel"] or state["index"] >= len(state["tracks"]):
            return

        embed = self._build_now_playing_embed(guild_id)
        view = YMPlayerView(queue=state["tracks"], current_index=state["index"])

        try:
            if state["np_msg"]:
                await state["np_msg"].edit(content="", embed=embed, view=view)
            else:
                state["np_msg"] = await state["channel"].send(embed=embed, view=view)
        except Exception as _edit_err:
            # Если сообщение удалили или недоступно — шлём новое
            logger.warning("[send_now_playing] edit failed (%s), sending new message", _edit_err)
            try:
                state["np_msg"] = await state["channel"].send(embed=embed, view=view)
            except Exception as _send_err:
                logger.error("[send_now_playing] failed to send new message: %s", _send_err)
                state["np_msg"] = None



    async def send_player_panel(self, interaction: discord.Interaction) -> None:
        """Отрисовывает интерфейс плеера (в ответ на команду или кнопку)."""
        guild_id = interaction.guild_id
        state = self.get_state(guild_id)
        state["channel"] = interaction.channel

        # Если уже играет трек
        if state["tracks"] and state["index"] < len(state["tracks"]):
            embed = self._build_now_playing_embed(guild_id)
            view = YMPlayerView(queue=state["tracks"], current_index=state["index"])
        else:
            embed = discord.Embed(
                title="📻 Яндекс.Музыка",
                description="Готов к проигрыванию. Нажмите **Моя Волна** 🌊 или воспользуйтесь слэш-командой `/ym play` для поиска.",
                color=discord.Color.from_rgb(255, 204, 0)
            )
            view = YMReadyView()

        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=view)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=False)

    def _build_config_embed(self, guild_id: int, settings: dict) -> discord.Embed:
        keep_alive = settings.get("keep_alive", False)
        logout_on_disconnect = settings.get("logout_on_disconnect", False)
        control_mode = settings.get("control_mode", "everyone")
        like_mode = settings.get("like_mode", "owner_only")
        dj_roles = settings.get("dj_role_ids", [])
        last_channel_id = settings.get("last_channel_id")

        control_mode_mapping = {
            "everyone": "Все пользователи в канале",
            "owner_only": "Только владелец комнаты / инициатор",
            "dj_only": "Только пользователи с ролью DJ"
        }

        like_mode_mapping = {
            "everyone": "Все пользователи в канале",
            "owner_only": "Только владелец комнаты / инициатор",
            "off": "Лайки отключены ❌"
        }

        roles_str = ", ".join(f"<@&{r_id}>" for r_id in dj_roles) if dj_roles else "❌ Не настроены"
        channel_str = f"<#{last_channel_id}>" if last_channel_id else "❌ Не выбран"

        embed = discord.Embed(
            title="🎵 Настройки Яндекс.Музыки",
            description="Здесь вы можете изменить глобальные параметры Яндекс.Музыки для сервера.",
            color=discord.Color.from_rgb(255, 204, 0)
        )
        embed.add_field(name="📻 Режим 24/7", value="🟢 Включен" if keep_alive else "🔴 Выключен", inline=True)
        embed.add_field(name="🚪 Выход при дисконнекте", value="🟢 Включен" if logout_on_disconnect else "🔴 Выключен", inline=True)
        embed.add_field(name="🎛️ Кто может управлять", value=control_mode_mapping.get(control_mode, "Все"), inline=True)
        embed.add_field(name="❤️ Кто может лайкать", value=like_mode_mapping.get(like_mode, "Владелец"), inline=True)
        embed.add_field(name="🎧 Роли DJ", value=roles_str, inline=False)
        embed.add_field(name="🔊 Канал 24/7", value=channel_str, inline=False)
        embed.set_footer(text="Изменения вступают в силу немедленно")
        return embed

    # ──────────────────────────────────────────
    # Обработчики кнопок
    # ──────────────────────────────────────────

    async def play_prev(self, interaction: discord.Interaction) -> None:
        """Вернуться к предыдущему треку."""
        if not await self._check_interaction_permissions(interaction, mode="control"):
            return

        guild_id = interaction.guild_id
        state = self.get_state(guild_id)
        if not state["prev_history"]:
            await interaction.response.send_message("⏮️ Нет предыдущих треков в истории.", ephemeral=True)
            return

        await interaction.response.defer()
        prev_idx = state["prev_history"].pop()
        state["index"] = prev_idx
        await self.play_track(guild_id)

    async def skip_track(self, interaction: discord.Interaction) -> None:
        """Пропустить текущий трек."""
        if not await self._check_interaction_permissions(interaction, mode="control"):
            return

        guild_id = interaction.guild_id
        state = self.get_state(guild_id)
        vc = self._voice_clients.get(guild_id)
        if not vc or not vc.is_connected():
            await interaction.response.send_message("❌ Бот не подключен к голосовому каналу.", ephemeral=True)
            return

        await interaction.response.defer()

        # Отправляем фидбек о скипе
        client = await self.get_ym_client(guild_id)
        if client and state["source"] in ("wave", "radio") and state["current_track_id"] and state["station_id"]:
            try:
                curr_track = state["tracks"][state["index"]]
                played_sec = 10  # Заглушка, если нет точной информации о сыгранном
                await client.rotor_station_feedback_skip(
                    station=state["station_id"],
                    track_id=state["current_track_id"],
                    total_played_seconds=played_sec,
                    batch_id=curr_track["batch_id"]
                )
            except Exception:
                pass

        if state["shuffle"] and state["source"] not in ("wave", "radio") and len(state["tracks"]) > 1:
            import random
            state["index"] = random.randint(0, len(state["tracks"]) - 1)
        else:
            state["index"] += 1

        await self.play_track(guild_id)

    async def jump_to_track(self, interaction: discord.Interaction, target_index: int) -> None:
        """Переключение на указанный трек в очереди по индексу с отправкой фидбека в Яндекс.Музыку."""
        if not await self._check_interaction_permissions(interaction, mode="control"):
            return

        guild_id = interaction.guild_id
        state = self.get_state(guild_id)
        vc = self._voice_clients.get(guild_id)

        if not vc or not vc.is_connected():
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Плеер не подключен к голосовому каналу.", ephemeral=True)
            return

        if not state["tracks"] or target_index < 0 or target_index >= len(state["tracks"]):
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Указанный трек не найден в очереди.", ephemeral=True)
            return

        if not interaction.response.is_done():
            await interaction.response.defer()

        # Отправляем фидбек о пропуске всех пропущенных треков при Моей Волне / Радио
        client = await self.get_ym_client(guild_id)
        if client and state["source"] in ("wave", "radio") and state["station_id"]:
            current_i = state["index"]
            for i in range(current_i, target_index):
                if i < len(state["tracks"]):
                    t_item = state["tracks"][i]
                    if t_item.get("id") and t_item.get("batch_id"):
                        try:
                            await client.rotor_station_feedback_skip(
                                station=state["station_id"],
                                track_id=t_item["id"],
                                total_played_seconds=10,
                                batch_id=t_item["batch_id"]
                            )
                        except Exception:
                            pass

        state["index"] = target_index

        # Если остаток очереди менее 3 треков, дозапрашиваем новую волну
        if len(state["tracks"]) - state["index"] <= 3:
            if state["source"] in ("wave", "radio"):
                await self.refill_wave(guild_id, background=True)
            elif state.get("pending_tracks"):
                await self.refill_pending(guild_id)

        await self.play_track(guild_id)

    async def stop_playback(self, interaction: discord.Interaction) -> None:
        """Остановить воспроизведение и выйти."""
        if not await self._check_interaction_permissions(interaction, mode="control"):
            return
        await interaction.response.defer()
        await self._stop_and_cleanup(interaction.guild_id, "⏹️ Воспроизведение остановлено пользователем.")

    async def toggle_pause(self, interaction: discord.Interaction) -> None:
        """Поставить на паузу или снять с паузы."""
        if not await self._check_interaction_permissions(interaction, mode="control"):
            return
        
        guild_id = interaction.guild_id
        vc = self._voice_clients.get(guild_id)
        
        if not vc or not vc.is_connected():
            await interaction.response.send_message("❌ Плеер не подключен.", ephemeral=True)
            return

        if vc.is_paused():
            vc.resume()
            await interaction.response.send_message("▶️ Воспроизведение продолжено.", ephemeral=True)
        elif vc.is_playing():
            vc.pause()
            await interaction.response.send_message("⏸️ Воспроизведение приостановлено.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Сейчас ничего не играет.", ephemeral=True)

    async def change_volume(self, interaction: discord.Interaction, level: int) -> None:
        """Изменить громкость воспроизведения."""
        if not await self._check_interaction_permissions(interaction, mode="control"):
            return
        guild_id = interaction.guild_id
        volume = level / 100.0
        self._volume[guild_id] = volume
        await db.update_ym_volume(guild_id, volume)

        vc = self._voice_clients.get(guild_id)
        if vc and vc.source:
            vc.source.volume = volume * 0.50

        await interaction.response.defer()
        await self.send_now_playing(guild_id)

    async def toggle_shuffle(self, interaction: discord.Interaction) -> None:
        """Переключить режим шаффла."""
        if not await self._check_interaction_permissions(interaction, mode="control"):
            return
        state = self.get_state(interaction.guild_id)
        state["shuffle"] = not state["shuffle"]
        if state["shuffle"] and state.get("pending_tracks"):
            import random
            random.shuffle(state["pending_tracks"])
        await interaction.response.defer()
        await self.send_now_playing(interaction.guild_id)

    async def toggle_loop(self, interaction: discord.Interaction) -> None:
        """Переключить режим цикла."""
        if not await self._check_interaction_permissions(interaction, mode="control"):
            return
        state = self.get_state(interaction.guild_id)
        state["loop"] = not state["loop"]
        await interaction.response.defer()
        await self.send_now_playing(interaction.guild_id)

    async def show_queue(self, interaction: discord.Interaction) -> None:
        """Показать текущую очередь треков."""
        state = self.get_state(interaction.guild_id)
        total_active = len(state["tracks"])
        total_pending = len(state.get("pending_tracks", []))
        total = total_active + total_pending

        if not total:
            await interaction.response.send_message("📋 Очередь пуста.", ephemeral=True)
            return

        current_idx = state["index"]

        # Определение типа источника для заголовка очереди
        sources = set()
        for t in state["tracks"]:
            if t.get("source"):
                sources.add(t["source"])
        for item in state.get("pending_tracks", []):
            if isinstance(item, dict) and item.get("source"):
                sources.add(item["source"])
            elif state.get("pending_type"):
                sources.add(state["pending_type"])

        if len(sources) == 1:
            source_key = list(sources)[0]
        elif len(sources) > 1:
            source_key = "mixed"
        else:
            source_key = state.get("source") or ""

        source_labels = {
            "wave": "🌊 Моя волна",
            "radio": "📻 Радиостанция",
            "search": "🔍 Поиск",
            "playlist": "📋 Плейлист",
            "album": "💿 Альбом",
            "artist": "👤 Артист",
            "mixed": "📋 Смешанная очередь",
        }
        source_label = source_labels.get(source_key, "📋 Очередь")
        msg = f"**{source_label} — {total} треков:**\n\n"

        start = max(0, current_idx - 2)
        end = min(total, current_idx + 8)

        for i in range(start, end):
            if i < total_active:
                track = state["tracks"][i]
                prefix = "▶️ " if i == current_idx else f"`{i+1}.` "
                dur = format_duration(track["duration"])
                title = track["title"]
                artist = track["artist"]
            else:
                item = state["pending_tracks"][i - total_active]
                if isinstance(item, dict) and "track" in item:
                    track_raw = item["track"]
                else:
                    track_raw = item
                prefix = f"`{i+1}.` "
                dur = format_duration(track_raw.duration_ms)
                artists = ", ".join(a.name for a in track_raw.artists) if track_raw.artists else "Неизвестный исполнитель"
                title = track_raw.title or "Без названия"
                artist = artists

            msg += f"{prefix}**{title}** — {artist} (`{dur}`)\n"

        if total > end:
            msg += f"\n*...и ещё {total - end} треков в очереди.*"

        if state.get("source", "") != "wave":
            from views.ym_views import YMQueueClearView
            view = YMQueueClearView()
            await interaction.response.send_message(msg, view=view, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

    async def like_track(self, interaction: discord.Interaction) -> None:
        """Поставить лайк текущему треку на Яндекс.Музыке."""
        if not await self._check_interaction_permissions(interaction, mode="like"):
            return

        guild_id = interaction.guild_id
        state = self.get_state(guild_id)
        if not state["current_track_id"]:
            await interaction.response.send_message("❌ Сейчас ничего не воспроизводится.", ephemeral=True)
            return

        client = await self.get_ym_client(guild_id)
        if not client:
            await interaction.response.send_message("❌ Ошибка авторизации Яндекс.Музыки.", ephemeral=True)
            return

        try:
            await client.users_likes_tracks_add(state["current_track_id"])
            await interaction.response.send_message("❤️ Трек добавлен в ваши любимые на Яндекс.Музыке!", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ Не удалось поставить лайк: {e}", ephemeral=True)

    async def dislike_track(self, interaction: discord.Interaction) -> None:
        """Поставить дизлайк текущему треку и пропустить его."""
        if not await self._check_interaction_permissions(interaction, mode="like"):
            return

        guild_id = interaction.guild_id
        state = self.get_state(guild_id)
        if not state["current_track_id"]:
            await interaction.response.send_message("❌ Сейчас ничего не воспроизводится.", ephemeral=True)
            return

        client = await self.get_ym_client(guild_id)
        if not client:
            await interaction.response.send_message("❌ Ошибка авторизации Яндекс.Музыки.", ephemeral=True)
            return

        try:
            await client.users_dislikes_tracks_add(state["current_track_id"])
            await interaction.response.send_message("💔 Дизлайк! Трек больше не будет воспроизводиться в рекомендациях.", ephemeral=True)
            
            # Автоматически пропускаем трек
            if state["shuffle"] and state["source"] not in ("wave", "radio") and len(state["tracks"]) > 1:
                import random
                state["index"] = random.randint(0, len(state["tracks"]) - 1)
            else:
                state["index"] += 1
            
            await self.play_track(guild_id)
        except Exception as e:
            await interaction.response.send_message(f"❌ Не удалось отправить дизлайк: {e}", ephemeral=True)

    async def ensure_connection(self, interaction: discord.Interaction) -> discord.VoiceClient | None:
        """Подключается к голосовому каналу пользователя или перемещается в него."""
        voice_channel = await get_user_voice_channel(interaction)

        if not voice_channel:
            msg = "❌ Сначала зайдите в голосовой канал!\n*(Если вы уже там, проверьте, есть ли у бота права на **просмотр** этого канала!)*"
            await safe_send(interaction, msg, ephemeral=True)
            return None

        guild_id = interaction.guild_id
        guild = interaction.guild

        vc = await safe_voice_connect(guild, voice_channel, self_deaf=True)

        if not vc:
            await safe_send(
                interaction,
                "❌ Не удалось подключиться к голосовому каналу (превышено время ожидания подключения к сети).",
                ephemeral=True,
            )
            return None

        self._voice_clients[guild_id] = vc
        # Сохраняем последний голосовой канал в БД
        await db.update_ym_last_channel(guild_id, voice_channel.id)
        return vc

    async def play_by_search(self, interaction: discord.Interaction, query: str) -> None:
        """Ищет трек (или парсит ссылку) и воспроизводит его, сбрасывая очередь."""
        guild_id = interaction.guild_id
        vc_exists = self._voice_clients.get(guild_id) or interaction.guild.voice_client
        if vc_exists and vc_exists.is_connected():
            if not await self._check_interaction_permissions(interaction, mode="control"):
                return

        # Поддержка прямых ссылок Яндекс.Музыки через кнопку "Поиск"
        if "music.yandex.ru" in query:
            from utils.ym_url_parser import parse_ym_url
            parsed = parse_ym_url(query)
            if not parsed:
                await safe_send(interaction, "❌ Неверная или неподдерживаемая ссылка.", ephemeral=True)
                return

            state = self.get_state(guild_id)
            state["tracks"].clear()
            if vc_exists and (vc_exists.is_playing() or vc_exists.is_paused()):
                vc_exists.stop()
            return await self.play_from_url(interaction, parsed)

        await safe_defer(interaction)

        client = await self.get_ym_client(guild_id)
        if not client:
            await safe_send(interaction, "❌ Бот не авторизован в Яндекс.Музыке. Войдите под своим аккаунтом.", ephemeral=True)
            return

        vc = await self.ensure_connection(interaction)
        if not vc:
            return

        try:
            # Ищем треки
            search_result = await client.search(query, type_="track")
            tracks = search_result.tracks.results if search_result and search_result.tracks else []
            if not tracks:
                try:
                    await interaction.edit_original_response(content="❌ Ничего не найдено.", embed=None, view=None)
                except Exception:
                    await interaction.followup.send("❌ Ничего не найдено.")
                return

            track = tracks[0]
            state = self.get_state(guild_id)
            state.clear()
            state["initiator_id"] = interaction.user.id
            state["channel"] = interaction.channel
            state["source"] = "search"
            state["station_id"] = None
            state["batch_id"] = None

            try:
                msg = await interaction.original_response()
                await msg.delete()
            except Exception:
                pass
            if state.get("np_msg"):
                try:
                    await state["np_msg"].delete()
                except Exception:
                    pass
            new_msg = await interaction.channel.send(f"⏳ Скачиваю **{track.title}**...")
            state["np_msg"] = new_msg
            ok = await self.queue_track(guild_id, track, "search", None)
            if not ok:
                await interaction.followup.send("❌ Не удалось загрузить трек.")
                return

            await self.play_track(guild_id)
        except Exception as e:
            logger.error("Ошибка при воспроизведении через поиск: %s", e)
            await interaction.followup.send(f"❌ Произошла ошибка: {e}")

    async def show_playlists_menu(self, interaction: discord.Interaction) -> None:
        """Показывает меню выбора персональных подборок."""
        guild_id = interaction.guild_id
        vc_exists = self._voice_clients.get(guild_id) or interaction.guild.voice_client
        if vc_exists and vc_exists.is_connected():
            if not await self._check_interaction_permissions(interaction, mode="control"):
                return

        logger.info("show_playlists_menu вызван на сервере %s", guild_id)
        client = await self.get_ym_client(guild_id)
        if not client:
            await interaction.response.send_message("❌ Клиент Яндекс.Музыки не инициализирован.", ephemeral=True)
            return

        await interaction.response.send_message("⏳ Загружаю персональные подборки...", ephemeral=True)

        try:
            logger.info("Запрашиваем feed из Яндекс.Музыки...")
            feed = await client.feed()
            playlists = feed.generated_playlists or []
            logger.info("Получено generated_playlists: %s", len(playlists))

            options = []
            options.append(discord.SelectOption(
                label="Моя Волна 🌊",
                value="user:onyourwave",
                description="Бесконечный персональный поток рекомендаций"
            ))

            seen_values = {"user:onyourwave"}
            for p in playlists:
                pl = p.data
                if pl and pl.track_count > 0:
                    val = f"{pl.owner.uid}:{pl.kind}"
                    if val in seen_values:
                        continue
                    seen_values.add(val)

                    clean_title = pl.title.replace("\r", "").replace("\n", " ").strip()
                    desc = f"Песен: {pl.track_count}"
                    if pl.description:
                        clean_desc = pl.description.replace("\r", "").replace("\n", " ").strip()
                        desc += f" | {clean_desc[:50]}"
                    options.append(discord.SelectOption(
                        label=clean_title[:100],
                        value=val,
                        description=desc[:100]
                    ))

            logger.info("Подготовлено SelectOption элементов: %s", len(options))

            if not options:
                await interaction.edit_original_response(content="❌ Не удалось найти персональные подборки в вашем фиде Яндекс.Музыки.")
                return

            from views.ym_views import YMPlaylistsView
            view = YMPlaylistsView(options[:25])

            embed = discord.Embed(
                title="🌀 Подборки Яндекс.Музыки",
                description="Выберите подборку из списка ниже для воспроизведения:",
                color=discord.Color.from_rgb(255, 204, 0)
            )

            logger.info("Отправляем сообщение с селектом в чат...")
            await interaction.channel.send(embed=embed, view=view)
            await interaction.edit_original_response(content="🌀 Меню выбора отправлено в чат канала.")
            logger.info("Сообщение успешно отправлено.")
        except Exception as e:
            logger.error("Ошибка получения фида подборок: %s", e, exc_info=True)
            await interaction.edit_original_response(content=f"❌ Не удалось загрузить подборки: {e}")

    async def play_playlist(self, interaction: discord.Interaction, uid: int | str, kind: int | str, title: str) -> None:
        """Воспроизводит плейлист/подборку."""
        await safe_defer(interaction)

        guild_id = interaction.guild_id
        vc_exists = self._voice_clients.get(guild_id) or interaction.guild.voice_client
        if vc_exists and vc_exists.is_connected():
            if not await self._check_interaction_permissions(interaction, mode="control"):
                return

        client = await self.get_ym_client(guild_id)
        if not client:
            return

        vc = await self.ensure_connection(interaction)
        if not vc:
            return

        state = self.get_state(guild_id)
        try:
            msg = await interaction.original_response()
            await msg.delete()
        except Exception:
            pass
        if state.get("np_msg"):
            try:
                await state["np_msg"].delete()
            except Exception:
                pass
        new_msg = await interaction.channel.send(f"⏳ Загружаю подборку **{title}**...")
        state["np_msg"] = new_msg

        try:
            res = await client.users_playlists(kind, uid)
            if not res:
                if state["np_msg"]:
                    await state["np_msg"].edit(content="❌ Не удалось найти подборку.")
                else:
                    await interaction.followup.send("❌ Не удалось найти подборку.")
                return

            playlist = res[0] if isinstance(res, list) else res
            tracks_raw = await playlist.fetch_tracks_async()
            if not tracks_raw:
                if state["np_msg"]:
                    await state["np_msg"].edit(content="❌ Подборка пуста или недоступна.")
                else:
                    await interaction.followup.send("❌ Подборка пуста или недоступна.")
                return

            tracks_to_queue = [t.track for t in tracks_raw if t.track][:MAX_QUEUE]
            if not tracks_to_queue:
                if state["np_msg"]:
                    await state["np_msg"].edit(content="❌ Подборка пуста или недоступна.")
                else:
                    await interaction.followup.send("❌ Подборка пуста или недоступна.")
                return

            state.clear()
            state["initiator_id"] = interaction.user.id
            state["channel"] = interaction.channel
            state["source"] = "playlist"
            state["station_id"] = f"{uid}:{kind}"
            state["batch_id"] = None

            if state["shuffle"]:
                import random
                random.shuffle(tracks_to_queue)

            first_batch = tracks_to_queue[:5]
            remaining_tracks = tracks_to_queue[5:]

            state["pending_tracks"] = [
                {"track": t, "source": "playlist", "station_id": f"{uid}:{kind}"}
                for t in remaining_tracks
            ]
            state["pending_type"] = "playlist"
            state["pending_source_id"] = f"{uid}:{kind}"

            # Скачиваем и запускаем первый трек
            first_track = first_batch[0]
            ok = await self.queue_track(guild_id, first_track, "playlist", f"{uid}:{kind}")
            if not ok:
                await interaction.followup.send("❌ Не удалось запустить первый трек подборки.")
                return

            await self.play_track(guild_id)

            # Фоном качаем остальные из первого батча
            current_version = state.version
            async def load_initial_batch():
                for track in first_batch[1:]:
                    if state.version != current_version:
                        break
                    if state["source"] != "playlist" or state["station_id"] != f"{uid}:{kind}":
                        break
                    if await self.queue_track(guild_id, track, "playlist", f"{uid}:{kind}"):
                        await self.send_now_playing(guild_id)

            task = asyncio.create_task(load_initial_batch())
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)
        except Exception as e:
            logger.error("Ошибка при воспроизведении подборки: %s", e)
            await interaction.followup.send(f"❌ Произошла ошибка: {e}")

    async def start_wave(self, interaction: discord.Interaction) -> None:
        """Запускает Мою Волну (бесконечный поток)."""
        await safe_defer(interaction)

        guild_id = interaction.guild_id
        vc_exists = self._voice_clients.get(guild_id) or interaction.guild.voice_client
        if vc_exists and vc_exists.is_connected():
            if not await self._check_interaction_permissions(interaction, mode="control"):
                return

        client = await self.get_ym_client(guild_id)
        if not client:
            # Показываем панель входа
            embed = discord.Embed(
                title="🔑 Вход в Яндекс",
                description="Для запуска **Моей Волны** сначала авторизуйте бота в Яндекс.Музыке.",
                color=discord.Color.red()
            )
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, view=YMAuthView(), ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, view=YMAuthView(), ephemeral=True)
            return

        # 1. Сбрасываем старое состояние и инициализируем новое
        state = self.get_state(guild_id)
        if state.get("np_msg"):
            try:
                await state["np_msg"].delete()
            except Exception:
                pass

        state.clear()
        state["initiator_id"] = interaction.user.id
        state["channel"] = interaction.channel
        state["source"] = "wave"
        state["station_id"] = "user:onyourwave"

        # 2. Мгновенно отправляем индикатор загрузки
        new_msg = await interaction.channel.send("🌊 Загружаю **Мою волну**...")
        state["np_msg"] = new_msg

        # 3. Подключаемся к голосовому каналу
        vc = await self.ensure_connection(interaction)
        if not vc:
            if state.get("np_msg"):
                try:
                    await state["np_msg"].delete()
                except Exception:
                    pass
            state.clear()
            return

        # Отправляем фидбек о старте радио
        try:
            task = asyncio.create_task(
                self._safe_feedback(
                    client.rotor_station_feedback_radio_started(
                        station=state["station_id"],
                        from_="web-radio-user-onyourwave"
                    )
                )
            )
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)
        except Exception as e:
            logger.warning("Не удалось отправить radioStarted фидбек для сервера %s: %s", guild_id, e)

        # Заполняем первую партию
        ok = await self.refill_wave(guild_id, background=False)
        if not ok or not state["tracks"]:
            if state["np_msg"]:
                await state["np_msg"].edit(content="❌ Не удалось получить рекомендации Яндекс.Музыки.")
            else:
                await interaction.followup.send("❌ Не удалось получить рекомендации Яндекс.Музыки.")
            return

        await self.play_track(guild_id)

        # Убираем деферед "Bot is thinking..." — карточка плеера отправлена через channel.send
        try:
            await interaction.delete_original_response()
        except Exception:
            pass


    # ──────────────────────────────────────────
    # Добавление в очередь / URL-обработка
    # ──────────────────────────────────────────

    async def add_to_queue(self, interaction: discord.Interaction, query: str) -> None:
        """Ищет трек по запросу и добавляет в конец текущей очереди без сброса."""
        guild_id = interaction.guild_id

        client = await self.get_ym_client(guild_id)
        if not client:
            await interaction.response.send_message(
                "❌ Бот не авторизован в Яндекс.Музыке.", ephemeral=True
            )
            return

        await safe_defer(interaction, ephemeral=True)

        try:
            search_result = await client.search(query, type_="track")
            tracks = (
                search_result.tracks.results
                if search_result and search_result.tracks
                else []
            )
            if not tracks:
                await interaction.followup.send("❌ Ничего не найдено.", ephemeral=True)
                return

            track = tracks[0]
            state = self.get_state(guild_id)

            ok = await self.queue_track(guild_id, track, "search", None)
            if not ok:
                await interaction.followup.send(
                    "❌ Не удалось загрузить трек.", ephemeral=True
                )
                return

            artists = (
                ", ".join(a.name for a in track.artists)
                if track.artists
                else "Неизвестный исполнитель"
            )

            # Если бот не играет — запустить воспроизведение
            vc = self._voice_clients.get(guild_id)
            if not vc or not vc.is_connected() or (
                not vc.is_playing() and not vc.is_paused()
            ):
                vc = await self.ensure_connection(interaction)
                if not vc:
                    return
                state["channel"] = interaction.channel
                state["source"] = state.get("source") or "search"
                state["index"] = len(state["tracks"]) - 1
                await interaction.followup.send(
                    f"▶️ Запускаю: **{track.title}** — {artists}",
                    ephemeral=True,
                )
                await self.play_track(guild_id)
            else:
                pos = len(state["tracks"])
                await interaction.followup.send(
                    f"✅ Добавлено в очередь (`#{pos}`): **{track.title}** — {artists}",
                    ephemeral=True,
                )
                await self.send_now_playing(guild_id)
        except Exception as e:
            logger.error("Ошибка при добавлении в очередь: %s", e, exc_info=True)
            await interaction.followup.send(
                f"❌ Произошла ошибка: {e}", ephemeral=True
            )

    async def play_album(self, interaction: discord.Interaction, album_id: int) -> None:
        """Воспроизводит альбом по его ID."""
        guild_id = interaction.guild_id
        client = await self.get_ym_client(guild_id)
        if not client:
            await interaction.response.send_message(
                "❌ Бот не авторизован в Яндекс.Музыке.", ephemeral=True
            )
            return

        vc = await self.ensure_connection(interaction)
        if not vc:
            return

        await safe_defer(interaction)

        try:
            album = await client.albums_with_tracks(album_id)
            if not album or not album.volumes:
                await interaction.followup.send(
                    "❌ Альбом не найден или пуст.", ephemeral=True
                )
                return

            all_tracks = [t for vol in album.volumes for t in vol]
            if not all_tracks:
                await interaction.followup.send(
                    "❌ В альбоме нет доступных треков.", ephemeral=True
                )
                return

            tracks_to_queue = all_tracks[:MAX_QUEUE]
            album_title = album.title or "Альбом"

            state = self.get_state(guild_id)
            try:
                msg = await interaction.original_response()
                await msg.delete()
            except Exception:
                pass
            if state.get("np_msg"):
                try:
                    await state["np_msg"].delete()
                except Exception:
                    pass
            new_msg = await interaction.channel.send(
                f"💿 Загружаю альбом **{album_title}** ({len(tracks_to_queue)} треков)..."
            )
            state["np_msg"] = new_msg

            state.clear()
            state["initiator_id"] = interaction.user.id
            state["channel"] = interaction.channel
            state["source"] = "album"
            state["station_id"] = str(album_id)
            state["batch_id"] = None

            if state["shuffle"]:
                import random
                random.shuffle(tracks_to_queue)

            first_batch = tracks_to_queue[:5]
            remaining_tracks = tracks_to_queue[5:]

            state["pending_tracks"] = [
                {"track": t, "source": "album", "station_id": str(album_id)}
                for t in remaining_tracks
            ]
            state["pending_type"] = "album"
            state["pending_source_id"] = str(album_id)

            # Скачиваем и запускаем первый трек
            first_track = first_batch[0]
            ok = await self.queue_track(guild_id, first_track, "album", str(album_id))
            if not ok:
                await interaction.followup.send(
                    "❌ Не удалось загрузить первый трек альбома."
                )
                return

            await self.play_track(guild_id)

            # Фоном качаем остальные из первого батча
            current_version = state.version
            async def load_initial_batch() -> None:
                for track in first_batch[1:]:
                    if state.version != current_version:
                        break
                    if (
                        state["source"] != "album"
                        or state["station_id"] != str(album_id)
                    ):
                        break
                    if await self.queue_track(guild_id, track, "album", str(album_id)):
                        await self.send_now_playing(guild_id)

            task = asyncio.create_task(load_initial_batch())
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)
        except Exception as e:
            logger.error("Ошибка при воспроизведении альбома %s: %s", album_id, e, exc_info=True)
            await interaction.followup.send(f"❌ Произошла ошибка: {e}")

    async def play_artist_top(
        self, interaction: discord.Interaction, artist_id: int
    ) -> None:
        """Воспроизводит топ треков артиста."""
        guild_id = interaction.guild_id
        client = await self.get_ym_client(guild_id)
        if not client:
            await interaction.response.send_message(
                "❌ Бот не авторизован в Яндекс.Музыке.", ephemeral=True
            )
            return

        vc = await self.ensure_connection(interaction)
        if not vc:
            return

        await safe_defer(interaction)

        try:
            result = await client.artists_tracks(artist_id, page_size=100)
            if not result or not result.tracks:
                await interaction.followup.send(
                    "❌ Треки артиста не найдены.", ephemeral=True
                )
                return

            tracks_to_queue = result.tracks[:MAX_QUEUE]

            # Получаем имя артиста
            artist_name = "Артист"
            try:
                artist_info = await client.artists([artist_id])
                if artist_info:
                    artist_name = artist_info[0].name or "Артист"
            except Exception:
                pass

            state = self.get_state(guild_id)
            try:
                msg = await interaction.original_response()
                await msg.delete()
            except Exception:
                pass
            if state.get("np_msg"):
                try:
                    await state["np_msg"].delete()
                except Exception:
                    pass
            new_msg = await interaction.channel.send(
                f"👤 Загружаю топ треков **{artist_name}** ({len(tracks_to_queue)} треков)..."
            )
            state["np_msg"] = new_msg

            state.clear()
            state["initiator_id"] = interaction.user.id
            state["channel"] = interaction.channel
            state["source"] = "artist"
            state["station_id"] = str(artist_id)
            state["batch_id"] = None

            if state["shuffle"]:
                import random
                random.shuffle(tracks_to_queue)

            first_batch = tracks_to_queue[:5]
            remaining_tracks = tracks_to_queue[5:]

            state["pending_tracks"] = [
                {"track": t, "source": "artist", "station_id": str(artist_id)}
                for t in remaining_tracks
            ]
            state["pending_type"] = "artist"
            state["pending_source_id"] = str(artist_id)

            first_track = first_batch[0]
            ok = await self.queue_track(
                guild_id, first_track, "artist", str(artist_id)
            )
            if not ok:
                await interaction.followup.send(
                    "❌ Не удалось загрузить первый трек."
                )
                return

            await self.play_track(guild_id)

            # Фоном качаем остальные из первого батча
            current_version = state.version
            async def load_initial_batch() -> None:
                for track in first_batch[1:]:
                    if state.version != current_version:
                        break
                    if (
                        state["source"] != "artist"
                        or state["station_id"] != str(artist_id)
                    ):
                        break
                    if await self.queue_track(guild_id, track, "artist", str(artist_id)):
                        await self.send_now_playing(guild_id)

            task = asyncio.create_task(load_initial_batch())
            self._bg_tasks.add(task)
            task.add_done_callback(self._bg_tasks.discard)
        except Exception as e:
            logger.error(
                "Ошибка при воспроизведении артиста %s: %s",
                artist_id, e, exc_info=True,
            )
            await interaction.followup.send(f"❌ Произошла ошибка: {e}")

    async def play_from_url(
        self, interaction: discord.Interaction, parsed: "YMParsedLink"
    ) -> None:
        """Воспроизводит или добавляет в очередь контент по распарсенной ссылке Яндекс.Музыки."""
        from utils.ym_url_parser import YMParsedLink  # type: ignore[misc]

        guild_id = interaction.guild_id
        client = await self.get_ym_client(guild_id)
        if not client:
            await interaction.response.send_message(
                "❌ Бот не авторизован в Яндекс.Музыке.", ephemeral=True
            )
            return

        vc = await self.ensure_connection(interaction)
        if not vc:
            return

        # Если бот уже играет/поставлен на паузу, добавляем ссылку в очередь
        is_playing = vc.is_playing() or vc.is_paused()

        if is_playing and parsed.type != "track":
            await safe_defer(interaction, ephemeral=True)
            try:
                state = self.get_state(guild_id)
                if parsed.type == "playlist_uuid":
                    playlist = await client.playlist(parsed.playlist_uuid)
                    if not playlist:
                        await interaction.followup.send("❌ Плейлист не найден.", ephemeral=True)
                        return
                    tracks_raw = await playlist.fetch_tracks_async()
                    tracks_to_queue = [t.track for t in tracks_raw if t.track][:MAX_QUEUE]

                    await interaction.followup.send(
                        f"✅ Добавлен плейлист **{playlist.title}** в очередь ({len(tracks_to_queue)} треков).",
                        ephemeral=True
                    )

                    new_pending = [
                        {"track": t, "source": "playlist", "station_id": parsed.playlist_uuid}
                        for t in tracks_to_queue
                    ]
                    state["pending_tracks"].extend(new_pending)
                    if len(state["tracks"]) - state["index"] < 5:
                        asyncio.create_task(self.refill_pending(guild_id))
                    await self.send_now_playing(guild_id)

                elif parsed.type == "album":
                    album = await client.albums_with_tracks(parsed.album_id)
                    if not album or not album.volumes:
                        await interaction.followup.send("❌ Альбом не найден или пуст.", ephemeral=True)
                        return
                    all_tracks = [t for vol in album.volumes for t in vol]
                    tracks_to_queue = all_tracks[:MAX_QUEUE]

                    await interaction.followup.send(
                        f"✅ Добавлен альбом **{album.title}** в очередь ({len(tracks_to_queue)} треков).",
                        ephemeral=True
                    )

                    new_pending = [
                        {"track": t, "source": "album", "station_id": str(parsed.album_id)}
                        for t in tracks_to_queue
                    ]
                    state["pending_tracks"].extend(new_pending)
                    if len(state["tracks"]) - state["index"] < 5:
                        asyncio.create_task(self.refill_pending(guild_id))
                    await self.send_now_playing(guild_id)

                elif parsed.type == "artist":
                    artist = await client.artists([parsed.artist_id])
                    if not artist:
                        await interaction.followup.send("❌ Артист не найден.", ephemeral=True)
                        return
                    tracks_raw = await client.artists_tracks(parsed.artist_id, page_size=100)
                    tracks_to_queue = tracks_raw.tracks[:MAX_QUEUE]

                    await interaction.followup.send(
                        f"✅ Добавлен топ артиста **{artist[0].name}** в очередь ({len(tracks_to_queue)} треков).",
                        ephemeral=True
                    )

                    new_pending = [
                        {"track": t, "source": "artist", "station_id": str(parsed.artist_id)}
                        for t in tracks_to_queue
                    ]
                    state["pending_tracks"].extend(new_pending)
                    if len(state["tracks"]) - state["index"] < 5:
                        asyncio.create_task(self.refill_pending(guild_id))
                    await self.send_now_playing(guild_id)

                return
            except Exception as e:
                logger.error("Ошибка при добавлении ссылки в очередь: %s", e, exc_info=True)
                await interaction.followup.send(f"❌ Не удалось добавить в очередь: {e}", ephemeral=True)
                return

        if parsed.type == "playlist_uuid":
            # Плейлист нового формата (UUID)
            await safe_defer(interaction)

            try:
                playlist = await client.playlist(parsed.playlist_uuid)
                if not playlist:
                    await interaction.followup.send(
                        "❌ Плейлист не найден.", ephemeral=True
                    )
                    return

                tracks_raw = await playlist.fetch_tracks_async()
                if not tracks_raw:
                    await interaction.followup.send(
                        "❌ Плейлист пуст.", ephemeral=True
                    )
                    return

                tracks_to_queue = [t.track for t in tracks_raw if t.track][:MAX_QUEUE]
                if not tracks_to_queue:
                    await interaction.followup.send(
                        "❌ В плейлисте нет доступных треков.", ephemeral=True
                    )
                    return

                pl_title = playlist.title or "Плейлист"

                state = self.get_state(guild_id)
                try:
                    msg = await interaction.original_response()
                    await msg.delete()
                except Exception:
                    pass
                if state.get("np_msg"):
                    try:
                        await state["np_msg"].delete()
                    except Exception:
                        pass
                new_msg = await interaction.channel.send(
                    f"📋 Загружаю плейлист **{pl_title}** ({len(tracks_to_queue)} треков)..."
                )
                state["np_msg"] = new_msg

                state.clear()
                state["initiator_id"] = interaction.user.id
                state["channel"] = interaction.channel
                state["source"] = "playlist"
                state["station_id"] = parsed.playlist_uuid
                state["batch_id"] = None

                if state["shuffle"]:
                    import random
                    random.shuffle(tracks_to_queue)

                first_batch = tracks_to_queue[:5]
                remaining_tracks = tracks_to_queue[5:]

                state["pending_tracks"] = [
                    {"track": t, "source": "playlist", "station_id": parsed.playlist_uuid}
                    for t in remaining_tracks
                ]
                state["pending_type"] = "playlist"
                state["pending_source_id"] = parsed.playlist_uuid

                first_track = first_batch[0]
                ok = await self.queue_track(
                    guild_id, first_track, "playlist", parsed.playlist_uuid
                )
                if not ok:
                    await interaction.followup.send(
                        "❌ Не удалось загрузить первый трек."
                    )
                    return

                await self.play_track(guild_id)

                # Фоном качаем остальные из первого батча
                current_version = state.version
                async def load_initial_batch() -> None:
                    for track in first_batch[1:]:
                        if state.version != current_version:
                            break
                        if (
                            state["source"] != "playlist"
                            or state["station_id"] != parsed.playlist_uuid
                        ):
                            break
                        if await self.queue_track(guild_id, track, "playlist", parsed.playlist_uuid):
                            await self.send_now_playing(guild_id)

                task = asyncio.create_task(load_initial_batch())
                self._bg_tasks.add(task)
                task.add_done_callback(self._bg_tasks.discard)
            except Exception as e:
                logger.error(
                    "Ошибка при воспроизведении плейлиста UUID %s: %s",
                    parsed.playlist_uuid, e, exc_info=True,
                )
                await interaction.followup.send(f"❌ Произошла ошибка: {e}")

        elif parsed.type == "playlist_legacy":
            # Плейлист старого формата — делегируем в существующий play_playlist
            uid = parsed.uid if parsed.uid is not None else parsed.playlist_uuid
            await self.play_playlist(
                interaction, uid, parsed.kind, "Плейлист"
            )

        elif parsed.type == "album":
            await self.play_album(interaction, parsed.album_id)

        elif parsed.type == "artist":
            await self.play_artist_top(interaction, parsed.artist_id)

        elif parsed.type == "track":
            # Одиночный трек — ищем по ID и добавляем/играем
            client = await self.get_ym_client(guild_id)
            if not client:
                await interaction.response.send_message(
                    "❌ Бот не авторизован в Яндекс.Музыке.", ephemeral=True
                )
                return

            vc = await self.ensure_connection(interaction)
            if not vc:
                return
            await safe_defer(interaction, ephemeral=True)

            try:
                tracks = await client.tracks([str(parsed.track_id)])
                if not tracks:
                    await interaction.followup.send(
                        "❌ Трек не найден.", ephemeral=True
                    )
                    return

                track = tracks[0]
                state = self.get_state(guild_id)

                ok = await self.queue_track(guild_id, track, "search", None)
                if not ok:
                    await interaction.followup.send(
                        "❌ Не удалось загрузить трек.", ephemeral=True
                    )
                    return

                artists = (
                    ", ".join(a.name for a in track.artists)
                    if track.artists
                    else "Неизвестный исполнитель"
                )

                # Если не играет — запустить
                if not vc.is_playing() and not vc.is_paused():
                    state["channel"] = interaction.channel
                    state["source"] = "search"
                    state["index"] = len(state["tracks"]) - 1
                    await interaction.followup.send(
                        f"▶️ Запускаю: **{track.title}** — {artists}",
                        ephemeral=True,
                    )
                    await self.play_track(guild_id)
                else:
                    pos = len(state["tracks"])
                    await interaction.followup.send(
                        f"✅ Добавлено в очередь (`#{pos}`): **{track.title}** — {artists}",
                        ephemeral=True,
                    )
                    await self.send_now_playing(guild_id)
            except Exception as e:
                logger.error(
                    "Ошибка при воспроизведении трека %s: %s",
                    parsed.track_id, e, exc_info=True,
                )
                await interaction.followup.send(
                    f"❌ Произошла ошибка: {e}", ephemeral=True
                )

    # ──────────────────────────────────────────
    # Слэш-команды
    # ──────────────────────────────────────────

    ym_group = app_commands.Group(name="ym", description="Управление Яндекс.Музыкой")

    @ym_group.command(name="play", description="Искать трек, плейлист или альбом на Яндекс.Музыке")
    @app_commands.describe(query="Название песни, ссылка на плейлист/альбом/артиста")
    async def ym_play(self, interaction: discord.Interaction, query: str) -> None:
        await safe_defer(interaction)

        if is_bot_busy_in_other_channel(interaction):
            await interaction.followup.send("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return

        if not await self._check_interaction_permissions(interaction, mode="control"):
            return

        # Сначала пробуем распарсить как ссылку ЯМ
        parsed = parse_ym_url(query)
        if parsed:
            await self.play_from_url(interaction, parsed)
            return

        # Если бот уже играет — добавляем в очередь
        vc = self._voice_clients.get(interaction.guild_id)
        if vc and vc.is_connected() and (vc.is_playing() or vc.is_paused()):
            await self.add_to_queue(interaction, query)
        else:
            # Если не играет — запуск с нуля (старое поведение)
            await self.play_by_search(interaction, query)

    @ym_group.command(name="wave", description="Включить Мою Волну (бесконечный поток)")
    async def ym_wave(self, interaction: discord.Interaction) -> None:
        await safe_defer(interaction)

        if is_bot_busy_in_other_channel(interaction):
            if interaction.response.is_done():
                await interaction.followup.send("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            else:
                await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return

        if not await self._check_interaction_permissions(interaction, mode="control"):
            return

        await self.start_wave(interaction)

    @ym_group.command(name="stop", description="Остановить плеер и выйти из канала")
    async def ym_stop(self, interaction: discord.Interaction) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return

        if not await self._check_interaction_permissions(interaction, mode="control"):
            return

        guild_id = interaction.guild_id
        vc = self._voice_clients.get(guild_id)
        if not vc or not vc.is_connected():
            await interaction.response.send_message("❌ Бот не подключен к голосовому каналу.", ephemeral=True)
            return

        settings = await db.get_ym_settings(guild_id)
        keep_alive = settings.get("keep_alive", False) if settings else False

        await interaction.response.send_message("⏹️ Воспроизведение остановлено.")
        await self._stop_and_cleanup(guild_id, "", disconnect=not keep_alive)

    @ym_group.command(name="panel", description="Показать панель управления Яндекс.Музыкой")
    async def ym_panel(self, interaction: discord.Interaction) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return

        if not await self._check_interaction_permissions(interaction, mode="control"):
            return

        client = await self.get_ym_client(interaction.guild_id)
        if not client:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="🔑 Вход в Яндекс.Музыку",
                    description="Для управления плеером сначала войдите под аккаунтом Яндекса.",
                    color=discord.Color.red()
                ),
                view=YMAuthView(),
                ephemeral=True
            )
            return

        await self.send_player_panel(interaction)

    @ym_group.command(name="auth", description="Авторизовать сервер в Яндекс.Музыке (Device Flow)")
    async def ym_auth(self, interaction: discord.Interaction) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        await self.start_auth_flow(interaction)

    @ym_group.command(name="config", description="Настроить Яндекс.Музыку на сервере (только для Админов)")
    async def ym_config_cmd(self, interaction: discord.Interaction) -> None:
        if not interaction.permissions.administrator:
            await interaction.response.send_message("❌ Только администраторы могут изменять настройки.", ephemeral=True)
            return

        settings = await db.get_ym_settings(interaction.guild_id)
        from views.ym_views import YMConfigView
        embed = self._build_config_embed(interaction.guild_id, settings)
        view = YMConfigView(interaction.guild, settings)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # ──────────────────────────────────────────
    # Автовыход из пустых каналов
    # ──────────────────────────────────────────

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Автовосстановление 24/7 голосовых подключений Яндекс.Музыки при старте."""
        await asyncio.sleep(5)
        
        logger.info("Яндекс.Музыка: запуск автовосстановления 24/7 подключений...")
        try:
            to_restore = await db.get_all_ym_configs_to_restore()
            for item in to_restore:
                guild_id = item["guild_id"]
                channel_id = item["last_channel_id"]
                
                guild = self.bot.get_guild(guild_id)
                if not guild:
                    continue
                    
                channel = guild.get_channel(channel_id)
                if not channel or not isinstance(channel, discord.VoiceChannel):
                    logger.warning("Яндекс.Музыка: канал %s не найден на сервере %s. Сбрасываем.", channel_id, guild_id)
                    await db.update_ym_last_channel(guild_id, None)
                    continue
                    
                bot_member = guild.get_member(self.bot.user.id)
                if not bot_member:
                    continue
                    
                permissions = channel.permissions_for(bot_member)
                if not permissions.connect or not permissions.speak:
                    logger.warning("Яндекс.Музыка: нет прав для подключения к каналу %s на сервере %s. Сбрасываем.", channel_id, guild_id)
                    await db.update_ym_last_channel(guild_id, None)
                    continue
                
                client = await self.get_ym_client(guild_id)
                if not client:
                    continue

                try:
                    vc = await ensure_voice_connection(guild, channel)
                except Exception as e:
                    logger.error("Яндекс.Музыка: не удалось автоподключиться к каналу %s: %s", channel_id, e)
                    continue

                self._voice_clients[guild_id] = vc

                # Если в канале есть пользователи, отправляем панель управления YM
                if has_listeners(channel):
                    state = self.get_state(guild_id)
                    if not state.get("np_msg"):
                        if not client:
                            from views.ym_views import YMAuthView
                            embed = discord.Embed(
                                title="🔑 Вход в Яндекс",
                                description="Бот работает в режиме 24/7. Для запуска музыки авторизуйте бота в Яндекс.Музыке.",
                                color=discord.Color.red()
                            )
                            view = YMAuthView()
                        else:
                            from views.ym_views import YMReadyView
                            embed = discord.Embed(
                                title="📻 Яндекс.Музыка",
                                description="Готов к проигрыванию. Нажмите **Волна** 🎵 или воспользуйтесь слэш-командой `/ym play` для поиска.",
                                color=discord.Color.from_rgb(255, 204, 0)
                            )
                            view = YMReadyView()
                        try:
                            new_msg = await vc.channel.send(embed=embed, view=view)
                            state["np_msg"] = new_msg
                            state["channel"] = vc.channel
                        except Exception as e:
                            logger.error("Яндекс.Музыка: ошибка отправки интерфейса при 24/7 автовосстановлении: %s", e)

                await asyncio.sleep(2)
        except Exception as e:
            logger.error("Ошибка в on_ready автовосстановления Яндекс.Музыки: %s", e, exc_info=True)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Автоматический выход из канала, если все пользователи вышли, или бот был отключен (с учетом keep_alive)."""
        guild_id = member.guild.id

        # Уведомляем BlendManager об изменении состава участников голосового канала
        channel = after.channel or before.channel
        if channel and hasattr(channel, 'members'):
            try:
                member_ids = {m.id for m in channel.members if getattr(m, 'bot', False) is False}
                from utils.blend import blend_manager
                await blend_manager.handle_voice_state_update(guild_id, channel.id, member_ids, self)
            except Exception as _be:
                logger.debug("BlendManager voice state update skipped: %s", _be)

        # Если это сам бот
        if member.id == self.bot.user.id:
            if before.channel and not after.channel:
                logger.info("Яндекс.Музыка: бот был отключен от канала %s (гильдия %s), очищаем состояние.", before.channel.id, guild_id)
                await self._stop_and_cleanup(guild_id, None, disconnect=True)
                
                # Проверяем логику logout_on_disconnect
                settings = await db.get_ym_settings(guild_id)
                if settings and settings.get("logout_on_disconnect"):
                    logger.info("Яндекс.Музыка: logout_on_disconnect включен, удаляем авторизацию.")
                    await db.delete_ym_config(guild_id)
                    self.unload_ym_client(guild_id)
                
                keep_alive = settings.get("keep_alive", False) if settings else False
                if not keep_alive:
                    await db.update_ym_last_channel(guild_id, None)
            return

        if not before.channel and after.channel:
            settings = await db.get_ym_settings(guild_id)
            keep_alive = settings.get("keep_alive", False) if settings else False
            last_channel_id = settings.get("last_channel_id") if settings else None
            
            if keep_alive and after.channel and (last_channel_id == after.channel.id or last_channel_id is None):
                non_bot = [m for m in after.channel.members if not m.bot]
                if len(non_bot) == 1:
                    vc = self._voice_clients.get(guild_id) or member.guild.voice_client
                    if not vc or not vc.is_connected():
                        # Проверяем, не занят ли бот другим плеером
                        lofi_cog = self.bot.get_cog("LofiRadio")
                        rutube_cog = self.bot.get_cog("RutubeMusic")
                        spotify_cog = self.bot.get_cog("SpotifyMusic")
                        if (lofi_cog and lofi_cog._voice_clients.get(guild_id)) or (rutube_cog and rutube_cog.get_state(guild_id).queue) or (spotify_cog and spotify_cog.get_state(guild_id).queue):
                            return
                        
                        try:
                            vc = await safe_voice_connect(member.guild, after.channel, self_deaf=True)
                            if not vc:
                                logger.warning("Яндекс.Музыка: автоподключение 24/7 не удалось для канала %s", after.channel.id)
                                return
                            self._voice_clients[guild_id] = vc
                        except Exception as e:
                            logger.error("Яндекс.Музыка: ошибка при автоподключении 24/7: %s", e)
                            return
                            
                    if vc and vc.channel and after.channel and vc.channel.id == after.channel.id:
                        logger.info("Яндекс.Музыка: пользователь зашел в пустой канал 24/7. Отправляем интерфейс.")
                        state = self.get_state(guild_id)
                        client = await self.get_ym_client(guild_id)
                        
                        if not client:
                            from views.ym_views import YMAuthView
                            embed = discord.Embed(
                                title="🔑 Вход в Яндекс",
                                description="Бот работает в режиме 24/7. Для запуска музыки авторизуйте бота в Яндекс.Музыке.",
                                color=discord.Color.red()
                            )
                            view = YMAuthView()
                        else:
                            from views.ym_views import YMReadyView
                            embed = discord.Embed(
                                title="📻 Яндекс.Музыка",
                                description="Готов к проигрыванию. Нажмите **Волна** 🎵 или воспользуйтесь слэш-командой `/ym play` для поиска.",
                                color=discord.Color.from_rgb(255, 204, 0)
                            )
                            view = YMReadyView()
                            
                        if state.get("np_msg"):
                            try:
                                await state["np_msg"].delete()
                            except Exception:
                                pass
                                
                        try:
                            new_msg = await vc.channel.send(embed=embed, view=view)
                            state["np_msg"] = new_msg
                            state["channel"] = vc.channel
                        except Exception as e:
                            logger.error("Ошибка при отправке панели после входа: %s", e)
            return

        vc = self._voice_clients.get(guild_id)
        if not vc or not vc.is_connected():
            return

        if vc.channel.id != before.channel.id:
            return

        non_bot = [m for m in before.channel.members if not m.bot]
        if len(non_bot) == 0:
            settings = await db.get_ym_settings(guild_id)
            keep_alive = settings.get("keep_alive", False) if settings else False
            logout_on_disconnect = settings.get("logout_on_disconnect", False) if settings else False

            if logout_on_disconnect:
                logger.info("Яндекс.Музыка: все вышли из канала %s (гильдия %s), logout_on_disconnect включен. Удаляем авторизацию.", before.channel.id, guild_id)
                await db.delete_ym_config(guild_id)
                self.reset_state(guild_id)

            if keep_alive:
                logger.info("Яндекс.Музыка: все вышли из канала %s (гильдия %s). Режим 24/7 активен: останавливаем плеер.", before.channel.id, guild_id)
                await self._stop_and_cleanup(guild_id, None, disconnect=False)
            else:
                logger.info("Яндекс.Музыка: все вышли из канала %s (гильдия %s), проверяем 24/7 для других плееров...", before.channel.id, guild_id)
                lofi_cfg = await db.get_lofi_config(guild_id)
                lofi_keep_alive = lofi_cfg.get("keep_alive", False) if lofi_cfg else False
                
                rutube_cfg = await db.get_rutube_config(guild_id)
                rutube_keep_alive = rutube_cfg.get("keep_alive", False) if rutube_cfg else False
                
                spotify_cfg = await db.get_spotify_config(guild_id)
                spotify_keep_alive = spotify_cfg.get("keep_alive", False) if spotify_cfg else False
                
                if lofi_keep_alive:
                    logger.info("Яндекс.Музыка: Канал %s переходит в режим ожидания 24/7 для Lofi. Оставляем бота в канале и передаем управление.", before.channel.id)
                    await self._stop_and_cleanup(guild_id, None, disconnect=False)
                    lofi_cog = self.bot.get_cog("LofiRadio")
                    if lofi_cog:
                        lofi_cog._voice_clients[guild_id] = vc
                        self._voice_clients.pop(guild_id, None)
                elif rutube_keep_alive:
                    logger.info("Яндекс.Музыка: Канал %s переходит в режим ожидания 24/7 для RuTube. Оставляем бота в канале и передаем управление.", before.channel.id)
                    await self._stop_and_cleanup(guild_id, None, disconnect=False)
                    self._voice_clients.pop(guild_id, None)
                elif spotify_keep_alive:
                    logger.info("Яндекс.Музыка: Канал %s переходит в режим ожидания 24/7 для Spotify. Оставляем бота в канале и передаем управление.", before.channel.id)
                    await self._stop_and_cleanup(guild_id, None, disconnect=False)
                    self._voice_clients.pop(guild_id, None)
                else:
                    await self._stop_and_cleanup(guild_id, None, disconnect=True)

    # ──────────────────────────────────────────
    # Очистка дискового кэша (LRU)
    # ──────────────────────────────────────────

    async def _cache_cleaner_loop(self) -> None:
        """Фоновый таск очистки дискового кэша от старых mp3-файлов."""
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                # Принудительный сбор мусора
                collected = gc.collect()
                try:
                    import resource
                    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                    rss_mb = round(rss_kb / 1024, 1)
                    logger.info("Фоновый GC: очищено %s объектов. Текущий RSS: %s MB", collected, rss_mb)
                except Exception:
                    logger.info("Фоновый GC: очищено %s объектов", collected)

                # Сканируем папку кэша
                files = []
                total_size = 0
                for name in os.listdir(CACHE_DIR):
                    path = os.path.join(CACHE_DIR, name)
                    if os.path.isfile(path) and name.endswith(".mp3"):
                        stat = os.stat(path)
                        total_size += stat.st_size
                        files.append((path, stat.st_atime, stat.st_size))

                # Если размер превышает лимит, чистим
                if total_size > MAX_CACHE_BYTES:
                    # Сортируем по времени последнего доступа (самые старые в начало)
                    files.sort(key=lambda x: x[1])
                    
                    bytes_to_delete = total_size - TARGET_CACHE_BYTES
                    deleted_bytes = 0
                    deleted_count = 0

                    for path, atime, size in files:
                        if deleted_bytes >= bytes_to_delete:
                            break
                        try:
                            os.remove(path)
                            deleted_bytes += size
                            deleted_count += 1
                        except Exception as e:
                            logger.warning("Не удалось удалить кэшированный трек %s: %s", path, e)

                    logger.info("Очистка кэша: удалено %s файлов (%s МБ)", deleted_count, round(deleted_bytes / (1024 * 1024), 2))
            except Exception as e:
                logger.error("Ошибка при очистке кэша: %s", e)

            # Спим 1 час
            await asyncio.sleep(3600)

    async def logout_yandex(self, interaction: discord.Interaction) -> None:
        """Выходит из аккаунта Яндекс.Музыки для текущей гильдии."""
        guild_id = interaction.guild_id
        await interaction.response.defer(ephemeral=True)

        try:
            # 1. Останавливаем воспроизведение и отключаем бота
            await self._stop_and_cleanup(guild_id, message="🚪 Выход из аккаунта.", disconnect=True)
            
            # 2. Удаляем конфигурацию из БД
            await db.delete_ym_config(guild_id)
            
            # 3. Полностью сбрасываем состояние сессии и выгружаем клиент
            self.reset_state(guild_id)
            
            # 4. Отправляем панель авторизации (YMAuthView) в канал
            embed = discord.Embed(
                title="📻 Яндекс.Музыка",
                description="Войдите в свой аккаунт Яндекс.Музыки, чтобы запустить Мою Волну или искать треки.",
                color=discord.Color.from_rgb(255, 204, 0)
            )
            from views.ym_views import YMAuthView
            await interaction.channel.send(embed=embed, view=YMAuthView())
            await interaction.followup.send("✅ Вы успешно вышли из аккаунта Яндекс.Музыки на этом сервере.", ephemeral=True)
        except Exception as e:
            logger.error("Ошибка при выходе из аккаунта на сервере %s: %s", guild_id, e, exc_info=True)
            await interaction.followup.send(f"❌ Не удалось выйти из аккаунта: {e}", ephemeral=True)

    async def _timeline_updater_loop(self) -> None:
        """Фоновый цикл обновления прогресс-бара плеера во время воспроизведения."""
        await run_timeline_updater_loop(self)

    # ──────────────────────────────────────────
    # Интеграция Smart Blend DJ (Совместная Волна)
    # ──────────────────────────────────────────

    async def add_blend_track_to_queue(self, guild_id: int, user_id: int, track: Any) -> bool:
        """Добавляет трек участника Совместной Волны в очередь гильдии."""
        state = self.get_state(guild_id)
        source = "blend"
        station_id = state.get("station_id") or "user:onyourwave"

        username = None
        try:
            token_info = await db.get_blend_user_token(user_id, guild_id)
            if token_info:
                username = token_info.get("username")
        except Exception:
            pass

        return await self.queue_track(
            guild_id,
            track,
            source,
            station_id,
            blend_user_id=user_id,
            blend_username=username,
        )

    async def remove_user_tracks_from_queue(self, guild_id: int, user_id: int, unplayed_tracks: list) -> None:
        """Удаляет несыгранные треки вышедшего участника волны из очереди."""
        state = self.get_state(guild_id)
        if not state.get("tracks"):
            return

        curr_idx = state.get("index", 0)
        new_tracks = []
        for idx, t in enumerate(state["tracks"]):
            if idx <= curr_idx or t.get("blend_user_id") != user_id:
                new_tracks.append(t)

        state["tracks"] = new_tracks

    async def join_blend_from_player(self, interaction: discord.Interaction) -> None:
        """Обрабатывает нажатие кнопки Совместная Волна прямо на панели плеера."""
        cfg = await db.get_blend_config(interaction.guild_id)
        if not cfg.get("blend_enabled", True):
            await interaction.response.send_message(
                "❌ Совместная Волна отключена администратором этого сервера.", ephemeral=True
            )
            return

        user_channel = await get_user_voice_channel(interaction)
        if not user_channel:
            await interaction.response.send_message("❌ Вы должны находиться в голосовом канале!", ephemeral=True)
            return

        user_token_data = await db.get_blend_user_token(interaction.user.id, interaction.guild_id)
        if not user_token_data or not user_token_data.get("decrypted_token") or not user_token_data.get("is_active"):
            embed = discord.Embed(
                title="🔗 Подключение Яндекс.Музыки",
                description=(
                    "Авторизуйтесь в Яндекс.Музыке, чтобы алгоритмы бота подбирали ваши любимые треки в общий микс ✨"
                ),
                color=discord.Color.from_rgb(255, 204, 0)
            )
            from views.ym_views import YMAuthView
            await interaction.response.send_message(embed=embed, view=YMAuthView(), ephemeral=True)
            return

        from utils.blend import blend_manager
        session = await blend_manager.get_or_create_session(interaction.guild_id, user_channel.id)
        session.add_participant(interaction.user.id)

        tracks_added = await blend_manager.generate_wave_batch(interaction.guild_id, self, target_user_ids={interaction.user.id})

        await interaction.response.send_message(
            f"✅ **Вы успешно присоединились к Совместной Волне в {user_channel.mention}!**\n"
            f"🎵 В общий микс добавлено **{tracks_added}** ваших предпочтений.",
            ephemeral=True
        )

    blend_group = app_commands.Group(name="blend", description="Управление Совместной Волной (Smart Blend DJ)")

    @blend_group.command(name="start", description="Запустить Совместную Волну в вашем голосовом канале")
    async def blend_start(self, interaction: discord.Interaction):
        cfg = await db.get_blend_config(interaction.guild_id)
        if not cfg.get("blend_enabled", True):
            await interaction.response.send_message("❌ Совместная Волна выключена администратором этого сервера.", ephemeral=True)
            return

        user_channel = await get_user_voice_channel(interaction)
        if not user_channel:
            await interaction.response.send_message("❌ Вы должны находиться в голосовом канале!", ephemeral=True)
            return

        channel = user_channel
        from utils.blend import blend_manager
        session = await blend_manager.get_or_create_session(interaction.guild_id, channel.id)
        
        members = [m for m in channel.members if not m.bot]
        added_count = 0
        for m in members:
            token_data = await db.get_blend_user_token(m.id, interaction.guild_id)
            if token_data and token_data.get("decrypted_token") and token_data.get("is_active"):
                session.add_participant(m.id)
                added_count += 1

        if added_count == 0:
            await interaction.response.send_message(
                "⚠️ Ни один из находящихся в канале участников еще не привязал Яндекс.Музыку.\n"
                "Используйте `/blend link` для привязки аккаунта.",
                ephemeral=True
            )
            return

        await interaction.response.defer()
        tracks_added = await blend_manager.generate_wave_batch(interaction.guild_id, self)
        await interaction.followup.send(
            f"🔀 **Совместная Волна запущена в {channel.mention}!**\n"
            f"👥 Активных участников: **{added_count}**\n"
            f"🎵 Добавлено треков в микс: **{tracks_added}**"
        )

    @blend_group.command(name="leave", description="Выйти из текущей Совместной Волны")
    async def blend_leave(self, interaction: discord.Interaction):
        try:
            from utils.blend import blend_manager
        except ModuleNotFoundError:
            from src.utils.blend import blend_manager

        session = await blend_manager.get_session(interaction.guild_id)
        if not session or interaction.user.id not in session.active_participants:
            await interaction.response.send_message("ℹ️ Вы не состояли в активной Совместной Волне на этом сервере.", ephemeral=True)
            return

        unplayed_tracks = session.remove_participant(interaction.user.id)
        await self.remove_user_tracks_from_queue(interaction.guild_id, interaction.user.id, unplayed_tracks)
        if not session.active_participants:
            await blend_manager.remove_session(interaction.guild_id, ym_cog=self)

        await interaction.response.send_message("🚪 **Вы вышли из Совместной Волны.**", ephemeral=True)

    @blend_group.command(name="stop", description="Остановить Совместную Волну на сервере")
    async def blend_stop(self, interaction: discord.Interaction):
        try:
            from utils.blend import blend_manager
        except ModuleNotFoundError:
            from src.utils.blend import blend_manager

        await blend_manager.remove_session(interaction.guild_id, ym_cog=self)
        await interaction.response.send_message("⏹️ **Совместная Волна остановлена.**", ephemeral=True)

    @blend_group.command(name="link", description="Привязать ваш аккаунт Яндекс.Музыки для Совместной Волны")
    async def blend_link(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🔗 Подключение Яндекс.Музыки",
            description=(
                "Авторизуйтесь в Яндекс.Музыке, чтобы алгоритмы бота подбирали ваши любимые треки в общий микс ✨"
            ),
            color=discord.Color.from_rgb(255, 204, 0)
        )
        from views.ym_views import YMAuthView
        await interaction.response.send_message(embed=embed, view=YMAuthView(), ephemeral=True)

    @blend_group.command(name="unlink", description="Удалить ваш токен Яндекс.Музыки из Совместной Волны (GDPR)")
    async def blend_unlink(self, interaction: discord.Interaction):
        try:
            from utils.blend import blend_manager
        except ModuleNotFoundError:
            from src.utils.blend import blend_manager

        session = await blend_manager.get_session(interaction.guild_id)
        if session and interaction.user.id in session.active_participants:
            unplayed_tracks = session.remove_participant(interaction.user.id)
            await self.remove_user_tracks_from_queue(interaction.guild_id, interaction.user.id, unplayed_tracks)
            if not session.active_participants:
                await blend_manager.remove_session(interaction.guild_id, ym_cog=self)

        deleted = await db.delete_blend_user_token(interaction.user.id, interaction.guild_id)
        if deleted:
            await interaction.response.send_message("🗑️ **Ваш аккаунт Яндекс.Музыки удален из Совместной Волны.**", ephemeral=True)
        else:
            await interaction.response.send_message("ℹ️ У вас нет привязанных токенов на этом сервере.", ephemeral=True)

    @blend_group.command(name="status", description="Просмотреть статус и участников текущей Совместной Волны")
    async def blend_status(self, interaction: discord.Interaction):
        try:
            from utils.blend import blend_manager
        except ModuleNotFoundError:
            from src.utils.blend import blend_manager

        session = await blend_manager.get_session(interaction.guild_id)
        if not session or not session.active_participants:
            await interaction.response.send_message("ℹ️ На этом сервере сейчас нет активных Совместных Волн.", ephemeral=True)
            return

        state = self.get_state(interaction.guild_id)
        main_user = None
        if state.get("initiator_id"):
            main_user = f"<@{state['initiator_id']}>"
        else:
            cfg = await db.get_ym_config(interaction.guild_id)
            if cfg and cfg.get("user_id"):
                main_user = f"<@{cfg['user_id']}>"
            elif cfg and cfg.get("username"):
                main_user = f"**{cfg['username']}**"
            else:
                main_user = interaction.user.mention

        members_mentions = [f"<@{uid}>" for uid in session.active_participants]
        embed = discord.Embed(
            title="🔀 Статус Совместной Волны",
            description=(
                f"**Канал:** <#{session.channel_id}>\n"
                f"👑 **Инициатор плеера:** {main_user}\n"
                f"👥 **Участники Совместной Волны ({len(session.active_participants)}):** " + ", ".join(members_mentions)
            ),
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(YandexMusic(bot))
