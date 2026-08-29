"""UI-компоненты для Яндекс.Музыки в DynamicVoiceBot.

Содержит persistent View с кнопками управления плеером,
модальное окно для регулировки громкости и панель авторизации по коду.
"""

from __future__ import annotations

import logging
import lolka as discord
from utils.voice_utils import safe_voice_connect, safe_defer
from typing import Optional
from views.base_player import BasePlayerView, QueueSelect, UniversalVolumeModal
import db

logger = logging.getLogger(__name__)


class YMSearchModal(discord.ui.Modal, title="🔍 Поиск / Вставить ссылку"):
    query_input = discord.ui.TextInput(
        label="Название трека / исполнитель / ссылка",
        placeholder="Кино - Группа крови ИЛИ ссылка на плейлист",
        required=True,
        min_length=1,
        max_length=100
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await safe_defer(interaction)
        cog = interaction.client.get_cog("YandexMusic")
        if not cog:
            await interaction.followup.send("❌ Модуль Яндекс.Музыки не загружен.", ephemeral=True)
            return
        await cog.play_by_search(interaction, self.query_input.value)


class YMAddToQueueModal(discord.ui.Modal, title="➕ Добавить в очередь"):
    """Модальное окно для поиска и добавления трека в конец очереди."""

    query_input = discord.ui.TextInput(
        label="Название трека / ссылка",
        placeholder="например: Кино - Группа крови или ссылка на плейлист",
        required=True,
        min_length=1,
        max_length=200,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await safe_defer(interaction)
        cog = interaction.client.get_cog("YandexMusic")
        if not cog:
            await interaction.followup.send(
                "❌ Модуль Яндекс.Музыки не загружен.", ephemeral=True
            )
            return

        query = self.query_input.value

        # Проверяем, может это ссылка
        from utils.ym_url_parser import parse_ym_url

        parsed = parse_ym_url(query)
        if parsed:
            await cog.play_from_url(interaction, parsed)
        else:
            await cog.add_to_queue(interaction, query)


class YMPlaylistsView(discord.ui.View):
    def __init__(self, options: list[discord.SelectOption]):
        super().__init__(timeout=180)

        select = discord.ui.Select(
            placeholder="Выберите подборку для воспроизведения...",
            options=options,
            min_values=1,
            max_values=1,
            row=0
        )
        select.callback = self.select_callback
        self.add_item(select)

        back_btn = discord.ui.Button(
            label="Назад",
            style=discord.ButtonStyle.secondary,
            emoji="🔙",
            row=1
        )
        back_btn.callback = self.back_callback
        self.add_item(back_btn)

    async def select_callback(self, interaction: discord.Interaction):
        if not self.children or not hasattr(self.children[0], "values") or not self.children[0].values:
            await interaction.response.send_message("❌ Не удалось получить выбранное значение.", ephemeral=True)
            return

        await safe_defer(interaction)

        val = self.children[0].values[0]
        uid, kind = val.split(":", 1)

        title = "Подборка"
        for opt in self.children[0].options:
            if opt.value == val:
                title = opt.label
                break

        # Удаляем сообщение с селектом из чата
        try:
            await interaction.message.delete()
        except Exception:
            pass

        cog = interaction.client.get_cog("YandexMusic")
        if cog:
            if uid == "user" and kind == "onyourwave":
                await cog.start_wave(interaction)
            else:
                await cog.play_playlist(interaction, uid, kind, title)

        # Убираем деферед "Bot is thinking..." — карточка плеера уже в канале
        try:
            await interaction.delete_original_response()
        except Exception:
            pass

    async def back_callback(self, interaction: discord.Interaction):
        try:
            await interaction.message.delete()
        except Exception:
            pass
        cog = interaction.client.get_cog("YandexMusic")
        if cog:
            await cog.send_player_panel(interaction)


class YMAuthView(discord.ui.View):
    """View с кнопкой авторизации Яндекс.Музыки для конкретного сервера."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Войти в Яндекс",
        style=discord.ButtonStyle.primary,
        emoji="🔑",
        custom_id="ym_auth_btn",
    )
    async def auth_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        cog = interaction.client.get_cog("YandexMusic")
        if not cog:
            await interaction.response.send_message(
                "❌ Модуль Яндекс.Музыки не загружен.",
                ephemeral=True,
            )
            return

        await cog.start_auth_flow(interaction)


class YMPlayerView(BasePlayerView):
    """Persistent View с кнопками управления Яндекс.Музыка-плеером."""

    def __init__(self, queue: Optional[list] = None, current_index: int = 0) -> None:
        super().__init__(timeout=None)
        self.add_item(QueueSelect(queue=queue, current_index=current_index, row=0, player_prefix="ym"))

    @discord.ui.button(emoji="⏮️", style=discord.ButtonStyle.secondary, custom_id="ym_prev", row=1)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        cog = interaction.client.get_cog("YandexMusic")
        if cog:
            await cog.play_prev(interaction)

    @discord.ui.button(emoji="⏯️", style=discord.ButtonStyle.secondary, custom_id="ym_pause", row=1)
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        cog = interaction.client.get_cog("YandexMusic")
        if cog:
            await cog.toggle_pause(interaction)

    @discord.ui.button(emoji="⏭️", style=discord.ButtonStyle.secondary, custom_id="ym_skip", row=1)
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        cog = interaction.client.get_cog("YandexMusic")
        if cog:
            await cog.skip_track(interaction)

    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger, custom_id="ym_stop", row=1)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        cog = interaction.client.get_cog("YandexMusic")
        if cog:
            await cog.stop_playback(interaction)

    @discord.ui.button(emoji="🔊", style=discord.ButtonStyle.secondary, custom_id="ym_volume", row=1)
    async def volume_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        cog = interaction.client.get_cog("YandexMusic")
        await interaction.response.send_modal(UniversalVolumeModal(cog=cog))

    @discord.ui.button(emoji="🔍", style=discord.ButtonStyle.primary, custom_id="ym_search_btn", row=2)
    async def search_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(YMSearchModal())

    @discord.ui.button(emoji="🌊", style=discord.ButtonStyle.success, custom_id="ym_wave_btn", row=2)
    async def wave_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        cog = interaction.client.get_cog("YandexMusic")
        if cog:
            await cog.start_wave(interaction)

    @discord.ui.button(emoji="❤️", style=discord.ButtonStyle.secondary, custom_id="ym_like", row=2)
    async def like_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        cog = interaction.client.get_cog("YandexMusic")
        if cog:
            await cog.like_track(interaction)

    @discord.ui.button(emoji="💔", style=discord.ButtonStyle.secondary, custom_id="ym_dislike", row=2)
    async def dislike_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        cog = interaction.client.get_cog("YandexMusic")
        if cog:
            await cog.dislike_track(interaction)

    @discord.ui.button(emoji="🎵", style=discord.ButtonStyle.secondary, custom_id="ym_source", row=2)
    async def source_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        cog = interaction.client.get_cog("YandexMusic")
        if cog:
            state = cog.get_state(interaction.guild_id)
            state["np_msg"] = None
            vc = cog._voice_clients.get(interaction.guild_id) or interaction.guild.voice_client
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

    @discord.ui.button(emoji="🔀", style=discord.ButtonStyle.secondary, custom_id="ym_shuffle", row=3)
    async def shuffle_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        cog = interaction.client.get_cog("YandexMusic")
        if cog:
            await cog.toggle_shuffle(interaction)

    @discord.ui.button(emoji="🔁", style=discord.ButtonStyle.secondary, custom_id="ym_loop", row=3)
    async def loop_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        cog = interaction.client.get_cog("YandexMusic")
        if cog:
            await cog.toggle_loop(interaction)

    @discord.ui.button(emoji="👥", style=discord.ButtonStyle.secondary, custom_id="ym_blend_btn", row=3)
    async def blend_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        cog = interaction.client.get_cog("YandexMusic")
        if cog:
            await cog.join_blend_from_player(interaction)


class YMQueueClearView(discord.ui.View):
    """View для очистки очереди воспроизведения."""
    def __init__(self) -> None:
        super().__init__(timeout=300)

    @discord.ui.button(
        label="🗑️ Сбросить очередь",
        style=discord.ButtonStyle.danger,
        custom_id="ym_queue_clear",
    )
    async def clear_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        cog = interaction.client.get_cog("YandexMusic")
        if cog:
            state = cog.get_state(interaction.guild_id)
            if state.tracks:
                state.clear_upcoming()
                await interaction.response.edit_message(content="✅ Очередь сброшена (остался только текущий трек).", view=None)
                # Обновляем UI
                await cog.send_now_playing(interaction.guild_id)
            else:
                await interaction.response.edit_message(content="📋 Очередь уже пуста.", view=None)


class YMReadyView(discord.ui.View):
    """View для состояния 'Готов к проигрыванию' (авторизован, но не играет)."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="🎵 Моя Волна",
        style=discord.ButtonStyle.success,
        custom_id="ym_ready_wave_btn",
        row=0,
    )
    async def wave_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        cog = interaction.client.get_cog("YandexMusic")
        if cog:
            await cog.start_wave(interaction)

    @discord.ui.button(
        label="🔍 Поиск",
        style=discord.ButtonStyle.primary,
        custom_id="ym_ready_search_btn",
        row=0,
    )
    async def search_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await interaction.response.send_modal(YMSearchModal())

    @discord.ui.button(
        label="🌀 Подборки",
        style=discord.ButtonStyle.secondary,
        custom_id="ym_ready_playlists_btn",
        row=1,
    )
    async def playlists_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        cog = interaction.client.get_cog("YandexMusic")
        if cog:
            await cog.show_playlists_menu(interaction)

    @discord.ui.button(
        label="🚪 Выйти",
        style=discord.ButtonStyle.danger,
        custom_id="ym_ready_logout_btn",
        row=1,
    )
    async def logout_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        cog = interaction.client.get_cog("YandexMusic")
        if cog:
            await cog.logout_yandex(interaction)


class YMLogoutConfirmModal(discord.ui.Modal, title="🚪 Подтверждение отключения"):
    def __init__(self, parent_view: YMConfigView):
        super().__init__()
        self.parent_view = parent_view

    confirm_field = discord.ui.TextInput(
        label="Отключить выход при выходе бота из канала?",
        style=discord.TextStyle.short,
        placeholder="Напишите ДА для подтверждения",
        required=True,
        max_length=10,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        val = self.confirm_field.value.strip().lower()
        if val in ("да", "yes", "д", "y"):
            self.parent_view.settings["logout_on_disconnect"] = False
            await db.update_ym_settings(self.parent_view.guild_id, logout_on_disconnect=False)
            self.parent_view.update_components()
            
            cog = interaction.client.get_cog("YandexMusic")
            embed = cog._build_config_embed(self.parent_view.guild_id, self.parent_view.settings)
            await interaction.response.edit_message(embed=embed, view=self.parent_view)
        else:
            await interaction.response.send_message(
                "❌ Отменено. Опция «Выход при дисконнекте» осталась включенной.",
                ephemeral=True
            )


class YMConfigView(discord.ui.View):
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
            row=4,
            custom_id="ym_channel_select"
        )
        self.channel_select_dropdown.callback = self._channel_select_callback
        self.add_item(self.channel_select_dropdown)
        
        self.update_components()

    def update_components(self):
        keep_alive = self.settings.get("keep_alive", False)
        self.toggle_247.style = discord.ButtonStyle.success if keep_alive else discord.ButtonStyle.secondary
        self.toggle_247.label = f"Режим 24/7: {'ВКЛ' if keep_alive else 'ВЫКЛ'}"

        logout_on_disconnect = self.settings.get("logout_on_disconnect", False)
        self.toggle_logout_on_disconnect.style = discord.ButtonStyle.success if logout_on_disconnect else discord.ButtonStyle.secondary
        self.toggle_logout_on_disconnect.label = f"Выход при дисконнекте: {'ВКЛ' if logout_on_disconnect else 'ВЫКЛ'}"
        
        control_mode = self.settings.get("control_mode", "everyone")
        for option in self.control_mode_select.options:
            option.default = (option.value == control_mode)

        like_mode = self.settings.get("like_mode", "owner_only")
        for option in self.like_mode_select.options:
            option.default = (option.value == like_mode)

    @discord.ui.button(label="Режим 24/7", style=discord.ButtonStyle.secondary, emoji="📻", row=0)
    async def toggle_247(self, interaction: discord.Interaction, button: discord.ui.Button):
        keep_alive = not self.settings.get("keep_alive", False)
        self.settings["keep_alive"] = keep_alive
        
        # Сначала записываем новое состояние в БД, чтобы слушатели событий видели актуальное значение
        await db.update_ym_settings(self.guild_id, keep_alive=keep_alive)
        
        if keep_alive:
            # При включении 24/7 для ЯМ, выключаем 24/7 для остальных плееров в БД
            await db.update_lofi_config(self.guild_id, keep_alive=False)
            await db.update_rutube_config(self.guild_id, keep_alive=False)
            await db.update_spotify_config(self.guild_id, keep_alive=False)
            
            # Отключаем текущего бота, если он есть
            vc = interaction.guild.voice_client
            if vc:
                try:
                    vc.stop()
                    await vc.disconnect(force=True)
                except Exception:
                    pass
                    
            # Если есть сохраненный канал для ЯМ, сразу заходим туда
            last_channel_id = self.settings.get("last_channel_id")
            if last_channel_id:
                channel = interaction.guild.get_channel(last_channel_id)
                if channel:
                    ym_cog = interaction.client.get_cog("YandexMusic")
                    if ym_cog:
                        try:
                            new_vc = await channel.connect(self_deaf=True)
                            ym_cog._voice_clients[self.guild_id] = new_vc
                        except Exception:
                            pass
        else:
            vc = interaction.guild.voice_client
            if vc and vc.channel:
                non_bot = [m for m in vc.channel.members if not m.bot]
                if len(non_bot) == 0:
                    try:
                        ym_cog = interaction.client.get_cog("YandexMusic")
                        if ym_cog and ym_cog._voice_clients.get(self.guild_id):
                            await ym_cog._stop_and_cleanup(self.guild_id, None, disconnect=True)
                            
                        lofi_cog = interaction.client.get_cog("LofiRadio")
                        if lofi_cog and lofi_cog._voice_clients.get(self.guild_id):
                            if vc.is_playing():
                                vc.stop()
                            async for msg in vc.channel.history(limit=5):
                                if msg.author == interaction.client.user:
                                    await msg.delete()
                            lofi_cog._voice_clients.pop(self.guild_id, None)
                            await vc.disconnect(force=True)
                            
                        if not ym_cog and not lofi_cog:
                            await vc.disconnect(force=True)
                    except Exception:
                        pass

        self.update_components()
        
        cog = interaction.client.get_cog("YandexMusic")
        embed = cog._build_config_embed(self.guild_id, self.settings)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Выход при дисконнекте", style=discord.ButtonStyle.secondary, emoji="🚪", row=0)
    async def toggle_logout_on_disconnect(self, interaction: discord.Interaction, button: discord.ui.Button):
        current_val = self.settings.get("logout_on_disconnect", True)
        
        if current_val:
            # Пользователь пытается ВЫКЛЮЧИТЬ (сделать False) -> показываем модалку
            await interaction.response.send_modal(YMLogoutConfirmModal(self))
        else:
            # Пользователь пытается ВКЛЮЧИТЬ (сделать True) -> включаем без подтверждения
            self.settings["logout_on_disconnect"] = True
            await db.update_ym_settings(self.guild_id, logout_on_disconnect=True)
            self.update_components()
            
            cog = interaction.client.get_cog("YandexMusic")
            embed = cog._build_config_embed(self.guild_id, self.settings)
            await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Сбросить DJ роли", style=discord.ButtonStyle.danger, emoji="🔄", row=0)
    async def reset_dj_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.settings["dj_role_ids"] = []
        await db.update_ym_settings(self.guild_id, dj_role_ids=[])
        self.update_components()
        
        cog = interaction.client.get_cog("YandexMusic")
        embed = cog._build_config_embed(self.guild_id, self.settings)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Назад", style=discord.ButtonStyle.secondary, emoji="🔙", row=0)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        from views.ui import render_music_manager
        await render_music_manager(interaction)

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
        custom_id="ym_control_mode_select"
    )
    async def control_mode_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        control_mode = select.values[0]
        self.settings["control_mode"] = control_mode
        
        await db.update_ym_settings(self.guild_id, control_mode=control_mode)
        self.update_components()
        
        cog = interaction.client.get_cog("YandexMusic")
        embed = cog._build_config_embed(self.guild_id, self.settings)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.select(
        placeholder="Кто может лайкать треки...",
        options=[
            discord.SelectOption(label="Владелец комнаты / Инициатор", value="owner_only", description="Только создатель комнаты или запустивший музыку"),
            discord.SelectOption(label="Все в канале", value="everyone", description="Все слушатели в канале"),
            discord.SelectOption(label="Никто (отключить лайки)", value="off", description="Отключить лайки/дизлайки на сервере"),
        ],
        min_values=1,
        max_values=1,
        row=2,
        custom_id="ym_like_mode_select"
    )
    async def like_mode_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        like_mode = select.values[0]
        self.settings["like_mode"] = like_mode

        await db.update_ym_settings(self.guild_id, like_mode=like_mode)
        self.update_components()

        cog = interaction.client.get_cog("YandexMusic")
        embed = cog._build_config_embed(self.guild_id, self.settings)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="Выбрать роли DJ...",
        min_values=1,
        max_values=10,
        row=3,
        custom_id="ym_dj_roles_select"
    )
    async def dj_roles_select(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        roles = [role.id for role in select.values]
        self.settings["dj_role_ids"] = roles
        
        await db.update_ym_settings(self.guild_id, dj_role_ids=roles)
        self.update_components()
        
        cog = interaction.client.get_cog("YandexMusic")
        embed = cog._build_config_embed(self.guild_id, self.settings)
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
        await db.update_ym_last_channel(self.guild_id, channel.id)
        
        for opt in self.channel_select_dropdown.options:
            opt.default = (opt.value == str(channel_id))
        
        keep_alive = self.settings.get("keep_alive", False)
        if keep_alive:
            ym_cog = interaction.client.get_cog("YandexMusic")
            if ym_cog:
                vc = interaction.guild.voice_client
                if vc:
                    try:
                        vc.stop()
                        await vc.disconnect(force=True)
                    except Exception:
                        pass
                
                try:
                    new_vc = await safe_voice_connect(interaction.guild, channel, self_deaf=True)
                    if new_vc:
                        ym_cog._voice_clients[self.guild_id] = new_vc
                    else:
                        logger.warning("Не удалось автоподключить ЯМ к каналу 24/7 %s", channel.id)
                except Exception as e:
                    logger.error("Ошибка автоподключения Яндекс.Музыки к каналу 24/7: %s", e)

        self.update_components()
        cog = interaction.client.get_cog("YandexMusic")
        embed = cog._build_config_embed(self.guild_id, self.settings)
        await interaction.response.edit_message(embed=embed, view=self)
