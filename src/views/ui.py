import logging
from typing import Optional, Dict, Any, List
import lolka as discord
import db
import config

logger = logging.getLogger(__name__)

# Палитра пресетных цветов для embed
COLOR_PRESETS: dict[str, int] = {
    "🟡 Золотой": discord.Color.gold().value,
    "🟢 Зелёный": discord.Color.brand_green().value,
    "🔵 Синий": discord.Color.blue().value,
    "🔴 Красный": discord.Color.brand_red().value,
    "🟣 Фиолетовый": discord.Color.purple().value,
    "⚪ Белый": discord.Color.light_grey().value,
    "🟠 Оранжевый": discord.Color.orange().value,
    "🌸 Розовый": discord.Color.fuchsia().value,
}

# Дефолтные значения (дублируем из voice.py для отображения в UI)
DEFAULT_CHANNEL_NAME = "📞 │ {user}"
DEFAULT_EMBED_TITLE = "Управление комнатой"
DEFAULT_EMBED_DESC = "Привет, {user_mention}! Это твоя личная комната.\nИспользуй кнопки ниже для её настройки."


def _color_name_by_value(value: int | None) -> str:
    """Получить название цвета по его int-значению."""
    if value is None:
        return "🟡 Золотой (по умолчанию)"
    for name, v in COLOR_PRESETS.items():
        if v == value:
            return name
    return f"Кастомный ({value})"


# ==========================================
# ADMIN DASHBOARD
# ==========================================
async def render_voice_manager(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    cog = interaction.client.get_cog("DynamicVoice")
    
    embed = discord.Embed(
        title="🎙️ Панель управления Dynamic Voice",
        description="Добро пожаловать в настройки динамических голосовых комнат!\nЗдесь вы можете привязать триггерные каналы к категориям.",
        color=discord.Color.brand_green()
    )
    
    if cog and guild_id in cog.configs and cog.configs[guild_id]:
        desc = ""
        for m_id, cfg in cog.configs[guild_id].items():
            c_id = cfg['category_id']
            has_custom = any([
                cfg.get('channel_name_template'),
                cfg.get('embed_title'),
                cfg.get('embed_description'),
                cfg.get('embed_color') is not None,
                cfg.get('mention_user') is not None,
            ])
            custom_badge = " 🎨" if has_custom else ""
            desc += f"🔹 **Мастер:** <#{m_id}>\n ➔ **Категория:** <#{c_id}>{custom_badge}\n\n"
        embed.add_field(name="📋 Текущие мастер-комнаты:", value=desc, inline=False)
    else:
        embed.add_field(name="📋 Текущие мастер-комнаты:", value="*Пока нет настроенных комнат.*", inline=False)
        
    embed.add_field(
        name="💡 Подсказка", 
        value="Мастер-комната — это обычный голосовой канал. Когда пользователь заходит в него, бот создаёт для него личный голосовой канал в указанной категории и перекидывает туда пользователя.\n🎨 — мастер-канал с кастомным оформлением.",
        inline=False
    )
    embed.set_footer(text="Управляйте своими комнатами с помощью кнопок ниже.")

    view = VoiceManagerMainView()
    if interaction.response.is_done():
        await interaction.edit_original_response(embed=embed, view=view)
    elif interaction.type == discord.InteractionType.component:
        await interaction.response.edit_message(embed=embed, view=view)
    else:
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class VoiceManagerMainView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Добавить мастер", style=discord.ButtonStyle.primary, emoji="➕", row=0)
    async def add_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = VoiceSettingsView(guild=interaction.guild)
        embed = discord.Embed(
            title="➕ Добавление мастер-комнаты",
            description="Выберите голосовой канал (мастер-канал) и категорию, в которой будут создаваться временные каналы.",
            color=discord.Color.blue()
        )
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Удалить мастер", style=discord.ButtonStyle.danger, emoji="🗑️", row=0)
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = interaction.guild_id
        cog = interaction.client.get_cog("DynamicVoice")
        if not cog or not guild_id or not cog.configs.get(guild_id):
            await interaction.response.send_message("Нет настроенных комнат для удаления.", ephemeral=True)
            return

        view = VoiceDeleteView(cog.configs[guild_id])
        embed = discord.Embed(
            title="🗑️ Удаление мастер-комнаты",
            description="Выберите мастер-комнату из списка, чтобы удалить её конфигурацию.",
            color=discord.Color.red()
        )
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Настроить оформление", style=discord.ButtonStyle.primary, emoji="🎨", row=0)
    async def customize_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = interaction.guild_id
        cog = interaction.client.get_cog("DynamicVoice")
        if not cog or not guild_id or not cog.configs.get(guild_id):
            await interaction.response.send_message("Сначала добавьте хотя бы одну мастер-комнату.", ephemeral=True)
            return

        view = VoiceCustomizeSelectView(cog.configs[guild_id])
        embed = discord.Embed(
            title="🎨 Настройка оформления",
            description="Выберите мастер-канал, оформление которого хотите настроить.",
            color=discord.Color.purple()
        )
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Настройки Музыки", style=discord.ButtonStyle.primary, emoji="📻", row=0)
    async def music_settings_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await render_music_manager(interaction)

    @discord.ui.button(label="Закрыть", style=discord.ButtonStyle.secondary, emoji="✖️", row=1)
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.message.delete()
        except Exception:
            pass


class VoiceDeleteView(discord.ui.View):
    def __init__(self, guild_configs: dict):
        super().__init__(timeout=300)
        options = []
        for master_id, cfg in guild_configs.items():
            cat_id = cfg['category_id']
            options.append(discord.SelectOption(
                label=f"Мастер-канал ID: {master_id}", 
                value=str(master_id), 
                description=f"Категория: {cat_id}"
            ))
            
        select = discord.ui.Select(
            placeholder="Выберите мастер-комнату для удаления",
            options=options, min_values=1, max_values=1, row=0
        )
        select.callback = self.select_callback
        self.add_item(select)
        
        back_btn = discord.ui.Button(label="Отмена", style=discord.ButtonStyle.secondary, emoji="🔙", row=1)
        back_btn.callback = self.back_callback
        self.add_item(back_btn)

    async def select_callback(self, interaction: discord.Interaction):
        master_id = int(self.children[0].values[0])
        guild_id = interaction.guild_id
        try:
            await db.delete_voice_config(master_id)
            cog = interaction.client.get_cog("DynamicVoice")
            if cog and guild_id in cog.configs and master_id in cog.configs[guild_id]:
                del cog.configs[guild_id][master_id]
            await render_voice_manager(interaction)
        except Exception as e:
            logger.error(f"Error deleting config: {e}")
            await interaction.response.send_message("Ошибка удаления.", ephemeral=True)

    async def back_callback(self, interaction: discord.Interaction):
        await render_voice_manager(interaction)


class VoiceSettingsView(discord.ui.View):
    def __init__(
        self,
        guild: discord.Guild,
        master_channel_id: int | None = None,
        category_id: int | None = None,
    ):
        super().__init__(timeout=300)
        self.master_channel_id = master_channel_id
        self.category_id = category_id

        # --- Мастер-канал (только голосовые) ---
        voice_channels = [
            ch for ch in guild.channels
            if ch.type == discord.ChannelType.voice
        ]
        master_options = [
            discord.SelectOption(
                label=ch.name[:100],
                value=str(ch.id),
                description=f"Категория: {ch.category.name}" if ch.category else "Без категории",
                emoji="🔊",
                default=(ch.id == master_channel_id),
            )
            for ch in voice_channels[:25]
        ]
        if not master_options:
            master_options = [discord.SelectOption(label="Нет голосовых каналов", value="0")]

        master_select = discord.ui.Select(
            placeholder="🔊 Выберите Мастер-канал (голосовой)",
            options=master_options,
            min_values=1,
            max_values=1,
            row=0,
        )
        master_select.callback = self._master_callback
        self.add_item(master_select)

        # --- Категория ---
        categories = [
            ch for ch in guild.channels
            if ch.type == discord.ChannelType.category
        ]
        cat_options = [
            discord.SelectOption(
                label=ch.name[:100],
                value=str(ch.id),
                emoji="📁",
                default=(ch.id == category_id),
            )
            for ch in categories[:25]
        ]
        if not cat_options:
            cat_options = [discord.SelectOption(label="Нет категорий", value="0")]

        cat_select = discord.ui.Select(
            placeholder="📁 Выберите Категорию для новых комнат",
            options=cat_options,
            min_values=1,
            max_values=1,
            row=1,
        )
        cat_select.callback = self._category_callback
        self.add_item(cat_select)

        self.update_save_button()

    def update_save_button(self):
        self.save_button.disabled = not (self.master_channel_id and self.category_id)

    async def _master_callback(self, interaction: discord.Interaction) -> None:
        selected_id = int(interaction.data["values"][0])
        if selected_id == 0:
            await interaction.response.send_message(
                "❌ На сервере нет голосовых каналов.", ephemeral=True
            )
            return
        self.master_channel_id = selected_id
        self.update_save_button()
        await interaction.response.edit_message(view=self)

    async def _category_callback(self, interaction: discord.Interaction) -> None:
        selected_id = int(interaction.data["values"][0])
        if selected_id == 0:
            await interaction.response.send_message(
                "❌ На сервере нет категорий.", ephemeral=True
            )
            return
        self.category_id = selected_id
        self.update_save_button()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Сохранить", style=discord.ButtonStyle.success, emoji="✅", row=2)
    async def save_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = interaction.guild_id
        try:
            await db.add_voice_config(guild_id, self.master_channel_id, self.category_id)
            cog = interaction.client.get_cog("DynamicVoice")
            if cog:
                if self.master_channel_id in cog.configs[guild_id]:
                    cog.configs[guild_id][self.master_channel_id]['category_id'] = self.category_id
                else:
                    cog.configs[guild_id][self.master_channel_id] = {
                        'category_id': self.category_id,
                        'channel_name_template': None,
                        'embed_title': None,
                        'embed_description': None,
                        'embed_color': None,
                        'mention_user': None,
                    }
            await render_voice_manager(interaction)
        except Exception as e:
            logger.error(f"Error saving voice settings: {e}")
            await interaction.response.send_message("Произошла ошибка при сохранении настроек.", ephemeral=True)

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary, emoji="🔙", row=2)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await render_voice_manager(interaction)


# ==========================================
# CUSTOMIZATION VIEWS
# ==========================================

class VoiceCustomizeSelectView(discord.ui.View):
    """Выбор мастер-канала для кастомизации."""

    def __init__(self, guild_configs: dict):
        super().__init__(timeout=300)
        self._guild_configs = guild_configs
        options = []
        for master_id, cfg in guild_configs.items():
            cat_id = cfg['category_id']
            has_custom = any([
                cfg.get('channel_name_template'),
                cfg.get('embed_title'),
                cfg.get('embed_description'),
                cfg.get('embed_color') is not None,
                cfg.get('mention_user') is not None,
            ])
            label = f"Мастер-канал ID: {master_id}"
            desc = f"Категория: {cat_id}"
            if has_custom:
                desc += " | 🎨 Кастомный"
            options.append(discord.SelectOption(label=label, value=str(master_id), description=desc))

        select = discord.ui.Select(
            placeholder="Выберите мастер-канал для настройки",
            options=options, min_values=1, max_values=1, row=0
        )
        select.callback = self.select_callback
        self.add_item(select)

        back_btn = discord.ui.Button(label="Назад", style=discord.ButtonStyle.secondary, emoji="🔙", row=1)
        back_btn.callback = self.back_callback
        self.add_item(back_btn)

    async def select_callback(self, interaction: discord.Interaction):
        master_id = int(self.children[0].values[0])
        cfg = self._guild_configs.get(master_id, {})
        view = VoiceCustomizeView(master_id, cfg)
        embed = _build_customize_embed(master_id, cfg)
        await interaction.response.edit_message(embed=embed, view=view)

    async def back_callback(self, interaction: discord.Interaction):
        await render_voice_manager(interaction)


def _build_customize_embed(master_id: int, cfg: dict) -> discord.Embed:
    """Строит embed с текущими настройками кастомизации."""
    embed = discord.Embed(
        title=f"🎨 Настройка оформления",
        description=f"Мастер-канал: <#{master_id}>",
        color=discord.Color.purple()
    )

    name_tmpl = cfg.get('channel_name_template') or DEFAULT_CHANNEL_NAME
    title_tmpl = cfg.get('embed_title') or DEFAULT_EMBED_TITLE
    desc_tmpl = cfg.get('embed_description') or DEFAULT_EMBED_DESC
    color_value = cfg.get('embed_color')
    mention = cfg.get('mention_user')
    if mention is None:
        mention = True
    welcome = cfg.get('send_welcome')
    if welcome is None:
        welcome = True

    embed.add_field(
        name="📝 Имя канала",
        value=f"`{name_tmpl}`",
        inline=False
    )
    embed.add_field(
        name="📌 Заголовок embed",
        value=f"`{title_tmpl}`",
        inline=True
    )
    embed.add_field(
        name="📋 Описание embed",
        value=f"```{desc_tmpl}```",
        inline=False
    )
    embed.add_field(
        name="🎨 Цвет embed",
        value=_color_name_by_value(color_value),
        inline=True
    )
    embed.add_field(
        name="@ Упоминание",
        value="✅ Включено" if mention else "❌ Выключено",
        inline=True
    )
    embed.add_field(
        name="💬 Приветствие",
        value="✅ Включено" if welcome else "❌ Выключено",
        inline=True
    )

    embed.add_field(
        name="💡 Плейсхолдеры",
        value="`{user}` — имя пользователя\n`{user_mention}` — упоминание @user\n`{server}` — название сервера",
        inline=False
    )

    is_default = not any([
        cfg.get('channel_name_template'),
        cfg.get('embed_title'),
        cfg.get('embed_description'),
        cfg.get('embed_color') is not None,
        cfg.get('mention_user') is not None,
        cfg.get('send_welcome') is not None,
    ])
    if is_default:
        embed.set_footer(text="Сейчас используются настройки по умолчанию.")
    else:
        embed.set_footer(text="Используются кастомные настройки.")

    return embed


class VoiceCustomizeView(discord.ui.View):
    """Основной экран кастомизации с кнопками действий."""

    def __init__(self, master_id: int, cfg: dict):
        super().__init__(timeout=300)
        self.master_id = master_id
        self.cfg = cfg

        # Динамический label кнопки упоминания
        mention = cfg.get('mention_user')
        if mention is None:
            mention = True
        self.mention_toggle.label = "Упоминание: Вкл" if mention else "Упоминание: Выкл"
        self.mention_toggle.style = discord.ButtonStyle.success if mention else discord.ButtonStyle.secondary

        # Динамический label кнопки приветствия
        welcome = cfg.get('send_welcome')
        if welcome is None:
            welcome = True
        self.welcome_toggle.label = "Приветствие: Вкл" if welcome else "Приветствие: Выкл"
        self.welcome_toggle.style = discord.ButtonStyle.success if welcome else discord.ButtonStyle.secondary

        # Select для выбора цвета
        color_options = []
        current_color = cfg.get('embed_color')
        for name, value in COLOR_PRESETS.items():
            is_default = (current_color is None and value == discord.Color.gold().value) or (current_color == value)
            color_options.append(discord.SelectOption(
                label=name,
                value=str(value),
                default=is_default
            ))
        self.color_select.options = color_options

    @discord.ui.select(placeholder="🎨 Выберите цвет embed", min_values=1, max_values=1, row=0)
    async def color_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        new_color = int(select.values[0])
        self.cfg['embed_color'] = new_color
        await self._save_and_refresh(interaction)

    @discord.ui.button(label="✏️ Редактировать тексты", style=discord.ButtonStyle.primary, row=1)
    async def edit_texts_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = VoiceCustomizeModal(self.master_id, self.cfg)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Упоминание: Вкл", style=discord.ButtonStyle.success, emoji="@", row=1)
    async def mention_toggle(self, interaction: discord.Interaction, button: discord.ui.Button):
        current = self.cfg.get('mention_user')
        if current is None:
            current = True
        new_value = not current
        self.cfg['mention_user'] = new_value
        await self._save_and_refresh(interaction)

    @discord.ui.button(label="Приветствие: Вкл", style=discord.ButtonStyle.success, emoji="💬", row=1)
    async def welcome_toggle(self, interaction: discord.Interaction, button: discord.ui.Button):
        current = self.cfg.get('send_welcome')
        if current is None:
            current = True
        new_value = not current
        self.cfg['send_welcome'] = new_value
        await self._save_and_refresh(interaction)

    @discord.ui.button(label="Сбросить к дефолтам", style=discord.ButtonStyle.danger, emoji="🔄", row=2)
    async def reset_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await db.reset_voice_customization(self.master_id)
            # Обновляем кэш
            cog = interaction.client.get_cog("DynamicVoice")
            if cog and interaction.guild_id in cog.configs:
                cfg_ref = cog.configs[interaction.guild_id].get(self.master_id)
                if cfg_ref:
                    cfg_ref['channel_name_template'] = None
                    cfg_ref['embed_title'] = None
                    cfg_ref['embed_description'] = None
                    cfg_ref['embed_color'] = None
                    cfg_ref['mention_user'] = None
                    cfg_ref['send_welcome'] = None
                    self.cfg = cfg_ref

            embed = _build_customize_embed(self.master_id, self.cfg)
            view = VoiceCustomizeView(self.master_id, self.cfg)
            await interaction.response.edit_message(embed=embed, view=view)
        except Exception as e:
            logger.error(f"Error resetting customization: {e}", exc_info=True)
            await interaction.response.send_message("❌ Ошибка при сбросе настроек.", ephemeral=True)

    @discord.ui.button(label="Назад", style=discord.ButtonStyle.secondary, emoji="🔙", row=2)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await render_voice_manager(interaction)

    async def _save_and_refresh(self, interaction: discord.Interaction):
        """Сохраняет текущие настройки в БД и обновляет embed."""
        try:
            await db.update_voice_customization(
                master_channel_id=self.master_id,
                channel_name_template=self.cfg.get('channel_name_template'),
                embed_title=self.cfg.get('embed_title'),
                embed_description=self.cfg.get('embed_description'),
                embed_color=self.cfg.get('embed_color'),
                mention_user=self.cfg.get('mention_user'),
                send_welcome=self.cfg.get('send_welcome'),
            )
            # Обновляем кэш
            cog = interaction.client.get_cog("DynamicVoice")
            if cog and interaction.guild_id in cog.configs:
                cog.configs[interaction.guild_id][self.master_id] = self.cfg

            embed = _build_customize_embed(self.master_id, self.cfg)
            view = VoiceCustomizeView(self.master_id, self.cfg)
            await interaction.response.edit_message(embed=embed, view=view)
        except Exception as e:
            logger.error(f"Error saving customization: {e}", exc_info=True)
            await interaction.response.send_message("❌ Ошибка при сохранении настроек.", ephemeral=True)


class VoiceCustomizeModal(discord.ui.Modal, title="Редактирование текстов"):
    """Модалка для редактирования шаблонов текстов."""

    channel_name = discord.ui.TextInput(
        label="Имя канала (шаблон)",
        style=discord.TextStyle.short,
        placeholder="📞 │ {user}",
        required=False,
        max_length=100,
    )
    embed_title_input = discord.ui.TextInput(
        label="Заголовок embed",
        style=discord.TextStyle.short,
        placeholder="Управление комнатой",
        required=False,
        max_length=256,
    )
    embed_desc_input = discord.ui.TextInput(
        label="Описание embed",
        style=discord.TextStyle.paragraph,
        placeholder="Привет, {user_mention}! Это твоя личная комната.\nИспользуй кнопки ниже для её настройки.",
        required=False,
        max_length=2000,
    )

    def __init__(self, master_id: int, cfg: dict):
        super().__init__()
        self.master_id = master_id
        self.cfg = cfg

        # Предзаполняем текущими значениями
        if cfg.get('channel_name_template'):
            self.channel_name.default = cfg['channel_name_template']
        if cfg.get('embed_title'):
            self.embed_title_input.default = cfg['embed_title']
        if cfg.get('embed_description'):
            self.embed_desc_input.default = cfg['embed_description']

    async def on_submit(self, interaction: discord.Interaction):
        # Пустая строка = сброс к дефолту (NULL)
        name_val = self.channel_name.value.strip() or None
        title_val = self.embed_title_input.value.strip() or None
        desc_val = self.embed_desc_input.value.strip() or None

        self.cfg['channel_name_template'] = name_val
        self.cfg['embed_title'] = title_val
        self.cfg['embed_description'] = desc_val

        try:
            await db.update_voice_customization(
                master_channel_id=self.master_id,
                channel_name_template=name_val,
                embed_title=title_val,
                embed_description=desc_val,
                embed_color=self.cfg.get('embed_color'),
                mention_user=self.cfg.get('mention_user'),
                send_welcome=self.cfg.get('send_welcome'),
            )
            # Обновляем кэш
            cog = interaction.client.get_cog("DynamicVoice")
            if cog and interaction.guild_id in cog.configs:
                cog.configs[interaction.guild_id][self.master_id] = self.cfg

            embed = _build_customize_embed(self.master_id, self.cfg)
            view = VoiceCustomizeView(self.master_id, self.cfg)
            await interaction.response.edit_message(embed=embed, view=view)
        except Exception as e:
            logger.error(f"Error saving texts: {e}", exc_info=True)
            await interaction.response.send_message("❌ Ошибка при сохранении текстов.", ephemeral=True)


# ==========================================
# USER CONTROL PANEL
# ==========================================

class ChannelNameModal(discord.ui.Modal, title="Изменение названия комнаты"):
    new_name = discord.ui.TextInput(
        label="Новое название",
        style=discord.TextStyle.short,
        placeholder="Введите название канала...",
        required=True,
        max_length=100
    )

    async def on_submit(self, interaction: discord.Interaction):
        channel = interaction.user.voice.channel if interaction.user.voice else None
        if not channel:
            await interaction.response.send_message("Вы не находитесь в голосовом канале.", ephemeral=True)
            return
        try:
            await channel.edit(name=self.new_name.value)
            await interaction.response.send_message(f"✅ Название канала изменено на **{self.new_name.value}**", ephemeral=True)
        except Exception as e:
            logger.error(f"Error renaming channel: {e}")
            await interaction.response.send_message("❌ Не удалось изменить название канала. Возможно, сработал лимит Discord (2 раза в 10 минут).", ephemeral=True)

class ChannelLimitModal(discord.ui.Modal, title="Изменение лимита пользователей"):
    new_limit = discord.ui.TextInput(
        label="Новый лимит (0 - без лимита)",
        style=discord.TextStyle.short,
        placeholder="Например: 5",
        required=True,
        max_length=2
    )

    async def on_submit(self, interaction: discord.Interaction):
        channel = interaction.user.voice.channel if interaction.user.voice else None
        if not channel:
            await interaction.response.send_message("Вы не находитесь в голосовом канале.", ephemeral=True)
            return
            
        try:
            limit = int(self.new_limit.value)
            if limit < 0 or limit > 99:
                limit = 0
            await channel.edit(user_limit=limit)
            if limit == 0:
                await interaction.response.send_message("✅ Лимит пользователей убран.", ephemeral=True)
            else:
                await interaction.response.send_message(f"✅ Лимит пользователей установлен на **{limit}**.", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("❌ Пожалуйста, введите корректное число.", ephemeral=True)
        except Exception as e:
            logger.error(f"Error changing user limit: {e}")
            await interaction.response.send_message("❌ Ошибка при изменении лимита.", ephemeral=True)

def is_bot_busy_in_other_channel(interaction: discord.Interaction) -> bool:
    """Проверяет, занят ли бот воспроизведением для людей в другом канале."""
    guild = interaction.guild
    if not guild:
        return False

    vc = guild.voice_client
    if not vc or not vc.channel:
        return False

    user = interaction.user
    user_id = user.id
    user_channel = None

    # 1. Свойство voice у пользователя/члена
    if hasattr(user, "voice") and user.voice and user.voice.channel:
        user_channel = user.voice.channel

    # 2. _voice_state_for гильдии
    if not user_channel and hasattr(guild, "_voice_state_for"):
        vs = guild._voice_state_for(user_id)
        if vs and vs.channel:
            user_channel = vs.channel

    # 3. Прямой поиск по каналам гильдии
    if not user_channel:
        for ch in guild.voice_channels:
            if hasattr(ch, "voice_states") and user_id in ch.voice_states:
                user_channel = ch
                break
            if hasattr(ch, "members") and any(m.id == user_id for m in ch.members):
                user_channel = ch
                break

    if vc.channel != user_channel:
        active_members = [m for m in vc.channel.members if not m.bot]
        if active_members:
            return True
    return False


# Представление для управления комнатой
class UserControlPanel(discord.ui.View):
    def __init__(self, soundscapes_enabled: bool = True):
        super().__init__(timeout=None)

        # Кнопка "Музыка" (Вызывает панель плееров)
        music_btn = discord.ui.Button(
            label="Музыка",
            style=discord.ButtonStyle.primary,
            emoji="🎵",
            custom_id="ucp_music",
            row=1,
        )
        music_btn.callback = self._music_callback
        self.add_item(music_btn)

        # Добавляем кнопку "Фоновая Атмосфера" только если она включена на сервере
        if soundscapes_enabled:
            soundscape_btn = discord.ui.Button(
                label="Атмосфера",
                style=discord.ButtonStyle.secondary,
                emoji="🌧️",
                custom_id="ucp_soundscapes",
                row=1,
            )
            soundscape_btn.callback = self._soundscapes_callback
            self.add_item(soundscape_btn)

    async def _music_callback(self, interaction: discord.Interaction) -> None:
        """Открывает меню выбора музыкального плеера."""
        embed = build_music_selection_embed()
        view = MusicSelectionView()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def _soundscapes_callback(self, interaction: discord.Interaction) -> None:
        """Открывает меню выбора фоновой атмосферы."""
        cfg = await db.get_soundscapes_config(interaction.guild_id)
        if not cfg.get("soundscapes_enabled", True):
            await interaction.response.send_message(
                "❌ Фоновые Атмосферы отключены администратором этого сервера.", ephemeral=True
            )
            return

        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("❌ Вы должны находиться в голосовом канале!", ephemeral=True)
            return

        vc = interaction.guild.voice_client
        current_scape = None
        if vc:
            voice_conn = getattr(vc, "_conn", None) or getattr(vc, "_connection", None)
            if voice_conn:
                current_scape = getattr(voice_conn, "_current_soundscape", None)

        view = SoundscapeSelectView(current_soundscape=current_scape)
        await interaction.response.send_message(
            embed=discord.Embed(
                title="🌧️ Фоновые Атмосферы (Soundscapes)",
                description="Выберите фоновый звук для отдыха или фоновой атмосферы в вашей комнате:",
                color=discord.Color.blue()
            ),
            view=view,
            ephemeral=True
        )

    async def _spotify_callback(self, interaction: discord.Interaction) -> None:
        """Запускает панель управления Spotify или восстанавливает сессию."""
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        cog = interaction.client.get_cog("SpotifyMusic")
        if not cog:
            await interaction.response.send_message(
                "❌ Модуль Spotify не загружен.", ephemeral=True
            )
            return

        # Отправляем панель плеера
        await cog.send_player_panel(interaction)

    async def _lofi_callback(self, interaction: discord.Interaction) -> None:
        """Запускает lofi-радио в текущем голосовом канале пользователя."""
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        cog = interaction.client.get_cog("LofiRadio")
        if not cog:
            await interaction.response.send_message(
                "❌ Модуль Lofi Radio не загружен.", ephemeral=True
            )
            return
        await cog.start_radio(interaction)

    async def _ym_callback(self, interaction: discord.Interaction) -> None:
        """Запускает панель управления Яндекс.Музыки или авторизацию."""
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        cog = interaction.client.get_cog("YandexMusic")
        if not cog:
            await interaction.response.send_message(
                "❌ Модуль Яндекс.Музыки не загружен.", ephemeral=True
            )
            return

        client = await cog.get_ym_client(interaction.guild_id)
        if not client:
            from views.ym_views import YMAuthView
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="🔑 Вход в Яндекс.Музыку",
                    description="Для работы плеера сначала авторизуйте бота в Яндекс.Музыке.",
                    color=discord.Color.red()
                ),
                view=YMAuthView(),
                ephemeral=True
            )
            return

        await cog.send_player_panel(interaction)

    async def _rutube_callback(self, interaction: discord.Interaction) -> None:
        """Запускает панель управления RuTube или восстанавливает сессию."""
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        cog = interaction.client.get_cog("RutubeMusic")
        if not cog:
            await interaction.response.send_message(
                "❌ Модуль RuTube не загружен.", ephemeral=True
            )
            return

        # Отправляем панель плеера
        await cog.send_player_panel(interaction)

    async def check_permissions(self, interaction: discord.Interaction) -> bool:
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("❌ Вы должны находиться в этом голосовом канале!", ephemeral=True)
            return False
            
        channel = interaction.user.voice.channel
        permissions = channel.permissions_for(interaction.user)
        if not permissions.manage_channels:
            await interaction.response.send_message("❌ У вас нет прав на управление этой комнатой!", ephemeral=True)
            return False
            
        return True

    @discord.ui.button(label="Название", style=discord.ButtonStyle.primary, emoji="✏️", custom_id="ucp_rename", row=0)
    async def rename_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self.check_permissions(interaction):
            await interaction.response.send_modal(ChannelNameModal())

    @discord.ui.button(label="Лимит", style=discord.ButtonStyle.primary, emoji="👥", custom_id="ucp_limit", row=0)
    async def limit_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self.check_permissions(interaction):
            await interaction.response.send_modal(ChannelLimitModal())

    @discord.ui.button(style=discord.ButtonStyle.danger, emoji="🔒", custom_id="ucp_lock", row=0)
    async def lock_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self.check_permissions(interaction):
            channel = interaction.user.voice.channel
            overwrite = channel.overwrites_for(interaction.guild.default_role)
            overwrite.connect = False
            await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
            await interaction.response.send_message("✅ Комната закрыта от посторонних.", ephemeral=True)

    @discord.ui.button(style=discord.ButtonStyle.success, emoji="🔓", custom_id="ucp_unlock", row=0)
    async def unlock_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await self.check_permissions(interaction):
            channel = interaction.user.voice.channel
            overwrite = channel.overwrites_for(interaction.guild.default_role)
            overwrite.connect = None
            await channel.set_permissions(interaction.guild.default_role, overwrite=overwrite)
            await interaction.response.send_message("✅ Комната открыта для всех.", ephemeral=True)


# ==========================================
# MUSIC SETTINGS UI
# ==========================================

async def render_music_manager(interaction: discord.Interaction):
    cfg_ducking = await db.get_ducking_config(interaction.guild_id)
    cfg_scapes = await db.get_soundscapes_config(interaction.guild_id)
    cfg_blend = await db.get_blend_config(interaction.guild_id)
    ducking_enabled = cfg_ducking.get("ducking_enabled", True)
    soundscapes_enabled = cfg_scapes.get("soundscapes_enabled", True)
    blend_enabled = cfg_blend.get("blend_enabled", True)

    embed = discord.Embed(
        title="📻 Настройки Музыкальных Плееров",
        description=(
            "Здесь вы можете настроить параметры Яндекс.Музыки, Lofi Radio и RuTube для этого сервера.\n\n"
            "**Доступные опции:**\n"
            "🔹 Режим 24/7 (Бот остается в канале без отключения)\n"
            "🔹 Ограничение прав на управление плеером (Все / Владельцы комнат / DJ-роли)\n"
            "🔹 Настройка ролей DJ для управления\n"
            f"🔹 Smart Ducking (Приглушение при речи): {'**Включено** 🎙️' if ducking_enabled else '**Выключено** 🔇'}\n"
            f"🔹 Фоновые Атмосферы: {'**Включены** 🌧️' if soundscapes_enabled else '**Выключены** 🔇'}\n"
            f"🔹 Совместная Волна (Blend DJ): {'**Включена** 🔀' if blend_enabled else '**Выключена** 🔇'}"
        ),
        color=discord.Color.from_rgb(255, 204, 0)
    )
    view = MusicSettingsMainView(
        ducking_enabled=ducking_enabled,
        soundscapes_enabled=soundscapes_enabled,
        blend_enabled=blend_enabled
    )
    if interaction.response.is_done():
        await interaction.edit_original_response(embed=embed, view=view)
    elif interaction.type == discord.InteractionType.component:
        await interaction.response.edit_message(embed=embed, view=view)
    else:
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class MusicSettingsMainView(discord.ui.View):
    def __init__(self, ducking_enabled: bool = True, soundscapes_enabled: bool = True, blend_enabled: bool = True):
        super().__init__(timeout=300)
        self.ducking_toggle_btn.label = f"Smart Ducking: {'Вкл' if ducking_enabled else 'Выкл'}"
        self.ducking_toggle_btn.style = discord.ButtonStyle.success if ducking_enabled else discord.ButtonStyle.danger

        self.soundscapes_toggle_btn.label = f"Атмосферы: {'Вкл' if soundscapes_enabled else 'Выкл'}"
        self.soundscapes_toggle_btn.style = discord.ButtonStyle.success if soundscapes_enabled else discord.ButtonStyle.danger

        self.blend_toggle_btn.label = f"Совместная Волна: {'Вкл' if blend_enabled else 'Выкл'}"
        self.blend_toggle_btn.style = discord.ButtonStyle.success if blend_enabled else discord.ButtonStyle.danger

    @discord.ui.button(label="Яндекс.Музыка", style=discord.ButtonStyle.primary, emoji="📻", row=0)
    async def ym_settings_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        ym_cog = interaction.client.get_cog("YandexMusic")
        if not ym_cog:
            await interaction.response.send_message("❌ Модуль Яндекс.Музыки не загружен.", ephemeral=True)
            return
        
        settings = await db.get_ym_settings(interaction.guild_id)
        
        cfg = await db.get_ym_config(interaction.guild_id)
        settings["username"] = cfg.get("username") if cfg else None
        
        from views.ym_views import YMConfigView
        embed = ym_cog._build_config_embed(interaction.guild_id, settings)
        view = YMConfigView(interaction.guild, settings)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Lofi Radio", style=discord.ButtonStyle.primary, emoji="🎵", row=0)
    async def lofi_settings_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        lofi_cog = interaction.client.get_cog("LofiRadio")
        if not lofi_cog:
            await interaction.response.send_message("❌ Модуль Lofi Radio не загружен.", ephemeral=True)
            return
        
        settings = await db.get_lofi_config(interaction.guild_id)
        from views.lofi_views import LofiConfigView
        
        embed = lofi_cog._build_config_embed(interaction.guild_id, settings)
        view = LofiConfigView(interaction.guild, settings)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="RuTube", style=discord.ButtonStyle.primary, emoji="📺", row=0)
    async def rutube_settings_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        rutube_cog = interaction.client.get_cog("RutubeMusic")
        if not rutube_cog:
            await interaction.response.send_message("❌ Модуль RuTube не загружен.", ephemeral=True)
            return
            
        settings = await db.get_rutube_config(interaction.guild_id)
        from views.rutube_views import RutubeConfigView, build_rutube_config_embed
        
        embed = await build_rutube_config_embed(interaction.guild_id, settings)
        view = RutubeConfigView(interaction.guild, settings)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Dynamic Музыка", style=discord.ButtonStyle.primary, emoji="🔴", row=0)
    async def spotify_settings_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        spotify_cog = interaction.client.get_cog("SpotifyMusic")
        if not spotify_cog:
            await interaction.response.send_message("❌ Модуль Spotify не загружен.", ephemeral=True)
            return
            
        settings = await db.get_spotify_config(interaction.guild_id)
        from views.spotify_views import SpotifyConfigView, build_spotify_config_embed
        
        embed = await build_spotify_config_embed(interaction.guild_id, settings)
        view = SpotifyConfigView(interaction.guild, settings)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Smart Ducking: Вкл", style=discord.ButtonStyle.success, emoji="🎙️", row=1)
    async def ducking_toggle_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cfg = await db.get_ducking_config(interaction.guild_id)
        current = cfg.get("ducking_enabled", True)
        new_val = not current
        await db.update_ducking_config(interaction.guild_id, enabled=new_val, level=0.35)

        # Применяем горячую смену прямо в активном голосовом соединении
        for vc in interaction.client.voice_clients:
            if vc.guild and vc.guild.id == interaction.guild_id:
                voice_conn = getattr(vc, "_conn", None) or getattr(vc, "_connection", None)
                if voice_conn:
                    voice_conn._ducking_enabled = new_val

        button.label = f"Smart Ducking: {'Вкл' if new_val else 'Выкл'}"
        button.style = discord.ButtonStyle.success if new_val else discord.ButtonStyle.danger
        
        status_str = "включено 🎙️" if new_val else "выключено 🔇"
        await interaction.response.send_message(f"⚙️ **Приглушение музыки при речи (Smart Ducking)** {status_str} для этого сервера.", ephemeral=True)
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass

    @discord.ui.button(label="Атмосферы: Вкл", style=discord.ButtonStyle.success, emoji="🌧️", row=1)
    async def soundscapes_toggle_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cfg = await db.get_soundscapes_config(interaction.guild_id)
        current = cfg.get("soundscapes_enabled", True)
        new_val = not current
        await db.update_soundscapes_config(interaction.guild_id, enabled=new_val)

        for vc in interaction.client.voice_clients:
            if vc.guild and vc.guild.id == interaction.guild_id:
                voice_conn = getattr(vc, "_conn", None) or getattr(vc, "_connection", None)
                if voice_conn:
                    voice_conn._soundscapes_enabled = new_val

        button.label = f"Атмосферы: {'Вкл' if new_val else 'Выкл'}"
        button.style = discord.ButtonStyle.success if new_val else discord.ButtonStyle.danger
        
        status_str = "включены 🌧️" if new_val else "выключены 🔇"
        await interaction.response.send_message(f"⚙️ **Фоновые Атмосферы (Soundscapes)** {status_str} для этого сервера.", ephemeral=True)
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass

    @discord.ui.button(label="Совместная Волна: Вкл", style=discord.ButtonStyle.success, emoji="🔀", row=1)
    async def blend_toggle_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        cfg = await db.get_blend_config(interaction.guild_id)
        current = cfg.get("blend_enabled", True)
        new_val = not current
        await db.update_blend_config(interaction.guild_id, enabled=new_val)

        button.label = f"Совместная Волна: {'Вкл' if new_val else 'Выкл'}"
        button.style = discord.ButtonStyle.success if new_val else discord.ButtonStyle.danger
        
        status_str = "включена 🔀" if new_val else "выключена 🔇"
        await interaction.response.send_message(f"⚙️ **Совместная Волна (Smart Blend DJ)** {status_str} для этого сервера.", ephemeral=True)
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass

    @discord.ui.button(label="Назад", style=discord.ButtonStyle.secondary, emoji="🔙", row=1)
    async def back_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await render_voice_manager(interaction)


class SoundscapeSelectView(discord.ui.View):
    """View-контейнер для выпадающего списка SoundscapeSelect."""
    def __init__(self, current_soundscape: Optional[str] = None):
        super().__init__(timeout=180)
        self.add_item(SoundscapeSelect(current_soundscape=current_soundscape))


class SoundscapeSelect(discord.ui.Select):
    """Dropdown меню для выбора фоновой атмосферы в плеерах."""
    def __init__(self, current_soundscape: Optional[str] = None):
        options = [
            discord.SelectOption(label="Выключить", value="off", emoji="⛔", default=(current_soundscape is None or current_soundscape == "off")),
            discord.SelectOption(label="Дождь за окном", value="rain", emoji="🌧️", default=(current_soundscape == "rain")),
            discord.SelectOption(label="Уютный камин", value="fireplace", emoji="🔥", default=(current_soundscape == "fireplace")),
            discord.SelectOption(label="Шум прибоя", value="ocean", emoji="🌊", default=(current_soundscape == "ocean")),
            discord.SelectOption(label="Ночной костёр", value="bonfire", emoji="🏕️", default=(current_soundscape == "bonfire")),
            discord.SelectOption(label="Лесные капли", value="drops", emoji="💧", default=(current_soundscape == "drops")),
        ]
        super().__init__(placeholder="🌧️ Выберите фоновую атмосферу...", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction):
        choice = self.values[0]
        new_val = None if choice == "off" else choice
        
        voice_state = getattr(interaction.user, 'voice', None)
        if not voice_state or not voice_state.channel:
            await interaction.response.send_message("❌ Вы должны находиться в голосовом канале!", ephemeral=True)
            return

        channel = voice_state.channel
        vc = interaction.guild.voice_client

        if not vc:
            try:
                vc = await channel.connect(cls=discord.VoiceClient)
            except Exception as e:
                logger.error("Ошибка подключения к голосовой комнате: %s", e)
                await interaction.response.send_message("❌ Не удалось подключиться к голосовому каналу.", ephemeral=True)
                return
        elif vc.channel != channel:
            try:
                await vc.move_to(channel)
            except Exception as e:
                logger.error("Ошибка перемещения в голосовую комнату: %s", e)

        voice_conn = getattr(vc, "_conn", None) or getattr(vc, "_connection", None)
        if voice_conn:
            voice_conn._current_soundscape = new_val

        label_map = {
            "off": "выключена 🔇",
            "rain": "🌧️ Дождь за окном",
            "fireplace": "🔥 Уютный камин",
            "ocean": "🌊 Шум прибоя",
            "bonfire": "🏕️ Ночной костёр",
            "drops": "💧 Лесные капли",
        }

        is_music = getattr(vc, "_is_music_playing", False)
        if is_music and new_val:
            await interaction.response.send_message(
                "ℹ️ Фоновые атмосферы работают автономно, когда музыка выключена. Чтобы слушать атмосферу, остановите или поставьте плеер на паузу.",
                ephemeral=True
            )
            return

        if not is_music:
            if new_val:
                if vc.is_playing() or vc.is_paused():
                    vc.stop()
                try:
                    from utils.soundscapes import build_soundscape_ffmpeg_args
                except ImportError:
                    from src.utils.soundscapes import build_soundscape_ffmpeg_args
                target_src, before_opts, opts = build_soundscape_ffmpeg_args(
                    music_source=None,
                    soundscape_key=new_val,
                    soundscape_enabled=True,
                    volume_scape=0.15,
                )
                try:
                    source = discord.FFmpegPCMAudio(target_src, before_options=before_opts, options=opts)
                    vc.play(source)
                except Exception as exc:
                    logger.error("Ошибка запуска аудио атмосферы: %s", exc)
            else:
                if vc.is_playing() or vc.is_paused():
                    vc.stop()

        await interaction.response.send_message(f"🌧️ Атмосфера изменена на: **{label_map.get(choice, choice)}**", ephemeral=True)



def build_music_selection_embed() -> discord.Embed:
    import config
    
    desc_lines = [
        "Бот готов играть музыку в вашем голосовом канале!\n",
        "Выберите желаемый источник воспроизведения:"
    ]
    
    if getattr(config, "ENABLE_YANDEX_MUSIC", False):
        desc_lines.append("🔹 **Яндекс.Музыка** — ваша личная волна, поиск песен, плейлисты.")
    if getattr(config, "ENABLE_LOFI_RADIO", False):
        desc_lines.append("🔹 **Lofi Radio** — круглосуточный расслабряющий лоу-фай поток.")
    if getattr(config, "ENABLE_RUTUBE_MUSIC", False):
        desc_lines.append("🔹 **RuTube Музыка** — видео, стримы и плейлисты из RuTube.")
    if getattr(config, "ENABLE_SPOTIFY", False):
        desc_lines.append("🔹 **Dynamic Музыка** — плейлисты Spotify и YouTube, треки, прямые ссылки.")
        
    embed = discord.Embed(
        title="📻 Выбор Музыкального Плеера",
        description="\n".join(desc_lines),
        color=discord.Color.from_rgb(255, 204, 0)
    )
    return embed


class MusicSelectionView(discord.ui.View):
    """View для команды /music_panel, позволяющий выбрать и запустить нужный плеер."""
    def __init__(self) -> None:
        super().__init__(timeout=180)

    @discord.ui.button(label="Яндекс.Музыка", style=discord.ButtonStyle.secondary, emoji="📻", row=0)
    async def ym_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        try:
            await interaction.message.delete()
        except Exception:
            pass
        
        ym_cog = interaction.client.get_cog("YandexMusic")
        if ym_cog:
            client = await ym_cog.get_ym_client(interaction.guild_id)
            if not client:
                from views.ym_views import YMAuthView
                await interaction.response.send_message(
                    embed=discord.Embed(
                        title="🔑 Вход в Яндекс.Музыку",
                        description="Для запуска Яндекс.Музыки сначала авторизуйте бота.",
                        color=discord.Color.red()
                    ),
                    view=YMAuthView(),
                    ephemeral=True
                )
                return
            await ym_cog.send_player_panel(interaction)

    @discord.ui.button(label="Lofi Radio", style=discord.ButtonStyle.success, emoji="🎵", row=0)
    async def lofi_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        try:
            await interaction.message.delete()
        except Exception:
            pass
        
        lofi_cog = interaction.client.get_cog("LofiRadio")
        if lofi_cog:
            await lofi_cog.send_player_panel(interaction)

    @discord.ui.button(label="RuTube", style=discord.ButtonStyle.primary, emoji="📺", row=1)
    async def rutube_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        try:
            await interaction.message.delete()
        except Exception:
            pass
            
        rutube_cog = interaction.client.get_cog("RutubeMusic")
        if rutube_cog:
            await rutube_cog.send_player_panel(interaction)

    @discord.ui.button(label="Dynamic Музыка", style=discord.ButtonStyle.danger, emoji="🔴", row=1)
    async def spotify_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if is_bot_busy_in_other_channel(interaction):
            await interaction.response.send_message("❌ Бот сейчас занят в другом голосовом канале.", ephemeral=True)
            return
        try:
            await interaction.message.delete()
        except Exception:
            pass
            
        spotify_cog = interaction.client.get_cog("SpotifyMusic")
        if spotify_cog:
            await spotify_cog.send_player_panel(interaction)


