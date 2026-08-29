import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
DEFAULT_BOT_NICKNAME = os.getenv("DEFAULT_BOT_NICKNAME", "Dynamic Voice")
DISCORD_LOG_WEBHOOK_URL = os.getenv("DISCORD_LOG_WEBHOOK_URL")

# Донаты и поддержка проекта
DONATION_BOOSTY_URL = os.getenv("DONATION_BOOSTY_URL")
DONATION_ALERTS_URL = os.getenv("DONATION_ALERTS_URL")

# Feature flags
ENABLE_LOFI_RADIO: bool = os.getenv("ENABLE_LOFI_RADIO", "false").lower() == "true"
ENABLE_YANDEX_MUSIC: bool = os.getenv("ENABLE_YANDEX_MUSIC", "false").lower() == "true"
ENABLE_RUTUBE_MUSIC: bool = os.getenv("ENABLE_RUTUBE_MUSIC", "true").lower() == "true"
ENABLE_SPOTIFY: bool = os.getenv("ENABLE_SPOTIFY", "false").lower() == "true"
ENABLE_BLEND: bool = os.getenv("ENABLE_BLEND", "true").lower() == "true"

SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
YTDLP_PROXY = os.getenv("YTDLP_PROXY")
SPOTIFY_PROXY = os.getenv("SPOTIFY_PROXY", YTDLP_PROXY)
STREAM_PROXY = os.getenv("STREAM_PROXY")

VK_COOKIE = os.getenv("VK_COOKIE", "")
VK_USER_TOKEN = os.getenv("VK_USER_TOKEN", "")

spotify_providers_raw = os.getenv("SPOTIFY_SEARCH_PROVIDERS", "youtube,soundcloud")
SPOTIFY_SEARCH_PROVIDERS = [p.strip().lower() for p in spotify_providers_raw.split(",") if p.strip()]

YM_ENCRYPTION_KEY = os.getenv("YM_ENCRYPTION_KEY")
YM_CLIENT_ID = os.getenv("YM_CLIENT_ID", "23cabbbdc6cd418abb4b39c32c41195d")
if ENABLE_YANDEX_MUSIC and not YM_ENCRYPTION_KEY:
    import sys
    print("❌ CRITICAL ERROR: YM_ENCRYPTION_KEY is not set in .env!", file=sys.stderr)
    print("Yandex Music encryption is enabled, but no key is provided.", file=sys.stderr)
    print("Please generate a key with command: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"", file=sys.stderr)
    sys.exit(1)
