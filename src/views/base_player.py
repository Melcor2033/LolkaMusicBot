import lolka as discord
import logging
from typing import Optional, List, Dict, Any

logger = logging.getLogger("base_views")


async def ensure_voice_connection(guild: discord.Guild, channel: discord.VoiceChannel) -> Optional[discord.VoiceClient]:
    """Подключает бота к голосовому каналу или перемещает его, если подключение уже существует."""
    vc = guild.voice_client
    if not vc:
        return await channel.connect(self_deaf=True)
    elif vc.channel.id != channel.id:
        await vc.move_to(channel)
    return vc


def get_non_bot_members(channel: Optional[discord.abc.GuildChannel]) -> list[discord.Member]:
    """Возвращает список реальных участников (не ботов) в голосовом канале."""
    if not channel or not hasattr(channel, "members"):
        return []
    return [m for m in channel.members if not m.bot]


def has_listeners(channel: Optional[discord.abc.GuildChannel]) -> bool:
    """Проверяет, есть ли в голосовом канале живые слушатели (пользователи-неботы)."""
    return len(get_non_bot_members(channel)) > 0


def parse_time_to_seconds(time_str: str) -> Optional[int]:
    """Преобразует строку времени (например '1:30' или '90') в секунды."""
    if not time_str:
        return None
    time_str = time_str.strip()
    try:
        if ":" in time_str:
            parts = time_str.split(":")
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            elif len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        return int(time_str)
    except ValueError:
        return None


def create_progress_bar(elapsed_seconds: int, total_seconds: Optional[int], length: int = 8) -> str:
    """Генерирует визуальный прогресс-бар воспроизведения.
    Пример: `01:30` 🔘▬▬▬▬▬▬▬▬▬▬▬▬ `03:45`
    Если длительность неизвестна (например, прямая трансляция), возвращает: `01:30` 🔴 **Прямой эфир**
    """
    def format_time(sec: float | int) -> str:
        sec = max(0, int(sec))
        m, s = divmod(sec, 60)
        h, m = divmod(m, 60)
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    elapsed_str = format_time(elapsed_seconds)

    if not total_seconds or total_seconds <= 0:
        return f"`{elapsed_str}` 🔴 **Прямой эфир**"

    total_str = format_time(total_seconds)
    progress = min(1.0, max(0.0, elapsed_seconds / total_seconds))
    filled_length = min(length - 1, max(0, int(round(length * progress))))
    
    bar_chars = []
    for i in range(length):
        if i == filled_length:
            bar_chars.append("🔘")
        else:
            bar_chars.append("▬")
            
    bar = "".join(bar_chars)
    return f"`{elapsed_str}` {bar} `{total_str}`"


def format_player_status(is_paused: bool = False, is_live: bool = False) -> str:
    """Возвращает лаконичную строку статуса плеера (▶️ Играет, ⏸️ Пауза, 🔴 В эфире)."""
    if is_live:
        return "🔴 В эфире"
    if is_paused:
        return "⏸️ Пауза"
    return "▶️ Играет"


async def run_timeline_updater_loop(cog, interval: float = 5.0) -> None:
    """Универсальный фоновый цикл обновления прогресс-бара для любого музыкального кога."""
    import asyncio
    await cog.bot.wait_until_ready()
    while not cog.bot.is_closed():
        try:
            await asyncio.sleep(interval)
            
            states_dict = getattr(cog, "states", getattr(cog, "_states", {}))
            for guild_id, state in list(states_dict.items()):
                np_msg = getattr(state, "np_msg", None) if not isinstance(state, dict) else state.get("np_msg")
                if np_msg:
                    guild = cog.bot.get_guild(guild_id)
                    vc = getattr(cog, "_voice_clients", {}).get(guild_id) or (guild.voice_client if guild else None)
                    if vc and vc.is_connected() and vc.is_playing():
                        try:
                            await cog.send_now_playing(guild_id)
                        except Exception:
                            pass
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.debug("Ошибка в run_timeline_updater_loop (%s): %s", cog.__class__.__name__, e)


class BasePlayerState:
    """Базовый класс состояния воспроизведения для плееров (Spotify, RuTube и др.)."""
    def __init__(self, guild_id: int):
        import asyncio
        self.guild_id: int = guild_id
        self.queue: List[dict] = []
        self.index: int = 0
        self.np_msg: Optional[discord.Message] = None
        self.text_channel: Optional[discord.TextChannel] = None
        self.volume: float = 0.5
        self.is_paused: bool = False
        self.start_offset: int = 0
        self.is_seeking: bool = False
        self.playback_start_time: float = 0.0
        self.playback_elapsed: float = 0.0
        self.is_temporary: bool = False
        self.single_track_mode: bool = False
        self.initiator_id: Optional[int] = None
        self.is_sleeping: bool = False
        self.source_playlist_id: Optional[int] = None
        self.lock: asyncio.Lock = asyncio.Lock()
        self.idle_msg: Optional[discord.Message] = None

    def get_current_time(self) -> int:
        import time
        if self.is_paused or self.is_sleeping:
            return int(self.playback_elapsed)
        if self.playback_start_time > 0:
            return int(self.playback_elapsed + (time.time() - self.playback_start_time))
        return int(self.playback_elapsed)


def stop_other_cogs(bot: Any, guild_id: int, current_cog_name: str) -> None:
    """Очищает состояния и активные сессии всех остальных плееров при старте нового."""
    all_cogs = ("YandexMusic", "SpotifyMusic", "RutubeMusic", "LofiRadio")
    for name in all_cogs:
        if name == current_cog_name:
            continue
        cog = bot.get_cog(name)
        if cog:
            if hasattr(cog, "reset_state"):
                cog.reset_state(guild_id)
            elif hasattr(cog, "_voice_clients"):
                cog._voice_clients.pop(guild_id, None)
                if hasattr(cog, "_current_station"):
                    cog._current_station.pop(guild_id, None)


class UniversalSeekModal(discord.ui.Modal, title="⏱️ Перемотка ко времени"):
    time_input = discord.ui.TextInput(
        label="Укажите время (например 1:30 или 90)",
        placeholder="1:30",
        required=True,
        max_length=10
    )

    def __init__(self, cog=None):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        seconds = parse_time_to_seconds(self.time_input.value)
        if seconds is None or seconds < 0:
            await interaction.response.send_message("❌ Некорректный формат времени. Используйте `1:30` или `90`.", ephemeral=True)
            return

        cog = self.cog
        if not cog:
            for cog_name in ("SpotifyMusic", "RutubeMusic", "YandexMusic"):
                c = interaction.client.get_cog(cog_name)
                if c:
                    vc = interaction.guild.voice_client if interaction.guild else None
                    if vc and vc.is_connected():
                        cog = c
                        break

        if cog and hasattr(cog, "seek_to"):
            await cog.seek_to(interaction, seconds)
        else:
            await interaction.response.send_message("❌ Плеер не поддерживает перемотку.", ephemeral=True)


class UniversalVolumeModal(discord.ui.Modal, title="🔊 Громкость"):
    """Универсальное модальное окно ввода уровня громкости для всех плееров."""
    volume_input = discord.ui.TextInput(
        label="Громкость (1–100)",
        style=discord.TextStyle.short,
        placeholder="50",
        required=True,
        min_length=1,
        max_length=3,
    )

    def __init__(self, cog=None):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            level = int(self.volume_input.value)
            if not 1 <= level <= 100:
                await interaction.response.send_message("❌ Введите число от 1 до 100.", ephemeral=True)
                return
        except ValueError:
            await interaction.response.send_message("❌ Введите корректное число.", ephemeral=True)
            return

        cog = self.cog
        if not cog:
            for cog_name in ("SpotifyMusic", "RutubeMusic", "YandexMusic", "LofiRadio"):
                c = interaction.client.get_cog(cog_name)
                if c:
                    vc = interaction.guild.voice_client if interaction.guild else None
                    if vc and vc.is_connected():
                        cog = c
                        break

        if cog and hasattr(cog, "change_volume"):
            await cog.change_volume(interaction, level)
        elif cog and hasattr(cog, "set_volume"):
            await cog.set_volume(interaction, level)
        else:
            await interaction.response.send_message("❌ Не удалось найти активный плеер.", ephemeral=True)


class QueueSelect(discord.ui.Select):
    """Выпадающий список очереди воспроизведения с плавающим окном (Sliding Window max 10 элементов)."""
    def __init__(self, queue: Optional[List[Dict[str, Any]]] = None, current_index: int = 0, row: int = 0, player_prefix: str = "universal"):
        queue = queue or []
        options = []
        if not queue:
            options = [
                discord.SelectOption(
                    label="📋 Очередь пуста",
                    value="empty",
                    description="Добавьте треки через поиск [🔍]"
                )
            ]
            placeholder = "📋 Очередь пуста"
        else:
            total_tracks = len(queue)
            max_options = 10

            # Рассчитываем скользящее окно из 10 треков около current_index:
            # Пытаемся показать 2 предыдущих трека, текущий трек и до 7 следующих
            start_idx = max(0, current_index - 2)
            end_idx = min(total_tracks, start_idx + max_options)

            # Если в конце массива осталось меньше 10 элементов, сдвигаем начало назад
            if end_idx - start_idx < max_options:
                start_idx = max(0, end_idx - max_options)

            for i in range(start_idx, end_idx):
                item = queue[i]
                raw_title = item.get("title") or item.get("name") or f"Трек #{i+1}"
                title = str(raw_title)[:80]
                is_current = (i == current_index)
                prefix = "▶️ " if is_current else f"{i+1}. "
                options.append(
                    discord.SelectOption(
                        label=f"{prefix}{title}"[:100],
                        value=str(i),
                        default=is_current
                    )
                )

            placeholder = f"📋 Очередь ({current_index + 1}/{total_tracks})..."

        super().__init__(
            placeholder=placeholder,
            options=options,
            min_values=1,
            max_values=1,
            row=row,
            disabled=not bool(queue),
            custom_id=f"{player_prefix}_queue_select"
        )

    async def callback(self, interaction: discord.Interaction):
        val = self.values[0]
        if val == "empty":
            await interaction.response.send_message("📋 Очередь пуста.", ephemeral=True)
            return

        # Определяем cog по префиксу custom_id (rt_, sp_, ym_)
        prefix = self.custom_id.split("_")[0]
        prefix_to_cog = {
            "rt": "RutubeMusic",
            "sp": "SpotifyMusic",
            "ym": "YandexMusic",
        }
        cog_name = prefix_to_cog.get(prefix)
        cog = interaction.client.get_cog(cog_name) if cog_name else None

        # Fallback: любой активный cog с голосовым каналом
        if not cog:
            for name in ("YandexMusic", "SpotifyMusic", "RutubeMusic"):
                c = interaction.client.get_cog(name)
                if c:
                    vc = interaction.guild.voice_client if interaction.guild else None
                    if vc and vc.is_connected():
                        cog = c
                        break

        if val == "show_full_queue":
            if cog and hasattr(cog, "show_queue"):
                await cog.show_queue(interaction)
            else:
                await interaction.response.send_message("📋 Открытие полной очереди...", ephemeral=True)
            return
        
        target_index = int(val)
        if cog and hasattr(cog, "jump_to_track"):
            await cog.jump_to_track(interaction, target_index)
        elif cog and hasattr(cog, "seek_to_index"):
            await cog.seek_to_index(interaction, target_index)
        else:
            await interaction.response.send_message(f"⏩ Переключение на трек #{target_index + 1}...", ephemeral=True)


class BasePlayerView(discord.ui.View):
    """Базовый универсальный View для всех музыкальных плееров."""

    def __init__(self, cog=None, timeout: Optional[float] = None):
        super().__init__(timeout=timeout)
        self.cog = cog

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item):
        logger.error(f"Ошибка во View {self.__class__.__name__} в элементе {item}: {error}", exc_info=True)
        if not interaction.response.is_done():
            await interaction.response.send_message("❌ Произошла ошибка при обработке взаимодействия.", ephemeral=True)

    async def handle_seek_relative(self, interaction: discord.Interaction, delta_seconds: int):
        if self.cog and hasattr(self.cog, "seek_relative"):
            await self.cog.seek_relative(interaction, delta_seconds)
        else:
            await interaction.response.send_message("❌ Перемотка не поддерживается данным плеером.", ephemeral=True)

    async def handle_source_change(self, interaction: discord.Interaction):
        try:
            from views.ui import MusicSelectionView
        except ImportError:
            from src.views.ui import MusicSelectionView
        embed = discord.Embed(
            title="🎵 Выбор музыкального плеера",
            description="Выберите плеер для воспроизведения музыки:",
            color=discord.Color.red()
        )
        view = MusicSelectionView()
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
