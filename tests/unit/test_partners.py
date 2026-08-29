"""Юнит-тесты для модуля партнёров и спонсоров (src/partners.py и src/views/partner_views.py)."""

import pytest
import lolka as discord
from partners import (
    PARTNERS,
    get_all_partners,
    get_partner_by_id,
    get_partner_statuses
)
from views.partner_views import build_partner_embed, PartnersView


def test_get_all_partners():
    partners = get_all_partners()
    assert isinstance(partners, list)
    assert len(partners) >= 2
    
    # Главный спонсор iTelepat идет первым!
    top_sponsor = partners[0]
    assert top_sponsor["id"] == "itelepat"
    assert "СПОНСОР" in top_sponsor.get("badge", "")
    assert len(top_sponsor.get("extra_buttons", [])) >= 4


def test_get_partner_by_id():
    itelepat = get_partner_by_id("itelepat")
    assert itelepat is not None
    assert itelepat["name"] == "iTelepat | Games"

    diomyx = get_partner_by_id("diomyxbot")
    assert diomyx is not None
    assert diomyx["name"] == "Diomyx Bot"

    assert get_partner_by_id("non_existent_id") is None


def test_get_partner_statuses():
    statuses = get_partner_statuses()
    assert isinstance(statuses, list)
    assert len(statuses) > 0
    # Проверяем наличие статусов от iTelepat и Diomyx
    has_itelepat = any("iTelepat" in text for _, text in statuses)
    has_diomyx = any("Diomyx" in text for _, text in statuses)
    assert has_itelepat
    assert has_diomyx


def test_build_partner_embed():
    itelepat = get_partner_by_id("itelepat")
    embed = build_partner_embed(itelepat)
    
    assert isinstance(embed, discord.Embed)
    assert "iTelepat" in embed.title
    assert embed.color == discord.Color.gold()
    assert "Dynamic Labs" in embed.footer.text

    diomyx = get_partner_by_id("diomyxbot")
    embed_diomyx = build_partner_embed(diomyx)
    assert embed_diomyx.color == discord.Color.brand_green()


def test_partners_view_initialization():
    view = PartnersView(current_partner_id="itelepat")
    assert len(view.children) > 0
    
    # С выпадающим списком при 2+ партнерах
    dropdowns = [c for c in view.children if isinstance(c, discord.ui.Select)]
    assert len(dropdowns) == 1
    assert len(dropdowns[0].options) >= 2
