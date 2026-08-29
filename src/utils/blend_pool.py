import asyncio
import time
import gc
import logging
from typing import Dict, Tuple, Optional
from yandex_music import ClientAsync

logger = logging.getLogger(__name__)


class YMClientPool:
    """Управляемый пул сессий ClientAsync с LRU-вытеснением и контролем памяти."""

    MAX_ACTIVE_CLIENTS = 100  # Максимум 100 одновременно открытых HTTP-сессий в памяти
    IDLE_TTL = 600            # 10 минут простая до закрытия сессии

    def __init__(self):
        # (user_id, guild_id) -> (ClientAsync, last_used_timestamp)
        self._pool: Dict[Tuple[int, int], Tuple[ClientAsync, float]] = {}
        self._lock = asyncio.Lock()

    async def get_client(self, user_id: int, guild_id: int, token: str) -> Optional[ClientAsync]:
        """Возвращает инициализированный экземпляр ClientAsync из пула или создает новый."""
        if not token:
            return None

        key = (user_id, guild_id)
        now = time.monotonic()

        async with self._lock:
            if key in self._pool:
                client, _ = self._pool[key]
                self._pool[key] = (client, now)
                return client

            if len(self._pool) >= self.MAX_ACTIVE_CLIENTS:
                await self._evict_oldest_unlocked()

        try:
            # Прямое РФ-соединение без заграничного прокси
            client = ClientAsync(token)
            await client.init()
            async with self._lock:
                self._pool[key] = (client, now)
            return client
        except Exception as e:
            logger.error("Ошибка инициализации YMClientPool для user %s guild %s: %s", user_id, guild_id, e)
            if 'client' in locals() and client:
                await self._close_client_session(client)
            return None

    async def _evict_oldest_unlocked(self):
        """Вытесняет самую старую неиспользуемую сессию."""
        if not self._pool:
            return
        oldest_key = min(self._pool.keys(), key=lambda k: self._pool[k][1])
        client, _ = self._pool.pop(oldest_key)
        await self._close_client_session(client)
        gc.collect()

    async def _close_client_session(self, client: ClientAsync):
        """Безопасно закрывает внутренний aiohttp.ClientSession клиента."""
        try:
            if hasattr(client, '_request') and hasattr(client._request, 'session'):
                session = client._request.session
                if session and not session.closed:
                    await session.close()
        except Exception as e:
            logger.warning("Ошибка при закрытии HTTP-сессии YMClientPool: %s", e)

    async def cleanup_idle(self):
        """Фоновый метод очистки простаивающих сессий (старше IDLE_TTL)."""
        async with self._lock:
            now = time.monotonic()
            to_remove = [k for k, (_, last_used) in self._pool.items() if now - last_used > self.IDLE_TTL]
            for key in to_remove:
                client, _ = self._pool.pop(key)
                await self._close_client_session(client)
            if to_remove:
                gc.collect()

    async def close_all(self):
        """Полное закрытие всех клиентов при завершении работы бота."""
        async with self._lock:
            for key, (client, _) in list(self._pool.items()):
                await self._close_client_session(client)
            self._pool.clear()
            gc.collect()


# Глобальный синглтон пула клиентов
ym_client_pool = YMClientPool()
