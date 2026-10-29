#!/usr/bin/env python3
"""
Инвентаризация заявок: «Новая» и «Счет выставлен» без актуального комментария менеджера.
Список заявок и история статусов читаются из admin-api BIFIT.
"""
from __future__ import annotations

import html
import io
import json
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import psycopg2
import requests
from flask import Flask, Response, render_template_string, request
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from config import CRM_CONFIG, DB_CONFIG, MANAGER_NAMES, STATUS_LABELS
from last_comment import (
    empty_bank_request_bundle,
    extract_bank_comment_list,
    fetch_bank_request_bundle,
    last_manager_comment,
    comment_datetime,
    normalize_bank_comment_list,
)

app = Flask(__name__)

INV_STATUSES = ("NEW", "INCOME_CREATED")
INV_STATUS_LABELS = {
    **STATUS_LABELS,
    "INCOME_CREATED": "Счет выставлен",
}
DEFAULT_MAX_AGE_DAYS = 7
LIVE_WORKERS = 12
CALL_PAGE_SIZE = 1000
CALL_STATUS_LABELS = {
    "ANSWER": "дозвонились",
    "NOANSWER": "не взял",
    "BUSY": "занято",
}
MOBILE_RE = re.compile(r"(?:\+7|8|7)?(9\d{9})")


def get_crm_token() -> str:
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("SELECT current_token FROM auth_tokens WHERE service = 'crm'")
    result = cur.fetchone()
    cur.close()
    conn.close()
    if not result or not result[0]:
        raise ValueError("Токен CRM не найден")
    return result[0]


def default_start(today: datetime) -> datetime:
    if today.month >= 6:
        return datetime(today.year, 6, 1)
    return datetime(today.year - 1, 6, 1)


def parse_date_arg(raw: Optional[str], fallback: datetime) -> datetime:
    if not raw:
        return fallback
    try:
        return datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        return fallback


def parse_ms(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def format_dt(value: Optional[datetime]) -> str:
    if not value:
        return "—"
    return value.strftime("%d.%m.%Y %H:%M")


def format_day(value: Optional[datetime]) -> str:
    if not value:
        return "—"
    return value.strftime("%d.%m.%Y")


def manager_name(manager_id: Any) -> str:
    try:
        uid = int(manager_id)
    except (TypeError, ValueError):
        return "Без менеджера"
    return MANAGER_NAMES.get(uid, f"Менеджер {uid}")


def fetch_live_list(token: str, start_dt: datetime, end_dt: datetime) -> List[dict]:
    payload = {
        "statuses": list(INV_STATUSES),
        "managerIds": CRM_CONFIG["manager_ids"],
        "startTime": int(start_dt.timestamp() * 1000),
        "endTime": int(end_dt.timestamp() * 1000),
    }
    headers = {
        "Authorization": token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    response = requests.post(
        CRM_CONFIG["api_url"],
        json=payload,
        headers=headers,
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def fetch_db_list(start_dt: datetime, end_dt: datetime) -> List[dict]:
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT data
        FROM requests
        WHERE created_at >= %s
          AND created_at <= %s
          AND COALESCE(data->>'status', '') = ANY(%s)
        ORDER BY created_at DESC
        """,
        (start_dt, end_dt, list(INV_STATUSES)),
    )
    rows = []
    for (raw,) in cur.fetchall():
        if isinstance(raw, dict):
            rows.append(raw)
        elif isinstance(raw, str):
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(parsed, dict):
                rows.append(parsed)
    cur.close()
    conn.close()
    return rows


def fetch_histories(token: str, request_ids: List[int]) -> Dict[int, dict]:
    result: Dict[int, dict] = {}
    if not request_ids:
        return result

    def one(rid: int) -> Tuple[int, dict]:
        return rid, fetch_bank_request_bundle(token, rid)

    with ThreadPoolExecutor(max_workers=LIVE_WORKERS) as pool:
        futs = [pool.submit(one, rid) for rid in request_ids]
        for fut in as_completed(futs):
            try:
                rid, bundle = fut.result()
            except Exception:
                continue
            result[int(rid)] = bundle or empty_bank_request_bundle()
    return result


def histories_from_cache(orders: List[dict]) -> Dict[int, List[dict]]:
    out: Dict[int, List[dict]] = {}
    for order in orders:
        rid = parse_ms(order.get("id"))
        if rid is None:
            continue
        out[int(rid)] = extract_bank_comment_list(order)
    return out


def national_phone(raw: Any) -> Optional[str]:
    digits = re.sub(r"\D", "", str(raw or ""))
    if digits.startswith("000"):
        digits = digits[3:]
    if len(digits) >= 11 and digits[-11] in "78":
        return digits[-10:]
    if len(digits) == 10:
        return digits
    return None


def format_phone(national: str) -> str:
    if len(national) != 10:
        return national
    return f"+7 {national[0:3]} {national[3:6]}-{national[6:8]}-{national[8:10]}"


def add_phone(found: List[str], raw: Any) -> None:
    number = national_phone(raw)
    if number and number not in found:
        found.append(number)


def extract_install_phones(install: Optional[dict]) -> List[str]:
    found: List[str] = []
    if not isinstance(install, dict):
        return found
    request = install.get("request") if isinstance(install.get("request"), dict) else {}
    merchant = request.get("merchantObject") if isinstance(request.get("merchantObject"), dict) else {}
    contacts = merchant.get("contacts") if isinstance(merchant.get("contacts"), list) else []
    for contact in contacts:
        if isinstance(contact, dict):
            add_phone(found, contact.get("contactPhone") or contact.get("phone"))
    add_phone(found, install.get("contactPhone"))
    return found


def extract_comment_phones(comments: List[dict]) -> List[str]:
    found: List[str] = []
    for item in comments or []:
        compact = re.sub(r"[\s\-()]+", "", str(item.get("comment") or ""))
        for match in MOBILE_RE.findall(compact):
            add_phone(found, match)
    return found


def collect_phones(order: dict, install: Optional[dict], comments: List[dict]) -> List[str]:
    found: List[str] = []
    add_phone(found, order.get("contactPhone"))
    for number in extract_install_phones(install):
        add_phone(found, number)
    for number in extract_comment_phones(comments):
        add_phone(found, number)
    return found


def format_duration(seconds: Optional[int]) -> str:
    if seconds is None:
        return "—"
    minutes, sec = divmod(max(0, int(seconds)), 60)
    return f"{minutes}:{sec:02d}"


def fetch_installs(token: str, request_ids: List[int]) -> Dict[int, dict]:
    result: Dict[int, dict] = {}
    if not request_ids:
        return result
    headers = {
        "Authorization": token,
        "Accept": "application/json, text/plain, */*",
    }
    base = f"{CRM_CONFIG['base_url']}/admin-api/protected/boxed_kkm/install"

    def one(rid: int) -> Tuple[int, Optional[dict]]:
        response = requests.get(f"{base}/{rid}", headers=headers, timeout=20)
        response.raise_for_status()
        data = response.json()
        return rid, data if isinstance(data, dict) else None

    with ThreadPoolExecutor(max_workers=LIVE_WORKERS) as pool:
        futs = [pool.submit(one, rid) for rid in request_ids]
        for fut in as_completed(futs):
            try:
                rid, data = fut.result()
            except Exception:
                continue
            if data:
                result[int(rid)] = data
    return result


def fetch_telephony_page(
    token: str,
    start_ms: int,
    end_ms: int,
    route_type: str,
    offset: int,
) -> dict:
    headers = {
        "Authorization": token,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
    }
    url = f"{CRM_CONFIG['base_url']}/admin-api/protected/telephony/list/read"
    response = requests.post(
        url,
        headers=headers,
        params={
            "keyword": "",
            "begin_time": start_ms,
            "end_time": end_ms,
            "limit": CALL_PAGE_SIZE,
            "offset": offset,
            "route_type": route_type,
            "include_empty_admin_user": "true",
        },
        json=list(CRM_CONFIG["manager_ids"]),
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    return data if isinstance(data, dict) else {}


def parse_call_item(item: dict, route_type: str) -> dict:
    raw = item.get("adminPhoneCall") if isinstance(item, dict) else {}
    if not isinstance(raw, dict):
        raw = {}
    begin = comment_datetime(raw.get("beginTime"))
    duration = None
    if raw.get("endTime") is not None and raw.get("beginTime") is not None:
        try:
            duration = max(0, int((int(raw["endTime"]) - int(raw["beginTime"])) / 1000))
        except (TypeError, ValueError):
            duration = None
    status = str(raw.get("status") or "")
    return {
        "id": raw.get("id"),
        "route": raw.get("routeType") or route_type,
        "status": status,
        "status_label": CALL_STATUS_LABELS.get(status, status or "—"),
        "who": raw.get("callerFullName") or "",
        "admin_user_id": raw.get("adminUserId"),
        "begin": begin,
        "begin_label": format_dt(begin),
        "duration": duration,
        "duration_label": format_duration(duration),
        "receiver": national_phone(raw.get("receiverNumber")),
        "caller": national_phone(raw.get("callerNumber")),
        "caller_ext": raw.get("callerNumber") or "",
        "receiver_raw": raw.get("receiverNumber") or "",
    }


def fetch_calls_for_route(token: str, start_dt: datetime, end_dt: datetime, route_type: str) -> List[dict]:
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    first = fetch_telephony_page(token, start_ms, end_ms, route_type, 0)
    items = list(first.get("items") or [])
    try:
        total = int(first.get("total") or 0)
    except (TypeError, ValueError):
        total = len(items)
    offsets = list(range(CALL_PAGE_SIZE, total, CALL_PAGE_SIZE))
    if not offsets:
        return [parse_call_item(item, route_type) for item in items if isinstance(item, dict)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = [
            pool.submit(fetch_telephony_page, token, start_ms, end_ms, route_type, offset)
            for offset in offsets
        ]
        for fut in as_completed(futs):
            try:
                page = fut.result()
            except Exception:
                continue
            items.extend(page.get("items") or [])
    return [parse_call_item(item, route_type) for item in items if isinstance(item, dict)]


def index_calls(calls: List[dict], client_field: str) -> Dict[str, List[dict]]:
    index: Dict[str, List[dict]] = defaultdict(list)
    seen: Dict[str, set] = defaultdict(set)
    for call in calls:
        key = call.get(client_field)
        cid = call.get("id")
        if not key or cid in seen[key]:
            continue
        seen[key].add(cid)
        index[key].append(call)
    for key in index:
        index[key].sort(key=lambda item: item.get("begin") or datetime.min, reverse=True)
    return index


def fetch_call_indexes(token: str, start_dt: datetime, end_dt: datetime) -> Tuple[Dict[str, List[dict]], Dict[str, List[dict]]]:
    with ThreadPoolExecutor(max_workers=2) as pool:
        out_f = pool.submit(fetch_calls_for_route, token, start_dt, end_dt, "OUTGOING")
        in_f = pool.submit(fetch_calls_for_route, token, start_dt, end_dt, "INCOMING")
        outgoing = out_f.result()
        incoming = in_f.result()
    return index_calls(outgoing, "receiver"), index_calls(incoming, "caller")


def calls_for_phones(
    phones: List[str],
    index_out: Dict[str, List[dict]],
    index_in: Dict[str, List[dict]],
) -> Tuple[List[dict], List[dict]]:
    outgoing: List[dict] = []
    incoming: List[dict] = []
    seen_out = set()
    seen_in = set()
    for phone in phones:
        for call in index_out.get(phone, []):
            cid = call.get("id")
            if cid in seen_out:
                continue
            seen_out.add(cid)
            outgoing.append(call)
        for call in index_in.get(phone, []):
            cid = call.get("id")
            if cid in seen_in:
                continue
            seen_in.add(cid)
            incoming.append(call)
    outgoing.sort(key=lambda item: item.get("begin") or datetime.min, reverse=True)
    incoming.sort(key=lambda item: item.get("begin") or datetime.min, reverse=True)
    return outgoing, incoming


def classify_row(
    order: dict,
    comments: List[dict],
    cutoff: datetime,
    today: datetime,
    install: Optional[dict] = None,
    index_out: Optional[Dict[str, List[dict]]] = None,
    index_in: Optional[Dict[str, List[dict]]] = None,
) -> dict:
    rid = parse_ms(order.get("id"))
    created = comment_datetime(order.get("created"))
    status = str(order.get("status") or "")
    mid_raw = order.get("callCenterManagerId")
    try:
        mid = int(mid_raw)
    except (TypeError, ValueError):
        mid = mid_raw
    human = last_manager_comment(comments)
    auto_last = comments[0] if comments else None
    comment_at = comment_datetime(human.get("created")) if human else None
    if human is None:
        verdict = "missing"
        verdict_label = "Нет комментария менеджера"
        age_days = None
    elif comment_at is None:
        verdict = "missing"
        verdict_label = "Нет даты комментария"
        age_days = None
    elif comment_at < cutoff:
        verdict = "stale"
        age_days = (today.date() - comment_at.date()).days
        verdict_label = f"Комментарий старше недели ({age_days} дн.)"
    else:
        verdict = "ok"
        age_days = (today.date() - comment_at.date()).days
        verdict_label = "Актуален"

    phones = collect_phones(order, install, comments)
    outgoing, incoming = calls_for_phones(phones, index_out or {}, index_in or {})
    answered = sum(1 for call in outgoing if call.get("status") == "ANSWER")
    last_out = outgoing[0]["begin"] if outgoing else None
    if not phones:
        calls_verdict = "nophone"
        calls_label = "Нет номера"
    elif not outgoing:
        calls_verdict = "none"
        calls_label = "Исходящих нет"
    else:
        calls_verdict = "ok"
        calls_label = (
            f"{len(outgoing)} исх. · дозв. {answered} · посл. {format_day(last_out)}"
        )

    return {
        "id": int(rid) if rid is not None else 0,
        "organization": order.get("organizationName") or "—",
        "inn": order.get("organizationInn") or "—",
        "contact": order.get("contactName") or "—",
        "phone": order.get("contactPhone") or "—",
        "phones": phones,
        "phones_label": ", ".join(format_phone(item) for item in phones) or "нет номера",
        "status": status,
        "status_label": INV_STATUS_LABELS.get(status, status or "—"),
        "manager_id": mid,
        "manager": manager_name(mid),
        "created": created,
        "created_label": format_dt(created),
        "comment": (human or {}).get("comment") or "",
        "comment_author": (human or {}).get("sender") or "—",
        "comment_at": comment_at,
        "comment_label": format_dt(comment_at),
        "age_days": age_days,
        "verdict": verdict,
        "verdict_label": verdict_label,
        "auto_last": (auto_last or {}).get("comment") or "",
        "auto_sender": (auto_last or {}).get("sender") or "",
        "auto_label": (auto_last or {}).get("created_label") or "",
        "comments": comments,
        "calls_out": outgoing,
        "calls_in": incoming,
        "calls_out_n": len(outgoing),
        "calls_in_n": len(incoming),
        "calls_answered": answered,
        "calls_last_label": format_dt(last_out) if last_out else "—",
        "calls_verdict": calls_verdict,
        "calls_label": calls_label,
        "crm_url": f"{CRM_CONFIG['admin_url']}?id={rid}" if rid else CRM_CONFIG["admin_url"],
    }


def build_rows(
    orders: List[dict],
    histories: Dict[int, Any],
    cutoff: datetime,
    today: datetime,
    from_live: bool,
    installs: Optional[Dict[int, dict]] = None,
    index_out: Optional[Dict[str, List[dict]]] = None,
    index_in: Optional[Dict[str, List[dict]]] = None,
) -> List[dict]:
    rows = []
    installs = installs or {}
    for order in orders:
        rid = parse_ms(order.get("id"))
        if rid is None:
            continue
        if from_live:
            bundle = histories.get(int(rid)) or empty_bank_request_bundle()
            comments = normalize_bank_comment_list(bundle.get("comments"))
        else:
            comments = histories.get(int(rid)) or []
        rows.append(
            classify_row(
                order,
                comments,
                cutoff,
                today,
                install=installs.get(int(rid)),
                index_out=index_out,
                index_in=index_in,
            )
        )
    rows.sort(
        key=lambda item: (
            0 if item["verdict"] == "missing" else 1 if item["verdict"] == "stale" else 2,
            0 if item["calls_verdict"] == "none" else 1,
            -(item["age_days"] or 0),
            item["manager"],
            item["organization"],
        )
    )
    return rows


def manager_stats(rows: List[dict]) -> List[dict]:
    grouped: Dict[Any, dict] = defaultdict(
        lambda: {
            "manager": "",
            "manager_id": None,
            "total": 0,
            "ok": 0,
            "stale": 0,
            "missing": 0,
            "no_calls": 0,
        }
    )
    for row in rows:
        key = row["manager_id"]
        bucket = grouped[key]
        bucket["manager"] = row["manager"]
        bucket["manager_id"] = key
        bucket["total"] += 1
        bucket[row["verdict"]] += 1
        if row.get("calls_verdict") == "none":
            bucket["no_calls"] += 1
    result = list(grouped.values())
    result.sort(key=lambda item: (-(item["missing"] + item["stale"]), item["manager"]))
    return result


def load_inventory(start_dt: datetime, end_dt: datetime, cutoff: datetime, today: datetime, live: bool):
    source = "db"
    warning = ""
    orders: List[dict] = []
    histories: Dict[int, Any] = {}
    installs: Dict[int, dict] = {}
    index_out: Dict[str, List[dict]] = {}
    index_in: Dict[str, List[dict]] = {}
    from_live = False
    if live:
        try:
            token = get_crm_token()
            orders = fetch_live_list(token, start_dt, end_dt)
            ids = [int(item["id"]) for item in orders if parse_ms(item.get("id")) is not None]
            with ThreadPoolExecutor(max_workers=3) as gate:
                hist_f = gate.submit(fetch_histories, token, ids)
                inst_f = gate.submit(fetch_installs, token, ids)
                call_f = gate.submit(fetch_call_indexes, token, start_dt, end_dt)
                histories = hist_f.result()
                installs = inst_f.result()
                try:
                    index_out, index_in = call_f.result()
                except Exception as exc:
                    warning = f"Звонки из раздела «Телефонные обращения» не загрузились ({exc})."
            source = "crm"
            from_live = True
        except Exception as exc:
            warning = f"CRM admin-api недоступен ({exc}). Показаны данные из локальной базы."
            orders = fetch_db_list(start_dt, end_dt)
            histories = histories_from_cache(orders)
    else:
        orders = fetch_db_list(start_dt, end_dt)
        histories = histories_from_cache(orders)
        warning = "Режим без запроса в CRM: комментарии из последнего обогащения."
    rows = build_rows(
        orders,
        histories,
        cutoff,
        today,
        from_live=from_live,
        installs=installs,
        index_out=index_out,
        index_in=index_in,
    )
    return rows, source, warning


def parse_filters():
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    now = datetime.now()
    start_dt = parse_date_arg(request.args.get("start"), default_start(now))
    end_raw = parse_date_arg(request.args.get("end"), now)
    end_dt = end_raw.replace(hour=23, minute=59, second=59, microsecond=0)
    try:
        days = int(request.args.get("days") or DEFAULT_MAX_AGE_DAYS)
    except ValueError:
        days = DEFAULT_MAX_AGE_DAYS
    days = max(1, min(60, days))
    cutoff = datetime.combine((now.date() - timedelta(days=days)), datetime.min.time())
    manager_raw = request.args.get("manager") or ""
    manager_id = None
    if manager_raw not in ("", "all"):
        try:
            manager_id = int(manager_raw)
        except ValueError:
            manager_id = None
    only_problems = request.args.get("view", "problems") != "all"
    live = request.args.get("live", "1") != "0"
    return {
        "today": today,
        "now": now,
        "start_dt": start_dt,
        "end_dt": end_dt,
        "days": days,
        "cutoff": cutoff,
        "manager_id": manager_id,
        "only_problems": only_problems,
        "live": live,
    }


def apply_filters(rows: List[dict], filters: dict) -> List[dict]:
    out = rows
    if filters["manager_id"] is not None:
        out = [row for row in out if row["manager_id"] == filters["manager_id"]]
    if filters["only_problems"]:
        out = [row for row in out if row["verdict"] != "ok"]
    return out


def render_calls_table(outgoing: List[dict], incoming: List[dict]) -> str:
    chunks = []
    for title, items in (("Исходящие", outgoing), ("Входящие", incoming)):
        if not items:
            chunks.append(f'<div class="crm-empty">{html.escape(title)}: нет</div>')
            continue
        rows = []
        for item in items:
            rows.append(
                "<tr>"
                f"<td>{html.escape(item.get('begin_label') or '—')}</td>"
                f"<td>{html.escape(item.get('who') or '—')}</td>"
                f"<td>{html.escape(item.get('status_label') or '—')}</td>"
                f"<td>{html.escape(item.get('duration_label') or '—')}</td>"
                f"<td>{html.escape(format_phone(item.get('receiver') or item.get('caller') or ''))}</td>"
                "</tr>"
            )
        chunks.append(
            f"<div class=\"crm-empty\">{html.escape(title)}</div>"
            '<table class="crm-comments">'
            "<thead><tr><th>Когда</th><th>Кто звонил</th><th>Статус</th><th>Длит.</th><th>Номер</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>"
        )
    return "".join(chunks)


def render_comments_table(items: List[dict]) -> str:
    if not items:
        return '<div class="crm-empty">Нет статусов в CRM</div>'
    rows = []
    for item in items:
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(item.get('sender') or '—'))}</td>"
            f"<td>{html.escape(str(item.get('comment') or '—'))}</td>"
            f"<td class=\"crm-date\">{html.escape(str(item.get('created_label') or '—'))}</td>"
            "</tr>"
        )
    return (
        '<table class="crm-comments">'
        "<thead><tr><th>Отправитель</th><th>Комментарий</th><th>Дата</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


PAGE = r"""
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Инвентаризация заявок</title>
    <style>
        :root { --good:#0f9d58; --bad:#dc2626; --warning:#d97706; --header-from:#1e3a5f; --header-to:#0f766e; --text:#1f2a37; --muted:#6b7c93; --card:#fff; }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif; background:#f7f9fc; color:var(--text); }
        .header { background: linear-gradient(135deg, var(--header-from), var(--header-to)); color:#fff; padding:24px; }
        .header-top { display:flex; justify-content:space-between; gap:16px; flex-wrap:wrap; margin-bottom:16px; }
        h1 { font-size:1.8rem; font-weight:650; }
        .sub { opacity:.88; margin-top:6px; max-width:820px; line-height:1.45; }
        .actions { display:flex; gap:8px; flex-wrap:wrap; }
        .btn { display:inline-flex; align-items:center; padding:9px 12px; border-radius:10px; text-decoration:none; color:#fff; border:1px solid rgba(255,255,255,.25); background:rgba(255,255,255,.12); font-weight:600; font-size:.9rem; }
        .period-bar { display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
        .period-bar label { font-size:.85rem; opacity:.85; }
        .period-bar input, .period-bar select { border:0; border-radius:8px; padding:7px 10px; font-size:.9rem; }
        .period-bar button { border:1px solid rgba(255,255,255,.3); background:#fff; color:var(--header-from); border-radius:8px; padding:7px 12px; font-weight:700; cursor:pointer; }
        .container { padding:20px 24px 40px; }
        .warn { background:#fff7ed; border:1px solid #fdba74; color:#9a3412; padding:10px 12px; border-radius:12px; margin-bottom:14px; }
        .kpi-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; margin-bottom:16px; }
        .kpi { background:var(--card); border:1px solid #e6ebf2; border-radius:14px; padding:14px 16px; box-shadow:0 8px 20px rgba(15,23,42,.04); }
        .kpi .n { font-size:1.8rem; font-weight:750; }
        .kpi .l { color:var(--muted); font-size:.85rem; margin-top:4px; }
        .kpi.bad .n { color:var(--bad); }
        .kpi.warn .n { color:var(--warning); }
        .kpi.ok .n { color:var(--good); }
        .mgr { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:10px; margin:8px 0 18px; }
        .mgr a { text-decoration:none; color:inherit; background:#fff; border:1px solid #e6ebf2; border-radius:14px; padding:12px; display:block; }
        .mgr a.active, .mgr a:hover { border-color:#0f766e; box-shadow:0 8px 18px rgba(15,118,110,.12); }
        .mgr .name { font-weight:700; margin-bottom:6px; }
        .mgr .meta { color:var(--muted); font-size:.82rem; }
        .mgr .fail { color:var(--bad); font-weight:700; }
        table { width:100%; border-collapse:collapse; background:#fff; border-radius:14px; overflow:hidden; }
        th, td { border-bottom:1px solid #e6ebf2; padding:8px 10px; text-align:left; vertical-align:top; font-size:.86rem; }
        th { background:#f8fafc; color:var(--muted); position:sticky; top:0; }
        tr.missing { background:#fff1f1; }
        tr.stale { background:#fff8eb; }
        .badge { display:inline-block; padding:2px 8px; border-radius:99px; font-size:.75rem; font-weight:700; }
        .badge.missing { background:#fee2e2; color:#991b1b; }
        .badge.stale { background:#ffedd5; color:#9a3412; }
        .badge.ok { background:#dcfce7; color:#166534; }
        .badge.none { background:#fee2e2; color:#991b1b; }
        .badge.nophone { background:#e5e7eb; color:#374151; }
        .badge.callsok { background:#dcfce7; color:#166534; }
        .id-link { color:#0f766e; font-weight:700; }
        .muted { color:var(--muted); }
        .hist-btn { border:1px solid #dbe3ee; background:#f8fafc; border-radius:8px; padding:4px 8px; cursor:pointer; font-weight:650; margin:0 4px 4px 0; }
        .inv-detail td { background:#f8fafc; padding:10px 12px; }
        .inv-detail .crm-comments { min-width: 520px; }
        .crm-comments { width:100%; margin-top:8px; }
        .crm-comments th, .crm-comments td { font-size:.8rem; }
        .crm-date { white-space:nowrap; color:var(--muted); }
        .crm-empty { color:var(--muted); padding:6px 0; }
        .note { color:var(--muted); font-size:.85rem; margin-top:14px; max-width:900px; line-height:1.45; }
        .toolbar { display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin-bottom:12px; }
        .toolbar a { color:#0f766e; font-weight:650; }
    </style>
</head>
<body>
    <div class="header">
        <div class="header-top">
            <div>
                <h1>Инвентаризация заявок</h1>
                <div class="sub">
                    Заявки в статусах «Новая» и «Счет выставлен» (в CRM — «Счет создан»)
                    с {{ start_label }} по {{ end_label }}.
                    Актуальный комментарий менеджера — не старше {{ days }} дн.
                    (не старше {{ cutoff_label }} включительно). Автотексты CRM не считаются.
                    Звонки — исходящие из «Телефонных обращений» по всем номерам заявки.
                </div>
            </div>
            <div class="actions">
                <a class="btn" href="/dashboard">Дашборд</a>
                <a class="btn" href="/sla">SLA</a>
                <a class="btn" href="/report">Отчёт CRM</a>
                <a class="btn" href="/">На главную</a>
            </div>
        </div>
        <form class="period-bar" method="GET" action="/inventory">
            <label>с</label>
            <input type="date" name="start" value="{{ start_val }}" required>
            <label>по</label>
            <input type="date" name="end" value="{{ end_val }}" required>
            <label>комментарий ≤</label>
            <input type="number" name="days" min="1" max="60" value="{{ days }}" style="width:70px">
            <label>дн.</label>
            <select name="manager">
                <option value="all">Все менеджеры</option>
                {% for item in managers %}
                <option value="{{ item.manager_id }}" {% if selected_manager == item.manager_id %}selected{% endif %}>{{ item.manager }}</option>
                {% endfor %}
            </select>
            <select name="view">
                <option value="problems" {% if only_problems %}selected{% endif %}>Только нарушения</option>
                <option value="all" {% if not only_problems %}selected{% endif %}>Все заявки</option>
            </select>
            <input type="hidden" name="live" value="{{ '1' if live else '0' }}">
            <button type="submit">Показать</button>
        </form>
    </div>
    <div class="container">
        {% if warning %}<div class="warn">{{ warning }}</div>{% endif %}
        <div class="kpi-grid">
            <div class="kpi"><div class="n">{{ totals.total }}</div><div class="l">В инвентаризации</div></div>
            <div class="kpi bad"><div class="n">{{ totals.missing }}</div><div class="l">Без комментария менеджера</div></div>
            <div class="kpi warn"><div class="n">{{ totals.stale }}</div><div class="l">Комментарий старше {{ days }} дн.</div></div>
            <div class="kpi ok"><div class="n">{{ totals.ok }}</div><div class="l">Актуальные</div></div>
            <div class="kpi bad"><div class="n">{{ totals.no_calls }}</div><div class="l">Без исходящих звонков</div></div>
        </div>
        <h2 style="font-size:1.05rem;margin-bottom:8px;">По менеджерам — куда ткнуть носом</h2>
        <div class="mgr">
            {% for item in managers %}
            <a class="{{ 'active' if selected_manager == item.manager_id else '' }}" href="{{ item.href }}">
                <div class="name">{{ item.manager }}</div>
                <div class="fail">нарушений {{ item.missing + item.stale }} из {{ item.total }}</div>
                <div class="meta">нет комментария {{ item.missing }} · устарел {{ item.stale }} · ок {{ item.ok }} · без звонков {{ item.no_calls }}</div>
            </a>
            {% endfor %}
        </div>
        <div class="toolbar">
            <span class="muted">Источник: {{ 'живой admin-api BIFIT' if data_source == 'crm' else 'локальная база' }}. Показано {{ shown }} из {{ totals.total }}.</span>
            <a href="{{ export_url }}">Скачать Excel по нарушениям</a>
        </div>
        <table>
            <thead>
                <tr>
                    <th>Менеджер</th>
                    <th>ID</th>
                    <th>Организация</th>
                    <th>Статус</th>
                    <th>Создана</th>
                    <th>Комментарий менеджера</th>
                    <th>Дата</th>
                    <th>Звонки</th>
                    <th>Нарушение</th>
                    <th></th>
                </tr>
            </thead>
            <tbody>
                {% for row in rows %}
                <tr class="{{ row.verdict }}">
                    <td>{{ row.manager }}</td>
                    <td><a class="id-link" href="{{ row.crm_url }}" target="_blank" rel="noopener">{{ row.id }}</a></td>
                    <td>
                        <div>{{ row.organization }}</div>
                        <div class="muted">ИНН {{ row.inn }} · {{ row.contact }}</div>
                        <div class="muted">{{ row.phones_label }}</div>
                    </td>
                    <td>{{ row.status_label }}</td>
                    <td>{{ row.created_label }}</td>
                    <td>
                        {% if row.comment %}{{ row.comment }}<div class="muted">{{ row.comment_author }}</div>
                        {% else %}<span class="muted">Нет</span>{% endif %}
                    </td>
                    <td>{{ row.comment_label }}{% if row.age_days is not none %}<div class="muted">{{ row.age_days }} дн.</div>{% endif %}</td>
                    <td>
                        <span class="badge {{ 'callsok' if row.calls_verdict == 'ok' else row.calls_verdict }}">{{ row.calls_label }}</span>
                        {% if row.calls_in_n %}<div class="muted">вх. {{ row.calls_in_n }}</div>{% endif %}
                    </td>
                    <td><span class="badge {{ row.verdict }}">{{ row.verdict_label }}</span></td>
                    <td>
                        <button type="button" class="hist-btn" data-id="{{ row.id }}">История</button>
                        <button type="button" class="hist-btn" data-calls="{{ row.id }}">Звонки</button>
                    </td>
                </tr>
                <tr class="inv-detail" id="detail-{{ row.id }}" hidden>
                    <td colspan="10">
                        <div class="hist" id="hist-{{ row.id }}" hidden>{{ row.history_html|safe }}</div>
                        <div class="hist" id="calls-{{ row.id }}" hidden>{{ row.calls_html|safe }}</div>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        {% if not rows %}
        <p class="note">Нарушений нет — все комментарии менеджеров свежие.</p>
        {% endif %}
        <p class="note">
            «Актуальный комментарий» — последняя запись менеджера в истории статусов CRM
            (<code>boxed_bank_request/install/bank_requests</code>), не автоматическое
            «Счет создан / Клиент не оплачивает». Список заявок — <code>install/list/read</code>.
            Клик по ID открывает карточку в админке BIFIT.
            Звонки читаются из <code>telephony/list/read</code> (исходящие и входящие)
            по всем телефонам карточки, не только по основному.
        </p>
    </div>
    <script>
        document.querySelectorAll('.hist-btn').forEach((btn) => {
            btn.addEventListener('click', () => {
                const id = btn.dataset.calls || btn.dataset.id;
                const isCalls = Boolean(btn.dataset.calls);
                const detail = document.getElementById('detail-' + id);
                const hist = document.getElementById('hist-' + id);
                const calls = document.getElementById('calls-' + id);
                const target = isCalls ? calls : hist;
                const other = isCalls ? hist : calls;
                const open = target.hidden;
                target.hidden = !open;
                other.hidden = true;
                detail.hidden = target.hidden && other.hidden;
                document.querySelectorAll('.hist-btn[data-id="' + id + '"]').forEach((b) => { b.textContent = 'История'; });
                document.querySelectorAll('.hist-btn[data-calls="' + id + '"]').forEach((b) => { b.textContent = 'Звонки'; });
                if (!target.hidden) btn.textContent = isCalls ? 'Скрыть звонки' : 'Скрыть';
            });
        });
    </script>
</body>
</html>
"""


def page_context(filters: dict, rows_all: List[dict], data_source: str, warning: str) -> dict:
    visible = apply_filters(rows_all, filters)
    stats = manager_stats(rows_all)
    totals = {
        "total": len(rows_all),
        "ok": sum(1 for row in rows_all if row["verdict"] == "ok"),
        "stale": sum(1 for row in rows_all if row["verdict"] == "stale"),
        "missing": sum(1 for row in rows_all if row["verdict"] == "missing"),
        "no_calls": sum(1 for row in rows_all if row.get("calls_verdict") == "none"),
    }
    q = {
        "start": filters["start_dt"].strftime("%Y-%m-%d"),
        "end": filters["end_dt"].strftime("%Y-%m-%d"),
        "days": str(filters["days"]),
        "live": "1" if filters["live"] else "0",
        "view": "problems" if filters["only_problems"] else "all",
    }

    def href_for(manager_id: Any) -> str:
        params = dict(q)
        if manager_id is not None:
            params["manager"] = str(manager_id)
        return "/inventory?" + urlencode(params)

    managers = []
    for item in stats:
        managers.append({**item, "href": href_for(item["manager_id"])})
    export_params = dict(q)
    if filters["manager_id"] is not None:
        export_params["manager"] = str(filters["manager_id"])
    export_url = "/inventory/export?" + urlencode(export_params)

    for row in visible:
        row["history_html"] = render_comments_table(row.get("comments") or [])
        row["calls_html"] = render_calls_table(row.get("calls_out") or [], row.get("calls_in") or [])

    return {
        "start_label": format_day(filters["start_dt"]),
        "end_label": format_day(filters["end_dt"]),
        "start_val": q["start"],
        "end_val": q["end"],
        "days": filters["days"],
        "cutoff_label": format_day(filters["cutoff"]),
        "managers": managers,
        "selected_manager": filters["manager_id"],
        "only_problems": filters["only_problems"],
        "live": filters["live"],
        "warning": warning,
        "totals": totals,
        "data_source": data_source,
        "shown": len(visible),
        "rows": visible,
        "export_url": export_url,
    }


@app.route("/inventory")
def inventory_page():
    filters = parse_filters()
    rows_all, source, warning = load_inventory(
        filters["start_dt"],
        filters["end_dt"],
        filters["cutoff"],
        filters["now"],
        filters["live"],
    )
    return render_template_string(PAGE, **page_context(filters, rows_all, source, warning))


@app.route("/inventory/export")
def inventory_export():
    filters = parse_filters()
    filters["only_problems"] = True
    rows_all, _, _ = load_inventory(
        filters["start_dt"],
        filters["end_dt"],
        filters["cutoff"],
        filters["now"],
        filters["live"],
    )
    rows = apply_filters(rows_all, filters)
    wb = Workbook()
    ws = wb.active
    ws.title = "Нарушения"
    ws.append(
        [
            "Менеджер",
            "ID",
            "Организация",
            "ИНН",
            "Контакт",
            "Телефоны",
            "Статус",
            "Создана",
            "Комментарий менеджера",
            "Автор",
            "Дата комментария",
            "Возраст дн.",
            "Исходящие",
            "Дозвонились",
            "Последний исходящий",
            "Входящие",
            "Нарушение",
        ]
    )
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="D1FAE5")
    fills = {
        "missing": PatternFill("solid", fgColor="FECACA"),
        "stale": PatternFill("solid", fgColor="FED7AA"),
    }
    for row in rows:
        ws.append(
            [
                row["manager"],
                row["id"],
                row["organization"],
                row["inn"],
                row["contact"],
                row.get("phones_label") or row.get("phone") or "",
                row["status_label"],
                row["created_label"],
                row["comment"],
                row["comment_author"],
                row["comment_label"],
                row["age_days"] if row["age_days"] is not None else "",
                row.get("calls_out_n") or 0,
                row.get("calls_answered") or 0,
                row.get("calls_last_label") or "",
                row.get("calls_in_n") or 0,
                row["verdict_label"],
            ]
        )
        fill = fills.get(row["verdict"])
        if fill:
            for cell in ws[ws.max_row]:
                cell.fill = fill
                cell.alignment = Alignment(wrap_text=True, vertical="top")
    widths = (16, 10, 42, 14, 22, 28, 18, 18, 42, 14, 18, 12, 12, 12, 20, 12, 36)
    for idx, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width
    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    filename = f"inventory_{filters['start_dt'].strftime('%Y%m%d')}_{filters['end_dt'].strftime('%Y%m%d')}.xlsx"
    return Response(
        bio.getvalue(),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
