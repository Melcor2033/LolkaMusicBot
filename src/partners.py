"""Реестр партнёров и спонсоров DynamicVoiceBot.

Модуль содержит конфигурацию всех официальных партнёров и спонсоров проекта
и утилиты для их отображения и использования в ротации статусов.
"""

from __future__ import annotations

from typing import Any
import lolka as discord

# Список всех партнёров и спонсоров проекта (Главный спонсор идет первым!)
PARTNERS: list[dict[str, Any]] = [
    {
        "id": "itelepat",
        "name": "iTelepat | Games",
        "emoji": "👑",
        "badge": "⭐ ГЛАВНЫЙ СПОНСОР ⭐",
        "short_desc": "Показываю вам игры без мата — новости, мемы, моды, конкурсы",
        "full_desc": (
            "👑 **Главный спонсор проекта Dynamic Labs**\n\n"
            "🎮 **iTelepat | Games** — уютное игровое комьюнити и стримы!\n"
            "> *— Показываю вам игры без мата —*\n\n"
            "✨ **На канале вас ждут:**\n"
            "• 🎮 Стримы и прохождения игр без мата\n"
            "• 📰 Игровые новости, эксклюзивные моды и мемы\n"
            "• 🎁 Конкурсы и регулярные розыгрыши\n\n"
            "📌 **Смотрите стримы там, где удобно:**\n"
            "• 📺 [VK Video](https://vkvideo.ru/@itelepatgames)\n"
            "• 🔴 [VK Play Live](https://live.vkvideo.ru/itelepat)\n"
            "• 🟣 [Twitch](https://twitch.tv/itelepat)\n"
            "• ▶️ [YouTube](https://youtube.com/@itelepatgames)\n"
            "• 🌐 [Сервер в Lolka](https://lolka.gg/itelepat)"
        ),
        "invite_url": None,
        "support_url": "https://lolka.gg/itelepat",
        "button_label": "Сервер в Lolka",
        "extra_buttons": [
            {"label": "VK Video", "url": "https://vkvideo.ru/@itelepatgames", "emoji": "📺"},
            {"label": "VK Live", "url": "https://live.vkvideo.ru/itelepat", "emoji": "🔴"},
            {"label": "Twitch", "url": "https://twitch.tv/itelepat", "emoji": "🟣"},
            {"label": "YouTube", "url": "https://youtube.com/@itelepatgames", "emoji": "▶️"},
        ],
        "statuses": [
            (discord.ActivityType.watching, "iTelepat | Games | /partner"),
            (discord.ActivityType.listening, "Стримы без мата с iTelepat"),
            (discord.ActivityType.playing, "Игры с iTelepat | /partner"),
        ]
    },
    {
        "id": "diomyxbot",
        "name": "Diomyx Bot",
        "emoji": "🛡️",
        "badge": "Официальный партнёр",
        "short_desc": "Надёжный бот-модератор и защитник вашего сервера",
        "full_desc": (
            "За уют и качественную музыку в ваших комнатах отвечает **Вольт** 🎧, "
            "а за порядок, модерацию и чистый чат на сервере — наш партнёр **Diomyx Bot** 🛡️!\n\n"
            "✨ **Ключевые возможности Diomyx Bot:**\n"
            "• 🛡️ Защита от спама, рейдов и нежелательных ссылок\n"
            "• 🧹 Удобная автомодерация и гибкая очистка чата\n"
            "• ⚙️ Настройка прав, роли и автоматизация сервера\n\n"
            "Подключите Diomyx Bot на свой сервер для полной безопасности!"
        ),
        "invite_url": "https://lolka.app/bot-authorize?client_id=0fccb19c-97e5-415d-959c-7ba7bad3582d",
        "support_url": "https://lolka.gg/diomyxbot",
        "button_label": "Страница Diomyx Bot",
        "statuses": [
            (discord.ActivityType.watching, "За порядком с Diomyx Bot | /partner"),
            (discord.ActivityType.listening, "Музыку | Защита от Diomyx Bot"),
            (discord.ActivityType.playing, "В патруле с Diomyx Bot | /partner"),
            (discord.ActivityType.watching, "За чистым чатом с Diomyx Bot"),
        ]
    }
]


def get_all_partners() -> list[dict[str, Any]]:
    """Возвращает список всех активных партнёров и спонсоров."""
    return PARTNERS


def get_partner_by_id(partner_id: str) -> dict[str, Any] | None:
    """Находит партнёра по его идентификатору."""
    for partner in PARTNERS:
        if partner["id"] == partner_id:
            return partner
    return None


def get_partner_statuses() -> list[tuple[discord.ActivityType, str]]:
    """Собирает все статусы присутствия от всех партнёров и спонсоров для ротации."""
    statuses: list[tuple[discord.ActivityType, str]] = []
    for partner in PARTNERS:
        statuses.extend(partner.get("statuses", []))
    return statuses
