import lolka as discord
from typing import Optional
from views.ui import is_bot_busy_in_other_channel
from views.base_player import BasePlayerView, UniversalSeekModal, QueueSelect, UniversalVolumeModal
import db



class SpotifyLinkModal(discord.ui.Modal, title="🔍 Поиск и добавление музыки"):
    def __init__(self, clear_queue: bool = False) -> None:
        super().__init__()
        self.clear_queue = clear_queue

    url_input = discord.ui.TextInput(
        label="Ссылка (Spotify, YT, VK, SoundCloud) / поиск",
        style=discord.TextStyle.paragraph,
        placeholder="https://open.spotify.com/...\nили https://youtube.com/playlist?list=...\nили https://vk.com/music/playlist/...\nили Группа крови",
        required=True,
        max_length=1000
    )

    async def on_submit(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("SpotifyMusic")
        if not cog:
            await interaction.response.send_message("❌ Модуль Spotify не загружен.", ephemeral=True)
            return
            
        await interaction.response.defer(ephemeral=True, thinking=True)
        await cog.play_spotify_link(interaction, self.url_input.value, clear_queue=self.clear_queue)


class SpotifyReadyView(discord.ui.View):
    """View для режима ожидания Spotify/Dynamic-плеера (без активного воспроизведения)."""
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Поиск / Ссылка", emoji="🔍", style=discord.ButtonStyle.primary, custom_id="sp_ready_search", row=0)
    async def search_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        await interaction.response.send_modal(SpotifyLinkModal())

    @discord.ui.button(label="Выбрать плейлист", emoji="📁", style=discord.ButtonStyle.secondary, custom_id="sp_ready_select_pl", row=0)
    async def select_playlist_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        playlists = await db.get_spotify_playlists(interaction.guild_id)
        if not playlists:
            await interaction.response.send_message("❌ На этом сервере еще нет сохраненных плейлистов Spotify.", ephemeral=True)
            return
        from views.spotify_views import SpotifySelectPlaylistView
        await interaction.response.send_message("📁 Выберите плейлист для воспроизведения:", view=SpotifySelectPlaylistView(playlists), ephemeral=True)

    @discord.ui.button(label="Выбор плеера", emoji="🎵", style=discord.ButtonStyle.secondary, custom_id="sp_ready_source", row=0)
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


class SpotifyPlayerView(BasePlayerView):
    """Persistent View с кнопками управления Spotify/Dynamic-плеером."""
    def __init__(self, queue: Optional[list] = None, current_index: int = 0) -> None:
        super().__init__(timeout=None)
        self.add_item(QueueSelect(queue=queue, current_index=current_index, row=0, player_prefix="sp"))

    @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.secondary, custom_id="sp_prev", row=1)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        cog = interaction.client.get_cog("SpotifyMusic")
        if cog:
            await cog.previous_track(interaction)

    @discord.ui.button(emoji="⏯️", style=discord.ButtonStyle.secondary, custom_id="sp_pause", row=1)
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        cog = interaction.client.get_cog("SpotifyMusic")
        if cog:
            await cog.toggle_pause(interaction)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary, custom_id="sp_skip", row=1)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        cog = interaction.client.get_cog("SpotifyMusic")
        if cog:
            await cog.skip_track(interaction)

    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger, custom_id="sp_stop", row=1)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        cog = interaction.client.get_cog("SpotifyMusic")
        if cog:
            await cog.stop_playback(interaction)

    @discord.ui.button(emoji="🔊", style=discord.ButtonStyle.secondary, custom_id="sp_volume", row=1)
    async def volume_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        cog = interaction.client.get_cog("SpotifyMusic")
        await interaction.response.send_modal(UniversalVolumeModal(cog=cog))

    @discord.ui.button(emoji="↺", style=discord.ButtonStyle.secondary, custom_id="sp_rewind", row=2)
    async def rewind_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        cog = interaction.client.get_cog("SpotifyMusic")
        if cog:
            await cog.seek_relative(interaction, -10)

    @discord.ui.button(emoji="↻", style=discord.ButtonStyle.secondary, custom_id="sp_forward", row=2)
    async def forward_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        cog = interaction.client.get_cog("SpotifyMusic")
        if cog:
            await cog.seek_relative(interaction, 10)

    @discord.ui.button(emoji="⏱️", style=discord.ButtonStyle.secondary, custom_id="sp_seek_modal", row=2)
    async def seek_modal_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        cog = interaction.client.get_cog("SpotifyMusic")
        await interaction.response.send_modal(UniversalSeekModal(cog=cog))

    @discord.ui.button(emoji="🔍", style=discord.ButtonStyle.primary, custom_id="sp_search_btn", row=2)
    async def search_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        await interaction.response.send_modal(SpotifyLinkModal())

    @discord.ui.button(emoji="🎵", style=discord.ButtonStyle.secondary, custom_id="sp_exit_sources", row=2)
    async def exit_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        cog = interaction.client.get_cog("SpotifyMusic")
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

class SpotifyConfigView(discord.ui.View):
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
            placeholder="Выбрать канал для вещания 24/7...",
            options=options,
            min_values=1,
            max_values=1,
            row=2, # Над кнопками DJ и Назад
            custom_id="sp_channel_select"
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
        await db.update_spotify_config(self.guild_id, keep_alive=keep_alive)
        
        if keep_alive:
            # Отключаем keep_alive у остальных когов
            await db.update_ym_settings(self.guild_id, keep_alive=False)
            await db.update_lofi_config(self.guild_id, keep_alive=False)
            await db.update_rutube_config(self.guild_id, keep_alive=False)
            
            # Отключаем бота, чтобы обновить подключение к каналу Spotify 24/7
            vc = interaction.guild.voice_client
            if vc:
                try:
                    vc.stop()
                    await vc.disconnect(force=True)
                except Exception:
                    pass
                    
            # Подключаемся к каналу Spotify
            last_channel_id = self.settings.get("last_channel_id")
            if last_channel_id:
                channel = interaction.guild.get_channel(last_channel_id)
                if channel:
                    spotify_cog = interaction.client.get_cog("SpotifyMusic")
                    if spotify_cog:
                        try:
                            new_vc = await channel.connect(self_deaf=True)
                            state = spotify_cog.get_state(self.guild_id)
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
        
        embed = await build_spotify_config_embed(self.guild_id, self.settings)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Плейлисты 📋", style=discord.ButtonStyle.secondary, row=0)
    async def playlists_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = SpotifyPlaylistsManagerView(self.guild, self.settings)
        embed = await view.build_embed()
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.select(
        placeholder="Кто может управлять плеером...",
        options=[
            discord.SelectOption(label="Все пользователи", value="everyone", description="Любой участник в канале может управлять."),
            discord.SelectOption(label="Только владелец комнаты", value="owner_only", description="Управляет создатель привата и инициатор."),
            discord.SelectOption(label="DJ-роли", value="dj_only", description="Только владельцы и пользователи с DJ-ролью."),
        ],
        custom_id="sp_control_mode_select",
        row=1
    )
    async def control_mode_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        mode = select.values[0]
        self.settings["control_mode"] = mode
        await db.update_spotify_config(self.guild_id, control_mode=mode)
        self.update_components()
        embed = await build_spotify_config_embed(self.guild_id, self.settings)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="DJ Роли", style=discord.ButtonStyle.primary, emoji="🎧", row=3)
    async def dj_roles_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        from views.ui import RoleSelectView
        
        dj_roles = self.settings.get("dj_role_ids", [])
        
        async def on_roles_selected(selected_role_ids: list[int]):
            self.settings["dj_role_ids"] = selected_role_ids
            await db.update_spotify_config(self.guild_id, dj_role_ids=selected_role_ids)
            
            embed = await build_spotify_config_embed(self.guild_id, self.settings)
            await interaction.message.edit(embed=embed, view=self)
            
        view = RoleSelectView(interaction.guild, dj_roles, on_roles_selected, back_view=self)
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="🎧 Настройка DJ-ролей для Spotify",
                description="Выберите роли участников, которые смогут управлять плеером Spotify в режиме 'DJ-роли':",
                color=discord.Color.green()
            ),
            view=view
        )

    @discord.ui.button(label="Назад", style=discord.ButtonStyle.secondary, emoji="🔙", row=3)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        from views.ui import render_music_manager
        await render_music_manager(interaction)

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

        self.settings["last_channel_id"] = channel_id
        await db.update_spotify_last_channel(self.guild_id, channel_id)
        
        for opt in self.channel_select_dropdown.options:
            opt.default = (opt.value == str(channel_id))
            
        self.update_components()
        
        # Если 24/7 включен, перезапускаем подключение
        if self.settings.get("keep_alive", False):
            vc = interaction.guild.voice_client
            if vc:
                try:
                    vc.stop()
                    await vc.disconnect(force=True)
                except Exception:
                    pass
            
            try:
                new_vc = await channel.connect(self_deaf=True)
                spotify_cog = interaction.client.get_cog("SpotifyMusic")
                if spotify_cog:
                    state = spotify_cog.get_state(self.guild_id)
                    state.text_channel = channel
                    state.is_sleeping = True
            except Exception as e:
                import logging
                logging.getLogger("views.spotify").error("Ошибка автоподключения к каналу 24/7: %s", e)
                        
        embed = await build_spotify_config_embed(self.guild_id, self.settings)
        await interaction.response.edit_message(embed=embed, view=self)


async def build_spotify_config_embed(guild_id: int, settings: dict) -> discord.Embed:
    keep_alive = settings.get("keep_alive", False)
    control_mode = settings.get("control_mode", "everyone")
    dj_roles = settings.get("dj_role_ids", [])
    last_channel_id = settings.get("last_channel_id")
    default_playlist_id = settings.get("default_playlist_id")
    
    control_str = {
        "everyone": "🟢 Все участники",
        "owner_only": "🔒 Только владелец комнаты",
        "dj_only": "🎧 DJ-роли + владелец"
    }.get(control_mode, "everyone")
    
    dj_roles_str = ", ".join(f"<@&{r}>" for r in dj_roles) if dj_roles else "Не настроены"
    channel_str = f"<#{last_channel_id}>" if last_channel_id else "Не настроен"
    
    playlist_str = "Не выбран"
    if default_playlist_id:
        playlists = await db.get_spotify_playlists(guild_id)
        pl = next((p for p in playlists if p["id"] == default_playlist_id), None)
        if pl:
            playlist_str = pl["name"]
            
    embed = discord.Embed(
        title="🔴 Настройки Dynamic плеера",
        description=(
            "Здесь вы можете настроить параметры Dynamic плеера для вашего сервера.\n\n"
            "**Настройка 24/7 режима:**\n"
            "Выберите голосовой канал в выпадающем списке ниже и включите режим 24/7."
        ),
        color=discord.Color.red()
    )
    
    embed.add_field(name="📻 Режим 24/7", value="🟢 Включен" if keep_alive else "🔴 Выключен", inline=True)
    embed.add_field(name="🛡️ Управление", value=control_str, inline=True)
    embed.add_field(name="📋 Плейлист по умолчанию", value=playlist_str, inline=True)
    embed.add_field(name="🔊 Канал 24/7", value=channel_str, inline=False)
    embed.add_field(name="🎧 DJ-роли", value=dj_roles_str, inline=False)
    
    return embed


class SpotifySelectPlaylistSelect(discord.ui.Select):
    def __init__(self, playlists: list[dict]) -> None:
        options = [
            discord.SelectOption(
                label=p["name"],
                description=f"Треков: {len(p['track_ids'].split(chr(10)))}",
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
            custom_id="sp_playlist_play_select"
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        playlist_id = self.values[0]
        if playlist_id == "__none__":
            await interaction.response.send_message("❌ Нет доступных плейлистов.", ephemeral=True)
            return
            
        cog = interaction.client.get_cog("SpotifyMusic")
        if not cog:
            await interaction.response.send_message("❌ Модуль Spotify не загружен.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        await cog.play_server_playlist(interaction, int(playlist_id))


class SpotifySelectPlaylistView(discord.ui.View):
    def __init__(self, playlists: list[dict]) -> None:
        super().__init__(timeout=180)
        self.add_item(SpotifySelectPlaylistSelect(playlists))


class SpotifyCreatePlaylistModal(discord.ui.Modal, title="➕ Создать плейлист Dynamic"):
    name_input = discord.ui.TextInput(
        label="Название плейлиста",
        style=discord.TextStyle.short,
        placeholder="Например: Моя медиатека",
        required=True,
        max_length=100
    )
    links_input = discord.ui.TextInput(
        label="Ссылки (Spotify, YT, VK, SoundCloud) / поиск",
        style=discord.TextStyle.paragraph,
        placeholder="Каждая ссылка (Spotify, YouTube плейлист/трек, VK, SoundCloud) или название трека с новой строки...",
        required=True,
        max_length=2000
    )

    def __init__(self, settings: dict):
        super().__init__()
        self.settings = settings

    async def on_submit(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("SpotifyMusic")
        if not cog:
            await interaction.response.send_message("❌ Модуль Spotify не загружен.", ephemeral=True)
            return

        raw_lines = [line.strip() for line in self.links_input.value.split("\n") if line.strip()]
        lines = []
        for rl in raw_lines:
            tokens = rl.split()
            current_query = []
            for tok in tokens:
                if tok.startswith("http://") or tok.startswith("https://"):
                    if current_query:
                        lines.append(" ".join(current_query))
                        current_query = []
                    lines.append(tok)
                else:
                    current_query.append(tok)
            if current_query:
                lines.append(" ".join(current_query))

        if not lines:
            await interaction.response.send_message("❌ Список треков пуст.", ephemeral=True)
            return

        track_ids = "\n".join(lines)
        await db.add_spotify_playlist(interaction.guild_id, self.name_input.value, track_ids)
        
        view = SpotifyPlaylistsManagerView(interaction.guild, self.settings)
        embed = await view.build_embed()
        await interaction.response.send_message("✅ Плейлист Dynamic сохранен!", ephemeral=True)
        await interaction.message.edit(embed=embed, view=view)


class SpotifyEditPlaylistModal(discord.ui.Modal, title="✏️ Редактировать плейлист Dynamic"):
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
        self.links_input = discord.ui.TextInput(
            label="Ссылки (Spotify, YT, VK, SoundCloud) / поиск",
            style=discord.TextStyle.paragraph,
            default=playlist["track_ids"][:3900],
            required=True,
            max_length=4000
        )
        self.add_item(self.name_input)
        self.add_item(self.links_input)

    async def on_submit(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("SpotifyMusic")
        if not cog:
            await interaction.response.send_message("❌ Модуль Spotify не загружен.", ephemeral=True)
            return

        raw_lines = [line.strip() for line in self.links_input.value.split("\n") if line.strip()]
        lines = []
        for rl in raw_lines:
            tokens = rl.split()
            current_query = []
            for tok in tokens:
                if tok.startswith("http://") or tok.startswith("https://"):
                    if current_query:
                        lines.append(" ".join(current_query))
                        current_query = []
                    lines.append(tok)
                else:
                    current_query.append(tok)
            if current_query:
                lines.append(" ".join(current_query))

        if not lines:
            await interaction.response.send_message("❌ Список треков пуст.", ephemeral=True)
            return

        track_ids = "\n".join(lines)
        await db.update_spotify_playlist(interaction.guild_id, self.playlist["id"], self.name_input.value, track_ids)
        
        view = SpotifyPlaylistsManagerView(interaction.guild, self.settings)
        view.selected_playlist_id = self.playlist["id"]
        embed = await view.build_embed()
        await interaction.response.send_message("✅ Плейлист Dynamic обновлен!", ephemeral=True)
        await interaction.message.edit(embed=embed, view=view)


class SpotifyPlaylistsManagerView(discord.ui.View):
    def __init__(self, guild: discord.Guild, settings: dict):
        super().__init__(timeout=300)
        self.guild = guild
        self.guild_id = guild.id
        self.settings = settings
        self.playlists = []
        self.selected_playlist_id: Optional[int] = None

    async def build_embed(self) -> discord.Embed:
        self.playlists = await db.get_spotify_playlists(self.guild_id)
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
            title="📋 Управление плейлистами Dynamic",
            description="Здесь вы можете создавать, редактировать плейлисты сервера (до 5 штук) и выбирать плейлист по умолчанию для 24/7.",
            color=discord.Color.red()
        )
        
        if not self.playlists:
            embed.description += "\n\n*У вас пока нет сохраненных плейлистов.*"
        else:
            list_str = ""
            for p in self.playlists:
                is_selected = "👉 " if p["id"] == self.selected_playlist_id else "▫️ "
                is_default = "⭐ " if p["id"] == default_playlist_id else ""
                track_count = len([t for t in p['track_ids'].split('\n') if t.strip()])
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
            self.add_item(SpotifyPlaylistSelect(options))

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
                    await interaction.response.send_modal(SpotifyEditPlaylistModal(self.settings, playlist))
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
                    await db.update_spotify_config(self.guild_id, default_playlist_id=self.selected_playlist_id)
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
                    await db.delete_spotify_playlist(self.guild_id, target_id)
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
            await interaction.response.send_modal(SpotifyCreatePlaylistModal(self.settings))
        create_btn.callback = create_cb
        self.add_item(create_btn)

        # Кнопка Назад
        back_btn = discord.ui.Button(
            label="Назад",
            style=discord.ButtonStyle.secondary,
            row=2
        )
        async def back_cb(interaction: discord.Interaction):
            view = SpotifyConfigView(self.guild, self.settings)
            embed = await build_spotify_config_embed(self.guild_id, self.settings)
            await interaction.response.edit_message(embed=embed, view=view)
        back_btn.callback = back_cb
        self.add_item(back_btn)


class SpotifyPlaylistSelect(discord.ui.Select):
    def __init__(self, options):
        super().__init__(
            placeholder="Выберите плейлист для управления...",
            options=options,
            min_values=1,
            max_values=1,
            row=0,
            custom_id="sp_playlist_select"
        )

    async def callback(self, interaction: discord.Interaction):
        view: SpotifyPlaylistsManagerView = self.view
        view.selected_playlist_id = int(self.values[0])
        embed = await view.build_embed()
        await interaction.response.edit_message(embed=embed, view=view)

