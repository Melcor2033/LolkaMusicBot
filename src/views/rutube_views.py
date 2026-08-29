import lolka as discord
from typing import Optional
from views.ui import is_bot_busy_in_other_channel
from views.base_player import BasePlayerView, UniversalSeekModal, QueueSelect, UniversalVolumeModal
import db

class RutubePlaylistModal(discord.ui.Modal, title="➕ Добавление временного плейлиста"):
    links = discord.ui.TextInput(
        label="Ссылки на видео RuTube",
        style=discord.TextStyle.paragraph,
        placeholder="Вставьте ссылки на видео, каждую с новой строки...\nhttps://rutube.ru/video/...\nhttps://rutube.ru/video/...",
        required=True,
        max_length=2000
    )

    async def on_submit(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("RutubeMusic")
        if not cog:
            await interaction.response.send_message("❌ Модуль RuTube не загружен.", ephemeral=True)
            return
            
        await interaction.response.defer(ephemeral=True, thinking=True)
        await cog.add_playlist_from_text(interaction, self.links.value)

class RutubeSeekModal(discord.ui.Modal, title="⏱️ Перемотка"):
    time_input = discord.ui.TextInput(
        label="Время (секунды или ММ:СС)",
        style=discord.TextStyle.short,
        placeholder="Например: 90 или 1:30",
        required=True,
        max_length=10
    )

    async def on_submit(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("RutubeMusic")
        if not cog:
            await interaction.response.send_message("❌ Модуль RuTube не загружен.", ephemeral=True)
            return

        time_str = self.time_input.value
        seconds = 0
        try:
            if ":" in time_str:
                parts = time_str.split(":")
                if len(parts) == 2:
                    seconds = int(parts[0]) * 60 + int(parts[1])
                elif len(parts) == 3:
                    seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            else:
                seconds = int(time_str)
        except ValueError:
            await interaction.response.send_message("❌ Неверный формат времени.", ephemeral=True)
            return
            
        await cog.seek_to(interaction, seconds)

class RutubeSearchModal(discord.ui.Modal, title="🔍 Поиск и добавление RuTube"):
    url_input = discord.ui.TextInput(
        label="Ссылка(и) на видео RuTube или названия",
        style=discord.TextStyle.paragraph,
        placeholder="https://rutube.ru/video/...\n(можно указывать несколько ссылок с новой строки)",
        required=True,
        max_length=1000
    )

    async def on_submit(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("RutubeMusic")
        if not cog:
            await interaction.response.send_message("❌ Модуль RuTube не загружен.", ephemeral=True)
            return
            
        await cog.play_rutube_search(interaction, self.url_input.value)

class RutubeSelectPlaylistSelect(discord.ui.Select):
    def __init__(self, playlists: list[dict]) -> None:
        options = [
            discord.SelectOption(
                label=p["name"],
                description=f"Треков: {len(p['video_ids'].split(','))}",
                value=str(p["id"])
            ) for p in playlists
        ][:25]
        if not options:
            options = [discord.SelectOption(label="Нет сохраненных плейлистов", value="__none__")]
        super().__init__(
            placeholder="Выберите плейлист для запуска...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="rt_playlist_play_select"
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        playlist_id = self.values[0]
        if playlist_id == "__none__":
            await interaction.response.send_message("❌ Нет доступных плейлистов.", ephemeral=True)
            return
            
        cog = interaction.client.get_cog("RutubeMusic")
        if cog:
            await cog.play_playlist(interaction, int(playlist_id))

class RutubeReadyView(discord.ui.View):
    """View для режима ожидания RuTube-плеера (без активного воспроизведения)."""
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Поиск / Ссылка", emoji="🔍", style=discord.ButtonStyle.primary, custom_id="rt_ready_search", row=0)
    async def search_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        await interaction.response.send_modal(RutubeSearchModal())

    @discord.ui.button(label="Выбрать плейлист", emoji="📁", style=discord.ButtonStyle.secondary, custom_id="rt_ready_select_pl", row=0)
    async def select_playlist_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        playlists = await db.get_rutube_playlists(interaction.guild_id)
        if not playlists:
            await interaction.response.send_message("❌ На этом сервере еще нет сохраненных плейлистов RuTube.", ephemeral=True)
            return
        await interaction.response.send_message("📁 Выберите плейлист для воспроизведения:", view=RutubeSelectPlaylistView(playlists), ephemeral=True)

    @discord.ui.button(label="Выбор плеера", emoji="🎵", style=discord.ButtonStyle.secondary, custom_id="rt_ready_source", row=0)
    async def source_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        from views.ui import MusicSelectionView, build_music_selection_embed
        embed = build_music_selection_embed()
        if interaction.response.is_done():
            await interaction.message.edit(embed=embed, view=MusicSelectionView())
        else:
            await interaction.response.edit_message(embed=embed, view=MusicSelectionView())

class RutubeSelectPlaylistView(discord.ui.View):
    def __init__(self, playlists: list[dict]) -> None:
        super().__init__(timeout=180)
        self.add_item(RutubeSelectPlaylistSelect(playlists))

class RutubePlayerView(BasePlayerView):
    """Persistent View с кнопками управления RuTube-плеером."""
    def __init__(self, queue: Optional[list] = None, current_index: int = 0) -> None:
        super().__init__(timeout=None)
        self.add_item(QueueSelect(queue=queue, current_index=current_index, row=0, player_prefix="rt"))

    @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.secondary, custom_id="rt_prev", row=1)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        cog = interaction.client.get_cog("RutubeMusic")
        if cog:
            await cog.previous_track(interaction)

    @discord.ui.button(emoji="⏯️", style=discord.ButtonStyle.secondary, custom_id="rt_pause", row=1)
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        cog = interaction.client.get_cog("RutubeMusic")
        if cog:
            await cog.toggle_pause(interaction)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary, custom_id="rt_skip", row=1)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        cog = interaction.client.get_cog("RutubeMusic")
        if cog:
            await cog.skip_track(interaction)

    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger, custom_id="rt_stop", row=1)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        cog = interaction.client.get_cog("RutubeMusic")
        if cog:
            await cog.stop_playback(interaction)

    @discord.ui.button(emoji="🔊", style=discord.ButtonStyle.secondary, custom_id="rt_volume", row=1)
    async def volume_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        cog = interaction.client.get_cog("RutubeMusic")
        await interaction.response.send_modal(UniversalVolumeModal(cog=cog))

    @discord.ui.button(emoji="↺", style=discord.ButtonStyle.secondary, custom_id="rt_rewind_15", row=2)
    async def rewind_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        cog = interaction.client.get_cog("RutubeMusic")
        if cog:
            await cog.seek_relative(interaction, -10)

    @discord.ui.button(emoji="↻", style=discord.ButtonStyle.secondary, custom_id="rt_forward_15", row=2)
    async def forward_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        cog = interaction.client.get_cog("RutubeMusic")
        if cog:
            await cog.seek_relative(interaction, 10)

    @discord.ui.button(emoji="⏱️", style=discord.ButtonStyle.secondary, custom_id="rt_seek_modal", row=2)
    async def seek_modal_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        cog = interaction.client.get_cog("RutubeMusic")
        await interaction.response.send_modal(UniversalSeekModal(cog=cog))

    @discord.ui.button(emoji="🔍", style=discord.ButtonStyle.primary, custom_id="rt_play_single_btn", row=2)
    async def play_single_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        await interaction.response.send_modal(RutubeSearchModal())

    @discord.ui.button(emoji="🎵", style=discord.ButtonStyle.secondary, custom_id="rt_source", row=2)
    async def source_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        cog = interaction.client.get_cog("RutubeMusic")
        if cog:
            state = cog.get_state(interaction.guild_id)
            state.np_msg = None
            vc = interaction.guild.voice_client
            if vc and vc.is_connected():
                try:
                    if vc.is_playing() or vc.is_paused():
                        vc.stop()
                except Exception:
                    pass

        from views.ui import MusicSelectionView, build_music_selection_embed
        embed = build_music_selection_embed()
        if interaction.response.is_done():
            await interaction.message.edit(embed=embed, view=MusicSelectionView())
        else:
            await interaction.response.edit_message(embed=embed, view=MusicSelectionView())


# ──────────────────────────────────────────────
# ПАНЕЛЬ НАСТРОЕК (ADMIN CONFIG VIEWS)
# ──────────────────────────────────────────────

class RutubeConfigView(discord.ui.View):
    def __init__(self, guild: discord.Guild, settings: dict):
        super().__init__(timeout=300)
        self.guild = guild
        self.guild_id = guild.id
        self.settings = settings
        
        # Динамически создаем выпадающий список только с голосовыми каналами
        voice_channels = [
            ch for ch in guild.channels
            if ch.type == discord.ChannelType.voice
        ]
        options = [
            discord.SelectOption(
                label=ch.name[:100],
                value=str(ch.id),
                emoji="🔊",
                default=(ch.id == settings.get("last_channel_id")),
            )
            for ch in voice_channels[:25]
        ]
        if not options:
            options = [discord.SelectOption(label="Нет голосовых каналов", value="0")]
            
        self.channel_select_dropdown = discord.ui.Select(
            placeholder="Выбрать канал для 24/7...",
            options=options,
            min_values=1,
            max_values=1,
            row=3,
            custom_id="rt_channel_select"
        )
        self.channel_select_dropdown.callback = self._channel_select_callback
        self.add_item(self.channel_select_dropdown)
        
        self.update_components()

    def update_components(self):
        keep_alive = self.settings.get("keep_alive", False)
        self.toggle_247.style = discord.ButtonStyle.success if keep_alive else discord.ButtonStyle.secondary
        self.toggle_247.label = f"Режим 24/7: {'ВКЛ' if keep_alive else 'ВЫКЛ'}"
        
        control_mode = self.settings.get("control_mode", "everyone")
        for option in self.control_mode_select.options:
            option.default = (option.value == control_mode)

    @discord.ui.button(label="Режим 24/7", style=discord.ButtonStyle.secondary, emoji="📻", row=0)
    async def toggle_247(self, interaction: discord.Interaction, button: discord.ui.Button):
        keep_alive = not self.settings.get("keep_alive", False)
        self.settings["keep_alive"] = keep_alive
        
        # Сначала записываем новое состояние в БД, чтобы слушатели событий видели актуальное значение
        await db.update_rutube_config(self.guild_id, keep_alive=keep_alive)
        
        if keep_alive:
            # Отключаем keep_alive у остальных плееров в БД
            await db.update_ym_settings(self.guild_id, keep_alive=False)
            await db.update_lofi_config(self.guild_id, keep_alive=False)
            await db.update_spotify_config(self.guild_id, keep_alive=False)
            
            # Отключаем от текущего канала
            vc = interaction.guild.voice_client
            if vc:
                try:
                    vc.stop()
                    await vc.disconnect(force=True)
                except Exception:
                    pass
                    
            # Подключаемся к каналу RuTube
            last_channel_id = self.settings.get("last_channel_id")
            if last_channel_id:
                channel = interaction.guild.get_channel(last_channel_id)
                if channel:
                    rutube_cog = interaction.client.get_cog("RutubeMusic")
                    if rutube_cog:
                        try:
                            new_vc = await channel.connect(self_deaf=True)
                            state = rutube_cog.get_state(self.guild_id)
                            state.text_channel = channel
                            state.is_sleeping = True
                        except Exception:
                            pass
        else:
            vc = interaction.guild.voice_client
            if vc and vc.channel:
                non_bot = [m for m in vc.channel.members if not m.bot]
                if len(non_bot) == 0:
                    try:
                        await vc.disconnect(force=True)
                    except Exception:
                        pass

        self.update_components()
        
        embed = await build_rutube_config_embed(self.guild_id, self.settings)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Плейлисты 📋", style=discord.ButtonStyle.secondary, row=0)
    async def playlists_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = RutubePlaylistsManagerView(self.guild, self.settings)
        embed = await view.build_embed()
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.select(
        placeholder="Кто может управлять плеером...",
        options=[
            discord.SelectOption(label="Все в канале", value="everyone", description="Управлять могут все слушатели"),
            discord.SelectOption(label="Владелец комнаты / Инициатор", value="owner_only", description="Только создатель комнаты или запустивший музыку"),
            discord.SelectOption(label="Только роли DJ", value="dj_only", description="Только пользователи с настроенными DJ-ролями"),
        ],
        min_values=1,
        max_values=1,
        row=1,
        custom_id="rt_control_mode_select"
    )
    async def control_mode_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        control_mode = select.values[0]
        self.settings["control_mode"] = control_mode
        
        await db.update_rutube_config(self.guild_id, control_mode=control_mode)
        self.update_components()
        
        embed = await build_rutube_config_embed(self.guild_id, self.settings)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="Выбрать роли DJ...",
        min_values=1,
        max_values=10,
        row=2,
        custom_id="rt_dj_roles_select"
    )
    async def dj_roles_select(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        roles = [role.id for role in select.values]
        self.settings["dj_role_ids"] = roles
        
        await db.update_rutube_config(self.guild_id, dj_role_ids=roles)
        self.update_components()
        
        embed = await build_rutube_config_embed(self.guild_id, self.settings)
        await interaction.response.edit_message(embed=embed, view=self)

    async def _channel_select_callback(self, interaction: discord.Interaction):
        if not self.channel_select_dropdown.values or self.channel_select_dropdown.values[0] == "0":
            return
        channel_id = int(self.channel_select_dropdown.values[0])
        channel = interaction.guild.get_channel(channel_id)
        if not channel:
            await interaction.response.send_message("❌ Не удалось найти выбранный канал.", ephemeral=True)
            return
            
        bot_member = interaction.guild.get_member(interaction.client.user.id)
        permissions = channel.permissions_for(bot_member)
        if not permissions.connect or not permissions.speak:
            await interaction.response.send_message(
                "❌ У бота нет прав на подключение (`Connect`) или воспроизведение (`Speak`) в этом канале!",
                ephemeral=True
            )
            return

        self.settings["last_channel_id"] = channel.id
        await db.update_rutube_last_channel(self.guild_id, channel.id)
        
        keep_alive = self.settings.get("keep_alive", False)
        if keep_alive:
            rutube_cog = interaction.client.get_cog("RutubeMusic")
            if rutube_cog:
                vc = interaction.guild.voice_client
                if vc:
                    try:
                        vc.stop()
                        await vc.disconnect(force=True)
                    except Exception:
                        pass
                
                try:
                    new_vc = await channel.connect(self_deaf=True)
                    state = rutube_cog.get_state(self.guild_id)
                    state.text_channel = channel
                    state.is_sleeping = True
                except Exception as e:
                    import logging
                    logging.getLogger("views.rutube").error("Ошибка автоподключения к каналу 24/7: %s", e)

        self.update_components()
        embed = await build_rutube_config_embed(self.guild_id, self.settings)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Сбросить DJ роли", style=discord.ButtonStyle.danger, emoji="🔄", row=4)
    async def reset_dj_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.settings["dj_role_ids"] = []
        await db.update_rutube_config(self.guild_id, dj_role_ids=[])
        self.update_components()
        
        embed = await build_rutube_config_embed(self.guild_id, self.settings)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Назад", style=discord.ButtonStyle.secondary, emoji="🔙", row=4)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        from views.ui import render_music_manager
        await render_music_manager(interaction)


async def build_rutube_config_embed(guild_id: int, settings: dict) -> discord.Embed:
    keep_alive = settings.get("keep_alive", False)
    control_mode = settings.get("control_mode", "everyone")
    dj_roles = settings.get("dj_role_ids", [])
    last_channel_id = settings.get("last_channel_id")
    default_playlist_id = settings.get("default_playlist_id")

    mode_mapping = {
        "everyone": "Все пользователи в канале",
        "owner_only": "Только владелец комнаты / инициатор",
        "dj_only": "Только пользователи с ролью DJ"
    }

    roles_str = ", ".join(f"<@&{r_id}>" for r_id in dj_roles) if dj_roles else "❌ Не настроены"
    channel_str = f"<#{last_channel_id}>" if last_channel_id else "❌ Не выбран"
    
    playlist_str = "❌ Не выбран"
    if default_playlist_id:
        playlists = await db.get_rutube_playlists(guild_id)
        pl = next((p for p in playlists if p["id"] == default_playlist_id), None)
        if pl:
            playlist_str = pl["name"]

    embed = discord.Embed(
        title="📺 Настройки RuTube Плеера",
        description="Здесь вы можете изменить глобальные параметры RuTube для сервера.",
        color=discord.Color.purple()
    )
    embed.add_field(name="📻 Режим 24/7", value="🟢 Включен" if keep_alive else "🔴 Выключен", inline=True)
    embed.add_field(name="🎛️ Кто может управлять", value=mode_mapping.get(control_mode, "Все"), inline=True)
    embed.add_field(name="🎧 Роли DJ", value=roles_str, inline=False)
    embed.add_field(name="🔊 Канал 24/7", value=channel_str, inline=False)
    embed.add_field(name="📋 Плейлист по умолчанию", value=playlist_str, inline=False)
    embed.set_footer(text="Изменения вступают в силу немедленно")
    return embed


class RutubeCreatePlaylistModal(discord.ui.Modal, title="➕ Создать плейлист"):
    name_input = discord.ui.TextInput(
        label="Название плейлиста",
        style=discord.TextStyle.short,
        placeholder="Например: Любимая музыка",
        required=True,
        max_length=100
    )
    links_input = discord.ui.TextInput(
        label="Ссылки на видео RuTube",
        style=discord.TextStyle.paragraph,
        placeholder="Каждая ссылка с новой строки...",
        required=True,
        max_length=2000
    )

    def __init__(self, settings: dict):
        super().__init__()
        self.settings = settings

    async def on_submit(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("RutubeMusic")
        if not cog:
            await interaction.response.send_message("❌ Модуль RuTube не загружен.", ephemeral=True)
            return

        # Парсим ID видео из ссылок
        video_ids = cog.extract_video_ids(self.links_input.value)
        if not video_ids:
            await interaction.response.send_message("❌ В тексте не найдено ссылок с ID видео RuTube.", ephemeral=True)
            return

        unique_ids = ",".join(list(dict.fromkeys(video_ids)))
        await db.add_rutube_playlist(interaction.guild_id, self.name_input.value, unique_ids)
        
        # Перегружаем вью
        view = RutubePlaylistsManagerView(interaction.guild, self.settings)
        embed = await view.build_embed()
        await interaction.response.send_message("✅ Плейлист сохранен!", ephemeral=True)
        await interaction.message.edit(embed=embed, view=view)


class RutubeEditPlaylistModal(discord.ui.Modal, title="✏️ Редактировать плейлист"):
    def __init__(self, settings: dict, playlist: dict):
        super().__init__()
        self.settings = settings
        self.playlist = playlist
        
        self.name_input = discord.ui.TextInput(
            label="Название плейлиста",
            style=discord.TextStyle.short,
            default=playlist["name"],
            required=True,
            max_length=100
        )
        
        video_ids = [v.strip() for v in playlist["video_ids"].split(",") if v.strip()]
        formatted_links = "\n".join(f"https://rutube.ru/video/{vid}/" for vid in video_ids)
        
        self.links_input = discord.ui.TextInput(
            label="Ссылки на видео RuTube",
            style=discord.TextStyle.paragraph,
            default=formatted_links[:3900],
            required=True,
            max_length=4000
        )
        self.add_item(self.name_input)
        self.add_item(self.links_input)

    async def on_submit(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("RutubeMusic")
        if not cog:
            await interaction.response.send_message("❌ Модуль RuTube не загружен.", ephemeral=True)
            return

        video_ids = cog.extract_video_ids(self.links_input.value)
        if not video_ids:
            await interaction.response.send_message("❌ В тексте не найдено ссылок с ID видео RuTube.", ephemeral=True)
            return

        unique_ids = ",".join(list(dict.fromkeys(video_ids)))
        await db.update_rutube_playlist(interaction.guild_id, self.playlist["id"], self.name_input.value, unique_ids)
        
        view = RutubePlaylistsManagerView(interaction.guild, self.settings)
        view.selected_playlist_id = self.playlist["id"]
        embed = await view.build_embed()
        await interaction.response.send_message("✅ Плейлист обновлен!", ephemeral=True)
        await interaction.message.edit(embed=embed, view=view)


class RutubePlaylistsManagerView(discord.ui.View):
    def __init__(self, guild: discord.Guild, settings: dict):
        super().__init__(timeout=300)
        self.guild = guild
        self.guild_id = guild.id
        self.settings = settings
        self.playlists = []
        self.selected_playlist_id: Optional[int] = None

    async def build_embed(self) -> discord.Embed:
        self.playlists = await db.get_rutube_playlists(self.guild_id)
        default_playlist_id = self.settings.get("default_playlist_id")
        
        valid_ids = [p["id"] for p in self.playlists]
        if self.playlists and (self.selected_playlist_id is None or self.selected_playlist_id not in valid_ids):
            if default_playlist_id in valid_ids:
                self.selected_playlist_id = default_playlist_id
            else:
                self.selected_playlist_id = self.playlists[0]["id"]
        elif not self.playlists:
            self.selected_playlist_id = None

        embed = discord.Embed(
            title="📋 Управление плейлистами RuTube",
            description="Здесь вы можете создавать, редактировать плейлисты сервера (до 5 штук) и выбирать плейлист по умолчанию.",
            color=discord.Color.purple()
        )
        
        if not self.playlists:
            embed.description += "\n\n*У вас пока нет сохраненных плейлистов.*"
        else:
            list_str = ""
            for p in self.playlists:
                is_selected = "👉 " if p["id"] == self.selected_playlist_id else "▫️ "
                is_default = "⭐ " if p["id"] == default_playlist_id else ""
                track_count = len([v for v in p['video_ids'].split(',') if v.strip()])
                list_str += f"{is_selected}{is_default}**{p['name']}** (ID: {p['id']}, треков: {track_count})\n"
            embed.add_field(name="Сохраненные плейлисты сервера:", value=list_str, inline=False)
            
        embed.set_footer(text=f"Всего плейлистов: {len(self.playlists)} / 5")
        
        self.update_components()
        return embed

    def update_components(self):
        self.clear_items()
        
        if self.playlists:
            options = [
                discord.SelectOption(
                    label=p["name"][:100],
                    value=str(p["id"]),
                    default=(p["id"] == self.selected_playlist_id)
                ) for p in self.playlists
            ]
            self.add_item(RutubePlaylistSelect(options))

            # Кнопка редактирования
            edit_btn = discord.ui.Button(
                label="✏️ Изменить",
                style=discord.ButtonStyle.primary,
                row=1,
                disabled=(self.selected_playlist_id is None)
            )
            async def edit_cb(interaction: discord.Interaction):
                playlist = next((p for p in self.playlists if p["id"] == self.selected_playlist_id), None)
                if playlist:
                    await interaction.response.send_modal(RutubeEditPlaylistModal(self.settings, playlist))
            edit_btn.callback = edit_cb
            self.add_item(edit_btn)

            # Кнопка установки дефолтного
            set_default_btn = discord.ui.Button(
                label="⭐ Основной",
                style=discord.ButtonStyle.secondary,
                row=1,
                disabled=(self.selected_playlist_id is None)
            )
            async def set_default_cb(interaction: discord.Interaction):
                if self.selected_playlist_id:
                    self.settings["default_playlist_id"] = self.selected_playlist_id
                    await db.update_rutube_config(self.guild_id, default_playlist_id=self.selected_playlist_id)
                    embed = await self.build_embed()
                    await interaction.response.edit_message(embed=embed, view=self)
            set_default_btn.callback = set_default_cb
            self.add_item(set_default_btn)

            # Кнопка удаления
            delete_btn = discord.ui.Button(
                label="🗑️ Удалить",
                style=discord.ButtonStyle.danger,
                row=1,
                disabled=(self.selected_playlist_id is None)
            )
            async def delete_cb(interaction: discord.Interaction):
                if self.selected_playlist_id:
                    target_id = self.selected_playlist_id
                    await db.delete_rutube_playlist(self.guild_id, target_id)
                    if self.settings.get("default_playlist_id") == target_id:
                        self.settings["default_playlist_id"] = None
                    self.selected_playlist_id = None
                    embed = await self.build_embed()
                    await interaction.response.edit_message(embed=embed, view=self)
            delete_btn.callback = delete_cb
            self.add_item(delete_btn)

        # Кнопка создания
        create_btn = discord.ui.Button(
            label="➕ Создать плейлист",
            style=discord.ButtonStyle.success,
            row=2,
            disabled=(len(self.playlists) >= 5)
        )
        async def create_cb(interaction: discord.Interaction):
            await interaction.response.send_modal(RutubeCreatePlaylistModal(self.settings))
        create_btn.callback = create_cb
        self.add_item(create_btn)

        # Кнопка Назад
        back_btn = discord.ui.Button(
            label="Назад",
            style=discord.ButtonStyle.secondary,
            row=2
        )
        async def back_cb(interaction: discord.Interaction):
            view = RutubeConfigView(self.guild, self.settings)
            embed = await build_rutube_config_embed(self.guild_id, self.settings)
            await interaction.response.edit_message(embed=embed, view=view)
        back_btn.callback = back_cb
        self.add_item(back_btn)


class RutubePlaylistSelect(discord.ui.Select):
    def __init__(self, options):
        super().__init__(
            placeholder="Выберите плейлист для управления...",
            options=options,
            min_values=1,
            max_values=1,
            row=0,
            custom_id="rt_playlist_select"
        )

    async def callback(self, interaction: discord.Interaction):
        view: RutubePlaylistsManagerView = self.view
        view.selected_playlist_id = int(self.values[0])
        embed = await view.build_embed()
        await interaction.response.edit_message(embed=embed, view=view)

