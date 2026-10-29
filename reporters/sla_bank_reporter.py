#!/usr/bin/env python3
"""
SLA-отчёт для Альфа-Банка: по каждой заявке сроки прохождения этапов.

Считаются только рабочие дни (пн–пт), как в отчёте для банка:
  1. Рабочие часы до первого звонка
  2. Рабочие дни до оплаты счёта
  3. Рабочие дни до отправки оборудования
  4. Рабочие дни до получения оборудования
  5. Рабочие дни до успешной реализации

Внутренняя детализация SLA с тепловой картой остаётся в /sla.
"""
import html
import io
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import psycopg2
import requests
from flask import Flask, Response, request
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from config import CRM_CONFIG, DB_CONFIG
from last_comment import extract_bank_comment_list

app = Flask(__name__)

logger = logging.getLogger(__name__)

MS_DAY = 86400000

# Отменённые заявки в отчёт банку не попадают
EXCLUDED_STATUSES = {
    'CANCELED_BY_CLIENT',
    'CANCELED_BY_BANK',
    'REFUND',
}

STAGES = [
    ('first_call_hours', 'Рабочих часов до первого звонка', 'раб. ч.', 1),
    ('payment_days', 'Рабочих дней до оплаты счёта', 'раб. дн.', 2),
    ('dispatch_days', 'Рабочих дней до отправки оборудования', 'раб. дн.', 3),
    ('receipt_days', 'Рабочих дней до получения оборудования', 'раб. дн.', 4),
    ('success_days', 'Рабочих дней до успешной реализации', 'раб. дн.', 5),
]

# Страница выдачи звонков телефонии CRM (как в серверной версии SLA)
CALL_PAGE_SIZE = 1000

# Мобильный номер в тексте комментария
MOBILE_RE = re.compile(r'(?:\+7|8|7)?(9\d{9})')

# Кэш месячных срезов звонков: ключ «год-месяц» → (время загрузки, индекс)
CALLS_CACHE_TTL = 600
_CALLS_CACHE = {}


# --------------------------------------------------------------------------
# Расчёт сроков
# --------------------------------------------------------------------------
def parse_ms(value):
    if value is None or value == '':
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def working_hours_between(start_ms, end_ms):
    """Часы между метками только внутри пн–пт."""
    start_ms = parse_ms(start_ms)
    end_ms = parse_ms(end_ms)
    if start_ms is None or end_ms is None or end_ms < start_ms:
        return None
    cursor = datetime.fromtimestamp(start_ms / 1000)
    end = datetime.fromtimestamp(end_ms / 1000)
    seconds = 0.0
    while cursor < end:
        next_day = (cursor + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        segment_end = min(next_day, end)
        if cursor.weekday() < 5:
            seconds += (segment_end - cursor).total_seconds()
        cursor = segment_end
    return round(seconds / 3600, 4)


def working_days_between(start_ms, end_ms):
    hours = working_hours_between(start_ms, end_ms)
    return None if hours is None else round(hours / 24, 4)


def extract_tid(data):
    """TID из «Идентификатора терминала»."""
    if not isinstance(data, dict):
        return None
    request_data = data.get('request') if isinstance(data.get('request'), dict) else {}
    for source in (request_data, data):
        device_info = source.get('deviceInfo')
        if not isinstance(device_info, list) or not device_info:
            continue
        first_device = device_info[0]
        if not isinstance(first_device, dict):
            continue
        tid = first_device.get('tid')
        if tid is not None and str(tid).strip():
            return str(tid).strip()
    return None


# --------------------------------------------------------------------------
# CRM: токен, карточки, TID, звонки
# --------------------------------------------------------------------------
def get_crm_token():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute("SELECT current_token FROM auth_tokens WHERE service = 'crm'")
    result = cur.fetchone()
    cur.close()
    conn.close()
    if not result or not result[0]:
        raise ValueError('Токен CRM не найден')
    return result[0]


def crm_headers(token):
    return {
        'Authorization': token,
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }


def fetch_period_rows(start_dt, end_dt):
    """Заявки периода создания из локальной базы."""
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, data
        FROM requests
        WHERE data IS NOT NULL
          AND (data->>'created') ~ '^[0-9]+$'
          AND (data->>'created')::bigint BETWEEN %s AND %s
          AND COALESCE(data->>'status', '') <> ALL(%s)
        ORDER BY id DESC
        """,
        (start_ms, end_ms, list(EXCLUDED_STATUSES)),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    result = []
    for req_id, data in rows:
        if isinstance(data, str):
            data = json.loads(data)
        result.append((int(req_id), data or {}))
    return result


def fetch_live_requests(token, start_dt, end_dt):
    """Полные карточки за период: свежие даты этапов и телефоны."""
    payload = {
        'statuses': list(CRM_CONFIG['statuses']),
        'managerIds': list(CRM_CONFIG['manager_ids']),
        'startTime': int(start_dt.timestamp() * 1000),
        'endTime': int(end_dt.timestamp() * 1000),
    }
    response = requests.post(
        CRM_CONFIG['api_url'], json=payload, headers=crm_headers(token), timeout=120
    )
    response.raise_for_status()
    items = response.json()
    if not isinstance(items, list):
        return {}
    return {int(item['id']): item for item in items if isinstance(item, dict) and item.get('id')}


def fetch_request_tids(token, request_ids):
    """TID из карточки установки (в локальной базе он часто уже перетёрт)."""
    result = {}
    if not request_ids:
        return result
    base_url = CRM_CONFIG['base_url'].rstrip('/')

    def _fetch_one_tid(request_id):
        target = (
            base_url
            + '/admin-api/protected/boxed_kkm/install'
            + f'/{request_id}'
        )
        try:
            response = requests.get(target, headers=crm_headers(token), timeout=20)
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return request_id, None
        return request_id, extract_tid(payload)

    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(_fetch_one_tid, rid) for rid in request_ids]
        for future in as_completed(futures):
            rid, tid = future.result()
            if tid:
                result[rid] = tid
    return result


def load_saved_tids():
    """Сохранённые TID: чтобы не запрашивать CRM по каждой заявке заново."""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        cur.execute('SELECT request_id, tid FROM request_tids')
        result = {int(row[0]): row[1] for row in cur.fetchall()}
        cur.close()
        conn.close()
        return result
    except Exception as exc:
        logger.warning('Таблица request_tids недоступна: %s', exc)
        return {}


def save_tids(tids):
    """Сохраняет найденные TID, чтобы отчёт открывался быстро."""
    if not tids:
        return
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur = conn.cursor()
        cur.executemany(
            """
            INSERT INTO request_tids (request_id, tid, updated_at)
            VALUES (%s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (request_id) DO UPDATE
            SET tid = EXCLUDED.tid,
                updated_at = CURRENT_TIMESTAMP
            """,
            list(tids.items()),
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as exc:
        logger.warning('Не удалось сохранить TID: %s', exc)


def national_phone(value):
    """Номер в «национальном» виде: 10 цифр без 7/8 (как в телефонии CRM)."""
    digits = re.sub(r'\D', '', str(value or ''))
    if digits.startswith('000'):
        digits = digits[3:]
    if len(digits) >= 11 and digits[-11] in '78':
        return digits[-10:]
    if len(digits) == 10:
        return digits
    return None


def add_phone(found, raw):
    number = national_phone(raw)
    if number and number not in found:
        found.append(number)


def collect_phones(*sources):
    """Телефоны заявки: контакт, контакты мерчанта, номера из комментариев."""
    found = []
    for source in sources:
        if not isinstance(source, dict):
            continue
        add_phone(found, source.get('contactPhone'))
        request_block = source.get('request') if isinstance(source.get('request'), dict) else {}
        merchant = (
            request_block.get('merchantObject')
            if isinstance(request_block.get('merchantObject'), dict) else {}
        )
        for contact in merchant.get('contacts') or []:
            if isinstance(contact, dict):
                add_phone(found, contact.get('contactPhone') or contact.get('phone'))
    for comment in sources:
        if isinstance(comment, dict):
            for match in MOBILE_RE.findall(re.sub(r'[\s\-()]+', '', str(comment.get('comment') or ''))):
                add_phone(found, match)
    return found


def fetch_calls_page(token, start_ms, end_ms, offset, manager_ids=None):
    """Страница исходящих звонков телефонии CRM."""
    url = CRM_CONFIG['base_url'].rstrip('/') + '/admin-api/protected/telephony/list/read'
    headers = {
        'Authorization': token,
        'Accept': 'application/json, text/plain, */*',
        'Content-Type': 'application/json',
    }
    manager_filter = (
        list(CRM_CONFIG['manager_ids']) if manager_ids is None else list(manager_ids)
    )
    response = requests.post(
        url,
        headers=headers,
        params={
            'keyword': '',
            'begin_time': start_ms,
            'end_time': end_ms,
            'limit': CALL_PAGE_SIZE,
            'offset': offset,
            'route_type': 'OUTGOING',
            'include_empty_admin_user': 'true',
        },
        json=manager_filter,
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


def fetch_outgoing_calls(token, start_dt, end_dt, manager_ids=None):
    """Исходящие звонки периода: индекс «номер получателя → время начала, мс».

    У звонка в CRM несколько записей (по сменам), поэтому на номер
    храним только самое раннее время — для SLA важен первый звонок.
    manager_ids=None — звонки всех менеджеров (для отчёта банку по всем заявкам).
    """
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    first = fetch_calls_page(token, start_ms, end_ms, 0, manager_ids)
    items = list(first.get('items') or [])
    try:
        total = int(first.get('total') or len(items))
    except (TypeError, ValueError):
        total = len(items)
    offsets = list(range(CALL_PAGE_SIZE, total, CALL_PAGE_SIZE))
    if offsets:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [
                pool.submit(fetch_calls_page, token, start_ms, end_ms, offset)
                for offset in offsets
            ]
            for future in as_completed(futures):
                try:
                    items.extend(future.result().get('items') or [])
                except Exception:
                    continue

    index = {}
    for item in items:
        raw = item.get('adminPhoneCall') if isinstance(item, dict) else {}
        if not isinstance(raw, dict):
            continue
        phone = national_phone(raw.get('receiverNumber'))
        begin = parse_ms(raw.get('beginTime'))
        if not phone or begin is None:
            continue
        current = index.get(phone)
        if current is None or begin < current:
            index[phone] = begin
    return {phone: [begin] for phone, begin in index.items()}


def month_ranges(start_dt, end_dt):
    """Календарные месяцы периода — по ним кэшируем звонки."""
    current = start_dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    while current <= end_dt:
        if current.month == 12:
            next_month = current.replace(year=current.year + 1, month=1)
        else:
            next_month = current.replace(month=current.month + 1)
        yield max(current, start_dt), min(next_month - timedelta(milliseconds=1), end_dt)
        current = next_month


def fetch_calls_index_by_month(token, start_dt, end_dt):
    """Звонки периода по месяцам: месячные срезы кэшируются в памяти."""
    index = {}
    months = list(month_ranges(start_dt, end_dt))
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(cached_month_calls, token, month_start, month_end)
            for month_start, month_end in months
        ]
        for future in as_completed(futures):
            for phone, begins in future.result().items():
                known = index.setdefault(phone, [])
                for begin in begins:
                    if begin not in known:
                        known.append(begin)
    return index


def cached_month_calls(token, start_dt, end_dt):
    """Месячный индекс звонков; перечитывается раз в 10 минут."""
    key = (start_dt.strftime('%Y-%m'), end_dt.strftime('%Y-%m'))
    now = datetime.now()
    cached = _CALLS_CACHE.get(key)
    if cached and (now - cached[0]).total_seconds() < CALLS_CACHE_TTL:
        return cached[1]
    result = fetch_outgoing_calls(token, start_dt, end_dt)
    _CALLS_CACHE[key] = (now, result)
    return result


def first_outgoing_call_ms(order, call_index, comments):
    """Первый исходящий звонок по телефонам заявки после её создания."""
    created_ms = parse_ms(order.get('created'))
    if created_ms is None:
        return None
    timestamps = [
        begin
        for phone in collect_phones(order, {'comments': comments}, *comments)
        for begin in call_index.get(phone) or []
        if begin >= created_ms
    ]
    if not timestamps:
        return None
    return min(timestamps)


# --------------------------------------------------------------------------
# Сборка строк
# --------------------------------------------------------------------------
def build_sla_rows(start_dt, end_dt):
    """Строки отчёта + предупреждения о недоступности CRM."""
    warnings = []
    rows = fetch_period_rows(start_dt, end_dt)
    if not rows:
        return [], warnings

    try:
        token = get_crm_token()
    except Exception as exc:
        logger.warning('Нет токена CRM: %s', exc)
        warnings.append('Нет токена CRM: TID и первый звонок могут быть пустыми.')
        token = None

    live_requests = {}
    tids = {}
    calls = {}
    if token:
        with ThreadPoolExecutor(max_workers=2) as pool:
            requests_future = pool.submit(fetch_live_requests, token, start_dt, end_dt)
            calls_future = pool.submit(fetch_calls_index_by_month, token, start_dt, end_dt)
            try:
                live_requests = requests_future.result()
            except Exception as exc:
                logger.warning('Карточки CRM недоступны: %s', exc)
                warnings.append('Карточки CRM недоступны: TID взят из локальной базы.')
            try:
                calls = calls_future.result()
            except Exception as exc:
                logger.warning('Звонки CRM недоступны: %s', exc)
                warnings.append('Звонки CRM недоступны: первый показатель не рассчитан.')

        tids = load_saved_tids()

        missing_tid = [
            request_id for request_id, data in rows
            if not extract_tid(data)
            and not extract_tid(live_requests.get(request_id) or {})
            and request_id not in tids
        ]
        if missing_tid:
            try:
                fresh = fetch_request_tids(token, missing_tid)
            except Exception as exc:
                logger.warning('Не удалось получить TID: %s', exc)
                fresh = {}
            if fresh:
                save_tids(fresh)
                tids.update(fresh)

    result = []
    for request_id, data in rows:
        merged = dict(data)
        merged.update(live_requests.get(request_id) or {})
        created_ms = parse_ms(merged.get('created'))
        first_call_ms = first_outgoing_call_ms(
            merged, calls, extract_bank_comment_list(merged) or []
        )

        result.append({
            'id': request_id,
            'inn': str(merged.get('organizationInn') or ''),
            'tid': extract_tid(merged) or tids.get(request_id) or '',
            'first_call_hours': working_hours_between(created_ms, first_call_ms),
            'payment_days': working_days_between(
                merged.get('incomeSetDate'), merged.get('incomePaidDate')
            ),
            'dispatch_days': working_days_between(
                merged.get('incomePaidDate'), merged.get('sentToDeliveryDate')
            ),
            'receipt_days': working_days_between(
                merged.get('sentToDeliveryDate'), merged.get('deliveredDate')
            ),
            'success_days': working_days_between(
                created_ms, merged.get('kkmRegistrationDate')
            ),
        })
    return result, warnings


# --------------------------------------------------------------------------
# Оформление
# --------------------------------------------------------------------------
def format_value(value, unit):
    if value is None:
        return ''
    if unit == 'раб. ч.':
        return f'{value:.2f} ч'.replace('.', ',')
    return f'{value:.2f} дн'.replace('.', ',')


def format_iso(value):
    return value.strftime('%Y-%m-%d')


def render_html(rows, start_dt, end_dt, warnings):
    header_cells = ''.join(
        f'<th><span class="num">{num}</span>{html.escape(title)}<small>{unit}</small></th>'
        for _key, title, unit, num in STAGES
    )

    body_rows = []
    for item in rows:
        cells = []
        for key, _title, unit, _num in STAGES:
            value = item[key]
            text = html.escape(format_value(value, unit)) if value is not None else '—'
            css = '' if value is None else ' class="has-value"'
            cells.append(f'<td data-export="{"" if value is None else format(value, ".4f")}"{css}>{text}</td>')
        body_rows.append(
            '<tr>'
            f'<td>{item["id"]}</td>'
            f'<td>{html.escape(item["inn"])}</td>'
            f'<td>{html.escape(item["tid"])}</td>'
            + ''.join(cells)
            + '</tr>'
        )

    warning_html = ''.join(
        f'<div class="warning">{html.escape(message)}</div>' for message in warnings
    )

    return f'''<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SLA по заявкам — {format_iso(start_dt)} — {format_iso(end_dt)}</title>
<style>
    :root {{ --green:#1f5c4a; --green-dark:#164236; --line:#e3e8e6; --muted:#7b8a86; --good:#cdebd8; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:#eef2f1; color:#1d2b28;
        font-family:'Segoe UI',Tahoma,Geneva,Verdana,sans-serif; }}
    header.header {{ background:linear-gradient(135deg,var(--green) 0%,var(--green-dark) 100%);
        color:#fff; padding:18px 26px 20px; }}
    .header-top {{ display:flex; justify-content:space-between; align-items:flex-start; gap:16px; flex-wrap:wrap; }}
    h1 {{ margin:0 0 4px; font-size:24px; font-weight:700; }}
    .sub {{ font-size:13px; opacity:.85; }}
    .actions {{ display:flex; gap:10px; }}
    .btn {{ padding:8px 16px; border-radius:8px; border:1px solid rgba(255,255,255,.6);
        color:#fff; text-decoration:none; font-size:13px; background:transparent; cursor:pointer; }}
    .btn:hover {{ background:rgba(255,255,255,.12); }}
    .period {{ display:flex; gap:10px; align-items:flex-end; margin-top:16px; flex-wrap:wrap; }}
    .period label {{ display:flex; flex-direction:column; gap:4px; font-size:12px; opacity:.9; }}
    .period input {{ padding:7px 10px; border-radius:7px; border:none; font-size:13px; min-width:150px; }}
    .primary {{ padding:9px 18px; border:none; border-radius:7px; background:#fff;
        color:var(--green-dark); font-weight:600; font-size:13px; cursor:pointer; }}
    .export {{ padding:9px 18px; border:1px solid rgba(255,255,255,.6); border-radius:7px;
        background:transparent; color:#fff; font-size:13px; cursor:pointer; }}
    .page {{ padding:18px 26px 40px; }}
    .card {{ background:#fff; border-radius:12px; box-shadow:0 2px 10px rgba(0,0,0,.06); padding:16px 18px 20px; }}
    .card-head {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; }}
    h2 {{ margin:0; font-size:17px; }}
    .count {{ font-size:12px; color:var(--muted); }}
    .table-wrap {{ max-height:calc(100vh - 330px); overflow:auto; border:1px solid var(--line); border-radius:10px; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    thead th {{ position:sticky; top:0; z-index:2; background:#f5f7f6; text-align:left;
        padding:10px 12px; border-bottom:1px solid var(--line); color:#3a4a46; font-weight:600; }}
    thead th small {{ display:block; font-weight:400; font-size:10px; color:var(--muted); text-transform:none; }}
    .num {{ display:inline-flex; align-items:center; justify-content:center; width:17px; height:17px;
        border-radius:50%; background:var(--green); color:#fff; font-size:10px; margin-right:6px; }}
    tbody td {{ padding:9px 12px; border-bottom:1px solid #f0f3f2; white-space:nowrap; }}
    tbody tr:hover td {{ background:#fafbfb; }}
    td.has-value {{ background:var(--good); font-weight:700; }}
    .warning {{ margin-bottom:12px; padding:10px 12px; border-radius:8px;
        background:#fffbeb; color:#92400e; font-size:13px; }}
    .note {{ margin:12px 2px 0; color:var(--muted); font-size:12px; line-height:1.5; }}
</style>
</head>
<body>
<header class="header">
    <div class="header-top">
        <div>
            <h1>Сроки SLA по заявкам</h1>
            <div class="sub">Период создания: {start_dt.strftime('%d.%m.%Y')} — {end_dt.strftime('%d.%m.%Y')}</div>
        </div>
        <div class="actions">
            <a class="btn" href="/sla">Внутренний SLA</a>
            <a class="btn" href="/dashboard">Дашборд</a>
            <a class="btn" href="/">На главную</a>
        </div>
    </div>
    <form class="period" method="GET" action="/sla/bank">
        <label>Дата создания с<input type="date" name="start" value="{format_iso(start_dt)}" required></label>
        <label>по<input type="date" name="end" value="{format_iso(end_dt)}" required></label>
        <button class="primary" type="submit">Показать</button>
        <button class="export" type="button" id="export-xlsx">Выгрузить Excel</button>
    </form>
</header>
<main class="page">
    {warning_html}
    <section class="card">
        <div class="card-head">
            <h2>Сроки прохождения этапов</h2>
            <span class="count">{len(rows):,} заявок</span>
        </div>
        <div class="table-wrap">
            <table id="sla-table">
                <thead><tr>
                    <th>ID заявки</th><th>ИНН</th>
                    <th>TID<small>Идентификатор терминала</small></th>{header_cells}
                </tr></thead>
                <tbody>{''.join(body_rows)}</tbody>
            </table>
        </div>
        <p class="note">Все сроки считаются только по рабочим дням: понедельник–пятница.
            Праздничные дни учитываются как рабочие. Последний этап считается напрямую
            от создания заявки, а не как сумма предыдущих.</p>
    </section>
</main>
<script>
    document.getElementById('export-xlsx').addEventListener('click', function () {{
        const params = new URLSearchParams(window.location.search);
        const start = params.get('start') || '{format_iso(start_dt)}';
        const end = params.get('end') || '{format_iso(end_dt)}';
        window.location.href = '/sla/bank/export?start=' + encodeURIComponent(start)
            + '&end=' + encodeURIComponent(end);
    }});
</script>
</body>
</html>'''


def build_xlsx(rows, start_dt, end_dt):
    wb = Workbook()
    ws = wb.active
    ws.title = f'sla_{format_iso(start_dt)}_{format_iso(end_dt)}'

    ws.append(['ID заявки', 'ИНН', 'TID'] + [title for _k, title, _u, _n in STAGES])
    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(wrap_text=True, vertical='center')

    green = PatternFill('solid', fgColor='CDEBD8')
    for item in rows:
        row = [item['id'], item['inn'], item['tid']]
        for key, _title, _unit, _num in STAGES:
            value = item[key]
            row.append('' if value is None else float(f'{value:.4f}'))
        ws.append(row)

    widths = [12, 16, 16, 22, 22, 26, 26, 28]
    for index, width in enumerate(widths, start=1):
        ws.column_dimensions[chr(64 + index)].width = width
    for row in ws.iter_rows(min_row=2, min_col=4, max_col=len(widths)):
        for cell in row:
            if cell.value not in (None, ''):
                cell.fill = green
    ws.freeze_panes = 'D2'

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output


def parse_period():
    start_param = request.args.get('start')
    end_param = request.args.get('end')
    now = datetime.now()
    if start_param and end_param:
        try:
            start_dt = datetime.strptime(start_param, '%Y-%m-%d')
            # конец периода — включительно по день, а не по полночь
            end_dt = datetime.strptime(end_param, '%Y-%m-%d').replace(
                hour=23, minute=59, second=59, microsecond=999000
            )
            return start_dt, end_dt
        except ValueError:
            pass
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0), now


@app.route('/sla/bank')
def sla_bank_page():
    """Отчёт по срокам SLA для Альфа-Банка."""
    start_dt, end_dt = parse_period()
    try:
        rows, warnings = build_sla_rows(start_dt, end_dt)
    except Exception as exc:
        logger.exception('Ошибка расчёта SLA для банка')
        return Response(
            f'<h1>Ошибка расчёта SLA</h1><pre>{html.escape(str(exc))}</pre>',
            status=500,
            mimetype='text/html; charset=utf-8',
        ), 500
    return Response(render_html(rows, start_dt, end_dt, warnings), mimetype='text/html; charset=utf-8')


@app.route('/sla/bank/export')
def sla_bank_export():
    """Excel-выгрузка отчёта для банка."""
    start_dt, end_dt = parse_period()
    rows, _warnings = build_sla_rows(start_dt, end_dt)
    output = build_xlsx(rows, start_dt, end_dt)
    filename = f'sla_{format_iso(start_dt)}_{format_iso(end_dt)}.xlsx'
    return Response(
        output.read(),
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=5003)