"""UI-компоненты для Lofi Radio.

Содержит persistent View с кнопками управления плеером
и модальное окно для ввода громкости.
Все кнопки имеют custom_id, чтобы работать после перезагрузки бота.
"""

from __future__ import annotations

import logging

import lolka as discord
from views.base_player import BasePlayerView, UniversalVolumeModal
from views.ui import is_bot_busy_in_other_channel
import db
from lofi_streams import STATIONS, LofiStation, get_station_by_name

logger = logging.getLogger(__name__)


class LofiStationSelect(discord.ui.Select):
    def __init__(self, stations: list[LofiStation] | None = None) -> None:
        source = stations or STATIONS
        options = [
            discord.SelectOption(
                label=s.name,
                description=s.genre,
                emoji=s.emoji,
                value=s.name
            ) for s in source
        ][:25]
        if not options:
            options = [discord.SelectOption(label="Нет доступных станций", value="__none__")]
        super().__init__(
            placeholder="Выберите станцию...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="lofi_station_select",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("LofiRadio")
        if not cog:
            await interaction.response.send_message(
                "❌ Модуль Lofi Radio не загружен.",
                ephemeral=True,
            )
            return
            
        if not cog.check_cooldown(interaction.guild_id):
            await interaction.response.send_message("⏳ Пожалуйста, подождите пару секунд перед следующим действием.", ephemeral=True)
            return
        
        station_name = self.values[0]
        if station_name == "__none__":
            await interaction.response.send_message("❌ Нет доступных станций.", ephemeral=True)
            return
            
        # Сначала ищем в предустановленных
        station = get_station_by_name(station_name)
        if not station:
            # Затем в кастомных
            active = await cog.get_active_stations(interaction.guild_id)
            station = next((s for s in active if s.name == station_name), None)
        
        if not station:
            await interaction.response.send_message(
                "❌ Станция не найдена.",
                ephemeral=True,
            )
            return

        await cog.start_radio(interaction, station=station)


class LofiPlayerView(BasePlayerView):
    """Persistent View с кнопками управления Lofi Radio."""

    def __init__(self, stations: list[LofiStation] | None = None) -> None:
        super().__init__(timeout=None)
        self.add_item(LofiStationSelect(stations))

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.success, custom_id="lofi_play", row=1)
    async def play_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        cog = interaction.client.get_cog("LofiRadio")
        if not cog:
            await interaction.response.send_message("❌ Модуль Lofi Radio не загружен.", ephemeral=True)
            return
        if not cog.check_cooldown(interaction.guild_id):
            await interaction.response.send_message("⏳ Пожалуйста, подождите пару секунд перед следующим действием.", ephemeral=True)
            return
        await cog.start_radio(interaction)

    @discord.ui.button(emoji="⏹️", style=discord.ButtonStyle.danger, custom_id="lofi_stop", row=1)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        cog = interaction.client.get_cog("LofiRadio")
        if not cog:
            await interaction.response.send_message("❌ Модуль Lofi Radio не загружен.", ephemeral=True)
            return
        if not cog.check_cooldown(interaction.guild_id):
            await interaction.response.send_message("⏳ Пожалуйста, подождите пару секунд перед следующим действием.", ephemeral=True)
            return
        await cog.stop_radio(interaction)

    @discord.ui.button(emoji="🔊", style=discord.ButtonStyle.secondary, custom_id="lofi_volume", row=1)
    async def volume_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        cog = interaction.client.get_cog("LofiRadio")
        await interaction.response.send_modal(UniversalVolumeModal(cog=cog))

    @discord.ui.button(emoji="🎵", style=discord.ButtonStyle.secondary, custom_id="lofi_source", row=1)
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



class LofiConfigView(discord.ui.View):
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
            custom_id="lofi_channel_select"
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
        await db.update_lofi_config(self.guild_id, keep_alive=keep_alive)
        
        if keep_alive:
            # Отключаем keep_alive у остальных плееров в БД
            await db.update_ym_settings(self.guild_id, keep_alive=False)
            await db.update_rutube_config(self.guild_id, keep_alive=False)
            await db.update_spotify_config(self.guild_id, keep_alive=False)
            
            # Отключаем от текущего канала
            vc = interaction.guild.voice_client
            if vc:
                try:
                    vc.stop()
                    await vc.disconnect(force=True)
                except Exception:
                    pass
                    
            # Подключаемся к каналу Lofi
            last_channel_id = self.settings.get("last_channel_id")
            if last_channel_id:
                channel = interaction.guild.get_channel(last_channel_id)
                if channel:
                    lofi_cog = interaction.client.get_cog("LofiRadio")
                    if lofi_cog:
                        try:
                            new_vc = await channel.connect(self_deaf=True)
                            lofi_cog._voice_clients[self.guild_id] = new_vc
                        except Exception:
                            pass
        else:
            vc = interaction.guild.voice_client
            if vc and vc.channel:
                non_bot = [m for m in vc.channel.members if not m.bot]
                if len(non_bot) == 0:
                    try:
                        lofi_cog = interaction.client.get_cog("LofiRadio")
                        if lofi_cog and lofi_cog._voice_clients.get(self.guild_id):
                            if vc.is_playing():
                                vc.stop()
                            async for msg in vc.channel.history(limit=5):
                                if msg.author == interaction.client.user:
                                    await msg.delete()
                            lofi_cog._voice_clients.pop(self.guild_id, None)
                            
                        ym_cog = interaction.client.get_cog("YandexMusic")
                        if ym_cog and ym_cog._voice_clients.get(self.guild_id):
                            await ym_cog._stop_and_cleanup(self.guild_id, None, disconnect=True)
                        else:
                            await vc.disconnect(force=True)
                    except Exception:
                        pass

        self.update_components()
        
        cog = interaction.client.get_cog("LofiRadio")
        embed = cog._build_config_embed(self.guild_id, self.settings)
        await interaction.response.edit_message(embed=embed, view=self)

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
        custom_id="lofi_control_mode_select"
    )
    async def control_mode_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        control_mode = select.values[0]
        self.settings["control_mode"] = control_mode
        
        await db.update_lofi_config(self.guild_id, control_mode=control_mode)
        self.update_components()
        
        cog = interaction.client.get_cog("LofiRadio")
        embed = cog._build_config_embed(self.guild_id, self.settings)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="Выбрать роли DJ...",
        min_values=1,
        max_values=10,
        row=2,
        custom_id="lofi_dj_roles_select"
    )
    async def dj_roles_select(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        roles = [role.id for role in select.values]
        self.settings["dj_role_ids"] = roles
        
        await db.update_lofi_config(self.guild_id, dj_role_ids=roles)
        self.update_components()
        
        cog = interaction.client.get_cog("LofiRadio")
        embed = cog._build_config_embed(self.guild_id, self.settings)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Сбросить DJ роли", style=discord.ButtonStyle.danger, emoji="🔄", row=4)
    async def reset_dj_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.settings["dj_role_ids"] = []
        await db.update_lofi_config(self.guild_id, dj_role_ids=[])
        self.update_components()
        
        cog = interaction.client.get_cog("LofiRadio")
        embed = cog._build_config_embed(self.guild_id, self.settings)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Настройка станций", style=discord.ButtonStyle.primary, emoji="📻", row=4)
    async def manage_stations_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = LofiStationsManagerView(self.guild)
        embed = await view.build_embed(interaction)
        await interaction.response.edit_message(embed=embed, view=view)

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
        await db.update_lofi_last_channel(self.guild_id, channel.id)
        
        for opt in self.channel_select_dropdown.options:
            opt.default = (opt.value == str(channel_id))
        
        keep_alive = self.settings.get("keep_alive", False)
        if keep_alive:
            lofi_cog = interaction.client.get_cog("LofiRadio")
            if lofi_cog:
                vc = interaction.guild.voice_client
                if vc:
                    try:
                        vc.stop()
                        await vc.disconnect(force=True)
                    except Exception:
                        pass
                
                try:
                    new_vc = await channel.connect(self_deaf=True)
                    lofi_cog._voice_clients[self.guild_id] = new_vc
                    lofi_cog._current_station.pop(self.guild_id, None)
                except Exception as e:
                    logger.error("Ошибка автоподключения к каналу 24/7: %s", e)

        self.update_components()
        cog = interaction.client.get_cog("LofiRadio")
        embed = cog._build_config_embed(self.guild_id, self.settings)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Назад", style=discord.ButtonStyle.secondary, emoji="🔙", row=4)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        from views.ui import render_music_manager
        await render_music_manager(interaction)


# ──────────────────────────────────────────────
# Менеджер станций
# ──────────────────────────────────────────────

class LofiStationsManagerView(discord.ui.View):
    """Вью управления списком станций Lofi Radio для сервера."""
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=300)
        self.guild = guild
        self.guild_id = guild.id

    async def build_embed(self, interaction: discord.Interaction) -> discord.Embed:
        """Собирает embed со списком активных станций."""
        cog = interaction.client.get_cog("LofiRadio")
        active = await cog.get_active_stations(self.guild_id) if cog else []
        hidden = await db.get_lofi_hidden_stations(self.guild_id)
        custom = await db.get_lofi_custom_stations(self.guild_id)
        custom_names = {r["name"] for r in custom}
        
        embed = discord.Embed(
            title="📻 Управление станциями Lofi Radio",
            description=f"Активных станций: **{len(active)}** | Скрыто: **{len(hidden)}**",
            color=discord.Color.from_rgb(138, 43, 226)
        )
        
        if active:
            lines = []
            for s in active:
                tag = "⭐" if s.name in custom_names else "📌"
                lines.append(f"{tag} {s.emoji} **{s.name}** — {s.genre}")
            embed.add_field(
                name="🎧 Активные станции",
                value="\n".join(lines[:20]),
                inline=False
            )
        else:
            embed.add_field(name="🎧 Станции", value="*Нет активных станций*", inline=False)
        
        embed.add_field(
            name="💡 Обозначения",
            value="📌 Встроенная станция | ⭐ Пользовательская станция",
            inline=False
        )
        embed.set_footer(text="Добавляйте свои потоки или скрывайте ненужные")
        return embed

    @discord.ui.button(label="Добавить станцию", style=discord.ButtonStyle.success, emoji="➕", row=0)
    async def add_station_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(LofiAddStationModal(self.guild_id))

    @discord.ui.button(label="Удалить станцию", style=discord.ButtonStyle.danger, emoji="❌", row=0)
    async def delete_station_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("LofiRadio")
        if not cog:
            await interaction.response.send_message("❌ Модуль Lofi Radio не загружен.", ephemeral=True)
            return
        
        active = await cog.get_active_stations(self.guild_id)
        if not active:
            await interaction.response.send_message("❌ Нет станций для удаления.", ephemeral=True)
            return
        
        custom = await db.get_lofi_custom_stations(self.guild_id)
        custom_names = {r["name"] for r in custom}
        
        view = LofiDeleteStationView(self.guild_id, active, custom_names)
        embed = discord.Embed(
            title="❌ Удаление станции",
            description="Выберите станцию для удаления.\nВстроенные станции будут скрыты, пользовательские — удалены.",
            color=discord.Color.red()
        )
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Сбросить к стандартным", style=discord.ButtonStyle.secondary, emoji="🔄", row=0)
    async def reset_stations_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await db.unhide_all_lofi_stations(self.guild_id)
        await db.delete_all_lofi_custom_stations(self.guild_id)
        
        embed = await self.build_embed(interaction)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Назад", style=discord.ButtonStyle.secondary, emoji="🔙", row=1)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("LofiRadio")
        if not cog:
            return
        settings = await db.get_lofi_config(self.guild_id)
        embed = cog._build_config_embed(self.guild_id, settings)
        view = LofiConfigView(interaction.guild, settings)
        await interaction.response.edit_message(embed=embed, view=view)


class LofiAddStationModal(discord.ui.Modal, title="➕ Добавление станции"):
    """Модальное окно для добавления кастомной радиостанции."""

    def __init__(self, guild_id: int):
        super().__init__()
        self.guild_id = guild_id

    station_name = discord.ui.TextInput(
        label="Название станции",
        style=discord.TextStyle.short,
        placeholder="Rock Radio, Jazz FM, ...",
        required=True,
        min_length=1,
        max_length=100,
    )

    station_url = discord.ui.TextInput(
        label="URL потока (http/https)",
        style=discord.TextStyle.short,
        placeholder="http://stream.example.com/radio.mp3",
        required=True,
        min_length=10,
        max_length=500,
    )

    station_emoji = discord.ui.TextInput(
        label="Эмодзи (необязательно)",
        style=discord.TextStyle.short,
        placeholder="🎵",
        required=False,
        max_length=10,
        default="🎵",
    )

    station_genre = discord.ui.TextInput(
        label="Жанр (необязательно)",
        style=discord.TextStyle.short,
        placeholder="Rock, Jazz, Lo-Fi, ...",
        required=False,
        max_length=100,
        default="Custom",
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        name = self.station_name.value.strip()
        url = self.station_url.value.strip()
        emoji = self.station_emoji.value.strip() or "🎵"
        genre = self.station_genre.value.strip() or "Custom"

        # Валидация URL
        if not url.startswith(("http://", "https://")):
            await interaction.response.send_message(
                "❌ URL должен начинаться с `http://` или `https://`.",
                ephemeral=True,
            )
            return

        # Сначала отправляем эфемерное сообщение о начале проверки (пользователь увидит анимацию)
        await interaction.response.send_message("⏳ Проверяем доступность потока, подождите...", ephemeral=True)
        
        from cogs.lofi import validate_stream_url
        ok, reason = await validate_stream_url(url)
        if not ok:
            await interaction.edit_original_response(
                content=f"❌ Поток недоступен: {reason}\nПроверьте URL и попробуйте снова."
            )
            return

        # Сохраняем в БД
        await db.add_lofi_custom_station(self.guild_id, name, url, emoji, genre)
        
        # Обновляем UI (главное сообщение, из которого вызвано модальное окно)
        manager_view = LofiStationsManagerView(interaction.guild)
        embed = await manager_view.build_embed(interaction)
        if interaction.message:
            await interaction.message.edit(embed=embed, view=manager_view)
            
        # Обновляем эфемерное сообщение на успешное завершение
        await interaction.edit_original_response(content=f"✅ Станция **{name}** успешно добавлена!")


class LofiDeleteStationView(discord.ui.View):
    """Вью с выпадающим списком и кнопкой подтверждения для удаления станций."""
    def __init__(self, guild_id: int, stations: list, custom_names: set[str]):
        super().__init__(timeout=120)
        self.guild_id = guild_id
        self.custom_names = custom_names
        self.selected_stations = []
        self.add_item(LofiDeleteStationSelect(guild_id, stations, custom_names))

    @discord.ui.button(label="Подтвердить удаление", style=discord.ButtonStyle.danger, emoji="🗑️", row=1, disabled=True)
    async def confirm_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.selected_stations:
            await interaction.response.send_message("❌ Вы не выбрали ни одной станции для удаления.", ephemeral=True)
            return

        deleted_count = 0
        hidden_count = 0
        
        for station_name in self.selected_stations:
            if station_name in self.custom_names:
                await db.delete_lofi_custom_station(self.guild_id, station_name)
                deleted_count += 1
            else:
                await db.hide_lofi_predefined_station(self.guild_id, station_name)
                hidden_count += 1
                
        parts = []
        if deleted_count > 0:
            parts.append(f"удалено кастомных: **{deleted_count}**")
        if hidden_count > 0:
            parts.append(f"скрыто встроенных: **{hidden_count}**")
            
        action_msg = " и ".join(parts)
        
        manager = LofiStationsManagerView(interaction.guild)
        embed = await manager.build_embed(interaction)
        embed.set_field_at(0, name=embed.fields[0].name, value=f"✅ Успешно {action_msg}.\n\n" + (embed.fields[0].value or ""), inline=False)
        await interaction.response.edit_message(embed=embed, view=manager)

    @discord.ui.button(label="Назад", style=discord.ButtonStyle.secondary, emoji="🔙", row=2)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        manager = LofiStationsManagerView(interaction.guild)
        embed = await manager.build_embed(interaction)
        await interaction.response.edit_message(embed=embed, view=manager)


class LofiDeleteStationSelect(discord.ui.Select):
    """Выпадающий список для выбора удаляемых/скрываемых станций."""
    def __init__(self, guild_id: int, stations: list, custom_names: set[str]):
        self.guild_id = guild_id
        self.custom_names = custom_names
        
        options = []
        for s in stations[:25]:
            tag = "⭐ Пользовательская" if s.name in custom_names else "📌 Встроенная"
            options.append(discord.SelectOption(
                label=s.name,
                description=f"{tag} — {s.genre}",
                emoji=s.emoji,
                value=s.name,
            ))
        
        super().__init__(
            placeholder="Выберите станции для удаления/скрытия...",
            min_values=1,
            max_values=len(options) if options else 1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.view.selected_stations = self.values
        
        # Находим и обновляем состояние кнопки подтверждения
        for child in self.view.children:
            if isinstance(child, discord.ui.Button) and child.label == "Подтвердить удаление":
                child.disabled = len(self.values) == 0
                break
                
        # Обновляем описание в эмбеде, показывая выбранные станции
        embed = interaction.message.embeds[0]
        selected_text = ", ".join([f"**{name}**" for name in self.values])
        embed.description = f"Выбрано для удаления/скрытия:\n{selected_text}\n\nНажмите кнопку «Подтвердить удаление» для применения изменений."
        
        await interaction.response.edit_message(embed=embed, view=self.view)
