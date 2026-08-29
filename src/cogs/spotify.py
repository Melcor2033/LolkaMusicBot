import lolka as discord
from lolka.ext import commands
from lolka import app_commands
import logging
import asyncio
import re
import time
from typing import Dict, Optional, List
from cogs.spotify_manager import SpotifyManager, is_vk_url
import yt_dlp
import db
import config
from views.spotify_views import SpotifyPlayerView
from views.base_player import create_progress_bar, format_player_status, run_timeline_updater_loop, BasePlayerState, stop_other_cogs, ensure_voice_connection, has_listeners
from views.ui import is_bot_busy_in_other_channel
from utils.voice_utils import get_user_voice_channel, safe_defer, safe_send, safe_voice_connect

logger = logging.getLogger("cogs.spotify")

YOUTUBE_PLAYLIST_MAX_TRACKS = 100
YOUTUBE_PLAYLIST_SOCKET_TIMEOUT = 15

YOUTUBE_PLAYLIST_ID_RE = re.compile(
    r"(?:PL|RD|OL|UU|LL|FL|HL|PU)[A-Za-z0-9_-]{10,}",
    re.IGNORECASE
)

YOUTUBE_PLAYLIST_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.|music\.|m\.)?"
    r"(?:youtube\.com/(?:watch\?(?:.*&)?v=[\w-]+&|playlist\?|embed/[\w-]+/?\?|live/[\w-]+/?\?)"
    r"|youtu\.be/[\w-]+\?)"
    r"list=([A-Za-z0-9_-]+)",
    re.IGNORECASE
)

_DELETED_TITLE_MARKERS = ("[Private video]", "[Deleted video]", "[Video unavailable]")


def _googlevideo_is_fresh(url: str, margin_sec: int = 60) -> bool:
    """Проверяет, не истёк ли expire-параметр googlevideo.com URL.

    googlevideo URL содержит ?expire=UNIX_TIMESTAMP. Типичный TTL — 6 часов.
    Возвращает True если до истечения осталось > margin_sec секунд.
    """
    from urllib.parse import urlparse, parse_qs
    try:
        qs = parse_qs(urlparse(url).query)
        vals = qs.get("expire", [])
        if not vals:
            return False
        return time.time() < (int(vals[0]) - margin_sec)
    except Exception:
        return False


def extract_youtube_playlist_id(text: str) -> Optional[str]:
    if not text:
        return None
    text = text.strip()
    m = YOUTUBE_PLAYLIST_URL_RE.search(text)
    if m:
        return m.group(1)
    if "youtube.com/playlist" in text or "music.youtube.com/playlist" in text:
        m_id = YOUTUBE_PLAYLIST_ID_RE.search(text)
        if m_id:
            return m_id.group(0)
    if YOUTUBE_PLAYLIST_ID_RE.fullmatch(text):
        return text
    return None


def normalize_youtube_playlist_entry(entry: dict, fallback_uploader: Optional[str] = None) -> Optional[dict]:
    if not entry or not isinstance(entry, dict):
        return None
    url = entry.get("url")
    video_id = entry.get("id")
    title = (entry.get("title") or "").strip()

    if not url and not video_id:
        return None
    if any(marker in title for marker in _DELETED_TITLE_MARKERS):
        return None

    if url and (url.startswith("http://") or url.startswith("https://")):
        video_url = url
    elif video_id:
        video_url = f"https://www.youtube.com/watch?v={video_id}"
    elif url:
        video_url = f"https://www.youtube.com/watch?v={url}"
    else:
        return None

    live_status = entry.get("live_status")
    is_live = entry.get("is_live") is True or live_status in ("is_live", "is_upcoming")
    if is_live:
        return None

    thumbnail_url = None
    if entry.get("thumbnail") and isinstance(entry.get("thumbnail"), str):
        thumbnail_url = entry["thumbnail"]
    elif entry.get("thumbnails") and isinstance(entry.get("thumbnails"), list) and len(entry["thumbnails"]) > 0:
        thumbnail_url = entry["thumbnails"][-1].get("url")
    elif video_id:
        thumbnail_url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

    uploader = (
        entry.get("uploader")
        or entry.get("channel")
        or entry.get("uploader_id")
        or fallback_uploader
        or "YouTube"
    )

    duration = int(entry.get("duration", 0)) if entry.get("duration") else 0

    return {
        "id": f"search:{video_url}",
        "title": title or f"YouTube video {video_id or ''}",
        "artists": uploader,
        "duration": duration,
        "thumbnail_url": thumbnail_url,
        "search_query": video_url
    }


class SpotifyState(BasePlayerState):
    def __init__(self, guild_id: int):
        super().__init__(guild_id)
        self.preload_task: Optional[asyncio.Task] = None


def format_duration(seconds: int) -> str:
    if seconds <= 0:
        return "00:00"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class SpotifyMusic(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.states: Dict[int, SpotifyState] = {}
        self._volume: Dict[int, float] = {}
        self.manager = SpotifyManager()
        self._bg_tasks = set()
        
        # Запускаем автовосстановление и обновление прогресс-бара
        task = asyncio.create_task(self._auto_restore_connections())
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

        self._timeline_task = asyncio.create_task(self._timeline_updater_loop())
        self._bg_tasks.add(self._timeline_task)

    async def _timeline_updater_loop(self) -> None:
        """Фоновый цикл обновления прогресс-бара плеера Spotify во время воспроизведения."""
        await run_timeline_updater_loop(self)

    def get_state(self, guild_id: int) -> SpotifyState:
        if guild_id not in self.states:
            state = SpotifyState(guild_id)
            state.volume = self._volume.get(guild_id, 0.5)
            self.states[guild_id] = state
        return self.states[guild_id]

    def reset_state(self, guild_id: int) -> None:
        if guild_id in self.states:
            del self.states[guild_id]

    async def save_session_to_db(self, guild_id: int) -> None:
        state = self.get_state(guild_id)
        if not state.queue:
            await db.delete_spotify_session(guild_id)
            return
            
        import json
        queue_json = json.dumps(state.queue)
        playback_pos = state.get_current_time()
        
        await db.save_spotify_session(
            guild_id=guild_id,
            queue_json=queue_json,
            current_index=state.index,
            playback_position=playback_pos,
            source_playlist_id=getattr(state, "source_playlist_id", None),
            is_temporary=state.is_temporary,
            single_track_mode=state.single_track_mode
        )

    def extract_spotify_id(self, url: str) -> tuple[Optional[str], Optional[str]]:
        """Возвращает (type, id) из ссылки Spotify."""
        pattern = r"open\.spotify\.com/(track|album|playlist)/([a-zA-Z0-9]+)"
        match = re.search(pattern, url)
        if match:
            return match.group(1), match.group(2)
        return None, None

    async def resolve_spotify_metadata(self, sp_type: str, sp_id: str) -> List[dict]:
        """Получает метаданные треков через scraping (без API ключей)."""
        url = f"https://open.spotify.com/{sp_type}/{sp_id}"
        tracks = await self.manager.parse_spotify_url(url)
        
        mapped_tracks = []
        for t in tracks:
            mapped_tracks.append({
                "id": t['id'],
                "title": t['title'],
                "artists": t['artist'],
                "duration": t['duration'],
                "thumbnail_url": t['cover']
            })
        return mapped_tracks

    async def extract_audio_stream(self, title: str, artists: str, search_query: Optional[str] = None) -> tuple[Optional[str], Optional[dict]]:
        """Ищет трек по провайдерам и возвращает (stream_url, info_dict)."""
        if search_query:
            query = search_query
        else:
            query = f"{artists} - {title}" if artists and artists != "Поиск" else title
            
        providers = config.SPOTIFY_SEARCH_PROVIDERS
        
        # Если это прямая ссылка, мы не используем поисковые префиксы
        if query.startswith("http://") or query.startswith("https://"):
            providers_to_try = [None]
        else:
            providers_to_try = providers

        ydl_opts = {
            'format': 'bestaudio/best',
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'default_search': 'auto',
            'source_address': '0.0.0.0'
        }
        is_age_restricted = False
        def _check_age_err(exc: Exception) -> bool:
            s = str(exc).lower()
            return any(m in s for m in ("sign in to confirm your age", "inappropriate for some users", "age-restricted", "confirm your age"))

        for provider in providers_to_try:
            if provider is None:
                full_query = query
                logger.info("[Spotify] Стриминг прямой ссылки '%s'...", query)
            else:
                search_prefix = ""
                if provider == "soundcloud":
                    search_prefix = "scsearch1:"
                elif provider == "youtube":
                    search_prefix = "ytsearch1:"
                else:
                    continue
                full_query = f"{search_prefix}{query}"
                logger.info("[Spotify] Ищем трек '%s' через %s...", query, provider)

            success = False
            # 1. Пробуем напрямую (без прокси), если прокси не задан
            if not config.YTDLP_PROXY:
                try:
                    opts = ydl_opts.copy()
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        info = await asyncio.to_thread(ydl.extract_info, full_query, download=False)
                        success = True
                except Exception as e:
                    if _check_age_err(e):
                        is_age_restricted = True
                    logger.warning("[Spotify] Не удалось получить поток напрямую для %s: %s", provider or "прямой ссылки", e)

            # 2. Если прокси задан — используем сразу (или как fallback если прямой не сработал)
            if not success and config.YTDLP_PROXY:
                try:
                    logger.info("[Spotify] Получаем поток через прокси для %s...", provider or "прямой ссылки")
                    opts = ydl_opts.copy()
                    opts['proxy'] = config.YTDLP_PROXY
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        info = await asyncio.to_thread(ydl.extract_info, full_query, download=False)
                        success = True
                except Exception as proxy_err:
                    if _check_age_err(proxy_err):
                        is_age_restricted = True
                    logger.warning("[Spotify] Не удалось получить поток через прокси для %s: %s", provider or "прямой ссылки", proxy_err)


            if success:
                if 'entries' in info and info['entries']:
                    entry = info['entries'][0]
                    if entry and 'url' in entry:
                        logger.info("[Spotify] Ссылка успешно получена")
                        return entry['url'], entry
                elif info and 'url' in info:
                    logger.info("[Spotify] Ссылка успешно получена")
                    return info['url'], info
        
        # Если провайдеры закончились и ничего не найдено, проверяем:
        # 1. Если у трека есть название и артист, пробуем текстовый фоллбек по "Исполнитель - Название"
        if query.startswith("http://") or query.startswith("https://"):
            if title and title != query and artists and artists not in ("Поиск", "VK Music"):
                fallback_query = f"{artists} - {title}"
                logger.info("[Spotify] Прямая ссылка '%s' не отдала поток. Переключаемся на фоллбек-поиск по названию: '%s'...", query, fallback_query)
                return await self.extract_audio_stream(title=title, artists=artists, search_query=fallback_query)

            # 2. Вдруг это была прямая ссылка на YouTube, которая заблокирована.
            # Попробуем вытащить название через oEmbed и запустить поиск повторно уже по названию.
            if "youtube.com" in query or "youtu.be" in query:
                logger.info("[Spotify] Прямая ссылка на YouTube %s не сработала. Пробуем получить название через oEmbed для фоллбека...", query)
                import aiohttp
                oembed_url = f"https://www.youtube.com/oembed?url={query}&format=json"
                title_oe, author_oe = None, None
                
                # 1. Пробуем напрямую (через Zapret на хосте)
                try:
                    async with aiohttp.ClientSession() as session:
                        logger.info("[Spotify] Пробуем получить oEmbed напрямую...")
                        async with session.get(oembed_url, timeout=4) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                title_oe = data.get("title")
                                author_oe = data.get("author_name")
                except Exception as e:
                    logger.warning("[Spotify] Не удалось получить oEmbed напрямую: %s", e)
                
                # 2. Если напрямую не вышло, пробуем через SPOTIFY_PROXY (датский Xray)
                if not title_oe:
                    try:
                        async with aiohttp.ClientSession() as session:
                            logger.info("[Spotify] Пробуем получить oEmbed через прокси...")
                            async with session.get(oembed_url, proxy=config.SPOTIFY_PROXY, timeout=5) as resp:
                                if resp.status == 200:
                                    data = await resp.json()
                                    title_oe = data.get("title")
                                    author_oe = data.get("author_name")
                    except Exception as e:
                        logger.warning("[Spotify] Не удалось получить oEmbed через прокси: %s", e)

                if title_oe:
                    fallback_query = f"{author_oe} - {title_oe}" if author_oe else title_oe
                    logger.info("[Spotify] Успешно получено название для фоллбека: '%s'. Запускаем резервный поиск...", fallback_query)
                    return await self.extract_audio_stream(title=title_oe, artists=author_oe or "YouTube", search_query=fallback_query)

        if is_age_restricted:
            return None, {"error_reason": "age_restricted"}

        return None, None

    def start_preload(self, guild_id: int) -> None:
        """Запускает предзагрузку, отменяя предыдущую задачу при наличии."""
        state = self.get_state(guild_id)
        if state.preload_task and not state.preload_task.done():
            state.preload_task.cancel()
        state.preload_task = self.bot.loop.create_task(self.preload_next_tracks(guild_id))

    async def is_stream_url_valid(self, url: str) -> bool:
        """Проверяет доступность стрим-ссылки (200/206/30x)."""
        import aiohttp
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            is_youtube = "googlevideo.com" in url or "youtube.com" in url or "youtu.be" in url
            proxy = config.STREAM_PROXY if (is_youtube and config.STREAM_PROXY) else None
            async with aiohttp.ClientSession() as session:
                async with session.head(url, headers=headers, timeout=1.5, allow_redirects=True, proxy=proxy) as resp:
                    if resp.status in (200, 206, 301, 302, 307, 308):
                        return True
                    if resp.status in (400, 404, 405):
                        get_headers = {"User-Agent": "Mozilla/5.0", "Range": "bytes=0-0"}
                        async with session.get(url, headers=get_headers, timeout=1.5, proxy=proxy) as get_resp:
                            return get_resp.status in (200, 206)
                    return False
        except Exception as e:
            logger.warning("[Spotify] Исключение при валидации ссылки: %s", e)
            return True

    async def preload_next_tracks(self, guild_id: int) -> None:
        """В фоне параллельно ищет аудиопотоки для следующих 2 треков в очереди."""
        state = self.get_state(guild_id)
        try:
            tasks = []
            for i in range(1, 3):
                next_idx = state.index + i
                if next_idx >= len(state.queue):
                    break
                track_data = state.queue[next_idx]
                if "stream_url" not in track_data:
                    tasks.append(self._preload_one(guild_id, next_idx))
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            logger.info("[Spotify] Предзагрузка отменена для сервера %s", guild_id)
        except Exception as e:
            logger.error("[Spotify] Ошибка предзагрузки: %s", e)

    async def _preload_one(self, guild_id: int, track_idx: int) -> None:
        """Загружает stream_url для одного трека в фоне (параллельный воркер предзагрузки)."""
        state = self.get_state(guild_id)
        if track_idx >= len(state.queue):
            return
        track_data = state.queue[track_idx]
        # Guard: другой воркер или play_track уже загрузил URL
        if "stream_url" in track_data:
            return
        logger.info("[Spotify] Фоновая предзагрузка трека '%s'...", track_data["title"])
        stream_url, metadata = await self.extract_audio_stream(
            track_data["title"], track_data["artists"], track_data.get("search_query")
        )
        if stream_url and "stream_url" not in track_data:
            track_data["stream_url"] = stream_url
            if (track_data.get("id") == "search" or track_data.get("id", "").startswith("search:")) and metadata:
                track_data["title"] = metadata.get("title", track_data["title"])
                track_data["artists"] = metadata.get("uploader", "YouTube/SoundCloud")
                duration = metadata.get("duration")
                if duration:
                    track_data["duration"] = int(duration)
                thumbnail = metadata.get("thumbnail")
                if thumbnail:
                    track_data["thumbnail_url"] = thumbnail

    async def create_player_embed(self, guild_id: int) -> discord.Embed:
        state = self.get_state(guild_id)
        track = state.queue[state.index]
        status_str = format_player_status(is_paused=state.is_paused)
        elapsed = state.get_current_time()
        duration = track.get("duration", 0)
        progress_bar = create_progress_bar(elapsed, duration)
        
        embed = discord.Embed(
            title=f"{track['artists']} — {track['title']}",
            description=f"▶️ **Прогресс:**\n{progress_bar}",
            color=discord.Color.red()
        )
        embed_icon = self.bot.user.avatar.url if self.bot.user.avatar else None
        embed.set_author(name="Dynamic Музыка", icon_url=embed_icon)
        embed.add_field(name="📻 Статус", value=status_str, inline=True)
        embed.add_field(name="📋 Очередь", value=f"{state.index + 1} / {len(state.queue)}", inline=True)
        embed.add_field(name="🔊 Громкость", value=f"{int(state.volume * 100)}%", inline=True)
        
        # Если есть обложка трека - ставим её, иначе нашу фирменную
        thumbnail_url = track.get("thumbnail_url")
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)
        else:
            if embed_icon:
                embed.set_thumbnail(url=embed_icon)
            
        embed.set_footer(text="S&Y Integration • DynamicVoiceBot")
        return embed

    async def send_now_playing(self, guild_id: int) -> None:
        state = self.get_state(guild_id)
        if not state.text_channel:
            logger.warning("[Spotify] send_now_playing: text_channel не задан для гильдии %s", guild_id)
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

            track = state.queue[state.index]
            embed = await self.create_player_embed(guild_id)
            view = SpotifyPlayerView(queue=state.queue, current_index=state.index)
            
            if state.np_msg:
                try:
                    await state.np_msg.edit(embed=embed, view=view)
                    return
                except Exception as edit_err:
                    logger.warning("[Spotify] Не удалось отредактировать сообщение плеера (%s), отправляем новое.", edit_err)
                    state.np_msg = None
                    
            try:
                state.np_msg = await state.text_channel.send(embed=embed, view=view)
            except discord.Forbidden:
                logger.warning("[Spotify] Отсутствуют права Embed Links в канале %s, отправляем текстовую панель с кнопками.", state.text_channel)
                try:
                    text_card = f"▶️ **Сейчас играет:** {track.get('artists', 'VK')} — {track.get('title', 'Track')}\n📋 **Очередь:** {state.index + 1} / {len(state.queue)}"
                    state.np_msg = await state.text_channel.send(text_card, view=view)
                except Exception as inner_e:
                    logger.error("[Spotify] Не удалось отправить текстовую панель плеера: %s", inner_e)
            except Exception as e:
                logger.error("[Spotify] Не удалось отправить сообщение плеера: %s", e)

    def _play_next(self, guild_id: int) -> None:
        logger.info("[Spotify] _play_next: запускаем play_track для guild=%s, index=%s", guild_id, self.get_state(guild_id).index)
        coro = self.play_track(guild_id)
        asyncio.run_coroutine_threadsafe(coro, self.bot.loop)

    async def jump_to_track(self, interaction: discord.Interaction, target_index: int) -> None:
        """Переключение на указанный трек в очереди по индексу."""
        guild_id = interaction.guild_id
        state = self.get_state(guild_id)
        vc = interaction.guild.voice_client

        if not vc or not vc.is_connected():
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Плеер не подключен к голосовому каналу.", ephemeral=True)
            return

        if not state.queue or target_index < 0 or target_index >= len(state.queue):
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Указанный трек не найден в очереди.", ephemeral=True)
            return

        await safe_defer(interaction)

        state.index = target_index
        await self.play_track(guild_id)

    async def send_player_panel(self, interaction: discord.Interaction) -> None:
        guild_id = interaction.guild_id
        state = self.get_state(guild_id)
        
        vc = interaction.guild.voice_client
        state.text_channel = vc.channel if vc else interaction.channel

        if state.queue and state.index < len(state.queue):
            embed = await self.create_player_embed(guild_id)
            view = SpotifyPlayerView(queue=state.queue, current_index=state.index)
        else:
            embed = discord.Embed(
                title="🔴 Dynamic Музыка",
                description=(
                    "Готов к проигрыванию.\n\n"
                    "🔹 Нажмите **🔍 Поиск / Ссылка** для добавления трека или плейлиста.\n"
                    "🔹 Нажмите **📁 Выбрать плейлист** для запуска сохраненного плейлиста сервера."
                ),
                color=discord.Color.red()
            )
            embed.set_footer(text="S&Y Integration • DynamicVoiceBot")

            from views.spotify_views import SpotifyReadyView
            view = SpotifyReadyView()

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
        try:
            state = self.get_state(guild_id)
            if guild_id not in self._volume:
                cfg = await db.get_spotify_config(guild_id)
                volume = cfg.get("volume", 0.5) if cfg else 0.5
                self._volume[guild_id] = volume
                state.volume = volume
            state.current_play_id = getattr(state, "current_play_id", 0) + 1
            play_id = state.current_play_id
            state.is_sleeping = False  # Просыпаемся принудительно, если начали играть!
            logger.info(
                "[Spotify] play_track: guild=%s index=%s queue_len=%s play_id=%s",
                guild_id, state.index, len(state.queue), play_id
            )
            vc = None
            for client_vc in self.bot.voice_clients:
                if client_vc.guild.id == guild_id:
                    vc = client_vc
                    break
                    
            if not vc:
                await self._stop_and_cleanup(guild_id)
                return

            if not vc.is_connected():
                await self._stop_and_cleanup(guild_id)
                return

            if state.index >= len(state.queue):
                await self._stop_and_cleanup(guild_id, "⏹️ Очередь воспроизведения Spotify завершена.")
                return

            track_data = state.queue[state.index]
            state.is_paused = False

            if vc.is_playing() or vc.is_paused():
                state.is_switching = True
                vc.stop()
                await asyncio.sleep(0.1)

            stream_url = track_data.get("stream_url")
            if stream_url:
                if "googlevideo.com" in stream_url:
                    # Проверяем expire-параметр: googlevideo URL живут ~6 часов.
                    # Если свежий — используем кеш напрямую (0 мс), не делаем новый запрос к yt-dlp.
                    if _googlevideo_is_fresh(stream_url):
                        logger.debug("[Spotify] googlevideo URL свежий (expire ok), используем кеш для '%s'.", track_data["title"])
                    else:
                        logger.info("[Spotify] googlevideo URL устарел или без expire для '%s', перезапрашиваем.", track_data["title"])
                        track_data.pop("stream_url", None)
                        stream_url = None
                else:
                    valid = await self.is_stream_url_valid(stream_url)
                    if state.is_sleeping or state.current_play_id != play_id:
                        logger.info("[Spotify] play_track прерван после проверки валидности URL (плеер уснул или запущен другой трек)")
                        return
                    if not valid:
                        logger.info("[Spotify] Кэшированный stream_url для '%s' устарел или недоступен, сбрасываем и получаем заново.", track_data["title"])
                        track_data.pop("stream_url", None)
                        stream_url = None

            if not stream_url:
                # Ищем аудиопоток
                stream_url, metadata = await self.extract_audio_stream(track_data["title"], track_data["artists"], track_data.get("search_query"))
                if state.is_sleeping or state.current_play_id != play_id:
                    logger.info("[Spotify] play_track прерван после поиска потока (плеер уснул или запущен другой трек)")
                    return
                if not stream_url:
                    logger.error("[Spotify] Не удалось найти аудиопоток для трека %s", track_data["title"])
                    if state.text_channel:
                        if isinstance(metadata, dict) and metadata.get("error_reason") == "age_restricted":
                            await state.text_channel.send(
                                f"🔞 Трек **{track_data['artists']} — {track_data['title']}** не может быть воспроизведён "
                                f"из-за возрастных ограничений YouTube (18+). (Пропускаем...)"
                            )
                        else:
                            await state.text_channel.send(
                                f"⚠️ Не удалось найти аудиопоток для трека: **{track_data['artists']} — {track_data['title']}** (пропускаем...)"
                            )
                    state.index += 1
                    self._play_next(guild_id)
                    return
                
                track_data["stream_url"] = stream_url

                # Обновляем метаданные, если это поиск или прямая ссылка/плейлист YouTube
                if (track_data.get("id") == "search" or track_data.get("id", "").startswith("search:")) and metadata:
                    track_data["title"] = metadata.get("title", track_data["title"])
                    track_data["artists"] = metadata.get("uploader", "YouTube/SoundCloud")
                    
                    duration = metadata.get("duration")
                    if duration:
                        track_data["duration"] = int(duration)
                        
                    thumbnail = metadata.get("thumbnail")
                    if thumbnail:
                        track_data["thumbnail_url"] = thumbnail

            before_opts = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
            opts = "-vn -sn -dn -nostdin -threads 1 -loglevel error -map 0:a:0"

            is_vk = any(domain in stream_url for domain in ["vkuseraudio.net", "vk.me", "vk.com", "vk.ru", "userapi", ".m3u8"])
            if is_vk:
                vk_headers = (
                    "Referer: https://vk.com/\r\n"
                    "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36\r\n"
                )
                before_opts += f' -headers "{vk_headers}" -protocol_whitelist file,http,https,tcp,tls,crypto'

            # Передаём прокси напрямую в FFmpeg через -http_proxy флаг.
            # FFmpeg-бинарник НЕ читает os.environ['https_proxy'] для HTTPS-потоков,
            # поэтому единственный надёжный способ — флаг в before_options.
            is_youtube = (
                "googlevideo.com" in stream_url
                or "youtube.com" in stream_url
                or "youtu.be" in stream_url
            )
            if is_youtube and config.STREAM_PROXY:
                before_opts += f" -http_proxy {config.STREAM_PROXY}"

            if getattr(state, "start_offset", 0) > 0:
                before_opts += f" -ss {state.start_offset}"
                state.playback_elapsed = state.start_offset
                state.start_offset = 0
            else:
                state.playback_elapsed = 0.0

            source = discord.FFmpegPCMAudio(
                stream_url,
                before_options=before_opts,
                options=opts
            )
            transformed = discord.PCMVolumeTransformer(source, volume=state.volume * 0.50)

            def after_callback(error: Exception | None) -> None:
                try:
                    logger.info(
                        "[Spotify] after_callback: guild=%s error=%s is_switching=%s is_sleeping=%s "
                        "is_seeking=%s single_track_mode=%s index=%s queue_len=%s",
                        guild_id, error,
                        getattr(state, "is_switching", False),
                        getattr(state, "is_sleeping", False),
                        getattr(state, "is_seeking", False),
                        getattr(state, "single_track_mode", False),
                        state.index, len(state.queue)
                    )
                    if getattr(state, "is_switching", False):
                        state.is_switching = False
                        return

                    if error:
                        logger.error("Ошибка воспроизведения Spotify на сервере %s: %s", guild_id, error)
                    
                    if getattr(state, "is_sleeping", False):
                        return

                    if getattr(state, "is_seeking", False):
                        state.is_seeking = False
                    elif getattr(state, "single_track_mode", False):
                        state.is_paused = True
                        coro = self.send_now_playing(guild_id)
                        asyncio.run_coroutine_threadsafe(coro, self.bot.loop)
                        return
                    else:
                        state.index += 1
                        
                    self._play_next(guild_id)
                except Exception as ex:
                    logger.exception("Исключение в after_callback Spotify на сервере %s: %s", guild_id, ex)

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
            
            # Запускаем фоновую предзагрузку для следующих треков
            self.start_preload(guild_id)

        except Exception as e:
            logger.exception("Критическая ошибка в play_track Spotify на сервере %s: %s", guild_id, e)

    def _stop_other_cogs(self, guild_id: int) -> None:
        stop_other_cogs(self.bot, guild_id, "SpotifyMusic")

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

        vc = None
        for client_vc in self.bot.voice_clients:
            if client_vc.guild.id == guild_id:
                vc = client_vc
                break
        if vc and vc.is_connected():
            await vc.disconnect()

        await db.delete_spotify_session(guild_id)
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

        cfg = await db.get_spotify_config(guild_id)
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

    # ── Управление плеером ──

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
        if not await self._check_interaction_permissions(interaction):
            return
        guild_id = interaction.guild_id
        state = self.get_state(guild_id)
        volume = level / 100.0
        state.volume = volume
        self._volume[guild_id] = volume
        await db.update_spotify_volume(guild_id, volume)

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
            state.single_track_mode = False

        if state.index + 1 >= len(state.queue):
            await interaction.response.send_message("⏹️ Очередь пуста, останавливаю воспроизведение.", ephemeral=True)
            await self._stop_and_cleanup(guild_id, "⏹️ Очередь воспроизведения Spotify завершена.")
            return

        await safe_defer(interaction)
        state.index += 1
        await self.play_track(guild_id)

    async def jump_to_track(self, interaction: discord.Interaction, target_index: int) -> None:
        if not await self._check_interaction_permissions(interaction):
            return
        guild_id = interaction.guild_id
        state = self.get_state(guild_id)
        if not state.queue or target_index < 0 or target_index >= len(state.queue):
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Указанный индекс трека вне диапазона очереди.", ephemeral=True)
            return

        if not interaction.response.is_done():
            await interaction.response.send_message(f"⏩ Переключение на трек #{target_index + 1}...", ephemeral=True)
        else:
            await interaction.followup.send(f"⏩ Переключение на трек #{target_index + 1}...", ephemeral=True)

        state.index = target_index
        state.playback_elapsed = 0.0
        state.single_track_mode = False
        await self.play_track(guild_id)

    async def seek_to_index(self, interaction: discord.Interaction, target_index: int) -> None:
        await self.jump_to_track(interaction, target_index)

    async def stop_playback(self, interaction: discord.Interaction) -> None:
        if not await self._check_interaction_permissions(interaction):
            return
        guild_id = interaction.guild_id
        await interaction.response.send_message("⏹️ Воспроизведение остановлено.", ephemeral=True)
        await self._stop_and_cleanup(guild_id, "⏹️ Стриминг Spotify остановлен.")

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
        if track_duration > 0:
            target_seconds = max(0, min(target_seconds, track_duration - 2))
        else:
            target_seconds = max(0, target_seconds)

        state.start_offset = target_seconds
        state.is_seeking = False
        
        # Сбрасываем кэшированную ссылку, чтобы при перемотке ffmpeg получил свежую сессию от CDN
        track_data = state.queue[state.index]
        track_data.pop("stream_url", None)

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

        embed = discord.Embed(title="📋 Очередь воспроизведения Spotify", color=discord.Color.green())
        
        current_track = state.queue[state.index] if state.index < len(state.queue) else None
        if current_track:
            embed.description = f"**Сейчас играет:**\n`[{format_duration(current_track['duration'])}]` {current_track['artists']} — {current_track['title']}\n\n**Далее в очереди:**\n"
        else:
            embed.description = "**Очередь завершена.**\n\n**Далее в очереди:**\n"

        next_tracks = state.queue[state.index + 1 : state.index + 11]
        if not next_tracks:
            embed.description += "*Пусто*"
        else:
            for idx, track in enumerate(next_tracks, start=1):
                embed.description += f"**{idx}.** `[{format_duration(track['duration'])}]` {track['artists']} — {track['title']}\n"

        total_remaining = sum(t["duration"] for t in state.queue[state.index:])
        embed.set_footer(text=f"Всего треков: {len(state.queue)} • Осталось играть: {format_duration(total_remaining)}")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def play_spotify_link(self, interaction: discord.Interaction, raw_input: str, clear_queue: bool = False) -> None:
        guild_id = interaction.guild_id
        state = self.get_state(guild_id)
        
        all_tracks = await self.resolve_spotify_metadata_from_text(raw_input)
        if not all_tracks:
            if is_vk_url(raw_input):
                await interaction.followup.send(
                    "⚠️ **Не удалось загрузить трек из VK.**\n\n"
                    "ВКонтакте блокирует анонимный доступ к персональным аудиозаписям профиля (`audio...`).\n\n"
                    "💡 **Как проиграть музыку из VK:**\n"
                    "• Используйте ссылку на **публичный плейлист** (`https://vk.com/music/playlist/...`)\n"
                    "• Или ссылку на **пост на стене с треком** (`https://vk.com/wall...`)\n"
                    "• Или просто введите **название песни** (например: `/spotify play Группа крови`)",
                    ephemeral=True
                )
            else:
                await interaction.followup.send("❌ Не удалось получить информацию о треках.", ephemeral=True)
            return

        vc = interaction.guild.voice_client
        is_playing = vc and (vc.is_playing() or vc.is_paused())

        voice_channel = await get_user_voice_channel(interaction)
        if not voice_channel:
            await interaction.followup.send("❌ Войдите в голосовой канал для старта.", ephemeral=True)
            return

        if not vc or not vc.is_connected():
            vc = await safe_voice_connect(interaction.guild, voice_channel)
            if not vc:
                await safe_send(interaction, "❌ Не удалось подключиться к голосовому каналу.", ephemeral=True)
                return
        elif vc.channel.id != voice_channel.id:
            vc = await safe_voice_connect(interaction.guild, voice_channel)
            if not vc:
                await safe_send(interaction, "❌ Не удалось переместить бота в ваш канал.", ephemeral=True)
                return

        state.text_channel = interaction.channel
        state.is_temporary = True
        state.initiator_id = interaction.user.id

        if not is_playing or not state.queue or clear_queue:
            if clear_queue:
                state.queue.clear()
            state.queue.extend(all_tracks)
            state.index = len(state.queue) - len(all_tracks)
            state.single_track_mode = (len(state.queue) == 1)
            await self.play_track(guild_id)
            await db.update_spotify_last_channel(guild_id, voice_channel.id)
            await interaction.followup.send(f"▶️ Добавлено и запущено {len(all_tracks)} треков!", ephemeral=True)
        else:
            played_tracks = state.queue[:state.index]
            remaining_tracks = state.queue[state.index:]
            
            state.queue = played_tracks + list(all_tracks) + remaining_tracks
            state.index = len(played_tracks)
            state.single_track_mode = False
            
            await self.play_track(guild_id)
            await interaction.followup.send(f"▶️ Переключено на новые треки! Добавлено {len(all_tracks)} в очередь.", ephemeral=True)

        self.start_preload(guild_id)

    async def resolve_youtube_playlist(self, url: str) -> List[dict]:
        """Парсит YouTube плейлист с помощью yt-dlp в flat режиме."""
        ydl_opts = {
            'extract_flat': 'in_playlist',
            'quiet': True,
            'no_warnings': True,
            'socket_timeout': YOUTUBE_PLAYLIST_SOCKET_TIMEOUT,
            'ignoreerrors': True,
        }
        if config.YTDLP_PROXY:
            ydl_opts['proxy'] = config.YTDLP_PROXY

        def _extract():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                return ydl.extract_info(url, download=False)

        try:
            info = await asyncio.to_thread(_extract)
            tracks = []
            if info and 'entries' in info:
                playlist_uploader = info.get('uploader') or info.get('channel') or info.get('uploader_id') or 'YouTube'
                for entry in info['entries']:
                    if len(tracks) >= YOUTUBE_PLAYLIST_MAX_TRACKS:
                        break
                    normalized = normalize_youtube_playlist_entry(entry, playlist_uploader)
                    if normalized:
                        tracks.append(normalized)
            return tracks
        except Exception as e:
            logger.error("[Spotify] Ошибка извлечения YouTube плейлиста: %s", e)
            return []

    async def resolve_spotify_metadata_from_text(self, text: str) -> List[dict]:
        """Парсит строки из текста, разрешает ссылки Spotify/VK/YouTube и обычные поисковые запросы."""
        lines = [line.strip() for line in re.split(r'[\r\n,]+', text) if line.strip()]
        all_tracks = []
        for line in lines:
            # Проверяем, ссылка ли это на Spotify
            sp_type, sp_id = self.extract_spotify_id(line)
            if sp_type and sp_id:
                res = await self.resolve_spotify_metadata(sp_type, sp_id)
                all_tracks.extend(res)
            # Проверяем, ссылка ли это на VK Музыку
            elif is_vk_url(line):
                vk_tracks = await self.manager.parse_vk_url(line)
                for t in vk_tracks:
                    direct_url = t.get('direct_url')
                    all_tracks.append({
                        "id": t['id'],
                        "title": t['title'],
                        "artists": t['artist'],
                        "duration": t['duration'],
                        "thumbnail_url": t['cover'],
                        "search_query": direct_url if direct_url else t['search_query'],
                        "direct_url": direct_url
                    })
            # Проверяем YouTube плейлист по URL или ID (исключая миксы RD, Liked LL, Watch Later WL)
            elif extract_youtube_playlist_id(line) and not any(f"list={prefix}" in line for prefix in ("RD", "LL", "WL")):
                pl_id = extract_youtube_playlist_id(line)
                canonical_url = f"https://www.youtube.com/playlist?list={pl_id}"
                res = await self.resolve_youtube_playlist(canonical_url)
                all_tracks.extend(res)
            # Все остальное (видео YouTube, SoundCloud, или поисковые запросы)
            else:
                all_tracks.append({
                    "id": "search",
                    "title": line,
                    "artists": "Поиск",
                    "duration": 0,
                    "thumbnail_url": None,
                    "search_query": line
                })
        return all_tracks

    async def play_server_playlist(self, interaction: discord.Interaction, playlist_id: int) -> None:
        """Запускает проигрывание серверного плейлиста."""
        guild_id = interaction.guild_id
        state = self.get_state(guild_id)

        playlists = await db.get_spotify_playlists(guild_id)
        pl = next((p for p in playlists if p["id"] == playlist_id), None)
        if not pl:
            await interaction.followup.send("❌ Плейлист не найден.", ephemeral=True)
            return

        tracks = await self.resolve_spotify_metadata_from_text(pl["track_ids"])
        if not tracks:
            await interaction.followup.send("❌ Не удалось извлечь треки из плейлиста.", ephemeral=True)
            return

        state.queue.clear()
        state.index = 0
        vc = interaction.guild.voice_client
        if vc and (vc.is_playing() or vc.is_paused()):
            vc.stop()

        state.queue.extend(tracks)
        state.is_temporary = False
        state.single_track_mode = False
        state.initiator_id = interaction.user.id
        state.source_playlist_id = playlist_id

        voice_channel = await get_user_voice_channel(interaction)
        if not voice_channel:
            await interaction.followup.send("❌ Войдите в голосовой канал для старта.", ephemeral=True)
            return

        if not vc or not vc.is_connected():
            vc = await safe_voice_connect(interaction.guild, voice_channel)
            if not vc:
                await safe_send(interaction, "❌ Не удалось подключиться к голосовому каналу.", ephemeral=True)
                return
        elif vc.channel.id != voice_channel.id:
            vc = await safe_voice_connect(interaction.guild, voice_channel)
            if not vc:
                await safe_send(interaction, "❌ Не удалось переместить бота.", ephemeral=True)
                return

        state.text_channel = interaction.channel
        await self.play_track(guild_id)
        await db.update_spotify_last_channel(guild_id, voice_channel.id)
        await interaction.followup.send(f"▶️ Запущен плейлист сервера: **{pl['name']}** ({len(tracks)} треков)!", ephemeral=True)

        self.start_preload(guild_id)

    # ── Слэш-команды ──

    @app_commands.command(name="dynamic", description="Управление плеером Dynamic (Spotify/VK/YouTube/SoundCloud)")
    @app_commands.describe(url="Ссылка (Spotify, YouTube, VK, SoundCloud) или поисковый запрос")
    async def spotify_command(self, interaction: discord.Interaction, url: str) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return

        await safe_defer(interaction, ephemeral=True, thinking=True)
        await self.play_spotify_link(interaction, url)

    @app_commands.command(name="vk", description="Воспроизведение музыки из VK по публичной ссылке или запросу в Dynamic плеере")
    @app_commands.describe(url="Публичная ссылка VK (трек, плейлист, альбом, профиль, пост) или поисковый запрос")
    async def vk_command(self, interaction: discord.Interaction, url: str) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return

        await safe_defer(interaction, ephemeral=True, thinking=True)
        await self.play_spotify_link(interaction, url)

    # ── 24/7 Автоподключение и Фоновые проверки ──

    async def _auto_restore_connections(self) -> None:
        await self.bot.wait_until_ready()
        logger.info("[Spotify] запуск автовосстановления 24/7 подключений...")
        try:
            configs = await db.get_all_spotify_configs_to_restore()
            for cfg in configs:
                guild_id = cfg["guild_id"]
                channel_id = cfg["last_channel_id"]
                
                guild = self.bot.get_guild(guild_id)
                if not guild:
                    continue
                channel = guild.get_channel(channel_id)
                if not channel or not isinstance(channel, discord.VoiceChannel):
                    continue

                try:
                    vc = await safe_voice_connect(guild, channel, self_deaf=True)
                    if not vc:
                        logger.warning("[Spotify] не удалось подключиться к 24/7 каналу %s", channel_id)
                        continue
                except Exception as e:
                    logger.error("[Spotify] ошибка при подключении к 24/7 каналу: %s", e)
                    continue

                # Восстанавливаем состояние и плейлист
                try:
                    state = self.get_state(guild_id)
                    state.text_channel = channel
                    
                    # 1. Попробуем загрузить сохраненную сессию
                    saved = await db.get_spotify_session(guild_id)
                    has_queue = False
                    if saved and saved.get("queue_track_ids"):
                        logger.info("[Spotify] найдена сохраненная сессия для сервера %s. Загружаем...", guild_id)
                        import json
                        try:
                            state.queue = json.loads(saved["queue_track_ids"])
                            state.index = saved["current_index"]
                            state.playback_elapsed = float(saved["playback_position"])
                            state.single_track_mode = saved["single_track_mode"]
                            state.source_playlist_id = saved.get("source_playlist_id")
                            has_queue = len(state.queue) > 0
                        except Exception as je:
                            logger.error("[Spotify] ошибка десериализации сессии: %s", je)

                    # 2. Если сессии нет, пробуем загрузить дефолтный плейлист
                    if not has_queue:
                        default_pl_id = cfg.get("default_playlist_id")
                        if default_pl_id:
                            playlists = await db.get_spotify_playlists(guild_id)
                            pl = next((p for p in playlists if p["id"] == default_pl_id), None)
                            if pl:
                                logger.info("[Spotify] загружаем дефолтный плейлист сервера для %s...", guild_id)
                                state.queue = await self.resolve_spotify_metadata_from_text(pl["track_ids"])
                                state.index = 0
                                state.playback_elapsed = 0.0
                                state.single_track_mode = False
                                state.source_playlist_id = default_pl_id
                                has_queue = len(state.queue) > 0

                    # 3. Запуск/засыпание в зависимости от активных участников
                    if has_listeners(channel):
                        if has_queue and state.index < len(state.queue) and not (vc.is_playing() or vc.is_paused()):
                            state.start_offset = int(state.playback_elapsed)
                            await self.play_track(guild_id)
                    else:
                        logger.info("[Spotify] канал 24/7 пуст при старте, бот засыпает.")
                        state.is_sleeping = True
                except Exception as e:
                    logger.error("[Spotify] ошибка при обработке автовосстановления 24/7: %s", e)
        except Exception as e:
            logger.error("[Spotify] ошибка автовосстановления: %s", e)

    async def wakeup_player(self, guild_id: int, vc: discord.VoiceClient) -> None:
        state = self.get_state(guild_id)
        if not state.is_sleeping:
            return
        state.is_sleeping = False
        cfg = await db.get_spotify_config(guild_id)
        
        try:
            # Восстанавливаем сессию
            saved = await db.get_spotify_session(guild_id)
            has_queue = False
            if saved and saved.get("queue_track_ids"):
                import json
                try:
                    state.queue = json.loads(saved["queue_track_ids"])
                    state.index = saved["current_index"]
                    state.playback_elapsed = float(saved["playback_position"])
                    state.single_track_mode = saved["single_track_mode"]
                    state.source_playlist_id = saved.get("source_playlist_id")
                    has_queue = len(state.queue) > 0
                except Exception as je:
                    logger.error("[Spotify] ошибка десериализации сессии: %s", je)
                
            if not has_queue:
                # Сессии нет, пробуем загрузить дефолтный плейлист
                default_pl_id = cfg.get("default_playlist_id") if cfg else None
                if default_pl_id:
                    playlists = await db.get_spotify_playlists(guild_id)
                    pl = next((p for p in playlists if p["id"] == default_pl_id), None)
                    if pl:
                        logger.info("[Spotify] загружаем дефолтный плейлист сервера для %s...", guild_id)
                        state.queue = await self.resolve_spotify_metadata_from_text(pl["track_ids"])
                        state.index = 0
                        state.playback_elapsed = 0.0
                        state.single_track_mode = False
                        state.source_playlist_id = default_pl_id
                        has_queue = len(state.queue) > 0

            if has_queue and state.index < len(state.queue):
                state.start_offset = int(state.playback_elapsed)
                await self.play_track(guild_id)
            else:
                state.queue.clear()
                state.index = 0
                await self.send_player_panel_on_wakeup(vc.channel)
        except Exception as e:
            logger.exception("[Spotify] Ошибка в wakeup_player на сервере %s: %s", guild_id, e)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState) -> None:
        if member.bot and member.id != self.bot.user.id:
            return

        guild_id = member.guild.id
        vc = member.guild.voice_client

        if member.id == self.bot.user.id:
            if before.channel and not after.channel:
                logger.info("[Spotify] бота отключили от голосового канала %s (сервер %s). Очищаем состояние.", before.channel.id, guild_id)
                cfg = await db.get_spotify_config(guild_id)
                keep_alive = cfg.get("keep_alive", False) if cfg else False
                
                state = self.get_state(guild_id)
                if not keep_alive:
                    await db.delete_spotify_session(guild_id)
                    self.reset_state(guild_id)
                else:
                    if state.playback_start_time > 0:
                        state.playback_elapsed += (time.time() - state.playback_start_time)
                    state.playback_start_time = 0.0
                    if vc and (vc.is_playing() or vc.is_paused()):
                        vc.stop()
                    state.is_sleeping = True
                    await self.save_session_to_db(guild_id)
                    state.np_msg = None
            elif after.channel and before.channel != after.channel:
                # Бот подключился или переместился в канал
                logger.info("[Spotify] бот подключился к каналу %s (сервер %s). Проверяем участников.", after.channel.id, guild_id)
                state = self.get_state(guild_id)
                state.text_channel = after.channel
                non_bot_members = [m for m in after.channel.members if not m.bot]
                cfg = await db.get_spotify_config(guild_id)
                keep_alive = cfg.get("keep_alive", False) if cfg else False
                
                if keep_alive:
                    if non_bot_members:
                        logger.info("[Spotify] в канале уже есть люди. Пробуждаем плеер.")
                        await self.wakeup_player(guild_id, vc)
                    else:
                        logger.info("[Spotify] канал пуст. Засыпаем.")
                        state.is_sleeping = True
            return

        if not before.channel and after.channel:
            cfg = await db.get_spotify_config(guild_id)
            keep_alive = cfg.get("keep_alive", False) if cfg else False
            last_channel_id = cfg.get("last_channel_id") if cfg else None
            
            if keep_alive and after.channel and (last_channel_id == after.channel.id or last_channel_id is None):
                non_bot = [m for m in after.channel.members if not m.bot]
                if len(non_bot) == 1:
                    if not vc or not vc.is_connected() or vc.channel is None:
                        lofi_cog = self.bot.get_cog("LofiRadio")
                        ym_cog = self.bot.get_cog("YandexMusic")
                        rutube_cog = self.bot.get_cog("RutubeMusic")
                        if (lofi_cog and lofi_cog._voice_clients.get(guild_id)) or (ym_cog and ym_cog._voice_clients.get(guild_id)) or (rutube_cog and rutube_cog.get_state(guild_id).queue):
                            return
                        
                        try:
                            logger.info("[Spotify] Пользователь зашел в пустой канал. Автоподключение 24/7.")
                            vc = await safe_voice_connect(member.guild, after.channel, self_deaf=True)
                            if not vc:
                                logger.warning("[Spotify] Автоподключение 24/7 не удалось.")
                            return
                        except Exception as e:
                            logger.error("[Spotify] Ошибка при автоподключении 24/7: %s", e)
                            return

        # 1. Засыпание / Просыпание
        if vc and vc.channel:
            state = self.get_state(guild_id)
            cfg = await db.get_spotify_config(guild_id)
            keep_alive = cfg.get("keep_alive", False)

            # Проверяем количество людей в канале
            non_bot_members = [m for m in vc.channel.members if not m.bot]
            
            if before.channel == vc.channel and after.channel != vc.channel:
                # Люди вышли
                if not non_bot_members:
                    if keep_alive:
                        logger.info("[Spotify] канал 24/7 опустел на сервере %s. Засыпаем.", guild_id)
                        if vc.is_playing() or vc.is_paused():
                            if state.playback_start_time > 0:
                                state.playback_elapsed += (time.time() - state.playback_start_time)
                            state.playback_start_time = 0.0
                            vc.stop()
                        state.is_sleeping = True
                        await self.save_session_to_db(guild_id)
                        state.np_msg = None
                    else:
                        # 24/7 выключен, проверяем другие коги перед отключением
                        if vc.is_playing() or vc.is_paused():
                            vc.stop()

                        # Передаем управление другим когам с включенным 24/7
                        # RuTube
                        rt_cfg = await db.get_rutube_config(guild_id)
                        if rt_cfg and rt_cfg.get("keep_alive", False):
                            logger.info("[Spotify] Канал переходит в режим ожидания 24/7 для RuTube. Передаем управление.")
                            await db.delete_spotify_session(guild_id)
                            self.reset_state(guild_id)
                            return
                            
                        # Yandex Music
                        ym_cfg = await db.get_ym_settings(guild_id)
                        if ym_cfg and ym_cfg.get("keep_alive", False):
                            logger.info("[Spotify] Канал переходит в режим ожидания 24/7 для ЯМ. Передаем управление.")
                            ym_cog = self.bot.get_cog("YandexMusic")
                            if ym_cog:
                                ym_cog._voice_clients[guild_id] = vc
                            await db.delete_spotify_session(guild_id)
                            self.reset_state(guild_id)
                            return

                        # Lofi Radio
                        lofi_cfg = await db.get_lofi_config(guild_id)
                        if lofi_cfg and lofi_cfg.get("keep_alive", False):
                            logger.info("[Spotify] Канал переходит в режим ожидания 24/7 для Lofi. Передаем управление.")
                            lofi_cog = self.bot.get_cog("LofiRadio")
                            if lofi_cog:
                                lofi_cog._voice_clients[guild_id] = vc
                            await db.delete_spotify_session(guild_id)
                            self.reset_state(guild_id)
                            return

                        logger.info("[Spotify] канал опустел и 24/7 выключен. Отключаемся.")
                        await vc.disconnect()
                        await db.delete_spotify_session(guild_id)
                        self.reset_state(guild_id)
                        
            elif after.channel == vc.channel and before.channel != vc.channel:
                # Люди зашли в спящий канал
                if state.is_sleeping and non_bot_members:
                    logger.info("[Spotify] пользователь зашел в спящий канал 24/7. Просыпаемся.")
                    await self.wakeup_player(guild_id, vc)

    async def resolve_spotify_metadata_from_ids(self, track_ids: List[str]) -> List[dict]:
        """Восстановление очереди треков по ID (без API ключей, через скрапинг/поиск)."""
        if not track_ids:
            return []
        
        tracks = []
        try:
            for tid in track_ids:
                if tid.startswith("search:"):
                    query = tid.replace("search:", "", 1)
                    tracks.append({
                        "id": tid,
                        "title": query,
                        "artists": "Поиск",
                        "duration": 0,
                        "thumbnail_url": None,
                        "search_query": query
                    })
                else:
                    res = await self.resolve_spotify_metadata("track", tid)
                    if res:
                        tracks.extend(res)
        except Exception as e:
            logger.error("[Spotify] Ошибка восстановления метаданных из ID: %s", e)
        return tracks

    async def send_player_panel_on_wakeup(self, channel: discord.VoiceChannel):
        embed = discord.Embed(
            title="🔴 Dynamic Музыка",
            description="Бот проснулся в режиме 24/7! Для запуска музыки отправьте ссылку с помощью кнопки ниже.",
            color=discord.Color.red()
        )
        embed.set_footer(text="S&Y Integration • DynamicVoiceBot")
        view = SpotifyPlayerView()
        await channel.send(embed=embed, view=view)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SpotifyMusic(bot))
