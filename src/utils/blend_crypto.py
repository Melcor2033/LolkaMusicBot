import base64
import hmac
import hashlib
from cryptography.fernet import Fernet
import config


def _derive_user_key(user_id: int) -> bytes:
    """Генерирует уникальный 32-байтовый ключ Fernet для конкретного user_id."""
    master_key = config.YM_ENCRYPTION_KEY or "fallback_dev_secret_key_32bytes_len"
    raw_key = master_key.encode('utf-8')
    message = str(user_id).encode('utf-8')
    derived = hmac.new(raw_key, message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(derived)


def encrypt_user_token(user_id: int, token: str) -> str:
    """Зашифровывает OAuth токен пользователя с изолированным ключом по user_id."""
    if not token:
        return ""
    f = Fernet(_derive_user_key(user_id))
    return f.encrypt(token.encode('utf-8')).decode('utf-8')


def decrypt_user_token(user_id: int, encrypted_token: str) -> str:
    """Расшифровывает OAuth токен пользователя по его user_id."""
    if not encrypted_token:
        return ""
    f = Fernet(_derive_user_key(user_id))
    return f.decrypt(encrypted_token.encode('utf-8')).decode('utf-8')
