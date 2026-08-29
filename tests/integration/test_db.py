import pytest
import os
import asyncio
from testcontainers.postgres import PostgresContainer
from alembic.config import Config
from alembic import command
import config
import db

@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for each test case."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

@pytest.fixture(scope="session")
def postgres_container():
    """Spins up a PostgreSQL TestContainer, applies Alembic migrations, and provides the DB URL."""
    with PostgresContainer("postgres:15-alpine") as postgres:
        # testcontainers returns "postgresql+psycopg2://...", we need "postgresql://" for asyncpg/alembic setup
        url = postgres.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
        
        # Override the environment variable so Alembic can pick it up
        os.environ["DATABASE_URL"] = url
        config.DATABASE_URL = url
        
        # Apply Alembic migrations to the test database
        alembic_cfg = Config("alembic.ini")
        alembic_cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(alembic_cfg, "head")
        
        yield url

@pytest.fixture(autouse=True)
async def setup_db(postgres_container):
    """Initializes the connection pool before tests and closes it after, and cleans tables."""
    # We re-initialize the pool
    await db.init_db_pool()
    
    # Truncate tables before test to clear any alembic seed data
    tables = [
        "voice_config", "dynamic_channels", "yandex_music_config", 
        "ym_settings", "lofi_config", "lofi_custom_stations",
        "lofi_hidden_stations", "rutube_settings", "rutube_playlists",
        "rutube_sessions"
    ]
    async with db.db_connection() as conn:
        for table in tables:
            await conn.execute(f"TRUNCATE TABLE {table} CASCADE")
            
    yield
    # Clean tables after each test so tests don't interfere
    async with db.db_connection() as conn:
        for table in tables:
            await conn.execute(f"TRUNCATE TABLE {table} CASCADE")
            
    await db.close_db_pool()


@pytest.mark.asyncio
async def test_voice_config_crud():
    # Test ADD
    await db.add_voice_config(guild_id=10, master_channel_id=100, category_id=1000)
    
    # Test GET ALL
    configs = await db.get_all_voice_configs()
    assert len(configs) == 1
    assert configs[0]["guild_id"] == 10
    assert configs[0]["master_channel_id"] == 100
    assert configs[0]["category_id"] == 1000
    
    # Test GET CUSTOMIZATION (default empty JSONB)
    cust = await db.get_voice_customization(100)
    assert cust is not None
    assert cust["channel_name_template"] is None
    
    # Test UPDATE CUSTOMIZATION
    await db.update_voice_customization(100, channel_name_template="Room {user}")
    cust = await db.get_voice_customization(100)
    assert cust["channel_name_template"] == "Room {user}"
    
    # Test RESET CUSTOMIZATION
    await db.reset_voice_customization(100)
    cust = await db.get_voice_customization(100)
    assert cust["channel_name_template"] is None
    
    # Test DELETE
    success = await db.delete_voice_config(100)
    assert success is True
    
    configs = await db.get_all_voice_configs()
    assert len(configs) == 0


@pytest.mark.asyncio
async def test_dynamic_channels_crud():
    # Need voice config first due to foreign key (if it exists, wait, dynamic_channels doesn't have a FK to voice_config)
    await db.add_dynamic_channel(channel_id=200, guild_id=20, owner_id=2000)
    
    # Get all
    channels = await db.get_all_dynamic_channels()
    assert len(channels) == 1
    assert channels[0]["channel_id"] == 200
    
    # Get owner
    owner = await db.get_dynamic_channel_owner(200)
    assert owner == 2000
    
    # Update owner
    await db.update_dynamic_channel_owner(200, 3000)
    owner = await db.get_dynamic_channel_owner(200)
    assert owner == 3000
    
    # Remove
    await db.remove_dynamic_channel(200)
    channels = await db.get_all_dynamic_channels()
    assert len(channels) == 0


@pytest.mark.asyncio
async def test_yandex_music_crud():
    # Save config
    await db.save_ym_config(guild_id=30, username="test_user", token="test_token")
    
    # Get config
    config_ym = await db.get_ym_config(30)
    assert config_ym is not None
    assert config_ym["username"] == "test_user"
    assert config_ym["token"] == "test_token"
    
    # Get settings (should auto-create default)
    settings = await db.get_ym_settings(30)
    assert settings is not None
    assert settings["dj_role_ids"] == []
    assert settings["keep_alive"] is False
    
    # Update settings
    await db.update_ym_settings(30, dj_role_ids=[300], keep_alive=True)
    settings = await db.get_ym_settings(30)
    assert settings["dj_role_ids"] == [300]
    assert settings["keep_alive"] is True
    
    # Update last channel
    await db.update_ym_last_channel(30, channel_id=3000)
    settings = await db.get_ym_settings(30)
    assert settings["last_channel_id"] == 3000
    
    # Get all to restore
    restores = await db.get_all_ym_configs_to_restore()
    assert len(restores) == 1
    assert restores[0]["guild_id"] == 30
    
    # Delete config
    success = await db.delete_ym_config(30)
    assert success is True
    config_ym = await db.get_ym_config(30)
    assert config_ym is None


@pytest.mark.asyncio
async def test_lofi_crud():
    # Get config (should be auto-created if None, but get_lofi_config doesn't auto-create, update does)
    await db.update_lofi_config(40, keep_alive=True)
    
    cfg = await db.get_lofi_config(40)
    assert cfg is not None
    assert cfg["keep_alive"] is True
    
    await db.update_lofi_last_channel(40, 4000)
    cfg = await db.get_lofi_config(40)
    assert cfg["last_channel_id"] == 4000
    
    restores = await db.get_all_lofi_configs_to_restore()
    assert len(restores) == 1
    
    await db.update_lofi_last_station(40, "synthwave")
    cfg = await db.get_lofi_config(40)
    assert cfg["last_station_name"] == "synthwave"
    
    # Custom stations
    await db.add_lofi_custom_station(40, "my_radio", "http://test.com/stream")
    stations = await db.get_lofi_custom_stations(40)
    assert len(stations) == 1
    assert stations[0]["name"] == "my_radio"
    
    await db.delete_lofi_custom_station(40, "my_radio")
    stations = await db.get_lofi_custom_stations(40)
    assert len(stations) == 0
    
    await db.add_lofi_custom_station(40, "radio1", "url1")
    await db.delete_all_lofi_custom_stations(40)
    assert len(await db.get_lofi_custom_stations(40)) == 0
    
    # Hidden stations
    await db.hide_lofi_predefined_station(40, "lofi_girl")
    hidden = await db.get_lofi_hidden_stations(40)
    assert "lofi_girl" in hidden
    
    await db.unhide_all_lofi_stations(40)
    hidden = await db.get_lofi_hidden_stations(40)
    assert len(hidden) == 0


@pytest.mark.asyncio
async def test_rutube_crud():
    # Config
    await db.update_rutube_config(50, dj_role_ids=[500], keep_alive=True)
    cfg = await db.get_rutube_config(50)
    assert cfg is not None
    assert cfg["dj_role_ids"] == [500]
    assert cfg["keep_alive"] is True
    
    await db.update_rutube_last_channel(50, 6000)
    cfg = await db.get_rutube_config(50)
    assert cfg["last_channel_id"] == 6000
    
    restores = await db.get_all_rutube_configs_to_restore()
    assert len(restores) == 1
    
    # Playlists
    pid = await db.add_rutube_playlist(50, "favorites", "vid1,vid2")
    assert pid > 0
    playlists = await db.get_rutube_playlists(50)
    assert len(playlists) == 1
    assert playlists[0]["name"] == "favorites"
    assert playlists[0]["video_ids"] == "vid1,vid2"
    
    success = await db.delete_rutube_playlist(50, pid)
    assert success is True
    assert len(await db.get_rutube_playlists(50)) == 0
    
    # Sessions
    await db.save_rutube_session(50, ["token_string"], 0, 10, None, True, False)
    sess = await db.get_rutube_session(50)
    assert sess is not None
    assert sess["queue_video_ids"] == ["token_string"]
    
    await db.delete_rutube_session(50)
    sess = await db.get_rutube_session(50)
    assert sess is None
