from __future__ import annotations

import asyncio
import logging
from typing import Optional, Union

import lolka as discord

logger = logging.getLogger(__name__)

VoiceChannelLike = Union[discord.VoiceChannel, discord.StageChannel]
VoiceSource = Union[discord.Member, discord.User, discord.Interaction]


# ==========================================
# 1. Резолвер голосовых каналов
# ==========================================

async def get_user_voice_channel(
    source: VoiceSource,
    *,
    guild: Optional[discord.Guild] = None,
) -> Optional[VoiceChannelLike]:
    """
    Надежный резолвер голосового канала пользователя.

    Поддерживает:
      - Interaction (извлекает interaction.user и interaction.guild)
      - Member (с поиском по member.voice)
      - User (с поиском member в guild)

    Стратегия:
      Tier 1: Чтение member.voice.channel (стандартный кэш).
      Tier 2: Поиск через guild._voice_state_for(user_id) на уровне гильдии.
      Tier 3: Сканирование guild.voice_channels на случай рассинхронизации кэша Gateway.
    """
    if source is None:
        return None

    if isinstance(source, discord.Interaction):
        user: discord.abc.User = source.user
        target_guild = source.guild or guild
    else:
        user = source
        target_guild = getattr(user, "guild", None) or guild

    if target_guild is None:
        return None

    user_id = user.id

    # 1. Получаем объект Member
    if isinstance(user, discord.Member):
        member: Optional[discord.Member] = user
    else:
        member = target_guild.get_member(user_id)

    # Tier 1: Проверка прямого свойства member.voice
    if member and hasattr(member, "voice") and member.voice and member.voice.channel:
        return member.voice.channel

    # Tier 2: Чтение внутреннего voice_state гильдии
    if hasattr(target_guild, "_voice_state_for"):
        try:
            vs = target_guild._voice_state_for(user_id)
            if vs and vs.channel:
                return vs.channel
        except Exception as e:
            logger.debug("[Voice Resolver] _voice_state_for failed: %s", e)

    # Tier 3: Прямой поиск по голосовым каналам гильдии (запасной вариант при рассинхроне)
    try:
        for channel in target_guild.voice_channels:
            if hasattr(channel, "voice_states") and user_id in channel.voice_states:
                logger.info("[Voice Resolver] User %s found in channel %s via voice_states scan.", user_id, channel.id)
                return channel
            if hasattr(channel, "members"):
                if any(m.id == user_id for m in channel.members):
                    logger.info("[Voice Resolver] User %s found in channel %s via members scan.", user_id, channel.id)
                    return channel
    except Exception as e:
        logger.error("[Voice Resolver] Error during voice channels scan: %s", e)

    return None


# ==========================================
# 2. Безопасный defer для взаимодействия Discord
# ==========================================

async def safe_defer(
    interaction: discord.Interaction,
    *,
    ephemeral: bool = False,
    thinking: bool = False,
) -> bool:
    """
    Безопасный defer для взаимодействия Discord.
    Предотвращает падение с 400 Bad Request (Interaction has already been acknowledged).
    Логирует ошибку на WARNING для видимости в продакшен-логах.
    Возвращает True, если defer прошёл успешно, иначе False.
    """
    if interaction is None:
        return False

    response = getattr(interaction, "response", None)
    if response and hasattr(response, "is_done") and not response.is_done():
        try:
            kwargs = {}
            if ephemeral:
                kwargs["ephemeral"] = True
            if thinking:
                kwargs["thinking"] = True
            await response.defer(**kwargs)
            return True
        except Exception as exc:
            logger.warning("[safe_defer] Failed to defer interaction: %s", exc)
    return False


# ==========================================
# 3. Безопасная отправка ответа на взаимодействие
# ==========================================

async def safe_send(
    interaction: discord.Interaction,
    content: Optional[str] = None,
    *,
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
    ephemeral: bool = False,
) -> bool:
    """
    Безопасная отправка сообщения через interaction.
    Автоматически выбирает response.send_message или followup.send.
    Ловит NotFound (404), Forbidden, HTTPException, InteractionResponded.
    Возвращает True если отправлено, False если нет.
    """
    if interaction is None:
        return False

    kwargs: dict = {}
    if content is not None:
        kwargs["content"] = content
    if embed is not None:
        kwargs["embed"] = embed
    if view is not None:
        kwargs["view"] = view
    kwargs["ephemeral"] = ephemeral

    try:
        if interaction.response.is_done():
            await interaction.followup.send(**kwargs)
        else:
            await interaction.response.send_message(**kwargs)
        return True
    except (discord.NotFound, discord.Forbidden,
            discord.HTTPException) as exc:
        logger.warning("[safe_send] Failed to send interaction response: %s", exc)
        return False
    except Exception as exc:
        # InteractionResponded и прочие нестандартные ошибки
        logger.warning("[safe_send] Unexpected error sending response: %s", exc)
        return False


# ==========================================
# 4. Per-guild Lock и безопасное подключение к ГС
# ==========================================

_voice_locks: dict[int, asyncio.Lock] = {}


def get_voice_lock(guild_id: int) -> asyncio.Lock:
    """Возвращает asyncio.Lock для данной гильдии (ленивая инициализация)."""
    return _voice_locks.setdefault(guild_id, asyncio.Lock())


async def safe_voice_connect(
    guild: discord.Guild,
    channel: VoiceChannelLike,
    *,
    self_deaf: bool = True,
    retries: int = 3,
) -> Optional[discord.VoiceClient]:
    """
    Единый отказоустойчивый коннект к голосовому каналу с per-guild Lock.

    - Если бот уже подключен к целевому каналу — мгновенный возврат.
    - Если бот в другом канале — move_to.
    - Если guild.voice_client в broken state — disconnect(force=True) + retry.
    - Обрабатывает ClientException('Already connected').
    - Per-guild asyncio.Lock исключает race condition при параллельных нажатиях.
    """
    async with get_voice_lock(guild.id):
        vc = guild.voice_client

        # Уже подключен к целевому каналу
        if vc and vc.is_connected():
            if vc.channel and vc.channel.id == channel.id:
                return vc
            # Подключен к другому каналу — перемещаемся
            try:
                await vc.move_to(channel)
                return vc
            except Exception as exc:
                logger.warning(
                    "[safe_voice_connect] move_to channel %s failed: %s, will reconnect",
                    channel.id, exc,
                )
                # Не получилось — пробуем полный реконнект ниже

        # Принудительно отключаем зависший клиент
        if guild.voice_client:
            try:
                await guild.voice_client.disconnect(force=True)
            except Exception:
                pass

        # Retry цикл подключения
        last_exc: Optional[Exception] = None
        for attempt in range(retries):
            try:
                return await channel.connect(self_deaf=self_deaf)
            except discord.ClientException as exc:
                # "Already connected to a voice channel" — force disconnect и retry
                logger.warning(
                    "[safe_voice_connect] Attempt %s: ClientException: %s",
                    attempt + 1, exc,
                )
                last_exc = exc
                if guild.voice_client:
                    try:
                        await guild.voice_client.disconnect(force=True)
                    except Exception:
                        pass
                if attempt < retries - 1:
                    await asyncio.sleep(1.0)
            except asyncio.TimeoutError as exc:
                logger.warning(
                    "[safe_voice_connect] Attempt %s: Timeout connecting to %s",
                    attempt + 1, channel.id,
                )
                last_exc = exc
                if attempt < retries - 1:
                    await asyncio.sleep(2.0)
            except Exception as exc:
                logger.warning(
                    "[safe_voice_connect] Attempt %s: %s",
                    attempt + 1, exc,
                )
                last_exc = exc
                if guild.voice_client:
                    try:
                        await guild.voice_client.disconnect(force=True)
                    except Exception:
                        pass
                if attempt < retries - 1:
                    await asyncio.sleep(2.0)

        logger.error(
            "[safe_voice_connect] All %s attempts to connect to channel %s exhausted. Last error: %s",
            retries, channel.id, last_exc,
        )
        return None




