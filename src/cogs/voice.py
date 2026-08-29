import logging
import random
import asyncio
from typing import Optional
from collections import defaultdict
import lolka as discord
from lolka.ext import commands
from lolka import app_commands
import db

logger = logging.getLogger(__name__)

# Дефолтные значения шаблонов (используются когда в БД NULL)
DEFAULT_CHANNEL_NAME = "📞 │ {user}"
DEFAULT_EMBED_TITLE = "Управление комнатой"
DEFAULT_EMBED_DESC = "Привет, {user_mention}! Это твоя личная комната.\nИспользуй кнопки ниже для её настройки."
DEFAULT_EMBED_COLOR = discord.Color.gold().value
DEFAULT_MENTION_USER = True
DEFAULT_SEND_WELCOME = True


def render_template(template: str, member: discord.Member, mention: bool = True) -> str:
    """Подставляет плейсхолдеры в шаблон текста.

    Поддерживаемые плейсхолдеры:
    - {user} — display name пользователя
    - {user_mention} — упоминание @user (или display_name если mention=False)
    - {server} — имя сервера
    """
    return template.format(
        user=member.display_name,
        user_mention=member.mention if mention else member.display_name,
        server=member.guild.name,
    )


class DynamicVoice(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.locks = defaultdict(asyncio.Lock)
        # {guild_id: {master_channel_id: config_dict}}
        # config_dict содержит: category_id, channel_name_template, embed_title,
        # embed_description, embed_color, mention_user, send_welcome
        self.configs: dict[int, dict[int, dict]] = defaultdict(dict)
        self.dynamic_channels: set[int] = set()

    async def cog_load(self):
        await self.sync_cache()

    async def sync_cache(self):
        try:
            configs = await db.get_all_voice_configs()
            self.configs.clear()
            for cfg in configs:
                self.configs[cfg['guild_id']][cfg['master_channel_id']] = {
                    'category_id': cfg['category_id'],
                    'channel_name_template': cfg.get('channel_name_template'),
                    'embed_title': cfg.get('embed_title'),
                    'embed_description': cfg.get('embed_description'),
                    'embed_color': cfg.get('embed_color'),
                    'mention_user': cfg.get('mention_user'),
                    'send_welcome': cfg.get('send_welcome'),
                }

            channels = await db.get_all_dynamic_channels()
            self.dynamic_channels = {ch['channel_id'] for ch in channels}
            logger.info(f"Dynamic Voice: loaded {len(configs)} master channels, {len(self.dynamic_channels)} dynamic channels.")
        except Exception as e:
            logger.error(f"Error loading Dynamic Voice cache: {e}")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        if member.bot:
            return

        guild_id = member.guild.id
        if guild_id not in self.configs or not self.configs[guild_id]:
            if before.channel and before.channel.id in self.dynamic_channels:
                lock = self.locks[guild_id]
                async with lock:
                    await self.check_dynamic_channel_leave(before.channel, member)
            return

        guild_configs = self.configs[guild_id]
        lock = self.locks[guild_id]

        async with lock:
            if after.channel and after.channel.id in guild_configs:
                master_id = after.channel.id
                master_cfg = guild_configs[master_id]
                await self.create_temp_channel(member, master_id, master_cfg)
            
            if before.channel and before.channel.id in self.dynamic_channels:
                await self.check_dynamic_channel_leave(before.channel, member)

    async def _send_error_to_server(self, guild: discord.Guild, member: discord.Member, master_id: int, text: str):
        channels_to_try = []
        if member.voice and member.voice.channel and hasattr(member.voice.channel, "send"):
            channels_to_try.append(member.voice.channel)
        
        master_ch = guild.get_channel(master_id)
        if master_ch and hasattr(master_ch, "send") and master_ch not in channels_to_try:
            channels_to_try.append(master_ch)

        if getattr(guild, "system_channel", None) and hasattr(guild.system_channel, "send") and guild.system_channel not in channels_to_try:
            channels_to_try.append(guild.system_channel)

        for ch in channels_to_try:
            try:
                await ch.send(text)
                return True
            except Exception:
                continue
        return False

    async def create_temp_channel(self, member: discord.Member, master_id: int, master_cfg: dict):
        guild = member.guild
        category_id = master_cfg['category_id']
        category = guild.get_channel(category_id)
        if not category or not (isinstance(category, discord.CategoryChannel) or hasattr(category, "create_voice_channel")):
            logger.warning(f"Category {category_id} not found in guild {guild.id}.")
            return

        # Рендерим имя канала из шаблона (или дефолт)
        name_template = master_cfg.get('channel_name_template') or DEFAULT_CHANNEL_NAME
        mention_user = master_cfg.get('mention_user')
        if mention_user is None:
            mention_user = DEFAULT_MENTION_USER

        try:
            channel_name = render_template(name_template, member, mention=mention_user)
        except (KeyError, ValueError) as e:
            logger.warning(f"Invalid channel name template for master {master_id}: {e}")
            channel_name = render_template(DEFAULT_CHANNEL_NAME, member)

        logger.info(f"Creating channel '{channel_name}' in category {category_id} for {member} (guild {guild.id})")

        try:
            new_channel = await category.create_voice_channel(
                name=channel_name,
                overwrites={
                    member: discord.PermissionOverwrite(manage_channels=True, manage_permissions=True, move_members=True)
                }
            )
            await db.add_dynamic_channel(new_channel.id, guild.id, member.id)
            self.dynamic_channels.add(new_channel.id)
        except discord.Forbidden:
            logger.warning(f"Bot lacks permissions to create voice channel in guild {guild.id} (category {category_id})")
            await self._send_error_to_server(
                guild, member, master_id,
                f"❌ {member.mention}, **не удалось создать приватную комнатку**.\n"
                f"У бота отсутствуют права на создание каналов в этой категории. "
                f"Попросите администратора сервера выдать боту разрешение **'Управление каналами' (Manage Channels)**."
            )
            return
        except Exception as e:
            logger.error(f"Error creating voice channel in category {category_id} for guild {guild.id}: {e}", exc_info=True)
            return

        moved_successfully = False
        last_error = None
        for attempt in range(10):
            try:
                logger.debug(f"Move attempt {attempt+1}/10: moving {member} to channel {new_channel.id} (guild {guild.id})")
                await member.move_to(new_channel)
                    
                def check(m, b, a):
                    return m.id == member.id and a.channel and a.channel.id == new_channel.id
                    
                await self.bot.wait_for('voice_state_update', check=check, timeout=1.0)
                moved_successfully = True
                logger.debug(f"Move confirmed on attempt {attempt+1} for {member} to channel {new_channel.id}")
                break
            except asyncio.TimeoutError:
                last_error = "TimeoutError"
                continue
            except discord.HTTPException as e:
                last_error = f"HTTPException: {e.status} {e.code} - {e.text}"
                logger.warning(f"HTTPException moving {member} to channel {new_channel.id} (guild {guild.id}): {last_error}")
                break
                    
        if not moved_successfully:
            logger.warning(
                f"Failed to move {member} to channel {new_channel.id} (guild {guild.id}) "
                f"after retries. Last error: {last_error}. Deleting channel."
            )
            try:
                await new_channel.delete()
            except discord.HTTPException as e:
                logger.error(f"Failed to delete orphaned channel {new_channel.id}: {e}")
            self.dynamic_channels.discard(new_channel.id)
            await db.remove_dynamic_channel(new_channel.id)

            if last_error and ("403" in str(last_error) or "Forbidden" in str(last_error)):
                await self._send_error_to_server(
                    guild, member, master_id,
                    f"❌ {member.mention}, **не удалось переместить вас в созданную комнатку**.\n"
                    f"У бота отсутствует право **'Перемещение участников' (Move Members)** на сервере. "
                    f"Попросите администратора выдать боту это разрешение."
                )
            else:
                await self._send_error_to_server(
                    guild, member, master_id,
                    f"❌ {member.mention}, **не удалось переместить вас в созданную комнатку**.\n"
                    f"Причина: `{last_error}`."
                )
        else:
            send_welcome = master_cfg.get('send_welcome')
            if send_welcome is None:
                send_welcome = DEFAULT_SEND_WELCOME

            if send_welcome:
                # Send the control panel with customized embed
                from views.ui import UserControlPanel
                try:
                    # Рендерим embed из шаблонов (или дефолт)
                    embed_title_template = master_cfg.get('embed_title') or DEFAULT_EMBED_TITLE
                    embed_desc_template = master_cfg.get('embed_description') or DEFAULT_EMBED_DESC
                    embed_color_value = master_cfg.get('embed_color')
                    if embed_color_value is None:
                        embed_color_value = DEFAULT_EMBED_COLOR

                    try:
                        rendered_title = render_template(embed_title_template, member, mention=mention_user)
                    except (KeyError, ValueError):
                        rendered_title = render_template(DEFAULT_EMBED_TITLE, member)

                    try:
                        rendered_desc = render_template(embed_desc_template, member, mention=mention_user)
                    except (KeyError, ValueError):
                        rendered_desc = render_template(DEFAULT_EMBED_DESC, member)

                    embed = discord.Embed(
                        title=rendered_title,
                        description=rendered_desc,
                        color=discord.Color(embed_color_value)
                    )

                    cfg_s = await db.get_soundscapes_config(guild.id)
                    await new_channel.send(
                        embed=embed,
                        view=UserControlPanel(
                            soundscapes_enabled=cfg_s.get("soundscapes_enabled", True)
                        )
                    )
                except Exception as e:
                    logger.error(f"Failed to send control panel to {new_channel.id}: {e}")

    async def check_dynamic_channel_leave(self, channel: discord.VoiceChannel, leaving_member: discord.Member):
        non_bot_members = [m for m in channel.members if not m.bot]
        
        if len(non_bot_members) == 0:
            try:
                await channel.delete()
            except discord.NotFound:
                pass
            except discord.HTTPException as e:
                logger.error(f"Failed to delete empty dynamic channel {channel.id}: {e}")
                
            self.dynamic_channels.discard(channel.id)
            await db.remove_dynamic_channel(channel.id)
        else:
            owner_id = await db.get_dynamic_channel_owner(channel.id)
            if owner_id == leaving_member.id:
                new_owner = random.choice(non_bot_members)
                try:
                    await channel.set_permissions(leaving_member, overwrite=None)
                    await channel.set_permissions(new_owner, manage_channels=True, manage_permissions=True, move_members=True)
                    await db.update_dynamic_channel_owner(channel.id, new_owner.id)
                    logger.info(f"Transferred ownership of channel {channel.id} to {new_owner.id}")
                except discord.HTTPException as e:
                    logger.error(f"Failed to transfer ownership of channel {channel.id}: {e}")

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        guild_id = channel.guild.id
        if channel.id in self.dynamic_channels:
            self.dynamic_channels.discard(channel.id)
            await db.remove_dynamic_channel(channel.id)
        
        if guild_id in self.configs:
            for master_id, cfg in list(self.configs[guild_id].items()):
                cat_id = cfg['category_id']
                if channel.id == master_id or channel.id == cat_id:
                    await db.delete_voice_config(master_id)
                    del self.configs[guild_id][master_id]
                    logger.info(f"Master channel or category deleted. Voice config for master {master_id} removed.")

    @commands.Cog.listener()
    async def on_ready(self):
        await asyncio.sleep(5)
        
        for ch_id in list(self.dynamic_channels):
            channel = self.bot.get_channel(ch_id)
            
            if not channel:
                self.dynamic_channels.discard(ch_id)
                await db.remove_dynamic_channel(ch_id)
                continue
                
            if isinstance(channel, discord.VoiceChannel):
                non_bot_members = [m for m in channel.members if not m.bot]
                if len(non_bot_members) == 0:
                    try:
                        await channel.delete()
                    except discord.HTTPException:
                        pass
                    self.dynamic_channels.discard(ch_id)
                    await db.remove_dynamic_channel(ch_id)

    @app_commands.command(name="voice_panel", description="Вызвать панель управления текущей комнатой в чат")
    async def voice_panel(self, interaction: discord.Interaction) -> None:
        user = interaction.user
        guild_id = interaction.guild_id

        # 1. Проверяем, находится ли пользователь в голосовом канале
        voice_state = getattr(user, 'voice', None)
        if not voice_state or not voice_state.channel:
            await interaction.response.send_message("❌ Вы должны находиться в голосовом канале!", ephemeral=True)
            return

        channel = voice_state.channel

        # 2. Проверяем, является ли канал динамическим
        if channel.id not in self.dynamic_channels:
            await interaction.response.send_message("❌ Вы должны находиться в динамическом голосовом канале!", ephemeral=True)
            return

        # 3. Проверяем права управления
        permissions = channel.permissions_for(user)
        if not permissions.manage_channels:
            await interaction.response.send_message("❌ У вас нет прав на управление этой комнатой!", ephemeral=True)
            return

        # 4. Находим владельца канала в БД
        owner_id = await db.get_dynamic_channel_owner(channel.id)
        owner = interaction.guild.get_member(owner_id or 0)
        if not owner:
            owner = user

        # 5. Находим конфигурацию мастер-канала
        master_cfg = None
        if guild_id in self.configs:
            for m_id, cfg in self.configs[guild_id].items():
                if channel.category and cfg['category_id'] == channel.category.id:
                    master_cfg = cfg
                    break

        # 6. Рендерим embed
        embed_title_template = master_cfg.get('embed_title') if master_cfg else None
        embed_desc_template = master_cfg.get('embed_description') if master_cfg else None
        embed_color_value = master_cfg.get('embed_color') if master_cfg else None
        mention_user = master_cfg.get('mention_user') if master_cfg else None

        if embed_title_template is None: embed_title_template = DEFAULT_EMBED_TITLE
        if embed_desc_template is None: embed_desc_template = DEFAULT_EMBED_DESC
        if embed_color_value is None: embed_color_value = DEFAULT_EMBED_COLOR
        if mention_user is None: mention_user = DEFAULT_MENTION_USER

        try:
            rendered_title = render_template(embed_title_template, owner, mention=mention_user)
        except Exception:
            rendered_title = render_template(DEFAULT_EMBED_TITLE, owner)

        try:
            rendered_desc = render_template(embed_desc_template, owner, mention=mention_user)
        except Exception:
            rendered_desc = render_template(DEFAULT_EMBED_DESC, owner)

        embed = discord.Embed(
            title=rendered_title,
            description=rendered_desc,
            color=discord.Color(embed_color_value)
        )

        from views.ui import UserControlPanel
        await interaction.response.send_message("Панель управления отправлена в чат.", ephemeral=True)
        cfg_s = await db.get_soundscapes_config(guild_id)
        await channel.send(
            embed=embed,
            view=UserControlPanel(
                soundscapes_enabled=cfg_s.get("soundscapes_enabled", True)
            )
        )

    @app_commands.command(name="soundscape", description="Выбрать или запустить фоновую атмосферу (дождь, камин, прибой, костёр, капли)")
    @app_commands.describe(preset="Быстрый запуск выбранного фона")
    @app_commands.choices(preset=[
        app_commands.Choice(name="🌧️ Дождь за окном", value="rain"),
        app_commands.Choice(name="🔥 Уютный камин", value="fireplace"),
        app_commands.Choice(name="🌊 Шум прибоя", value="ocean"),
        app_commands.Choice(name="🏕️ Ночной костёр", value="bonfire"),
        app_commands.Choice(name="💧 Лесные капли", value="drops"),
        app_commands.Choice(name="🔇 Выключить атмосферу", value="off"),
    ])
    async def soundscape_cmd(
        self,
        interaction: discord.Interaction,
        preset: Optional[str] = None
    ) -> None:
        """Открыть панель выбора или сразу включить выбранную атмосферу."""
        guild_id = interaction.guild_id
        if not guild_id:
            await interaction.response.send_message("❌ Команда доступна только на серверах.", ephemeral=True)
            return

        cfg = await db.get_soundscapes_config(guild_id)
        if not cfg.get("soundscapes_enabled", True):
            await interaction.response.send_message("❌ Фоновые Атмосферы отключены администратором этого сервера.", ephemeral=True)
            return

        user = interaction.user
        voice_state = getattr(user, 'voice', None)
        if not voice_state or not voice_state.channel:
            await interaction.response.send_message("❌ Вы должны находиться в голосовом канале!", ephemeral=True)
            return

        vc = interaction.guild.voice_client
        if preset:
            if not vc:
                try:
                    vc = await voice_state.channel.connect(self_deaf=True)
                except Exception as e:
                    logger.error("Ошибка подключения к голосовой комнате: %s", e)
                    await interaction.response.send_message("❌ Не удалось подключиться к голосовому каналу.", ephemeral=True)
                    return
            elif vc.channel != voice_state.channel:
                try:
                    await vc.move_to(voice_state.channel)
                except Exception as e:
                    logger.error("Ошибка перемещения в голосовую комнату: %s", e)

            voice_conn = getattr(vc, "_conn", None) or getattr(vc, "_connection", None)
            if voice_conn:
                voice_conn._current_soundscape = preset if preset != "off" else None

            label_map = {
                "off": "выключена 🔇",
                "rain": "🌧️ Дождь за окном",
                "fireplace": "🔥 Уютный камин",
                "ocean": "🌊 Шум прибоя",
                "bonfire": "🏕️ Ночной костёр",
                "drops": "💧 Лесные капли",
            }

            is_music = getattr(vc, "_is_music_playing", False)
            if is_music and preset != "off":
                await interaction.response.send_message(
                    "ℹ️ Фоновые атмосферы работают автономно, когда музыка выключена. Чтобы слушать атмосферу, остановите или поставьте плеер на паузу.",
                    ephemeral=True
                )
                return

            if not is_music:
                if preset != "off":
                    if vc.is_playing() or vc.is_paused():
                        vc.stop()
                    from utils.soundscapes import build_soundscape_ffmpeg_args
                    target_src, before_opts, opts = build_soundscape_ffmpeg_args(
                        music_source=None,
                        soundscape_key=preset,
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

            await interaction.response.send_message(f"🌧️ Атмосфера изменена на: **{label_map.get(preset, preset)}**", ephemeral=True)
            return

        current_scape = None
        if vc:
            voice_conn = getattr(vc, "_conn", None) or getattr(vc, "_connection", None)
            if voice_conn:
                current_scape = getattr(voice_conn, "_current_soundscape", None)

        from views.ui import SoundscapeSelectView
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

    @app_commands.command(name="volume", description="Изменить громкость активного плеера бота (1-100%)")
    @app_commands.describe(level="Громкость от 1 до 100")
    async def volume(
        self,
        interaction: discord.Interaction,
        level: app_commands.Range[int, 1, 100],
    ) -> None:
        """Изменить громкость активного плеера бота."""
        guild_id = interaction.guild_id
        if not guild_id:
            await interaction.response.send_message("❌ Команда доступна только на серверах.", ephemeral=True)
            return

        # 1. Проверяем, подключен ли бот к голосовому каналу
        if not interaction.guild or not interaction.guild.voice_client:
            await interaction.response.send_message("❌ Бот не подключен к голосовому каналу на этом сервере.", ephemeral=True)
            return

        # 2. Определяем активный плеер
        active_cog = None
        cog_name = None

        # Проверяем Yandex Music
        ym_cog = self.bot.get_cog("YandexMusic")
        if ym_cog and hasattr(ym_cog, "_voice_clients") and ym_cog._voice_clients.get(guild_id):
            active_cog = ym_cog
            cog_name = "YandexMusic"

        # Проверяем Spotify Music
        if not active_cog:
            spotify_cog = self.bot.get_cog("SpotifyMusic")
            if spotify_cog and hasattr(spotify_cog, "get_state"):
                try:
                    state = spotify_cog.get_state(guild_id)
                    if state and state.queue:
                        active_cog = spotify_cog
                        cog_name = "SpotifyMusic"
                except Exception:
                    pass

        # Проверяем RuTube Music
        if not active_cog:
            rutube_cog = self.bot.get_cog("RutubeMusic")
            if rutube_cog and hasattr(rutube_cog, "get_state"):
                try:
                    state = rutube_cog.get_state(guild_id)
                    if state and state.queue:
                        active_cog = rutube_cog
                        cog_name = "RutubeMusic"
                except Exception:
                    pass

        # Проверяем Lofi Radio
        if not active_cog:
            lofi_cog = self.bot.get_cog("LofiRadio")
            if lofi_cog and hasattr(lofi_cog, "_voice_clients") and lofi_cog._voice_clients.get(guild_id):
                active_cog = lofi_cog
                cog_name = "LofiRadio"

        if not active_cog:
            await interaction.response.send_message("❌ В данный момент музыка не воспроизводится ни через один плеер бота.", ephemeral=True)
            return

        # 3. Делегируем вызов change_volume
        try:
            await active_cog.change_volume(interaction, level)
        except Exception as e:
            logger.error("Ошибка при изменении громкости через %s: %s", cog_name, e, exc_info=True)
            if not interaction.response.is_done():
                await interaction.response.send_message(f"❌ Произошла ошибка при изменении громкости: {e}", ephemeral=True)
            else:
                try:
                    await interaction.followup.send(f"❌ Произошла ошибка при изменении громкости: {e}", ephemeral=True)
                except Exception:
                    pass
            return

        # 4. Чтобы избежать зависания статуса "thinking..." у плееров, которые только вызывают defer()
        # (YandexMusic, SpotifyMusic, RutubeMusic), отправляем подтверждение.
        if cog_name in ["YandexMusic", "SpotifyMusic", "RutubeMusic"]:
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(f"🔊 Громкость {cog_name} установлена на {level}%", ephemeral=True)
                else:
                    await interaction.response.send_message(f"🔊 Громкость {cog_name} установлена на {level}%", ephemeral=True)
            except Exception:
                pass

async def setup(bot: commands.Bot):
    await bot.add_cog(DynamicVoice(bot))
