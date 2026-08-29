from __future__ import annotations

import io
import json
import logging
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Optional, Tuple

from utils.logger_sanitizer import TOKEN_REGEX


class DiscordWebhookHandler(logging.Handler):
    """
    Асинхронный/неблокирующий Handler логгера для отправки ошибок в Discord Webhook.
    
    Особенности:
      - Отправляет только события уровня ERROR и выше.
      - Автоматически санирует токены и секреты.
      - Дедуплицирует повторяющиеся ошибки (cooldown 60 секунд на одинаковый трейсбек/сообщение).
      - При превышении лимита длины (1800 символов) отправляет полный лог файлом-вложением error_log.txt.
      - Использует ThreadPoolExecutor, чтобы не блокировать основной поток asyncio.
    """

    def __init__(
        self,
        webhook_url: str,
        level: int = logging.ERROR,
        cooldown_seconds: float = 60.0,
        max_workers: int = 2,
    ) -> None:
        super().__init__(level=level)
        self.webhook_url = webhook_url
        self.cooldown_seconds = cooldown_seconds
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="DiscordWebhookLogger")
        self._recent_errors: Dict[Tuple[str, str], float] = {}

    def _sanitize(self, text: str) -> str:
        if not text:
            return ""
        return TOKEN_REGEX.sub("[TOKEN_REDACTED]", text)

    def _is_rate_limited(self, key: Tuple[str, str]) -> bool:
        now = time.time()
        # Очистка устаревших записей
        expired_keys = [k for k, timestamp in self._recent_errors.items() if now - timestamp > self.cooldown_seconds]
        for k in expired_keys:
            del self._recent_errors[k]

        if key in self._recent_errors:
            return True
        self._recent_errors[key] = now
        return False

    def emit(self, record: logging.LogRecord) -> None:
        if not self.webhook_url:
            return

        try:
            formatted_msg = self.format(record)
            sanitized_msg = self._sanitize(formatted_msg)
            
            # Формируем уникальный ключ для дедупликации
            dedup_key = (record.name, record.getMessage())
            if self._is_rate_limited(dedup_key):
                return

            # Отправляем в отдельном потоке executor'а
            self._executor.submit(self._send_webhook, record.levelname, record.name, sanitized_msg)
        except Exception:
            self.handleError(record)

    def _send_webhook(self, levelname: str, logger_name: str, message: str) -> None:
        try:
            title = f"🚨 [{levelname}] {logger_name}"
            
            if len(message) <= 1800:
                payload = {
                    "embeds": [
                        {
                            "title": title,
                            "description": f"```python\n{message}\n```",
                            "color": 0xFF0000 if levelname == "CRITICAL" else 0xE74C3C,
                        }
                    ]
                }
                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    self.webhook_url,
                    data=data,
                    headers={"Content-Type": "application/json", "User-Agent": "DynamicVoiceBot-Logger/1.0"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    resp.read()
            else:
                # Если сообщение слишком длинное, отправляем краткое embed + полный файл
                boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
                payload_json = json.dumps({
                    "embeds": [
                        {
                            "title": title,
                            "description": f"⚠️ Лог ошибки превысил лимит длины ({len(message)} символов). Полный трейсбек прикреплен файлом.",
                            "color": 0xFF0000,
                        }
                    ]
                })

                body = io.BytesIO()
                # 1. payload_json part
                body.write(f"--{boundary}\r\n".encode())
                body.write(b'Content-Disposition: form-data; name="payload_json"\r\n')
                body.write(b"Content-Type: application/json\r\n\r\n")
                body.write(payload_json.encode("utf-8"))
                body.write(b"\r\n")

                # 2. file part
                body.write(f"--{boundary}\r\n".encode())
                body.write(b'Content-Disposition: form-data; name="file"; filename="traceback_log.txt"\r\n')
                body.write(b"Content-Type: text/plain; charset=utf-8\r\n\r\n")
                body.write(message.encode("utf-8"))
                body.write(b"\r\n")
                body.write(f"--{boundary}--\r\n".encode())

                req = urllib.request.Request(
                    self.webhook_url,
                    data=body.getvalue(),
                    headers={
                        "Content-Type": f"multipart/form-data; boundary={boundary}",
                        "User-Agent": "DynamicVoiceBot-Logger/1.0",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    resp.read()
        except Exception:
            # Игнорируем сетевые ошибки отправки логов, чтобы не вызывать рекурсию логгера
            pass

    def close(self) -> None:
        self._executor.shutdown(wait=False)
        super().close()
