import lolka as discord
from lolka.ext import commands
from lolka import app_commands
import logging
import asyncio
import aiohttp
import re
import os
import time
from typing import Dict, Optional, List
import db
from views.rutube_views import RutubePlayerView
from views.base_player import create_progress_bar, format_player_status, run_timeline_updater_loop, BasePlayerState, stop_other_cogs, ensure_voice_connection, has_listeners
from views.ui import is_bot_busy_in_other_channel
from utils.voice_utils import safe_defer, safe_voice_connect

logger = logging.getLogger("cogs.rutube")

class RutubeState(BasePlayerState):
    pass


def format_duration(seconds: int) -> str:
    if seconds <= 0:
        return "00:00"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"

class RutubeMusic(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.states: Dict[int, RutubeState] = {}
        self._volume: Dict[int, float] = {}
        self._bg_tasks = set()
        self._timeline_task = asyncio.create_task(self._timeline_updater_loop())
        self._bg_tasks.add(self._timeline_task)

    async def _timeline_updater_loop(self) -> None:
        """Фоновый цикл обновления прогресс-бара плеера RuTube во время воспроизведения."""
        await run_timeline_updater_loop(self)

    def get_state(self, guild_id: int) -> RutubeState:
        if guild_id not in self.states:
            state = RutubeState(guild_id)
            state.volume = self._volume.get(guild_id, 0.5)
            self.states[guild_id] = state
        return self.states[guild_id]

    def reset_state(self, guild_id: int) -> None:
        if guild_id in self.states:
            del self.states[guild_id]

    async def save_session_to_db(self, guild_id: int) -> None:
        state = self.get_state(guild_id)
        if not state.queue:
            await db.delete_rutube_session(guild_id)
            return
            
        video_ids = [track["id"] for track in state.queue]
        playback_pos = state.get_current_time()
        
        await db.save_rutube_session(
            guild_id=guild_id,
            queue_video_ids=video_ids,
            current_index=state.index,
            playback_position=playback_pos,
            source_playlist_id=getattr(state, "source_playlist_id", None),
            is_temporary=state.is_temporary,
            single_track_mode=state.single_track_mode
        )
        logger.info("[RuTube] Сохранена сессия для гильдии %s: трек %s, позиция %s сек", guild_id, state.index, playback_pos)

    def extract_video_ids(self, text: str) -> List[str]:

        # Находим все 32-значные hex-строки
        return re.findall(r'[a-f0-9]{32}', text.lower())

    async def fetch_video_info(self, video_id: str) -> Optional[dict]:
        api_url = f"https://rutube.ru/api/play/options/{video_id}/"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(api_url, headers=headers, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        video_balancer = data.get("video_balancer", {})
                        m3u8_url = video_balancer.get("m3u8")
                        if not m3u8_url:
                            return None
                        
                        author_data = data.get("author", {})
                        
                        return {
                            "id": video_id,
                            "title": data.get("title", "RuTube Видео"),
                            "duration": int(data.get("duration", 0) / 1000),
                            "thumbnail_url": data.get("thumbnail_url"),
                            "author_name": author_data.get("name", "Неизвестный автор"),
                            "author_avatar": author_data.get("avatar_url"),
                            "m3u8_url": m3u8_url
                        }
            except Exception as e:
                logger.error("Ошибка при получении данных RuTube видео %s: %s", video_id, e)
        return None

    async def create_player_embed(self, guild_id: int) -> discord.Embed:
        state = self.get_state(guild_id)
        track = state.queue[state.index]
        
        status_str = format_player_status(is_paused=state.is_paused)
        elapsed = state.get_current_time()
        duration = track.get("duration", 0)
        progress_bar = create_progress_bar(elapsed, duration)

        embed = discord.Embed(
            title=track["title"],
            url=f"https://rutube.ru/video/{track['id']}/",
            description=f"▶️ **Прогресс:**\n{progress_bar}",
            color=discord.Color.blue()
        )
        embed.set_author(name=track["author_name"], icon_url=track["author_avatar"])
        embed.add_field(name="📻 Статус", value=status_str, inline=True)
        embed.add_field(name="📋 Очередь", value=f"{state.index + 1} / {len(state.queue)}", inline=True)
        embed.add_field(name="🔊 Громкость", value=f"{int(state.volume * 100)}%", inline=True)
        
        if track["thumbnail_url"]:
            embed.set_image(url=track["thumbnail_url"])
            
        embed.set_footer(text="RuTube Player Integration • DynamicVoiceBot", icon_url=self.bot.user.avatar.url if self.bot.user.avatar else None)
        return embed

    async def send_now_playing(self, guild_id: int) -> None:
        state = self.get_state(guild_id)
        if not state.text_channel:
            logger.warning("[RuTube] send_now_playing: text_channel не задан для гильдии %s", guild_id)
            return

        async with state.lock:
            if not state.queue or state.index >= len(state.queue):
                return

            if state.idle_msg:
                try:
                    await state.idle_msg.delete()
                except Exception:
                    pass
                finally:
                    state.idle_msg = None

            embed = await self.create_player_embed(guild_id)
            view = RutubePlayerView(queue=state.queue, current_index=state.index)
            
            if state.np_msg:
                try:
                    await state.np_msg.edit(embed=embed, view=view)
                    return
                except Exception as edit_err:
                    logger.warning("[RuTube] Не удалось отредактировать сообщение плеера (%s), отправляем новое.", edit_err)
                    state.np_msg = None
                    
            try:
                state.np_msg = await state.text_channel.send(embed=embed, view=view)
            except Exception as e:
                logger.error("[RuTube] Не удалось отправить сообщение плеера: %s", e)

    def _play_next(self, guild_id: int) -> None:
        coro = self.play_track(guild_id)
        asyncio.run_coroutine_threadsafe(coro, self.bot.loop)

    async def jump_to_track(self, interaction: discord.Interaction, target_index: int) -> None:
        """Переключение на указанный трек в очереди по индексу."""
        guild_id = interaction.guild_id
        state = self.get_state(guild_id)
        vc = interaction.guild.voice_client

        if not vc or not vc.is_connected():
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Бот не подключен к голосовому каналу.", ephemeral=True)
            return

        if not state.queue or target_index < 0 or target_index >= len(state.queue):
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Указанный трек не найден в очереди.", ephemeral=True)
            return

        await safe_defer(interaction)

        state.index = target_index
        await self.play_track(guild_id)

    async def send_player_panel(self, interaction: discord.Interaction) -> None:
        """Отрисовывает интерфейс плеера (в ответ на команду или кнопку)."""
        guild_id = interaction.guild_id
        state = self.get_state(guild_id)
        
        vc = interaction.guild.voice_client
        state.text_channel = vc.channel if vc else interaction.channel

        # Если уже играет трек
        if state.queue and state.index < len(state.queue):
            embed = await self.create_player_embed(guild_id)
            view = RutubePlayerView(queue=state.queue, current_index=state.index)
        else:
            embed = discord.Embed(
                title="📺 RuTube Музыка",
                description=(
                    "Готов к проигрыванию.\n\n"
                    "🔹 Нажмите **🔍 Поиск / Ссылка** для запуска видео.\n"
                    "🔹 Нажмите **➕ Временный плейлист** для списка ссылок.\n"
                    "🔹 Нажмите **📁 Выбрать плейлист** для запуска плейлиста сервера."
                ),
                color=discord.Color.purple()
            )
            if self.bot.user.avatar:
                embed.set_footer(text="RuTube Player Integration • DynamicVoiceBot", icon_url=self.bot.user.avatar.url)
            else:
                embed.set_footer(text="RuTube Player Integration • DynamicVoiceBot")

            from views.rutube_views import RutubeReadyView
            view = RutubeReadyView()

        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=view)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=False)

        if state.queue and state.index < len(state.queue):
            try:
                state.np_msg = await interaction.original_response()
            except Exception:
                pass
        else:
            if state.idle_msg:
                try:
                    await state.idle_msg.delete()
                except Exception:
                    pass
                state.idle_msg = None
            try:
                state.idle_msg = await interaction.original_response()
            except Exception:
                pass

    async def play_track(self, guild_id: int) -> None:
        logger.info("[RuTube Debug] play_track вызван для гильдии %s. Индекс: %s", guild_id, self.get_state(guild_id).index)
        try:
            state = self.get_state(guild_id)
            if guild_id not in self._volume:
                cfg = await db.get_rutube_config(guild_id)
                volume = cfg.get("volume", 0.5) if cfg else 0.5
                self._volume[guild_id] = volume
                state.volume = volume
            vc = None
            for client_vc in self.bot.voice_clients:
                if client_vc.guild.id == guild_id:
                    vc = client_vc
                    break
                    
            if not vc:
                logger.warning("[RuTube Debug] VoiceClient не найден в bot.voice_clients для гильдии %s. Вызываем стоп.", guild_id)
                await self._stop_and_cleanup(guild_id)
                return

            if not vc.is_connected():
                logger.warning("[RuTube Debug] VoiceClient найден, но vc.is_connected() == False для гильдии %s. Вызываем стоп.", guild_id)
                await self._stop_and_cleanup(guild_id)
                return

            if state.index >= len(state.queue):
                logger.info("[RuTube Debug] Индекс (%s) >= размера очереди (%s). Завершаем.", state.index, len(state.queue))
                await self._stop_and_cleanup(guild_id, "⏹️ Очередь воспроизведения RuTube завершена.")
                return

            track_data = state.queue[state.index]
            state.is_paused = False

            if vc.is_playing() or vc.is_paused():
                logger.info("[RuTube Debug] Бот уже играет/на паузе. Останавливаем текущий поток.")
                state.is_switching = True
                vc.stop()
                await asyncio.sleep(0.1)

            # Опции для HLS потока
            before_opts = "-vn -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -http_persistent 0"
            opts = "-vn -sn -dn -nostdin -threads 1 -loglevel error -map 0:a:0"
            if getattr(state, "start_offset", 0) > 0:
                before_opts += f" -ss {state.start_offset}"
                state.playback_elapsed = state.start_offset
                state.start_offset = 0
            else:
                state.playback_elapsed = 0.0
                
            source = discord.FFmpegPCMAudio(
                track_data["m3u8_url"],
                before_options=before_opts,
                options=opts
            )
            transformed = discord.PCMVolumeTransformer(source, volume=state.volume * 0.50)

            def after_callback(error: Exception | None) -> None:
                logger.info("[RuTube Debug] after_callback вызван. Ошибка: %s", error)
                try:
                    if getattr(state, "is_switching", False):
                        logger.info("[RuTube Debug] Ручной переход (is_switching), пропускаем after_callback.")
                        state.is_switching = False
                        return

                    if error:
                        logger.error("Ошибка воспроизведения на сервере %s: %s", guild_id, error)
                    
                    if getattr(state, "is_sleeping", False):
                        logger.info("[RuTube Debug] Бот спит, игнорируем переход.")
                        return

                    if getattr(state, "is_seeking", False):
                        logger.info("[RuTube Debug] Режим перемотки (is_seeking), индекс не меняем.")
                        state.is_seeking = False
                    elif getattr(state, "single_track_mode", False):
                        logger.info("[RuTube Debug] Режим одного трека. Ставим на паузу.")
                        state.is_paused = True
                        coro = self.send_now_playing(guild_id)
                        asyncio.run_coroutine_threadsafe(coro, self.bot.loop)
                        return
                    else:
                        logger.info("[RuTube Debug] Обычный переход. Увеличиваем индекс с %s.", state.index)
                        state.index += 1
                        
                    self._play_next(guild_id)
                except Exception as ex:
                    logger.exception("Исключение в after_callback на сервере %s: %s", guild_id, ex)

            logger.info("[RuTube Debug] Запускаем vc.play для трека: %s", track_data['title'])
            if vc.is_playing() or vc.is_paused():
                try:
                    vc.stop()
                except Exception:
                    pass
            self._stop_other_cogs(guild_id)
            state.is_switching = False
            vc.play(transformed, after=after_callback)
            state.playback_start_time = time.time()
            await self.send_now_playing(guild_id)
            await self.save_session_to_db(guild_id)

        except Exception as e:
            logger.exception("Критическая ошибка в play_track на сервере %s: %s", guild_id, e)

    def _stop_other_cogs(self, guild_id: int) -> None:
        stop_other_cogs(self.bot, guild_id, "RutubeMusic")

    async def _stop_and_cleanup(self, guild_id: int, message: str | None = None) -> None:
        state = self.get_state(guild_id)
        if state.idle_msg:
            try:
                await state.idle_msg.delete()
            except Exception:
                pass
            finally:
                state.idle_msg = None

        if state.np_msg:
            try:
                await state.np_msg.delete()
            except Exception:
                pass
            finally:
                state.np_msg = None
                
        if message and state.text_channel:
            try:
                await state.text_channel.send(message)
            except Exception:
                pass

        # Отключаем бота
        vc = None
        for client_vc in self.bot.voice_clients:
            if client_vc.guild.id == guild_id:
                vc = client_vc
                break
        if vc and vc.is_connected():
            await vc.disconnect()

        await db.delete_rutube_session(guild_id)
        self.reset_state(guild_id)


    async def _check_interaction_permissions(self, interaction: discord.Interaction) -> bool:
        user = interaction.user
        guild_id = interaction.guild_id
        
        if user.guild_permissions.administrator:
            return True

        vc = interaction.guild.voice_client
        if not vc or not vc.channel:
            return True
            
        if not user.voice or user.voice.channel != vc.channel:
            await interaction.response.send_message("❌ Вы должны находиться в том же голосовом канале, что и бот, чтобы управлять им.", ephemeral=True)
            return False

        cfg = await db.get_rutube_config(guild_id)
        control_mode = cfg.get("control_mode", "everyone")
        dj_roles = cfg.get("dj_role_ids", [])

        if control_mode == "everyone":
            return True

        state = self.get_state(guild_id)
        dynamic_voice_cog = self.bot.get_cog("DynamicVoice")
        room_owner_id = None
        if dynamic_voice_cog:
            room_owner_id = await db.get_dynamic_channel_owner(vc.channel.id)
            
        is_owner = (user.id == state.initiator_id) or (room_owner_id and user.id == room_owner_id)

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

    # ── Методы управления (вызываются кнопками) ──

    async def toggle_pause(self, interaction: discord.Interaction) -> None:
        if not await self._check_interaction_permissions(interaction):
            return
        guild_id = interaction.guild_id
        state = self.get_state(guild_id)
        vc = interaction.guild.voice_client

        if not vc or not (vc.is_playing() or vc.is_paused()):
            await interaction.response.send_message("❌ Сейчас ничего не играет.", ephemeral=True)
            return

        if vc.is_paused():
            vc.resume()
            state.is_paused = False
            await safe_defer(interaction)
            await self.send_now_playing(guild_id)
            await self.save_session_to_db(guild_id)
        else:
            vc.pause()
            state.is_paused = True
            state.playback_elapsed += (time.time() - state.playback_start_time)
            await safe_defer(interaction)
            await self.send_now_playing(guild_id)
            await self.save_session_to_db(guild_id)


    async def change_volume(self, interaction: discord.Interaction, level: int) -> None:
        """Изменить громкость воспроизведения RuTube."""
        if not await self._check_interaction_permissions(interaction):
            return
        guild_id = interaction.guild_id
        state = self.get_state(guild_id)
        volume = level / 100.0
        state.volume = volume
        self._volume[guild_id] = volume
        await db.update_rutube_volume(guild_id, volume)

        vc = None
        for client_vc in self.bot.voice_clients:
            if client_vc.guild.id == guild_id:
                vc = client_vc
                break

        if vc and vc.source:
            vc.source.volume = volume * 0.50

        await safe_defer(interaction)
        await self.send_now_playing(guild_id)


    async def previous_track(self, interaction: discord.Interaction) -> None:
        if not await self._check_interaction_permissions(interaction):
            return
        guild_id = interaction.guild_id
        state = self.get_state(guild_id)
        vc = interaction.guild.voice_client

        if not vc:
            await interaction.response.send_message("❌ Бот не подключен к голосовому каналу.", ephemeral=True)
            return

        # Если трек уже играет какое-то время (например, 5 секунд или более)
        # или если это самый первый трек в очереди, запускаем его сначала
        if state.get_current_time() >= 5 or state.index <= 0:
            await safe_defer(interaction)
            await self.play_track(guild_id)
            return

        await safe_defer(interaction)
        state.index -= 1
        await self.play_track(guild_id)

    async def skip_track(self, interaction: discord.Interaction) -> None:
        if not await self._check_interaction_permissions(interaction):
            return
        guild_id = interaction.guild_id
        state = self.get_state(guild_id)
        vc = interaction.guild.voice_client

        if not vc:
            await interaction.response.send_message("❌ Бот не подключен к голосовому каналу.", ephemeral=True)
            return

        if state.single_track_mode:
            # Скип одиночного трека отключает одиночный режим
            state.single_track_mode = False

        if state.index + 1 >= len(state.queue):
            await interaction.response.send_message("⏹️ Очередь пуста, останавливаю воспроизведение.", ephemeral=True)
            await self._stop_and_cleanup(guild_id, "⏹️ Очередь воспроизведения RuTube завершена.")
            return

        await safe_defer(interaction)
        state.index += 1
        await self.play_track(guild_id)

    async def stop_playback(self, interaction: discord.Interaction) -> None:
        if not await self._check_interaction_permissions(interaction):
            return
        guild_id = interaction.guild_id
        await interaction.response.send_message("⏹️ Воспроизведение остановлено.", ephemeral=True)
        await self._stop_and_cleanup(guild_id, "⏹️ Стриминг RuTube остановлен.")

    async def seek_to(self, interaction: discord.Interaction, target_seconds: int) -> None:
        if not await self._check_interaction_permissions(interaction):
            return
        guild_id = interaction.guild_id
        state = self.get_state(guild_id)
        vc = interaction.guild.voice_client

        if not vc or not (vc.is_playing() or vc.is_paused()):
            await interaction.response.send_message("❌ Сейчас ничего не играет.", ephemeral=True)
            return

        if state.index >= len(state.queue):
            return

        track_duration = state.queue[state.index].get("duration", 0)
        
        # Ограничиваем временем трека
        if track_duration > 0:
            # Оставляем запас в 2 секунды до конца, чтобы не вызвать ошибку
            target_seconds = max(0, min(target_seconds, track_duration - 2))
        else:
            target_seconds = max(0, target_seconds)

        state.start_offset = target_seconds
        state.is_seeking = False
        
        await interaction.response.send_message(f"⏩ Перемотка на {format_duration(target_seconds)}...", ephemeral=True)
        
        await self.play_track(guild_id)

    async def seek_relative(self, interaction: discord.Interaction, delta_seconds: int) -> None:
        state = self.get_state(interaction.guild_id)
        current = state.get_current_time()
        await self.seek_to(interaction, current + delta_seconds)

    async def show_queue(self, interaction: discord.Interaction) -> None:
        state = self.get_state(interaction.guild_id)
        if not state.queue:
            await interaction.response.send_message("📋 Очередь воспроизведения пуста.", ephemeral=True)
            return

        embed = discord.Embed(title="📋 Очередь воспроизведения RuTube", color=discord.Color.purple())
        
        # Сейчас играет
        current_track = state.queue[state.index] if state.index < len(state.queue) else None
        if current_track:
            embed.description = f"**Сейчас играет:**\n`[{format_duration(current_track['duration'])}]` {current_track['title']} (от {current_track['author_name']})\n\n**Далее в очереди:**\n"
        else:
            embed.description = "**Очередь завершена.**\n\n**Далее в очереди:**\n"

        # Список следующих треков (до 10 штук)
        next_tracks = state.queue[state.index + 1 : state.index + 11]
        if not next_tracks:
            embed.description += "*Пусто*"
        else:
            for idx, track in enumerate(next_tracks, start=1):
                embed.description += f"**{idx}.** `[{format_duration(track['duration'])}]` {track['title']}\n"

        # Считаем общую длительность оставшихся треков
        total_remaining = sum(t["duration"] for t in state.queue[state.index:])
        embed.set_footer(text=f"Всего треков: {len(state.queue)} • Осталось играть: {format_duration(total_remaining)}")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def add_playlist_from_text(self, interaction: discord.Interaction, text: str) -> None:
        guild_id = interaction.guild_id
        state = self.get_state(guild_id)
        
        video_ids = self.extract_video_ids(text)
        if not video_ids:
            await interaction.followup.send("❌ В тексте не найдено ссылок с ID видео RuTube.", ephemeral=True)
            return

        # Исключаем дубликаты ID в рамках этого добавления
        unique_ids = list(dict.fromkeys(video_ids))
        
        # Загружаем метаданные видео параллельно
        tasks = [self.fetch_video_info(vid) for vid in unique_ids]
        results = await asyncio.gather(*tasks)
        
        added_tracks = [track for track in results if track is not None]
        if not added_tracks:
            await interaction.followup.send("❌ Не удалось получить информацию ни по одному видео RuTube.", ephemeral=True)
            return

        state.queue.extend(added_tracks)
        state.is_temporary = True
        state.single_track_mode = False
        state.initiator_id = interaction.user.id
        state.source_playlist_id = None

        
        vc = interaction.guild.voice_client
        is_playing = vc and (vc.is_playing() or vc.is_paused())
        
        # Если ничего не играло, запускаем
        if not is_playing:
            # Находим пользователя и его голосовой канал
            voice_state = interaction.user.voice
            if not voice_state or not voice_state.channel:
                await interaction.followup.send(
                    f"➕ Добавлено {len(added_tracks)} треков в очередь, но вам нужно зайти в голосовой канал для старта воспроизведения.", 
                    ephemeral=True
                )
                return
                
            try:
                # Подключаемся
                vc = await safe_voice_connect(interaction.guild, voice_state.channel)
                if not vc:
                    await interaction.followup.send("❌ Не удалось подключиться к голосовому каналу.", ephemeral=True)
                    return
            except Exception as e:
                await interaction.followup.send(f"❌ Не удалось подключиться к голосовому каналу: {e}", ephemeral=True)
                return
                
            state.text_channel = interaction.channel
            state.index = len(state.queue) - len(added_tracks)
            await self.play_track(guild_id)
            await db.update_rutube_last_channel(guild_id, voice_state.channel.id)
            await interaction.followup.send(f"▶️ Добавлено и запущено плейлист из {len(added_tracks)} видео RuTube!", ephemeral=True)
        else:
            await interaction.followup.send(f"➕ Добавлено {len(added_tracks)} видео RuTube в очередь воспроизведения!", ephemeral=True)

    # ── Слэш-команды бота ──

    @app_commands.command(name="rutube", description="Управление плеером RuTube")
    @app_commands.describe(url="Ссылка (или несколько ссылок через пробел/запятую) на видео RuTube")
    async def rutube_command(self, interaction: discord.Interaction, url: str) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return

        # Парсим ID видео из строки
        video_ids = self.extract_video_ids(url)
        if not video_ids:
            await interaction.response.send_message(
                "❌ Некорректная ссылка. Укажите ссылку вида `https://rutube.ru/video/9364551b610cad12c0a2afff00ce0dee/`",
                ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        
        # Получаем данные о видео в фоне
        unique_ids = list(dict.fromkeys(video_ids))
        tasks = [self.fetch_video_info(vid) for vid in unique_ids]
        results = await asyncio.gather(*tasks)
        
        added_tracks = [track for track in results if track is not None]
        if not added_tracks:
            await interaction.followup.send("❌ Не удалось получить информацию об указанном видео RuTube.", ephemeral=True)
            return

        guild_id = interaction.guild_id
        state = self.get_state(guild_id)
        state.queue.extend(added_tracks)
        state.is_temporary = True
        state.single_track_mode = False
        state.initiator_id = interaction.user.id
        state.source_playlist_id = None


        vc = interaction.guild.voice_client
        is_playing = vc and (vc.is_playing() or vc.is_paused())

        if not is_playing:
            voice_state = interaction.user.voice
            if not voice_state or not voice_state.channel:
                await interaction.followup.send(
                    f"➕ Добавлено {len(added_tracks)} треков в очередь, но войдите в голосовой канал, чтобы запустить плеер.",
                    ephemeral=True
                )
                return

            try:
                vc = await safe_voice_connect(interaction.guild, voice_state.channel)
                if not vc:
                    await interaction.followup.send("❌ Не удалось подключиться к голосовому каналу.", ephemeral=True)
                    return
            except Exception as e:
                await interaction.followup.send(f"❌ Ошибка подключения к голосовому каналу: {e}", ephemeral=True)
                return

            state.text_channel = interaction.channel
            state.index = len(state.queue) - len(added_tracks)
            await self.play_track(guild_id)
            await db.update_rutube_last_channel(guild_id, voice_state.channel.id)
            await interaction.followup.send(f"▶️ Добавлено и запущено {len(added_tracks)} видео RuTube!", ephemeral=True)
        else:
            await interaction.followup.send(f"➕ Добавлено {len(added_tracks)} видео RuTube в очередь!", ephemeral=True)

    @app_commands.command(name="rseek", description="Перемотать текущее видео RuTube на указанное время (например: 1:30 или 90)")
    @app_commands.describe(time="Время в секундах или в формате ММ:СС / ЧЧ:ММ:СС")
    async def rutube_seek(self, interaction: discord.Interaction, time: str) -> None:
        if not await self._check_interaction_permissions(interaction):
            return
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return

        guild_id = interaction.guild_id
        state = self.get_state(guild_id)
        vc = interaction.guild.voice_client

        if not vc or not (vc.is_playing() or vc.is_paused()):
            await interaction.response.send_message("❌ Сейчас ничего не играет.", ephemeral=True)
            return

        # Парсим время
        seconds = 0
        try:
            if ":" in time:
                parts = time.split(":")
                if len(parts) == 2: # MM:SS
                    seconds = int(parts[0]) * 60 + int(parts[1])
                elif len(parts) == 3: # HH:MM:SS
                    seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            else:
                seconds = int(time)
        except ValueError:
            await interaction.response.send_message("❌ Неверный формат времени. Используйте секунды (например, 90) или формат ММ:СС (например, 1:30).", ephemeral=True)
            return

        if seconds < 0:
            seconds = 0

        # Устанавливаем флаги и останавливаем текущий поток
        state.start_offset = seconds
        state.is_seeking = True
        
        await interaction.response.send_message(f"⏩ Перемотка на {format_duration(seconds)}...", ephemeral=True)
        
        # vc.stop() вызовет after_callback, который запустит _play_next с тем же state.index
        vc.stop()

    async def play_playlist(self, interaction: discord.Interaction, playlist_id: int) -> None:
        guild_id = interaction.guild_id
        state = self.get_state(guild_id)
        
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
            
        playlists = await db.get_rutube_playlists(guild_id)
        pl = next((p for p in playlists if p["id"] == playlist_id), None)
        if not pl:
            await interaction.response.send_message("❌ Плейлист не найден.", ephemeral=True)
            return
            
        video_ids = [vid.strip() for vid in pl["video_ids"].split(",") if vid.strip()]
        if not video_ids:
            await interaction.response.send_message("❌ В плейлисте нет видео.", ephemeral=True)
            return
            
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        tasks = [self.fetch_video_info(vid) for vid in video_ids]
        results = await asyncio.gather(*tasks)
        added_tracks = [t for t in results if t is not None]
        
        if not added_tracks:
            await interaction.followup.send("❌ Не удалось загрузить ни одного трека из плейлиста.", ephemeral=True)
            return
            
        voice_state = interaction.user.voice
        if not voice_state or not voice_state.channel:
            await interaction.followup.send("❌ Вы должны находиться в голосовом канале.", ephemeral=True)
            return
            
        vc = interaction.guild.voice_client
        if not vc:
            try:
                vc = await safe_voice_connect(interaction.guild, voice_state.channel)
                if not vc:
                    await interaction.followup.send("❌ Не удалось подключиться к голосовому каналу.", ephemeral=True)
                    return
            except Exception as e:
                await interaction.followup.send(f"❌ Ошибка подключения: {e}", ephemeral=True)
                return
                
        state.queue = added_tracks
        state.index = 0
        state.is_temporary = False
        state.single_track_mode = False
        state.initiator_id = interaction.user.id
        state.text_channel = interaction.channel
        state.source_playlist_id = playlist_id

        
        await self.play_track(guild_id)
        await db.update_rutube_last_channel(guild_id, voice_state.channel.id)
        await interaction.followup.send(f"▶️ Запущен плейлист **{pl['name']}** ({len(added_tracks)} видео)!", ephemeral=True)

    async def search_rutube_api(self, query: str) -> Optional[dict]:
        from urllib.parse import quote
        url = f"https://rutube.ru/api/search/video/?query={quote(query)}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, headers=headers, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        results = data.get("results", [])
                        if results:
                            video_id = results[0].get("id")
                            if video_id:
                                return await self.fetch_video_info(video_id)
            except Exception as e:
                logger.error("Ошибка при поиске RuTube по запросу %s: %s", query, e)
        return None

    async def play_rutube_search(self, interaction: discord.Interaction, raw_input: str) -> None:
        guild_id = interaction.guild_id
        state = self.get_state(guild_id)
        
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
            
        await interaction.response.defer(ephemeral=True, thinking=True)
        video_ids = self.extract_video_ids(raw_input)
        added_tracks = []
        if video_ids:
            unique_ids = list(dict.fromkeys(video_ids))
            tasks = [self.fetch_video_info(vid) for vid in unique_ids]
            results = await asyncio.gather(*tasks)
            added_tracks = [track for track in results if track is not None]
        else:
            lines = [line.strip() for line in raw_input.splitlines() if line.strip()]
            tasks = [self.search_rutube_api(line) for line in lines[:5]]
            results = await asyncio.gather(*tasks)
            added_tracks = [track for track in results if track is not None]

        if not added_tracks:
            await interaction.followup.send("❌ Не удалось найти видео RuTube по запросу/ссылке.", ephemeral=True)
            return
            
        voice_state = interaction.user.voice
        if not voice_state or not voice_state.channel:
            await interaction.followup.send("❌ Вы должны находиться в голосовом канале.", ephemeral=True)
            return
            
        vc = interaction.guild.voice_client
        if not vc or not vc.is_connected():
            try:
                vc = await safe_voice_connect(interaction.guild, voice_state.channel)
                if not vc:
                    await interaction.followup.send("❌ Не удалось подключиться к голосовому каналу.", ephemeral=True)
                    return
            except Exception as e:
                await interaction.followup.send(f"❌ Ошибка подключения: {e}", ephemeral=True)
                return

        state.is_temporary = True
        state.single_track_mode = (len(added_tracks) == 1)
        state.initiator_id = interaction.user.id
        state.text_channel = interaction.channel
        state.source_playlist_id = None

        is_playing = vc and (vc.is_playing() or vc.is_paused())

        if not is_playing or not state.queue:
            state.queue.extend(added_tracks)
            state.index = len(state.queue) - len(added_tracks)
            state.single_track_mode = (len(state.queue) == 1)
            await self.play_track(guild_id)
            await db.update_rutube_last_channel(guild_id, voice_state.channel.id)
            await interaction.followup.send(f"▶️ Запущено {len(added_tracks)} видео RuTube!", ephemeral=True)
        else:
            played_tracks = state.queue[:state.index]
            remaining_tracks = state.queue[state.index:]
            
            state.queue = played_tracks + list(added_tracks) + remaining_tracks
            state.index = len(played_tracks)
            state.single_track_mode = False
            
            await self.play_track(guild_id)
            await interaction.followup.send(f"▶️ Переключено на новые видео RuTube! Добавлено {len(added_tracks)} в очередь.", ephemeral=True)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Автовосстановление 24/7 голосовых подключений RuTube при старте."""
        await asyncio.sleep(5)
        logger.info("RuTube: запуск автовосстановления 24/7 подключений...")
        try:
            to_restore = await db.get_all_rutube_configs_to_restore()
            for item in to_restore:
                guild_id = item["guild_id"]
                channel_id = item["last_channel_id"]
                
                guild = self.bot.get_guild(guild_id)
                if not guild:
                    continue
                    
                channel = guild.get_channel(channel_id)
                if not channel or not isinstance(channel, discord.VoiceChannel):
                    logger.warning("RuTube: канал %s не найден на сервере %s. Сбрасываем.", channel_id, guild_id)
                    await db.update_rutube_last_channel(guild_id, None)
                    continue
                    
                bot_member = guild.get_member(self.bot.user.id)
                if not bot_member:
                    continue
                    
                permissions = channel.permissions_for(bot_member)
                if not permissions.connect or not permissions.speak:
                    logger.warning("RuTube: нет прав для подключения к каналу %s на сервере %s. Сбрасываем.", channel_id, guild_id)
                    await db.update_rutube_last_channel(guild_id, None)
                    continue
                
                try:
                    vc = await ensure_voice_connection(guild, channel)
                except Exception as e:
                    logger.error("Ошибка при автоподключении RuTube 24/7 к каналу %s: %s", channel_id, e)
                    continue

                try:
                    state = self.get_state(guild_id)
                    state.text_channel = channel
                    
                    # 1. Попробуем загрузить сохраненную сессию
                    session = await db.get_rutube_session(guild_id)
                    has_session = False
                    if session and session.get("queue_video_ids"):
                        logger.info("RuTube: найдена сохраненная сессия для сервера %s. Загружаем...", guild_id)
                        video_ids = session["queue_video_ids"]
                        tasks = [self.fetch_video_info(vid) for vid in video_ids]
                        results = await asyncio.gather(*tasks)
                        state.queue = [t for t in results if t is not None]
                        state.index = session["current_index"]
                        state.playback_elapsed = float(session["playback_position"])
                        state.source_playlist_id = session["source_playlist_id"]
                        state.is_temporary = session["is_temporary"]
                        state.single_track_mode = session["single_track_mode"]
                        has_session = len(state.queue) > 0

                    # 2. Если сессии нет, пробуем загрузить дефолтный плейлист
                    if not has_session:
                        default_pl_id = item.get("default_playlist_id")
                        if default_pl_id:
                            playlists = await db.get_rutube_playlists(guild_id)
                            pl = next((p for p in playlists if p["id"] == default_pl_id), None)
                            if pl:
                                video_ids = [vid.strip() for vid in pl["video_ids"].split(",") if vid.strip()]
                                tasks = [self.fetch_video_info(vid) for vid in video_ids]
                                results = await asyncio.gather(*tasks)
                                state.queue = [t for t in results if t is not None]
                                state.index = 0
                                state.playback_elapsed = 0.0
                                state.source_playlist_id = default_pl_id
                                state.is_temporary = False
                                state.single_track_mode = False

                    # 3. Управляем запуском/засыпанием в зависимости от людей в канале
                    if has_listeners(channel):
                        if state.queue and not (vc.is_playing() or vc.is_paused()):
                            state.start_offset = int(state.playback_elapsed)
                            await self.play_track(guild_id)
                    else:
                        logger.info("RuTube: канал 24/7 пуст при старте, бот засыпает.")
                        state.is_sleeping = True
                except Exception as e:
                    logger.error("Ошибка при обработке автовосстановления RuTube 24/7: %s", e)

        except Exception as e:
            logger.error("Ошибка при автовосстановлении RuTube: %s", e)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Управляет засыпанием/просыпанием бота и автоподключением 24/7."""
        guild_id = member.guild.id
        
        if member.id == self.bot.user.id:
            if before.channel and not after.channel:
                logger.info("RuTube: бота отключили от голосового канала %s (сервер %s). Очищаем состояние.", before.channel.id, guild_id)
                cfg = await db.get_rutube_config(guild_id)
                keep_alive = cfg.get("keep_alive", False) if cfg else False
                
                state = self.get_state(guild_id)
                if not keep_alive:
                    await db.update_rutube_last_channel(guild_id, None)
                    await db.delete_rutube_session(guild_id)
                else:
                    # Сохраняем состояние воспроизведения перед очисткой in-memory
                    if state.queue:
                        state.playback_elapsed = state.get_current_time()
                        await self.save_session_to_db(guild_id)
                        
                # Очищаем только свою панель при отключении
                if state.np_msg:
                    try:
                        await state.np_msg.delete()
                    except Exception:
                        pass
                    state.np_msg = None
                self.reset_state(guild_id)
            return

        if not before.channel and after.channel:
            cfg = await db.get_rutube_config(guild_id)
            keep_alive = cfg.get("keep_alive", False) if cfg else False
            last_channel_id = cfg.get("last_channel_id") if cfg else None
            
            if keep_alive and after.channel and (last_channel_id == after.channel.id or last_channel_id is None):
                non_bot = [m for m in after.channel.members if not m.bot]
                if len(non_bot) == 1:
                    vc = member.guild.voice_client
                    if not vc or not vc.is_connected():
                        lofi_cog = self.bot.get_cog("LofiRadio")
                        ym_cog = self.bot.get_cog("YandexMusic")
                        spotify_cog = self.bot.get_cog("SpotifyMusic")
                        if (lofi_cog and lofi_cog._voice_clients.get(guild_id)) or (ym_cog and ym_cog._voice_clients.get(guild_id)) or (spotify_cog and spotify_cog.get_state(guild_id).queue):
                            return
                        
                        try:
                            vc = await safe_voice_connect(member.guild, after.channel, self_deaf=True)
                            if not vc:
                                logger.warning("RuTube: автоподключение 24/7 не удалось для канала %s", after.channel.id)
                                return
                        except Exception as e:
                            logger.error("Ошибка при подключении RuTube 24/7: %s", e)
                            return
                            
                    state = self.get_state(guild_id)
                    if state.is_sleeping or not (vc.is_playing() or vc.is_paused()):
                        state.is_sleeping = False
                        # При просыпании направляем плеер в голосовой канал
                        if not state.text_channel:
                            state.text_channel = after.channel
                        if not state.queue:
                            # 1. Попробуем восстановить из сессии
                            session = await db.get_rutube_session(guild_id)
                            has_session = False
                            if session and session.get("queue_video_ids"):
                                logger.info("RuTube: просыпание, восстанавливаем сохраненную сессию для сервера %s", guild_id)
                                video_ids = session["queue_video_ids"]
                                tasks = [self.fetch_video_info(vid) for vid in video_ids]
                                results = await asyncio.gather(*tasks)
                                state.queue = [t for t in results if t is not None]
                                state.index = session["current_index"]
                                state.playback_elapsed = float(session["playback_position"])
                                state.source_playlist_id = session["source_playlist_id"]
                                state.is_temporary = session["is_temporary"]
                                state.single_track_mode = session["single_track_mode"]
                                has_session = len(state.queue) > 0
                                
                            # 2. Fallback на дефолтный плейлист
                            if not has_session:
                                default_pl_id = cfg.get("default_playlist_id")
                                if default_pl_id:
                                    playlists = await db.get_rutube_playlists(guild_id)
                                    pl = next((p for p in playlists if p["id"] == default_pl_id), None)
                                    if pl:
                                        video_ids = [vid.strip() for vid in pl["video_ids"].split(",") if vid.strip()]
                                        tasks = [self.fetch_video_info(vid) for vid in video_ids]
                                        results = await asyncio.gather(*tasks)
                                        state.queue = [t for t in results if t is not None]
                                        state.index = 0
                                        state.playback_elapsed = 0.0
                                        state.source_playlist_id = default_pl_id
                                        state.is_temporary = False
                                        state.single_track_mode = False
                        
                        if state.queue:
                            state.start_offset = int(state.playback_elapsed)
                            await self.play_track(guild_id)
                        else:
                            # Если очередь пуста, отправляем пустую панель плеера
                            embed = discord.Embed(
                                title="📺 RuTube Музыка",
                                description=(
                                    "Готов к проигрыванию.\n\n"
                                    "🔹 Нажмите **🎵 Запустить трек** для запуска одного видео.\n"
                                    "🔹 Нажмите **➕ Временный плейлист** для добавления сессионного списка.\n"
                                    "🔹 Нажмите **📁 Выбрать плейлист** для запуска плейлиста сервера."
                                ),
                                color=discord.Color.purple()
                            )
                            if self.bot.user.avatar:
                                embed.set_footer(text="RuTube Player Integration • DynamicVoiceBot", icon_url=self.bot.user.avatar.url)
                            else:
                                embed.set_footer(text="RuTube Player Integration • DynamicVoiceBot")
                            
                            view = RutubePlayerView()
                            try:
                                if state.np_msg:
                                    try:
                                        await state.np_msg.delete()
                                    except Exception:
                                        pass
                                state.np_msg = await state.text_channel.send(embed=embed, view=view)
                            except Exception as e:
                                logger.error("Не удалось отправить пустую панель RuTube при входе: %s", e)
            return

        if before.channel and not after.channel:
            vc = member.guild.voice_client
            if not vc or not vc.is_connected() or vc.channel.id != before.channel.id:
                return
                
            non_bot = [m for m in before.channel.members if not m.bot]
            if len(non_bot) == 0:
                state = self.get_state(guild_id)
                cfg = await db.get_rutube_config(guild_id)
                keep_alive = cfg.get("keep_alive", False) if cfg else False
                
                if state.is_temporary:
                    state.queue.clear()
                    state.index = 0
                    state.is_temporary = False
                    state.single_track_mode = False
                    
                if keep_alive:
                    logger.info("RuTube: канал 24/7 опустел на сервере %s. Засыпаем.", guild_id)
                    if vc.is_playing() or vc.is_paused():
                        if state.playback_start_time > 0:
                            state.playback_elapsed += (time.time() - state.playback_start_time)
                        state.playback_start_time = 0.0
                        vc.stop()
                    state.is_sleeping = True

                    await self.save_session_to_db(guild_id)
                    # Очищаем канал от старых панелей бота при засыпании
                    try:
                        async for msg in before.channel.history(limit=5):
                            if msg.author == self.bot.user:
                                await msg.delete()
                    except Exception as e:
                        logger.warning("Не удалось очистить сообщения RuTube при засыпании: %s", e)
                    state.np_msg = None
                else:
                    if vc.is_playing() or vc.is_paused():
                        vc.stop()
                    # Очищаем канал от старых панелей бота при выходе
                    try:
                        async for msg in before.channel.history(limit=5):
                            if msg.author == self.bot.user:
                                await msg.delete()
                    except Exception as e:
                        logger.warning("Не удалось очистить сообщения RuTube при отключении: %s", e)

                    # Проверяем 24/7 для других когов перед отключением
                    ym_cfg = await db.get_ym_settings(guild_id)
                    ym_keep_alive = ym_cfg.get("keep_alive", False) if ym_cfg else False
                    
                    lofi_cfg = await db.get_lofi_config(guild_id)
                    lofi_keep_alive = lofi_cfg.get("keep_alive", False) if lofi_cfg else False
                    
                    spotify_cfg = await db.get_spotify_config(guild_id)
                    spotify_keep_alive = spotify_cfg.get("keep_alive", False) if spotify_cfg else False
                    
                    if ym_keep_alive:
                        logger.info("RuTube: Канал %s переходит в режим ожидания 24/7 для ЯМ. Передаем управление.", before.channel.id)
                        ym_cog = self.bot.get_cog("YandexMusic")
                        if ym_cog:
                            ym_cog._voice_clients[guild_id] = vc
                        await db.delete_rutube_session(guild_id)
                        self.reset_state(guild_id)
                        return

                    if lofi_keep_alive:
                        logger.info("RuTube: Канал %s переходит в режим ожидания 24/7 для Lofi. Передаем управление.", before.channel.id)
                        lofi_cog = self.bot.get_cog("LofiRadio")
                        if lofi_cog:
                            lofi_cog._voice_clients[guild_id] = vc
                        await db.delete_rutube_session(guild_id)
                        self.reset_state(guild_id)
                        return

                    if spotify_keep_alive:
                        logger.info("RuTube: Канал %s переходит в режим ожидания 24/7 для Spotify. Передаем управление.", before.channel.id)
                        await db.delete_rutube_session(guild_id)
                        self.reset_state(guild_id)
                        return

                    logger.info("RuTube: канал опустел и 24/7 выключен. Отключаемся.")
                    await vc.disconnect()
                    await db.delete_rutube_session(guild_id)
                    self.reset_state(guild_id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RutubeMusic(bot))
