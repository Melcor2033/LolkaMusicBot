"""UI-компоненты для отображения партнёров и спонсоров DynamicVoiceBot."""

from __future__ import annotations

from typing import Any
import lolka as discord
from partners import get_all_partners, get_partner_by_id


def build_partner_embed(partner: dict[str, Any]) -> discord.Embed:
    """Создаёт стильный Embed для карточки партнёра или спонсора."""
    emoji = partner.get("emoji", "🤝")
    name = partner.get("name", "Партнёр")
    badge = partner.get("badge")
    full_desc = partner.get("full_desc", partner.get("short_desc", ""))

    title_text = f"{emoji} {name}"

    # Для главных спонсоров подсвечиваем золотым цветом, для партнеров — зелёным
    embed_color = discord.Color.gold() if badge and "СПОНСОР" in badge else discord.Color.brand_green()

    embed = discord.Embed(
        title=title_text,
        description=full_desc,
        color=embed_color,
    )
    
    if partner.get("avatar_url"):
        embed.set_thumbnail(url=partner["avatar_url"])

    footer_tag = "Партнёры и спонсоры" if badge else "Надежные партнёры"
    embed.set_footer(text=f"Dynamic Labs × {name} | {footer_tag}")
    return embed


class PartnerSelectDropdown(discord.ui.Select):
    """Выпадающий список для выбора партнёра, если их несколько."""

    def __init__(self, partners: list[dict[str, Any]], current_id: str):
        options = [
            discord.SelectOption(
                label=f"{p.get('emoji', '🤝')} {p['name']}"[:100],
                value=p["id"],
                description=p.get("short_desc", "")[:100],
                default=(p["id"] == current_id),
            )
            for p in partners[:25]
        ]
        super().__init__(
            placeholder="Выберите партнёра или спонсора для просмотра...",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        selected_id = self.values[0]
        partner = get_partner_by_id(selected_id)
        if not partner:
            await interaction.response.send_message("❌ Запись не найдена.", ephemeral=True)
            return

        view = PartnersView(current_partner_id=selected_id)
        embed = build_partner_embed(partner)
        await interaction.response.edit_message(embed=embed, view=view)


class PartnersView(discord.ui.View):
    """Интерактивный View с кнопками и селектором партнёров."""

    def __init__(self, current_partner_id: str | None = None) -> None:
        super().__init__(timeout=300)
        partners = get_all_partners()

        if not partners:
            return

        # Если текущий не указан, берем первого (Главного спонсора!)
        if not current_partner_id or not get_partner_by_id(current_partner_id):
            current_partner_id = partners[0]["id"]

        current_partner = get_partner_by_id(current_partner_id) or partners[0]

        # 1. Если партнёров больше одного, добавляем SelectMenu
        if len(partners) > 1:
            self.add_item(PartnerSelectDropdown(partners, current_partner_id))

        # 2. Добавляем URL-кнопку установки для текущего партнёра (если есть)
        if current_partner.get("invite_url"):
            self.add_item(
                discord.ui.Button(
                    label=f"Установить {current_partner['name']}",
                    url=current_partner["invite_url"],
                    emoji=current_partner.get("emoji", "🛡️"),
                    row=1,
                )
            )

        # 3. Добавляем URL-кнопку страницы / сервера поддержки
        if current_partner.get("support_url"):
            btn_label = current_partner.get("button_label") or "Перейти на сервер / страницу"
            self.add_item(
                discord.ui.Button(
                    label=btn_label,
                    url=current_partner["support_url"],
                    emoji=current_partner.get("emoji", "🌐"),
                    row=1,
                )
            )

        # 4. Дополнительные кнопки (например, ссылки на стрим-площадки)
        extra_buttons = current_partner.get("extra_buttons", [])
        for idx, btn in enumerate(extra_buttons):
            row_num = 1 if (idx < 3 and not current_partner.get("invite_url")) else 2
            self.add_item(
                discord.ui.Button(
                    label=btn["label"],
                    url=btn["url"],
                    emoji=btn.get("emoji", "🔗"),
                    row=row_num,
                )
            )
