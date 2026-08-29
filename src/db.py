import asyncpg
import logging
from contextlib import asynccontextmanager
import config

logger = logging.getLogger(__name__)

pool = None

async def init_db_pool():
    global pool
    pool = await asyncpg.create_pool(config.DATABASE_URL, min_size=2, max_size=10)
    logger.info("Database pool initialized.")

async def close_db_pool():
    if pool:
        await pool.close()
        logger.info("Database pool closed.")

@asynccontextmanager
async def db_connection():
    if not pool:
        raise Exception("DB pool is not initialized.")
    async with pool.acquire() as conn:
        yield conn

async def add_voice_config(guild_id: int, master_channel_id: int, category_id: int) -> None:
    async with db_connection() as conn:
        await conn.execute(
            """
            INSERT INTO voice_config (guild_id, master_channel_id, category_id)
            VALUES ($1, $2, $3)
            ON CONFLICT (master_channel_id) DO UPDATE 
            SET category_id = EXCLUDED.category_id,
                updated_at = NOW()
            """,
            guild_id, master_channel_id, category_id
        )

async def delete_voice_config(master_channel_id: int) -> bool:
    async with db_connection() as conn:
        res = await conn.execute("DELETE FROM voice_config WHERE master_channel_id = $1", master_channel_id)
        return res != "DELETE 0"

async def get_all_voice_configs() -> list[dict]:
    async with db_connection() as conn:
        rows = await conn.fetch(
            """
            SELECT guild_id, master_channel_id, category_id,
                   channel_name_template, embed_title, embed_description,
                   embed_color, mention_user, send_welcome
            FROM voice_config
            """
        )
        return [dict(r) for r in rows]


async def get_voice_customization(master_channel_id: int) -> dict | None:
    """Получить настройки кастомизации для конкретного мастер-канала."""
    async with db_connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT channel_name_template, embed_title, embed_description,
                   embed_color, mention_user, send_welcome
            FROM voice_config
            WHERE master_channel_id = $1
            """,
            master_channel_id
        )
        return dict(row) if row else None


async def update_voice_customization(
    master_channel_id: int,
    channel_name_template: str | None = None,
    embed_title: str | None = None,
    embed_description: str | None = None,
    embed_color: int | None = None,
    mention_user: bool | None = None,
    send_welcome: bool | None = None,
) -> None:
    """Обновить настройки кастомизации для мастер-канала."""
    async with db_connection() as conn:
        await conn.execute(
            """
            UPDATE voice_config
            SET channel_name_template = $1,
                embed_title = $2,
                embed_description = $3,
                embed_color = $4,
                mention_user = $5,
                send_welcome = $6,
                updated_at = NOW()
            WHERE master_channel_id = $7
            """,
            channel_name_template, embed_title, embed_description,
            embed_color, mention_user, send_welcome, master_channel_id
        )


async def reset_voice_customization(master_channel_id: int) -> None:
    """Сбросить все настройки кастомизации к дефолтам (NULL)."""
    async with db_connection() as conn:
        await conn.execute(
            """
            UPDATE voice_config
            SET channel_name_template = NULL,
                embed_title = NULL,
                embed_description = NULL,
                embed_color = NULL,
                mention_user = NULL,
                send_welcome = NULL,
                updated_at = NOW()
            WHERE master_channel_id = $1
            """,
            master_channel_id
        )


async def add_dynamic_channel(channel_id: int, guild_id: int, owner_id: int) -> None:
    async with db_connection() as conn:
        await conn.execute(
            """
            INSERT INTO dynamic_channels (channel_id, guild_id, owner_id)
            VALUES ($1, $2, $3)
            ON CONFLICT DO NOTHING
            """,
            channel_id, guild_id, owner_id
        )

async def remove_dynamic_channel(channel_id: int) -> None:
    async with db_connection() as conn:
        await conn.execute("DELETE FROM dynamic_channels WHERE channel_id = $1", channel_id)

async def get_all_dynamic_channels() -> list[dict]:
    async with db_connection() as conn:
        rows = await conn.fetch("SELECT channel_id FROM dynamic_channels")
        return [dict(r) for r in rows]

async def get_dynamic_channel_owner(channel_id: int) -> int | None:
    async with db_connection() as conn:
        row = await conn.fetchrow("SELECT owner_id FROM dynamic_channels WHERE channel_id = $1", channel_id)
        return row['owner_id'] if row else None

async def update_dynamic_channel_owner(channel_id: int, new_owner_id: int) -> None:
    async with db_connection() as conn:
        await conn.execute("UPDATE dynamic_channels SET owner_id = $1 WHERE channel_id = $2", new_owner_id, channel_id)


async def get_ym_config(guild_id: int) -> dict | None:
    """Получить настройки Яндекс.Музыки для гильдии."""
    async with db_connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT token, session_id, session_id2, username
            FROM yandex_music_config
            WHERE guild_id = $1
            """,
            guild_id
        )
        return dict(row) if row else None


async def save_ym_config(
    guild_id: int,
    token: str,
    session_id: str | None = None,
    session_id2: str | None = None,
    username: str | None = None,
) -> None:
    """Сохранить или обновить токен и сессию Яндекс.Музыки для гильдии."""
    async with db_connection() as conn:
        await conn.execute(
            """
            INSERT INTO yandex_music_config (guild_id, token, session_id, session_id2, username, updated_at)
            VALUES ($1, $2, $3, $4, $5, NOW())
            ON CONFLICT (guild_id) DO UPDATE
            SET token = EXCLUDED.token,
                session_id = EXCLUDED.session_id,
                session_id2 = EXCLUDED.session_id2,
                username = EXCLUDED.username,
                updated_at = NOW()
            """,
            guild_id, token, session_id, session_id2, username
        )


async def delete_ym_config(guild_id: int) -> bool:
    """Удалить настройки Яндекс.Музыки для гильдии."""
    async with db_connection() as conn:
        res = await conn.execute(
            "DELETE FROM yandex_music_config WHERE guild_id = $1",
            guild_id
        )
        return res != "DELETE 0"


async def get_ym_settings(guild_id: int) -> dict | None:
    """Получить настройки Яндекс.Музыки для гильдии."""
    async with db_connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT keep_alive, logout_on_disconnect, like_mode, control_mode, dj_role_ids, last_channel_id, volume
            FROM ym_settings
            WHERE guild_id = $1
            """,
            guild_id
        )
        if not row:
            # Возвращаем дефолтные настройки, если записи еще нет
            return {
                "keep_alive": False,
                "logout_on_disconnect": True,
                "like_mode": "owner_only",
                "control_mode": "everyone",
                "dj_role_ids": [],
                "last_channel_id": None,
                "volume": 0.5
            }
        
        data = dict(row)
        # Парсим CSV в список int
        data["dj_role_ids"] = [int(r) for r in data["dj_role_ids"].split(",") if r.strip()] if data.get("dj_role_ids") else []
        return data

async def update_ym_settings(
    guild_id: int,
    keep_alive: bool | None = None,
    logout_on_disconnect: bool | None = None,
    like_mode: str | None = None,
    control_mode: str | None = None,
    dj_role_ids: list[int] | None = None,
) -> None:
    """Обновить настройки Яндекс.Музыки для гильдии."""
    dj_role_str = ",".join(str(r) for r in dj_role_ids) if dj_role_ids is not None else None
    
    async with db_connection() as conn:
        await conn.execute(
            """
            INSERT INTO ym_settings (guild_id, keep_alive, logout_on_disconnect, like_mode, control_mode, dj_role_ids, updated_at)
            VALUES ($1, COALESCE($2, FALSE), COALESCE($3, TRUE), COALESCE($4, 'owner_only'), COALESCE($5, 'everyone'), $6, NOW())
            ON CONFLICT (guild_id) DO UPDATE
            SET keep_alive = COALESCE($2, ym_settings.keep_alive),
                logout_on_disconnect = COALESCE($3, ym_settings.logout_on_disconnect),
                like_mode = COALESCE($4, ym_settings.like_mode),
                control_mode = COALESCE($5, ym_settings.control_mode),
                dj_role_ids = COALESCE($6, ym_settings.dj_role_ids),
                updated_at = NOW()
            """,
            guild_id, keep_alive, logout_on_disconnect, like_mode, control_mode, dj_role_str
        )

async def update_ym_last_channel(guild_id: int, channel_id: int | None) -> None:
    """Обновить ID последнего канала Яндекс.Музыки."""
    async with db_connection() as conn:
        # Сначала убедимся, что запись существует
        await conn.execute(
            """
            INSERT INTO ym_settings (guild_id, last_channel_id, updated_at)
            VALUES ($1, $2, NOW())
            ON CONFLICT (guild_id) DO UPDATE
            SET last_channel_id = $2,
                updated_at = NOW()
            """,
            guild_id, channel_id
        )

async def update_ym_volume(guild_id: int, volume: float) -> None:
    """Обновить громкость Яндекс.Музыки для гильдии."""
    async with db_connection() as conn:
        await conn.execute(
            """
            INSERT INTO ym_settings (guild_id, volume, updated_at)
            VALUES ($1, $2, NOW())
            ON CONFLICT (guild_id) DO UPDATE
            SET volume = $2,
                updated_at = NOW()
            """,
            guild_id, volume
        )

async def get_all_ym_configs_to_restore() -> list[dict]:
    """Получить все настройки Яндекс.Музыки, требующие автоподключения."""
    async with db_connection() as conn:
        rows = await conn.fetch(
            """
            SELECT guild_id, last_channel_id
            FROM ym_settings
            WHERE keep_alive = TRUE AND last_channel_id IS NOT NULL
            """
        )
        return [dict(r) for r in rows]

async def get_lofi_config(guild_id: int) -> dict | None:
    """Получить настройки Lofi Radio для гильдии."""
    async with db_connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT keep_alive, control_mode, dj_role_ids, last_channel_id, last_station_name, volume
            FROM lofi_config
            WHERE guild_id = $1
            """,
            guild_id
        )
        if not row:
            return {
                "keep_alive": False,
                "control_mode": "everyone",
                "dj_role_ids": [],
                "last_channel_id": None,
                "last_station_name": None,
                "volume": 0.5
            }
        
        data = dict(row)
        data["dj_role_ids"] = [int(r) for r in data["dj_role_ids"].split(",") if r.strip()] if data.get("dj_role_ids") else []
        return data

async def update_lofi_config(
    guild_id: int,
    keep_alive: bool | None = None,
    control_mode: str | None = None,
    dj_role_ids: list[int] | None = None,
) -> None:
    """Обновить настройки Lofi Radio для гильдии."""
    dj_role_str = ",".join(str(r) for r in dj_role_ids) if dj_role_ids is not None else None
    
    async with db_connection() as conn:
        await conn.execute(
            """
            INSERT INTO lofi_config (guild_id, keep_alive, control_mode, dj_role_ids, updated_at)
            VALUES ($1, COALESCE($2, FALSE), COALESCE($3, 'everyone'), $4, NOW())
            ON CONFLICT (guild_id) DO UPDATE
            SET keep_alive = COALESCE($2, lofi_config.keep_alive),
                control_mode = COALESCE($3, lofi_config.control_mode),
                dj_role_ids = COALESCE($4, lofi_config.dj_role_ids),
                updated_at = NOW()
            """,
            guild_id, keep_alive, control_mode, dj_role_str
        )

async def update_lofi_last_channel(guild_id: int, channel_id: int | None) -> None:
    """Обновить ID последнего канала Lofi Radio."""
    async with db_connection() as conn:
        await conn.execute(
            """
            INSERT INTO lofi_config (guild_id, last_channel_id, updated_at)
            VALUES ($1, $2, NOW())
            ON CONFLICT (guild_id) DO UPDATE
            SET last_channel_id = $2,
                updated_at = NOW()
            """,
            guild_id, channel_id
        )

async def get_all_lofi_configs_to_restore() -> list[dict]:
    """Получить все настройки Lofi Radio, требующие автоподключения."""
    async with db_connection() as conn:
        rows = await conn.fetch(
            """
            SELECT guild_id, last_channel_id, last_station_name
            FROM lofi_config
            WHERE keep_alive = TRUE AND last_channel_id IS NOT NULL
            """
        )
        return [dict(r) for r in rows]

async def update_lofi_last_station(guild_id: int, station_name: str | None) -> None:
    """Обновить имя последней станции Lofi Radio."""
    async with db_connection() as conn:
        await conn.execute(
            """
            INSERT INTO lofi_config (guild_id, last_station_name, updated_at)
            VALUES ($1, $2, NOW())
            ON CONFLICT (guild_id) DO UPDATE
            SET last_station_name = $2,
                updated_at = NOW()
            """,
            guild_id, station_name
        )

async def update_lofi_volume(guild_id: int, volume: float) -> None:
    """Обновить громкость Lofi Radio для гильдии."""
    async with db_connection() as conn:
        await conn.execute(
            """
            INSERT INTO lofi_config (guild_id, volume, updated_at)
            VALUES ($1, $2, NOW())
            ON CONFLICT (guild_id) DO UPDATE
            SET volume = $2,
                updated_at = NOW()
            """,
            guild_id, volume
        )


# ──────────────────────────────────────────────
# Кастомные станции Lofi Radio
# ──────────────────────────────────────────────

async def get_lofi_custom_stations(guild_id: int) -> list[dict]:
    """Получить список кастомных станций для гильдии."""
    async with db_connection() as conn:
        rows = await conn.fetch(
            """
            SELECT name, url, emoji, genre
            FROM lofi_custom_stations
            WHERE guild_id = $1
            ORDER BY added_at
            """,
            guild_id
        )
        return [dict(r) for r in rows]


async def add_lofi_custom_station(
    guild_id: int,
    name: str,
    url: str,
    emoji: str = "🎵",
    genre: str = "Custom",
) -> None:
    """Добавить кастомную станцию для гильдии."""
    async with db_connection() as conn:
        await conn.execute(
            """
            INSERT INTO lofi_custom_stations (guild_id, name, url, emoji, genre, added_at)
            VALUES ($1, $2, $3, $4, $5, NOW())
            ON CONFLICT (guild_id, name) DO UPDATE
            SET url = EXCLUDED.url,
                emoji = EXCLUDED.emoji,
                genre = EXCLUDED.genre,
                added_at = NOW()
            """,
            guild_id, name, url, emoji, genre
        )


async def delete_lofi_custom_station(guild_id: int, name: str) -> bool:
    """Удалить кастомную станцию."""
    async with db_connection() as conn:
        res = await conn.execute(
            "DELETE FROM lofi_custom_stations WHERE guild_id = $1 AND name = $2",
            guild_id, name
        )
        return res != "DELETE 0"


async def delete_all_lofi_custom_stations(guild_id: int) -> None:
    """Удалить все кастомные станции гильдии."""
    async with db_connection() as conn:
        await conn.execute(
            "DELETE FROM lofi_custom_stations WHERE guild_id = $1",
            guild_id
        )


# ──────────────────────────────────────────────
# Скрытые предустановленные станции
# ──────────────────────────────────────────────

async def get_lofi_hidden_stations(guild_id: int) -> list[str]:
    """Получить список скрытых предустановленных станций."""
    async with db_connection() as conn:
        rows = await conn.fetch(
            "SELECT station_name FROM lofi_hidden_stations WHERE guild_id = $1",
            guild_id
        )
        return [r["station_name"] for r in rows]


async def hide_lofi_predefined_station(guild_id: int, station_name: str) -> None:
    """Скрыть предустановленную станцию для гильдии."""
    async with db_connection() as conn:
        await conn.execute(
            """
            INSERT INTO lofi_hidden_stations (guild_id, station_name)
            VALUES ($1, $2)
            ON CONFLICT DO NOTHING
            """,
            guild_id, station_name
        )


async def unhide_all_lofi_stations(guild_id: int) -> None:
    """Восстановить все скрытые предустановленные станции."""
    async with db_connection() as conn:
        await conn.execute(
            "DELETE FROM lofi_hidden_stations WHERE guild_id = $1",
            guild_id
        )


# ──────────────────────────────────────────────
# Настройки и плейлисты RuTube
# ──────────────────────────────────────────────

async def get_rutube_config(guild_id: int) -> dict | None:
    """Получить настройки RuTube для гильдии."""
    async with db_connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT keep_alive, control_mode, dj_role_ids, last_channel_id, default_playlist_id, volume
            FROM rutube_settings
            WHERE guild_id = $1
            """,
            guild_id
        )
        if not row:
            return {
                "keep_alive": False,
                "control_mode": "everyone",
                "dj_role_ids": [],
                "last_channel_id": None,
                "default_playlist_id": None,
                "volume": 0.5
            }
        
        data = dict(row)
        data["dj_role_ids"] = [int(r) for r in data["dj_role_ids"].split(",") if r.strip()] if data.get("dj_role_ids") else []
        return data

async def update_rutube_config(
    guild_id: int,
    keep_alive: bool | None = None,
    control_mode: str | None = None,
    dj_role_ids: list[int] | None = None,
    default_playlist_id: int | None = None,
) -> None:
    """Обновить настройки RuTube для гильдии."""
    dj_role_str = ",".join(str(r) for r in dj_role_ids) if dj_role_ids is not None else None
    
    async with db_connection() as conn:
        # Сначала проверим наличие записи, если нет — вставим дефолт
        await conn.execute(
            """
            INSERT INTO rutube_settings (guild_id, keep_alive, control_mode, dj_role_ids, default_playlist_id, updated_at)
            VALUES ($1, COALESCE($2, FALSE), COALESCE($3, 'everyone'), COALESCE($4, ''), $5, NOW())
            ON CONFLICT (guild_id) DO UPDATE
            SET keep_alive = COALESCE($2, rutube_settings.keep_alive),
                control_mode = COALESCE($3, rutube_settings.control_mode),
                dj_role_ids = COALESCE($4, rutube_settings.dj_role_ids),
                default_playlist_id = COALESCE($5, rutube_settings.default_playlist_id),
                updated_at = NOW()
            """,
            guild_id, keep_alive, control_mode, dj_role_str, default_playlist_id
        )

async def update_rutube_last_channel(guild_id: int, channel_id: int | None) -> None:
    """Обновить ID последнего канала RuTube."""
    async with db_connection() as conn:
        await conn.execute(
            """
            INSERT INTO rutube_settings (guild_id, last_channel_id, updated_at)
            VALUES ($1, $2, NOW())
            ON CONFLICT (guild_id) DO UPDATE
            SET last_channel_id = $2,
                updated_at = NOW()
            """,
            guild_id, channel_id
        )

async def update_rutube_volume(guild_id: int, volume: float) -> None:
    """Обновить громкость RuTube для гильдии."""
    async with db_connection() as conn:
        await conn.execute(
            """
            INSERT INTO rutube_settings (guild_id, volume, updated_at)
            VALUES ($1, $2, NOW())
            ON CONFLICT (guild_id) DO UPDATE
            SET volume = $2,
                updated_at = NOW()
            """,
            guild_id, volume
        )

async def get_all_rutube_configs_to_restore() -> list[dict]:
    """Получить все настройки RuTube, требующие автоподключения."""
    async with db_connection() as conn:
        rows = await conn.fetch(
            """
            SELECT guild_id, last_channel_id, default_playlist_id
            FROM rutube_settings
            WHERE keep_alive = TRUE AND last_channel_id IS NOT NULL
            """
        )
        return [dict(r) for r in rows]

async def get_rutube_playlists(guild_id: int) -> list[dict]:
    """Получить все сохраненные плейлисты RuTube для гильдии."""
    async with db_connection() as conn:
        rows = await conn.fetch(
            """
            SELECT id, name, video_ids, created_at
            FROM rutube_playlists
            WHERE guild_id = $1
            ORDER BY created_at
            """,
            guild_id
        )
        return [dict(r) for r in rows]

async def add_rutube_playlist(guild_id: int, name: str, video_ids: str) -> int:
    """Добавить новый плейлист RuTube для гильдии. Возвращает ID созданной записи."""
    async with db_connection() as conn:
        # Убедимся, что запись в rutube_settings существует, чтобы сработал Foreign Key
        await conn.execute(
            """
            INSERT INTO rutube_settings (guild_id)
            VALUES ($1)
            ON CONFLICT DO NOTHING
            """,
            guild_id
        )
        
        row = await conn.fetchrow(
            """
            INSERT INTO rutube_playlists (guild_id, name, video_ids, created_at)
            VALUES ($1, $2, $3, NOW())
            RETURNING id
            """,
            guild_id, name, video_ids
        )
        return row["id"]

async def delete_rutube_playlist(guild_id: int, playlist_id: int) -> bool:
    """Удалить плейлист RuTube по его ID, проверив guild_id для безопасности."""
    async with db_connection() as conn:
        await conn.execute(
            """
            UPDATE rutube_settings
            SET default_playlist_id = NULL
            WHERE default_playlist_id = $1 AND guild_id = $2
            """,
            playlist_id, guild_id
        )
        res = await conn.execute(
            """
            DELETE FROM rutube_playlists
            WHERE id = $1 AND guild_id = $2
            """,
            playlist_id, guild_id
        )
        return res != "DELETE 0"

async def update_rutube_playlist(guild_id: int, playlist_id: int, name: str, video_ids: str) -> bool:
    """Обновить существующий плейлист RuTube по его ID."""
    async with db_connection() as conn:
        res = await conn.execute(
            """
            UPDATE rutube_playlists
            SET name = $1, video_ids = $2
            WHERE id = $3 AND guild_id = $4
            """,
            name, video_ids, playlist_id, guild_id
        )
        return res != "UPDATE 0"



# ──────────────────────────────────────────────

# Сессии воспроизведения RuTube
# ──────────────────────────────────────────────

async def save_rutube_session(
    guild_id: int,
    queue_video_ids: list[str],
    current_index: int,
    playback_position: int,
    source_playlist_id: int | None,
    is_temporary: bool,
    single_track_mode: bool,
) -> None:
    """Сохранить сессию воспроизведения RuTube в БД."""
    queue_str = ",".join(queue_video_ids)
    async with db_connection() as conn:
        # Сначала убедимся, что запись в rutube_settings существует, чтобы не нарушать FK
        await conn.execute(
            """
            INSERT INTO rutube_settings (guild_id)
            VALUES ($1)
            ON CONFLICT DO NOTHING
            """,
            guild_id
        )
        await conn.execute(
            """
            INSERT INTO rutube_sessions (
                guild_id, queue_video_ids, current_index, playback_position,
                source_playlist_id, is_temporary, single_track_mode, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
            ON CONFLICT (guild_id) DO UPDATE
            SET queue_video_ids = EXCLUDED.queue_video_ids,
                current_index = EXCLUDED.current_index,
                playback_position = EXCLUDED.playback_position,
                source_playlist_id = EXCLUDED.source_playlist_id,
                is_temporary = EXCLUDED.is_temporary,
                single_track_mode = EXCLUDED.single_track_mode,
                updated_at = NOW()
            """,
            guild_id, queue_str, current_index, playback_position,
            source_playlist_id, is_temporary, single_track_mode
        )


async def get_rutube_session(guild_id: int) -> dict | None:
    """Получить сохраненную сессию воспроизведения RuTube."""
    async with db_connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT queue_video_ids, current_index, playback_position,
                   source_playlist_id, is_temporary, single_track_mode
            FROM rutube_sessions
            WHERE guild_id = $1
            """,
            guild_id
        )
        if not row:
            return None
        
        data = dict(row)
        # Парсим CSV обратно в список строк
        data["queue_video_ids"] = [v.strip() for v in data["queue_video_ids"].split(",") if v.strip()]
        return data


async def delete_rutube_session(guild_id: int) -> bool:
    """Удалить сессию воспроизведения RuTube."""
    async with db_connection() as conn:
        res = await conn.execute(
            "DELETE FROM rutube_sessions WHERE guild_id = $1",
            guild_id
        )
        return res != "DELETE 0"


# ──────────────────────────────────────────────
# Настройки и сессии Spotify
# ──────────────────────────────────────────────

async def get_spotify_config(guild_id: int) -> dict:
    """Получить настройки Spotify для гильдии."""
    async with db_connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT keep_alive, control_mode, dj_role_ids, last_channel_id, default_playlist_id, volume
            FROM spotify_settings
            WHERE guild_id = $1
            """,
            guild_id
        )
        if not row:
            return {
                "keep_alive": False,
                "control_mode": "everyone",
                "dj_role_ids": [],
                "last_channel_id": None,
                "default_playlist_id": None,
                "volume": 0.5
            }
        
        data = dict(row)
        data["dj_role_ids"] = [int(r) for r in data["dj_role_ids"].split(",") if r.strip()] if data.get("dj_role_ids") else []
        return data

async def update_spotify_config(
    guild_id: int,
    keep_alive: bool | None = None,
    control_mode: str | None = None,
    dj_role_ids: list[int] | None = None,
    default_playlist_id: int | None = None,
) -> None:
    """Обновить настройки Spotify для гильдии."""
    dj_role_str = ",".join(str(r) for r in dj_role_ids) if dj_role_ids is not None else None
    
    async with db_connection() as conn:
        await conn.execute(
            """
            INSERT INTO spotify_settings (guild_id, keep_alive, control_mode, dj_role_ids, default_playlist_id, updated_at)
            VALUES ($1, COALESCE($2, FALSE), COALESCE($3, 'everyone'), COALESCE($4, ''), $5, NOW())
            ON CONFLICT (guild_id) DO UPDATE
            SET keep_alive = COALESCE($2, spotify_settings.keep_alive),
                control_mode = COALESCE($3, spotify_settings.control_mode),
                dj_role_ids = COALESCE($4, spotify_settings.dj_role_ids),
                default_playlist_id = COALESCE($5, spotify_settings.default_playlist_id),
                updated_at = NOW()
            """,
            guild_id, keep_alive, control_mode, dj_role_str, default_playlist_id
        )

async def update_spotify_last_channel(guild_id: int, channel_id: int | None) -> None:
    """Обновить ID последнего канала Spotify."""
    async with db_connection() as conn:
        await conn.execute(
            """
            INSERT INTO spotify_settings (guild_id, last_channel_id, updated_at)
            VALUES ($1, $2, NOW())
            ON CONFLICT (guild_id) DO UPDATE
            SET last_channel_id = $2,
                updated_at = NOW()
            """,
            guild_id, channel_id
        )

async def update_spotify_volume(guild_id: int, volume: float) -> None:
    """Обновить громкость Spotify для гильдии."""
    async with db_connection() as conn:
        await conn.execute(
            """
            INSERT INTO spotify_settings (guild_id, volume, updated_at)
            VALUES ($1, $2, NOW())
            ON CONFLICT (guild_id) DO UPDATE
            SET volume = $2,
                updated_at = NOW()
            """,
            guild_id, volume
        )

async def get_all_spotify_configs_to_restore() -> list[dict]:
    """Получить все настройки Spotify, требующие автоподключения."""
    async with db_connection() as conn:
        rows = await conn.fetch(
            """
            SELECT guild_id, last_channel_id, default_playlist_id
            FROM spotify_settings
            WHERE keep_alive = TRUE AND last_channel_id IS NOT NULL
            """
        )
        return [dict(r) for r in rows]

async def save_spotify_session(
    guild_id: int,
    queue_json: str,
    current_index: int,
    playback_position: int,
    source_playlist_id: int | None,
    is_temporary: bool,
    single_track_mode: bool,
) -> None:
    """Сохранить сессию воспроизведения Spotify в БД."""
    async with db_connection() as conn:
        await conn.execute(
            """
            INSERT INTO spotify_settings (guild_id)
            VALUES ($1)
            ON CONFLICT DO NOTHING
            """,
            guild_id
        )
        await conn.execute(
            """
            INSERT INTO spotify_sessions (
                guild_id, queue_track_ids, current_index, playback_position,
                source_playlist_id, is_temporary, single_track_mode, updated_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
            ON CONFLICT (guild_id) DO UPDATE
            SET queue_track_ids = EXCLUDED.queue_track_ids,
                current_index = EXCLUDED.current_index,
                playback_position = EXCLUDED.playback_position,
                source_playlist_id = EXCLUDED.source_playlist_id,
                is_temporary = EXCLUDED.is_temporary,
                single_track_mode = EXCLUDED.single_track_mode,
                updated_at = NOW()
            """,
            guild_id, queue_json, current_index, playback_position,
            source_playlist_id, is_temporary, single_track_mode
        )

async def get_spotify_session(guild_id: int) -> dict | None:
    """Получить сохраненную сессию воспроизведения Spotify."""
    async with db_connection() as conn:
        row = await conn.fetchrow(
            """
            SELECT queue_track_ids, current_index, playback_position,
                   source_playlist_id, is_temporary, single_track_mode
            FROM spotify_sessions
            WHERE guild_id = $1
            """,
            guild_id
        )
        if not row:
            return None
        
        data = dict(row)
        return data

async def delete_spotify_session(guild_id: int) -> bool:
    """Удалить сессию воспроизведения Spotify."""
    async with db_connection() as conn:
        res = await conn.execute(
            "DELETE FROM spotify_sessions WHERE guild_id = $1",
            guild_id
        )
        return res != "DELETE 0"

async def get_spotify_playlists(guild_id: int) -> list[dict]:
    """Получить все сохраненные плейлисты Spotify для гильдии."""
    async with db_connection() as conn:
        rows = await conn.fetch(
            """
            SELECT id, name, track_ids, created_at
            FROM spotify_playlists
            WHERE guild_id = $1
            ORDER BY created_at
            """,
            guild_id
        )
        return [dict(r) for r in rows]

async def add_spotify_playlist(guild_id: int, name: str, track_ids: str) -> int:
    """Добавить новый плейлист Spotify для гильдии. Возвращает ID созданной записи."""
    async with db_connection() as conn:
        await conn.execute(
            """
            INSERT INTO spotify_settings (guild_id)
            VALUES ($1)
            ON CONFLICT DO NOTHING
            """,
            guild_id
        )
        row = await conn.fetchrow(
            """
            INSERT INTO spotify_playlists (guild_id, name, track_ids, created_at)
            VALUES ($1, $2, $3, NOW())
            RETURNING id
            """,
            guild_id, name, track_ids
        )
        return row["id"]

async def delete_spotify_playlist(guild_id: int, playlist_id: int) -> bool:
    """Удалить плейлист Spotify по его ID."""
    async with db_connection() as conn:
        await conn.execute(
            """
            UPDATE spotify_settings
            SET default_playlist_id = NULL
            WHERE default_playlist_id = $1 AND guild_id = $2
            """,
            playlist_id, guild_id
        )
        res = await conn.execute(
            """
            DELETE FROM spotify_playlists
            WHERE id = $1 AND guild_id = $2
            """,
            playlist_id, guild_id
        )
        return res != "DELETE 0"

async def update_spotify_playlist(guild_id: int, playlist_id: int, name: str, track_ids: str) -> bool:
    """Обновить существующий плейлист Spotify по его ID."""
    async with db_connection() as conn:
        res = await conn.execute(
            """
            UPDATE spotify_playlists
            SET name = $1, track_ids = $2
            WHERE id = $3 AND guild_id = $4
            """,
            name, track_ids, playlist_id, guild_id
        )
        return res != "UPDATE 0"


async def get_ducking_config(guild_id: int) -> dict:
    """Получить глобальные настройки Smart Ducking для гильдии из voice_config в БД."""
    if not pool:
        return {"ducking_enabled": True, "ducking_level": 0.35}
    try:
        async with db_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT ducking_enabled, ducking_level
                FROM voice_config
                WHERE guild_id = $1
                LIMIT 1
                """,
                guild_id
            )
            if row and row["ducking_enabled"] is not None:
                return {
                    "ducking_enabled": bool(row["ducking_enabled"]),
                    "ducking_level": float(row.get("ducking_level") or 0.35)
                }
    except Exception as e:
        logger.error("Ошибка получения ducking_config из БД для %s: %s", guild_id, e)
    return {"ducking_enabled": True, "ducking_level": 0.35}


async def update_ducking_config(guild_id: int, enabled: bool, level: float = 0.35) -> None:
    """Обновить настройки Smart Ducking для гильдии в voice_config."""
    if not pool:
        return
    try:
        async with db_connection() as conn:
            await conn.execute(
                """
                UPDATE voice_config
                SET ducking_enabled = $2,
                    ducking_level = $3,
                    updated_at = NOW()
                WHERE guild_id = $1
                """,
                guild_id, enabled, level
            )
    except Exception as e:
        logger.error("Ошибка обновления ducking_config в БД для %s: %s", guild_id, e)


async def get_soundscapes_config(guild_id: int) -> dict:
    """Получить глобальные настройки Soundscapes (Фоновые Атмосферы) для гильдии из voice_config в БД."""
    if not pool:
        return {"soundscapes_enabled": True}
    try:
        async with db_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT soundscapes_enabled
                FROM voice_config
                WHERE guild_id = $1
                LIMIT 1
                """,
                guild_id
            )
            if row and row["soundscapes_enabled"] is not None:
                return {
                    "soundscapes_enabled": bool(row["soundscapes_enabled"])
                }
    except Exception as e:
        logger.error("Ошибка получения soundscapes_config из БД для %s: %s", guild_id, e)
    return {"soundscapes_enabled": True}


async def update_soundscapes_config(guild_id: int, enabled: bool) -> None:
    """Обновить настройки Soundscapes (Фоновые Атмосферы) для гильдии в voice_config."""
    if not pool:
        return
    try:
        async with db_connection() as conn:
            await conn.execute(
                """
                UPDATE voice_config
                SET soundscapes_enabled = $2,
                    updated_at = NOW()
                WHERE guild_id = $1
                """,
                guild_id, enabled
            )
    except Exception as e:
        logger.error("Ошибка обновления soundscapes_config в БД для %s: %s", guild_id, e)


# ──────────────────────────────────────────────
# Настройки и токены Smart Blend DJ (Совместная Волна)
# ──────────────────────────────────────────────

async def get_blend_config(guild_id: int) -> dict:
    """Получить глобальные настройки Blend (blend_enabled) для гильдии из voice_config."""
    if not pool:
        return {"blend_enabled": True}
    try:
        async with db_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT blend_enabled
                FROM voice_config
                WHERE guild_id = $1
                LIMIT 1
                """,
                guild_id
            )
            if row and row["blend_enabled"] is not None:
                return {"blend_enabled": bool(row["blend_enabled"])}
    except Exception as e:
        logger.error("Ошибка получения blend_config из БД для %s: %s", guild_id, e)
    return {"blend_enabled": True}


async def update_blend_config(guild_id: int, enabled: bool = True, blend_enabled: bool | None = None) -> None:
    """Обновить настройки Blend (blend_enabled) для гильдии в voice_config."""
    if blend_enabled is not None:
        enabled = blend_enabled
    if not pool:
        return
    try:
        async with db_connection() as conn:
            await conn.execute(
                """
                UPDATE voice_config
                SET blend_enabled = $2,
                    updated_at = NOW()
                WHERE guild_id = $1
                """,
                guild_id, enabled
            )
    except Exception as e:
        logger.error("Ошибка обновления blend_config в БД для %s: %s", guild_id, e)


async def save_blend_user_token(
    user_id: int,
    guild_id: int,
    token: str,
    username: str = None,
    forget_on_disconnect: bool | None = None
) -> None:
    """Сохранить или обновить зашифрованный OAuth токен пользователя."""
    if not pool:
        return
    try:
        try:
            from utils.blend_crypto import encrypt_user_token
        except ModuleNotFoundError:
            from src.utils.blend_crypto import encrypt_user_token
        encrypted = encrypt_user_token(user_id, token)
        async with db_connection() as conn:
            await conn.execute(
                """
                INSERT INTO blend_user_tokens (user_id, guild_id, oauth_token, username, is_active, forget_on_disconnect, updated_at)
                VALUES ($1, $2, $3, $4, TRUE, $5, NOW())
                ON CONFLICT (user_id, guild_id) DO UPDATE
                SET oauth_token = EXCLUDED.oauth_token,
                    username = EXCLUDED.username,
                    is_active = TRUE,
                    forget_on_disconnect = EXCLUDED.forget_on_disconnect,
                    updated_at = NOW()
                """,
                user_id, guild_id, encrypted, username, forget_on_disconnect
            )
    except Exception as e:
        logger.error("Ошибка сохранения blend_user_token в БД для user %s guild %s: %s", user_id, guild_id, e)


async def get_blend_user_token(user_id: int, guild_id: int) -> dict | None:
    """Получить зашифрованный токен и настройки пользователя."""
    if not pool:
        return None
    try:
        async with db_connection() as conn:
            row = await conn.fetchrow(
                """
                SELECT user_id, guild_id, oauth_token, username, is_active, forget_on_disconnect, updated_at
                FROM blend_user_tokens
                WHERE user_id = $1 AND guild_id = $2
                """,
                user_id, guild_id
            )
            if not row:
                return None
            data = dict(row)
            if data.get("oauth_token"):
                try:
                    from utils.blend_crypto import decrypt_user_token
                except ModuleNotFoundError:
                    from src.utils.blend_crypto import decrypt_user_token
                try:
                    data["decrypted_token"] = decrypt_user_token(user_id, data["oauth_token"])
                except Exception as de:
                    logger.error("Ошибка расшифровки токена для user %s: %s", user_id, de)
                    data["decrypted_token"] = None
            return data
    except Exception as e:
        logger.error("Ошибка получения blend_user_token из БД для user %s guild %s: %s", user_id, guild_id, e)
        return None


async def get_blend_guild_tokens(guild_id: int) -> list[dict]:
    """Получить список всех активных зашифрованных токенов гильдии."""
    if not pool:
        return []
    try:
        async with db_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT user_id, guild_id, oauth_token, username, is_active, forget_on_disconnect, updated_at
                FROM blend_user_tokens
                WHERE guild_id = $1 AND is_active = TRUE
                """,
                guild_id
            )
            result = []
            try:
                from utils.blend_crypto import decrypt_user_token
            except ModuleNotFoundError:
                from src.utils.blend_crypto import decrypt_user_token
            for r in rows:
                d = dict(r)
                try:
                    d["decrypted_token"] = decrypt_user_token(d["user_id"], d["oauth_token"])
                except Exception:
                    d["decrypted_token"] = None
                result.append(d)
            return result
    except Exception as e:
        logger.error("Ошибка получения blend_guild_tokens для гильдии %s: %s", guild_id, e)
        return []


async def delete_blend_user_token(user_id: int, guild_id: int) -> bool:
    """Удалить (unlink) токен пользователя (GDPR / Privacy)."""
    if not pool:
        return False
    try:
        async with db_connection() as conn:
            res = await conn.execute(
                """
                DELETE FROM blend_user_tokens
                WHERE user_id = $1 AND guild_id = $2
                """,
                user_id, guild_id
            )
            return res != "DELETE 0"
    except Exception as e:
        logger.error("Ошибка удаления blend_user_token из БД для user %s guild %s: %s", user_id, guild_id, e)
        return False


async def mark_blend_token_inactive(user_id: int, guild_id: int) -> None:
    """Пометить токен пользователя как неактивный (401 Unauthorized)."""
    if not pool:
        return
    try:
        async with db_connection() as conn:
            await conn.execute(
                """
                UPDATE blend_user_tokens
                SET is_active = FALSE,
                    updated_at = NOW()
                WHERE user_id = $1 AND guild_id = $2
                """,
                user_id, guild_id
            )
    except Exception as e:
        logger.error("Ошибка пометки неактивным blend_user_token в БД для user %s guild %s: %s", user_id, guild_id, e)





