import lolka as discord
from lolka.ext import commands
import logging
import asyncio
import sys
import datetime
import random

import config
import db
from utils.logger_sanitizer import TokenMaskingFilter

# Настраиваем логирование в первую очередь, чтобы не терять лог монкипатча
root_logger = logging.getLogger()
root_logger.setLevel(getattr(logging, config.LOG_LEVEL.upper(), logging.INFO))
stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
stream_handler.addFilter(TokenMaskingFilter())
root_logger.addHandler(stream_handler)

if getattr(config, "DISCORD_LOG_WEBHOOK_URL", None):
    from utils.logger_webhook import DiscordWebhookHandler
    webhook_handler = DiscordWebhookHandler(config.DISCORD_LOG_WEBHOOK_URL)
    webhook_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    root_logger.addHandler(webhook_handler)

logger = logging.getLogger('bot')

# ── Монкипатч: заменяем утекающий PyAV OpusEncoder на ctypes-версию ──
# PyAV av.CodecContext.encode() утекает ~82 байт/фрейм на уровне C.
# lolka.opus.Encoder вызывает opus_encode() напрямую через ctypes — 0 утечки.
try:
    from patches._opus_codec import PatchedOpusEncoder
    import aiortc.codecs.opus as _aiortc_opus
    import aiortc.codecs as _aiortc_codecs

    _aiortc_opus.OpusEncoder = PatchedOpusEncoder
    _aiortc_codecs.OpusEncoder = PatchedOpusEncoder

    logger.info(
        'Монкипатч OpusEncoder применён к обоим пространствам имён (PyAV → ctypes)'
    )
except Exception as _e:
    logger.warning(
        'Не удалось применить монкипатч OpusEncoder: %s. '
        'Используется стандартный PyAV-энкодер (с утечкой).', _e
    )

# ── Монкипатч: Защита от спайков (Уровень 1) ──
# Ограничиваем количество одновременных установок WebRTC-соединений.
# Создание VoiceClient требует спавна FFmpeg пайпов и аллокации C-буферов, 
# что сильно бьет по CPU. Семафор сглаживает массовые реконнекты (например, при рестарте).
_voice_connect_semaphore = asyncio.Semaphore(1)
_original_voice_connect = discord.VoiceChannel.connect

async def _patched_voice_connect(self, *args, **kwargs):
    async with _voice_connect_semaphore:
        res = await _original_voice_connect(self, *args, **kwargs)
        await asyncio.sleep(10.0)
        return res

discord.VoiceChannel.connect = _patched_voice_connect

if hasattr(discord, 'StageChannel'):
    _original_stage_connect = discord.StageChannel.connect
    async def _patched_stage_connect(self, *args, **kwargs):
        async with _voice_connect_semaphore:
            res = await _original_stage_connect(self, *args, **kwargs)
            await asyncio.sleep(10.0)
            return res
    discord.StageChannel.connect = _patched_stage_connect

# ── Монкипатч: Защита от 429 при старте (Уровень 2) ──
# lolka.py падает с HTTPException 429 при static_login, если IP заблокирован.
_original_static_login = discord.http.HTTPClient.static_login

async def _patched_static_login(self, token: str):
    import json
    max_retries = 10
    for attempt in range(max_retries):
        try:
            return await _original_static_login(self, token)
        except discord.HTTPException as e:
            if e.status == 429:
                try:
                    # Попытаемся извлечь retry_after из ответа (e.text или e.response)
                    if hasattr(e, 'response') and hasattr(e.response, 'text'):
                        text = await e.response.text()
                        data = json.loads(text)
                        retry_after = data.get('retry_after', 30.0)
                    else:
                        retry_after = 30.0
                except:
                    retry_after = 30.0
                    
                # Принудительно ограничиваем сон (если дали бан на час - придется ждать)
                # но обычно это секунд 10-60
                sleep_time = max(min(float(retry_after), 120.0), 5.0)
                logger.warning(f"Rate Limit 429 при логине! Ждём {sleep_time} сек (попытка {attempt+1}/{max_retries})...")
                await asyncio.sleep(sleep_time)
            else:
                raise
    # Если исчерпали попытки
    return await _original_static_login(self, token)

discord.http.HTTPClient.static_login = _patched_static_login


class DynamicVoiceBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.voice_states = True
        intents.guilds = True
        super().__init__(command_prefix="!", intents=intents, help_command=None)

    async def on_message(self, message: discord.Message) -> None:
        """Игнорируем текстовые сообщения (префиксные команды).
        
        Бот работает исключительно через Slash Commands и UI-компоненты.
        """
        return

    async def on_guild_join(self, guild: discord.Guild) -> None:
        """Автоматическая установка ника бота при добавлении на новый сервер."""
        logger.info("Бот добавлен на новый сервер: %s (ID: %s)", guild.name, guild.id)
        desired_nick = getattr(config, "DEFAULT_BOT_NICKNAME", "Dynamic Voice")
        if not desired_nick:
            return

        try:
            me = guild.me or (guild.get_member(self.user.id) if self.user else None)
            if me:
                await me.edit(nick=desired_nick)
                logger.info("Установлен никнейм '%s' на сервере %s", desired_nick, guild.id)
        except discord.Forbidden:
            logger.warning("Отсутствуют права Change Nickname на сервере %s (ID: %s)", guild.name, guild.id)
        except Exception as e:
            logger.warning("Не удалось изменить никнейм на сервере %s: %s", guild.id, e)

    async def setup_hook(self):
        await db.init_db_pool()
        await self.load_extension("cogs.voice")
        
        from views.ui import UserControlPanel
        self.add_view(UserControlPanel())

        # Lofi Radio — условная загрузка по feature flag
        if config.ENABLE_LOFI_RADIO:
            await self.load_extension("cogs.lofi")
            from views.lofi_views import LofiPlayerView
            self.add_view(LofiPlayerView())
            logger.info("Lofi Radio module enabled.")
        else:
            logger.info("Lofi Radio module disabled (ENABLE_LOFI_RADIO=false).")

        # Yandex Music — условная загрузка по feature flag
        if config.ENABLE_YANDEX_MUSIC:
            await self.load_extension("cogs.yandex_music")
            from views.ym_views import YMAuthView, YMReadyView
            self.add_view(YMAuthView())
            self.add_view(YMReadyView())
            logger.info("Yandex Music module enabled.")
        else:
            logger.info("Yandex Music module disabled (ENABLE_YANDEX_MUSIC=false).")
        
        # RuTube Music — условная загрузка по feature flag
        if config.ENABLE_RUTUBE_MUSIC:
            await self.load_extension("cogs.rutube")
            logger.info("RuTube Music module enabled.")
        else:
            logger.info("RuTube Music module disabled (ENABLE_RUTUBE_MUSIC=false).")

        # Spotify Music — условная загрузка по feature flag
        if getattr(config, "ENABLE_SPOTIFY", False):
            await self.load_extension("cogs.spotify")
            logger.info("Spotify Music module enabled.")
        else:
            logger.info("Spotify Music module disabled (ENABLE_SPOTIFY=false).")
        
        # Фоновый таск: мягкий автоперезапуск в 5:00 UTC (страховка от мелких утечек)
        self._auto_restart_task = self.loop.create_task(self._auto_restart_loop())

        # Фоновый таск: ротация статусов бота
        self._status_rotation_task = self.loop.create_task(self._status_rotation_loop())

        await self.tree.sync()
        logger.info("Bot is ready and commands synced.")

    async def _status_rotation_loop(self) -> None:
        """Фоновый цикл ротации статусов бота."""
        await self.wait_until_ready()

        from partners import get_partner_statuses

        base_statuses = [
            (discord.ActivityType.listening, "Музыку | /help"),
            (discord.ActivityType.listening, "Lofi | /help"),
            (discord.ActivityType.listening, "Пластинки | /help"),
            (discord.ActivityType.listening, "Чилл | /help"),
            (discord.ActivityType.watching, "За порядком | /help"),
            (discord.ActivityType.watching, "Спектрограмм | /help"),
            (discord.ActivityType.watching, "За звуком | /help"),
            (discord.ActivityType.listening, "Биты | /help")
        ]

        while not self.is_closed():
            try:
                # Копируем базовый список
                statuses = base_statuses.copy()

                # Добавляем статусы от наших официальных партнёров
                statuses.extend(get_partner_statuses())

                # Если настроен Boosty, добавляем один статус спонсорства
                if config.DONATION_BOOSTY_URL:
                    statuses.append((discord.ActivityType.watching, "На Boosty | /donate"))

                # Если настроен DonationAlerts, добавляем один статус доната
                if config.DONATION_ALERTS_URL:
                    statuses.append((discord.ActivityType.watching, "На DonationAlerts | /donate"))

                # Перемешиваем статусы для случайного порядка
                random.shuffle(statuses)

                for act_type, act_name in statuses:
                    if self.is_closed():
                        break

                    logger.debug("Смена статуса бота на: %s (%s)", act_name, act_type)
                    try:
                        await self.change_presence(
                            activity=discord.Activity(
                                type=act_type,
                                name=act_name
                            )
                        )
                    except Exception as e:
                        logger.warning("Не удалось изменить статус присутствия: %s", e)

                    # Ждем 10 минут (600 секунд) перед сменой
                    await asyncio.sleep(600)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.error("Ошибка в _status_rotation_loop", exc_info=True)
                await asyncio.sleep(60)

    async def _auto_restart_loop(self) -> None:
        """Ежедневно в 5:00 UTC проверяет, есть ли активные пользователи.

        Если голосовые каналы бота пусты (нет живых слушателей) —
        выполняет sys.exit(0), и Docker перезапускает контейнер чисто.
        Это страховка от возможных мелких утечек, не пойманных патчем.
        """
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                now = datetime.datetime.now(datetime.timezone.utc)
                # Вычисляем время до следующих 5:00 UTC
                target = now.replace(hour=5, minute=0, second=0, microsecond=0)
                if now >= target:
                    target += datetime.timedelta(days=1)
                delay = (target - now).total_seconds()
                logger.info(
                    "Автоперезапуск: следующая проверка через %.0f сек (%s UTC)",
                    delay, target.strftime('%Y-%m-%d %H:%M')
                )
                await asyncio.sleep(delay)

                # Проверяем, есть ли живые пользователи в голосовых каналах бота
                has_listeners = False
                for vc in self.voice_clients:
                    if vc.channel:
                        human_members = [m for m in vc.channel.members if not m.bot]
                        if human_members:
                            has_listeners = True
                            break

                if has_listeners:
                    logger.info(
                        "Автоперезапуск: в каналах есть слушатели, пропускаем."
                    )
                    continue

                logger.info(
                    "Автоперезапуск: слушателей нет, выполняем чистый перезапуск."
                )
                sys.exit(0)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.error("Ошибка в _auto_restart_loop", exc_info=True)
                await asyncio.sleep(3600)

    async def close(self):
        await super().close()
        await db.close_db_pool()

bot = DynamicVoiceBot()


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: discord.app_commands.AppCommandError,
) -> None:
    """Глобальный обработчик ошибок слэш-команд."""
    original = getattr(error, "original", error)

    if isinstance(original, discord.errors.DiscordServerError):
        logger.warning(
            "Server returned %s for command '%s' (guild %s): %s",
            original.status,
            interaction.command.name if interaction.command else "?",
            interaction.guild_id,
            original,
        )
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "⚠️ Сервер временно недоступен. Пожалуйста, попробуйте ещё раз через пару секунд.",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    "⚠️ Сервер временно недоступен. Пожалуйста, попробуйте ещё раз через пару секунд.",
                    ephemeral=True,
                )
        except discord.HTTPException:
            pass
        return

    logger.error(
        "Unhandled error in command '%s': %s",
        interaction.command.name if interaction.command else "?",
        error,
        exc_info=error,
    )


@bot.event
async def on_command_error(
    ctx: commands.Context,
    error: commands.CommandError,
) -> None:
    """Тихо игнорируем CommandNotFound от префиксных команд."""
    if isinstance(error, commands.CommandNotFound):
        return
    logger.error("Prefix command error: %s", error, exc_info=error)

@bot.tree.command(name="setup", description="Настройка мастер-комнат и приватных голосовых каналов (только для Админов)")
async def setup_command(interaction: discord.Interaction):
    if not interaction.permissions.administrator:
        await interaction.response.send_message("❌ Только администраторы могут настраивать комнаты.", ephemeral=True)
        return
        
    from views.ui import render_voice_manager
    await render_voice_manager(interaction)

class HelpView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        
        # Кнопки поддержки на первом ряду (row=0)
        if config.DONATION_BOOSTY_URL:
            self.add_item(discord.ui.Button(
                label="Поддержать на Boosty", 
                url=config.DONATION_BOOSTY_URL, 
                emoji="💎",
                row=0
            ))
        if config.DONATION_ALERTS_URL:
            self.add_item(discord.ui.Button(
                label="DonationAlerts", 
                url=config.DONATION_ALERTS_URL, 
                emoji="💸",
                row=0
            ))

        # Кнопка партнёров на втором ряду (row=1)
        partners_btn = discord.ui.Button(
            label="Наши партнёры",
            style=discord.ButtonStyle.success,
            emoji="🤝",
            row=1
        )
        partners_btn.callback = self.partners_callback
        self.add_item(partners_btn)

    async def partners_callback(self, interaction: discord.Interaction) -> None:
        from partners import get_all_partners
        from views.partner_views import PartnersView, build_partner_embed

        partners = get_all_partners()
        if not partners:
            await interaction.response.send_message("🤝 Пока нет активных партнёров.", ephemeral=True)
            return

        partner = partners[0]
        embed = build_partner_embed(partner)
        view = PartnersView(current_partner_id=partner["id"])
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

@bot.tree.command(name="partner", description="Информация о наших официальных партнёрах")
async def partner_command(interaction: discord.Interaction):
    from partners import get_all_partners
    from views.partner_views import PartnersView, build_partner_embed

    partners = get_all_partners()
    if not partners:
        await interaction.response.send_message("🤝 Пока нет активных партнёров.", ephemeral=True)
        return

    partner = partners[0]
    embed = build_partner_embed(partner)
    view = PartnersView(current_partner_id=partner["id"])
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@bot.tree.command(name="partners", description="Информация о наших официальных партнёрах")
async def partners_command(interaction: discord.Interaction):
    await partner_command(interaction)


@bot.tree.command(name="donate", description="Поддержать разработку и оплату серверов бота")
async def donate_command(interaction: discord.Interaction):

    embed = discord.Embed(
        title="💖 Поддержать проект",
        description="Спасибо, что используете DynamicVoiceBot! Ваша поддержка помогает оплачивать серверы и разрабатывать новые фичи.",
        color=discord.Color.brand_red()
    )
    await interaction.response.send_message(embed=embed, view=HelpView(), ephemeral=True)


@bot.tree.command(name="help", description="Полная инструкция по использованию бота и плееров")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🎙️ Инструкция по DynamicVoiceBot",
        description=(
            "Бот позволяет создавать приватные голосовые комнаты и наслаждаться музыкой из **4 независимых плееров**!\n\n"
            
            "**1. Приватные голосовые комнаты:**\n"
            "• `/setup` — (Для Админов) Создать мастер-канал (обычно с иконкой ➕).\n"
            "• Зайдите в мастер-канал, и бот мгновенно создаст вашу личную комнату и перенесет вас туда.\n"
            "• `/voice_panel` — Вызвать панель управления своей комнатой в чат.\n\n"
            
            "**2. Музыкальные плееры:**\n"
            "Управлять всеми плеерами можно через единое меню:\n"
            "👉 `/music_panel` — **Главное меню музыки**. Позволяет выбрать любой плеер (Яндекс.Музыка, Spotify, RuTube, Lofi Radio) и открыть его визуальный интерфейс.\n\n"
            
            "**Прямые команды для запуска музыки:**\n"
            "• `/ym play [запрос]` — Найти и включить трек из Яндекс.Музыки.\n"
            "• `/ym wave` — Запустить «Мою Волну» (бесконечный поток).\n"
            "• `/dynamic [ссылка]` — Включить музыку из Spotify или YouTube по ссылке или названию.\n"
            "• `/rutube [ссылка]` — Включить видео/аудио из RuTube по прямой ссылке.\n\n"
            
            "**Настройка 24/7 (Для Админов):**\n"
            "Режим 24/7 включается через команду `/setup` ➔ Настройка музыки. Администраторы могут включить круглосуточное вещание, но одновременно 24/7 может работать только для одного выбранного плеера.\n\n"
            
            "**Поддержка проекта 🤍**\n"
            "Бот работает бесплатно и без рекламы, но аренда мощных серверов и прокси требует затрат.\n"
            "Будем очень благодарны за любую поддержку! (кнопки ниже 👇)"
        ),
        color=discord.Color.blurple()
    )
    embed.set_footer(text="Создано с любовью | DynamicVoiceBot")
    
    view = HelpView()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@bot.tree.command(name="music_panel", description="Вызвать панель управления музыкой (Яндекс.Музыка / Lofi Radio / RuTube)")
async def music_panel_command(interaction: discord.Interaction):
    # 1. Проверяем нахождение пользователя в голосовом канале
    if not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.response.send_message("❌ Вы должны находиться в голосовом канале, чтобы вызвать панель управления музыкой.", ephemeral=True)
        return

    # 2. Проверяем, не занят ли бот людьми в другом канале
    voice_client = interaction.guild.voice_client
    if voice_client and voice_client.channel:
        if voice_client.channel != interaction.user.voice.channel:
            active_members = [m for m in voice_client.channel.members if not m.bot]
            if active_members:
                await interaction.response.send_message("❌ Бот сейчас занят воспроизведением в другом голосовом канале.", ephemeral=True)
                return

    # 3. Проверяем активный плеер
    ym_cog = bot.get_cog("YandexMusic")
    if ym_cog:
        ym_state = ym_cog._states.get(interaction.guild_id)
        if ym_state and ym_state.get("tracks") and voice_client and voice_client.channel == interaction.user.voice.channel:
            await ym_cog.send_player_panel(interaction)
            return

    lofi_cog = bot.get_cog("LofiRadio")
    if lofi_cog:
        if lofi_cog._current_station.get(interaction.guild_id) and voice_client and voice_client.channel == interaction.user.voice.channel:
            if hasattr(lofi_cog, "send_player_panel"):
                await lofi_cog.send_player_panel(interaction)
            else:
                station = lofi_cog._current_station.get(interaction.guild_id)
                volume = lofi_cog._volume.get(interaction.guild_id, 0.5)
                from cogs.lofi import _build_player_embed
                from views.lofi_views import LofiPlayerView
                embed = _build_player_embed(station, volume, connected=True)
                active = await lofi_cog.get_active_stations(interaction.guild_id)
                await interaction.response.send_message(embed=embed, view=LofiPlayerView(active))
            return

    # Если ничего не играет (или бот свободен/в режиме ожидания)
    from views.ui import MusicSelectionView, build_music_selection_embed
    embed = build_music_selection_embed()
    await interaction.response.send_message(embed=embed, view=MusicSelectionView())


if __name__ == "__main__":

    if not config.DISCORD_TOKEN or not config.DATABASE_URL:
        logger.error("Missing DISCORD_TOKEN or DATABASE_URL in .env")
        sys.exit(1)
        
    bot.run(config.DISCORD_TOKEN)
