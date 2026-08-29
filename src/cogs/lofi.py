"""Lofi Radio — Cog для воспроизведения lo-fi музыки в голосовых каналах.

Загружается условно: только если config.ENABLE_LOFI_RADIO == True.
Предоставляет группу слэш-команд /lofi и слушатель для автовыхода
из пустых каналов.
"""

from __future__ import annotations

import logging
import asyncio
import aiohttp
import time
from typing import TYPE_CHECKING

import lolka as discord
from lolka import app_commands
from lolka.ext import commands

from lofi_streams import (
    DEFAULT_STATION,
    STATIONS,
    LofiStation,
    get_random_station,
    get_station_by_name,
)

if TYPE_CHECKING:
    from bot import DynamicVoiceBot

import db
import config
from views.base_player import ensure_voice_connection, has_listeners
from utils.voice_utils import safe_voice_connect

logger = logging.getLogger(__name__)

# Параметры FFmpeg для стабильного HTTP-стриминга
# Флаг -re убран, так как для живых потоков он вреден (вызывает разрыв соединения на строгих CDN типа ВГТРК)
FFMPEG_BEFORE_OPTIONS = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -user_agent \"Mozilla/5.0 (Windows NT 10.0; Win64; x64)\""
FFMPEG_OPTIONS = "-vn -sn -dn -nostdin -threads 1 -loglevel error"

DEFAULT_VOLUME = 0.5  # 50%
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


from views.base_player import create_progress_bar, format_player_status, stop_other_cogs

def _build_player_embed(
    station: LofiStation,
    volume: float,
    *,
    connected: bool = True,
    start_time: float | None = None,
) -> discord.Embed:
    """Создаёт embed плеера с информацией о текущей станции."""
    status = format_player_status(is_paused=False, is_live=True) if connected else "🔴 Оффлайн"
    elapsed = int(time.time() - start_time) if (connected and start_time) else 0
    progress_bar = create_progress_bar(elapsed, None)

    embed = discord.Embed(
        title=f"🎵 Lofi Radio — {station.name}",
        description=f"▶️ **Прогресс:**\n{progress_bar}",
        color=discord.Color.from_rgb(138, 43, 226),  # Фиолетовый
    )
    embed.add_field(name="📻 Статус", value=status, inline=True)
    embed.add_field(name="🎶 Жанр", value=station.genre, inline=True)
    embed.add_field(
        name="🔊 Громкость",
        value=f"{int(volume * 100)}%",
        inline=True,
    )
    embed.set_footer(text="DynamicVoiceBot • Lofi Radio")
    return embed


async def validate_stream_url(url: str) -> tuple[bool, str]:
    """Асинхронно проверяет доступность URL радио-потока.
    
    Быстрая проверка заголовков с таймаутом 2.0 секунды.
    """
    if not url or not url.startswith(("http://", "https://")):
        return False, "URL должен начинаться с http:// или https://"
    
    try:
        timeout = aiohttp.ClientTimeout(connect=2.0, total=2.5)
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DynamicVoiceBot/2.0"}
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            # Пробуем быстро считать только заголовки (stream=True)
            async with session.get(url, allow_redirects=True) as resp:
                resp.close()
                if resp.status not in (200, 301, 302, 303, 307, 308):
                    return False, f"Сервер вернул статус {resp.status}"
                
                content_type = resp.headers.get("Content-Type", "").lower()
                valid_types = ("audio/", "application/ogg", "application/octet-stream", "video/", "binary/octet-stream")
                if content_type and not any(t in content_type for t in valid_types):
                    if "text/html" in content_type:
                        return False, f"Сервер вернул HTML-страницу, а не аудиопоток (Content-Type: {content_type})"
                
                return True, ""
    except (asyncio.TimeoutError, TimeoutError):
        return False, "Сервер не ответил за 2 секунды (таймаут)"
    except aiohttp.ClientError as e:
        return False, f"Ошибка подключения: {e}"
    except Exception as e:
        return False, f"Неизвестная ошибка: {e}"


class LofiRadio(commands.Cog):
    """Модуль Lofi Radio для DynamicVoiceBot."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Состояние по серверам (guild_id -> data)
        self._voice_clients: dict[int, discord.VoiceClient] = {}
        self._current_station: dict[int, LofiStation] = {}
        self._volume: dict[int, float] = {}
        self._cooldowns: dict[int, float] = {}
        self._initiators: dict[int, int] = {}  # guild_id -> user_id
        self._start_times: dict[int, float] = {}

    async def get_active_stations(self, guild_id: int) -> list[LofiStation]:
        """Возвращает объединённый список станций для гильдии.
        
        Предустановленные (минус скрытые) + кастомные из БД.
        """
        hidden = await db.get_lofi_hidden_stations(guild_id)
        hidden_set = set(hidden)
        
        # Фильтруем предустановленные
        active = [s for s in STATIONS if s.name not in hidden_set]
        
        # Добавляем кастомные
        custom_rows = await db.get_lofi_custom_stations(guild_id)
        for row in custom_rows:
            active.append(LofiStation(
                name=row["name"],
                url=row["url"],
                emoji=row.get("emoji", "🎵"),
                genre=row.get("genre", "Custom"),
            ))
        
        return active

    async def _find_fallback_station(self, guild_id: int, exclude_station: LofiStation | None = None) -> LofiStation | None:
        """Ищет первую рабочую станцию для сервера, кроме указанной."""
        active = await self.get_active_stations(guild_id)
        for s in active:
            if exclude_station and s.name == exclude_station.name:
                continue
            if await self._verify_stream_url(s.url):
                return s
        return None

    async def _check_interaction_permissions(self, interaction: discord.Interaction) -> bool:
        """Проверяет права пользователя на управление Lofi Radio."""
        user = interaction.user
        guild_id = interaction.guild_id
        
        vc = self._voice_clients.get(guild_id) or interaction.guild.voice_client
        if not vc or not vc.channel:
            await interaction.response.send_message("❌ Бот не подключен к голосовому каналу.", ephemeral=True)
            return False
            
        if not user.voice or user.voice.channel != vc.channel:
            await interaction.response.send_message("❌ Вы должны находиться в том же голосовом канале, что и бот, чтобы управлять им.", ephemeral=True)
            return False

        if user.guild_permissions.administrator:
            return True

        cfg = await db.get_lofi_config(guild_id)
        control_mode = cfg.get("control_mode", "everyone")
        dj_roles = cfg.get("dj_role_ids", [])

        if control_mode == "everyone":
            return True

        is_initiator = self._initiators.get(guild_id) == user.id
        channel_permissions = vc.channel.permissions_for(user)
        is_owner = is_initiator or channel_permissions.manage_channels

        if control_mode == "owner_only":
            if is_owner:
                return True
            await interaction.response.send_message("❌ Управлять плеером может только владелец комнаты или инициатор воспроизведения.", ephemeral=True)
            return False

        if control_mode == "dj_only":
            if not dj_roles:
                if is_owner:
                    return True
                await interaction.response.send_message("❌ DJ-роли не настроены. Управлять плеером может только владелец комнаты.", ephemeral=True)
                return False
            
            user_role_ids = {role.id for role in user.roles}
            has_dj_role = any(r_id in user_role_ids for r_id in dj_roles)
            
            if has_dj_role or is_owner:
                return True
                
            await interaction.response.send_message("❌ У вас нет роли DJ для управления плеером.", ephemeral=True)
            return False

        return True

    def check_cooldown(self, guild_id: int) -> bool:
        """Возвращает True если действие разрешено, False если кулдаун."""
        import time
        now = time.time()
        last = self._cooldowns.get(guild_id, 0)
        if now - last < 3.0:
            return False
        self._cooldowns[guild_id] = now
        return True

    async def cog_unload(self) -> None:
        """Graceful disconnect при выгрузке cog'а."""
        for guild_id, vc in list(self._voice_clients.items()):
            try:
                if vc.is_connected():
                    await vc.disconnect(force=True)
            except Exception as exc:
                logger.warning(
                    "Ошибка при отключении от гильдии %s: %s",
                    guild_id,
                    exc,
                )
        self._voice_clients.clear()
        self._current_station.clear()
        self._volume.clear()

    # Убран _handle_defer_and_loading, так как мы будем отвечать моментально

    async def _send_or_edit_panel(self, interaction: discord.Interaction, embed: discord.Embed, view: discord.ui.View | None = None) -> None:
        """Отправляет или обновляет панель управления. Всегда использует edit_original_response."""
        if view is None:
            from views.lofi_views import LofiPlayerView
            active_stations = await self.get_active_stations(interaction.guild_id)
            view = LofiPlayerView(active_stations)

        try:
            if not interaction.response.is_done():
                custom_id = ""
                if interaction.type == discord.InteractionType.component and interaction.data:
                    custom_id = interaction.data.get("custom_id", "")

                is_lofi = custom_id.startswith("lofi_") or interaction.type == discord.InteractionType.modal_submit

                if is_lofi:
                    await interaction.response.edit_message(embed=embed, view=view)
                else:
                    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            else:
                await interaction.edit_original_response(embed=embed, view=view)
        except Exception as exc:
            logger.warning("Не удалось отправить/отредактировать панель Lofi: %s", exc)

    async def _play_station(
        self,
        vc: discord.VoiceClient,
        station: LofiStation,
        volume: float,
    ) -> None:
        """Запускает воспроизведение станции через FFmpeg."""
        try:
            if vc.is_playing() or vc.is_paused():
                vc.stop()
                await asyncio.sleep(0.2)
        except Exception as exc:
            logger.warning("Ошибка при остановке предыдущего потока Lofi: %s", exc)

        source = discord.FFmpegPCMAudio(
            station.url,
            before_options=FFMPEG_BEFORE_OPTIONS,
            options=FFMPEG_OPTIONS,
        )
        transformed = discord.PCMVolumeTransformer(source, volume=volume * 0.50)

        guild_id = vc.guild.id
        self._start_times[guild_id] = time.time()

        def after_callback(error: Exception | None) -> None:
            if error:
                logger.error(
                    "Ошибка воспроизведения в гильдии %s: %s",
                    guild_id,
                    error,
                    exc_info=True,
                )

        if vc.is_playing() or vc.is_paused():
            try:
                vc.stop()
            except Exception:
                pass
        self._stop_other_cogs(vc.guild.id)
        vc.play(transformed, after=after_callback)

    def _stop_other_cogs(self, guild_id: int) -> None:
        stop_other_cogs(self.bot, guild_id, "LofiRadio")

    async def start_radio(
        self,
        interaction: discord.Interaction,
        station: LofiStation | None = None,
    ) -> None:
        """Публичный метод: подключается к каналу и отправляет плеер.

        Вызывается из слэш-команды /lofi play и из кнопки
        в UserControlPanel.
        """
        vc_exists = self._voice_clients.get(interaction.guild_id) or interaction.guild.voice_client
        if vc_exists and vc_exists.is_connected():
            if not await self._check_interaction_permissions(interaction):
                return

        user = interaction.user
        voice_state = None
        
        # 1. Попытка достать из кэша гильдии клиента (самый надежный способ в обход временных Member объектов)
        guild = interaction.client.get_guild(interaction.guild_id)
        if guild:
            voice_state = guild._voice_state_for(user.id)
            
        # 2. Фолбэк на свойство user.voice
        if not voice_state:
            voice_state = getattr(user, 'voice', None)
            
        # 3. Фолбэк на get_member
        if not voice_state and guild:
            member = guild.get_member(user.id)
            if member:
                voice_state = getattr(member, 'voice', None)

        if not voice_state or not voice_state.channel:
            logger.info("Cannot find voice channel for user %s (ID %s).", user, user.id)
            msg = "❌ Сначала зайдите в голосовой канал!\n*(Если вы уже там, проверьте, есть ли у бота права на **просмотр** этого канала!)*"
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
            return

        voice_channel = voice_state.channel
        guild_id = interaction.guild_id

        active_stations = await self.get_active_stations(guild_id)
        station = station or self._current_station.get(guild_id)
        if not station:
            station = active_stations[0] if active_stations else DEFAULT_STATION

        volume = self._volume.get(guild_id)
        if volume is None:
            cfg = await db.get_lofi_config(guild_id)
            volume = cfg.get("volume", DEFAULT_VOLUME) if cfg else DEFAULT_VOLUME
            self._volume[guild_id] = volume

        vc = self._voice_clients.get(guild_id)

        from views.lofi_views import LofiPlayerView
        view = LofiPlayerView(active_stations)

        # Показываем честную загрузку с кнопками
        await self._send_loading_state(
            interaction, 
            view, 
            title="⏳ Подключение к радио...", 
            desc="Подключаемся к голосовому каналу, подождите пару секунд..."
        )

        # Выполняем параллельно подключение к Voice и мгновенную проверку потока
        import asyncio
        async def _bg_connect():
            try:
                # 1. Параллельно подключаемся к голосовому каналу и проверяем доступность аудиопотока
                connect_task = asyncio.create_task(safe_voice_connect(interaction.guild, voice_channel, self_deaf=True))
                verify_task = asyncio.create_task(self._verify_stream_url(station.url))

                vc, is_valid = await asyncio.gather(connect_task, verify_task)

                current_station = station
                fallback_notified = False
                if not is_valid:
                    fallback = await self._find_fallback_station(guild_id, exclude_station=station)
                    if fallback:
                        current_station = fallback
                        fallback_notified = True
                    else:
                        if vc:
                            try:
                                await vc.disconnect(force=True)
                            except Exception:
                                pass
                        error_embed = discord.Embed(
                            title="❌ Ошибка воспроизведения",
                            description=f"Радиостанция **{station.name}** временно недоступна, и не удалось найти работающую замену на сервере.",
                            color=discord.Color.red(),
                        )
                        try:
                            await interaction.edit_original_response(embed=error_embed, view=view)
                        except (discord.NotFound, discord.HTTPException, discord.InteractionResponded):
                            pass
                        return

                if not vc:
                    error_embed = discord.Embed(
                        title="❌ Ошибка",
                        description="Не удалось подключиться к голосовому каналу.",
                        color=discord.Color.red(),
                    )
                    try:
                        await interaction.edit_original_response(embed=error_embed, view=view)
                    except (discord.NotFound, discord.HTTPException, discord.InteractionResponded):
                        pass
                    return
                self._voice_clients[guild_id] = vc

                self._current_station[guild_id] = current_station
                await self._play_station(vc, current_station, volume)
                
                self._initiators[guild_id] = user.id
                await db.update_lofi_last_station(guild_id, current_station.name)
                await db.update_lofi_last_channel(guild_id, voice_channel.id)

                # Небольшая задержка, чтобы FFmpeg успел сбуферизировать аудиопоток,
                # и панель "Онлайн" появлялась ровно в момент начала музыки.
                await asyncio.sleep(1.5)
                
                # Обновляем панель на финальную
                embed = _build_player_embed(current_station, volume, connected=True)
                await self._send_or_edit_panel(interaction, embed, view)

                if fallback_notified:
                    try:
                        await voice_channel.send(
                            f"⚠️ Радиостанция **{station.name}** временно недоступна. "
                            f"Автоматически переключено на резервную станцию **{current_station.name}**."
                        )
                    except Exception:
                        pass
            except Exception as exc:
                logger.warning("Ошибка подключения к каналу %s: %s", voice_channel.id, exc)
                error_embed = discord.Embed(
                    title="❌ Ошибка",
                    description="Не удалось подключиться к голосовому каналу.",
                    color=discord.Color.red(),
                )
                try:
                    await interaction.edit_original_response(embed=error_embed, view=view)
                except (discord.NotFound, discord.HTTPException, discord.InteractionResponded):
                    pass  # Интеракция истекла — лог выше уже записан

        if not hasattr(self, "_bg_tasks"):
            self._bg_tasks = set()
        task = asyncio.create_task(_bg_connect())
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def stop_radio(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Публичный метод: останавливает радио и отключает бота."""
        guild_id = interaction.guild_id
        vc = self._voice_clients.get(guild_id)

        if not vc or not vc.is_connected():
            msg = "❌ Бот не подключён к голосовому каналу."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
            return

        if not await self._check_interaction_permissions(interaction):
            return

        active_stations = await self.get_active_stations(guild_id)
        station = self._current_station.get(guild_id)
        if not station:
            station = active_stations[0] if active_stations else DEFAULT_STATION
        volume = self._volume.get(guild_id, DEFAULT_VOLUME)

        from views.lofi_views import LofiPlayerView
        view = LofiPlayerView(active_stations)

        await self._send_loading_state(
            interaction, 
            view, 
            title="⏳ Отключение...", 
            desc="Останавливаем плеер..."
        )

        import asyncio
        async def _bg_disconnect():
            try:
                if vc.is_playing():
                    vc.stop()
                await vc.disconnect(force=True)
            except Exception as exc:
                logger.warning("Ошибка при отключении: %s", exc)
            self._voice_clients.pop(guild_id, None)
            self._current_station.pop(guild_id, None)
            self._initiators.pop(guild_id, None)
            cfg = await db.get_lofi_config(guild_id)
            keep_alive = cfg.get("keep_alive", False) if cfg else False
            if not keep_alive:
                await db.update_lofi_last_channel(guild_id, None)
            
            embed = _build_player_embed(station, volume, connected=False)
            await self._send_or_edit_panel(interaction, embed, view)

        if not hasattr(self, "_bg_tasks"):
            self._bg_tasks = set()
        task = asyncio.create_task(_bg_disconnect())
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def change_volume(
        self,
        interaction: discord.Interaction,
        level: int,
    ) -> None:
        """Публичный метод: изменяет громкость (1–100)."""
        if not await self._check_interaction_permissions(interaction):
            return

        guild_id = interaction.guild_id
        volume = max(0.01, min(1.0, level / 100))
        self._volume[guild_id] = volume
        await db.update_lofi_volume(guild_id, volume)

        vc = self._voice_clients.get(guild_id)
        if vc and vc.is_playing() and vc.source:
            vc.source.volume = volume * 0.50

        active_stations = await self.get_active_stations(guild_id)
        station = self._current_station.get(guild_id)
        if not station:
            station = active_stations[0] if active_stations else DEFAULT_STATION
        connected = bool(vc and vc.is_connected())

        embed = _build_player_embed(station, volume, connected=connected)

        await self._send_or_edit_panel(interaction, embed)

    async def shuffle_station(
        self,
        interaction: discord.Interaction,
    ) -> None:
        """Публичный метод: переключает на случайную станцию."""
        if not await self._check_interaction_permissions(interaction):
            return

        guild_id = interaction.guild_id
        current = self._current_station.get(guild_id)
        
        active_stations = await self.get_active_stations(guild_id)
        candidates = [s for s in active_stations if s != current] if current else active_stations
        if not candidates:
            candidates = active_stations

        new_station = None
        fallback_notified = False
        
        if candidates:
            import random
            random.shuffle(candidates)
            for s in candidates:
                if await self._verify_stream_url(s.url):
                    new_station = s
                    break

        if not new_station:
            # Попробуем текущую
            if current and await self._verify_stream_url(current.url):
                new_station = current
            else:
                error_embed = discord.Embed(
                    title="❌ Ошибка",
                    description="Нет доступных радиостанций. Все потоки временно не отвечают.",
                    color=discord.Color.red(),
                )
                from views.lofi_views import LofiPlayerView
                view = LofiPlayerView(active_stations)
                await interaction.response.edit_message(embed=error_embed, view=view)
                return

        vc = self._voice_clients.get(guild_id)
        volume = self._volume.get(guild_id, DEFAULT_VOLUME)

        self._current_station[guild_id] = new_station

        if vc and vc.is_connected():
            await self._play_station(vc, new_station, volume)
            connected = True
        else:
            connected = False

        from views.lofi_views import LofiPlayerView
        active_stations = await self.get_active_stations(guild_id)
        view = LofiPlayerView(active_stations)

        await self._send_loading_state(
            interaction, 
            view, 
            title="⏳ Смена станции...", 
            desc="Подключаемся к новому потоку..."
        )

        # Фактическое переключение обычно происходит быстро, но на всякий случай можно оставить как есть
        # и просто обновить панель
        import asyncio
        if connected:
            await asyncio.sleep(1.5)
        
        embed = _build_player_embed(new_station, volume, connected=connected)
        await self._send_or_edit_panel(interaction, embed, view)

    # ──────────────────────────────────────────
    # Слэш-команды (группа /lofi)
    # ──────────────────────────────────────────

    lofi_group = app_commands.Group(
        name="lofi",
        description="Управление Lofi Radio",
    )

    @lofi_group.command(name="play", description="Включить Lofi Radio в вашем голосовом канале")
    async def lofi_play(self, interaction: discord.Interaction) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        await self.start_radio(interaction)

    @lofi_group.command(name="stop", description="Выключить Lofi Radio")
    async def lofi_stop(self, interaction: discord.Interaction) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        await self.stop_radio(interaction)

    @lofi_group.command(name="volume", description="Изменить громкость Lofi Radio")
    @app_commands.describe(level="Громкость от 1 до 100")
    async def lofi_volume(
        self,
        interaction: discord.Interaction,
        level: app_commands.Range[int, 1, 100],
    ) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        await self.change_volume(interaction, level)

    @lofi_group.command(name="station", description="Переключить радиостанцию")
    @app_commands.describe(name="Название станции")
    async def lofi_station(
        self,
        interaction: discord.Interaction,
        name: str,
    ) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        station = get_station_by_name(name)
        if not station:
            # Проверяем кастомные станции
            active = await self.get_active_stations(interaction.guild_id)
            station = next((s for s in active if s.name.lower() == name.lower()), None)
        if not station:
            active = await self.get_active_stations(interaction.guild_id)
            names = ", ".join(s.name for s in active)
            await interaction.response.send_message(
                f"❌ Станция не найдена. Доступные: {names}",
                ephemeral=True,
            )
            return
        await self.start_radio(interaction, station=station)

    @lofi_station.autocomplete("name")
    async def station_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return [
            app_commands.Choice(name=f"{s.emoji} {s.name} ({s.genre})", value=s.name)
            for s in await self.get_active_stations(interaction.guild_id)
            if current.lower() in s.name.lower()
        ][:25]

    @lofi_group.command(name="panel", description="Показать панель управления Lofi Radio")
    async def lofi_panel(self, interaction: discord.Interaction) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        await self.send_player_panel(interaction)

    async def send_player_panel(self, interaction: discord.Interaction) -> None:
        """Отрисовывает интерфейс плеера Lofi Radio."""
        guild_id = interaction.guild_id
        active_stations = await self.get_active_stations(guild_id)
        station = self._current_station.get(guild_id)
        if not station:
            station = active_stations[0] if active_stations else DEFAULT_STATION
        volume = self._volume.get(guild_id, DEFAULT_VOLUME)
        vc = self._voice_clients.get(guild_id)
        connected = bool(vc and vc.is_connected())

        from views.lofi_views import LofiPlayerView
        embed = _build_player_embed(station, volume, connected=connected)
        view = LofiPlayerView(active_stations)
        
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=view)
        else:
            await interaction.response.send_message(embed=embed, view=view)

    @lofi_group.command(name="config", description="Настроить Lofi Radio на сервере (только для Админов)")
    async def lofi_config_cmd(self, interaction: discord.Interaction) -> None:
        if not interaction.permissions.administrator:
            await interaction.response.send_message("❌ Только администраторы могут изменять настройки.", ephemeral=True)
            return

        settings = await db.get_lofi_config(interaction.guild_id)
        from views.lofi_views import LofiConfigView
        embed = self._build_config_embed(interaction.guild_id, settings)
        view = LofiConfigView(interaction.guild, settings)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    def _build_config_embed(self, guild_id: int, settings: dict) -> discord.Embed:
        keep_alive = settings.get("keep_alive", False)
        control_mode = settings.get("control_mode", "everyone")
        dj_roles = settings.get("dj_role_ids", [])
        last_channel_id = settings.get("last_channel_id")

        mode_mapping = {
            "everyone": "Все пользователи в канале",
            "owner_only": "Только владелец комнаты / инициатор",
            "dj_only": "Только пользователи с ролью DJ"
        }

        roles_str = ", ".join(f"<@&{r_id}>" for r_id in dj_roles) if dj_roles else "❌ Не настроены"
        channel_str = f"<#{last_channel_id}>" if last_channel_id else "❌ Не выбран"

        embed = discord.Embed(
            title="🎵 Настройки Lofi Radio",
            description="Здесь вы можете изменить глобальные параметры Lofi Radio для сервера.",
            color=discord.Color.from_rgb(138, 43, 226)
        )
        embed.add_field(name="📻 Режим 24/7", value="🟢 Включен" if keep_alive else "🔴 Выключен", inline=True)
        embed.add_field(name="🎛️ Кто может управлять", value=mode_mapping.get(control_mode, "Все"), inline=True)
        embed.add_field(name="🎧 Роли DJ", value=roles_str, inline=False)
        embed.add_field(name="🔊 Канал 24/7", value=channel_str, inline=False)
        embed.set_footer(text="Изменения вступают в силу немедленно")
        return embed

    # ──────────────────────────────────────────
    # Внутренние хелперы
    # ──────────────────────────────────────────

    async def _send_loading_state(self, interaction: discord.Interaction, view: discord.ui.View, title: str = "⏳ Обработка...", desc: str = "Пожалуйста, подождите...") -> None:
        """Показывает экран загрузки, СОХРАНЯЯ кнопки, чтобы они не пропали из-за бага Discord API."""
        if interaction.response.is_done():
            return

        loading_embed = discord.Embed(
            title=title,
            description=desc,
            color=discord.Color.gold()
        )

        custom_id = ""
        if interaction.type == discord.InteractionType.component and interaction.data:
            custom_id = interaction.data.get("custom_id", "")

        is_lofi = custom_id.startswith("lofi_") or interaction.type == discord.InteractionType.modal_submit

        try:
            if is_lofi:
                await interaction.response.edit_message(embed=loading_embed, view=view)
            else:
                await interaction.response.send_message(embed=loading_embed, view=view, ephemeral=True)
        except Exception as exc:
            logger.warning("Не удалось показать экран загрузки: %s", exc)

    # ──────────────────────────────────────────
    # Автовыход из пустых каналов
    # ──────────────────────────────────────────

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Автовосстановление 24/7 голосовых подключений Lofi Radio при старте."""
        await asyncio.sleep(5)
        
        logger.info("Lofi Radio: запуск автовосстановления 24/7 подключений...")
        try:
            to_restore = await db.get_all_lofi_configs_to_restore()
            for item in to_restore:
                guild_id = item["guild_id"]
                channel_id = item["last_channel_id"]
                
                guild = self.bot.get_guild(guild_id)
                if not guild:
                    continue
                    
                channel = guild.get_channel(channel_id)
                if not channel or not isinstance(channel, discord.VoiceChannel):
                    logger.warning("Lofi Radio: канал %s не найден на сервере %s. Сбрасываем.", channel_id, guild_id)
                    await db.update_lofi_last_channel(guild_id, None)
                    continue
                    
                bot_member = guild.get_member(self.bot.user.id)
                if not bot_member:
                    continue
                    
                permissions = channel.permissions_for(bot_member)
                if not permissions.connect or not permissions.speak:
                    logger.warning("Lofi Radio: нет прав для подключения к каналу %s на сервере %s. Сбрасываем.", channel_id, guild_id)
                    await db.update_lofi_last_channel(guild_id, None)
                    continue
                
                try:
                    vc = await ensure_voice_connection(guild, channel)
                except Exception as e:
                    logger.error("Lofi Radio: не удалось автоподключиться к каналу %s: %s", channel_id, e)
                    continue

                try:
                    self._voice_clients[guild_id] = vc
                    
                    # Восстанавливаем сохранённую станцию
                    saved_name = item.get("last_station_name")
                    active = await self.get_active_stations(guild_id)
                    station = None
                    if saved_name:
                        station = next((s for s in active if s.name == saved_name), None)
                    if not station:
                        station = active[0] if active else DEFAULT_STATION
                    
                    # Проверяем доступность при старте
                    if not await self._verify_stream_url(station.url):
                        fallback = await self._find_fallback_station(guild_id, exclude_station=station)
                        if fallback:
                            station = fallback
                            
                    self._current_station[guild_id] = station
                    
                    if has_listeners(channel) and not vc.is_playing():
                        logger.info("Lofi Radio: 24/7 автовосстановление, в канале есть слушатели. Запускаем поток...")
                        volume = item.get("volume", DEFAULT_VOLUME)
                        await self._play_station(vc, station, volume)
                except Exception as e:
                    logger.error("Lofi Radio: ошибка при восстановлении станции для канала %s: %s", channel_id, e)
                
                await asyncio.sleep(2)
        except Exception as e:
            logger.error("Ошибка в on_ready автовосстановления Lofi: %s", e, exc_info=True)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Отключает бота если все пользователи вышли из канала (с учетом keep_alive)."""
        guild_id = member.guild.id

        # Если это сам бот и его отключили от канала
        if member.id == self.bot.user.id:
            if before.channel and not after.channel:
                logger.info("Lofi Radio: бот был отключен от канала %s (гильдия %s), очищаем состояние.", before.channel.id, guild_id)
                self._voice_clients.pop(guild_id, None)
                self._current_station.pop(guild_id, None)
                self._initiators.pop(guild_id, None)
                cfg = await db.get_lofi_config(guild_id)
                keep_alive = cfg.get("keep_alive", False) if cfg else False
                if not keep_alive:
                    await db.update_lofi_last_channel(guild_id, None)
            return

        if not before.channel and after.channel:
            cfg = await db.get_lofi_config(guild_id)
            keep_alive = cfg.get("keep_alive", False) if cfg else False
            last_channel_id = cfg.get("last_channel_id") if cfg else None
            
            if keep_alive and after.channel and (last_channel_id == after.channel.id or last_channel_id is None):
                non_bot = [m for m in after.channel.members if not m.bot]
                if len(non_bot) == 1:
                    vc = self._voice_clients.get(guild_id)
                    if not vc or not vc.is_connected():
                        try:
                            # Проверяем, может ЯМ сейчас занял vc в другом канале?
                            ym_cog = self.bot.get_cog("YandexMusic")
                            spotify_cog = self.bot.get_cog("SpotifyMusic")
                            if (ym_cog and ym_cog._voice_clients.get(guild_id)) or (spotify_cog and spotify_cog.get_state(guild_id).queue):
                                return # Занят другим модулем
                            
                            vc = await safe_voice_connect(member.guild, after.channel, self_deaf=True)
                            if not vc:
                                logger.warning("Lofi: автоподключение 24/7 не удалось для канала %s", after.channel.id)
                                return
                            self._voice_clients[guild_id] = vc
                        except Exception as e:
                            logger.error("Ошибка при подключении Lofi 24/7: %s", e)
                            return
                            
                    if vc and vc.channel and after.channel and vc.channel.id == after.channel.id and not vc.is_playing():
                        logger.info("Lofi Radio: пользователь зашел в пустой канал 24/7. Возобновляем воспроизведение.")
                        # Пытаемся восстановить последнюю станцию из БД
                        saved_name = cfg.get("last_station_name") if cfg else None
                        active = await self.get_active_stations(guild_id)
                        station = self._current_station.get(guild_id)
                        if not station and saved_name:
                            station = next((s for s in active if s.name == saved_name), None)
                        if not station:
                            station = active[0] if active else DEFAULT_STATION
                        
                        # Проверяем доступность восстанавливаемой станции
                        if not await self._verify_stream_url(station.url):
                            fallback = await self._find_fallback_station(guild_id, exclude_station=station)
                            if fallback:
                                try:
                                    await vc.channel.send(
                                        f"⚠️ Сохранённая станция **{station.name}** недоступна. "
                                        f"Автоматически переключено на резервную **{fallback.name}**."
                                    )
                                except Exception:
                                    pass
                                station = fallback
                                
                        self._current_station[guild_id] = station
                        volume = cfg.get("volume", DEFAULT_VOLUME) if cfg else DEFAULT_VOLUME
                        self._volume[guild_id] = volume
                        await self._play_station(vc, station, volume)
                        
                        from views.lofi_views import LofiPlayerView
                        embed = _build_player_embed(station, volume, connected=True)
                        active_stations = await self.get_active_stations(guild_id)
                        view = LofiPlayerView(active_stations)
                        try:
                            await vc.channel.send(embed=embed, view=view)
                        except Exception as e:
                            logger.error("Ошибка при отправке панели Lofi после входа: %s", e)
            return

        vc = self._voice_clients.get(guild_id)
        if not vc or not vc.is_connected():
            return

        if vc.channel.id != before.channel.id:
            return

        # Считаем живых людей (не ботов)
        non_bot = [m for m in before.channel.members if not m.bot]
        if len(non_bot) == 0:
            cfg = await db.get_lofi_config(guild_id)
            keep_alive = cfg.get("keep_alive", False) if cfg else False
            
            if keep_alive:
                logger.info(
                    "Lofi Radio: все пользователи ушли из канала %s (гильдия %s). Режим 24/7 активен: останавливаем поток.",
                    before.channel.id,
                    guild_id,
                )
                try:
                    if vc.is_playing():
                        vc.stop()
                        
                    # Очищаем канал от старых панелей бота
                    async for msg in before.channel.history(limit=5):
                        if msg.author == self.bot.user:
                            await msg.delete()
                except Exception as exc:
                    logger.warning("Ошибка при остановке воспроизведения Lofi в канале 24/7: %s", exc)
            else:
                logger.info(
                    "Lofi Radio: все пользователи ушли из канала %s (гильдия %s), проверяем 24/7 для ЯМ...",
                    before.channel.id,
                    guild_id,
                )
                try:
                    if vc.is_playing():
                        vc.stop()
                        
                    # Очищаем канал от старых панелей бота
                    try:
                        async for msg in before.channel.history(limit=5):
                            if msg.author == self.bot.user:
                                await msg.delete()
                    except Exception as e:
                        logger.warning("Не удалось очистить сообщения Lofi: %s", e)
                        
                    ym_cfg = await db.get_ym_settings(guild_id)
                    ym_keep_alive = ym_cfg.get("keep_alive", False) if ym_cfg else False
                    
                    rutube_cfg = await db.get_rutube_config(guild_id)
                    rutube_keep_alive = rutube_cfg.get("keep_alive", False) if rutube_cfg else False

                    spotify_cfg = await db.get_spotify_config(guild_id)
                    spotify_keep_alive = spotify_cfg.get("keep_alive", False) if spotify_cfg else False
                    
                    # Если ЯМ 24/7 включен
                    if ym_keep_alive:
                        logger.info("Lofi Radio: Канал %s переходит в режим ожидания 24/7 для ЯМ. Передаем управление.", before.channel.id)
                        ym_cog = self.bot.get_cog("YandexMusic")
                        if ym_cog:
                            ym_cog._voice_clients[guild_id] = vc
                        self._voice_clients.pop(guild_id, None)
                        self._current_station.pop(guild_id, None)
                        return

                    # Если RuTube 24/7 включен
                    if rutube_keep_alive:
                        logger.info("Lofi Radio: Канал %s переходит в режим ожидания 24/7 для RuTube. Передаем управление.", before.channel.id)
                        self._voice_clients.pop(guild_id, None)
                        self._current_station.pop(guild_id, None)
                        return

                    # Если Spotify 24/7 включен
                    if spotify_keep_alive:
                        logger.info("Lofi Radio: Канал %s переходит в режим ожидания 24/7 для Spotify. Передаем управление.", before.channel.id)
                        self._voice_clients.pop(guild_id, None)
                        self._current_station.pop(guild_id, None)
                        return

                    await vc.disconnect(force=True)
                except Exception as exc:
                    logger.warning(
                        "Ошибка при автоотключении из канала %s: %s",
                        before.channel.id,
                        exc,
                    )
                self._voice_clients.pop(guild_id, None)
                self._current_station.pop(guild_id, None)
                self._initiators.pop(guild_id, None)

    async def _verify_stream_url(self, url: str) -> bool:
        """Обёртка над validate_stream_url для обратной совместимости."""
        ok, _ = await validate_stream_url(url)
        return ok

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LofiRadio(bot))
