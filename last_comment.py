#!/usr/bin/env python3
"""
Комментарий для Альфа-Банка (поля CRM, не внутренний чат).

  lastBankComment — текст из справочника для банка
  lastBankCommentDate — время (мс)

Хранится в enrichment.last_comment как снимок; в отчёте приоритет у полей в корне data.
"""
from __future__ import annotations

import html as html_module
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

from config import COMMENTS_CONFIG


def extract_alfa_bank_comment_from_dict(d: Optional[dict]) -> Optional[Dict[str, Any]]:
    if not d or not isinstance(d, dict):
        return None
    raw = d.get("lastBankComment")
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    at = d.get("lastBankCommentDate")
    at_val = at if isinstance(at, (int, float)) else None
    return {
        "text": text,
        "author": None,
        "at": at_val,
        "source": "alfa_bank",
    }


def extract_alfa_bank_comment(
    order_dict: dict,
    install_data: Optional[dict] = None,
) -> Optional[Dict[str, Any]]:
    c = extract_alfa_bank_comment_from_dict(order_dict if isinstance(order_dict, dict) else None)
    if c:
        return c
    return extract_alfa_bank_comment_from_dict(install_data)


def resolve_last_comment(
    token: str,
    request_id: int,
    install_data: Optional[dict],
    order_dict: dict,
) -> Dict[str, Any]:
    _ = token, request_id
    c = extract_alfa_bank_comment(order_dict, install_data)
    if c:
        return c
    return {"text": "", "author": None, "at": None, "source": "empty"}


def get_last_comment_for_display(order: dict) -> Tuple[str, str]:
    c = _get_last_comment(order)
    if not c or not str(c.get("text", "")).strip():
        dash = "—"
        safe = html_module.escape(dash)
        return safe, safe

    text = str(c["text"]).strip()
    try:
        max_len = min(200, int(COMMENTS_CONFIG.get("max_length", 200)))
    except (TypeError, ValueError):
        max_len = 200
    short = text if len(text) <= max_len else text[: max_len - 1] + "…"

    return html_module.escape(short), html_module.escape(text)


def get_last_comment_plain(order: dict) -> str:
    """Текст комментария для банка без HTML (Excel, экспорт). Пустая строка если нет."""
    c = _get_last_comment(order)
    return str(c["text"]).strip() if c and str(c.get("text", "")).strip() else ""


def get_last_comment_date_plain(order: dict) -> str:
    """Дата последнего банковского комментария в формате отчёта."""
    c = _get_last_comment(order)
    return _format_comment_timestamp(c.get("at")) if c else ""


def _get_last_comment(order: dict) -> Optional[Dict[str, Any]]:
    c = extract_alfa_bank_comment_from_dict(order if isinstance(order, dict) else None)
    if not c:
        en = order.get("enrichment") if isinstance(order.get("enrichment"), dict) else {}
        lc = en.get("last_comment") if isinstance(en.get("last_comment"), dict) else None
        if lc and str(lc.get("source", "")) == "alfa_bank" and str(lc.get("text", "")).strip():
            c = lc
    return c


def _format_comment_timestamp(at: Any) -> str:
    if at is None:
        return ""
    try:
        ms = float(at)
        if ms < 1e12:
            ms *= 1000
        return datetime.fromtimestamp(ms / 1000.0).strftime("%d.%m.%Y %H:%M")
    except (ValueError, TypeError, OSError):
        return ""


def build_status_fallback_comment(order: dict) -> Dict[str, Any]:
    _ = order
    return {"text": "", "author": None, "at": None, "source": "empty"}
