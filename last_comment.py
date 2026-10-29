#!/usr/bin/env python3
"""
Комментарий для Альфа-Банка (поля CRM, не внутренний чат).

  lastBankComment — текст из справочника для банка
  lastBankCommentDate — время (мс)
  boxed_bank_request/install/bank_requests — полная история статусов

Хранится в enrichment.last_comment как снимок; история — в enrichment.bank_request.comments.
В отчёте приоритет у полей в корне data.
"""
from __future__ import annotations

import html as html_module
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests

from config import COMMENTS_CONFIG, CRM_CONFIG, MANAGER_NAMES

AUTO_SENDER = "Автоматическое отправление"


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


def _sender_label(user_id: Any) -> str:
    if user_id in (None, "", 0, "0"):
        return AUTO_SENDER
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return str(user_id)
    return MANAGER_NAMES.get(uid) or str(uid)


def normalize_bank_comment_list(raw_list: Any) -> List[Dict[str, Any]]:
    """Список статусов CRM: отправитель, текст, дата (новые сверху)."""
    if not isinstance(raw_list, list):
        return []
    items: List[Dict[str, Any]] = []
    for raw in raw_list:
        if not isinstance(raw, dict):
            continue
        if "responsibleUserId" in raw or "requestId" in raw:
            comment = str(raw.get("comment") or "").strip()
            created = raw.get("created")
            user_id = raw.get("responsibleUserId")
            sender = _sender_label(user_id)
        else:
            comment = str(raw.get("comment") or raw.get("text") or "").strip()
            created = raw.get("created") or raw.get("at")
            user_id = raw.get("responsible_user_id")
            sender = str(raw.get("sender") or "").strip() or _sender_label(user_id)
        if not comment and created is None:
            continue
        items.append({
            "sender": sender,
            "comment": comment,
            "created": created,
            "created_label": raw.get("created_label") or _format_comment_timestamp(created),
            "responsible_user_id": user_id,
        })
    items.sort(key=lambda item: item.get("created") or 0, reverse=True)
    return items


def empty_bank_request_bundle() -> Dict[str, Any]:
    return {
        "latest_status": None,
        "latest_comment": None,
        "latest_created": None,
        "responsible_user_id": None,
        "total_requests": 0,
        "comments": [],
    }


def fetch_bank_request_bundle(token: str, request_id: int) -> Dict[str, Any]:
    """История банковских статусов заявки из CRM."""
    url = (
        f"{CRM_CONFIG['base_url']}"
        "/admin-api/protected/boxed_bank_request/install/bank_requests"
    )
    headers = {
        "Authorization": token,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
    }
    response = requests.get(
        url,
        headers=headers,
        params={"request_id": request_id},
        timeout=15,
    )
    response.raise_for_status()
    raw_list = response.json()
    if not isinstance(raw_list, list) or not raw_list:
        return empty_bank_request_bundle()
    comments = normalize_bank_comment_list(raw_list)
    latest = max(raw_list, key=lambda item: item.get("created") or 0)
    return {
        "latest_status": latest.get("status"),
        "latest_comment": latest.get("comment"),
        "latest_created": latest.get("created"),
        "responsible_user_id": latest.get("responsibleUserId"),
        "total_requests": len(raw_list),
        "comments": comments,
    }


def comment_datetime(at: Any) -> Optional[datetime]:
    """Дата комментария из миллисекунд CRM."""
    if at is None or at == "":
        return None
    try:
        ms = float(at)
        if ms < 1e12:
            ms *= 1000
        return datetime.fromtimestamp(ms / 1000.0)
    except (ValueError, TypeError, OSError):
        return None


def last_manager_comment(comments: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Последний комментарий человека, без «Автоматическое отправление»."""
    for item in comments or []:
        if str(item.get("sender") or "") == AUTO_SENDER:
            continue
        if not str(item.get("comment") or "").strip():
            continue
        return item
    return None


def extract_bank_comment_list(order: dict) -> List[Dict[str, Any]]:
    """История статусов из enrichment.bank_request.comments, если уже сохранена."""
    if not isinstance(order, dict):
        return []
    enrichment = order.get("enrichment") if isinstance(order.get("enrichment"), dict) else {}
    bank_request = (
        enrichment.get("bank_request")
        if isinstance(enrichment.get("bank_request"), dict)
        else {}
    )
    return normalize_bank_comment_list(bank_request.get("comments"))
